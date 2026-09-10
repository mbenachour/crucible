"""Crucible HTTP API (milestone: API — runs, findings, reports & artifacts).

Read-first surface over everything a run produces: the domain store
(`findings.sqlite`), the LangGraph execution state (`checkpoints.sqlite`), and
the git-per-node workspace tree (§7). Makes no outbound network calls.

`crucible.api.artifacts` has no web dependency and is import-safe everywhere.
Everything under `crucible.api.app` needs the optional `crucible[api]` extra
(FastAPI + uvicorn) and is imported lazily by `crucible serve`.
"""

from __future__ import annotations
