"""Report stage (specs.md §9.5).

Deterministic script against a fixed schema. No model required. Output is
queryable data, not free-form prose.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState


def run(state: CrucibleState, deps=None) -> CrucibleState:
    # TODO(phase1): select findings upheld by Pass B AND Pass C from the store;
    # render report.json (+ optional markdown) into the workspace with full
    # provenance (model + prompt version + sampling params per finding).
    raise NotImplementedError("report.run — Phase 1 render pending")
