"""StateGraph assembly, edges, checkpointer (specs.md §4, §3).

Phase 1 topology:

    recon
      -> hunt            (self-loop for bounded continuation, §8)
      -> validate_mechanical   (no model calls; cheapest filter first)
      -> validate_bug          (VALIDATOR_BUG; "is it real?")
      -> validate_reachability (VALIDATOR_REACH; "can an attacker get here?")
      -> report                (deterministic; no model)

Persistence before parallelism (§1.3): the SQLite checkpointer is wired first
and resume-from-crash is proven before Hunt fan-out is enabled.

Live dependencies (model registry, store, sandbox) are NOT graph state — they
are closed over here and bound to each node with `functools.partial`.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from crucible.graph.deps import NodeDeps
from crucible.graph.hooks import continuation_gate
from crucible.graph.nodes import (
    recon,
    hunt,
    validate_mechanical,
    validate_bug,
    validate_reachability,
    report,
)
from crucible.graph.state import CrucibleState
from crucible.obs import span

_log = logging.getLogger("crucible.graph")


def _traced(name: str, fn, deps: NodeDeps):
    """Wrap a node so every entry/exit is logged and (if on) traced."""

    def _node(state: CrucibleState) -> CrucibleState:
        _log.info("→ %s", name)
        t0 = time.monotonic()
        try:
            with span(f"node.{name}", node=name, run_id=state.get("run_id", "")):
                out = fn(state, deps=deps)
        except Exception as e:  # noqa: BLE001 — log then re-raise for the CLI
            _log.warning("✗ %s failed after %.1fs: %s: %s", name, time.monotonic() - t0,
                         type(e).__name__, e)
            raise
        _log.info("✓ %s  %.1fs", name, time.monotonic() - t0)
        return out

    return _node


def build_graph(deps: NodeDeps, checkpoint_db: str | Path = "checkpoints.sqlite"):
    """Assemble the Phase 1 graph and bind the SQLite checkpointer + deps."""
    g = StateGraph(CrucibleState)

    g.add_node("recon", _traced("recon", recon.run, deps))
    g.add_node("hunt", _traced("hunt", hunt.run, deps))
    g.add_node("validate_mechanical", _traced("validate_mechanical", validate_mechanical.run, deps))
    g.add_node("validate_bug", _traced("validate_bug", validate_bug.run, deps))
    g.add_node("validate_reachability", _traced("validate_reachability", validate_reachability.run, deps))
    g.add_node("report", _traced("report", report.run, deps))

    g.add_edge(START, "recon")
    g.add_edge("recon", "hunt")

    # Bounded continuation (§8): re-enter Hunt in a fresh context window until the
    # completion goal is met or the hard cap (3) is hit. State carries via the
    # workspace, not the window.
    g.add_conditional_edges(
        "hunt",
        continuation_gate,
        {"continue": "hunt", "done": "validate_mechanical"},
    )

    g.add_edge("validate_mechanical", "validate_bug")
    g.add_edge("validate_bug", "validate_reachability")
    g.add_edge("validate_reachability", "report")
    g.add_edge("report", END)

    conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)
