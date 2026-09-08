"""LangGraph hooks: compaction, tool-output offloading, bounded continuation (§7, §8).

These are harness mechanics, deliberately kept out of agent/prompt logic.
"""

from __future__ import annotations

import os

from crucible.graph.state import CrucibleState

CONTEXT_CEILING = 0.25          # §1.4
MAX_CONTINUATIONS = 3           # §8 — hard cap, non-negotiable
OFFLOAD_TOKEN_THRESHOLD = 2000  # §7 — starting value

# §11 — the producer-consumer loop runs stages 4-8 (hunt → dedup → validate →
# gapfill/feedback → re-queue) repeatedly within one run. Each cycle contains a
# full bounded-continuation Hunt loop, so the effective Hunt ceiling is
# MAX_CYCLES * (MAX_CONTINUATIONS + 1) batches. This is a second safety bound on
# top of the §8 cap, not a replacement for it.
MAX_CYCLES = max(1, int(os.environ.get("CRUCIBLE_MAX_CYCLES", "2")))


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


def should_rehunt(state: CrucibleState) -> bool:
    """Outer-loop decision (§11, issue #22): another producer-consumer cycle is
    worth running only if Gapfill/Feedback left work queued and the cycle cap is
    not yet hit. Bounded exactly like the §8 continuation cap."""
    if state.get("cycle_count", 0) >= MAX_CYCLES:
        return False
    return bool(state.get("pending_hunts"))


def loop_gate(state: CrucibleState) -> str:
    """Conditional edge after `loop_control`: back into Hunt, or on to the
    validate/report tail."""
    return "rehunt" if should_rehunt(state) else "proceed"


def graph_recursion_limit() -> int:
    """LangGraph super-step budget for one full run. The loop is already bounded
    by MAX_CYCLES and MAX_CONTINUATIONS; this is only the backstop that keeps a
    genuinely stuck graph from spinning forever."""
    per_cycle = (MAX_CONTINUATIONS + 1) + 7  # hunt batches + phase-2 stages
    return 12 + MAX_CYCLES * per_cycle + 12


def offload_and_compact(node_name: str, state: CrucibleState) -> CrucibleState:
    """Applied around every node. Offload large tool outputs to
    workspace/offload/<call_id>.txt; on approaching the ceiling, summarize
    prior turns into coverage/<area>.md and continue fresh.
    """
    # TODO(phase1): implement via the tool wrapper in agents/tools.py and a
    # token-accounting check; this stub keeps the wiring point explicit.
    return state
