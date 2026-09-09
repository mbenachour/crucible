"""Validate Pass B / Pass C + Report — the deterministic funnel to `report.json`
(issues #10, #11, #12). Model calls are faked; the plumbing and persistence are
real."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from crucible.graph.nodes import report as report_node
from crucible.graph.nodes import validate_bug, validate_reachability
from crucible.store.dao import Store
from crucible.store.models import FindingRow
from crucible.workspace.fs import init_workspace

_PAYLOAD = {
    "threat_model": {
        "attacker": "unauthenticated remote client",
        "boundary_crossed": "HTTP body -> os.system",
        "assumption_broken": "shell metacharacters rejected",
    },
    "title": "command injection in ping handler",
    "file_path": "app.py",
    "line_start": 1,
    "line_end": 3,
    "description": "user-controlled host reaches os.system",
    "poc_test": "def test_it():\n    assert run('a;id') != 0",
    "proposed_patch": "--- a/app.py\n+++ b/app.py\n@@\n-bad\n+good\n",
    "severity": "high",
}


# --- fakes ---------------------------------------------------------------------


class _Bound:
    def __init__(self, name, verdict):
        self._name, self._verdict = name, verdict

    def invoke(self, _msgs):
        return SimpleNamespace(
            content="",
            tool_calls=[{"name": self._name, "args": {"verdict": self._verdict, "reasoning": "fake"}}],
        )


class _Model:
    def __init__(self, verdict):
        self._verdict = verdict

    def bind_tools(self, _schemas, tool_choice=None):
        return _Bound(tool_choice, self._verdict)


class _Registry:
    def __init__(self, verdict):
        self._verdict = verdict

    def chat_model(self, _role):
        return _Model(self._verdict)

    def endpoint(self, _role):
        return SimpleNamespace(provider=SimpleNamespace(value="fake"), model="m1")


def _deps(store, verdict=None):
    return SimpleNamespace(store=store, registry=_Registry(verdict) if verdict else None)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    init_workspace(ws)
    (ws / "app.py").write_text("import os\n\nos.system('ping ' + host)\n")
    return ws


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{tmp_path / 'f.sqlite'}")


def _state(ws):
    return {
        "run_id": "r1", "repo_path": str(ws), "workspace_path": str(ws),
        "repo_commit": "deadbeef", "primary_language": "python",
        "architecture_path": "", "recon_quality": "seed_only",
        "cycle_count": 1, "continuation_count": 2, "fork_count": 0, "token_spend": 0,
    }


def _seed(store, status: str, fid="h0001-aaaaaa"):
    store.create_run("r1", "/x", "deadbeef", "python")
    store.add_finding(FindingRow(
        finding_id=fid, run_id="r1", stable_key="k", payload=_PAYLOAD, status=status,
        hunter_model="fake:h", hunter_prompt_version="cmd@1", hunter_sampling={},
    ))
    return fid


# --- Pass B ------------------------------------------------------------------


def test_validate_bug_refuted(store, workspace):
    fid = _seed(store, "mechanical_passed")
    validate_bug.run(_state(workspace), deps=_deps(store, "refuted"))
    assert store.get_finding(fid).status == "bug_refuted"
    assert store.finding_reasons(fid, "bug")  # reasoning recorded


def test_validate_bug_upheld(store, workspace):
    fid = _seed(store, "mechanical_passed")
    validate_bug.run(_state(workspace), deps=_deps(store, "upheld"))
    assert store.get_finding(fid).status == "bug_upheld"


def test_validate_bug_no_model_upholds_conservatively(store, workspace):
    fid = _seed(store, "mechanical_passed")
    validate_bug.run(_state(workspace), deps=_deps(store, None))
    assert store.get_finding(fid).status == "bug_upheld"
    assert "no VALIDATOR_BUG model" in " ".join(store.finding_reasons(fid, "bug"))


def test_validate_bug_ignores_non_candidates(store, workspace):
    fid = _seed(store, "raw")  # not mechanical_passed
    validate_bug.run(_state(workspace), deps=_deps(store, "refuted"))
    assert store.get_finding(fid).status == "raw"


# --- Pass C ------------------------------------------------------------------


def test_validate_reachability_promotes_bug_upheld(store, workspace):
    fid = _seed(store, "bug_upheld")
    validate_reachability.run(_state(workspace), deps=_deps(store, "upheld"))
    assert store.get_finding(fid).status == "reach_upheld"


def test_validate_reachability_refuted(store, workspace):
    fid = _seed(store, "bug_upheld")
    validate_reachability.run(_state(workspace), deps=_deps(store, "refuted"))
    assert store.get_finding(fid).status == "reach_refuted"


# --- Report ----------------------------------------------------------------


def test_report_renders_upheld_finding(store, workspace):
    fid = _seed(store, "reach_upheld")
    report_node.run(_state(workspace), deps=_deps(store))

    rep = json.loads((workspace / "report.json").read_text())
    assert rep["counts"]["upheld"] == 1
    assert rep["counts"]["total"] == 1
    assert rep["findings"][0]["finding_id"] == fid
    assert rep["findings"][0]["provenance"]["hunter_model"] == "fake:h"
    assert rep["repo_commit"] == "deadbeef"

    md = (workspace / "report.md").read_text()
    assert "command injection in ping handler" in md
    assert "```diff" in md


def test_report_empty_is_valid(store, workspace):
    store.create_run("r1", "/x", "deadbeef", "python")
    report_node.run(_state(workspace), deps=_deps(store))
    rep = json.loads((workspace / "report.json").read_text())
    assert rep["counts"]["upheld"] == 0
    assert "None survived" in (workspace / "report.md").read_text()
