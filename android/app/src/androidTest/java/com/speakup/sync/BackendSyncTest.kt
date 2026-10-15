// Exercise Android upload, transcript edit and delete against a local service.
// Uses real WorkManager, Room and ApiClient with a fixture speech worker.
// This validates synchronization, not microphone capture or speech quality.
package com.speakup.sync

import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import com.speakup.security.Credentials
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.net.URL
import java.util.concurrent.TimeUnit
import java.util.UUID
import kotlinx.coroutines.runBlocking

class BackendSyncTest {
    @Test
    fun resumableUploadRevisionConflictAndDeleteWorkAgainstLocalService() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        Credentials(context).save("http://10.0.2.2:18080", "h7-test-token")
        val api = ApiClient(Credentials(context).load()!!)
        val recordingId = UUID.randomUUID().toString()
        val audio = ByteArray(600 * 1024) { 0xff.toByte() }
        val file = File(context.cacheDir, "$recordingId.m4a")
        file.writeBytes(audio)
        try {
            val database = RecordingDatabase.open(context)
            val operation = database.recordings().nextOperation()
            val recording = File(context.filesDir, "recordings/$recordingId.m4a")
            recording.parentFile?.mkdirs()
            recording.writeBytes(audio)
            database.recordings().insert(RecordingEntity(recordingId, "LOCAL_READY", recording.path, operation, System.currentTimeMillis()))
            database.close()

            val uploadKey = "$recordingId:sync:$operation"
            val uploadWork = UploadWorker.request(context, recordingId, uploadKey)
            assertEquals(WorkInfo.State.SUCCEEDED, awaitWork(context, uploadWork).state)
            assertEquals("1", URL("http://10.0.2.2:18080/__fault_state").readText())
            var current = RecordingDatabase.open(context)
            val transcribed = current.recordings().find(recordingId)!!
            assertEquals("TRANSCRIBED", transcribed.state)
            assertEquals(1, transcribed.serverRevision)
            assertEquals(audio.size.toLong(), transcribed.uploadOffset)
            current.close()

            current = RecordingDatabase.open(context)
            val editOperation = current.recordings().nextOperation()
            current.recordings().edit(recordingId, "reviewed transcript", editOperation, System.currentTimeMillis())
            current.close()
            val editWorkKey = "$recordingId:sync:$editOperation"
            val editWork = UploadWorker.request(context, recordingId, editWorkKey)
            assertEquals(WorkInfo.State.SUCCEEDED, awaitWork(context, editWork).state)
            val edit = JSONObject().put("text", "reviewed transcript").put("base_revision", 1).put("idempotency_key", "$recordingId:edit:$editOperation")
            val saved = api.json("POST", "/v1/recordings/$recordingId/transcript", edit)
            val duplicate = api.json("POST", "/v1/recordings/$recordingId/transcript", edit)
            assertEquals(2, saved.getInt("revision"))
            assertEquals(saved.getInt("revision"), duplicate.getInt("revision"))

            try {
                api.json("POST", "/v1/recordings/$recordingId/transcript", JSONObject().put("text", "stale overwrite").put("base_revision", 1).put("idempotency_key", "$recordingId:edit:stale"))
                throw AssertionError("stale edit should be rejected")
            } catch (failure: ApiFailure) {
                assertEquals(409, failure.status)
            }
            assertEquals("reviewed transcript", api.json("GET", "/v1/recordings/$recordingId/transcript").getString("text"))

            current = RecordingDatabase.open(context)
            val deleteOperation = current.recordings().nextOperation()
            current.recordings().delete(recordingId, deleteOperation, System.currentTimeMillis())
            current.close()
            val deleteWorkKey = "$recordingId:sync:$deleteOperation"
            val deleteWork = UploadWorker.request(context, recordingId, deleteWorkKey)
            assertEquals(WorkInfo.State.SUCCEEDED, awaitWork(context, deleteWork).state)
            assertEquals("DELETED", api.json("GET", "/v1/recordings/$recordingId").getString("state"))
            current = RecordingDatabase.open(context)
            assertEquals("DELETED", current.recordings().find(recordingId)!!.state)
            current.close()
        } finally {
            file.delete()
            File(context.filesDir, "recordings/$recordingId.m4a").delete()
        }
        Unit
    }

    private fun awaitWork(context: android.content.Context, workId: UUID): WorkInfo {
        val deadline = System.currentTimeMillis() + TimeUnit.MINUTES.toMillis(2)
        while (System.currentTimeMillis() < deadline) {
            val info = WorkManager.getInstance(context).getWorkInfoById(workId).get(10, TimeUnit.SECONDS)
            val terminal = info?.takeIf { it.state.isFinished }
            if (terminal != null) return terminal
            Thread.sleep(250)
        }
        throw AssertionError("WorkManager did not finish sync for $workId")
    }
}
