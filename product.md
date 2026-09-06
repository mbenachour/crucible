# Product thesis — what this harness is for

[`architecture.md`](architecture.md) describes the code. This document ties every
piece of it back to the product we are trying to build, and to the two Cloudflare
articles that argument rests on.

**Sources**

1. Bourzikas, G. *"Project Glasswing: what Mythos showed us."* Cloudflare Blog,
   May 18 2026 — `blog.cloudflare.com/cyber-frontier-models/`. What the frontier
   can and cannot do, refusal inconsistency, the signal-to-noise factors, and the
   claim that the orchestration layer — not the model — is the durable asset.
2. Jones, D., Godoi, A., Bourzikas, G. *"Build your own vulnerability harness."*
   Cloudflare Blog, June 18 2026 —
   `blog.cloudflare.com/build-your-own-vulnerability-harness/`. The funnel
   numbers, the wishlist, dedup at scale, the per-repo economics, the VDH/VVS
   split, backlog-sweep vs per-PR.

---

## 1. The product in one sentence

A **model-agnostic, self-hostable vulnerability-discovery pipeline** that turns a
codebase into a ranked list of **reachable** bugs, each with a **working PoC and a
proposed patch**, and does the triage that a security team would otherwise do by
hand.

We are not selling a bug finder. Per article 2: *"raw candidate findings are cheap
now, and the only work worth doing is turning them into sound, verifiable code
fixes."* The product is the **funnel and the proof**, not the model call at the
top of it.

---

## 2. Why the harness — not the model — is the product

Both articles converge on one point, from different directions:

- **Article 1:** Cloudflare treats models as interchangeable and the orchestration
  layer as the durable asset. Their funnel gains (validation rejection 40% → 11%,
  high-integrity findings 35% → 58%) came from **context and gating discipline,
  not a better model**.
- **Article 2:** *"The harness is the bit that lasts. If you build your own
  system, design it to be model-agnostic from day one."*

So the defensible engineering — the part a competitor cannot get by swapping in
next quarter's model — is: Recon context quality, the deterministic gates, the
two-model adversarial validation, dedup at scale, the coverage loop, and the
provenance trail. That is exactly what [`architecture.md`](architecture.md) §3–§11
build.

**Our added wedge (spec §0):** open-weight models have *less* agentic
post-training, so they overfit less to a harness they were not trained against —
and low, consistent refusal is a measurable differentiator (article 1 documents
frontier models inconsistently refusing legitimate security research; the same
request reframed produces the opposite outcome). `llm/classify.py` tracks refusal
rate as a first-class metric for precisely this reason.

---

## 3. The thesis as four claims — and what backs each

| Claim we want to make | Component that earns it | Article evidence |
|---|---|---|
| **"Every finding is real and reachable, or we don't ship it."** | Validate A (deterministic gates + PoC gate), Validate B (`VALIDATOR_BUG`, disprove-oriented, cannot file), Validate C (reachability as a separate call) | Art. 2: 20,799 raw → 12,057 past discovery-validation (58%); rejection 40%→11% from better Recon context |
| **"No vendor lock-in. Runs on your infrastructure."** | `llm/registry.py` (LiteLLM → self-hosted vLLM, `HUNTER ≠ VALIDATOR_BUG` assertion), OTel→local default, self-hosted sandbox option | Art. 2: *"developers who've built tightly around a single model have already experienced what happens when that model is no longer available"*; Art. 1: models interchangeable |
| **"We do the triage, not just the detection."** | Dedup (Phase 2), VVS judgment + Fixer (Phase 4), the status funnel in `findings.sqlite`, `report.py` as queryable data | Art. 2: 5,442 of 13,841 collapsed as duplicates; Fixer ~5 min/bug; *"eliminate the manual triage bottleneck"* |
| **"Auditable end to end."** | git-commit-per-node (`workspace/fs.py`), provenance columns on every finding and validation, the wishlist as the visible "what we couldn't do" record | Art. 2: change-management compliance, cryptographic audit trails; wishlist written 25,472× across 128 repos |

---

## 4. Component → product function

Read this as: *why each thing in `architecture.md` exists commercially.*

### Recon (§4.1) — **the quality lever**
Article 1 attributes most of the 40%→11% rejection-rate drop to better context
injection here. Product consequence: Recon prompt quality is the single biggest
input to "how much noise reaches the customer." We treat Recon regressions as
release blockers and gate them on `fixture-holdout`.

### Hunt (§4.2) — **deliberately the noisy part**
Tuned for coverage, not precision (spec §1.5). This is a *product* decision: the
customer never sees Hunt output. What they see is what survives §4.3–§4.6. Fork
rate (article 2: ~9% fleet-wide, near-zero to ~20% by model) is an **open-weight
selection metric** — a model that never forks is exploring less, which shows up as
coverage loss, not an obvious failure. `crucible status` surfaces it.

### The three validate passes (§4.3–§4.6) — **the funnel = the product**
Splitting "is it real?" from "is it reachable?" (spec §1.7) is what converts *"there
is a flaw"* into *"there is a reachable vulnerability"* — the only kind worth a
customer's engineering time. `VALIDATOR_BUG` on a different model lineage is the
adversarial third party from article 1's reasoning; it **cannot file findings**,
only uphold or refute.

### The PoC gate (`validation/mechanical.py`, §5.2) — **the proof**
No PoC that fails clean and passes patched → no finding. Article 2 ships a working
PoC test *and* a proposed patch with every delivered finding. This is the feature
that makes output actionable instead of a scanner report. It is currently
fail-closed (needs the sandbox) — **nothing is deliverable until this is real.**

### `llm/registry.py` (§6.1) — **the anti-lock-in guarantee, enforced**
The `HUNTER.model != VALIDATOR_BUG.model` startup assertion is not a config
nicety; it is the mechanism behind the "different logical weights judge the
finding" claim. Provider volatility (temperature, caching, effort budgets change
under one version) is assumed, not fought.

### `llm/classify.py` (§6.2) — **the refusal differentiator, measured**
Article 1: frontier models inconsistently refuse legitimate security research.
Classifying response *text* (not exception type), never silently retrying a
refusal, and tracking refusal rate as a product metric is how we substantiate
"low, predictable refusal on open weights."

### `workspace/` + git-per-node (§7) — **the audit trail, for free**
Every node's changes are a diff. Article 2 calls out change-management compliance
and cryptographic audit trails as enterprise requirements. We get the diffable
history as a side effect of using the filesystem as the state surface.

### The wishlist (`store`, §10.3) — **a primary interface, not a log**
Article 2: written 25,472 times across 128 repos; the main way agents talk back to
the team, while a plumbed-in static analyzer went unused for a month. Product
consequence: the wishlist gets real UX (a review/resolve/re-queue flow), and
"tasks blocked on a missing dependency" is a dashboard number, not a grep.

### `store/` two databases (§10) — **findings outlive runs**
Domain state is separate from execution state so a finding is queryable,
re-openable (via `stable_key`), and reportable independently of the run that
produced it. This is what lets "deliver incrementally over 15–20 days" (article 2)
work.

### Sandbox (§9) — **the data-residency story, and the safety boundary**
Self-hostable (AerolVM) so customer code never leaves their infrastructure —
directly supports the residency pitch. *No network egress by default* is the exact
control whose failure started the July 2026 incident chain; any egress attempt is
a run-level alert.

### Instrumentation (`agents/instrumentation.py`, §8.2) — **delete what goes unused**
Article 1's static-analyzer-used-zero-times lesson, operationalised. Per-tool
counts tell us which capabilities to invest in and which to cut.

---

## 5. The funnel is the product — the numbers to reproduce

From article 2, the shape we are aiming our metrics at:

```
20,799  raw candidates (Hunt output — cheap, noisy, never shown)
12,057  survive discovery-validation           58%   ← our Validate A+B+C
 7,245  actionable after dedup                        ← our Phase 2 Dedup
   ~80  distinct bugs for a ~30k-LOC repo
    10  critical → production in ~5 days
        rest rolled out over 15–20 days
```

- Validation rejection rate: **40% → 11%** (target trend; driven by Recon).
- High-integrity share: **35% → 58%** (target trend).
- Dedup collapse: **5,442 of 13,841** in the pool were duplicates.
- Per-repo economics: ~100 findings and 3–4 h discovery for ~30k LOC; compression
  ~3 h; Fixer ~5 min/bug; full discovery-to-PR ~14 h. Gapfill costs ~half the
  initial hunt.
- Fleet: **50–200 workers, strict task cap per repository** — deliberately cap
  spend on low-signal codebases.

`report.py` and the §17 metrics exist to produce our version of this table per
run, per repo. **Cost per actionable finding** is the headline commercial number.

---

## 6. What is defensible

1. **Recon context engineering** — the quality lever, and the hardest thing to
   copy because it is prompt + taxonomy + coverage-model, not code.
2. **Two-model adversarial validation** with an enforced lineage split.
3. **Dedup at scale** — article 2 needed dedicated reasoning agents over a
   deterministic shortlist; string/path matching does not work. LangChain lists
   trace self-analysis (our Feedback stage) as an *open research problem*;
   Cloudflare already ships it. This is differentiated territory.
4. **The coverage loop** — Gapfill + Feedback turning a linear scan into a
   producer–consumer loop, so a bug found late is still validated and deduped in
   the same run.
5. **Provenance + audit trail** as a built-in, not a bolt-on.
6. **Open-weight + self-host** — residency, no lock-in, measured low refusal.

The model at the top is *not* on this list, on purpose.

---

## 7. Positioning

### Backlog sweep, not per-PR
Article 2 is explicit: *"the big scans are a periodic backlog sweep and not a
per-PR check… the worst run took just over 14 hours. Cheaper, smaller harnesses
are the right tool for that job."* We sell the deep harness as a **periodic
fleet-wide sweep**. Per-PR is a different, cheaper product built later, if at all
(spec §15).

### Managed vs self-hosted
The architecture supports both (`llm/registry` → any vLLM, sandbox provider
adapter, OTel→local). The **self-hosted tier is the data-residency product**;
sending customer-code-derived traces to a hosted observability service would
undercut it (spec §13), so LangSmith is internal-dev-only.

### Who it is for
Security teams at organisations with a large code fleet and a **triage
bottleneck** — the KPI is *processing velocity and eliminating manual triage*,
not raw bug count (article 2).

---

## 8. KPIs — the metrics that *are* the product

| Metric | Where measured | Target / trend |
|---|---|---|
| Cost per actionable finding, per repo | `report.py` + billing | ↓ — the headline number |
| Validation rejection rate | `validations` table | → ~11% |
| High-integrity finding share | `findings.status` funnel | → ~58% |
| FP rate on `fixture-clean` | test harness | 0 |
| PoC reproducibility | PoC gate (by definition) | 100% |
| Refusal rate | `llm/classify.py` | low + stable (differentiator) |
| Fork rate per model | `crucible status` | non-zero (coverage proxy) |
| Tool invocation counts | `tool_usage` table | inform cut/keep |
| Coverage-cell growth across re-runs | Gapfill loop | keeps growing = still finding |

**Honesty constraint:** we do **not** claim recall against real-world codebases —
there is no labelled set of every real bug. We track whether re-runs keep
surfacing new bugs and whether coverage cells keep growing (article-honest
proxies).

---

## 9. Claims we cannot make

Straight from the articles and spec §12:

- No recall percentage on customer code ("we find 90% of bugs" is unsupportable).
- No "zero false positives" — only "zero on `fixture-clean`", and a
  **per-language** FP budget (C/C++ run hotter; article 1's language factor).
- Cloudflare's numbers are *point-in-time snapshots of evolving work* — cite them
  as targets/precedent, not guarantees.
- Findings are potential issues with PoCs, **not** confirmed live-exploitable
  vulnerabilities in a running system until Validate C (and later VVS) says so.
  Article 2's own disclaimer: *"these findings do not represent active, unpatched
  vulnerabilities in our live production environment."*

---

## 10. Phase → product milestone

| Phase (spec §2) | Engineering | What we can sell |
|---|---|---|
| **0** — the skill | 7-phase single-session audit, tuned on fixtures | nothing yet — internal proof the prompts find real bugs |
| **1** — minimal harness | Recon→Hunt→Validate→Report, checkpointed, two-model split, PoC gate | **design partner / pilot**: "reachable bugs with PoCs on one repo, reproducibly" |
| **2** — coverage + noise | Gapfill, Dedup, Feedback | **the actual product**: fleet sweep with the funnel numbers and cost-per-finding |
| **3** — reachability at scale | cross-repo Tracer | multi-repo orgs; "reachable across service boundaries" |
| **4** — triage + fixing | VVS judgment, human-gated Fixer | **the triage-bottleneck pitch**: discovery-to-PR, human signs off |

The pilot claim in Phase 1 is deliberately narrow. The commercial claim needs
Phase 2's funnel and cost numbers to exist.

---

## 11. Open product questions

- **Packaging** — per-repo scan credits? per-seat for the triage UI? fleet
  licence? Article 2 gives economics but no pricing.
- **The Fixer's human gate** — how much of the 15–20-day rollout do we own vs hand
  back? Where does liability sit on a proposed patch?
- **Wishlist SLA** — if the wishlist is a primary interface, what is the promise
  on resolving a wish (human turnaround) and how much self-healing (spec §9.3) is
  realistic?
- **Per-PR** — separate product, or never? Article 2 implies separate-and-cheaper;
  spec §15 defers it entirely.
- **Benchmark we publish** — open-vs-frontier in the Hunter role, segmented by bug
  class (spec §12). The gap is widest on multi-bug exploit chaining (article 1).
  This determines what we can honestly claim vs a frontier-model competitor.
