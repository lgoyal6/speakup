import hashlib, json, sqlite3, threading, uuid
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

    def apply_sync(self,operation: dict):
        validate_sync_operation(operation)
        with self._lock:
            old=self.db.execute("SELECT server_cursor FROM sync_ops WHERE idempotency_key=?",(operation["idempotency_key"],)).fetchone()
            if old: return {"applied":False,"duplicate":True,"server_cursor":old[0]}
            cursor=(self.db.execute("SELECT COALESCE(MAX(server_cursor),0)+1 FROM sync_ops").fetchone()[0]); self.db.execute("INSERT INTO sync_ops VALUES(?,?,?,?,?,?)",(operation["idempotency_key"],operation["device_id"],operation["local_operation_id"],operation["kind"],json.dumps(operation.get("payload",{})),cursor)); self.db.commit(); return {"applied":True,"duplicate":False,"server_cursor":cursor}
