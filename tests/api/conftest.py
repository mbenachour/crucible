"""Hermetic fixtures for the API contract suite (issue #46).

No network, no Docker. A seeded `findings.sqlite` + a fixture workspace tree +
a `TestClient`. Mirrors the pattern in `tests/conftest.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from crucible.store.dao import Store, stable_key
from crucible.store.models import FindingRow, ValidationRow, WishRow

RUN_A = "aaaaaaaaaaaa"
RUN_B = "bbbbbbbbbbbb"
SHARED_KEY = stable_key("app/app.py", "login", "http->db")


def _payload(title, sev, *, file="app/app.py", ls=10, le=20, dup=None):
    p = {
        "threat_model": {
            "attacker": "unauthenticated remote client",
            "boundary_crossed": "HTTP body -> query",
            "assumption_broken": "input treated as inert string",
        },
        "title": title,
        "file_path": file,
        "line_start": ls,
        "line_end": le,
        "description": f"{title} — details",
        "poc_test": "def test_it():\n    assert False",
        "proposed_patch": "--- a/x\n+++ b/x\n@@\n-bad\n+good\n",
        "severity": sev,
    }
    if dup:
        p["duplicate_of"] = dup
    return p


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "findings.sqlite"
    st = Store(f"sqlite:///{db}")
    now = datetime.now(UTC)

    st.create_run(RUN_A, "/repos/vuln-app", "deadbeef1234", "python", str(tmp_path / "ws_a"))
    st.create_run(RUN_B, "/repos/other-app", "cafebabe5678", "javascript", str(tmp_path / "ws_b"))
    st.finish_run(RUN_A, "completed", str(tmp_path / "ws_a" / "report.json"))

    rows = [
        # run A
        ("A-crit", RUN_A, "reach_upheld", "critical", "SQLi in /search", "sql_injection@1.0.0", SHARED_KEY),
        ("A-high", RUN_A, "bug_upheld", "high", "SSTI in error handler", "template_injection@1.0.0", "k2"),
        ("A-med", RUN_A, "mechanical_failed", "medium", "path traversal maybe", "path_traversal@1.0.0", "k3"),
        ("A-low", RUN_A, "raw", "low", "verbose error", "misconfiguration@1.0.0", "k4"),
        ("A-dupe", RUN_A, "duplicate", "high", "SSTI (dupe)", "template_injection@1.0.0", "k2"),
        # run B — shares SHARED_KEY with A-crit
        ("B-crit", RUN_B, "reach_upheld", "critical", "SQLi again", "sql_injection@1.0.0", SHARED_KEY),
    ]
    for i, (fid, rid, status, sev, title, pv, sk) in enumerate(rows):
        dup = "A-high" if fid == "A-dupe" else None
        st.add_finding(FindingRow(
            finding_id=fid, run_id=rid, stable_key=sk,
            payload=_payload(title, sev, dup=dup),
            status=status,
            hunter_model="deepseek/deepseek-chat",
            hunter_prompt_version=pv,
            hunter_sampling={"temperature": 0.3},
            created_at=now - timedelta(minutes=len(rows) - i),
        ))

    st.record_validation(ValidationRow(
        finding_id="A-crit", pass_name="mechanical", verdict="mechanical_passed",
        reasoning="patch applies; poc parses", created_at=now,
    ))
    st.record_validation(ValidationRow(
        finding_id="A-crit", pass_name="bug", verdict="upheld",
        reasoning="confirmed: user input reaches raw SQL", model="openai/gpt-4o",
        prompt_version="1.0.0", response_class="ok", created_at=now + timedelta(seconds=1),
    ))
    st.record_validation(ValidationRow(
        finding_id="A-crit", pass_name="reachability", verdict="upheld",
        reasoning="route is unauthenticated", model="google/gemini-2.0-flash",
        created_at=now + timedelta(seconds=2),
    ))
    st.record_validation(ValidationRow(
        finding_id="A-med", pass_name="mechanical", verdict="mechanical_failed",
        reasoning="patch does not apply cleanly: corrupt patch at line 3", created_at=now,
    ))

    st.add_wish(WishRow(run_id=RUN_A, blocked_task_id="h0007", need="a mongodb instance",
                        context="cannot run the PoC without a live DB", status="open"))
    st.add_wish(WishRow(run_id=RUN_A, blocked_task_id="h0009", need="build toolchain",
                        context="C fixture won't compile", status="resolved"))
    st.add_wish(WishRow(run_id=RUN_B, blocked_task_id="h0002", need="prod config",
                        context="need the real feature flags", status="open"))

    st.flush_tool_usage([
        {"run_id": RUN_A, "role": "hunter", "tool_name": "sandbox_exec", "count": 12, "latency_s": 0, "errors": 1},
        {"run_id": RUN_A, "role": "hunter", "tool_name": "read_file", "count": 40, "latency_s": 0, "errors": 0},
        {"run_id": RUN_A, "role": "hunter", "tool_name": "fork_sibling", "count": 3, "latency_s": 0, "errors": 0},
    ])
    return st


@pytest.fixture
def workspace(tmp_path):
    """A fixture workspace tree for RUN_A, mirroring a real run."""
    ws = tmp_path / "ws_a"
    (ws / "recon").mkdir(parents=True)
    (ws / "coverage").mkdir()
    (ws / "dedup").mkdir()
    (ws / "findings").mkdir()
    (ws / "offload").mkdir()

    (ws / "architecture.md").write_text("# Architecture\n\n## 1 Overview\ndemo\n")
    report = {
        "run_id": RUN_A, "repo": "/repos/vuln-app", "repo_commit": "deadbeef1234",
        "language": "python", "generated_at": "2026-09-10T00:00:00Z", "recon_quality": "full",
        "counts": {"total": 5, "upheld": 1, "reach_upheld": 1},
        "metrics": {"cycles": 2, "continuations": 3, "fork_count": 3, "token_spend": 123456,
                    "fork_rate": "3/12", "tool_usage": {}},
        "findings": [],
    }
    (ws / "report.json").write_text(json.dumps(report, indent=2))
    (ws / "report.md").write_text("# Security report — /repos/vuln-app\n\n## Upheld findings (1)\n")
    (ws / "recon" / "seed.json").write_text(json.dumps({"repo_kind": "web_api", "entry_points": 4}))
    (ws / "recon" / "module_map.json").write_text(json.dumps({"subsystems": [{"name": "api"}]}))
    (ws / "recon" / "threat_model.json").write_text(json.dumps({"attackers": ["remote"], "stride": []}))
    (ws / "recon" / "attack_surface.json").write_text(json.dumps([{"target": "app/app.py:10", "score": 15}]))
    (ws / "recon" / "task_manifest.json").write_text(json.dumps({"chunks": [
        {"area": "api", "attack_class": "sql_injection", "chunk_type": "taint"},
        {"area": "api", "attack_class": "template_injection", "chunk_type": "surface"},
        {"area": "api", "attack_class": "path_traversal", "chunk_type": "catch_all"},
        {"area": "core", "attack_class": "auth_bypass", "chunk_type": "catch_all"},
    ]}))
    (ws / "dedup" / "clusters.json").write_text(json.dumps({"clusters": [["A-high", "A-dupe"]]}))
    (ws / "coverage" / "api.md").write_text(
        "## h0000 · `sql_injection` · taint · 2026-09-10 00:00:00\n"
        "- **finding** F1\n\n"
        "## h0001 · `template_injection` · surface · 2026-09-10 00:01:00\n"
        "- negative: guarded\n"
    )
    (ws / "findings" / "A-crit.json").write_text(json.dumps(_payload("SQLi in /search", "critical")))
    (ws / "offload" / "call-1.txt").write_text("full tool output ...\n")
    (ws / "run.log").write_text("line1\nline2\nline3\nline4\nline5\n")

    # make it a git checkout so artifact_index uses the git path
    import subprocess
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "seed"]):
        subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)
    return ws


@pytest.fixture
def client(store, workspace, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    settings = ApiSettings(
        store_url=str(store.engine.url),
        checkpoint_db=str(tmp_path / "checkpoints.sqlite"),
        workspace_root=str(tmp_path / "ws_a"),
        serve_ui=False,  # API contract tests are independent of whether ui/ is built
    )
    app = create_app(settings)
    app.state.store = store  # reuse the seeded store instance
    return TestClient(app, raise_server_exceptions=False)
