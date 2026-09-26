"""Parallel Hunt (issue #98).

No docker, no model: the `docker` CLI is faked at the `subprocess.run` layer
(each fake container keeps its own scratch state), and Hunt's `_explore` /
`_emit` phases are monkeypatched. Asserts that

  (a) concurrent tasks each only ever see their own container,
  (b) a parallel batch folds into graph state exactly like a sequential one,
  (c) caps / worker count are configurable and `workers=1` stays sequential.
"""

from __future__ import annotations

import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from crucible import config
from crucible.graph import hooks
from crucible.graph.nodes import hunt
from crucible.sandbox import SandboxLimits, supports_concurrency
from crucible.sandbox import docker as docker_mod
from crucible.workspace.fs import init_workspace

_REAL_RUN = subprocess.run

_FINDING = {
    "threat_model": {
        "attacker": "unauthenticated remote client",
        "boundary_crossed": "HTTP query param -> eval()",
        "assumption_broken": "request params are never evaluated",
    },
    "title": "eval of request parameter",
    "file_path": "api/views.py",
    "line_start": 12,
    "line_end": 12,
    "description": "q reaches eval()",
    "poc_test": "def test_it():\n    assert search('__import__(\"os\")') is not None",
    "proposed_patch": "--- a/api/views.py\n+++ b/api/views.py\n",
    "severity": "high",
}


# ----------------------------------------------------------------- fake docker


class FakeDocker:
    """Stands in for the `docker` CLI. `sh -lc 'put X'` stores X in that
    container's scratch; `sh -lc get` returns it. Anything that isn't a docker
    invocation (e.g. `git` from `commit_node`) goes to the real subprocess.run."""

    def __init__(self) -> None:
        self.scratch: dict[str, str] = {}
        self.removed: set[str] = set()
        self.max_live = 0
        self._n = 0
        self._lock = threading.Lock()

    def run(self, args, *a, **kw):
        if not args or args[0] != "docker":
            return _REAL_RUN(args, *a, **kw)
        verb = args[1]
        with self._lock:
            if verb == "run":
                self._n += 1
                cid = f"c{self._n:03d}"
                self.scratch[cid] = ""
                self.max_live = max(self.max_live, len(self.scratch))
                return SimpleNamespace(returncode=0, stdout=cid + "\n", stderr="")
            if verb == "rm":
                cid = args[-1]
                self.scratch.pop(cid, None)
                self.removed.add(cid)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if verb == "exec":
                cid, cmd = args[2], args[-1]
                if cid not in self.scratch:
                    return SimpleNamespace(returncode=1, stdout="", stderr="no such container")
                if cmd.startswith("put "):
                    self.scratch[cid] = cmd[4:]
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout=self.scratch[cid], stderr="")
        raise AssertionError(f"unexpected docker call {args}")


@pytest.fixture
def fake_docker(monkeypatch):
    fd = FakeDocker()
    monkeypatch.setattr(docker_mod, "_docker", lambda: "docker")
    monkeypatch.setattr(docker_mod.subprocess, "run", fd.run)
    return fd


# -------------------------------------------------------------- hunt harness


def _task(i: int, area: str = "api") -> dict:
    return {
        "task_id": f"t{i:02d}", "area": area, "attack_class": "command_injection",
        "chunk_type": "surface", "scope_hint": f"hint {i}", "seed_path": "api/views.py",
        "continuation_count": 0,
    }


def _state(ws, repo, tasks) -> dict:
    return {
        "run_id": "r1", "repo_path": str(repo), "workspace_path": str(ws),
        "architecture_path": "", "pending_hunts": list(tasks), "completed_cells": [],
        "finding_ids": [], "fork_count": 0, "continuation_count": 0,
    }


def _deps(sandbox_provider=None):
    reg = SimpleNamespace(chat_model=lambda role: object())
    return SimpleNamespace(registry=reg, store=None, sandbox_provider=sandbox_provider)


@pytest.fixture
def fake_agent(monkeypatch):
    """`_explore` sleeps longer for earlier tasks (so a pool finishes them out
    of order), forks for every third task, and exercises the sandbox tool when
    one is present. `_emit` reports a finding for even-numbered tasks."""
    rec = SimpleNamespace(threads=[], active=0, max_active=0, done_order=[], seen={})
    lock = threading.Lock()

    def fake_explore(*, thread_id, tools, **kw):
        tid = thread_id.rsplit(":", 1)[-1]
        i = int(tid[1:])
        by_name = {t.name: t for t in tools}
        with lock:
            rec.active += 1
            rec.max_active = max(rec.max_active, rec.active)
            rec.threads.append(threading.current_thread().name)
        by_name["sandbox_exec"].invoke({"cmd": f"put {tid}"})
        time.sleep(0.01 * (8 - i % 8))
        seen = by_name["sandbox_exec"].invoke({"cmd": "get"})
        if i % 3 == 0:
            by_name["fork_sibling"].invoke({
                "structural_seed": f"api/views.py:{i} — fork of {tid}", "reason": "out of scope",
            })
        with lock:
            rec.active -= 1
            rec.done_order.append(tid)
            rec.seen[tid] = seen
        # `n` tool calls: odd tasks come back shallow (0) and get re-queued once
        return [], (0 if i % 2 else 3)

    def fake_emit(model, system_prompt, task_text, digest, *, repo=None):
        i = int(task_text.split("# Hunt task t", 1)[1][:2])
        if i % 2 == 0:
            return hunt.HuntResult(finding_found=True, finding=_FINDING)
        return hunt.HuntResult(finding_found=False, negative_note="looked safe")

    monkeypatch.setattr(hunt, "_explore", fake_explore)
    monkeypatch.setattr(hunt, "_emit", fake_emit)
    return rec


def _normalize(state: dict) -> dict:
    """Graph state minus the random parts (finding-id / fork-id uuid suffixes)."""
    def _task_key(t):
        t = dict(t)
        if t["task_id"].startswith("fork-"):
            t["task_id"] = "fork"
        return t

    return {
        "finding_ids": [f.rsplit("-", 1)[0] for f in state["finding_ids"]],
        "completed_cells": state["completed_cells"],
        "fork_count": state["fork_count"],
        "pending_hunts": [_task_key(t) for t in state["pending_hunts"]],
    }


def _run_hunt(tmp_path, repo, monkeypatch, *, workers, tasks, provider=None, name="ws", **env):
    monkeypatch.setenv("CRUCIBLE_HUNT_WORKERS", str(workers))
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    ws = init_workspace(tmp_path / name)
    return hunt.run(_state(ws, repo, tasks), deps=_deps(provider))


# ------------------------------------------------ (a) sandbox isolation stress


def test_provider_create_returns_independent_handles(fake_docker, tmp_path):
    provider = docker_mod.DockerSandboxProvider()
    assert supports_concurrency(provider)
    assert not hasattr(provider, "exec"), "provider-level exec is the shared-state hazard"
    a = provider.create("a", str(tmp_path), SandboxLimits())
    b = provider.create("b", str(tmp_path), SandboxLimits())
    a.exec("put A", 5)
    b.exec("put B", 5)
    assert a.exec("get", 5).stdout == "A" and b.exec("get", 5).stdout == "B"
    a.destroy()
    assert b.exec("get", 5).stdout == "B", "destroying one handle killed a sibling"
    with pytest.raises(RuntimeError):
        a.exec("get", 5)
    b.destroy()


def test_concurrent_sandbox_exec_never_crosses_containers(fake_docker, tmp_path, repo_web):
    """The issue's contamination stress test, through the real Hunt tool path:
    many tasks at once, each writes its own token then reads it back after the
    others have written — every task must read back exactly its own."""
    provider = docker_mod.DockerSandboxProvider()
    n, rounds = 16, 20
    barrier = threading.Barrier(n)
    bad: list[str] = []

    def worker(i: int) -> None:
        # never `assert` in here — an exception in a worker thread only warns;
        # record the problem in `bad` so the main thread fails the test.
        tid = f"task{i}"
        sb = hunt._make_sandbox(provider, tid, str(repo_web))
        if sb is None or sb is provider:
            bad.append(f"{tid}: got {sb!r}, not a per-task handle")
        tools = hunt._hunt_tools(
            repo=str(repo_web), sandbox=sb, task=_task(i), ws=tmp_path, forks=[], wishes=[],
            fork_budget=hunt._ForkBudget(0),
        )
        sandbox_exec = next(t for t in tools if t.name == "sandbox_exec")
        try:
            barrier.wait()
            for r in range(rounds):
                token = f"{tid}#{r}"
                sandbox_exec.invoke({"cmd": f"put {token}"})
                time.sleep(0.0005)
                out = sandbox_exec.invoke({"cmd": "get"})
                if f"--- stdout ---\n{token}\n" not in out:
                    bad.append(f"{tid} expected {token}, got {out!r}")
        finally:
            hunt._destroy_sandbox(sb)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    crossed = [b for b in bad if " expected " in b]
    assert not bad, f"{len(crossed)} cross-container read(s), e.g. {(crossed or bad)[:3]}"
    assert fake_docker.max_live == n, "containers were not live concurrently"
    assert not fake_docker.scratch and len(fake_docker.removed) == n, "a container leaked"


def test_parallel_hunt_tasks_each_see_only_their_own_sandbox(
    fake_docker, fake_agent, tmp_path, repo_web, monkeypatch,
):
    tasks = [_task(i) for i in range(8)]
    _run_hunt(tmp_path, repo_web, monkeypatch, workers=8, tasks=tasks,
              provider=docker_mod.DockerSandboxProvider(), CRUCIBLE_HUNT_MAX_TASKS=8)
    assert fake_agent.max_active >= 2, "tasks never overlapped"
    for tid, out in fake_agent.seen.items():
        assert f"--- stdout ---\n{tid}\n" in out, f"{tid} saw {out!r}"
    assert not fake_docker.scratch, "a task's container was not destroyed"


# ------------------------------------------------------------ (b) determinism


def test_parallel_matches_sequential(fake_agent, tmp_path, repo_web, monkeypatch):
    tasks = [_task(i, area=("api" if i < 4 else "core")) for i in range(8)]
    seq = _run_hunt(tmp_path, repo_web, monkeypatch, workers=1, tasks=tasks, name="seq",
                    CRUCIBLE_HUNT_MAX_TASKS=6)
    seq_done = list(fake_agent.done_order)
    fake_agent.done_order.clear()
    par = _run_hunt(tmp_path, repo_web, monkeypatch, workers=6, tasks=tasks, name="par",
                    CRUCIBLE_HUNT_MAX_TASKS=6)

    assert fake_agent.max_active >= 2
    assert fake_agent.done_order != seq_done, "pool finished in batch order; test proves nothing"
    assert _normalize(par) == _normalize(seq)
    n = _normalize(seq)
    assert n["finding_ids"] == ["t00", "t02", "t04"]           # batch order, evens
    assert n["fork_count"] == 2                                # t00, t03
    assert [t["task_id"] for t in n["pending_hunts"]] == [
        "t01", "t03", "t05",           # shallow odd tasks, re-queued once
        "t06", "t07",                  # beyond max_tasks_per_run
        "fork", "fork",                # forks, in parent (t00, t03) order
    ]
    assert [t.get("forked_from") for t in n["pending_hunts"][-2:]] == ["t00", "t03"]
    # every finding file landed, one per finding
    assert len(list((tmp_path / "par" / "findings").glob("*.json"))) == 3


def test_fork_budget_is_shared_across_concurrent_tasks(fake_agent, tmp_path, repo_web, monkeypatch):
    # 12 tasks, 4 of which fork (t00, t03, t06, t09), budget 2 -> exactly 2.
    tasks = [_task(i) for i in range(12)]
    out = _run_hunt(tmp_path, repo_web, monkeypatch, workers=12, tasks=tasks,
                    CRUCIBLE_HUNT_MAX_TASKS=12, CRUCIBLE_HUNT_MAX_FORKS=2)
    assert out["fork_count"] == 2
    assert sum(t["task_id"].startswith("fork-") for t in out["pending_hunts"]) == 2


def test_coverage_blocks_are_not_interleaved(fake_agent, tmp_path, repo_web, monkeypatch):
    tasks = [_task(i) for i in range(8)]  # all in one area -> one coverage file
    _run_hunt(tmp_path, repo_web, monkeypatch, workers=8, tasks=tasks, CRUCIBLE_HUNT_MAX_TASKS=8)
    text = (tmp_path / "ws" / "coverage" / "api.md").read_text()
    blocks = [b for b in text.split("\n## ") if b.strip()]
    assert len(blocks) == 8
    for b in blocks:
        lines = b.strip().splitlines()
        assert lines[0].startswith("t") and lines[1].startswith("scope: hint ")
        assert lines[1] == f"scope: hint {int(lines[0][1:3])}", "blocks interleaved"


# ------------------------------------------------ (c) config + sequential mode


def test_hunt_settings_defaults_and_env(monkeypatch):
    for var, *_ in config._HUNT_KNOBS.values():
        monkeypatch.delenv(var, raising=False)
    assert config.hunt_settings() == config.HuntSettings(
        workers=4, max_tasks_per_run=12, max_forks_per_run=12, max_cycles=2,
    )
    monkeypatch.setenv("CRUCIBLE_HUNT_WORKERS", "0")    # clamped to the minimum
    monkeypatch.setenv("CRUCIBLE_HUNT_MAX_TASKS", "30")
    monkeypatch.setenv("CRUCIBLE_MAX_CYCLES", "3")
    s = config.hunt_settings()
    assert (s.workers, s.max_tasks_per_run, s.max_cycles) == (1, 30, 3)
    assert hooks.max_cycles() == 3
    monkeypatch.setenv("CRUCIBLE_HUNT_WORKERS", "lots")
    with pytest.raises(ValueError, match="CRUCIBLE_HUNT_WORKERS"):
        config.hunt_settings()


def test_config_file_hunt_block_env_still_wins(monkeypatch, tmp_path):
    for var, *_ in config._HUNT_KNOBS.values():
        monkeypatch.delenv(var, raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("hunt:\n  workers: 1\n  max_tasks_per_run: 24\n  max_cycles: 3\n")
    monkeypatch.setenv("CRUCIBLE_MAX_CYCLES", "5")
    config.apply_file_hunt_env(cfg)
    s = config.hunt_settings()
    assert (s.workers, s.max_tasks_per_run, s.max_forks_per_run, s.max_cycles) == (1, 24, 12, 5)


def test_workers_1_runs_on_the_node_thread(fake_agent, tmp_path, repo_web, monkeypatch):
    tasks = [_task(i) for i in range(5)]
    out = _run_hunt(tmp_path, repo_web, monkeypatch, workers=1, tasks=tasks,
                    CRUCIBLE_HUNT_MAX_TASKS=3)
    assert set(fake_agent.threads) == {threading.current_thread().name}
    assert fake_agent.max_active == 1
    assert fake_agent.done_order == ["t00", "t01", "t02"]
    # t03/t04 were over the cap and stay queued (after the shallow re-queue of t01)
    assert [t["task_id"] for t in out["pending_hunts"][:3]] == ["t01", "t03", "t04"]


def test_legacy_single_sandbox_provider_forces_sequential(fake_agent, tmp_path, repo_web,
                                                          monkeypatch):
    class LegacyProvider:
        """Old §10 shape: create() mutates self, exec/destroy on the provider."""

        def __init__(self):
            self.state = None

        def create(self, task_id, repo_mount, limits):
            self.state = ""

        def exec(self, cmd, timeout_s):
            if cmd.startswith("put "):
                self.state = cmd[4:]
            return SimpleNamespace(exit_code=0, stdout=self.state, stderr="", timed_out=False)

        def destroy(self):
            self.state = None

    tasks = [_task(i) for i in range(4)]
    _run_hunt(tmp_path, repo_web, monkeypatch, workers=4, tasks=tasks,
              provider=LegacyProvider(), CRUCIBLE_HUNT_MAX_TASKS=4)
    assert fake_agent.max_active == 1
    for tid, out in fake_agent.seen.items():
        assert f"--- stdout ---\n{tid}\n" in out
