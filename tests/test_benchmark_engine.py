"""Tests for the benchmark's measurement, safety and non-empty result checks."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import build_opener
from scripts.benchmark_engine import (
    NoRedirect, fetch_signature, percentile, run_stage, summarize, validate_target,
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("X-Engine-Key") != "a" * 40:
            status, result = 401, {"detail": "unauthorized"}
        else:
            status, result = 200, {"html": "<p>BENCHMARK_SIGNATURE</p>"}
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BenchmarkEngineTests(unittest.TestCase):
    def test_statistics(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([10, 20, 30, 40, 50], 95), 48)
        report = summarize(2, [(True, 10, None), (False, 20, "http_503")], 2)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["error_types"], {"http_503": 1})
        self.assertEqual(report["successful_requests_per_second"], 0.5)

    def test_target_guard(self):
        self.assertEqual(validate_target("http://localhost:8085/"), "http://localhost:8085")
        with self.assertRaises(ValueError):
            validate_target("https://example.test")
        with self.assertRaises(ValueError):
            validate_target("http://example.test", allow_remote=True)
        with self.assertRaises(ValueError):
            validate_target("https://user:pass@example.test", allow_remote=True)
        self.assertEqual(validate_target("https://example.test", allow_remote=True),
                         "https://example.test")

    def test_concurrent_api_and_signature_validation(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_address[1])
            opener = build_opener(NoRedirect())
            result = run_stage(opener, origin, "bench-static.example.test", 4,
                               "a" * 40, "BENCHMARK_SIGNATURE", 5, 20, 3)
            self.assertEqual(result["successful"], 20)
            self.assertEqual(result["failed"], 0)
            success, _, reason = fetch_signature(opener, origin, "user0000@bench-static.example.test",
                                                 "wrong", 3, "BENCHMARK_SIGNATURE")
            self.assertFalse(success)
            self.assertEqual(reason, "http_401")
            success, _, reason = fetch_signature(opener, origin, "user0000@bench-static.example.test",
                                                 "a" * 40, 3, "MISSING")
            self.assertFalse(success)
            self.assertEqual(reason, "missing_signature")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
