"""Hunt _emit's structural repair loop (issue #69).

A Hunter that emits a `proposed_patch` whose unified-diff hunk header
miscounts lines (or a `file_path` that doesn't exist) gets one repair turn,
fed the exact `git apply` / path error, before Pass A ever sees it.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from crucible.graph.nodes.hunt import HuntResult, _emit

_BASE_FINDING = {
    "threat_model": {
        "attacker": "unauthenticated remote client",
        "boundary_crossed": "HTTP body -> os.system",
        "assumption_broken": "shell metacharacters rejected",
    },
    "title": "command injection in ping handler",
    "file_path": "app.py",
    "line_start": 2,
    "line_end": 2,
    "description": "user-controlled host reaches os.system",
    "poc_test": "def test_it():\n    assert run('a;id') != 0",
    "severity": "high",
}

# Header claims 5 old / 5 new lines but only 3 lines actually follow — the
# exact "off by a few" hunk-header miscount pattern seen from real Hunter
# output.
_CORRUPT_PATCH = "--- a/app.py\n+++ b/app.py\n@@ -1,5 +1,5 @@\n line1\n-bad\n+good\n line3\n"
# Correct: 1 context line, 1 removed, 1 added, 1 context — header matches.
_GOOD_PATCH = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n line1\n-bad\n+good\n line3\n"


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("line1\nbad\nline3\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return str(repo)


class _Bound:
    """Fake bound-tool model: returns a scripted sequence of tool_calls."""

    def __init__(self, name, responses):
        self._name = name
        self._responses = list(responses)
        self.n_calls = 0

    def invoke(self, _msgs):
        self.n_calls += 1
        args = self._responses[min(self.n_calls, len(self._responses)) - 1]
        return SimpleNamespace(content="", tool_calls=[{"name": self._name, "args": args}])


class _Model:
    def __init__(self, responses):
        self._responses = responses
        self.bound: _Bound | None = None

    def bind_tools(self, _schemas, tool_choice=None):
        self.bound = _Bound(tool_choice, self._responses)
        return self.bound


def test_emit_repairs_corrupt_patch_on_retry(tmp_path):
    repo = _init_repo(tmp_path)
    bad = {**_BASE_FINDING, "proposed_patch": _CORRUPT_PATCH}
    good = {**_BASE_FINDING, "proposed_patch": _GOOD_PATCH}
    model = _Model([
        {"finding_found": True, "finding": bad, "negative_note": ""},
        {"finding_found": True, "finding": good, "negative_note": ""},
    ])

    result = _emit(model, "system", "task", "digest", repo=repo)

    assert isinstance(result, HuntResult)
    assert result.finding.proposed_patch == _GOOD_PATCH
    assert model.bound.n_calls == 2  # one repair turn, no more


def test_emit_gives_up_after_repair_budget_and_still_returns_result(tmp_path):
    repo = _init_repo(tmp_path)
    bad = {**_BASE_FINDING, "proposed_patch": _CORRUPT_PATCH}
    model = _Model([{"finding_found": True, "finding": bad, "negative_note": ""}])

    result = _emit(model, "system", "task", "digest", repo=repo)

    # Still broken after every attempt -> Pass A will mark it mechanical_failed,
    # but _emit must not crash or silently drop the finding.
    assert isinstance(result, HuntResult)
    assert result.finding.proposed_patch == _CORRUPT_PATCH
    assert model.bound.n_calls == 3


def test_emit_skips_repair_check_when_no_repo_given():
    bad = {**_BASE_FINDING, "proposed_patch": _CORRUPT_PATCH}
    model = _Model([{"finding_found": True, "finding": bad, "negative_note": ""}])

    result = _emit(model, "system", "task", "digest", repo=None)

    assert isinstance(result, HuntResult)
    assert model.bound.n_calls == 1  # no repo -> no patch check -> no retry


def test_emit_passes_through_clean_finding_on_first_try(tmp_path):
    repo = _init_repo(tmp_path)
    good = {**_BASE_FINDING, "proposed_patch": _GOOD_PATCH}
    model = _Model([{"finding_found": True, "finding": good, "negative_note": ""}])

    result = _emit(model, "system", "task", "digest", repo=repo)

    assert isinstance(result, HuntResult)
    assert result.finding.proposed_patch == _GOOD_PATCH
    assert model.bound.n_calls == 1
