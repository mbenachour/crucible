"""Gapfill stage (specs.md §11, issue #19).

Re-queues under-tested `(area × attack_class)` cells so coverage is driven
toward the full matrix Recon laid out — across cycles within a run and across
runs. The primary cost-to-coverage lever: each extra pass costs roughly half
the initial hunt, and it counteracts the model's drift toward attack classes
where it has already had success.

Three kinds of gap, deterministic (§1.8), no model call, re-queued in this order
so a broken result is not starved by breadth:

  1. **failed** — hunted `< GAPFILL_CELL_RETRY` times and its only finding failed
     Validate A on an *actionable* mechanical reason (bad line range, patch does
     not apply, …). We know something is wrong here; Feedback rewrites its prompt.
  2. **missing** — a manifest cell that has never been hunted at all.
  3. **barren** — hunted `< GAPFILL_CELL_RETRY` times with no finding at all — a
     shallow pass or a genuine negative worth one more, deeper look.

Bounded (`GAPFILL_MAX_REQUEUE` per invocation) and resumable: it only appends to
`state["pending_hunts"]`, and every bucket shrinks monotonically as cells get
covered / retried, so the loop converges.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from crucible.coverage import (
    CellStats,
    actionable_mechanical_failures,
    cell_key,
    load_manifest_cells,
    manifest_task,
    parse_coverage,
)
from crucible.graph.state import CrucibleState
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.gapfill")

GAPFILL_MAX_REQUEUE = int(os.environ.get("CRUCIBLE_GAPFILL_MAX_REQUEUE", "8"))
GAPFILL_CELL_RETRY = int(os.environ.get("CRUCIBLE_GAPFILL_CELL_RETRY", "2"))


def run(state: CrucibleState, deps=None) -> CrucibleState:
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]

    chunks = load_manifest_cells(ws)
    if not chunks:
        log.info("gapfill  no task manifest — nothing to gapfill")
        return state

    covered = set(state.get("completed_cells") or [])
    stats = parse_coverage(ws)
    invalid_classes = actionable_mechanical_failures(getattr(deps, "store", None), run_id)

    seen: set[str] = set()
    failed: list[dict] = []
    missing: list[dict] = []
    barren: list[dict] = []
    for chunk in chunks:
        key = cell_key(chunk.get("area", "."), chunk["attack_class"])
        if key in seen:
            continue
        seen.add(key)
        cs: CellStats | None = stats.get(chunk["attack_class"])
        if key not in covered and (cs is None or cs.passes == 0):
            missing.append(chunk)
        elif cs is not None and cs.passes < GAPFILL_CELL_RETRY:
            if chunk["attack_class"] in invalid_classes:
                failed.append(chunk)
            elif not cs.productive:
                barren.append(chunk)

    cycle = state.get("cycle_count", 0)
    picks = (
        [("f", c) for c in failed]
        + [("", c) for c in missing]
        + [("b", c) for c in barren]
    )[:GAPFILL_MAX_REQUEUE]
    requeued: list[dict] = []
    for i, (tag, chunk) in enumerate(picks):
        prefix = "[gapfill re-sweep]" if tag else "[gapfill]"
        requeued.append(manifest_task(chunk, f"gf{cycle}-{tag}{i:03d}", scope_prefix=prefix))

    if requeued:
        state["pending_hunts"] = list(state.get("pending_hunts") or []) + requeued
        commit_node(ws, "gapfill", run_id)

    log.info(
        "gapfill done  matrix=%d covered=%d failed=%d missing=%d barren=%d requeued=%d  queue=%d",
        len(seen), sum(1 for k in seen if k in covered),
        len(failed), len(missing), len(barren), len(requeued),
        len(state.get("pending_hunts") or []),
    )
    return state
