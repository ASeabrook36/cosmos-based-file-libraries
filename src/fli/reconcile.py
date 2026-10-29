"""Enqueue library reconciliation with managed identity.

Run periodically for all experiment member IDs, including deleted members, while
remote workflow executions may still finish. Blob scope discovery also covers new
libraries and orphans written after deletion. No cloud resources are provisioned.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from fli.config import Settings
from fli.queue import ServiceBusQueue


async def blob_scope_ids(container):
    """List only top-level canonical library prefixes, including deleted scopes.

    This never queries chunks across libraries or trusts a file's contents as scope.
    walk_blobs with a delimiter avoids enumerating every document/artifact.
    """
    async for prefix in container.walk_blobs(delimiter="/"):
        name = prefix.name
        if not name.endswith("/"):
            continue
        candidate = name[:-1]
        try:
            canonical = str(UUID(candidate))
        except ValueError:
            continue
        if candidate == canonical:
            yield canonical


async def enqueue_scopes(queue, library_ids, discovered):
    seen = set()

    async def send(library_id):
        library_id = str(UUID(library_id))
        if library_id not in seen:
            await queue.send({"type": "reconcileLibrary", "libraryId": library_id}, session_id=library_id)
            seen.add(library_id)

    for library_id in library_ids:
        await send(library_id)
    async for library_id in discovered:
        await send(library_id)


async def enqueue(settings, library_ids, *, discover_blob_scopes=False):
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient
    from azure.storage.blob.aio import BlobServiceClient

    async with DefaultAzureCredential() as credential:
        async with ServiceBusClient(settings.servicebus_host, credential=credential) as bus:
            async with bus.get_queue_sender(settings.servicebus_queue) as sender:
                queue = ServiceBusQueue(sender)
                if discover_blob_scopes:
                    async with BlobServiceClient(settings.blob_account_url, credential=credential) as blobs:
                        await enqueue_scopes(queue, library_ids, blob_scope_ids(blobs.get_container_client("files")))
                else:

                    async def empty():
                        for _ in ():
                            yield _

                    await enqueue_scopes(queue, library_ids, empty())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-id", action="append", default=[])
    parser.add_argument("--discover-blob-scopes", action="store_true")
    args = parser.parse_args()
    if not args.library_id and not args.discover_blob_scopes:
        parser.error("provide --library-id or --discover-blob-scopes")
    asyncio.run(enqueue(Settings.from_env(), args.library_id, discover_blob_scopes=args.discover_blob_scopes))


if __name__ == "__main__":
    main()
