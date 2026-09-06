"""Validate Pass B — "is it real?" (specs.md §9.4).

VALIDATOR_BUG, a structurally different open-weight model from the Hunter
(§6). Re-reads the code with a different prompt and tries to DISPROVE the
finding.

Validators cannot file findings (§1.6): output is `upheld` / `refuted` +
reasoning only. No finding-creation tool, no write access to the findings table.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState


def run(state: CrucibleState) -> CrucibleState:
    # TODO(phase1): for each finding that passed Pass A
    #   - run ModelRole.VALIDATOR_BUG with a disprove-oriented prompt
    #   - classify the response (llm/classify.py) before parsing
    #   - persist verdict {upheld|refuted} + reasoning; no finding writes
    raise NotImplementedError("validate_bug.run — Phase 1 prompt work pending")
