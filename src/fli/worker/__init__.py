"""Ingestion worker entry point (spec §2, §7).

Lanes (spec §7.1): up to ``INGEST_MAX_CONCURRENT_SESSIONS`` lanes each take the next library
with messages waiting (a Service Bus session), process its messages one at a time in order,
and let it go after a few idle seconds. One library's backlog therefore occupies one lane.
The session lock is renewed while a long document is processed.

Settling (``settle``): success completes the message. An unusable message dead-letters at
once. Any other error abandons it for redelivery, until the final delivery: then the document
is marked failed and the message dead-lettered by the worker itself, so the two agree.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

from fli.config import ConfigError, Settings
from fli.worker.handlers import BadMessage, Worker

log = logging.getLogger("fli.worker")

# Must equal maxDeliveryCount on the ingest queue (infra/main.bicep); a test checks they agree.
MAX_DELIVERY_COUNT = 5
SESSION_IDLE_SECONDS = 5
LOCK_RENEWAL_SECONDS = 3600


@dataclass(frozen=True)
class Settlement:
    action: Literal["complete", "abandon", "dead_letter"]
    reason: str | None = None


def attempt_number(delivery_count: int | None) -> int:
    """1 for the first delivery. Service Bus's AMQP ``delivery_count`` counts earlier attempts."""
    return (delivery_count or 0) + 1


async def settle(worker: Worker, body: bytes, delivery_count: int | None) -> Settlement:
    try:
        message = json.loads(body)
        if not isinstance(message, dict):
            raise BadMessage("message body is not a JSON object")
        await worker.handle(message)
        return Settlement("complete")
    except (BadMessage, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("bad message dead-lettered", extra={"reason": type(exc).__name__})
        return Settlement("dead_letter", f"bad_message:{type(exc).__name__}")
    except Exception as exc:
        reason = f"processing_error:{type(exc).__name__}"
        attempt = attempt_number(delivery_count)
        log.warning("message failed", extra={"reason": reason, "attempt": attempt}, exc_info=True)
        if attempt < MAX_DELIVERY_COUNT:
            return Settlement("abandon", reason)
        try:
            await worker.fail(message, reason)
        except Exception:  # still dead-letter: the message must not cycle forever
            log.exception("could not mark the document failed")
        return Settlement("dead_letter", reason)


async def run_lanes(lanes: int, take_session: Callable[[], Awaitable[bool]], stopping: asyncio.Event) -> None:
    """Run ``lanes`` loops, each repeatedly taking a session until ``stopping`` is set.

    ``take_session`` processes one session and returns whether it found one.
    """

    async def lane(number: int) -> None:
        while not stopping.is_set():
            try:
                found = await take_session()
            except Exception:
                log.exception("lane error", extra={"lane": number})
                found = False
            if not found:
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=1)
                except TimeoutError:
                    pass

    await asyncio.gather(*(lane(n) for n in range(lanes)))


async def run(settings: Settings, stopping: asyncio.Event) -> None:
    for name in ("cosmos_endpoint", "servicebus_namespace", "blob_account_url", "aoai_endpoint"):
        if not getattr(settings, name):
            raise ConfigError(f"{name.upper()} is required")
    if not settings.aoai_embedding_deployment:
        raise ConfigError("AOAI_EMBEDDING_DEPLOYMENT is required")

    import httpx
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus import NEXT_AVAILABLE_SESSION
    from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient
    from azure.servicebus.exceptions import OperationTimeoutError
    from azure.storage.blob.aio import BlobServiceClient

    from fli.blobs import AzureBlobs
    from fli.chunking import Chunker, default_tokenizer
    from fli.chunks import CosmosChunks
    from fli.docintel import SCOPE as COGNITIVE_SCOPE
    from fli.docintel import DocumentIntelligenceReader
    from fli.embedding import AzureOpenAIEmbedder
    from fli.governor import Governor
    from fli.proposals import AzureOpenAIChat
    from fli.queue import ServiceBusQueue
    from fli.store import CosmosDocumentStore, CosmosLibraryStore
    from fli.tokens import CachedToken

    def report(event: str, fields: dict[str, Any]) -> None:
        log.info(event, extra=fields)

    async with AsyncExitStack() as stack:
        credential = await stack.enter_async_context(DefaultAzureCredential())
        tokens = CachedToken(credential, COGNITIVE_SCOPE)
        http = await stack.enter_async_context(httpx.AsyncClient())
        cosmos = await stack.enter_async_context(CosmosClient(settings.cosmos_endpoint, credential=credential))
        database = cosmos.get_database_client(settings.cosmos_database)
        bus = await stack.enter_async_context(ServiceBusClient(settings.servicebus_host, credential=credential))
        sender = await stack.enter_async_context(bus.get_queue_sender(settings.servicebus_queue))
        blob_service = await stack.enter_async_context(
            BlobServiceClient(settings.blob_account_url, credential=credential)
        )

        async def renewal_failed(renewable: Any, error: Exception | None) -> None:
            # A lost session lock lets another lane take the same library and redo the message.
            log.error("lock renewal failed", extra={"error": type(error).__name__ if error else None})

        renewer = await stack.enter_async_context(
            AutoLockRenewer(max_lock_renewal_duration=LOCK_RENEWAL_SECONDS, on_lock_renew_failure=renewal_failed)
        )

        writes = Governor("cosmos_writes", max_concurrency=settings.cosmos_write_concurrency, on_event=report)
        embeddings = Governor("embeddings", max_concurrency=4, on_event=report)
        ocr = (
            DocumentIntelligenceReader(
                endpoint=settings.docintel_endpoint,
                token_provider=tokens,
                http=http,
                max_pages=settings.ocr_max_pages,
                max_bytes=settings.upload_max_bytes,
            )
            if settings.docintel_endpoint
            else None
        )
        proposer = (
            AzureOpenAIChat(
                endpoint=settings.aoai_endpoint,
                deployment=settings.aoai_chat_deployment,
                token_provider=tokens,
                http=http,
            )
            if settings.aoai_chat_deployment
            else None
        )
        if proposer is None:
            log.info("proposals off: AOAI_CHAT_DEPLOYMENT is not set")
        worker = Worker(
            libraries=CosmosLibraryStore(database.get_container_client("libraries")),
            documents=CosmosDocumentStore(database.get_container_client("documents")),
            chunks=CosmosChunks(database.get_container_client("chunks"), writes),
            blobs=AzureBlobs(blob_service.get_container_client(settings.blob_container)),
            queue=ServiceBusQueue(sender),
            embedder=AzureOpenAIEmbedder(
                endpoint=settings.aoai_endpoint,
                deployment=settings.aoai_embedding_deployment,
                token_provider=tokens,
                http=http,
                dimensions=settings.aoai_embedding_dimensions,
                governor=embeddings,
            ),
            chunker=Chunker(default_tokenizer()),
            ocr=ocr,
            max_chunks_per_document=settings.ingest_max_chunks_per_document,
            proposer=proposer,
        )

        async def take_session() -> bool:
            try:
                receiver = bus.get_queue_receiver(
                    settings.servicebus_queue,
                    session_id=NEXT_AVAILABLE_SESSION,
                    max_wait_time=SESSION_IDLE_SECONDS,
                    prefetch_count=0,
                )
                async with receiver:
                    renewer.register(receiver, receiver.session)
                    library_id = receiver.session.session_id
                    log.info("session taken", extra={"libraryId": library_id})
                    async for message in receiver:  # ends after SESSION_IDLE_SECONDS without messages
                        log.info(
                            "message received",
                            extra={"libraryId": library_id, "deliveryCount": message.delivery_count},
                        )
                        outcome = await settle(worker, b"".join(message.body), message.delivery_count)
                        log.info("message settled", extra={"libraryId": library_id, "action": outcome.action})
                        if outcome.action == "complete":
                            await receiver.complete_message(message)
                        elif outcome.action == "abandon":
                            await receiver.abandon_message(message)
                        else:
                            await receiver.dead_letter_message(message, reason=outcome.reason)
                        if stopping.is_set():
                            break
                    return True
            except OperationTimeoutError:
                return False  # no library has messages waiting

        log.info("worker started", extra={"lanes": settings.ingest_max_concurrent_sessions})
        await run_lanes(settings.ingest_max_concurrent_sessions, take_session, stopping)


def main() -> None:
    from fli.logs import configure

    configure()
    settings = Settings.from_env()
    stopping = asyncio.Event()

    async def start() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopping.set)  # finish the message in hand, then stop
        await run(settings, stopping)

    try:
        asyncio.run(start())
    except ConfigError as exc:
        print(f"fli-worker: {exc}", file=sys.stderr)
        sys.exit(2)
