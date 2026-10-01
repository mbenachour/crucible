"""Hunt _emit: fix_plan -> patch-rewrite -> proposed_patch (issues #69, #111).

The Hunter emits a `fix_plan`; a patch-rewrite model edits the file text and
git writes the diff. Repair turns go back to the Hunter only for what it owns
(a citation or plan naming files/lines that don't exist, a failed PoC gate);
a rewrite failure is retried once by the rewrite step and otherwise leaves the
finding with an empty `proposed_patch`.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from crucible.graph.nodes.hunt import Emission, _emit, _stalled
from crucible.validation.mechanical import patch_problems

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
_GOOD_STEP = {"file_path": "app.py", "line_start": 2, "line_end": 2, "change": "replace bad with good"}
_FIXED = "```\nline1\ngood\nline3\n```"


def _finding(*steps, **over):
    return {"finding_found": True, "negative_note": "",
            "finding": {**_BASE_FINDING, "fix_plan": list(steps or [_GOOD_STEP]), **over}}


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("line1\nbad\nline3\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return str(repo)


class _Bound:
    """Fake bound-tool model: returns a scripted sequence of tool_calls. A
    response of `None` is a reply with no tool call (cut off at the limit)."""

    def __init__(self, name, responses):
        self._name = name
        self._responses = list(responses)
        self.n_calls = 0
        self.asks: list[str] = []

    def invoke(self, msgs):
        self.n_calls += 1
        self.asks.append(msgs[-1].content)
        args = self._responses[min(self.n_calls, len(self._responses)) - 1]
        if args is None:
            return SimpleNamespace(content="", tool_calls=[],
                                   response_metadata={"finish_reason": "length"})
        return SimpleNamespace(content="", tool_calls=[{"name": self._name, "args": args}])


class _Model:
    def __init__(self, responses):
        self._responses = responses
        self.bound: _Bound | None = None

    def bind_tools(self, _schemas, tool_choice=None):
        self.bound = _Bound(tool_choice, self._responses)
        return self.bound


class _Rewriter:
    """Fake patch-rewrite model: scripted plain-text replies."""

    def __init__(self, *replies, finish_reason="stop"):
        self._replies = list(replies)
        self._finish = finish_reason
        self.n_calls = 0
        self.asks: list[str] = []

    def invoke(self, msgs):
        self.n_calls += 1
        self.asks.append(msgs[-1].content)
        text = self._replies[min(self.n_calls, len(self._replies)) - 1]
        return SimpleNamespace(content=text, response_metadata={"finish_reason": self._finish})


def _emit_with(repo, hunter, rewriter, **kw):
    return _emit(hunter, "system", "task", "digest", repo=repo, rewrite_model=rewriter,
                 rewrite_model_id="fake/rewrite", **kw)


def _applies(repo, patch):
    return subprocess.run(["git", "-C", repo, "apply", "--check", "-"], input=patch,
                          text=True, capture_output=True, check=False).returncode == 0


def test_clean_plan_builds_an_applying_patch_on_first_try(tmp_path):
    repo = _init_repo(tmp_path)
    hunter, rw = _Model([_finding()]), _Rewriter(_FIXED)

    result = _emit_with(repo, hunter, rw)

    assert isinstance(result, Emission)
    patch = result.finding.proposed_patch
    assert _applies(repo, patch)
    assert "-bad\n+good\n" in patch
    assert hunter.bound.n_calls == 1 and rw.n_calls == 1
    assert result.extra["fix_plan"][0]["file_path"] == "app.py"
    assert result.extra["patch_rewrite"]["model"] == "fake/rewrite"
    assert result.extra["patch_rewrite"]["calls"] == 1
    assert result.extra["emit"] == {"repairs": 0, "patch_errors": []}


def test_rewrite_model_sees_text_without_line_numbers(tmp_path):
    repo = _init_repo(tmp_path)
    rw = _Rewriter(_FIXED)
    _emit_with(repo, _Model([_finding()]), rw)
    assert "line1\nbad\nline3\n" in rw.asks[0]
    assert "1: line1" not in rw.asks[0]


def test_plan_naming_a_missing_file_goes_back_to_the_hunter(tmp_path):
    repo = _init_repo(tmp_path)
    bad = _finding({**_GOOD_STEP, "file_path": "src/app.py"})
    hunter, rw = _Model([bad, _finding()]), _Rewriter(_FIXED)

    result = _emit_with(repo, hunter, rw)

    assert hunter.bound.n_calls == 2
    assert "file does not exist" in hunter.bound.asks[1]
    assert rw.n_calls == 1  # the bad plan was never rewritten
    assert _applies(repo, result.finding.proposed_patch)
    assert result.extra["emit"]["repairs"] == 1


def test_out_of_range_lines_go_back_to_the_hunter(tmp_path):
    repo = _init_repo(tmp_path)
    bad = _finding({**_GOOD_STEP, "line_start": 9, "line_end": 12})
    hunter = _Model([bad, _finding()])

    result = _emit_with(repo, hunter, _Rewriter(_FIXED))

    assert "out of range (the file has 3 lines)" in hunter.bound.asks[1]
    assert result.finding.proposed_patch


def test_finding_withdrawn_on_a_structure_turn_is_kept(tmp_path):
    repo = _init_repo(tmp_path)
    bad = _finding({**_GOOD_STEP, "file_path": "nope.py"})
    withdrawn = {"finding_found": False, "negative_note": "on reflection, not sure"}
    hunter = _Model([bad, withdrawn])

    result = _emit_with(repo, hunter, _Rewriter(_FIXED))

    assert result.finding_found and result.finding.title == _BASE_FINDING["title"]
    assert result.finding.proposed_patch == ""


def test_rejected_rewrite_is_retried_by_the_rewrite_step_not_the_hunter(tmp_path):
    repo = _init_repo(tmp_path)
    hunter = _Model([_finding()])
    rw = _Rewriter("```\nline1\nbad\nline3\n```", _FIXED)  # unchanged, then fixed

    result = _emit_with(repo, hunter, rw)

    assert hunter.bound.n_calls == 1
    assert rw.n_calls == 2
    assert "came back unchanged" in rw.asks[1]
    assert _applies(repo, result.finding.proposed_patch)
    assert any("unchanged" in e for e in result.extra["emit"]["patch_errors"])


def test_rewrite_failing_twice_leaves_an_empty_patch_for_pass_a(tmp_path):
    repo = _init_repo(tmp_path)
    hunter = _Model([_finding()])
    rw = _Rewriter("```\nline1\nbad\nline3\n```")

    result = _emit_with(repo, hunter, rw)

    assert hunter.bound.n_calls == 1  # a rewrite failure is not the Hunter's to fix
    assert rw.n_calls == 2
    assert result.finding.proposed_patch == ""
    assert patch_problems(result.finding.proposed_patch, repo) == [
        "patch does not apply cleanly: proposed_patch is empty"
    ]


def test_reply_cut_off_at_the_token_limit_is_rejected(tmp_path):
    repo = _init_repo(tmp_path)
    rw = _Rewriter(_FIXED, finish_reason="length")

    result = _emit_with(repo, _Model([_finding()]), rw)

    assert result.finding.proposed_patch == ""
    assert any("cut off" in e for e in result.extra["emit"]["patch_errors"])


def test_placeholder_in_built_patch_triggers_a_rewrite_retry(tmp_path):
    repo = _init_repo(tmp_path)
    rw = _Rewriter("```\nline1\n<commit-sha>\nline3\n```", _FIXED)

    result = _emit_with(repo, _Model([_finding()]), rw)

    assert rw.n_calls == 2
    assert "placeholder" in rw.asks[1]
    assert "+good" in result.finding.proposed_patch


def test_poc_repair_turn_with_unchanged_plan_reuses_the_rewrite(tmp_path):
    repo = _init_repo(tmp_path)
    hunter, rw = _Model([_finding(), _finding()]), _Rewriter(_FIXED)
    gate = iter([["PoC passed on the unmodified repo"], []])

    result = _emit_with(repo, hunter, rw, poc_check=lambda f: next(gate))

    assert hunter.bound.n_calls == 2
    assert "failed the PoC gate" in hunter.bound.asks[1]
    assert "+good" in hunter.bound.asks[1]  # the Hunter sees the patch it got
    assert rw.n_calls == 1
    assert result.extra["emit"]["repairs"] == 1


def test_poc_gate_is_not_run_without_a_patch(tmp_path):
    repo = _init_repo(tmp_path)
    called = []
    _emit_with(repo, _Model([_finding()]), _Rewriter("```\nline1\nbad\nline3\n```"),
               poc_check=lambda f: called.append(f) or ["x"])
    assert called == []


def test_skips_rewrite_when_no_repo_given():
    hunter, rw = _Model([_finding()]), _Rewriter(_FIXED)

    result = _emit_with(None, hunter, rw)

    assert hunter.bound.n_calls == 1 and rw.n_calls == 0
    assert result.finding.proposed_patch == ""


def test_new_file_step_builds_a_creating_patch(tmp_path):
    repo = _init_repo(tmp_path)
    step = {"file_path": "lib/guard.py", "new_file": True, "change": "an allow-list helper"}
    rw = _Rewriter("```python\ndef allowed(h):\n    return h.isalnum()\n```")

    result = _emit_with(repo, _Model([_finding(step)]), rw)

    patch = result.finding.proposed_patch
    assert "new file mode" in patch and "+++ b/lib/guard.py" in patch
    assert _applies(repo, patch)


# --- emit-call failures ----------------------------------------------------


def test_cut_off_emission_is_labelled_and_retried(tmp_path):
    repo = _init_repo(tmp_path)
    hunter = _Model([None, _finding()])

    result = _emit_with(repo, hunter, _Rewriter(_FIXED))

    assert "ran out of output tokens" in hunter.bound.asks[1]
    assert result.finding_found


def test_stalled_negative_is_retried_not_recorded(tmp_path):
    repo = _init_repo(tmp_path)
    stalled = {"finding_found": False, "negative_note": "placeholder — will explore first"}
    real = {"finding_found": False, "negative_note": "checked the handler; input is quoted"}
    hunter = _Model([stalled, real])

    result = _emit_with(repo, hunter, _Rewriter(_FIXED))

    assert hunter.bound.n_calls == 2
    assert "placeholder note" in hunter.bound.asks[1]
    assert result.negative_note == real["negative_note"]


def test_stalled_every_time_records_nothing(tmp_path):
    repo = _init_repo(tmp_path)
    hunter = _Model([{"finding_found": False, "negative_note": "TBD"}])

    assert _emit_with(repo, hunter, _Rewriter(_FIXED)) is None
    assert hunter.bound.n_calls == 3


def test_reasoned_negative_mentioning_placeholder_is_recorded(tmp_path):
    repo = _init_repo(tmp_path)
    note = "Placeholder values in config.js are replaced at build time; nothing reaches the shell."
    hunter = _Model([{"finding_found": False, "negative_note": note}])

    result = _emit_with(repo, hunter, _Rewriter(_FIXED))

    assert hunter.bound.n_calls == 1
    assert result.negative_note == note


@pytest.mark.parametrize("note,stalled", [
    ("placeholder — will explore first", True),
    ("Placeholder", True),
    ("TODO (exploring)", True),
    ("N/A", True),
    ("Will explore the router first", True),
    ("", True),
    ("Checked the template; the placeholder `{{x}}` is auto-escaped.", False),
    ("Pending requests are rate-limited before parsing, so this is safe.", False),
    ("No injection: the TODO in utils.py is unrelated dead code.", False),
])
def test_stalling_pattern(note, stalled):
    assert _stalled(note) is stalled
