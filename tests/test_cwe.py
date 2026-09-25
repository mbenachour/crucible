"""Deterministic CWE tagging (issue #94)."""

from __future__ import annotations

import json
from pathlib import Path

from crucible.validation.cwe import ATTACK_CLASS_CWE, CWE_NAMES, cwe_for_attack_class, cwe_label

_ATTACK_CLASSES_DIR = Path(__file__).resolve().parents[1] / "crucible" / "skills" / "attack_classes"


def test_every_mapped_class_is_a_real_attack_class_file():
    """A stale/typo'd key would silently never fire — catch it here instead."""
    on_disk = {p.stem for p in _ATTACK_CLASSES_DIR.glob("*.md")}
    assert set(ATTACK_CLASS_CWE) <= on_disk


def test_every_mapped_cwe_has_a_name():
    """cwe_label falls back to the bare id when a name is missing — make sure
    that fallback is never actually needed for an id this module emits."""
    for cwe_id in ATTACK_CLASS_CWE.values():
        assert cwe_id in CWE_NAMES, f"{cwe_id} has no entry in CWE_NAMES"


def test_known_attack_class_resolves():
    assert cwe_for_attack_class("sql_injection") == "CWE-89"
    assert cwe_for_attack_class("insecure_storage") == "CWE-312"
    assert cwe_for_attack_class("path_traversal") == "CWE-22"


def test_deliberately_unmapped_class_resolves_to_none():
    # Too broad/ambiguous to map confidently — see the module docstring.
    for cls in ("misconfiguration", "protocol_parsing", "dynamic_dispatch",
                "api_misuse", "webview_injection"):
        assert cwe_for_attack_class(cls) is None


def test_repo_invented_class_never_guesses():
    """Recon can invent a repo-specific attack class on the spot (specs.md
    §9.1) — free text, not in the table. Must resolve to None, never a
    lookalike/nearest-match guess."""
    assert cwe_for_attack_class("v_html_xss_via_marked_sanitizer") is None
    assert cwe_for_attack_class("") is None


def test_cwe_label_formats_with_and_without_a_name():
    assert cwe_label("CWE-312") == "CWE-312 - Cleartext Storage of Sensitive Information"
    assert cwe_label("CWE-999999") == "CWE-999999"  # unknown id — bare, no crash
    assert cwe_label(None) == ""


def test_persist_finding_attaches_deterministic_cwe(tmp_path):
    """hunt._persist_finding writes `cwe` into the finding's payload — derived
    from attack_class, never asked of the model (the Finding schema itself
    has no cwe field)."""
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

    (tmp_path / "findings").mkdir()
    _persist_finding(
        store=None, ws=tmp_path, run_id="r1", fid="h0001-abcdef", f=f,
        attack_class="sql_injection", prompt_version="sql_injection@1.0.0", registry=None,
    )

    written = json.loads((tmp_path / "findings" / "h0001-abcdef.json").read_text())
    assert written["cwe"] == "CWE-89"


def test_persist_finding_leaves_cwe_none_for_unmapped_class(tmp_path):
    from crucible.graph.nodes.hunt import _persist_finding
    from crucible.validation.schema import Finding

    f = Finding.model_validate({
        "threat_model": {
            "attacker": "unauthenticated remote client",
            "boundary_crossed": "config -> runtime behavior",
            "assumption_broken": "default is secure",
        },
        "title": "insecure default",
        "file_path": "app.py",
        "line_start": 1,
        "line_end": 1,
        "description": "bad default config",
        "poc_test": "def test_it():\n    assert True",
        "proposed_patch": "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-bad\n+good\n",
        "severity": "low",
    })

    (tmp_path / "findings").mkdir()
    _persist_finding(
        store=None, ws=tmp_path, run_id="r1", fid="h0002-abcdef", f=f,
        attack_class="misconfiguration", prompt_version="misconfiguration@1.0.0", registry=None,
    )

    written = json.loads((tmp_path / "findings" / "h0002-abcdef.json").read_text())
    assert written["cwe"] is None
