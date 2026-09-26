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
from collections.abc import Callable
from dataclasses import dataclass
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


# Stages whose outgoing edge is conditional (a gate function picks the next
# node), so there is no single static edge to splice a new stage into.
_CONDITIONAL_STAGES = ("hunt", "loop_control")


@dataclass(frozen=True)
class NodeSpec:
    """An extra stage to splice into the graph — the open-core extension seam.

    A downstream distribution (see `crucible.ext`) adds stages the OSS pipeline
    doesn't have (cross-repo tracing, a fixer, …) without forking `build_graph`
    and without touching the stages it already has:

        build_graph(deps, extra_nodes=[
            NodeSpec("tracer", tracer.run, after="validate_reachability"),
            NodeSpec("fixer",  fixer.run,  after="report"),
        ])

    `after` names the stage this one runs after; the static edge leaving that
    stage is rewired through the new node (``after -> name -> old_successor``),
    so the rest of the topology is untouched. Several specs may share an
    `after` — they chain in the order given. Extra stages get the same
    `_traced` treatment as built-in ones (logging, spans, `stop_after`).

    `run` has the node signature: ``run(state, deps=...) -> state``.
    """

    name: str
    run: Callable[..., CrucibleState]
    after: str


def _splice(edges: list[tuple[str, str]], specs: tuple[NodeSpec, ...]) -> list[tuple[str, str]]:
    """Rewire `edges` so each spec runs directly after the stage it names.

    Raises `ValueError` rather than silently producing a graph the caller
    didn't ask for — a mis-wired pipeline is the kind of thing that would
    otherwise only show up as a stage mysteriously never running.
    """
    out = list(edges)
    known = {src for src, _ in out} | {dst for _, dst in out}
    # The last node spliced after a given stage, so repeated `after` values
    # chain in declaration order instead of stacking up in reverse.
    tail = {}
    for spec in specs:
        if spec.name in known:
            raise ValueError(f"extra node {spec.name!r} collides with an existing stage")
        if spec.after in _CONDITIONAL_STAGES:
            raise ValueError(
                f"cannot splice {spec.name!r} after {spec.after!r}: that stage's outgoing "
                f"edge is conditional. Splice after one of the stages it branches to instead."
            )
        src = tail.get(spec.after, spec.after)
        for i, (edge_src, edge_dst) in enumerate(out):
            if edge_src == src:
                out[i] = (edge_src, spec.name)
                out.insert(i + 1, (spec.name, edge_dst))
                break
        else:
            raise ValueError(
                f"cannot splice {spec.name!r} after unknown stage {spec.after!r}; "
                f"expected one of {sorted(known - {START, END})}"
            )
        known.add(spec.name)
        tail[spec.after] = spec.name
    return out


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
    extra_nodes: tuple[NodeSpec, ...] | list[NodeSpec] = (),
):
    """Assemble the Phase 1 graph and bind the SQLite checkpointer + deps.

    ``stop_after`` (one of :data:`STAGE_NODES`, or an extra node's name) makes
    the run halt cleanly once that node has completed; ``None`` keeps the full
    end-to-end semantics.

    ``extra_nodes`` splices additional stages in (see :class:`NodeSpec`). The
    ten built-in stages are always present and always run their own
    implementations — an extra node can be added between them but cannot
    replace one. That is deliberate: it is the seam a downstream distribution
    extends through, and keeping it additive is what stops the two from
    drifting into forks of each other.
    """
    g = StateGraph(CrucibleState)

    builtin = {
        "recon": recon.run,
        "hunt": hunt.run,
        "dedup": dedup.run,
        "validate_mechanical": validate_mechanical.run,
        "gapfill": gapfill.run,
        "feedback": feedback.run,
        "loop_control": loop_control.run,
        "validate_bug": validate_bug.run,
        "validate_reachability": validate_reachability.run,
        "report": report.run,
    }
    for name, fn in builtin.items():
        g.add_node(name, _traced(name, fn, deps, stop_after))
    for spec in extra_nodes:
        g.add_node(spec.name, _traced(spec.name, spec.run, deps, stop_after))

    # Static edges, as data so `extra_nodes` can be spliced into them. The two
    # conditional edges below are not splice targets (see `_CONDITIONAL_STAGES`).
    edges = [
        (START, "recon"),
        ("recon", "hunt"),
        # Phase 2 producer-consumer loop (§11, issue #22): hunt output -> dedup
        # -> validate -> gapfill/feedback re-queue -> back to hunt, bounded by
        # hooks.max_cycles().
        ("dedup", "validate_mechanical"),
        ("validate_mechanical", "gapfill"),
        ("gapfill", "feedback"),
        ("feedback", "loop_control"),
        ("validate_bug", "validate_reachability"),
        ("validate_reachability", "report"),
        ("report", END),
    ]
    for src, dst in _splice(edges, tuple(extra_nodes)):
        g.add_edge(src, dst)

    # Bounded continuation (§8): re-enter Hunt in a fresh context window until the
    # completion goal is met or the hard cap (3) is hit. State carries via the
    # workspace, not the window.
    g.add_conditional_edges(
        "hunt",
        continuation_gate,
        {"continue": "hunt", "done": "dedup"},
    )
    g.add_conditional_edges(
        "loop_control",
        loop_gate,
        {"rehunt": "hunt", "proceed": "validate_bug"},
    )

    conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)
