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

import sqlite3
from functools import partial
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


def build_graph(deps: NodeDeps, checkpoint_db: str | Path = "checkpoints.sqlite"):
    """Assemble the Phase 1 graph and bind the SQLite checkpointer + deps."""
    g = StateGraph(CrucibleState)

    g.add_node("recon", partial(recon.run, deps=deps))
    g.add_node("hunt", partial(hunt.run, deps=deps))
    g.add_node("validate_mechanical", partial(validate_mechanical.run, deps=deps))
    g.add_node("validate_bug", partial(validate_bug.run, deps=deps))
    g.add_node("validate_reachability", partial(validate_reachability.run, deps=deps))
    g.add_node("report", partial(report.run, deps=deps))

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
