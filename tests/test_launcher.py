"""Launch state machine: pending -> cloning -> cloned/clone_failed (issue #57).

`clone_repo` and the spawned subprocess are mocked — no real network, no real
`crucible run` process.
"""

from __future__ import annotations

import signal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from crucible.api.launcher import CancelError, cancel_run, launch_run
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
        popen.return_value = SimpleNamespace(pid=4242, wait=lambda: 0)

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


def test_launch_run_persists_and_threads_through_a_model_override(tmp_path):
    """issue #77: the override is on the row synchronously and reaches the
    spawned `crucible run` process as `--model-override <json>`."""
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)
    override = {"hunter": {"provider": "deepseek", "model": "deepseek-chat"}}

    with patch("crucible.api.launcher.clone_repo") as clone, \
         patch("crucible.api.launcher.subprocess.Popen") as popen:
        clone.return_value = ClonedRepo(path=tmp_path / "runs/x/repo", commit="a" * 40, url="https://github.com/o/r.git")
        popen.return_value = SimpleNamespace(pid=4242, wait=lambda: 0)

        run_id = launch_run(store, settings, source_spec="o/r", ref=None, model_override=override)

        # persisted synchronously, before the background thread necessarily runs
        assert store.get_run(run_id).model_override == override
        assert _wait_for(lambda: store.get_run(run_id).clone_status == "cloned")

    argv = popen.call_args.args[0]
    i = argv.index("--model-override")
    import json

    assert json.loads(argv[i + 1]) == override


def test_launch_run_without_override_never_adds_the_flag(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)

    with patch("crucible.api.launcher.clone_repo") as clone, \
         patch("crucible.api.launcher.subprocess.Popen") as popen:
        clone.return_value = ClonedRepo(path=tmp_path / "runs/x/repo", commit="a" * 40, url="https://github.com/o/r.git")
        popen.return_value = SimpleNamespace(pid=4242, wait=lambda: 0)
        run_id = launch_run(store, settings, source_spec="o/r", ref=None)
        assert _wait_for(lambda: store.get_run(run_id).clone_status == "cloned")

    assert store.get_run(run_id).model_override is None
    assert "--model-override" not in popen.call_args.args[0]


def test_launch_run_process_crash_marks_the_run_failed(tmp_path):
    """A `crucible run` that spawns fine but exits non-zero on its own (e.g.
    assert_boot_environment raising because Docker isn't reachable) must not
    leave the row at status="running" with a dead pid forever — the
    supervising thread should close it out the moment the process exits."""
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    settings = _settings(tmp_path)

    def _fake_popen(argv, stdout, stderr, start_new_session):
        stderr.write(b"DockerUnavailableError: `docker` not found on PATH\n")
        return SimpleNamespace(pid=4242, wait=lambda: 1)

    with patch("crucible.api.launcher.clone_repo") as clone, \
         patch("crucible.api.launcher.subprocess.Popen", side_effect=_fake_popen):
        clone.return_value = ClonedRepo(path=tmp_path / "runs/x/repo", commit="a" * 40, url="https://github.com/o/r.git")
        run_id = launch_run(store, settings, source_spec="o/r", ref=None)
        assert _wait_for(lambda: store.get_run(run_id).status == "finished")

    row = store.get_run(run_id)
    assert row.outcome == "failed"
    assert "DockerUnavailableError" in row.clone_error


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


# --- cancel_run (kill a running run) -----------------------------------


def test_cancel_run_rejects_unknown_run(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    with pytest.raises(CancelError, match="not found"):
        cancel_run(store, "nope")


def test_cancel_run_rejects_already_finished(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_run("done1", "/repo", "abc123", "python")
    store.finish_run("done1", "completed")
    with pytest.raises(CancelError, match="already finished"):
        cancel_run(store, "done1")


def test_cancel_run_rejects_no_pid_recorded(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_run("cli_started", "/repo", "abc123", "python")  # plain CLI run — no pid
    with pytest.raises(CancelError, match="no pid recorded"):
        cancel_run(store, "cli_started")


def test_cancel_run_sends_sigterm_and_marks_cancelled(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_launch("live1", "o/r")
    store.set_pid("live1", 4242)

    # pid "dies" after SIGTERM (killpg call #1) — no SIGKILL needed.
    calls = []
    alive = {"v": True}

    def _os_kill(pid, sig):
        if not alive["v"]:
            raise ProcessLookupError()

    def _killpg(pid, sig):
        calls.append((pid, sig))
        alive["v"] = False

    with patch("crucible.api.launcher.os.kill", side_effect=_os_kill), \
         patch("crucible.api.launcher.os.killpg", side_effect=_killpg):
        cancel_run(store, "live1")

    assert calls == [(4242, signal.SIGTERM)]  # never escalated to SIGKILL
    row = store.get_run("live1")
    assert row.outcome == "cancelled"
    assert row.status == "finished"
    assert row.finished_at is not None


def test_cancel_run_escalates_to_sigkill_if_still_alive(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_launch("stubborn1", "o/r")
    store.set_pid("stubborn1", 4242)

    calls = []

    with patch("crucible.api.launcher.CANCEL_GRACE_S", 0.05), \
         patch("crucible.api.launcher.os.kill"), \
         patch("crucible.api.launcher.os.killpg", side_effect=lambda pid, sig: calls.append((pid, sig))):
        cancel_run(store, "stubborn1")

    assert calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert store.get_run("stubborn1").outcome == "cancelled"


def test_cancel_run_already_dead_just_marks_cancelled(tmp_path):
    """pid recorded but the process is already gone (e.g. it crashed between
    the UI showing "running" and the user clicking Kill) — no signal needed,
    still closes the row out."""
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    store.create_launch("already_dead", "o/r")
    store.set_pid("already_dead", 999999)

    with patch("crucible.api.launcher.os.kill", side_effect=ProcessLookupError), \
         patch("crucible.api.launcher.os.killpg") as killpg:
        cancel_run(store, "already_dead")

    killpg.assert_not_called()
    assert store.get_run("already_dead").outcome == "cancelled"
