#!/usr/bin/env python3
"""One-shot fault proxy for SpeakUp Android HTTP-client campaigns.

The original single 503 remains for the final WorkManager retry test. The owned
instrumentation campaign controls one scoped fault at a time with /__fault_config.
"""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
from threading import Lock

TARGET_PORT = int(os.environ.get("SPEAKUP_PROXY_TARGET_PORT", "18081"))
legacy_faults = 0
legacy_enabled = True
matrix = {"plan": None, "injected": 0, "requests": 0, "last_mode": None, "events": [], "operations": []}
state_lock = Lock()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        if self.path == "/__fault_state":
            with state_lock: body = str(legacy_faults).encode()
            self.respond(200, body, "text/plain")
        elif self.path == "/__matrix_state":
            with state_lock: body = json.dumps({k:v for k,v in matrix.items() if k != "plan"}).encode()
            self.respond(200, body, "application/json")
        else: self.forward()

    def do_POST(self):
        if self.path == "/__fault_config":
            try:
                plan = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if "legacy_enabled" in plan:
                    global legacy_enabled
                    legacy_enabled = bool(plan["legacy_enabled"])
                    self.respond(200, b"{}", "application/json")
                    return
                if plan.get("clear_plan") is True:
                    with state_lock: matrix["plan"] = None
                    self.respond(200, b"{}", "application/json")
                    return
                if plan.get("phase") not in {"create", "ticket", "upload", "complete"}: raise ValueError("unsupported phase")
                if plan.get("mode") not in {"http_401", "http_429", "http_503", "drop_response", "partial_body"}: raise ValueError("unsupported fault mode")
                if plan["mode"] == "partial_body" and plan["phase"] != "upload": raise ValueError("partial_body is only valid for upload")
                if plan.get("phase") == "upload" and not plan.get("path", "").startswith("/v1/uploads/"): raise ValueError("upload path must be a signed upload URL path")
                with state_lock: matrix["plan"] = plan
                self.respond(200, b"{}", "application/json")
            except (ValueError, KeyError, TypeError) as error:
                self.respond(400, json.dumps({"error":str(error)}).encode(), "application/json")
            return
        self.forward()

    def do_PUT(self): self.forward()

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with state_lock:
            matrix["requests"] += 1
            if self.path == "/v1/recordings" or self.path.startswith("/v1/recordings/"):
                matrix["operations"].append({
                    "method": self.command,
                    "path": self.path,
                    "idempotency_key": self.headers.get("Idempotency-Key"),
                })
            plan = matrix["plan"]
            if plan and plan["mode"] == "partial_body" and self.command == "PUT" and self.path.split("?", 1)[0] == plan.get("path", "").split("?", 1)[0]:
                matrix["plan"] = None
                inject = "partial_body"
            elif plan and self._matches(plan, body):
                matrix["plan"] = None
                inject = plan["mode"]
            else: inject = None
            if inject:
                matrix["injected"] += 1
                matrix["last_mode"] = inject
                matrix["events"].append({"phase":plan["phase"],"mode":inject,"path":self.path})

        global legacy_faults
        if inject is None and self.path.startswith("/v1/uploads/") and legacy_enabled:
            with state_lock:
                if legacy_faults == 0:
                    legacy_faults = 1
                    inject = "legacy_503"
        if inject in {"http_401", "http_429", "http_503", "legacy_503"}:
            status = {"http_401":401,"http_429":429,"http_503":503,"legacy_503":503}[inject]
            self.respond(status, json.dumps({"error":"injected one-shot fault"}).encode(), "application/json")
            return
        headers = {k:v for k,v in self.headers.items() if k.lower() not in {"host","connection","content-length"}}
        if inject == "partial_body":
            try: self.forward_partial(body, headers)
            finally: self.drop_client()
            return
        status, response_headers, payload = self.upstream(self.command, self.path, body, headers)
        if inject == "drop_response":
            self.drop_client()
            return
        self.respond(status, payload, response_headers.get("Content-Type","application/json"), response_headers)

    def _matches(self, plan, body):
        phase = plan.get("phase")
        recording_id = plan.get("recording_id")
        if phase == "create":
            if self.command != "POST" or self.path != "/v1/recordings": return False
            try: return json.loads(body).get("id") == recording_id
            except (ValueError, TypeError, AttributeError): return False
        if phase == "ticket": return self.command == "POST" and self.path == f"/v1/recordings/{recording_id}/upload-url"
        if phase == "complete": return self.command == "POST" and self.path == f"/v1/recordings/{recording_id}/complete"
        return self.command == "PUT" and self.path.split("?", 1)[0] == plan.get("path", "").split("?", 1)[0]

    def upstream(self, method, path, body, headers):
        upstream = HTTPConnection("127.0.0.1", TARGET_PORT, timeout=30)
        try:
            upstream.request(method, path, body, headers)
            response = upstream.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally: upstream.close()

    def forward_partial(self, body, headers):
        if len(body) < 2: raise ValueError("partial body fixture requires at least two bytes")
        sock = socket.create_connection(("127.0.0.1", TARGET_PORT), timeout=10)
        try:
            lines = [f"PUT {self.path} HTTP/1.1", "Host: 127.0.0.1", f"Content-Length: {len(body)}"]
            lines += [f"{key}: {value}" for key,value in headers.items()]
            sock.sendall(("\r\n".join(lines)+"\r\n\r\n").encode()+body[:len(body)//2])
            sock.shutdown(socket.SHUT_WR)
            try: sock.recv(4096)
            except OSError: pass
        finally: sock.close()

    def respond(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key,value in (extra or {}).items():
            if key.lower() not in {"content-length","transfer-encoding","connection","date","server","content-type"}: self.send_header(key,value)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def drop_client(self):
        try: self.connection.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.connection.close()

    def log_message(self, _format, *_args): pass

def main(): ThreadingHTTPServer(("127.0.0.1",18080),Handler).serve_forever()

if __name__ == "__main__": main()
