"""Original files in Blob Storage (spec §2: Blob holds bytes that are handed back).

``MemoryBlobs`` stands in for tests.
"""

from __future__ import annotations

from typing import Any, Protocol


class BlobStore(Protocol):
    async def upload(self, path: str, data: bytes, content_type: str) -> None: ...
    async def download(self, path: str) -> bytes: ...
    async def delete_prefix(self, prefix: str) -> int: ...


class AzureBlobs:
    def __init__(self, container: Any) -> None:  # azure.storage.blob.aio.ContainerClient
        self._c = container

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        await self._c.upload_blob(
            path, data, overwrite=True, content_settings=ContentSettings(content_type=content_type or None)
        )

    async def download(self, path: str) -> bytes:
        stream = await self._c.download_blob(path)
        return await stream.readall()

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every blob under ``prefix`` (which must end in ``/``). Returns the count."""
        if not prefix.endswith("/"):
            raise ValueError("a blob prefix must end in '/' so it cannot match a sibling id")
        from azure.core.exceptions import ResourceNotFoundError

        count = 0
        async for blob in self._c.list_blobs(name_starts_with=prefix):
            try:
                await self._c.delete_blob(blob.name, delete_snapshots="include")
                count += 1
            except ResourceNotFoundError:  # already gone: a retried purge
                pass
        return count


class MemoryBlobs:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.blobs[path] = (data, content_type)

    async def download(self, path: str) -> bytes:
        return self.blobs[path][0]

    async def delete_prefix(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            raise ValueError("a blob prefix must end in '/' so it cannot match a sibling id")
        doomed = [p for p in self.blobs if p.startswith(prefix)]
        for p in doomed:
            del self.blobs[p]
        return len(doomed)
