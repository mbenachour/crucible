"""API server configuration (issue #39). Env-first, no external services."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import crucible
from crucible.repo_acquire import DEFAULT_ALLOWED_HOSTS


def _split(val: str | None) -> list[str]:
    return [p.strip() for p in (val or "").split(",") if p.strip()]


def _default_ui_dir() -> str:
    """Where the built dashboard lives, if it has been built (issue #49).

    Preference: an env override, then a packaged copy, then the repo's `ui/dist`.
    """
    env = os.environ.get("CRUCIBLE_API_UI_DIR")
    if env:
        return env
    pkg = Path(crucible.__file__).parent / "api" / "ui_dist"
    if (pkg / "index.html").is_file():
        return str(pkg)
    repo = Path(crucible.__file__).parent.parent / "ui" / "dist"
    if (repo / "index.html").is_file():
        return str(repo)
    return ""


@dataclass
class ApiSettings:
    store_url: str = "sqlite:///findings.sqlite"
    checkpoint_db: str = "checkpoints.sqlite"
    # Fallback workspace when a run row has no recorded workspace_path (older runs).
    workspace_root: str = ".crucible-workspace"
    host: str = "127.0.0.1"
    port: int = 8787
    # Auth (issue #45). Empty == no auth (only allowed on a loopback bind).
    auth_token: str = ""
    auth_token_readonly: str = ""
    allow_no_auth: bool = False
    cors_origins: list[str] = field(default_factory=list)
    # Built dashboard (issue #49). "" → not served. `serve_ui=False` → never serve.
    ui_dir: str = ""
    serve_ui: bool = True
    # Triggering runs (issue #57/#63). Where clones + per-run workspaces land,
    # which git hosts a clone may target, and the abuse caps.
    runs_dir: str = ".crucible-runs"
    allowed_git_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS))
    max_concurrent_runs: int = 2
    clone_timeout_s: int = 120
    clone_max_mb: int = 500

    @classmethod
    def from_env(cls, **overrides) -> ApiSettings:
        env = os.environ.get
        base = cls(
            store_url=env("CRUCIBLE_API_STORE_URL", env("CRUCIBLE_STORE_URL", cls.store_url)),
            checkpoint_db=env("CRUCIBLE_API_CHECKPOINT_DB", cls.checkpoint_db),
            workspace_root=env("CRUCIBLE_API_WORKSPACE_ROOT", cls.workspace_root),
            host=env("CRUCIBLE_API_HOST", cls.host),
            port=int(env("CRUCIBLE_API_PORT", cls.port)),
            auth_token=env("CRUCIBLE_API_TOKEN", ""),
            auth_token_readonly=env("CRUCIBLE_API_TOKEN_READONLY", ""),
            allow_no_auth=env("CRUCIBLE_API_ALLOW_NO_AUTH", "") == "1",
            cors_origins=_split(env("CRUCIBLE_API_CORS_ORIGINS", "")),
            ui_dir=_default_ui_dir(),
            serve_ui=env("CRUCIBLE_API_NO_UI", "") != "1",
            runs_dir=env("CRUCIBLE_API_RUNS_DIR", cls.runs_dir),
            allowed_git_hosts=_split(env("CRUCIBLE_API_ALLOWED_GIT_HOSTS", ",".join(DEFAULT_ALLOWED_HOSTS)))
            or list(DEFAULT_ALLOWED_HOSTS),
            max_concurrent_runs=int(env("CRUCIBLE_API_MAX_CONCURRENT_RUNS", cls.max_concurrent_runs)),
            clone_timeout_s=int(env("CRUCIBLE_API_CLONE_TIMEOUT_S", cls.clone_timeout_s)),
            clone_max_mb=int(env("CRUCIBLE_API_CLONE_MAX_MB", cls.clone_max_mb)),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(base, k, v)
        return base

    def resolved_ui_dir(self) -> str:
        if not self.serve_ui:
            return ""
        return self.ui_dir or _default_ui_dir()

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_token or self.auth_token_readonly)

    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "::1", "localhost")

    def validate(self) -> None:
        """Refuse an unsafe combination early (issue #45)."""
        if not self.auth_enabled and not self.is_loopback() and not self.allow_no_auth:
            raise RuntimeError(
                f"refusing to bind {self.host} with no auth token — set CRUCIBLE_API_TOKEN "
                "or pass --no-auth to override for a trusted network"
            )
