package com.speakup.recording

import android.content.Context
import android.media.MediaRecorder
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import java.io.File
import java.security.MessageDigest
import java.util.UUID

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
        recordingId = UUID.randomUUID().toString(); recorder = instance; output = file; return file
    }
    fun stop(): File {
        val instance = checkNotNull(recorder) { "not recording" }; val file = checkNotNull(output); val id = checkNotNull(recordingId)
        try { instance.stop() } finally { instance.release(); recorder = null; output = null; recordingId = null }
        check(file.isFile && file.length() > 0) { "recording produced no audio" }
        val hash = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        val database = RecordingDatabase.open(context)
        kotlinx.coroutines.runBlocking {
            val operation = database.recordings().nextOperation()
            database.recordings().insert(RecordingEntity(id, "LOCAL_READY", file.path, operation, System.currentTimeMillis()))
        }
        database.close()
        return file
    }
    fun release() { recorder?.release(); recorder = null; output?.delete(); recorder = null; output = null; recordingId = null }
}
