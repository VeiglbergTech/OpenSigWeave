"""Operator-facing packaging and verification commands for OpenSigWeave backups.

This first CLI layer operates on an already prepared staging directory. Docker
snapshot orchestration and destructive restore are intentionally separate work.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

from backup_archive import BackupArchiveError, create_archive, write_manifest
from backup_encryption import (
    BackupEncryptionError,
    decrypt_and_verify_archive,
    decrypt_archive,
    encrypt_archive,
)
from backup_docker import BackupDockerError, create_docker_backup
from backup_restore import BackupRestoreError, restore_fresh_deployment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opensigweave-backup")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a complete encrypted backup from a Docker Compose project")
    create.add_argument("--project-dir", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--recipient", required=True)
    create.add_argument("--identity", required=True, type=Path)
    create.add_argument("--docker-binary", default="docker")
    create.add_argument("--age-binary", default="age")

    restore = commands.add_parser("restore", help="Restore a complete backup into a fresh Compose deployment")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--identity", required=True, type=Path)
    restore.add_argument("--target-dir", required=True, type=Path)
    restore.add_argument("--project-name", required=True)
    restore.add_argument("--docker-binary", default="docker")
    restore.add_argument("--age-binary", default="age")
    restore.add_argument("--health-timeout", type=int, default=180)

    package = commands.add_parser("package", help="Create and encrypt an archive from a prepared staging directory")
    package.add_argument("--staging", required=True, type=Path)
    package.add_argument("--output", required=True, type=Path)
    package.add_argument("--recipient", required=True)
    package.add_argument("--age-binary", default="age")

    verify = commands.add_parser("verify", help="Decrypt and verify an encrypted backup without retaining plaintext")
    verify.add_argument("backup", type=Path)
    verify.add_argument("--identity", required=True, type=Path)
    verify.add_argument("--age-binary", default="age")

    decrypt = commands.add_parser("decrypt", help="Decrypt and verify a backup into a new archive file")
    decrypt.add_argument("backup", type=Path)
    decrypt.add_argument("--identity", required=True, type=Path)
    decrypt.add_argument("--output", required=True, type=Path)
    decrypt.add_argument("--age-binary", default="age")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_docker_backup(
                args.project_dir,
                args.output,
                args.recipient,
                args.identity,
                docker_binary=args.docker_binary,
                age_binary=args.age_binary,
            )
            print(f"Full encrypted backup created and verified: {result}")
            return 0

        if args.command == "restore":
            result = restore_fresh_deployment(
                args.backup,
                args.identity,
                args.target_dir,
                project_name=args.project_name,
                docker_binary=args.docker_binary,
                age_binary=args.age_binary,
                health_timeout=args.health_timeout,
            )
            print(f"Restore completed and services are healthy: {result}")
            print("Review the restored deployment before changing DNS or reverse-proxy routing.")
            return 0

        if args.command == "package":
            staging = args.staging.expanduser().absolute()
            if staging.is_symlink() or not staging.is_dir():
                raise BackupArchiveError("Staging path must be an existing directory")
            write_manifest(staging, {"backup_kind": "prepared-staging"})
            with tempfile.TemporaryDirectory(prefix="opensigweave-package-") as temporary:
                plaintext = Path(temporary) / "backup.tar.gz"
                create_archive(staging, plaintext)
                encrypt_archive(
                    plaintext, args.output, args.recipient, executable=args.age_binary
                )
            print(f"Encrypted backup created: {args.output}")
            return 0

        if args.command == "verify":
            manifest = decrypt_and_verify_archive(
                args.backup, args.identity, executable=args.age_binary
            )
            print(
                "Backup verified: "
                f"format={manifest['format']} "
                f"version={manifest['format_version']} "
                f"files={len(manifest['files'])}"
            )
            return 0

        if args.command == "decrypt":
            decrypt_archive(
                args.backup,
                args.identity,
                args.output,
                executable=args.age_binary,
            )
            print(f"Verified archive decrypted: {args.output}")
            return 0
    except (BackupArchiveError, BackupEncryptionError, BackupDockerError, BackupRestoreError, OSError) as exc:
        print(f"Backup operation failed: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
