"""R3 decompose — deterministic hunt-queue seeding (issue #5; #34 phase 4)."""

from __future__ import annotations

from crucible.recon.decompose import BASELINE_BY_KIND, decompose
from crucible.recon.schema import (
    AttackClassSpec,
    AttackSurfaceItem,
    ChunkType,
    EntryPoint,
    EntryPointKind,
    FileEntry,
    RepoKind,
    Seed,
    StrideThreat,
    SubsystemMap,
    ThreatModel,
)
from crucible.recon.seed import build_seed


def _seed(**kw) -> Seed:
    base = {"repo_path": "/x", "primary_language": "python", "repo_kind": RepoKind.WEB_API}
    base.update(kw)
    return Seed(**base)


def test_language_prunes_memory_classes():
    s = _seed(primary_language="javascript", repo_kind=RepoKind.NATIVE)
    classes = {c.attack_class for c in decompose(s, None)}
    assert not (classes & {"memory_oob_write", "use_after_free", "format_string"})


def test_native_c_keeps_memory_classes(repo_native):
    s = build_seed(str(repo_native))
    classes = {c.attack_class for c in decompose(s, None)}
    assert {"memory_oob_read", "memory_oob_write"} & classes


def test_entry_point_gets_taint_chunk_when_dynamic_sink_is_near():
    s = _seed(
        entry_points=[EntryPoint(kind=EntryPointKind.FRAMEWORK, file="api/x.py", line=10, framework="flask")],
        reflection_facts=[{"file": "api/x.py", "line": 25, "kind": "eval_exec", "snippet": "eval(user)"}],
    )
    taint = [c for c in decompose(s, None) if c.chunk_type is ChunkType.TAINT]
    assert taint and taint[0].seed_path == "api/x.py:10 -> api/x.py:25"
    assert taint[0].priority == 1


def test_reflection_fact_becomes_risk_chunk():
    s = _seed(reflection_facts=[{"file": "a.py", "line": 3, "kind": "dynamic_getattr", "snippet": "getattr(o,n)"}])
    risks = [c for c in decompose(s, None) if c.chunk_type is ChunkType.RISK]
    assert risks and risks[0].attack_class == "dynamic_dispatch"


def test_cap_is_respected(repo_web):
    s = build_seed(str(repo_web))
    assert len(decompose(s, None, 15)) <= 15


def test_baseline_sweep_emitted_without_entry_points(repo_clean):
    s = build_seed(str(repo_clean))
    chunks = decompose(s, None)
    assert chunks
    assert all(c.chunk_type is ChunkType.CATCH_ALL for c in chunks)


def test_mobile_uses_masvs_baseline(repo_mobile):
    s = build_seed(str(repo_mobile))
    classes = {c.attack_class for c in decompose(s, None)}
    assert classes & set(BASELINE_BY_KIND[RepoKind.MOBILE])
    assert not (classes & {"memory_oob_write", "use_after_free"})


# ---------------------------------------------------- #34 phase 4: boundary-aware


def _web_seed_with_parts():
    seed = _seed(
        files=[
            FileEntry(path="api/views.py", language="python", loc=120, role="source"),
            FileEntry(path="core/db.py", language="python", loc=80, role="source"),
        ],
        entry_points=[EntryPoint(kind=EntryPointKind.FRAMEWORK, file="api/views.py", line=10, framework="flask")],
    )
    part = [
        {"name": "http-api", "external_facing": True, "paths": ["api"], "files": ["api/views.py"], "depends_on": ["data"]},
        {"name": "data", "external_facing": False, "paths": ["core"], "files": ["core/db.py"], "depends_on": []},
    ]
    return seed, part


def test_chunk_area_is_owning_subsystem_not_path_segment():
    seed, part = _web_seed_with_parts()
    chunks = decompose(seed, None, partition=part)
    areas = {c.area for c in chunks}
    assert "http-api" in areas and "data" in areas
    assert "api" not in areas and "core" not in areas
    ep_chunks = [c for c in chunks if c.seed_path == "api/views.py:10"]
    assert ep_chunks and all(c.area == "http-api" for c in ep_chunks)


def test_external_facing_subsystem_chunks_are_prioritised():
    seed, part = _web_seed_with_parts()
    ext = decompose(seed, None, partition=part)
    flipped = [{**p, "external_facing": False} for p in part]
    base = decompose(seed, None, partition=flipped)

    def min_prio(chunks, area):
        return min(c.priority for c in chunks if c.area == area)

    assert min_prio(ext, "http-api") < min_prio(base, "http-api")


def test_cross_subsystem_flow_becomes_taint_chunk():
    seed, part = _web_seed_with_parts()
    sms = [SubsystemMap(subsystem="http-api",
                        data_flows=["api/views.py:12 -> core/db.py:7"])]
    chunks = decompose(seed, None, partition=part, subsystem_maps=sms)
    taint = [c for c in chunks if c.attack_class == "injection_passthrough"]
    assert taint
    assert taint[0].chunk_type is ChunkType.TAINT
    assert taint[0].seed_path == "api/views.py:12 -> core/db.py:7"
    assert taint[0].area == "http-api"
    assert taint[0].priority == 1


def test_same_subsystem_flow_is_not_a_cross_taint():
    seed, part = _web_seed_with_parts()
    sms = [SubsystemMap(subsystem="http-api",
                        data_flows=["api/views.py:5 -> api/views.py:12"])]
    chunks = decompose(seed, None, partition=part, subsystem_maps=sms)
    assert not any(c.attack_class == "injection_passthrough" for c in chunks)


def test_rn_mobile_deeplink_to_webview_taint_chunk(repo_mobile):
    seed = build_seed(str(repo_mobile))
    assert seed.repo_kind is RepoKind.MOBILE

    part = [
        {"name": "linking", "external_facing": True, "paths": ["App/linking"],
         "files": ["App/linking/DeepLink.js"], "depends_on": ["webview"]},
        {"name": "webview", "external_facing": True, "paths": ["App/webview"],
         "files": ["App/webview/WebViewScreen.js"], "depends_on": []},
        {"name": "api", "external_facing": False, "paths": ["App/api"],
         "files": ["App/api/Client.js"], "depends_on": []},
    ]
    sms = [SubsystemMap(
        subsystem="linking",
        data_flows=["App/linking/DeepLink.js:12 -> App/webview/WebViewScreen.js:8"],
    )]
    chunks = decompose(seed, None, partition=part, subsystem_maps=sms)
    taint = [c for c in chunks if c.chunk_type is ChunkType.TAINT
             and c.seed_path and "DeepLink.js" in c.seed_path and "WebViewScreen.js" in c.seed_path]
    assert taint, "expected a deep-link -> WebView taint chunk"
    assert taint[0].area == "linking"


# ---------------------------------------------------- #35: attack-surface-first queue


def _surf(file, line, kind, sub, score, *, exposure="external", rationale="why", target=None):
    return AttackSurfaceItem(
        target=target or f"{file}:{line}",
        subsystem=sub,
        entry_point=f"{file}:{line} {kind}",
        exposure=exposure,
        rationale=rationale,
        score=score,
    )


def test_attack_surface_items_become_surface_chunks_in_rank_order():
    seed, part = _web_seed_with_parts()
    surf = [
        _surf("api/views.py", 10, "framework", "http-api", 24.0),
        _surf("core/db.py", 7, "file", "data", 4.0, exposure="internal"),
    ]
    chunks = decompose(seed, None, partition=part, attack_surface=surf)
    sc = [c for c in chunks if c.chunk_type is ChunkType.SURFACE]
    # each top-ranked item -> a chunk with matching seed_path, in rank order
    assert [c.seed_path for c in sc] == ["api/views.py:10", "core/db.py:7"]
    assert sc[0].priority < sc[1].priority
    assert sc[0].area == "http-api" and sc[1].area == "data"
    assert "score 24" in sc[0].scope_hint and "external" in sc[0].scope_hint
    # highest-scored target is the very first chunk in the queue
    assert chunks[0].chunk_type is ChunkType.SURFACE
    assert chunks[0].seed_path == "api/views.py:10"


def test_surface_chunk_attack_class_follows_entry_kind():
    seed = _seed(repo_kind=RepoKind.CLI, primary_language="go")
    surf = [_surf("arguments/parser.go", 232, "cli", "arguments", 12.0)]
    sc = [c for c in decompose(seed, None, attack_surface=surf)
          if c.chunk_type is ChunkType.SURFACE]
    assert sc and sc[0].attack_class == "command_injection"
    assert sc[0].seed_path == "arguments/parser.go:232"


def test_surface_chunk_uses_framework_marker_from_target():
    seed, part = _web_seed_with_parts()
    surf = [_surf("api/views.py", 10, "framework", "http-api", 20.0, target="login:10 (flask)")]
    sc = next(c for c in decompose(seed, None, partition=part, attack_surface=surf)
              if c.chunk_type is ChunkType.SURFACE)
    assert sc.attack_class in {
        "command_injection", "sql_injection", "template_injection", "ssrf", "auth_bypass",
    }


def test_queue_tier_ordering_invariant():
    seed = _seed(
        entry_points=[EntryPoint(kind=EntryPointKind.FRAMEWORK, file="api/x.py", line=10, framework="flask")],
        reflection_facts=[{"file": "api/x.py", "line": 200, "kind": "eval_exec", "snippet": "eval(z)"}],
        files=[
            FileEntry(path="api/x.py", language="python", loc=50, role="source"),
            FileEntry(path="svc/y.py", language="python", loc=50, role="source"),
        ],
    )
    tm = ThreatModel(
        repo_specific_classes=[AttackClassSpec(name="cli_target_eval_injection", methodology="m", rationale="r")],
        stride=[StrideThreat(entry_point="api/x.py:10", category="tampering", description="d", attacker="remote")],
    )
    sms = [SubsystemMap(subsystem="api", dangerous_sinks=["api/x.py:205 raw sql cursor.execute"])]
    surf = [_surf("api/x.py", 10, "framework", "api", 30.0)]
    chunks = decompose(seed, tm, attack_surface=surf, subsystem_maps=sms)

    pos = {ct: [i for i, c in enumerate(chunks) if c.chunk_type is ct] for ct in ChunkType}

    def lo(ct):
        return min(pos[ct]) if pos[ct] else 10**9

    def hi(ct):
        return max(pos[ct]) if pos[ct] else -1

    assert pos[ChunkType.SURFACE] and pos[ChunkType.TAINT] and pos[ChunkType.SPECIALIST]
    assert pos[ChunkType.RISK] and pos[ChunkType.THREAT_FALLBACK] and pos[ChunkType.CATCH_ALL]
    # surface + taint before specialist before risk before threat_fallback before catch_all
    assert max(hi(ChunkType.SURFACE), hi(ChunkType.TAINT)) < lo(ChunkType.SPECIALIST)
    assert hi(ChunkType.SPECIALIST) < lo(ChunkType.RISK)
    assert hi(ChunkType.RISK) < lo(ChunkType.THREAT_FALLBACK)
    assert hi(ChunkType.THREAT_FALLBACK) < lo(ChunkType.CATCH_ALL)
    assert chunks[0].chunk_type is ChunkType.SURFACE


def test_dangerous_sinks_become_anchored_chunks():
    seed, part = _web_seed_with_parts()
    sms = [SubsystemMap(subsystem="data", dangerous_sinks=[
        "core/db.py:44 raw SQL string built with % then cursor.execute",
        "core/db.py:88 subprocess.Popen(cmd, shell=True)",
        "core/db.py:120 some opaque sink",
    ])]
    chunks = decompose(seed, None, partition=part, subsystem_maps=sms)
    by_path = {c.seed_path: c for c in chunks if c.seed_path in
               {"core/db.py:44", "core/db.py:88", "core/db.py:120"}}
    assert by_path["core/db.py:44"].attack_class == "sql_injection"
    assert by_path["core/db.py:44"].chunk_type is ChunkType.TAINT
    assert by_path["core/db.py:44"].area == "data"
    assert by_path["core/db.py:88"].attack_class == "command_injection"
    assert by_path["core/db.py:120"].chunk_type is ChunkType.RISK


def test_empty_attack_surface_leaves_queue_unchanged():
    seed, part = _web_seed_with_parts()
    sms = [SubsystemMap(subsystem="http-api", data_flows=["api/views.py:12 -> core/db.py:7"])]
    base = decompose(seed, None, partition=part, subsystem_maps=sms)

    def key(cs):
        return [(c.chunk_type, c.area, c.attack_class, c.seed_path, c.priority) for c in cs]

    for surf in (None, []):
        assert key(decompose(seed, None, partition=part, subsystem_maps=sms, attack_surface=surf)) == key(base)
    assert not any(c.chunk_type is ChunkType.SURFACE for c in base)


def test_baseline_sweep_pruned_for_surface_covered_areas():
    seed, part = _web_seed_with_parts()
    surf = [
        _surf("api/views.py", 10, "framework", "http-api", 20.0),
        _surf("core/db.py", 7, "file", "data", 5.0, exposure="internal"),
    ]
    chunks = decompose(seed, None, partition=part, attack_surface=surf)
    surface_cov = {(c.area, c.attack_class) for c in chunks if c.chunk_type is ChunkType.SURFACE}
    sweeps = {(c.area, c.attack_class) for c in chunks
              if c.chunk_type is ChunkType.CATCH_ALL and c.scope_hint.startswith("baseline sweep")}
    assert not (surface_cov & sweeps)  # no sweep duplicates a ranked-surface chunk
    # 'data' is internal, has no seed entry point, and the surface covers it -> no blind sweep
    assert not any(c.area == "data" and c.scope_hint.startswith("baseline sweep") for c in chunks)


def test_first_batch_head_is_the_ranked_surface():
    seed, part = _web_seed_with_parts()
    surf = [
        _surf("api/views.py", 10, "framework", "http-api", 30.0),
        _surf("api/views.py", 25, "framework", "http-api", 18.0),
        _surf("core/db.py", 7, "file", "data", 6.0, exposure="internal"),
    ]
    head = decompose(seed, None, partition=part, attack_surface=surf)[:6]
    assert head[0].chunk_type is ChunkType.SURFACE and head[0].seed_path == "api/views.py:10"
    assert {"api/views.py:10", "api/views.py:25", "core/db.py:7"} <= {c.seed_path for c in head}
    assert all(c.chunk_type is not ChunkType.CATCH_ALL for c in head[:3])
