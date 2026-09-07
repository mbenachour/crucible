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
| `crucible/graph/nodes/recon.py` | §9.1 + issue #5 | **R0 seed + R3 decompose done; R1/R2 model steps wired** |
| `crucible/recon/` | issue #5 — `seed.py` (R0), `decompose.py` (R3), `schema.py` | done (deterministic) |
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
| `crucible/sandbox/__init__.py` | §10 provider adapter protocol + policy | done |
| `crucible/sandbox/docker.py` | §10 Docker dev backend (`DockerSandboxProvider`) | wired + smoke-tested (create/exec/destroy, ro source mount, `--network none`); escape-test suite (§14.7) still owed |
| `crucible/store/` | §3 SQLite domain store (findings/validations/wishlist/tool-usage) | done |
| `crucible/skills/` | §7 prompt files with `version:` front-matter | 28 attack-class methodologies (full builtin taxonomy, issue #16) + 2 validators + 3 recon; fixture tuning owed (#3) |
| `tests/fixtures/repos/` | §12 golden fixtures + `bugs.yaml` manifests | fixture-c / -py / -clean / -holdout seeded |

## Two stores, deliberately (§3)

LangGraph `SqliteSaver` holds **execution** state (`checkpoints.sqlite`).
`crucible/store/` holds **domain** state (`findings.sqlite`) — findings must
outlive and be queryable independently of any run.

## Install, build, use

### Prerequisites
- Python ≥ 3.11
- **Docker** running (the sandbox backend; skip with `--no-sandbox`). The
  invoking user must reach the daemon without `sudo` — add it to the `docker`
  group (`sudo usermod -aG docker "$USER"`, then re-login), or prefix commands
  with `sg docker -c '…'` for the current session. First run pulls
  `python:3.12-slim-bookworm`.
- An LLM provider — local **Ollama** (default) or **DeepSeek** (hosted)

### Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # editable install + pytest/ruff
crucible --help
```

### Models

Provider-configurable via `crucible/llm/registry.py`. Wired providers: `ollama`
(local, default) and `deepseek` (hosted, OpenAI-compatible). Defaults use Ollama
with **different lineages** for hunter vs validator (the §6 assertion):

```bash
ollama serve
ollama pull qwen2.5-coder:7b     # recon + hunter
ollama pull llama3.1:8b          # validators
```

Override per role, highest precedence last: `crucible/config.py` defaults →
`crucible.toml` → env vars.

```toml
# crucible.toml — put a hosted model in the Hunter slot
[models.hunter]
provider = "deepseek"
model    = "deepseek-chat"       # api key via DEEPSEEK_API_KEY
```

```bash
# or by env. `<ROLE>_LLM` is the short provider alias; CRUCIBLE_PROVIDER_<ROLE> wins.
export RECON_LLM=deepseek                    # ollama | deepseek | openai (openai reserved)
export DEEPSEEK_MODEL=deepseek-v4-flash      # default model for any deepseek role
export DEEPSEEK_API_KEY=sk-...
export CRUCIBLE_MODEL_HUNTER=deepseek-v4-pro # per-role model (wins over DEEPSEEK_MODEL)
```

Secrets and overrides: a `.env` at the repo root (gitignored; loaded automatically
by `crucible run` / `status`) — e.g. `DEEPSEEK_API_KEY=sk-...`,
`DEEPSEEK_MODEL=deepseek-v4-flash`, `RECON_LLM=deepseek`.

### Run

```bash
crucible run --repo <path-to-target-checkout>
```

| Flag | Default | Meaning |
|---|---|---|
| `--repo` | *(required)* | read-only checkout to audit |
| `--workspace` | `.crucible-workspace` | agent-writable, git-initialised working tree |
| `--checkpoint-db` | `checkpoints.sqlite` | LangGraph execution state (resume) |
| `--store-url` | `sqlite:///findings.sqlite` | domain store (findings, validations, tool usage) |
| `--config` | `crucible.toml` | model config file |
| `--resume <run_id>` | — | continue a run from its last checkpoint |
| `--no-sandbox` | off | skip the Docker boot check (nodes needing exec will fail) |

Phase 1 status: Recon is implemented; Hunt/Validate/Report are stubs, so a run
exits at `stopped at stub node: hunt` (code 3) after Recon writes its artifacts
and checkpoints.

### Logs & tracing

Every run logs to the console and to `<workspace>/run.log` (DEBUG) — per-node
entry/exit + durations, Recon phase counts, and every `recon/errors.jsonl` line.
`CRUCIBLE_LOG_LEVEL=DEBUG` for more on the console.

Distributed tracing is opt-in — three modes, chosen by env (`crucible/obs.py`):

**LangSmith** (internal-dev path, specs §13) — full agent traces: every model
call, tool call, and middleware span, grouped by `run_id`.

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_...
export LANGSMITH_PROJECT=crucible          # defaulted if unset
crucible run --repo <path>
```

**Local OTLP only** (data-residency path) — spans go **only** to your collector
(Jaeger / Tempo / SigNoz / OpenObserve / Langfuse), never LangChain's cloud
(`LANGSMITH_OTEL_ONLY=true` is forced):

```bash
pip install "crucible[otel]"
export CRUCIBLE_OTEL=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Set both to fan out to LangSmith *and* a collector. Put these in `.env`
(gitignored, auto-loaded). Tracing off if neither is configured.

```bash
# smoke test the whole substrate against a fixture
crucible run --repo tests/fixtures/repos/fixture-py --no-sandbox
crucible status <run_id>          # fork rate + per-(role,tool) invocation counts (§14.10)
```

### Build a distributable

```bash
pip install build
python -m build                  # -> dist/crucible-<v>-py3-none-any.whl + .tar.gz
pipx install dist/crucible-*.whl # or: pip install dist/crucible-*.whl
```

### Tests

```bash
pytest                           # 34 deterministic units, no network / no Docker
pytest tests/test_cli.py -v      # CLI smoke: help, status, run-to-stub
```

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
