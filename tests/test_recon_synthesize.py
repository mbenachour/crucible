"""R1c synthesis + architecture.md render — deterministic, no model (issue #34).

Covers the issue's acceptance bullets: attack-surface ranking, auth-model
derivation, the 11-section doc structure, and the degradation banner.
"""

from __future__ import annotations

from crucible.recon.decompose import ARCHITECTURE_SECTIONS, render_architecture, subsystem_rows
from crucible.recon.schema import (
    EntryPoint,
    EntryPointKind,
    FileEntry,
    ModuleMap,
    RepoKind,
    Seed,
    StrideThreat,
    SubsystemMap,
    ThreatModel,
)
from crucible.recon.seed import build_seed
from crucible.recon.synthesize import (
    cross_subsystem_flows,
    derive_auth_model,
    owner_of,
    rank_attack_surface,
    stitch_data_flows,
)


def _seed(**kw) -> Seed:
    base = {
        "repo_path": "/x",
        "primary_language": "python",
        "repo_kind": RepoKind.WEB_API,
        "files": [
            FileEntry(path="api/views.py", language="python", loc=120, role="source"),
            FileEntry(path="core/db.py", language="python", loc=80, role="source"),
        ],
        "stats": {"files": 2, "source_files": 2, "call_edges": 4},
    }
    base.update(kw)
    return Seed(**base)


PARTITION = [
    {"name": "api", "responsibility": "HTTP layer", "external_facing": True,
     "paths": ["api"], "files": ["api/views.py"], "depends_on": ["core"]},
    {"name": "core", "responsibility": "persistence", "external_facing": False,
     "paths": ["core"], "files": ["core/db.py"], "depends_on": []},
]


# --------------------------------------------------------------- ownership


def test_owner_of_longest_prefix_then_misc():
    assert owner_of(PARTITION, "api/views.py") == "api"
    assert owner_of(PARTITION, "core/db.py") == "core"
    assert owner_of(PARTITION, "scripts/deploy.py") == "misc"
    assert owner_of(None, "api/views.py") == "misc"


# ----------------------------------------------------------- attack surface


def test_external_entry_outranks_internal_of_same_kind():
    seed = _seed(entry_points=[
        EntryPoint(kind=EntryPointKind.FILE, file="api/views.py", line=10),
        EntryPoint(kind=EntryPointKind.FILE, file="core/db.py", line=10),
    ])
    surf = rank_attack_surface(seed, [], None, None, PARTITION)
    assert surf[0].subsystem == "api" and surf[0].exposure == "external"
    assert surf[-1].subsystem == "core" and surf[-1].exposure == "internal"
    assert surf[0].score > surf[-1].score


def test_sink_proximity_and_threat_model_raise_score():
    ep = EntryPoint(kind=EntryPointKind.FRAMEWORK, file="api/views.py", line=10, framework="flask")
    bare = _seed(entry_points=[ep])
    loud = _seed(
        entry_points=[ep],
        reflection_facts=[{"file": "api/views.py", "line": 40, "kind": "eval_exec", "snippet": "eval(x)"}],
    )
    tm = ThreatModel(stride=[StrideThreat(
        entry_point="api/views.py:10", category="tampering", description="d", attacker="remote")])
    s_bare = rank_attack_surface(bare, [], None, None, PARTITION)[0].score
    s_sink = rank_attack_surface(loud, [], None, None, PARTITION)[0].score
    s_both = rank_attack_surface(loud, [], None, tm, PARTITION)[0].score
    assert s_bare < s_sink < s_both


def test_ranking_falls_back_to_seed_entry_points_with_no_maps():
    seed = _seed(entry_points=[
        EntryPoint(kind=EntryPointKind.NETWORK, file="core/db.py", line=3),
    ])
    surf = rank_attack_surface(seed, [], None, None, None)
    assert len(surf) == 1
    assert surf[0].exposure == "external"  # network is inherently external


def test_map_entry_point_missed_by_seed_is_included():
    seed = _seed(entry_points=[])
    sm = SubsystemMap(subsystem="api", entry_points=["api/views.py:99 framework — hidden route"])
    surf = rank_attack_surface(seed, [sm], None, None, PARTITION)
    assert any("api/views.py:99" in a.entry_point for a in surf)


# --------------------------------------------------------------- auth model


def test_auth_model_prefers_lead_agent_prose():
    mm = ModuleMap(auth_model="JWT bearer tokens verified in api/mw.py; no RBAC.")
    assert derive_auth_model(_seed(), [], mm).startswith("JWT bearer")


def test_auth_model_picks_up_markers_and_touchpoints():
    sm = SubsystemMap(subsystem="api", auth_touchpoints=["@login_required on api/views.py:8"],
                      notes="sessions stored in a signed cookie")
    out = derive_auth_model(_seed(), [sm], None)
    assert "login_required" in out or "session" in out.lower()
    assert "api/views.py:8" in out


def test_auth_model_is_explicit_when_nothing_found():
    out = derive_auth_model(_seed(), [], None)
    assert "No authentication" in out and "unauthenticated" in out


# --------------------------------------------------------------- data flows


def test_stitch_marks_boundary_crossing_flows_first():
    sm = SubsystemMap(subsystem="api", data_flows=[
        "api/views.py:10 -> core/db.py:20",   # crosses api -> core
        "api/views.py:5 -> api/views.py:9",   # internal to api
    ])
    flows = stitch_data_flows([sm], PARTITION)
    assert flows[0].startswith("(api -> core)")
    assert any("[api]" in f for f in flows)


def test_cross_subsystem_flows_yields_endpoints_and_label():
    sm = SubsystemMap(subsystem="api", data_flows=["api/views.py:10 -> core/db.py:20"])
    pairs = cross_subsystem_flows([sm], PARTITION)
    assert pairs == [("api/views.py:10", "core/db.py:20", "api -> core")]


# ----------------------------------------------------- architecture.md render


def _full_render(quality="full"):
    seed = _seed(entry_points=[
        EntryPoint(kind=EntryPointKind.FRAMEWORK, file="api/views.py", line=10, framework="flask"),
    ], reflection_facts=[{"file": "api/views.py", "line": 30, "kind": "eval_exec", "snippet": "eval(x)"}])
    sms = [SubsystemMap(subsystem="api", trust_boundaries=["HTTP body -> SQL"],
                        third_party_parsers=["PyYAML"], data_flows=["api/views.py:10 -> core/db.py:20"])]
    surf = rank_attack_surface(seed, sms, None, None, PARTITION)
    auth = derive_auth_model(seed, sms, None)
    return render_architecture(seed, sms, partition=PARTITION, auth_model=auth,
                               attack_surface=surf, quality=quality)


def test_render_has_all_eleven_sections_in_order():
    md = _full_render()
    idx = [md.find(h) for h in ARCHITECTURE_SECTIONS]
    assert all(i >= 0 for i in idx), [h for h, i in zip(ARCHITECTURE_SECTIONS, idx) if i < 0]
    assert idx == sorted(idx), "sections out of order"
    assert len(ARCHITECTURE_SECTIONS) == 11


def test_render_tags_entry_points_with_subsystem():
    md = _full_render()
    assert "`[api]`" in md


def test_render_banner_only_when_degraded():
    assert "Recon degradation" not in _full_render("full")
    assert "Recon degradation — `seed_only`" in _full_render("seed_only")
    assert "Recon degradation — `partial`" in _full_render("partial")


def test_subsystem_rows_report_loc_and_external_flag(repo_web):
    seed = build_seed(str(repo_web))
    part = [
        {"name": "api", "responsibility": "http", "external_facing": True, "paths": ["api"], "files": []},
        {"name": "core", "responsibility": "db", "external_facing": False, "paths": ["core"], "files": []},
    ]
    rows = {r["name"]: r for r in subsystem_rows(part, seed)}
    assert rows["api"]["external_facing"] is True
    assert rows["core"]["loc"] > 0 and rows["core"]["files"] >= 1
