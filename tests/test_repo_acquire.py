"""Repo-acquisition validation + clone execution (issue #62). No real network."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from crucible.repo_acquire import (
    CloneFailed,
    CloneTimedOut,
    CloneTooLarge,
    RepoRefError,
    clone_repo,
    resolve_repo_ref,
    validate_ref,
)

# --- resolve_repo_ref ------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("octocat/Hello-World", "https://github.com/octocat/Hello-World.git"),
    ("https://github.com/octocat/Hello-World.git", "https://github.com/octocat/Hello-World.git"),
    ("https://gitlab.com/group/proj", "https://gitlab.com/group/proj"),
])
def test_valid_specs_resolve(spec, expected):
    assert resolve_repo_ref(spec) == expected


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "-oProxyCommand=touch x",
    "--upload-pack=touch x",
    "http://github.com/octocat/Hello-World.git",       # not https
    "git://github.com/octocat/Hello-World.git",
    "ssh://git@github.com/octocat/Hello-World.git",
    "git@github.com:octocat/Hello-World.git",
    "file:///etc/passwd",
    "ext::sh -c touch x",
    "https://internal.example/repo.git",                # not on the allowlist
    "https://169.254.169.254/repo.git",                  # link-local (cloud metadata)
    "https://127.0.0.1/repo.git",                        # loopback
    "https://10.0.0.5/repo.git",                         # private range
    "https://localhost/repo.git",
    "https://-evil.com/repo.git",
])
def test_rejected_specs(bad):
    with pytest.raises(RepoRefError):
        resolve_repo_ref(bad)


def test_allowed_hosts_is_configurable():
    resolve_repo_ref("https://git.internal.example/team/proj.git", allowed_hosts=["git.internal.example"])
    with pytest.raises(RepoRefError):
        resolve_repo_ref("https://github.com/octocat/Hello-World.git", allowed_hosts=["git.internal.example"])


# --- validate_ref ------------------------------------------------------

def test_ref_validation():
    assert validate_ref(None) is None
    assert validate_ref("") is None
    assert validate_ref("main") == "main"
    assert validate_ref("release/1.2.3") == "release/1.2.3"
    with pytest.raises(RepoRefError):
        validate_ref("--upload-pack=x")
    with pytest.raises(RepoRefError):
        validate_ref("bad ref with spaces")


# --- clone_repo (subprocess mocked) -----------------------------------------

class _Proc:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _fake_clone_ok(*args, **kwargs):
    """Simulate `git clone` actually creating the destination directory."""
    argv = args[0]
    dest = Path(argv[-1])
    dest.mkdir(parents=True)
    return _Proc()


def test_clone_repo_invokes_git_with_expected_argv(tmp_path):
    dest = tmp_path / "clone"
    with patch("crucible.repo_acquire.subprocess.run", side_effect=_fake_clone_ok) as run, \
         patch("crucible.repo_acquire.git_commit", return_value="a" * 40):
        result = clone_repo("octocat/Hello-World", dest, ref="main")

    argv = run.call_args.args[0]
    assert argv == [
        "git", "clone", "--depth", "1", "--no-recurse-submodules", "--single-branch",
        "--branch", "main", "--", "https://github.com/octocat/Hello-World.git", str(dest),
    ]
    assert "--" in argv  # the argument-injection guard
    assert result.commit == "a" * 40
    assert result.url == "https://github.com/octocat/Hello-World.git"


def test_clone_repo_rejects_bad_spec_before_any_subprocess(tmp_path):
    with patch("crucible.repo_acquire.subprocess.run") as run, pytest.raises(RepoRefError):
        clone_repo("--upload-pack=x", tmp_path / "d")
    run.assert_not_called()


def test_clone_repo_cleans_up_on_failure(tmp_path):
    dest = tmp_path / "clone"
    with patch("crucible.repo_acquire.subprocess.run", return_value=_Proc(returncode=1, stderr="fatal: not found")):
        with pytest.raises(CloneFailed, match="not found"):
            clone_repo("octocat/Hello-World", dest)
    assert not dest.exists()


def test_clone_repo_timeout_cleans_up(tmp_path):
    import subprocess as sp

    dest = tmp_path / "clone"

    def _side_effect(*args, **kwargs):
        Path(args[0][-1]).mkdir(parents=True)  # git had already started writing
        raise sp.TimeoutExpired(cmd="git", timeout=1)

    with patch("crucible.repo_acquire.subprocess.run", side_effect=_side_effect):
        with pytest.raises(CloneTimedOut):
            clone_repo("octocat/Hello-World", dest, timeout_s=1)
    assert not dest.exists()


def test_clone_repo_enforces_size_cap(tmp_path):
    dest = tmp_path / "clone"

    def _fake_clone_big(*args, **kwargs):
        d = Path(args[0][-1])
        d.mkdir(parents=True)
        (d / "big.bin").write_bytes(b"0" * 2048)
        return _Proc()

    with patch("crucible.repo_acquire.subprocess.run", side_effect=_fake_clone_big), \
         patch("crucible.repo_acquire.git_commit", return_value="a" * 40):
        with pytest.raises(CloneTooLarge):
            clone_repo("octocat/Hello-World", dest, max_bytes=1024)
    assert not dest.exists()


def test_clone_repo_refuses_existing_dest(tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()
    with pytest.raises(Exception, match="already exists"):
        clone_repo("octocat/Hello-World", dest)


@pytest.mark.network
def test_live_clone_of_a_small_public_repo(tmp_path):
    result = clone_repo("octocat/Hello-World", tmp_path / "clone")
    assert len(result.commit) == 40
