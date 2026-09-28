"""Tautology deny-list (specs.md §9.2), including insider attackers (issue #105)."""

from __future__ import annotations

import pytest

from crucible.validation.schema import Finding, tautology_reasons


def _finding(attacker: str, title: str = "t", description: str = "d") -> Finding:
    return Finding.model_validate({
        "threat_model": {
            "attacker": attacker,
            "boundary_crossed": "b",
            "assumption_broken": "a",
        },
        "title": title,
        "file_path": "f.js",
        "line_start": 1,
        "line_end": 1,
        "description": description,
        "poc_test": "test('x', () => { expect(run()).toBe(1) })",
        "proposed_patch": "--- a/f.js\n+++ b/f.js\n@@ -1 +1 @@\n-a\n+b\n",
        "severity": "high",
    })


# Attacker descriptions taken from run 13349559c81a, plus close variants.
@pytest.mark.parametrize("attacker", [
    "malicious contributor with push access to the repository",
    "A user with push access to the repository (can modify package.json)",
    "A repository maintainer with push access (collaborator)",
    "anyone who can modify the workflow file",
    "an attacker with write access to the repo",
    "a collaborator on the project",
    "someone with commit rights",
])
def test_insider_attacker_is_rejected(attacker):
    reasons = tautology_reasons(_finding(attacker))
    assert any("write access to the repository" in r for r in reasons)


@pytest.mark.parametrize("attacker", [
    "whoever compromises the upstream peaceiris/actions-gh-pages action",
    "a compromised npm dependency executing an install script",
    "an attacker who compromises a third-party action maintainer's account",
    "unauthenticated remote client",
    "external contributor opening a pull request from a fork",
])
def test_real_boundary_attacker_is_accepted(attacker):
    assert tautology_reasons(_finding(attacker)) == []


def test_existing_privilege_impact_pair_still_fires():
    f = _finding("user with database write access", title="can write to the database")
    assert any("already implies" in r for r in tautology_reasons(f))
