"""Securely copy a generated age recovery identity out of the Compose volume.

Never print the secret, pass it as a command argument or overwrite an existing file.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def export_key(target: Path) -> None:
    target = target.expanduser().absolute()
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("Export destination directory must exist and not be a symlink")
    mode = parent.stat().st_mode
    if stat.S_IMODE(mode) & 0o077:
        raise ValueError("Use a private export destination directory (0700)")
    if target.exists() or target.is_symlink():
        raise ValueError("Refusing to overwrite a recovery identity")
    with tempfile.TemporaryDirectory(prefix=".osw-key-export-", dir=parent) as tmp:
        os.chmod(tmp, 0o700)
        candidate = Path(tmp) / "age-identity.txt"
        result = subprocess.run(
            ["docker", "compose", "cp",
             "opensigweave:/app/backups/.recovery/age-identity.txt",
             str(candidate)],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if result.returncode:
            raise ValueError("Unable to export recovery identity. "
                             "Check that an encrypted backup has run successfully")
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("Unexpected recovery key file type")
        os.chmod(candidate, 0o600)
        content = candidate.read_text(encoding="ascii")
        if not any(line.startswith("AGE-SECRET-KEY-") for line in content.splitlines()):
            raise ValueError("Exported identity file does not contain an age private key")
        # link creates exclusively; target cannot already exist.
        os.link(candidate, target, follow_symlinks=False)
    print("[ OK ] Recovery identity exported to a protected file (0600)")
    print("[INFO] Store it securely outside this host and separately from the .env and backups.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export the age recovery identity to an offline file")
    parser.add_argument("destination", type=Path, help="Nonexisting file under a private (0700) directory")
    args = parser.parse_args(argv)
    try:
        export_key(args.destination)
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
