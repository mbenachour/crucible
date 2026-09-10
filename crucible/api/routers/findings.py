"""GET /runs/{id}/findings[/upheld], /findings/{id}[/validations], /findings?stable_key= (issue #41)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from crucible.api.deps import get_store, require_read
from crucible.api.errors import ApiError
from crucible.api.mappers import finding_out, validation_out
from crucible.api.schemas import FindingOut, Page, ValidationOut
from crucible.store.dao import Store

router = APIRouter(tags=["findings"], dependencies=[Depends(require_read)])

_STATUSES = {
    "raw", "mechanical_failed", "mechanical_passed", "bug_refuted", "bug_upheld",
    "reach_refuted", "reach_upheld", "duplicate",
}
_SEVERITIES = {"low", "medium", "high", "critical"}
_MIN_SEV_ORDER = ["low", "medium", "high", "critical"]


def _page(rows, total, limit, offset, *, trail=None) -> Page[FindingOut]:
    return Page[FindingOut](
        items=[finding_out(f) for f in rows], total=total, limit=limit, offset=offset,
    )


@router.get("/runs/{run_id}/findings", response_model=Page[FindingOut])
def list_run_findings(
    run_id: str,
    store: Store = Depends(get_store),
    status: list[str] | None = Query(None),
    severity: list[str] | None = Query(None),
    min_severity: str | None = Query(None),
    attack_class: str | None = None,
    order: str = Query("severity", pattern="^(severity|created_at)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[FindingOut]:
    if status and not set(status) <= _STATUSES:
        raise ApiError(422, "bad status filter", f"allowed: {sorted(_STATUSES)}")
    sev = set(severity or [])
    if min_severity:
        if min_severity not in _SEVERITIES:
            raise ApiError(422, "bad min_severity", f"allowed: {sorted(_SEVERITIES)}")
        sev |= set(_MIN_SEV_ORDER[_MIN_SEV_ORDER.index(min_severity):])
    if sev and not sev <= _SEVERITIES:
        raise ApiError(422, "bad severity filter", f"allowed: {sorted(_SEVERITIES)}")
    rows, total = store.query_findings(
        run_id=run_id, status=status, severity=sorted(sev) or None,
        attack_class=attack_class, order=order, limit=limit, offset=offset,
    )
    return _page(rows, total, limit, offset)


@router.get("/runs/{run_id}/findings/upheld", response_model=Page[FindingOut])
def list_upheld(
    run_id: str,
    store: Store = Depends(get_store),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[FindingOut]:
    rows, total = store.query_findings(
        run_id=run_id, status=["reach_upheld"], limit=limit, offset=offset,
    )
    return _page(rows, total, limit, offset)


@router.get("/findings", response_model=Page[FindingOut])
def find_by_stable_key(
    stable_key: str = Query(..., min_length=1),
    store: Store = Depends(get_store),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[FindingOut]:
    rows, total = store.query_findings(
        stable_key=stable_key, order="created_at", limit=limit, offset=offset,
    )
    return _page(rows, total, limit, offset)


@router.get("/findings/{finding_id}", response_model=FindingOut)
def get_finding(finding_id: str, store: Store = Depends(get_store)) -> FindingOut:
    f = store.get_finding(finding_id)
    if f is None:
        raise ApiError(404, "finding not found", finding_id)
    trail = store.list_validations(finding_id)
    return finding_out(f, trail=trail)


@router.get("/findings/{finding_id}/validations", response_model=list[ValidationOut])
def get_validations(finding_id: str, store: Store = Depends(get_store)) -> list[ValidationOut]:
    if store.get_finding(finding_id) is None:
        raise ApiError(404, "finding not found", finding_id)
    return [validation_out(v) for v in store.list_validations(finding_id)]
