"""Adapter-authenticated activity accounting (not a signature/delivery test)."""
from datetime import datetime, timedelta, timezone
import logging
import threading
import time

from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError
from models import SystemSetupDB

log = logging.getLogger(__name__)
_lock = threading.Lock()
_next_attempt = {}
INTERVAL_SECONDS = 45


def reset_activity_cache():
    with _lock:
        _next_attempt.clear()


def connection_label(when, *, now=None):
    if when is None:
        return "Unverified"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = max(0, (current - when).total_seconds())
    if seconds <= 120:
        return "Active"
    if seconds < 3600:
        return f"Active {int(seconds // 60)}m"
    if seconds <= 28800:
        hours = int(seconds // 3600)
        minutes = int(seconds % 3600 // 60)
        return f"Active {hours}h" + (f" {minutes}m" if minutes else "")
    return "Unverified"


def record_verified_adapter_activity(session_factory, adapter, *, now=None, on_change=None):
    """Call only after route authentication and successful adapter processing.

    An in-process lock prevents redundant requests per worker, and a conditional
    SQL UPDATE prevents multiple workers from writing on every message.
    """
    servers = {"engine": ("mailcow_rspamd", "rspamd", "other"),
               "stalwart": ("stalwart",)}
    if adapter not in servers:
        return False
    clock = time.monotonic()
    with _lock:
        if clock < _next_attempt.get(adapter, 0):
            return False
        _next_attempt[adapter] = clock + INTERVAL_SECONDS
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    try:
        with session_factory() as session:
            stmt = (update(SystemSetupDB)
                .where(SystemSetupDB.id == 1)
                .where(SystemSetupDB.mail_server.in_(servers[adapter]))
                .where(or_(SystemSetupDB.last_authenticated_at.is_(None),
                           SystemSetupDB.last_authenticated_at <= current - timedelta(seconds=INTERVAL_SECONDS),
                           SystemSetupDB.setup_completed.is_(False)))
                .values(last_authenticated_at=current, setup_completed=True))
            result = session.execute(stmt)
            changed = bool(result.rowcount)
            session.commit()
        if changed and on_change is not None:
            try:
                on_change()
            except Exception:
                log.warning("Realtime mail status notification unavailable")
        if not changed:
            with _lock:
                _next_attempt[adapter] = clock + 5
        return changed
    except (SQLAlchemyError, OSError):
        with _lock:
            _next_attempt[adapter] = clock + 5
        log.warning("Adapter activity persistence unavailable; mail processing unaffected")
        return False
