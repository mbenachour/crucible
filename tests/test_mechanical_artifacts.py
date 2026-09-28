"""Pass A post-apply artifact checks (issue #106). Cases mirror the broken
patches from run 13349559c81a."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from crucible.validation.mechanical import _check_patch_artifacts, emit_repair_reasons

_PACKAGE_JSON = '{\n  "name": "demo",\n  "scripts": {\n    "build": "vite build"\n  }\n}\n'
_WORKFLOW = (
    "jobs:\n"
    "  build:\n"
    "    steps:\n"
    "      - uses: actions/checkout@v6\n"
)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "package.json").write_text(_PACKAGE_JSON)
    (tmp_path / "deploy.yml").write_text(_WORKFLOW)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return str(tmp_path)


def _f(patch: str):
    return SimpleNamespace(proposed_patch=patch, file_path="package.json", line_start=1, line_end=1)


def _workflow_patch(new_ref: str) -> str:
    return (
        "--- a/deploy.yml\n+++ b/deploy.yml\n@@ -1,4 +1,4 @@\n"
        " jobs:\n   build:\n     steps:\n"
        "-      - uses: actions/checkout@v6\n"
        f"+      - uses: actions/checkout@{new_ref}\n"
    )


def test_invalid_json_after_patch_is_rejected(repo):
    # h0001: missing comma after the scripts object
    patch = (
        "--- a/package.json\n+++ b/package.json\n@@ -1,6 +1,7 @@\n"
        " {\n   \"name\": \"demo\",\n   \"scripts\": {\n     \"build\": \"vite build\"\n"
        "-  }\n+  }\n+  \"private\": true\n }\n"
    )
    reasons = _check_patch_artifacts(_f(patch), repo)
    assert any("package.json no longer parses" in r for r in reasons)


def test_duplicate_json_key_is_rejected(repo):
    patch = (
        "--- a/package.json\n+++ b/package.json\n@@ -1,6 +1,7 @@\n"
        " {\n   \"name\": \"demo\",\n+  \"name\": \"demo2\",\n"
        "   \"scripts\": {\n     \"build\": \"vite build\"\n   }\n }\n"
    )
    reasons = _check_patch_artifacts(_f(patch), repo)
    assert any("duplicate key" in r for r in reasons)


def test_placeholder_ref_is_rejected(repo):
    # h0011: literal "<commit-sha>"
    reasons = _check_patch_artifacts(_f(_workflow_patch("<commit-sha>")), repo)
    assert any("placeholder" in r for r in reasons)


def test_fabricated_sha_is_rejected(repo):
    # h0003: sequential-hex "SHA"
    reasons = _check_patch_artifacts(_f(_workflow_patch("4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f")), repo)
    assert any("looks invented" in r for r in reasons)


def test_real_looking_sha_is_accepted(repo):
    # gf0-004: actions/checkout v4.2.2's real commit
    assert _check_patch_artifacts(_f(_workflow_patch("11bd71901bbe5b1630ceea73d27597364c9af683")), repo) == []


def test_duplicate_yaml_key_is_rejected(repo):
    patch = (
        "--- a/deploy.yml\n+++ b/deploy.yml\n@@ -1,4 +1,5 @@\n"
        " jobs:\n   build:\n     steps:\n       - uses: actions/checkout@v6\n"
        "+  build:\n"
    )
    reasons = _check_patch_artifacts(_f(patch), repo)
    assert any("deploy.yml no longer parses" in r for r in reasons)


def test_original_repo_is_never_modified(repo):
    _check_patch_artifacts(_f(_workflow_patch("<commit-sha>")), repo)
    assert "actions/checkout@v6" in open(f"{repo}/deploy.yml").read()


def test_artifact_problems_reach_the_hunter_repair_turn(repo):
    """A placeholder is fixable, so it goes back to the Hunter during emit
    instead of only failing Pass A afterward."""
    f = _f(_workflow_patch("<commit-sha>"))
    f.file_path = "deploy.yml"
    assert any("placeholder" in r for r in emit_repair_reasons(f, repo))
