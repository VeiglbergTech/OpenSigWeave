# Minimal verified main release installation

> **Release gate:** This installation method is available only when an
> approved GitHub Release actually contains
> `opensigweave-VERSION-install.tar.gz` with a GitHub-published SHA-256 asset
> digest. As of the initial implementation in `dev`, **no new release or
> archive has been published as part of this change**. Existing Git-clone
> deployments are supported and are not silently converted.

## Why this format

A normal published installation needs only the OpenSigWeave application
container and PostgreSQL. There are no additional default Redis, monitoring,
backup-worker or provisioning containers. Signature, identity and backup data
continue to live in independent persistent Docker Compose volumes.

Previously the default installer fetched a full repository and needed Git and
Python 3 on the installation host; the application image itself was already
prebuilt. The new **optional main release archive** replaces the source
checkout with a compact, allowlisted host-tool package. In particular it
contains no tests, source code of the web application, CKEditor development
assets, CI configuration, migrations or unrelated mailserver adapters.

### Installed files

The versioned installer archive contains these exact paths:

```text
install.sh
update.sh
generate_config.sh
compose.yaml
.env.example
auth_config.py
scripts/config_manager.py
scripts/deploy_common.py
scripts/install_manager.py
scripts/update_manager.py
scripts/release_target.py
scripts/release_bundle.py
.opensigweave-release.json
```

The last file is an internal SHA-256 file manifest tied to the exact approved
40-character release commit and version tag. In addition, the **whole archive**
must match the SHA-256 digest published in GitHub's Release asset metadata
*before extracting or executing any scripts*. The installer independently
checks that the tag corresponds to a published GitHub Release and a commit on
the authorized `main` history. The deployed application image is pulled from
`ghcr.io/veiglbergtech/opensigweave:VERSION` and its OCI revision label must
match that exact release commit. PostgreSQL uses the existing pinned image
digest in `compose.yaml`. Do not substitute `:alpha`, `:latest`, an
unverified image or a locally built release image.

Secrets, `.env`, the one-time setup token, database/signature/identity volumes,
backups and update recovery journals are **not release assets** and are never
overwritten by the allowlisted package updater.

### Host requirements

- Linux x86_64 or aarch64, Docker Engine 24+, Docker Compose plugin 2.20+
- Python 3 standard library, terminal/shell and Docker daemon access
- At least 2 GiB RAM and 6 GiB available disk capacity
- HTTPS outbound to GitHub API/Releases and GHCR, with valid TLS certificates
- No host Git, npm or Node.js, and no application image builds
- A protected installation directory; Docker access is effectively privileged

Python 3 **has not been eliminated**: retaining the existing audited Python
secret-generation, configuration validation, backup, update journal and
recovery code is safer than an entirely new shell implementation. Mailcow can
run on the same host, another Docker network or another host with an
authenticated HTTPS route to the OpenSigWeave engine. The installation does
not install Docker or modify your mailserver.

### Obtain and verify the release archive

Set `VERSION` to an actual approved and published release, and obtain its
version-specific `opensigweave-VERSION-install.tar.gz` from the official
GitHub Releases page. Before unpacking, inspect the release's **Asset digest**
(`sha256:...`) from GitHub Release metadata using an authenticated HTTPS
connection. Compare it to the downloaded archive via `sha256sum`; do not
execute a downloaded script or run `tar` when the digest is missing or does
not match.

Example verification using the GitHub CLI (optional convenience tool, not a
permanent OpenSigWeave host dependency):

```bash
VERSION="YYYY.MM-alpha"
gh release download "$VERSION" --repo VeiglbergTech/OpenSigWeave \
  --pattern "opensigweave-$VERSION-install.tar.gz"
EXPECTED="$(gh api "repos/VeiglbergTech/OpenSigWeave/releases/tags/$VERSION" \
  --jq '.assets[] | select(.name == "opensigweave-'"$VERSION"'-install.tar.gz") | .digest')"
test "${EXPECTED#sha256:}" != "$EXPECTED" && test "${#EXPECTED}" -eq 71
printf '%s  %s\n' "${EXPECTED#sha256:}" "opensigweave-$VERSION-install.tar.gz" | sha256sum -c -
```

The `gh` example illustrates the independent asset digest check. If
`gh` is unavailable, use HTTPS/curl and the release page or API to obtain
the same trustworthy SHA-256 digest, verify it using `sha256sum`, and only
then extract the archive to a **new protected directory**. Avoid extracting
into a running Git checkout or over existing `.env` files.

```bash
mkdir -m 700 opensigweave-release
tar -xzf "opensigweave-$VERSION-install.tar.gz" -C opensigweave-release
cd opensigweave-release
./install.sh
```

`install.sh` validates every package file against its manifest, re-verifies
the published release's tag ancestry, checks the host and Docker Compose
configuration, generates new independent secrets only if `.env` is absent,
pulls the pinned application and PostgreSQL images, and performs container
health checks. It does not silently rotate existing credentials.

If no valid installer asset exists for the release, use the existing
[Git-based source installation](INSTALL_UPDATE.md). **Do not invent an
installer URL or use an unverified development branch archive.**

## Updates without Git

`./update.sh --check` compares the installed package revision with the most
recent published main GitHub Release; `--dry-run` performs a read-only
preflight, and `--prefetch` downloads and validates the package but does not
replace it or alter containers. `./update.sh` rechecks the target release
history, downloads the exact-version archive and SHA-256 digest, then pulls and
verifies the matching image. It retains the existing optional Backup Rules
logic: no backup configured means no backup required, whereas a **configured
backup that fails** requires explicit administrator approval before proceeding.

The updater records its recovery state under
`.opensigweave/opensigweave-update-state.json` (0700 protected state
directory), replaces only the allowlisted package files, safely migrates
existing config options, pins the new image, and performs serialized Alembic
migration and health checks. If an interrupted update leaves a journal, it
stops for manual investigation instead of silently undoing potentially
migrated database changes. Docker volumes are **never deleted** by these
scripts. Commands `--version`, `--yes`, `--skip-start`, and
`--continue-without-backup` remain available.

The `dev`/`beta` source-checkout workflow remains unchanged. Only a
published, main-approved release is eligible for the minimal package. Future
approved release packaging is controlled by
`.github/workflows/publish-release-installer.yml`; it **does not run on
ordinary dev pushes** and does not create releases.

## Integrations are separate

The standard release archive contains **no** Mailcow/Rspamd, Postfix Milter or
Stalwart deployment files. Install only the matching adapter after selecting
the target server in **Admin → System → Mail Server**. The Mailcow files are
listed in [Mailcow integration](MAILCOW_RSPAMD_INTEGRATION.md), and the
per-domain offline JSON policy is documented in
[Mailcow offline policy](MAILCOW_DOMAIN_FAILURE_POLICIES.md).

## Limits and recovery

This is a new installation format and **has not yet been production
certified**. Test fresh and upgrade installs on disposable hosts before
replacing existing installation procedures. The package's internal manifest
is not a standalone signature; trust derives from TLS, the published GitHub
asset SHA-256 and exact release revision checks. Gitless updates are not
atomic across *all* host files; any interrupted replacement leaves a protected
journal and needs manual intervention. Existing source checkouts are not
converted automatically, and the loader does not perform unattended system
package updates, Docker installation or schema rollback.
