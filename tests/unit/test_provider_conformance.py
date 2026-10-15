"""One capability contract, run against every speech adapter that can run here.

Adapters and evidence boundary:
  fixture          FixtureProvider, offline test only.
  openai-stub      WhisperHTTPProvider against a loopback stub that speaks the
                   OpenAI /audio/transcriptions contract (status/body failure classes).
  whispercpp-local WhisperHTTPProvider against a real local whisper.cpp
                   `whisper-server --convert` process. Runs only when
                   SPEAKUP_CONFORMANCE_WHISPER_URL and SPEAKUP_CONFORMANCE_AUDIO are set;
                   otherwise it is skipped, never counted as passing.
A hosted OpenAI-compatible endpoint is not exercised here (needs a key and spend approval).
"""
import hashlib
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from speakup.provider import FixtureProvider, ProviderError, SpeechProvider, Transcript, WhisperHTTPProvider


class _Stub:
    def __init__(self):
        owner = self
        self.calls, self.status, self.body = 0, 200, json.dumps({"text": "hello world", "provider": "spoof", "model": "spoof"}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.calls += 1
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(owner.status)
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SpeechContract:
    """Mixin: subclasses provide make_provider(), sample_audio() and the adapter's provenance."""
    expected_provider: str
    expected_model: str
    network_failures_supported = False

    def call_count(self):
        return None

    def test_contract_success_returns_text_with_configured_provenance(self):
        audio = self.sample_audio()
        result = self.make_provider().transcribe(audio, hashlib.sha256(audio).hexdigest())
        self.assertIsInstance(result, Transcript)
        self.assertIsInstance(result.text, str)
        self.assertTrue(result.text.strip())
        self.assertEqual((result.provider, result.model_version), (self.expected_provider, self.expected_model))

    def test_contract_hash_mismatch_is_terminal_before_any_work(self):
        before = self.call_count()
        with self.assertRaises(ProviderError) as ctx:
            self.make_provider().transcribe(self.sample_audio(), "0" * 64)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(self.call_count(), before)

    def test_contract_empty_and_oversized_audio_are_terminal(self):
        provider = self.make_provider()
        for audio in (b"", b"x" * (25 * 1024 * 1024 + 1)):
            with self.assertRaises(ProviderError) as ctx:
                provider.transcribe(audio, hashlib.sha256(audio).hexdigest())
            self.assertFalse(ctx.exception.retryable)

    def test_contract_adapter_is_a_speech_provider(self):
        self.assertIsInstance(self.make_provider(), SpeechProvider)


class FixtureConformance(SpeechContract, unittest.TestCase):
    expected_provider, expected_model = "fixture", "fixture-1"

    def make_provider(self):
        return FixtureProvider()

    def sample_audio(self):
        return b"fixture words"

    def test_unsupported_network_failure_classes_are_declared(self):
        self.assertFalse(self.network_failures_supported)


class OpenAIStubConformance(SpeechContract, unittest.TestCase):
    expected_provider, expected_model = "openai-compatible", "whisper-1"
    network_failures_supported = True

    def setUp(self):
        self.stub = _Stub()
        self.addCleanup(self.stub.close)

    def call_count(self):
        return self.stub.calls

    def make_provider(self):
        return WhisperHTTPProvider(f"http://127.0.0.1:{self.stub.server.server_port}/v1/audio/transcriptions", "secret-token", "whisper-1", timeout=5)

    def sample_audio(self):
        return b"m4a bytes"

    def test_contract_failure_retry_classes_and_redaction(self):
        audio = self.sample_audio()
        self.stub.body = b"vendor said secret-token"
        for status, retryable in ((400, False), (401, False), (408, True), (429, True), (500, True), (503, True)):
            self.stub.status = status
            with self.assertRaises(ProviderError) as ctx:
                self.make_provider().transcribe(audio, hashlib.sha256(audio).hexdigest())
            self.assertEqual(ctx.exception.retryable, retryable, status)
            self.assertNotIn("secret", str(ctx.exception))


@unittest.skipUnless(os.getenv("SPEAKUP_CONFORMANCE_WHISPER_URL") and os.getenv("SPEAKUP_CONFORMANCE_AUDIO"),
                     "local whisper.cpp server not configured; this adapter is NOT counted as passing")
class WhisperCppLocalConformance(SpeechContract, unittest.TestCase):
    expected_provider = "openai-compatible"
    expected_model = os.getenv("SPEAKUP_CONFORMANCE_MODEL", "whisper.cpp")

    def make_provider(self):
        # whisper-server ignores the model field; the label is our provenance, set by config.
        return WhisperHTTPProvider(os.environ["SPEAKUP_CONFORMANCE_WHISPER_URL"], "local-no-auth", self.expected_model, timeout=120)

    def sample_audio(self):
        return Path(os.environ["SPEAKUP_CONFORMANCE_AUDIO"]).read_bytes()


if __name__ == "__main__":
    unittest.main()
