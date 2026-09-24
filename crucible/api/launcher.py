"""Background clone + launch for API-triggered runs (issue #57).

`launch_run` returns a `run_id` synchronously — a `Run` row already exists at
that point — and does the clone plus the `crucible run` spawn on a background
thread. The spawned process is detached (`start_new_session=True`) so it
outlives both the request and the API process.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from crucible.repo_acquire import CloneError, RepoRefError, clone_repo

log = logging.getLogger("crucible.api.launcher")

CANCEL_GRACE_S = 3.0  # SIGTERM, then SIGKILL if still alive after this long


class CancelError(ValueError):
    """Raised by `cancel_run` for a request the caller should turn into a
    4xx — an unknown run, one with nothing to kill, or one already over."""


def launch_run(
    store, settings, *, source_spec: str, ref: str | None = None,
    model_override: dict | None = None,
) -> str:
    """Pre-register the `Run` row and return its id immediately; clone + spawn
    happen on a background thread.

    `model_override` (issue #77) — already validated by the caller (`POST
    /runs`) — is persisted on the row and threaded through to the spawned
    `crucible run` process via `--model-override`."""
    run_id = uuid.uuid4().hex[:12]
    store.create_launch(run_id, source_spec, model_override=model_override)
    thread = threading.Thread(
        target=_do_launch, args=(store, settings, run_id, source_spec, ref, model_override),
        daemon=True,
    )
    thread.start()
    return run_id


def _do_launch(
    store, settings, run_id: str, source_spec: str, ref: str | None,
    model_override: dict | None = None,
) -> None:
    run_root = Path(settings.runs_dir) / run_id
    repo_dir = run_root / "repo"
    workspace_dir = run_root / "workspace"

    store.set_clone_status(run_id, "cloning")
    try:
        cloned = clone_repo(
            source_spec,
            repo_dir,
            ref=ref,
            timeout_s=settings.clone_timeout_s,
            max_bytes=settings.clone_max_mb * 1024 * 1024,
            allowed_hosts=settings.allowed_git_hosts,
        )
    except (CloneError, RepoRefError) as e:
        # RepoRefError is already rejected synchronously by the endpoint (#58);
        # caught again here so a background failure can never hang a launch.
        log.warning("launch %s: clone of %r failed: %s", run_id, source_spec, e)
        store.set_clone_status(run_id, "clone_failed", str(e))
        store.finish_run(run_id, "failed")
        return

    store.set_clone_status(run_id, "cloned")

    argv = [
        sys.executable, "-m", "crucible.cli", "run",
        "--repo", str(cloned.path),
        "--workspace", str(workspace_dir),
        "--run-id", run_id,
        "--store-url", settings.store_url,
        "--checkpoint-db", settings.checkpoint_db,
    ]
    if model_override:
        argv += ["--model-override", json.dumps(model_override)]
    # `crucible run`'s own tracebacks (e.g. assert_boot_environment raising
    # when Docker isn't reachable — the most common early-crash cause) go to
    # stderr; captured to a file, not DEVNULL, so a crash has a reason
    # attached instead of the run just silently going quiet.
    stderr_path = run_root / "launch.stderr"
    try:
        run_root.mkdir(parents=True, exist_ok=True)
        with stderr_path.open("wb") as stderr_f:
            proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=stderr_f, start_new_session=True,
            )
    except OSError as e:
        log.error("launch %s: failed to spawn `crucible run`: %s", run_id, e)
        store.set_clone_status(run_id, "clone_failed", f"failed to launch crucible run: {e}")
        store.finish_run(run_id, "failed")
        return

    store.set_pid(run_id, proc.pid)
    log.info("launch %s: spawned crucible run pid=%s repo=%s", run_id, proc.pid, cloned.path)

    # Supervise to completion on this same background thread (its only other
    # job — launching — is already done) so a process that dies on its own,
    # not just one that fails to spawn, gets its row closed out immediately
    # instead of sitting at status="running" with a dead pid until the next
    # `POST /runs` happens to run `reap_dead_runs` (issue #58 gap).
    returncode = proc.wait()
    if returncode != 0:
        tail = stderr_path.read_text(errors="replace")[-4000:] if stderr_path.exists() else ""
        log.warning("launch %s: crucible run exited %s: %s", run_id, returncode, tail[-500:])
        store.mark_run_crashed(
            run_id, f"crucible run exited with code {returncode}:\n{tail}" if tail
            else f"crucible run exited with code {returncode}"
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but not ours — treat as alive, same as dao._pid_alive
    return True


def cancel_run(store, run_id: str) -> None:
    """Kill an API-launched run's process tree and mark the row cancelled.

    `run.pid` is a process-*group* leader (the spawn in `_do_launch` sets
    `start_new_session=True`), so `os.killpg` reaches `crucible run` and
    whatever it forked (the hunter/validator/etc. Python process itself —
    not any Docker *container* it started via `docker run`, which keeps
    executing independently of its caller dying; out of scope here, same
    as it would be for a plain Ctrl-C on the CLI).

    Marks the row cancelled itself (`store.mark_run_cancelled`) rather than
    waiting for `_do_launch`'s own supervising thread to notice the process
    exit and call `mark_run_crashed` — both guard on `finished_at is None`,
    so whichever runs first wins, but only marking here gets the outcome
    right (`cancelled`, not `failed`) instead of racing to relabel it after.
    """
    run = store.get_run(run_id)
    if run is None:
        raise CancelError(f"run not found: {run_id}")
    if run.finished_at is not None:
        raise CancelError(f"run already finished (outcome={run.outcome!r})")
    if run.pid is None:
        raise CancelError(
            "no pid recorded for this run — it wasn't launched via the API "
            "(or predates pid tracking); kill the `crucible run` process yourself"
        )

    pid = run.pid
    if _pid_alive(pid):
        log.info("cancel %s: SIGTERM pid=%s", run_id, pid)
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + CANCEL_GRACE_S
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        if _pid_alive(pid):
            log.warning("cancel %s: still alive after %.0fs, SIGKILL pid=%s", run_id, CANCEL_GRACE_S, pid)
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    store.mark_run_cancelled(run_id)
