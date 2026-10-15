#!/usr/bin/env python3
"""Inject one upload retry for the Android loopback test harness.
Forward API traffic to the local service used by test_android_backend.sh.
A locked fault claim keeps simultaneous uploads from failing twice."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
from threading import Lock
import os

TARGET_PORT = int(os.environ.get("SPEAKUP_PROXY_TARGET_PORT", "18081"))
faults = 0
fault_lock = Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/__fault_state":
            with fault_lock:
                body = str(faults).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.forward()

    def do_POST(self):
        self.forward()

    def do_PUT(self):
        global faults
        inject = False
        if self.path.startswith("/v1/uploads/"):
            with fault_lock:
                if faults == 0:
                    faults = 1
                    inject = True
        if inject:
            self.send_error(503, "injected one-shot upload interruption")
            return
        self.forward()

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {key: value for key, value in self.headers.items() if key.lower() not in {"host", "connection"}}
        upstream = HTTPConnection("127.0.0.1", TARGET_PORT, timeout=30)
        try:
            upstream.request(self.command, self.path, body, headers)
            response = upstream.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"content-length", "transfer-encoding", "connection", "date", "server"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            upstream.close()

    def log_message(self, _format, *_args):
        pass


def main():
    ThreadingHTTPServer(("127.0.0.1", 18080), Handler).serve_forever()


if __name__ == "__main__":
    main()
