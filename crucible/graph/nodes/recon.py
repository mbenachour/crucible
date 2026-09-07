"""Recon stage — R0 seed → R1 map → R2 threat model → R3 decompose (issue #5).

One LangGraph node. R0 and R3 are deterministic (no model). R1 and R2 use
`build_agent` (LangChain `create_agent` + our middleware) on `ModelRole.RECON`.
If no registry is available (graph compile / deterministic test) or a model
call fails, R1/R2 are skipped and R3 still produces a hunt queue from the seed
alone — every failure is logged to `workspace/recon/errors.jsonl` (VVAH's
pattern), the run continues.

Artifacts written to the workspace:
  recon/seed.json            R0
  architecture.md            R1 (rendered from seed + contributions)
  recon/threat_model.json    R2
  recon/task_manifest.json   R3
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from crucible.config import MODEL_CALLS_PER_TASK
from crucible.graph.state import CrucibleState
from crucible.obs import span
from crucible.recon.decompose import decompose, render_architecture, task_cap
from crucible.recon.schema import MapContribution, Seed, ThreatModel
from crucible.recon.seed import build_seed
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.recon")

RECON_SUBAGENTS = 3
# Every agent loop iteration costs 4 LangGraph super-steps
# (ModelCallLimitMiddleware.before_model → model → after_model → tools), not 2,
# so the recursion limit must sit above 4x the model-call cap (plus a margin for
# start / structured-output / after_agent steps). Otherwise a model that keeps
# calling read tools without emitting structured output trips a hard
# GraphRecursionError before ModelCallLimitMiddleware's graceful "end" fires.
RECON_RECURSION_LIMIT = 4 * MODEL_CALLS_PER_TASK + 20


def run(state: CrucibleState, deps=None) -> CrucibleState:
    repo = state["repo_path"]
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]
    recon_dir = ws / "recon"
    recon_dir.mkdir(parents=True, exist_ok=True)
    errors_path = recon_dir / "errors.jsonl"

    log.info("recon start  repo=%s", repo)

    # ---- R0: deterministic seed -------------------------------------
    with span("recon.r0_seed"):
        t0 = time.monotonic()
        seed = build_seed(repo)
        (recon_dir / "seed.json").write_text(seed.model_dump_json(indent=2))
    log.info(
        "R0 seed  %.1fs  kind=%s lang=%s files=%d entry_points=%d reflection=%d call_edges=%d",
        time.monotonic() - t0, seed.repo_kind.value, seed.primary_language,
        seed.stats.get("files", 0), seed.stats.get("entry_points", 0),
        seed.stats.get("reflection_facts", 0), seed.stats.get("call_edges", 0),
    )

    # ---- R1: map (model, fan-out over slices) ---------------------
    contributions: list[MapContribution] = []
    threat_model: ThreatModel | None = None
    registry = getattr(deps, "registry", None)

    if registry is not None:
        with span("recon.r1_map"):
            t0 = time.monotonic()
            contributions = _run_map(seed, repo, run_id, deps, errors_path)
        log.info("R1 map  %.1fs  %d/%d slices contributed",
                 time.monotonic() - t0, len(contributions), RECON_SUBAGENTS)
    else:
        log.info("R1 map  skipped (no registry) — using the seed-only map")
    architecture_md = render_architecture(seed, contributions)
    (ws / "architecture.md").write_text(architecture_md)

    # ---- R2: threat model (model) --------------------------------
    if registry is not None:
        with span("recon.r2_threatmodel"):
            t0 = time.monotonic()
            threat_model = _run_threatmodel(seed, repo, architecture_md, run_id, deps, errors_path)
        if threat_model is not None:
            (recon_dir / "threat_model.json").write_text(threat_model.model_dump_json(indent=2))
            log.info(
                "R2 threat model  %.1fs  attackers=%d assets=%d stride=%d repo_specific=%d",
                time.monotonic() - t0, len(threat_model.attackers), len(threat_model.assets),
                len(threat_model.stride), len(threat_model.repo_specific_classes),
            )
        else:
            log.warning("R2 threat model  %.1fs  failed — see recon/errors.jsonl",
                        time.monotonic() - t0)

    # ---- R3: decompose (deterministic) --------------------------
    cap = task_cap(seed)
    with span("recon.r3_decompose"):
        chunks = decompose(seed, threat_model, cap)
    (recon_dir / "task_manifest.json").write_text(
        json.dumps(
            {"cap": cap, "count": len(chunks),
             "chunks": [c.model_dump(mode="json") for c in chunks]},
            indent=2,
        )
    )

    state["architecture_path"] = str(ws / "architecture.md")
    state["taxonomy_path"] = str(recon_dir / "threat_model.json")
    state["pending_hunts"] = [
        {
            "task_id": f"h{i:04d}",
            "area": c.area,
            "attack_class": c.attack_class,
            "scope_hint": c.scope_hint,
            "chunk_type": c.chunk_type.value,
            "seed_path": c.seed_path,
            "continuation_count": 0,
        }
        for i, c in enumerate(chunks)
    ]
    from collections import Counter

    log.info(
        "R3 decompose  %d/%d chunks queued  %s",
        len(chunks), cap, dict(Counter(c.chunk_type.value for c in chunks)),
    )
    commit_node(ws, "recon", run_id)
    log.info("recon done  pending_hunts=%d", len(state["pending_hunts"]))
    return state


# --------------------------------------------------------------------- helpers


def _area(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) > 1 else "."


def _slice_repo(seed: Seed, n: int) -> list[dict]:
    """Group source files by top-level area, pack areas into <= n LOC-balanced buckets."""
    by_area: dict[str, list] = {}
    for f in seed.files:
        if f.role == "source":
            by_area.setdefault(_area(f.path), []).append(f)
    if not by_area:
        return []
    areas = sorted(by_area, key=lambda a: -sum(f.loc for f in by_area[a]))
    k = max(1, min(n, len(areas)))
    buckets: list[list[str]] = [[] for _ in range(k)]
    load = [0] * k
    for a in areas:
        i = load.index(min(load))
        buckets[i].append(a)
        load[i] += sum(f.loc for f in by_area[a])
    out = []
    for b in buckets:
        if not b:
            continue
        files = [f.path for a in b for f in by_area[a]]
        out.append({"name": "+".join(b), "areas": b, "files": files})
    return out


def _seed_eps_for(seed: Seed, files: set[str]) -> list[str]:
    return [
        f"{ep.file}:{ep.line} {ep.kind.value}"
        + (f" ({ep.framework})" if ep.framework else "")
        + (f" — {ep.evidence}" if ep.evidence else "")
        for ep in seed.entry_points
        if ep.file in files
    ]


def _map_task_text(sl: dict, seed: Seed) -> str:
    files = "\n".join(f"- {p}" for p in sl["files"][:80])
    eps = _seed_eps_for(seed, set(sl["files"]))
    eps_txt = "\n".join(f"- {e}" for e in eps[:60]) or "(none found by the seed)"
    return (
        f"Repo kind: {seed.repo_kind.value}. Primary language: {seed.primary_language}. "
        f"Frameworks: {', '.join(seed.frameworks) or 'none'}.\n\n"
        f"Slice '{sl['name']}' — files:\n{files}\n\n"
        f"Seed entry points in this slice:\n{eps_txt}\n\n"
        "Refine this into the structured map for this slice."
    )


def _run_map(seed: Seed, repo: str, run_id: str, deps, errors_path: Path) -> list[MapContribution]:
    from crucible.agents.core import build_agent
    from crucible.agents.tools import read_only_fs_tools
    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    tools = read_only_fs_tools(repo)
    allowed = {t.name for t in tools}
    prompt = load_skill("recon/map.md")
    out: list[MapContribution] = []

    for sl in _slice_repo(seed, RECON_SUBAGENTS):
        try:
            agent = build_agent(
                role=ModelRole.RECON, registry=deps.registry, run_id=run_id,
                tools=tools, system_prompt=prompt, allowed_tools=allowed,
                store=getattr(deps, "store", None),
                response_format=MapContribution, summarize=False,
            )
            res = agent.invoke(
                {"messages": [("user", _map_task_text(sl, seed))]},
                config={
                    "configurable": {"thread_id": f"{run_id}:recon:map:{sl['name']}"},
                    "recursion_limit": RECON_RECURSION_LIMIT,
                },
            )
            mc = res.get("structured_response")
            if isinstance(mc, MapContribution):
                mc.slice_name = sl["name"]
                out.append(mc)
            else:
                _log_error(errors_path, "R1", sl["name"], "no structured_response")
        except Exception as e:  # noqa: BLE001 — resilience: log and continue
            _log_error(errors_path, "R1", sl["name"], _fmt_exc(e))
    return out


def _threatmodel_task_text(seed: Seed, architecture_md: str) -> str:
    return (
        f"Repo kind: {seed.repo_kind.value}. Primary language: {seed.primary_language}. "
        f"Frameworks: {', '.join(seed.frameworks) or 'none'}.\n\n"
        f"Architecture map:\n\n{architecture_md[:12_000]}\n\n"
        f"Static seed stats: {json.dumps(seed.stats)}.\n\n"
        "Build the threat model."
    )


def _run_threatmodel(
    seed: Seed, repo: str, architecture_md: str, run_id: str, deps, errors_path: Path
) -> ThreatModel | None:
    from crucible.agents.core import build_agent
    from crucible.agents.tools import read_only_fs_tools
    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    tools = read_only_fs_tools(repo)
    try:
        agent = build_agent(
            role=ModelRole.RECON, registry=deps.registry, run_id=run_id,
            tools=tools, system_prompt=load_skill("recon/threatmodel.md"),
            allowed_tools={t.name for t in tools},
            store=getattr(deps, "store", None),
            response_format=ThreatModel, summarize=False,
        )
        res = agent.invoke(
            {"messages": [("user", _threatmodel_task_text(seed, architecture_md))]},
            config={
                "configurable": {"thread_id": f"{run_id}:recon:threatmodel"},
                "recursion_limit": RECON_RECURSION_LIMIT,
            },
        )
        tm = res.get("structured_response")
        if isinstance(tm, ThreatModel):
            return tm
        _log_error(errors_path, "R2", "threatmodel", "no structured_response")
    except Exception as e:  # noqa: BLE001
        _log_error(errors_path, "R2", "threatmodel", _fmt_exc(e))
    return None


def _fmt_exc(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def _log_error(path: Path, stage: str, unit: str, detail: str) -> None:
    log.warning("%s[%s] failed: %s", stage, unit, detail[:300])
    with path.open("a") as fh:
        fh.write(json.dumps({"stage": stage, "unit": unit, "detail": detail[:2000]}) + "\n")
