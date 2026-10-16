"""Bounded public protocol endpoint. No caller files, paths or upstream URLs."""
from http.server import BaseHTTPRequestHandler
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("speakup_demo", Path(__file__).resolve().parents[1] / "public-demo/live.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class handler(BaseHTTPRequestHandler):
    def reply(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(405, {"error": "Use POST"})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                self.reply(413, {"error": "Input must contain 1 to 2048 bytes"})
                return
            result = demo.run_upload(json.loads(self.rfile.read(size)))
        except (ValueError, TypeError, KeyError):
            self.reply(400, {"error": "Invalid bounded upload inputs"})
            return
        except Exception:
            self.reply(500, {"error": "Protocol run failed; no result is available"})
            return
        self.reply(200, result)
