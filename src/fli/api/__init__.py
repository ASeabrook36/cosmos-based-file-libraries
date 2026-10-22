"""Index API entry point (spec §2, §6).

Phase 0 served only ``/healthz``. The endpoints of spec §6 arrive in Phase 1.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fli.auth import check_auth_supported
from fli.config import Settings
from fli.errors import ApiError


def create_app(settings: Settings) -> FastAPI:
    check_auth_supported(settings)
    app = FastAPI(title="File Library Index", version="0.0.1")
    app.state.settings = settings

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()  # raises before anything listens on a refused configuration
    uvicorn.run(
        create_app(settings), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000"))
    )
