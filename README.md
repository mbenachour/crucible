# Crucible — Vulnerability Discovery Harness (Phase 1 scaffold)

First-draft implementation of **Phase 1** from [`specs.md`](specs.md): the
minimal harness — **Recon → Hunt → Validate → Report** on a database, with a
separate validator that cannot file its own findings.

This is scaffolding: the structure, state types, deterministic gates, model
routing, schema, and instrumentation are real; the model-facing node bodies
(`recon`, `hunt`, `validate_bug`, `validate_reachability`) and the sandbox and
PoC-gate execution paths are `NotImplementedError` stubs with the spec
requirements written into their docstrings and `TODO(phase1)` markers.

## Architecture

Two layers. The **stage machine** (LangGraph `StateGraph`) is coarse, durable, and
checkpointed — it survives a crash and resumes. Each **agent stage** is a
`create_agent` + middleware stack (LangChain) that reads and writes the `workspace/`
filesystem — large artifacts never travel through model context or the DB (§1.1).

The diagram is the **whole harness across all four build phases**. Solid blue is
**Phase 1** (this scaffold): Recon → Hunt → Validate → Report. Dashed nodes are
designed-now / built-later: <span title="Phase 2">Gapfill · Dedup · Feedback</span>
(§11), cross-repo Trace (§3 P3), VVS judgment + human-gated Fixer (§2 P4). The
Phase-2+ stages turn Report into a **producer–consumer loop** — a bug found late in
a cycle is still validated, reported, and deduped in the same run (§11).

```mermaid
flowchart TB
    cli(["crucible run --repo REPO"]):::p1
    cli --> machine

    subgraph machine["Stage machine · LangGraph StateGraph · checkpointed + resumable"]
        direction TB
        recon["Recon (§9.1)<br/>writes architecture.md + its own taxonomy.json"]:::p1
        queue{{"Hunt queue · area × attack_class cells · task cap per run"}}:::p1
        hunt["Hunt (§9.2)<br/>1 attack class + 1 scope · over-reports · attacks in sandbox"]:::p1
        dedup["Dedup (§11)<br/>inverted index → agent judges shared root cause"]:::p2
        vmech["Validate A · mechanical (§9.4)<br/>no model · PoC gate: fail clean / pass patched"]:::p1
        vbug["Validate B · is it real? (§9.4)<br/>VALIDATOR_BUG · tries to disprove · can't file"]:::p1
        vreach["Validate C · is it reachable? (§9.4)<br/>VALIDATOR_REACH · single-repo"]:::p1
        trace["Trace (P3)<br/>cross-repo reachability · unified symbol index"]:::p3
        report["Report (§9.5)<br/>deterministic render · queryable data"]:::p1
        gapfill["Gapfill (§11)<br/>re-queues under-tested cells"]:::p2
        feedback["Feedback (§11)<br/>rewrites queued prompts from failures"]:::p2
        vvs["VVS judgment (P4)<br/>prod reachability · wiki / Jira / git context"]:::p4
        fixer["Fixer (P4)<br/>patch + regression test"]:::p4
        human{{"human sign-off before merge"}}:::p4

        recon -->|seeds| queue --> hunt
        hunt -->|"early exit → fresh window · ≤ 3 continuations"| hunt
        hunt -->|raw findings| dedup --> vmech --> vbug --> vreach --> report
        vreach -.->|"P3 replaces Pass C"| trace
        report --> vvs --> fixer --> human
        report -.->|coverage plateau| gapfill -.-> queue
        report -.->|validation misses| feedback -.-> queue
        trace -.-> queue
    end

    subgraph infra["Shared infrastructure"]
        direction TB
        ws[("workspace/ · git commit per node (§7)<br/>architecture.md · coverage/ · findings/ · scratch/ · offload/")]:::p1
        sbx["Sandbox (§10)<br/>no egress · read-only source · writes only scratch/"]:::p1
        reg["llm/registry — role → vLLM<br/>assert HUNTER ≠ VALIDATOR_BUG (§6)"]:::p1
        clf["llm/classify — response text →<br/>ok / transient / refusal / malformed (§1.10)"]:::p1
        wish[("wishlist — missing dep →<br/>re-run exact task on resolve (§9.3)")]:::p1
        reg --- clf
    end

    subgraph stores["Two stores, deliberately (§3)"]
        direction LR
        ckpt[("checkpoints.sqlite<br/>execution state")]:::p1
        dom[("findings.sqlite<br/>findings · validations · provenance · tool-usage")]:::p1
    end

    recon -.writes.-> ws
    hunt -.reads / writes.-> ws
    hunt -->|"compile / crash PoC"| sbx
    vmech -->|"PoC gate exec"| sbx
    hunt -.blocked.-> wish
    machine -.checkpoint every superstep.-> ckpt
    hunt -->|raw| dom
    vmech -->|verdict| dom
    vbug -->|verdict| dom
    vreach -->|verdict| dom
    report -.reads upheld.-> dom
    recon -.model call.-> reg
    hunt -.model call.-> reg
    vbug -.model call.-> reg
    vreach -.model call.-> reg

    classDef p1 fill:#1f6feb26,stroke:#1f6feb,stroke-width:2px,color:#1f6feb
    classDef p2 fill:#8957e51f,stroke:#8957e5,stroke-width:1px,stroke-dasharray:5 3,color:#8957e5
    classDef p3 fill:#2da44e1f,stroke:#2da44e,stroke-width:1px,stroke-dasharray:5 3,color:#2da44e
    classDef p4 fill:#bf87091f,stroke:#bf8709,stroke-width:1px,stroke-dasharray:5 3,color:#bf8709
```

**Legend** — <span>■</span> blue solid = Phase 1 (built) · purple dashed = Phase 2
(Gapfill / Dedup / Feedback) · green dashed = Phase 3 (cross-repo Trace) · amber
dashed = Phase 4 (VVS + Fixer). Solid arrows = the request path; dotted arrows =
side effects (filesystem, stores, model calls, queue feedback).

## Layout

| Path | Spec | Status |
|---|---|---|
| `crucible/graph/build.py` | §4 topology + checkpointer | wired |
| `crucible/graph/state.py` | §5 `CrucibleState` (pointers/counters only) | done |
| `crucible/graph/hooks.py` | §7 offload/compaction, §8 continuation gate | gate done, offload stub |
| `crucible/graph/nodes/recon.py` | §9.1 | stub |
| `crucible/graph/nodes/hunt.py` | §9.2 | stub |
| `crucible/graph/nodes/validate_mechanical.py` | §9.4 Pass A | wired → `validation/mechanical.py` |
| `crucible/graph/nodes/validate_bug.py` | §9.4 Pass B | stub |
| `crucible/graph/nodes/validate_reachability.py` | §9.4 Pass C | stub |
| `crucible/graph/nodes/report.py` | §9.5 deterministic render | stub |
| `crucible/llm/registry.py` | §6 role→endpoint, `HUNTER != VALIDATOR_BUG` assert | done |
| `crucible/llm/classify.py` | §6 / §1.10 response classification | done |
| `crucible/validation/schema.py` | §9.2 `Finding` (field order load-bearing) + tautology deny-list | done |
| `crucible/validation/mechanical.py` | §9.4 deterministic gates + PoC gate | gates done, PoC gate stub (fail-closed) |
| `crucible/workspace/` | §7 layout + git-per-node | done |
| `crucible/agents/tools.py` | §9.2 tools + universal offload wrapper | offload done, tools stub |
| `crucible/agents/instrumentation.py` | §1.12 per-tool counters | done |
| `crucible/sandbox/__init__.py` | §10 provider adapter + policy | protocol done, impl external |
| `crucible/store/` | §3 SQLite domain store (findings/validations/wishlist/tool-usage) | done |
| `crucible/skills/` | §7 prompt files with `version:` front-matter | 2 attack classes + 2 validators drafted |
| `tests/fixtures/repos/` | §12 golden fixtures + `bugs.yaml` manifests | fixture-c / -py / -clean / -holdout seeded |

## Two stores, deliberately (§3)

LangGraph `SqliteSaver` holds **execution** state (`checkpoints.sqlite`).
`crucible/store/` holds **domain** state (`findings.sqlite`) — findings must
outlive and be queryable independently of any run.

## Run

```bash
pip install -e ".[dev]"
crucible run --repo tests/fixtures/repos/fixture-py     # end-to-end (stops at first stub node)
crucible status <run_id>                                # fork rate + per-tool counts (§14.10)
pytest                                                 # deterministic units pass today
```

**Models.** Provider-configurable via `crucible/llm/registry.py` — wired:
`ollama` (local, default) and `deepseek` (hosted, OpenAI-compatible). Defaults
use Ollama with different lineages for hunter vs validator (the §6 assertion):

```bash
ollama serve && ollama pull qwen2.5-coder:7b && ollama pull llama3.1:8b
```

Override per role in `crucible.toml` (`[models.hunter] provider="deepseek"
model="deepseek-chat"`, key via `DEEPSEEK_API_KEY`) or env
(`CRUCIBLE_PROVIDER_HUNTER`, `CRUCIBLE_MODEL_HUNTER`, `CRUCIBLE_API_KEY_HUNTER`).

## What Phase 1 "done" needs (specs.md §14)

1. `crucible run --repo .../fixture-py` completes and emits a report
2. kill mid-run, resume, lose no completed work
3. hunter ≠ bug-validator model; startup fails if identical ✅ (`test_registry.py`)
4. bug-detection and reachability are separate model calls ✅ (topology)
5. every upheld finding: populated threat model + PoC (fails clean / passes patched) + provenance
6. `fixture-clean` yields zero upheld findings
7. sandbox escape-test suite passes
8. tool outputs over threshold offloaded to disk ✅ (`test_offload.py`)
9. continuation cap enforced and observable ✅ (`test_continuation_cap.py`)
10. fork rate + per-tool counts in `crucible status`
11. prompt changes regression-tested against `fixture-holdout`

## Not in this draft (Phase 0 and Phase 2+)

Phase 0's single-session `security-audit` skill (§2), and Gapfill / Dedup /
Feedback / cross-repo Trace / VVS / Fixer (§11, §15).
