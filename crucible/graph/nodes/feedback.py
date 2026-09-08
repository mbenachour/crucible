"""Feedback stage (specs.md §11, issue #21).

Reads this run's own trace — mechanical validation failures, shallow Hunt
passes, and cells missed repeatedly — and rewrites the *queued prompts* for the
cells about to be re-hunted so they carry that context. LangChain lists
"agents that analyze their own traces to fix harness-level failure modes" as an
open research problem; this is the deterministic core of it.

Guard rails (non-negotiable):
  * only `scope_hint` is rewritten — never a cap, a deny-list, a continuation
    count, or any other guard;
  * bounded to `FEEDBACK_MAX_REWRITES` per invocation;
  * each rewrite only *appends* context, so a prompt never loses information.

Runs after Gapfill so it can annotate the freshly re-queued cells.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from crucible.coverage import parse_coverage
from crucible.graph.state import CrucibleState
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.feedback")

FEEDBACK_MAX_REWRITES = int(os.environ.get("CRUCIBLE_FEEDBACK_MAX_REWRITES", "6"))
REPEATED_MISS_THRESHOLD = 2

# Mechanical reasons that are the Hunter's fault (and thus worth feeding back).
# "poc_gate not implemented" / "finding not found" are harness gaps, not Hunter
# errors — never rewrite a prompt over those.
_ACTIONABLE = ("does not exist", "out of bounds", "does not apply", "not fully populated",
               "tautology", "poc_test is empty")


def run(state: CrucibleState, deps=None) -> CrucibleState:
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]
    pending: list[dict] = list(state.get("pending_hunts") or [])
    if not pending:
        log.info("feedback  queue empty — nothing to rewrite")
        return state

    store = getattr(deps, "store", None)
    stats = parse_coverage(ws)
    fail_reason_by_class = _mechanical_failures(store, run_id)

    counts = {"validation_failure": 0, "shallow": 0, "repeated_miss": 0}
    rewrites = 0
    for task in pending:
        if rewrites >= FEEDBACK_MAX_REWRITES:
            break
        cls = task["attack_class"]
        cs = stats.get(cls)
        note = ""
        trigger = ""
        if cls in fail_reason_by_class:
            trigger = "validation_failure"
            note = (
                f"PRIOR ATTEMPT: a `{cls}` finding here failed mechanical validation "
                f"({fail_reason_by_class[cls]}). Cite the exact file:line at the pinned "
                f"commit and give a unified-diff patch that applies cleanly to the "
                f"unmodified tree; the PoC must fail clean and pass patched."
            )
        elif task.get("continuation_count", 0) >= 1 or (cs is not None and cs.shallow):
            trigger = "shallow"
            note = (
                "PRIOR ATTEMPT WAS SHALLOW (no result emitted) — that usually means a "
                "crashed dependency, not clean code. Run one trivial `sandbox_exec` "
                "first to confirm the sandbox works, then go deeper. Do not return empty."
            )
        elif cs is not None and cs.passes >= REPEATED_MISS_THRESHOLD and not cs.productive:
            trigger = "repeated_miss"
            note = (
                f"REPEATED MISS: {cs.passes} prior passes on `{cls}` found nothing. Either "
                f"name the specific guard/validation that makes this safe (a definitive "
                f"negative) or escalate one concrete primitive with a PoC sketch."
            )
        if not note:
            continue
        if "feedback:" in task["scope_hint"]:
            continue
        task["scope_hint"] = f"{task['scope_hint']}  ||  feedback: {note}"
        counts[trigger] += 1
        rewrites += 1
        log.info("feedback  rewrote %s prompt for cell %s::%s (trigger=%s)",
                 task["task_id"], task.get("area", "."), cls, trigger)

    if rewrites:
        state["pending_hunts"] = pending
        commit_node(ws, "feedback", run_id)
    log.info("feedback done  rewrote %d/%d queued prompt(s)  %s",
             rewrites, len(pending), counts)
    return state


def _mechanical_failures(store, run_id: str) -> dict[str, str]:
    """attack_class -> first actionable mechanical-failure reason for this run.

    The class is recovered from `FindingRow.hunter_prompt_version` (`"<class>@<ver>"`,
    set by `hunt._attack_class_body`)."""
    if store is None:
        return {}
    out: dict[str, str] = {}
    try:
        rows = store.run_findings(run_id, ["mechanical_failed"])
    except Exception as e:  # noqa: BLE001 — feedback degrades to the other two triggers
        log.debug("feedback  could not read mechanical failures: %s", e)
        return {}
    for row in rows:
        cls = (row.hunter_prompt_version or "").split("@")[0] or "?"
        if cls in out:
            continue
        reasons = store.finding_reasons(row.finding_id, "mechanical")
        actionable = [r for r in reasons if any(k in r.lower() for k in _ACTIONABLE)]
        if actionable:
            out[cls] = actionable[0][:200]
    return out
