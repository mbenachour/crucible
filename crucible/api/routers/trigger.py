"""POST /runs — trigger a run from a git URL (issue #58, milestone: Trigger).

The one write route beyond `POST /wishes/{id}/resolve`. Gated behind the same
write token (#45) — document loudly that this token can clone arbitrary public
repos and spawn processes on this host.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from crucible.api.deps import get_settings, get_store, require_write
from crucible.api.errors import ApiError
from crucible.api.launcher import launch_run
from crucible.api.settings import ApiSettings
from crucible.repo_acquire import RepoRefError, resolve_repo_ref, validate_ref
from crucible.store.dao import Store

router = APIRouter(tags=["trigger"], dependencies=[Depends(require_write)])


class TriggerRunIn(BaseModel):
    repo: str = Field(..., max_length=200, description="'owner/repo' or a full https:// git URL")
    ref: str | None = Field(None, max_length=200, description="branch, tag, or commit")


class TriggerRunOut(BaseModel):
    run_id: str
    clone_status: str


@router.post("/runs", response_model=TriggerRunOut, status_code=202)
def trigger_run(
    body: TriggerRunIn,
    response: Response,
    store: Store = Depends(get_store),
    settings: ApiSettings = Depends(get_settings),
) -> TriggerRunOut:
    repo = body.repo.strip()
    # Fail fast and synchronously on a bad spec/host — don't make the caller
    # poll a background failure for something checkable right here. The same
    # checks run again inside the background clone (defense in depth); this
    # call is authoritative for what `git clone` will actually be given.
    try:
        resolve_repo_ref(repo, allowed_hosts=settings.allowed_git_hosts)
        ref = validate_ref(body.ref)
    except RepoRefError as e:
        raise ApiError(422, "invalid repo", str(e)) from e

    # A launch stuck mid-clone (its process died without updating status)
    # would otherwise permanently occupy a concurrency slot.
    store.sweep_stuck_launches(2 * settings.clone_timeout_s)

    active = store.count_active_runs()
    if active >= settings.max_concurrent_runs:
        raise ApiError(
            429, "too many runs in progress",
            f"{active}/{settings.max_concurrent_runs} active — try again shortly",
        )

    run_id = launch_run(store, settings, source_spec=repo, ref=ref)
    response.headers["Location"] = f"/runs/{run_id}"
    return TriggerRunOut(run_id=run_id, clone_status="pending")
