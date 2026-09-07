"""Recon stage internals — R0 seed / R1 map / R2 threat model / R3 decompose.

The `recon` graph node (`crucible/graph/nodes/recon.py`) orchestrates these.
Design: issue #5. Shape borrowed from Visa VVAH's S0–S3, expressed in our stack
(LangGraph node + LangChain `build_agent` + our registry/store/workspace).
"""

from crucible.recon.schema import (  # noqa: F401
    EntryPoint,
    EntryPointKind,
    HuntChunk,
    MapContribution,
    RepoKind,
    Seed,
    ThreatModel,
)
