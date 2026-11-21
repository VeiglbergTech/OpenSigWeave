# Branch policy for OpenSigWeave

This document defines the release process. Source-code changes to this policy must
be committed to `dev` first. Branch protection is configured in GitHub, not in
repository files, and **is not enforced by this document alone**.

## Allowed paths

| Destination | Allowed source | Conditions |
| --- | --- | --- |
| `dev` | Development or dependency-update work | All new code/configuration changes land here. CI runs on push and PR. |
| `beta` | `dev` in the **same repository** | PR only, source at latest `dev` tip, green regular and security CI on `dev` plus successful PR checks, manual merge. |
| `main` | `beta` in the **same repository** | PR only, source at latest `beta` tip, green CI on `beta`, real-world beta test documented in the PR, explicit maintainer approval and manual merge. |

No workflow should merge, tag or publish `main` automatically.
No direct push, force push, deletion or API ref update is permitted on `beta`
or `main`. Never promote an untested `dev` commit.

### Automation in this repository

- `.github/workflows/tests.yml`: regular tests for pushes and PRs to
  `dev`, `beta` and `main`.
- `.github/workflows/security.yml`: dependency/source, filesystem,
  image and CodeQL security checks for those branches.
- `.github/workflows/postfix-milter-compat.yml`: path-filtered checks.
  It **must not** be a globally required check, or unrelated PRs may wait forever.
- `.github/workflows/promotion-policy.yml`: checks PR source repository,
  source/target branch pairing, current source-tip SHA and successful
  source-branch push runs of regular CI and Security CI. If the path-filtered
  Milter workflow ran, it must also have succeeded.
- `.github/dependabot.yml`: dependency updates target `dev`.

**Important:** A green check on `dev` does not provide branch protection.
The GitHub rulesets below are mandatory; without them direct pushes and PR
merges can bypass the intended policy.

## Required administrator configuration

Open https://github.com/VeiglbergTech/OpenSigWeave/settings/rules
and create **active** branch rulesets, not evaluate-mode rulesets.
If the UI does not offer rulesets, use
https://github.com/VeiglbergTech/OpenSigWeave/settings/branches
to configure classic branch protection. Avoid granting broad bypass access.

### Ruleset: development

- Target branch: `dev`.
- Block force pushes and branch deletion.
- Permit normal development pushes on `dev` (do **not** require PRs for
  `dev` if development requires direct commits).
- Do not enable automatic promotion to `beta`.

### Ruleset: beta

- Target branch: `beta` (exact match).
- **Require a pull request before merging**; no direct push.
- **Require status checks to pass** and preferably **require branches to be
  up to date before merging**.
- Block force pushes and branch deletion; no automatic bypass even for administrators.
- Require these GitHub Actions checks (use their exact displayed names,
  with the GitHub Actions app as expected source):
  - `unit`
  - `Python dependency and source audit`
  - `Filesystem vulnerabilities, misconfiguration and secrets`
  - `CodeQL (python)`
  - `CodeQL (javascript-typescript)`
  - `Scan built application Docker image`
  - `Promotion source and CI gate`
- Initially, the promotion-policy workflow may not yet exist on the target
  branch. Add its check as required **once GitHub has run it**; until then,
  verify the branch source and green source CI manually. Do not silently drop
  other checks just to unblock a PR.
- If at least one independent reviewer is available, require an approving
  review and dismiss stale approvals after new commits. On a one-maintainer
  repository, a required external review can prevent the owner from merging
  their own PRs; keep the explicit manual merge step in that case.

### Ruleset: main

- Target branch: `main` (exact match).
- **Require a pull request before merging**; no direct push.
- Block force pushes and deletion; do not allow automated actors to bypass.
- Keep `Allow auto-merge` disabled at repository level.
- Require a separate maintainer review when there is a second reviewer.
  If only one maintainer exists, reserve the merge permission for that
  maintainer and require manual review of recorded beta-test evidence.
- Require the same six CI checks as for `beta` and the
  `Promotion source and CI gate` once PR checks actually run for `main`.
- **Bootstrap limitation:** `main` currently does not contain the Actions
  workflows. Enabling unavailable required checks can deadlock the first
  promotion. Protect `main` against direct pushes now, leave any proposed
  release as a draft until a real beta test has succeeded, and only perform
  the first `beta -> main` PR merge after explicit owner approval. Then
  enable all required checks on `main` for future releases.
- The built-in review requirement cannot prove that a real-world beta test
  occurred. Before every release, record the tested environment, tested
  commit SHA, test date, outcome, and approver in the PR.

Release PR checklist:

- [ ] PR source is exactly `beta` in `VeiglbergTech/OpenSigWeave`.
- [ ] No divergent or partial cherry-picked release is used.
- [ ] `beta` source-commit regular CI and Security CI were green.
- [ ] The actual application was tested in the designated beta environment.
- [ ] Beta environment, version/SHA, date and result are documented.
- [ ] The maintainer has explicitly authorized the promotion.
- [ ] Merge to `main` is performed manually, never by an Action or bot.

### GitHub Actions / repository settings

- Under **Settings > General > Pull Requests**, leave **Allow auto-merge**
  disabled.
- Under **Settings > Actions > General > Workflow permissions**, use
  **Read repository contents and packages permissions** by default and
  do not grant automated PR approval/creation unless explicitly needed.
- Do not configure automatic `workflow_run`, `push` or
  `release`-triggered promotions. Release tags and GitHub Releases must
  be created manually from a commit that is already on `main`.
- Optionally add a separate tag ruleset for release tags such as `v*`
  to prevent unauthorized creation, rewrite or deletion of release tags.

## Verification after configuring rules

1. PR `dev -> beta`: verify the source-policy check and all mandatory CI
   and security checks must be green before GitHub enables the Merge button.
2. Attempt a test PR from another branch to `beta`: policy check must fail.
3. Verify direct and force pushes to `beta` and `main` are rejected.
4. PR `beta -> main`: verify that actual beta-test evidence and explicit
   approval are required and that source checks pass.
5. Verify the release never occurs by merging or publishing automatically.

As long as the GitHub App cannot configure branch protection, the above
rulesets require manual repository-administrator action. Do not treat the
policy as fully enforced until the rules appear as **Active** in GitHub.
