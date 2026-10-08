import hashlib, json, sqlite3, threading, uuid
import time
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

    def _audit(self, user_id, recording_id, action, details=None):
        self.db.execute("INSERT INTO audit_events(user_id,recording_id,action,details,created_at) VALUES(?,?,?,?,?)", (user_id,recording_id,action,json.dumps(details or {}),now()))

    def create_recording(self, user_id: str, local_path: str | None = None, audio_sha256: str | None = None, recording_id: str | None = None) -> dict:
        if recording_id is not None:
            recording_id = str(uuid.UUID(recording_id))
        rid, ts = recording_id or str(uuid.uuid4()), now()
        with self._lock:
            self.db.execute("INSERT INTO recordings VALUES(?,?,?,?,?,?,?,NULL)", (rid,user_id,RecordingState.LOCAL_DRAFT,local_path,audio_sha256,ts,ts)); self._audit(user_id,rid,"recording_created"); self.db.commit()
        return self.get_recording(user_id,rid)

    def get_recording(self, user_id, rid):
        row = self.db.execute("SELECT * FROM recordings WHERE id=? AND user_id=?",(rid,user_id)).fetchone()
        if not row: raise KeyError("recording not found")
        return dict(row)

    def transition(self,user_id,rid,target: RecordingState):
        with self._lock:
            current = RecordingState(self.get_recording(user_id,rid)["state"])
            from .domain import Recording
            updated = Recording(rid,user_id,current).transition(target)
            self.db.execute("UPDATE recordings SET state=?,updated_at=? WHERE id=? AND user_id=?",(updated.state,now(),rid,user_id)); self._audit(user_id,rid,"state_changed",{"from":current,"to":target}); self.db.commit()
            return self.get_recording(user_id,rid)

    def enqueue_job(self,user_id,rid):
        self.get_recording(user_id,rid)
        with self._lock:
            row=self.db.execute("SELECT id,status FROM jobs WHERE recording_id=?",(rid,)).fetchone()
            if row: return dict(row)
            jid=str(uuid.uuid4()); self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",(jid,rid,"QUEUED",0,None,now())); self.db.commit(); return {"id":jid,"status":"QUEUED"}

    def claim_job(self, job_id: str):
        """Atomically claim one queued or retryable job; safe after worker restart."""
        with self._lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in {"QUEUED", "RETRYING"}:
                return dict(row) if row else None
            self.db.execute("UPDATE jobs SET status='RUNNING', attempts=attempts+1, updated_at=? WHERE id=?", (now(), job_id))
            self.db.commit()
            return dict(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim_next_job(self):
        with self._lock:
            row = self.db.execute("SELECT id FROM jobs WHERE status IN ('QUEUED','RETRYING') ORDER BY updated_at,id LIMIT 1").fetchone()
            return self.claim_job(row[0]) if row else None

    def get_job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recording_for_job(self, job_id):
        row = self.db.execute("SELECT r.* FROM recordings r JOIN jobs j ON j.recording_id=r.id WHERE j.id=?", (job_id,)).fetchone()
        if not row: raise KeyError("recording not found")
        return dict(row)

    def finish_job(self, job_id: str, success: bool, error: str | None = None):
        status = "SUCCEEDED" if success else "RETRYING"
        with self._lock:
            self.db.execute("UPDATE jobs SET status=?,last_error=?,updated_at=? WHERE id=?", (status, error, now(), job_id)); self.db.commit()
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def save_transcript(self,user_id,rid,text,source="provider",model_version="demo-1",input_sha256=None):
        self.get_recording(user_id,rid)
        output=hashlib.sha256(text.encode()).hexdigest()
        with self._lock:
            rev=self.db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM transcript_revisions WHERE recording_id=?",(rid,)).fetchone()[0]
            tid=str(uuid.uuid4()); self.db.execute("INSERT INTO transcript_revisions VALUES(?,?,?,?,?,?,?,?,?)",(tid,rid,rev,text,source,model_version,input_sha256,output,now())); self.db.commit(); return self.get_transcript(user_id,rid)

    def get_transcript(self,user_id,rid):
        self.get_recording(user_id,rid); row=self.db.execute("SELECT * FROM transcript_revisions WHERE recording_id=? ORDER BY revision DESC LIMIT 1",(rid,)).fetchone(); return dict(row) if row else None

    def export(self,user_id,rid,fmt="txt"):
        rec=self.get_recording(user_id,rid); transcript=self.get_transcript(user_id,rid)
        if not transcript: raise ValueError("transcript is not ready")
        content=transcript["text"] if fmt=="txt" else json.dumps({"recording_id":rid,"transcript":transcript["text"]})
        with self._lock:
            eid=str(uuid.uuid4()); self.db.execute("INSERT INTO exports VALUES(?,?,?,?,?)",(eid,rid,fmt,content,now())); self._audit(user_id,rid,"exported",{"format":fmt}); self.db.commit(); return {"id":eid,"recording_id":rid,"format":fmt,"content":content}

    def delete(self,user_id,rid):
        self.get_recording(user_id,rid)
        with self._lock:
            self.db.execute("UPDATE recordings SET state=?,deleted_at=?,updated_at=? WHERE id=? AND user_id=?",(RecordingState.DELETED,now(),now(),rid,user_id)); self._audit(user_id,rid,"deleted"); self.db.commit(); return self.get_recording(user_id,rid)

    def purge_deleted(self, retention_seconds: int) -> int:
        """Remove tombstoned data only after the configured retention window."""
        cutoff = datetime.fromtimestamp(time.time() - retention_seconds, timezone.utc).isoformat()
        with self._lock:
            rows = self.db.execute("SELECT id FROM recordings WHERE state=? AND deleted_at < ?", (RecordingState.DELETED, cutoff)).fetchall()
            for row in rows:
                recording = self.db.execute("SELECT local_path FROM recordings WHERE id=?", (row[0],)).fetchone()
                for table in ("transcript_revisions", "exports", "jobs"):
                    self.db.execute(f"DELETE FROM {table} WHERE recording_id=?", (row[0],))
                if recording and recording[0]: Path(recording[0]).unlink(missing_ok=True)
                self.db.execute("DELETE FROM recordings WHERE id=?", (row[0],))
            self.db.commit()
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
