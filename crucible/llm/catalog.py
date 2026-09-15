"""The allowed-model catalog (issue #80).

Crucible only talks to OpenRouter (see `registry.py`) — one hosted key, any
model, so "which provider" is no longer a question the UI or config needs to
ask. What *is* still a question, per role, is "which model" — and a free-text
field for that is how a typo silently degrades a validator to "upheld,
not reviewed" instead of failing loudly (see validate_bug.py / issue #77
history). This module is the fix: a small, curated table of known-good
OpenRouter model ids, each tagged with a `family` (for grouping in the UI)
and a `size` tier (small/mid/big) so a role can trade cost against capability
without leaving the catalog.

Deliberately narrow for now — DeepSeek, Qwen, and GLM, picked for being
cheap, capable, and (crucially) different lineages from one another, so a
hunter/validator_bug pairing drawn from different families keeps specs.md
§6's "structurally different models" property. Extending to another family
later is a one-table edit here; nothing else needs to change.

Ids are real OpenRouter model ids (verified against `GET /api/v1/models`
at https://openrouter.ai/api/v1/models) — not aliases or guesses. Every entry
here has also been live-tested against OpenRouter with a *forced*
`tool_choice` call — every role in this codebase (`recon.py`, `hunt.py`,
`dedup.py`, `validate_bug.py`, `validate_reachability.py`) emits its
structured output that way, so a model that can't do forced tool-choice is
unusable here regardless of how good it otherwise is. Ids tried and dropped
for exactly that reason: `qwen/qwen-2.5-coder-32b-instruct` and
`qwen/qwen3-8b` (no OpenRouter endpoint supports tool use for either at all)
and `qwen/qwen3-235b-a22b-thinking-2507` (rejects a forced `tool_choice`
while in its "thinking mode"). Re-verify a new entry against forced
tool-choice before adding it, not just plain chat.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogModel:
    id: str      # OpenRouter model id, e.g. "deepseek/deepseek-chat-v3.1"
    label: str   # short human label for the UI
    family: str  # "deepseek" | "qwen" | "glm"
    size: str    # "small" | "mid" | "big" — rough cost/capability tier


CATALOG: list[CatalogModel] = [
    # --- DeepSeek -----------------------------------------------------
    CatalogModel("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek", "small"),
    CatalogModel("deepseek/deepseek-chat-v3.1", "DeepSeek Chat V3.1", "deepseek", "mid"),
    CatalogModel("deepseek/deepseek-r1-0528", "DeepSeek R1 (0528)", "deepseek", "big"),
    CatalogModel("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek", "big"),
    # --- Qwen -----------------------------------------------------------
    CatalogModel("qwen/qwen3-14b", "Qwen3 14B", "qwen", "small"),
    CatalogModel("qwen/qwen3-30b-a3b", "Qwen3 30B A3B", "qwen", "mid"),
    CatalogModel("qwen/qwen3-32b", "Qwen3 32B", "qwen", "mid"),
    CatalogModel("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B Instruct", "qwen", "big"),
    CatalogModel("qwen/qwen3-235b-a22b-2507", "Qwen3 235B", "qwen", "big"),
    # --- GLM --------------------------------------------------------------
    CatalogModel("z-ai/glm-4.7-flash", "GLM 4.7 Flash", "glm", "small"),
    CatalogModel("z-ai/glm-4.7", "GLM 4.7", "glm", "mid"),
    CatalogModel("z-ai/glm-5.1", "GLM 5.1", "glm", "mid"),
    CatalogModel("z-ai/glm-5.3", "GLM 5.3", "glm", "big"),
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
