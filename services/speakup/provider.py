"""Speech provider boundary with a Whisper-compatible HTTP adapter."""
import json, os, urllib.request
from dataclasses import dataclass

@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    model_version: str

class SpeechProvider:
    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript: raise NotImplementedError

class FixtureProvider(SpeechProvider):
    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        text = " ".join(audio.decode("utf-8", errors="ignore").split()) or "[no speech detected]"
        return Transcript(text, "fixture", "fixture-1")

class WhisperHTTPProvider(SpeechProvider):
    def __init__(self, endpoint: str, token: str, model: str = "whisper-1", timeout: float = 30):
        if not endpoint or not token: raise ValueError("speech provider endpoint and token are required")
        self.endpoint, self.token, self.model, self.timeout = endpoint, token, model, timeout
    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        req = urllib.request.Request(self.endpoint, data=audio, method="POST", headers={"Authorization": "Bearer " + self.token, "Content-Type": "audio/mpeg", "X-Input-SHA256": input_sha256, "X-Model": self.model})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            if response.status != 200: raise RuntimeError(f"speech provider status {response.status}")
            body = json.loads(response.read(1 << 20))
        text = body.get("text")
        if not isinstance(text, str): raise RuntimeError("speech provider response omitted text")
        return Transcript(text, body.get("provider", "whisper-http"), body.get("model", self.model))

def configured_provider() -> SpeechProvider:
    if os.getenv("SPEAKUP_SPEECH_ENDPOINT"):
        return WhisperHTTPProvider(os.environ["SPEAKUP_SPEECH_ENDPOINT"], os.getenv("SPEAKUP_SPEECH_TOKEN", ""), os.getenv("SPEAKUP_SPEECH_MODEL", "whisper-1"))
    if os.getenv("SPEAKUP_ALLOW_FIXTURE_PROVIDER") == "1": return FixtureProvider()
    raise RuntimeError("no speech provider configured; set SPEAKUP_SPEECH_ENDPOINT or explicitly enable the fixture provider")
