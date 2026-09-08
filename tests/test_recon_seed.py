"""R0 seed — deterministic, no model (issue #5, regression guard for #34)."""

from __future__ import annotations

from crucible.recon.schema import EntryPointKind, RepoKind
from crucible.recon.seed import build_seed


def test_seed_is_deterministic(repo_web):
    a = build_seed(str(repo_web))
    b = build_seed(str(repo_web))
    assert a.model_dump() == b.model_dump()


def test_web_seed_shape(repo_web):
    s = build_seed(str(repo_web))
    assert s.primary_language == "python"
    assert s.repo_kind is RepoKind.WEB_API
    assert "flask" in s.frameworks
    kinds = {ep.kind for ep in s.entry_points}
    assert EntryPointKind.FRAMEWORK in kinds
    assert any(r.kind == "eval_exec" for r in s.reflection_facts)


def test_native_seed_is_c(repo_native):
    s = build_seed(str(repo_native))
    assert s.repo_kind is RepoKind.NATIVE
    assert s.primary_language == "c"


def test_mobile_seed_is_react_native(repo_mobile):
    s = build_seed(str(repo_mobile))
    assert s.repo_kind is RepoKind.MOBILE
    assert "react-native" in s.frameworks
    fw = {ep.framework for ep in s.entry_points}
    assert any("deeplink" in f for f in fw)
    assert any("webview" in f for f in fw)


def test_clean_repo_has_no_entry_points(repo_clean):
    s = build_seed(str(repo_clean))
    assert s.entry_points == []
