"""Small, verifiable Git-free installation bundle for published main releases.

The builder is run on an approved release commit. Normal installation receives
only the allowlisted host-side tooling and Compose configuration. Artifact
authenticity MUST be verified using the sha256 digest from the published GitHub
Release API before executing install.sh; internal manifest hashes protect the
already downloaded install directory from accidental edits.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO_API = "https://api.github.com/repos/VeiglbergTech/OpenSigWeave"
TAG = re.compile(r"20\d\d\.(?:0[1-9]|1[0-2])(?:-(?:alpha|beta)[1-9]\d*)?$|20\d\d\.(?:0[1-9]|1[0-2])-(?:alpha|beta)$")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:([a-f0-9]{64})")
FORMAT = "opensigweave-release-bundle-v1"
MANIFEST = ".opensigweave-release.json"
FILES = (
    "install.sh",
    "update.sh",
    "generate_config.sh",
    "compose.yaml",
    ".env.example",
    "auth_config.py",
    "scripts/config_manager.py",
    "scripts/deploy_common.py",
    "scripts/install_manager.py",
    "scripts/update_manager.py",
    "scripts/release_target.py",
    "scripts/release_bundle.py",
)
MAX_ARCHIVE_BYTES = 3 * 1024 * 1024
MAX_CONTENT_BYTES = 2 * 1024 * 1024
HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def asset_name(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError("Invalid release bundle version")
    return "opensigweave-" + tag + "-install.tar.gz"


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(root: Path) -> dict:
    file = root / MANIFEST
    if file.is_symlink() or not file.is_file():
        raise ValueError("Missing or symlinked release manifest")
    value = json.loads(file.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or set(value) != {"format", "tag", "revision", "files"}
        or value["format"] != FORMAT or not isinstance(value["tag"], str)
        or not TAG.fullmatch(value["tag"]) or not isinstance(value["revision"], str)
        or not COMMIT.fullmatch(value["revision"])
        or not isinstance(value["files"], dict) or set(value["files"]) != set(FILES)):
        raise ValueError("Invalid release manifest")
    for path, digest in value["files"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid release file digest")
        source = root / path
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_CONTENT_BYTES:
            raise ValueError("Release file is missing, oversized or symlinked: " + path)
        if _file_hash(source.read_bytes()) != digest:
            raise ValueError("Release package file was modified: " + path)
    return value


def build(root: Path, tag: str, revision: str, output: Path) -> None:
    if not TAG.fullmatch(tag) or not COMMIT.fullmatch(revision):
        raise ValueError("Approved version and 40-character revision required")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing release bundle")
    root = root.resolve()
    data = {}
    manifest = {"format": FORMAT, "tag": tag, "revision": revision, "files": {}}
    for path in FILES:
        source = root / path
        if source.is_symlink() or not source.is_file():
            raise ValueError("Missing or symlinked bundle source: " + path)
        raw = source.read_bytes()
        if len(raw) > MAX_CONTENT_BYTES:
            raise ValueError("Oversized bundle source: " + path)
        data[path] = raw
        manifest["files"][path] = _file_hash(raw)
    data[MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, mode="w:gz") as archive:
        for path, raw in sorted(data.items()):
            item = tarfile.TarInfo(path)
            item.size = len(raw)
            item.mtime = 0
            item.uid = item.gid = 0
            item.uname = item.gname = ""
            item.mode = 0o700 if path in ("install.sh", "update.sh", "generate_config.sh") else 0o600
            archive.addfile(item, io.BytesIO(raw))
    if output.stat().st_size > MAX_ARCHIVE_BYTES:
        output.unlink()
        raise ValueError("Release bundle is unexpectedly large")


class CheckedRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        target = urlsplit(new_url)
        if target.scheme != "https" or target.hostname not in HOSTS or target.username or target.password:
            return None
        return super().redirect_request(request, fp, code, msg, headers, new_url)


_OPENER = build_opener(CheckedRedirects)


def _download(url: str, limit: int, *, accept: str = "application/vnd.github+json") -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS | {"api.github.com"}:
        raise ValueError("Untrusted package URL")
    response = _OPENER.open(Request(url, headers={
        "Accept": accept, "User-Agent": "OpenSigWeave-release-installer/1"
    }), timeout=20)
    with response:
        if response.status != 200:
            raise ValueError("Cannot fetch approved release data")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Oversized release metadata or archive")
    return raw


def _api(path: str) -> dict:
    data = json.loads(_download(REPO_API + path, 1024 * 1024))
    if not isinstance(data, dict):
        raise ValueError("Invalid GitHub release response")
    return data


def release_commit_sha(tag: str) -> str:
    """Verify the published Git tag and its ancestry on the approved main."""
    if not TAG.fullmatch(tag):
        raise ValueError("Invalid release tag")
    release = _api("/releases/tags/" + quote(tag, safe=""))
    if release.get("draft") or release.get("tag_name") != tag or release.get("published_at") is None:
        raise ValueError("Tag does not refer to an approved published release")
    ref = _api("/git/ref/tags/" + quote(tag, safe=""))
    obj = ref.get("object")
    if not isinstance(obj, dict):
        raise ValueError("Invalid Git tag reference")
    if obj.get("type") == "tag":
        tag_obj = _api("/git/tags/" + str(obj.get("sha", "")))
        obj = tag_obj.get("object")
    if not isinstance(obj, dict) or obj.get("type") != "commit":
        raise ValueError("Release tag does not point to a commit")
    sha = obj.get("sha")
    if not isinstance(sha, str) or not COMMIT.fullmatch(sha):
        raise ValueError("Invalid release commit")
    ancestry = _api("/compare/" + sha + "...main")
    if ancestry.get("status") not in ("ahead", "identical") or ancestry.get("behind_by") != 0:
        raise ValueError("Published release commit is not on approved main history")
    return sha


def fetch_verified_bundle(tag: str, directory: Path) -> tuple[Path, dict]:
    """Download only the release asset whose digest GitHub publishes."""
    expected_revision = release_commit_sha(tag)
    release = _api("/releases/tags/" + quote(tag, safe=""))
    wanted = asset_name(tag)
    assets = [a for a in release.get("assets", []) if a.get("name") == wanted]
    if len(assets) != 1:
        raise ValueError("No unique published minimal installer asset for this release")
    asset = assets[0]
    digest = asset.get("digest")
    matched = DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if not matched:
        raise ValueError("Published release asset lacks a trusted SHA-256 digest")
    url = asset.get("browser_download_url")
    prefix = "https://github.com/VeiglbergTech/OpenSigWeave/releases/download/" + tag + "/"
    if not isinstance(url, str) or url != prefix + wanted:
        raise ValueError("Unexpected release asset location")
    archive_data = _download(url, MAX_ARCHIVE_BYTES, accept="application/octet-stream")
    if _file_hash(archive_data) != matched.group(1):
        raise ValueError("GitHub release asset SHA-256 mismatch")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="release-", dir=directory))
    try:
        # Never use extractall(): archives cannot create links, devices, or
        # unexpected paths, and no data or secret paths are accepted.
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) != len(FILES) + 1 or {m.name for m in members} != set(FILES) | {MANIFEST}:
                raise ValueError("Unexpected release package contents")
            total = 0
            for member in members:
                if not member.isfile() or member.size > MAX_CONTENT_BYTES:
                    raise ValueError("Unsafe release archive entry")
                total += member.size
                if total > MAX_CONTENT_BYTES:
                    raise ValueError("Release package expanded size exceeded")
                payload = archive.extractfile(member)
                if payload is None:
                    raise ValueError("Corrupt release archive")
                raw = payload.read(MAX_CONTENT_BYTES + 1)
                if len(raw) != member.size:
                    raise ValueError("Corrupt release entry size")
                target = staging / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o700 if member.name.endswith(".sh") else 0o600)
        manifest = read_manifest(staging)
        if manifest["revision"] != expected_revision or manifest["tag"] != tag:
            raise ValueError("Published package identity differs from approved release tag")
        return staging, manifest
    except BaseException:
        import shutil
        shutil.rmtree(staging)
        raise


def install_files(staging: Path, root: Path) -> None:
    """Replace only allowlisted release tooling; retain all local data and secrets.

    Caller must hold the exclusive deployment lock and journal source switching.
    Any interruption leaves a recovery journal; no automatic schema rollback.
    """
    verified = read_manifest(staging)
    for path in (*FILES, MANIFEST):
        source = staging / path
        target = root / path
        if target.is_symlink():
            raise ValueError("Refusing to overwrite symlink: " + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".osw-update-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o700 if path.endswith(".sh") else 0o600)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    read_manifest(root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a minimal release installer from approved source")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    build(Path(__file__).resolve().parents[1], args.tag, args.revision, args.output)
    print("Release bundle: " + str(args.output))
    print("SHA256: " + _file_hash(args.output.read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
