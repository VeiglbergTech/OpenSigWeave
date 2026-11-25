"""Tests for the benchmark's measurement, safety and non-empty result checks."""
import asyncio
import json
import threading
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import build_opener
import httpx2 as httpx
from scripts.benchmark_engine import (
    NoRedirect, fetch_signature, fetch_signature_async, main, percentile, run_stage, summarize, validate_target,
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
        self.assertEqual(report["requests_per_second"], 1)
        self.assertEqual(report["timeouts"], 0)
        self.assertEqual(report["http_status_codes"], {})

    def test_async_client_reports_http_status_codes(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_address[1])
            async def request_once():
                async with httpx.AsyncClient(trust_env=False) as client:
                    return await fetch_signature_async(
                        client, origin, "user0000@bench-static.example.test",
                        "wrong", "BENCHMARK_SIGNATURE",
                    )
            sample = asyncio.run(request_once())
            self.assertEqual(sample[2:], ("http_401", 401))
            report = summarize(1, [sample], 1.0)
            self.assertEqual(report["http_status_codes"], {"401": 1})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

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

    def test_failed_stages_write_complete_report_and_strict_mode_is_opt_in(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_address[1])
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "failed.json"
                options = ["--base-url", origin, "--domain", "bench-static.example.test",
                           "--levels", "1,2", "--requests-per-level", "2",
                           "--warmup", "0", "--marker", "NOT_PRESENT",
                           "--output", str(output)]
                with patch.dict("os.environ", {"ENGINE_API_KEY": "a" * 40}):
                    main(options)
                    data = json.loads(output.read_text())
                    self.assertEqual(len(data["stages"]), 2)
                    self.assertEqual([stage["failed"] for stage in data["stages"]], [2, 2])
                    with self.assertRaises(SystemExit):
                        main(options + ["--fail-on-errors"])
                    self.assertTrue(output.is_file())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
