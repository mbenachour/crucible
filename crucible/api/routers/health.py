"""GET /health (issue #39)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

import crucible
from crucible.api.deps import get_store
from crucible.api.schemas import HealthOut
from crucible.sandbox.docker import sandbox_status
from crucible.store.dao import Store

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthOut)
def health(store: Store = Depends(get_store)) -> HealthOut:
    ok = True
    try:
        with store.read_session() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — health must not raise
        ok = False
    sandbox_ok, sandbox_detail = sandbox_status()
    return HealthOut(version=crucible.__version__, store_ok=ok, sandbox_ok=sandbox_ok, sandbox_detail=sandbox_detail)
