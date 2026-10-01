"""report.py renders the deterministic, DB-backed CWE label when present
(issue #94)."""

from __future__ import annotations

from crucible.graph.nodes.report import _markdown
from crucible.store.dao import Store

_BASE_REPORT = {
    "repo": "/repo",
    "run_id": "r1",
    "repo_commit": "a" * 40,
    "language": "py",
    "generated_at": "2026-01-01T00:00:00Z",
    "recon_quality": "full",
    "counts": {"total": 1, "upheld": 1},
    "metrics": {"cycles": 1, "fork_rate": "0/1", "token_spend": 0},
}


def _finding(**overrides):
    f = {
        "finding_id": "h0001-abcdef",
        "severity": "high",
        "title": "SQL injection in search handler",
        "cwe": "CWE-89",
        "file_path": "app.py",
        "line_start": 10,
        "line_end": 12,
        "threat_model": {
            "attacker": "unauthenticated remote client",
            "boundary_crossed": "HTTP body -> DB query",
            "assumption_broken": "input treated as inert string",
        },
        "description": "user-controlled term reaches raw SQL",
        "poc_test": "def test_it():\n    assert True",
        "proposed_patch": "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-bad\n+good\n",
        "validation_trail": [],
    }
    f.update(overrides)
    return f


def test_markdown_shows_cwe_label_when_present(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    report = {**_BASE_REPORT, "findings": [_finding()]}
    md = _markdown(report, store)
    assert "CWE-89 - SQL Injection" in md


def test_markdown_omits_cwe_when_unmapped(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/f.sqlite")
    report = {**_BASE_REPORT, "findings": [_finding(cwe=None)]}
    md = _markdown(report, store)
    assert "CWE-" not in md


def test_markdown_falls_back_to_bare_id_without_a_store():
    report = {**_BASE_REPORT, "findings": [_finding()]}
    md = _markdown(report)  # no store — e.g. a deterministic/no-registry run
    assert "CWE-89" in md


def test_patch_emission_metric_counts_how_patches_were_built():
    """issue #111: first_try / repaired / unbuilt; pre-#111 findings not counted."""
    from types import SimpleNamespace

    from crucible.graph.nodes.report import _patch_emission

    def row(patch, emit):
        p = {"proposed_patch": patch}
        if emit is not None:
            p["emit"] = emit
        return SimpleNamespace(payload=p)

    rows = [
        row("diff", {"repairs": 0, "patch_errors": []}),
        row("diff", {"repairs": 1, "patch_errors": []}),
        row("diff", {"repairs": 0, "patch_errors": ["app.py: unchanged"]}),
        row("", {"repairs": 0, "patch_errors": ["x"]}),
        row("old hand-written diff", None),
    ]
    assert _patch_emission(rows) == {"first_try": 1, "repaired": 2, "unbuilt": 1}
