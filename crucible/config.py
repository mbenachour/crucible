"""Run configuration — model roles, endpoints, and knobs (specs.md §3, §6).

Resolution order (lowest to highest precedence):
  1. `DEFAULT_ENDPOINTS` below
  2. a file config — ``crucible.toml`` then ``config.yaml`` (both auto-discovered
     in the CWD; YAML wins where they overlap), or an explicit ``--config PATH``
     (format by suffix: ``.yaml`` / ``.yml`` -> YAML, else TOML)
  3. environment variables (see `ModelRegistry.from_env`)

**Secrets never come from the file** — API keys (``OPENROUTER_API_KEY``,
``DEEPSEEK_API_KEY``, ``LANGSMITH_API_KEY``) are read from the environment / a
gitignored ``.env`` only. The file carries non-secret routing and toggles.

Wired providers: ``ollama`` (local), ``deepseek`` (hosted, OpenAI-compatible;
needs ``DEEPSEEK_API_KEY``) and ``openrouter`` (one key for any hosted model —
the "model matrix"; needs ``OPENROUTER_API_KEY`` and a per-role model id). The
default models are Ollama and chosen so that HUNTER and VALIDATOR_BUG are
**different lineages** (the §6 assertion). Pull them first, e.g.::

    ollama pull qwen2.5-coder:7b
    ollama pull llama3.1:8b

Example ``config.yaml`` — the model matrix on OpenRouter plus tracing toggles::

    models:
      recon:          { provider: openrouter, model: qwen/qwen-2.5-coder-32b-instruct }
      hunter:         { provider: openrouter, model: anthropic/claude-sonnet-4 }
      validator_bug:  { provider: openrouter, model: openai/gpt-4o }
      validator_reach:{ provider: openrouter, model: google/gemini-2.0-flash }
    tracing:
      langsmith: { enabled: true, project: crucible }   # LANGSMITH_API_KEY from .env
      otel:      { enabled: false, endpoint: http://localhost:4318 }

The equivalent ``crucible.toml`` still works (``[models.hunter] provider = ...``).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from crucible.llm.registry import (
    ModelEndpoint,
    ModelRegistry,
    ModelRole,
    Provider,
    default_model_for,
)

# Hunter lineage (DeepSeek) != validator lineage (Llama) -> §6 assertion passes.
# Hunter defaults to hosted deepseek-v4-flash (needs DEEPSEEK_API_KEY); override
# per role via config.yaml / env as usual.
DEFAULT_ENDPOINTS: dict[ModelRole, ModelEndpoint] = {
    ModelRole.RECON: ModelEndpoint(
        role=ModelRole.RECON, model="qwen2.5-coder:7b", temperature=0.1, num_ctx=16384
    ),
    ModelRole.HUNTER: ModelEndpoint(
        role=ModelRole.HUNTER, model="deepseek-v4-flash",
        provider=Provider.DEEPSEEK, temperature=0.3,
    ),
    ModelRole.VALIDATOR_BUG: ModelEndpoint(
        role=ModelRole.VALIDATOR_BUG, model="llama3.1:8b", temperature=0.1
    ),
    ModelRole.VALIDATOR_REACH: ModelEndpoint(
        role=ModelRole.VALIDATOR_REACH, model="llama3.1:8b", temperature=0.1
    ),
}

# Context-management thresholds (specs.md §7, §1.4).
CONTEXT_CEILING = 0.25          # target share of the window the harness holds
SUMMARIZE_AT_FRACTION = 0.70    # SummarizationMiddleware trigger
MODEL_CALLS_PER_TASK = 60       # ModelCallLimitMiddleware run_limit (paired with §8 cap)

# Recon re-engineering knobs (issue #34). Env overrides use the CRUCIBLE_ prefix.
#  - RECON_MAX_SUBAGENTS  : hard cap on R1b subsystem agents (N = clamp(1, len, this))
#  - RECON_MAX_PARALLEL   : R1b fan-out width; 1 keeps the sequential path
#  - RECON_ORIENT_READ_BUDGET : model-call budget for the single R1a lead agent
RECON_MAX_SUBAGENTS = max(1, int(os.environ.get("CRUCIBLE_RECON_MAX_SUBAGENTS", "8")))
RECON_MAX_PARALLEL = max(1, int(os.environ.get("CRUCIBLE_RECON_MAX_PARALLEL", "4")))
RECON_ORIENT_READ_BUDGET = max(4, int(os.environ.get("CRUCIBLE_RECON_ORIENT_BUDGET", "24")))


# Auto-discovered in the CWD when no --config is given, applied in this order
# (so a value in config.yaml wins over the same value in crucible.toml).
_AUTO_CONFIG_NAMES = ("crucible.toml", "config.yaml", "config.yml")


def _read_config_file(path: Path) -> dict[str, Any]:
    """Parse a config file to a plain dict. YAML for ``.yaml`` / ``.yml``,
    TOML otherwise. Non-secret config only — secrets stay in the environment."""
    text = path.read_text()
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # pyyaml is a hard dependency

        return yaml.safe_load(text) or {}
    return tomllib.loads(text)


def _config_paths(config_path: str | os.PathLike | None) -> list[Path]:
    """Explicit --config wins outright; otherwise the auto-discovered set."""
    if config_path:
        p = Path(config_path)
        return [p] if p.is_file() else []
    return [p for name in _AUTO_CONFIG_NAMES if (p := Path(name)).is_file()]


# Fields the /config/models endpoint (issue #73) tracks provenance for — the
# ones that actually appear in the API response, plus `source`.
_TRACKED_FIELDS = ("provider", "model", "temperature", "base_url")


def _apply_models(
    endpoints: dict[ModelRole, ModelEndpoint],
    models: dict[str, Any] | None,
    *,
    origin: str,
    origins: dict[ModelRole, dict[str, str]],
) -> dict[ModelRole, ModelEndpoint]:
    out = dict(endpoints)
    for role in ModelRole:
        tbl = (models or {}).get(role.value)
        if not tbl:
            continue
        cur = out[role]
        out[role] = ModelEndpoint(
            role=role,
            model=tbl.get("model", cur.model),
            provider=Provider.parse(tbl.get("provider", cur.provider.value)),
            base_url=tbl.get("base_url", cur.base_url),
            api_key=tbl.get("api_key", cur.api_key),
            temperature=float(tbl.get("temperature", cur.temperature)),
            top_p=float(tbl.get("top_p", cur.top_p)),
            num_ctx=int(tbl.get("num_ctx", cur.num_ctx)),
            num_predict=int(tbl.get("num_predict", cur.num_predict)),
            seed=tbl.get("seed", cur.seed),
            extra=tbl.get("extra", cur.extra),
        )
        role_origin = origins.setdefault(role, {})
        for field in _TRACKED_FIELDS:
            if field in tbl:
                role_origin[field] = origin
    return out


def load_registry(
    config_path: str | os.PathLike | None = None,
    run_override: dict[str, dict] | None = None,
) -> ModelRegistry:
    """DEFAULT_ENDPOINTS -> file config (yaml/toml) -> env overrides -> [run
    override] -> registry."""
    registry, _origins = load_registry_with_provenance(config_path, run_override)
    return registry


def load_registry_with_provenance(
    config_path: str | os.PathLike | None = None,
    run_override: dict[str, dict] | None = None,
) -> tuple[ModelRegistry, dict[ModelRole, dict[str, str]]]:
    """Like `load_registry`, but also returns per-role, per-field provenance:
    ``{role: {"provider": "default" | "crucible.toml" | "config.yaml" | "env:VAR" |
    "run override", ...}}``. Backs `GET /config/models` (issue #73) — "why is
    hunter on deepseek?".

    `run_override` (issue #77) is the highest-precedence layer — a per-run
    partial-endpoint override, applied *after* env resolution — in the same
    ``{role: {"provider"|"model"|"temperature"|"base_url": ...}}`` shape as a
    config file's ``models:`` block. See `apply_model_override`.
    """
    endpoints = dict(DEFAULT_ENDPOINTS)
    origins: dict[ModelRole, dict[str, str]] = {
        role: {field: "default" for field in _TRACKED_FIELDS} for role in ModelRole
    }
    for path in _config_paths(config_path):
        endpoints = _apply_models(
            endpoints, _read_config_file(path).get("models"), origin=path.name, origins=origins
        )
    registry, origins = ModelRegistry.from_env_with_origins(defaults=endpoints, base_origins=origins)
    if run_override:
        env_endpoints = {role: registry.endpoint(role) for role in ModelRole}
        merged, origins = apply_model_override(env_endpoints, origins, run_override)
        registry = ModelRegistry.from_endpoints(merged)
    return registry, origins


RUN_OVERRIDE_ORIGIN = "run override"


class ModelOverrideError(ValueError):
    """Raised for an invalid per-run model override (issue #77): an unknown
    role, an unresolvable provider/model, or a resulting HUNTER ==
    VALIDATOR_BUG collision (specs.md §6). Never silently coerced — the caller
    (the API's `POST /runs`) turns this into a 422."""


def apply_model_override(
    endpoints: dict[ModelRole, ModelEndpoint],
    origins: dict[ModelRole, dict[str, str]],
    override: dict[str, dict] | None,
) -> tuple[dict[ModelRole, ModelEndpoint], dict[ModelRole, dict[str, str]]]:
    """Layer a per-run model override (issue #77) on top of already-resolved
    endpoints/origins — the highest-precedence layer, above env vars.

    `override` is ``{role: {"provider": ..., "model": ..., "temperature": ...,
    "base_url": ...}}`` with only the fields the caller wants to change; a
    field left out keeps the role's current effective value. Returns a new
    (endpoints, origins) pair — the inputs are never mutated. Raises
    `ModelOverrideError` rather than ever silently coercing an invalid
    request.
    """
    out = dict(endpoints)
    out_origins = {role: dict(origins.get(role, {})) for role in ModelRole}
    if not override:
        return out, out_origins

    valid_roles = {r.value for r in ModelRole}
    unknown = sorted(set(override) - valid_roles)
    if unknown:
        raise ModelOverrideError(
            f"unknown model role(s): {unknown}; expected one of {sorted(valid_roles)}"
        )

    for role_name, partial in override.items():
        role = ModelRole(role_name)
        cur = out[role]
        partial = partial or {}
        # Only these four fields are ever accepted — in particular, no
        # `api_key`/key material can enter an override (issue #73's absolute
        # no-secrets rule, extended to #77). Reject by field *name* only;
        # never echo a field's value back in the error.
        extra_fields = sorted(set(partial) - set(_TRACKED_FIELDS))
        if extra_fields:
            raise ModelOverrideError(
                f"role {role_name!r}: unexpected field(s) {extra_fields} — only "
                f"{sorted(_TRACKED_FIELDS)} are accepted"
            )
        try:
            provider = (
                Provider.parse(partial["provider"]) if partial.get("provider") is not None
                else cur.provider
            )
        except ValueError as e:
            raise ModelOverrideError(f"role {role_name!r}: {e}") from e

        explicit_model = partial.get("model")
        if explicit_model is not None:
            model = explicit_model
        elif provider is cur.provider:
            model = cur.model
        elif provider is Provider.OPENROUTER:
            raise ModelOverrideError(
                f"role {role_name!r} is routed to openrouter but no model is set — "
                f'add "model" to the override (e.g. anthropic/claude-sonnet-4)'
            )
        else:
            model = default_model_for(provider) or cur.model

        temperature = partial.get("temperature")
        base_url = partial.get("base_url")
        if temperature is None:
            resolved_temperature = cur.temperature
        else:
            try:
                resolved_temperature = float(temperature)
            except (TypeError, ValueError) as e:
                raise ModelOverrideError(
                    f"role {role_name!r}: invalid temperature {temperature!r}: {e}"
                ) from e
            if not (0.0 <= resolved_temperature <= 2.0):
                raise ModelOverrideError(
                    f"role {role_name!r}: temperature {resolved_temperature} out of range [0.0, 2.0]"
                )
        out[role] = ModelEndpoint(
            role=role,
            model=model,
            provider=provider,
            base_url=cur.base_url if base_url is None else base_url,
            api_key=cur.api_key,
            temperature=resolved_temperature,
            top_p=cur.top_p,
            num_ctx=cur.num_ctx,
            num_predict=cur.num_predict,
            seed=cur.seed,
            extra=cur.extra,
        )
        role_origin = out_origins.setdefault(role, {})
        for field, value in (
            ("provider", partial.get("provider")),
            ("model", explicit_model),
            ("temperature", temperature),
            ("base_url", base_url),
        ):
            if value is not None:
                role_origin[field] = RUN_OVERRIDE_ORIGIN

    # Reuse the registry's own construction assertion (specs.md §6) as the
    # single source of truth for "hunter and validator_bug must differ" —
    # rather than duplicating the comparison here.
    try:
        ModelRegistry.from_endpoints(out)
    except RuntimeError as e:
        raise ModelOverrideError(str(e)) from e

    return out, out_origins


def apply_file_tracing_env(config_path: str | os.PathLike | None = None) -> None:
    """Fold a config file's ``tracing:`` block into ``os.environ`` (via
    ``setdefault``, so a real env var / ``.env`` value always wins) so that
    `crucible.obs.setup_tracing` — which only reads env — picks it up.

    API keys are never taken from the file; ``LANGSMITH_API_KEY`` still must be
    set in the environment for LangSmith export to actually run.
    """
    for path in _config_paths(config_path):
        tracing = _read_config_file(path).get("tracing") or {}
        ls = tracing.get("langsmith") or {}
        if ls.get("enabled"):
            os.environ.setdefault("LANGSMITH_TRACING", "true")
        if ls.get("project"):
            os.environ.setdefault("LANGSMITH_PROJECT", str(ls["project"]))
        if ls.get("endpoint"):
            os.environ.setdefault("LANGSMITH_ENDPOINT", str(ls["endpoint"]))
        otel = tracing.get("otel") or {}
        if otel.get("enabled"):
            os.environ.setdefault("CRUCIBLE_OTEL", "1")
        if otel.get("endpoint"):
            os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", str(otel["endpoint"]))
