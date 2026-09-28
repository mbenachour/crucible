"""Deterministic validation gates — Pass A (specs.md §9.4).

No model calls. Cheapest filter first. Plain Python doing deterministic work
(§1.8): path checks, schema conformance, patch/test parsing, PoC gate.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MechStatus(str, Enum):
    PASSED = "passed"
    MECHANICAL_FAILED = "mechanical_failed"


@dataclass
class MechResult:
    finding_id: str
    status: MechStatus
    reasons: list[str] = field(default_factory=list)


def check_finding(
    finding_id: str,
    *,
    repo_path: str,
    repo_commit: str,
    workspace_path: str,
    store=None,
    sandbox_provider=None,
    prepared=None,
) -> MechResult:
    """Run every deterministic gate; accumulate failure reasons."""
    reasons: list[str] = []

    finding = _load_finding(finding_id, store, workspace_path)
    if finding is None:
        return MechResult(finding_id, MechStatus.MECHANICAL_FAILED, ["finding not found"])

    reasons += _check_path_and_range(finding, repo_path)
    reasons += _check_schema(finding)
    reasons += _check_patch_applies(finding, repo_path, repo_commit)
    reasons += _check_patch_artifacts(finding, repo_path)
    reasons += _check_poc_parses(finding)
    if not reasons:
        # Last and costliest: a sandbox run. Pointless once anything above failed.
        from crucible.validation.poc_gate import poc_gate_reasons

        reasons += poc_gate_reasons(
            finding, provider=sandbox_provider, prepared=prepared, task_id=finding_id,
        )

    status = MechStatus.PASSED if not reasons else MechStatus.MECHANICAL_FAILED
    return MechResult(finding_id, status, reasons)


def _load_finding(finding_id: str, store, workspace_path: str):
    """Reconstruct the `Finding` from the store, falling back to the workspace
    JSON the Hunter wrote (`findings/<id>.json`)."""
    from crucible.validation.schema import Finding

    payload = None
    if store is not None:
        row = store.get_finding(finding_id)
        if row is not None:
            payload = row.payload
    if payload is None:
        from crucible.workspace import layout

        p = layout.finding_path(workspace_path, finding_id)
        if p.is_file():
            import json

            payload = json.loads(p.read_text())
    if not isinstance(payload, dict):
        return None
    try:
        return Finding.model_validate(payload)
    except Exception:  # noqa: BLE001 — a malformed payload is a mechanical failure
        return None


def _check_path_and_range(finding, repo_path: str) -> list[str]:
    p = Path(repo_path) / finding.file_path
    if not p.is_file():
        return [f"cited path does not exist at repo_commit: {finding.file_path}"]
    n_lines = len(p.read_text(errors="replace").splitlines())
    if not (1 <= finding.line_start <= finding.line_end <= n_lines):
        return [f"line range out of bounds: {finding.line_start}-{finding.line_end} of {n_lines}"]
    return []


def _check_schema(finding) -> list[str]:
    from crucible.validation.schema import tautology_reasons

    reasons: list[str] = []
    if not (finding.threat_model.attacker and finding.threat_model.boundary_crossed
            and finding.threat_model.assumption_broken):
        reasons.append("threat_model not fully populated")
    reasons += tautology_reasons(finding)
    return reasons


def _check_patch_applies(finding, repo_path: str, repo_commit: str) -> list[str]:
    """Dry-run the unified diff against the unmodified tree, then revert."""
    proc = subprocess.run(
        ["git", "-C", repo_path, "apply", "--check", "-"],
        input=finding.proposed_patch, text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return [f"patch does not apply cleanly: {proc.stderr.strip()}"]
    return []


def emit_repair_reasons(finding, repo_path: str) -> list[str]:
    """Structural reasons worth giving a Hunter one repair attempt on, during
    Hunt's own emit step rather than only after the fact in Pass A. A bad
    line range or a diff whose hunk header miscounts context/added lines are
    mistakes the model can plausibly fix given the exact `git apply` error —
    unlike a tautological finding or a genuinely unreachable defect, which
    belong to `_check_schema` / the bug validator and are left alone here."""
    # Artifact problems ride along with apply errors so one repair turn can
    # fix both; the file-parse half skips itself when the patch doesn't apply.
    return (
        _check_path_and_range(finding, repo_path)
        + _check_patch_applies(finding, repo_path, "")
        + _check_patch_artifacts(finding, repo_path)
    )


# --- post-apply artifact checks (issue #106) --------------------------------
#
# A patch can apply cleanly and still be useless: invalid JSON, a duplicate
# key, a placeholder where a value belongs, or a commit SHA the model made up.

_PLACEHOLDER = re.compile(
    r"<[^<>\s]*(sha|hash|commit|digest|token|version|value)[^<>\s]*>"
    r"|\bREPLACE[_-]?ME\b|\bTODO\b|\bFIXME\b|\bXXX\b|\byour[-_][\w-]*[-_]here\b",
    re.IGNORECASE,
)
_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")
# Longest run of stepwise-incrementing hex digits that marks a SHA as invented
# (e.g. "3d4e5f6a7b8c9d0e1f"). Across 200k random SHAs the longest run seen was
# 5; real commit SHAs a model has memorized score about 1.
_FABRICATED_SHA_RUN = 6


def _added_lines(patch: str) -> list[str]:
    return [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def _incrementing_run(sha: str) -> int:
    best = 0
    for step in (1, 2):
        run = 0
        for i in range(len(sha) - step):
            if (int(sha[i + step], 16) - int(sha[i], 16)) % 16 == 1:
                run += 1
                best = max(best, run)
            else:
                run = 0
    return best


def _check_patch_artifacts(finding, repo_path: str) -> list[str]:
    reasons: list[str] = []
    added = _added_lines(finding.proposed_patch)
    for line in added:
        m = _PLACEHOLDER.search(line)
        if m:
            reasons.append(f"patch adds a placeholder instead of a real value: {m.group(0)!r}")
            break
    for line in added:
        fake = next((s for s in _SHA40.findall(line) if _incrementing_run(s) >= _FABRICATED_SHA_RUN), None)
        if fake:
            reasons.append(
                f"patch adds a commit SHA that looks invented ({fake}); recommend a pinning "
                "tool instead of guessing SHAs"
            )
            break
    reasons += _check_patched_files_parse(finding.proposed_patch, repo_path)
    return reasons


def _check_patched_files_parse(patch: str, repo_path: str) -> list[str]:
    """Apply the patch to scratch copies of the files it touches, then re-parse
    every JSON/YAML/TOML result. The real repo is never modified."""
    import shutil
    import tempfile

    numstat = subprocess.run(
        ["git", "-C", repo_path, "apply", "--numstat", "-"],
        input=patch, text=True, capture_output=True, check=False,
    )
    paths = [ln.split("\t", 2)[2] for ln in numstat.stdout.splitlines() if ln.count("\t") >= 2]
    parseable = [p for p in paths if Path(p).suffix.lower() in _PARSERS]
    if not parseable:
        return []

    with tempfile.TemporaryDirectory() as tmp:
        for p in paths:
            src = Path(repo_path) / p
            if src.is_file():
                dst = Path(tmp) / p
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        applied = subprocess.run(
            ["git", "apply", "-"], cwd=tmp, input=patch, text=True, capture_output=True, check=False,
        )
        if applied.returncode != 0:
            return []  # _check_patch_applies already owns this failure
        reasons = []
        for p in parseable:
            target = Path(tmp) / p
            if not target.is_file():
                continue  # deleted by the patch
            try:
                _PARSERS[target.suffix.lower()](target.read_text(errors="replace"))
            except Exception as e:  # noqa: BLE001 — any parse error is the finding
                reasons.append(f"patched {p} no longer parses: {str(e).splitlines()[0]}")
        return reasons


def _parse_json(text: str) -> None:
    import json

    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate key(s): {', '.join(sorted(dupes))}")
        return dict(pairs)

    json.loads(text, object_pairs_hook=no_dupes)


def _parse_yaml(text: str) -> None:
    import yaml

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen and key != "<<":
                raise ValueError(f"duplicate key: {key!r}")
            seen.add(key)
        return loader.construct_mapping(node, deep)

    UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    list(yaml.load_all(text, Loader=UniqueKeyLoader))


def _parse_toml(text: str) -> None:
    import tomllib

    tomllib.loads(text)


_PARSERS = {".json": _parse_json, ".yaml": _parse_yaml, ".yml": _parse_yaml, ".toml": _parse_toml}


def _check_poc_parses(finding) -> list[str]:
    # TODO(phase1): language-aware parse (py: ast.parse; c: compile-only).
    if not finding.poc_test.strip():
        return ["poc_test is empty"]
    return []


