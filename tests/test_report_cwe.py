"""report.py renders the deterministic CWE label when present (issue #94)."""

from __future__ import annotations

from crucible.graph.nodes.report import _markdown

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


def test_markdown_shows_cwe_label_when_present():
    report = {**_BASE_REPORT, "findings": [_finding()]}
    md = _markdown(report)
    assert "CWE-89 - SQL Injection" in md


def test_markdown_omits_cwe_when_unmapped():
    report = {**_BASE_REPORT, "findings": [_finding(cwe=None)]}
    md = _markdown(report)
    assert "CWE-" not in md
