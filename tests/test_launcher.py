"""Launch state machine: pending -> cloning -> cloned/clone_failed (issue #57).

`clone_repo` and the spawned subprocess are mocked — no real network, no real
`crucible run` process.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from crucible.api.launcher import launch_run
from crucible.api.settings import ApiSettings
from crucible.repo_acquire import ClonedRepo, CloneFailed
from crucible.store.dao import Store


def _settings(tmp_path, **kw):
    return ApiSettings(
        store_url=f"sqlite:///{tmp_path}/f.sqlite",
        checkpoint_db=str(tmp_path / "c.sqlite"),
        runs_dir=str(tmp_path / "runs"),
        **kw,
    )


def _wait_for(predicate, timeout=2.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_launch_run_returns_immediately_with_a_pending_row(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)

    with patch("crucible.api.launcher.clone_repo") as clone, \
         patch("crucible.api.launcher.subprocess.Popen") as popen:
        clone.return_value = ClonedRepo(path=tmp_path / "runs/x/repo", commit="a" * 40, url="https://github.com/o/r.git")
        popen.return_value = SimpleNamespace(pid=4242)

        run_id = launch_run(store, settings, source_spec="o/r", ref=None)

        # the row exists synchronously, before the background thread necessarily finishes
        row = store.get_run(run_id)
        assert row is not None
        assert row.clone_status in ("pending", "cloning", "cloned")
        assert row.source_spec == "o/r"

        assert _wait_for(lambda: store.get_run(run_id).clone_status == "cloned")
        assert store.get_run(run_id).pid == 4242
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert argv[:3] == [__import__("sys").executable, "-m", "crucible.cli"]
        assert "--run-id" in argv and run_id in argv
        assert "--repo" in argv


def test_launch_run_clone_failure_marks_the_run_failed(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)

    with patch("crucible.api.launcher.clone_repo", side_effect=CloneFailed("fatal: repository not found")), \
         patch("crucible.api.launcher.subprocess.Popen") as popen:
        run_id = launch_run(store, settings, source_spec="o/does-not-exist", ref=None)
        assert _wait_for(lambda: store.get_run(run_id).clone_status == "clone_failed")

    row = store.get_run(run_id)
    assert row.outcome == "failed"
    assert "not found" in row.clone_error
    popen.assert_not_called()


def test_launch_run_spawn_failure_marks_the_run_failed(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)

    with patch("crucible.api.launcher.clone_repo") as clone, \
         patch("crucible.api.launcher.subprocess.Popen", side_effect=OSError("no such file")):
        clone.return_value = ClonedRepo(path=tmp_path / "runs/x/repo", commit="a" * 40, url="https://github.com/o/r.git")
        run_id = launch_run(store, settings, source_spec="o/r", ref=None)
        assert _wait_for(lambda: store.get_run(run_id).clone_status == "clone_failed")

    row = store.get_run(run_id)
    assert row.outcome == "failed"
    assert "no such file" in row.clone_error


def test_count_active_runs_and_sweep(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_launch("stuck1", "o/r")
    store.create_launch("fresh1", "o/r2")
    assert store.count_active_runs() == 2

    # backdate "stuck1" past the sweep threshold
    with store.session() as s:
        from datetime import UTC, datetime, timedelta

        from crucible.store.models import Run

        r = s.get(Run, "stuck1")
        r.created_at = datetime.now(UTC) - timedelta(seconds=1000)

    swept = store.sweep_stuck_launches(older_than_s=10)
    assert swept == 1
    assert store.get_run("stuck1").clone_status == "clone_failed"
    assert store.get_run("stuck1").outcome == "failed"
    assert store.get_run("fresh1").clone_status == "pending"  # untouched
    assert store.count_active_runs() == 1


def test_reap_dead_runs_by_pid_liveness(tmp_path):
    """The bug this guards against: a plain `crucible run` from before this
    reaper existed (no pid recorded) sat at status=running forever and
    permanently ate a concurrency slot."""
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_run("cli_old_stale", "/repo", "abc123", "python")  # no pid — the historical case
    store.create_launch("api_dead", "o/r")
    store.set_pid("api_dead", 999999)  # not a real pid
    store.create_launch("api_alive", "o/r2")
    store.set_pid("api_alive", 1)  # pid 1 (init) — always alive on any real system
    store.create_launch("api_no_pid_fresh", "o/r3")  # never got past cloning

    from datetime import UTC, datetime, timedelta

    from crucible.store.models import Run

    with store.session() as s:
        s.get(Run, "cli_old_stale").created_at = datetime.now(UTC) - timedelta(hours=48)

    with patch("os.kill", side_effect=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if pid == 999999 else None):
        reaped = store.reap_dead_runs(stale_after_s=3600)

    assert reaped == 2
    assert store.get_run("cli_old_stale").outcome == "stale"
    assert store.get_run("api_dead").outcome == "failed"
    assert store.get_run("api_alive").clone_status == "pending"  # untouched — pid 1 is alive
    assert store.get_run("api_no_pid_fresh").clone_status == "pending"  # untouched — not stale yet
    assert store.count_active_runs() == 2
