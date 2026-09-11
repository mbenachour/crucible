"""Background clone + launch for API-triggered runs (issue #57).

`launch_run` returns a `run_id` synchronously — a `Run` row already exists at
that point — and does the clone plus the `crucible run` spawn on a background
thread. The spawned process is detached (`start_new_session=True`) so it
outlives both the request and the API process.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from crucible.repo_acquire import CloneError, RepoRefError, clone_repo

log = logging.getLogger("crucible.api.launcher")


def launch_run(store, settings, *, source_spec: str, ref: str | None = None) -> str:
    """Pre-register the `Run` row and return its id immediately; clone + spawn
    happen on a background thread."""
    run_id = uuid.uuid4().hex[:12]
    store.create_launch(run_id, source_spec)
    thread = threading.Thread(
        target=_do_launch, args=(store, settings, run_id, source_spec, ref), daemon=True,
    )
    thread.start()
    return run_id


def _do_launch(store, settings, run_id: str, source_spec: str, ref: str | None) -> None:
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
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as e:
        log.error("launch %s: failed to spawn `crucible run`: %s", run_id, e)
        store.set_clone_status(run_id, "clone_failed", f"failed to launch crucible run: {e}")
        store.finish_run(run_id, "failed")
        return

    store.set_pid(run_id, proc.pid)
    log.info("launch %s: spawned crucible run pid=%s repo=%s", run_id, proc.pid, cloned.path)
