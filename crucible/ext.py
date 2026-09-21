"""The public extension API — the open-core seam.

Crucible is open core: this repository is the whole engine, and a downstream
distribution (a hosted/commercial layer, an internal deployment, anyone's fork
of the *product* rather than the *engine*) builds on top of it by importing
**this module and nothing else** from `crucible`.

Why a single named surface rather than "just import what you need":

* Everything re-exported here is **semver-protected**. Breaking one of these is
  a major version bump, and `tests/test_ext_contract.py` fails the moment the
  set changes, so it cannot happen by accident.
* Everything *not* here — node internals, the store schema, recon's helpers,
  router modules — is an implementation detail and may change in any release.
  A downstream that reaches past this module is choosing to be broken later.
* It makes "is this thing a public promise?" answerable by looking at one file,
  which is what keeps a downstream from quietly turning into a hard fork of the
  engine. The engine is the shared part; that only stays true if extending it
  never requires editing it.

What a downstream composes with:

    from crucible.ext import NodeSpec, build_graph, create_app, require_write

    # extra stages, spliced in — never replacing a built-in one
    graph = build_graph(deps, extra_nodes=[
        NodeSpec("tracer", tracer.run, after="validate_reachability"),
    ])

    # the same API, with its auth/store dependencies swapped for tenant-aware
    # ones (plain FastAPI dependency_overrides — no hooks needed on our side)
    app = create_app(settings)
    app.dependency_overrides[require_write] = tenant_require_write
    app.include_router(my_router, prefix="/cloud")

The API-layer names (`create_app`, `ApiSettings`, the FastAPI dependencies)
need the optional `api` extra; they are resolved lazily so that importing this
module for graph composition alone does not require FastAPI to be installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# --- composing the graph -------------------------------------------------
from crucible.graph.build import (
    STAGE_NODES,
    NodeSpec,
    StopAfterStage,
    build_graph,
)
from crucible.graph.deps import NodeDeps
from crucible.graph.state import CrucibleState

# --- what a stage needs to do its job ------------------------------------
from crucible.llm.registry import ModelEndpoint, ModelRegistry, ModelRole
from crucible.obs import span
from crucible.skills import load_skill, skill_front_matter
from crucible.store.dao import Store
from crucible.store.models import FindingRow, ValidationRow, WishRow
from crucible.validation.schema import Finding, Severity, ThreatModel

if TYPE_CHECKING:  # import-time cost and the `api` extra are both avoided below
    from crucible.api.app import create_app
    from crucible.api.deps import (
        get_settings,
        get_store,
        require_read,
        require_write,
    )
    from crucible.api.settings import ApiSettings

# name -> (module, attribute). Resolved on first access by `__getattr__`.
_LAZY_API = {
    "create_app": ("crucible.api.app", "create_app"),
    "ApiSettings": ("crucible.api.settings", "ApiSettings"),
    "get_settings": ("crucible.api.deps", "get_settings"),
    "get_store": ("crucible.api.deps", "get_store"),
    "require_read": ("crucible.api.deps", "require_read"),
    "require_write": ("crucible.api.deps", "require_write"),
}


def __getattr__(name: str) -> Any:
    """Resolve the API-layer names on demand (PEP 562).

    Keeps `import crucible.ext` working for a downstream that only composes a
    graph, while giving one clear error — instead of a bare ImportError from
    somewhere inside the package — to one that wants the HTTP layer without
    having installed the extra that provides it.
    """
    target = _LAZY_API.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    try:
        from importlib import import_module

        return getattr(import_module(module_name), attr)
    except ModuleNotFoundError as e:  # fastapi / uvicorn not installed
        raise ModuleNotFoundError(
            f"crucible.ext.{name} needs the optional API extra: "
            f"pip install 'crucible[api]'"
        ) from e


# The public promise, in one list. `tests/test_ext_contract.py` pins it.
# Grouped by what a downstream reaches for rather than alphabetised — the
# grouping is the documentation of what this surface is *for*.
__all__ = [  # noqa: RUF022
    # graph composition
    "STAGE_NODES",
    "NodeSpec",
    "StopAfterStage",
    "build_graph",
    "NodeDeps",
    "CrucibleState",
    # stage building blocks
    "ModelEndpoint",
    "ModelRegistry",
    "ModelRole",
    "span",
    "load_skill",
    "skill_front_matter",
    "Store",
    "FindingRow",
    "ValidationRow",
    "WishRow",
    "Finding",
    "Severity",
    "ThreatModel",
    # API composition (lazy — needs the `api` extra)
    "create_app",
    "ApiSettings",
    "get_settings",
    "get_store",
    "require_read",
    "require_write",
]
