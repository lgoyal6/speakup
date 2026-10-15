// Resume uploads and synchronize transcript edits through WorkManager.
// Reads Room state and asks ApiClient for the server upload offset.
// Stable operation keys and revision checks protect retried requests.
package com.speakup.sync

import android.content.Context
import androidx.work.*
import com.speakup.data.RecordingDatabase
import com.speakup.security.Credentials
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.util.UUID
import java.security.MessageDigest

/** Durable upload and transcript synchronization. Each operation key is stable across retries. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val recordingId = inputData.getString("recording_id") ?: return@withContext Result.failure()
        val operationKey = inputData.getString("idempotency_key") ?: return@withContext Result.failure()
        val database = RecordingDatabase.open(applicationContext)
        try {
            val record = database.recordings().find(recordingId) ?: return@withContext Result.failure()
            val connection = Credentials(applicationContext).load() ?: return@withContext Result.retry()
            val api = ApiClient(connection)
            suspend fun stillCurrent(): Boolean {
                val latest = database.recordings().find(recordingId) ?: return false
                return !latest.deleted && latest.localOperationId == record.localOperationId
            }
            if (record.state == "DELETE_PENDING") {
                api.json("POST", "/v1/recordings/$recordingId/delete", JSONObject(), operationKey)
                database.recordings().deleted(recordingId, record.localOperationId)
                return@withContext Result.success()
            }
            var state = record.state
            if (state in setOf("LOCAL_READY", "UPLOAD_RETRY", "UPLOADING")) {
                val file = java.io.File(record.localPath)
                if (!file.isFile || file.length() !in 1..(25L * 1024 * 1024)) return@withContext Result.failure()
                val hash = MessageDigest.getInstance("SHA-256")
                file.inputStream().use { input ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        hash.update(buffer, 0, count)
                    }
                }
                val digest = hash.digest().joinToString("") { "%02x".format(it) }
                val create = JSONObject().put("id", recordingId).put("size", file.length()).put("sha256", digest)
                api.json("POST", "/v1/recordings", create, operationKey)
                var upload = api.json("POST", "/v1/recordings/$recordingId/upload-url", JSONObject(), operationKey)
                var offset = upload.optLong("offset", 0L)
                database.recordings().uploadOffset(recordingId, record.localOperationId, offset, System.currentTimeMillis())
                while (!upload.optBoolean("complete") && offset < file.length()) {
                    if (!stillCurrent()) return@withContext Result.success()
                    val result = try {
                        api.uploadChunk(upload.getString("url"), file, offset, file.length(), 256 * 1024)
                    } catch (failure: ApiFailure) {
                        if (failure.status != 409) throw failure
                        upload = api.json("POST", "/v1/recordings/$recordingId/upload-url", JSONObject(), operationKey)
                        offset = upload.optLong("offset", 0L)
                        database.recordings().uploadOffset(recordingId, record.localOperationId, offset, System.currentTimeMillis())
                        continue
                    }
                    val nextOffset = result.getLong("offset")
                    if (nextOffset <= offset || nextOffset > file.length()) throw IOException("Server returned an invalid upload offset")
                    offset = nextOffset
                    database.recordings().uploadOffset(recordingId, record.localOperationId, offset, System.currentTimeMillis())
                }
                api.json("POST", "/v1/recordings/$recordingId/complete", JSONObject(), operationKey)
                if (!stillCurrent()) return@withContext Result.success()
                state = "UPLOADED"
            }
            if (state == "UPLOADED") {
                if (!stillCurrent()) return@withContext Result.success()
                api.json("POST", "/v1/recordings/$recordingId/process", JSONObject(), operationKey)
                state = "PROCESSING"
            }
            if (state == "PROCESSING") {
                if (!stillCurrent()) return@withContext Result.success()
                val transcript = api.json("GET", "/v1/recordings/$recordingId/transcript")
                if (transcript.optInt("revision") > 0) {
                    database.recordings().synced(recordingId, record.localOperationId, transcript.optString("text"), transcript.optString("text"), transcript.getInt("revision"), System.currentTimeMillis())
                    return@withContext Result.success()
                }
                return@withContext Result.retry()
            }
            if (record.state == "EDITED" && record.pendingEdit != null) {
                if (!stillCurrent()) return@withContext Result.success()
                val revision = record.serverRevision ?: return@withContext Result.failure()
                val editKey = "$recordingId:edit:${record.localOperationId}"
                try {
                    val saved = api.json("POST", "/v1/recordings/$recordingId/transcript", JSONObject()
                        .put("text", record.pendingEdit).put("base_revision", revision).put("idempotency_key", editKey), editKey)
                    database.recordings().savedEdit(recordingId, record.localOperationId, saved.getString("text"), saved.getInt("revision"), System.currentTimeMillis())
                } catch (failure: ApiFailure) {
                    if (failure.status != 409) throw failure
                    val current = api.json("GET", "/v1/recordings/$recordingId/transcript")
                    // A newer local edit may have replaced this operation during the request.
                    database.recordings().conflict(recordingId, record.localOperationId, current.optString("text"), current.getInt("revision"), System.currentTimeMillis())
                }
                return@withContext Result.success()
            }
            return@withContext Result.success()
        } catch (failure: ApiFailure) {
            if (failure.status in setOf(401, 403, 408, 429) || failure.status >= 500) Result.retry() else Result.failure()
        } catch (_: IOException) {
            Result.retry()
        } finally { database.close() }
    }

    companion object {
        fun request(context: Context, recordingId: String, operationKey: String = UUID.randomUUID().toString()): UUID {
            val request = OneTimeWorkRequestBuilder<UploadWorker>().setInputData(workDataOf("recording_id" to recordingId, "idempotency_key" to operationKey)).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).setBackoffCriteria(BackoffPolicy.EXPONENTIAL, java.time.Duration.ofSeconds(10)).build()
            WorkManager.getInstance(context).enqueueUniqueWork("upload-$recordingId", ExistingWorkPolicy.REPLACE, request)
            return request.id
        }
    }
}
