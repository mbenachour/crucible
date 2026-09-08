"""Gapfill stage (specs.md §11, issue #19).

Re-queues under-tested `(area × attack_class)` cells so coverage is driven
toward the full matrix Recon laid out — across cycles within a run and across
runs. The primary cost-to-coverage lever: each extra pass costs roughly half
the initial hunt, and it counteracts the model's drift toward attack classes
where it has already had success.

Two kinds of gap, deterministic (§1.8), no model call:

  * **missing** — a manifest cell that has never been hunted at all.
  * **weak**    — a cell that was hunted but produced no finding, fewer than
    `GAPFILL_CELL_RETRY` times (a shallow pass or a genuine negative that is
    worth one more, deeper look).

Bounded (`GAPFILL_MAX_REQUEUE` per invocation) and resumable: it only appends to
`state["pending_hunts"]`, and `missing` shrinks monotonically as cells get
covered, so the loop converges.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from crucible.coverage import (
    CellStats,
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

    seen: set[str] = set()
    missing: list[dict] = []
    weak: list[dict] = []
    for chunk in chunks:
        key = cell_key(chunk.get("area", "."), chunk["attack_class"])
        if key in seen:
            continue
        seen.add(key)
        cs: CellStats | None = stats.get(chunk["attack_class"])
        if key not in covered and (cs is None or cs.passes == 0):
            missing.append(chunk)
        elif cs is not None and not cs.productive and cs.passes < GAPFILL_CELL_RETRY:
            weak.append(chunk)

    cycle = state.get("cycle_count", 0)
    requeued: list[dict] = []
    for i, chunk in enumerate(missing[:GAPFILL_MAX_REQUEUE]):
        requeued.append(manifest_task(chunk, f"gf{cycle}-{i:03d}", scope_prefix="[gapfill]"))
    for j, chunk in enumerate(weak[: GAPFILL_MAX_REQUEUE - len(requeued)]):
        requeued.append(manifest_task(chunk, f"gf{cycle}-w{j:03d}", scope_prefix="[gapfill re-sweep]"))

    if requeued:
        state["pending_hunts"] = list(state.get("pending_hunts") or []) + requeued
        commit_node(ws, "gapfill", run_id)

    log.info(
        "gapfill done  matrix=%d covered=%d missing=%d weak=%d requeued=%d  queue=%d",
        len(seen), sum(1 for k in seen if k in covered),
        len(missing), len(weak), len(requeued), len(state.get("pending_hunts") or []),
    )
    return state
