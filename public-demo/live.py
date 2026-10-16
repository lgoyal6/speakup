"""Run the owning upload/store components on bounded synthetic bytes.

Every run owns a temporary database and files. This is a local protocol run,
not Android, real network faults, audio recording or transcription.
"""
from dataclasses import asdict
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from services.speakup.domain import RecordingState
from services.speakup.store import Store
from services.speakup.uploads import DigestMismatch, OffsetMismatch, ResumableUploads


def run_upload(value):
    if not isinstance(value, dict) or set(value) != {"chunks", "chunk_bytes", "seed", "retry_chunk", "restart_after", "corrupt"}:
        raise ValueError("Expected bounded protocol inputs only")
    bounds = {"chunks": (2, 12), "chunk_bytes": (64, 1024), "seed": (0, 255)}
    for key, (low, high) in bounds.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"Invalid {key}")
    count, width, seed = value["chunks"], value["chunk_bytes"], value["seed"]
    for key in ("retry_chunk", "restart_after"):
        if type(value[key]) is not int or not -1 <= value[key] < count:
            raise ValueError(f"Invalid {key}")
    if type(value["corrupt"]) is not bool:
        raise ValueError("Invalid corruption selection")
    payload = bytes((seed + i) % 256 for i in range(count * width))
    expected = hashlib.sha256(payload).hexdigest()
    timeline = []
    with TemporaryDirectory(prefix="speakup-live-") as directory:
        root = Path(directory)
        store = Store(str(root / "upload.sqlite3"))
        try:
            uploads = ResumableUploads(store, root / "bytes")
            rid = store.create_recording("synthetic", recording_id="00000000-0000-4000-8000-000000000001", expected_size=len(payload), expected_sha256=expected)["id"]
            store.transition("synthetic", rid, RecordingState.LOCAL_READY)
            store.transition("synthetic", rid, RecordingState.UPLOADING)
            completed = False
            for i in range(count):
                start = i * width
                data = payload[start:start + width]
                if value["corrupt"] and i == count - 1:
                    data = bytes([data[0] ^ 1]) + data[1:]
                try:
                    result = uploads.append("synthetic", rid, start, len(payload), data)
                    completed = result.complete
                    timeline.append({"event": f"Append chunk {i}", **asdict(result)})
                except DigestMismatch:
                    timeline.append({"event": "Digest mismatch rejected", "offset": uploads.offset(rid), "complete": False})
                    break
                if value["retry_chunk"] == i:
                    try:
                        retry = uploads.append("synthetic", rid, start, len(payload), data)
                        timeline.append({"event": "Retry acknowledged without appending", **asdict(retry)})
                    except OffsetMismatch as error:
                        timeline.append({"event": "Retry refused; client learns authoritative offset", "offset": error.offset})
                if value["restart_after"] == i:
                    store.db.close()
                    store = Store(str(root / "upload.sqlite3"))
                    uploads = ResumableUploads(store, root / "bytes")
                    timeline.append({"event": "Reopened store and upload component", "offset": uploads.offset(rid)})
            recorded = store.get_recording("synthetic", rid)["audio_sha256"]
            return {"input": value, "total_bytes": len(payload), "expected_sha256": expected,
                    "recorded_sha256": recorded, "completed": completed, "timeline": timeline,
                    "scope": "Actual local Python upload/store execution with synthetic bytes; temporary files removed. No Android, external network, audio or transcription."}
        finally:
            store.db.close()
