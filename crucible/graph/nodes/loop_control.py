"""Producer-consumer loop control (specs.md §11, issue #22).

Stages 4-8 run as a continuous loop within one run: Hunt produces findings,
Dedup folds overlaps, Validate filters, Gapfill and Feedback produce new tasks,
and control comes back here to decide whether to feed those tasks back into
Hunt. A vulnerability found late in a cycle is still deduped, validated, and
(when the tail is built) reported in the same run — instead of the machine
stopping the moment the first bounded-continuation loop drains.

This node owns the outer bound. It increments `cycle_count` and, when another
cycle is warranted (`hooks.should_rehunt`), resets `continuation_count` so the
§8 continuation loop can run again for the re-queued cells. `hooks.loop_gate`
reads the same predicate to route the conditional edge.
"""

from __future__ import annotations

import logging
from pathlib import Path

from crucible.graph.hooks import MAX_CYCLES, should_rehunt
from crucible.graph.state import CrucibleState
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.loop")


def run(state: CrucibleState, deps=None) -> CrucibleState:
    state["cycle_count"] = state.get("cycle_count", 0) + 1
    cycle = state["cycle_count"]
    queued = len(state.get("pending_hunts") or [])
    findings = len(state.get("finding_ids") or [])

    if should_rehunt(state):
        state["continuation_count"] = 0
        log.info(
            "loop  cycle %d/%d complete — re-hunting %d re-queued cell(s); "
            "findings so far=%d", cycle, MAX_CYCLES, queued, findings,
        )
    else:
        reason = "cycle cap reached" if cycle >= MAX_CYCLES else "no new tasks queued"
        log.info(
            "loop  producer-consumer loop done after %d cycle(s) (%s) — "
            "%d finding(s) to the validate/report tail", cycle, reason, findings,
        )

    commit_node(Path(state["workspace_path"]), "loop_control", state["run_id"])
    return state
