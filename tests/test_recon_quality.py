"""recon_quality classification (issue #34)."""

from __future__ import annotations

from crucible.graph.nodes.recon import _recon_quality
from crucible.recon.schema import StrideThreat, SubsystemMap, ThreatModel

REG = object()  # any non-None stand-in for a model registry
PART = [{"name": "a"}, {"name": "b"}]
MAPS = [SubsystemMap(subsystem="a"), SubsystemMap(subsystem="b")]
TM = ThreatModel(stride=[StrideThreat(entry_point="x:1", category="tampering",
                                      description="d", attacker="remote")])


def test_seed_only_without_registry():
    assert _recon_quality(None, PART, MAPS, TM) == "seed_only"


def test_seed_only_without_any_map():
    assert _recon_quality(REG, PART, [], TM) == "seed_only"


def test_partial_when_a_subsystem_did_not_contribute():
    assert _recon_quality(REG, PART, MAPS[:1], TM) == "partial"


def test_partial_when_threat_model_is_empty():
    assert _recon_quality(REG, PART, MAPS, ThreatModel()) == "partial"
    assert _recon_quality(REG, PART, MAPS, None) == "partial"


def test_full_when_all_maps_and_a_real_threat_model():
    assert _recon_quality(REG, PART, MAPS, TM) == "full"
