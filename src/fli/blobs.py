"""Original files in Blob Storage (spec §2: Blob holds bytes that are handed back).

``MemoryBlobs`` stands in for tests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote


class BlobStore(Protocol):
    async def upload(self, path: str, data: bytes, content_type: str) -> None: ...
    async def create(self, path: str, data: bytes, content_type: str) -> bool: ...
    async def download(self, path: str) -> bytes: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def list_prefix(self, prefix: str) -> list[str]: ...
    async def delete(self, path: str) -> None: ...
    async def read_link(self, path: str) -> dict[str, str]: ...


class AzureBlobs:
    def __init__(self, container: Any, service: Any = None) -> None:  # azure.storage.blob.aio.ContainerClient
        self._c = container
        self._service = service
        self._key = None
        self._key_until = datetime.min.replace(tzinfo=UTC)
        self._key_lock = asyncio.Lock()

    async def read_link(self, path: str) -> dict[str, str]:
        """15-minute HTTPS, blob-only, read-only user-delegation SAS; never an account key."""
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = datetime.now(UTC)
        expiry = now + timedelta(minutes=15)
        async with self._key_lock:
            if self._key is None or self._key_until < expiry + timedelta(minutes=5):
                until = now + timedelta(hours=1)
                key = await self._service.get_user_delegation_key(now - timedelta(minutes=5), until)
                self._key, self._key_until = key, until
        sas = generate_blob_sas(
            account_name=self._c.account_name,
            container_name=self._c.container_name,
            blob_name=path,
            user_delegation_key=self._key,
            permission=BlobSasPermissions(read=True),
            start=now - timedelta(minutes=5),
            expiry=expiry,
            protocol="https",
        )
        return {"url": f"{self._c.get_blob_client(path).url}?{sas}", "expiresAt": expiry.isoformat()}

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        await self._c.upload_blob(
            path, data, overwrite=True, content_settings=ContentSettings(content_type=content_type or None)
        )

    async def create(self, path: str, data: bytes, content_type: str) -> bool:
        """Atomically create immutable bytes. False means another writer won."""
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        try:
            await self._c.upload_blob(
                path, data, overwrite=False, content_settings=ContentSettings(content_type=content_type)
            )
        except ResourceExistsError:
            return False
        return True

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

    async def list_prefix(self, prefix: str) -> list[str]:
        if not prefix.endswith("/"):
            raise ValueError("a blob prefix must end in '/' so it cannot match a sibling id")
        return [blob.name async for blob in self._c.list_blobs(name_starts_with=prefix)]

    async def delete(self, path: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            await self._c.delete_blob(path, delete_snapshots="include")
        except ResourceNotFoundError:  # already gone: a retried purge
            pass


class MemoryBlobs:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, str]] = {}

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.blobs[path] = (data, content_type)

    async def create(self, path: str, data: bytes, content_type: str) -> bool:
        if path in self.blobs:
            return False
        self.blobs[path] = (data, content_type)
        return True

    async def download(self, path: str) -> bytes:
        return self.blobs[path][0]

    async def read_link(self, path: str) -> dict[str, str]:
        if path not in self.blobs:
            raise KeyError("blob unavailable")
        return {
            "url": "https://files.example.invalid/" + quote(path, safe="/"),
            "expiresAt": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        }

    async def delete_prefix(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            raise ValueError("a blob prefix must end in '/' so it cannot match a sibling id")
        doomed = [p for p in self.blobs if p.startswith(prefix)]
        for p in doomed:
            del self.blobs[p]
        return len(doomed)

    async def list_prefix(self, prefix: str) -> list[str]:
        if not prefix.endswith("/"):
            raise ValueError("a blob prefix must end in '/' so it cannot match a sibling id")
        return [p for p in self.blobs if p.startswith(prefix)]

    async def delete(self, path: str) -> None:
        self.blobs.pop(path, None)
