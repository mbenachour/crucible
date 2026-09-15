"""Role -> model-endpoint routing; enforces hunter != validator (specs.md §6).

Hunter and bug-validator MUST be structurally different models (different
training lineage). This is the primary noise-control mechanism, not a config
preference. The registry asserts `HUNTER != VALIDATOR_BUG` at construction and
fails loudly if they match.

OpenRouter only (issue #80) — one hosted key, any model, via `langchain_openai`
(OpenRouter is OpenAI-compatible). Earlier versions of this module also wired
`ollama` (local) and native `deepseek`; that multi-provider abstraction added
real complexity (per-provider base URLs, API-key env vars, default models,
`<ROLE>_LLM` aliasing) for providers we no longer use, so it's gone. Which
*model* a role uses is still very much a live question — see
`crucible/llm/catalog.py` for the curated, per-family model list the Settings
UI and per-run override both validate against.

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
    OPENROUTER = "openrouter"  # the only wired provider (issue #80)

    @classmethod
    def parse(cls, value: str) -> "Provider":
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValueError(
                f"unknown LLM provider {value!r}; Crucible only talks to OpenRouter "
                f"(issue #80) — expected {cls.OPENROUTER.value!r}"
            ) from None


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"


@dataclass(frozen=True)
class ModelEndpoint:
    """A role's model binding — an OpenRouter model id (e.g.
    ``deepseek/deepseek-chat-v3.1``, ``qwen/qwen3-32b``)."""

    role: ModelRole
    model: str
    provider: Provider = Provider.OPENROUTER
    base_url: str = ""              # "" -> DEFAULT_OPENROUTER_BASE_URL
    api_key: str | None = None      # falls back to OPENROUTER_API_KEY
    temperature: float = 0.2
    top_p: float = 0.95
    num_ctx: int = 8192             # unused by OpenRouter; kept for provenance/back-compat
    num_predict: int = 4096         # -> max_tokens
    seed: int | None = None
    # Provider-specific knobs, passed through and recorded verbatim on findings.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def resolved_base_url(self) -> str:
        return self.base_url or DEFAULT_OPENROUTER_BASE_URL


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
            "base_url": e.resolved_base_url(),
            "temperature": e.temperature,
            "top_p": e.top_p,
            "num_ctx": e.num_ctx,
            "num_predict": e.num_predict,
            "seed": e.seed,
            **dict(e.extra),
        }

    def _api_key(self, e: ModelEndpoint) -> str:
        key = e.api_key or os.environ.get(OPENROUTER_API_KEY_ENV, "")
        if not key:
            raise RuntimeError(
                "openrouter needs an API key: set ModelEndpoint.api_key or the "
                f"{OPENROUTER_API_KEY_ENV} environment variable."
            )
        return key

    def chat_model(self, role: ModelRole) -> BaseChatModel:
        if role in self._cache:
            return self._cache[role]
        e = self._endpoints[role]
        from langchain_openai import ChatOpenAI

        or_kwargs: dict = {
            "model": e.model,
            "base_url": e.resolved_base_url(),
            "api_key": self._api_key(e),
            "temperature": e.temperature,
            "top_p": e.top_p,
            "max_tokens": e.num_predict,   # OpenAI-compatible knob
            # OpenRouter's optional attribution headers — harmless if the
            # env vars are unset, and overridable via `extra`.
            "default_headers": {
                "HTTP-Referer": os.environ.get(
                    "OPENROUTER_SITE_URL", "https://github.com/mbenachour/crucible"
                ),
                "X-Title": os.environ.get("OPENROUTER_APP_NAME", "crucible"),
            },
        }
        or_kwargs.update(dict(e.extra))
        model = ChatOpenAI(**or_kwargs)
        self._cache[role] = model
        return model

    # --- constructors --------------------------------------------------
    @classmethod
    def from_endpoints(cls, endpoints: dict[ModelRole, ModelEndpoint]) -> "ModelRegistry":
        return cls(endpoints)

    @classmethod
    def from_env(cls, defaults: dict[ModelRole, ModelEndpoint] | None = None) -> "ModelRegistry":
        """Build from `defaults`, then apply env overrides:

        - ``CRUCIBLE_MODEL_<ROLE>``         per-role OpenRouter model id — the
          "model matrix"; e.g. ``CRUCIBLE_MODEL_HUNTER=deepseek/deepseek-chat-v3.1``
        - ``CRUCIBLE_TEMPERATURE_<ROLE>``
        - ``CRUCIBLE_API_KEY_<ROLE>``       per-role key (else ``OPENROUTER_API_KEY``)
        - ``OPENROUTER_BASE_URL``          applies to every role
        """
        out, _origins = cls._resolve_env(defaults, None)
        return cls(out)

    @classmethod
    def from_env_with_origins(
        cls,
        defaults: dict[ModelRole, ModelEndpoint] | None = None,
        base_origins: dict[ModelRole, dict[str, str]] | None = None,
    ) -> tuple[ModelRegistry, dict[ModelRole, dict[str, str]]]:
        """Like `from_env`, but also returns per-field provenance (issue #73):
        for each role, a dict mapping ``provider``/``model``/``temperature``/
        ``base_url`` to the origin that last set it — ``"env:<VARNAME>"`` if an
        env var won here, else whatever `base_origins` already said (e.g. a
        config file name, or ``"default"``)."""
        out, origins = cls._resolve_env(defaults, base_origins)
        return cls(out), origins

    @classmethod
    def _resolve_env(
        cls,
        defaults: dict[ModelRole, ModelEndpoint] | None,
        base_origins: dict[ModelRole, dict[str, str]] | None,
    ) -> tuple[dict[ModelRole, ModelEndpoint], dict[ModelRole, dict[str, str]]]:
        """Shared resolution loop behind `from_env` / `from_env_with_origins`."""
        from crucible.config import DEFAULT_ENDPOINTS  # local import: avoid cycle

        base = dict(defaults or DEFAULT_ENDPOINTS)
        origins: dict[ModelRole, dict[str, str]] = {
            role: dict((base_origins or {}).get(role, {})) for role in base
        }
        openrouter_base = os.environ.get("OPENROUTER_BASE_URL")
        out: dict[ModelRole, ModelEndpoint] = {}
        for role, ep in base.items():
            r = role.value.upper()
            o = origins.setdefault(role, {})
            explicit_model_env_name = f"CRUCIBLE_MODEL_{r}"
            explicit_model = os.environ.get(explicit_model_env_name)
            if explicit_model:
                model = explicit_model
                o["model"] = f"env:{explicit_model_env_name}"
            else:
                model = ep.model  # unchanged — keep prior origin
            temp_env_name = f"CRUCIBLE_TEMPERATURE_{r}"
            temp = os.environ.get(temp_env_name)
            if temp is not None:
                o["temperature"] = f"env:{temp_env_name}"
            base_url = ep.base_url
            if openrouter_base:
                base_url = openrouter_base
                o["base_url"] = "env:OPENROUTER_BASE_URL"
            out[role] = ModelEndpoint(
                role=role,
                model=model,
                provider=Provider.OPENROUTER,
                base_url=base_url,
                api_key=os.environ.get(f"CRUCIBLE_API_KEY_{r}", ep.api_key),
                temperature=float(temp) if temp is not None else ep.temperature,
                top_p=ep.top_p,
                num_ctx=ep.num_ctx,
                num_predict=ep.num_predict,
                seed=ep.seed,
                extra=ep.extra,
            )
        return out, origins
