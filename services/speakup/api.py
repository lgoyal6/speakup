import hashlib
import hmac
import json
import os
import time
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from .domain import RecordingState
from .store import Store, now


def authenticate(authorization: str, expected_digest: str) -> bool:
    if not authorization.startswith("Bearer "):
        return False
    token = authorization.removeprefix("Bearer ").strip()
    if not token or len(expected_digest) != 64:
        return False
    actual = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(actual, expected_digest)


def _upload_signature(recording_id: str, user_id: str, expires: int) -> str:
    secret = os.getenv("SPEAKUP_UPLOAD_SECRET", "")
    if not secret:
        raise RuntimeError("SPEAKUP_UPLOAD_SECRET is not configured")
    message = f"{recording_id}:{user_id}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signed_upload_path(recording_id: str, user_id: str, expires: int) -> str:
    return "/v1/uploads/" + recording_id + "?" + urlencode(
        {"exp": expires, "sig": _upload_signature(recording_id, user_id, expires)}
    )


def verify_upload_signature(recording_id: str, user_id: str, expires: int, signature: str) -> bool:
    try:
        return hmac.compare_digest(_upload_signature(recording_id, user_id, expires), signature)
    except RuntimeError:
        return False


class Handler(BaseHTTPRequestHandler):
    store: Store  # Bound by the process entrypoint or an isolated test server.
    max_body = 64 * 1024

    def _json(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(raw)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
            raise ValueError("ambiguous request framing")
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length < 0 or length > self.max_body:
            raise ValueError("request body exceeds 64 KiB limit")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict): raise ValueError("JSON object required")
        return body

    def _user(self):
        config = json.loads(os.getenv("SPEAKUP_API_TOKENS", "{}"))
        if not config:
            digest = os.getenv("SPEAKUP_API_TOKEN_SHA256", "")
            configured_user = os.getenv("SPEAKUP_USER_ID", "")
            if digest and configured_user: config = {digest: configured_user}
        for digest, user in config.items():
            if user and authenticate(self.headers.get("Authorization", ""), digest): return user
        raise PermissionError("bearer authentication required")

    def do_POST(self):
        try:
            p = urlparse(self.path).path
            u = self._user()
            b = self._body()
            if p == "/v1/recordings":
                self._json(201, self.store.create_recording(u, recording_id=b.get("id")))
                return
            if p.startswith("/v1/recordings/"):
                rid = p.split("/")[3]
                if p.endswith("/transition"):
                    target = RecordingState(b["state"])
                    if target not in {RecordingState.LOCAL_READY, RecordingState.UPLOAD_RETRY}:
                        raise ValueError("use the upload, processing, or deletion endpoint for this state")
                    self._json(200, self.store.transition(u, rid, target))
                    return
                if p.endswith("/upload-url"):
                    state = RecordingState(self.store.get_recording(u, rid)["state"])
                    if state == RecordingState.LOCAL_DRAFT:
                        self.store.transition(u, rid, RecordingState.LOCAL_READY)
                    if state in {RecordingState.LOCAL_DRAFT, RecordingState.LOCAL_READY, RecordingState.UPLOAD_RETRY}:
                        self.store.transition(u, rid, RecordingState.UPLOADING)
                    if self.store.get_recording(u, rid)["state"] != RecordingState.UPLOADING:
                        raise ValueError("recording is not uploading")
                    expires = int(time.time()) + 900
                    self._json(200, {"url": signed_upload_path(rid, u, expires), "expires_in": 900, "method": "PUT"})
                    return
                if p.endswith("/complete"):
                    recording = self.store.get_recording(u, rid)
                    if not recording["audio_sha256"] or not recording["local_path"]: raise ValueError("upload bytes before completing")
                    self._json(200, self.store.transition(u, rid, RecordingState.UPLOADED))
                    return
                if p.endswith("/process"):
                    self._json(202, self.store.enqueue_job(u, rid))
                    return
                if p.endswith("/transcript"):
                    self._json(201, self.store.save_transcript(u, rid, b["text"], "user_edit", None))
                    return
                if p.endswith("/export"):
                    self._json(201, self.store.export(u, rid, b.get("format", "txt")))
                    return
                if p.endswith("/delete"):
                    self._json(200, self.store.delete(u, rid))
                    return
            if p == "/v1/sync":
                operation = dict(b)
                # A user cannot reuse another user's device or idempotency namespace.
                for field in ("device_id", "idempotency_key"):
                    if not isinstance(operation.get(field), str) or not operation[field]: raise ValueError(field + " required")
                    operation[field] = json.dumps([u, operation[field]])
                self._json(200, self.store.apply_sync(operation))
                return
            self._json(404, {"error": "not found"})
        except PermissionError as e:
            self._json(401, {"error": str(e)})
        except KeyError:
            self._json(404, {"error": "recording not found"})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception:
            self._json(500, {"error": "internal error"})

    def do_GET(self):
        try:
            p = urlparse(self.path).path
            if p == "/healthz":
                self._json(200, {"status": "ok"})
                return
            if p == "/metrics":
                queued = self.store.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('QUEUED','RETRYING','RUNNING')").fetchone()[0]
                body = f"speakup_jobs_inflight {queued}\n"
                raw = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            u = self._user()
            if p.startswith("/v1/recordings/") and p.endswith("/transcript"):
                self._json(200, self.store.get_transcript(u, p.split("/")[3]) or {})
                return
            if p.startswith("/v1/recordings/"):
                self._json(200, self.store.get_recording(u, p.split("/")[3]))
                return
            self._json(404, {"error": "not found"})
        except PermissionError as e:
            self._json(401, {"error": str(e)})
        except KeyError as e:
            self._json(404, {"error": str(e)})

    def do_PUT(self):
        try:
            parsed = urlparse(self.path)
            p = parsed.path
            u = self._user()
            if not p.startswith("/v1/uploads/"):
                self._json(404, {"error": "not found"})
                return
            rid = p.split("/")[3]
            query = parse_qs(parsed.query)
            expires = int(query.get("exp", ["0"])[0])
            signature = query.get("sig", [""])[0]
            if expires < int(time.time()) or not verify_upload_signature(rid, u, expires, signature):
                self._json(403, {"error": "upload URL is invalid or expired"})
                return
            recording = self.store.get_recording(u, rid)
            if recording["state"] != RecordingState.UPLOADING: raise ValueError("recording is not uploading")
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise ValueError("ambiguous request framing")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 50 * 1024 * 1024:
                self._json(413, {"error": "audio must be 1 byte to 50 MiB"})
                return
            data = self.rfile.read(length)
            if len(data) != length: raise ValueError("incomplete audio upload")
            digest = hashlib.sha256(data).hexdigest()
            audio_dir = Path(os.getenv("SPEAKUP_AUDIO_DIR", str(Path(self.store.path).parent / "audio")))
            audio_dir.mkdir(parents=True, exist_ok=True)
            path = audio_dir / (rid + ".audio")
            with self.store._transaction():
                current = self.store.get_recording(u, rid)
                if current["state"] != RecordingState.UPLOADING:
                    raise ValueError("recording is not uploading")
                if current["audio_sha256"] and current["audio_sha256"] != digest:
                    self._json(409, {"error": "recording already uploaded with different bytes"})
                    return
                with tempfile.NamedTemporaryFile(dir=audio_dir, delete=False) as audio:
                    temporary = Path(audio.name)
                    audio.write(data)
                    audio.flush()
                    os.fsync(audio.fileno())
                try:
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                self.store.db.execute("UPDATE recordings SET local_path=?,audio_sha256=?,updated_at=? WHERE id=? AND user_id=?", (str(path), digest, now(), rid, u))
            self._json(200, {"recording_id": rid, "bytes": len(data), "sha256": digest})
        except PermissionError as e:
            self._json(401, {"error": str(e)})
        except KeyError as e:
            self._json(404, {"error": str(e)})

        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception:
            self._json(500, {"error": "internal error"})

    def log_message(self, *args):
        pass


def main():
    Handler.store = Store(os.getenv("SPEAKUP_DB", "/tmp/speakup.db"))
    ThreadingHTTPServer((os.getenv("SPEAKUP_HOST", "127.0.0.1"), int(os.getenv("SPEAKUP_PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
