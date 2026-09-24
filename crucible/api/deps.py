"""FastAPI dependencies (issues #39, #45)."""

from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import Depends, Request

from crucible.api.errors import ApiError
from crucible.api.settings import ApiSettings
from crucible.store.dao import Store


def get_settings(request: Request) -> ApiSettings:
    return request.app.state.settings


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_workspace(run_id: str, request: Request) -> Path:
    """Resolve a run's workspace dir (issue #38).

    `run.workspace_path` is only recorded once the spawned `crucible run`
    process gets far enough to call `create_run`/`set_repo_info` (see
    `cli.py`) — a run whose process crashed before that point (e.g. during
    clone or model-registry setup) leaves it blank forever. For those, prefer
    the deterministic per-run directory `launcher.py` already created at
    launch (`<runs_dir>/<run_id>/workspace`) over the shared `workspace_root`
    fallback: the latter is a *global* directory reused across runs, so
    serving it for an unrelated run silently shows a stranger's logs/artifacts
    instead of this run's (near-empty, but its own) state. Only truly old runs
    predating per-run directories (`workspace_path` blank and no per-run dir
    on disk) fall through to `workspace_root`.
    """
    store: Store = request.app.state.store
    settings: ApiSettings = request.app.state.settings
    run = store.get_run(run_id)
    if run is None:
        raise ApiError(404, "run not found", run_id)
    if run.workspace_path:
        return Path(run.workspace_path)
    per_run = Path(settings.runs_dir) / run_id / "workspace"
    if per_run.is_dir():
        return per_run
    return Path(settings.workspace_root)


def _token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def require_read(request: Request, settings: ApiSettings = Depends(get_settings)) -> None:
    if not settings.auth_enabled:
        return
    tok = _token(request)
    if _eq(tok, settings.auth_token) or _eq(tok, settings.auth_token_readonly):
        return
    raise ApiError(401, "unauthorized", "valid bearer token required")


def require_write(request: Request, settings: ApiSettings = Depends(get_settings)) -> None:
    if not settings.auth_enabled:
        return
    tok = _token(request)
    # A readonly-only deployment (no full token) still allows writes with that token.
    full = settings.auth_token or settings.auth_token_readonly
    if _eq(tok, full):
        return
    raise ApiError(403, "forbidden", "write token required")
