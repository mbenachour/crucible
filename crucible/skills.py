"""Load prompt skills from `crucible/skills/` (specs.md §4, §7).

Prompts are versioned files with YAML front-matter. `load_skill` returns the
methodology body (front-matter stripped). Progressive disclosure — callers load
only what they need.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import crucible

_SKILLS_DIR = Path(crucible.__file__).parent / "skills"


@lru_cache(maxsize=64)
def load_skill(rel_path: str) -> str:
    """`rel_path` like ``recon/map.md`` or ``attack_classes/command_injection.md``."""
    text = (_SKILLS_DIR / rel_path).read_text()
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return text.strip()


@lru_cache(maxsize=64)
def skill_front_matter(rel_path: str) -> dict[str, str]:
    """Parse the simple ``key: value`` front-matter (no nested YAML)."""
    text = (_SKILLS_DIR / rel_path).read_text()
    if not text.lstrip().startswith("---"):
        return {}
    fm = text.split("---", 2)[1]
    out: dict[str, str] = {}
    for line in fm.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out
