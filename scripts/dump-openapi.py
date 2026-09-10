#!/usr/bin/env python
"""Regenerate tests/data/openapi.json (issue #46).

    python scripts/dump-openapi.py

The committed snapshot is diffed by tests/api/test_openapi.py — a change to the
API contract must be intentional and show up in review.
"""

from __future__ import annotations

import json
from pathlib import Path

from crucible.api.app import create_app
from crucible.api.settings import ApiSettings

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "openapi.json"


def main() -> None:
    app = create_app(ApiSettings(store_url="sqlite:///:memory:"))
    OUT.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(app.openapi()['paths'])} paths)")


if __name__ == "__main__":
    main()
