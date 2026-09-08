"""StateGraph assembly, edges, checkpointer (specs.md §4, §3).

Topology (Phase 1 tail + the Phase 2 producer-consumer loop, §11):

    recon
      -> hunt                  (self-loop for bounded continuation, §8)
      -> dedup                 (inverted-index shortlist + agent judge, issue #20)
      -> validate_mechanical   (no model calls; cheapest filter first)
      -> gapfill               (re-queue under-tested cells, issue #19)
      -> feedback              (rewrite queued prompts from failures, issue #21)
      -> loop_control ─┬─(rehunt)─→ hunt      (issue #22: stages 4-8 loop)
                       └─(proceed)→ validate_bug
      -> validate_bug          (VALIDATOR_BUG; "is it real?")       [Phase 1 stub]
      -> validate_reachability (VALIDATOR_REACH; "can an attacker get here?") [stub]
      -> report                (deterministic; no model)           [Phase 1 stub]

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
from crucible.graph.hooks import continuation_gate, loop_gate
from crucible.graph.nodes import (
    recon,
    hunt,
    dedup,
    validate_mechanical,
    gapfill,
    feedback,
    loop_control,
    validate_bug,
    validate_reachability,
    report,
)
from crucible.graph.state import CrucibleState
from crucible.obs import span

_log = logging.getLogger("crucible.graph")

# Canonical stage-node names, in topological order. Used by the CLI to validate
# `--stop-after <stage>` and to render its help text.
STAGE_NODES = (
    "recon",
    "hunt",
    "dedup",
    "validate_mechanical",
    "gapfill",
    "feedback",
    "loop_control",
    "validate_bug",
    "validate_reachability",
    "report",
)


class StopAfterStage(Exception):
    """A traced node raises this right after it completes successfully when it is
    the ``--stop-after`` target. The CLI catches it and stops the run on the same
    clean exit path as a stub node (checkpoint written, resume hint, exit 3)."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


def _traced(name: str, fn, deps: NodeDeps, stop_after: str | None = None):
    """Wrap a node so every entry/exit is logged and (if on) traced.

    When ``stop_after == name`` the wrapper raises :class:`StopAfterStage` after
    the node body has run and logged its success — the run then unwinds through
    the CLI's clean-stop handler and no further stages execute.
    """

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
        if stop_after and name == stop_after:
            _log.info("stop-after: %s complete — halting run (--stop-after)", name)
            raise StopAfterStage(name)
        return out

    return _node


def build_graph(
    deps: NodeDeps,
    checkpoint_db: str | Path = "checkpoints.sqlite",
    stop_after: str | None = None,
):
    """Assemble the Phase 1 graph and bind the SQLite checkpointer + deps.

    ``stop_after`` (one of :data:`STAGE_NODES`) makes the run halt cleanly once
    that node has completed; ``None`` keeps the full end-to-end semantics.
    """
    g = StateGraph(CrucibleState)

    g.add_node("recon", _traced("recon", recon.run, deps, stop_after))
    g.add_node("hunt", _traced("hunt", hunt.run, deps, stop_after))
    g.add_node("dedup", _traced("dedup", dedup.run, deps, stop_after))
    g.add_node("validate_mechanical", _traced("validate_mechanical", validate_mechanical.run, deps, stop_after))
    g.add_node("gapfill", _traced("gapfill", gapfill.run, deps, stop_after))
    g.add_node("feedback", _traced("feedback", feedback.run, deps, stop_after))
    g.add_node("loop_control", _traced("loop_control", loop_control.run, deps, stop_after))
    g.add_node("validate_bug", _traced("validate_bug", validate_bug.run, deps, stop_after))
    g.add_node("validate_reachability", _traced("validate_reachability", validate_reachability.run, deps, stop_after))
    g.add_node("report", _traced("report", report.run, deps, stop_after))

    g.add_edge(START, "recon")
    g.add_edge("recon", "hunt")

    # Bounded continuation (§8): re-enter Hunt in a fresh context window until the
    # completion goal is met or the hard cap (3) is hit. State carries via the
    # workspace, not the window.
    g.add_conditional_edges(
        "hunt",
        continuation_gate,
        {"continue": "hunt", "done": "dedup"},
    )

    # Phase 2 producer-consumer loop (§11, issue #22): hunt output -> dedup ->
    # validate -> gapfill/feedback re-queue -> back to hunt, bounded by MAX_CYCLES.
    g.add_edge("dedup", "validate_mechanical")
    g.add_edge("validate_mechanical", "gapfill")
    g.add_edge("gapfill", "feedback")
    g.add_edge("feedback", "loop_control")
    g.add_conditional_edges(
        "loop_control",
        loop_gate,
        {"rehunt": "hunt", "proceed": "validate_bug"},
    )

    g.add_edge("validate_bug", "validate_reachability")
    g.add_edge("validate_reachability", "report")
    g.add_edge("report", END)

    conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)
