"""Tautology deny-list + schema field order (specs.md §9.2 unit tests)."""

from crucible.validation.schema import Finding, Severity, ThreatModel, tautology_reasons


def _finding(**over):
    base = dict(
        threat_model=ThreatModel(
            attacker="unauthenticated remote client",
            boundary_crossed="network input -> parser memory",
            assumption_broken="length field validated before use",
        ),
        title="OOB write in length handling",
        file_path="src/parse.c",
        line_start=142,
        line_end=158,
        description="crafted length field overflows the destination buffer",
        poc_test="int main(){ return run_parser(payload); }",
        proposed_patch="--- a/src/parse.c\n+++ b/src/parse.c\n",
        severity=Severity.HIGH,
    )
    base.update(over)
    return Finding(**base)


def test_field_order_threat_model_first():
    assert list(Finding.model_fields)[0] == "threat_model"


def test_clean_finding_has_no_denylist_hits():
    assert tautology_reasons(_finding()) == []


def test_privilege_equals_impact_is_rejected():
    f = _finding(
        threat_model=ThreatModel(
            attacker="authenticated user with database write access",
            boundary_crossed="app -> db",
            assumption_broken="none really",
        ),
        title="User can write to the database",
        description="a user with db write access can write to the database",
    )
    assert tautology_reasons(f)


def test_tautological_poc_marker_is_rejected():
    f = _finding(poc_test="parser.run(x)\nassert True  # this always passes")
    assert any("tautological" in r for r in tautology_reasons(f))
