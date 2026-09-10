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

    _mount_ui(app, settings)
    return app


def _mount_ui(app, settings: ApiSettings) -> None:
    """Serve the built dashboard (issue #49).

    `StaticFiles` (mounted after the routers, so `/runs`, `/findings`, … keep
    returning JSON) handles `/`, `/assets/*` and sibling files. A tiny middleware
    adds the SPA deep-link fallback: a *browser navigation* (`GET` with
    `text/html` in `Accept`) that would 404 gets `index.html` instead — the SPA's
    own `fetch()` calls send `*/*`/`application/json` and are untouched.
    """
    from pathlib import Path

    ui_dir = settings.resolved_ui_dir()
    if not ui_dir or not (Path(ui_dir) / "index.html").is_file():
        return

    from fastapi import Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    index = Path(ui_dir) / "index.html"

    @app.middleware("http")
    async def _spa_fallback(request: Request, call_next):
        resp = await call_next(request)
        if (
            resp.status_code == 404
            and request.method == "GET"
            and "text/html" in request.headers.get("accept", "")
        ):
            return FileResponse(index)
        return resp

    app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
