"""GET /runs, /runs/{id}, /runs/{id}/report[.md], /metrics, /coverage (issue #40)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from crucible.api.deps import get_store, get_workspace, require_read
from crucible.api.errors import ApiError
from crucible.api.mappers import run_out
from crucible.api.schemas import (
    CoverageCellOut,
    CoverageOut,
    MetricsOut,
    Page,
    RunOut,
)
from crucible.coverage import (
    CellStats,
    actionable_mechanical_failures,
    cell_key,
    load_manifest_cells,
    parse_coverage,
)
from crucible.store.dao import Store

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_read)])


@router.get("", response_model=Page[RunOut])
def list_runs(
    store: Store = Depends(get_store),
    repo: str | None = None,
    outcome: str | None = None,
    language: str | None = None,
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[RunOut]:
    rows, total = store.list_runs(
        repo=repo, outcome=outcome, language=language, since=since,
        limit=limit, offset=offset,
    )
    items = [run_out(r, store.run_counts(r.run_id)) for r in rows]
    return Page[RunOut](items=items, total=total, limit=limit, offset=offset)


def _load_run(store: Store, run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise ApiError(404, "run not found", run_id)
    return run


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, store: Store = Depends(get_store)) -> RunOut:
    run = _load_run(store, run_id)
    out = run_out(run, store.run_counts(run_id))
    return out


def _report_path(run, ws: Path) -> Path:
    if run.report_path and Path(run.report_path).is_file():
        return Path(run.report_path)
    return ws / "report.json"


@router.get("/{run_id}/report")
def get_report(run_id: str, store: Store = Depends(get_store), ws: Path = Depends(get_workspace)) -> dict:
    run = _load_run(store, run_id)
    p = _report_path(run, ws)
    if not p.is_file():
        raise ApiError(404, "report not available", "the report node has not run for this run")
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ApiError(500, "report unreadable", str(e)) from e


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
def get_report_md(run_id: str, store: Store = Depends(get_store), ws: Path = Depends(get_workspace)) -> str:
    _load_run(store, run_id)
    p = ws / "report.md"
    if not p.is_file():
        raise ApiError(404, "report.md not available")
    return p.read_text()


@router.get("/{run_id}/metrics", response_model=MetricsOut)
def get_metrics(run_id: str, store: Store = Depends(get_store), ws: Path = Depends(get_workspace)) -> MetricsOut:
    run = _load_run(store, run_id)
    usage = store.tool_usage_by_tool(run_id)
    forks = usage.get("fork_sibling", {}).get("count", 0)
    hunt_exec = sum(
        u.count for u in store.tool_usage(run_id)
        if u.role == "hunter" and u.tool_name in ("bash", "sandbox_exec")
    )
    cycles = continuations = token_spend = None
    p = _report_path(run, ws)
    if p.is_file():
        try:
            m = json.loads(p.read_text()).get("metrics", {})
            cycles, continuations, token_spend = (
                m.get("cycles"), m.get("continuations"), m.get("token_spend"),
            )
        except (OSError, json.JSONDecodeError):
            pass
    return MetricsOut(
        run_id=run_id,
        counts=store.run_counts(run_id),
        fork_rate=f"{forks}/{hunt_exec}",
        tool_usage=usage,
        cycles=cycles,
        continuations=continuations,
        token_spend=token_spend,
    )


@router.get("/{run_id}/coverage", response_model=CoverageOut)
def get_coverage(run_id: str, store: Store = Depends(get_store), ws: Path = Depends(get_workspace)) -> CoverageOut:
    _load_run(store, run_id)
    manifest = load_manifest_cells(ws)
    stats = parse_coverage(ws)
    if not manifest and not stats:
        raise ApiError(404, "no coverage yet", "recon/hunt have not produced coverage for this run")

    invalid = actionable_mechanical_failures(store, run_id)
    seen: set[str] = set()
    cells: list[CoverageCellOut] = []
    buckets: dict[str, list[str]] = {"failed": [], "missing": [], "barren": []}
    covered = 0
    for chunk in manifest:
        area = chunk.get("area", ".")
        cls = chunk["attack_class"]
        key = cell_key(area, cls)
        if key in seen:
            continue
        seen.add(key)
        cs: CellStats | None = stats.get(cls)
        passes = cs.passes if cs else 0
        findings = cs.findings if cs else 0
        if passes > 0:
            covered += 1
        cells.append(CoverageCellOut(
            area=area, attack_class=cls, passes=passes, findings=findings,
            productive=bool(cs and cs.productive), shallow=bool(cs and cs.shallow),
        ))
        if passes == 0:
            buckets["missing"].append(key)
        elif findings == 0 and passes < 2:
            buckets["barren"].append(key)
        if cls in invalid:
            buckets["failed"].append(key)

    return CoverageOut(
        run_id=run_id,
        matrix_cells=len(seen),
        covered_cells=covered,
        cells=cells,
        gapfill_buckets=buckets,
    )
