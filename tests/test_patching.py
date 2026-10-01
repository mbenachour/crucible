"""fix_plan -> files -> windows -> git-built diff (issue #111). No model."""

from __future__ import annotations

import os
import subprocess

import pytest

from crucible.graph.patch_rewrite import extract_text
from crucible.validation.patching import (
    PlanError,
    build_patch,
    normalize_rewrite,
    resolve_plan,
    rewrite_problem,
    splice,
)
from crucible.validation.schema import EmittedFinding, Finding, FixStep


def _step(path="app.py", a=2, b=2, **kw):
    return FixStep(file_path=path, line_start=a, line_end=b, change="fix it", **kw)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "app.py").write_text("line1\nbad\nline3\n")
    (r / "big.py").write_text("".join(f"x{i} = {i}\n" for i in range(1, 1001)))
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    return r


def _applies(repo, patch):
    return subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"], input=patch,
                          text=True, capture_output=True, check=False).returncode == 0


def _build(repo, target, text):
    text = normalize_rewrite(target, text)
    assert rewrite_problem(target, text) == ""
    return build_patch(str(repo), {target.path: splice(target, text)})


# --- resolve_plan ----------------------------------------------------------


def test_short_file_is_shown_whole(repo):
    [t] = resolve_plan([_step()], str(repo))
    assert t.whole and t.text == "line1\nbad\nline3\n"


def test_long_file_gets_cited_lines_plus_margin(repo):
    [t] = resolve_plan([_step("big.py", 500, 502)], str(repo))
    assert (t.start, t.end) == (499 - 40, 502 + 40)
    assert t.text.startswith("x460 = 460\n")


def test_steps_in_one_file_share_one_window(repo):
    [t] = resolve_plan([_step("big.py", 100, 100), _step("big.py", 200, 201)], str(repo))
    assert (t.start, t.end) == (99 - 40, 201 + 40) and len(t.steps) == 2


def test_window_is_clamped_to_the_file(repo):
    [t] = resolve_plan([_step("big.py", 990, 1000)], str(repo))
    assert t.end == 1000


@pytest.mark.parametrize("path,why", [
    ("/etc/passwd", "relative"),
    ("../outside.py", "'..'"),
    ("src/../../x.py", "'..'"),
    (".git/config", ".git"),
    ("", "empty"),
])
def test_unsafe_paths_are_refused(repo, path, why):
    with pytest.raises(PlanError) as e:
        resolve_plan([_step(path)], str(repo))
    assert why in str(e.value)


def test_symlink_out_of_the_repo_is_refused(repo, tmp_path):
    (tmp_path / "secret.txt").write_text("a\nb\n")
    os.symlink(tmp_path / "secret.txt", repo / "link.txt")
    with pytest.raises(PlanError, match="outside the repo"):
        resolve_plan([_step("link.txt", 1, 1)], str(repo))


def test_symlink_inside_the_repo_names_the_real_file(repo):
    os.symlink(repo / "app.py", repo / "alias.py")
    with pytest.raises(PlanError, match="use 'app.py'"):
        resolve_plan([_step("alias.py", 1, 1)], str(repo))


def test_missing_file_and_bad_range_are_both_reported(repo):
    with pytest.raises(PlanError) as e:
        resolve_plan([_step("nope.py"), _step("app.py", 3, 7)], str(repo))
    assert len(e.value.problems) == 2
    assert "does not exist" in e.value.problems[0]
    assert "out of range (the file has 3 lines)" in e.value.problems[1]


def test_new_file_that_exists_is_refused(repo):
    with pytest.raises(PlanError, match="file exists"):
        resolve_plan([FixStep(file_path="app.py", change="x", new_file=True)], str(repo))


def test_empty_plan_is_refused(repo):
    with pytest.raises(PlanError, match="empty"):
        resolve_plan([], str(repo))


# --- guards ----------------------------------------------------------------


def test_unchanged_rewrite_is_rejected(repo):
    [t] = resolve_plan([_step()], str(repo))
    assert "unchanged" in rewrite_problem(t, normalize_rewrite(t, "line1\nbad\nline3"))


def test_rewrite_keeping_under_half_the_lines_is_rejected(repo):
    [t] = resolve_plan([_step("big.py", 500, 500)], str(repo))
    assert "were kept" in rewrite_problem(t, normalize_rewrite(t, "x460 = 460\nx542 = 542\n"))


def test_window_that_lost_an_edge_line_is_rejected(repo):
    [t] = resolve_plan([_step("big.py", 500, 500)], str(repo))
    lines = t.text.splitlines(keepends=True)
    truncated = "".join(lines[:-1]).replace("x500 = 500", "x500 = 0")
    assert "first and last lines" in rewrite_problem(t, normalize_rewrite(t, truncated))


# --- building --------------------------------------------------------------


def test_window_rewrite_splices_into_a_small_patch(repo):
    [t] = resolve_plan([_step("big.py", 500, 500)], str(repo))
    patch = _build(repo, t, t.text.replace("x500 = 500", "x500 = -1"))
    assert _applies(repo, patch)
    assert "-x500 = 500\n+x500 = -1\n" in patch
    changed = [ln for ln in patch.splitlines()[4:] if ln[:1] in "+-"]
    assert changed == ["-x500 = 500", "+x500 = -1"]


def test_dropped_final_newline_is_restored(repo):
    [t] = resolve_plan([_step()], str(repo))
    patch = _build(repo, t, "line1\ngood\nline3")
    assert _applies(repo, patch) and "No newline" not in patch


def test_file_without_final_newline_keeps_it_that_way(repo):
    (repo / "app.py").write_text("line1\nbad\nline3")
    [t] = resolve_plan([_step()], str(repo))
    patch = _build(repo, t, "line1\ngood\nline3\n")
    assert _applies(repo, patch)
    # line3 is untouched context, still without a newline — not removed and re-added
    assert "-line3" not in patch and "+line3" not in patch


def test_crlf_file_keeps_crlf(repo):
    (repo / "app.py").write_bytes(b"line1\r\nbad\r\nline3\r\n")
    [t] = resolve_plan([_step()], str(repo))
    patch = _build(repo, t, "line1\ngood\nline3\n")
    assert _applies(repo, patch)
    assert "+good\r\n" in patch


def test_executable_bit_is_not_flipped(repo):
    os.chmod(repo / "app.py", 0o755)
    [t] = resolve_plan([_step()], str(repo))
    patch = _build(repo, t, "line1\ngood\nline3\n")
    assert "mode" not in patch and _applies(repo, patch)


def test_new_file_patch_creates_the_file(repo):
    [t] = resolve_plan([FixStep(file_path="pkg/new.py", change="x", new_file=True)], str(repo))
    patch = _build(repo, t, "VALUE = 1")
    assert "new file mode 100644" in patch and "+VALUE = 1\n" in patch
    assert _applies(repo, patch)


# --- reply parsing / schema -------------------------------------------------


def test_extract_text_keeps_inner_fences():
    reply = "Here you go:\n````markdown\n# doc\n```sh\nls\n```\n````\n"
    assert extract_text(reply) == "# doc\n```sh\nls\n```\n"


def test_extract_text_without_fences_is_the_reply():
    assert extract_text("a\nb\n") == "a\nb\n"


def test_emitted_finding_matches_finding_with_fix_plan_in_place_of_patch():
    expected = ["fix_plan" if f == "proposed_patch" else f for f in Finding.model_fields]
    assert list(EmittedFinding.model_fields) == expected


def test_to_finding_swaps_the_patch_in():
    ef = EmittedFinding.model_validate({
        "threat_model": {"attacker": "a", "boundary_crossed": "b", "assumption_broken": "c"},
        "title": "t", "file_path": "app.py", "line_start": 1, "line_end": 1,
        "description": "d", "poc_test": "p", "severity": "low",
        "fix_plan": [{"file_path": "app.py", "line_start": 1, "line_end": 1, "change": "x"}],
    })
    f = ef.to_finding("PATCH")
    assert isinstance(f, Finding) and f.proposed_patch == "PATCH" and f.title == "t"
