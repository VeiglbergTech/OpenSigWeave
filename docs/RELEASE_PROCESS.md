# OpenSigWeave release channels, GHCR and installation

**Status (28 November 2026):** the published `2026.11-alpha` prerelease has no
minimal installer asset. No stable Release 2 installer or stable tag has been
published or approved by this work.

## Version tags (no extra Git branches)

| Git release tag | GHCR version tag | Moving image tag |
| --- | --- | --- |
| `2026.11-alpha` | `:2026.11-alpha` | `:alpha` |
| `2026.11-alpha1` | `:2026.11-alpha1` | `:alpha` |
| `2026.11-alpha2` | `:2026.11-alpha2` | `:alpha` |
| `2026.11-beta` | `:2026.11-beta` | `:beta` |
| `2026.11-beta1` | `:2026.11-beta1` | `:beta` |
| `2026.11` | `:2026.11` | `:latest` |

The image name is `ghcr.io/veiglbergtech/opensigweave`. A release adds an immutable
version-named tag and **only one corresponding moving alias**. Alpha never changes
`beta` or `latest`. `latest` represents stable releases only. The three moving tags
are **container-image tags, not Git branches**. Git branches remain `dev`, `beta`,
and `main`. New versions are explicitly created by the maintainer; no daily
version bump, main promotion, Git tag creation or Release publication is automatic.

## Release approval and publishing

1. Make source changes exclusively on `dev`; existing CI fast-forwards tested
   code to `beta` after verified Tests and Security scan.
2. Test the exact `beta` commit. Promotion from `beta` into `main` requires
   **explicit maintainer approval**. Publishing workflow never touches branches.
3. Wait until **main push** Tests and Security scan succeed for that same SHA.
4. The maintainer creates and pushes a tag like `2026.11-alpha` referencing
   the current main HEAD. The [GHCR workflow](../.github/workflows/publish-ghcr.yml)
   starts automatically. It rechecks the current main SHA, both successful
   main push workflows and the exact tag format before any Docker build.
5. The workflow uses only `[self-hosted, linux, x64]`, builds one Docker image,
   attaches OCI source/revision/version labels, and pushes the version tag
   plus the corresponding `alpha`, `beta` or `latest` tag.
6. Review the image, set package visibility deliberately, smoke-test the
   released image and publish release notes. A GitHub Release entry can be
   created manually; the GHCR workflow does not create one.

Tags pointing at `dev`, `beta`, an earlier main commit or a main commit
without successful *push* checks are refused. The gate waits a bounded
time for currently running checks, then fails without publishing if they
cannot be confirmed.

## Verified GitHub billing rules (November 2026)

- GitHub states that self-hosted Actions runners are free and do not use
  included compute usage. The package workflow never selects a hosted
  runner, a larger runner or an Actions artifact upload.
- GitHub Container Registry image storage and bandwidth are **currently
  free**. GitHub says a policy change would be announced in advance.
- Existing source CI **does** use hosted standard runners in this private
  repository; it draws from the organization's included usage and may incur
  charges beyond it if billing has been configured. This package workflow
  is not a blanket guarantee about other workflows, electricity or network.

Official sources:

- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/billing/concepts/product-billing/github-packages

## GHCR visibility

GitHub creates a new GHCR package as **private** even if its source is linked
to a public repository. For anonymous pulls, visit the organization Packages
page, open the `opensigweave` package, use **Package settings**, and set
visibility to **Public**. Only do so after reviewing the image content:
making the image public may reveal application source bundled in the image
even if the repository is private. Private packages need authenticated Docker
access (for example an appropriate read-only GitHub token); the installer
does not create or retain GitHub credentials.

## Main installation and update: published image only

The existing Git-based alpha/main installation path below remains supported.
Once an explicitly approved **stable release** and verified installer asset
exist, the preferred production path will be the [minimal Git-free stable
installation](MINIMAL_RELEASE_INSTALL.md). Only stable releases receive an
installer bundle; alpha/beta tags stay on the legacy source workflow.

For the Git checkout installation:

```bash
git clone --branch main https://github.com/VeiglbergTech/OpenSigWeave.git
cd OpenSigWeave
./install.sh
./generate_config.sh --check
docker compose ps
```

The default Compose image is `ghcr.io/veiglbergtech/opensigweave:alpha`.
Optionally pin `OSW_IMAGE_TAG="2026.11-alpha"` (or `alpha1`, etc.)
in the protected `.env`; change a pinned tag intentionally before updating
to a newer approved Git revision. The main installer **pulls** the image
and compares its `org.opencontainers.image.revision` label with the
checked-out commit before starting. It **never builds the release image
locally**. This also prevents silently deploying a rolling alias which
belongs to the wrong source commit.

```bash
./update.sh --check
./update.sh --prefetch
./update.sh --dry-run
./update.sh
```

On main, the updater pre-pulls and verifies the image of the **target Git
commit before changing source or applying database migrations**. It retains
optional backup semantics: without configured backup, updates proceed
normally; a configured failed backup requires explicit administrator
approval. No named volumes are removed and no automatic database rollback
is attempted.

**Dev/beta staging exception:** no per-commit images are published from
the development branches. The installer/updater there uses the explicit
`compose.build.yaml` overlay to build the unreleased checkout locally.
For a manual development run:

```bash
COMPOSE_FILE=compose.yaml:compose.build.yaml docker compose up -d --build
```

Only the `main` path is a prebuilt GHCR release deployment. CI and the
performance benchmark also use the build overlay.

## Security boundaries and known limits

The release workflow requires current main HEAD and full successful
main push Tests and Security scan. It uses `GITHUB_TOKEN` with `contents:read`,
`actions:read` and `packages:write`, not a personal access token.
Keep self-hosted runners isolated from untrusted pull-request code;
review workflow/Dockerfile changes before tagging. The GHCR source SHA
label ensures release/deployed source alignment but does not certify
third-party mail clients or prove independent fail-closed signing when
both the Rspamd injector and its separate guard are missing. See
[mail acceptance matrix](MAIL_USAGE_ACCEPTANCE_MATRIX.md).
