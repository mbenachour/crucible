"""PoC gate — proof, not judgment (specs.md §1.9, §9.4 Pass A; issue #9).

"No PoC against the unmodified codebase, no finding." In a sandbox built from
the prepared tree (repo@commit plus installed dependencies, see
`crucible.sandbox.prepare`):

  1. copy the tree to scratch and write `poc_test` to `poc_filename`
  2. run `poc_command` — it must FAIL (non-zero) because of the bug, not
     because it couldn't run (missing module, syntax error, no tests found)
  3. check the run didn't modify any tracked source file
  4. apply `proposed_patch`
  5. run `poc_command` again — it must PASS (exit 0)
  6. check nothing tracked changed outside the patch

A PoC that passes on the clean repo proves nothing; one that still fails
patched either doesn't test the claim or the patch doesn't fix it. Both are
rejected with no model judgment involved.

Mode, from `CRUCIBLE_POC_GATE`:
  enforce  (default) a failed gate rejects the finding; no sandbox rejects too
  advisory run the gate and log the result, never reject
  off      skip the gate
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from crucible.sandbox import ExecResult, SandboxLimits

log = logging.getLogger("crucible.poc_gate")

MODES = ("enforce", "advisory", "off")
POC_RUN_TIMEOUT_S = int(os.environ.get("CRUCIBLE_POC_TIMEOUT", "180"))
# Room for a copy of the prepared tree (node_modules can be hundreds of MB) and
# a test runner. Hunt uses the same limits so a rehearsal matches the gate.
POC_LIMITS = SandboxLimits(
    cpu_seconds=120, memory_mb=4096, wall_clock_s=1800, max_pids=1024, disk_quota_mb=4096,
)
_REPO = "/scratch/crucible-poc-repo"
_PATCH = "/tmp/crucible-poc.patch"
_GIT = "git -c safe.directory='*' -c core.fileMode=false"

# Output that means the PoC never ran its check, so a non-zero exit proves
# nothing. Checked only on the clean run: a PoC that imports a module the
# patch *adds* would otherwise "fail clean, pass patched" without ever
# exercising the vulnerable code.
_COULD_NOT_RUN = [
    (re.compile(r": command not found\b"), "command not found"),
    (re.compile(r"ModuleNotFoundError|No module named|cannot import name"), "import error"),
    (re.compile(r"Cannot find module|ERR_MODULE_NOT_FOUND|Failed to (load url|resolve import)"
                r"|does not provide an export named"), "module not found"),
    # vitest: "No test files found" / "Tests  no tests"; jest: "No tests found";
    # pytest: "collected 0 items" / "no tests ran"
    (re.compile(r"No test files found|Tests\s+no tests|No tests found|no tests ran"
                r"|collected 0 items", re.IGNORECASE), "no tests found"),
    (re.compile(r"^\s*(SyntaxError|IndentationError):", re.MULTILINE), "syntax error"),
    (re.compile(r"can't open file"), "file not found"),
]


def gate_mode() -> str:
    mode = os.environ.get("CRUCIBLE_POC_GATE", "enforce").strip().lower()
    if mode == "strict":  # the pre-issue-#9 spelling
        return "enforce"
    return mode if mode in MODES else "enforce"


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def poc_gate_reasons(finding, *, provider, prepared, task_id: str = "poc") -> list[str]:
    """The gate as Pass A and Hunt's repair turn use it: failure reasons, or
    [] when the finding may proceed (passed, advisory, or off)."""
    mode = gate_mode()
    if mode == "off":
        return []
    if provider is None or prepared is None:
        if mode == "advisory":
            return []
        return [("poc_gate: running the PoC needs a sandbox — run with Docker instead of "
                 "--no-sandbox, or set CRUCIBLE_POC_GATE=advisory|off")]
    result = run_poc_gate(finding, provider=provider, prepared=prepared, task_id=task_id)
    if mode == "advisory":
        if not result.passed:
            log.info("poc_gate (advisory) %s: %s", task_id, "; ".join(result.reasons))
        return []
    return result.reasons


_cache: dict[str, GateResult] = {}
_cache_lock = threading.Lock()


def run_poc_gate(finding, *, provider, prepared, task_id: str = "poc") -> GateResult:
    """Run the gate once per distinct (PoC, patch, tree). Hunt's repair turn
    and Pass A usually ask about the same finding; the second ask is free."""
    key = hashlib.sha256((
        f"{prepared.path}\0{finding.poc_test}\0{finding.poc_filename}\0"
        f"{finding.poc_command}\0{finding.proposed_patch}"
    ).encode()).hexdigest()
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    problems = _precheck(finding, prepared)
    if problems:
        result = GateResult(False, problems)
    else:
        result = _run_in_sandbox(finding, provider, prepared, task_id)
    with _cache_lock:
        _cache[key] = result
    return result


def _precheck(finding, prepared) -> list[str]:
    if not finding.poc_command.strip():
        return ["poc_gate: no poc_command — the finding doesn't say how to run its PoC"]
    name = finding.poc_filename.strip()
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts:
        return [f"poc_gate: poc_filename must be a relative path inside the repo, got {name!r}"]
    if (Path(prepared.path) / name).exists():
        return [(f"poc_gate: poc_filename {name!r} already exists — the PoC must be a new "
                 "file, not a replacement for repo source")]
    if name in _patched_paths(finding.proposed_patch):
        return [f"poc_gate: the patch modifies the PoC file {name!r} itself"]
    return []


def _patched_paths(patch: str) -> set[str]:
    paths = set()
    for m in re.finditer(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", patch, re.MULTILINE):
        if m.group(1) != "/dev/null":
            paths.add(m.group(1))
    return paths


def _run_in_sandbox(finding, provider, prepared, task_id: str) -> GateResult:
    try:
        handle = provider.create(f"poc-{task_id}", prepared.path, POC_LIMITS)
    except Exception as e:  # noqa: BLE001 — no sandbox, no proof
        return GateResult(False, [f"poc_gate: couldn't start a sandbox: {type(e).__name__}: {e}"])
    sandbox = provider if handle is None else handle
    try:
        return _gate(sandbox, finding)
    finally:
        try:
            sandbox.destroy()
        except Exception as e:  # noqa: BLE001
            log.debug("poc_gate sandbox destroy failed (ignored): %s", e)


def _gate(sandbox, finding) -> GateResult:
    poc = shlex.quote(finding.poc_filename.strip())
    setup = _exec(sandbox, "setup", (
        f"set -e; rm -rf {_REPO}; cp -a --no-preserve=ownership /src {_REPO}; "
        f"cd {_REPO}; mkdir -p \"$(dirname {poc})\"; "
        f"printf %s {_b64(finding.poc_test)} | base64 -d > {poc}"
    ))
    if setup.exit_code != 0:
        return _fail(f"couldn't set up the PoC in the sandbox: {_tail(setup)}")

    clean = _exec(sandbox, "run_clean", _run_cmd(finding), timeout_s=POC_RUN_TIMEOUT_S + 60)
    if clean.timed_out or clean.exit_code == 124:
        return _fail(f"PoC timed out on the unmodified repo (limit {POC_RUN_TIMEOUT_S}s)")
    if clean.exit_code == 0:
        return _fail("PoC passes on the unmodified repo, so it doesn't demonstrate the bug. "
                     f"Output: {_tail(clean)}")
    why = _could_not_run(clean)
    if why:
        return _fail(f"PoC couldn't run on the unmodified repo ({why}), so its failure "
                     f"proves nothing. Output: {_tail(clean)}")
    touched = _tracked_changes(sandbox, "status_clean")
    if touched:
        return _fail(f"PoC modified tracked source files while running: {', '.join(sorted(touched))}")

    applied = _exec(sandbox, "apply", (
        f"printf %s {_b64(finding.proposed_patch)} | base64 -d > {_PATCH}; "
        f"cd {_REPO} && {_GIT} apply {_PATCH}"
    ))
    if applied.exit_code != 0:
        return _fail(f"patch didn't apply in the sandbox: {_tail(applied)}")

    patched = _exec(sandbox, "run_patched", _run_cmd(finding), timeout_s=POC_RUN_TIMEOUT_S + 60)
    if patched.timed_out or patched.exit_code == 124:
        return _fail(f"PoC timed out with the patch applied (limit {POC_RUN_TIMEOUT_S}s)")
    if patched.exit_code != 0:
        return _fail("PoC still fails with the patch applied, so either it doesn't test "
                     f"the claim or the patch doesn't fix it. Output: {_tail(patched)}")
    outside = _tracked_changes(sandbox, "status_patched") - _patched_paths(finding.proposed_patch)
    if outside:
        return _fail(f"tracked files changed outside the patch: {', '.join(sorted(outside))}")
    return GateResult(True)


def _run_cmd(finding) -> str:
    return (f"cd {_REPO} && timeout -k 5 {POC_RUN_TIMEOUT_S} "
            f"bash -c {shlex.quote(finding.poc_command.strip())}")


def _tracked_changes(sandbox, step: str) -> set[str]:
    """Tracked files that differ from the commit. Empty if git can't tell
    (a repo copied without .git) — the other gate steps still apply."""
    r = _exec(sandbox, step, f"cd {_REPO} && {_GIT} status --porcelain --untracked-files=no")
    if r.exit_code != 0:
        return set()
    return {line[3:].strip() for line in r.stdout.splitlines() if len(line) > 3}


def _exec(sandbox, step: str, cmd: str, *, timeout_s: int = 300) -> ExecResult:
    # The `: crucible:<step>;` prefix is a no-op that names the step for tests.
    return sandbox.exec(f": crucible:{step}; {cmd}", timeout_s=timeout_s)


def _could_not_run(r: ExecResult) -> str:
    if r.exit_code in (126, 127):
        return "command not found or not executable"
    text = f"{r.stdout}\n{r.stderr}"
    for pat, label in _COULD_NOT_RUN:
        if pat.search(text):
            return label
    return ""


def _fail(reason: str) -> GateResult:
    return GateResult(False, [f"poc_gate: {reason}"])


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _tail(r: ExecResult, n: int = 600) -> str:
    text = _ANSI.sub("", f"{r.stdout}\n{r.stderr}").strip()
    return ("…" + text[-n:]) if len(text) > n else (text or "(no output)")
