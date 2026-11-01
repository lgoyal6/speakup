"""Real sample bytes through original upload/store code; recorded ASR only."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from services.speakup.domain import RecordingState
from services.speakup.store import Store
from services.speakup.uploads import ResumableUploads, OffsetMismatch
from services.speakup.provider import Transcript

SAMPLE = Path(__file__).resolve().parent / "sample"
def run_workflow(value):
    if not isinstance(value, dict) or set(value) != {"step", "text"} or value["step"] not in {"upload", "review", "export"}:
        raise ValueError("Choose a workflow step")
    if not isinstance(value["text"], str) or len(value["text"].encode()) > 4000:
        raise ValueError("Review text must be at most 4000 bytes")
    sample = json.loads((SAMPLE / "recording.json").read_text())
    audio = (SAMPLE / "recording.m4a").read_bytes()
    sha = hashlib.sha256(audio).hexdigest()
    if sha != sample["audio_sha256"]:
        raise ValueError("Sample checksum mismatch")
    with TemporaryDirectory(prefix="speakup-recording-") as directory:
        root = Path(directory)
        store = Store(str(root / "store.db"))
        try:
            rid = store.create_recording("sample", expected_size=len(audio), expected_sha256=sha)["id"]
            store.transition("sample", rid, RecordingState.LOCAL_READY)
            store.transition("sample", rid, RecordingState.UPLOADING)
            uploads = ResumableUploads(store, root / "audio")
            split = len(audio) // 2
            uploads.append("sample", rid, 0, len(audio), audio[:split])
            store.db.close()
            store = Store(str(root / "store.db"))
            uploads = ResumableUploads(store, root / "audio")
            offset = uploads.offset(rid)
            try:
                uploads.append("sample", rid, 0, len(audio), audio[:split])
            except OffsetMismatch as error:
                assert error.offset == split
            uploads.append("sample", rid, offset, len(audio), audio[offset:])
            store.transition("sample", rid, RecordingState.UPLOADED)
            job = store.enqueue_job("sample", rid)
            claim = store.claim_job(job["id"])
            store.publish_transcript(job["id"], claim["lease_token"], Transcript(sample["hypothesis"].strip(), "recorded_cpu_study", sample["model"]), sha)
            original = store.get_transcript("sample", rid)
            if value["step"] in {"review", "export"}:
                store.save_transcript("sample", rid, value["text"], source="user_edit", input_sha256=sha, base_revision=original["revision"])
            exported = store.export("sample", rid) if value["step"] == "export" else None
            current = store.get_transcript("sample", rid)
            return {"audio_sha256": sha, "bytes": len(audio), "recovered_offset": offset,
                    "state": store.get_recording("sample", rid)["state"], "revision": current["revision"],
                    "text": current["text"], "original_text": original["text"],
                    "export_content": exported["content"] if exported else None,
                    "transcription_mode": sample["transcript_mode"],
                    "scope": "Native local upload, restart, review and export. Prerecorded public audio and ASR result. Temporary rows deleted. No Android or fresh inference."}
        finally:
            store.db.close()
