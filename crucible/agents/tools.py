"""Agent tools: bash, scoped read/grep, sandbox exec, fork_sibling, wishlist (§9.2).

bash is general purpose — the model designs its own approach; a fixed toolset
is the wrong shape here (§9.2). Every tool is wrapped so that:
  * invocations are counted (instrumentation.py, §1.12)
  * output above OFFLOAD_TOKEN_THRESHOLD goes to offload/<call_id>.txt and the
    model gets head + tail + path (§7)

The wrapper is applied to EVERY tool, not per-tool.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from crucible.agents.instrumentation import ToolUsage
from crucible.graph.hooks import OFFLOAD_TOKEN_THRESHOLD
from crucible.workspace import layout

_SKIP_DIRS = {
    ".git", "node_modules", "vendor", "third_party", ".venv", "venv", "dist",
    "build", "__pycache__", ".gradle", "Pods", ".next",
}
_MAX_FILE_BYTES = 400_000


@dataclass
class ToolContext:
    run_id: str
    task_id: str
    repo_path: str          # read-only mount
    workspace_path: str
    usage: ToolUsage


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def offload_if_large(ctx: ToolContext, output: str) -> str:
    """Return output unchanged, or head+tail+pointer if it exceeds the ceiling."""
    if _approx_tokens(output) <= OFFLOAD_TOKEN_THRESHOLD:
        return output
    call_id = uuid.uuid4().hex[:12]
    path = layout.offload_path(ctx.workspace_path, call_id)
    path.write_text(output)

    # head + tail, bounded by BOTH line count and characters — tool output is
    # sometimes one enormous line (minified JSON, a hex dump).
    char_budget = OFFLOAD_TOKEN_THRESHOLD * 2  # ~half the ceiling, in chars
    lines = output.splitlines()
    head = "\n".join(lines[:40])[:char_budget]
    tail = "\n".join(lines[-40:])[-char_budget:]
    return (
        f"{head}\n\n[... {len(output) - len(head) - len(tail)} chars offloaded ...]\n\n"
        f"{tail}\n\n[full output: {path}]"
    )


# --- Tool stubs ------------------------------------------------------------
# Each returns a string the agent sees; each is wrapped by ctx.usage.record().

def bash(ctx: ToolContext, cmd: str, timeout_s: int = 60) -> str:
    with ctx.usage.record("bash"):
        raise NotImplementedError  # runs in sandbox; see crucible.sandbox


def read_file(ctx: ToolContext, rel_path: str, start: int = 1, end: int | None = None) -> str:
    with ctx.usage.record("read"):
        p = Path(ctx.repo_path) / rel_path
        text = p.read_text(errors="replace")
        body = "\n".join(text.splitlines()[start - 1: end])
        return offload_if_large(ctx, body)


def grep(ctx: ToolContext, pattern: str, path_glob: str = "**/*") -> str:
    with ctx.usage.record("grep"):
        raise NotImplementedError


def sandbox_exec(ctx: ToolContext, cmd: str, timeout_s: int = 120) -> str:
    with ctx.usage.record("sandbox_exec"):
        raise NotImplementedError  # crucible.sandbox.SandboxProvider


def fork_sibling(ctx: ToolContext, structural_seed: str, reason: str) -> str:
    """A Hunter tripping over an interesting path OUTSIDE scope forks a sibling
    with a precise structural seed rather than wandering off (§9.2). Increments
    fork_count in graph state; fork rate is tracked per model as a selection
    metric.
    """
    with ctx.usage.record("fork_sibling"):
        raise NotImplementedError


def wishlist_write(ctx: ToolContext, need: str, context: str, blocked_task_id: str) -> str:
    """Structured request for a missing dependency — build env, VM, prod config,
    a credential to confirm a PoC. Enough context for the system to re-run that
    exact task once a human provides the dependency (§9.3). Primary interface,
    not a log.
    """
    with ctx.usage.record("wishlist_write"):
        raise NotImplementedError


# --- read-only filesystem tools (Recon R1/R2, and validator re-reads) ---------
# LangChain @tool callables rooted at a repo checkout. No writes, no exec, no
# path escape. Used by `build_agent(..., tools=read_only_fs_tools(repo), ...)`.

def read_only_fs_tools(repo_path: str) -> list:
    from langchain_core.tools import tool

    root = Path(repo_path).resolve()

    def _resolve(rel: str) -> Path:
        p = (root / rel).resolve()
        if root not in p.parents and p != root:
            raise ValueError(f"path escapes the repo: {rel}")
        return p

    @tool
    def list_dir(path: str = ".") -> str:
        """List entries under a directory path relative to the repo root."""
        p = _resolve(path)
        if not p.is_dir():
            return f"not a directory: {path}"
        out = []
        for c in sorted(p.iterdir()):
            if c.name in _SKIP_DIRS:
                continue
            out.append(f"{c.name}/" if c.is_dir() else c.name)
        return "\n".join(out[:400]) or "(empty)"

    @tool
    def read_file(path: str, start: int = 1, end: int = 400) -> str:
        """Read lines [start, end] (1-indexed) of a text file relative to the repo root."""
        p = _resolve(path)
        if not p.is_file():
            return f"not a file: {path}"
        if p.stat().st_size > _MAX_FILE_BYTES:
            return f"file too large ({p.stat().st_size} bytes)"
        lines = p.read_text(errors="replace").splitlines()
        chunk = lines[max(0, start - 1): max(start, end)]
        body = "\n".join(f"{i}: {ln}" for i, ln in enumerate(chunk, start=max(1, start)))
        return body[:12_000] or "(no lines in range)"

    @tool
    def search(pattern: str, glob: str = "**/*") -> str:
        """Regex-search files (glob relative to repo root). Returns 'path:line: text', capped at 200."""
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"bad regex: {e}"
        hits: list[str] = []
        for f in sorted(root.glob(glob)):
            if not f.is_file() or f.stat().st_size > _MAX_FILE_BYTES:
                continue
            if any(part in _SKIP_DIRS for part in f.relative_to(root).parts[:-1]):
                continue
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if rx.search(ln):
                    hits.append(f"{f.relative_to(root)}:{i}: {ln.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n[capped at 200]"
        return "\n".join(hits) or "(no matches)"

    return [list_dir, read_file, search]
