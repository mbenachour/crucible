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


def _apply_models(
    endpoints: dict[ModelRole, ModelEndpoint], models: dict[str, Any] | None
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
    return out


def load_registry(config_path: str | os.PathLike | None = None) -> ModelRegistry:
    """DEFAULT_ENDPOINTS -> file config (yaml/toml) -> env overrides -> registry."""
    endpoints = dict(DEFAULT_ENDPOINTS)
    for path in _config_paths(config_path):
        endpoints = _apply_models(endpoints, _read_config_file(path).get("models"))
    return ModelRegistry.from_env(defaults=endpoints)


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
