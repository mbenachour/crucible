"""API response models (issues #39–#44).

Rows from `crucible.store` are mapped to these before they cross the HTTP
boundary — ORM objects never leave the DAO. The finding payload sub-shape reuses
`crucible.validation.schema`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from crucible.validation.schema import Severity, ThreatModel

T = TypeVar("T")


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    store_ok: bool


# --- runs / reports / metrics / coverage -----------------------------------

class RunOut(BaseModel):
    run_id: str
    repo_path: str
    repo_commit: str
    primary_language: str
    created_at: datetime | None = None
    finished_at: datetime | None = None
    status: str
    outcome: str
    recon_quality: str = ""
    workspace_path: str = ""
    report_available: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    # API-triggered launches (issue #57) — source_spec/clone_status are empty
    # for a run started via the CLI. clone_error also carries the reap reason
    # (dao.reap_dead_runs) when a stale/dead run gets closed out automatically.
    source_spec: str = ""
    clone_status: str = ""
    clone_error: str = ""


class MetricsOut(BaseModel):
    run_id: str
    counts: dict[str, int]
    fork_rate: str
    tool_usage: dict[str, dict]
    cycles: int | None = None
    continuations: int | None = None
    token_spend: int | None = None


class CoverageCellOut(BaseModel):
    area: str
    attack_class: str
    passes: int
    findings: int
    productive: bool
    shallow: bool


class CoverageOut(BaseModel):
    run_id: str
    matrix_cells: int
    covered_cells: int
    cells: list[CoverageCellOut]
    gapfill_buckets: dict[str, list[str]]  # failed | missing | barren -> ["area::class", ...]


# --- findings / validations ----------------------------------------------

class ValidationOut(BaseModel):
    pass_name: str
    verdict: str
    reasoning: str = ""
    model: str = ""
    prompt_version: str = ""
    response_class: str = ""
    created_at: datetime | None = None


class FindingOut(BaseModel):
    finding_id: str
    run_id: str
    stable_key: str
    status: str
    severity: Severity | str | None = None
    title: str = ""
    file_path: str = ""
    line_start: int | None = None
    line_end: int | None = None
    description: str = ""
    threat_model: ThreatModel | dict | None = None
    poc_test: str = ""
    proposed_patch: str = ""
    provenance: dict = Field(default_factory=dict)
    duplicate_of: str | None = None
    created_at: datetime | None = None
    validation_trail: list[ValidationOut] | None = None


# --- wishlist -----------------------------------------------------------

class WishOut(BaseModel):
    id: int
    run_id: str
    blocked_task_id: str
    need: str
    context: str = ""
    status: str
    created_at: datetime | None = None


class WishResolveIn(BaseModel):
    note: str | None = None


# --- artifacts / execution state --------------------------------------

class ArtifactMetaOut(BaseModel):
    path: str
    kind: str
    bytes: int
    modified_at: str


class HuntTaskOut(BaseModel):
    task_id: str
    area: str = ""
    attack_class: str = ""
    chunk_type: str = ""
    seed_path: str | None = None
    scope_hint: str = ""
    continuation_count: int = 0


class StateOut(BaseModel):
    run_id: str
    recon_quality: str = ""
    subsystems: list[dict] = Field(default_factory=list)
    pending_hunts: list[HuntTaskOut] = Field(default_factory=list)
    pending_hunt_count: int = 0
    completed_cells: list[str] = Field(default_factory=list)
    completed_cell_count: int = 0
    finding_ids: list[str] = Field(default_factory=list)
    cycle_count: int = 0
    continuation_count: int = 0
    fork_count: int = 0
    token_spend: int = 0
    next_node: str | None = None
    checkpoint_ts: str | None = None
