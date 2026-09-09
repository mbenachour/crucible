"""Validate Pass C — "is it reachable?" (specs.md §9.4).

VALIDATOR_REACH. Separate call, separate agent from Pass B: can
attacker-controlled input actually reach this code from outside the system?
Splitting it from Pass B is deliberate (§1.7) — each question asked alone is
narrower and the model is better at it.

Funnel: `bug_upheld` -> `reach_upheld` | `reach_refuted`. Same conservative rule
as Pass B — a judge that cannot run upholds and records why. Phase 1 is
single-repo; Phase 3 replaces this with the cross-repo Tracer (#24).
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from crucible.graph.state import CrucibleState
from crucible.obs import span

log = logging.getLogger("crucible.validate_reachability")

PASS_NAME = "reachability"
_CANDIDATE_STATUSES = ["bug_upheld"]
_EXCERPT_CONTEXT = 40


class ReachVerdict(BaseModel):
    verdict: str = Field(description="'upheld' if attacker input can reach the sink, else 'refuted'")
    reasoning: str = Field(default="", description="the path from an external entry point, or why there is none")


def run(state: CrucibleState, deps=None) -> CrucibleState:
    store = getattr(deps, "store", None)
    registry = getattr(deps, "registry", None)
    run_id = state["run_id"]
    repo = state["repo_path"]

    if store is None:
        log.info("validate_reachability  no store — skipping")
        return state

    rows = store.run_findings(run_id, statuses=_CANDIDATE_STATUSES)
    if not rows:
        log.info("validate_reachability  0 candidate(s)")
        return state

    arch_md = ""
    ap = state.get("architecture_path") or ""
    if ap and Path(ap).is_file():
        arch_md = Path(ap).read_text(errors="replace")[:8000]

    model = None
    model_id = ""
    prompt_version = _prompt_version()
    if registry is not None:
        try:
            from crucible.llm.registry import ModelRole

            model = registry.chat_model(ModelRole.VALIDATOR_REACH)
            ep = registry.endpoint(ModelRole.VALIDATOR_REACH)
            model_id = f"{ep.provider.value}:{ep.model}"
        except Exception as e:  # noqa: BLE001
            log.warning("validate_reachability  no model (%s) — every candidate upheld unreviewed", e)

    upheld = refuted = 0
    with span("validate_reachability", run_id=run_id, candidates=len(rows)):
        for row in rows:
            verdict, reasoning, rclass = _judge(model, repo, arch_md, row)
            upheld += verdict == "upheld"
            refuted += verdict == "refuted"
            _persist(store, row.finding_id, verdict, reasoning, model_id, prompt_version, rclass)
            log.info("validate_reachability  %s  %-8s %s", row.finding_id, verdict, reasoning[:100])

    log.info("validate_reachability  %d candidate(s): %d upheld, %d refuted",
             len(rows), upheld, refuted)
    return state


# --------------------------------------------------------------------- internals


def _judge(model, repo: str, arch_md: str, row) -> tuple[str, str, str]:
    if model is None:
        return "upheld", "no VALIDATOR_REACH model available — not reviewed", ""

    from langchain_core.messages import HumanMessage, SystemMessage

    from crucible.llm.classify import classify

    system = _system_prompt()
    arch_block = f"\n\n--- architecture.md (Recon output) ---\n{arch_md}\n" if arch_md else ""
    ask = (
        f"{system}\n\n{_describe(repo, row)}{arch_block}\n\n"
        f"Call `{ReachVerdict.__name__}` exactly once."
    )
    try:
        bound = model.bind_tools([ReachVerdict], tool_choice=ReachVerdict.__name__)
        out = bound.invoke([SystemMessage(content=system), HumanMessage(content=ask)])
        rclass = classify(getattr(out, "content", "") or "", expect_json=False).value
        calls = getattr(out, "tool_calls", None) or []
        if not calls:
            return "upheld", "judge returned no verdict — upheld unreviewed", rclass
        v = ReachVerdict.model_validate(calls[0]["args"])
        verdict = "refuted" if v.verdict.strip().lower().startswith("refut") else "upheld"
        return verdict, (v.reasoning or "").strip()[:2000], rclass
    except (ValidationError, RuntimeError, ValueError, KeyError, TypeError) as e:
        return "upheld", f"judge call failed ({type(e).__name__}) — upheld unreviewed", ""
    except Exception as e:  # noqa: BLE001
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
        f"attacker: {tm.get('attacker', '')}\n"
        f"boundary crossed: {tm.get('boundary_crossed', '')}\n"
        f"description: {str(p.get('description', ''))[:1000]}\n\n"
        f"--- source {p.get('file_path')} ---\n{excerpt}"
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
        return load_skill("validate/reachability.md")
    except (FileNotFoundError, OSError):
        return (
            "Decide whether attacker-controlled input can actually reach this code "
            "from outside the system. Trace from an external entry point to the "
            "cited line. Output 'upheld' or 'refuted' with the path or the reason "
            "there is none."
        )


def _prompt_version() -> str:
    from crucible.skills import skill_front_matter

    try:
        return f"validate_reach@{skill_front_matter('validate/reachability.md').get('version', '0')}"
    except (FileNotFoundError, OSError):
        return "validate_reach@fallback"


def _persist(store, finding_id, verdict, reasoning, model_id, prompt_version, rclass) -> None:
    from crucible.store.models import ValidationRow

    try:
        store.set_finding_status(finding_id, f"reach_{verdict}")
        store.record_validation(ValidationRow(
            finding_id=finding_id, pass_name=PASS_NAME, verdict=verdict,
            reasoning=reasoning[:2000], model=model_id,
            prompt_version=prompt_version, response_class=rclass,
        ))
    except Exception as e:  # noqa: BLE001
        log.warning("validate_reachability  could not persist %s: %s", finding_id, e)
