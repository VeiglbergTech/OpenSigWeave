"""Read-only, bounded support-alert snapshots from an independent notifier.

Never expose SMTP credentials, message bodies, or caller-supplied sender addresses.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

_MAX_BYTES = 64 * 1024
_REASON = re.compile(r"^[A-Z0-9_]{3,80}$")


def read_support_alert_status(path: str | None, *, now: int | None = None) -> dict:
    empty = {"active_count": 0, "pending_count": 0, "incidents": []}
    if not path:
        return {"status": "not_configured", **empty}
    try:
        target = Path(path)
        if not target.is_absolute() or target.is_symlink():
            raise ValueError("Unsafe alert status path")
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_BYTES:
                raise ValueError("Invalid status file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(_MAX_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > _MAX_BYTES:
            raise ValueError("Status file too large")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Invalid alert snapshot")
        updated = payload.get("generated_at")
        active = payload.get("active_count")
        pending = payload.get("pending_count")
        incidents = payload.get("incidents")
        if (type(updated) is not int or type(active) is not int or
                type(pending) is not int or not isinstance(incidents, list) or
                not 0 <= active <= 1000000 or not 0 <= pending <= 1000000 or
                len(incidents) > 10):
            raise ValueError("Invalid alert snapshot metadata")
        clean = []
        for item in incidents:
            if not isinstance(item, dict):
                raise ValueError("Invalid incident record")
            reason = item.get("reason")
            count = item.get("failures")
            active_flag = item.get("active")
            if (not isinstance(reason, str) or not _REASON.fullmatch(reason) or
                    type(count) is not int or not 0 <= count <= 1000000 or
                    type(active_flag) is not bool):
                raise ValueError("Invalid incident metadata")
            clean.append({"reason": reason, "failures": count, "active": active_flag})
        age = int(time.time() if now is None else now) - updated
        status = "stale" if age < -60 or age > 180 else "active" if active else "ok"
        return {"status": status, "active_count": active, "pending_count": pending,
                "incidents": clean, "generated_at": updated}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"status": "unavailable", **empty}
