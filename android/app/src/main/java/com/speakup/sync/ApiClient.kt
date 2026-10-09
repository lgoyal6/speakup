package com.speakup.sync

import com.speakup.security.Credentials
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

class ApiFailure(val status: Int) : IOException("API returned HTTP $status")

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
        if (http.responseCode !in 200..299) throw ApiFailure(http.responseCode)
        val bytes = http.inputStream.use { it.readBytesBounded(2 * 1024 * 1024) }
        return if (bytes.isEmpty()) JSONObject() else JSONObject(String(bytes, Charsets.UTF_8))
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
