"""Sandbox provider adapter (specs.md §10).

Hunters compile and execute untrusted, model-generated code. DO NOT write this
layer. Use a provider adapter so the backend is swappable (E2B / Modal /
self-hosted AerolVM across Docker, gVisor, Firecracker).

Policy regardless of backend (§10):
  * NO network egress by default — the single most important control; any
    egress attempt is a run-level alert.
  * No host credentials, no cloud instance metadata (169.254.169.254), no
    access to the harness's own DB or config.
  * Hard ceilings: CPU seconds, memory, wall clock, PIDs, file size, disk quota.
  * Read-only source mount; writes only to scratch/<task_id>/, destroyed on
    completion.
  * Own the patch cadence — pin policy is a security decision.

Nested-containerization trap (§10): if the harness runs inside Docker and the
sandbox uses namespace isolation, it may need seccomp=unconfined /
apparmor=unconfined or it fails silently at startup. Detect at boot and fail
loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    egress_attempted: bool = False  # -> run-level alert


@dataclass
class SandboxLimits:
    cpu_seconds: int = 60
    memory_mb: int = 1024
    wall_clock_s: int = 300
    max_pids: int = 256
    max_file_bytes: int = 64 * 1024 * 1024
    disk_quota_mb: int = 512
    network_egress: bool = False  # default deny (§10)


class Sandbox(Protocol):
    """One live, isolated sandbox — the per-task handle `create()` returns.
    `exec`/`destroy` act on this sandbox only, never on a sibling's."""

    def exec(self, cmd: str, timeout_s: int) -> ExecResult: ...
    def destroy(self) -> None: ...


@runtime_checkable
class SandboxProvider(Protocol):
    """A factory for sandboxes (issue #98). `create()` returns a fresh,
    independent `Sandbox` handle and must be safe to call from several threads
    at once — Hunt runs tasks concurrently and gives each its own handle, so a
    provider holds no per-sandbox state of its own.

    The §10 sketch put `exec`/`destroy` on the provider; that shape can only
    ever hold one live sandbox, so it moved onto the handle. A legacy provider
    whose `create()` returns ``None`` (mutating itself instead) still works:
    Hunt uses the provider as the handle and runs sequentially, unless the
    provider sets ``concurrent_sandboxes = True`` (see `supports_concurrency`).
    """

    def create(self, task_id: str, repo_mount: str, limits: SandboxLimits) -> Sandbox: ...


def supports_concurrency(provider: object) -> bool:
    """True when `provider.create()` hands out independent handles that may be
    driven from several threads at once. Opt-in by a class attribute, so an
    unknown/legacy provider is treated as single-sandbox (sequential only)."""
    return bool(getattr(provider, "concurrent_sandboxes", False))


def assert_boot_environment() -> None:
    """Detect the nested-containerization trap and fail loudly (§10).

    Delegates to the configured backend. Only the Docker dev backend is wired
    today; a managed/self-hosted provider would register its own check here.
    """
    from crucible.sandbox.docker import assert_boot_environment as _docker_boot

    _docker_boot()
