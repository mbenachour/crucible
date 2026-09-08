"""R3 decompose — deterministic hunt-queue seeding (issue #5; #34 phase 4)."""

from __future__ import annotations

from crucible.recon.decompose import BASELINE_BY_KIND, decompose
from crucible.recon.schema import (
    ChunkType,
    EntryPoint,
    EntryPointKind,
    FileEntry,
    RepoKind,
    Seed,
    SubsystemMap,
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
