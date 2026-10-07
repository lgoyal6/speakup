package com.speakup.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "recordings")
data class RecordingEntity(
    @PrimaryKey val id: String,
    val state: String,
    val localPath: String?,
    val localOperationId: Long,
    val updatedAtEpochMs: Long,
    val deleted: Boolean = false,
)
