"""Validate Pass A — mechanical, no model calls (specs.md §9.4).

Cheapest filter first. Failure -> status `mechanical_failed`, no model call spent.

Checks:
  * cited path exists at repo_commit; line range in bounds
  * schema conformant, threat_model populated
  * patch applies cleanly to the unmodified tree (dry run, revert)
  * poc_test parses
  * PoC gate: test FAILS on the unmodified repo and PASSES with the patch
    applied. Any source modification outside the patch invalidates the finding.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState
from crucible.validation import mechanical


def run(state: CrucibleState) -> CrucibleState:
    for finding_id in state["finding_ids"]:
        result = mechanical.check_finding(
            finding_id,
            repo_path=state["repo_path"],
            repo_commit=state["repo_commit"],
            workspace_path=state["workspace_path"],
        )
        # TODO(phase1): persist result.status + result.reasons to the store
        _ = result
    return state
