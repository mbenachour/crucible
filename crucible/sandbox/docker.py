"""DockerSandboxProvider — local Docker backend for dev (specs.md §10).

Swappable: implements the `SandboxProvider` protocol by shelling out to the
`docker` CLI (no docker SDK dependency). This is the **dev** backend; a
managed (E2B/Modal) or self-hosted (AerolVM/Firecracker) provider drops in
behind the same protocol for production.

Policy enforced here regardless of the caller:
  * ``--network none``            — no egress, ever (the §10 control)
  * ``--read-only`` root fs, source mounted ``:ro``, writes only under /scratch
  * ``--memory --cpus --pids-limit`` hard ceilings
  * no env passthrough, no ``--privileged``, no docker socket
  * container removed on `destroy()` (and best-effort on GC)

Not covered yet: cloud-metadata blocking beyond `--network none` (moot with no
network), disk quota (needs a sized tmpfs/volume), the escape-test suite (§14.7).
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from crucible.sandbox import ExecResult, Sandbox, SandboxLimits

DEFAULT_IMAGE = "python:3.12-slim-bookworm"
SCRATCH_MOUNT = "/scratch"
SOURCE_MOUNT = "/src"


class DockerUnavailableError(RuntimeError):
    pass


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise DockerUnavailableError("`docker` not found on PATH")
    return exe


class DockerSandbox(Sandbox):
    def __init__(self, container_id: str, docker_bin: str) -> None:
        self._id = container_id
        self._docker = docker_bin
        self._alive = True

    def exec(self, cmd: str, timeout_s: int) -> ExecResult:
        if not self._alive:
            raise RuntimeError("sandbox already destroyed")
        try:
            proc = subprocess.run(
                [self._docker, "exec", self._id, "sh", "-lc", cmd],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(
                exit_code=124,
                stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "" if isinstance(e.stderr, str) else "") + "\n[timeout]",
                timed_out=True,
            )
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )

    def destroy(self) -> None:
        if not self._alive:
            return
        subprocess.run(
            [self._docker, "rm", "-f", self._id],
            capture_output=True,
            text=True,
        )
        self._alive = False

    def __del__(self) -> None:  # best-effort safety net
        try:
            self.destroy()
        except Exception:
            pass


class DockerSandboxProvider:
    """Implements `crucible.sandbox.SandboxProvider`."""

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        self.image = image
        self._docker = _docker()
        self._current: DockerSandbox | None = None

    def create(self, task_id: str, repo_mount: str, limits: SandboxLimits) -> DockerSandbox:
        src = Path(repo_mount).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"repo_mount not a directory: {src}")
        name = f"crucible-{task_id}-{uuid.uuid4().hex[:8]}"
        args = [
            self._docker, "run", "-d", "--rm",
            "--name", name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(limits.max_pids),
            "--memory", f"{limits.memory_mb}m",
            "--cpus", str(max(1, limits.cpu_seconds // 30) or 1),
            "--tmpfs", f"{SCRATCH_MOUNT}:rw,size={limits.disk_quota_mb}m,mode=1777",
            "-v", f"{src}:{SOURCE_MOUNT}:ro",
            "-w", SCRATCH_MOUNT,
            self.image,
            "sleep", str(limits.wall_clock_s),
        ]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()}")
        self._current = DockerSandbox(proc.stdout.strip(), self._docker)
        return self._current

    def exec(self, cmd: str, timeout_s: int) -> ExecResult:
        if self._current is None:
            raise RuntimeError("no sandbox created")
        return self._current.exec(cmd, timeout_s)

    def destroy(self) -> None:
        if self._current is not None:
            self._current.destroy()
            self._current = None


def assert_boot_environment() -> None:
    """Detect the nested-containerization trap and fail loudly (specs.md §10)."""
    try:
        docker = _docker()
    except DockerUnavailableError as e:
        raise DockerUnavailableError(
            f"{e}. Install Docker or configure a different SandboxProvider."
        ) from e
    info = subprocess.run([docker, "info"], capture_output=True, text=True)
    if info.returncode != 0:
        raise DockerUnavailableError(
            "`docker info` failed — daemon not running or not reachable:\n"
            + info.stderr.strip()
        )
    nested = Path("/.dockerenv").exists()
    if nested:
        # Namespace isolation inside a container often needs relaxed profiles.
        smoke = subprocess.run(
            [
                docker, "run", "--rm", "--network", "none",
                DEFAULT_IMAGE, "true",
            ],
            capture_output=True,
            text=True,
        )
        if smoke.returncode != 0:
            raise RuntimeError(
                "Crucible appears to run inside a container and a sandbox smoke "
                "test failed. You likely need `--security-opt seccomp=unconfined "
                "--security-opt apparmor=unconfined` on the Crucible container, or "
                "a non-namespace sandbox backend. docker said:\n" + smoke.stderr.strip()
            )
