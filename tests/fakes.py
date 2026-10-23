"""An API wired to in-memory services, for endpoint tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from fli.api import create_app
from fli.blobs import MemoryBlobs
from fli.chunks import MemoryChunks
from fli.config import Settings
from fli.queue import MemoryQueue
from fli.store import MemoryDocumentStore, MemoryLibraryStore

KEY = "k"
AUTH = {"Authorization": f"Bearer {KEY}"}


@dataclass
class Services:
    libraries: MemoryLibraryStore = field(default_factory=MemoryLibraryStore)
    documents: MemoryDocumentStore = field(default_factory=MemoryDocumentStore)
    queue: MemoryQueue = field(default_factory=MemoryQueue)
    blobs: MemoryBlobs = field(default_factory=MemoryBlobs)
    chunks: MemoryChunks = field(default_factory=MemoryChunks)


def settings(**overrides: str) -> Settings:
    return Settings.from_env({"ENVIRONMENT": "local", "AUTH_MODE": "dev", "DEV_API_KEY": KEY, **overrides})


def api(services: Services | None = None, **overrides: str) -> tuple[TestClient, Services]:
    s = services or Services()
    app = create_app(settings(**overrides), libraries=s.libraries, documents=s.documents, queue=s.queue, blobs=s.blobs)
    return TestClient(app, headers=AUTH), s
