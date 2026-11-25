# OpenSigWeave GitHub CI

The only active development/release branches are dev, beta and main. A one-time history archive branch (archive/beta-before-fast-forward-20261122) preserves the pre-migration beta merge history; it is not a release channel. Changes are committed to dev. Tests and Security scan must pass on the exact dev push SHA. The Postfix workflow is required for relevant source changes. A benchmark is informative, never a required promotion check.

## Workflows

| Workflow | Purpose |
| --- | --- |
| tests.yml | Python, JavaScript, shell, actionlint, migrations, Docker, browser and backup/restore regression |
| security.yml | Dependency audit, Bandit, Gitleaks history scan, Trivy filesystem/image, CodeQL and exact-SHA open CodeQL finding gate |
| postfix-milter-compat.yml | Conditional real Postfix and PyMilter integration |
| promotion-policy.yml | Manual beta to main PR proof: beta ancestry in dev, exact beta commit dev CI, and matching PR preview tree |
| auto-promote-beta.yml | Force-free fast-forward from verified dev SHA to beta, with zero merge/squash/rebase commits |
| benchmark.yml | Filtered dev quick benchmark; manually requested stress run |

Beta is advanced by Git refs PATCH with force=false, only if its previous commit is an ancestor of the exact successfully tested dev commit. No PR merges, new commit objects or history rewrites occur during routine promotion. An installed GitHub App mints a short-lived, repository-scoped installation token for the fast-forward ref update. Unlike GITHUB_TOKEN updates, this triggers beta push CI. Exact-SHA dev push Tests + Security scan are still the mandatory promotion gate; beta CI does not replace them. Missing, stale or failed source CI blocks promotion. The manual beta-to-main PR gate proves that beta is a real commit in dev history and verifies exact beta SHA dev CI.

## Required administrative setup

1. Install the **GitHub App on VeiglbergTech/OpenSigWeave** (creating an App and its secrets alone is insufficient). App repository permissions: **Contents read/write**, **Actions read-only**; no Pull requests or Administration permission is needed. Store both Actions secrets `CI_PROMOTION_APP_ID` and `CI_PROMOTION_APP_PRIVATE_KEY` (complete PEM key). The workflow requires both and fails closed if token creation or installation is missing; it never moves main.
2. Repository Settings -> Actions -> General: keep default GITHUB_TOKEN read-only (the workflow uses a separate short-lived App token for beta writes); check organization restrictions. No automatic beta pull request creation or approval is needed. Keep fork PR tokens read-only, without secrets, and approval for untrusted contributors.
3. Repository Settings -> Rules -> Rulesets (or Branches): protect **main** with a PR from beta, required human review, and passing Tests, Security scan, and Promotion source and CI gate checks. Disallow direct pushes, bot bypasses, force pushes and deletion. Protect **beta** against force push and deletion; if restricting direct writes, allow the dedicated installed App to fast-forward beta as the only approved actor. A mandatory PR rule on beta without an explicit App bypass will block the direct fast-forward automation. Check real check names and do not require path-filtered jobs that can be skipped.
4. Repository Security -> Advanced Security / Code scanning: verify whether default CodeQL setup duplicates Advanced Setup from security.yml. Keep Advanced Setup and its security-extended queries; only remove duplicate default setup after Advanced Setup has a working analysis. Do not treat a green CodeQL job as proof of zero security alerts.
5. Repository Settings -> Environments: no deployment approval gates on ordinary CI; keep explicit manual approval for any production release/publishing environment.

## Troubleshooting

Inspect workflow event, branch, exact SHA, failed step, and merge preview tree. A 403 means missing permissions or a ruleset denial, not a reason to bypass security. Use the code scanning alerts page to individually review findings by rule, severity, file, line and exploitability; compare huge PRs against the base branch to separate old and new findings. Full benchmark artifacts are generated only for relevant changes or explicit manual runs.

**Not managed by repository code:** branch rulesets, required checks, and other repository-side access restrictions are GitHub-side settings. On 2026-11-22, the API showed protected=false for dev, beta and main, with no active repository rulesets. Do not mistake the in-repository policy for enforcement against direct pushes.

## CodeQL warning gate

Security scan has a dev-push-only CodeQL findings gate. It requires fresh CodeQL analyses for the exact commit and checks open medium/high/critical security alerts on dev using GitHub's code-scanning API. Any unresolved alert or API/ingestion failure blocks automatic beta promotion. An administrator must investigate and fix or individually justify the findings; do not disable analysis to make the gate pass. The gate needs GITHUB_TOKEN security-events:read. When CodeQL Default Setup duplicates Advanced Setup, keep only one verified active scanning configuration.
