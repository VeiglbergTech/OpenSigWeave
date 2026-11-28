"""Adapter-independent monitoring entrypoint with an optional Rspamd probe.

Read-only, no Docker socket, and no mail-server modification. The probe
checks registered symbols by asking Rspamd's EXISTING controller endpoint.
It cannot prove actual signature rewriting or enforce SMTP rejection.
"""
from __future__ import annotations

import ipaddress
import json
import os
import hashlib
import re
import shlex
from pathlib import Path
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
        # The independent guard is experimental and not required for a working
        # default adapter. Missing the injector is a real configuration failure.
        if "OPENSIGWEAVE_INJECT" not in found:
            return {"status": "missing", "detail": "Required OpenSigWeave injector symbol is missing"}
        if "OPENSIGWEAVE_GUARD" not in found:
            return {"status": "configured", "detail": "Injector registered; optional guard not installed (not a mail delivery test)"}
        return {"status": "configured", "detail": "Injector and optional guard registered (not a mail delivery test)"}
    except HTTPError as exc:
        if exc.code in (401, 403):
            return {"status": "auth_required", "detail": "Rspamd read-only controller authentication required or refused"}
        return {"status": "unknown", "detail": "Controller returned HTTP " + str(exc.code)}
    except (ValueError, KeyError, TypeError, OSError, URLError) as exc:
        # Never return URLs, passwords or error response bodies in diagnostics.
        return {"status": "unknown", "detail": "Controller probe unavailable or returned invalid data"}


MAILCOW_ARTIFACTS = (
    "rspamd/opensigweave.lua",
    "rspamd/opensigweave_mime.lua",
    "rspamd/opensigweave_cid.lua",
    "rspamd/opensigweave_domain_policy.lua",
    "scripts/sync_domain_policy.py",
    "scripts/mailcow_setup.py",
)


def _mailcow_install_commands(engine_url: str) -> tuple[str, str, str]:
    """Two commands for the deployed, published release; never use moving branches."""
    tag = os.environ.get("OSW_RELEASE_TAG", "").strip()
    revision = os.environ.get("OSW_RELEASE_SHA", "").strip().lower()
    if not (re.fullmatch(r"20\d\d\.(?:0[1-9]|1[0-2])(?:-(?:alpha|beta)[1-9]\d*)?", tag)
            and re.fullmatch(r"[a-f0-9]{40}", revision)):
        return "", "", ("No immutable published release identity is available in this "
                        "development/older image. Do not copy unverified adapters: deploy "
                        "an approved release with built-in revision metadata.")
    if not engine_url or not engine_url.startswith("https://"):
        return "", "", "Save a valid engine HTTPS endpoint first."
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    try:
        for path in MAILCOW_ARTIFACTS:
            file = root / path
            if file.is_symlink() or not file.is_file():
                raise OSError("Missing adapter file")
            hashes[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return "", "", "The deployed image lacks required matching adapter files."
    lines = [
        "# Run from the Mailcow Docker Compose directory (on the Mailcow HOST).",
        "(",
        "set -eu",
        "OSW_TAG=" + shlex.quote(tag),
        "OSW_REV=" + shlex.quote(revision),
        "OSW_ENGINE_URL=" + shlex.quote(engine_url),
        "# Confirm that the release tag actually resolves to the image's pinned commit.",
        "python3 - \"$OSW_TAG\" \"$OSW_REV\" <<'PY'",
        "import json, sys, urllib.request",
        "tag, expected = sys.argv[1:]",
        "base = 'https://api.github.com/repos/VeiglbergTech/OpenSigWeave'",
        "def get(path):",
        "    req = urllib.request.Request(base + path, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'OpenSigWeave-Mailcow-installer'})",
        "    with urllib.request.urlopen(req, timeout=15) as result:",
        "        return json.load(result)",
        "release = get('/releases/tags/' + tag)",
        "if release.get('draft') or release.get('tag_name') != tag or not release.get('published_at'):",
        "    raise SystemExit('Not a published release')",
        "obj = get('/git/ref/tags/' + tag)['object']",
        "if obj.get('type') == 'tag':",
        "    obj = get('/git/tags/' + obj['sha'])['object']",
        "if obj.get('type') != 'commit' or obj.get('sha') != expected:",
        "    raise SystemExit('Deployed release/revision mismatch; refusing to install')",
        "PY",
        "OSW_TMP=\"$(mktemp -d)\"",
        "trap 'rm -rf \"$OSW_TMP\"' EXIT",
    ]
    for path in MAILCOW_ARTIFACTS:
        lines.extend((
            "mkdir -p \"$OSW_TMP/" + path.rsplit("/", 1)[0] + "\"",
            "curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL --retry 2 "
            + shlex.quote("https://raw.githubusercontent.com/VeiglbergTech/OpenSigWeave/" + revision + "/" + path)
            + " -o \"$OSW_TMP/" + path + "\"",
            "printf '%s  %s\\n' " + shlex.quote(hashes[path]) + " \"$OSW_TMP/" + path
            + "\" | sha256sum -c --status",
        ))
    lines.extend((
        "sudo python3 \"$OSW_TMP/scripts/mailcow_setup.py\" prepare "
        "--mailcow-root \"$PWD\" --source \"$OSW_TMP\" --revision \"$OSW_REV\" "
        "--engine-url \"$OSW_ENGINE_URL\"",
        ")",
    ))
    activation = (
        "# Run from the same Mailcow Docker Compose directory after preparation.\n"
        "sudo python3 /usr/local/lib/opensigweave-mailcow/mailcow_setup.py "
        "activate --mailcow-root \"$PWD\""
    )
    return "\n".join(lines), activation, ""


def adapter_instructions(server: str, engine_url: str = "") -> dict[str, object]:
    """Administrator-only, no-secret release-pinned Mailcow setup instructions."""
    if server == "mailcow_rspamd":
        commands, verify, issue = _mailcow_install_commands(engine_url)
        return {
            "supported": True, "title": "Mailcow / Rspamd",
            "description": issue or (
                "Run both blocks from your Mailcow Compose checkout, regardless of "
                "whether OpenSigWeave is on the same host. The first block verifies "
                "the published release revision and SHA-256 of all six adapter assets, "
                "installs four Lua modules, fetches the authenticated offline domain "
                "policy and asks for ENGINE_API_KEY via masked terminal entry. "
                "The second validates, activates Rspamd and enables 5-minute "
                "host-side domain-policy synchronization. You must securely transfer "
                "the Engine API key from the OpenSigWeave host; it never appears here. "
                "TLS must be valid; verify a real received SMTP message separately."
            ),
            "commands": commands, "verify": verify, "config": "",
        }
    if server == "rspamd":
        return {"supported": True, "title": "Rspamd / other MTA",
                "description": "Use the adapter documentation for manual Rspamd integration outside Mailcow. Never substitute a moving branch for a release.",
                "commands": "", "config": "", "verify": ""}
    if server == "stalwart":
        return {"supported": True, "title": "Stalwart",
                "description": "Configure the Stalwart DATA-stage hook using its Bearer token and the OpenSigWeave HTTPS URL described in docs/ADAPTERS.md. No Rspamd changes required.",
                "commands": "", "config": "", "verify": ""}
    return {"supported": False, "title": "Other mail server",
            "description": "Choose a supported adapter against the HTTPS engine API; see docs/ADAPTERS.md.",
            "commands": "", "config": "", "verify": ""}
