"""Adapter-independent monitoring entrypoint with an optional Rspamd probe.

Read-only, no Docker socket, and no mail-server modification. The probe
checks registered symbols by asking Rspamd's EXISTING controller endpoint.
It cannot prove actual signature rewriting or enforce SMTP rejection.
"""
from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, Request
from urllib.error import URLError, HTTPError


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
    if not (ip.is_private or ip.is_loopback) or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
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
    """Parse both Rspamd /symbols layouts without mistaking group names for rules."""
    if depth > 5:
        return
    if isinstance(obj, list):
        for item in obj[:5000]:
            _find_symbols(item, output, depth + 1)
    elif isinstance(obj, dict):
        nested = [obj[key] for key in ("rules", "symbols") if key in obj]
        if nested:
            # Group objects have a "name", but that is not a registered symbol.
            for entries in nested:
                _find_symbols(entries, output, depth + 1)
            return
        value = obj.get("symbol") or obj.get("name")
        if isinstance(value, str) and value in MODULE_SYMBOLS:
            output.add(value)
        # Some controller versions use a top-level symbol-name mapping.
        if depth <= 1:
            for name in MODULE_SYMBOLS:
                if isinstance(obj.get(name), dict):
                    output.add(name)


def probe(server: str, url: str, *, fetch=None, password: str | None = None) -> dict[str, object]:
    """Module registration probe; never mistake registration for mail delivery."""
    if server not in {"mailcow_rspamd", "rspamd"}:
        return {"status": "unsupported", "detail": "No module registration probe for this adapter"}
    try:
        origin = validate_controller_url(url)
        if not origin:
            return {"status": "unconfigured", "detail": "Private Rspamd controller address missing"}
        if fetch is None:
            opener = build_opener(_NoRedirect(), HTTPSHandler())
            # Use only the controller's read-only password, never enable_password.
            controller_password = password if password is not None else os.environ.get(
                "OSW_RSPAMD_CONTROLLER_PASSWORD", ""
            )
            def fetch(resource: str) -> bytes:
                headers = {"Accept": "application/json"}
                if controller_password:
                    headers["Password"] = controller_password
                request = Request(resource, headers=headers, method="GET")
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
            return {"status": "unknown", "detail": "Controller response does not verify OpenSigWeave symbols"}
        missing = [symbol for symbol in MODULE_SYMBOLS if symbol not in found]
        return ({"status": "missing", "detail": "Missing symbols: " + ", ".join(missing)}
                if missing else
                {"status": "configured", "detail": "Both OpenSigWeave symbols are registered (not a mail delivery test)"})
    except HTTPError as exc:
        if exc.code in (401, 403):
            return {"status": "auth_required", "detail": "Rspamd read-only controller authentication required or refused"}
        return {"status": "unknown", "detail": "Controller returned HTTP " + str(exc.code)}
    except (ValueError, KeyError, TypeError, OSError, URLError) as exc:
        # Never return URLs, passwords or error response bodies in diagnostics.
        return {"status": "unknown", "detail": "Controller probe unavailable or returned invalid data"}


def adapter_instructions(server: str, engine_url: str = "") -> dict[str, object]:
    """Minimal mail-host setup; does not depend on OpenSigWeave's Docker location.

    The browser never receives an installed ENGINE_API_KEY. Download only the
    three necessary Rspamd Lua assets from a pinned, tested Git commit.
    """
    if server in {"mailcow_rspamd", "rspamd"}:
        mailcow = server == "mailcow_rspamd"
        if mailcow:
            commands = """cd /opt/mailcow-dockerized
OSW_REF=REPLACE_WITH_TESTED_COMMIT
install -d -m 0755 data/conf/rspamd/plugins.d data/conf/rspamd/lua
curl -fsSL "https://raw.githubusercontent.com/VeiglbergTech/OpenSigWeave/$OSW_REF/rspamd/opensigweave.lua" -o data/conf/rspamd/plugins.d/opensigweave.lua
curl -fsSL "https://raw.githubusercontent.com/VeiglbergTech/OpenSigWeave/$OSW_REF/rspamd/opensigweave_mime.lua" -o data/conf/rspamd/lua/opensigweave_mime.lua
curl -fsSL "https://raw.githubusercontent.com/VeiglbergTech/OpenSigWeave/$OSW_REF/rspamd/opensigweave_cid.lua" -o data/conf/rspamd/lua/opensigweave_cid.lua"""
            verify = "docker compose exec -T rspamd-mailcow rspamadm configtest && docker compose restart rspamd-mailcow"
        else:
            commands = "Install the three required Lua files from the tested repository revision in your Rspamd plugin/Lua directories; see docs/ADAPTERS.md."
            verify = "Validate the Rspamd configuration and reload its service using your distribution's tooling."
        endpoint = engine_url or "https://YOUR_OPENSIGWEAVE_HOST/api/v1/engine/signatures/"
        config = ('opensigweave {\n'
                  '    api_url = "' + endpoint + '";\n'
                  '    engine_api_key = "REPLACE_WITH_YOUR_ENGINE_API_KEY";\n'
                  '    failure_policy = "disabled";\n'
                  '}')
        return {
            "supported": True, "title": "Mailcow / Rspamd" if mailcow else "Rspamd / other MTA",
            "description": ("Copy the module files onto the MAIL SERVER host, regardless of "
                            "where OpenSigWeave runs. Add the configuration to the existing "
                            "rspamd.conf.local and then validate/restart. The HTTPS endpoint "
                            "must be reachable from Rspamd and the key must match ENGINE_API_KEY. "
                            "No published controller port, Docker socket or extra service is required."),
            "commands": commands, "config": config, "verify": verify,
        }
    if server == "stalwart":
        return {"supported": True, "title": "Stalwart",
                "description": "Configure the Stalwart DATA-stage hook using its Bearer token and the OpenSigWeave HTTPS URL described in docs/ADAPTERS.md. No Rspamd changes required.",
                "commands": "", "config": "", "verify": ""}
    return {"supported": False, "title": "Other mail server",
            "description": "Choose a supported adapter against the HTTPS engine API; see docs/ADAPTERS.md.",
            "commands": "", "config": "", "verify": ""}
