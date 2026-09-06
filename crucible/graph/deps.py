"""Runtime dependencies handed to every node.

These are live objects (model registry, DB session factory, sandbox provider) —
they must NOT go in graph state, which is checkpointed and JSON-ish. `build_graph`
closes over a single `NodeDeps` and binds it to each node with `functools.partial`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NodeDeps:
    registry: Any            # crucible.llm.registry.ModelRegistry
    store: Any               # crucible.store.dao.Store
    sandbox_provider: Any    # crucible.sandbox.SandboxProvider (may be None until built)
    config_path: str | None = None
