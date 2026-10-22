"""Index API entry point (spec §2, §6).

Phase 0 serves only ``/healthz``. The endpoints of spec §6 arrive in Phase 1.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from fli.config import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="File Library Index", version="0.0.1")
    app.state.settings = settings

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
