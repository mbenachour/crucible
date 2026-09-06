# Build Spec v0.3: Vulnerability Discovery Harness

**Supersedes:** v0.2 (`harness-build-spec-v2.md`)
**Audience:** coding agent / implementing engineer

**What changed from v0.2:** v0.2 adopted LangChain's harness structure but under-used Cloudflare's operational detail. v0.3 adds a Phase 0 that builds the skill before the harness, splits bug-detection from reachability into separate model calls, reframes the Hunt stage as deliberately over-reporting, adds language-aware noise budgets, promotes the wishlist and tool-usage instrumentation to first-class components, and adds a held-out repo for prompt regression testing.

---

## 0. Framing

**Agent = Model + Harness.** Everything that isn't the model is the harness. Two independent results justify investing there:

- Cloudflare treats models as interchangeable and the orchestration layer as the durable asset. Their funnel improvements (validation rejection 40% → 11%, high-integrity findings 35% → 58%) came from context and gating discipline, not a better model.
- LangChain reports the same model scoring very differently across harnesses on Terminal Bench 2.0, and moved their coding agent from Top 30 to Top 5 by changing only the harness.

**Two capabilities define the frontier gap.** Cloudflare attributes Mythos Preview's edge specifically to *exploit chain construction* (combining several small primitives into a working exploit) and *proof generation* (writing, compiling, running code that proves the bug, then adjusting on failure). Other frontier models found similar underlying bugs but stalled at stitching the chain together. Expect open-weight models to trail here most; benchmark it (§12) rather than assuming.

**The overfitting risk.** Frontier coding agents are post-trained with their harnesses in the loop, and models can overfit to the tooling they trained against — LangChain cites tool-logic changes degrading performance. Open-weight models have less agentic post-training. Their own conclusion cuts our way: the best harness for a task is not necessarily the one the model was post-trained with.

---

## 1. Operating principles

1. **The filesystem is the state surface.** Agents read and write files; the harness reads files. Large artifacts never travel through model context or the database.
2. **LLM is stateless compute.** Durable state = LangGraph checkpoints + workspace filesystem + git.
3. **Persistence before parallelism.** Checkpoint and resume must be proven before any fan-out.
4. **Context ceiling of 25%**, held by scope narrowing, compaction, and tool-output offloading (§7).
5. **Hunters over-report by design; the funnel filters.** Do not tune Hunt for precision — tune it for coverage and let the gates do the work (§9.2).
6. **Validators cannot file findings.** Only `upheld` / `refuted` + reasoning.
7. **Split the questions.** "Is this code buggy?" and "can an attacker reach it?" are separate calls, to separate agents. Each is narrower than the combined version and the model is better at each alone (§9.4).
8. **Deterministic code does deterministic work.** Path checks, schema conformance, patch/test parsing, dedup pre-filtering, report rendering — plain Python.
9. **No PoC against the unmodified codebase, no finding.**
10. **Classify response text, not exception types.** Transient errors arrive inside `200 OK`; an unclassified response is a failed task, not a clean one.
11. **Bounded continuation.** Early-stopping agents get restarted, with a hard cap (§8).
12. **Instrument what agents actually reach for.** Cloudflare plumbed a static analyzer through the whole system and their Hunters invoked it zero times in a month, while the wishlist became the single most-used tool. Measure tool usage and delete what goes unused.

---

## 2. Phasing — build the skill first

Cloudflare started with a ~450-line `security-audit` skill run on a single repository, adjusted the prompts until real bugs surfaced, and only then added the orchestration that became the harness. The real value lives in the prompts; their production prompts still carry the original skill's attacker scenarios, bug classes, and anti-pattern detections nearly unchanged.

**Phase 0 — the skill (do this before writing any LangGraph).**
A single-session, 7-phase audit runnable in a dev environment against one repo:
1. Parallel recon agents write `architecture.md`
2. One Hunter per attack class, trying to break the code rather than review it
3. Adversarial validators try to disprove each finding
4. Survivors written as a human-readable report
5. Emitted as `findings.json` against a schema
6. Mechanical check validates the file
7. A fresh agent independently re-verifies every finding against source

Ship this, tune prompts against fixtures, and only move on when the bottleneck is real. The signal that you've outgrown it: a single run finds only about half the bugs you'd catch across multiple runs, and your process has become "run it ten times and diff by hand."

**Phase 1 — minimal harness.** Recon → Hunt → Validate in a database, with a separate validator that can't file its own findings. Everything in this spec's §9.

**Phase 2 — coverage and noise.** Gapfill, Dedup, Feedback. Add Dedup only when actively drowning in duplicates; add Gapfill when coverage plateaus.

**Phase 3 — reachability at scale.** Cross-repo Trace. Skip entirely until there is more than one repo that matters.

**Phase 4 — triage and fixing.** VVS layer, human-gated Fixer.

Use the harness's own models to help build the harness — Cloudflare used Mythos Preview to build, tailor, and improve their harnesses.

---

## 3. Stack

| Concern | Choice | Why |
|---|---|---|
| Orchestration | **LangGraph** (`StateGraph`) | Durable checkpointing, conditional edges, fan-out, resume. Replaces hand-rolled orchestrator/queue/worker pool. |
| Checkpointer | `SqliteSaver` → `PostgresSaver` | Resume-from-crash is a library feature. |
| Agent loop / subagents | **deepagents** or LangGraph subagent primitives | Planning tool, filesystem tools, isolated-context subagents. |
| Model routing | **LiteLLM** | Provider-agnostic; points at self-hosted vLLM. |
| Structured output | Pydantic + vLLM guided JSON | Constrain at generation, don't repair after. |
| Sandbox | Managed (E2B/Modal) or self-hosted (AerolVM) | Never hand-roll. §10. |
| Code intelligence | `tree-sitter` (P1), Joern CPG (P3 Trace) | Symbol map for scoping; data-flow for reachability. |
| Findings store | SQLite via SQLAlchemy | Domain data, separate from checkpoints. |
| Observability | OTel → local (default), LangSmith (internal dev only) | §13. |

**Two stores, deliberately.** LangGraph checkpoints hold *execution* state. Our SQLite holds *domain* state — findings, validations, provenance, wishlist, tool-usage counters. Findings must outlive and be queryable independently of any run.

---

## 4. Repository layout

```
harness/
  graph/
    build.py                    # StateGraph assembly, edges, checkpointer
    state.py                    # TypedDict graph state (§5)
    nodes/
      recon.py
      hunt.py
      validate_mechanical.py
      validate_bug.py           # "is this real?"
      validate_reachability.py  # "can an attacker get here?"
      report.py
    hooks.py                    # compaction, offloading, continuation
  workspace/
    fs.py, layout.py            # workspace + git operations
  agents/
    tools.py                    # bash, read/grep, sandbox exec, fork, wishlist
    instrumentation.py          # per-tool invocation counters (§1.12)
  llm/
    registry.py                 # role → endpoint; enforces hunter != validator
    classify.py                 # response classification
  validation/
    mechanical.py               # deterministic gates
    schema.py                   # finding schema, field order, tautology deny-list
  store/
    models.py, dao.py
  skills/
    attack_classes/*.md         # front-matter + methodology body (§7)
    recon/, validate/
  cli.py
tests/
  fixtures/repos/
    fixture-c/, fixture-py/, fixture-clean/, fixture-holdout/
```

**Prompts are files with `version:` front-matter**, recorded on every finding. They are the highest-value asset — diffable, versioned, hot-swappable.

---

## 5. Graph state

Pointers and counters only. Content lives on disk.

```python
class HarnessState(TypedDict):
    run_id: str
    repo_path: str            # read-only checkout
    workspace_path: str       # agent-writable, git-initialized
    repo_commit: str
    primary_language: str     # drives noise budget (§12)

    architecture_path: str
    taxonomy_path: str

    pending_hunts: list[HuntTask]     # attack_class + scope_hint only
    completed_cells: list[str]        # "area::attack_class"

    finding_ids: list[str]
    fork_count: int
    continuation_count: int
    token_spend: int
```

**Anti-pattern:** putting `architecture.md` contents, file bodies, or finding descriptions in graph state. LangGraph checkpoints every superstep; fat state makes checkpointing expensive and pushes content back into context.

---

## 6. Model roles

**Hunter and validator must be structurally different open-weight models** (different training lineage). This is the primary noise-control mechanism, not a config preference — forcing a second model with different weights and training data to judge the first gives you an adversarial third party rather than a model grading its own homework.

```python
class ModelRole(str, Enum):
    RECON = "recon"; HUNTER = "hunter"
    VALIDATOR_BUG = "validator_bug"; VALIDATOR_REACH = "validator_reach"
```

- Startup assertion: `HUNTER.model != VALIDATOR_BUG.model`. Fail loudly if equal.
- Treat providers as interchangeable commodities. They change temperature, caching, and inference-effort budgets over time, even within one model version — build to absorb that volatility rather than depending on stable behavior.
- Per-role sampling params recorded on every finding.

**Response classification** before parsing, every call:

| Class | Trigger | Handling |
|---|---|---|
| `ok` | Parses, non-empty, conformant | Proceed |
| `transient_error` | Error text in body, empty, truncated | Retry with backoff, capped |
| `refusal` | Model declines | Log, fail task. **No silent retry-with-rephrasing.** |
| `malformed` | Unparseable despite constraints | One repair attempt, then fail |

Track `refusal` rate as a product metric. Both Cloudflare and Hugging Face documented frontier models inconsistently refusing legitimate security research — same request reframed producing opposite outcomes. Low refusal on open weights is a stated differentiator; measure it.

---

## 7. Context and workspace

**Workspace** (git-initialized, separate from the read-only source checkout):

```
workspace/
  architecture.md          # Recon output; every Hunter reads this
  taxonomy.json            # attack classes for this repo
  coverage/<area>.md       # what was examined, by whom, what was found
  findings/<id>.json
  scratch/<task_id>/       # per-task PoC compile/run space
  offload/<call_id>.txt    # full tool outputs
```

Git-commit after each node: agents get a diff of what changed since they last looked, and you get a free audit trail. The filesystem is the collaboration surface between agents, not just storage.

**Tool-output offloading.** Compiler stderr, test output, and grep results are the context killers in this domain. Output above a threshold (start 2,000 tokens) goes to `offload/<call_id>.txt`; the model gets head + tail + path and can read the full file if needed. Implement as a wrapper applied to every tool, not per-tool.

**Compaction.** Approaching the ceiling, summarize prior turns into `coverage/<area>.md` and continue in a fresh window. A LangGraph hook, not agent logic.

**Progressive disclosure of attack classes.** Load only front-matter (name, one-line description) for every attack-class skill at Hunt start; load the full methodology body only for the scoped class. Loading the whole taxonomy upfront degrades performance before work begins.

---

## 8. Continuation, bounded

Intercept a Hunter's exit and evaluate a completion goal: did it examine every entry point in scope, and either produce a finding with a PoC or record an explicit negative in `coverage/`? If not, reinject the original prompt in a **fresh context window** — state carries via the workspace, not the window.

**Hard caps, non-negotiable:** 3 continuations per task, plus per-task wall-clock and token ceilings. Unbounded persistence — an agent refusing to give up on an unsolvable problem — was a named root cause of the July 2026 OpenAI incident. This mechanism is deliberately the same shape as that failure; the cap is a safety control, not a cost control.

**Shallow detection.** A Hunt node finishing suspiciously fast with zero findings *and* zero forks is marked shallow and re-queued once — this usually indicates a crashed dependency rather than clean code.

---

## 9. Stage specifications

### 9.1 Recon

Fan out N subagents (default 3) over subsystem slices. Deterministic merger writes `architecture.md` (build commands, entry points, trust boundaries, external inputs, likely attack surface) and `taxonomy.json`.

**Recon writes its own threat model** rather than receiving one. Beyond ~10 built-in attack classes (injection variants, memory corruption, protocol parsing, timing side channels), Recon may invent repo-specific classes on the spot, each with its own methodology — a custom taxonomy tailored to that codebase, used to tightly scope the Hunters.

Then deterministically seed the Hunt queue as `(area × attack_class)`, bounded by the run task cap.

**Recon quality drives everything downstream.** Cloudflare's validation rejection rate dropped 40% → 11% largely from better context injection at this stage. Treat Recon prompt regressions as critical.

### 9.2 Hunt

One task = one attack class + one scope hint + `architecture.md` + prior coverage. **Never** "find vulnerabilities in this repo." Narrow scoping is what makes the model behave like a researcher rather than wander.

**Tune Hunters to deliberately over-report.** Cloudflare tunes theirs to over-report subtle primitives that could chain into larger attacks, accepting more noise so they see more and miss less. The measure of success is not Hunt precision — it's how sharply the funnel refines raw output before it reaches a human.

**Move past reading into execution.** Reading source is insufficient for subtle undefined-behavior bugs. Hunters compile fragments, build small versions, and attack them. Cloudflare's biggest single quality jump came from giving Hunters a sandbox to crash binaries in.

**Tools:** bash (general purpose — the model designs its own approach; a fixed toolset is the wrong shape here), scoped read/grep, sandbox exec, `fork_sibling`, `wishlist_write`. Instrument every tool's invocation count (§1.12).

**Sibling forking.** A Hunter tripping over an interesting path outside scope forks a sibling with a precise structural seed rather than wandering off. Expect this to be **highly model-dependent** — Cloudflare saw roughly 9% of fleet-wide tasks come from forks, ranging from near-zero to about a fifth depending on which model was hunting. **Track fork rate per model as an open-weight selection metric:** a model with near-zero fork rate is exploring less, and that shows up as coverage loss, not as an obvious failure.

**Output schema — field order is load-bearing.** `threat_model` first, so the model commits to an attacker and a boundary before describing a bug:

```json
{
  "threat_model": {
    "attacker": "unauthenticated remote client",
    "boundary_crossed": "network input → parser memory",
    "assumption_broken": "length field validated before use"
  },
  "title": "...",
  "file_path": "src/parse.c",
  "line_start": 142, "line_end": 158,
  "description": "...",
  "poc_test": "<test source>",
  "proposed_patch": "<unified diff>",
  "severity": "high"
}
```

**Tautology deny-list** (parse-time, no model call): reject where `threat_model.attacker` implies privilege equivalent to the claimed impact — the "user with DB write access can write to the DB" class.

**The three failure modes to design against.** Left unchecked a Hunter will: edit the source so its own exploit works and then report the bug it created; write a tautological test that proves nothing ("exec() executes things, therefore critical"); or build an exploit that runs fine but proves nothing because the threat model behind it is nonsense. The threat-model requirement kills the third, the deny-list the second, the PoC gate the first.

### 9.3 The wishlist

When an agent needs something it doesn't have — a build environment, a VM, prod config, a credential to confirm a PoC — it writes a structured request with enough context for the system to re-run that exact task once a human provides the dependency.

**Expect this to be heavily used.** Cloudflare's wishlist was written to 25,472 times across 128 repos and became the main way agents talk back to the team — the single most-used tool in the system, while a plumbed-in static analyzer went unused for a month. Design the UX accordingly: this is a primary interface, not a log.

**Partial self-healing.** Some wishes resolve without a human — if a container needs rebuilding with changes, a generic coding harness monitoring the logs can do it autonomously after the run, then re-queue the task.

### 9.4 Validate — three passes

**Pass A: mechanical (no model calls).**
- Cited path exists at `repo_commit`; line range in bounds
- Schema conformant, `threat_model` populated
- Patch applies cleanly to the unmodified tree (dry run, revert)
- `poc_test` parses
- **PoC gate:** test fails on the unmodified repo (demonstrating the bug) and passes with the patch applied. Any source modification outside the patch invalidates the finding

Failure → `mechanical_failed`. No model call spent. Cheapest filter first.

**Pass B: is it real?** (`VALIDATOR_BUG`, different model from Hunter.) Re-reads the code with a different prompt and tries to **disprove** the finding. No finding-creation tool, no write access to the findings table.

**Pass C: is it reachable?** (`VALIDATOR_REACH`.) Separate call, separate agent: can attacker-controlled input actually reach this code from outside the system? Splitting this from Pass B is deliberate — each question is narrower than the combined version and the model is better at each one asked alone. This is also the stage that matters most, converting "there is a flaw" into "there is a reachable vulnerability."

In Phase 1, Pass C is single-repo. In Phase 3 it becomes the cross-repo Tracer requiring a unified symbol index and dependency graph.

### 9.5 Report

Deterministic script against a fixed schema. **No model required.** Output is queryable data, not free-form prose.

---

## 10. Sandbox

Hunters compile and execute untrusted, model-generated code. **Do not write this layer.** Use a provider adapter so the backend is swappable:

```python
class SandboxProvider(Protocol):
    def create(self, task_id: str, repo_mount: str) -> Sandbox: ...
    def exec(self, cmd: str, timeout_s: int) -> ExecResult: ...
    def destroy(self) -> None: ...
```

**Backend choice.** Firecracker microVMs give each sandbox its own kernel, so escape requires a hypervisor exploit rather than a syscall-implementation bug — stronger than gVisor's user-space kernel, which still shares the host. But a 2026 comparative study found Firecracker's first two escape-class CVEs that year (out-of-bounds write in virtio-pci at 8.7; jailer symlink host-write at 6.0), and one self-hosted product pinned a Firecracker build for 399 days, leaving a CVE unpatched at orchestrator defaults for 44+ days. **Own the patch cadence — pin policy is a security decision.**

Self-hosting supports the data-residency claim. AerolVM (MIT, Go single binary, SQLite-backed) runs on your own Linux host with per-sandbox filesystem/network/vCPU isolation across Docker, gVisor, and Firecracker runtimes.

**Policy regardless of backend:**
- **No network egress by default.** The single most important control — the exact boundary whose failure started the July 2026 incident chain. Any egress attempt is a run-level alert.
- No host credentials, no cloud instance metadata (`169.254.169.254`), no access to the harness's own DB or config.
- Hard ceilings: CPU seconds, memory, wall clock, PIDs, file size, disk quota.
- Read-only source mount; writes only to `scratch/<task_id>/`, destroyed on completion.

**Nested-containerization trap:** if the harness runs inside Docker and the sandbox uses namespace isolation, it may need `seccomp=unconfined` and `apparmor=unconfined` or it fails silently at startup. Detect at boot and fail loudly with a clear message rather than degrading.

**Acceptance:** an escape-test suite (network calls, metadata reads, writes outside scratch, fork bombs, oversized allocations) is fully contained, every attempt logged.

---

## 11. Phase 2 components (design now, build later)

**Gapfill.** Re-queues under-tested `(area × attack_class)` cells. Primary cost-to-coverage lever — each additional pass costs roughly half the initial hunt, so it's the cheapest coverage you can buy. Also counteracts the model's tendency to drift toward attack classes where it has already had success.

**Dedup.** Comparing every finding against every other with an LLM is O(N²) and falls apart at scale. Deterministic code builds inverted indexes over structured data (touched files/functions, trust boundary, rare tokens) to produce a short candidate list; only then does an agent judge whether one fix would close several. Stable cross-run keys reopen existing records rather than spawning new ones. String matching and file-path checks are not sufficient — determining whether two complex logic flaws share a root cause requires real reasoning, which is why it needs its own agents.

**Feedback.** Takes validation failures, shallow runs, and repeated misses and rewrites queued prompts. Worth noting: LangChain lists "agents that analyze their own traces to identify and fix harness-level failure modes" as an *open research problem* — Cloudflare already ships it. Treat this as differentiated territory, not a commodity feature.

Stages 4–8 run as a continuous producer-consumer loop: Gapfill, Feedback, and Trace generate new tasks while Dedup folds overlapping findings back together, so a vulnerability found late in a cycle is still validated, reported, and checked against other code within the same run.

---

## 12. Testing and measurement

**Golden fixtures** — build before writing the first Hunter prompt, or prompt changes are unmeasurable.

- `fixture-c/` — memory-safety bugs (out-of-bounds read, off-by-one length check)
- `fixture-py/` — injection classes (command injection, unsafe deserialization, template injection)
- `fixture-clean/` — structurally similar, **no planted bugs**; any upheld finding is a false positive
- `fixture-holdout/` — never used for prompt tuning. Prompt updates are tested here to confirm coverage actually moved rather than overfitting to the tuning set

**Language-aware noise budget.** Two factors dominate the false-positive rate: language and model bias. C and C++ give direct memory control and bug classes that memory-safe languages eliminate at compile time, producing consistently more false positives. And models don't report calibrated confidence — ask a model to find bugs and it will find them whether or not the code has any, with hedged findings ("possibly," "could in theory") vastly outnumbering solid ones. Set per-language FP expectations in metrics; don't compare a C fixture's FP rate to a Python one.

**Metrics:**
- Recall per attack class against `bugs.yaml` manifests
- FP rate on `fixture-clean` (target 0)
- Validation rejection rate, trending toward ~11%
- Share of high-integrity findings, trending toward ~58%
- PoC reproducibility (100% by gate definition)
- **Fork rate per model** (§9.2) — coverage proxy for model selection
- **Tool invocation counts** — delete unused tools
- Cost per actionable finding, per repo

**Do not claim recall against real-world codebases.** There's no labeled set of every real bug in a codebase, so any claimed recall number there is speculative. Track instead whether re-runs keep surfacing new bugs and whether coverage cells keep growing — proxies, but honest ones.

**Coverage measurement:** divide the repo into `(area × attack_class)` cells and run Gapfill iteratively until it stops producing findings. When a prompt changes, check whether the total coverage-cell count actually moves on the held-out repo.

**Open-vs-frontier benchmark.** Same harness, frontier model in the Hunter role, same fixtures, results segmented by bug class. Expect the gap to be widest on multi-bug exploit chaining. This determines what you can honestly claim commercially.

**Unit tests:** checkpoint/resume, response classification (including error-text-in-`200`), offloading threshold behavior, continuation cap enforcement, mechanical gate rejections, sandbox escape containment, dedup pre-filter correctness.

---

## 13. Observability and health signals

Every node emits: role, model, prompt version, token counts, latency, classification, cost, tool invocations.

**Automated health signals** catch system failure early. A hunt that finishes suspiciously fast and fails to spawn sub-hunts or gap tasks usually means a crashed dependency, not a clean codebase — flag and requeue. Watch for runs logging as successful with zero output, which is the signature of unclassified transient errors (§1.10).

**Self-hosting default.** For a product whose pitch includes data residency, sending customer-code-derived traces to a hosted observability service undercuts the claim. Default to OpenTelemetry → local storage; make LangSmith opt-in for internal development only.

---

## 14. Definition of done — Phase 1

1. `harness run --repo tests/fixtures/repos/fixture-py` completes end to end and emits a report.
2. Killing the process mid-run and resuming loses no completed work.
3. Hunter and bug-validator run on different open-weight models; startup fails if identical.
4. Bug-detection and reachability are separate model calls.
5. Every upheld finding carries a populated threat model, a PoC that fails clean and passes patched, and full provenance (model + prompt version).
6. `fixture-clean` yields zero upheld findings.
7. Sandbox escape-test suite passes fully.
8. Tool outputs above threshold are offloaded to disk, not context — verified by a token-accounting test.
9. Continuation cap enforced and observable.
10. Fork rate and per-tool invocation counts are reported by `harness status`.
11. Prompt changes are regression-tested against `fixture-holdout`.

---

## 15. Out of scope for Phase 1

Gapfill, Dedup, Feedback, cross-repo Trace, VVS triage, automated fixing, multi-tenancy, per-PR CI. Add each only when its absence is the specific blocker. Note also that full scans are periodic backlog sweeps, not per-PR checks — a complex repo can take hours (Cloudflare's worst run exceeded 14). Per-PR needs a separate, cheaper, smaller harness.

---

## 16. References

1. Bourzikas, G. "Project Glasswing: what Mythos showed us." Cloudflare Blog, May 18, 2026. `https://blog.cloudflare.com/cyber-frontier-models/` — Mythos capabilities, refusal inconsistency, signal-to-noise factors, harness lessons, stage table.
2. Jones, D., Godoi, A., Bourzikas, G. "Build your own vulnerability harness." Cloudflare Blog, June 18, 2026. `https://blog.cloudflare.com/build-your-own-vulnerability-harness/` — skill→harness path, VDH/VVS, dedup at scale, wishlist, fork rates, funnel numbers, cost/time economics.
3. Trivedy, V. "The Anatomy of an Agent Harness." LangChain Blog, March 10, 2026. `https://www.langchain.com/blog/the-anatomy-of-an-agent-harness` — component taxonomy, filesystem primitive, context-rot mitigations, continuation pattern, progressive disclosure, harness-vs-model benchmark evidence. Vendor post promoting LangGraph/deepagents/LangSmith.
4. Cloudflare seed skill: `github.com/cloudflare/security-audit-skill`
5. "AI Code Sandboxes: A Comparative Security Study, Part 1." arXiv, 2026 — isolation models, CVE history, patch cadence.

_Cloudflare's and LangChain's figures are point-in-time snapshots of evolving work. LangGraph and deepagents APIs move quickly — verify signatures against current docs._