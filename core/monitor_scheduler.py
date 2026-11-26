"""Read-only adapter monitor loop; no Docker socket or mail-server changes."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading

from core.adapter_monitor import probe
from models import SystemSetupDB

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread: threading.Thread | None = None


def check_once(session_factory, *, force: bool = False) -> dict[str, object] | None:
    with session_factory() as session:
        config = session.get(SystemSetupDB, 1)
        if config is None or (not config.monitoring_enabled and not force):
            return None
        now = datetime.now(timezone.utc)
        checked = config.last_monitored_at
        if checked is not None:
            checked = checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else checked
            if not force and (now - checked).total_seconds() < config.monitoring_interval:
                return None
        result = probe(config.mail_server, config.controller_url)
        config.last_monitor_status = str(result["status"])[:32]
        config.last_monitor_detail = str(result["detail"])[:255]
        config.last_monitored_at = now
        session.commit()
        return result


def start(session_factory) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop() -> None:
        # Simple periodic read-only checks. Multi-worker deployments can run
        # duplicate probes; future cluster deployments need leader election.
        while not _stop.wait(5):
            try:
                check_once(session_factory)
            except Exception as exc:
                log.warning("Adapter health probe could not run (%s)", type(exc).__name__)

    _thread = threading.Thread(target=loop, name="osw-mail-adapter-health",
                               daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=3)
