"""R1b parallel fan-out — issue #34 phase 3.

No real model: `_explore` / `_emit` are monkeypatched. Asserts that (a) the
result set is identical for parallel=1 and parallel=4, (b) distinct thread_ids
are used, and (c) the pool actually overlaps work when width > 1.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from crucible.graph.nodes import recon
from crucible.recon.schema import FileEntry, RepoKind, Seed, SubsystemMap

PARTITION = [
    {"name": f"s{i}", "responsibility": "", "external_facing": False,
     "paths": [f"s{i}"], "files": [f"s{i}/a.py"], "depends_on": []}
    for i in range(4)
]


def _seed() -> Seed:
    return Seed(
        repo_path="/x", primary_language="python", repo_kind=RepoKind.WEB_API,
        files=[FileEntry(path=f"s{i}/a.py", language="python", loc=10, role="source")
               for i in range(4)],
    )


@pytest.fixture
def fake_agent(monkeypatch):
    seen_threads: list[str] = []
    seen_tids: list[str] = []
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def fake_explore(*, thread_id, **kw):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            seen_threads.append(threading.current_thread().name)
            seen_tids.append(thread_id)
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return []

    def fake_emit(model, prompt, task, digest, schema):
        return SubsystemMap()

    monkeypatch.setattr(recon, "_explore", fake_explore)
    monkeypatch.setattr(recon, "_emit", fake_emit)
    return SimpleNamespace(threads=seen_threads, tids=seen_tids, active=active)


def _deps():
    reg = SimpleNamespace(chat_model=lambda role: object())
    return SimpleNamespace(registry=reg, store=None)


def test_parallel_and_sequential_agree(fake_agent, monkeypatch, tmp_path):
    monkeypatch.setattr(recon, "RECON_MAX_PARALLEL", 1)
    seq = recon._run_map(_seed(), "/x", "run1", _deps(), tmp_path / "e.jsonl", PARTITION)

    monkeypatch.setattr(recon, "RECON_MAX_PARALLEL", 4)
    par = recon._run_map(_seed(), "/x", "run2", _deps(), tmp_path / "e2.jsonl", PARTITION)

    assert {s.subsystem for s in seq} == {s.subsystem for s in par} == {"s0", "s1", "s2", "s3"}


def test_pool_overlaps_and_uses_distinct_thread_ids(fake_agent, monkeypatch, tmp_path):
    monkeypatch.setattr(recon, "RECON_MAX_PARALLEL", 4)
    recon._run_map(_seed(), "/x", "runX", _deps(), tmp_path / "e.jsonl", PARTITION)
    assert fake_agent.active["max"] >= 2, "work never overlapped"
    assert len(set(fake_agent.tids)) == 4, "thread_ids not distinct per subsystem"
    assert all("runX:recon:map:s" in t for t in fake_agent.tids)


def test_one_subsystem_failure_does_not_sink_the_batch(fake_agent, monkeypatch, tmp_path):
    orig = recon._emit

    def flaky(model, prompt, task, digest, schema):
        if "Subsystem 's2'" in task:
            raise RuntimeError("boom")
        return orig(model, prompt, task, digest, schema)

    monkeypatch.setattr(recon, "_emit", flaky)
    monkeypatch.setattr(recon, "RECON_MAX_PARALLEL", 4)
    out = recon._run_map(_seed(), "/x", "runF", _deps(), tmp_path / "e.jsonl", PARTITION)
    assert {s.subsystem for s in out} == {"s0", "s1", "s3"}
    assert (tmp_path / "e.jsonl").read_text().count("s2") >= 1
