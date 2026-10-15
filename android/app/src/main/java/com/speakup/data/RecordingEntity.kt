// Define the Room rows for recordings and the local operation clock.
// RecordingDatabase persists these fields for the UI and sync worker.
// Keep pending edits separate from the last server transcript.
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
    val uploadOffset: Long = 0,
)

@Entity(tableName = "sync_clock")
data class SyncClock(@PrimaryKey val id: Int = 1, val operationId: Long = 0)
