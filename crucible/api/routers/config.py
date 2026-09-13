"""GET /config/models — effective per-role model config with provenance (issue #73)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from crucible.api.deps import require_read
from crucible.api.schemas import ConfigModelsOut, ModelEndpointOut, ModelSourceOut
from crucible.config import load_registry_with_provenance
from crucible.llm.registry import ModelRole

router = APIRouter(tags=["config"], dependencies=[Depends(require_read)])


@router.get("/config/models", response_model=ConfigModelsOut)
def get_config_models() -> ConfigModelsOut:
    registry, origins = load_registry_with_provenance()
    roles: dict[str, ModelEndpointOut] = {}
    for role in ModelRole:
        e = registry.endpoint(role)
        o = origins.get(role, {})
        # `ModelEndpointOut` has no `api_key` field — the secret never enters the
        # response even in memory, let alone the env vars it can fall back to.
        roles[role.value] = ModelEndpointOut(
            provider=e.provider.value,
            model=e.model,
            temperature=e.temperature,
            base_url=e.resolved_base_url(),
            source=ModelSourceOut(
                provider=o.get("provider", "default"),
                model=o.get("model", "default"),
                temperature=o.get("temperature", "default"),
                base_url=o.get("base_url", "default"),
            ),
        )
    return ConfigModelsOut(roles=roles)
