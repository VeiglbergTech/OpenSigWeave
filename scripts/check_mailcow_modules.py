#!/usr/bin/env python3
"""Read-only Mailcow Rspamd OpenSigWeave module health checker.

Run independently on the Mailcow host, not from the OpenSigWeave web app.
Exit 0: modules/configuration present; 1: a required module absent/disabled;
2: inspection failed. Does not verify live SMTP message injection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

FILES = (
    "/etc/rspamd/plugins.d/opensigweave.lua",
    "/etc/rspamd/plugins.d/opensigweave_guard.lua",
    "/etc/rspamd/lua/opensigweave_mime.lua",
    "/etc/rspamd/lua/opensigweave_cid.lua",
)
MODULES = ("opensigweave", "opensigweave_guard")


def run_command(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=20, check=False)


def parse_modules(output: str) -> tuple[set[str], set[str]] | None:
    enabled = re.search(r"(?m)^\s*Modules enabled:\s*([^\r\n]*)", output)
    failed = re.search(r"(?m)^\s*Modules disabled \(failed\):\s*([^\r\n]*)", output)
    if enabled is None or failed is None:
        return None

    def names(group: str) -> set[str]:
        return {token.strip().lower() for token in group.split(",") if token.strip()}

    return names(enabled.group(1)), names(failed.group(1))


def inspect(mailcow_dir: str, policy: str = "enabled",
            runner=run_command) -> tuple[int, dict]:
    required_modules = MODULES if policy == "enabled" else ("opensigweave",)
    messages: list[str] = []
    prefix = ["docker", "compose", "exec", "-T", "rspamd-mailcow"]
    try:
        # Fixed file paths, and no shell interpolation of untrusted input.
        shell = 'for f in ' + ' '.join(FILES) + (
            '; do if ! test -r "$f"; then printf "MISSING:%s\n" "$f"; fi; done'
        )
        file_result = runner(prefix + ["sh", "-c", shell], mailcow_dir)
        if file_result.returncode:
            return 2, {"status": "UNKNOWN", "messages": ["File inspection failed"]}
        absent = [line.partition(":")[2] for line in file_result.stdout.splitlines()
                  if line.startswith("MISSING:")]
        if policy == "disabled":
            absent = [p for p in absent if not p.endswith("/opensigweave_guard.lua")]
        messages.extend(f"Missing/unreadable: {p}" for p in absent)

        module_result = runner(prefix + ["rspamadm", "configdump", "-m"], mailcow_dir)
        if module_result.returncode:
            return 2, {"status": "UNKNOWN", "messages": messages + [
                "Rspamd module inspection failed"
            ]}
        parsed = parse_modules(module_result.stdout + "\n" + module_result.stderr)
        if parsed is None:
            return 2, {"status": "UNKNOWN", "messages": messages + [
                "Cannot parse rspamadm configdump -m output"
            ]}
        enabled, failed = parsed
        for module in required_modules:
            if module not in enabled or module in failed:
                messages.append(f"Required Rspamd module not enabled: {module}")
    except (OSError, subprocess.TimeoutExpired):
        return 2, {"status": "UNKNOWN", "messages": messages + [
            "Docker/Compose command unavailable or timed out"
        ]}

    return (1 if messages else 0), {
        "status": "FAIL" if messages else "PASS",
        "policy": policy,
        "messages": messages or ["Required module files and configuration are present"],
        "note": "Configuration-level only; live SMTP signature insertion is not verified",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailcow-dir", type=Path, required=True,
                        help="Directory holding the Mailcow Compose configuration")
    parser.add_argument("--policy", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)
    if not args.mailcow_dir.is_dir():
        parser.error("--mailcow-dir must be an existing directory")
    status, details = inspect(str(args.mailcow_dir), args.policy)
    if args.json:
        print(json.dumps(details, sort_keys=True))
    else:
        print(f'{details["status"]}: OpenSigWeave Mailcow modules ({args.policy})')
        for message in details["messages"]:
            print(f"  {message}")
        print(f'  {details.get("note", "Check could not complete")}')
    return status


if __name__ == "__main__":
    sys.exit(main())
