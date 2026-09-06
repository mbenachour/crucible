"""Recon stage (specs.md §9.1).

Fan out N subagents (default 3) over subsystem slices. Deterministic merger
writes architecture.md (build commands, entry points, trust boundaries,
external inputs, likely attack surface) and taxonomy.json.

Recon writes its own threat model rather than receiving one — beyond the
~10 built-in attack classes it may invent repo-specific classes, each with a
methodology, to tightly scope the Hunters.

Then deterministically seed the Hunt queue as (area x attack_class), bounded
by the run task cap.

Recon quality drives everything downstream — treat prompt regressions as
critical.
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState

RECON_SUBAGENTS = 3
BUILTIN_ATTACK_CLASSES = [
    "command_injection",
    "sql_injection",
    "template_injection",
    "unsafe_deserialization",
    "path_traversal",
    "memory_oob_read",
    "memory_oob_write",
    "integer_overflow",
    "protocol_parsing",
    "timing_side_channel",
]


def run(state: CrucibleState) -> CrucibleState:
    # TODO(phase1):
    #   1. slice repo into subsystems; spawn RECON_SUBAGENTS with ModelRole.RECON
    #   2. deterministic merge -> workspace/architecture.md
    #   3. build workspace/taxonomy.json (builtin + repo-specific classes)
    #   4. seed pending_hunts = [(area x attack_class)] up to run task cap
    #   5. git-commit the workspace
    raise NotImplementedError("recon.run — Phase 1 prompt work pending")
