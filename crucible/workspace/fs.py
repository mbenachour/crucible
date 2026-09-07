"""Workspace + git operations (specs.md §7).

Git-commit after each node: agents get a diff of what changed since they last
looked, and you get a free audit trail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from crucible.workspace import layout


def init_workspace(workspace_path: str | Path) -> Path:
    ws = Path(workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    for sub in layout.SUBDIRS:
        (ws / sub).mkdir(exist_ok=True)
    if not (ws / ".git").exists():
        _git(ws, "init", "-q")
        # Pin a local identity so `commit_node` actually records commits even
        # when the host has no global git user (CI, fresh containers). The
        # workspace history is the per-node audit trail (§7).
        _git(ws, "config", "user.email", "crucible@localhost")
        _git(ws, "config", "user.name", "crucible")
        _git(ws, "commit", "--allow-empty", "-q", "-m", "workspace: init")
    return ws


def commit_node(workspace_path: str | Path, node_name: str, run_id: str) -> str:
    ws = Path(workspace_path)
    _git(ws, "add", "-A")
    proc = _git(ws, "commit", "-q", "--allow-empty", "-m", f"{node_name} ({run_id})")
    _ = proc
    return _git(ws, "rev-parse", "HEAD").stdout.strip()


def diff_since(workspace_path: str | Path, ref: str) -> str:
    return _git(Path(workspace_path), "diff", f"{ref}..HEAD").stdout


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True, capture_output=True, check=False,
    )
