"""Writing and deleting chunks under one worker-wide cap (spec §7.2 step 8, §7.6; evidence E10).

Every chunk write and delete in the worker goes through one ``Governor``. Its concurrency is
``COSMOS_WRITE_CONCURRENCY`` (default 2) across all lanes, not per document: Phase 0 measured
two writers sustaining about 27 chunks per second on serverless, and eight exhausting the
SDK's own 429 retries within about 100 writes.

A 429 that outlasts the SDK's retries is retried here, after the ``x-ms-retry-after-ms`` the
service asked for. Each one is reported to the governor, which pauses every writer and, on
repeated throttling, halves the cap until writes succeed again. After ``max_attempts`` the
error is raised, the queue message is retried later, and deterministic chunk ids make the
repeated writes overwrite rather than duplicate.

Queries here are single-partition (``[libraryId, documentId]``) and never vector queries.

``patch`` sets top-level fields on one chunk (``retag``, spec §7.7). Its request charge is
returned, so the worker can report what metadata changes cost: unmeasured so far, and possibly
close to a full write, since Cosmos re-indexes the item.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fli.governor import Governor

Record = dict[str, Any]

DEFAULT_MAX_ATTEMPTS = 10


class ChunkStore(Protocol):
    async def upsert(self, chunk: Record) -> None: ...
    async def generations(self, library_id: str, document_id: str) -> list[tuple[str, int]]: ...
    async def delete(self, library_id: str, document_id: str, chunk_id: str) -> None: ...
    async def patch(self, library_id: str, document_id: str, chunk_id: str, fields: Record) -> float: ...


def retry_after_seconds(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None) or {}
    raw = headers.get("x-ms-retry-after-ms") if hasattr(headers, "get") else None
    try:
        return float(raw) / 1000.0 if raw is not None else None
    except (TypeError, ValueError):
        return None


def is_throttle(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 429


async def capped[T](
    governor: Governor, call: Callable[[], Awaitable[T]], max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> T:
    """Run one Cosmos write inside the cap, retrying 429s the SDK gave up on."""
    for attempt in range(1, max_attempts + 1):
        try:
            result = await governor.run(1, call)
        except Exception as exc:
            if not is_throttle(exc) or attempt == max_attempts:
                raise
            # The governor pauses every writer, so this request waits at its next run().
            governor.record_throttle(retry_after_seconds(exc), operation="chunk_write")
            continue
        governor.record_success()
        return result
    raise AssertionError("unreachable")


class CosmosChunks:
    def __init__(self, container: Any, governor: Governor, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        self._c = container  # azure.cosmos.aio.ContainerProxy for "chunks"
        self.governor = governor
        self.max_attempts = max_attempts

    async def upsert(self, chunk: Record) -> None:
        await capped(self.governor, lambda: self._c.upsert_item(chunk), self.max_attempts)

    async def generations(self, library_id: str, document_id: str) -> list[tuple[str, int]]:
        items = self._c.query_items(
            "SELECT c.id, c.generation FROM c WHERE c.libraryId = @libraryId AND c.documentId = @documentId",
            parameters=[{"name": "@libraryId", "value": library_id}, {"name": "@documentId", "value": document_id}],
            partition_key=[library_id, document_id],
        )
        return [(item["id"], item["generation"]) async for item in items]

    async def delete(self, library_id: str, document_id: str, chunk_id: str) -> None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        async def call() -> None:
            try:
                await self._c.delete_item(chunk_id, partition_key=[library_id, document_id])
            except CosmosResourceNotFoundError:  # already gone: a retried purge
                pass

        await capped(self.governor, call, self.max_attempts)

    async def patch(self, library_id: str, document_id: str, chunk_id: str, fields: Record) -> float:
        """Set ``fields`` on the chunk. Returns the request charge; 0 if the chunk is already gone."""
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        operations = [{"op": "set", "path": f"/{name}", "value": value} for name, value in fields.items()]

        async def call() -> float:
            try:
                item = await self._c.patch_item(
                    chunk_id, partition_key=[library_id, document_id], patch_operations=operations
                )
            except CosmosResourceNotFoundError:  # purged meanwhile: nothing to update
                return 0.0
            return float(item.get_response_headers().get("x-ms-request-charge", 0) or 0)

        return await capped(self.governor, call, self.max_attempts)


class MemoryChunks:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], Record] = {}
        self.writes = 0
        self.patches = 0

    async def upsert(self, chunk: Record) -> None:
        self.writes += 1
        self.items[(chunk["libraryId"], chunk["documentId"], chunk["id"])] = dict(chunk)

    async def generations(self, library_id: str, document_id: str) -> list[tuple[str, int]]:
        return [
            (c["id"], c["generation"])
            for (lib, doc, _), c in self.items.items()
            if (lib, doc) == (library_id, document_id)
        ]

    async def delete(self, library_id: str, document_id: str, chunk_id: str) -> None:
        self.items.pop((library_id, document_id, chunk_id), None)

    async def patch(self, library_id: str, document_id: str, chunk_id: str, fields: Record) -> float:
        self.patches += 1
        if (chunk := self.items.get((library_id, document_id, chunk_id))) is not None:
            chunk.update(fields)
        return 0.0
