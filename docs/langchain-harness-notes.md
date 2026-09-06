# LangChain agent-harness research → how it maps to our Phase 1

Sources:
- [How to Build a Custom Agent Harness](https://www.langchain.com/blog/how-to-build-a-custom-agent-harness) (LangChain blog)
- [The Anatomy of an Agent Harness](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness) (LangChain blog — specs.md §16 ref 3)
- [How Middleware Lets You Customize Your Agent Harness](https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness)
- [Custom middleware — LangChain docs](https://docs.langchain.com/oss/python/langchain/middleware/custom)
- [AgentMiddleware reference](https://reference.langchain.com/python/langchain/agents/middleware/types/AgentMiddleware)

---

## 1. LangChain's own framing

> **agent = model + harness.** The harness is "every piece of code, configuration, and
> execution logic that isn't the model itself." Its primary job: deliver the right
> context to the model at each step.

This is verbatim the same framing as specs.md §0. The spec's two "independent results"
(Cloudflare + LangChain) — the LangChain half is this body of writing.

**Benchmark evidence (Anatomy post):** on Terminal Bench 2.0 the *same* model scores
very differently across harnesses ("Opus in Claude Code scores far below Opus in other
harnesses"). Harness optimization is real and independent of the base model — the
justification for investing in the harness rather than chasing a better model.

## 2. Component taxonomy (Anatomy post)

| Component | Role |
|---|---|
| **System prompts** | foundational instructions |
| **Tools / Skills / MCPs** | capability interfaces + their descriptions |
| **Bundled infrastructure** | filesystem, sandbox, browser |
| **Orchestration logic** | subagent spawning, handoffs, model routing |
| **Hooks / middleware** | deterministic execution patterns around the loop |

### The filesystem primitive
"Arguably the most foundational harness primitive." Gives: durable cross-session state,
context offloading beyond the window, multi-agent collaboration as "a shared ledger of
work", memory files (`AGENTS.md` / `AGENTS.md`-style) for continual learning.
→ specs.md §1.1, §7. Our `crucible/workspace/` is this.

### Context-rot mitigations (three, named)
1. **Compaction** — summarize existing context to keep going.
2. **Tool-call offloading** — large tool outputs go to the filesystem, keep only
   "head and tail tokens" in context.
3. **Skills / progressive disclosure** — avoid "too many tools or MCP servers loaded
   into context on agent start"; load detail only when needed.
→ specs.md §7 (offload threshold, compaction hook, progressive disclosure of attack
classes) is a direct restatement.

### Continuation — "the Ralph Loop"
Intercept the model's exit attempt via a hook, **reinject the original prompt in a
clean context window**, read prior state back from the filesystem. Enables
multi-window coherence.
→ specs.md §8, our `continuation_gate` in `crucible/graph/hooks.py`. Note the spec adds
what the blog does not: a **hard cap** (3) as a safety control, citing the July 2026
OpenAI incident.

### Subagents
"Spawn subagents for independent subtasks, each with isolated context." Parallel
delegation over decomposed work.
→ specs.md §9.1 Recon fan-out, §9.2 sibling forking.

## 3. The modern build API: `create_agent` + middleware

The "How to Build" post says the current way to build a custom harness is **not**
hand-wiring a graph — it is `create_agent(...)` plus a **middleware** list.

```python
from langchain.agents import create_agent

agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=tools,
    system_prompt="...",
    middleware=[log_before_model, retry_model, CallCounterMiddleware()],
)
```

Middleware "bundles related logic into composable, reusable units" and hooks the loop
at: before/after model calls, before/after tool calls, agent startup/teardown.

### Hook types

**Node-style** (run sequentially at a point in the loop; return a dict merged into
agent state via graph reducers; may `jump_to` `end` / `model` / `tools` with
`can_jump_to=[...]`):

| Hook | Fires | Typical use |
|---|---|---|
| `before_agent` | once per invocation | load memory, connect resources, validate input |
| `before_model` | before each model call | trim history, strip PII, enforce a turn limit |
| `after_model` | after each model response | logging, state updates, block on content |
| `after_agent` | once, at end | cleanup, final logging |

**Wrap-style** (nest around a call; decide if the handler runs zero / one / many times):

| Hook | Scope | Use |
|---|---|---|
| `wrap_model_call` | each model invocation | retries, caching, dynamic model / tool set |
| `wrap_tool_call` | each tool execution | inject context, intercept results, gate tools |

### Execution order
`before_*` hooks first→last (list order) → `wrap_*` hooks nest (first middleware is
outermost) → `after_*` hooks last→first (reverse). Same shape as web middleware.

### Subclass / decorator forms

```python
class LoggingMiddleware(AgentMiddleware):
    state_schema = CustomState          # optional: extend AgentState
    tools = (...)                       # optional: register extra tools

    def before_model(self, state, runtime) -> dict | None: ...
    def wrap_tool_call(self, request, handler): ...

# or decorator form
@before_model(can_jump_to=["end"])
def check_message_limit(state, runtime) -> dict | None:
    if len(state["messages"]) >= 50:
        return {"messages": [AIMessage("limit reached")], "jump_to": "end"}
```

### Prebuilt middleware named across the posts
`SummarizationMiddleware`, `ContextEditingMiddleware` (context mgmt) ·
`FilesystemMiddleware`, `MemoryMiddleware` (memory/knowledge) ·
`ShellToolMiddleware`, `CodeInterpreterMiddleware` (environment) ·
`SubAgentMiddleware`, `TodoListMiddleware` (delegation/planning) ·
`ToolRetryMiddleware`, `ModelFallbackMiddleware` (resilience) ·
`PIIMiddleware`, `HumanInTheLoopMiddleware` (policy — "regardless of model behavior") ·
`PromptCachingMiddleware`, `ModelCallLimitMiddleware` (cost).

### Design stance
"Purposefully minimalistic" — middleware is exposed as a *primitive*, not an
opinionated pre-assembled stack. Success = **"task-harness fit"**: a coding agent and a
support agent need very different middleware sets.

---

## 4. What this changes for our build

specs.md §3 picks **LangGraph `StateGraph`** for orchestration and
**deepagents / LangGraph subagent primitives** for the agent loop. The blog posts point
at a cleaner split than we currently have in the scaffold:

**Two layers, explicit:**

1. **Stage machine = `StateGraph`** (keep). Recon → Hunt → Validate → Report, the
   SQLite checkpointer, fan-out over `pending_hunts`, resume-from-crash. This is
   coarse-grained and durable; middleware does not replace it. Our
   `crucible/graph/build.py` stays.

2. **Each stage's agent = `create_agent(...) + middleware`** (change). Right now
   `nodes/hunt.py` etc. are "write a raw agent loop" stubs. Instead each node builds a
   `create_agent` with a role-specific middleware stack and `.invoke()`s it.

**Our hand-rolled mechanics → middleware (mostly prebuilt):**

| Our code today | Replace with |
|---|---|
| `hooks.offload_and_compact` (stub) | `ContextEditingMiddleware` (head/tail offload) + `SummarizationMiddleware` (compaction into `coverage/<area>.md`) |
| `agents/tools.py` universal offload wrapper (`offload_if_large`) | `wrap_tool_call` middleware — or drop it, `ContextEditingMiddleware` already does head/tail |
| `agents/instrumentation.py` counters | `wrap_tool_call` + `after_model` middleware writing `ToolUsageRow` |
| `hooks.continuation_gate` + cap | custom `ContinuationMiddleware` (the Ralph Loop) with `before_agent` reading the workspace + a hard-cap `before_model` that can `jump_to="end"`; pair with `ModelCallLimitMiddleware` for the token ceiling |
| retry/classify on transient errors (§6) | `wrap_model_call` — but keep our `llm/classify.py` (classify **response text**, not exception type; the spec is stricter than `ToolRetryMiddleware`) |
| "validators cannot file findings" (§1.6) | `wrap_tool_call` that gates the tool set per role — validator middleware simply does not register a `file_finding` tool |
| sandbox exec | `CodeInterpreterMiddleware` / `ShellToolMiddleware` pointed at our `SandboxProvider` — still "do not hand-roll" per §10 |
| Recon fan-out, Hunt sibling forking | `SubAgentMiddleware` |
| structured Hunt output | keep Pydantic `Finding` + vLLM guided JSON (§3) — middleware doesn't own this |

**Keep as our own (spec is deliberately stricter than the framework default):**
- response **classification** by body text, refusal ≠ silent retry (§6, §1.10)
- continuation **hard cap** as a safety control, not cost control (§8)
- hunter-model ≠ validator-model **startup assertion** (§6) — `registry.py`
- domain store separate from checkpoints (§3) — findings outlive runs
- deterministic mechanical gates / PoC gate (§9.4) — plain Python, no agent

**Open version risk:** the blog's `create_agent(model=..., middleware=[...])` and hook
names (`before_model`, `wrap_tool_call`, `can_jump_to`) are the Sept-2026 API. specs.md
§16 closing note already warns "LangGraph and deepagents APIs move quickly — verify
signatures against current docs." Pin versions in `pyproject.toml` before relying on
these.

## 5. Suggested scaffold adjustments (not yet applied)

- add `crucible/agents/middleware/` — `continuation.py`, `instrumentation.py`,
  `tool_gate.py`, `classify_retry.py`; thin wrappers over prebuilt where one exists.
- `nodes/hunt.py`, `nodes/recon.py`, `nodes/validate_*.py` build a `create_agent`
  with `llm.registry` picking the model and a role-specific middleware list, instead
  of describing a raw loop.
- delete `offload_if_large` from `agents/tools.py` once `ContextEditingMiddleware` is
  wired (keep `instrumentation.ToolUsage`, move its calls into a `wrap_tool_call`).
- `crucible/graph/hooks.py` keeps only what is genuinely graph-level (the stage-machine
  continuation edge if still wanted); per-agent continuation moves to middleware.
