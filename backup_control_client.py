"""Minimal Unix-socket client for the privileged backup operator.

No Docker socket, age identity or host filesystem paths are exposed to FastAPI.
"""
from __future__ import annotations

import json
import os
import socket
from typing import Any, Iterator


class BackupControlError(Exception):
    """The host backup operator refused or could not perform the request."""


def _exchange(action: str, **values: Any):
    path = os.environ.get("BACKUP_CONTROL_SOCKET", "").strip()
    if not path:
        raise BackupControlError("Host backup service is not configured")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(15)
    try:
        connection.connect(path)
        request = json.dumps({"action": action, **values}, separators=(",", ":")).encode("utf-8")
        if len(request) > 4096:
            raise BackupControlError("Backup request is too large")
        connection.sendall(request + b"\n")
        reader = connection.makefile("rb")
        header = reader.readline(8193)
        if not header or len(header) > 8192:
            raise BackupControlError("Invalid response from host backup service")
        result = json.loads(header)
        if not isinstance(result, dict):
            raise BackupControlError("Invalid host backup response")
        if not result.get("ok"):
            raise BackupControlError(str(result.get("error", "Backup operation was refused"))[:160])
        return connection, reader, result
    except (OSError, ValueError, TypeError) as exc:
        connection.close()
        raise BackupControlError("Host backup service is unavailable") from exc
    except BackupControlError:
        connection.close()
        raise


def backup_request(action: str, **values: Any) -> dict[str, Any]:
    connection, reader, response = _exchange(action, **values)
    try:
        return {key: value for key, value in response.items() if key != "ok"}
    finally:
        reader.close()
        connection.close()


def backup_download(name: str) -> tuple[int, Iterator[bytes]]:
    connection, reader, response = _exchange("download", name=name)
    length = response.get("size")
    if not isinstance(length, int) or length < 0:
        reader.close()
        connection.close()
        raise BackupControlError("Invalid backup download length")

    def stream() -> Iterator[bytes]:
        remaining = length
        try:
            while remaining:
                block = reader.read(min(1024 * 1024, remaining))
                if not block:
                    raise BackupControlError("Backup download was interrupted")
                remaining -= len(block)
                yield block
        finally:
            reader.close()
            connection.close()

    return length, stream()


def backup_managed_download(run_id: str) -> tuple[int, Iterator[bytes]]:
    """Stream verified ciphertext from the root-side managed spool."""
    connection, reader, response = _exchange("managed-download", run_id=run_id)
    length = response.get("size")
    if type(length) is not int or length <= 0:
        reader.close()
        connection.close()
        raise BackupControlError("Invalid managed archive download length")

    def stream() -> Iterator[bytes]:
        remaining = length
        try:
            while remaining:
                data = reader.read(min(1024 * 1024, remaining))
                if not data:
                    raise BackupControlError("Encrypted archive transfer was interrupted")
                remaining -= len(data)
                yield data
        finally:
            reader.close()
            connection.close()

    return length, stream()
