import hashlib
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from speakup.provider import WhisperHTTPProvider, ProviderError


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        owner = self
        self.received = None
        self.delay = 0
        self.status = 200
        self.body = json.dumps({"text": "spoken words", "provider": "spoof", "model": "spoof"}).encode()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.received = (self.headers, self.rfile.read(int(self.headers["Content-Length"])))
                time.sleep(owner.delay)
                self.send_response(owner.status)
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                try:
                    self.wfile.write(owner.body)
                except BrokenPipeError:
                    pass
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.provider = WhisperHTTPProvider(f"http://127.0.0.1:{self.server.server_port}/audio/transcriptions", "token", "whisper-1")
        self.audio = b"m4a test audio"
        self.digest = hashlib.sha256(self.audio).hexdigest()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_standard_multipart_file_model_and_response_format(self):
        result = self.provider.transcribe(self.audio, self.digest)
        headers, body = self.received
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(headers["Authorization"], "Bearer token")
        self.assertIn(b'name="file"; filename="recording.m4a"', body)
        self.assertIn(b'name="model"\r\n\r\nwhisper-1', body)
        self.assertIn(b'name="response_format"\r\n\r\njson', body)
        self.assertIn(self.audio, body)
        self.assertEqual((result.text, result.provider, result.model_version), ("spoken words", "openai-compatible", "whisper-1"))

    def test_oversized_and_non_text_responses_are_terminal(self):
        for body in (b'x' * ((1 << 20) + 1), b'[]', b'{"text": 4}', b'not json'):
            self.body = body
            with self.assertRaises(ProviderError) as ctx:
                self.provider.transcribe(self.audio, self.digest)
            self.assertFalse(ctx.exception.retryable)

    def test_http_failures_have_bounded_redacted_retry_class(self):
        self.body = b'secret token'
        for status, retryable in ((401, False), (429, True), (503, True), (302, False)):
            self.status = status
            with self.assertRaises(ProviderError) as ctx:
                self.provider.transcribe(self.audio, self.digest)
            self.assertEqual(ctx.exception.retryable, retryable)
            self.assertNotIn("secret", str(ctx.exception))

    def test_input_hash_and_unencrypted_remote_endpoint_are_rejected(self):
        with self.assertRaises(ProviderError):
            self.provider.transcribe(self.audio, "0" * 64)
        self.assertIsNone(self.received)
        with self.assertRaises(ValueError):
            WhisperHTTPProvider("http://example.com/transcriptions", "token")

    def test_provider_timeout_is_retryable(self):
        self.delay = 0.05
        self.provider.timeout = 0.01
        with self.assertRaises(ProviderError) as ctx:
            self.provider.transcribe(self.audio, self.digest)
        self.assertTrue(ctx.exception.retryable)
