"""Target-repo introspection: pinned commit + primary language (specs.md §5, §12).

Language detection is a plain extension histogram for now — enough to pick the
noise budget. tree-sitter-based detection can replace it later (codeintel extra).
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

# extension -> language key used by the noise budget and fixtures
_EXT_LANG = {
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".py": "py", ".pyi": "py",
    ".js": "js", ".mjs": "js", ".ts": "ts", ".tsx": "ts", ".jsx": "js",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php",
}
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", "vendor"}


def git_commit(repo_path: str | Path) -> str:
    """HEAD sha, or '' if the target is not a git checkout."""
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def primary_language(repo_path: str | Path) -> str:
    counts: Counter[str] = Counter()
    root = Path(repo_path)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        lang = _EXT_LANG.get(p.suffix.lower())
        if lang:
            counts[lang] += 1
    return counts.most_common(1)[0][0] if counts else "unknown"
