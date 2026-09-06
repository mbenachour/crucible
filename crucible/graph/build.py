"""StateGraph assembly, edges, checkpointer (specs.md §4, §3).

Phase 1 topology:

    recon
      -> hunt            (fan-out over pending_hunts, bounded by run task cap)
      -> validate_mechanical   (no model calls; cheapest filter first)
      -> validate_bug          (VALIDATOR_BUG; "is it real?")
      -> validate_reachability (VALIDATOR_REACH; "can an attacker get here?")
      -> report                (deterministic; no model)

Persistence before parallelism (§1.3): the SqliteSaver checkpointer is wired
first and resume-from-crash is proven before Hunt fan-out is enabled.
"""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from crucible.graph.hooks import continuation_gate, offload_and_compact
from crucible.graph.nodes import (
    recon,
    hunt,
    validate_mechanical,
    validate_bug,
    validate_reachability,
    report,
)
from crucible.graph.state import CrucibleState


def build_graph(checkpoint_db: str | Path = "checkpoints.sqlite"):
    """Assemble the Phase 1 graph and bind the SQLite checkpointer."""
    g = StateGraph(CrucibleState)

    g.add_node("recon", recon.run)
    g.add_node("hunt", hunt.run)
    g.add_node("validate_mechanical", validate_mechanical.run)
    g.add_node("validate_bug", validate_bug.run)
    g.add_node("validate_reachability", validate_reachability.run)
    g.add_node("report", report.run)

    g.add_edge(START, "recon")
    g.add_edge("recon", "hunt")

    # Bounded continuation (§8): re-inject the Hunt prompt in a fresh window
    # until the completion goal is met or the hard cap (3) is hit.
    g.add_conditional_edges(
        "hunt",
        continuation_gate,
        {"continue": "hunt", "done": "validate_mechanical"},
    )

    g.add_edge("validate_mechanical", "validate_bug")
    g.add_edge("validate_bug", "validate_reachability")
    g.add_edge("validate_reachability", "report")
    g.add_edge("report", END)

    checkpointer = SqliteSaver.from_conn_string(str(checkpoint_db))
    return g.compile(checkpointer=checkpointer, interrupt_before=[])


# Compaction / tool-output offloading run as a hook on every node, not as
# agent logic (§7). Wired here so build.py stays the single place topology is
# described.
NODE_HOOKS = [offload_and_compact]
