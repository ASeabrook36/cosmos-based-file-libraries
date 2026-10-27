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
from fli.blobs import AzureBlobs, BlobStore
from fli.config import ConfigError, Settings
from fli.errors import ApiError
from fli.metrics import record as record_metric
from fli.queue import Queue, ServiceBusQueue
from fli.search import CosmosChunkReader, Searcher
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
    required = (
        "cosmos_endpoint",
        "servicebus_namespace",
        "blob_account_url",
        "aoai_endpoint",
        "aoai_embedding_deployment",
    )
    missing = [name.upper() for name in required if not getattr(settings, name)]
    if missing:
        raise ConfigError(f"required: {', '.join(missing)}")
    import httpx
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient
    from azure.storage.blob.aio import BlobServiceClient

    from fli.docintel import SCOPE
    from fli.embedding import AzureOpenAIEmbedder
    from fli.tokens import CachedToken

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        cosmos = await stack.enter_async_context(CosmosClient(settings.cosmos_endpoint, credential=credential))
        database = cosmos.get_database_client(settings.cosmos_database)
        bus = await stack.enter_async_context(ServiceBusClient(settings.servicebus_host, credential=credential))
        sender = await stack.enter_async_context(bus.get_queue_sender(settings.servicebus_queue))
        blob_service = await stack.enter_async_context(
            BlobServiceClient(settings.blob_account_url, credential=credential)
        )
        app.state.blobs = AzureBlobs(blob_service.get_container_client(settings.blob_container), blob_service)
        app.state.libraries = CosmosLibraryStore(database.get_container_client("libraries"))
        app.state.documents = CosmosDocumentStore(database.get_container_client("documents"))
        app.state.queue = ServiceBusQueue(sender)
        http = await stack.enter_async_context(httpx.AsyncClient())

        embedder = AzureOpenAIEmbedder(
            endpoint=settings.aoai_endpoint,
            deployment=settings.aoai_embedding_deployment,
            token_provider=CachedToken(credential, SCOPE),
            http=http,
            dimensions=settings.aoai_embedding_dimensions,
        )
        app.state.searcher = Searcher(
            app.state.libraries,
            app.state.documents,
            CosmosChunkReader(database.get_container_client("chunks")),
            embedder,
            mode=settings.search_mode,
            blobs=app.state.blobs,
        )
        yield


def create_app(
    settings: Settings,
    *,
    libraries: LibraryStore | None = None,
    documents: DocumentStore | None = None,
    queue: Queue | None = None,
    blobs: BlobStore | None = None,
    searcher: Searcher | None = None,
) -> FastAPI:
    """Build the API. Pass every service for tests; pass none to connect to Azure at startup."""
    check_auth_supported(settings)
    injected = (libraries, documents, queue, blobs, searcher)
    if any(x is not None for x in injected) and not all(x is not None for x in injected):
        raise ValueError("pass libraries, documents, queue, blobs and searcher together, or none of them")

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
        app.state.libraries, app.state.documents, app.state.queue, app.state.blobs, app.state.searcher = injected
        searcher.blobs = blobs

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _invalid(exc)

    @app.exception_handler(ValidationError)
    async def model_invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return _invalid(exc)

    @app.middleware("http")
    async def retrieval_errors(request: Request, call_next):
        import time

        started = time.perf_counter()
        operations = {
            "/search": "search",
            "/documents:read": "read",
            "/documents:discover": "discover",
            "/documents:images": "images",
        }
        operation = next((v for k, v in operations.items() if request.url.path.endswith(k)), None)
        try:
            response = await call_next(request)
        except Exception:
            if operation and (library_id := request.path_params.get("library_id")):
                record_metric(
                    "error",
                    library_id,
                    agentId=request.headers.get("X-Agent-Id"),
                    failedOperation=operation,
                    status=500,
                    totalMs=round((time.perf_counter() - started) * 1000, 1),
                )
            raise
        if operation and response.status_code >= 400 and (library_id := request.path_params.get("library_id")):
            record_metric(
                "error",
                library_id,
                agentId=request.headers.get("X-Agent-Id"),
                failedOperation=operation,
                status=response.status_code,
                totalMs=round((time.perf_counter() - started) * 1000, 1),
            )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from fli.api import documents as document_routes
    from fli.api import libraries as library_routes
    from fli.api import metrics as metrics_routes
    from fli.api import reading as reading_routes
    from fli.api import search as search_routes

    app.include_router(library_routes.router)
    app.include_router(document_routes.router)
    app.include_router(search_routes.router)
    app.include_router(reading_routes.router)
    app.include_router(metrics_routes.router)
    return app


def main() -> None:
    import uvicorn

    from fli.logs import configure

    settings = Settings.from_env()  # raises before anything listens on a refused configuration
    configure()
    uvicorn.run(
        create_app(settings), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000"))
    )
