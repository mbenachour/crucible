"""Validate Pass B — "is it real?" (specs.md §9.4).

VALIDATOR_BUG, a structurally different open-weight model from the Hunter
(§6). Re-reads the code with a disprove-oriented prompt and returns a verdict
only — no finding-creation tool, no write access to the findings table (§1.6).

Funnel: `mechanical_passed` -> `bug_upheld` | `bug_refuted`. A judge that
cannot be reached or cannot produce a parseable verdict **upholds** the finding
(a failed disprove is not a refutation) and records why.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from crucible.graph.state import CrucibleState
from crucible.obs import span

log = logging.getLogger("crucible.validate_bug")

PASS_NAME = "bug"
_CANDIDATE_STATUSES = ["mechanical_passed"]
_EXCERPT_CONTEXT = 40  # lines either side of the cited range


class BugVerdict(BaseModel):
    verdict: str = Field(description="'upheld' if the defect is real, else 'refuted'")
    reasoning: str = Field(default="", description="why, citing specific lines")


def run(state: CrucibleState, deps=None) -> CrucibleState:
    store = getattr(deps, "store", None)
    registry = getattr(deps, "registry", None)
    run_id = state["run_id"]
    repo = state["repo_path"]

    if store is None:
        log.info("validate_bug  no store — skipping")
        return state

    rows = store.run_findings(run_id, statuses=_CANDIDATE_STATUSES)
    if not rows:
        log.info("validate_bug  0 candidate(s)")
        return state

    model = None
    model_id = ""
    prompt_version = _prompt_version()
    if registry is not None:
        try:
            from crucible.llm.registry import ModelRole

            model = registry.chat_model(ModelRole.VALIDATOR_BUG)
            ep = registry.endpoint(ModelRole.VALIDATOR_BUG)
            model_id = f"{ep.provider.value}:{ep.model}"
        except Exception as e:  # noqa: BLE001
            log.warning("validate_bug  no model (%s) — every candidate upheld unreviewed", e)

    upheld = refuted = 0
    with span("validate_bug", run_id=run_id, candidates=len(rows)):
        for row in rows:
            verdict, reasoning, rclass = _judge(model, repo, row)
            upheld += verdict == "upheld"
            refuted += verdict == "refuted"
            _persist(store, row.finding_id, verdict, reasoning, model_id, prompt_version, rclass)
            log.info("validate_bug  %s  %-8s %s", row.finding_id, verdict, reasoning[:100])

    log.info("validate_bug  %d candidate(s): %d upheld, %d refuted", len(rows), upheld, refuted)
    return state


# --------------------------------------------------------------------- internals


def _judge(model, repo: str, row) -> tuple[str, str, str]:
    """Returns (verdict, reasoning, response_class). No model -> upheld."""
    if model is None:
        return "upheld", "no VALIDATOR_BUG model available — not reviewed", ""

    from langchain_core.messages import HumanMessage, SystemMessage

    from crucible.llm.classify import classify

    system = _system_prompt()
    ask = f"{system}\n\n{_describe(repo, row)}\n\nCall `{BugVerdict.__name__}` exactly once."
    try:
        bound = model.bind_tools([BugVerdict], tool_choice=BugVerdict.__name__)
        out = bound.invoke([SystemMessage(content=system), HumanMessage(content=ask)])
        rclass = classify(getattr(out, "content", "") or "", expect_json=False).value
        calls = getattr(out, "tool_calls", None) or []
        if not calls:
            return "upheld", "judge returned no verdict — upheld unreviewed", rclass
        v = BugVerdict.model_validate(calls[0]["args"])
        verdict = "refuted" if v.verdict.strip().lower().startswith("refut") else "upheld"
        return verdict, (v.reasoning or "").strip()[:2000], rclass
    except (ValidationError, RuntimeError, ValueError, KeyError, TypeError) as e:
        return "upheld", f"judge call failed ({type(e).__name__}) — upheld unreviewed", ""
    except Exception as e:  # noqa: BLE001 — a judge failure never fails the run
        return "upheld", f"judge errored ({type(e).__name__}) — upheld unreviewed", ""


def _describe(repo: str, row) -> str:
    p = row.payload or {}
    tm = p.get("threat_model") or {}
    ls, le = int(p.get("line_start", 1)), int(p.get("line_end", 1))
    excerpt = _excerpt(Path(repo) / str(p.get("file_path", "")), ls, le)
    return (
        f"--- Finding {row.finding_id} ---\n"
        f"file: {p.get('file_path')}:{ls}-{le}\n"
        f"title: {p.get('title', '')}\n"
        f"severity: {p.get('severity', '')}\n"
        f"attacker: {tm.get('attacker', '')}\n"
        f"boundary crossed: {tm.get('boundary_crossed', '')}\n"
        f"assumption broken: {tm.get('assumption_broken', '')}\n"
        f"description: {str(p.get('description', ''))[:1200]}\n"
        f"poc_test:\n{str(p.get('poc_test', ''))[:1500]}\n\n"
        f"--- source {p.get('file_path')}:{max(1, ls - _EXCERPT_CONTEXT)}+ ---\n{excerpt}"
    )


def _excerpt(path: Path, line_start: int, line_end: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return "(source unavailable)"
    lo = max(0, line_start - 1 - _EXCERPT_CONTEXT)
    hi = min(len(lines), line_end + _EXCERPT_CONTEXT)
    return "\n".join(f"{i + 1}: {lines[i]}" for i in range(lo, hi))[:6000]


def _system_prompt() -> str:
    from crucible.skills import load_skill

    try:
        return load_skill("validate/bug.md")
    except (FileNotFoundError, OSError):
        return (
            "You are a second, independent reviewer on a different model from the "
            "Hunter. A finding has been filed. Try to DISPROVE it. Output a verdict "
            "'upheld' or 'refuted' with reasoning citing specific lines."
        )


def _prompt_version() -> str:
    from crucible.skills import skill_front_matter

    try:
        return f"validate_bug@{skill_front_matter('validate/bug.md').get('version', '0')}"
    except (FileNotFoundError, OSError):
        return "validate_bug@fallback"


def _persist(store, finding_id, verdict, reasoning, model_id, prompt_version, rclass) -> None:
    from crucible.store.models import ValidationRow

    try:
        store.set_finding_status(finding_id, f"bug_{verdict}")
        store.record_validation(ValidationRow(
            finding_id=finding_id, pass_name=PASS_NAME, verdict=verdict,
            reasoning=reasoning[:2000], model=model_id,
            prompt_version=prompt_version, response_class=rclass,
        ))
    except Exception as e:  # noqa: BLE001 — persistence failure must not crash the loop
        log.warning("validate_bug  could not persist %s: %s", finding_id, e)
