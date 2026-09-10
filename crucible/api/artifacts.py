"""Safe access to a run's workspace files (issue #38). No web dependency.

The workspace is git-initialised (`workspace/fs.py`) and its layout is fixed
(`workspace/layout.py`). `artifact_index` enumerates what a run wrote;
`resolve_artifact` turns a client-supplied relative path into a real file path
that is provably inside the workspace (no `..`, no absolute paths, no symlink
escape).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MiB — served inline; larger → 413

# Directories/files a run legitimately writes (workspace/layout.py + report/recon
# outputs). Used as the walk roots when the workspace is not a git checkout.
_WALK_ROOTS = ("", "recon", "coverage", "dedup", "findings", "offload")

_TEXT_SUFFIXES = {".md", ".txt", ".log", ".jsonl", ".diff", ".patch"}


class ArtifactError(Exception):
    """Base for artifact-access failures."""


class ArtifactNotFound(ArtifactError):
    pass


class ArtifactForbidden(ArtifactError):
    """Path escapes the workspace, or is a symlink pointing outside it."""


class ArtifactTooLarge(ArtifactError):
    pass


@dataclass(frozen=True)
class ArtifactMeta:
    path: str            # workspace-relative, POSIX separators
    kind: str            # architecture | report | recon | coverage | dedup | finding | offload | log | other
    bytes: int
    modified_at: str     # ISO-8601 UTC

    def as_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "bytes": self.bytes,
                "modified_at": self.modified_at}


def _kind_for(rel: str) -> str:
    if rel == "architecture.md":
        return "architecture"
    if rel in ("report.json", "report.md"):
        return "report"
    if rel == "run.log":
        return "log"
    head = rel.split("/", 1)[0]
    if head in ("recon", "coverage", "dedup", "findings", "offload"):
        return "finding" if head == "findings" else head
    return "other"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _git_tracked(workspace: Path) -> list[str] | None:
    """Relative paths git knows about, or None if this isn't a git checkout."""
    if not (workspace / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "-z"],
        text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.split("\0") if p]


def _walked(workspace: Path) -> list[str]:
    out: list[str] = []
    for root in _WALK_ROOTS:
        base = workspace / root if root else workspace
        if not base.is_dir():
            continue
        for p in base.rglob("*") if root else base.glob("*"):
            if p.is_file() and ".git" not in p.parts:
                out.append(p.relative_to(workspace).as_posix())
    return out


def artifact_index(workspace: str | Path) -> list[ArtifactMeta]:
    """Every file a run wrote under `workspace`, type-tagged. Prefers the git
    index; falls back to a bounded directory walk."""
    ws = Path(workspace)
    if not ws.is_dir():
        return []
    rels = _git_tracked(ws)
    if rels is None:
        rels = _walked(ws)
    metas: list[ArtifactMeta] = []
    for rel in sorted(set(rels)):
        fp = ws / rel
        try:
            st = fp.stat()
        except OSError:
            continue
        if not fp.is_file():
            continue
        metas.append(ArtifactMeta(rel, _kind_for(rel), st.st_size, _iso(st.st_mtime)))
    return metas


def resolve_artifact(workspace: str | Path, rel: str) -> Path:
    """Return the real path of `rel` inside `workspace`, or raise.

    Rejects absolute paths, `..` traversal, and any resolved path (including via
    symlink) that lands outside the workspace.
    """
    ws = Path(workspace).resolve()
    rel = (rel or "").strip().lstrip("/")
    if not rel or rel == "." or Path(rel).is_absolute():
        raise ArtifactForbidden(f"bad artifact path: {rel!r}")
    if ".git" in Path(rel).parts:
        raise ArtifactForbidden("the workspace .git directory is not served")
    target = (ws / rel).resolve()
    if target != ws and ws not in target.parents:
        raise ArtifactForbidden(f"path escapes the workspace: {rel!r}")
    if not target.is_file():
        raise ArtifactNotFound(rel)
    if target.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ArtifactTooLarge(rel)
    return target


def content_type_for(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".md":
        return "text/markdown; charset=utf-8"
    if suffix in _TEXT_SUFFIXES:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"
