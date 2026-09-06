"""Agent tools: bash, scoped read/grep, sandbox exec, fork_sibling, wishlist (§9.2).

bash is general purpose — the model designs its own approach; a fixed toolset
is the wrong shape here (§9.2). Every tool is wrapped so that:
  * invocations are counted (instrumentation.py, §1.12)
  * output above OFFLOAD_TOKEN_THRESHOLD goes to offload/<call_id>.txt and the
    model gets head + tail + path (§7)

The wrapper is applied to EVERY tool, not per-tool.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from crucible.agents.instrumentation import ToolUsage
from crucible.graph.hooks import OFFLOAD_TOKEN_THRESHOLD
from crucible.workspace import layout


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
