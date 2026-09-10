"""Wishlist endpoints — list / get / resolve (issue #44, specs §9.3)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from crucible.api.deps import get_store, require_read, require_write
from crucible.api.errors import ApiError
from crucible.api.mappers import wish_out
from crucible.api.schemas import Page, WishOut, WishResolveIn
from crucible.store.dao import Store

router = APIRouter(tags=["wishlist"])

_STATUSES = {"open", "resolved", "requeued"}


@router.get("/runs/{run_id}/wishes", response_model=Page[WishOut], dependencies=[Depends(require_read)])
def list_run_wishes(
    run_id: str,
    store: Store = Depends(get_store),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[WishOut]:
    rows, total = store.list_wishes(run_id=run_id, status=status, limit=limit, offset=offset)
    return Page[WishOut](items=[wish_out(w) for w in rows], total=total, limit=limit, offset=offset)


@router.get("/wishes", response_model=Page[WishOut], dependencies=[Depends(require_read)])
def list_wishes(
    store: Store = Depends(get_store),
    status: str | None = Query("open"),
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> Page[WishOut]:
    rows, total = store.list_wishes(status=status, since=since, limit=limit, offset=offset)
    return Page[WishOut](items=[wish_out(w) for w in rows], total=total, limit=limit, offset=offset)


@router.get("/wishes/{wish_id}", response_model=WishOut, dependencies=[Depends(require_read)])
def get_wish(wish_id: int, store: Store = Depends(get_store)) -> WishOut:
    w = store.get_wish(wish_id)
    if w is None:
        raise ApiError(404, "wish not found", str(wish_id))
    return wish_out(w)


@router.post("/wishes/{wish_id}/resolve", response_model=WishOut, dependencies=[Depends(require_write)])
def resolve_wish(
    wish_id: int, body: WishResolveIn | None = None, store: Store = Depends(get_store)
) -> WishOut:
    """Mark a wish resolved. Does NOT itself re-queue the blocked task — that
    happens on the next `crucible run --resume <run_id>` (specs §9.3)."""
    if not store.set_wish_status(wish_id, "resolved"):
        raise ApiError(404, "wish not found", str(wish_id))
    return wish_out(store.get_wish(wish_id))
