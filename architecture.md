# Architecture — Crucible (vulnerability discovery harness, Phase 1)

This document explains every component in the harness: what it is, what it does,
its key types and functions, its current build status, and how it connects to its
neighbours. It is the companion to [`specs.md`](specs.md) (the authoritative
design) and [`README.md`](README.md) (the diagram and quick start). For *why each
component exists commercially* — tied to the two Cloudflare articles — see
[`product.md`](product.md).

> **Naming note.** This file describes the *codebase*. There is a second,
> unrelated `architecture.md` produced *at runtime* inside `workspace/` — that one
> is Recon's output describing the *target repo under audit* (§7). Same filename,
> different purpose, different directory.

---

## 1. Mental model

**Agent = Model + Harness.** Everything that is not the model is the harness
(`specs.md` §0). The harness's job is to feed the model the right, narrow context
at each step and to filter its noisy output down to sound, reproducible findings.

The harness is built in **two layers**:

| Layer | Technology | Granularity | Responsibility |
|---|---|---|---|
| **Stage machine** | LangGraph `StateGraph` + `SqliteSaver` | coarse (6 nodes) | ordering, fan-out, checkpoint/resume, the producer–consumer loop |
| **Agent stage** | LangChain `create_agent` + middleware | fine (one agent loop per node) | tool use, context management, continuation, per-role policy |

Four operating rules shape every component (`specs.md` §1):

1. **The filesystem is the state surface.** Agents read/write files; the harness
   reads files. Large artifacts never pass through model context or the DB.
2. **The LLM is stateless compute.** Durable state = LangGraph checkpoints +
   `workspace/` + git.
3. **Deterministic code does deterministic work.** Path checks, schema
   conformance, patch/test parsing, dedup pre-filtering, report rendering — plain
   Python, no model call.
4. **Hunters over-report; the funnel filters.** Hunt is tuned for coverage, not
   precision. The gates downstream do the refining.

---

## 2. Repository layout

```
crucible/
  graph/        # the stage machine
    build.py            StateGraph assembly + checkpointer
    state.py            CrucibleState / HuntTask TypedDicts
    hooks.py            continuation gate, offload/compaction hooks
    nodes/             one module per stage
      recon.py
      hunt.py
      validate_mechanical.py
      validate_bug.py
      validate_reachability.py
      report.py
  llm/
    registry.py         role -> model endpoint; hunter != validator assertion
    classify.py          response-text classification
  validation/
    schema.py            Finding schema + tautology deny-list
    mechanical.py        deterministic Pass-A gates + PoC gate
  workspace/
    layout.py            workspace path constants
    fs.py                init + git-commit-per-node
  agents/
    tools.py             agent tools + universal offload wrapper
    instrumentation.py   per-tool invocation counters
  sandbox/
    __init__.py          SandboxProvider protocol + isolation policy
  store/
    models.py            SQLAlchemy domain tables
    dao.py               data-access helpers + stable cross-run key
  skills/
    attack_classes/*.md  prompt files, version: front-matter
    validate/*.md
  cli.py                 `crucible run`, `crucible status`
tests/
  test_*.py              deterministic unit tests
  fixtures/repos/         golden fixtures + bugs.yaml manifests
```

Status legend used below: **done** = implemented and unit-tested · **wired** =
plumbing complete, delegates to a done component · **stub** =
`NotImplementedError` / fail-closed placeholder with spec requirements in the
docstring.

---

## 3. The stage machine — `crucible/graph/`

### 3.1 `build.py` — topology and checkpointer  · *wired*

`build_graph(checkpoint_db)` assembles the `StateGraph` and binds the SQLite
checkpointer. Topology:

```
START → recon → hunt ─┬─(continue)→ hunt        # bounded self-loop, §8
                      └─(done)────→ dedup
                                 → validate_mechanical
                                 → gapfill
                                 → feedback
                                 → loop_control ─┬─(rehunt)──→ hunt     # §11 loop
                                                 └─(proceed)─→ validate_bug
                                 → validate_bug
                                 → validate_reachability
                                 → report → END
```

- The `recon → hunt` edge is plain. The `hunt → hunt` / `hunt → dedup` choice is a
  **conditional edge** driven by `continuation_gate` (see 3.3); the
  `loop_control → hunt` / `loop_control → validate_bug` choice is a second
  conditional edge driven by `loop_gate` (see 3.3, issue #22).
- **Phase 2 producer–consumer loop (`specs.md` §11).** `dedup`, `gapfill`,
  `feedback`, and `loop_control` turn the linear pipeline into a bounded loop:
  Hunt output → Dedup folds overlaps → Validate A filters → Gapfill re-queues
  under-tested cells → Feedback rewrites their prompts → `loop_control` runs
  another cycle (up to `hooks.MAX_CYCLES`, env `CRUCIBLE_MAX_CYCLES`, default 2)
  or falls through to the validate/report tail. Each cycle contains a full §8
  bounded-continuation Hunt loop, so a bug found late is still deduped and
  validated in the same run.
- The three validate nodes are **separate nodes on purpose** (`specs.md` §1.7):
  "is this buggy?" and "can an attacker reach it?" are different questions asked of
  different models; each is narrower than the combined version.
- `checkpointer = SqliteSaver.from_conn_string(...)` — resume-from-crash is a
  library feature, not hand-rolled. Every superstep is checkpointed.
- `NODE_HOOKS = [offload_and_compact]` records where the context-management hook
  attaches to every node.

### 3.2 `state.py` — graph state  · *done*

`CrucibleState` is a `TypedDict` of **pointers and counters only** (`specs.md` §5).
Content lives on disk; the state names *where*.

| Field | Meaning |
|---|---|
| `run_id` | unique per run; the LangGraph `thread_id` |
| `repo_path` | read-only checkout of the target |
| `workspace_path` | agent-writable, git-initialised working tree |
| `repo_commit` | commit the audit is pinned to |
| `primary_language` | drives the per-language noise budget (§12) |
| `architecture_path`, `taxonomy_path` | pointers to Recon output in `workspace/` |
| `pending_hunts: list[HuntTask]` | the Hunt queue — `attack_class` + `scope_hint` only |
| `completed_cells: list[str]` | `"area::attack_class"` cells already covered |
| `finding_ids: list[str]` | pointers into `findings.sqlite` |
| `fork_count`, `continuation_count`, `token_spend` | budget counters |

`HuntTask` is a `TypedDict`: `task_id`, `area`, `attack_class`, `scope_hint`,
`continuation_count`. **Anti-pattern the shape guards against:** putting
`architecture.md` contents, file bodies, or finding descriptions in state —
LangGraph checkpoints every superstep, so fat state makes checkpointing expensive
and pushes content back into context.

### 3.3 `hooks.py` — context and continuation mechanics  · *gate done, offload stub*

Harness mechanics kept deliberately out of prompt/agent logic.

Constants: `CONTEXT_CEILING = 0.25` (§1.4) · `MAX_CONTINUATIONS = 3` (§8, hard
cap) · `OFFLOAD_TOKEN_THRESHOLD = 2000` (§7) · `MAX_CYCLES = 2`
(env `CRUCIBLE_MAX_CYCLES`, §11 producer–consumer loop bound).

- **`continuation_gate(state) -> "continue" | "done"`** — *done, unit-tested.*
  The bounded-continuation control (`specs.md` §8). Returns `"done"` when either
  the hard cap is hit **or** the completion goal is met (`_completion_goal_met`,
  currently "queue drained"; will grow to inspect `coverage/<area>.md` for
  explicit negatives against `architecture.md` entry points). The cap is a
  **safety control, not a cost control** — unbounded agent persistence was a named
  root cause of the July 2026 OpenAI incident.
- **`should_rehunt(state) -> bool` / `loop_gate(state) -> "rehunt" | "proceed"`**
  — *done (issue #22).* The outer producer–consumer bound: another cycle runs
  only if Gapfill/Feedback left work queued **and** `cycle_count < MAX_CYCLES`.
  Same fail-closed shape as `continuation_gate`. `graph_recursion_limit()`
  derives the LangGraph super-step backstop from `MAX_CYCLES` +
  `MAX_CONTINUATIONS`.
- **`offload_and_compact(node_name, state) -> state`** — *stub.* The attach point
  for tool-output offloading (large output → `workspace/offload/<call_id>.txt`,
  model gets head+tail+path) and compaction (near the ceiling, summarise prior
  turns into `coverage/<area>.md`, continue fresh). In the LangChain layer these
  map to `ContextEditingMiddleware` + `SummarizationMiddleware`
  (see [`docs/langchain-harness-notes.md`](docs/langchain-harness-notes.md)).

---

## 4. Stages — `crucible/graph/nodes/`

Each node reads/writes `workspace/`, persists domain rows to `findings.sqlite`,
and git-commits the workspace on exit. In the target architecture each stage is a
`create_agent` + middleware stack; today the model-facing bodies are stubs with
the spec contract in the docstring.

### 4.1 `recon.py` — Recon (§9.1, issues #5 + #34)  · *re-engineered per #34 (all four phases): agentic top-down read, semantic subsystems, parallel fan-out, synthesized 11-section doc*

Sub-steps in one node (shape from Visa VVAH S0–S3 / Glasswing's "threat model
builder"), implemented in `crucible/recon/`. Issue #34 splits the old R1 into
**R1a orient** (top-down lead agent → `ModuleMap`), **R1b subsystem maps** (one
agent per subsystem), and **R1c synthesis** (deterministic — the cross-cutting
content no single subsystem agent sees):

| Step | Module | Model? | Artifact |
|---|---|---|---|
| **R0 seed** | `recon/seed.py` | no | `workspace/recon/seed.json` — file index + roles, framework detection, repo-kind, build/run commands, framework-aware entry points classified into 7 kinds (`network/framework/ipc/file/cli/deserialization/other`), reflection/dynamic-dispatch facts, tree-sitter name-based call graph |
| **R1a orient** | `recon/orient.py` + `_run_orient` | RECON | `workspace/recon/module_map.json` — the lead agent's `ModuleMap` (subsystems by responsibility, `external_facing`, `depends_on`, repo-wide build, auth paragraph); deterministic `partition_subsystems` guard rail (boundary source: LLM > manifests > CODEOWNERS > `fallback_partition`; assign longest-prefix → `misc`; split >40 % LOC; merge <3 %; clamp N ≤ `RECON_MAX_SUBAGENTS`) |
| **R1b subsystem maps** | `_run_map` fan-out | RECON | per-subsystem `SubsystemMap` — `_emit` forces one `tool_choice=SubsystemMap` call; runs one agent per subsystem concurrently on `ThreadPoolExecutor(RECON_MAX_PARALLEL)`, distinct `thread_id`, `=1` keeps sequential |
| **R1c synthesis** | `recon/synthesize.py` + `recon/decompose.render_architecture` | no | `workspace/architecture.md` (11 target sections), `workspace/recon/attack_surface.json` — `rank_attack_surface` (exposure × entry-kind severity × sink-proximity × threat-model corroboration), `derive_auth_model`, `stitch_data_flows`; a degradation banner when `recon_quality != full` |
| **R2 threat model** | node `_run_threatmodel` | RECON | `workspace/recon/threat_model.json` — `response_format=ThreatModel`; attackers, assets, trust boundaries, STRIDE, repo-specific classes |
| **R3 decompose** | `recon/decompose.decompose` | no | seeds `state["pending_hunts"]` + `workspace/recon/task_manifest.json`; chunk `area` = owning subsystem, `external_facing` subsystems get a priority bump, cross-subsystem stitched flows → `taint` chunks (`injection_passthrough`, `A/x.py:10 -> B/y.py:88`) |

`recon_quality ∈ {full, partial, seed_only}` is written to graph state (and
`workspace/recon/recon_quality.txt`, echoed by the CLI): `full` = every
subsystem contributed a map **and** R2 succeeded. Knobs: `CRUCIBLE_RECON_MAX_SUBAGENTS`
(8), `CRUCIBLE_RECON_MAX_PARALLEL` (4), `CRUCIBLE_RECON_ORIENT_BUDGET` (24).

**Repo-kind → baseline attack classes** (`BASELINE_BY_KIND`): `web_api` → OWASP-ish,
`native` → memory-safety, `mobile` → MASVS-ish, `library`/`iac`/`cli`/`unknown`.
Pruned against the primary language (`LANG_INCOMPATIBLE` — no memory classes in
Python/JS). Per-entry-point class choice is framework-aware (an Express route in
a mobile repo still gets web classes).

**Typed hunt chunks** (`HuntTask.chunk_type`): `taint` (entry point + a dynamic
sink nearby → `file:line -> file:line`, priority 1), `catch_all` (entry point ×
class, and an always-on area × baseline sweep), `risk` (a reflection fact),
`specialist` / `threat_fallback` (from R2).

**Resilience:** no registry, or an R1/R2 model failure → logged to
`workspace/recon/errors.jsonl`, the run continues on the seed alone (R3 still
produces a queue). **Recon quality drives everything downstream** — Cloudflare's
validation-rejection rate dropped 40%→11% largely from better context here.

### 4.2 `hunt.py` — Hunt (§9.2)  · *wired — two-phase agent per cell; sandbox-executing; feeds the validate queue*

One task = **one attack class + one scope hint + `architecture.md` + prior
coverage**. Never "find vulnerabilities in this repo" — narrow scoping is what
makes the model behave like a researcher instead of wandering.

**Node flow.** Pops up to `HUNT_MAX_TASKS_PER_RUN` cells (env
`CRUCIBLE_HUNT_MAX_TASKS`, default 6) off the head of `pending_hunts` per
invocation; the bounded-continuation self-loop (§8, cap 3) re-enters for the
next batch, so the effective ceiling is `MAX_TASKS * 4`, and `continuation_count`
is incremented here so the gate cannot loop forever. Whatever is unhunted when
the continuation cap is hit is left for Gapfill (Phase 2). Per cell:

1. **Explore** — a plain ReAct agent (`build_agent` on `ModelRole.HUNTER`, the
   same middleware stack Recon uses) with a per-task budget of
   `CRUCIBLE_HUNT_EXPLORE_LIMIT` model calls (default 14; the §8
   `MODEL_CALLS_PER_TASK` hard cap still bounds it). Tools: `list_dir` /
   `read_file` / `search` (read-only, repo-rooted), `sandbox_exec` (a
   per-task Docker container — `/src` ro, `/scratch` writable, no egress),
   `fork_sibling`, `wishlist_write`. `ToolGateMiddleware` enforces the set;
   every call is counted (§1.12).
2. **Emit** — one forced `tool_choice=HuntResult` call over a fresh message list
   built from a digest of the exploration transcript (small open-weight models
   almost never call an emit-tool on their own, and `create_agent` discards a
   plain-text answer — same reason Recon splits R1/R2). `HuntResult` carries an
   optional `Finding` plus a `negative_note`, so a reasoned negative is
   first-class coverage.
3. **Parse-time filters (no model call):** the **tautology deny-list**
   (`validation/schema.tautology_reasons`) drops a finding whose attacker
   privilege already implies its impact.
4. **Persist:** surviving findings → `workspace/findings/<id>.json` +
   `FindingRow` (`status='raw'`, Hunter model / prompt-version / sampling
   provenance, `stable_key` for cross-run dedup); wishes → `WishRow`;
   `coverage/<area>.md` gets the finding line or the negative note; the
   workspace is git-committed.

- **Over-reports by design** *(prompt-tuning target, issue #3)* — the current
  open-weight Hunter (deepseek-v4-flash) tends to file careful negatives rather
  than over-report; tightening that is skill-prompt work against the fixtures.
- **Sibling forking:** `fork_sibling` writes a `risk` chunk to
  `workspace/recon/forks.jsonl`, drained into `pending_hunts` for the next
  continuation (bounded by `MAX_FORKS_PER_RUN`). Fork rate is tracked per model.
- **Shallow detection (§13):** a cell whose explore made `< SHALLOW_TOOLCALLS`
  tool calls and produced no finding is re-queued once (a crashed dependency
  looks like a fast clean pass). An explore that ends on a provider 400
  (dangling `tool_calls`) is caught per-cell and still goes to the emit phase.

Three failure modes designed against: (a) editing source so the exploit works →
killed by the PoC gate; (b) tautological test → killed by the deny-list; (c)
exploit runs but the threat model is nonsense → killed by the `threat_model`
requirement.

### 4.3 `validate_mechanical.py` — Validate Pass A (§9.4)  · *wired → `validation/mechanical.py`; persistence added for the Phase 2 loop*

No model calls. Iterates `state["finding_ids"]`, calls
`mechanical.check_finding(..., store=store)`, and **persists the verdict**:
`FindingRow.status` → `mechanical_passed` / `mechanical_failed` plus a
`ValidationRow(pass_name="mechanical")` with the accumulated reasons.
`check_finding` now loads the `Finding` from the store (falling back to
`findings/<id>.json`) instead of the old "finding not found" placeholder, so the
deterministic gates (path/range, schema, `git apply --check`, non-empty PoC)
actually run. **The PoC gate itself is still fail-closed** (`_check_poc_gate`
returns `"poc_gate not implemented"` pending the sandbox exec path, issue #9) —
so no finding reaches `mechanical_passed` yet, but the funnel and the reasons are
real, which is what Feedback (issue #21) consumes. This is the *smallest* change
that lets the §11 loop show a real funnel; it does **not** close issue #9.

### 4.3a `dedup.py` — Dedup (§11, issue #20)  · *done — deterministic shortlist + agent judge*

Runs on the `hunt → dedup` edge, before Validate A, so no model spend downstream
is wasted on a duplicate. Two phases (`specs.md` §11):

1. **Deterministic shortlist (no model).** Inverted indexes over *structured*
   fields — cited `file_path`, normalised `threat_model.boundary_crossed`, and
   rare description tokens (document-frequency-filtered). A pair is a candidate
   if it shares the file bucket, or the boundary bucket **and** a rare token.
   Bounded at `DEDUP_MAX_PAIRS = 40`, ranked by shared-bucket count. A cross-run
   `stable_key` collision is an automatic duplicate — no model call.
2. **Agent judge.** For the remaining shortlist, a `VALIDATOR_BUG`-model call
   ([`skills/dedup/judge.md`](crucible/skills/dedup/judge.md), forced
   `tool_choice=DupeJudgment`) decides whether *one fix at one root cause* closes
   both. Bounded at `DEDUP_MAX_JUDGE = 12` calls.

Duplicates are folded via `store.link_duplicate(dup, canonical)` (status →
`duplicate`, `payload["duplicate_of"]` set — no schema migration) and dropped
from `state["finding_ids"]`. Canonical selection prefers an earlier run, then the
lexicographically smaller id. Cluster report → `workspace/dedup/clusters.json`.
Degrades cleanly with no registry (deterministic `stable_key` folding only) or
no store (skips).

### 4.3b `gapfill.py` — Gapfill (§11, issue #19)  · *done — deterministic, no model*

Runs after Validate A. Reconstructs Recon R3's intended `(area × attack_class)`
matrix from `recon/task_manifest.json`, then classifies each cell against
`state["completed_cells"]`, the parsed `coverage/<area>.md` blocks, and the
store (`crucible/coverage.py`), re-queued in this order:

1. **failed** — hunted `< GAPFILL_CELL_RETRY = 2` times and its only finding
   failed Validate A on an *actionable* mechanical reason (bad line range, patch
   does not apply, tautology). Ranked first so breadth does not starve it;
   Feedback rewrites its prompt with the reason.
2. **missing** — never hunted.
3. **barren** — hunted `< GAPFILL_CELL_RETRY` times with no finding at all.

Bounded at `GAPFILL_MAX_REQUEUE = 8` per invocation; only appends to
`pending_hunts`. Every bucket shrinks monotonically as cells get covered /
retried, so the loop converges. Logs
`matrix / covered / failed / missing / barren / requeued`.

### 4.3c `feedback.py` — Feedback (§11, issue #21)  · *done — deterministic trace analysis*

Runs after Gapfill, over the freshly re-queued `pending_hunts`. For each queued
cell it checks three trigger signals from this run's own trace and, if one
fires, **appends** a `feedback:` addendum to `scope_hint` (and nothing else —
never a cap, a deny-list, or a continuation count):

| Trigger | Signal | Addendum |
|---|---|---|
| `validation_failure` | `coverage.actionable_mechanical_failures` (shared with Gapfill) has this attack class — a `mechanical_failed` finding with a path/range/patch/schema reason, not the fail-closed PoC gate | "cite exact file:line at the pinned commit; give a patch that applies clean" |
| `shallow` | task `continuation_count ≥ 1`, or every coverage pass for the class was "no result emitted" | "run one trivial `sandbox_exec` first to confirm the sandbox; go deeper; do not return empty" |
| `repeated_miss` | ≥ 2 prior passes for the class, zero findings | "name the guard that makes this safe, or escalate one concrete primitive with a PoC sketch" |

Bounded at `FEEDBACK_MAX_REWRITES = 6`; skips a prompt that already carries a
`feedback:` note. The attack class is recovered from
`FindingRow.hunter_prompt_version` (`"<class>@<ver>"`).

### 4.3d `loop_control.py` — producer–consumer loop control (§11, issue #22)  · *done*

Owns the outer bound. Increments `cycle_count`; if `hooks.should_rehunt` (work
queued **and** `cycle_count < MAX_CYCLES`) it resets `continuation_count = 0` so
the §8 continuation loop runs again for the re-queued cells, and `hooks.loop_gate`
routes the conditional edge back to `hunt`. Otherwise control falls through to
`validate_bug`. Every cycle is a full bounded-continuation Hunt loop, so the
effective Hunt ceiling is `MAX_CYCLES × (MAX_CONTINUATIONS + 1)` batches — a
second safety bound on top of the §8 cap, never a replacement for it.

### 4.4 `validate_bug.py` — Validate Pass B, "is it real?" (§9.4)  · *stub*

Runs `ModelRole.VALIDATOR_BUG`, which **must be a structurally different
open-weight model from the Hunter** (§6). Re-reads the code with a
disprove-oriented prompt ([`skills/validate/bug.md`](crucible/skills/validate/bug.md)).
Classifies the response before parsing. **Cannot file findings** (§1.6): the only
output is `upheld` / `refuted` + reasoning, written to `validations`. No
finding-creation tool, no write access to the `findings` table.

### 4.5 `validate_reachability.py` — Validate Pass C, "is it reachable?" (§9.4)  · *stub*

Separate call, separate agent from Pass B
([`skills/validate/reachability.md`](crucible/skills/validate/reachability.md)).
Question: can attacker-controlled input actually reach this code from outside the
system? This is the stage that converts "there is a flaw" into "there is a
reachable vulnerability." **Phase 1: single-repo only.** Phase 3 replaces it with
the cross-repo Tracer (unified symbol index + dependency graph).

### 4.6 `report.py` — Report (§9.5)  · *stub*

Deterministic script, **no model**. Selects findings that are `upheld` by **both**
Pass B and Pass C, renders `report.json` (+ optional markdown) into `workspace/`
with full provenance per finding (model + prompt version + sampling params).
Output is queryable data, not free-form prose.

---

## 5. Validation layer — `crucible/validation/`

### 5.1 `schema.py` — finding schema + deny-list  · *done*

**`Finding`** (Pydantic model). Field order is **load-bearing** and must not be
reordered — `threat_model` is first so the model commits to an attacker and a
boundary *before* describing a bug. Pydantic preserves declared order for vLLM
guided-JSON generation.

| Field | Purpose |
|---|---|
| `threat_model` | `ThreatModel(attacker, boundary_crossed, assumption_broken)` |
| `title` | one line |
| `file_path`, `line_start`, `line_end` | cited location, checked against `repo_commit` |
| `description` | prose |
| `poc_test` | test source — must fail clean, pass patched |
| `proposed_patch` | unified diff |
| `severity` | `Severity` enum: low / medium / high / critical |

**`tautology_reasons(finding) -> list[str]`** — parse-time, no model call. Returns
deny-list hits (empty = accepted):

- **Privilege-implies-impact pairs** (`_PRIVILEGE_IMPACT_PAIRS`): reject where the
  attacker already holds privilege equivalent to the claimed impact — the "user
  with DB write access can write to the DB" class.
- **Tautological PoC markers** (`_TAUTOLOGICAL_TEST_MARKERS`): `assert True`,
  "this always passes", etc. — a soft signal alongside the PoC gate.

### 5.2 `mechanical.py` — deterministic gates (Pass A)  · *gates done, PoC-gate exec stub (fail-closed)*

`check_finding(finding_id, *, repo_path, repo_commit, workspace_path)
-> MechResult`. `MechResult` carries `status` (`MechStatus.PASSED` /
`MECHANICAL_FAILED`) and an accumulated `reasons` list. Gates:

| Gate | Function | What it checks |
|---|---|---|
| path & range | `_check_path_and_range` | cited file exists at `repo_commit`; `1 ≤ line_start ≤ line_end ≤ n_lines` |
| schema | `_check_schema` | `threat_model` fully populated; runs `tautology_reasons` |
| patch applies | `_check_patch_applies` | `git apply --check` the unified diff against the unmodified tree |
| PoC parses | `_check_poc_parses` | non-empty (TODO: `ast.parse` for py, compile-only for c) |
| **PoC gate** | `_check_poc_gate` | test **fails** on the unmodified repo (demonstrates the bug) and **passes** with the patch applied; any source change outside the patch invalidates the finding. Runs in the sandbox. **Currently returns a failure reason — fail-closed until the sandbox exec path exists.** |

`specs.md` §9: *no PoC against the unmodified codebase, no finding.*

---

## 6. LLM layer — `crucible/llm/`

### 6.1 `registry.py` — model routing + the core assertion  · *done*

- **`ModelRole`** enum: `RECON`, `HUNTER`, `VALIDATOR_BUG`, `VALIDATOR_REACH`.
- **`Provider`** enum: `ollama` (local, `langchain-ollama`) and `deepseek`
  (hosted, OpenAI-compatible, `langchain-deepseek`) are **wired**; `vllm` /
  `openai_compat` are reserved and raise from `chat_model` until built.
- **`ModelEndpoint`** dataclass (frozen): `role`, `model` (provider-native name —
  an Ollama tag or `deepseek-chat`), `provider`, `base_url` (`""` → the
  provider's default), `api_key` (optional; falls back to `DEEPSEEK_API_KEY`),
  `temperature`, `top_p`, `num_ctx` (ollama), `num_predict` (→ `max_tokens` for
  OpenAI-compatible providers), `seed`, `extra`.
- **`ModelRegistry`**:
  - Constructor runs `_assert_hunter_ne_validator()` — **fails loudly if
    `(HUNTER.provider, HUNTER.model) == (VALIDATOR_BUG.provider, …)`**
    (`specs.md` §6, §14.3). The primary noise-control mechanism: a second model
    with different weights and training data is an adversarial third party, not a
    model grading its own homework.
  - `endpoint(role)`, `sampling_params(role)` (flattened dict recorded on
    findings, no `api_key`), `chat_model(role)` → a LangChain `BaseChatModel`
    (`ChatOllama` / `ChatDeepSeek`), cached per role.
  - `from_env()` layers `CRUCIBLE_PROVIDER_<ROLE>` / `CRUCIBLE_MODEL_<ROLE>` /
    `CRUCIBLE_API_KEY_<ROLE>` / `CRUCIBLE_{OLLAMA,DEEPSEEK}_BASE_URL` over
    `crucible/config.py` `DEFAULT_ENDPOINTS`; `crucible.toml` `[models.<role>]`
    sits in between.
- Design stance: **providers are interchangeable commodities.** They change
  temperature, caching, and inference-effort budgets over time — build to absorb
  that volatility.

### 6.2 `classify.py` — response classification  · *done, unit-tested*

Runs **before parsing, on every model call** (`specs.md` §6, §1.10). Classifies
the **response text, not exception types** — transient errors arrive inside
`200 OK`, and an unclassified response is a failed task, not a clean one.

`classify(body, *, expect_json=True) -> ResponseClass`:

| Class | Trigger | Handling |
|---|---|---|
| `OK` | parses, non-empty, conformant | proceed |
| `TRANSIENT_ERROR` | empty body, or error text in body (`429`, `502/503/504`, "overloaded", "gateway timeout"…) | retry with backoff, capped at `MAX_TRANSIENT_RETRIES = 3` |
| `REFUSAL` | model declines (`_REFUSAL_PATTERNS`) | log, **fail the task — no silent retry-with-rephrasing** |
| `MALFORMED` | unparseable despite constraints | one repair attempt (`MAX_REPAIR_ATTEMPTS = 1`), then fail |

`_looks_like_json` tolerates ```` ```json ```` fences. **`REFUSAL` rate is tracked
as a product metric** — low refusal on open weights is a stated differentiator.

---

## 7. Workspace layer — `crucible/workspace/`

The filesystem is the collaboration surface between agents, not just storage
(`specs.md` §7). `workspace/` is git-initialised and **separate from the
read-only source checkout**.

### 7.1 `layout.py` — path constants  · *done*

```
workspace/
  architecture.md        Recon output; every Hunter reads this
  taxonomy.json          attack classes for this repo
  coverage/<area>.md     what was examined, by whom, what was found
  findings/<id>.json
  scratch/<task_id>/     per-task PoC compile/run space
  offload/<call_id>.txt  full tool outputs over the threshold
```

Helpers: `architecture_path`, `taxonomy_path`, `coverage_path(ws, area)`,
`finding_path(ws, id)`, `scratch_path(ws, task_id)`, `offload_path(ws, call_id)`.
`SUBDIRS` lists the directories `init_workspace` creates.

### 7.2 `fs.py` — workspace + git operations  · *done*

- `init_workspace(path)` — creates the tree, the subdirs, and an empty initial
  git commit.
- `commit_node(path, node_name, run_id)` — `git add -A` + commit after each node;
  returns the new HEAD sha. Gives agents a **diff of what changed since they last
  looked** and a free audit trail (relevant to enterprise change-management
  compliance).
- `diff_since(path, ref)` — `git diff ref..HEAD`.

---

## 8. Agents layer — `crucible/agents/`

### 8.1 `tools.py` — agent tools + universal offload wrapper  · *offload done, tools stub*

- **`ToolContext`** dataclass threaded through every tool: `run_id`, `task_id`,
  `repo_path` (read-only mount), `workspace_path`, `usage` (the `ToolUsage`
  accumulator).
- **`offload_if_large(ctx, output) -> str`** — *done, unit-tested.* The context
  killer in this domain is compiler stderr / test output / grep dumps. Output
  above `OFFLOAD_TOKEN_THRESHOLD` is written whole to
  `workspace/offload/<call_id>.txt`; the model gets **head + tail + a pointer**,
  bounded by both line count and characters (tool output is sometimes one
  enormous line). Applied as a wrapper to **every** tool, not per-tool.
- **Tool stubs**, each wrapped in `ctx.usage.record(<name>)`:
  - `bash` — general purpose; runs in the sandbox.
  - `read_file(ctx, rel_path, start, end)` — scoped read, offload-wrapped
    (partially real).
  - `grep` — scoped search.
  - `sandbox_exec` — compile/run in the sandbox.
  - `fork_sibling(ctx, structural_seed, reason)` — spawn a scoped sibling Hunter;
    increments `fork_count`.
  - `wishlist_write(ctx, need, context, blocked_task_id)` — structured request for
    a missing dependency (see 10.2).

### 8.2 `instrumentation.py` — per-tool counters  · *done*

*Instrument what agents actually reach for* (`specs.md` §1.12). Cloudflare plumbed
a static analyzer through the whole system; Hunters invoked it zero times in a
month, while the wishlist became the most-used tool. Measure usage, delete what
goes unused.

**`ToolUsage(run_id, role)`** accumulates in-process; flushed to the store at node
boundaries. `record(tool_name)` is a context manager that bumps `counts`, adds to
`latency_s`, and bumps `errors` on exception. `rows()` emits dicts for
`ToolUsageRow`. Counters are **domain state** — they live in `findings.sqlite`,
keyed by `(run_id, role, tool_name)`, not in graph state.

---

## 9. Sandbox — `crucible/sandbox/`

Hunters compile and execute untrusted, model-generated code. **This layer is not
hand-rolled** (`specs.md` §10) — a provider adapter makes the backend swappable
(E2B / Modal / self-hosted AerolVM across Docker, gVisor, Firecracker).

The **dev backend is Docker** (`crucible/sandbox/docker.py`,
`DockerSandboxProvider`) — shells out to the `docker` CLI, no SDK dependency.
Smoke-tested end to end (create → exec → destroy) on Ubuntu 24.04 with
`docker.io` 29.x: source mount is read-only, `/scratch` tmpfs is writable, root
fs is read-only, and `--network none` blocks egress. Run needs the invoking user
in the `docker` group (persistent) or `sg docker -c '<cmd>'` for the current
login session.

### 9.1 Interface  · *protocol done; Docker backend wired + smoke-tested*

- **`SandboxProvider`** (`Protocol`): `create(task_id, repo_mount, limits) -> Sandbox`,
  `exec(cmd, timeout_s) -> ExecResult`, `destroy()`.
- **`Sandbox`** (`Protocol`): `exec`, `destroy`.
- **`ExecResult`**: `exit_code`, `stdout`, `stderr`, `timed_out`,
  `egress_attempted` (→ run-level alert).
- **`SandboxLimits`**: `cpu_seconds`, `memory_mb`, `wall_clock_s`, `max_pids`,
  `max_file_bytes`, `disk_quota_mb`, `network_egress = False`.

### 9.2 Policy (enforced regardless of backend)

- **No network egress by default** — the single most important control; the exact
  boundary whose failure started the July 2026 incident chain. Any egress attempt
  is a run-level alert.
- No host credentials; no cloud instance metadata (`169.254.169.254`); no access
  to the harness's own DB or config.
- Hard ceilings: CPU seconds, memory, wall clock, PIDs, file size, disk quota.
- Read-only source mount; writes only to `scratch/<task_id>/`, destroyed on
  completion.
- **Own the patch cadence** — pin policy is a security decision.

### 9.3 `assert_boot_environment()`  · *done (in `sandbox/docker.py`)*

Fails loudly if `docker` is missing from `PATH` or `docker info` fails (daemon
down / socket not reachable). Then detects the **nested-containerization trap**:
if the harness runs inside Docker (`/.dockerenv`) and the sandbox uses namespace
isolation, it runs a `docker run` smoke test and, on failure, tells the operator
to add `seccomp=unconfined` / `apparmor=unconfined` or pick a non-namespace
backend rather than degrading silently. `cli.py run` calls it before building the
provider unless `--no-sandbox` is passed.

### 9.4 Acceptance

An escape-test suite (network calls, metadata reads, writes outside scratch, fork
bombs, oversized allocations) must be fully contained, every attempt logged
(§14.7).

---

## 10. Store — `crucible/store/`

**Two stores, deliberately** (`specs.md` §3). LangGraph checkpoints hold
*execution* state (`checkpoints.sqlite`). This store holds *domain* state
(`findings.sqlite`) — findings, validations, provenance, wishlist, tool-usage
counters. **Findings must outlive and be queryable independently of any run.**

### 10.1 `models.py` — SQLAlchemy tables  · *done*

| Table | Row | Key columns |
|---|---|---|
| `runs` | `Run` | `run_id` (pk), `repo_path`, `repo_commit`, `primary_language`, `status` |
| `findings` | `FindingRow` | `finding_id` (pk), `run_id`, `stable_key` (cross-run dedup), `payload` (the `Finding` JSON, field order preserved), `status`, **provenance**: `hunter_model`, `hunter_prompt_version`, `hunter_sampling` |
| `validations` | `ValidationRow` | `finding_id`, `pass_name` (`mechanical`/`bug`/`reachability`), `verdict` (`upheld`/`refuted`/`mechanical_failed`), `reasoning`, `model`, `prompt_version`, `response_class` |
| `wishlist` | `WishRow` | `run_id`, `blocked_task_id`, `need`, `context`, `status` (`open`/`resolved`/`requeued`) |
| `tool_usage` | `ToolUsageRow` | `run_id`, `role`, `tool_name`, `count`, `latency_s`, `errors` |

**Finding status funnel** (the `status` column):
`raw → mechanical_failed | mechanical_passed → bug_refuted | bug_upheld →
reach_refuted | reach_upheld`. Only `reach_upheld` reaches Report.

### 10.2 `dao.py` — data access  · *done*

**`Store(url="sqlite:///findings.sqlite")`** — creates tables on init, provides a
transactional `session()` context manager.

- Writes: `create_run`, `add_finding`, `record_validation`,
  `set_finding_status`, `add_wish`, `flush_tool_usage`.
- Reads: `upheld_findings(run_id)` (status `reach_upheld` — survived **both**
  model passes), `tool_usage(run_id)`.
- **`stable_key(file_path, function, boundary) -> str`** — a 16-char SHA-1 over
  *structured* inputs (not the free-text description). Cross-run identity: a later
  run reopens the existing record instead of spawning a new one. This is the hook
  Phase 2 Dedup builds on.

### 10.3 The wishlist as an interface (§9.3)

When an agent needs something it does not have — a build environment, a VM, prod
config, a credential to confirm a PoC — it writes a structured `WishRow` with
enough context to **re-run that exact task** once a human (or an autonomous
self-healing step) provides the dependency. Cloudflare's wishlist was written to
25,472 times across 128 repos and became the primary channel agents use to talk
back to the team. Design its UX as a primary interface, not a log.

---

## 11. Skills / prompts — `crucible/skills/`

**Prompts are files with `version:` front-matter, recorded on every finding**
(`specs.md` §4). They are the highest-value asset — diffable, versioned,
hot-swappable — and carry Cloudflare's original attacker scenarios, bug classes,
and anti-pattern detections nearly unchanged.

```
skills/
  attack_classes/<name>.md   front-matter (name, version, description, languages,
                             builtin) + methodology body
  validate/bug.md            Pass B — disprove-oriented
  validate/reachability.md   Pass C — attacker-input-to-sink
  recon/                     (to add) per-slice recon subagent prompt
```

**Progressive disclosure** (§7): at Hunt start, load only the front-matter
(`name`, `description`) for *every* attack-class file; load the full methodology
body only for the scoped class. Loading the whole taxonomy upfront degrades
performance before work begins.

**Attack-class library (issue #16): complete.** Every class the decomposer
(`recon/decompose.py`) can queue has a methodology file — 28 in total, covering
injection (web/CLI/lib), access control, C/C++ memory safety, parsing, library
correctness, mobile, and IaC/config. `specialist` / `threat_fallback` chunks
carrying a model-invented `repo_specific_class` have no file and fall back to
the generic methodology plus the chunk `scope_hint`. `validate/bug.md` and
`validate/reachability.md` cover the two validator passes. Each methodology body
has the same shape: attacker &
boundary · where to look · move into execution · PoC shape · output · anti-patterns to
reject in your own output.

---

## 12. CLI — `crucible/cli.py`  · *wired*

Typer app, entry point `crucible`.

- **`crucible run --repo <path> [--workspace ...] [--checkpoint-db ...] [--resume <run_id>]`**
  Initialises the workspace, builds the graph, and `invoke`s it with a fresh
  `CrucibleState`. `--resume` reuses a `run_id` so LangGraph reloads from the
  checkpoint. (`repo_commit` / `primary_language` detection is TODO.)
- **`crucible status <run_id> [--store-url ...]`**
  Prints per-`(role, tool)` invocation counts and error counts from
  `tool_usage` (§14.10). Fork rate is derived from the `fork_sibling` row.

---

## 13. Tests and fixtures — `tests/`

### 13.1 Unit tests  · *18 passing*

| File | Covers | Spec |
|---|---|---|
| `test_classify.py` | response classification, incl. error-text-in-`200`, refusal ≠ silent-retry | §6, §1.10 |
| `test_schema_denylist.py` | `threat_model` is field 0; privilege=impact rejection; tautological PoC marker | §9.2 |
| `test_registry.py` | startup fails when `HUNTER.model == VALIDATOR_BUG.model`; sampling params recorded | §6, §14.3 |
| `test_offload.py` | small output passes through; large output → disk + head/tail pointer | §7, §14.8 |
| `test_continuation_cap.py` | gate stops at `MAX_CONTINUATIONS` even with work pending | §8, §14.9 |

### 13.2 Golden fixtures  · *seeded*

Built **before** the first Hunter prompt so prompt changes are measurable
(`specs.md` §12).

| Fixture | Contents | Role |
|---|---|---|
| `fixture-c/` | `tlv.c` (attacker-controlled length → `memcpy` size), `name.c` (off-by-one write), ASan/UBSan `Makefile` | memory-safety recall |
| `fixture-py/` | `ping.py` (shell=True cmd injection), `cache.py` (`pickle.loads` on untrusted blob) | injection recall |
| `fixture-clean/` | correct counterparts of the above, **no planted bugs** | any upheld finding = false positive; target 0 |
| `fixture-holdout/` | `render.py` (Jinja SSTI); **never used for prompt tuning** | confirm coverage moved, not overfit |

Each fixture has a `bugs.yaml` manifest; every `BUG(<id>)` comment maps to an
entry. Recall per attack class is measured against these manifests.

**Language-aware noise budget** (§12): C/C++ give direct memory control and bug
classes memory-safe languages eliminate at compile time — set per-language FP
expectations; do not compare a C fixture's FP rate to a Python one.

---

## 14. End-to-end data flow (one finding)

1. **`crucible run --repo R`** → `init_workspace`, `build_graph`, `invoke`.
2. **Recon** fans out, writes `workspace/architecture.md` + `taxonomy.json`, seeds
   `pending_hunts` with `(area × attack_class)` cells. Workspace committed.
3. **Hunt** pops a cell. Loads front-matter for all attack classes + the full body
   for this one. Reads scoped source, compiles fragments in the sandbox, tries to
   break them. Large tool outputs offload to disk. Emits a `Finding` (guided
   JSON); the tautology deny-list runs at parse time. Row inserted into `findings`
   with `status='raw'` and Hunter provenance. `coverage/<area>.md` updated.
   `continuation_gate` decides `continue` (fresh window, ≤3×) or `done`.
4. **Dedup** (`specs.md` §11): deterministic inverted-index shortlist, then a
   `VALIDATOR_BUG` judge on the shortlist; duplicates folded to a canonical
   (`stable_key` across runs) and dropped from `finding_ids`.
5. **Validate A** (`mechanical.check_finding`): path/range, schema, patch dry-run,
   PoC gate (fail-closed). Verdict persisted → `status='mechanical_failed'` /
   `'mechanical_passed'`, no model spent.
6. **Gapfill / Feedback** (`specs.md` §11): re-queue under-tested cells, rewrite
   their prompts from this run's failures; `loop_control` runs another cycle
   (≤ `MAX_CYCLES`) or falls through.
7. **Validate B** (`VALIDATOR_BUG`, different model): re-reads, tries to disprove.
   Response classified before parse. `ValidationRow(pass_name='bug',
   verdict=...)`. Finding → `bug_upheld` / `bug_refuted`.
8. **Validate C** (`VALIDATOR_REACH`): attacker-input-to-sink path, single repo.
   Finding → `reach_upheld` / `reach_refuted`.
9. **Report**: deterministic select of `reach_upheld`, render `report.json` with
   full provenance. No model.

At any step an agent may `wishlist_write` a blocking dependency; that task is
re-runnable verbatim once the wish is resolved.

---

## 15. Deferred components (Phase 3–4) and where they attach

Designed now, built when their absence is the specific blocker (`specs.md` §11,
§15).

**Phase 2 — shipped (issues #19–#22).** Gapfill, Dedup, Feedback, and the
producer–consumer loop wiring are implemented and real-run verified (see §3.1,
§4.3a–§4.3d). They turn the linear pipeline into a bounded loop around the
existing Phase-1 tail; `validate_bug` / `validate_reachability` / `report`
remain Phase-1 stubs, so a run still stops cleanly at `validate_bug` after the
loop drains.

| Component | Phase | Attaches | Role |
|---|---|---|---|
| **Trace** | 3 | replaces Pass C | cross-repo reachability with a unified symbol index + dependency graph (Joern CPG) |
| **VVS judgment** | 4 | after Report | production reachability, wiki/Jira/git context, applicability scoring |
| **Fixer** | 4 | after VVS | automated patch + regression test; **human sign-off required before merge** |

Explicitly **out of scope for Phase 1**: all of the above, plus multi-tenancy and
per-PR CI. Full scans are periodic backlog sweeps (hours; Cloudflare's worst
exceeded 14) — per-PR needs a separate, cheaper, smaller harness.

---

## 16. Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `<ROLE>_LLM` | `registry.from_env` | short provider alias, e.g. `RECON_LLM=deepseek` (`ollama` \| `deepseek` \| `openai`*); `CRUCIBLE_PROVIDER_<ROLE>` overrides it |
| `CRUCIBLE_PROVIDER_<ROLE>` | `registry.from_env` | provider per role (`RECON`, `HUNTER`, `VALIDATOR_BUG`, `VALIDATOR_REACH`) |
| `CRUCIBLE_MODEL_<ROLE>` | `registry.from_env` | per-role model name (wins over `DEEPSEEK_MODEL`) |
| `DEEPSEEK_MODEL` | `registry.from_env` | default model for any deepseek role (default `deepseek-v4-flash`) |
| `CRUCIBLE_API_KEY_<ROLE>` | `registry.from_env` | per-role key (else `DEEPSEEK_API_KEY`) |
| `CRUCIBLE_OLLAMA_BASE_URL` / `CRUCIBLE_DEEPSEEK_BASE_URL` | `registry.from_env` | base URL for all roles on that provider |
| `--checkpoint-db` | `cli run` | `SqliteSaver` path (execution state) |
| `--store-url` | `cli status` | SQLAlchemy URL for `findings.sqlite` (domain state) |
| `--workspace` | `cli run` | agent-writable working tree (default `.crucible-workspace`) |
| `CRUCIBLE_MAX_CYCLES` | `hooks` | producer–consumer loop bound (§11, default 2) |
| `CRUCIBLE_GAPFILL_MAX_REQUEUE` / `CRUCIBLE_GAPFILL_CELL_RETRY` | `gapfill` | cells re-queued per pass (8) / max passes before a weak cell is left alone (2) |
| `CRUCIBLE_FEEDBACK_MAX_REWRITES` | `feedback` | queued prompts rewritten per pass (6) |

Constants worth knowing: `hooks.MAX_CONTINUATIONS = 3`,
`hooks.MAX_CYCLES = 2`, `hooks.OFFLOAD_TOKEN_THRESHOLD = 2000`,
`hooks.CONTEXT_CEILING = 0.25`, `classify.MAX_TRANSIENT_RETRIES = 3`,
`recon.RECON_SUBAGENTS = 3`, `dedup.DEDUP_MAX_PAIRS = 40`,
`dedup.DEDUP_MAX_JUDGE = 12`.

---

## 17. Cross-cutting concerns

- **Provenance** — every finding records the Hunter model, prompt version, and
  sampling params; every validation records its model, prompt version, and
  response class. Nothing is actionable without it.
- **Metrics** (`specs.md` §12) — recall per attack class vs `bugs.yaml`; FP rate
  on `fixture-clean` (target 0); validation rejection rate (→ ~11%); high-integrity
  share (→ ~58%); fork rate per model; tool invocation counts; cost per actionable
  finding. Do **not** claim recall against real-world codebases.
- **Observability** (`specs.md` §13) — [`crucible/obs.py`](crucible/obs.py):
  - **Logging** always on. `crucible run` calls `configure_logging(workspace)` →
    console (INFO, `CRUCIBLE_LOG_LEVEL` to change) + `<workspace>/run.log`
    (DEBUG). `crucible/graph/build.py` wraps every node so entry/exit/duration
    are logged (`→ recon` / `✓ recon 34.4s` / `✗ hunt failed …`); Recon logs
    per-phase counts and timings (R0/R1/R2/R3); `_log_error` mirrors every
    `recon/errors.jsonl` line to the log.
  - **Tracing** opt-in, three modes (`setup_tracing()` returns
    `'' | 'langsmith' | 'otel' | 'both'`):
    - **LangSmith** — `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`. LangChain
      auto-traces every model / tool / middleware span to LangSmith;
      `LANGSMITH_PROJECT` defaults to `crucible`. specs §13's internal-dev path.
    - **Local OTLP** — `CRUCIBLE_OTEL=1` or `OTEL_EXPORTER_OTLP_ENDPOINT` (and
      LangSmith not configured) → an OTLP `TracerProvider` +
      **`LANGSMITH_OTEL_ONLY=true`** so spans go **only** to your collector
      (Jaeger / Tempo / SigNoz / OpenObserve / Langfuse), never LangChain's
      cloud. Needs `pip install "crucible[otel]"`; missing → warning, run
      continues.
    - **Both** — LangSmith vars *and* an OTLP endpoint → fan out to both.
    `span()` opens an OTel span only in the OTLP modes; a no-op otherwise
    (LangSmith mode traces via LangChain's own callbacks).
  - `run_id` is the LangGraph `thread_id`, so a resumed run's turns group into
    one trace/thread.
  - Per-tool counts still go to the SQLite `tool_usage` table
    (`crucible/agents/instrumentation.py`) — queryable independently of any trace
    backend.
  - A data-residency product cannot send customer-code-derived traces to a third
    party; that is why LangSmith cloud is never enabled by default.
- **Health signals** — a hunt that finishes fast and spawns no sub-hunts/gap
  tasks usually means a crashed dependency, not clean code: flag and requeue. A
  run logging as successful with zero output is the signature of an unclassified
  transient error.

---

## 18. Status summary

| Area | Status |
|---|---|
| Graph topology, checkpointer, state types | wired / done |
| Continuation gate + cap | done, tested |
| Response classification | done, tested |
| Finding schema + tautology deny-list | done, tested |
| Mechanical gates (path, schema, patch) | done; loads finding from store, verdict persisted (funnel + Feedback signal) |
| PoC-gate execution | stub (fail-closed) — needs sandbox (issue #9) |
| Dedup (shortlist + agent judge, cross-run `stable_key`) | **done (issue #20)** |
| Gapfill (re-queue under-tested `area × attack_class` cells) | **done (issue #19)** |
| Feedback (rewrite queued prompts from failures / shallow / repeated miss) | **done (issue #21)** |
| Producer–consumer loop (stages 4–8, bounded by `MAX_CYCLES`) | **done (issue #22)** |
| Model registry + hunter≠validator assertion | done, tested |
| Workspace layout + git-per-node | done |
| Tool-output offloading | done, tested |
| Tool implementations (bash, grep, sandbox_exec, fork, wishlist) | stub |
| Per-tool instrumentation | done |
| Sandbox provider | Docker backend wired + smoke-tested (create/exec/destroy, ro-mount, no-egress); escape-test suite still owed (§14.7) |
| Domain store (all tables + DAO) | done |
| Recon / Hunt bodies | wired (real agents, sandbox exec, guided-JSON emit) |
| Validate-bug / Validate-reach / Report bodies | stub |
| Prompts | 28 attack-class methodologies (full builtin taxonomy) + 2 validators + 3 recon; fixture tuning owed (#3) |
| Golden fixtures + manifests | seeded |

See [`README.md`](README.md) §"What Phase 1 'done' needs" for the acceptance
checklist (`specs.md` §14).
