import hashlib, json, sqlite3, threading, uuid
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .domain import RecordingState, validate_sync_operation

def now() -> str: return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, path: str = ":memory:"):
        self.path, self._lock = path, threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,state TEXT NOT NULL,local_path TEXT,audio_sha256 TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS transcript_revisions(id TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id),revision INTEGER NOT NULL,text TEXT NOT NULL,source TEXT NOT NULL,model_version TEXT,input_sha256 TEXT,output_sha256 TEXT,created_at TEXT NOT NULL,UNIQUE(recording_id,revision));
        CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id),format TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id),status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_ops(idempotency_key TEXT PRIMARY KEY,device_id TEXT NOT NULL,local_operation_id INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,server_cursor INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,recording_id TEXT,action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL);
        """)
        self.db.commit()
        # SQLite serializes this migration across API and worker startup.
        with self._transaction():
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
            for name, definition in (("lease_token", "TEXT"), ("lease_expires_at", "REAL"), ("next_attempt_at", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_job_per_recording ON jobs(recording_id)")

    @contextmanager
    def _transaction(self):
        # A Python lock protects one connection; BEGIN IMMEDIATE protects all processes.
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def _audit(self, user_id, recording_id, action, details=None):
        self.db.execute("INSERT INTO audit_events(user_id,recording_id,action,details,created_at) VALUES(?,?,?,?,?)", (user_id,recording_id,action,json.dumps(details or {}),now()))

    def create_recording(self, user_id: str, local_path: str | None = None, audio_sha256: str | None = None, recording_id: str | None = None) -> dict:
        if recording_id is not None:
            recording_id = str(uuid.UUID(recording_id))
        rid, ts = recording_id or str(uuid.uuid4()), now()
        with self._transaction():
            prior = self.db.execute("SELECT * FROM recordings WHERE id=?", (rid,)).fetchone()
            if prior:
                if prior["user_id"] != user_id or prior["deleted_at"]:
                    raise KeyError("recording not found")
                if prior["local_path"] != local_path or prior["audio_sha256"] != audio_sha256:
                    raise ValueError("recording id reused with different metadata")
                return dict(prior)
            self.db.execute("INSERT INTO recordings VALUES(?,?,?,?,?,?,?,NULL)", (rid,user_id,RecordingState.LOCAL_DRAFT,local_path,audio_sha256,ts,ts)); self._audit(user_id,rid,"recording_created")
        return self.get_recording(user_id,rid)

    def get_recording(self, user_id, rid):
        row = self.db.execute("SELECT * FROM recordings WHERE id=? AND user_id=?",(rid,user_id)).fetchone()
        if not row: raise KeyError("recording not found")
        return dict(row)

    def transition(self,user_id,rid,target: RecordingState):
        with self._transaction():
            current = RecordingState(self.get_recording(user_id,rid)["state"])
            from .domain import Recording
            updated = Recording(rid,user_id,current).transition(target)
            self.db.execute("UPDATE recordings SET state=?,updated_at=? WHERE id=? AND user_id=?",(updated.state,now(),rid,user_id)); self._audit(user_id,rid,"state_changed",{"from":current,"to":target})
            return self.get_recording(user_id,rid)

    def enqueue_job(self, user_id, rid):
        with self._transaction():
            recording = self.get_recording(user_id, rid)
            if recording["state"] not in {RecordingState.UPLOADED, RecordingState.PROCESSING}:
                raise ValueError("upload and complete audio before processing")
            row = self.db.execute("SELECT * FROM jobs WHERE recording_id=?", (rid,)).fetchone()
            if row:
                return dict(row)
            jid = str(uuid.uuid4())
            self.db.execute("INSERT INTO jobs(id,recording_id,status,attempts,last_error,updated_at) VALUES(?,?,'QUEUED',0,NULL,?)", (jid, rid, now()))
            self._audit(user_id, rid, "processing_queued", {"job_id": jid})
            return self.get_job(jid)

    def claim_job(self, job_id, *, lease_seconds=60, current_time=None):
        return self._claim(job_id, lease_seconds, current_time)

    def claim_next_job(self, *, lease_seconds=60, current_time=None):
        return self._claim(None, lease_seconds, current_time)

    def _claim(self, job_id, lease_seconds, current_time):
        if not 0 < lease_seconds <= 3600:
            raise ValueError("lease duration must be 1..3600 seconds")
        instant = time.time() if current_time is None else current_time
        token = str(uuid.uuid4())
        with self._transaction():
            self.db.execute("UPDATE jobs SET status='FAILED',lease_token=NULL,lease_expires_at=NULL,last_error='attempt budget exhausted',updated_at=? WHERE attempts>=3 AND (status IN ('QUEUED','RETRYING') OR (status='RUNNING' AND COALESCE(lease_expires_at,0)<=?))", (now(), instant))
            self.db.execute("UPDATE recordings SET state='FAILED',updated_at=? WHERE state='PROCESSING' AND id IN (SELECT recording_id FROM jobs WHERE status='FAILED')", (now(),))
            # SELECT and UPDATE share a database write lock, including across processes.
            row = self.db.execute("SELECT j.id FROM jobs j JOIN recordings r ON r.id=j.recording_id WHERE (? IS NULL OR j.id=?) AND r.state IN ('UPLOADED','PROCESSING') AND j.attempts<3 AND ((j.status IN ('QUEUED','RETRYING') AND j.next_attempt_at<=?) OR (j.status='RUNNING' AND COALESCE(j.lease_expires_at,0)<=?)) ORDER BY j.updated_at,j.id LIMIT 1", (job_id, job_id, instant, instant)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,lease_token=?,lease_expires_at=?,updated_at=? WHERE id=?", (token, instant + lease_seconds, now(), row[0]))
            job = self.get_job(row[0])
            self.db.execute("UPDATE recordings SET state='PROCESSING',updated_at=? WHERE id=?", (now(), job["recording_id"]))
            return job

    def get_job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recording_for_job(self, job_id):
        row = self.db.execute("SELECT r.* FROM recordings r JOIN jobs j ON j.recording_id=r.id WHERE j.id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("recording not found")
        return dict(row)

    def _active_job(self, job_id, token, instant):
        return self.db.execute("SELECT j.* FROM jobs j JOIN recordings r ON r.id=j.recording_id WHERE j.id=? AND j.lease_token=? AND j.status='RUNNING' AND j.lease_expires_at>? AND r.deleted_at IS NULL", (job_id, token, instant)).fetchone()

    def finish_job(self, job_id, success, error=None, *, lease_token, retryable=True, current_time=None):
        if success:
            raise ValueError("successful attempts must atomically publish a transcript")
        instant = time.time() if current_time is None else current_time
        with self._transaction():
            job = self._active_job(job_id, lease_token, instant)
            if not job:
                return None
            retry = retryable and job["attempts"] < 3
            status = "RETRYING" if retry else "FAILED"
            delay = 2 ** (job["attempts"] - 1)
            self.db.execute("UPDATE jobs SET status=?,last_error=?,next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?", (status, error, instant + delay, now(), job_id))
            if not retry:
                self.db.execute("UPDATE recordings SET state='FAILED',updated_at=? WHERE id=? AND state='PROCESSING'", (now(), job["recording_id"]))
            return self.get_job(job_id)

    def publish_transcript(self, job_id, lease_token, result, input_sha256, *, current_time=None):
        instant = time.time() if current_time is None else current_time
        with self._transaction():
            job = self._active_job(job_id, lease_token, instant)
            if not job:
                return None
            rec = self.recording_for_job(job_id)
            if input_sha256 != rec["audio_sha256"]:
                raise ValueError("processing input differs from uploaded audio")
            self._insert_transcript(rec["id"], result.text, result.provider, result.model_version, input_sha256)
            # The raw provider revision stays available, but never supersedes an edit.
            state = RecordingState.EDITED if self.db.execute("SELECT 1 FROM transcript_revisions WHERE recording_id=? AND source='user_edit'", (rec["id"],)).fetchone() else RecordingState.TRANSCRIBED
            if rec["state"] != RecordingState.EXPORTED:
                self.db.execute("UPDATE recordings SET state=?,updated_at=? WHERE id=?", (state, now(), rec["id"]))
            self.db.execute("UPDATE jobs SET status='SUCCEEDED',last_error=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?", (now(), job_id))
            self._audit(rec["user_id"], rec["id"], "transcription_published", {"job_id": job_id, "provider": result.provider, "model": result.model_version, "input_sha256": input_sha256})
            return self.get_job(job_id)

    def _insert_transcript(self, rid, text, source, model_version, input_sha256):
        if not isinstance(text, str) or len(text.encode()) > 1 << 20:
            raise ValueError("transcript text must be at most 1 MiB")
        output = hashlib.sha256(text.encode()).hexdigest()
        revision = self.db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM transcript_revisions WHERE recording_id=?", (rid,)).fetchone()[0]
        self.db.execute("INSERT INTO transcript_revisions VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), rid, revision, text, source, model_version, input_sha256, output, now()))

    def save_transcript(self, user_id, rid, text, source="provider", model_version="demo-1", input_sha256=None):
        with self._transaction():
            rec = self.get_recording(user_id, rid)
            if rec["deleted_at"]:
                raise ValueError("recording was deleted")
            if source == "user_edit" and self.get_transcript(user_id, rid) is None:
                raise ValueError("process audio before editing a transcript")
            self._insert_transcript(rid, text, source, model_version, input_sha256)
            if source == "user_edit":
                self.db.execute("UPDATE recordings SET state='EDITED',updated_at=? WHERE id=?", (now(), rid))
                self._audit(user_id, rid, "transcript_edited")
            return self.get_transcript(user_id, rid)

    def get_transcript(self, user_id, rid):
        rec = self.get_recording(user_id, rid)
        if rec["deleted_at"]:
            raise KeyError("recording not found")
        row = self.db.execute("SELECT * FROM transcript_revisions WHERE recording_id=? ORDER BY (source='user_edit') DESC,revision DESC LIMIT 1", (rid,)).fetchone()
        return dict(row) if row else None

    def export(self, user_id, rid, fmt="txt"):
        if fmt not in {"txt", "json"}:
            raise ValueError("export format must be txt or json")
        with self._transaction():
            transcript = self.get_transcript(user_id, rid)
            if not transcript:
                raise ValueError("transcript is not ready")
            content = transcript["text"] if fmt == "txt" else json.dumps({"recording_id": rid, "transcript": transcript["text"]})
            eid = str(uuid.uuid4())
            self.db.execute("INSERT INTO exports VALUES(?,?,?,?,?)", (eid, rid, fmt, content, now()))
            self.db.execute("UPDATE recordings SET state='EXPORTED',updated_at=? WHERE id=?", (now(), rid))
            self._audit(user_id, rid, "exported", {"format": fmt, "revision": transcript["revision"]})
            return {"id": eid, "recording_id": rid, "format": fmt, "content": content}

    def delete(self, user_id, rid):
        with self._transaction():
            self.get_recording(user_id, rid)
            self.db.execute("UPDATE recordings SET state=?,deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE id=? AND user_id=?", (RecordingState.DELETED, now(), now(), rid, user_id))
            self.db.execute("UPDATE jobs SET status='CANCELLED',lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE recording_id=? AND status IN ('QUEUED','RUNNING','RETRYING')", (now(), rid))
            self._audit(user_id, rid, "deleted")
            return self.get_recording(user_id, rid)

    def purge_deleted(self, retention_seconds: int) -> int:
        """Remove tombstoned data only after the configured retention window."""
        cutoff = datetime.fromtimestamp(time.time() - retention_seconds, timezone.utc).isoformat()
        with self._transaction():
            rows = self.db.execute("SELECT id FROM recordings WHERE state=? AND deleted_at < ?", (RecordingState.DELETED, cutoff)).fetchall()
            for row in rows:
                recording = self.db.execute("SELECT local_path FROM recordings WHERE id=?", (row[0],)).fetchone()
                for table in ("transcript_revisions", "exports", "jobs"):
                    self.db.execute(f"DELETE FROM {table} WHERE recording_id=?", (row[0],))
                if recording and recording[0]: Path(recording[0]).unlink(missing_ok=True)
                self.db.execute("DELETE FROM recordings WHERE id=?", (row[0],))
            return len(rows)

    def apply_sync(self,operation: dict):
        validate_sync_operation(operation)
        with self._lock:
            old=self.db.execute("SELECT server_cursor FROM sync_ops WHERE idempotency_key=?",(operation["idempotency_key"],)).fetchone()
            if old: return {"applied":False,"duplicate":True,"server_cursor":old[0]}
            latest=self.db.execute("SELECT MAX(local_operation_id) FROM sync_ops WHERE device_id=?",(operation["device_id"],)).fetchone()[0]
            if latest is not None and operation["local_operation_id"] <= latest:
                return {"applied":False,"duplicate":False,"conflict":"out_of_order","server_cursor":None}
            cursor=(self.db.execute("SELECT COALESCE(MAX(server_cursor),0)+1 FROM sync_ops").fetchone()[0]); self.db.execute("INSERT INTO sync_ops VALUES(?,?,?,?,?,?)",(operation["idempotency_key"],operation["device_id"],operation["local_operation_id"],operation["kind"],json.dumps(operation.get("payload",{})),cursor)); self.db.commit(); return {"applied":True,"duplicate":False,"server_cursor":cursor}
