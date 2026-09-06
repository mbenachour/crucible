"""LangGraph hooks: compaction, tool-output offloading, bounded continuation (§7, §8).

These are harness mechanics, deliberately kept out of agent/prompt logic.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState

CONTEXT_CEILING = 0.25          # §1.4
MAX_CONTINUATIONS = 3           # §8 — hard cap, non-negotiable
OFFLOAD_TOKEN_THRESHOLD = 2000  # §7 — starting value


def continuation_gate(state: CrucibleState) -> str:
    """Decide whether Hunt re-runs in a fresh context window.

    Completion goal (§8): every in-scope entry point examined, and each either
    produced a finding with a PoC or an explicit negative recorded in coverage/.
    State carries via the workspace, not the window.
    """
    if state["continuation_count"] >= MAX_CONTINUATIONS:
        return "done"
    if _completion_goal_met(state):
        return "done"
    return "continue"


def _completion_goal_met(state: CrucibleState) -> bool:
    # TODO(phase1): inspect workspace coverage/<area>.md for explicit negatives
    # and cross-check against architecture.md entry points.
    return not state["pending_hunts"]


def offload_and_compact(node_name: str, state: CrucibleState) -> CrucibleState:
    """Applied around every node. Offload large tool outputs to
    workspace/offload/<call_id>.txt; on approaching the ceiling, summarize
    prior turns into coverage/<area>.md and continue fresh.
    """
    # TODO(phase1): implement via the tool wrapper in agents/tools.py and a
    # token-accounting check; this stub keeps the wiring point explicit.
    return state
