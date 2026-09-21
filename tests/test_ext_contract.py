"""The open-core extension contract (`crucible.ext`).

This suite is deliberately strict: `crucible.ext` is the surface a downstream
distribution builds on, so a change here is a change to a semver promise. If a
test in this file fails, the fix is either "put the name back" or "this is a
major version bump and the downstream needs updating" — never "loosen the
test".
"""

from __future__ import annotations

import pytest

from crucible import ext
from crucible.graph.build import NodeSpec, _splice

# The exact public surface, pinned. Adding a name here is a minor bump;
# removing or renaming one is a major bump.
EXPECTED_SURFACE = {
    "STAGE_NODES",
    "NodeSpec",
    "StopAfterStage",
    "build_graph",
    "NodeDeps",
    "CrucibleState",
    "ModelEndpoint",
    "ModelRegistry",
    "ModelRole",
    "span",
    "load_skill",
    "skill_front_matter",
    "Store",
    "FindingRow",
    "ValidationRow",
    "WishRow",
    "Finding",
    "Severity",
    "ThreatModel",
    "create_app",
    "ApiSettings",
    "get_settings",
    "get_store",
    "require_read",
    "require_write",
}


def test_surface_is_exactly_what_we_promised():
    assert set(ext.__all__) == EXPECTED_SURFACE


def test_every_promised_name_actually_resolves():
    """Including the lazily-resolved API names — a typo in the lazy map would
    otherwise only surface in the downstream, at runtime."""
    for name in ext.__all__:
        assert getattr(ext, name) is not None, name


def test_unknown_attribute_still_raises_attribute_error():
    """The lazy `__getattr__` must not turn every typo into an import error
    from somewhere deep in the package."""
    with pytest.raises(AttributeError):
        _ = ext.definitely_not_exported


def test_stage_nodes_are_the_ten_oss_stages():
    """A downstream asserts against this list to prove it hasn't replaced an
    engine stage, so its contents are part of the contract too."""
    assert ext.STAGE_NODES == (
        "recon",
        "hunt",
        "dedup",
        "validate_mechanical",
        "gapfill",
        "feedback",
        "loop_control",
        "validate_bug",
        "validate_reachability",
        "report",
    )


# --- the splice itself ----------------------------------------------------
#
# `_splice` is the whole mechanism behind `extra_nodes`; testing it directly
# keeps these cases readable (no sqlite checkpointer, no compiled graph).

BASE = [
    ("__start__", "recon"),
    ("recon", "hunt"),
    ("dedup", "validate_mechanical"),
    ("validate_mechanical", "gapfill"),
    ("gapfill", "feedback"),
    ("feedback", "loop_control"),
    ("validate_bug", "validate_reachability"),
    ("validate_reachability", "report"),
    ("report", "__end__"),
]


def _spec(name, after):
    return NodeSpec(name=name, run=lambda state, deps=None: state, after=after)


def test_splice_rewires_the_edge_through_the_new_node():
    out = _splice(BASE, (_spec("tracer", "validate_reachability"),))
    assert ("validate_reachability", "tracer") in out
    assert ("tracer", "report") in out
    assert ("validate_reachability", "report") not in out


def test_splice_before_end():
    out = _splice(BASE, (_spec("fixer", "report"),))
    assert ("report", "fixer") in out
    assert ("fixer", "__end__") in out


def test_several_specs_after_the_same_stage_chain_in_order():
    out = _splice(BASE, (_spec("vvs", "report"), _spec("fixer", "report")))
    assert ("report", "vvs") in out
    assert ("vvs", "fixer") in out
    assert ("fixer", "__end__") in out


def test_splice_leaves_untouched_edges_alone():
    out = _splice(BASE, (_spec("tracer", "validate_reachability"),))
    for edge in BASE:
        if edge != ("validate_reachability", "report"):
            assert edge in out


def test_cannot_shadow_an_existing_stage():
    with pytest.raises(ValueError, match="collides with an existing stage"):
        _splice(BASE, (_spec("report", "validate_reachability"),))


@pytest.mark.parametrize("stage", ["hunt", "loop_control"])
def test_cannot_splice_after_a_conditional_stage(stage):
    """`hunt` and `loop_control` branch through a gate function — there's no
    single static edge to rewire, and silently picking one branch would be
    worse than refusing."""
    with pytest.raises(ValueError, match="conditional"):
        _splice(BASE, (_spec("tracer", stage),))


def test_unknown_after_stage_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown stage"):
        _splice(BASE, (_spec("tracer", "no_such_stage"),))


def test_splice_does_not_mutate_the_caller_s_edge_list():
    before = list(BASE)
    _splice(BASE, (_spec("tracer", "report"),))
    assert BASE == before


# --- the seam, end to end -------------------------------------------------


def test_extra_nodes_compile_into_a_real_graph(tmp_path):
    """The integration proof a downstream depends on: extra stages land in the
    compiled graph, wired where asked, with every engine stage still present
    and still its own implementation."""
    deps = ext.NodeDeps(registry=None, store=None, sandbox_provider=None)
    graph = ext.build_graph(
        deps,
        checkpoint_db=tmp_path / "ckpt.sqlite",
        extra_nodes=[
            ext.NodeSpec("tracer", lambda s, deps=None: s, after="validate_reachability"),
            ext.NodeSpec("fixer", lambda s, deps=None: s, after="report"),
        ],
    )
    drawn = graph.get_graph()
    nodes = set(drawn.nodes) - {"__start__", "__end__"}
    edges = {(e.source, e.target) for e in drawn.edges}

    assert set(ext.STAGE_NODES) <= nodes, "an engine stage went missing"
    assert {"tracer", "fixer"} <= nodes
    assert {("validate_reachability", "tracer"), ("tracer", "report"), ("report", "fixer")} <= edges
    assert ("validate_reachability", "report") not in edges, "old edge not rewired"


def test_no_extra_nodes_leaves_the_topology_untouched(tmp_path):
    """The default path is the one every OSS user runs — adding the seam must
    not have changed it."""
    deps = ext.NodeDeps(registry=None, store=None, sandbox_provider=None)
    drawn = ext.build_graph(deps, checkpoint_db=tmp_path / "ckpt.sqlite").get_graph()
    nodes = set(drawn.nodes) - {"__start__", "__end__"}
    edges = {(e.source, e.target) for e in drawn.edges}

    assert nodes == set(ext.STAGE_NODES)
    assert ("validate_reachability", "report") in edges
    assert ("recon", "hunt") in edges
