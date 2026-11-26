# OpenSigWeave — installation, updates and onboarding contract

**Project-wide product requirements.** Changes to the installer, updater,
mail adapters, admin portal and docs MUST preserve this contract. Do not
silently weaken it or count optional diagnosis as a required installation step.

## Installation: at most four copy/paste blocks

A fresh installation must require no more than **four terminal copy/paste
sections across the OpenSigWeave host and the mail server combined**.
Prerequisites such as Docker, firewall rules and DNS must be documented
upfront and not disguised as extra installer steps. Never claim that external
infrastructure setup is automatic when it is not.

Use an interactive, restart-safe installer that automatically creates secrets,
pulls the approved image, applies migrations and starts services. Configuration
of the local administrator and mail server belongs in a protected first-run
admin wizard, not additional terminal blocks. A one-time admin bootstrap token
must be required before the administrator account is created. The setup endpoint
must be unavailable once initialized, and never rely on a publicly accessible
unauthenticated default password.

The wizard should:
1. Set administrator email and password on first login/setup.
2. Select the mail-server type and adapter.
3. Display only the relevant server-side configuration instructions, with
   no more than the remaining total copy/paste block budget.
4. Verify the integration before marking setup complete; explain when only
   partial verification is available.

Supported adapters and unavailable capabilities must be labeled truthfully.
Do not claim universal mail-server support merely because the core is
adapter-neutral.

## Update: at most two copy/paste blocks

- `./update.sh` selects the **latest published main release** (including a
  pre-release if that is the only published main release), retrieves its exact
  Git release tag/commit and uses the matching GHCR image. Never use an
  arbitrary current main commit with an unrelated moving image tag.
- `./update.sh --beta` retrieves the latest `beta` branch commit, ensures
  the local source matches that commit and **builds the beta image locally**.
- Update must be restart-safe and idempotent on clean, valid installations,
  preserving PostgreSQL volumes, signatures, secrets and configuration.
- Never silently downgrade the database, reset Git history, switch to
  unrelated repository origins, force-update branches, or roll back code
  after a possibly applied schema migration.
- Keep meaningful `--check`, `--dry-run` and recovery diagnostics without
  requiring extra copy/paste sections for an ordinary update.
- Any incompatibility preventing safe one-command updates is a product bug:
  report it, do not claim that skipping checks makes updating always safe.
- Installation and updates must remain functional independently of whether
  adapter health monitoring is enabled.

## Mail-server changes and health monitoring

Minimize changes to Rspamd and other mail servers. The core owns common
configuration/monitoring. A mail adapter implements only its minimal necessary
injection/hook functionality. **No Docker socket inside the web application.**

Admin > System must own:
- monitoring enabled/disabled,
- interval/frequency (validated bounds),
- adapter selection, connection details and last result,
- clear distinctions between reachable, module configured/registered,
  actual signing proven and unknown.

No heartbeat in an idle mail system is proof of failure. Health checks are
**observability**, not fail-closed enforcement if all adapter modules disappear.
Checks must not expose controller passwords, Docker credentials, or unrestricted
server-side HTTP requests; no outbound public controller exposure is required.

## Branch policy

Only `dev`, `beta`, `main`. Work on `dev`. Promotion and release images
are governed by existing CI and approval policy. Do not create additional
feature branches without explicit instruction.

## Acceptance

- CI unit tests for update mode selection, release tag/commit/image consistency,
  branch-source transitions, interruptions, and recovery.
- Browser tests for first-run onboarding, permissions and CSRF.
- Monitoring tests for enabled/disabled, interval changes, adapter unreachable,
  module missing, and unknown status.
- Operator verification of actual SMTP injection (not just health checks).
