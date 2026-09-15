"""GET /config/models — effective per-role model config with provenance (issue #73).
GET /config/catalog — the curated OpenRouter model list (issue #80).
PUT /config/models — save a host-default per-role override (Settings tab, issue #80)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from crucible.api.deps import get_store, require_read, require_write
from crucible.api.errors import ApiError
from crucible.api.schemas import (
    CatalogModelOut,
    ConfigCatalogOut,
    ConfigModelsOut,
    ModelEndpointOut,
    ModelSourceOut,
)
from crucible.config import ModelOverrideError, apply_model_override, load_registry_with_provenance
from crucible.llm.catalog import families
from crucible.llm.registry import ModelEndpoint, ModelRole
from crucible.store.dao import Store

router = APIRouter(tags=["config"], dependencies=[Depends(require_read)])


@router.get("/config/catalog", response_model=ConfigCatalogOut)
def get_config_catalog() -> ConfigCatalogOut:
    return ConfigCatalogOut(
        families={
            family: [
                CatalogModelOut(id=m.id, label=m.label, family=m.family, size=m.size) for m in models
            ]
            for family, models in families().items()
        }
    )


@router.get("/config/models", response_model=ConfigModelsOut)
def get_config_models(
    run_id: str | None = None,
    store: Store = Depends(get_store),
) -> ConfigModelsOut:
    registry, origins = load_registry_with_provenance(store=store)
    endpoints = {role: registry.endpoint(role) for role in ModelRole}
    if run_id is not None:
        run = store.get_run(run_id)
        if run is None:
            raise ApiError(404, "run not found", run_id)
        if run.model_override:
            # Already validated at POST /runs time — a fresh 422 here would
            # only mean the host config itself changed since (rare); reported
            # via the normal 500 path rather than special-cased.
            endpoints, origins = apply_model_override(endpoints, origins, run.model_override)
    return _to_out(endpoints, origins)


def _to_out(
    endpoints: dict[ModelRole, ModelEndpoint], origins: dict[ModelRole, dict[str, str]]
) -> ConfigModelsOut:
    roles: dict[str, ModelEndpointOut] = {}
    for role in ModelRole:
        e = endpoints[role]
        o = origins.get(role, {})
        # `ModelEndpointOut` has no `api_key` field — the secret never enters the
        # response even in memory, let alone the env vars it can fall back to.
        roles[role.value] = ModelEndpointOut(
            provider=e.provider.value,
            model=e.model,
            temperature=e.temperature,
            base_url=e.resolved_base_url(),
            source=ModelSourceOut(
                model=o.get("model", "default"),
                temperature=o.get("temperature", "default"),
                base_url=o.get("base_url", "default"),
            ),
        )
    return ConfigModelsOut(roles=roles)


class SetHostModelsIn(BaseModel):
    # Deliberately typed as a raw `dict`, not a nested pydantic model — same
    # reasoning as TriggerRunIn.models (crucible/api/routers/trigger.py):
    # field-level validation happens by hand via `apply_model_override`, so a
    # rejected value is never echoed back through pydantic's default error.
    #
    # `null` for a role clears its saved override (reverts to
    # config.yaml/env/DEFAULT_ENDPOINTS); a role omitted from the map is left
    # untouched. Only `model`/`temperature`/`base_url` are ever accepted —
    # no `provider` (issue #80: every role is OpenRouter).
    models: dict[str, dict | None] = Field(
        ...,
        description=(
            "role -> a partial endpoint override to save ({model?, temperature?, "
            "base_url?}), or null to clear that role's saved override. A role "
            "left out of the map keeps its current saved state."
        ),
    )


@router.put("/config/models", response_model=ConfigModelsOut, dependencies=[Depends(require_write)])
def set_config_models(
    body: SetHostModelsIn,
    store: Store = Depends(get_store),
) -> ConfigModelsOut:
    """Save (or clear) the host-default per-role model override — what the
    Settings tab's dropdown writes to. Takes effect for every run started
    from here on; a run already in flight keeps whatever it resolved at
    launch. Validated the same way as a per-run override (issue #77):
    catalog membership, and the resulting config must still keep hunter and
    validator_bug on different models (specs.md §6)."""
    valid_roles = {r.value for r in ModelRole}
    unknown = sorted(set(body.models) - valid_roles)
    if unknown:
        raise ApiError(
            422, "invalid model config",
            f"unknown model role(s): {unknown}; expected one of {sorted(valid_roles)}",
        )

    # Validate the config this request would *result in* — not just the
    # fields it touches — before persisting anything. In particular,
    # clearing a role (partial=None) can itself produce a HUNTER ==
    # VALIDATOR_BUG collision against another role's still-saved override,
    # so the check has to run against the merged saved state, not a diff.
    env_registry, _env_origins = load_registry_with_provenance()  # no store: env-only baseline
    env_endpoints = {role: env_registry.endpoint(role) for role in ModelRole}

    resulting = {role: dict(partial) for role, partial in store.get_host_model_config().items()}
    for role_name, partial in body.models.items():
        if partial is None:
            resulting.pop(role_name, None)
        else:
            resulting[role_name] = {**resulting.get(role_name, {}), **partial}

    try:
        if resulting:
            apply_model_override(env_endpoints, _env_origins, resulting)
    except ModelOverrideError as e:
        raise ApiError(422, "invalid model config", str(e)) from e

    for role_name, partial in body.models.items():
        if partial is None:
            store.clear_host_model_config(role_name)
        else:
            store.set_host_model_config(role_name, partial)

    registry, origins = load_registry_with_provenance(store=store)
    return _to_out({role: registry.endpoint(role) for role in ModelRole}, origins)
