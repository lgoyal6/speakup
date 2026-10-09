"""Explicit transcription adapters. Fixture output requires caller opt-in."""
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    model_version: str


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class SpeechProvider:
    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        raise NotImplementedError


class FixtureProvider(SpeechProvider):
    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        text = " ".join(audio.decode("utf-8", errors="ignore").split()) or "[no speech detected]"
        return Transcript(text, "fixture", "fixture-1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class WhisperHTTPProvider(SpeechProvider):
    """OpenAI-compatible /audio/transcriptions multipart contract, not raw audio HTTP."""
    def __init__(self, endpoint: str, token: str, model: str = "whisper-1", timeout: float = 30):
        url = urllib.parse.urlparse(endpoint)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if url.scheme != "https" and not (url.scheme == "http" and local):
            raise ValueError("speech endpoint requires HTTPS, or loopback HTTP for tests")
        if url.username or url.password or url.fragment:
            raise ValueError("speech endpoint must not contain credentials or a fragment")
        if not token or not model or any(c in token + model for c in "\r\n") or not 0 < timeout <= 120:
            raise ValueError("speech token, model and bounded timeout are required")
        self.endpoint, self.token, self.model, self.timeout = endpoint, token, model, timeout

    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        if not audio or len(audio) > 25 * 1024 * 1024:
            raise ProviderError("provider audio must be 1 byte to 25 MiB", retryable=False)
        if hashlib.sha256(audio).hexdigest() != input_sha256:
            raise ProviderError("provider input hash mismatch", retryable=False)
        boundary = "speakup-" + secrets.token_hex(16)
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.m4a"\r\n'
            'Content-Type: audio/mp4\r\n\r\n'
        ).encode()
        payload = prefix + audio + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(self.endpoint, data=payload, method="POST", headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        })
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ProviderError("unexpected speech provider status")
                body = response.read((1 << 20) + 1)
                if len(body) > (1 << 20):
                    raise ProviderError("speech response exceeds 1 MiB", retryable=False)
        except urllib.error.HTTPError as error:
            # Never persist a vendor error body, URL or token in job failure records.
            error.close()
            raise ProviderError(f"speech provider HTTP {error.code}", retryable=error.code in {408, 429} or error.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError("speech provider connection failed") from None
        try:
            result = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise ProviderError("speech response is not JSON", retryable=False) from None
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ProviderError("speech response requires text", retryable=False)
        # Provenance comes from the configured adapter, never arbitrary response fields.
        return Transcript(result["text"], "openai-compatible", self.model)


def configured_provider() -> SpeechProvider:
    if os.getenv("SPEAKUP_SPEECH_ENDPOINT"):
        return WhisperHTTPProvider(os.environ["SPEAKUP_SPEECH_ENDPOINT"], os.getenv("SPEAKUP_SPEECH_TOKEN", ""), os.getenv("SPEAKUP_SPEECH_MODEL", "whisper-1"))
    if os.getenv("SPEAKUP_ALLOW_FIXTURE_PROVIDER") == "1":
        return FixtureProvider()
    raise RuntimeError("no speech provider configured; explicitly configure a provider or opt into fixture mode")
