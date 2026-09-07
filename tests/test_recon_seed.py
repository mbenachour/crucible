"""R0 seed — deterministic, no model (issue #5)."""

from pathlib import Path

from crucible.recon.schema import EntryPointKind, RepoKind
from crucible.recon.seed import build_seed

REPOS = Path(__file__).parent / "fixtures" / "repos"
RN = REPOS / "fixture-rn"


def test_seed_is_deterministic():
    a = build_seed(str(REPOS / "fixture-py"))
    b = build_seed(str(REPOS / "fixture-py"))
    assert a.model_dump() == b.model_dump()


def test_fixture_py_seed():
    s = build_seed(str(REPOS / "fixture-py"))
    assert s.primary_language == "python"
    # pickle.loads in app/cache.py -> a deserialization entry point
    kinds = {(ep.kind, ep.file) for ep in s.entry_points}
    assert (EntryPointKind.DESERIALIZATION, "app/cache.py") in kinds
    assert s.stats["call_edges"] >= 1  # tree-sitter ran


def test_fixture_c_seed_is_native():
    s = build_seed(str(REPOS / "fixture-c"))
    assert s.repo_kind is RepoKind.NATIVE
    assert s.primary_language == "c"


def test_react_native_seed_is_mobile():
    s = build_seed(str(RN))
    assert s.repo_kind is RepoKind.MOBILE
    assert "react-native" in s.frameworks
    # RN bridge + a webview/deeplink or express entry point somewhere
    fw = {ep.framework for ep in s.entry_points}
    assert any("bridge" in f for f in fw)
    # eval() in the router -> a reflection fact
    assert any(r.kind == "eval_exec" for r in s.reflection_facts)


def test_clean_fixture_has_no_entry_points():
    s = build_seed(str(REPOS / "fixture-clean"))
    # the safe counterparts expose nothing the static seed recognises
    assert s.entry_points == []
