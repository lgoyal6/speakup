// Capture microphone audio into app-private files.
// RecordingDatabase tracks drafts; UploadWorker starts after finalization.
// Failed stops retain an interrupted draft and release recorder state.
package com.speakup.recording

import android.content.Context
import android.os.Build
import android.media.MediaRecorder
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import java.io.File
import java.util.UUID
import java.io.RandomAccessFile
import com.speakup.sync.UploadWorker

/** Creates app-private files and commits metadata only after a complete fsynced recording. */
class AudioRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var recordingId: String? = null
    fun start(): File {
        check(recorder == null) { "already recording" }
        val id = UUID.randomUUID().toString()
        val file = File(context.filesDir, "recordings/$id.m4a").also { it.parentFile?.mkdirs() }
        val database = RecordingDatabase.open(context)
        val operation = try {
            kotlinx.coroutines.runBlocking {
                val next = database.recordings().nextOperation()
                database.recordings().insert(RecordingEntity(id, "LOCAL_DRAFT", file.path, next, System.currentTimeMillis()))
                next
            }
        } finally { database.close() }
        val instance = (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(context) else MediaRecorder()).apply { setAudioSource(MediaRecorder.AudioSource.MIC); setOutputFormat(MediaRecorder.OutputFormat.MPEG_4); setAudioEncoder(MediaRecorder.AudioEncoder.AAC); setOutputFile(file) }
        try { instance.prepare(); instance.start() } catch (error: Exception) {
            instance.release(); file.delete()
            val cleanup = RecordingDatabase.open(context)
            kotlinx.coroutines.runBlocking { cleanup.recordings().discardFailedStart(id, operation) }
            cleanup.close()
            throw error
        }
        recordingId = id
        recorder = instance; output = file
        return file
    }
    fun stop(): File {
        val instance = checkNotNull(recorder) { "not recording" }; val file = checkNotNull(output); val id = checkNotNull(recordingId)
        var ready = false
        return recordingStopWithCleanup(
            action = {
                instance.stop()
                check(file.isFile && file.length() > 0) { "recording produced no audio" }
                RandomAccessFile(file, "rw").use { it.fd.sync() }
                val database = RecordingDatabase.open(context)
                try {
                    kotlinx.coroutines.runBlocking {
                        check(database.recordings().ready(id, System.currentTimeMillis()) == 1) {
                            "recording draft could not be finalized"
                        }
                    }
                    ready = true
                } finally { database.close() }
                // WorkManager owns retry and process-death recovery. The request is unique per recording.
                UploadWorker.request(context, id)
                file
            },
            onFailure = { error ->
                if (!ready) {
                    val database = RecordingDatabase.open(context)
                    try {
                        kotlinx.coroutines.runBlocking {
                            database.recordings().interrupted(id, error.message ?: "recording could not be finalized", System.currentTimeMillis())
                        }
                    } finally { database.close() }
                }
            },
            cleanup = {
                try { instance.release() } finally { recorder = null; output = null; recordingId = null }
            },
        )
    }
    fun release() {
        val id = recordingId
        recorder?.release(); recorder = null
        output = null; recordingId = null
        if (id != null) {
            val database = RecordingDatabase.open(context)
            kotlinx.coroutines.runBlocking { database.recordings().interrupted(id, "recording process stopped", System.currentTimeMillis()) }
            database.close()
        }
    }
}

internal inline fun <T> recordingStopWithCleanup(
    action: () -> T,
    onFailure: (Exception) -> Unit,
    cleanup: () -> Unit,
): T {
    var failure: Exception? = null
    try {
        return action()
    } catch (error: Exception) {
        failure = error
        try { onFailure(error) } catch (persistenceError: Exception) { error.addSuppressed(persistenceError) }
        throw error
    } finally {
        try { cleanup() } catch (cleanupError: Exception) {
            if (failure != null) failure.addSuppressed(cleanupError) else throw cleanupError
        }
    }
}
