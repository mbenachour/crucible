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
    OLLAMA = "ollama"        # local, via langchain-ollama
    DEEPSEEK = "deepseek"    # hosted, OpenAI-compatible, via langchain-deepseek
    # Reserved — accepted in config/env but `chat_model` raises until wired.
    OPENAI = "openai"
    VLLM = "vllm"
    OPENAI_COMPAT = "openai_compat"

    @classmethod
    def parse(cls, value: str) -> "Provider":
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValueError(
                f"unknown LLM provider {value!r}; expected one of "
                f"{[p.value for p in cls]}"
            ) from None


DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"

# per-provider env var holding the API key (checked at chat_model() time)
_PROVIDER_API_KEY_ENV = {Provider.DEEPSEEK: "DEEPSEEK_API_KEY"}
# per-provider env var holding the default model name
_PROVIDER_MODEL_ENV = {Provider.DEEPSEEK: "DEEPSEEK_MODEL"}
_PROVIDER_DEFAULT_MODEL = {Provider.DEEPSEEK: DEFAULT_DEEPSEEK_MODEL}
_PROVIDER_DEFAULT_BASE_URL = {
    Provider.OLLAMA: DEFAULT_OLLAMA_BASE_URL,
    Provider.DEEPSEEK: DEFAULT_DEEPSEEK_BASE_URL,
}


@dataclass(frozen=True)
class ModelEndpoint:
    """A role's model binding. `model` is the provider-native model name
    (an Ollama tag like ``qwen2.5-coder:7b``, or ``deepseek-chat`` /
    ``deepseek-reasoner``)."""

    role: ModelRole
    model: str
    provider: Provider = Provider.OLLAMA
    # "" -> use the provider's default (localhost for ollama, api.deepseek.com).
    base_url: str = ""
    api_key: str | None = None      # falls back to the provider's API-key env var
    temperature: float = 0.2
    top_p: float = 0.95
    num_ctx: int = 8192             # ollama only
    num_predict: int = 4096         # -> max_tokens for OpenAI-compatible providers
    seed: int | None = None
    # Provider-specific knobs, passed through and recorded verbatim on findings.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def resolved_base_url(self) -> str:
        return self.base_url or _PROVIDER_DEFAULT_BASE_URL.get(self.provider, "")


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
        if e.api_key:
            return e.api_key
        env = _PROVIDER_API_KEY_ENV.get(e.provider)
        key = os.environ.get(env, "") if env else ""
        if not key:
            raise RuntimeError(
                f"{e.provider.value} needs an API key: set ModelEndpoint.api_key "
                f"or the {env} environment variable."
            )
        return key

    def chat_model(self, role: ModelRole) -> BaseChatModel:
        if role in self._cache:
            return self._cache[role]
        e = self._endpoints[role]
        if e.provider is Provider.OLLAMA:
            from langchain_ollama import ChatOllama

            model = ChatOllama(
                model=e.model,
                base_url=e.resolved_base_url(),
                temperature=e.temperature,
                top_p=e.top_p,
                num_ctx=e.num_ctx,
                num_predict=e.num_predict,
                seed=e.seed,
                reasoning=False,
                validate_model_on_init=False,  # fail at call time, not import time
                **dict(e.extra),
            )
        elif e.provider is Provider.DEEPSEEK:
            from langchain_deepseek import ChatDeepSeek

            ds_kwargs: dict = {
                "model": e.model,
                "api_base": e.resolved_base_url(),
                "api_key": self._api_key(e),
                "temperature": e.temperature,
                "top_p": e.top_p,
                "max_tokens": e.num_predict,   # OpenAI-compatible knob
                # v4 models default to "thinking mode", which rejects forced
                # tool_choice (our structured-output path). Disable it unless
                # the caller overrides via `extra`.
                "extra_body": {"thinking": {"type": "disabled"}},
            }
            ds_kwargs.update(dict(e.extra))
            model = ChatDeepSeek(**ds_kwargs)
        else:
            raise NotImplementedError(
                f"provider {e.provider.value!r} is reserved but not wired; "
                "use 'ollama' or 'deepseek'"
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

        - ``<ROLE>_LLM``                   friendly provider alias, e.g. ``RECON_LLM=deepseek``
        - ``CRUCIBLE_PROVIDER_<ROLE>``      provider (wins over ``<ROLE>_LLM``)
        - ``CRUCIBLE_MODEL_<ROLE>``         per-role model (wins over ``DEEPSEEK_MODEL``)
        - ``DEEPSEEK_MODEL``               default model for any deepseek role
        - ``CRUCIBLE_TEMPERATURE_<ROLE>``
        - ``CRUCIBLE_API_KEY_<ROLE>``       per-role key (else the provider env var)
        - ``CRUCIBLE_OLLAMA_BASE_URL``      applies to every ollama role
        - ``CRUCIBLE_DEEPSEEK_BASE_URL``    applies to every deepseek role
        """
        from crucible.config import DEFAULT_ENDPOINTS  # local import: avoid cycle

        base = dict(defaults or DEFAULT_ENDPOINTS)
        ollama_base = os.environ.get("CRUCIBLE_OLLAMA_BASE_URL")
        deepseek_base = os.environ.get("CRUCIBLE_DEEPSEEK_BASE_URL")
        out: dict[ModelRole, ModelEndpoint] = {}
        for role, ep in base.items():
            r = role.value.upper()
            # `<ROLE>_LLM` (e.g. RECON_LLM=deepseek) is the friendly alias;
            # `CRUCIBLE_PROVIDER_<ROLE>` wins if both are set.
            provider = Provider.parse(
                os.environ.get(f"CRUCIBLE_PROVIDER_{r}")
                or os.environ.get(f"{r}_LLM")
                or ep.provider.value
            )
            # model resolution:
            #   CRUCIBLE_MODEL_<ROLE>  (per-role, explicit)   — highest
            #   <PROVIDER>_MODEL env / built-in default        — when provider was
            #     switched away from the config's, or set explicitly
            #   ep.model                                       — keep config value
            explicit_model = os.environ.get(f"CRUCIBLE_MODEL_{r}")
            provider_model_env = os.environ.get(_PROVIDER_MODEL_ENV.get(provider, ""))
            if explicit_model:
                model = explicit_model
            elif provider is not ep.provider:
                model = provider_model_env or _PROVIDER_DEFAULT_MODEL.get(provider, ep.model)
            elif provider_model_env:
                model = provider_model_env
            else:
                model = ep.model
            temp = os.environ.get(f"CRUCIBLE_TEMPERATURE_{r}")
            base_url = ep.base_url
            if provider is Provider.OLLAMA and ollama_base:
                base_url = ollama_base
            elif provider is Provider.DEEPSEEK and deepseek_base:
                base_url = deepseek_base
            out[role] = ModelEndpoint(
                role=role,
                model=model,
                provider=provider,
                base_url=base_url,
                api_key=os.environ.get(f"CRUCIBLE_API_KEY_{r}", ep.api_key),
                temperature=float(temp) if temp is not None else ep.temperature,
                top_p=ep.top_p,
                num_ctx=ep.num_ctx,
                num_predict=ep.num_predict,
                seed=ep.seed,
                extra=ep.extra,
            )
        return cls(out)
