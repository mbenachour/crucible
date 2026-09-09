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
import threading
import time
from pathlib import Path

from crucible.config import (
    MODEL_CALLS_PER_TASK,
    RECON_MAX_PARALLEL,
    RECON_MAX_SUBAGENTS,
    RECON_ORIENT_READ_BUDGET,
)
from crucible.graph.state import CrucibleState
from crucible.obs import span
from crucible.recon.decompose import (
    decompose,
    render_architecture,
    subsystem_rows,
    task_cap,
)
from crucible.recon.orient import partition_subsystems
from crucible.recon.schema import ModuleMap, Seed, SubsystemMap, ThreatModel
from crucible.recon.seed import build_seed
from crucible.recon.synthesize import derive_auth_model, rank_attack_surface
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.recon")

# Phase-A exploration budget for each recon agent. deepseek-v4-flash will read
# files until something stops it and (left to a ToolStrategy) almost never calls
# the emit-tool on its own, so recon runs it in two phases: a plain ReAct agent
# explores under this budget, then one forced `tool_choice=<schema>` call emits
# the typed result (see `_explore` / `_emit`). The §8 hard cap
# (MODEL_CALLS_PER_TASK) still bounds it; this lower value just keeps recon fast.
RECON_EXPLORE_LIMIT = 16
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

    registry = getattr(deps, "registry", None)
    threat_model: ThreatModel | None = None

    # ---- R1a: orient — lead agent's top-down read (ModuleMap) ----
    module_map = None
    if registry is not None:
        with span("recon.r1a_orient"):
            t0 = time.monotonic()
            module_map = _run_orient(seed, repo, run_id, deps, errors_path)
        if module_map is not None:
            (recon_dir / "module_map.json").write_text(module_map.model_dump_json(indent=2))
            log.info("R1a orient  %.1fs  %d subsystem(s) proposed", time.monotonic() - t0,
                     len(module_map.subsystems))
        else:
            log.warning("R1a orient  %.1fs  failed — deterministic partition",
                        time.monotonic() - t0)

    partition = partition_subsystems(
        seed, module_map, max_subagents=RECON_MAX_SUBAGENTS, repo_path=repo
    )
    _validate_coverage(seed, partition)
    src_kind = (partition[0].get("source") if partition else None) or "fallback"
    log.info("R1a partition  %d subsystem(s) via %s  %s", len(partition), src_kind,
             ", ".join(f"{p['name']}({p['loc']})" for p in partition[:8]))

    # ---- R1b: subsystem maps (model, fan-out per subsystem) -----
    subsystem_maps: list[SubsystemMap] = []
    if registry is not None and partition:
        par = max(1, min(RECON_MAX_PARALLEL, len(partition)))
        with span("recon.r1b_map"):
            t0 = time.monotonic()
            subsystem_maps = _run_map(seed, repo, run_id, deps, errors_path, partition)
        log.info("R1b subsystem maps  %.1fs  %d/%d subsystems contributed  (parallel=%d)",
                 time.monotonic() - t0, len(subsystem_maps), len(partition), par)
    else:
        log.info("R1b subsystem maps  skipped (no registry) — seed-only map")

    # ---- R1c: synthesis (deterministic) ------------------------
    auth_model = derive_auth_model(seed, subsystem_maps, module_map)
    # draft doc (no ranked surface / quality yet) — this is R2's input
    architecture_md = render_architecture(
        seed, subsystem_maps, partition=partition, module_map=module_map,
        auth_model=auth_model,
    )
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

    # ---- R1c: finalise synthesis with the threat model ---------
    quality = _recon_quality(registry, partition, subsystem_maps, threat_model)
    attack_surface = rank_attack_surface(
        seed, subsystem_maps, module_map, threat_model, partition
    )
    architecture_md = render_architecture(
        seed, subsystem_maps, partition=partition, module_map=module_map,
        auth_model=auth_model, attack_surface=attack_surface, quality=quality,
    )
    (ws / "architecture.md").write_text(architecture_md)
    (recon_dir / "attack_surface.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in attack_surface], indent=2)
    )
    (recon_dir / "recon_quality.txt").write_text(quality)
    subsystems = subsystem_rows(partition, seed)
    (recon_dir / "subsystems.json").write_text(json.dumps(subsystems, indent=2))
    state["recon_quality"] = quality
    state["subsystems"] = subsystems
    top = "; ".join(f"{a.target}={a.score:g}" for a in attack_surface[:3]) or "(none)"
    log.info("R1c synthesis  quality=%s  subsystems=%d  attack_surface=%d  top: %s",
             quality, len(subsystems), len(attack_surface), top)

    # ---- R3: decompose (deterministic) --------------------------
    cap = task_cap(seed)
    with span("recon.r3_decompose"):
        chunks = decompose(seed, threat_model, cap,
                           partition=partition, subsystem_maps=subsystem_maps,
                           attack_surface=attack_surface)
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

    xtaint = sum(1 for c in chunks if c.chunk_type.value == "taint" and c.seed_path
                 and c.seed_path.split(" -> ")[0].split(":")[0]
                 != c.seed_path.split(" -> ")[-1].split(":")[0])
    log.info("R3 decompose  areas=%s  cross-subsystem taint=%d",
             sorted({c.area for c in chunks})[:8], xtaint)
    log.info(
        "R3 decompose  %d/%d chunks queued  %s",
        len(chunks), cap, dict(Counter(c.chunk_type.value for c in chunks)),
    )
    commit_node(ws, "recon", run_id)
    log.info("recon done  pending_hunts=%d", len(state["pending_hunts"]))
    return state


# --------------------------------------------------------------------- helpers


def _validate_coverage(seed: Seed, partition: list[dict]) -> None:
    """Hard check: every source file maps to exactly one subsystem. Raises so a
    partitioner bug fails the run loudly rather than silently dropping code."""
    src = {f.path for f in seed.files if f.role == "source"}
    if not src:
        return
    assigned: dict[str, int] = {}
    for p in partition:
        for f in p["files"]:
            assigned[f] = assigned.get(f, 0) + 1
    missing = src - assigned.keys()
    dup = {f for f, n in assigned.items() if n > 1}
    extra = assigned.keys() - src
    if missing or dup or extra:
        raise RuntimeError(
            f"partition coverage broken: {len(missing)} unassigned, "
            f"{len(dup)} double-assigned, {len(extra)} unknown "
            f"(e.g. missing={sorted(missing)[:3]} dup={sorted(dup)[:3]})"
        )


def _seed_eps_for(seed: Seed, files: set[str]) -> list[str]:
    return [
        f"{ep.file}:{ep.line} {ep.kind.value}"
        + (f" ({ep.framework})" if ep.framework else "")
        + (f" — {ep.evidence}" if ep.evidence else "")
        for ep in seed.entry_points
        if ep.file in files
    ]


def _map_task_text(sub: dict, siblings: list[dict], seed: Seed) -> str:
    files = "\n".join(f"- {p}" for p in sub["files"][:80])
    eps = _seed_eps_for(seed, set(sub["files"]))
    eps_txt = "\n".join(f"- {e}" for e in eps[:60]) or "(none found by the seed)"
    sib_txt = "\n".join(
        f"- {s['name']}: {s.get('responsibility') or '(no responsibility stated)'}"
        for s in siblings if s["name"] != sub["name"]
    ) or "(none — single subsystem)"
    face = "external-facing" if sub.get("external_facing") else "internal"
    return (
        f"Repo kind: {seed.repo_kind.value}. Primary language: {seed.primary_language}. "
        f"Frameworks: {', '.join(seed.frameworks) or 'none'}.\n\n"
        f"Subsystem '{sub['name']}' ({face}) — {sub.get('responsibility') or 'responsibility not stated'}\n"
        f"Files:\n{files}\n\n"
        f"Seed entry points in this subsystem:\n{eps_txt}\n\n"
        f"Neighbouring subsystems (name cross-boundary flows against these):\n{sib_txt}\n\n"
        "Refine this into the structured SubsystemMap. For data_flows, use "
        "'A/x.py:10 -> B/y.py:88' form and name the neighbour when a flow leaves "
        "this subsystem."
    )


def _recon_quality(registry, partition, subsystem_maps, threat_model) -> str:
    """full  = every subsystem contributed a map AND R2 produced real content
       partial = some maps, or R2 empty / missing
       seed_only = no registry, or not one usable map.

    An R2 model that returns a parseable-but-empty ThreatModel counts as a soft
    failure — `full` requires at least one attacker / STRIDE row / repo class."""
    if registry is None or not subsystem_maps:
        return "seed_only"
    r2_ok = threat_model is not None and bool(
        threat_model.stride or threat_model.attackers or threat_model.repo_specific_classes
    )
    if len(subsystem_maps) >= len(partition) and r2_ok:
        return "full"
    return "partial"


def _orient_task_text(seed: Seed, repo: str) -> str:
    src = sorted(
        ((f.path, f.loc) for f in seed.files if f.role == "source"),
        key=lambda t: -t[1],
    )
    by_top: dict[str, int] = {}
    for path, loc in src:
        by_top[path.split("/")[0]] = by_top.get(path.split("/")[0], 0) + loc
    tops = "\n".join(f"- {d}/  (~{loc} LOC)" for d, loc in
                     sorted(by_top.items(), key=lambda t: -t[1])[:20])
    manifests = sorted(
        f.path for f in seed.files
        if f.path.rsplit("/", 1)[-1] in {
            "package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml",
            "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
        }
    )
    man_txt = "\n".join(f"- {m}" for m in manifests[:40]) or "(none)"
    ep_files = sorted({ep.file for ep in seed.entry_points})
    ep_txt = "\n".join(f"- {p}" for p in ep_files[:40]) or "(none found by the seed)"
    codeowners = "yes" if any(
        (Path(repo) / c).is_file() for c in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
    ) else "no"
    return (
        f"Repo kind: {seed.repo_kind.value}. Primary language: {seed.primary_language}. "
        f"Frameworks: {', '.join(seed.frameworks) or 'none'}. CODEOWNERS present: {codeowners}.\n\n"
        f"Top-level source areas by LOC:\n{tops}\n\n"
        f"Package manifests:\n{man_txt}\n\n"
        f"Files the seed flagged as entry points:\n{ep_txt}\n\n"
        "Read top-down and emit the ModuleMap: 2-8 subsystems by responsibility, "
        "each with paths[] covering the source tree, an external_facing flag, and "
        "depends_on; plus repo-wide build/run/test and a one-paragraph auth model."
    )


def _run_orient(seed: Seed, repo: str, run_id: str, deps, errors_path: Path) -> ModuleMap | None:
    from crucible.agents.tools import read_only_fs_tools
    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    tools = read_only_fs_tools(repo)
    allowed = {t.name for t in tools}
    prompt = load_skill("recon/orient.md")
    model = deps.registry.chat_model(ModelRole.RECON)
    try:
        task = _orient_task_text(seed, repo)
        msgs = _explore(
            deps=deps, run_id=run_id, thread_id=f"{run_id}:recon:orient",
            tools=tools, allowed=allowed, system_prompt=prompt, task_text=task,
            budget=RECON_ORIENT_READ_BUDGET,
        )
        mm = _emit(model, prompt, task, _digest(msgs), ModuleMap)
        return mm if mm.subsystems else None
    except Exception as e:  # noqa: BLE001 — resilience: deterministic fallback
        _log_error(errors_path, "R1a", "orient", _fmt_exc(e))
    return None


def _run_map(
    seed: Seed, repo: str, run_id: str, deps, errors_path: Path, partition: list[dict]
) -> list[SubsystemMap]:
    """R1b fan-out. One independent agent per subsystem (own `create_agent`,
    own `thread_id`, own context window) — the harness pattern, just run
    concurrently on a thread pool of width `RECON_MAX_PARALLEL`. `=1` keeps the
    old strictly-sequential path. Each worker is I/O-bound on the model API, so
    threads (not processes) are the right tool and no state is shared beyond the
    lock-guarded `errors.jsonl` append.
    """
    from concurrent.futures import ThreadPoolExecutor

    from crucible.agents.tools import read_only_fs_tools
    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    tools = read_only_fs_tools(repo)
    allowed = {t.name for t in tools}
    prompt = load_skill("recon/map.md")
    model = deps.registry.chat_model(ModelRole.RECON)
    err_lock = threading.Lock()

    def _one(sub: dict) -> SubsystemMap | None:
        try:
            task = _map_task_text(sub, partition, seed)
            msgs = _explore(
                deps=deps, run_id=run_id,
                thread_id=f"{run_id}:recon:map:{sub['name']}",
                tools=tools, allowed=allowed, system_prompt=prompt, task_text=task,
            )
            sm = _emit(model, prompt, task, _digest(msgs), SubsystemMap)
            sm.subsystem = sub["name"]
            return sm
        except Exception as e:  # noqa: BLE001 — resilience: log and continue
            with err_lock:
                _log_error(errors_path, "R1b", sub["name"], _fmt_exc(e))
            return None

    workers = max(1, min(RECON_MAX_PARALLEL, len(partition)))
    if workers == 1:
        results = [_one(sub) for sub in partition]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recon-r1b") as ex:
            results = list(ex.map(_one, partition))
    return [sm for sm in results if sm is not None]


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
    from crucible.agents.tools import read_only_fs_tools
    from crucible.llm.registry import ModelRole
    from crucible.skills import load_skill

    tools = read_only_fs_tools(repo)
    allowed = {t.name for t in tools}
    prompt = load_skill("recon/threatmodel.md")
    model = deps.registry.chat_model(ModelRole.RECON)
    try:
        task = _threatmodel_task_text(seed, architecture_md)
        msgs = _explore(
            deps=deps, run_id=run_id, thread_id=f"{run_id}:recon:threatmodel",
            tools=tools, allowed=allowed, system_prompt=prompt, task_text=task,
        )
        return _emit(model, prompt, task, _digest(msgs), ThreatModel)
    except Exception as e:  # noqa: BLE001
        _log_error(errors_path, "R2", "threatmodel", _fmt_exc(e))
    return None


# --- two-phase structured recon: explore, then force one typed emission -------
# deepseek-v4-flash reads files reliably but (with a ToolStrategy in reach)
# almost never calls the emit-tool before the call budget runs out, and
# `create_agent` discards a plain-text answer — so R1/R2 came back empty
# ("no structured_response"). The ToolStrategy retry path could also leave
# sibling tool_calls unanswered and produce a malformed next request
# ("insufficient tool messages following tool_calls message", HTTP 400).
# Splitting the two removes both failure modes.


def _explore(
    *, deps, run_id: str, thread_id: str, tools, allowed: set[str],
    system_prompt: str, task_text: str, budget: int = RECON_EXPLORE_LIMIT,
) -> list:
    """Phase A — bounded read-only exploration with a plain ReAct agent (no
    structured-output tool in reach). Returns the message transcript."""
    from crucible.agents.core import build_agent
    from crucible.llm.registry import ModelRole

    agent = build_agent(
        role=ModelRole.RECON, registry=deps.registry, run_id=run_id,
        tools=tools, system_prompt=system_prompt, allowed_tools=allowed,
        store=getattr(deps, "store", None), response_format=None,
        summarize=False, model_call_limit=budget,
    )
    explore_task = (
        task_text
        + "\n\nFirst, inspect the important files with the read-only tools "
        "(list_dir, read_file, search). You will be asked for the structured "
        "answer in a follow-up step — for now, gather facts and note what matters."
    )
    res = agent.invoke(
        {"messages": [("user", explore_task)]},
        config={
            "configurable": {"thread_id": thread_id},
            "recursion_limit": RECON_RECURSION_LIMIT,
        },
    )
    return res.get("messages", [])


def _digest(messages: list, *, budget: int = 20_000) -> str:
    """Condense an exploration transcript to plain text — the tool outputs the
    agent saw and any analysis it wrote, most-recent-first, capped."""
    from langchain_core.messages import AIMessage, ToolMessage

    blocks: list[str] = []
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            body = m.content if isinstance(m.content, str) else str(m.content)
            label = getattr(m, "name", None) or "tool"
        elif isinstance(m, AIMessage):
            body = m.content if isinstance(m.content, str) else ""
            label = "analysis"
        else:
            continue
        body = (body or "").strip()
        if not body:
            continue
        blocks.append(f"[{label}]\n{body[:4_000]}")
        if sum(len(b) for b in blocks) > budget:
            break
    return "\n\n".join(reversed(blocks))[:budget]


def _emit(model, system_prompt: str, task_text: str, digest: str, schema):
    """Phase B — one forced structured emission from the gathered context. A
    fresh message list (no tool-call history) keeps the request well-formed;
    `tool_choice=<schema>` forces the single call we parse. One repair retry."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from pydantic import ValidationError

    name = schema.__name__
    ask = (
        f"{task_text}\n\n"
        f"--- notes gathered while reading the repo ---\n{digest or '(no files read)'}\n\n"
        f"Now call the `{name}` tool exactly once with your final answer, based on "
        f"the notes above and the seed facts. Accurate partial content is fine."
    )
    bound = model.bind_tools([schema], tool_choice=name)
    msgs: list = [SystemMessage(content=system_prompt), HumanMessage(content=ask)]
    err = "model did not call the emit tool"
    for _ in range(2):
        out = bound.invoke(msgs)
        calls = getattr(out, "tool_calls", None) or []
        call = next((c for c in calls if c["name"] == name), calls[0] if calls else None)
        if call is None:
            break
        try:
            return schema.model_validate(call["args"])
        except ValidationError as e:
            err = str(e).splitlines()[0]
            msgs = [
                *msgs, out,
                ToolMessage(
                    content=f"That did not validate: {err}. Call `{name}` again with corrected fields.",
                    tool_call_id=call.get("id", ""), name=name,
                ),
            ]
    raise RuntimeError(f"structured emit failed: {err}")


def _fmt_exc(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def _log_error(path: Path, stage: str, unit: str, detail: str) -> None:
    log.warning("%s[%s] failed: %s", stage, unit, detail[:300])
    with path.open("a") as fh:
        fh.write(json.dumps({"stage": stage, "unit": unit, "detail": detail[:2000]}) + "\n")
