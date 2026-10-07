from dataclasses import dataclass
from enum import StrEnum
from typing import Any

class RecordingState(StrEnum):
    LOCAL_DRAFT = "LOCAL_DRAFT"
    LOCAL_READY = "LOCAL_READY"
    UPLOADING = "UPLOADING"
    UPLOAD_RETRY = "UPLOAD_RETRY"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    TRANSCRIBED = "TRANSCRIBED"
    EDITED = "EDITED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    DELETED = "DELETED"

_ALLOWED: dict[RecordingState, set[RecordingState]] = {
    RecordingState.LOCAL_DRAFT: {RecordingState.LOCAL_READY, RecordingState.DELETED},
    RecordingState.LOCAL_READY: {RecordingState.UPLOADING, RecordingState.DELETED},
    RecordingState.UPLOADING: {RecordingState.UPLOADED, RecordingState.UPLOAD_RETRY, RecordingState.DELETED},
    RecordingState.UPLOAD_RETRY: {RecordingState.UPLOADING, RecordingState.DELETED},
    RecordingState.UPLOADED: {RecordingState.PROCESSING, RecordingState.DELETED},
    RecordingState.PROCESSING: {RecordingState.TRANSCRIBED, RecordingState.FAILED, RecordingState.DELETED},
    RecordingState.TRANSCRIBED: {RecordingState.EDITED, RecordingState.EXPORTED, RecordingState.DELETED},
    RecordingState.EDITED: {RecordingState.EXPORTED, RecordingState.DELETED},
    RecordingState.EXPORTED: {RecordingState.DELETED},
    RecordingState.FAILED: {RecordingState.RETRYING, RecordingState.DELETED},
    RecordingState.RETRYING: {RecordingState.PROCESSING, RecordingState.FAILED, RecordingState.DELETED},
    RecordingState.DELETED: set(),
}

class InvalidTransition(ValueError):
    pass

@dataclass(frozen=True)
class Recording:
    id: str
    user_id: str
    state: RecordingState
    local_path: str | None = None
    audio_sha256: str | None = None

    def transition(self, target: RecordingState) -> "Recording":
        if target == self.state:
            return self
        if target not in _ALLOWED[self.state]:
            raise InvalidTransition(f"{self.state} -> {target} is not allowed")
        return Recording(self.id, self.user_id, target, self.local_path, self.audio_sha256)

@dataclass(frozen=True)
class TranscriptRevision:
    id: str
    recording_id: str
    revision: int
    text: str
    source: str
    model_version: str | None
    input_sha256: str | None
    output_sha256: str | None


def validate_sync_operation(operation: dict[str, Any]) -> None:
    required = {"device_id", "local_operation_id", "idempotency_key", "kind"}
    missing = required - operation.keys()
    if missing:
        raise ValueError(f"missing sync fields: {sorted(missing)}")
    if not isinstance(operation["local_operation_id"], int) or operation["local_operation_id"] < 1:
        raise ValueError("local_operation_id must be a positive integer")
