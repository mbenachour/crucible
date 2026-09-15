"""GET /config/models — effective per-role model config with provenance (issue #73).
GET /config/catalog — the curated OpenRouter model list (issue #80)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from crucible.api.deps import get_store, require_read
from crucible.api.errors import ApiError
from crucible.api.schemas import (
    CatalogModelOut,
    ConfigCatalogOut,
    ConfigModelsOut,
    ModelEndpointOut,
    ModelSourceOut,
)
from crucible.config import apply_model_override, load_registry_with_provenance
from crucible.llm.catalog import families
from crucible.llm.registry import ModelRole
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
    registry, origins = load_registry_with_provenance()
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
