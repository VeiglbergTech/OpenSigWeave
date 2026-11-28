#!/usr/bin/env python3
"""Isolated, Git-free *local fixture* installer/update smoke test.

Runs in GitHub Actions after the source image was built. The two synthetic
2099.xx tags, container labels and mocked release API responses are TEST
FIXTURES ONLY: no Git tag, GitHub Release, GHCR push or production deployment.
A separately authorized smoke test against actual published assets is still
required for stable-release acceptance.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
from urllib.parse import urlencode

PROJECT = Path(__file__).resolve().parents[1]
OLD_TAG, NEW_TAG = "2099.01", "2099.02"
FIRST_SHA = os.environ.get("GITHUB_SHA", "a" * 40)
SECOND_SHA = "b" * 40
IMAGE = "ghcr.io/veiglbergtech/opensigweave"
PORT = 18085


def command(*args: str, cwd: Path = PROJECT) -> str:
    completed = subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, timeout=600,
        check=False,
    )
    if completed.returncode:
        # Logs from Docker may contain secrets; show only the command name.
        raise RuntimeError(f"Local package smoke command {args[0]} failed: exit {completed.returncode}")
    return completed.stdout.strip()


def make_local_test_image(tag: str, revision: str) -> None:
    container = command("docker", "create", "opensigweave-source-test:local")
    try:
        command("docker", "commit",
                "--change", f"LABEL org.opencontainers.image.revision={revision}",
                "--change", f"LABEL org.opencontainers.image.version={tag}",
                "--change", f"ENV OSW_RELEASE_SHA={revision}",
                "--change", f"ENV OSW_RELEASE_TAG={tag}",
                container, f"{IMAGE}:{tag}")
    finally:
        command("docker", "rm", "-f", container)


def stage_bundle(tag: str, revision: str, root: Path) -> tuple[Path, dict]:
    from scripts import release_bundle as builder
    archive = root / (tag + "-local-fixture.tar.gz")
    builder.build(PROJECT, tag, revision, archive)
    target = root / ("package-" + tag)
    target.mkdir(mode=0o700)
    with tarfile.open(archive, "r|gz") as reader:
        seen = set()
        for member in reader:
            if member.name not in set(builder.FILES) | {builder.MANIFEST}:
                raise RuntimeError("Unexpected local archive member")
            if member.name in seen or not member.isfile():
                raise RuntimeError("Duplicated or unsafe local archive member")
            seen.add(member.name)
            content = reader.extractfile(member).read()
            destination = target / member.name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(0o700 if member.name.endswith(".sh") else 0o600)
        if seen != set(builder.FILES) | {builder.MANIFEST}:
            raise RuntimeError("Missing local archive file")
    manifest = builder.read_manifest(target)
    assert manifest["tag"] == tag and manifest["revision"] == revision
    return target, manifest


def request(path: str, *, data: dict | None = None, cookie: str = ""):
    connection = HTTPConnection("127.0.0.1", PORT, timeout=20)
    try:
        body = urlencode(data).encode() if data is not None else None
        headers = {"Host": "localhost"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        connection.request("POST" if data is not None else "GET",
                           path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def complete_web_bootstrap(root: Path) -> None:
    token = (root / ".admin-setup-token").read_text().strip()
    assert len(token) >= 48
    status, headers, body = request("/setup")
    assert status == 200, f"Initial browser setup returned {status}"
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert csrf is not None, "Missing first-run CSRF token"
    cookies = [value.split(";", 1)[0] for name, value in headers
               if name.lower() == "set-cookie"]
    assert cookies, "First-run browser setup did not set a session cookie"
    password = "CI local fixture administrator password 2099"
    status, headers, _ = request("/setup", data={
        "csrf_token": csrf.group(1), "setup_token": token,
        "email": "release-smoke@example.test",
        "password": password, "confirm_password": password,
    }, cookie="; ".join(cookies))
    assert status == 303, f"First-run administrator setup failed: HTTP {status}"
    assert request("/login")[0] == 200, "Login not accessible after administrator setup"
    assert request("/readyz")[0] == 200, "Application not ready after initial setup"


def main() -> int:
    if not re.fullmatch(r"[a-f0-9]{40}", FIRST_SHA):
        raise RuntimeError("CI commit must be a full Git SHA")
    os.environ.pop("COMPOSE_FILE", None)  # Deliberately disable source build overlay.
    os.environ["COMPOSE_PROJECT_NAME"] = "osw-release-package-ci"
    # Synthetic local-only tags are not fetched from GHCR or published.
    make_local_test_image(OLD_TAG, FIRST_SHA)
    make_local_test_image(NEW_TAG, SECOND_SHA)
    with tempfile.TemporaryDirectory(prefix="osw-release-package-") as temp:
        sandbox = Path(temp)
        install_root, _ = stage_bundle(OLD_TAG, FIRST_SHA, sandbox)
        updated_root, updated_manifest = stage_bundle(NEW_TAG, SECOND_SHA, sandbox)
        sys.path.insert(0, str(install_root / "scripts"))
        sys.path.insert(0, str(install_root))
        import config_manager
        import deploy_common
        import install_manager
        import release_bundle
        import update_manager

        # No Git calls are permitted from the installed bundle even when
        # the CI runner happens to have Git available.
        def forbid_git(*args):
            raise AssertionError("Git was used during package installation/update")

        def installed_compose(*args, **kwargs):
            if args[:2] == ("pull", "opensigweave"):
                return ""  # Synthetic OCI images are local fixtures, never GHCR assets.
            return deploy_common.compose(*args, **kwargs)

        def create_ci_config(**kwargs):
            config_manager.create_config(**kwargs)
            # CI has no HTTPS reverse proxy. These are isolated, test-only
            # browser-cookie options; real installations must use HTTPS.
            with (install_root / ".env").open("a", encoding="utf-8") as file:
                file.write('SESSION_COOKIE_NAME="osw-ci-session"\n')
                file.write('SESSION_COOKIE_SECURE="false"\n')

        try:
            with patch.object(deploy_common, "git", side_effect=forbid_git), \
                 patch.object(config_manager, "_free_port", return_value=PORT), \
                 patch.object(config_manager, "prompt", return_value="127.0.0.1"), \
                 patch.object(install_manager, "create_config", side_effect=create_ci_config), \
                 patch.object(release_bundle, "release_commit_sha", return_value=FIRST_SHA), \
                 patch.object(install_manager, "compose", side_effect=installed_compose):
                install_manager.perform_install(
                    None, hostname="localhost", allowed_hosts="localhost",
                    trusted_proxy_cidrs="",
                )
            complete_web_bootstrap(install_root)
            original_env = deploy_common.parse_env(install_root / ".env")
            assert original_env["OSW_IMAGE_TAG"] == OLD_TAG
            command("docker", "compose", "exec", "-T", "postgres", "psql",
                    "-U", "opensigweave", "-d", "opensigweave", "-c",
                    "CREATE TABLE ci_release_persistence (item text);",
                    cwd=install_root)
            command("docker", "compose", "exec", "-T", "postgres", "psql",
                    "-U", "opensigweave", "-d", "opensigweave", "-c",
                    "INSERT INTO ci_release_persistence VALUES ('preserved');",
                    cwd=install_root)
            command("docker", "compose", "exec", "-T", "opensigweave",
                    "sh", "-c", "printf preserved > /data/signatures/ci-release.txt",
                    cwd=install_root)

            with patch.object(deploy_common, "git", side_effect=forbid_git), \
                 patch.object(update_manager, "compose", side_effect=installed_compose), \
                 patch.object(release_bundle, "fetch_verified_bundle",
                              return_value=(updated_root, updated_manifest)):
                # Fail after the journal is written, before any file is changed.
                with patch.object(release_bundle, "install_files",
                                  side_effect=OSError("simulated source-switch interrupt")):
                    try:
                        update_manager.apply_bundle_update(
                            SECOND_SHA, NEW_TAG, yes=True,
                            skip_start=False, continue_without_backup=False,
                        )
                    except deploy_common.DeploymentError as exc:
                        assert exc.code == 8
                    else:
                        raise AssertionError("Interrupted update unexpectedly succeeded")
                journal = update_manager.UPDATE_JOURNAL
                assert journal.is_file(), "Interrupted update did not persist its journal"
                assert json.loads(journal.read_text())["stage"] == "source-switch"
                try:
                    update_manager.ensure_no_incomplete_update()
                except deploy_common.DeploymentError:
                    pass
                else:
                    raise AssertionError("Incomplete update was not blocked")
                assert deploy_common.parse_env(install_root / ".env") == original_env
                assert request("/readyz")[0] == 200, "Interrupted update disrupted running app"
                # Recovery test: the mocked interruption occurred before
                # any package file, database or container was changed.
                update_manager.clear_update_journal()
                update_manager.apply_bundle_update(
                    SECOND_SHA, NEW_TAG, yes=True,
                    skip_start=False, continue_without_backup=False,
                )
            new_env = deploy_common.parse_env(install_root / ".env")
            assert new_env["OSW_IMAGE_TAG"] == NEW_TAG
            for key, value in original_env.items():
                if key != "OSW_IMAGE_TAG":
                    assert new_env[key] == value, f"Update changed protected option {key}"
            assert release_bundle.read_manifest(install_root)["revision"] == SECOND_SHA
            assert not update_manager.UPDATE_JOURNAL.exists()
            assert request("/readyz")[0] == 200
            assert "preserved" in command(
                "docker", "compose", "exec", "-T", "postgres", "psql",
                "-U", "opensigweave", "-d", "opensigweave", "-At", "-c",
                "SELECT item FROM ci_release_persistence;", cwd=install_root,
            )
            assert "preserved" == command(
                "docker", "compose", "exec", "-T", "opensigweave",
                "cat", "/data/signatures/ci-release.txt", cwd=install_root,
            )
            app_id = command("docker", "compose", "ps", "-q", "opensigweave", cwd=install_root)
            running_image = command(
                "docker", "inspect", "-f", "{{.Config.Image}}", app_id,
            )
            assert running_image == f"{IMAGE}:{NEW_TAG}"
            print("PASS: synthetic local package install, browser setup, interrupted update,")
            print("      journal recovery, data/credentials/volumes and image revision; no Git used.")
            print("NOT TESTED: official published Release 2 archive or actual GHCR image.")
        finally:
            # Remove only this disposable CI project's containers and volumes.
            command("docker", "compose", "down", "-v", "--remove-orphans",
                    cwd=install_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
