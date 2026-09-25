"""Deterministic, DB-backed CWE tagging (issue #94).

The mapping lives in the `attack_class_cwe` table, seeded once from
`dao._DEFAULT_ATTACK_CLASS_CWE` the first time a Store opens an empty DB,
then loaded into memory at Store construction — never re-derived from code
after that first seed, and never touched by an LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

from types import SimpleNamespace

from crucible.store.dao import Store, _DEFAULT_ATTACK_CLASS_CWE
from crucible.store.models import AttackClassCweRow

_FAKE_ENDPOINT = SimpleNamespace(provider=SimpleNamespace(value="openrouter"), model="fake-model")
_FAKE_REGISTRY = SimpleNamespace(
    endpoint=lambda role: _FAKE_ENDPOINT,
    sampling_params=lambda role: {"provider": "openrouter", "model": "fake-model"},
)

_ATTACK_CLASSES_DIR = Path(__file__).resolve().parents[1] / "crucible" / "skills" / "attack_classes"


def test_every_mapped_class_is_a_real_attack_class_file():
    """A stale/typo'd key would silently never fire — catch it here instead."""
    on_disk = {p.stem for p in _ATTACK_CLASSES_DIR.glob("*.md")}
    assert set(_DEFAULT_ATTACK_CLASS_CWE) <= on_disk


def test_opening_a_fresh_db_seeds_the_table(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    with store.session() as s:
        rows = s.query(AttackClassCweRow).all()
    assert {r.attack_class for r in rows} == set(_DEFAULT_ATTACK_CLASS_CWE)


def test_known_attack_class_resolves(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    assert store.cwe_for_attack_class("sql_injection") == "CWE-89"
    assert store.cwe_for_attack_class("insecure_storage") == "CWE-312"
    assert store.cwe_for_attack_class("path_traversal") == "CWE-22"


def test_deliberately_unmapped_class_resolves_to_none(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    # Too broad/ambiguous to map confidently — see dao._DEFAULT_ATTACK_CLASS_CWE.
    for cls in ("misconfiguration", "protocol_parsing", "dynamic_dispatch",
                "api_misuse", "webview_injection"):
        assert store.cwe_for_attack_class(cls) is None


def test_repo_invented_class_never_guesses(tmp_path):
    """Recon can invent a repo-specific attack class on the spot (specs.md
    §9.1) — free text, not in the table. Must resolve to None, never a
    lookalike/nearest-match guess."""
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    assert store.cwe_for_attack_class("v_html_xss_via_marked_sanitizer") is None
    assert store.cwe_for_attack_class("") is None


def test_cwe_label_formats_with_and_without_a_name(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    assert store.cwe_label("CWE-312") == "CWE-312 - Cleartext Storage of Sensitive Information"
    assert store.cwe_label("CWE-999999") == "CWE-999999"  # unknown id — bare, no crash
    assert store.cwe_label(None) == ""


def test_a_hand_edited_row_is_never_overwritten_by_reseeding(tmp_path):
    """The whole point of moving this into the DB: an operator can fix or
    extend a mapping without a code change, and it has to stick."""
    db = tmp_path / "f.sqlite"
    store1 = Store(f"sqlite:///{db}")
    with store1.session() as s:
        s.query(AttackClassCweRow).filter_by(attack_class="sql_injection").update(
            {"cwe_id": "CWE-943", "cwe_name": "Improper Neutralization of Special Elements in Data Query Logic"}
        )

    # A second Store opening the SAME db (e.g. the next `crucible run`, or
    # the API server restarting) must load the edited row, not reseed over it.
    store2 = Store(f"sqlite:///{db}")
    assert store2.cwe_for_attack_class("sql_injection") == "CWE-943"


def test_persist_finding_attaches_deterministic_cwe(tmp_path):
    """hunt._persist_finding writes `cwe` into the finding's payload — read
    from the store's cache, never asked of the model (the Finding schema
    itself has no cwe field)."""
    from crucible.graph.nodes.hunt import _persist_finding
    from crucible.validation.schema import Finding

    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    f = Finding.model_validate({
        "threat_model": {
            "attacker": "unauthenticated remote client",
            "boundary_crossed": "HTTP body -> DB query",
            "assumption_broken": "input treated as inert string",
        },
        "title": "SQL injection in search handler",
        "file_path": "app.py",
        "line_start": 10,
        "line_end": 12,
        "description": "user-controlled term reaches raw SQL",
        "poc_test": "def test_it():\n    assert True",
        "proposed_patch": "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-bad\n+good\n",
        "severity": "high",
    })

    ws = tmp_path / "ws"
    (ws / "findings").mkdir(parents=True)
    _persist_finding(
        store=store, ws=ws, run_id="r1", fid="h0001-abcdef", f=f,
        attack_class="sql_injection", prompt_version="sql_injection@1.0.0", registry=_FAKE_REGISTRY,
    )

    written = json.loads((ws / "findings" / "h0001-abcdef.json").read_text())
    assert written["cwe"] == "CWE-89"


def test_persist_finding_leaves_cwe_none_without_a_store(tmp_path):
    """No store (deterministic/no-registry path) -> no lookup, no CWE — the
    DB is the only source of truth now, so there's nothing to fall back to."""
    from crucible.graph.nodes.hunt import _persist_finding
    from crucible.validation.schema import Finding

    f = Finding.model_validate({
        "threat_model": {
            "attacker": "unauthenticated remote client",
            "boundary_crossed": "HTTP body -> DB query",
            "assumption_broken": "input treated as inert string",
        },
        "title": "SQL injection in search handler",
        "file_path": "app.py",
        "line_start": 10,
        "line_end": 12,
        "description": "user-controlled term reaches raw SQL",
        "poc_test": "def test_it():\n    assert True",
        "proposed_patch": "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-bad\n+good\n",
        "severity": "high",
    })

    ws = tmp_path / "ws"
    (ws / "findings").mkdir(parents=True)
    _persist_finding(
        store=None, ws=ws, run_id="r1", fid="h0001-abcdef", f=f,
        attack_class="sql_injection", prompt_version="sql_injection@1.0.0", registry=None,
    )

    written = json.loads((ws / "findings" / "h0001-abcdef.json").read_text())
    assert written["cwe"] is None
