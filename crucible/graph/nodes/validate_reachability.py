"""Validate Pass C — "is it reachable?" (specs.md §9.4).

VALIDATOR_REACH. Separate call, separate agent from Pass B: can
attacker-controlled input actually reach this code from outside the system?

Splitting this from Pass B is deliberate (§1.7) — each question is narrower
than the combined version and the model is better at each one asked alone.
This is the stage that matters most: it converts "there is a flaw" into
"there is a reachable vulnerability."

Phase 1: single-repo. Phase 3: becomes the cross-repo Tracer with a unified
symbol index and dependency graph.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState


def run(state: CrucibleState, deps=None) -> CrucibleState:
    # TODO(phase1): for each finding upheld by Pass B
    #   - run ModelRole.VALIDATOR_REACH scoped to this repo only
    #   - persist verdict {upheld|refuted} + reasoning
    raise NotImplementedError("validate_reachability.run — Phase 1 prompt work pending")
