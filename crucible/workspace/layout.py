"""Workspace layout constants (specs.md §7).

workspace/ is git-initialized and separate from the read-only source checkout.
The filesystem is the collaboration surface between agents, not just storage.

    architecture.md          # Recon output; every Hunter reads this
    taxonomy.json            # attack classes for this repo
    coverage/<area>.md       # what was examined, by whom, what was found
    findings/<id>.json
    scratch/<task_id>/       # per-task PoC compile/run space
    offload/<call_id>.txt    # full tool outputs
"""

from __future__ import annotations

from pathlib import Path

ARCHITECTURE_MD = "architecture.md"
TAXONOMY_JSON = "taxonomy.json"
COVERAGE_DIR = "coverage"
FINDINGS_DIR = "findings"
SCRATCH_DIR = "scratch"
OFFLOAD_DIR = "offload"

SUBDIRS = (COVERAGE_DIR, FINDINGS_DIR, SCRATCH_DIR, OFFLOAD_DIR)


def architecture_path(ws: str | Path) -> Path:
    return Path(ws) / ARCHITECTURE_MD


def taxonomy_path(ws: str | Path) -> Path:
    return Path(ws) / TAXONOMY_JSON


def coverage_path(ws: str | Path, area: str) -> Path:
    return Path(ws) / COVERAGE_DIR / f"{area}.md"


def finding_path(ws: str | Path, finding_id: str) -> Path:
    return Path(ws) / FINDINGS_DIR / f"{finding_id}.json"


def scratch_path(ws: str | Path, task_id: str) -> Path:
    return Path(ws) / SCRATCH_DIR / task_id


def offload_path(ws: str | Path, call_id: str) -> Path:
    return Path(ws) / OFFLOAD_DIR / f"{call_id}.txt"
