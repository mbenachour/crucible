"""Dependency preparation for PoC runs (issue #9)."""

from __future__ import annotations

import pytest

from crucible.sandbox import ExecResult
from crucible.sandbox.prepare import (
    MARKER,
    NODE_IMAGE,
    PYTHON_IMAGE,
    detect_ecosystem,
    prepare_repo,
    prepared_dir_for,
)


@pytest.mark.parametrize("files, name, image, cmd_part", [
    ({"package.json": "{}", "pnpm-lock.yaml": ""}, "node", NODE_IMAGE, "pnpm install --frozen-lockfile --ignore-scripts"),
    ({"package.json": "{}", "yarn.lock": ""}, "node", NODE_IMAGE, "--ignore-scripts"),
    ({"package.json": "{}", "package-lock.json": "{}"}, "node", NODE_IMAGE, "npm ci --ignore-scripts"),
    ({"package.json": "{}"}, "node", NODE_IMAGE, "npm install --ignore-scripts"),
    ({"requirements.txt": "flask\n"}, "python", PYTHON_IMAGE, "-r requirements.txt"),
    ({"README.md": "hi"}, "none", PYTHON_IMAGE, None),
])
def test_detect_ecosystem(tmp_path, files, name, image, cmd_part):
    for f, body in files.items():
        (tmp_path / f).write_text(body)
    eco = detect_ecosystem(tmp_path)
    assert (eco.name, eco.image) == (name, image)
    if cmd_part is None:
        assert eco.install_cmd is None
    else:
        assert cmd_part in eco.install_cmd


def test_pyproject_installs_dependencies_but_not_the_project(tmp_path):
    # An installed copy of the project would shadow the patched source.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["requests>=2", "pyyaml"]\n'
        '[project.optional-dependencies]\ntest = ["pytest-mock"]\n'
    )
    cmd = detect_ecosystem(tmp_path).install_cmd
    assert "'requests>=2'" in cmd and "pyyaml" in cmd and "pytest-mock" in cmd
    assert not cmd.rstrip().endswith(" .")


def test_prepared_dir_is_outside_the_workspace(tmp_path):
    ws = tmp_path / "run1" / "workspace"
    assert prepared_dir_for(ws) == (tmp_path / "run1" / "workspace.prepared").resolve()


class FakeInstaller:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.calls = []

    def install_dependencies(self, host_dir, image, cmd, timeout_s):
        self.calls.append((host_dir, image, cmd))
        return ExecResult(self.exit_code, "done", "" if self.exit_code == 0 else "ERR_PNPM_FETCH_404")


@pytest.fixture
def node_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text("{}")
    (repo / "package-lock.json").write_text("{}")
    (repo / "index.js").write_text("module.exports = 1\n")
    return repo


def test_prepare_copies_and_installs(tmp_path, node_repo):
    installer = FakeInstaller()
    p = prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    assert p.installed and p.ecosystem == "node"
    assert (tmp_path / "prep" / "index.js").is_file()
    assert installer.calls[0][1] == NODE_IMAGE


def test_prepare_is_reused_for_the_same_commit(tmp_path, node_repo):
    installer = FakeInstaller()
    prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    assert len(installer.calls) == 1


def test_prepare_redoes_a_different_commit(tmp_path, node_repo):
    installer = FakeInstaller()
    prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    prepare_repo(node_repo, tmp_path / "prep", installer, commit="def")
    assert len(installer.calls) == 2


def test_failed_install_is_reported_and_retried_next_time(tmp_path, node_repo):
    installer = FakeInstaller(exit_code=1)
    p = prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    assert not p.installed and "ERR_PNPM_FETCH_404" in p.detail
    assert not (tmp_path / "prep" / MARKER).exists()
    prepare_repo(node_repo, tmp_path / "prep", installer, commit="abc")
    assert len(installer.calls) == 2


def test_provider_without_an_installer_still_prepares_the_tree(tmp_path, node_repo):
    p = prepare_repo(node_repo, tmp_path / "prep", object(), commit="abc")
    assert not p.installed and "can't install" in p.detail
    assert (tmp_path / "prep" / "index.js").is_file()
