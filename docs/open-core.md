# Open core: what's in this repo, and how a downstream extends it

Crucible is **open core**. This repository is the whole engine — the pipeline,
the prompts, the validation gates, the sandbox, the model registry, the API, and
a working dashboard. It is not a teaser build: the same code runs in the hosted
product.

A downstream distribution (the hosted service, an internal deployment, anything
that wraps the engine in a product) builds on top of this repo by importing
[`crucible.ext`](../crucible/ext.py) — and nothing else from `crucible`.

## Why the boundary exists

The commodity/moat split this is built on:

| Stays open (commodity — copying it gains nobody an edge) | Stays private (expensive to accumulate or operate) |
|---|---|
| The Recon → Hunt → Validate → Report pipeline | Multi-tenancy, RBAC/SSO, billing, scheduling |
| The prompt/skill library, incl. all attack classes | The validated-findings corpus and benchmarks |
| Validation gates (mechanical, bug, reachability) | The hosted model-catalog service |
| Sandbox, model registry, findings store, API | Cross-repo tracer, VVS, fixer (Phase 3/4) |
| The single-user dashboard | The multi-tenant web app |

The prompts are deliberately **open**. A security tool nobody can audit is a
security tool nobody should trust, and "our secret prompts are better" is not a
claim a serious buyer takes on faith. The defensible parts are the data and the
operations, not the text files.

## The extension API

Everything a downstream is allowed to import lives in `crucible.ext`:

```python
from crucible.ext import NodeSpec, build_graph, create_app, require_write
```

Two things it gives you:

**1. Extra pipeline stages.** `build_graph(deps, extra_nodes=[...])` splices new
stages into the graph. Each `NodeSpec(name, run, after)` rewires the static edge
leaving `after` to run through the new node. Stages can be *added between*
built-in ones; a built-in one can never be *replaced*.

```python
graph = build_graph(deps, extra_nodes=[
    NodeSpec("tracer", tracer.run, after="validate_reachability"),
    NodeSpec("fixer",  fixer.run,  after="report"),
])
```

`hunt` and `loop_control` branch through gate functions, so they are not splice
targets — `_splice` raises rather than silently picking a branch.

**2. A composable API.** `create_app(settings)` returns the FastAPI app; auth and
store access are plain FastAPI dependencies, so a downstream swaps them with
`dependency_overrides` and needs no hooks on our side:

```python
app = create_app(settings)
app.dependency_overrides[require_write] = tenant_require_write
app.dependency_overrides[get_store] = tenant_store
app.include_router(cloud_router, prefix="/cloud")
```

## The rules

1. **`crucible.ext` is semver-protected.** Removing or renaming anything in
   `__all__` is a major version bump. `tests/test_ext_contract.py` pins the exact
   set, so it cannot change by accident.
2. **Everything else is an implementation detail.** Node internals, the store
   schema, recon's helpers, router modules — all fair game to change in any
   release. A downstream that imports past `ext` is choosing to break later.
3. **Upstream first.** If a downstream needs behaviour the engine doesn't
   expose, the seam is added *here*, released, and then consumed — never
   patched downstream. That rule is what keeps the two from drifting into
   forks of each other.
4. **Additive only.** The ten stages in `STAGE_NODES` always run their own
   implementations. A downstream asserts this against its own composed graph;
   if that assertion ever fails, the split has failed.

## Adding a seam

When a downstream genuinely needs something new:

1. Add it to `crucible/ext.py` and to `__all__`.
2. Add the name to `EXPECTED_SURFACE` in `tests/test_ext_contract.py` (the test
   fails until you do — that's the point).
3. Release a minor version.
4. Bump the pin downstream.

Keep the surface small. Every name added is a promise that's expensive to take
back.
