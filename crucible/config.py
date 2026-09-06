"""Run configuration — model roles, endpoints, and knobs (specs.md §3, §6).

Resolution order (lowest to highest precedence):
  1. `DEFAULT_ENDPOINTS` below
  2. a TOML file (``crucible.toml`` or ``--config PATH``), ``[models.<role>]`` tables
  3. environment variables (see `ModelRegistry.from_env`)

Ollama is the only wired provider. The default models are chosen so that
HUNTER and VALIDATOR_BUG are **different lineages** (the §6 assertion). You must
pull them first, e.g.::

    ollama pull qwen2.5-coder:7b
    ollama pull llama3.1:8b
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from crucible.llm.registry import (
    DEFAULT_OLLAMA_BASE_URL,
    ModelEndpoint,
    ModelRegistry,
    ModelRole,
    Provider,
)

# Hunter lineage (Qwen) != validator lineage (Llama) -> §6 assertion passes.
DEFAULT_ENDPOINTS: dict[ModelRole, ModelEndpoint] = {
    ModelRole.RECON: ModelEndpoint(
        role=ModelRole.RECON, model="qwen2.5-coder:7b", temperature=0.1, num_ctx=16384
    ),
    ModelRole.HUNTER: ModelEndpoint(
        role=ModelRole.HUNTER, model="qwen2.5-coder:7b", temperature=0.3, num_ctx=16384
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


def _apply_toml(endpoints: dict[ModelRole, ModelEndpoint], path: Path) -> dict[ModelRole, ModelEndpoint]:
    data = tomllib.loads(path.read_text())
    models = data.get("models", {})
    out = dict(endpoints)
    for role in ModelRole:
        tbl = models.get(role.value)
        if not tbl:
            continue
        cur = out[role]
        out[role] = ModelEndpoint(
            role=role,
            model=tbl.get("model", cur.model),
            provider=Provider(tbl.get("provider", cur.provider.value)),
            base_url=tbl.get("base_url", cur.base_url),
            temperature=float(tbl.get("temperature", cur.temperature)),
            top_p=float(tbl.get("top_p", cur.top_p)),
            num_ctx=int(tbl.get("num_ctx", cur.num_ctx)),
            num_predict=int(tbl.get("num_predict", cur.num_predict)),
            seed=tbl.get("seed", cur.seed),
            extra=tbl.get("extra", cur.extra),
        )
    return out


def load_registry(config_path: str | os.PathLike | None = None) -> ModelRegistry:
    """DEFAULT_ENDPOINTS -> TOML (if present) -> env overrides -> ModelRegistry."""
    endpoints = dict(DEFAULT_ENDPOINTS)
    path = Path(config_path) if config_path else Path("crucible.toml")
    if path.is_file():
        endpoints = _apply_toml(endpoints, path)
    return ModelRegistry.from_env(defaults=endpoints)
