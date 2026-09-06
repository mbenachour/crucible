"""Role -> model-endpoint routing; enforces hunter != validator (specs.md §6).

Hunter and bug-validator MUST be structurally different models (different
training lineage). This is the primary noise-control mechanism, not a config
preference. The registry asserts `HUNTER != VALIDATOR_BUG` at construction and
fails loudly if they match.

Providers are interchangeable commodities. The registry is written to absorb
that: a role is bound to a `(provider, model, sampling)` triple, and
`chat_model(role)` returns a LangChain `BaseChatModel`. Only the **Ollama**
provider is wired today (`langchain-ollama`); `vllm` / `openai_compat` are
reserved enum values so config and call sites do not have to change later.

Per-role sampling params are recorded on every finding (see `sampling_params`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from langchain_core.language_models.chat_models import BaseChatModel


class ModelRole(str, Enum):
    RECON = "recon"
    HUNTER = "hunter"
    VALIDATOR_BUG = "validator_bug"
    VALIDATOR_REACH = "validator_reach"


class Provider(str, Enum):
    OLLAMA = "ollama"
    # Reserved — not wired yet. Config may name them; `chat_model` will raise.
    VLLM = "vllm"
    OPENAI_COMPAT = "openai_compat"


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass(frozen=True)
class ModelEndpoint:
    """A role's model binding. `model` is the provider-native model name
    (e.g. an Ollama tag like ``qwen2.5-coder:7b``)."""

    role: ModelRole
    model: str
    provider: Provider = Provider.OLLAMA
    base_url: str = DEFAULT_OLLAMA_BASE_URL
    temperature: float = 0.2
    top_p: float = 0.95
    num_ctx: int = 8192
    num_predict: int = 4096
    seed: int | None = None
    # Provider-specific knobs, passed through and recorded verbatim on findings.
    extra: Mapping[str, Any] = field(default_factory=dict)


class ModelRegistry:
    def __init__(self, endpoints: dict[ModelRole, ModelEndpoint]):
        missing = [r for r in ModelRole if r not in endpoints]
        if missing:
            raise ValueError(f"registry missing endpoints for: {[r.value for r in missing]}")
        self._endpoints = endpoints
        self._assert_hunter_ne_validator()
        self._cache: dict[ModelRole, BaseChatModel] = {}

    def _assert_hunter_ne_validator(self) -> None:
        h = self._endpoints[ModelRole.HUNTER]
        v = self._endpoints[ModelRole.VALIDATOR_BUG]
        if (h.provider, h.model) == (v.provider, v.model):
            raise RuntimeError(
                "Hunter and VALIDATOR_BUG must be different models "
                f"(both are {h.provider.value}:{h.model!r}). See specs.md §6."
            )

    # --- lookups ---------------------------------------------------------
    def endpoint(self, role: ModelRole) -> ModelEndpoint:
        return self._endpoints[role]

    def sampling_params(self, role: ModelRole) -> dict:
        """Flat dict recorded on every finding / validation for provenance."""
        e = self._endpoints[role]
        return {
            "provider": e.provider.value,
            "model": e.model,
            "base_url": e.base_url,
            "temperature": e.temperature,
            "top_p": e.top_p,
            "num_ctx": e.num_ctx,
            "num_predict": e.num_predict,
            "seed": e.seed,
            **dict(e.extra),
        }

    def chat_model(self, role: ModelRole) -> BaseChatModel:
        if role in self._cache:
            return self._cache[role]
        e = self._endpoints[role]
        if e.provider is Provider.OLLAMA:
            from langchain_ollama import ChatOllama

            model = ChatOllama(
                model=e.model,
                base_url=e.base_url,
                temperature=e.temperature,
                top_p=e.top_p,
                num_ctx=e.num_ctx,
                num_predict=e.num_predict,
                seed=e.seed,
                reasoning=False,
                validate_model_on_init=False,  # fail at call time, not import time
                **dict(e.extra),
            )
        else:
            raise NotImplementedError(
                f"provider {e.provider.value!r} is reserved but not wired; use 'ollama'"
            )
        self._cache[role] = model
        return model

    # --- constructors --------------------------------------------------
    @classmethod
    def from_endpoints(cls, endpoints: dict[ModelRole, ModelEndpoint]) -> "ModelRegistry":
        return cls(endpoints)

    @classmethod
    def from_env(cls, defaults: dict[ModelRole, ModelEndpoint] | None = None) -> "ModelRegistry":
        """Build from `defaults`, then apply env overrides:

        - ``CRUCIBLE_PROVIDER_<ROLE>``      e.g. ``ollama``
        - ``CRUCIBLE_MODEL_<ROLE>``         e.g. ``llama3.1:8b``
        - ``CRUCIBLE_TEMPERATURE_<ROLE>``
        - ``CRUCIBLE_OLLAMA_BASE_URL``      applies to every ollama role
        """
        from crucible.config import DEFAULT_ENDPOINTS  # local import: avoid cycle

        base = dict(defaults or DEFAULT_ENDPOINTS)
        ollama_base = os.environ.get("CRUCIBLE_OLLAMA_BASE_URL")
        out: dict[ModelRole, ModelEndpoint] = {}
        for role, ep in base.items():
            r = role.value.upper()
            provider = Provider(os.environ.get(f"CRUCIBLE_PROVIDER_{r}", ep.provider.value))
            model = os.environ.get(f"CRUCIBLE_MODEL_{r}", ep.model)
            temp = os.environ.get(f"CRUCIBLE_TEMPERATURE_{r}")
            base_url = ep.base_url
            if provider is Provider.OLLAMA and ollama_base:
                base_url = ollama_base
            out[role] = ModelEndpoint(
                role=role,
                model=model,
                provider=provider,
                base_url=base_url,
                temperature=float(temp) if temp is not None else ep.temperature,
                top_p=ep.top_p,
                num_ctx=ep.num_ctx,
                num_predict=ep.num_predict,
                seed=ep.seed,
                extra=ep.extra,
            )
        return cls(out)
