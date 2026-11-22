# OpenSigWeave GitHub CI

The only branches are dev, beta and main. Changes are committed to dev. Tests and Security scan must pass on the exact dev push SHA. The Postfix workflow is required for relevant source changes. A benchmark is informative, never a required promotion check.

## Workflows

| Workflow | Purpose |
| --- | --- |
| tests.yml | Python, JavaScript, shell, actionlint, migrations, Docker, browser and backup/restore regression |
| security.yml | Dependency audit, Bandit, Gitleaks history scan, Trivy filesystem/image and CodeQL |
| postfix-milter-compat.yml | Conditional real Postfix and PyMilter integration |
| promotion-policy.yml | Exact branch and dev CI proof, second-parent ancestry, matching source and PR merge trees |
| auto-promote-beta.yml | CI-gated dev to beta promotion only |
| benchmark.yml | Filtered dev quick benchmark; manually requested stress run |

GitHub-created beta merges do not necessarily trigger a new push workflow when created with GITHUB_TOKEN. The policy therefore proves the underlying dev commit and exact tree rather than requiring an impossible beta push run. Missing, stale or failed source CI blocks promotion. A newer cancelled run cannot erase an earlier successful exact-SHA push run, but a later real failure still blocks. The PR merge preview must equal the verified source tree.

## Required administrative setup

1. Create a dedicated GitHub App, installed only on VeiglbergTech/OpenSigWeave, with **Contents read/write**, **Pull requests read/write**, **Actions read-only**, and no Administration rights. Add repository Actions secrets CI_PROMOTION_APP_ID and CI_PROMOTION_APP_PRIVATE_KEY. The workflow mints a short-lived installation token. Without both, it falls back to GITHUB_TOKEN and bot-created PRs may still need workflow approval.
2. Repository Settings -> Actions -> General: choose read-only default GITHUB_TOKEN, allow GitHub Actions to create and approve pull requests if fallback is required, and check organization-level restrictions. Keep fork PR tokens read-only, without secrets, and approval for untrusted contributors.
3. Repository Settings -> Rules -> Rulesets (or Branches): protect **main** with a PR from beta, required human review, and the successful Tests, Security scan and Promotion source and CI gate checks. Disallow direct pushes, bot bypasses, force pushes and deletion. Protect **beta** to permit only verified dev PRs; let the narrowly scoped App automate this branch only, never main. Check concrete job check names against actual runs. Do not require path-filtered jobs that can be skipped.
4. Repository Security -> Advanced Security / Code scanning: verify whether default CodeQL setup duplicates Advanced Setup from security.yml. Keep Advanced Setup and its security-extended queries; only remove duplicate default setup after Advanced Setup has a working analysis. Do not treat a green CodeQL job as proof of zero security alerts.
5. Repository Settings -> Environments: no deployment approval gates on ordinary CI; keep explicit manual approval for any production release/publishing environment.

## Troubleshooting

Inspect workflow event, branch, exact SHA, failed step, and merge preview tree. A 403 means missing permissions or a ruleset denial, not a reason to bypass security. Use the code scanning alerts page to individually review findings by rule, severity, file, line and exploitability; compare huge PRs against the base branch to separate old and new findings. Full benchmark artifacts are generated only for relevant changes or explicit manual runs.

**Not yet provided by repository code:** branch protection, required checks and any GitHub App secret installation are GitHub-side settings. On 2026-11-22, the API showed protected=false for dev, beta and main, with no active repository rulesets.
