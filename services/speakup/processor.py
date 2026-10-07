"""Restart-safe demo processor. A real provider is injected at this boundary."""
import hashlib
from .domain import RecordingState
from .store import Store

def process_one(store: Store, user_id: str, recording_id: str, audio: bytes, provider="demo"):
    digest=hashlib.sha256(audio).hexdigest(); rec=store.get_recording(user_id,recording_id)
    if rec["state"] == RecordingState.UPLOADED: store.transition(user_id,recording_id,RecordingState.PROCESSING)
    text=" ".join(audio.decode("utf-8",errors="ignore").split()) or "[no speech detected]"
    store.save_transcript(user_id,recording_id,text,provider,"demo-1",digest)
    return store.transition(user_id,recording_id,RecordingState.TRANSCRIBED)

def main(): raise SystemExit("Use process_one from a worker with a durable queue adapter")
