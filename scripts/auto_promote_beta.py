#!/usr/bin/env python3
"""Promote an exactly CI-verified dev commit to beta; never touch main.

Invoked on a push to dev. Do not run repository code fetched from GitHub:
the workflow only executes this checked-out, versioned script.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

TESTS_WORKFLOW = ".github/workflows/tests.yml"
SECURITY_WORKFLOW = ".github/workflows/security.yml"
POSTFIX_WORKFLOW = ".github/workflows/postfix-milter-compat.yml"
POSTFIX_PATHS = frozenset(
    {
        "docs/POSTFIX_MILTER_DESIGN.md",
        "adapters/postfix_milter.py",
        "tests/test_postfix_milter_adapter.py",
        "tests/postfix_milter_e2e_server.py",
        POSTFIX_WORKFLOW,
    }
)


def workflow_state(runs: list[dict[str, Any]], path: str) -> str:
    """Return pending, success, or failed; all matching push runs must pass."""
    matches = [run for run in runs if run.get("path") == path]
    if not matches:
        return "pending"
    if any(
        run.get("status") == "completed" and run.get("conclusion") != "success"
        for run in matches
    ):
        return "failed"
    if all(
        run.get("status") == "completed" and run.get("conclusion") == "success"
        for run in matches
    ):
        return "success"
    return "pending"


def requires_postfix(changed_paths: list[str]) -> bool:
    return bool(POSTFIX_PATHS.intersection(changed_paths))


def api(method: str, endpoint: str, fields: dict[str, str | bool] | None = None) -> Any:
    command = ["gh", "api", "--method", method, endpoint]
    for key, value in (fields or {}).items():
        if isinstance(value, bool):
            command.extend(["-F", f"{key}={str(value).lower()}"])
        else:
            command.extend(["-f", f"{key}={value}"])
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=45
    )
    if result.returncode != 0:
        # Show GitHub's actual API error, not just the shell command used.
        raise RuntimeError(
            f"GitHub API {method} {endpoint} returned {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def branch_sha(repo: str, name: str) -> str:
    return str(api("GET", f"repos/{repo}/branches/{name}")["commit"]["sha"])


def expected_sha(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("Expected a full, lowercase Git commit SHA")
    return value


def changed_files(repo: str, before: str, sha: str) -> list[str]:
    if before == "0" * 40:
        # A newly created branch cannot be compared with the null SHA.
        # Conservative default: require the optional integration workflow.
        return sorted(POSTFIX_PATHS)
    expected_sha(before)
    comparison = api("GET", f"repos/{repo}/compare/{before}...{sha}")
    if len(comparison["files"]) >= 300:
        return sorted(POSTFIX_PATHS)  # Treat truncated comparisons conservatively.
    return [entry["filename"] for entry in comparison["files"]]


def wait_for_ci(repo: str, sha: str, postfix_expected: bool) -> bool:
    endpoint = (
        f"repos/{repo}/actions/runs?branch=dev&head_sha={sha}"
        "&event=push&per_page=100"
    )
    for attempt in range(180):
        if branch_sha(repo, "dev") != sha:
            print("dev advanced to a newer commit; skip superseded promotion.")
            return False

        runs = api("GET", endpoint)["workflow_runs"]
        paths = [TESTS_WORKFLOW, SECURITY_WORKFLOW]
        if postfix_expected or any(
            run.get("path") == POSTFIX_WORKFLOW for run in runs
        ):
            paths.append(POSTFIX_WORKFLOW)
        states = {path: workflow_state(runs, path) for path in paths}
        if "failed" in states.values():
            raise RuntimeError(f"CI failed or was cancelled at {sha}: {states}")
        if all(state == "success" for state in states.values()):
            print(f"All required dev push CI successful at {sha}: {states}")
            return True
        if attempt % 6 == 0:
            print(f"Waiting for required dev CI at {sha}: {states}", flush=True)
        time.sleep(20)
    raise TimeoutError(f"Required CI did not finish successfully at {sha}")


def promote(repo: str, sha: str) -> None:
    """Move beta to a fully CI-verified dev SHA, strictly via fast-forward.

    The GitHub refs update is forced explicitly *off*. A diverged beta is a
    hard failure, never a reason to create a merge commit or rewrite history.
    """
    if branch_sha(repo, "dev") != sha:
        print("dev changed before promotion; no update.")
        return

    beta_before = branch_sha(repo, "beta")
    comparison = api("GET", f"repos/{repo}/compare/{beta_before}...{sha}")
    if comparison.get("behind_by") != 0:
        raise RuntimeError(
            "beta is not an ancestor of the CI-verified dev commit; "
            "refuse merge commits, rebases, and forced updates."
        )
    if comparison.get("ahead_by") == 0:
        if beta_before == sha:
            print("beta already points to the verified dev commit.")
            return
        raise RuntimeError("No forward commits available, but beta differs from dev.")

    if branch_sha(repo, "dev") != sha:
        print("dev changed during the promotion; skip stale commit.")
        return
    if branch_sha(repo, "beta") != beta_before:
        raise RuntimeError("beta moved while verifying fast-forward; retry after fresh CI.")

    # GitHub PATCH /git/refs has force=false by default. Set it explicitly.
    # Passing False via gh -F sends a JSON boolean, never the string "false".
    result = api(
        "PATCH",
        f"repos/{repo}/git/refs/heads/beta",
        {"sha": sha, "force": False},
    )
    if result.get("object", {}).get("sha") != sha:
        raise RuntimeError("GitHub did not confirm the exact promoted SHA.")
    if branch_sha(repo, "beta") != sha:
        raise RuntimeError("beta was changed again during promotion; inspect ref.")

    print(f"Fast-forward promoted verified dev {sha} to beta (no extra commits).")

def main() -> None:
    repo = os.environ["GH_REPO"]
    sha = expected_sha(os.environ["SOURCE_SHA"])
    before = os.environ["PREVIOUS_SHA"]
    if repo != "VeiglbergTech/OpenSigWeave":
        raise ValueError("Unexpected repository; automation not authorized.")
    if branch_sha(repo, "dev") != sha:
        print("Newer dev commit already present; nothing to promote.")
        return
    paths = changed_files(repo, before, sha)
    if wait_for_ci(repo, sha, requires_postfix(paths)):
        promote(repo, sha)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"::error::Automatic dev -> beta promotion stopped: {exc}", file=sys.stderr)
        sys.exit(1)
