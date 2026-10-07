import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .domain import RecordingState
from .store import Store

class Handler(BaseHTTPRequestHandler):
    store = Store(os.getenv("SPEAKUP_DB", "/tmp/speakup.db"))
    def _json(self, status, body):
        raw=json.dumps(body).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _body(self): return json.loads(self.rfile.read(int(self.headers.get("Content-Length",0)) or 0) or b"{}")
    def _user(self): return self.headers.get("X-User-Id") or "demo-user"
    def do_POST(self):
        try:
            p=urlparse(self.path).path; b=self._body(); u=self._user()
            if p=="/v1/recordings": self._json(201,self.store.create_recording(u,b.get("local_path"),b.get("audio_sha256"),b.get("id"))); return
            if p.startswith("/v1/recordings/"):
                rid=p.split("/")[3]
                if p.endswith("/transition"): self._json(200,self.store.transition(u,rid,RecordingState(b["state"]))); return
                if p.endswith("/upload-url"): self._json(200,{"url":f"/v1/uploads/{rid}","expires_in":900,"method":"PUT"}); return
                if p.endswith("/complete"): self._json(200,self.store.transition(u,rid,RecordingState.UPLOADED)); return
                if p.endswith("/process"): self._json(202,self.store.enqueue_job(u,rid)); return
                if p.endswith("/transcript"): self._json(201,self.store.save_transcript(u,rid,b["text"],"user_edit",None)); return
                if p.endswith("/export"): self._json(201,self.store.export(u,rid,b.get("format","txt"))); return
                if p.endswith("/delete"): self._json(200,self.store.delete(u,rid)); return
            if p=="/v1/sync": self._json(200,self.store.apply_sync(b)); return
            self._json(404,{"error":"not found"})
        except (KeyError,ValueError) as e: self._json(400,{"error":str(e)})
        except Exception as e: self._json(500,{"error":str(e)})
    def do_GET(self):
        try:
            p=urlparse(self.path).path; u=self._user()
            if p.startswith("/v1/recordings/") and p.endswith("/transcript"): self._json(200,self.store.get_transcript(u,p.split("/")[3]) or {}) ; return
            if p.startswith("/v1/recordings/"): self._json(200,self.store.get_recording(u,p.split("/")[3])); return
            self._json(404,{"error":"not found"})
        except KeyError as e: self._json(404,{"error":str(e)})
    def log_message(self,*args): pass

def main(): ThreadingHTTPServer((os.getenv("SPEAKUP_HOST","127.0.0.1"),int(os.getenv("SPEAKUP_PORT","8080"))),Handler).serve_forever()

if __name__ == "__main__":
    main()
