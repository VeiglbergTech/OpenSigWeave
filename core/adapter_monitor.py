"""Adapter-independent monitoring entrypoint with an optional Rspamd probe.

Read-only, no Docker socket, and no mail-server modification. The probe
checks registered symbols by asking Rspamd's EXISTING controller endpoint.
It cannot prove actual signature rewriting or enforce SMTP rejection.
"""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, Request
from urllib.error import URLError


MAIL_SERVERS = ("unconfigured", "mailcow_rspamd", "rspamd", "stalwart", "other")
MONITOR_MIN_SECONDS = 30
MONITOR_MAX_SECONDS = 3600
MODULE_SYMBOLS = ("OPENSIGWEAVE_INJECT", "OPENSIGWEAVE_GUARD")


def validate_controller_url(url: str) -> str:
    """Allow only private literal IP controllers; prevent SSRF and redirects."""
    url = url.strip()
    if not url:
        return ""
    if len(url) > 255:
        raise ValueError("Controller address is too long")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Controller URL must be an HTTP(S) URL without credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Controller URL must not include a path, query or fragment")
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        raise ValueError("Use a private numeric IP address for the Rspamd controller") from None
    if not (ip.is_private or ip.is_loopback) or ip.is_multicast or ip.is_unspecified:
        raise ValueError("Controller must use a private IP address")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Invalid controller port") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid controller port")
    return url.rstrip("/")


class _NoRedirect(HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):
        raise URLError("Controller redirect refused")
    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


def _find_symbols(obj, output: set[str], depth: int = 0) -> None:
    if depth > 4:
        return
    if isinstance(obj, dict):
        symbol = obj.get("name") or obj.get("symbol")
        if isinstance(symbol, str):
            output.add(symbol)
        if "symbols" in obj:
            _find_symbols(obj["symbols"], output, depth + 1)
        elif depth == 0:
            for name in obj.keys():
                if name in MODULE_SYMBOLS:
                    output.add(name)
    elif isinstance(obj, list):
        for item in obj[:5000]:
            _find_symbols(item, output, depth + 1)


def probe(server: str, url: str, *, fetch=None) -> dict[str, object]:
    """Return UNKNOWN on a failed or incompatible controller probe, never PASS."""
    if server not in {"mailcow_rspamd", "rspamd"}:
        return {"status": "unsupported", "detail": "No active probe for this adapter yet"}
    try:
        origin = validate_controller_url(url)
        if not origin:
            return {"status": "unconfigured", "detail": "Rspamd controller address missing"}
        if fetch is None:
            opener = build_opener(_NoRedirect(), HTTPSHandler())
            def fetch(resource: str) -> bytes:
                request = Request(resource, headers={"Accept": "application/json"},
                                  method="GET")
                with opener.open(request, timeout=3) as response:
                    if response.status != 200:
                        raise ValueError("Unexpected HTTP status")
                    return response.read(256 * 1024 + 1)
        data = fetch(origin + "/symbols")
        if len(data) > 256 * 1024:
            raise ValueError("Response exceeds probe size limit")
        payload = json.loads(data)
        found: set[str] = set()
        _find_symbols(payload, found)
        if not found:
            return {"status": "unknown", "detail": "Symbols response cannot verify module registration"}
        missing = [symbol for symbol in MODULE_SYMBOLS if symbol not in found]
        return ({"status": "missing", "detail": "Missing symbols: " + ", ".join(missing)}
                if missing else
                {"status": "configured", "detail": "Both OpenSigWeave symbols are configured"})
    except (ValueError, KeyError, TypeError, OSError, URLError) as exc:
        # Never report full URLs, error bodies or secrets in diagnostics.
        return {"status": "unknown", "detail": "Controller probe unavailable or refused"}


def adapter_instructions(server: str) -> dict[str, object]:
    """Server-side commands are a single terminal block per supported adapter."""
    if server == "mailcow_rspamd":
        return {"supported": True, "title": "Mailcow / Rspamd",
                "description": "Copy both Lua plugins and their two helpers onto the Mailcow host. Configure the existing Rspamd module with the OpenSigWeave engine URL and key; use failure_policy = enabled.",
                "commands": "cd /opt/mailcow-dockerized\n"
                    "install -m 0644 /opt/OpenSigWeave/rspamd/opensigweave.lua data/conf/rspamd/plugins.d/opensigweave.lua\n"
                    "install -m 0644 /opt/OpenSigWeave/rspamd/opensigweave_guard.lua data/conf/rspamd/plugins.d/opensigweave_guard.lua\n"
                    "install -m 0644 /opt/OpenSigWeave/rspamd/opensigweave_mime.lua data/conf/rspamd/lua/opensigweave_mime.lua\n"
                    "install -m 0644 /opt/OpenSigWeave/rspamd/opensigweave_cid.lua data/conf/rspamd/lua/opensigweave_cid.lua\n"
                    "docker compose exec -T rspamd-mailcow rspamadm configtest && docker compose restart rspamd-mailcow"}
    if server == "rspamd":
        return {"supported": True, "title": "Rspamd / other MTA",
                "description": "Deploy the four Lua files in the server's Rspamd directories. Configure the existing Rspamd module and connect the MTA's authenticated submission path to Rspamd Milter. Exact commands depend on your distribution.",
                "commands": ""}
    if server == "stalwart":
        return {"supported": True, "title": "Stalwart",
                "description": "Configure the Stalwart outgoing MTA hook and shared authentication token as described in docs/ADAPTERS.md. No Rspamd changes required.", "commands": ""}
    return {"supported": False, "title": "Other mail server",
            "description": "No supported adapter selected. Review docs/ADAPTERS.md before sending production email.", "commands": ""}
