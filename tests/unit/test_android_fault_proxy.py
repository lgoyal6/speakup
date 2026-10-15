"""Check the Android fault proxy using owned loopback HTTP servers.
The sync harness needs one injected failure and unchanged forwarded bodies.
A barrier exposes competing fault claims without relying on thread timing."""
import importlib.util
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

PROXY = Path(__file__).resolve().parents[2] / "scripts/android_fault_proxy.py"


def load_proxy():
    spec = importlib.util.spec_from_file_location("android_fault_proxy_under_test", PROXY)
    module = importlib.util.module_from_spec(spec)
    # The inherited module starts serving on import; intercept that constructor.
    with patch("http.server.ThreadingHTTPServer") as constructor:
        spec.loader.exec_module(module)
    return module, constructor


@contextmanager
def running(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError("owned loopback server did not stop")


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def forward(self):
        payload = b"forwarded:" + self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(201)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_PUT = forward
    do_POST = forward

    def log_message(self, *args):
        pass


def request(server, method, path, body=b"recording bytes"):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body, {"Content-Type": "application/octet-stream"})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()
    finally:
        connection.close()


class AndroidFaultProxyTests(unittest.TestCase):
    def test_import_does_not_start_a_server(self):
        _, constructor = load_proxy()
        constructor.assert_not_called()

    def test_main_binds_only_host_loopback(self):
        module, constructor = load_proxy()
        constructor.reset_mock()
        module.main()
        constructor.assert_called_once_with(("127.0.0.1", 18080), module.Handler)
        constructor.return_value.serve_forever.assert_called_once_with()

    def test_one_injected_503_then_successful_body_forwarding(self):
        module, _ = load_proxy()
        with running(Upstream) as upstream:
            module.TARGET_PORT = upstream.server_port
            with running(module.Handler) as proxy:
                self.assertEqual(request(proxy, "PUT", "/v1/uploads/first")[0], 503)
                status, headers, body = request(proxy, "PUT", "/v1/uploads/first", b"exact audio")
                self.assertEqual((status, body), (201, b"forwarded:exact audio"))
                self.assertEqual([v for k, v in headers if k.lower() == "content-type"], ["application/octet-stream"])
                self.assertEqual(request(proxy, "GET", "/__fault_state", b"")[2], b"1")

    def test_forwarded_response_has_exactly_one_correct_content_length(self):
        module, _ = load_proxy()
        with running(Upstream) as upstream:
            module.TARGET_PORT = upstream.server_port
            with running(module.Handler) as proxy:
                status, headers, body = request(proxy, "POST", "/v1/recordings", b"body")
                self.assertEqual(status, 201)
                self.assertEqual(body, b"forwarded:body")
                self.assertEqual([v for k, v in headers if k.lower() == "content-length"], [str(len(body))])

    def test_concurrent_uploads_claim_the_one_shot_fault_once(self):
        module, _ = load_proxy()
        barrier = threading.Barrier(2)
        class ScheduledPath(str):
            def startswith(self, prefix, *args):
                matches = super().startswith(prefix, *args)
                if matches and prefix == "/v1/uploads/":
                    barrier.wait(timeout=5)
                return matches
        class ScheduledHandler(module.Handler):
            def do_PUT(self):
                # Pause both requests after path inspection to expose a read/claim gap.
                self.path = ScheduledPath(self.path)
                super().do_PUT()
        with running(Upstream) as upstream:
            module.TARGET_PORT = upstream.server_port
            with running(ScheduledHandler) as proxy, ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(request, proxy, "PUT", "/v1/uploads/race", b"bytes") for _ in range(2)]
                results = [future.result(timeout=8) for future in futures]
                self.assertEqual(sorted(status for status, _, _ in results), [201, 503])
                self.assertEqual(next(body for status, _, body in results if status == 201), b"forwarded:bytes")
                self.assertEqual(request(proxy, "GET", "/__fault_state", b"")[2], b"1")


if __name__ == "__main__":
    unittest.main()
