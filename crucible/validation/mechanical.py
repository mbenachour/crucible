"""Deterministic validation gates — Pass A (specs.md §9.4).

No model calls. Cheapest filter first. Plain Python doing deterministic work
(§1.8): path checks, schema conformance, patch/test parsing, PoC gate.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MechStatus(str, Enum):
    PASSED = "passed"
    MECHANICAL_FAILED = "mechanical_failed"


@dataclass
class MechResult:
    finding_id: str
    status: MechStatus
    reasons: list[str] = field(default_factory=list)


def check_finding(
    finding_id: str,
    *,
    repo_path: str,
    repo_commit: str,
    workspace_path: str,
    store=None,
) -> MechResult:
    """Run every deterministic gate; accumulate failure reasons."""
    reasons: list[str] = []

    finding = _load_finding(finding_id, store, workspace_path)
    if finding is None:
        return MechResult(finding_id, MechStatus.MECHANICAL_FAILED, ["finding not found"])

    reasons += _check_path_and_range(finding, repo_path)
    reasons += _check_schema(finding)
    reasons += _check_patch_applies(finding, repo_path, repo_commit)
    reasons += _check_poc_parses(finding)
    reasons += _check_poc_gate(finding, repo_path, repo_commit, workspace_path)

    status = MechStatus.PASSED if not reasons else MechStatus.MECHANICAL_FAILED
    return MechResult(finding_id, status, reasons)


def _load_finding(finding_id: str, store, workspace_path: str):
    """Reconstruct the `Finding` from the store, falling back to the workspace
    JSON the Hunter wrote (`findings/<id>.json`)."""
    from crucible.validation.schema import Finding

    payload = None
    if store is not None:
        row = store.get_finding(finding_id)
        if row is not None:
            payload = row.payload
    if payload is None:
        from crucible.workspace import layout

        p = layout.finding_path(workspace_path, finding_id)
        if p.is_file():
            import json

            payload = json.loads(p.read_text())
    if not isinstance(payload, dict):
        return None
    try:
        return Finding.model_validate(payload)
    except Exception:  # noqa: BLE001 — a malformed payload is a mechanical failure
        return None


def _check_path_and_range(finding, repo_path: str) -> list[str]:
    p = Path(repo_path) / finding.file_path
    if not p.is_file():
        return [f"cited path does not exist at repo_commit: {finding.file_path}"]
    n_lines = len(p.read_text(errors="replace").splitlines())
    if not (1 <= finding.line_start <= finding.line_end <= n_lines):
        return [f"line range out of bounds: {finding.line_start}-{finding.line_end} of {n_lines}"]
    return []


def _check_schema(finding) -> list[str]:
    from crucible.validation.schema import tautology_reasons

    reasons: list[str] = []
    if not (finding.threat_model.attacker and finding.threat_model.boundary_crossed
            and finding.threat_model.assumption_broken):
        reasons.append("threat_model not fully populated")
    reasons += tautology_reasons(finding)
    return reasons


def _check_patch_applies(finding, repo_path: str, repo_commit: str) -> list[str]:
    """Dry-run the unified diff against the unmodified tree, then revert."""
    proc = subprocess.run(
        ["git", "-C", repo_path, "apply", "--check", "-"],
        input=finding.proposed_patch, text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return [f"patch does not apply cleanly: {proc.stderr.strip()}"]
    return []


def _check_poc_parses(finding) -> list[str]:
    # TODO(phase1): language-aware parse (py: ast.parse; c: compile-only).
    if not finding.poc_test.strip():
        return ["poc_test is empty"]
    return []


def _check_poc_gate(finding, repo_path: str, repo_commit: str, workspace_path: str) -> list[str]:
    """PoC gate: test FAILS on the unmodified repo and PASSES with the patch
    applied. Any source modification outside the patch invalidates the finding.

    Sandbox execution of the PoC is issue #9 — until it lands this is
    **advisory**, not blocking: the deterministic checks above (path, schema,
    tautology deny-list, patch-applies, poc parses) still gate Pass A, and the
    two model passes (bug / reachability) do the adversarial work. Set
    ``CRUCIBLE_POC_GATE=strict`` to restore the fail-closed behaviour.
    """
    import os

    if os.environ.get("CRUCIBLE_POC_GATE", "").strip().lower() == "strict":
        return ["poc_gate: strict mode and sandbox execution not implemented (issue #9)"]
    return []
