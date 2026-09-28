"""PoC gate end to end, in real Docker sandboxes (issue #9).

Opt-in: set CRUCIBLE_DOCKER_TESTS=1 (needs a running Docker daemon; pulls
python:3.12-bookworm and node:22-bookworm on first use).
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from crucible.sandbox.prepare import NODE_IMAGE, PYTHON_IMAGE, prepare_repo
from crucible.validation import poc_gate
from crucible.validation.poc_gate import run_poc_gate

pytestmark = pytest.mark.skipif(
    os.environ.get("CRUCIBLE_DOCKER_TESTS") != "1", reason="set CRUCIBLE_DOCKER_TESTS=1",
)

_VULN = 'def is_safe_redirect(url):\n    return True\n'
_FIXED_PATCH = (
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def is_safe_redirect(url):\n"
    "-    return True\n+    return url.startswith(\"/\") and not url.startswith(\"//\")\n"
)
_REAL_POC = (
    "from app import is_safe_redirect\n"
    "assert not is_safe_redirect('https://evil.example'), 'open redirect'\n"
)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(poc_gate, "_cache", {})


def _git_repo(path, files):
    path.mkdir()
    for name, body in files.items():
        (path / name).write_text(body)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    return path


def _setup(tmp_path, image, files):
    from crucible.sandbox.docker import DockerSandboxProvider

    provider = DockerSandboxProvider(image=image)
    repo = _git_repo(tmp_path / "repo", files)
    prepared = prepare_repo(repo, tmp_path / "prep", provider, commit="c1")
    return provider, prepared


def _finding(poc, command="python3 crucible_poc.py", filename="crucible_poc.py", patch=_FIXED_PATCH):
    return SimpleNamespace(poc_test=poc, poc_command=command, poc_filename=filename,
                           proposed_patch=patch)


def test_real_poc_fails_clean_and_passes_patched(tmp_path):
    provider, prepared = _setup(tmp_path, PYTHON_IMAGE, {"app.py": _VULN})
    result = run_poc_gate(_finding(_REAL_POC), provider=provider, prepared=prepared)
    assert result.passed, result.reasons


def test_vacuous_poc_is_rejected(tmp_path):
    provider, prepared = _setup(tmp_path, PYTHON_IMAGE, {"app.py": _VULN})
    result = run_poc_gate(_finding("print('All actions are pinned')"), provider=provider, prepared=prepared)
    assert "passes on the unmodified repo" in result.reasons[0]


def test_poc_that_does_not_test_the_claim_is_rejected(tmp_path):
    provider, prepared = _setup(tmp_path, PYTHON_IMAGE, {"app.py": _VULN})
    poc = "from app import is_safe_redirect\nassert is_safe_redirect('https://x') is False\nraise SystemExit(1)\n"
    result = run_poc_gate(_finding(poc), provider=provider, prepared=prepared)
    assert "still fails with the patch applied" in result.reasons[0]


def test_poc_with_missing_module_is_rejected(tmp_path):
    provider, prepared = _setup(tmp_path, PYTHON_IMAGE, {"app.py": _VULN})
    result = run_poc_gate(_finding("import yaml\n"), provider=provider, prepared=prepared)
    assert "couldn't run on the unmodified repo (import error)" in result.reasons[0]


def test_poc_that_edits_source_is_rejected(tmp_path):
    provider, prepared = _setup(tmp_path, PYTHON_IMAGE, {"app.py": _VULN})
    poc = "open('app.py', 'w').write('def is_safe_redirect(u): return False\\n')\nraise SystemExit(1)\n"
    result = run_poc_gate(_finding(poc), provider=provider, prepared=prepared)
    assert "modified tracked source files while running: app.py" in result.reasons[0]


def test_node_poc_runs_in_the_node_image(tmp_path):
    provider, prepared = _setup(tmp_path, NODE_IMAGE, {
        "package.json": '{"name": "app", "version": "1.0.0"}',
        "package-lock.json": '{"name": "app", "version": "1.0.0", "lockfileVersion": 3, '
                             '"requires": true, "packages": {"": {"name": "app", "version": "1.0.0"}}}',
        "app.js": "exports.isSafe = (u) => true\n",
    })
    assert prepared.installed, prepared.detail
    patch = ("--- a/app.js\n+++ b/app.js\n@@ -1 +1 @@\n-exports.isSafe = (u) => true\n"
             "+exports.isSafe = (u) => u.startsWith('/') && !u.startsWith('//')\n")
    poc = "const {isSafe} = require('./app'); if (isSafe('https://evil')) process.exit(1)\n"
    f = _finding(poc, command="node crucible-poc.js", filename="crucible-poc.js", patch=patch)
    result = run_poc_gate(f, provider=provider, prepared=prepared)
    assert result.passed, result.reasons
