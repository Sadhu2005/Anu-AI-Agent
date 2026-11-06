#!/usr/bin/env python3
"""V1.3 Hermes isolation policy: air-gapped terminal backend + disposable jobs.

Hermes host process may still reach LM Studio / OpenRouter. Code execution
must use rootless Podman (HERMES_DOCKER_BINARY=podman) with network off,
resource limits, and a temp job directory that is never under /srv/anu.

Does not modify anu-agent.service or grant git credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


JOBS_ROOT_DEFAULT = "/var/lib/anu-hermes/jobs"
PRODUCTION_ROOT = "/srv/anu"
SECRETS_ROOT = "/srv/anu/secrets"
BRAIN_ROOT = "/srv/anu/brain"

# Host paths that must never appear as a container mount source.
FORBIDDEN_MOUNT_ROOTS = (
    PRODUCTION_ROOT,
    "/run/docker.sock",
    "/var/run/docker.sock",
    "/run/podman",
    "/var/run/podman",
    "/run/user",
)

# Never forward these into the code container (host .env may still hold them).
TOKEN_ENV_NAMES = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "ANU_GITHUB_TOKEN",
    "OPENROUTER_API_KEY",
    "HERMES_OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "LM_API_KEY",
)

SKIP_COPY_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "github-token",
}
SKIP_COPY_DIRS = {"secrets", ".ssh"}

# Pi 5 is 8 GB and already runs ANU. Leave headroom.
DEFAULT_CPU = 1
DEFAULT_MEMORY_MB = 2048
DEFAULT_DISK_MB = 8192
DEFAULT_TIMEOUT_S = 180

JOB_MARKER = ".anu-hermes-job.json"


class IsolationError(ValueError):
    """Raised when a path or mount would break the V1.3 boundary."""


def _posix(path: str | Path) -> str:
    return str(path).replace("\\", "/").rstrip("/")


def is_under(path: str | Path, root: str | Path) -> bool:
    child = _posix(path)
    parent = _posix(root)
    return child == parent or child.startswith(parent + "/")


def assert_job_root_allowed(jobs_root: str | Path) -> None:
    raw = _posix(jobs_root)
    if not raw.startswith("/"):
        raise IsolationError(f"jobs root must be an absolute path: {jobs_root}")
    if is_under(raw, PRODUCTION_ROOT):
        raise IsolationError(f"jobs root must not be under {PRODUCTION_ROOT}: {jobs_root}")
    for forbidden in FORBIDDEN_MOUNT_ROOTS:
        if is_under(raw, forbidden) or is_under(forbidden, raw):
            raise IsolationError(f"jobs root overlaps forbidden mount {forbidden}: {jobs_root}")


def assert_source_allowed(source: str | Path) -> None:
    raw = _posix(source)
    if is_under(raw, SECRETS_ROOT) or is_under(raw, BRAIN_ROOT):
        raise IsolationError(f"refusing to snapshot secrets or brain: {source}")


def volume_host_path(spec: str) -> str:
    # host:container[:mode] — host may be a Windows path only in tests; policy
    # paths are POSIX. Split on the first colon that begins the container path.
    text = spec.strip()
    if not text:
        raise IsolationError("empty docker volume spec")
    if text.startswith("/"):
        rest = text[1:]
        if ":" not in rest:
            raise IsolationError(f"volume spec needs host:container: {spec}")
        host, _container = text.split(":", 1)
        return host
    raise IsolationError(f"volume host path must be absolute: {spec}")


def volume_is_forbidden(spec: str) -> bool:
    lowered = spec.lower()
    if "docker.sock" in lowered or "podman.sock" in lowered or "--privileged" in lowered:
        return True
    host = volume_host_path(spec)
    return any(is_under(host, root) for root in FORBIDDEN_MOUNT_ROOTS)


def filter_extra_args(args: list) -> list[str]:
    kept: list[str] = []
    for item in args or []:
        text = str(item)
        low = text.lower()
        if any(
            token in low
            for token in (
                "--network",
                "--privileged",
                "docker.sock",
                "podman.sock",
                "--cap-add",
                "/srv/anu",
            )
        ):
            continue
        kept.append(text)
    return kept


def apply_terminal_isolation(cfg: dict, *, job_dir: str | None = None) -> dict:
    """Return a copy of cfg with the V1.3 terminal sandbox enforced."""
    if not isinstance(cfg, dict):
        raise IsolationError("config must be a mapping")
    out = dict(cfg)
    terminal = dict(out.get("terminal") or {})

    volumes: list[str] = []
    if job_dir:
        assert_job_root_allowed(job_dir)
        volumes.append(f"{_posix(job_dir)}:/workspace:rw")
    for spec in terminal.get("docker_volumes") or []:
        if volume_is_forbidden(str(spec)):
            continue
        host = volume_host_path(str(spec))
        # Only keep mounts that already sit inside the Hermes job tree.
        if is_under(host, "/var/lib/anu-hermes") or (
            job_dir and is_under(host, job_dir)
        ):
            rendered = str(spec)
            if rendered not in volumes:
                volumes.append(rendered)

    forward = [
        name
        for name in (terminal.get("docker_forward_env") or [])
        if str(name) not in TOKEN_ENV_NAMES
    ]

    terminal.update(
        {
            "backend": "docker",
            "docker_network": False,
            "container_cpu": DEFAULT_CPU,
            "container_memory": DEFAULT_MEMORY_MB,
            "container_disk": DEFAULT_DISK_MB,
            "container_persistent": False,
            "docker_persist_across_processes": False,
            "docker_mount_cwd_to_workspace": False,
            "docker_forward_env": forward,
            "docker_volumes": volumes,
            "docker_extra_args": filter_extra_args(terminal.get("docker_extra_args") or []),
            "timeout": int(terminal.get("timeout") or DEFAULT_TIMEOUT_S),
        }
    )
    # Drop literal secrets if a previous config injected them.
    env = dict(terminal.get("docker_env") or {})
    for key in list(env):
        if key in TOKEN_ENV_NAMES:
            env.pop(key, None)
    terminal["docker_env"] = env
    out["terminal"] = terminal
    return out


def podman_probe_args(workspace: str | Path) -> list[str]:
    """Args for a one-shot denial probe. Does not mount production or the socket."""
    assert_job_root_allowed(workspace)
    return [
        "podman",
        "run",
        "--rm",
        "--network=none",
        f"--memory={DEFAULT_MEMORY_MB}m",
        f"--cpus={DEFAULT_CPU}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "-v",
        f"{_posix(workspace)}:/workspace:rw",
    ]


def _copy_filter(src: str, names: list[str]) -> set[str]:
    skipped = set()
    for name in names:
        if name in SKIP_COPY_NAMES or name in SKIP_COPY_DIRS:
            skipped.add(name)
    return skipped


def prepare_job_workspace(
    jobs_root: str | Path,
    job_id: str,
    source: str | Path | None = None,
) -> Path:
    """Create a disposable snapshot. Never writes under /srv/anu."""
    if not job_id or any(ch in job_id for ch in "/\\") or job_id in {".", ".."}:
        raise IsolationError(f"invalid job id: {job_id!r}")
    assert_job_root_allowed(jobs_root)
    root = Path(jobs_root)
    dest = root / job_id
    assert_job_root_allowed(dest)
    if source is not None:
        assert_source_allowed(source)
        src = Path(source)
        if not src.is_dir():
            raise IsolationError(f"snapshot source is not a directory: {source}")
        if is_under(dest, src):
            raise IsolationError("job directory must not sit inside the snapshot source")
    if dest.exists():
        raise IsolationError(f"job workspace already exists: {dest}")
    dest.mkdir(parents=True)
    if source is not None:
        # copytree into dest: copy contents, not the source directory name.
        for child in Path(source).iterdir():
            target = dest / child.name
            if child.name in SKIP_COPY_NAMES or child.name in SKIP_COPY_DIRS:
                continue
            if child.is_symlink():
                continue
            if child.is_dir():
                shutil.copytree(child, target, ignore=_copy_filter, symlinks=False)
            elif child.is_file():
                shutil.copy2(child, target)
    marker = {
        "job_id": job_id,
        "network": "none",
        "production_root_writable": False,
        "workspace": _posix(dest),
    }
    (dest / JOB_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return dest


def dispose_job_workspace(jobs_root: str | Path, job_id: str) -> None:
    assert_job_root_allowed(jobs_root)
    dest = Path(jobs_root) / job_id
    assert_job_root_allowed(dest)
    marker = dest / JOB_MARKER
    if not marker.is_file():
        raise IsolationError(f"refusing to delete workspace without {JOB_MARKER}: {dest}")
    shutil.rmtree(dest)


def _upsert_env_line(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix) or line.startswith(f"export {prefix}"):
            existing = line.split("=", 1)[1].strip().strip('"').strip("'")
            if existing:
                return
            lines[i] = f"{key}={value}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
            return
    lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def apply_hermes_home(hermes_home: Path, *, job_dir: str | None = None) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc

    config_path = hermes_home / "config.yaml"
    cfg = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        cfg = loaded if isinstance(loaded, dict) else {}
    updated = apply_terminal_isolation(cfg, job_dir=job_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    _upsert_env_line(hermes_home / ".env", "HERMES_DOCKER_BINARY", "podman")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply V1.3 Hermes isolation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    apply_p = sub.add_parser("apply", help="enforce terminal sandbox in HERMES_HOME")
    apply_p.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", ""))
    apply_p.add_argument("--job-dir", default="")

    prep = sub.add_parser("prepare", help="create a disposable job snapshot")
    prep.add_argument("--jobs-root", default=os.environ.get("ANU_HERMES_JOBS_ROOT", JOBS_ROOT_DEFAULT))
    prep.add_argument("--job-id", required=True)
    prep.add_argument("--source", default="")

    drop = sub.add_parser("dispose", help="delete a job snapshot that has the marker")
    drop.add_argument("--jobs-root", default=os.environ.get("ANU_HERMES_JOBS_ROOT", JOBS_ROOT_DEFAULT))
    drop.add_argument("--job-id", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "apply":
        home = Path(args.hermes_home or (Path.home() / ".hermes"))
        apply_hermes_home(home, job_dir=args.job_dir or None)
        print(f"OK isolation applied under {home}")
        return 0
    if args.cmd == "prepare":
        dest = prepare_job_workspace(
            args.jobs_root,
            args.job_id,
            args.source or None,
        )
        print(dest)
        return 0
    dispose_job_workspace(args.jobs_root, args.job_id)
    print(f"OK disposed {args.job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
