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


def api(method: str, endpoint: str, fields: dict[str, str] | None = None) -> Any:
    command = ["gh", "api", "--method", method, endpoint]
    for key, value in (fields or {}).items():
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
    if branch_sha(repo, "dev") != sha:
        print("dev changed before merging; no promotion.")
        return

    comparison = api("GET", f"repos/{repo}/compare/beta...{sha}")
    if comparison["ahead_by"] == 0:
        print("beta already includes this dev commit; nothing to promote.")
        return

    owner = repo.split("/", 1)[0]
    pull_requests = api(
        "GET",
        f"repos/{repo}/pulls?state=open&base=beta&head={owner}:dev&per_page=20",
    )
    if len(pull_requests) > 1:
        raise RuntimeError("Multiple competing dev -> beta pull requests.")
    if pull_requests:
        pr = pull_requests[0]
    else:
        pr = api(
            "POST",
            f"repos/{repo}/pulls",
            {
                "title": "Auto-promote verified dev to beta",
                "body": (
                    "Automatically promoted only after successful source-branch "
                    f"push Tests and Security scan for commit {sha}. "
                    "When relevant, Postfix Milter compatibility must also pass. "
                    "No promotion to main or release is performed."
                ),
                "head": "dev",
                "base": "beta",
            },
        )

    if (
        pr["head"]["sha"] != sha
        or pr["head"]["ref"] != "dev"
        or pr["head"]["repo"]["full_name"] != repo
        or pr["base"]["ref"] != "beta"
    ):
        raise RuntimeError("Pull request source changed; refuse to merge.")

    if branch_sha(repo, "dev") != sha:
        print("dev changed after opening the pull request; no promotion.")
        return

    # GitHub must propose exactly the tree that passed dev CI.
    preview = None
    for attempt in range(10):
        current = api("GET", f"repos/{repo}/pulls/{pr['number']}")
        if current["head"]["sha"] != sha or current["base"]["sha"] != branch_sha(repo, "beta"):
            raise RuntimeError("Promotion PR or beta moved while verifying merge")
        preview = current.get("merge_commit_sha")
        if preview:
            break
        time.sleep(3)
    if not preview:
        raise RuntimeError("GitHub did not create a merge preview")
    proposed_tree = api("GET", f"repos/{repo}/commits/{preview}")["commit"]["tree"]["sha"]
    source_tree = api("GET", f"repos/{repo}/commits/{sha}")["commit"]["tree"]["sha"]
    if proposed_tree != source_tree:
        raise RuntimeError("PR merge would introduce an untested tree")

    result = api(
        "PUT",
        f"repos/{repo}/pulls/{pr['number']}/merge",
        {
            "sha": sha,
            "merge_method": "merge",
            "commit_title": "Promote CI-verified dev to beta",
            "commit_message": (
                f"CI-verified dev commit: {sha}\n"
                "Tests + Security scan passed; Postfix compatibility verified "
                "when applicable. No change to main."
            ),
        },
    )
    if not result.get("merged"):
        raise RuntimeError(f"GitHub refused beta promotion: {result}")
    print(f"Promoted dev {sha} to beta via PR #{pr['number']}: {result['sha']}")


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
