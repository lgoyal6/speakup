"""Restart-safe transcription worker."""
import hashlib, os
from pathlib import Path
from .domain import RecordingState
from .store import Store
from .provider import FixtureProvider, SpeechProvider, configured_provider

def process_one(store: Store, user_id: str, recording_id: str, audio: bytes, provider: SpeechProvider | None = None):
    digest=hashlib.sha256(audio).hexdigest(); rec=store.get_recording(user_id,recording_id)
    if rec["state"] == RecordingState.UPLOADED: store.transition(user_id,recording_id,RecordingState.PROCESSING)
    # Direct calls are fixture-safe for local tests; the long-running worker
    # selects a configured real provider before calling this function.
    result = (provider or FixtureProvider()).transcribe(audio, digest)
    store.save_transcript(user_id,recording_id,result.text,result.provider,result.model_version,digest)
    return store.transition(user_id,recording_id,RecordingState.TRANSCRIBED)

def process_next(store: Store, provider: SpeechProvider | None = None):
    provider = provider or configured_provider()
    job = store.claim_next_job()
    if not job: return None
    try:
        rec = store.recording_for_job(job["id"]); process_one(store, rec["user_id"], rec["id"], Path(rec["local_path"]).read_bytes(), provider)
        return store.finish_job(job["id"], True)
    except Exception as error:
        store.finish_job(job["id"], False, str(error)); return store.get_job(job["id"])

def main():
    store = Store(os.getenv("SPEAKUP_DB", ":memory:"));
    while process_next(store): pass
