"""PoC gate decision logic (issue #9), against a fake sandbox that returns a
scripted result for each gate step. `test_poc_gate_docker.py` runs the real
thing."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from crucible.sandbox import ExecResult
from crucible.validation import poc_gate
from crucible.validation.poc_gate import poc_gate_reasons, run_poc_gate

_PATCH = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-bad\n+good\n"
OK, FAIL = ExecResult(0, "", ""), ExecResult(1, "AssertionError: bug present", "")


class FakeSandbox:
    def __init__(self, results: dict[str, ExecResult]):
        self.results = results
        self.steps: list[str] = []
        self.destroyed = False

    def exec(self, cmd, timeout_s):
        step = re.match(r": crucible:(\w+);", cmd).group(1)
        self.steps.append(step)
        return self.results.get(step, OK)

    def destroy(self):
        self.destroyed = True


class FakeProvider:
    def __init__(self, results=None):
        self.sandbox = FakeSandbox(results or {})
        self.creates = 0

    def create(self, task_id, repo_mount, limits):
        self.creates += 1
        return self.sandbox


def _finding(**kw):
    base = {"poc_test": "assert fixed()", "poc_filename": "crucible_poc.py",
            "poc_command": "python3 crucible_poc.py", "proposed_patch": _PATCH}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def prepared(tmp_path):
    (tmp_path / "app.py").write_text("bad\n")
    return SimpleNamespace(path=str(tmp_path))


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(poc_gate, "_cache", {})
    monkeypatch.delenv("CRUCIBLE_POC_GATE", raising=False)


def _gate(prepared, finding=None, **results):
    provider = FakeProvider(results)
    result = run_poc_gate(finding or _finding(), provider=provider, prepared=prepared)
    return result, provider


def test_fail_clean_then_pass_patched_is_accepted(prepared):
    result, provider = _gate(prepared, run_clean=FAIL, run_patched=OK)
    assert result.passed and result.reasons == []
    assert provider.sandbox.steps == [
        "setup", "run_clean", "status_clean", "apply", "run_patched", "status_patched",
    ]
    assert provider.sandbox.destroyed


def test_poc_that_passes_on_clean_repo_is_rejected(prepared):
    # The vacuous-PoC case: "All actions are pinned" on an unpinned workflow.
    result, _ = _gate(prepared, run_clean=OK)
    assert not result.passed
    assert "passes on the unmodified repo" in result.reasons[0]


def test_poc_that_still_fails_patched_is_rejected(prepared):
    result, _ = _gate(prepared, run_clean=FAIL, run_patched=FAIL)
    assert "still fails with the patch applied" in result.reasons[0]


@pytest.mark.parametrize("output", [
    "ModuleNotFoundError: No module named 'yaml'",
    "Error: Cannot find module 'src/plugins/marked'",
    "No test files found, exiting with code 1",
    " ❯ src/crucible-poc.spec.ts (0 test)\n Test Files  1 failed (1)\n      Tests  no tests",
    "No tests found, exiting with code 1",
    "  File \"x.py\", line 3\nSyntaxError: invalid syntax",
])
def test_poc_that_could_not_run_is_rejected_even_if_patched_passes(prepared, output):
    # Otherwise a PoC importing a module the patch adds would "fail clean,
    # pass patched" without ever exercising the vulnerable code.
    result, provider = _gate(prepared, run_clean=ExecResult(1, output, ""), run_patched=OK)
    assert "couldn't run on the unmodified repo" in result.reasons[0]
    assert "run_patched" not in provider.sandbox.steps


def test_command_not_found_exit_code_is_rejected(prepared):
    result, _ = _gate(prepared, run_clean=ExecResult(127, "", "bash: vitest: command not found"))
    assert "couldn't run" in result.reasons[0]


def test_timeout_is_rejected(prepared):
    result, _ = _gate(prepared, run_clean=ExecResult(124, "", "", timed_out=True))
    assert "timed out on the unmodified repo" in result.reasons[0]


def test_poc_modifying_tracked_source_is_rejected(prepared):
    result, _ = _gate(prepared, run_clean=FAIL, status_clean=ExecResult(0, " M app.py\n", ""))
    assert "modified tracked source files" in result.reasons[0] and "app.py" in result.reasons[0]


def test_changes_outside_the_patch_are_rejected(prepared):
    result, _ = _gate(prepared, run_clean=FAIL, run_patched=OK,
                      status_patched=ExecResult(0, " M app.py\n M other.py\n", ""))
    assert "outside the patch: other.py" in result.reasons[0]


def test_changes_inside_the_patch_are_fine(prepared):
    result, _ = _gate(prepared, run_clean=FAIL, run_patched=OK,
                      status_patched=ExecResult(0, " M app.py\n", ""))
    assert result.passed


@pytest.mark.parametrize("finding, expected", [
    (_finding(poc_command=""), "no poc_command"),
    (_finding(poc_filename="/etc/passwd"), "relative path"),
    (_finding(poc_filename="../escape.py"), "relative path"),
    (_finding(poc_filename="app.py"), "already exists"),
    (_finding(poc_filename="app2.py",
              proposed_patch="--- a/app2.py\n+++ b/app2.py\n@@ -1 +1 @@\n-a\n+b\n"),
     "modifies the PoC file"),
])
def test_prechecks_reject_before_any_sandbox_is_started(prepared, finding, expected):
    result, provider = _gate(prepared, finding)
    assert expected in result.reasons[0]
    assert provider.creates == 0


def test_same_finding_is_gated_once(prepared):
    provider = FakeProvider({"run_clean": FAIL})
    run_poc_gate(_finding(), provider=provider, prepared=prepared)
    run_poc_gate(_finding(), provider=provider, prepared=prepared)  # Pass A after Hunt's repair turn
    assert provider.creates == 1


def test_sandbox_start_failure_is_a_rejection(prepared):
    class Broken:
        def create(self, *a):
            raise RuntimeError("docker run failed")

    result = run_poc_gate(_finding(), provider=Broken(), prepared=prepared)
    assert "couldn't start a sandbox" in result.reasons[0]


# --- modes -------------------------------------------------------------------


def test_enforce_is_the_default_and_rejects(prepared):
    reasons = poc_gate_reasons(_finding(), provider=FakeProvider({"run_clean": OK}), prepared=prepared)
    assert "passes on the unmodified repo" in reasons[0]


def test_enforce_without_a_sandbox_rejects():
    reasons = poc_gate_reasons(_finding(), provider=None, prepared=None)
    assert "needs a sandbox" in reasons[0]


def test_advisory_runs_but_never_rejects(prepared, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_POC_GATE", "advisory")
    provider = FakeProvider({"run_clean": OK})
    assert poc_gate_reasons(_finding(), provider=provider, prepared=prepared) == []
    assert provider.creates == 1
    assert poc_gate_reasons(_finding(), provider=None, prepared=None) == []


def test_off_skips_entirely(prepared, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_POC_GATE", "off")
    provider = FakeProvider({"run_clean": OK})
    assert poc_gate_reasons(_finding(), provider=provider, prepared=prepared) == []
    assert provider.creates == 0


def test_old_strict_spelling_means_enforce(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_POC_GATE", "strict")
    assert poc_gate.gate_mode() == "enforce"


def test_report_tells_a_reader_how_to_run_the_poc():
    from crucible.graph.nodes.report import _markdown

    finding = {
        "finding_id": "h1", "severity": "high", "title": "t", "cwe": None,
        "file_path": "app.py", "line_start": 1, "line_end": 1, "threat_model": {},
        "description": "d", "poc_test": "assert fixed()", "poc_filename": "crucible_poc.py",
        "poc_command": "python3 crucible_poc.py", "proposed_patch": _PATCH, "validation_trail": [],
    }
    md = _markdown({
        "repo": "/r", "run_id": "r1", "repo_commit": "a" * 40, "language": "py",
        "generated_at": "now", "recon_quality": "full", "counts": {"upheld": 1},
        "metrics": {"cycles": 1, "fork_rate": "0/0", "token_spend": 0}, "findings": [finding],
    })
    assert "Run `python3 crucible_poc.py` from the repo root" in md
    assert "`crucible_poc.py`" in md
