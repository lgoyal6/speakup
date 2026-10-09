"""Durable transcription with fenced publication and bounded failure retries."""
import hashlib
import os
import signal
import threading
from pathlib import Path
from .domain import RecordingState
from .store import Store
from .provider import SpeechProvider, configured_provider


def process_one(store: Store, user_id: str, recording_id: str, audio: bytes, provider: SpeechProvider | None = None):
    """Direct local helper. Background work must use process_next's lease contract."""
    provider = provider or configured_provider()
    digest = hashlib.sha256(audio).hexdigest()
    rec = store.get_recording(user_id, recording_id)
    if rec["state"] == RecordingState.UPLOADED:
        store.transition(user_id, recording_id, RecordingState.PROCESSING)
    result = provider.transcribe(audio, digest)
    store.save_transcript(user_id, recording_id, result.text, result.provider, result.model_version, digest)
    rec = store.get_recording(user_id, recording_id)
    return store.transition(user_id, recording_id, RecordingState.TRANSCRIBED) if rec["state"] == RecordingState.PROCESSING else rec


def process_next(store: Store, provider: SpeechProvider | None = None):
    provider = provider or configured_provider()
    job = store.claim_next_job(lease_seconds=max(60, int(getattr(provider, "timeout", 30)) + 15))
    if not job:
        return None
    try:
        rec = store.recording_for_job(job["id"])
        if not rec["local_path"]:
            raise ValueError("uploaded audio missing")
        with Path(rec["local_path"]).open("rb") as source:
            audio = source.read(25 * 1024 * 1024 + 1)
        if not audio or len(audio) > 25 * 1024 * 1024:
            raise ValueError("audio exceeds transcription provider limit")
        digest = hashlib.sha256(audio).hexdigest()
        if digest != rec["audio_sha256"]:
            raise ValueError("uploaded audio hash mismatch")
        result = provider.transcribe(audio, digest)
        return store.publish_transcript(job["id"], job["lease_token"], result, digest) or store.get_job(job["id"])
    except Exception as error:
        store.finish_job(job["id"], False, type(error).__name__, lease_token=job["lease_token"], retryable=getattr(error, "retryable", not isinstance(error, ValueError)))
        return store.get_job(job["id"])


def run_worker(store, provider, stop, idle_seconds=1):
    while not stop.is_set():
        if process_next(store, provider) is None:
            stop.wait(idle_seconds)


def main():
    provider = configured_provider()
    store = Store(os.getenv("SPEAKUP_DB", "/tmp/speakup.db"))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        run_worker(store, provider, stop)
    finally:
        store.db.close()


if __name__ == "__main__":
    main()
