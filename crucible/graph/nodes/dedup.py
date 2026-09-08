"""Dedup stage (specs.md §11, issue #20).

Comparing every finding against every other with a model is O(N²) and falls
apart at scale. So this stage is two-phase, cheap part first (§1.8):

  1. **Deterministic shortlist.** Build inverted indexes over *structured*
     fields — cited file, trust boundary crossed, rare description tokens — and
     keep only the pairs that collide in enough buckets. A cross-run
     `stable_key` collision is an automatic duplicate, no model call.
  2. **Agent judge.** For the surviving shortlist, a `VALIDATOR_BUG`-model call
     decides whether *one fix at one root cause* would close both. String and
     path matching cannot answer that — it needs reasoning.

Duplicates are folded into a canonical record (`store.link_duplicate`) and
dropped from `state["finding_ids"]` so the rest of the loop never re-validates
them. Runs before `validate_mechanical` so no model spend is wasted downstream.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from crucible.graph.state import CrucibleState
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.dedup")

DEDUP_MAX_PAIRS = 40   # shortlist ceiling — deterministic, cheap
DEDUP_MAX_JUDGE = 12   # agent calls per invocation — the only model spend here

_TOKEN = re.compile(r"[a-z0-9_]{5,}")
_STOPWORDS = {
    "which", "there", "these", "those", "where", "while", "would", "could",
    "should", "about", "value", "input", "using", "without", "based", "cause",
    "attacker", "function", "method", "return", "string", "before", "after",
    "because", "through", "allows", "allow", "check", "checks", "field",
}


class DupeJudgment(BaseModel):
    """Forced structured verdict from the dedup judge."""

    same_root_cause: bool = Field(
        description="true only if ONE fix at ONE root cause would close both findings"
    )
    reason: str = Field(default="", description="one sentence, citing the shared cause or the difference")


@dataclass
class _Rec:
    finding_id: str
    run_id: str
    stable_key: str
    payload: dict
    this_run: bool

    @property
    def file_path(self) -> str:
        return str(self.payload.get("file_path", ""))

    @property
    def boundary(self) -> str:
        return str((self.payload.get("threat_model") or {}).get("boundary_crossed", ""))

    @property
    def blurb(self) -> str:
        return f"{self.payload.get('title', '')} {self.payload.get('description', '')}"


def run(state: CrucibleState, deps=None) -> CrucibleState:
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]
    this_ids = list(state.get("finding_ids") or [])
    store = getattr(deps, "store", None)

    if store is None or len(this_ids) < 1:
        log.info("dedup  skipped (findings=%d, store=%s)", len(this_ids), store is not None)
        return state

    recs = _load_records(store, run_id, this_ids)
    this_recs = [r for r in recs if r.this_run]
    if not this_recs:
        log.info("dedup  no loadable findings for this run")
        return state

    pairs = _shortlist(recs)
    log.info("dedup  %d finding(s) this run, %d in corpus, %d shortlist pair(s)",
             len(this_recs), len(recs), len(pairs))

    registry = getattr(deps, "registry", None)
    dup_of: dict[str, str] = {}
    clusters: list[dict] = []
    judged = 0
    cross_run = 0

    for a, b in pairs:
        # a is always the this-run candidate; b is the potential canonical.
        if a.finding_id in dup_of:
            continue
        basis = ""
        if a.stable_key and a.stable_key == b.stable_key:
            basis = "stable_key"
        elif registry is not None and judged < DEDUP_MAX_JUDGE:
            judged += 1
            verdict = _judge(registry, a, b)
            if verdict is not None and verdict.same_root_cause:
                basis = "agent"
        if not basis:
            continue
        canonical, dup = _orient(a, b)
        dup_of[dup.finding_id] = canonical.finding_id
        if not dup.this_run or not canonical.this_run:
            cross_run += 1
        clusters.append({
            "canonical": canonical.finding_id,
            "canonical_run": canonical.run_id,
            "duplicate": dup.finding_id,
            "basis": basis,
        })
        log.info("dedup  fold %s -> %s (%s)", dup.finding_id, canonical.finding_id, basis)

    for dup_id, canonical_id in dup_of.items():
        try:
            store.link_duplicate(dup_id, canonical_id)
        except Exception as e:  # noqa: BLE001 — folding is best-effort
            log.warning("dedup  could not link %s: %s", dup_id, e)

    state["finding_ids"] = [fid for fid in this_ids if fid not in dup_of]

    _write_clusters(ws, clusters, shortlist=len(pairs), judged=judged)
    commit_node(ws, "dedup", run_id)
    log.info("dedup done  shortlist=%d judged=%d folded=%d (cross_run=%d)  findings_out=%d",
             len(pairs), judged, len(dup_of), cross_run, len(state["finding_ids"]))
    return state


# --------------------------------------------------------------------- records


def _load_records(store, run_id: str, this_ids: list[str]) -> list[_Rec]:
    recs: list[_Rec] = []
    for fid in this_ids:
        row = store.get_finding(fid)
        if row is not None and isinstance(row.payload, dict):
            recs.append(_Rec(row.finding_id, row.run_id, row.stable_key or "", row.payload, True))
    try:
        for row in store.other_run_findings(run_id):
            if isinstance(row.payload, dict) and row.status != "duplicate":
                recs.append(_Rec(row.finding_id, row.run_id, row.stable_key or "", row.payload, False))
    except Exception as e:  # noqa: BLE001 — cross-run corpus is optional
        log.debug("dedup  cross-run corpus unavailable: %s", e)
    return recs


# ------------------------------------------------------------------- shortlist


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _rare_tokens(recs: list[_Rec]) -> dict[str, set[str]]:
    """Per-record token set, restricted to tokens that are distinctive across
    the corpus (low document frequency)."""
    per: dict[str, set[str]] = {}
    df: dict[str, int] = {}
    for r in recs:
        toks = {t for t in _TOKEN.findall(r.blurb.lower()) if t not in _STOPWORDS}
        per[r.finding_id] = toks
        for t in toks:
            df[t] = df.get(t, 0) + 1
    ceiling = max(2, int(0.4 * len(recs)))
    return {fid: {t for t in toks if df.get(t, 0) <= ceiling} for fid, toks in per.items()}


def _buckets(r: _Rec, rare: set[str]) -> set[str]:
    b = {f"file:{r.file_path}"} if r.file_path else set()
    if r.boundary:
        b.add(f"bound:{_norm(r.boundary)}")
    b |= {f"tok:{t}" for t in rare}
    return b


def _shortlist(recs: list[_Rec]) -> list[tuple[_Rec, _Rec]]:
    """Candidate pairs: same cited file, or same boundary plus a shared rare
    token. At least one side must be from this run."""
    rare = _rare_tokens(recs)
    bmap = {r.finding_id: _buckets(r, rare.get(r.finding_id, set())) for r in recs}
    scored: list[tuple[int, _Rec, _Rec]] = []
    this_recs = [r for r in recs if r.this_run]
    for i, a in enumerate(this_recs):
        # pair each this-run finding with every later this-run finding and with
        # every cross-run record; `a` is always the this-run side.
        for b in this_recs[i + 1:] + [r for r in recs if not r.this_run]:
            shared = bmap[a.finding_id] & bmap[b.finding_id]
            same_file = any(x.startswith("file:") for x in shared)
            same_bound = any(x.startswith("bound:") for x in shared)
            shared_tok = any(x.startswith("tok:") for x in shared)
            if same_file or (same_bound and shared_tok):
                scored.append((len(shared), a, b))
    scored.sort(key=lambda t: -t[0])
    return [(a, b) for _, a, b in scored[:DEDUP_MAX_PAIRS]]


def _orient(a: _Rec, b: _Rec) -> tuple[_Rec, _Rec]:
    """Return (canonical, duplicate). Prefer an earlier run as canonical; within
    one run keep the lexicographically smaller id."""
    if a.this_run and not b.this_run:
        return b, a
    if b.this_run and not a.this_run:
        return a, b
    return (a, b) if a.finding_id <= b.finding_id else (b, a)


# ----------------------------------------------------------------- agent judge


def _judge(registry, a: _Rec, b: _Rec) -> DupeJudgment | None:
    from langchain_core.messages import HumanMessage, SystemMessage

    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    try:
        system = load_skill("dedup/judge.md")
    except (FileNotFoundError, OSError):
        system = "Decide whether one code fix at one root cause closes both findings."

    ask = (
        f"{system}\n\n"
        f"--- Finding A ({a.finding_id}) ---\n{_describe(a)}\n\n"
        f"--- Finding B ({b.finding_id}) ---\n{_describe(b)}\n\n"
        f"Call `{DupeJudgment.__name__}` once."
    )
    try:
        model = registry.chat_model(ModelRole.VALIDATOR_BUG)
        bound = model.bind_tools([DupeJudgment], tool_choice=DupeJudgment.__name__)
        out = bound.invoke([SystemMessage(content=system), HumanMessage(content=ask)])
        calls = getattr(out, "tool_calls", None) or []
        if not calls:
            return None
        return DupeJudgment.model_validate(calls[0]["args"])
    except (ValidationError, RuntimeError, ValueError, KeyError, TypeError) as e:
        log.debug("dedup  judge call failed: %s", e)
        return None
    except Exception as e:  # noqa: BLE001 — a judge failure is never a run failure
        log.debug("dedup  judge call errored: %s", e)
        return None


def _describe(r: _Rec) -> str:
    tm = r.payload.get("threat_model") or {}
    return (
        f"file: {r.file_path}:{r.payload.get('line_start')}-{r.payload.get('line_end')}\n"
        f"attacker: {tm.get('attacker', '')}\n"
        f"boundary: {tm.get('boundary_crossed', '')}\n"
        f"assumption broken: {tm.get('assumption_broken', '')}\n"
        f"title: {r.payload.get('title', '')}\n"
        f"description: {str(r.payload.get('description', ''))[:600]}"
    )


def _write_clusters(ws: Path, clusters: list[dict], *, shortlist: int, judged: int) -> None:
    out = ws / "dedup"
    out.mkdir(parents=True, exist_ok=True)
    (out / "clusters.json").write_text(json.dumps(
        {"shortlist_pairs": shortlist, "judge_calls": judged,
         "folded": len(clusters), "clusters": clusters},
        indent=2,
    ))
