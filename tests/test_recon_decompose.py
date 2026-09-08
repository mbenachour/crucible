"""R3 decompose — deterministic hunt-queue seeding (issue #5; #34 phase 4)."""

from __future__ import annotations

from crucible.recon.decompose import BASELINE_BY_KIND, decompose
from crucible.recon.schema import (
    ChunkType,
    EntryPoint,
    EntryPointKind,
    RepoKind,
    Seed,
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
