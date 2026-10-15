// Inject a recorder stop failure and inspect the persisted draft.
// Exercises AudioRecorder cleanup with the Room recording row.
// The original error must survive while recorder and UI state are released.
package com.speakup.recording

import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test
import java.util.UUID

class RecordingStopFailureTest {
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
