"""Graph state — pointers and counters only. Content lives on disk (specs.md §5).

Anti-pattern: putting architecture.md contents, file bodies, or finding
descriptions in graph state. LangGraph checkpoints every superstep; fat state
makes checkpointing expensive and pushes content back into context.
"""

from __future__ import annotations

from typing import TypedDict


class HuntTask(TypedDict):
    """One queued Hunt cell (issue #5). attack_class + scope_hint only — no
    source text. `chunk_type` and `seed_path` come from Recon R3."""

    task_id: str
    area: str
    attack_class: str
    scope_hint: str
    chunk_type: str            # taint | risk | specialist | catch_all | threat_fallback
    seed_path: str | None      # "file:line" or "file:line -> file:line"
    continuation_count: int


class CrucibleState(TypedDict):
    run_id: str
    repo_path: str            # read-only checkout
    workspace_path: str       # agent-writable, git-initialized
    repo_commit: str
    primary_language: str     # drives noise budget (§12)

    architecture_path: str
    taxonomy_path: str

    pending_hunts: list[HuntTask]     # attack_class + scope_hint only
    completed_cells: list[str]        # "area::attack_class"

    finding_ids: list[str]
    fork_count: int
    continuation_count: int
    token_spend: int
