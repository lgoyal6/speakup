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

/** Durable upload and transcript synchronization. Each operation key is stable across retries. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val recordingId = inputData.getString("recording_id") ?: return@withContext Result.failure()
        val operationKey = inputData.getString("idempotency_key") ?: return@withContext Result.failure()
        val database = RecordingDatabase.open(applicationContext)
        try {
            val record = database.recordings().find(recordingId) ?: return@withContext Result.failure()
            val connection = Credentials(applicationContext).load() ?: return@withContext Result.failure()
            val api = ApiClient(connection)
            if (record.state == "DELETE_PENDING") {
                api.json("POST", "/v1/recordings/$recordingId/delete", JSONObject(), operationKey)
                database.recordings().deleted(recordingId, record.localOperationId)
                return@withContext Result.success()
            }
            if (record.state in setOf("LOCAL_READY", "UPLOAD_RETRY")) {
                val upload = api.json("POST", "/v1/recordings/$recordingId/upload-url", JSONObject(), operationKey)
                api.upload(upload.getString("url"), java.io.File(record.localPath))
                api.json("POST", "/v1/recordings/$recordingId/complete", JSONObject(), operationKey)
            }
            if (record.state in setOf("LOCAL_READY", "UPLOAD_RETRY", "UPLOADING", "UPLOADED")) {
                api.json("POST", "/v1/recordings/$recordingId/process", JSONObject(), operationKey)
            }
            return@withContext Result.success()
        } catch (_: ApiFailure) {
            if (runAttemptCount >= 5) Result.failure() else Result.retry()
        } catch (_: IOException) {
            if (runAttemptCount >= 5) Result.failure() else Result.retry()
        } finally { database.close() }
    }

    companion object {
        fun request(context: Context, recordingId: String, operationKey: String = UUID.randomUUID().toString()) {
            val request = OneTimeWorkRequestBuilder<UploadWorker>().setInputData(workDataOf("recording_id" to recordingId, "idempotency_key" to operationKey)).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).setBackoffCriteria(BackoffPolicy.EXPONENTIAL, java.time.Duration.ofSeconds(10)).build()
            WorkManager.getInstance(context).enqueueUniqueWork("upload-$recordingId", ExistingWorkPolicy.KEEP, request)
        }
    }
}
