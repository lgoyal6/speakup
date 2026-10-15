// Store recording state and local operation order with Room.
// The UI and UploadWorker use these queries to resume pending work.
// Version 2 adds upload offsets without discarding version 1 recordings.
package com.speakup.data

import android.content.Context
import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Dao
abstract class RecordingDao {
    @Query("SELECT * FROM recordings WHERE deleted=0 ORDER BY updatedAtEpochMs DESC")
    abstract fun history(): Flow<List<RecordingEntity>>
    @Query("SELECT * FROM recordings WHERE id=:id")
    abstract suspend fun find(id: String): RecordingEntity?
    @Query("SELECT * FROM recordings WHERE state='LOCAL_DRAFT' AND deleted=0")
    abstract suspend fun interrupted(): List<RecordingEntity>
    @Insert abstract suspend fun insert(recording: RecordingEntity)
    @Query("DELETE FROM recordings WHERE id=:id AND localOperationId=:operation AND state='LOCAL_DRAFT'")
    abstract suspend fun discardFailedStart(id: String, operation: Long): Int
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun seedClock(clock: SyncClock)
    @Query("UPDATE sync_clock SET operationId=operationId+1 WHERE id=1") abstract suspend fun incrementClock()
    @Query("SELECT operationId FROM sync_clock WHERE id=1") abstract suspend fun clock(): Long
    @Transaction
    open suspend fun nextOperation(): Long { seedClock(SyncClock()); incrementClock(); return clock() }
    @Query("UPDATE recordings SET state=:state, error=:error, updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun state(id: String, operation: Long, state: String, error: String?, time: Long): Int
    @Query("UPDATE recordings SET state='INTERRUPTED',error=:error,updatedAtEpochMs=:time WHERE id=:id AND deleted=0")
    abstract suspend fun interrupted(id: String, error: String?, time: Long): Int
    @Query("UPDATE recordings SET state='LOCAL_READY',error=NULL,updatedAtEpochMs=:time WHERE id=:id AND state='LOCAL_DRAFT' AND deleted=0")
    abstract suspend fun ready(id: String, time: Long): Int
    @Query("UPDATE recordings SET transcript=:text,pendingEdit=:text,localOperationId=:operation,state='EDITED',error=NULL,updatedAtEpochMs=:time WHERE id=:id AND deleted=0")
    abstract suspend fun edit(id: String, text: String, operation: Long, time: Long)
    @Query("UPDATE recordings SET state='CONFLICT',rawTranscript=:serverText,serverRevision=:revision,error='Transcript changed on another device',updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun conflict(id: String, operation: Long, serverText: String, revision: Int, time: Long): Int
    @Query("UPDATE recordings SET transcript=:text,pendingEdit=NULL,serverRevision=:revision,state='EDITED',error=NULL,updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun savedEdit(id: String, operation: Long, text: String, revision: Int, time: Long): Int
    @Query("UPDATE recordings SET deleted=1,state='DELETE_PENDING',rawTranscript=NULL,transcript=NULL,pendingEdit=NULL,localOperationId=:operation,updatedAtEpochMs=:time WHERE id=:id")
    abstract suspend fun delete(id: String, operation: Long, time: Long)
    @Query("UPDATE recordings SET state='DELETED',error=NULL WHERE id=:id AND localOperationId=:operation AND deleted=1")
    abstract suspend fun deleted(id: String, operation: Long)
    @Query("UPDATE recordings SET uploadOffset=:offset,state='UPLOADING',error=NULL,updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun uploadOffset(id: String, operation: Long, offset: Long, time: Long): Int
    @Query("UPDATE recordings SET state='TRANSCRIBED',rawTranscript=:raw,transcript=:text,pendingEdit=NULL,serverRevision=:revision,error=NULL,updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun synced(id: String, operation: Long, raw: String, text: String, revision: Int, time: Long): Int
    @Query("SELECT id FROM recordings WHERE state NOT IN ('LOCAL_DRAFT','INTERRUPTED','DELETED')")
    abstract suspend fun resumableIds(): List<String>
}

@Database(entities = [RecordingEntity::class, SyncClock::class], version = 2, exportSchema = true)
abstract class RecordingDatabase : RoomDatabase() {
    abstract fun recordings(): RecordingDao
    companion object {
        internal val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE recordings ADD COLUMN uploadOffset INTEGER NOT NULL DEFAULT 0")
            }
        }
        fun open(context: Context, name: String = "speakup.db") = Room.databaseBuilder(context.applicationContext, RecordingDatabase::class.java, name)
            .addMigrations(MIGRATION_1_2).build()
    }
}
