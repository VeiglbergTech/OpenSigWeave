#!/usr/bin/env python3
"""Sample isolated benchmark containers and PostgreSQL contention (JSONL).

Run only on an explicitly disposable Docker Compose test stack. This sampler
reads diagnostics and never mutates data. Snapshots are not CPU/RAM peaks
between sampling instants.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time

running = True


def stop(*_):
    global running
    running = False


def command(args, timeout=8):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def sample():
    names = command(["docker", "compose", "ps", "-q", "opensigweave", "postgres"])
    ids = names.splitlines() if names else []
    resources = []
    if ids:
        payload = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids])
        if payload:
            for line in payload.splitlines():
                try:
                    resources.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    query = """
      SELECT json_build_object(
        'connections', COUNT(*),
        'active', COUNT(*) FILTER (WHERE state = 'active'),
        'idle_in_transaction', COUNT(*) FILTER (WHERE state = 'idle in transaction'),
        'lock_waiters', COUNT(*) FILTER (WHERE wait_event_type = 'Lock'),
        'io_waiters', COUNT(*) FILTER (WHERE wait_event_type = 'IO')
      )::text
      FROM pg_stat_activity WHERE datname = current_database();
    """
    postgres_user = os.environ.get("POSTGRES_USER", "opensigweave_benchmark")
    postgres_db = os.environ.get("POSTGRES_DB", "opensigweave_benchmark")
    db_raw = command([
        "docker", "compose", "exec", "-T", "postgres",
        "psql", "-X", "-t", "-A", "-U", postgres_user, "-d", postgres_db,
        "-c", query,
    ])
    try:
        database = json.loads(db_raw) if db_raw else None
    except json.JSONDecodeError:
        database = None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "docker_stats": resources,
        "postgres_activity": database,
        "configured_uvicorn_workers": int(os.environ.get("UVICORN_WORKERS", "1")),
        "configured_db_pool_size_per_worker": int(os.environ.get("DB_POOL_SIZE", "5")),
        "configured_db_max_overflow_per_worker": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.interval <= 30:
        parser.error("Interval must be 1..30 seconds")
    if os.environ.get("BENCHMARK_DISPOSABLE_DB") != "1":
        parser.error("Only a declared disposable benchmark instance may be sampled")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        while running:
            destination.write(json.dumps(sample()) + "\n")
            destination.flush()
            if running:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
