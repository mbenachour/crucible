"""Finding schema, field order, tautology deny-list (specs.md §9.2, §4).

Field order is load-bearing: threat_model FIRST, so the model commits to an
attacker and a boundary before describing a bug. Pydantic preserves declared
field order for vLLM guided-JSON generation.
"""

from __future__ import annotations

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
    proposed_patch: str = Field(..., description="unified diff")
    severity: Severity


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

# Tests that prove nothing on their own ("exec() executes things, therefore
# critical"). Used as a soft signal alongside the PoC gate.
_TAUTOLOGICAL_TEST_MARKERS = [
    r"assert True\b",
    r"# ?this always passes",
]


def tautology_reasons(finding: Finding) -> list[str]:
    """Return a list of deny-list hits. Empty list == accepted."""
    import re

    reasons: list[str] = []
    attacker = finding.threat_model.attacker.lower()
    impact = f"{finding.title} {finding.description}".lower()

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
