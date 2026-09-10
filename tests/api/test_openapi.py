"""The OpenAPI contract is committed; a change must be intentional (issue #46)."""

from __future__ import annotations

import json
from pathlib import Path

from crucible.api.app import create_app
from crucible.api.settings import ApiSettings

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "openapi.json"


def _current() -> dict:
    app = create_app(ApiSettings(store_url="sqlite:///:memory:"))
    return json.loads(json.dumps(app.openapi(), sort_keys=True))


def test_openapi_matches_snapshot():
    saved = json.loads(SNAPSHOT.read_text())
    current = _current()
    assert current == saved, (
        "OpenAPI schema drifted — run `python scripts/dump-openapi.py` and review the diff"
    )
