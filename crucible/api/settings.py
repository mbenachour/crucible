"""API server configuration (issue #39). Env-first, no external services."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split(val: str | None) -> list[str]:
    return [p.strip() for p in (val or "").split(",") if p.strip()]


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
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(base, k, v)
        return base

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
