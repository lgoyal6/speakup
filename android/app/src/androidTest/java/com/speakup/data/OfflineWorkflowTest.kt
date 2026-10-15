// Check Room migration and pending operations across database restarts.
// Uses the shipped RecordingDatabase schema and DAO on Android.
// These checks model durable state recovery without microphone capture.
package com.speakup.data

import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.io.File
import java.util.UUID

class OfflineWorkflowTest {
    @Test
    fun staleConflictCannotReplaceANewerLocalEdit() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val database = Room.inMemoryDatabaseBuilder(context, RecordingDatabase::class.java).build()
        try {
            val dao = database.recordings()
            dao.insert(RecordingEntity("edit-race", "EDITED", "/tmp/test.m4a", 2, 10,
                rawTranscript = "old server text", transcript = "latest local edit",
                pendingEdit = "latest local edit", serverRevision = 1))
            assertEquals(0, dao.conflict("edit-race", 1, "stale response", 2, 11))
            val unchanged = dao.find("edit-race")!!
            assertEquals("EDITED", unchanged.state)
            assertEquals("old server text", unchanged.rawTranscript)
            assertEquals(1, unchanged.serverRevision)
            assertEquals("latest local edit", unchanged.pendingEdit)
            assertEquals(1, dao.conflict("edit-race", 2, "current server text", 2, 12))
            val conflict = dao.find("edit-race")!!
            assertEquals("CONFLICT", conflict.state)
            assertEquals("current server text", conflict.rawTranscript)
            assertEquals("latest local edit", conflict.pendingEdit)
        } finally {
            database.close()
        }
    }

    @Test
    fun migrationAndPendingOperationsSurviveDatabaseRestart() {
        runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "speakup-restart-${UUID.randomUUID()}.db"
        val path = context.getDatabasePath(name)
        path.parentFile?.mkdirs()
        context.deleteDatabase(name)

        // Build a genuine v1 Room file so the test exercises the shipped migration.
        SQLiteDatabase.openOrCreateDatabase(path, null).use { old ->
            old.execSQL("CREATE TABLE IF NOT EXISTS recordings (`id` TEXT NOT NULL, `state` TEXT NOT NULL, `localPath` TEXT NOT NULL, `localOperationId` INTEGER NOT NULL, `updatedAtEpochMs` INTEGER NOT NULL, `deleted` INTEGER NOT NULL, `rawTranscript` TEXT, `transcript` TEXT, `pendingEdit` TEXT, `serverRevision` INTEGER, `error` TEXT, PRIMARY KEY(`id`))")
            old.execSQL("CREATE TABLE IF NOT EXISTS sync_clock (`id` INTEGER NOT NULL, `operationId` INTEGER NOT NULL, PRIMARY KEY(`id`))")
            old.execSQL("CREATE TABLE IF NOT EXISTS room_master_table (id INTEGER PRIMARY KEY,identity_hash TEXT)")
            old.execSQL("INSERT INTO room_master_table (id,identity_hash) VALUES(42,'bc5bc4d4d5117a6a75047bebb5817f16')")
            old.execSQL("INSERT INTO recordings(id,state,localPath,localOperationId,updatedAtEpochMs,deleted,transcript,pendingEdit,serverRevision) VALUES('recording-1','UPLOADING','/tmp/audio.m4a',3,1,0,'draft','draft',1)")
            old.version = 1
        }

        val firstOpen = RecordingDatabase.open(context, name)
        val migrated = firstOpen.recordings().find("recording-1")
        assertNotNull(migrated)
        assertEquals(0L, migrated!!.uploadOffset)
        firstOpen.recordings().uploadOffset("recording-1", 3, 262144, 2)
        firstOpen.recordings().edit("recording-1", "corrected text", 4, 3)
        firstOpen.close()

        val afterProcessDeath = RecordingDatabase.open(context, name)
        val resumed = afterProcessDeath.recordings().find("recording-1")!!
        assertEquals(262144L, resumed.uploadOffset)
        assertEquals("EDITED", resumed.state)
        assertEquals("corrected text", resumed.pendingEdit)
        afterProcessDeath.recordings().delete("recording-1", 5, 4)
        afterProcessDeath.close()

        val afterDeleteRestart = RecordingDatabase.open(context, name)
        val pendingDelete = afterDeleteRestart.recordings().find("recording-1")!!
        assertEquals("DELETE_PENDING", pendingDelete.state)
        assertEquals(true, pendingDelete.deleted)
        assertEquals(null, pendingDelete.pendingEdit)
        afterDeleteRestart.close()
        context.deleteDatabase(name)
        File(path.absolutePath + "-shm").delete()
        File(path.absolutePath + "-wal").delete()
        }
    }
}
