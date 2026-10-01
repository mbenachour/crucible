"""Finding schema, field order, tautology deny-list (specs.md §9.2, §4).

Field order is load-bearing: threat_model FIRST, so the model commits to an
attacker and a boundary before describing a bug. Pydantic preserves declared
field order for vLLM guided-JSON generation.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatModel(BaseModel):
    attacker: str = Field(..., description="e.g. 'unauthenticated remote client'")
    boundary_crossed: str = Field(..., description="e.g. 'network input -> parser memory'")
    assumption_broken: str = Field(..., description="e.g. 'length field validated before use'")


class Finding(BaseModel):
    # Order matches specs.md §9.2 exactly. Do not reorder.
    threat_model: ThreatModel
    title: str
    file_path: str
    line_start: int
    line_end: int
    description: str
    poc_test: str = Field(..., description="test source")
    # How the PoC gate runs `poc_test` (issue #9). Defaults keep findings
    # stored before the gate existed loadable; the gate rejects an empty
    # command.
    poc_filename: str = Field(
        default="",
        description="repo-relative path to write poc_test to; must be a NEW file, "
                    "e.g. 'crucible_poc_test.py' or 'src/crucible-poc.spec.ts'",
    )
    poc_command: str = Field(
        default="",
        description="shell command run from the repo root; must exit non-zero on the "
                    "unmodified repo and 0 once proposed_patch is applied, e.g. "
                    "'python3 crucible_poc_test.py' or 'npx vitest run src/crucible-poc.spec.ts'",
    )
    proposed_patch: str = Field(..., description="unified diff")
    severity: Severity


# --- What the Hunter emits (issue #111) -------------------------------------
#
# Hunters hand-writing unified diffs got most findings killed at Pass A on
# corrupt hunks and misquoted context. The Hunter now says *where* and *what*
# to change; a patch-rewrite model edits the file text and git writes the diff
# (`crucible.graph.patch_rewrite`, `crucible.validation.patching`).


class FixStep(BaseModel):
    file_path: str = Field(..., description="repo-relative path of the file to change")
    line_start: int = Field(
        default=0,
        description="first line to change, numbered as read_file shows it; 0 for a new file",
    )
    line_end: int = Field(
        default=0, description="last line to change (inclusive); 0 for a new file"
    )
    change: str = Field(
        ..., description="exactly what to change there: the new code, or a precise instruction"
    )
    new_file: bool = Field(default=False, description="true to create file_path as a new file")


class EmittedFinding(BaseModel):
    """`Finding` with `fix_plan` in place of `proposed_patch`. Same order, same
    meaning; `to_finding` swaps the built patch back in."""

    threat_model: ThreatModel
    title: str
    file_path: str
    line_start: int
    line_end: int
    description: str
    poc_test: str = Field(..., description="test source")
    poc_filename: str = Field(default="", description=Finding.model_fields["poc_filename"].description)
    poc_command: str = Field(
        default="",
        description="shell command run from the repo root; must exit non-zero on the "
                    "unmodified repo and 0 once the fix_plan is applied, e.g. "
                    "'python3 crucible_poc_test.py' or 'npx vitest run src/crucible-poc.spec.ts'",
    )
    fix_plan: list[FixStep] = Field(
        ..., description="the minimal fix, as one step per changed region; no diff"
    )
    severity: Severity

    def to_finding(self, proposed_patch: str) -> Finding:
        data = self.model_dump(exclude={"fix_plan"})
        return Finding(**data, proposed_patch=proposed_patch)


# --- Tautology deny-list (parse-time, no model call) -------------------------
#
# Reject where threat_model.attacker implies privilege equivalent to the
# claimed impact — the "user with DB write access can write to the DB" class.

_PRIVILEGE_IMPACT_PAIRS = [
    (r"\b(db|database) write access\b", r"\bwrit(e|ing) to the (db|database)\b"),
    (r"\broot\b|\badministrator\b|\bsuperuser\b", r"\b(execute|run) (arbitrary )?commands?\b"),
    (r"\bfilesystem write\b", r"\bwrit(e|ing) (arbitrary )?files?\b"),
    (r"\bauthenticated admin\b", r"\badmin (panel|action|endpoint)\b"),
]

# An attacker who can already write to the repo controls the product's code, so
# any impact they claim crosses no boundary (issue #105). Unlike the pairs above
# this needs no impact match.
_INSIDER_ATTACKER = [
    r"\b(push|commit|merge|write)\s+(access|rights|permissions?|privileges?)\b",
    r"\b(repo|repository)\s+(maintainer|owner|admin|administrator|collaborator)s?\b",
    r"\bcollaborators?\b",
    r"\bmaintainers?\b",
    r"\bcan\s+(modify|edit|change|alter|write\s+to|commit\s+to)\s+(the\s+|a\s+)?"
    r"[\w./-]*(workflow|package\.json|config|source|repo|repository|codebase)",
]
# A compromised third party (upstream action, dependency, registry) is a real
# boundary even when its description mentions a maintainer or write access.
_UPSTREAM_COMPROMISE = re.compile(
    r"compromis|upstream|third[- ]party|dependenc|supply[- ]chain|registry|typosquat"
)

# Tests that prove nothing on their own ("exec() executes things, therefore
# critical"). Used as a soft signal alongside the PoC gate.
_TAUTOLOGICAL_TEST_MARKERS = [
    r"assert True\b",
    r"# ?this always passes",
]


def tautology_reasons(finding: Finding) -> list[str]:
    """Return a list of deny-list hits. Empty list == accepted."""
    reasons: list[str] = []
    attacker = finding.threat_model.attacker.lower()
    impact = f"{finding.title} {finding.description}".lower()

    if not _UPSTREAM_COMPROMISE.search(attacker):
        for pat in _INSIDER_ATTACKER:
            if re.search(pat, attacker):
                reasons.append(
                    "tautology: attacker already has write access to the repository "
                    f"({pat}), which already implies control of the product's code"
                )
                break

    for priv_pat, impact_pat in _PRIVILEGE_IMPACT_PAIRS:
        if re.search(priv_pat, attacker) and re.search(impact_pat, impact):
            reasons.append(
                f"tautology: attacker privilege ({priv_pat}) already implies "
                f"claimed impact ({impact_pat})"
            )

    for marker in _TAUTOLOGICAL_TEST_MARKERS:
        if re.search(marker, finding.poc_test):
            reasons.append(f"tautological PoC marker: {marker!r}")

    return reasons
