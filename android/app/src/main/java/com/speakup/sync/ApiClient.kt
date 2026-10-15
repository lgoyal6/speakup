// Send bounded authenticated requests to the configured API origin.
// UploadWorker uses this transport for resumable chunks and transcript edits.
// Disable redirects so upload credentials stay on the chosen origin.
package com.speakup.sync

import com.speakup.security.Credentials
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject
import org.json.JSONException

class ApiFailure(val status: Int, val payload: JSONObject? = null) : IOException("API returned HTTP $status")

/** Blocking transport runs only on Dispatchers.IO. Signed upload paths stay on this origin. */
class ApiClient(private val connection: Credentials.Connection) {
    fun json(method: String, path: String, body: JSONObject? = null, key: String? = null): JSONObject = request(method, path, body?.toString()?.toByteArray(), key, "application/json")
    fun upload(path: String, file: File) {
        require(file.isFile && file.length() in 1..(25L * 1024 * 1024)) { "Audio must be at most 25 MiB" }
        open("PUT", path, null, "audio/mp4").useConnection { http ->
            http.setFixedLengthStreamingMode(file.length())
            http.doOutput = true
            http.outputStream.use { out -> file.inputStream().use { it.copyTo(out) } }
            read(http)
        }
    }
    fun uploadChunk(path: String, file: File, offset: Long, total: Long, chunkSize: Int): JSONObject {
        require(file.isFile && total == file.length() && offset in 0 until total && chunkSize in 1..(256 * 1024))
        val count = minOf(chunkSize.toLong(), total - offset).toInt()
        val bytes = ByteArray(count)
        file.inputStream().use { input ->
            var skipped = 0L
            while (skipped < offset) {
                val step = input.skip(offset - skipped)
                if (step <= 0) throw IOException("Could not seek to durable upload offset")
                skipped += step
            }
            var read = 0
            while (read < count) {
                val n = input.read(bytes, read, count - read)
                if (n < 0) throw IOException("Audio changed during upload")
                read += n
            }
        }
        return open("PUT", path, null, "audio/mp4").useConnection { http ->
            http.setFixedLengthStreamingMode(count)
            http.setRequestProperty("Content-Range", "bytes $offset-${offset + count - 1}/$total")
            http.doOutput = true
            http.outputStream.use { it.write(bytes) }
            read(http)
        }
    }
    private fun request(method: String, path: String, body: ByteArray?, key: String?, type: String): JSONObject = open(method, path, key, type).useConnection { http ->
        if (body != null) {
            http.doOutput = true
            http.setFixedLengthStreamingMode(body.size)
            http.outputStream.use { it.write(body) }
        }
        read(http)
    }
    private fun open(method: String, path: String, key: String?, type: String): HttpURLConnection {
        require(path.startsWith("/v1/") && !path.startsWith("//") && '\r' !in path && '\n' !in path) { "Unsafe API path" }
        return (URL(connection.baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            instanceFollowRedirects = false
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Authorization", "Bearer " + connection.token)
            setRequestProperty("Content-Type", type)
            key?.let { setRequestProperty("Idempotency-Key", it) }
        }
    }
    private fun read(http: HttpURLConnection): JSONObject {
        val status = http.responseCode
        if (status !in 200..299) {
            // Error bodies are optional; their size or format must not change retry policy.
            val payload = try {
                http.errorStream?.use { JSONObject(String(it.readBytesBounded(2 * 1024 * 1024), Charsets.UTF_8)) }
            } catch (_: IOException) {
                null
            } catch (_: JSONException) {
                null
            }
            throw ApiFailure(status, payload)
        }
        val bytes = http.inputStream.use { it.readBytesBounded(2 * 1024 * 1024) }
        return try {
            if (bytes.isEmpty()) JSONObject() else JSONObject(String(bytes, Charsets.UTF_8))
        } catch (_: JSONException) {
            throw IOException("API response was not a JSON object")
        }
    }
    private inline fun <T> HttpURLConnection.useConnection(block: (HttpURLConnection) -> T): T = try { block(this) } finally { disconnect() }
    private fun java.io.InputStream.readBytesBounded(limit: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val size = read(buffer)
            if (size < 0) break
            if (out.size() + size > limit) throw IOException("API response too large")
            out.write(buffer, 0, size)
        }
        return out.toByteArray()
    }
}
