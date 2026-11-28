# Minimal verified stable installation (prepared, not yet published)

> **Availability as of 28 November 2026:** GitHub Releases currently contains
> only `2026.11-alpha`, which has **no minimal installer asset**. The stable,
> Git-free installation procedure below is **not yet available for a published
> release**. Do not invent a stable tag or claim Release 2 is live. Until the
> maintainer publishes and approves a stable release with the installer asset,
> follow the existing [Git checkout instructions](INSTALL_UPDATE.md).

## Architecture and exact package contents

The standard installation starts exactly two Compose services:
`opensigweave` and `postgres`. No Redis, Rspamd, reverse proxy, monitoring
agent, provisioning agent, backup worker, Node.js or build container runs on the
target host. Built-in web/API, LDAP/OIDC, rendering, backup and scheduling
functions remain within the application image.

The **exact allowlisted** versioned host archive is named
`opensigweave-<APPROVED_STABLE_TAG>-install.tar.gz` and contains:

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

These **13 files** are the complete archive. No Git data, tests, CI files,
application source, Python dependencies, Node.js/npm modules, CKEditor source,
migrations or mailserver-specific adapter files belong to it. The entire
compressed archive has a strict 3 MiB limit, each file a 2 MiB limit and total
expanded content a 2 MiB limit. Packaging is deterministic (stable tar
metadata and gzip timestamp). The final size and SHA-256 are release-specific,
not fixed in advance.

`.opensigweave-release.json` contains the exact approved Git commit,
version, per-file SHA-256 and format revision. The **whole archive SHA-256**
must independently match the digest in the published GitHub Release asset
metadata over TLS. The installer then validates the manifest and independently
checks the stable Git tag against the approved `main` history. A matching
version-pinned GHCR application image is mandatory. The installer checks its
`org.opencontainers.image.revision` label against the approved commit before
running it. A moving alias such as `:latest`, `:alpha` or `:beta` is never an
acceptable stable package deployment.

Image references:
- Application: `ghcr.io/veiglbergtech/opensigweave:<APPROVED_STABLE_TAG>`
- Database: `postgres:17-alpine@sha256:d3244e8ce3f165ee3c93d9c4a4b1d0fe48b3a89e2287b717fdcbbde92d405065`

The PostgreSQL image uses a pinned multi-platform manifest digest. The
**currently implemented GHCR publishing workflow builds the application for
linux/amd64 only**, so the verified bundle installer requires an x86_64 Linux
host until a multi-platform release process is implemented and verified.

## Host requirements

- Linux x86_64, Docker Engine 24+, Docker Compose plugin 2.20+, a working Docker daemon
- Python 3 (host-side **standard library only**) and a POSIX-compatible shell
- At least 2 GiB RAM and 6 GiB free disk space, plus backup space if enabled
- TLS-trusted outbound access to GitHub API, GitHub Release assets and GHCR
- Docker permissions and a private installation directory (0700 recommended)
- Optionally `curl` for the one-time archive download shown below; no Git, GitHub CLI, npm or Node.js

Python stays because existing stdlib-only configuration, secret generation
(`secrets`/scrypt), manifest verification, safe archive handling, backups
coordination and update recovery are safer to retain than rewriting in shell.
The host scripts do not install Docker, edit the firewall or auto-deploy an
HTTPS reverse proxy. Docker access itself grants privileged host control.

## Fresh installation: download, verify, extract, install

**Only after a stable release and matching asset are published**, substitute
the exact authorized tag for `APPROVED_STABLE_TAG`. Use a new empty directory
that you own. No Git checkout or GitHub CLI is required.

```bash
umask 077
VERSION="APPROVED_STABLE_TAG"
ASSET="opensigweave-$VERSION-install.tar.gz"
curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
  -o "release-$VERSION.json" \
  "https://api.github.com/repos/VeiglbergTech/OpenSigWeave/releases/tags/$VERSION"
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
  -o "$ASSET" \
  "https://github.com/VeiglbergTech/OpenSigWeave/releases/download/$VERSION/$ASSET"
python3 - "$VERSION" <<'PY'
import hashlib, io, json, re, shutil, sys, tarfile
from pathlib import Path

version = sys.argv[1]
if not re.fullmatch(r"20\d\d\.(?:0[1-9]|1[0-2])", version):
    raise SystemExit("Use the exact approved stable YYYY.MM tag")
asset = f"opensigweave-{version}-install.tar.gz"
release = json.loads(Path(f"release-{version}.json").read_text())
if (release.get("tag_name") != version or release.get("draft")
        or release.get("prerelease") is not False or not release.get("published_at")):
    raise SystemExit("Not a published stable release")
items = [a for a in release.get("assets", []) if a.get("name") == asset]
if len(items) != 1 or items[0].get("browser_download_url") != (
        f"https://github.com/VeiglbergTech/OpenSigWeave/releases/download/{version}/{asset}"):
    raise SystemExit("Expected official release installer asset missing")
digest = items[0].get("digest", "")
content = Path(asset).read_bytes()
if (not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
        or len(content) > 3 * 1024 * 1024
        or hashlib.sha256(content).hexdigest() != digest[7:]):
    raise SystemExit("Missing or incorrect published SHA-256 digest")
allowed = {
    "install.sh", "update.sh", "generate_config.sh", "compose.yaml",
    ".env.example", "auth_config.py", "scripts/config_manager.py",
    "scripts/deploy_common.py", "scripts/install_manager.py",
    "scripts/update_manager.py", "scripts/release_target.py",
    "scripts/release_bundle.py", ".opensigweave-release.json",
}
files = {}
total = 0
with tarfile.open(fileobj=io.BytesIO(content), mode="r|gz") as archive:
    for member in archive:
        if member.name not in allowed or member.name in files or not member.isfile():
            raise SystemExit("Unsafe, duplicate or unexpected archive entry")
        if member.size > 2 * 1024 * 1024:
            raise SystemExit("Oversized archive entry")
        total += member.size
        if total > 2 * 1024 * 1024:
            raise SystemExit("Archive expands past the allowed limit")
        body = archive.extractfile(member).read(2 * 1024 * 1024 + 1)
        if len(body) != member.size:
            raise SystemExit("Truncated or oversized archive entry")
        files[member.name] = body
if set(files) != allowed:
    raise SystemExit("Missing release archive entries")
manifest = json.loads(files[".opensigweave-release.json"])
if (manifest.get("format") != "opensigweave-release-bundle-v1"
        or manifest.get("tag") != version
        or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("revision", ""))
        or set(manifest.get("files", {})) != allowed - {".opensigweave-release.json"}):
    raise SystemExit("Invalid release manifest")
for name, expected in manifest["files"].items():
    if not isinstance(expected, str) or hashlib.sha256(files[name]).hexdigest() != expected:
        raise SystemExit(f"Corrupt release file: {name}")
destination = Path(f"opensigweave-{version}")
destination.mkdir(mode=0o700, exist_ok=False)
try:
    for name, content in files.items():
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o700 if name.endswith(".sh") else 0o600)
except BaseException:
    shutil.rmtree(destination)
    raise
print(f"Verified installer extracted to {destination}; commit {manifest['revision']}")
PY
cd "opensigweave-$VERSION"
./install.sh
```

The Python block is **local, visible code**; it never runs a downloaded script.
It refuses links, devices, traversal entries, unexpected paths, oversized
content and modified files before extraction. It refuses to overwrite an
existing installation directory. The downloaded `release-*.json` and
`*.tar.gz` may be removed after successful setup; they are not required for
updates.

Installation checks prerequisites and package contents, safely creates or
reuses a protected `.env`, starts PostgreSQL and pulls the exact application
image without a local build. Container health, Alembic revision, signature
storage, identity-provider storage and application HTTP reachability are
validated. When prompted, use a **private bind IP** or `127.0.0.1` for a
same-host reverse proxy; the default `0.0.0.0` must not be exposed publicly.

Read the one-time `.admin-setup-token` (0600) on the host. Open
`https://<your-authorized-host>/setup` through your HTTPS reverse proxy,
complete the administrator setup, then securely delete the token file.
TLS termination, proxy access controls, DNS and certificates are operator
responsibilities. Never expose the unencrypted management port publicly.

## Normal Git-free updates

```bash
./update.sh --version
./update.sh --check
./update.sh --prefetch
./update.sh
```

Only **published stable GitHub Releases** with exact tags are eligible for
package-mode updates; published prereleases never replace an installed stable
version. Downgrades and moved release revisions are rejected. The updater
fetches the target versioned archive and verifies GitHub's SHA-256 and the
per-file manifest before touching existing files, pulls the matching GHCR
image, validates its revision and checks any enabled backup rule.

**Backup is optional:** with no configured enabled rules, no backup is
required. A configured backup that fails blocks the update unless the
administrator explicitly confirms continuation (or uses `--yes
--continue-without-backup`). A successful encrypted backup is verified before
proceeding. No backup worker container is installed.

Only the allowlisted host files are replaced; `.env`, original secrets,
backup settings, signature files and named PostgreSQL/identity/signature/backup
volumes are preserved. Schema migrations run serialized after a protected
update journal is recorded in
`.opensigweave/opensigweave-update-state.json` (0600). A failed or interrupted
file switch, migration or restart leaves the journal for manual review.
**There is no automatic downgrade of a potentially migrated database.**
Recover on a fresh isolated target with a verified backup and the correct
recovery key; never run `docker compose down -v` as a recovery shortcut.

`--dry-run` performs a read-only preflight; `--skip-start` intentionally
leaves the application stopped after migration. In package mode an installed
image tag conflicting with the package is not silently rewritten by running
`./install.sh`. Use the verified update path instead.

## Compatibility and Mail Server adapters

Existing Git checkout installations remain supported. No automatic migration
to bundle mode occurs, and no existing source checkout or secrets are removed.
Development and beta installations continue using the explicit source-build
Compose overlay and their existing branching policy. The legacy main/alpha
checkout instructions remain accurate while only the alpha release exists.
Migration to the bundle format requires a reviewed, backed-up procedure and
is **not** automated by this release task.

Mailserver adapters are deliberately **not** part of the host archive.
After core setup, select the desired server under **Admin → System → Mail
Server**. Download only that release-compatible adapter's integration files to
the mailserver as described in [Mailcow integration](MAILCOW_RSPAMD_INTEGRATION.md)
and [adapter documentation](ADAPTERS.md). A same-host or remote Mailcow
deployment is a separate integration step. SMTP/DKIM/MIME acceptance checks
are separate from core installation tests.

## Release publication and outstanding acceptance tests

`.github/workflows/publish-release-installer.yml` can attach the package
**only to an already published, non-draft stable GitHub Release**. It checks
the tag/main ancestry and pulls the corresponding exact-version GHCR image
to confirm its source revision, version and embedded release metadata. It
runs offline package tests before generating the deterministic archive. The
single archive is then uploaded without overwriting existing assets and its
GitHub-published digest is compared to the locally calculated SHA-256.
A missing image blocks publication of the installer. If the image was still
building at the `release.published` event, a maintainer can rerun through
the explicit `workflow_dispatch` input **after** confirming the image exists.
The workflow does not create Git tags, releases, branches or Docker images.

Before production approval, perform independent **fresh installation**, real
**older-to-newer release update**, failure/recovery and web/API/database
persistence smoke tests on an isolated Docker host. Once a stable Release 2 is
authorized and published, repeat the archive download/verification using the
**real published asset**, not mocks or a local archive. Until those checks
pass, deterministic package generation alone is **not** production readiness.
