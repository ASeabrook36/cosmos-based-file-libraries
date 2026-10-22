"""Index API entry point (spec §2, §6)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from fli.auth import check_auth_supported
from fli.config import ConfigError, Settings
from fli.errors import ApiError
from fli.queue import Queue, ServiceBusQueue
from fli.store import CosmosDocumentStore, CosmosLibraryStore, DocumentStore, LibraryStore


def _invalid(exc: RequestValidationError | ValidationError) -> JSONResponse:
    # Name the fields and the reasons, never the submitted values: they may be personal content.
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'body'}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(ApiError(400, "invalid_request", problems).body(), status_code=400)


@asynccontextmanager
async def _azure(app: FastAPI) -> AsyncIterator[None]:
    """Connect to Cosmos and Service Bus with the managed identity (CLAUDE.md rule 13)."""
    settings: Settings = app.state.settings
    if not (settings.cosmos_endpoint and settings.servicebus_namespace):
        raise ConfigError("COSMOS_ENDPOINT and SERVICEBUS_NAMESPACE are required")
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        cosmos = await stack.enter_async_context(CosmosClient(settings.cosmos_endpoint, credential=credential))
        database = cosmos.get_database_client(settings.cosmos_database)
        bus = await stack.enter_async_context(
            ServiceBusClient(f"{settings.servicebus_namespace}.servicebus.windows.net", credential=credential)
        )
        sender = await stack.enter_async_context(bus.get_queue_sender(settings.servicebus_queue))
        app.state.libraries = CosmosLibraryStore(database.get_container_client("libraries"))
        app.state.documents = CosmosDocumentStore(database.get_container_client("documents"))
        app.state.queue = ServiceBusQueue(sender)
        yield


def create_app(
    settings: Settings,
    *,
    libraries: LibraryStore | None = None,
    documents: DocumentStore | None = None,
    queue: Queue | None = None,
) -> FastAPI:
    """Build the API. Pass all three stores for tests; pass none to connect to Azure at startup."""
    check_auth_supported(settings)
    injected = (libraries, documents, queue)
    if any(x is not None for x in injected) and not all(x is not None for x in injected):
        raise ValueError("pass libraries, documents and queue together, or none of them")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if libraries is not None:
            yield
        else:
            async with _azure(app):
                yield

    app = FastAPI(title="File Library Index", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    if libraries is not None:
        app.state.libraries, app.state.documents, app.state.queue = libraries, documents, queue

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _invalid(exc)

    @app.exception_handler(ValidationError)
    async def model_invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return _invalid(exc)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from fli.api import libraries as library_routes

    app.include_router(library_routes.router)
    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()  # raises before anything listens on a refused configuration
    uvicorn.run(
        create_app(settings), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000"))
    )
