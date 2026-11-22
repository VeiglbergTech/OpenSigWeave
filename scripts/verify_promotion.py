#!/usr/bin/env python3
"""Fail-closed, read-only verification of exact commit and tree provenance."""
import os
import sys
from scripts.auto_promote_beta import api, expected_sha, requires_postfix, TESTS_WORKFLOW, SECURITY_WORKFLOW, POSTFIX_WORKFLOW

def workflow_result(runs, workflow, sha):
    matching = [r for r in runs if r.get("path") == workflow and r.get("head_sha") == sha
                and r.get("head_branch") == "dev" and r.get("event") == "push"]
    matching.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or 0), reverse=True)
    for r in matching:
        if r.get("status") == "completed" and r.get("conclusion") == "cancelled":
            continue
        return "success" if r.get("status") == "completed" and r.get("conclusion") == "success" else "failed"
    return "missing"

def beta_dev_proof(repo, beta_sha):
    """Prove beta is the exact already-tested commit in dev ancestry."""
    beta_sha = expected_sha(beta_sha)
    comparison = api("GET", f"repos/{repo}/compare/{beta_sha}...dev")
    base = comparison.get("merge_base_commit") or {}
    if comparison.get("behind_by") != 0 or base.get("sha") != beta_sha:
        raise ValueError("beta has commits not in dev; fast-forward provenance violated")
    return beta_sha

def source_ci_proof(repo, sha):
    resp = api("GET", f"repos/{repo}/actions/runs?branch=dev&head_sha={sha}&event=push&per_page=100")
    runs = resp.get("workflow_runs", [])
    if len(runs) >= 100:
        raise ValueError("CI run list may be truncated")
    commit = api("GET", f"repos/{repo}/commits/{sha}")
    parents = commit.get("parents", [])
    postfix = not parents
    if parents:
        previous = expected_sha(parents[0]["sha"])
        files = api("GET", f"repos/{repo}/compare/{previous}...{sha}").get("files", [])
        postfix = len(files) >= 300 or requires_postfix([f["filename"] for f in files])
    paths = [TESTS_WORKFLOW, SECURITY_WORKFLOW]
    if postfix or any(r.get("path") == POSTFIX_WORKFLOW for r in runs):
        paths.append(POSTFIX_WORKFLOW)
    states = {path: workflow_result(runs, path, sha) for path in paths}
    if any(s != "success" for s in states.values()):
        raise ValueError(f"Exact-source push CI is incomplete or unsuccessful: {states}")
    return states

def verify(repo, target, source, source_repo, source_sha, pr_number):
    if repo != "VeiglbergTech/OpenSigWeave" or source_repo != repo:
        raise ValueError("External repository/fork is forbidden")
    if (target, source) != ("main", "beta"):
        raise ValueError("Only beta -> main manual pull requests are allowed")
    source_sha = expected_sha(source_sha)
    if api("GET", f"repos/{repo}/branches/{source}")["commit"]["sha"] != source_sha:
        raise ValueError("Stale source branch tip")
    dev_sha = beta_dev_proof(repo, source_sha)
    checks = source_ci_proof(repo, dev_sha)
    pr = api("GET", f"repos/{repo}/pulls/{pr_number}")
    if (pr["head"]["sha"] != source_sha or pr["head"]["ref"] != source or pr["base"]["ref"] != target
            or pr["head"]["repo"]["full_name"] != repo):
        raise ValueError("Pull request source changed")
    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        raise ValueError("GitHub has no merge preview")
    preview = api("GET", f"repos/{repo}/commits/{expected_sha(merge_sha)}")
    src = api("GET", f"repos/{repo}/commits/{source_sha}")
    if preview["commit"]["tree"]["sha"] != src["commit"]["tree"]["sha"]:
        raise ValueError("Untested PR merge tree differs from verified source")
    return checks

if __name__ == "__main__":
    try:
        result = verify(os.environ["GH_REPO"], os.environ["TARGET_BRANCH"], os.environ["SOURCE_BRANCH"],
                        os.environ["SOURCE_REPOSITORY"], os.environ["SOURCE_SHA"], int(os.environ["PR_NUMBER"]))
        print(f"Verified beta ancestry, exact dev push CI, and merge preview tree: {result}")
        print("This is a technical CI gate, not a manual beta -> main release approval.")
    except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
        print(f"::error::Promotion policy failed: {exc}", file=sys.stderr)
        sys.exit(1)
