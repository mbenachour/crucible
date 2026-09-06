"""Role -> endpoint routing; enforces hunter != validator (specs.md §6).

Hunter and bug-validator MUST be structurally different open-weight models
(different training lineage). This is the primary noise-control mechanism,
not a config preference. Startup asserts HUNTER.model != VALIDATOR_BUG.model
and fails loudly if equal.

Providers are interchangeable commodities: they change temperature, caching,
and inference-effort budgets over time, even within one model version. Build
to absorb that volatility. Per-role sampling params are recorded on every
finding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ModelRole(str, Enum):
    RECON = "recon"
    HUNTER = "hunter"
    VALIDATOR_BUG = "validator_bug"
    VALIDATOR_REACH = "validator_reach"


@dataclass(frozen=True)
class ModelEndpoint:
    role: ModelRole
    model: str                 # LiteLLM model string (points at self-hosted vLLM)
    api_base: str
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 8192
    extra: dict | None = None   # provider-specific; recorded verbatim on findings


class ModelRegistry:
    def __init__(self, endpoints: dict[ModelRole, ModelEndpoint]):
        self._endpoints = endpoints
        self._assert_hunter_ne_validator()

    def _assert_hunter_ne_validator(self) -> None:
        hunter = self._endpoints[ModelRole.HUNTER].model
        validator = self._endpoints[ModelRole.VALIDATOR_BUG].model
        if hunter == validator:
            raise RuntimeError(
                "Hunter and VALIDATOR_BUG must be different models "
                f"(both are {hunter!r}). See specs.md §6."
            )

    def get(self, role: ModelRole) -> ModelEndpoint:
        return self._endpoints[role]

    def sampling_params(self, role: ModelRole) -> dict:
        e = self._endpoints[role]
        return {
            "model": e.model,
            "temperature": e.temperature,
            "top_p": e.top_p,
            "max_tokens": e.max_tokens,
            **(e.extra or {}),
        }

    @classmethod
    def from_env(cls) -> "ModelRegistry":
        """Load endpoints from HARNESS_MODEL_<ROLE> / HARNESS_APIBASE_<ROLE>."""
        def ep(role: ModelRole) -> ModelEndpoint:
            r = role.value.upper()
            return ModelEndpoint(
                role=role,
                model=os.environ.get(f"HARNESS_MODEL_{r}", ""),
                api_base=os.environ.get(f"HARNESS_APIBASE_{r}", ""),
            )

        return cls({role: ep(role) for role in ModelRole})
