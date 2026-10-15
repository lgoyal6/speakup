// A 401 remains visible and durable until the user saves valid credentials.
package com.speakup.sync

import android.content.Intent
import androidx.test.platform.app.InstrumentationRegistry
import com.speakup.MainActivity
import com.speakup.resumePendingSync
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import com.speakup.security.Credentials
import androidx.work.WorkInfo
import androidx.work.WorkManager
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking

class AuthRequiredResumeTest {
    @Test
    fun unauthorizedUploadStaysBlockedAcrossRestartThenResumesWithSameOperation() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val proxy = "http://10.0.2.2:18080"
        val id = UUID.randomUUID().toString()
        val operation = 1L
        val operationKey = "$id:sync:$operation"
        val bytes = ByteArray(8192) { (it * 31).toByte() }
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        val audio = File(context.filesDir, "recordings/$id.m4a").also {
            it.parentFile?.mkdirs()
            it.writeBytes(bytes)
        }
        var database = RecordingDatabase.open(context)
        var activity: MainActivity? = null
        try {
            database.recordings().insert(RecordingEntity(id, "LOCAL_READY", audio.path, operation, System.currentTimeMillis()))
            // The local fixture accepts this credential; the scoped proxy simulates its
            // rejection at upload after create and ticket have already succeeded.
            Credentials(context).save(proxy, "h7-test-token")
            setLegacyFault(proxy, false)
            val before = matrixState(proxy)
            configure(proxy, "upload", "http_401", id, "/v1/uploads/$id")

            val firstWorkId = UploadWorker.request(context, id, operationKey)
            val failed = awaitWork(context, firstWorkId)
            assertEquals("401 must stop automatic retries", WorkInfo.State.FAILED, failed.state)
            var local = database.recordings().find(id)!!
            assertEquals("UPLOAD state and offset survive auth failure", "UPLOADING", local.state)
            assertEquals(0L, local.uploadOffset)
            assertTrue(local.error.orEmpty().contains("Authentication required"))
            val afterFailure = matrixState(proxy)
            assertEquals(before.getInt("injected") + 1, afterFailure.getInt("injected"))

            // Launching the app again runs its startup reconciliation. It must leave the failed
            // unauthorized operation untouched until the user saves credentials.
            val intent = Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val restartedActivity = instrumentation.startActivitySync(intent) as MainActivity
            activity = restartedActivity
            Thread.sleep(12_000)
            assertEquals(WorkInfo.State.FAILED, WorkManager.getInstance(context).getWorkInfoById(firstWorkId).get(10, TimeUnit.SECONDS)!!.state)
            assertEquals("startup must not replay the rejected operation", afterFailure.getInt("requests"), matrixState(proxy).getInt("requests"))
            instrumentation.runOnMainSync { restartedActivity.finish() }
            activity = null

            // Reopen Room to model process death, then invoke the same resume path as Save connection.
            database.close()
            database = RecordingDatabase.open(context)
            local = database.recordings().find(id)!!
            assertEquals(operation, local.localOperationId)
            assertEquals("UPLOADING", local.state)
            assertTrue(local.error.orEmpty().contains("Authentication required"))
            Credentials(context).save(proxy, "h7-test-token")
            resumePendingSync(context, database)

            val replacementId = awaitReplacement(context, id, firstWorkId)
            assertTrue(WorkManager.getInstance(context).getWorkInfoById(replacementId).get(10, TimeUnit.SECONDS) != null)
            assertEquals(WorkInfo.State.SUCCEEDED, awaitWork(context, replacementId).state)
            val server = ApiClient(Credentials(context).load()!!).json("GET", "/v1/recordings/$id")
            assertEquals("TRANSCRIBED", server.getString("state"))
            assertEquals(bytes.size.toLong(), server.getLong("expected_size"))
            assertEquals(digest, server.getString("audio_sha256"))
            local = database.recordings().find(id)!!
            assertEquals("TRANSCRIBED", local.state)
            assertNull(local.error)
            assertEquals("same recording key, one scoped injection", before.getInt("injected") + 1, matrixState(proxy).getInt("injected"))
            val operations = matrixState(proxy).getJSONArray("operations")
            val createKeys = (0 until operations.length()).map { operations.getJSONObject(it) }
                .filter { it.getString("method") == "POST" && it.getString("path") == "/v1/recordings" }
                .map { it.optString("idempotency_key") }
            assertTrue("initial and resumed create requests must carry the same operation key", createKeys.count { it == operationKey } >= 2)
        } finally {
            activity?.let { pendingActivity ->
                instrumentation.runOnMainSync { pendingActivity.finish() }
            }
            val current = database.recordings().find(id)
            if (current != null) {
                val deleteOperation = database.recordings().nextOperation()
                database.recordings().delete(id, deleteOperation, System.currentTimeMillis())
                database.recordings().deleted(id, deleteOperation)
            }
            database.close()
            audio.delete()
            clearPlan(proxy)
            setLegacyFault(proxy, true)
        }
    }

    private fun awaitWork(context: android.content.Context, id: UUID): WorkInfo {
        val deadline = System.currentTimeMillis() + TimeUnit.MINUTES.toMillis(2)
        while (System.currentTimeMillis() < deadline) {
            val info = WorkManager.getInstance(context).getWorkInfoById(id).get(10, TimeUnit.SECONDS)
            if (info?.state?.isFinished == true) return info
            Thread.sleep(250)
        }
        throw AssertionError("WorkManager did not finish sync for $id")
    }

    private fun awaitReplacement(context: android.content.Context, id: String, previousId: UUID): UUID {
        val deadline = System.currentTimeMillis() + TimeUnit.MINUTES.toMillis(2)
        while (System.currentTimeMillis() < deadline) {
            val replacement = WorkManager.getInstance(context).getWorkInfosForUniqueWork("upload-$id").get(10, TimeUnit.SECONDS)
                .firstOrNull { it.id != previousId }
            if (replacement != null) return replacement.id
            Thread.sleep(250)
        }
        throw AssertionError("saving credentials did not enqueue replacement work for $id")
    }

    private fun configure(proxy: String, phase: String, mode: String, id: String, path: String) {
        val connection = URL("$proxy/__fault_config").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject().put("phase", phase).put("mode", mode).put("recording_id", id).put("path", path)
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
            assertEquals(200, connection.responseCode)
        } finally { connection.disconnect() }
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

    private fun matrixState(proxy: String): JSONObject = JSONObject(URL("$proxy/__matrix_state").readText())
}
