package com.speakup.data

import android.content.Context
import androidx.room.*
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
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun seedClock(clock: SyncClock)
    @Query("UPDATE sync_clock SET operationId=operationId+1 WHERE id=1") abstract suspend fun incrementClock()
    @Query("SELECT operationId FROM sync_clock WHERE id=1") abstract suspend fun clock(): Long
    @Transaction
    open suspend fun nextOperation(): Long { seedClock(SyncClock()); incrementClock(); return clock() }
    @Query("UPDATE recordings SET state=:state, error=:error, updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun state(id: String, operation: Long, state: String, error: String?, time: Long): Int
    @Query("UPDATE recordings SET transcript=:text,pendingEdit=:text,localOperationId=:operation,state='EDITED',error=NULL,updatedAtEpochMs=:time WHERE id=:id AND deleted=0")
    abstract suspend fun edit(id: String, text: String, operation: Long, time: Long)
    @Query("UPDATE recordings SET deleted=1,state='DELETE_PENDING',rawTranscript=NULL,transcript=NULL,pendingEdit=NULL,localOperationId=:operation,updatedAtEpochMs=:time WHERE id=:id")
    abstract suspend fun delete(id: String, operation: Long, time: Long)
    @Query("UPDATE recordings SET state='DELETED',error=NULL WHERE id=:id AND localOperationId=:operation AND deleted=1")
    abstract suspend fun deleted(id: String, operation: Long)
    @Query("UPDATE recordings SET state='TRANSCRIBED',rawTranscript=:raw,transcript=:text,pendingEdit=NULL,serverRevision=:revision,error=NULL,updatedAtEpochMs=:time WHERE id=:id AND localOperationId=:operation AND deleted=0")
    abstract suspend fun synced(id: String, operation: Long, raw: String, text: String, revision: Int, time: Long): Int
    @Query("SELECT id FROM recordings WHERE state NOT IN ('LOCAL_DRAFT','INTERRUPTED','DELETED')")
    abstract suspend fun resumableIds(): List<String>
}

@Database(entities = [RecordingEntity::class, SyncClock::class], version = 1, exportSchema = true)
abstract class RecordingDatabase : RoomDatabase() {
    abstract fun recordings(): RecordingDao
    companion object {
        fun open(context: Context) = Room.databaseBuilder(context.applicationContext, RecordingDatabase::class.java, "speakup.db").build()
    }
}
