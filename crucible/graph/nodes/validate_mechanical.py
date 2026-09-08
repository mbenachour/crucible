"""Validate Pass A — mechanical, no model calls (specs.md §9.4).

Cheapest filter first. Failure -> status `mechanical_failed`, no model call spent.

Checks:
  * cited path exists at repo_commit; line range in bounds
  * schema conformant, threat_model populated
  * patch applies cleanly to the unmodified tree (dry run, revert)
  * poc_test parses
  * PoC gate: test FAILS on the unmodified repo and PASSES with the patch
    applied. Any source modification outside the patch invalidates the finding.

The PoC gate itself (`mechanical._check_poc_gate`) is still fail-closed pending
the sandbox exec path (issue #9). What this node does now: it loads each finding
from the store, runs the deterministic gates, and **persists** the verdict
(`FindingRow.status` + a `ValidationRow`) so the Phase 2 loop has a real funnel
and Feedback (issue #21) has a validation-failure signal to act on.
"""

from __future__ import annotations

import logging

from crucible.graph.state import CrucibleState
from crucible.validation import mechanical
from crucible.validation.mechanical import MechStatus

log = logging.getLogger("crucible.validate_mechanical")


def run(state: CrucibleState, deps=None) -> CrucibleState:
    store = getattr(deps, "store", None)
    finding_ids = list(state.get("finding_ids") or [])
    passed = failed = 0

    for finding_id in finding_ids:
        result = mechanical.check_finding(
            finding_id,
            repo_path=state["repo_path"],
            repo_commit=state["repo_commit"],
            workspace_path=state["workspace_path"],
            store=store,
        )
        ok = result.status is MechStatus.PASSED
        passed += ok
        failed += not ok
        if store is not None:
            _persist(store, finding_id, result)

    log.info("validate_mechanical  %d finding(s): %d passed, %d failed",
             len(finding_ids), passed, failed)
    return state


def _persist(store, finding_id: str, result) -> None:
    from crucible.store.models import ValidationRow

    ok = result.status is MechStatus.PASSED
    try:
        store.set_finding_status(finding_id, "mechanical_passed" if ok else "mechanical_failed")
        store.record_validation(ValidationRow(
            finding_id=finding_id,
            pass_name="mechanical",
            verdict="upheld" if ok else "mechanical_failed",
            reasoning="; ".join(result.reasons)[:2000],
        ))
    except Exception as e:  # noqa: BLE001 — persistence failure must not crash the loop
        log.warning("validate_mechanical  could not persist %s: %s", finding_id, e)
