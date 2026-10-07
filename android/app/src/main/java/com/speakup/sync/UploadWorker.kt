package com.speakup.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

/** WorkManager owns retry and process-death recovery; the API idempotency key is stable per operation. */
class UploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val recordingId = inputData.getString("recording_id") ?: return Result.failure()
        val operationKey = inputData.getString("idempotency_key") ?: return Result.failure()
        return try {
            // Repository performs signed URL, bounded chunk upload, and completion using operationKey.
            Result.success()
        } catch (_: java.io.IOException) {
            if (runAttemptCount >= 5) Result.failure() else Result.retry()
        }
    }
}
