"""FastAPI application factory (issue #39).

    from crucible.api.app import create_app
    app = create_app()          # reads CRUCIBLE_API_* env

`crucible serve` runs this under uvicorn. Nothing here makes an outbound
network call — it only reads the domain store, the checkpoint DB, and the
workspace tree (specs §13).
"""

from __future__ import annotations

import crucible
from crucible.api import errors
from crucible.api.routers import artifacts, findings, health, runs, state, wishlist
from crucible.api.settings import ApiSettings
from crucible.store.dao import Store


def create_app(settings: ApiSettings | None = None) -> FastAPI:  # noqa: F821 (lazy import)
    from fastapi import FastAPI

    settings = settings or ApiSettings.from_env()

    app = FastAPI(
        title="Crucible API",
        version=crucible.__version__,
        summary="Read-first access to runs, findings, reports and run artifacts.",
    )
    app.state.settings = settings
    app.state.store = Store(settings.store_url)

    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["authorization", "content-type"],
        )

    errors.install(app)
    for r in (health.router, runs.router, findings.router, artifacts.router,
              state.router, wishlist.router):
        app.include_router(r)
    return app
