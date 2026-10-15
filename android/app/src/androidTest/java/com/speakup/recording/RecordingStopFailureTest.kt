// Exercise recorder setup and stop failures against persisted drafts.
// Startup tests call AudioRecorder.start with failing recorder factories.
// Cleanup must preserve the original error and release files and recorder state.
package com.speakup.recording

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import android.content.Context
import android.media.MediaRecorder
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.UUID

class RecordingStopFailureTest {
    @Test
    fun constructionAndConfigurationFailuresCleanRealStartAndPreserveOriginalError() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val before = failedStartSnapshot(context)
        val constructionFailure = IllegalStateException("recorder construction failed")
        val actual = try {
            AudioRecorder(context) { throw constructionFailure }.start()
            null
        } catch (error: Exception) { error }
        assertSame(constructionFailure, actual)
        assertEquals(before, failedStartSnapshot(context))

        val configurationFailure = IllegalStateException("recorder configuration failed")
        var recorderReleased = false
        val actualConfigurationFailure = try {
            AudioRecorder(context) {
                object : MediaRecorder() {
                    override fun setAudioSource(audioSource: Int) = Unit
                    override fun setOutputFormat(outputFormat: Int) = Unit
                    override fun setAudioEncoder(audioEncoder: Int) = Unit

                    override fun setOutputFile(file: File) {
                        file.writeBytes(byteArrayOf(1, 2, 3))
                        throw configurationFailure
                    }

                    override fun prepare() = Unit
                    override fun start() = Unit

                    override fun release() {
                        recorderReleased = true
                        super.release()
                        throw IllegalStateException("release failed")
                    }
                }
            }.start()
            null
        } catch (error: Exception) { error }

        assertSame(configurationFailure, actualConfigurationFailure)
        assertTrue(recorderReleased)
        assertEquals(1, actualConfigurationFailure!!.suppressed.size)
        assertEquals(before, failedStartSnapshot(context))
    }

    private suspend fun failedStartSnapshot(context: Context): Pair<Set<String>, Set<String>> {
        val database = RecordingDatabase.open(context)
        val drafts = try {
            database.recordings().interrupted().map { it.id }.toSet()
        } finally {
            database.close()
        }
        val files = File(context.filesDir, "recordings").listFiles()?.map { it.name }?.toSet().orEmpty()
        return drafts to files
    }

    @Test
    fun failedStopInterruptsDraftAndAlwaysReleasesRecorderState() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val database = Room.inMemoryDatabaseBuilder(context, RecordingDatabase::class.java).build()
        val id = UUID.randomUUID().toString()
        database.recordings().insert(RecordingEntity(id, "LOCAL_DRAFT", "/tmp/$id.m4a", 1, 1))
        var recorderReleased = false
        var uiRecording = true
        val stopFailure = IllegalStateException("stop failed")

        val actual = try {
            recordingStopWithCleanup(
                action = { throw stopFailure },
                onFailure = { error ->
                    database.recordings().interrupted(id, error.message, 2)
                },
                cleanup = { recorderReleased = true; uiRecording = false },
            )
            null
        } catch (error: Exception) { error }

        assertSame(stopFailure, actual)
        assertEquals("INTERRUPTED", database.recordings().find(id)!!.state)
        assertEquals(true, recorderReleased)
        assertEquals(false, uiRecording)
        database.close()
    }
}
