#!/usr/bin/env bash
#
# OpenSigWeave container builder
# Builds a versioned image and publishes it to GitHub Container Registry.
#
# Usage:
#   bash build/build-opensigweave.sh --check
#   bash build/build-opensigweave.sh --dry-run
#   GHCR_USERNAME=... GHCR_TOKEN=... bash build/build-opensigweave.sh
#
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
DOCKERFILE_PATH="${PROJECT_ROOT}/Dockerfile"
BUILD_CONTEXT="${PROJECT_ROOT}"
COMPOSE_FILE="${PROJECT_ROOT}/compose.yaml"

IMAGE_NAME="${IMAGE_NAME:-opensigweave}"
REGISTRY="${REGISTRY:-ghcr.io}"
REGISTRY_NAMESPACE="${REGISTRY_NAMESPACE:-veiglbergtech}"
REGISTRY_IMAGE="${REGISTRY}/${REGISTRY_NAMESPACE}/${IMAGE_NAME}"
LABEL_NAMESPACE="${LABEL_NAMESPACE:-org.opensigweave.build}"
EXPECTED_BRANCH="${EXPECTED_BRANCH:-architecture/refactor-foundation}"

BUILD_IMAGE="${IMAGE_NAME}:build-$$"
VERSION=""
STATE_HASH=""
SOURCE_STATE_HASH=""
SOURCE_REVISION=""
BUILD_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DRY_RUN=0
CHECK_ONLY=0

log() { printf '[OpenSigWeave] %s\n' "$*"; }
die() { printf '[OpenSigWeave] ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
    if [[ -n "${BUILD_IMAGE:-}" ]]; then
        docker image rm "$BUILD_IMAGE" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
OpenSigWeave Docker image builder

Options:
  --check       Check repository layout and local prerequisites; do not build.
  --dry-run     Show the planned image/release without building or pushing.
  --help        Show this help.

Environment:
  GHCR_USERNAME       GitHub username (optional if already logged in).
  GHCR_TOKEN          GitHub token with write:packages (used only for docker login).
  REGISTRY            Registry hostname (default: ghcr.io).
  REGISTRY_NAMESPACE  Registry namespace (default: veiglbergtech).
  IMAGE_NAME          Image name (default: opensigweave).
  EXPECTED_BRANCH     Required Git branch (default: architecture/refactor-foundation).

The script requires a clean Git worktree. It never modifies source files or Git refs.
A successful build publishes YYYY.MM.N first, then updates latest.
EOF
}

while (($#)); do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
    shift
done

require_file() {
    [[ -f "$1" ]] || die "Required file missing: $1"
}

require_file "$DOCKERFILE_PATH"
require_file "$COMPOSE_FILE"
require_file "${PROJECT_ROOT}/requirements.txt"
require_file "${PROJECT_ROOT}/.dockerignore"

command -v git >/dev/null 2>&1 || die "git is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

cd "$PROJECT_ROOT"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Project directory is not a Git repository"

CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || die "Expected branch '$EXPECTED_BRANCH', found '$CURRENT_BRANCH'. Refusing to build."

if [[ -n "$(git status --porcelain)" ]]; then
    die "Git worktree is not clean. Commit or discard local changes before building."
fi

SOURCE_REVISION="$(git rev-parse HEAD)"
DOCKERFILE_HASH="$(sha256sum "$DOCKERFILE_PATH" | awk '{print $1}')"
REQUIREMENTS_HASH="$(sha256sum "${PROJECT_ROOT}/requirements.txt" | awk '{print $1}')"
SOURCE_TREE_HASH="$(git archive HEAD | sha256sum | awk '{print $1}')"
SOURCE_STATE_HASH="$(printf '%s\n%s\n%s\n' "$SOURCE_TREE_HASH" "$DOCKERFILE_HASH" "$REQUIREMENTS_HASH" | sha256sum | awk '{print $1}')"

if ((CHECK_ONLY)); then
    log "Repository layout: OK"
    log "Branch: $CURRENT_BRANCH"
    log "Source revision: $SOURCE_REVISION"
    log "Target image: $REGISTRY_IMAGE"
    log "Source state: $SOURCE_STATE_HASH"
    exit 0
fi

if ((DRY_RUN)); then
    log "Dry run: no Docker commands, registry login, build or push will be performed."
    log "Source revision: $SOURCE_REVISION"
    log "Target image: $REGISTRY_IMAGE"
    log "Source state: $SOURCE_STATE_HASH"
    log "Version format: YYYY.MM.N (UTC)"
    log "Tags to publish after a successful build: version tag, then latest"
    exit 0
fi

command -v docker >/dev/null 2>&1 || die "Docker is required"
docker info >/dev/null 2>&1 || die "Docker daemon is not reachable"
docker buildx version >/dev/null 2>&1 || die "Docker Buildx is required"

if [[ -n "${GHCR_TOKEN:-}" ]]; then
    [[ -n "${GHCR_USERNAME:-}" ]] || die "Set GHCR_USERNAME when GHCR_TOKEN is supplied"
    printf '%s' "$GHCR_TOKEN" | docker login "$REGISTRY" --username "$GHCR_USERNAME" --password-stdin >/dev/null
    log "Authenticated to $REGISTRY"
else
    log "No GHCR_TOKEN supplied; using existing Docker registry credentials."
fi

# Include the resolved base-image digest in the build state. This detects upstream
# base-image updates even when the OpenSigWeave source has not changed.
BASE_IMAGE="$(awk '$1 == "FROM" { print $2; exit }' "$DOCKERFILE_PATH")"
[[ -n "$BASE_IMAGE" ]] || die "Could not determine the Docker base image from the Dockerfile"
docker pull "$BASE_IMAGE" >/dev/null
BASE_DIGEST="$(docker image inspect "$BASE_IMAGE" --format '{{range .RepoDigests}}{{println .}}{{end}}' | sort | head -n 1)"
[[ -n "$BASE_DIGEST" ]] || die "Could not resolve a digest for base image $BASE_IMAGE"
STATE_HASH="$(printf '%s\n%s\n' "$SOURCE_STATE_HASH" "$BASE_DIGEST" | sha256sum | awk '{print $1}')"

# Pull latest only for a comparison. A missing remote tag is expected on first publish.
if docker pull "$REGISTRY_IMAGE:latest" >/dev/null 2>&1; then
    REMOTE_STATE="$(docker image inspect "$REGISTRY_IMAGE:latest" --format "{{ index .Config.Labels \"$LABEL_NAMESPACE.state\" }}")"
    if [[ "$REMOTE_STATE" == "$STATE_HASH" ]]; then
        log "The published latest image already matches this source state. Nothing to do."
        exit 0
    fi
else
    log "No readable latest image found; preparing the first or recovery build."
fi

MONTH_TAG="$(date -u +%Y.%m)"
NEXT_BUILD=1
while docker manifest inspect "$REGISTRY_IMAGE:${MONTH_TAG}.${NEXT_BUILD}" >/dev/null 2>&1; do
    ((NEXT_BUILD+=1))
done
VERSION="${MONTH_TAG}.${NEXT_BUILD}"
log "Building $REGISTRY_IMAGE:$VERSION"

docker buildx build \
    --load \
    --file "$DOCKERFILE_PATH" \
    --label "$LABEL_NAMESPACE.version=$VERSION" \
    --label "$LABEL_NAMESPACE.state=$STATE_HASH" \
    --label "$LABEL_NAMESPACE.source-revision=$SOURCE_REVISION" \
    --label "$LABEL_NAMESPACE.build-date=$BUILD_TIMESTAMP" \
    --tag "$BUILD_IMAGE" \
    "$BUILD_CONTEXT"

# Basic image-level smoke checks. Full Compose/database startup is covered by CI.
docker run --rm --entrypoint python "$BUILD_IMAGE" -m pip check
docker run --rm --entrypoint python "$BUILD_IMAGE" -c 'import alembic, fastapi, sqlalchemy, uvicorn; print("OpenSigWeave runtime dependencies: OK")'

docker tag "$BUILD_IMAGE" "$REGISTRY_IMAGE:$VERSION"
docker push "$REGISTRY_IMAGE:$VERSION"
log "Published version tag: $REGISTRY_IMAGE:$VERSION"

docker tag "$BUILD_IMAGE" "$REGISTRY_IMAGE:latest"
docker push "$REGISTRY_IMAGE:latest"
log "Updated rolling tag: $REGISTRY_IMAGE:latest"

log "Build and publish completed successfully."
log "Pull with: docker pull $REGISTRY_IMAGE:$VERSION"
