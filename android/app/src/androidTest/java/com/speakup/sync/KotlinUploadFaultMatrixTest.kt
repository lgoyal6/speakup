// Run one hundred deterministic single-fault cases through the production Kotlin HTTP client.
// The host proxy and fixture API are private loopback services owned by the test run.
package com.speakup.sync

import androidx.test.platform.app.InstrumentationRegistry
import com.speakup.security.Credentials
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.Random
import java.util.UUID

class KotlinUploadFaultMatrixTest {
    @Test
    fun hundredSeededSingleFaultsRecoverOrStopForReauthentication() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val proxy = "http://10.0.2.2:18080"
        Credentials(context).save(proxy, "h7-test-token")
        val api = ApiClient(Credentials(context).load()!!)
        setLegacyFault(proxy, false)

        try {
            val initialInjections = matrixState(proxy).getInt("injected")
            // One clean control at each API phase proves the harness itself can complete recordings.
            repeat(4) { runScenario(api, proxy, -1 - it, null) }
            assertEquals("controls must not inject faults", initialInjections, matrixState(proxy).getInt("injected"))

            repeat(100) { index ->
                val phase = when (index % 4) {
                    0 -> "create"
                    1 -> "ticket"
                    2 -> "upload"
                    else -> "complete"
                }
                val caseWithinPhase = index / 4
                val mode = when (caseWithinPhase % 5) {
                    0 -> "http_503"
                    1 -> "http_429"
                    2 -> "http_401"
                    3 -> "drop_response"
                    else -> if (phase == "upload") "partial_body" else "http_503"
                }
                runScenario(api, proxy, index, phase to mode)
                assertEquals("scenario $index must add exactly one injection", initialInjections + index + 1, matrixState(proxy).getInt("injected"))
            }
        } finally {
            clearPlan(proxy)
            setLegacyFault(proxy, true)
        }
    }

    private fun runScenario(api: ApiClient, proxy: String, index: Int, fault: Pair<String, String>?) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val recordingId = UUID.nameUUIDFromBytes("h7-kotlin-fault-$index".toByteArray()).toString()
        val bytes = ByteArray(4096 + (index + 10_000) * 17).also { Random(20261015L + index).nextBytes(it) }
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        val file = File(context.cacheDir, "$recordingId.m4a").also { it.writeBytes(bytes) }
        val operationKey = "$recordingId:sync:1"
        var injected = false
        fun arm(phase: String, uploadPath: String? = null) {
            if (fault?.first == phase) configure(proxy, phase, fault.second, recordingId, uploadPath)
        }
        fun call(phase: String, path: String, body: JSONObject, success: () -> JSONObject): JSONObject? {
            try {
                return success()
            } catch (failure: Exception) {
                assertTrue("scenario $index injected more than one failure", !injected)
                assertExpectedFault(failure, fault?.second ?: "")
                injected = true
                if (fault?.second == "http_401") {
                    assertAuthFailureState(api, phase, recordingId, digest)
                    return null
                }
                // These are one-shot transport/server faults; retry the identical idempotent operation.
                return api.json("POST", path, body, operationKey)
            }
        }

        try {
            arm("create")
            val createBody = JSONObject().put("id", recordingId).put("size", bytes.size).put("sha256", digest)
            if (call("create", "/v1/recordings", createBody) { api.json("POST", "/v1/recordings", createBody, operationKey) } == null) return

            arm("ticket")
            val ticketBody = JSONObject()
            val ticket = call("ticket", "/v1/recordings/$recordingId/upload-url", ticketBody) {
                api.json("POST", "/v1/recordings/$recordingId/upload-url", ticketBody, operationKey)
            } ?: return

            val uploadPath = URL("$proxy${ticket.getString("url")}").file
            arm("upload", uploadPath)
            try {
                api.uploadChunk(ticket.getString("url"), file, 0, bytes.size.toLong(), bytes.size)
            } catch (failure: Exception) {
                assertTrue("scenario $index injected more than one failure", !injected)
                assertExpectedFault(failure, fault?.second ?: "")
                injected = true
                if (fault?.second == "http_401") {
                    assertAuthFailureState(api, "upload", recordingId, digest)
                    return
                }
                // Re-fetch the authoritative offset for both a committed upload with a lost response
                // and a truncated request that did not advance the server's durable offset.
                val refreshed = api.json("POST", "/v1/recordings/$recordingId/upload-url", ticketBody, operationKey)
                if (!refreshed.optBoolean("complete")) {
                    api.uploadChunk(refreshed.getString("url"), file, refreshed.optLong("offset", 0), bytes.size.toLong(), bytes.size)
                }
            }

            arm("complete")
            val completeBody = JSONObject()
            if (call("complete", "/v1/recordings/$recordingId/complete", completeBody) {
                    api.json("POST", "/v1/recordings/$recordingId/complete", completeBody, operationKey)
                } == null) return

            val server = api.json("GET", "/v1/recordings/$recordingId")
            assertEquals("scenario $index state", "UPLOADED", server.getString("state"))
            assertEquals("scenario $index declared bytes", bytes.size.toLong(), server.getLong("expected_size"))
            assertEquals("scenario $index digest", digest, server.getString("audio_sha256"))
            if (fault != null) assertTrue("scenario $index should observe its planned fault", injected)
            else assertTrue("control $index must be fault-free", !injected)
        } finally {
            file.delete()
        }
    }

    private fun assertAuthFailureState(api: ApiClient, phase: String, recordingId: String, digest: String) {
        if (phase == "create") {
            try {
                api.json("GET", "/v1/recordings/$recordingId")
                throw AssertionError("401 create must not create a server recording")
            } catch (failure: ApiFailure) {
                assertEquals(404, failure.status)
            }
            return
        }
        val server = api.json("GET", "/v1/recordings/$recordingId")
        when (phase) {
            "ticket" -> assertEquals("LOCAL_DRAFT", server.getString("state"))
            "upload" -> {
                assertEquals("UPLOADING", server.getString("state"))
                assertTrue(server.isNull("audio_sha256"))
            }
            "complete" -> {
                assertEquals("UPLOADING", server.getString("state"))
                assertEquals(digest, server.getString("audio_sha256"))
            }
            else -> throw AssertionError("unexpected 401 phase $phase")
        }
    }

    private fun setLegacyFault(proxy: String, enabled: Boolean) {
        val connection = URL("$proxy/__fault_config").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(JSONObject().put("legacy_enabled", enabled).toString().toByteArray()) }
            assertEquals(200, connection.responseCode)
        } finally { connection.disconnect() }
    }

    private fun clearPlan(proxy: String) {
        val connection = URL("$proxy/__fault_config").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(JSONObject().put("clear_plan", true).toString().toByteArray()) }
            assertEquals(200, connection.responseCode)
        } finally { connection.disconnect() }
    }

    private fun configure(proxy: String, phase: String, mode: String, recordingId: String, path: String? = null) {
        val plan = JSONObject().put("phase", phase).put("mode", mode).put("recording_id", recordingId)
        if (path != null) plan.put("path", path)
        val connection = URL("$proxy/__fault_config").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(plan.toString().toByteArray()) }
            assertEquals(200, connection.responseCode)
        } finally { connection.disconnect() }
    }

    private fun assertExpectedFault(failure: Exception, mode: String) {
        when (mode) {
            "http_401" -> assertTrue("expected HTTP 401, got $failure", failure is ApiFailure && failure.status == 401)
            "http_429" -> assertTrue("expected HTTP 429, got $failure", failure is ApiFailure && failure.status == 429)
            "http_503" -> assertTrue("expected HTTP 503, got $failure", failure is ApiFailure && failure.status == 503)
            "drop_response", "partial_body" -> assertTrue("expected transport failure, got $failure", failure is IOException)
            else -> throw AssertionError("unknown fault mode $mode")
        }
    }

    private fun matrixState(proxy: String): JSONObject = JSONObject(URL("$proxy/__matrix_state").readText())
}
