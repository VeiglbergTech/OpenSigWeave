#!/usr/bin/env python3
"""Root-operated, agentless Mailcow adapter setup.

Run from the Mailcow Compose checkout. Only a pinned, SHA-256-verified source
directory is accepted by the UI-generated bootstrap. This tool manages only
its named Rspamd files and a delimited section in rspamd.conf.local.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

FILES = {
    "rspamd/opensigweave.lua": "plugins.d/opensigweave.lua",
    "rspamd/opensigweave_mime.lua": "lua/opensigweave_mime.lua",
    "rspamd/opensigweave_cid.lua": "lua/opensigweave_cid.lua",
    "rspamd/opensigweave_domain_policy.lua": "lua/opensigweave_domain_policy.lua",
}
BEGIN = "# BEGIN OpenSigWeave managed Mailcow adapter"
END = "# END OpenSigWeave managed Mailcow adapter"
RUNTIME = Path("/usr/local/lib/opensigweave-mailcow")
SECRET = Path("/etc/opensigweave-mailcow/engine.key")
STATE = Path("/etc/opensigweave-mailcow/install-state.json")
SERVICE = Path("/etc/systemd/system/opensigweave-mailcow-policy.service")
TIMER = Path("/etc/systemd/system/opensigweave-mailcow-policy.timer")
POLICY_REL = "opensigweave-policy/policy.json"
ENGINE_SUFFIX = "/api/v1/engine/signatures/"
MAX_SIZE = 1024 * 1024
COMMIT = re.compile(r"[a-f0-9]{40}\Z")


def digest(data: bytes) -> str:
    """SHA-256 for nonsecret adapter file integrity only."""
    return hashlib.sha256(data).hexdigest()


def block_mac(block: str, api_key: str) -> str:
    """Protect the credential-bearing block using a keyed MAC, not a bare digest."""
    if len(api_key) < 32:
        raise ValueError("Invalid ENGINE_API_KEY for managed configuration verification")
    return hmac.new(api_key.encode("utf-8"), block.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def run(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def checked_regular(path: Path) -> bytes | None:
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(meta.st_mode):
        raise ValueError(f"Refusing non-regular file: {path}")
    if meta.st_size > MAX_SIZE:
        raise ValueError(f"Refusing oversized file: {path}")
    return path.read_bytes()


def atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".osw-stage-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def root_only() -> None:
    if os.geteuid() != 0:
        raise ValueError("Run as root using sudo; no Docker socket or network mode changes are needed")


def mailcow_root(value: str) -> Path:
    root = Path(value).resolve(strict=True)
    if not (root / "docker-compose.yml").is_file() and not (root / "compose.yaml").is_file():
        raise ValueError("Run this command from the Mailcow Docker Compose directory")
    rspamd = root / "data/conf/rspamd"
    if not rspamd.is_dir():
        raise ValueError("Mailcow Rspamd configuration directory was not found")
    services = subprocess.run(["docker", "compose", "config", "--services"],
                              cwd=root, capture_output=True, text=True, check=True)
    if "rspamd-mailcow" not in services.stdout.splitlines():
        raise ValueError("Current Compose project has no rspamd-mailcow service")
    return root


def engine_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != ENGINE_SUFFIX
            or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parsed.netloc)):
        raise ValueError("Use the configured HTTPS signature endpoint, with a trusted certificate")
    return value


def load_state() -> dict | None:
    data = checked_regular(STATE)
    if data is None:
        return None
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value) != {"root", "revision", "engine_url", "files", "block_hash", "sync_hash"}
            or not COMMIT.fullmatch(value["revision"])
            or not isinstance(value["sync_hash"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["sync_hash"])
            or not isinstance(value["files"], dict) or set(value["files"]) != set(FILES)
            or not all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v)
                       for v in value["files"].values())):
        raise ValueError("Invalid installer state; manual recovery required")
    return value


def managed_block(url: str, secret: str) -> str:
    # JSON string quoting is valid UCL quoting and cannot inject a new section.
    return (BEGIN + "\n"
            + "opensigweave {\n"
            + "  api_url = " + json.dumps(url) + ";\n"
            + "  engine_api_key = " + json.dumps(secret) + ";\n"
            + '  failure_policy = "enabled";\n'
            + '  domain_policy_file = "/etc/rspamd/opensigweave-policy/policy.json";\n'
            + '  inline_images = "cid";\n'
            + "  max_response_size = 6291456;\n"
            + "  timeout = 5.0;\n"
            + "}\n" + END)


def split_config(existing: str, state: dict | None, key: str | None = None) -> tuple[str, str | None]:
    nstart, nend = existing.count(BEGIN), existing.count(END)
    if nstart != nend or nstart > 1:
        raise ValueError("Conflicting OpenSigWeave managed block markers; refusing update")
    old = None
    if nstart:
        head, remaining = existing.split(BEGIN, 1)
        body, tail = remaining.split(END, 1)
        old = BEGIN + body + END
        if not state:
            raise ValueError("Existing managed block has no trusted installation state")
        if key is None:
            key = _key()
        if not hmac.compare_digest(block_mac(old, key), state["block_hash"]):
            raise ValueError("Existing managed configuration was changed; refusing to overwrite")
        existing = head + tail
    if re.search(r"(?m)^\s*opensigweave\s*\{", existing):
        raise ValueError("Unmanaged/duplicate OpenSigWeave configuration exists; migrate it manually")
    return existing.rstrip(), old


def _key() -> str:
    previous = checked_regular(SECRET)
    if previous is not None:
        meta = SECRET.stat()
        if meta.st_mode & 0o077:
            raise ValueError("Engine key file must have mode 0600")
        return previous.decode("utf-8").strip()
    if not sys.stdin.isatty():
        raise ValueError("First installation needs a TTY for masked secret entry")
    value = getpass.getpass("Paste ENGINE_API_KEY (input hidden): ").strip()
    if len(value) < 32 or any(ch.isspace() or ord(ch) < 33 for ch in value):
        raise ValueError("ENGINE_API_KEY must contain at least 32 non-whitespace characters")
    SECRET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic(SECRET, (value + "\n").encode(), 0o600)
    return value


def sync_policy(root: Path, url: str) -> None:
    script = RUNTIME / "sync_domain_policy.py"
    if checked_regular(script) is None:
        raise ValueError("Domain synchronization helper is missing")
    policy_url = url.removesuffix(ENGINE_SUFFIX) + "/api/v1/mail/domain-policy"
    destination = root / "data/conf/rspamd" / POLICY_REL
    run([sys.executable, str(script), "--url", policy_url,
         "--key-file", str(SECRET), "--output", str(destination)])


def status(root: Path) -> None:
    state = load_state()
    if not state or state["root"] != str(root):
        raise ValueError("This Mailcow installation has no matching managed state")
    for source, rel in FILES.items():
        current = checked_regular(root / "data/conf/rspamd" / rel)
        if current is None or digest(current) != state["files"][source]:
            raise ValueError("Missing or modified Rspamd adapter: " + rel)
    helper = checked_regular(RUNTIME / "sync_domain_policy.py")
    if helper is None or digest(helper) != state["sync_hash"]:
        raise ValueError("Missing or modified synchronization helper")
    conf = (root / "data/conf/rspamd/rspamd.conf.local").read_text()
    split_config(conf, state)
    run([sys.executable, str(RUNTIME / "sync_domain_policy.py"), "--status",
         "--output", str(root / "data/conf/rspamd" / POLICY_REL)])
    print("Configured files, managed block, and local policy validated; SMTP delivery NOT tested")


def prepare(root: Path, source: Path, revision: str, url: str) -> None:
    if not COMMIT.fullmatch(revision):
        raise ValueError("A pinned 40-character release revision is required")
    url = engine_url(url)
    previous = load_state()
    if previous and previous["root"] != str(root):
        raise ValueError("Existing installer state refers to another Mailcow installation")
    values = {}
    for src in (*FILES, "scripts/sync_domain_policy.py", "scripts/mailcow_setup.py"):
        payload = checked_regular(source / src)
        if payload is None:
            raise ValueError("Missing verified release artifact: " + src)
        values[src] = payload
    conf_path = root / "data/conf/rspamd/rspamd.conf.local"
    raw_conf = checked_regular(conf_path)
    original_conf = raw_conf.decode("utf-8") if raw_conf is not None else ""
    rest, old = split_config(original_conf, previous)
    for src, rel in FILES.items():
        target = root / "data/conf/rspamd" / rel
        existing = checked_regular(target)
        if existing is not None and (not previous or digest(existing) != previous["files"][src]):
            if existing != values[src]:
                raise ValueError("Unmanaged or locally edited adapter file: " + str(target))
    key = _key()
    if len(key) < 32 or "\n" in key or "\r" in key:
        raise ValueError("Invalid saved ENGINE_API_KEY")
    # Synchronize first and fail closed: do not activate incomplete offline policies.
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    local_sync = RUNTIME / "sync_domain_policy.py"
    existing_sync = checked_regular(local_sync)
    if existing_sync is not None:
        if previous and digest(existing_sync) != previous["sync_hash"]:
            raise ValueError("Locally edited synchronization helper; refusing overwrite")
        if not previous and existing_sync != values["scripts/sync_domain_policy.py"]:
            raise ValueError("Existing, unmanaged synchronization helper")
    atomic(local_sync, values["scripts/sync_domain_policy.py"], 0o644)
    try:
        policy_directory = root / "data/conf/rspamd/opensigweave-policy"
        if policy_directory.is_symlink():
            raise ValueError("Refusing symlinked Rspamd OpenSigWeave policy directory")
        policy_directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        sync_policy(root, url)
    except Exception:
        if existing_sync is None:
            local_sync.unlink(missing_ok=True)
        else:
            atomic(local_sync, existing_sync, 0o644)
        raise
    block = managed_block(url, key)
    new_config = (rest + "\n\n" if rest else "") + block + "\n"
    if original_conf == new_config and previous and previous["revision"] == revision:
        print("Already prepared at the same revision; configuration left unchanged")
        return
    conf_mode = stat.S_IMODE(conf_path.stat().st_mode) if raw_conf is not None else 0o644
    backup = {str(conf_path): raw_conf}
    for src, rel in FILES.items():
        path = root / "data/conf/rspamd" / rel
        backup[str(path)] = checked_regular(path)
    try:
        for src, rel in FILES.items():
            atomic(root / "data/conf/rspamd" / rel, values[src], 0o644)
        # Config contains the secret: prevent ordinary host users from reading it.
        atomic(conf_path, new_config.encode(), 0o600)
        atomic(RUNTIME / "mailcow_setup.py", values["scripts/mailcow_setup.py"], 0o755)
        next_state = {
            "root": str(root), "revision": revision, "engine_url": url,
            "files": {src: digest(values[src]) for src in FILES},
            "sync_hash": digest(values["scripts/sync_domain_policy.py"]),
            "block_hash": block_mac(block, key),
        }
        atomic(STATE, (json.dumps(next_state, sort_keys=True) + "\n").encode(), 0o600)
        run(["docker", "compose", "exec", "-T", "rspamd-mailcow",
             "rspamadm", "configtest"], root)
    except Exception:
        for path, content in backup.items():
            target = Path(path)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                atomic(target, content, conf_mode if target == conf_path else 0o644)
        if previous is None:
            STATE.unlink(missing_ok=True)
        else:
            atomic(STATE, (json.dumps(previous, sort_keys=True) + "\n").encode(), 0o600)
        raise
    print("Prepared and passed rspamadm configtest. Activation is a separate explicit step.")


def activate(root: Path) -> None:
    status(root)
    if shutil.which("systemctl") is None or not Path("/run/systemd/system").is_dir():
        raise ValueError("Automatic synchronization requires a systemd timer; configure an equivalent root cron job manually")
    state = load_state()
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(root)):
        raise ValueError("Mailcow directory contains unsupported systemd path characters")
    url = state["engine_url"].removesuffix(ENGINE_SUFFIX) + "/api/v1/mail/domain-policy"
    destination = root / "data/conf/rspamd" / POLICY_REL
    service = ("[Unit]\nDescription=OpenSigWeave authenticated domain policy synchronization\n"
               "[Service]\nType=oneshot\nUser=root\nExecStart=/usr/bin/python3 "
               + str(RUNTIME / "sync_domain_policy.py") + " --url " + url
               + " --key-file " + str(SECRET) + " --output " + str(destination) + "\n"
               "NoNewPrivileges=true\nPrivateTmp=true\n")
    timer = ("[Unit]\nDescription=OpenSigWeave domain policy synchronization timer\n"
             "[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\nPersistent=true\n"
             "[Install]\nWantedBy=timers.target\n")
    for target, content in ((SERVICE, service), (TIMER, timer)):
        old = checked_regular(target)
        if old is not None and b"OpenSigWeave" not in old:
            raise ValueError("Refusing to overwrite an unrelated systemd unit")
        atomic(target, content.encode(), 0o644)
    sync_policy(root, state["engine_url"])
    run(["docker", "compose", "exec", "-T", "rspamd-mailcow", "rspamadm", "configtest"], root)
    # A valid host file alone cannot prove that the running Rspamd container
    # sees the same mounted policy and adapter files.
    run(["docker", "compose", "exec", "-T", "rspamd-mailcow", "sh", "-c",
         "test -r /etc/rspamd/opensigweave-policy/policy.json && "
         "test -r /etc/rspamd/lua/opensigweave_domain_policy.lua && "
         "test -r /etc/rspamd/plugins.d/opensigweave.lua"], root)
    run(["docker", "compose", "restart", "rspamd-mailcow"], root)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", TIMER.name])
    print("Rspamd restarted; 5-minute domain synchronization enabled.")
    print("Send and inspect a real authenticated SMTP message. HTTP/configtest is NOT SMTP acceptance.")


def rollback(root: Path) -> None:
    state = load_state()
    if not state or state["root"] != str(root):
        raise ValueError("No matching managed installation")
    # Rollback also works if a local policy snapshot was damaged.
    for src, rel in FILES.items():
        current = checked_regular(root / "data/conf/rspamd" / rel)
        if current is None or digest(current) != state["files"][src]:
            raise ValueError("Locally modified adapter; refusing rollback: " + rel)
    path = root / "data/conf/rspamd/rspamd.conf.local"
    raw = path.read_text()
    rest, old = split_config(raw, state)
    if not old:
        raise ValueError("Managed block is absent")
    if TIMER.exists():
        run(["systemctl", "disable", "--now", TIMER.name])
    atomic(path, (rest + "\n").encode(), 0o600)
    try:
        run(["docker", "compose", "exec", "-T", "rspamd-mailcow", "rspamadm", "configtest"], root)
    except Exception:
        atomic(path, raw.encode(), 0o600)
        raise
    run(["docker", "compose", "restart", "rspamd-mailcow"], root)
    for src, rel in FILES.items():
        (root / "data/conf/rspamd" / rel).unlink()
    STATE.unlink()
    print("Managed adapter rolled back; local policy and protected secret retained.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenSigWeave Mailcow integration (run as root)")
    parser.add_argument("action", choices=("prepare", "activate", "status", "sync", "rollback"))
    parser.add_argument("--mailcow-root", default=".")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--revision", default="")
    parser.add_argument("--engine-url", default="")
    args = parser.parse_args(argv)
    try:
        root_only()
        root = mailcow_root(args.mailcow_root)
        if args.action == "prepare":
            if args.source is None:
                raise ValueError("--source is required")
            prepare(root, args.source, args.revision, args.engine_url)
        elif args.action == "activate":
            activate(root)
        elif args.action == "status":
            status(root)
        elif args.action == "sync":
            state = load_state()
            if not state or state["root"] != str(root):
                raise ValueError("Mailcow integration not installed")
            sync_policy(root, state["engine_url"])
        else:
            rollback(root)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        print("Mailcow setup failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
