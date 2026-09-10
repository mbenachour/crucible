"""Exception → HTTP mapping (issue #39). Never leak a traceback in the body."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from crucible.api.artifacts import (
    ArtifactForbidden,
    ArtifactNotFound,
    ArtifactTooLarge,
)

log = logging.getLogger("crucible.api")


class ApiError(Exception):
    """Raised by routers for a clean client-facing failure."""

    def __init__(self, status_code: int, error: str, detail: str | None = None):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.detail = detail


def _json(status: int, error: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


def not_found(what: str) -> ApiError:
    return ApiError(404, f"{what} not found")


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _json(exc.status_code, exc.error, exc.detail)

    @app.exception_handler(ArtifactNotFound)
    async def _art_not_found(_: Request, exc: ArtifactNotFound):
        return _json(404, "artifact not found", str(exc))

    @app.exception_handler(ArtifactForbidden)
    async def _forbidden(_: Request, exc: ArtifactForbidden):
        return _json(403, "forbidden", str(exc))

    @app.exception_handler(ArtifactTooLarge)
    async def _too_large(_: Request, exc: ArtifactTooLarge):
        return _json(413, "artifact too large", str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException):
        return _json(exc.status_code, str(exc.detail or "error"))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return _json(422, "invalid request", str(exc.errors()))

    @app.exception_handler(Exception)
    async def _catch_all(_: Request, exc: Exception):
        log.exception("unhandled error in API handler")
        return _json(500, "internal error", type(exc).__name__)
