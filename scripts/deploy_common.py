"""Shared, dependency-free host helpers for the OpenSigWeave deployment tools."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
from contextlib import contextmanager
from http.client import HTTPConnection

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ("dev", "beta", "main")
CONFIG_KEYS = (
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
    "SIGNATURE_STORAGE_PATH", "APP_BIND_ADDRESS", "APP_PORT",
    "SECRET_KEY", "ENGINE_API_KEY", "MANAGEMENT_API_KEY",
    "STALWART_HOOK_TOKEN", "APP_ALLOWED_HOSTS", "LOCAL_ADMIN_EMAIL",
    "LOCAL_ADMIN_PASSWORD_HASH", "LOCAL_ADMIN_BOOTSTRAP_HASH", "LOGIN_RATE_LIMIT",
    "AUTH_CALLBACK_RATE_LIMIT", "ENGINE_RATE_LIMIT", "TRUSTED_PROXY_CIDRS",
)
VERSION_RE = re.compile(r"\d+")


class DeploymentError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def ok(message: str) -> None:
    print(f"[ OK ] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[WARN] {message}", flush=True)


def run(*args: str, timeout: int = 300, cwd: Path = ROOT) -> str:
    """Never echo arguments or captured stderr; they may contain credentials."""
    try:
        completed = subprocess.run(
            list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentError(f"Cannot run {args[0]} (missing or timed out)", 3) from exc
    if completed.returncode:
        raise DeploymentError(
            f"{args[0]} failed (exit {completed.returncode}); check its service logs securely", 3
        )
    return completed.stdout.strip()


def git(*args: str) -> str:
    return run("git", *args, timeout=120)


def docker(*args: str, timeout: int = 600) -> str:
    return run("docker", *args, timeout=timeout)


def compose(*args: str, timeout: int = 600) -> str:
    # A source CI may explicitly set COMPOSE_FILE to include the build overlay.
    if os.environ.get("COMPOSE_FILE"):
        return docker("compose", *args, timeout=timeout)
    # On dev and beta the unreleased source can still be built for staging.
    # Main releases use ONLY the published image and never build locally.
    if current_channel() in ("dev", "beta"):
        return docker("compose", "-f", "compose.yaml", "-f",
                      "compose.build.yaml", *args, timeout=timeout)
    return docker("compose", *args, timeout=timeout)


GHCR_REPO = "ghcr.io/veiglbergtech/opensigweave"


def release_image_ref(env: dict[str, str]) -> str:
    tag = env.get("OSW_IMAGE_TAG", "alpha") or "alpha"
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise DeploymentError("Invalid OSW_IMAGE_TAG; use a published release tag", 4)
    return f"{GHCR_REPO}:{tag}"


def verify_release_image(env: dict[str, str], revision: str) -> None:
    """Refuse a GHCR tag pointing at any revision other than the approved main SHA."""
    image = release_image_ref(env)
    actual = docker(
        "image", "inspect", "--format",
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
        image,
    )
    if actual.strip() != revision:
        raise DeploymentError(
            f"GHCR image {image} does not match approved source SHA. "
            "Check the published image/tag before starting or migrating.", 4
        )


def parse_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"Configuration is missing or a symlink: {path}")
    if path.name == ".env" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise DeploymentError("Insecure .env permissions; run chmod 600 .env", 4)
    result: dict[str, str] = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)", line)
        if not match:
            raise DeploymentError(f"Invalid environment line {index} in {path}")
        key, value = match.groups()
        if key in result:
            raise DeploymentError(f"Duplicate environment key {key} in {path}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def dotenv_quote(value: str) -> str:
    if any(c in value for c in '\r\n"\\'):
        raise DeploymentError("Unsafe configuration character")
    return '"' + value + '"'


def current_channel() -> str:
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    if branch not in CHANNELS:
        raise DeploymentError(f"Unsupported checkout channel: {branch}", 4)
    return branch


def current_sha() -> str:
    return git("rev-parse", "--verify", "HEAD")


def pristine() -> None:
    if git("status", "--porcelain", "--untracked-files=no"):
        raise DeploymentError("Tracked Git changes exist; commit or move them before updating", 4)
    if git("rev-parse", "--is-shallow-repository") == "true":
        raise DeploymentError("Shallow checkout: unshallow it manually before updating", 4)
    origin = git("remote", "get-url", "origin")
    permitted = (
        "https://github.com/VeiglbergTech/OpenSigWeave.git",
        "https://github.com/VeiglbergTech/OpenSigWeave",
        "git@github.com:VeiglbergTech/OpenSigWeave.git",
        "ssh://git@github.com/VeiglbergTech/OpenSigWeave.git",
    )
    if origin not in permitted:
        raise DeploymentError("Git origin is not the approved OpenSigWeave repository", 4)


def remote_sha(channel: str) -> str:
    result = git("ls-remote", "--exit-code", "origin", f"refs/heads/{channel}")
    parts = result.split()
    if len(parts) != 2 or not re.fullmatch("[0-9a-f]{40,64}", parts[0]):
        raise DeploymentError("Cannot validate remote branch head", 4)
    return parts[0]


def system_checks() -> None:
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise DeploymentError("A supported Linux x86_64/aarch64 host is required", 4)
    for program in ("git", "python3", "docker"):
        if not shutil.which(program):
            raise DeploymentError(f"Missing dependency: {program}", 4)
    for label, command, minimum in (
        ("Docker Engine", ("docker", "--version"), (24, 0)),
        ("Docker Compose", ("docker", "compose", "version", "--short"), (2, 20)),
    ):
        version = run(*command)
        match = VERSION_RE.findall(version)
        if len(match) < 2 or tuple(map(int, match[:2])) < minimum:
            raise DeploymentError(f"{label} >= {'.'.join(map(str, minimum))} required; found {version}", 4)
    docker("info", "--format", "{{.ServerVersion}}")
    disk = shutil.disk_usage(ROOT)
    if disk.free < 6 * 1024**3:
        raise DeploymentError("At least 6 GiB free disk space required (more for backups)", 4)
    memory_kib = 0
    with Path("/proc/meminfo").open() as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                memory_kib = int(line.split()[1])
                break
    if memory_kib < 2 * 1024**2:
        raise DeploymentError("At least 2 GiB RAM required", 4)
    ok("Linux, Docker, Compose, Git, Python, permissions, memory and disk checks passed")


def validate_config(env: dict[str, str]) -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    from auth_config import is_valid_email, is_valid_password_hash

    required = ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY", "APP_ALLOWED_HOSTS")
    for key in required:
        if not env.get(key) or "REPLACE_" in env[key]:
            raise DeploymentError(f"Missing or placeholder value: {key}", 4)
    if len(set(env[key] for key in ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY"))) != 3:
        raise DeploymentError("Database and application secrets must be independent", 4)
    if any(len(env[key]) < 32 for key in ("POSTGRES_PASSWORD", "SECRET_KEY", "ENGINE_API_KEY")):
        raise DeploymentError("Application secrets must contain at least 32 characters", 4)
    # The existing application permits OIDC/LDAP-only deployments without a
    # local administrator. Never create or rotate a local password during updates.
    # Fresh installations still provision an emergency local administrator.
    email = env.get("LOCAL_ADMIN_EMAIL", "")
    admin_hash = env.get("LOCAL_ADMIN_PASSWORD_HASH", "")
    if admin_hash and (not is_valid_email(email) or not is_valid_password_hash(admin_hash)):
        raise DeploymentError("Invalid local administrator email or scrypt hash", 4)
    if email and not is_valid_email(email):
        raise DeploymentError("Invalid local administrator email", 4)
    if not admin_hash:
        warn("Local administrator login is disabled; ensure an external identity provider remains available")
    hosts = env["APP_ALLOWED_HOSTS"].split(",")
    if not hosts or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", x) for x in hosts):
        raise DeploymentError("APP_ALLOWED_HOSTS must contain explicit hostnames", 4)
    proxies = env.get("TRUSTED_PROXY_CIDRS", "")
    if proxies:
        for item in proxies.split(","):
            try:
                network = ipaddress.ip_network(item.strip(), strict=False)
            except ValueError as exc:
                raise DeploymentError("TRUSTED_PROXY_CIDRS must contain only IP addresses/CIDRs", 4) from exc
            if network.prefixlen == 0:
                raise DeploymentError("TRUSTED_PROXY_CIDRS must not trust every address (/0)", 4)
    bind = env.get("APP_BIND_ADDRESS", "0.0.0.0")  # nosec B104 - validated explicit bind setting
    try:
        ipaddress.ip_address(bind)
        port = int(env.get("APP_PORT", "8085"))
    except ValueError as exc:
        raise DeploymentError("Invalid bind IP or application port", 4) from exc
    if not 1024 <= port <= 65535:
        raise DeploymentError("Application port must be between 1024 and 65535", 4)


@contextmanager
def exclusive_lock():
    lock_path = ROOT / ".git" / "opensigweave-deployment.lock"
    if lock_path.is_symlink():
        raise DeploymentError("Unsafe deployment lock path", 4)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("Another installation or update is running", 5) from exc
        yield
    finally:
        os.close(fd)


def service_state(name: str) -> dict:
    container = compose("ps", "-q", name)
    if not container:
        raise DeploymentError(f"Container missing: {name}", 6)
    raw = docker("inspect", "--format", "{{json .State}}", container)
    state = json.loads(raw)
    if not state.get("Running") or (state.get("Health") or {}).get("Status") != "healthy":
        raise DeploymentError(f"Container not healthy: {name}", 6)
    return state


def check_migration() -> str:
    current = compose("exec", "-T", "opensigweave", "alembic", "current")
    heads = compose("exec", "-T", "opensigweave", "alembic", "heads")
    revision = current.split()[0] if current else ""
    expected = heads.split()[0] if heads else ""
    if not revision or revision != expected:
        raise DeploymentError(f"Alembic revision mismatch: current={revision}, head={expected}", 6)
    return revision


def healthcheck(env: dict[str, str]) -> None:
    service_state("postgres")
    service_state("opensigweave")
    check_migration()
    bind = env.get("APP_BIND_ADDRESS", "0.0.0.0")  # nosec B104 - validated explicit bind setting
    parsed = ipaddress.ip_address(bind)
    address = ("::1" if parsed.version == 6 else "127.0.0.1") if parsed.is_unspecified else bind
    port = int(env.get("APP_PORT", "8085"))
    # HTTPConnection only supports HTTP; no URL scheme or redirect may be injected.
    connection = HTTPConnection(address, port, timeout=10)
    try:
        connection.request("GET", "/login", headers={"Host": env["APP_ALLOWED_HOSTS"].split(",")[0]})
        response = connection.getresponse()
        if response.status != 200:
            raise DeploymentError("Application login endpoint is not healthy", 6)
        response.read(512)
    except (OSError, ValueError) as exc:
        raise DeploymentError("Cannot reach the application login endpoint", 6) from exc
    finally:
        connection.close()
    compose("exec", "-T", "opensigweave", "test", "-d", "/data/signatures")
    compose("exec", "-T", "opensigweave", "test", "-d", "/app/.idp")
    ok("PostgreSQL, application, Alembic, signature and IDP storage checks passed")
