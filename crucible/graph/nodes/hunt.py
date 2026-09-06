"""Hunt stage (specs.md §9.2).

One task = one attack class + one scope hint + architecture.md + prior coverage.
NEVER "find vulnerabilities in this repo." Narrow scoping is what makes the
model behave like a researcher rather than wander.

Tune Hunters to deliberately over-report (§1.5). Success is not Hunt precision;
it is how sharply the funnel refines raw output before a human sees it.

Move past reading into execution: compile fragments, build small versions,
attack them in the sandbox (§10). Cloudflare's biggest quality jump came from
giving Hunters a sandbox to crash binaries in.

Tools (§9.2): bash (general purpose), scoped read/grep, sandbox exec,
fork_sibling, wishlist_write. Every tool's invocation count is instrumented
(§1.12).

Three failure modes designed against (§9.2):
  * edits source so its own exploit works        -> killed by the PoC gate
  * writes a tautological test that proves nothing -> killed by the deny-list
  * exploit runs but threat model is nonsense      -> killed by threat_model req
"""

from __future__ import annotations

from crucible.graph.state import CrucibleState

HUNT_TOOLS = ["bash", "read", "grep", "sandbox_exec", "fork_sibling", "wishlist_write"]


def run(state: CrucibleState) -> CrucibleState:
    # TODO(phase1): for each pending HuntTask
    #   - load attack-class front-matter for all classes, full body for this one
    #     (progressive disclosure, §7)
    #   - run ModelRole.HUNTER agent with HUNT_TOOLS, vLLM guided JSON on the
    #     Hunt output schema (validation/schema.py)
    #   - apply the tautology deny-list at parse time (no model call)
    #   - persist raw findings to the store; write coverage/<area>.md
    #   - shallow-detection: fast finish + zero findings + zero forks -> requeue
    #   - git-commit the workspace
    raise NotImplementedError("hunt.run — Phase 1 prompt work pending")
