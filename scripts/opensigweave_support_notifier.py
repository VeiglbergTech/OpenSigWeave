#!/usr/bin/env python3
"""Independent, durable, rate-limited support notifier for Rspamd OpenSigWeave events.

Run OUTSIDE the Mailcow SMTP/Signatur processing path. The notifier must use a
separate trusted SMTP relay with TLS, so it never generates recursive sign-off.
All records are metadata only: no mail body, From header or engine secret.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import os
from pathlib import Path
import re
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import sys
import time


WINDOW_SECONDS = 15 * 60
FAILURE_PATTERN = re.compile(
    r"OpenSigWeave failure_id=([a-fA-F0-9]{8,64}) reason=([A-Z0-9_]{3,80}) policy=(?:tempfail|fail_open)"
)
SUCCESS_PATTERN = re.compile(r"OpenSigWeave processing_success adapter=rspamd\b")
ADDRESS_PATTERN = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
DEFAULT_DATABASE = "/var/lib/opensigweave-alerts/alerts.sqlite3"


def connect_database(path: str) -> sqlite3.Connection:
    db_path = Path(path).expanduser()
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(db_path.parent, 0o700)
    conn = sqlite3.connect(str(db_path), timeout=15, isolation_level="DEFERRED")
    os.chmod(db_path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            reason TEXT PRIMARY KEY,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            last_id TEXT NOT NULL,
            failures INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_notified INTEGER
        );
        CREATE TABLE IF NOT EXISTS seen (
            failure_id TEXT PRIMARY KEY,
            observed_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reason TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('failure','recovery')),
            created_at INTEGER NOT NULL,
            delivered_at INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _queue_once(conn: sqlite3.Connection, reason: str, kind: str, now: int) -> None:
    pending = conn.execute(
        "SELECT id FROM outbox WHERE reason=? AND kind=? AND delivered_at IS NULL",
        (reason, kind),
    ).fetchone()
    if pending is None:
        conn.execute(
            "INSERT INTO outbox(reason, kind, created_at) VALUES(?, ?, ?)",
            (reason, kind, now),
        )


def record_failure(conn: sqlite3.Connection, failure_id: str, reason: str, now: int) -> bool:
    if not re.fullmatch(r"[a-fA-F0-9]{8,64}", failure_id) or not re.fullmatch(r"[A-Z0-9_]{3,80}", reason):
        return False
    with conn:
        already_seen = conn.execute(
            "SELECT 1 FROM seen WHERE failure_id=?", (failure_id,)
        ).fetchone()
        if already_seen:
            return False
        conn.execute(
            "INSERT INTO seen(failure_id, observed_at) VALUES(?, ?)",
            (failure_id, now),
        )
        row = conn.execute("SELECT * FROM incidents WHERE reason=?", (reason,)).fetchone()
        if row is None or not row["active"]:
            conn.execute(
                """INSERT INTO incidents(reason,first_seen,last_seen,last_id,failures,active,last_notified)
                   VALUES(?,?,?,?,1,1,NULL)
                   ON CONFLICT(reason) DO UPDATE SET
                   first_seen=excluded.first_seen,last_seen=excluded.last_seen,
                   last_id=excluded.last_id,failures=1,active=1,last_notified=NULL""",
                (reason, now, now, failure_id),
            )
        else:
            conn.execute(
                "UPDATE incidents SET last_seen=?,last_id=?,failures=failures+1 WHERE reason=?",
                (now, failure_id, reason),
            )
        current = conn.execute(
            "SELECT last_notified FROM incidents WHERE reason=?", (reason,)
        ).fetchone()
        if current["last_notified"] is None or now - current["last_notified"] >= WINDOW_SECONDS:
            _queue_once(conn, reason, "failure", now)
        # Bound persisted seen IDs without pruning IDs still inside the dedup window.
        conn.execute("DELETE FROM seen WHERE observed_at < ?", (now - 7 * 86400,))
    return True


def record_success(conn: sqlite3.Connection, now: int) -> None:
    """Recovery means a valid signature mutation was observed, not full MTA health."""
    with conn:
        for row in conn.execute("SELECT reason FROM incidents WHERE active=1").fetchall():
            conn.execute(
                "UPDATE incidents SET active=0,last_seen=? WHERE reason=?",
                (now, row["reason"]),
            )
            _queue_once(conn, row["reason"], "recovery", now)


def process_log_line(conn: sqlite3.Connection, line: str, now: int | None = None) -> bool:
    timestamp = int(time.time() if now is None else now)
    found = FAILURE_PATTERN.search(line)
    if found:
        return record_failure(conn, found.group(1), found.group(2), timestamp)
    if SUCCESS_PATTERN.search(line):
        record_success(conn, timestamp)
    return False


def _format_notification(incident: sqlite3.Row, kind: str, sender: str, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = recipient
    msg["From"] = sender
    msg["Subject"] = (
        "[OpenSigWeave] Signature delivery " + ("failure: " if kind == "failure" else "recovery observed: ")
        + incident["reason"]
    )
    fmt = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    status = (
        "A signed outgoing submission experienced a technical failure."
        if kind == "failure" else
        "At least one successful signature mutation was observed after the failures. "
        "This is NOT proof that all mailserver components have recovered."
    )
    msg.set_content(
        f"{status}\n"
        f"Adapter: Rspamd (Mailcow)\nError class: {incident['reason']}\n"
        f"First seen (UTC): {fmt(incident['first_seen'])}\n"
        f"Most recent event (UTC): {fmt(incident['last_seen'])}\n"
        f"Occurrence count: {incident['failures']}\n"
        f"Latest correlation ID: {incident['last_id']}\n"
        "No message contents or credentials were collected.\n"
    )
    return msg


def _smtp_sender(message: EmailMessage) -> None:
    host = os.environ.get("OSW_ALERT_SMTP_HOST", "").strip()
    port = int(os.environ.get("OSW_ALERT_SMTP_PORT", "587"))
    username = os.environ.get("OSW_ALERT_SMTP_USER", "")
    password = os.environ.get("OSW_ALERT_SMTP_PASSWORD", "")
    if not host or port not in (465, 587):
        raise ValueError("A separate TLS SMTP relay on port 465 or 587 is required")
    if bool(username) != bool(password):
        raise ValueError("SMTP credentials are incomplete")
    tls_context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10, context=tls_context) as relay:
            if username:
                relay.login(username, password)
            relay.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as relay:
            relay.ehlo()
            relay.starttls(context=tls_context)
            relay.ehlo()
            if username:
                relay.login(username, password)
            relay.send_message(message)


def flush_outbox(conn: sqlite3.Connection, send=_smtp_sender, now: int | None = None) -> int:
    timestamp = int(time.time() if now is None else now)
    recipient = os.environ.get("OSW_ALERT_SUPPORT_TO", "").strip()
    sender = os.environ.get("OSW_ALERT_FROM", "").strip()
    if not ADDRESS_PATTERN.fullmatch(recipient) or not ADDRESS_PATTERN.fullmatch(sender):
        raise ValueError("Configure valid OSW_ALERT_SUPPORT_TO and OSW_ALERT_FROM addresses")
    delivered = 0
    pending = conn.execute(
        "SELECT id,reason,kind FROM outbox WHERE delivered_at IS NULL ORDER BY id ASC LIMIT 100"
    ).fetchall()
    for job in pending:
        incident = conn.execute(
            "SELECT * FROM incidents WHERE reason=?", (job["reason"],)
        ).fetchone()
        if not incident:
            continue
        message = _format_notification(incident, job["kind"], sender, recipient)
        try:
            send(message)
        except (OSError, smtplib.SMTPException, TimeoutError, ValueError) as exc:
            with conn:
                conn.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=?", (job["id"],))
            # Never log relay passwords or a server-supplied SMTP error string.
            print(f"Support alert delivery failed ({type(exc).__name__}); retained in outbox", file=sys.stderr)
            continue
        with conn:
            conn.execute(
                "UPDATE outbox SET delivered_at=?,attempts=attempts+1 WHERE id=?",
                (timestamp, job["id"]),
            )
            if job["kind"] == "failure":
                conn.execute(
                    "UPDATE incidents SET last_notified=? WHERE reason=?",
                    (timestamp, job["reason"]),
                )
        delivered += 1
    return delivered


def check_rspamd_health(conn: sqlite3.Connection, container: str, now: int | None = None) -> None:
    """Generate an independent outage event when the Lua callback cannot run."""
    timestamp = int(time.time() if now is None else now)
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            text=True, capture_output=True, timeout=8, check=False,
        )
        online = result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        online = False
    if not online:
        bucket = timestamp // 300
        token = hashlib.sha256(f"{container}:{bucket}".encode()).hexdigest()[:32]
        record_failure(conn, token, "RSPAMD_UNAVAILABLE", timestamp)


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent OpenSigWeave support alert notifier")
    parser.add_argument("action", choices=("ingest", "flush", "health", "status"))
    parser.add_argument("--database", default=os.environ.get("OSW_ALERT_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--container", default="rspamd-mailcow-1")
    args = parser.parse_args()
    conn = connect_database(args.database)
    try:
        if args.action == "ingest":
            for line in sys.stdin:
                process_log_line(conn, line)
        elif args.action == "flush":
            print(f"Delivered {flush_outbox(conn)} support alert(s)")
        elif args.action == "health":
            check_rspamd_health(conn, args.container)
        else:
            rows = conn.execute(
                "SELECT reason,first_seen,last_seen,failures,active FROM incidents ORDER BY last_seen DESC LIMIT 50"
            ).fetchall()
            queued = conn.execute("SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL").fetchone()[0]
            for row in rows:
                print(f"{row['reason']}: count={row['failures']} active={bool(row['active'])} last={row['last_seen']}")
            print(f"Pending support alerts: {queued}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
