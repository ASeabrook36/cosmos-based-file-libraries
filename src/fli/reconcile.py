"""Enqueue bounded, explicit library reconciliation with managed identity.

Run periodically for all experiment member IDs, including deleted members, while
remote workflow executions may still finish. No cloud resources are provisioned.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from fli.config import Settings
from fli.queue import ServiceBusQueue


async def enqueue(settings, library_ids):
    from azure.identity.aio import DefaultAzureCredential
    from azure.servicebus.aio import ServiceBusClient

    async with DefaultAzureCredential() as credential:
        async with ServiceBusClient(settings.servicebus_host, credential=credential) as bus:
            async with bus.get_queue_sender(settings.servicebus_queue) as sender:
                queue = ServiceBusQueue(sender)
                for library_id in library_ids:
                    await queue.send(
                        {"type": "reconcileLibrary", "libraryId": str(UUID(library_id))},
                        session_id=str(UUID(library_id)),
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-id", action="append", required=True)
    args = parser.parse_args()
    asyncio.run(enqueue(Settings.from_env(), args.library_id))


if __name__ == "__main__":
    main()
