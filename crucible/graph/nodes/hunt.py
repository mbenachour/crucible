"""Hunt stage (specs.md §9.2).

One task = one attack class + one scope hint + architecture.md + prior coverage.
NEVER "find vulnerabilities in this repo." Narrow scoping is what makes the
model behave like a researcher rather than wander.

Tune Hunters to deliberately over-report (§1.5). Success is not Hunt precision;
it is how sharply the funnel refines raw output before a human sees it.

Move past reading into execution: compile fragments, build small versions,
attack them in the sandbox (§10). Cloudflare's biggest quality jump came from
giving Hunters a sandbox to crash binaries in.

Structure mirrors Recon (`crucible/graph/nodes/recon.py`, the working
reference): a two-phase agent per task — a bounded read/exec **explore** with a
plain ReAct agent, then one forced `tool_choice=<schema>` **emit**. Small
open-weight models almost never call an emit-tool on their own while a
ToolStrategy is in reach, and `create_agent` discards a plain-text answer.

Failure modes designed against (§9.2):
  * edits source so its own exploit works        -> killed by the PoC gate
  * writes a tautological test that proves nothing -> killed by the deny-list
  * exploit runs but threat model is nonsense      -> killed by threat_model req
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from crucible.config import MODEL_CALLS_PER_TASK
from crucible.graph.hooks import MAX_CONTINUATIONS
from crucible.graph.state import CrucibleState
from crucible.obs import progress, span
from crucible.sandbox import SandboxLimits
from crucible.validation.schema import Finding, tautology_reasons
from crucible.workspace import layout
from crucible.workspace.fs import commit_node

log = logging.getLogger("crucible.hunt")

HUNT_TOOLS = ["list_dir", "read_file", "search", "sandbox_exec", "fork_sibling", "wishlist_write"]

# Per-invocation task budget — Hunt is re-entered up to MAX_CONTINUATIONS times
# (§8), so the effective ceiling is HUNT_MAX_TASKS_PER_RUN * (MAX_CONTINUATIONS+1).
# Gapfill (Phase 2) sweeps whatever is left. Overridable for fast smoke runs.
HUNT_MAX_TASKS_PER_RUN = int(os.environ.get("CRUCIBLE_HUNT_MAX_TASKS", "6"))
# Phase-A exploration budget per task (model calls). The §8 hard cap
# (MODEL_CALLS_PER_TASK) still bounds each agent; this lower value keeps Hunt
# from spending the whole budget reading one file tree.
HUNT_EXPLORE_LIMIT = int(os.environ.get("CRUCIBLE_HUNT_EXPLORE_LIMIT", "14"))
# Every agent loop iteration is 4 LangGraph super-steps; the recursion limit
# must sit above 4x the model-call cap plus a margin (same reasoning as Recon).
HUNT_RECURSION_LIMIT = 4 * MODEL_CALLS_PER_TASK + 20
# A hunt that finishes this fast with nothing to show is usually a crashed
# dependency, not a clean cell (§13) — retry it once via the continuation loop.
SHALLOW_TOOLCALLS = 2
MAX_FORKS_PER_RUN = 12
SANDBOX_EXEC_CAP = 6000  # chars of sandbox output shown to the model


class HuntResult(BaseModel):
    """Forced structured emission for one hunt task. `finding` is optional so a
    genuine negative is a first-class outcome (coverage, not a failure)."""

    finding_found: bool = Field(description="true only if you have a concrete, reachable defect")
    finding: Finding | None = Field(default=None, description="the finding, when finding_found")
    negative_note: str = Field(
        default="", description="if no finding: what you checked and why it looks safe"
    )


def run(state: CrucibleState, deps=None) -> CrucibleState:
    ws = Path(state["workspace_path"])
    run_id = state["run_id"]
    repo = state["repo_path"]
    recon_dir = ws / "recon"
    errors_path = recon_dir / "errors.jsonl"

    pending: list[dict] = list(state.get("pending_hunts") or [])
    state["continuation_count"] = state.get("continuation_count", 0) + 1
    cont = state["continuation_count"]
    log.info("hunt start  pending=%d  continuation=%d/%d", len(pending), cont, MAX_CONTINUATIONS)

    registry = getattr(deps, "registry", None)
    if registry is None:
        # deterministic path (graph compile / tests): nothing to hunt with.
        log.info("hunt  skipped (no registry) — clearing the queue")
        state["pending_hunts"] = []
        commit_node(ws, "hunt", run_id)
        return state

    arch_md = ""
    ap = state.get("architecture_path") or ""
    if ap and Path(ap).is_file():
        arch_md = Path(ap).read_text(errors="replace")

    catalog = _attack_class_catalog()
    store = getattr(deps, "store", None)
    sandbox_provider = getattr(deps, "sandbox_provider", None)

    # priority is already baked into queue order by Recon R3; take the head.
    batch = pending[:HUNT_MAX_TASKS_PER_RUN]
    rest = pending[HUNT_MAX_TASKS_PER_RUN:]
    requeue: list[dict] = []
    new_finding_ids: list[str] = []
    forks_this_run = 0

    bar_cm = progress("Hunt", len(batch))
    bar = bar_cm.__enter__()
    try:
        for task in batch:
            task_id = task["task_id"]
            area = task.get("area") or "."
            attack_class = task["attack_class"]
            bar.set(f"Hunt · {task_id} {attack_class}")
            log.info(
                "hunt task %s  START  %-22s %-10s area=%s  scope=%s",
                task_id, attack_class, task.get("chunk_type", "?"), area,
                (task.get("scope_hint") or "(none)")[:70],
            )
            with span("hunt.task", task_id=task_id, attack_class=attack_class):
                t0 = time.monotonic()
                try:
                    fids, forks, shallow = _hunt_one(
                        task=task, repo=repo, ws=ws, run_id=run_id, arch_md=arch_md,
                        catalog=catalog, deps=deps, registry=registry, store=store,
                        sandbox_provider=sandbox_provider,
                        forks_left=MAX_FORKS_PER_RUN - forks_this_run,
                    )
                except Exception as e:  # noqa: BLE001 — resilience: log and continue
                    _log_error(errors_path, "HUNT", task_id, f"{type(e).__name__}: {e}")
                    shallow, fids, forks = False, [], 0
                new_finding_ids.extend(fids)
                forks_this_run += forks
                dt = time.monotonic() - t0
                log.info(
                    "hunt task %s  DONE   %-22s %s  %.1fs  findings=%d forks=%d%s",
                    task_id, attack_class, task.get("chunk_type", "?"), dt,
                    len(fids), forks, "  [shallow]" if shallow else "",
                )
                cell = f"{area}::{attack_class}"
                if cell not in state["completed_cells"]:
                    state["completed_cells"].append(cell)
                # §13 shallow-detection: one retry via the continuation loop.
                if shallow and not fids and task.get("continuation_count", 0) < 1:
                    t2 = dict(task)
                    t2["continuation_count"] = task.get("continuation_count", 0) + 1
                    requeue.append(t2)
            bar.tick()
    finally:
        bar_cm.__exit__(None, None, None)

    state["finding_ids"] = list(state.get("finding_ids") or []) + new_finding_ids
    state["fork_count"] = state.get("fork_count", 0) + forks_this_run

    # Anything not attempted this pass stays queued for the next continuation;
    # once continuation_count hits the cap the gate ends the loop regardless.
    remaining = requeue + rest + _drain_forks(ws)
    if cont >= MAX_CONTINUATIONS:
        if remaining:
            log.info("hunt  continuation cap reached — %d task(s) left unhunted "
                     "(Gapfill territory, §11)", len(remaining))
        remaining = []
    state["pending_hunts"] = remaining

    commit_node(ws, "hunt", run_id)
    log.info(
        "hunt done  new_findings=%d  forks=%d  queued_for_next=%d",
        len(new_finding_ids), forks_this_run, len(remaining),
    )
    return state


# --------------------------------------------------------------------- one task


def _hunt_one(
    *, task: dict, repo: str, ws: Path, run_id: str, arch_md: str, catalog: str,
    deps, registry, store, sandbox_provider, forks_left: int,
) -> tuple[list[str], int, bool]:
    """Explore -> emit for a single hunt cell. Returns (finding_ids, forks, shallow)."""
    from crucible.llm.registry import ModelRole

    task_id = task["task_id"]
    attack_class = task["attack_class"]
    methodology, prompt_version = _attack_class_body(attack_class)

    forks: list[dict] = []
    wishes: list[dict] = []
    sandbox = _make_sandbox(sandbox_provider, task_id, repo)
    try:
        tools = _hunt_tools(
            repo=repo, sandbox=sandbox, task=task, ws=ws, forks=forks, wishes=wishes,
            forks_left=forks_left,
        )
        allowed = {t.name for t in tools}
        system_prompt = _hunt_system_prompt()
        task_text = _hunt_task_text(task, arch_md, methodology, catalog, sandbox is not None)

        msgs, n_toolcalls = _explore(
            deps=deps, run_id=run_id, thread_id=f"{run_id}:hunt:{task_id}",
            tools=tools, allowed=allowed, system_prompt=system_prompt, task_text=task_text,
        )
        model = registry.chat_model(ModelRole.HUNTER)
        result = _emit(model, system_prompt, task_text, _digest(msgs))
    finally:
        _destroy_sandbox(sandbox)

    # persist wishes regardless of finding outcome
    if store is not None:
        for w in wishes:
            _persist_wish(store, run_id, task_id, w)

    finding_ids: list[str] = []
    coverage_lines: list[str] = []
    if result is not None and result.finding_found and result.finding is not None:
        f = result.finding
        reasons = tautology_reasons(f)
        if reasons:
            log.info("hunt task %s  finding rejected at parse time: %s", task_id, "; ".join(reasons))
            coverage_lines.append(f"- rejected (tautology deny-list): {reasons[0]}")
        else:
            fid = f"{task_id}-{uuid.uuid4().hex[:6]}"
            _persist_finding(store, ws, run_id, fid, f, attack_class, prompt_version, registry)
            finding_ids.append(fid)
            coverage_lines.append(
                f"- **finding {fid}** ({f.severity.value}): {f.title} "
                f"@ `{f.file_path}:{f.line_start}`"
            )
    elif result is not None and result.negative_note:
        coverage_lines.append(f"- negative: {result.negative_note.strip()[:400]}")
    else:
        coverage_lines.append("- no result emitted")

    _append_coverage(ws, task, coverage_lines)
    shallow = n_toolcalls < SHALLOW_TOOLCALLS and not finding_ids
    return finding_ids, len(forks), shallow


# ------------------------------------------------------------------ agent phases


def _explore(
    *, deps, run_id: str, thread_id: str, tools, allowed: set[str],
    system_prompt: str, task_text: str,
) -> tuple[list, int]:
    """Phase A — bounded read/exec exploration with a plain ReAct agent (no
    structured-output tool in reach). Returns (messages, tool_call_count)."""
    from crucible.agents.core import build_agent
    from crucible.llm.registry import ModelRole

    agent = build_agent(
        role=ModelRole.HUNTER, registry=deps.registry, run_id=run_id,
        tools=tools, system_prompt=system_prompt, allowed_tools=allowed,
        store=getattr(deps, "store", None), response_format=None,
        summarize=False, model_call_limit=HUNT_EXPLORE_LIMIT,
    )
    explore_task = (
        task_text
        + "\n\nExplore now: read the code in scope, then use `sandbox_exec` to "
        "stand up the smallest slice and attack it. You will be asked for the "
        "structured result in a follow-up step — for now, gather evidence."
    )
    try:
        res = agent.invoke(
            {"messages": [("user", explore_task)]},
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": HUNT_RECURSION_LIMIT,
            },
        )
    except Exception as e:  # noqa: BLE001
        # A cut-off ReAct loop can leave a dangling tool_calls turn and the
        # provider 400s ("insufficient tool messages following tool_calls").
        # Salvage: fall through to the emit phase with whatever we can, rather
        # than losing the whole cell.
        log.warning("hunt explore for %s ended early: %s: %s", thread_id, type(e).__name__, e)
        return [], 0
    messages = res.get("messages", [])
    n = _count_tool_calls(messages)
    return messages, n


def _emit(model, system_prompt: str, task_text: str, digest: str) -> HuntResult | None:
    """Phase B — one forced structured emission from the gathered context. Fresh
    message list (no tool-call history) keeps the request well-formed;
    `tool_choice` forces the single call we parse. One repair retry."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    name = HuntResult.__name__
    ask = (
        f"{task_text}\n\n"
        f"--- evidence gathered while hunting ---\n{digest or '(no tools used)'}\n\n"
        f"Now call the `{name}` tool exactly once with your final result. Set "
        f"`finding_found` true only if you have a concrete, reachable defect "
        f"with a PoC idea; otherwise set it false and fill `negative_note`. "
        f"Field order is load-bearing — commit to the threat model first."
    )
    bound = model.bind_tools([HuntResult], tool_choice=name)
    msgs: list = [SystemMessage(content=system_prompt), HumanMessage(content=ask)]
    err = "model did not call the emit tool"
    for _ in range(2):
        out = bound.invoke(msgs)
        calls = getattr(out, "tool_calls", None) or []
        call = next((c for c in calls if c["name"] == name), calls[0] if calls else None)
        if call is None:
            break
        try:
            return HuntResult.model_validate(call["args"])
        except ValidationError as e:
            err = str(e).splitlines()[0]
            msgs = [
                *msgs, out,
                ToolMessage(
                    content=f"That did not validate: {err}. Call `{name}` again with corrected fields.",
                    tool_call_id=call.get("id", ""), name=name,
                ),
            ]
    log.warning("hunt emit failed: %s", err)
    return None


def _digest(messages: list, *, budget: int = 16_000) -> str:
    """Condense the exploration transcript to plain text — tool outputs the
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


def _count_tool_calls(messages: list) -> int:
    from langchain_core.messages import AIMessage

    return sum(len(getattr(m, "tool_calls", []) or []) for m in messages if isinstance(m, AIMessage))


# ------------------------------------------------------------------------ tools


def _hunt_tools(*, repo: str, sandbox, task: dict, ws: Path, forks: list, wishes: list, forks_left: int):
    from langchain_core.tools import tool

    from crucible.agents.tools import read_only_fs_tools

    tools = list(read_only_fs_tools(repo))

    @tool
    def sandbox_exec(cmd: str, timeout_s: int = 120) -> str:
        """Run a shell command in the isolated sandbox container. `/src` is the
        target repo (read-only), `/scratch` is writable, there is NO network.
        Use this to build the smallest runnable slice and attack it."""
        if sandbox is None:
            return (
                "sandbox is unavailable for this run (started with --no-sandbox, "
                "or the container failed to start). Reason from the source only; "
                "if execution is essential, call wishlist_write."
            )
        try:
            r = sandbox.exec(cmd, timeout_s=min(int(timeout_s or 120), 240))
        except Exception as e:  # noqa: BLE001
            return f"sandbox_exec error: {type(e).__name__}: {e}"
        out = f"exit={r.exit_code} timed_out={r.timed_out}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
        if len(out) > SANDBOX_EXEC_CAP:
            half = SANDBOX_EXEC_CAP // 2
            out = out[:half] + "\n[... truncated ...]\n" + out[-half:]
        return out

    @tool
    def fork_sibling(structural_seed: str, reason: str) -> str:
        """Spin off a sibling hunt for a serious issue OUTSIDE the current scope.
        `structural_seed` must be a precise 'file:line — what to look at' pointer;
        `reason` says why it is out of scope here. Do not use it to wander."""
        if len(forks) >= max(0, forks_left):
            return "fork budget exhausted for this run; note it in your finding/negative instead."
        seed = structural_seed.strip()
        forks.append({"structural_seed": seed, "reason": reason.strip()})
        sib = {
            "task_id": f"fork-{uuid.uuid4().hex[:8]}",
            "area": task.get("area", "."),
            "attack_class": task["attack_class"],
            "scope_hint": f"[fork] {reason.strip()[:160]} :: {seed[:200]}",
            "chunk_type": "risk",
            "seed_path": seed[:200],
            "continuation_count": 0,
        }
        fp = ws / "recon" / "forks.jsonl"
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a") as fh:
            fh.write(json.dumps(sib) + "\n")
        return f"forked: {seed[:120]}"

    @tool
    def wishlist_write(need: str, context: str) -> str:
        """Record a blocking dependency you need to confirm a PoC — a build
        toolchain, a VM, prod config, a credential. Enough context for the
        system to re-run this exact task once a human provides it."""
        wishes.append({"need": need.strip(), "context": context.strip()})
        return "wish recorded; continue with what you can prove now."

    return [*tools, sandbox_exec, fork_sibling, wishlist_write]


# --------------------------------------------------------------------- sandbox


def _make_sandbox(provider, task_id: str, repo: str):
    if provider is None:
        return None
    try:
        provider.create(task_id, repo, SandboxLimits())
        return provider
    except Exception as e:  # noqa: BLE001
        log.warning("hunt task %s  sandbox create failed: %s: %s", task_id, type(e).__name__, e)
        return None


def _destroy_sandbox(sandbox) -> None:
    if sandbox is None:
        return
    try:
        sandbox.destroy()
    except Exception as e:  # noqa: BLE001
        log.debug("sandbox destroy failed (ignored): %s", e)


# -------------------------------------------------------------- skills / prompts


def _hunt_system_prompt() -> str:
    from crucible.skills import load_skill

    return load_skill("hunt/hunt.md")


def _attack_class_catalog() -> str:
    """Front-matter only for every attack-class skill (progressive disclosure,
    §7). The scoped class's full body is loaded separately per task."""
    from crucible.skills import skill_front_matter

    root = Path(__file__).resolve().parents[2] / "skills" / "attack_classes"
    lines: list[str] = []
    for p in sorted(root.glob("*.md")):
        fm = skill_front_matter(f"attack_classes/{p.name}")
        lines.append(f"- `{fm.get('name', p.stem)}`: {fm.get('description', '').strip()}")
    return "\n".join(lines)


def _attack_class_body(attack_class: str) -> tuple[str, str]:
    """(methodology body, prompt_version) for the scoped class. Model-invented
    `repo_specific_class` names have no file — fall back to a generic playbook."""
    from crucible.skills import load_skill, skill_front_matter

    rel = f"attack_classes/{attack_class}.md"
    try:
        body = load_skill(rel)
        ver = skill_front_matter(rel).get("version", "0")
        return body, f"{attack_class}@{ver}"
    except (FileNotFoundError, OSError):
        generic = (
            "# Methodology (generic — no dedicated skill for this class)\n\n"
            "This attack class was proposed by Recon for this specific repo. Use "
            "the scope hint as your playbook. Name a concrete attacker and the "
            "boundary they cross, find where the described weakness lives, then "
            "prove it in the sandbox with a local effect. Over-report a credible "
            "primitive; do not invent one."
        )
        return generic, f"{attack_class}@generic"


def _hunt_task_text(task: dict, arch_md: str, methodology: str, catalog: str, has_sandbox: bool) -> str:
    chunk_type = task.get("chunk_type", "catch_all")
    framing = {
        "surface": "Recon ranked this entry point at the top of the attack surface. "
                   "The seed path is the exact target; the scope hint carries the "
                   "ranking rationale and exposure. Confirm reachability, then hunt "
                   "the named class from that entry point inward.",
        "taint": "A concrete source→sink path was seeded. Prove (or disprove) that "
                 "attacker input reaches the sink and what it can do there.",
        "risk": "A dynamic-dispatch / reflection site was flagged. Determine whether "
                "its selector or arguments are attacker-influenced and where that leads.",
        "specialist": "A repo-specific class from the threat model. The scope hint is "
                      "your methodology — apply it precisely.",
        "catch_all": "Sweep the named entry point for this attack class.",
        "threat_fallback": "A STRIDE threat with no obvious code yet. Find where it "
                           "lives, then test it.",
    }.get(chunk_type, "Sweep the named scope for this attack class.")
    seed_path = task.get("seed_path") or "(none)"
    sb = ("The sandbox is available via `sandbox_exec` — use it."
          if has_sandbox else
          "NOTE: no sandbox this run — reason from source; wishlist_write if exec is essential.")
    arch = arch_md.strip()
    if len(arch) > 9_000:
        arch = arch[:9_000] + "\n[... architecture.md truncated ...]"
    return (
        f"# Hunt task {task['task_id']}\n\n"
        f"**Attack class:** `{task['attack_class']}`  \n"
        f"**Chunk type:** `{chunk_type}` — {framing}  \n"
        f"**Area:** `{task.get('area', '.')}`  \n"
        f"**Scope hint:** {task.get('scope_hint', '(none)')}  \n"
        f"**Seed path:** `{seed_path}`  \n\n"
        f"{sb}\n\n"
        f"## Attack-class methodology\n\n{methodology}\n\n"
        f"## Other attack classes (context only — do NOT hunt these here)\n\n{catalog}\n\n"
        f"## Target architecture (Recon output)\n\n{arch or '(architecture.md is empty)'}\n"
    )


# ------------------------------------------------------------------- persistence


def _persist_finding(store, ws: Path, run_id: str, fid: str, f: Finding,
                     attack_class: str, prompt_version: str, registry) -> None:
    from crucible.llm.registry import ModelRole
    from crucible.store.dao import stable_key

    payload = f.model_dump(mode="json")
    layout.finding_path(ws, fid).write_text(json.dumps(payload, indent=2))
    if store is None:
        return
    from crucible.store.models import FindingRow

    ep = registry.endpoint(ModelRole.HUNTER)
    row = FindingRow(
        finding_id=fid,
        run_id=run_id,
        stable_key=stable_key(f.file_path, "", f.threat_model.boundary_crossed),
        payload=payload,
        status="raw",
        hunter_model=f"{ep.provider.value}:{ep.model}",
        hunter_prompt_version=prompt_version,
        hunter_sampling=registry.sampling_params(ModelRole.HUNTER),
    )
    try:
        store.add_finding(row)
    except Exception as e:  # noqa: BLE001
        log.warning("hunt  could not persist finding %s: %s", fid, e)


def _persist_wish(store, run_id: str, task_id: str, w: dict) -> None:
    from crucible.store.models import WishRow

    try:
        store.add_wish(WishRow(
            run_id=run_id, blocked_task_id=task_id,
            need=w.get("need", ""), context=w.get("context", ""),
        ))
    except Exception as e:  # noqa: BLE001
        log.warning("hunt  could not persist wish for %s: %s", task_id, e)


def _drain_forks(ws: Path) -> list[dict]:
    """Forks are staged to workspace/recon/forks.jsonl by the tool; turn any
    un-consumed ones into queue tasks for the next continuation."""
    fp = ws / "recon" / "forks.jsonl"
    if not fp.is_file():
        return []
    out: list[dict] = []
    try:
        for line in fp.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        fp.unlink()
    except Exception:  # noqa: BLE001
        return []
    return out


def _append_coverage(ws: Path, task: dict, lines: list[str]) -> None:
    area = re.sub(r"[^\w-]", "_", (task.get("area") or "").strip(" ./*")) or "root"
    p = layout.coverage_path(ws, area)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    block = [
        f"\n## {task['task_id']} · `{task['attack_class']}` · {task.get('chunk_type', '?')} · {stamp}",
        f"scope: {task.get('scope_hint', '(none)')}",
        *lines,
    ]
    with p.open("a") as fh:
        fh.write("\n".join(block) + "\n")


def _log_error(path: Path, stage: str, unit: str, detail: str) -> None:
    log.warning("%s[%s] failed: %s", stage, unit, detail[:300])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps({"stage": stage, "unit": unit, "detail": detail[:2000]}) + "\n")
    except Exception as e:  # noqa: BLE001
        log.debug("could not append to %s: %s", path, e)
