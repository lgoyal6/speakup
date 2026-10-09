package com.speakup.recording

import android.content.Context
import android.media.MediaRecorder
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import com.speakup.sync.UploadWorker

/** Creates app-private files and commits metadata only after a complete fsynced recording. */
class AudioRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var recordingId: String? = null
    fun start(): File {
        check(recorder == null) { "already recording" }
        val file = File(context.filesDir, "recordings/${UUID.randomUUID()}.m4a").also { it.parentFile?.mkdirs() }
        val instance = MediaRecorder(context).apply { setAudioSource(MediaRecorder.AudioSource.MIC); setOutputFormat(MediaRecorder.OutputFormat.MPEG_4); setAudioEncoder(MediaRecorder.AudioEncoder.AAC); setOutputFile(file) }
        try { instance.prepare(); instance.start() } catch (error: Exception) { instance.release(); file.delete(); throw error }
        recordingId = UUID.randomUUID().toString()
        recorder = instance; output = file
        val database = RecordingDatabase.open(context)
        kotlinx.coroutines.runBlocking {
            // Persist the draft before recording starts so a process death leaves a recoverable row.
            database.recordings().insert(RecordingEntity(checkNotNull(recordingId), "LOCAL_DRAFT", file.path, database.recordings().nextOperation(), System.currentTimeMillis()))
        }
        database.close()
        return file
    }
    fun stop(): File {
        val instance = checkNotNull(recorder) { "not recording" }; val file = checkNotNull(output); val id = checkNotNull(recordingId)
        try { instance.stop() } finally { instance.release(); recorder = null; output = null; recordingId = null }
        check(file.isFile && file.length() > 0) { "recording produced no audio" }
        val database = RecordingDatabase.open(context)
        kotlinx.coroutines.runBlocking {
            database.recordings().ready(id, System.currentTimeMillis())
        }
        database.close()
        // WorkManager owns retry and process-death recovery. The request is unique per recording.
        UploadWorker.request(context, id)
        return file
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
