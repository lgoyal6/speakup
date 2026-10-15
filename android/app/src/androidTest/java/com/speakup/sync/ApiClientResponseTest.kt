// Verify HTTP failures keep their status when a proxy returns a non-JSON body.
// Exercises ApiClient with a real loopback socket and Android's JSON parser.
// Malformed success bodies must fail as transport errors without logging content.
package com.speakup.sync

import com.speakup.security.Credentials
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

class ApiClientResponseTest {
    private fun response(status: Int, body: String, check: (ApiClient) -> Unit) {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = 10_000
            val worker = thread(isDaemon = true) {
                server.accept().use { socket ->
                    socket.soTimeout = 10_000
                    val input = socket.getInputStream().bufferedReader()
                    while (!input.readLine().isNullOrEmpty()) { }
                    val bytes = body.toByteArray(Charsets.UTF_8)
                    socket.getOutputStream().apply {
                        write("HTTP/1.1 $status Test\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
                        write(bytes)
                        flush()
                    }
                }
            }
            try {
                check(ApiClient(Credentials.Connection("http://127.0.0.1:${server.localPort}", "local-test-token")))
            } finally {
                worker.join(10_000)
            }
        }
    }

    @Test
    fun htmlServiceFailureKeepsRetryableStatus() = response(503, "<!DOCTYPE html><title>Unavailable</title>") { api ->
        try {
            api.json("GET", "/v1/recordings/test")
            fail("Expected a service failure")
        } catch (failure: ApiFailure) {
            assertEquals(503, failure.status)
            assertNull(failure.payload)
        }
    }

    @Test
    fun jsonFailureKeepsStructuredDetails() = response(409, "{\"offset\":256}") { api ->
        try {
            api.json("GET", "/v1/recordings/test")
            fail("Expected a conflict")
        } catch (failure: ApiFailure) {
            assertEquals(409, failure.status)
            assertEquals(256, failure.payload!!.getInt("offset"))
        }
    }

    @Test
    fun oversizedPermanentErrorKeepsItsStatus() = response(413, "x".repeat(2 * 1024 * 1024 + 1)) { api ->
        try {
            api.json("GET", "/v1/recordings/test")
            fail("Expected a permanent request failure")
        } catch (failure: ApiFailure) {
            assertEquals(413, failure.status)
            assertNull(failure.payload)
        }
    }

    @Test
    fun malformedSuccessBodyRaisesRedactedTransportError() = response(200, "private malformed response") { api ->
        try {
            api.json("GET", "/v1/recordings/test")
            fail("Expected a malformed response failure")
        } catch (failure: IOException) {
            assertEquals("API response was not a JSON object", failure.message)
            assertNull(failure.cause)
        }
    }
}
