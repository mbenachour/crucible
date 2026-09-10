"""GET /runs/{id}/state — LangGraph execution state from checkpoints.sqlite (issue #43)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from crucible.api.deps import get_settings, get_store, require_read
from crucible.api.errors import ApiError
from crucible.api.schemas import HuntTaskOut, StateOut
from crucible.api.settings import ApiSettings
from crucible.store.dao import Store

router = APIRouter(prefix="/runs/{run_id}", tags=["state"], dependencies=[Depends(require_read)])


@router.get("/state", response_model=StateOut)
def get_state(
    run_id: str,
    store: Store = Depends(get_store),
    settings: ApiSettings = Depends(get_settings),
    limit: int = Query(200, ge=1, le=2000),
) -> StateOut:
    if store.get_run(run_id) is None:
        raise ApiError(404, "run not found", run_id)

    from crucible.graph.build import build_graph
    from crucible.graph.deps import NodeDeps

    deps = NodeDeps(registry=None, store=store, sandbox_provider=None)
    graph = build_graph(deps, settings.checkpoint_db)
    snap = graph.get_state({"configurable": {"thread_id": run_id}})
    values = snap.values or {}
    if not values:
        raise ApiError(404, "no checkpoint", f"no LangGraph state recorded for run {run_id}")

    pending = list(values.get("pending_hunts") or [])
    completed = list(values.get("completed_cells") or [])
    next_node = snap.next[0] if getattr(snap, "next", None) else None
    ts = getattr(snap, "created_at", None)

    return StateOut(
        run_id=run_id,
        recon_quality=values.get("recon_quality", ""),
        subsystems=list(values.get("subsystems") or []),
        pending_hunts=[HuntTaskOut(**{k: t.get(k) for k in HuntTaskOut.model_fields if k in t})
                       for t in pending[:limit]],
        pending_hunt_count=len(pending),
        completed_cells=completed[:limit],
        completed_cell_count=len(completed),
        finding_ids=list(values.get("finding_ids") or []),
        cycle_count=values.get("cycle_count", 0),
        continuation_count=values.get("continuation_count", 0),
        fork_count=values.get("fork_count", 0),
        token_spend=values.get("token_spend", 0),
        next_node=next_node,
        checkpoint_ts=str(ts) if ts else None,
    )
