#!/usr/bin/env python3
"""Bounded concurrency benchmark for the real signature engine HTTP API."""
import argparse
import asyncio
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

import httpx2 as httpx


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
    # Each measurement is (ok, ms, reason[, HTTP status]). Keep legacy
    # three-element tuples compatible with the existing benchmark tests.
    latencies = [sample[1] for sample in measurements if sample[0]]
    errors = Counter(sample[2] for sample in measurements if not sample[0])
    statuses = Counter(str(sample[3]) for sample in measurements
                       if len(sample) > 3 and sample[3] is not None)
    return {
        "concurrency": parallel, "attempted": len(measurements),
        "successful": len(latencies), "failed": len(measurements) - len(latencies),
        "error_types": dict(sorted(errors.items())),
        "http_status_codes": dict(sorted(statuses.items())),
        "timeouts": sum(count for reason, count in errors.items() if "timeout" in reason),
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(measurements) / max(elapsed, 1e-9), 2),
        "successful_requests_per_second": round(len(latencies) / max(elapsed, 1e-9), 2),
        "p50_ms": percentile(latencies, 50), "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "mean_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
    }


async def fetch_signature_async(client, origin, sender, api_key, marker):
    """One HTTP request with separate timeout, transport and application errors."""
    url = origin + "/api/v1/engine/signatures/" + quote(sender, safe="")
    started = time.perf_counter()
    reason, status = None, None
    try:
        response = await client.get(url, headers={
            "X-Engine-Key": api_key, "Accept": "application/json",
        })
        status = response.status_code
        raw = response.content
        if status != 200:
            reason = "http_" + str(status)
        elif len(raw) > 1024 * 1024:
            reason = "response_too_large"
        else:
            obj = response.json()
            if not isinstance(obj, dict) or not isinstance(obj.get("html"), str) or marker not in obj["html"]:
                reason = "missing_signature"
    except httpx.PoolTimeout:
        reason = "client_pool_timeout"
    except httpx.ConnectTimeout:
        reason = "connect_timeout"
    except httpx.ReadTimeout:
        reason = "read_timeout"
    except httpx.WriteTimeout:
        reason = "write_timeout"
    except httpx.ConnectError:
        reason = "connect_error"
    except httpx.NetworkError:
        reason = "network_error"
    except httpx.HTTPError:
        reason = "http_client_error"
    except (ValueError, UnicodeError, TypeError):
        reason = "invalid_json"
    return reason is None, (time.perf_counter() - started) * 1000, reason, status


async def run_stage_async(origin, domain, senders, key, marker, parallel, attempts, timeout):
    # HTTPX has a connection pool, but also limit scheduled coroutines.
    # Otherwise 1000 queued tasks at concurrency=1 could time out in the
    # client pool and be misclassified as a server capacity failure.
    semaphore = asyncio.Semaphore(parallel)
    limits = httpx.Limits(
        max_connections=parallel,
        max_keepalive_connections=min(parallel, 100),
    )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        limits=limits,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        async def one(index):
            async with semaphore:
                sender = "user" + str(index % senders).zfill(4) + "@" + domain
                return await fetch_signature_async(client, origin, sender, key, marker)
        started = time.perf_counter()
        measurements = await asyncio.gather(*(one(index) for index in range(attempts)))
        return summarize(parallel, measurements, time.perf_counter() - started)

def run_stage(opener, origin, domain, senders, key, marker, parallel, attempts, timeout):
    # Retain the public call signature for existing unit tests. For measured
    # stages a bounded async HTTP client replaces thread-per-socket urllib.
    return asyncio.run(run_stage_async(
        origin, domain, senders, key, marker, parallel, attempts, timeout,
    ))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8085")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--sender-count", type=int, default=128)
    parser.add_argument("--levels", default="1,5,10,25,50,100,250,500")
    parser.add_argument("--requests-per-level", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--marker", default="BENCHMARK_SIGNATURE")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fail-on-errors", action="store_true",
                        help="Treat measured request failures as a nonzero process exit code")
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
        "client": "httpx2 AsyncClient, explicit per-stage keep-alive pool",
        "stages": stages,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failures = sum(stage["failed"] for stage in stages)
    if failures:
        print(f"WARNING: {failures} of {sum(stage['attempted'] for stage in stages)} requests failed; see JSON report", flush=True)
        if args.fail_on_errors:
            raise SystemExit("Request failures exceeded the strict zero-error threshold")


if __name__ == "__main__":
    main()
