"""Resolve latest published main GitHub release to its exact signed-off Git tag.

Never deploy an untagged main commit under a moving image alias.
GitHub releases/latest excludes prereleases; use the explicit releases feed.
"""
from __future__ import annotations

import json
import re
from http.client import HTTPSConnection

try:
    from deploy_common import DeploymentError, git
except ModuleNotFoundError:  # imported as scripts.release_target by CI
    from scripts.deploy_common import DeploymentError, git

REPO = "VeiglbergTech/OpenSigWeave"
URL = "https://api.github.com/repos/" + REPO + "/releases?per_page=100"
TAG = re.compile(r"20\d\d\.(?:0[1-9]|1[0-2])(?:-(?:alpha|beta)[1-9]\d*)?$|20\d\d\.(?:0[1-9]|1[0-2])-(?:alpha|beta)$")


def latest_release(*, fetch=None, stable_only: bool = False) -> str:
    """Get newest published tag; package deployments require stable-only releases."""
    if fetch is None:
        def fetch():
            # Fixed GitHub host/path: no redirects or arbitrary URL schemes.
            connection = HTTPSConnection("api.github.com", timeout=15)
            try:
                connection.request("GET", "/repos/" + REPO + "/releases?per_page=100", headers={
                    "User-Agent": "OpenSigWeave-installer",
                    "Accept": "application/vnd.github+json",
                })
                response = connection.getresponse()
                if response.status != 200:
                    raise RuntimeError("GitHub release API unavailable")
                return response.read(1024 * 1024)
            finally:
                connection.close()
    try:
        releases = json.loads(fetch())
        if not isinstance(releases, list):
            raise ValueError("Invalid GitHub releases response")
        candidates = [
            row for row in releases
            if isinstance(row, dict) and not row.get("draft")
            and isinstance(row.get("tag_name"), str)
            and TAG.fullmatch(row["tag_name"])
            and isinstance(row.get("published_at"), str)
            and (not stable_only or (row.get("prerelease") is False
                                     and re.fullmatch(r"20\d\d\.(?:0[1-9]|1[0-2])", row["tag_name"])))
        ]
        if not candidates:
            raise ValueError("No published main release found")
        return max(candidates, key=lambda row: row["published_at"])["tag_name"]
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        raise DeploymentError("Cannot determine the latest published main release; no change applied", 4) from exc


def release_tag_sha(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise DeploymentError("Invalid release tag", 4)
    # Duplicated tag listing for annotated tags; the ^{} object is the commit.
    output = git("ls-remote", "--tags", "origin",
                 f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    sha_by_ref: dict[str, str] = {}
    for line in output.splitlines():
        pieces = line.split()
        if len(pieces) == 2 and re.fullmatch(r"[a-f0-9]{40,64}", pieces[0]):
            sha_by_ref[pieces[1]] = pieces[0]
    sha = sha_by_ref.get(f"refs/tags/{tag}^{{}}") or sha_by_ref.get(f"refs/tags/{tag}")
    if not sha:
        raise DeploymentError("Published release Git tag is missing from approved origin", 4)
    return sha
