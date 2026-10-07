"""Security checks through real HTTP sockets and a restartable SQLite store."""
import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from speakup.api import Handler, signed_upload_path
from speakup.store import Store


class HttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        tokens = {hashlib.sha256(token.encode()).hexdigest(): user
                  for token, user in (("token-a", "alice"), ("token-b", "bob"))}
        self.env = patch.dict("os.environ", {"SPEAKUP_API_TOKENS": json.dumps(tokens),
            "SPEAKUP_UPLOAD_SECRET": "test-upload-secret", "SPEAKUP_AUDIO_DIR": str(self.path / "audio")})
        self.env.start()
        self.start()

    def start(self):
        self.store = Store(str(self.path / "state.db"))
        self.handler = type("TestHandler", (Handler,), {"store": self.store})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.db.close()

    def tearDown(self):
        self.stop()
        self.env.stop()
        self.temp.cleanup()

    def request(self, method, path, body=None, token="token-a", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            hdr = {"Authorization": "Bearer " + token, **(headers or {})}
            if isinstance(body, dict):
                body = json.dumps(body).encode()
                hdr["Content-Type"] = "application/json"
            conn.request(method, path, body, hdr)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def recording(self):
        status, record = self.request("POST", "/v1/recordings", {"local_path": "/etc/passwd", "audio_sha256": "spoof"})
        self.assertEqual(status, 201)
        self.assertIsNone(record["local_path"])
        self.assertIsNone(record["audio_sha256"])
        return record["id"]

    def upload(self, rid):
        status, result = self.request("POST", f"/v1/recordings/{rid}/upload-url", {})
        self.assertEqual(status, 200)
        return result["url"]

    def test_auth_and_every_recording_endpoint_are_user_scoped(self):
        rid = self.recording()
        for method, suffix, body in (("GET", "", None), ("GET", "/transcript", None),
            ("POST", "/transition", {"state": "LOCAL_READY"}), ("POST", "/upload-url", {}),
            ("POST", "/complete", {}), ("POST", "/process", {}),
            ("POST", "/transcript", {"text": "spoof"}), ("POST", "/export", {}), ("POST", "/delete", {})):
            with self.subTest(suffix=suffix):
                status, _ = self.request(method, f"/v1/recordings/{rid}{suffix}", body,
                                        token="token-b", headers={"X-User-Id": "alice"})
                self.assertEqual(status, 404)
        self.assertEqual(self.request("POST", "/v1/recordings", {}, token="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/v1/recordings", {"id": "../../bad"})[0], 400)

    def test_signed_upload_validation_size_retry_and_completion(self):
        rid = self.recording()
        url = self.upload(rid)
        self.assertEqual(self.request("PUT", url, b"audio", token="token-b")[0], 403)
        expired = signed_upload_path(rid, "alice", int(time.time()) - 1)
        self.assertEqual(self.request("PUT", expired, b"audio")[0], 403)
        self.assertEqual(self.request("PUT", url + "tampered", b"audio")[0], 403)
        # No body is sent: rejection must happen before attempting a blocking read.
        self.assertEqual(self.request("PUT", url, headers={"Content-Length": str(50 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.request("PUT", url, headers={"Content-Length": "garbage"})[0], 400)
        self.assertEqual(self.request("POST", f"/v1/recordings/{rid}/complete", {})[0], 400)
        self.assertEqual(self.request("POST", f"/v1/recordings/{rid}/transition", {"state": "UPLOADED"})[0], 400)
        self.assertEqual(self.request("PUT", url, b"hello audio")[0], 200)
        self.assertEqual(self.request("PUT", url, b"hello audio")[0], 200)
        self.assertEqual(self.request("PUT", url, b"different audio")[0], 409)
        self.assertEqual(self.request("POST", f"/v1/recordings/{rid}/complete", {})[0], 200)
        self.assertEqual(self.request("PUT", url, b"hello audio")[0], 400)

    def test_restart_preserves_upload_and_purge_removes_dependents(self):
        rid = self.recording()
        self.assertEqual(self.request("PUT", self.upload(rid), b"persistent audio")[0], 200)
        self.stop()
        self.start()
        record = self.request("GET", f"/v1/recordings/{rid}")[1]
        self.assertEqual(Path(record["local_path"]).read_bytes(), b"persistent audio")
        self.assertEqual(self.request("POST", f"/v1/recordings/{rid}/complete", {})[0], 200)
        self.request("POST", f"/v1/recordings/{rid}/transcript", {"text": "reviewed"})
        self.request("POST", f"/v1/recordings/{rid}/export", {})
        self.request("POST", f"/v1/recordings/{rid}/process", {})
        self.request("POST", f"/v1/recordings/{rid}/delete", {})
        self.store.db.execute("UPDATE recordings SET deleted_at='2000-01-01T00:00:00+00:00'")
        self.store.db.commit()
        self.assertEqual(self.store.purge_deleted(1), 1)
        self.assertFalse(Path(record["local_path"]).exists())
        self.assertEqual(self.request("GET", f"/v1/recordings/{rid}")[0], 404)

    def test_sync_idempotency_and_device_ordering_are_namespaced_per_user(self):
        op = {"device_id": "device", "idempotency_key": "same-key", "local_operation_id": 1, "kind": "create"}
        self.assertTrue(self.request("POST", "/v1/sync", op)[1]["applied"])
        self.assertTrue(self.request("POST", "/v1/sync", op)[1]["duplicate"])
        self.assertTrue(self.request("POST", "/v1/sync", op, token="token-b")[1]["applied"])
