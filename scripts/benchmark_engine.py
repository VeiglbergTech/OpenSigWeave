#!/usr/bin/env python3
"""Bounded concurrency benchmark for the real signature engine HTTP API."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import os
from pathlib import Path
import platform
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def percentile(values, percent):
    if not values:
        return None
    sorted_values = sorted(values)
    index = (len(values) - 1) * percent / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return round(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (index - lower), 2)


def validate_target(origin, allow_remote=False):
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("An explicit http(s) origin is required")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("Supply an origin without credentials, path or query")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = parts.hostname.lower() == "localhost"
    if not loopback and (not allow_remote or parts.scheme != "https"):
        raise ValueError("Non-loopback load requires HTTPS and --allow-remote")
    return origin.rstrip("/")


def fetch_signature(opener, origin, sender, api_key, timeout, marker):
    url = origin + "/api/v1/engine/signatures/" + quote(sender, safe="")
    req = Request(url, headers={"X-Engine-Key": api_key, "Accept": "application/json"}, method="GET")
    start = time.perf_counter()
    reason = None
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
            status = response.status
        if status != 200:
            reason = "http_" + str(status)
        elif len(raw) > 1024 * 1024:
            reason = "response_too_large"
        else:
            obj = json.loads(raw)
            if not isinstance(obj, dict) or not isinstance(obj.get("html"), str) or marker not in obj["html"]:
                reason = "missing_signature"
    except HTTPError as exc:
        reason = "http_" + str(exc.code)
    except (OSError, URLError, TimeoutError):
        reason = "network_or_timeout"
    except (ValueError, UnicodeError, TypeError):
        reason = "invalid_json"
    return reason is None, (time.perf_counter() - start) * 1000, reason


def summarize(parallel, measurements, elapsed):
    latencies = [ms for success, ms, _ in measurements if success]
    errors = Counter(reason for success, _, reason in measurements if not success)
    return {
        "concurrency": parallel, "attempted": len(measurements),
        "successful": len(latencies), "failed": len(measurements) - len(latencies),
        "error_types": dict(sorted(errors.items())),
        "elapsed_seconds": round(elapsed, 3),
        "successful_requests_per_second": round(len(latencies) / max(elapsed, 1e-9), 2),
        "p50_ms": percentile(latencies, 50), "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "mean_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
    }


def run_stage(opener, origin, domain, senders, key, marker, parallel, attempts, timeout):
    def do_one(index):
        sender = "user" + str(index % senders).zfill(4) + "@" + domain
        return fetch_signature(opener, origin, sender, key, timeout, marker)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        measurements = list(pool.map(do_one, range(attempts)))
    return summarize(parallel, measurements, time.perf_counter() - start)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8085")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--sender-count", type=int, default=128)
    parser.add_argument("--levels", default="10,50,100,500")
    parser.add_argument("--requests-per-level", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--marker", default="BENCHMARK_SIGNATURE")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        origin = validate_target(args.base_url, args.allow_remote)
        levels = [int(value.strip()) for value in args.levels.split(",")]
        if not levels or any(level < 1 or level > 500 for level in levels):
            raise ValueError("Concurrency levels must be between 1 and 500")
        if not max(levels) <= args.requests_per_level <= 10000:
            raise ValueError("Requests per level must be >= peak concurrency and <= 10000")
        if not 1 <= args.sender_count <= 2000 or not 0 <= args.warmup <= 1000:
            raise ValueError("Sender count or warmup is out of range")
        if not .5 <= args.timeout <= 30:
            raise ValueError("Timeout must be between 0.5 and 30 seconds")
        if not args.domain.endswith((".test", ".invalid")) or "@" in args.domain:
            raise ValueError("Use a reserved .test or .invalid test domain")
        api_key = os.environ.get("ENGINE_API_KEY", "")
        if len(api_key) < 32:
            raise ValueError("ENGINE_API_KEY must be provided via environment")
    except ValueError as exc:
        parser.error(str(exc))

    opener = build_opener(NoRedirect())
    for i in range(args.warmup):
        sender = "user" + str(i % args.sender_count).zfill(4) + "@" + args.domain
        success, _, reason = fetch_signature(opener, origin, sender, api_key, args.timeout, args.marker)
        if not success:
            parser.error("Warmup failed: " + str(reason))
    stages = []
    for level in levels:
        stage = run_stage(opener, origin, args.domain, args.sender_count, api_key,
                          args.marker, level, args.requests_per_level, args.timeout)
        stages.append(stage)
        print("concurrency={concurrency} success={successful}/{attempted} "
              "rps={successful_requests_per_second} p50={p50_ms}ms "
              "p95={p95_ms}ms p99={p99_ms}ms errors={error_types}".format(**stage), flush=True)

    report = {
        "scope": "HTTP signature selection/rendering only; not SMTP email injection",
        "domain": args.domain, "distinct_senders": args.sender_count,
        "cpu_count_visible": os.cpu_count(), "platform": platform.platform(),
        "requests_per_level": args.requests_per_level, "timeout_seconds": args.timeout,
        "stages": stages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if any(stage["failed"] for stage in stages):
        raise SystemExit("Some requests failed; see JSON report")


if __name__ == "__main__":
    main()
