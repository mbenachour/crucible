"""Correct counterpart to fixture-py/app/cache.py — JSON, not pickle; schema
checked before use. No planted bug (specs.md §12).
"""

import json


def load_entry(blob: bytes) -> dict:
    data = json.loads(blob)
    if not isinstance(data, dict) or "value" not in data:
        raise ValueError("bad cache entry")
    return data


def get(cache, key: str) -> dict:
    return load_entry(cache.get_raw(key))
