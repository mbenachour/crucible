"""Build `proposed_patch` from a Hunter's `fix_plan` (issue #111). No model calls.

Hunters hand-writing unified diffs lost most findings to `git apply` errors:
miscounted hunk headers, misquoted context lines. Nobody writes the diff now:

1. `resolve_plan` turns the plan into `Target`s — one per file, with the text a
   patch-rewrite model is shown (the whole file when it is short, otherwise the
   cited lines plus a margin).
2. The model returns that text with the fix made (`crucible.graph.patch_rewrite`);
   `rewrite_problem` rejects replies that would damage the file.
3. `build_patch` splices each rewrite back into its file and has git write the
   diff in a throwaway repo, so the patch always applies to the pristine tree.

Plan paths are model output read from the host, so `resolve_plan` refuses
absolute paths, `..`, `.git`, and symlinks that resolve outside the repo.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from crucible.validation.schema import FixStep

WHOLE_FILE_MAX_LINES = 400  # files up to this long are shown to the rewrite model whole
WINDOW_MARGIN = 40          # otherwise: the cited lines, plus this many on each side
MIN_KEPT_FRACTION = 0.5     # a rewrite keeping fewer of the original lines is rejected
_MAX_FILE_BYTES = 400_000


class PlanError(ValueError):
    """The plan names a file or lines that are not there. The Hunter wrote the
    plan, so this goes back to it as a repair turn."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Target:
    """One file of a plan: the region the rewrite model sees and the steps for it."""

    path: str                  # repo-relative, '/'-separated
    lines: list[str]           # the original file, line endings kept
    start: int                 # window, 0-based [start, end)
    end: int
    new_file: bool = False
    steps: list[FixStep] = field(default_factory=list)

    @property
    def whole(self) -> bool:
        return self.start == 0 and self.end == len(self.lines)

    @property
    def text(self) -> str:
        return "".join(self.lines[self.start:self.end])

    @property
    def original(self) -> str:
        return "".join(self.lines)


def resolve_plan(plan: list[FixStep], repo: str) -> list[Target]:
    """Group the plan's steps by file and pick each file's window. Raises
    `PlanError` listing every bad step."""
    root = Path(repo).resolve()
    problems: list[str] = []
    by_path: dict[str, Target] = {}
    if not plan:
        raise PlanError(["fix_plan is empty: give at least one step"])
    for i, step in enumerate(plan, 1):
        where = f"fix_plan[{i}] {step.file_path!r}"
        rel, err = _safe_relpath(root, step.file_path)
        if err:
            problems.append(f"{where}: {err}")
            continue
        path = root / rel
        if step.new_file:
            if path.exists():
                problems.append(f"{where}: new_file is true but the file exists; cite its lines instead")
                continue
            t = by_path.setdefault(rel, Target(path=rel, lines=[], start=0, end=0, new_file=True))
            if not t.new_file:
                problems.append(f"{where}: the same file is both new and existing in this plan")
                continue
            t.steps.append(step)
            continue
        if not path.is_file():
            problems.append(f"{where}: file does not exist (set new_file to create it)")
            continue
        raw = path.read_bytes()
        if len(raw) > _MAX_FILE_BYTES or b"\0" in raw:
            problems.append(f"{where}: not an editable text file")
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
        if not (1 <= step.line_start <= step.line_end <= len(lines)):
            problems.append(
                f"{where}: lines {step.line_start}-{step.line_end} are out of range "
                f"(the file has {len(lines)} lines)"
            )
            continue
        t = by_path.get(rel)
        if t is None:
            t = by_path[rel] = Target(path=rel, lines=lines, start=len(lines), end=0)
        elif t.new_file:
            problems.append(f"{where}: the same file is both new and existing in this plan")
            continue
        t.steps.append(step)
        t.start = min(t.start, step.line_start - 1)
        t.end = max(t.end, step.line_end)
    if problems:
        raise PlanError(problems)
    targets = list(by_path.values())
    for t in targets:
        if t.new_file:
            continue
        if len(t.lines) <= WHOLE_FILE_MAX_LINES:
            t.start, t.end = 0, len(t.lines)
        else:
            t.start = max(0, t.start - WINDOW_MARGIN)
            t.end = min(len(t.lines), t.end + WINDOW_MARGIN)
    return targets


def _safe_relpath(root: Path, raw: str) -> tuple[str, str]:
    """(normalized repo-relative path, "") or ("", reason it is refused)."""
    s = (raw or "").strip().replace("\\", "/")
    if not s:
        return "", "empty path"
    if s.startswith(("/", "~")) or os.path.isabs(s) or (len(s) > 1 and s[1] == ":"):
        return "", "path must be relative to the repo root"
    parts = [p for p in PurePosixPath(s).parts if p != "."]
    if not parts:
        return "", "path names the repo root, not a file"
    if ".." in parts:
        return "", "path may not contain '..'"
    if ".git" in parts:
        return "", "path may not be inside .git"
    rel = "/".join(parts)
    resolved = (root / rel).resolve()
    if root not in resolved.parents:
        return "", "path resolves outside the repo"
    if resolved != root / rel:
        # git would diff the link, not its target; ask for the real file.
        return "", f"path goes through a symlink; use {resolved.relative_to(root).as_posix()!r}"
    return rel, ""


def normalize_rewrite(target: Target, text: str) -> str:
    """Match the original's line endings and final newline, which models drop."""
    crlf = any(ln.endswith("\r\n") for ln in target.lines[target.start:target.end])
    text = text.replace("\r\n", "\n")
    if target.new_file or target.text.endswith("\n"):
        if text and not text.endswith("\n"):
            text += "\n"
    elif text.endswith("\n"):
        text = text[:-1]  # the window ends at EOF without a newline; keep it so
    return text.replace("\n", "\r\n") if crlf else text


def rewrite_problem(target: Target, text: str) -> str:
    """Why this rewrite (already normalized) must not be used, or "" if it is fine."""
    if target.new_file:
        return "" if text.strip() else "the new file is empty"
    original = target.text
    if text == original:
        return "the text came back unchanged; the fix was not made"
    old = [ln.rstrip("\r\n") for ln in target.lines[target.start:target.end]]
    new = [ln.rstrip("\r\n") for ln in text.splitlines()]
    kept = _kept_lines(old, new)
    if kept < MIN_KEPT_FRACTION * len(old):
        return (
            f"only {kept} of the original {len(old)} lines were kept; return the whole "
            "text with only the fix changed"
        )
    # A window must come back whole: losing its edges would delete code that
    # the splice no longer sees.
    if not target.whole and new and old and (new[0] != old[0] or new[-1] != old[-1]):
        return (
            "the first and last lines of the excerpt must come back unchanged; you "
            "returned less (or more) than you were given"
        )
    return ""


def _kept_lines(old: list[str], new: list[str]) -> int:
    from difflib import SequenceMatcher

    sm = SequenceMatcher(None, old, new, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks())


def splice(target: Target, text: str) -> str:
    """The whole new file: the rewrite in place of the window."""
    return "".join(target.lines[:target.start]) + text + "".join(target.lines[target.end:])


def build_patch(repo: str, files: dict[str, str]) -> str:
    """Unified diff turning the repo's current files into `files` (path -> new
    content; a path missing from the repo is a new file). git writes it, in a
    throwaway repo holding only these files, so it applies to the pristine tree."""
    root = Path(repo)
    git = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
           "-c", "user.name=crucible", "-c", "user.email=crucible@localhost"]
    with tempfile.TemporaryDirectory(prefix="crucible-patch-") as tmp:
        subprocess.run([*git, "init", "-q"], cwd=tmp, check=True, capture_output=True)
        new_paths = []
        for rel in files:
            src, dst = root / rel, Path(tmp) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                dst.write_bytes(src.read_bytes())
                os.chmod(dst, src.stat().st_mode & 0o777)
            else:
                new_paths.append(rel)
        subprocess.run([*git, "add", "-A"], cwd=tmp, check=True, capture_output=True)
        for rel, content in files.items():
            (Path(tmp) / rel).write_bytes(content.encode("utf-8"))
        if new_paths:
            subprocess.run([*git, "add", "-N", "--", *new_paths], cwd=tmp, check=True,
                           capture_output=True)
        proc = subprocess.run(
            [*git, "diff", "--no-color", "--no-ext-diff", "--no-renames", "--src-prefix=a/",
             "--dst-prefix=b/"],
            cwd=tmp, check=True, capture_output=True,
        )
        return proc.stdout.decode("utf-8", errors="replace")
