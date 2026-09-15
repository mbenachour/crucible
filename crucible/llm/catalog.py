"""The allowed-model catalog (issue #80).

Crucible only talks to OpenRouter (see `registry.py`) — one hosted key, any
model, so "which provider" is no longer a question the UI or config needs to
ask. What *is* still a question, per role, is "which model" — and a free-text
field for that is how a typo silently degrades a validator to "upheld,
not reviewed" instead of failing loudly (see validate_bug.py / issue #77
history). This module is the fix: a small, curated table of known-good
OpenRouter model ids, each tagged with a `family` for grouping in the UI.

Deliberately narrow for now — DeepSeek and Qwen only, picked for being cheap,
capable, and (crucially) different lineages from one another, so a
hunter/validator_bug pairing drawn from different families keeps specs.md
§6's "structurally different models" property. Extending to another family
later is a one-table edit here; nothing else needs to change.

Ids are real OpenRouter model ids (verified against `GET /api/v1/models`
at https://openrouter.ai/api/v1/models) — not aliases or guesses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogModel:
    id: str            # OpenRouter model id, e.g. "deepseek/deepseek-chat-v3.1"
    label: str          # short human label for the UI
    family: str          # "deepseek" | "qwen"


CATALOG: list[CatalogModel] = [
    # --- DeepSeek ---------------------------------------------------------
    CatalogModel("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek"),
    CatalogModel("deepseek/deepseek-v3.2", "DeepSeek V3.2", "deepseek"),
    CatalogModel("deepseek/deepseek-chat-v3.1", "DeepSeek Chat V3.1", "deepseek"),
    CatalogModel("deepseek/deepseek-r1-0528", "DeepSeek R1 (0528)", "deepseek"),
    CatalogModel("deepseek/deepseek-v3.1-terminus", "DeepSeek V3.1 Terminus", "deepseek"),
    # --- Qwen ---------------------------------------------------------
    CatalogModel("qwen/qwen-2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B", "qwen"),
    CatalogModel("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B Instruct", "qwen"),
    CatalogModel("qwen/qwen3-235b-a22b-thinking-2507", "Qwen3 235B Thinking", "qwen"),
    CatalogModel("qwen/qwen3-32b", "Qwen3 32B", "qwen"),
    CatalogModel("qwen/qwen3-30b-a3b", "Qwen3 30B A3B", "qwen"),
]

_BY_ID = {m.id: m for m in CATALOG}


def catalog_model(model_id: str) -> CatalogModel | None:
    """Look up a catalog entry by OpenRouter model id, or None if it isn't
    in the curated list."""
    return _BY_ID.get(model_id)


def is_allowed(model_id: str) -> bool:
    return model_id in _BY_ID


def families() -> dict[str, list[CatalogModel]]:
    """The catalog grouped by family, in table order — what the Settings /
    New Run dropdowns render as `<optgroup>`s."""
    out: dict[str, list[CatalogModel]] = {}
    for m in CATALOG:
        out.setdefault(m.family, []).append(m)
    return out
