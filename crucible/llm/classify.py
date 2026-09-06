"""Response classification — runs before parsing, every call (specs.md §6, §1.10).

Classify response TEXT, not exception types. Transient errors arrive inside
200 OK; an unclassified response is a failed task, not a clean one.

| Class            | Trigger                                  | Handling                              |
|------------------|------------------------------------------|--------------------------------------|
| ok               | Parses, non-empty, conformant            | Proceed                              |
| transient_error  | Error text in body, empty, truncated     | Retry with backoff, capped          |
| refusal          | Model declines                           | Log, fail task. No silent rephrase. |
| malformed        | Unparseable despite constraints          | One repair attempt, then fail       |

Track refusal rate as a product metric (§6): low refusal on open weights is a
stated differentiator.
"""

from __future__ import annotations

import re
from enum import Enum

MAX_TRANSIENT_RETRIES = 3
MAX_REPAIR_ATTEMPTS = 1


class ResponseClass(str, Enum):
    OK = "ok"
    TRANSIENT_ERROR = "transient_error"
    REFUSAL = "refusal"
    MALFORMED = "malformed"


_REFUSAL_PATTERNS = [
    r"\bI can(?:'|no)t (?:help|assist|comply)\b",
    r"\bI(?:'m| am) (?:not able|unable) to\b",
    r"\bagainst my (?:guidelines|policy)\b",
    r"\bI won'?t (?:be able to )?(?:help|assist|provide)\b",
]
_TRANSIENT_PATTERNS = [
    r"\b(?:rate.?limit|429|502|503|504|upstream error|gateway timeout)\b",
    r"\binternal server error\b",
    r"\bmodel (?:overloaded|is currently overloaded)\b",
]


def classify(body: str, *, expect_json: bool = True) -> ResponseClass:
    text = (body or "").strip()
    if not text:
        return ResponseClass.TRANSIENT_ERROR

    lowered = text.lower()
    if any(re.search(p, lowered) for p in _TRANSIENT_PATTERNS):
        return ResponseClass.TRANSIENT_ERROR
    if any(re.search(p, text, re.IGNORECASE) for p in _REFUSAL_PATTERNS):
        return ResponseClass.REFUSAL

    if expect_json and not _looks_like_json(text):
        return ResponseClass.MALFORMED
    return ResponseClass.OK


def _looks_like_json(text: str) -> bool:
    import json

    candidate = text
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = re.sub(r"^json\s*", "", candidate, flags=re.IGNORECASE).strip()
    try:
        json.loads(candidate)
        return True
    except ValueError:
        return False
