package com.speakup.recording

import android.content.Context
import android.media.MediaRecorder
import java.io.File
import java.util.UUID

/** Owns the process-local recorder and leaves a complete file before returning success. */
class AudioRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var output: File? = null

    fun start(): File {
        check(recorder == null) { "already recording" }
        val file = File(context.filesDir, "recordings/${UUID.randomUUID()}.m4a")
        file.parentFile?.mkdirs()
        val instance = MediaRecorder(context)
        instance.setAudioSource(MediaRecorder.AudioSource.MIC)
        instance.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        instance.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        instance.setOutputFile(file)
        try { instance.prepare(); instance.start() }
        catch (error: Exception) { instance.release(); file.delete(); throw error }
        recorder = instance; output = file
        return file
    }

    fun stop(): File {
        val instance = checkNotNull(recorder) { "not recording" }
        val file = checkNotNull(output)
        try { instance.stop() } finally { instance.release(); recorder = null; output = null }
        check(file.isFile && file.length() > 0) { "recording produced no audio" }
        return file
    }

    fun release() { recorder?.release(); recorder = null; output = null }
}
