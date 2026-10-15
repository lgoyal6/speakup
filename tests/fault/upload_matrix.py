"""Deterministic offline/fault matrix for the SpeakUp sync protocol.

A reference device client keeps all durable state in its own SQLite file, the
same facts the Android Room row holds, and talks to the real HTTP handler over
loopback sockets. Faults are injected at named request points. A "process death"
discards the client object and reopens it from disk. The server worker keeps
running while the device is dead.

Two client protocols run over identical scenarios:
  resumable: Content-Range chunks from the server offset, base_revision and
             idempotency_key on edits (docs/decisions/0003).
  baseline:  the original contract: whole-body PUT on every attempt, edits with
             no base revision or key. The create call is included so the arm can
             run at all (the original Android worker never created the row).

This module only imports names that exist in the baseline commit too, so the
same scenarios can run against the baseline server code via PYTHONPATH.
"""
import hashlib
import http.client
import json
import os
import random
import socket
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from speakup.api import Handler
from speakup.processor import process_next
from speakup.provider import FixtureProvider, Transcript
from speakup.store import Store

CHUNK = 256 * 1024
MAX_ATTEMPTS = 60
KINDS = ("none", "kill_record", "kill_upload", "kill_complete", "kill_process", "kill_edit", "airplane",
         "partial_bytes", "token_expired", "disk_full", "server_restart", "delete_during_transcription", "stale_edit")


class ProcessDeath(Exception):
    pass


class MatrixProvider(FixtureProvider):
    """Short deterministic output for synthetic binary audio, independent of size."""

    def transcribe(self, audio: bytes, input_sha256: str) -> Transcript:
        return Transcript("synthetic transcript " + input_sha256[:16], "matrix-fixture", "matrix-1")


class HttpStatus(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}")
        self.status, self.body = status, body


class Server:
    """The real Handler and Store, restartable on the same database file."""

    def __init__(self, root: Path):
        self.root = root
        self.tokens = {"token-1": "alice"}
        self.env = patch.dict("os.environ", {"SPEAKUP_UPLOAD_SECRET": "matrix-secret", "SPEAKUP_AUDIO_DIR": str(root / "audio")})
        self.env.start()
        self._apply_tokens()
        self.start()

    def _apply_tokens(self):
        os.environ["SPEAKUP_API_TOKENS"] = json.dumps({hashlib.sha256(t.encode()).hexdigest(): u for t, u in self.tokens.items()})

    def rotate_token(self, new_token):
        self.tokens = {new_token: "alice"}
        self._apply_tokens()

    def start(self):
        self.store = Store(str(self.root / "server.db"))
        handler = type("MatrixHandler", (Handler,), {"store": self.store})
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.store.db.close()

    def restart(self):
        self.stop()
        self.start()

    def drain_worker(self):
        while True:
            job = process_next(self.store, MatrixProvider())
            if job is None or job["status"] not in {"SUCCEEDED", "RETRYING"}:
                return
            if job["status"] == "RETRYING":
                return

    def close(self):
        self.stop()
        self.env.stop()


class Faults:
    """Named fault points: pre:<step> fires before sending, post:<step> after the response."""

    def __init__(self):
        self.armed, self.seen, self.net_down = {}, {}, 0
        self.fired = []

    def arm(self, point, occurrence, action):
        self.armed[(point, occurrence)] = action

    def hit(self, point):
        self.seen[point] = self.seen.get(point, 0) + 1
        action = self.armed.pop((point, self.seen[point]), None)
        if action:
            self.fired.append((point, self.seen[point], action))
        return action


class Net:
    def __init__(self, server: Server, faults: Faults, stats: dict):
        self.server, self.faults, self.stats = server, faults, stats
        self.token = "token-1"
        self.disk_full_next = False

    def call(self, step, method, path, body=None, headers=None):
        action = self.faults.hit("pre:" + step)
        if action == "kill":
            raise ProcessDeath(step)
        if action and action.startswith("net_down:"):
            self.faults.net_down = int(action.split(":")[1])
        if action == "token":
            self.server.rotate_token("token-2")
        if action == "restart":
            self.server.restart()
        if action == "diskfull":
            self.disk_full_next = True
        if self.faults.net_down > 0:
            self.faults.net_down -= 1
            self.stats["network_failures"] += 1
            raise ConnectionRefusedError("airplane mode")
        hdr = {"Authorization": "Bearer " + self.token, **(headers or {})}
        raw = body
        if isinstance(body, dict):
            raw = json.dumps(body).encode()
            hdr["Content-Type"] = "application/json"
        self.stats["requests"] += 1
        if method == "PUT":
            self.stats["put_bytes_sent"] += len(raw or b"")
        if action == "partial":
            self._partial(method, path, raw, hdr)
            self.stats["partial_puts"] += 1
            raise ConnectionResetError("connection dropped mid-body")
        status, payload = self._send(method, path, raw, hdr)
        if self.faults.hit("post:" + step) == "kill":
            raise ProcessDeath(step)
        if status >= 300:
            raise HttpStatus(status, payload)
        return payload

    def _send(self, method, path, raw, hdr):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        try:
            if self.disk_full_next and method == "PUT":
                self.disk_full_next = False
                real = os.fsync
                calls = {"n": 0}

                def failing_fsync(fd):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise OSError(28, "No space left on device")
                    return real(fd)
                with patch("os.fsync", failing_fsync):
                    conn.request(method, path, raw, hdr)
                    response = conn.getresponse()
                    data = response.read()
            else:
                conn.request(method, path, raw, hdr)
                response = conn.getresponse()
                data = response.read()
            try:
                payload = json.loads(data) if data else {}
            except ValueError:
                payload = {}
            return response.status, payload
        finally:
            conn.close()

    def _partial(self, method, path, raw, hdr):
        # Announce the full length, send half, then drop the connection.
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=10)
        try:
            lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1", f"Content-Length: {len(raw)}"]
            lines += [f"{k}: {v}" for k, v in hdr.items()]
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + raw[: len(raw) // 2])
            sock.shutdown(socket.SHUT_WR)
            try:
                sock.recv(4096)
            except OSError:
                pass
        finally:
            sock.close()


class Device:
    """Reference client. Every fact it needs after a restart lives in device.db."""

    def __init__(self, root: Path, net: Net, protocol: str):
        self.root, self.net, self.protocol = root, net, protocol
        self.db = sqlite3.connect(str(root / "device.db"))
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,state TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL DEFAULT 0,sha256 TEXT,op INTEGER NOT NULL,server_revision INTEGER,text TEXT,pending_edit TEXT,edit_key TEXT,auth_required INTEGER NOT NULL DEFAULT 0)")
        self.db.commit()

    def close(self):
        self.db.close()

    def row(self, rid):
        return self.db.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()

    def _set(self, rid, **fields):
        self.db.execute("UPDATE recordings SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", (*fields.values(), rid))
        self.db.commit()

    def recover(self):
        # Mirrors MainActivity: a draft that was recording when the process died is kept for review.
        self.db.execute("UPDATE recordings SET state='INTERRUPTED' WHERE state='LOCAL_DRAFT'")
        self.db.commit()

    def capture(self, rid, audio: bytes, die_after: int | None = None):
        path = self.root / "recordings" / (rid + ".m4a")
        path.parent.mkdir(exist_ok=True)
        # Row first, then bytes: a death mid-capture leaves a recoverable draft.
        self.db.execute("INSERT INTO recordings(id,state,path,op) VALUES(?,?,?,1)", (rid, "LOCAL_DRAFT", str(path)))
        self.db.commit()
        with path.open("wb") as handle:
            written = 0
            for i in range(0, len(audio), 64 * 1024):
                block = audio[i: i + 64 * 1024]
                handle.write(block)
                written += len(block)
                if die_after is not None and written >= die_after:
                    handle.flush()
                    raise ProcessDeath("record")
            handle.flush()
            os.fsync(handle.fileno())
        self._set(rid, state="LOCAL_READY", size=len(audio), sha256=hashlib.sha256(audio).hexdigest())

    def edit(self, rid, text):
        row = self.row(rid)
        op = row["op"] + 1
        self._set(rid, state="EDIT_PENDING", pending_edit=text, op=op, edit_key=f"{rid}:edit:{op}")

    def delete(self, rid):
        row = self.row(rid)
        self._set(rid, state="DELETE_PENDING", op=row["op"] + 1)

    def sync(self, rid):
        row = self.row(rid)
        if row["auth_required"]:
            return "auth"
        try:
            return self._sync(row)
        except HttpStatus as error:
            if error.status == 401:
                self._set(rid, auth_required=1)
                return "auth"
            raise

    def _sync(self, row):
        rid = row["id"]
        state = row["state"]
        if state == "DELETE_PENDING":
            self.net.call("delete", "POST", f"/v1/recordings/{rid}/delete", {}, {"Idempotency-Key": f"{rid}:delete:{row['op']}"})
            self._set(rid, state="DELETED", text=None, pending_edit=None)
            return "done"
        if state in {"LOCAL_READY", "UPLOADING"}:
            self._set(rid, state="UPLOADING")
            body = {"id": rid, "size": row["size"], "sha256": row["sha256"]} if self.protocol == "resumable" else {"id": rid}
            self.net.call("create", "POST", "/v1/recordings", body)
            ticket = self.net.call("upload_url", "POST", f"/v1/recordings/{rid}/upload-url", {})
            if not ticket.get("complete"):
                self._upload(rid, row, ticket)
            self.net.call("complete", "POST", f"/v1/recordings/{rid}/complete", {})
            self._set(rid, state="UPLOADED")
            state = "UPLOADED"
        if state == "UPLOADED":
            self.net.call("process", "POST", f"/v1/recordings/{rid}/process", {})
            self._set(rid, state="PROCESSING")
            state = "PROCESSING"
        if state == "PROCESSING":
            transcript = self.net.call("transcript_get", "GET", f"/v1/recordings/{rid}/transcript")
            if not transcript.get("text") and transcript.get("revision") is None:
                return "waiting"
            self._set(rid, state="TRANSCRIBED", text=transcript["text"], server_revision=transcript["revision"])
            return "done"
        if state == "EDIT_PENDING":
            body = {"text": row["pending_edit"]}
            if self.protocol == "resumable":
                body.update(base_revision=row["server_revision"], idempotency_key=row["edit_key"])
            try:
                saved = self.net.call("edit", "POST", f"/v1/recordings/{rid}/transcript", body)
            except HttpStatus as error:
                if error.status == 409:
                    # Keep the local text for review instead of overwriting the newer server text.
                    self._set(rid, state="CONFLICT")
                    return "done"
                raise
            self._set(rid, state="EDITED", text=saved["text"], server_revision=saved["revision"], pending_edit=None)
            return "done"
        return "done"

    def _upload(self, rid, row, ticket):
        data = Path(row["path"]).read_bytes()
        url = ticket["url"]
        if self.protocol == "baseline":
            self.net.call("put", "PUT", url, data, {"Content-Type": "audio/mp4"})
            return
        offset = ticket.get("offset") or 0
        while offset < len(data):
            end = min(offset + CHUNK, len(data))
            try:
                result = self.net.call("put", "PUT", url, data[offset:end], {"Content-Type": "audio/mp4", "Content-Range": f"bytes {offset}-{end - 1}/{len(data)}"})
            except HttpStatus as error:
                if error.status in (409, 507) and isinstance(error.body.get("offset"), int):
                    if error.status == 507:
                        raise
                    offset = error.body["offset"]  # Server facts win.
                    continue
                raise
            offset = result["offset"]


def synthetic_audio(rng: random.Random, long: bool) -> bytes:
    size = rng.randrange(512 * 1024, 3 * 1024 * 1024) if long else rng.randrange(1024, 48 * 1024)
    return rng.randbytes(size)


def scenario_spec(index: int):
    kind = KINDS[index % len(KINDS)]
    long = (index // len(KINDS)) % 2 == 1
    return {"index": index, "seed": 1000 + index, "kind": kind, "audio": "long" if long else "short"}


def run_scenario(spec, protocol: str, root: Path):
    rng = random.Random(spec["seed"])
    audio = synthetic_audio(rng, spec["audio"] == "long")
    chunks = max(1, -(-len(audio) // CHUNK))
    rid = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    stats = {"requests": 0, "put_bytes_sent": 0, "network_failures": 0, "partial_puts": 0, "process_deaths": 0,
             "sync_attempts": 0, "client_errors": 0, "auth_prompts": 0}
    faults = Faults()
    kind = spec["kind"]
    steps = ("create", "upload_url", "put", "complete", "process")
    if kind == "kill_upload":
        faults.arm("post:put", rng.randint(1, chunks) if protocol == "resumable" else 1, "kill")
    elif kind == "kill_complete":
        faults.arm("post:complete", 1, "kill")
    elif kind == "kill_process":
        faults.arm("post:process", 1, "kill")
    elif kind == "kill_edit":
        faults.arm("post:edit", 1, "kill")
    elif kind == "airplane":
        step = rng.choice(steps)
        occurrence = rng.randint(1, chunks) if step == "put" and protocol == "resumable" else 1
        faults.arm("pre:" + step, occurrence, f"net_down:{rng.randint(1, 4)}")
    elif kind == "partial_bytes":
        faults.arm("pre:put", rng.randint(1, chunks) if protocol == "resumable" else 1, "partial")
    elif kind == "token_expired":
        faults.arm("pre:" + rng.choice(steps), 1, "token")
    elif kind == "disk_full":
        faults.arm("pre:put", rng.randint(1, chunks) if protocol == "resumable" else 1, "diskfull")
    elif kind == "server_restart":
        faults.arm("pre:put", rng.randint(1, chunks) if protocol == "resumable" else 1, "restart")

    server = Server(root)
    net = Net(server, faults, stats)
    device = Device(root, net, protocol)
    outcome = {"converged": False}
    started = time.perf_counter()
    fault_at = None
    try:
        if kind == "kill_record":
            try:
                device.capture(rid, audio, die_after=rng.randrange(0, max(1, len(audio))))
            except ProcessDeath:
                stats["process_deaths"] += 1
                device.close()
                device = Device(root, net, protocol)
                device.recover()
            row = device.row(rid)
            server_row = server.store.db.execute("SELECT count(*) FROM recordings").fetchone()[0]
            outcome = {"converged": row is not None and row["state"] == "INTERRUPTED" and Path(row["path"]).exists() and server_row == 0,
                       "lost": row is None, "duplicates": server_row, "stale_overwrite": False, "final_client_state": row["state"] if row else None}
            return _record(spec, protocol, audio, stats, faults, outcome, started, started)
        device.capture(rid, audio)

        def drive(until_states, worker=True, max_attempts=MAX_ATTEMPTS):
            nonlocal device, fault_at
            for _ in range(max_attempts):
                if device.row(rid)["state"] in until_states:
                    return True
                stats["sync_attempts"] += 1
                try:
                    result = device.sync(rid)
                except ProcessDeath:
                    stats["process_deaths"] += 1
                    fault_at = fault_at or time.perf_counter()
                    device.close()
                    if worker:
                        server.drain_worker()  # The server keeps going while the phone is dead.
                    device = Device(root, net, protocol)
                    device.recover()
                    net.disk_full_next = False
                    continue
                except (ConnectionError, OSError):
                    fault_at = fault_at or time.perf_counter()
                    continue
                except HttpStatus as error:
                    fault_at = fault_at or time.perf_counter()
                    if error.status < 500:
                        stats["client_errors"] += 1
                    continue
                if result == "auth":
                    fault_at = fault_at or time.perf_counter()
                    stats["auth_prompts"] += 1
                    net.token = "token-2"  # The user signs in again; work resumes with the same keys.
                    device._set(rid, auth_required=0)
                    continue
                if result == "waiting" and worker:
                    server.drain_worker()
            return device.row(rid)["state"] in until_states

        if kind == "delete_during_transcription":
            reached = drive({"PROCESSING"}, worker=False)
            job = server.store.claim_next_job(lease_seconds=60)
            device.delete(rid)
            deleted = drive({"DELETED"}, worker=False)
            late = server.store.publish_transcript(job["id"], job["lease_token"], MatrixProvider().transcribe(audio, hashlib.sha256(audio).hexdigest()), hashlib.sha256(audio).hexdigest()) if job else None
            revisions = server.store.db.execute("SELECT count(*) FROM transcript_revisions WHERE recording_id=?", (rid,)).fetchone()[0]
            srv = server.store.db.execute("SELECT state FROM recordings WHERE id=?", (rid,)).fetchone()
            outcome = {"converged": bool(reached and deleted and late is None and revisions == 0 and srv and srv[0] == "DELETED"),
                       "lost": False, "duplicates": 0, "stale_overwrite": False, "final_client_state": device.row(rid)["state"]}
            return _record(spec, protocol, audio, stats, faults, outcome, started, fault_at)

        transcribed = drive({"TRANSCRIBED"})
        remote_text = None
        if kind == "stale_edit" and transcribed:
            # Another signed-in client edits first; this device is offline with an older base.
            remote_text = "remote reviewed text"
            remote = Net(server, Faults(), {"requests": 0, "put_bytes_sent": 0, "network_failures": 0, "partial_puts": 0})
            remote.token = net.token
            remote.call("remote_edit", "POST", f"/v1/recordings/{rid}/transcript", {"text": remote_text})
        local_text = f"reviewed transcript {spec['seed']}"
        if transcribed:
            device.edit(rid, local_text)
        edited = transcribed and drive({"EDITED", "CONFLICT"})
        outcome = _oracle(server, device, rid, audio, local_text, remote_text, transcribed and edited)
        return _record(spec, protocol, audio, stats, faults, outcome, started, fault_at)
    finally:
        device.close()
        server.close()


def _oracle(server, device, rid, audio, local_text, remote_text, finished):
    db = server.store.db
    srv = db.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
    row = device.row(rid)
    lost = srv is None or srv["audio_sha256"] != hashlib.sha256(audio).hexdigest() or not srv["local_path"] or Path(srv["local_path"]).read_bytes() != audio
    edits = db.execute("SELECT text FROM transcript_revisions WHERE recording_id=? AND source='user_edit'", (rid,)).fetchall()
    duplicates = sum(1 for e in edits if e[0] == local_text) - 1 if any(e[0] == local_text for e in edits) else 0
    duplicates += max(0, db.execute("SELECT count(*) FROM recordings").fetchone()[0] - 1)
    visible = None
    if srv is not None and not srv["deleted_at"]:
        visible = db.execute("SELECT text,revision FROM transcript_revisions WHERE recording_id=? ORDER BY (source='user_edit') DESC,revision DESC LIMIT 1", (rid,)).fetchone()
    stale_overwrite = remote_text is not None and visible is not None and visible[0] == local_text
    if remote_text is not None:
        converged = finished and row["state"] == "CONFLICT" and visible is not None and visible[0] == remote_text and row["pending_edit"] == local_text
    else:
        converged = finished and row["state"] == "EDITED" and visible is not None and visible[0] == local_text == row["text"] and visible[1] == row["server_revision"] and srv["state"] == "EDITED"
    return {"converged": bool(converged), "lost": bool(lost), "duplicates": duplicates, "stale_overwrite": bool(stale_overwrite),
            "final_client_state": row["state"], "final_server_state": srv["state"] if srv else None}


def _record(spec, protocol, audio, stats, faults, outcome, started, fault_at):
    ended = time.perf_counter()
    return {**spec, "protocol": protocol, "audio_bytes": len(audio), "audio_sha256": hashlib.sha256(audio).hexdigest(),
            "faults_fired": faults.fired, **stats, "bytes_retried": max(0, stats["put_bytes_sent"] - len(audio)),
            "elapsed_ms": round((ended - started) * 1000, 3),
            "recovery_ms": round((ended - fault_at) * 1000, 3) if fault_at else 0.0, **outcome}


def run_matrix(protocol: str, count: int = 100, indices=None):
    results = []
    for index in (indices if indices is not None else range(count)):
        with tempfile.TemporaryDirectory() as temp:
            results.append(run_scenario(scenario_spec(index), protocol, Path(temp)))
    return results


def summarize(results):
    def count(key):
        return sum(1 for r in results if r[key])
    by_kind = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], {"n": 0, "converged": 0, "lost": 0, "duplicates": 0, "stale_overwrite": 0, "bytes_retried": 0})
        k["n"] += 1
        k["converged"] += r["converged"]
        k["lost"] += r["lost"]
        k["duplicates"] += r["duplicates"]
        k["stale_overwrite"] += r["stale_overwrite"]
        k["bytes_retried"] += r["bytes_retried"]
    fired = [r for r in results if r["kind"] not in {"none", "kill_record", "delete_during_transcription", "stale_edit"}]
    return {"scenarios": len(results), "converged": count("converged"), "lost": count("lost"),
            "duplicates": sum(r["duplicates"] for r in results), "stale_overwrites": count("stale_overwrite"),
            "bytes_retried_total": sum(r["bytes_retried"] for r in results),
            "audio_bytes_total": sum(r["audio_bytes"] for r in results),
            "requests_total": sum(r["requests"] for r in results),
            "faults_armed_but_not_fired": sum(1 for r in fired if not r["faults_fired"]),
            "by_kind": by_kind}
