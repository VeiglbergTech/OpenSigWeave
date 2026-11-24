#!/usr/bin/env python3
"""Release channel version tags and fail-closed GitHub provenance gate.

Tags are created manually from the approved main branch. This script does
not create branches/tags or promote main.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

REPOSITORY = "VeiglbergTech/OpenSigWeave"
TAG_RE = re.compile(
    r"^(20[0-9]{2})\.(0[1-9]|1[0-2])(?:-(alpha|beta)([1-9][0-9]*)?)?$"
)


def parse_release_tag(tag: str) -> tuple[str, str]:
    """Return the immutable version tag and ONE matching moving alias."""
    result = TAG_RE.fullmatch(tag)
    if not result:
        raise ValueError(
            "Expected YYYY.MM-alpha, YYYY.MM-alpha1, YYYY.MM-beta, "
            "YYYY.MM-beta1, or YYYY.MM (stable); alpha0 is invalid"
        )
    return tag, result.group(3) or "latest"


def github_get(token: str, path: str) -> dict:
    request = Request(
        "https://api.github.com/" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opensigweave-release-gate",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main_matches_sha(token: str, sha: str) -> bool:
    data = github_get(token, f"repos/{REPOSITORY}/git/ref/heads/main")
    return (data.get("object") or {}).get("sha") == sha


def workflow_state(token: str, workflow: str, sha: str) -> str:
    data = github_get(
        token,
        f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs"
        f"?head_sha={sha}&branch=main&event=push&per_page=40",
    )
    runs = [
        r for r in data.get("workflow_runs", [])
        if r.get("head_sha") == sha and r.get("head_branch") == "main"
        and r.get("event") == "push"
    ]
    if any(r.get("status") == "completed" and r.get("conclusion") == "success"
           for r in runs):
        return "success"
    if any(r.get("status") == "completed" and
           r.get("conclusion") in ("failure", "timed_out", "action_required")
           for r in runs):
        return "failed"
    return "waiting"


def wait_for_main_gates(token: str, sha: str, limit_seconds: int = 3000) -> None:
    deadline = time.monotonic() + limit_seconds
    while True:
        if not main_matches_sha(token, sha):
            raise RuntimeError("Release tag must resolve to the approved current main HEAD")
        results = {
            workflow: workflow_state(token, workflow, sha)
            for workflow in ("tests.yml", "security.yml")
        }
        print(f"Release source checks: {results}", flush=True)
        if all(state == "success" for state in results.values()):
            return
        if "failed" in results.values():
            raise RuntimeError("Main Tests or Security scan failed; abort publication")
        if time.monotonic() >= deadline:
            raise RuntimeError("Main CI did not complete successfully in time")
        time.sleep(30)


def main() -> int:
    try:
        version, alias = parse_release_tag(os.environ["GITHUB_REF_NAME"])
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise RuntimeError("Unexpected repository")
        token = os.environ["GH_TOKEN"]
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=15
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise RuntimeError("Checkout is not at an exact Git commit")
        wait_for_main_gates(token, revision)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"version={version}\nalias={alias}\nrevision={revision}\n")
        print(f"Authorized: {version} and {alias} from {revision}")
        return 0
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"RELEASE BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
