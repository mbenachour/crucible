"""R2 threat model — config / hardening posture is a first-class threat (issue #97).

The R2 output is model-generated, so these assert the *framing* (prompt + task
text) and that a hardening threat is representable and reaches the hunt queue
routed to the built-in `misconfiguration` class.
"""

from __future__ import annotations

from crucible.graph.nodes.hunt import _attack_class_body
from crucible.graph.nodes.recon import _threatmodel_task_text
from crucible.recon.decompose import decompose
from crucible.recon.schema import ChunkType, RepoKind, Seed, ThreatModel
from crucible.skills import load_skill, skill_front_matter


def _seed() -> Seed:
    return Seed(
        repo_path="/x", primary_language="typescript",
        repo_kind=RepoKind.WEB_API, frameworks=["vue"],
    )


# What R2 should now emit for vue3-realworld-example-app (no CSP anywhere).
_HARDENING_TM = {
    "attackers": ["unauthenticated remote client"],
    "assets": ["user session token", "response-header hardening posture"],
    "trust_boundaries": ["browser <-> API"],
    "stride": [{
        "entry_point": "index.html",
        "category": "tampering",
        "description": "no Content-Security-Policy set anywhere, so any injected "
                       "script runs unrestricted",
        "attacker": "unauthenticated remote client",
    }],
    "repo_specific_classes": [{
        "name": "misconfiguration",
        "methodology": "Verify no CSP / HSTS / X-Frame-Options is set in index.html, "
                       "vite.config.ts or hosting config.",
        "rationale": "SPA served with no hardening headers.",
    }],
}


def test_prompt_names_hardening_posture_as_first_class():
    body = load_skill("recon/threatmodel.md").lower()
    assert "hardening posture" in body
    for marker in ("content-security-policy", "strict-transport-security",
                   "x-frame-options", "default-insecure"):
        assert marker in body, marker
    # routes to the existing taxonomy, not a new class
    assert "named exactly `misconfiguration`" in " ".join(body.split())


def test_prompt_version_bumped():
    assert skill_front_matter("recon/threatmodel.md")["version"] != "0.1.0"


def test_task_text_asks_for_hardening_posture():
    text = _threatmodel_task_text(_seed(), "# arch")
    assert "hardening posture" in text
    assert "CSP" in text


def test_hardening_threat_is_representable():
    tm = ThreatModel.model_validate(_HARDENING_TM)
    assert "Content-Security-Policy" in tm.stride[0].description
    assert tm.repo_specific_classes[0].name == "misconfiguration"


def test_pre_97_threat_model_still_parses():
    tm = ThreatModel.model_validate({"attackers": ["x"], "stride": []})
    assert tm.repo_specific_classes == []


def test_hardening_threat_reaches_hunt_queue_as_misconfiguration():
    chunks = decompose(_seed(), ThreatModel.model_validate(_HARDENING_TM))
    spec = [c for c in chunks
            if c.chunk_type is ChunkType.SPECIALIST and c.attack_class == "misconfiguration"]
    assert len(spec) == 1
    assert "CSP" in spec[0].scope_hint
    # deterministic
    again = decompose(_seed(), ThreatModel.model_validate(_HARDENING_TM))
    assert [c.model_dump() for c in chunks] == [c.model_dump() for c in again]


def test_misconfiguration_playbook_covers_response_headers():
    body, ver = _attack_class_body("misconfiguration")
    assert not ver.endswith("@generic")  # the built-in skill, not the fallback
    assert "Content-Security-Policy" in body
    assert "HSTS" in body
