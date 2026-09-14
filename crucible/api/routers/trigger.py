"""POST /runs — trigger a run from a git URL (issue #58, milestone: Trigger).

The one write route beyond `POST /wishes/{id}/resolve`. Gated behind the same
write token (#45) — document loudly that this token can clone arbitrary public
repos and spawn processes on this host.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from crucible.api.deps import get_settings, get_store, require_write
from crucible.api.errors import ApiError
from crucible.api.launcher import launch_run
from crucible.api.settings import ApiSettings
from crucible.config import ModelOverrideError, apply_model_override, load_registry_with_provenance
from crucible.llm.registry import ModelRole
from crucible.repo_acquire import RepoRefError, resolve_repo_ref, validate_ref
from crucible.store.dao import Store

router = APIRouter(tags=["trigger"], dependencies=[Depends(require_write)])


class TriggerRunIn(BaseModel):
    repo: str = Field(..., max_length=200, description="'owner/repo' or a full https:// git URL")
    ref: str | None = Field(None, max_length=200, description="branch, tag, or commit")
    # Deliberately typed as a raw `dict`, not a nested pydantic model: field-
    # level validation (including rejecting an unexpected key such as
    # `api_key`) happens by hand in `trigger_run`/`apply_model_override`, so a
    # rejected value is never echoed back via pydantic's default "extra
    # inputs are not permitted" error (which otherwise includes the raw
    # input) — issue #73's absolute no-echo rule, extended to #77.
    models: dict[str, dict[str, Any]] | None = Field(
        None,
        description=(
            "optional per-role model override (issue #77): role -> a partial "
            "endpoint object with any of provider/model/temperature/base_url. "
            "Omitted or empty means 'use the host config as-is'. Unknown "
            "roles or fields, an unresolvable provider/model, or a resulting "
            "hunter == validator_bug collision are all rejected with 422."
        ),
    )


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

    model_override: dict[str, dict] | None = body.models or None
    if model_override:
        registry, origins = load_registry_with_provenance()
        base_endpoints = {role: registry.endpoint(role) for role in ModelRole}
        try:
            apply_model_override(base_endpoints, origins, model_override)
        except ModelOverrideError as e:
            raise ApiError(422, "invalid model override", str(e)) from e

    # A launch stuck mid-clone, or any run whose process died / was abandoned
    # (crashed, killed terminal, or predates this reaper entirely) would
    # otherwise permanently occupy a concurrency slot.
    store.sweep_stuck_launches(2 * settings.clone_timeout_s)
    store.reap_dead_runs(stale_after_s=settings.stale_run_s)

    active = store.count_active_runs()
    if active >= settings.max_concurrent_runs:
        raise ApiError(
            429, "too many runs in progress",
            f"{active}/{settings.max_concurrent_runs} active — try again shortly",
        )

    run_id = launch_run(store, settings, source_spec=repo, ref=ref, model_override=model_override)
    response.headers["Location"] = f"/runs/{run_id}"
    return TriggerRunOut(run_id=run_id, clone_status="pending")
