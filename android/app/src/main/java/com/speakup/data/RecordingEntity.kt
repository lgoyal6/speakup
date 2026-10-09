package com.speakup.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "recordings")
data class RecordingEntity(
    @PrimaryKey val id: String,
    val state: String,
    val localPath: String,
    val localOperationId: Long,
    val updatedAtEpochMs: Long,
    val deleted: Boolean = false,
    val rawTranscript: String? = null,
    val transcript: String? = null,
    val pendingEdit: String? = null,
    val serverRevision: Int? = null,
    val error: String? = null,
)

@Entity(tableName = "sync_clock")
data class SyncClock(@PrimaryKey val id: Int = 1, val operationId: Long = 0)
