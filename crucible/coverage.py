"""Deterministic coverage bookkeeping for the Phase 2 loop (specs.md §11, §12).

Divide the repo into `(area × attack_class)` cells and measure what each cell
has actually received: how many Hunt passes, how many findings, how many
reasoned negatives. Gapfill (issue #19) and Feedback (issue #21) both read this;
neither should re-derive it.

Sources, all on the filesystem (§1.1):
  * `recon/task_manifest.json` — Recon R3's intended `(area × attack_class)` matrix
  * `coverage/<area>.md`       — one block per Hunt pass, appended by `hunt._append_coverage`
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# `## h0007 · `command_injection` · catch_all · 2026-09-07 12:00:00`
_BLOCK_HEADER = re.compile(r"^##\s+\S+\s+·\s+`([^`]+)`\s+·\s+(\S+)", re.MULTILINE)


@dataclass
class CellStats:
    """What one attack class has received across every area it was hunted in."""

    attack_class: str
    passes: int = 0
    findings: int = 0
    negatives: int = 0
    empty: int = 0  # "no result emitted" — a crashed dependency looks like this (§13)
    areas: set[str] = field(default_factory=set)

    @property
    def productive(self) -> bool:
        return self.findings > 0

    @property
    def shallow(self) -> bool:
        """Hunted, but every pass came back empty — likely a broken dependency."""
        return self.passes > 0 and self.empty == self.passes


def load_manifest_cells(ws: Path) -> list[dict]:
    """Recon R3's chunk list from `recon/task_manifest.json` (the intended matrix)."""
    p = ws / "recon" / "task_manifest.json"
    if not p.is_file():
        return []
    try:
        return list(json.loads(p.read_text()).get("chunks") or [])
    except (json.JSONDecodeError, OSError):
        return []


def parse_coverage(ws: Path) -> dict[str, CellStats]:
    """Aggregate every `coverage/<area>.md` block, keyed by attack class.

    Area granularity in the block filename is lossy (Recon collapses flat repos
    to a single area), so the stable key is the attack class; the raw areas seen
    are kept on the side for the matrix view.
    """
    out: dict[str, CellStats] = {}
    cov_dir = ws / "coverage"
    if not cov_dir.is_dir():
        return out
    for md in sorted(cov_dir.glob("*.md")):
        text = md.read_text(errors="replace")
        blocks = re.split(r"(?m)^(?=##\s)", text)
        for block in blocks:
            m = _BLOCK_HEADER.search(block)
            if not m:
                continue
            attack_class = m.group(1)
            cs = out.setdefault(attack_class, CellStats(attack_class))
            cs.passes += 1
            cs.areas.add(md.stem)
            cs.findings += len(re.findall(r"(?m)^- \*\*finding\b", block))
            cs.negatives += len(re.findall(r"(?m)^- negative:", block))
            cs.empty += len(re.findall(r"(?m)^- no result emitted\b", block))
    return out


def manifest_task(chunk: dict, task_id: str, *, scope_prefix: str = "") -> dict:
    """Rebuild a `HuntTask` dict from a manifest chunk (same shape Recon seeds)."""
    hint = chunk.get("scope_hint", "")
    if scope_prefix:
        hint = f"{scope_prefix} {hint}".strip()
    return {
        "task_id": task_id,
        "area": chunk.get("area", "."),
        "attack_class": chunk["attack_class"],
        "scope_hint": hint,
        "chunk_type": chunk.get("chunk_type", "catch_all"),
        "seed_path": chunk.get("seed_path"),
        "continuation_count": 0,
    }


def cell_key(area: str, attack_class: str) -> str:
    """Canonical `"area::attack_class"` string — matches `state["completed_cells"]`."""
    return f"{area}::{attack_class}"


# Mechanical-failure reasons that are the Hunter's fault (worth feeding back and
# worth another Gapfill pass). "poc_gate not implemented" / "finding not found"
# are harness gaps, not Hunter errors — they must never trigger a rewrite.
_ACTIONABLE_MECH = (
    "does not exist", "out of bounds", "does not apply", "not fully populated",
    "tautology", "poc_test is empty", "corrupt patch",
)


def actionable_mechanical_failures(store, run_id: str) -> dict[str, str]:
    """`attack_class -> first actionable mechanical-failure reason` for this run.

    The class is recovered from `FindingRow.hunter_prompt_version` (`"<class>@<ver>"`,
    set by `hunt._attack_class_body`). Shared by Gapfill (re-sweep a cell whose
    finding was mechanically invalid) and Feedback (rewrite that cell's prompt).
    """
    if store is None:
        return {}
    try:
        rows = store.run_findings(run_id, ["mechanical_failed"])
    except Exception:  # noqa: BLE001 — callers degrade to their other signals
        return {}
    out: dict[str, str] = {}
    for row in rows:
        cls = (row.hunter_prompt_version or "").split("@")[0] or "?"
        if cls in out:
            continue
        reasons = store.finding_reasons(row.finding_id, "mechanical")
        hits = [r for r in reasons if any(k in r.lower() for k in _ACTIONABLE_MECH)]
        if hits:
            out[cls] = hits[0][:200]
    return out
