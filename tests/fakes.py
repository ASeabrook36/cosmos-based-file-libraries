"""An API wired to in-memory services, for endpoint and worker tests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from fli.api import create_app
from fli.blobs import MemoryBlobs
from fli.chunks import MemoryChunks
from fli.config import Settings
from fli.query import PROJECTION, RenderedQuery, check_query
from fli.queue import MemoryQueue
from fli.search import QueryCost, Searcher
from fli.store import MemoryDocumentStore, MemoryLibraryStore

KEY = "k"
AUTH = {"Authorization": f"Bearer {KEY}"}
FIELDS = [p.removeprefix("c.") for p in PROJECTION]


class FakeEmbedder:
    model, dimensions, version = "text-embedding-3-large", 3, "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[float(len(t)), 0.5, 2.0**-20] for t in texts]  # the last value must be flushed to 0


def matches_filters(chunk: dict, params: dict) -> bool:
    """The spec §8 filter clauses, applied by the parameters the rendered query carries."""

    def any_of(prefix: str, value) -> bool:
        listed = [v for k, v in params.items() if re.fullmatch(rf"@{prefix}\d+", k)]
        return not listed or value in listed

    if "@pFrom" in params and not (
        chunk.get("periodStart") and chunk["periodStart"] <= params["@pTo"] and chunk["periodEnd"] >= params["@pFrom"]
    ):
        return False
    for lo, hi, column in (("@aFrom", "@aTo", "authoredAt"), ("@uFrom", "@uTo", "uploadedAt")):
        if lo in params and not (chunk.get(column) and params[lo] <= chunk[column] < params[hi]):
            return False
    if any(
        k in params and chunk.get(col) != params[k]
        for k, col in (("@documentId", "documentId"), ("@generation", "generation"))
    ):
        return False
    tags = [v for k, v in params.items() if re.fullmatch(r"@tag\d+", k)]
    return (
        all(t in chunk.get("facets", []) for t in tags)
        and any_of("au", chunk.get("author"))
        and any_of("ft", chunk.get("fileType"))
        and any_of("ck", chunk.get("contentKind"))
    )


class FakeChunkReader:
    """Stands in for Cosmos: the library's chunks that pass the query's filters, in a fixed order.

    Ranking is Cosmos's job and is tested live; this records the query for static checks.
    ``apply_filters = False`` returns chunks whatever the filters say, to test the checks on them.
    """

    def __init__(self, chunks: MemoryChunks) -> None:
        self.chunks = chunks
        self.queries: list[tuple[str, RenderedQuery]] = []
        self.order: list[str] | None = None  # chunk ids, best first; default: insertion order
        self.apply_filters = True

    async def search(self, library_id: str, query: RenderedQuery, cost: QueryCost):
        check_query(query.sql)
        self.queries.append((library_id, query))
        cost.request_charge += 4.0
        params = {p["name"]: p["value"] for p in query.parameters}
        rows = [
            c
            for (lib, _, _), c in self.chunks.items.items()
            if lib == library_id and (not self.apply_filters or matches_filters(c, params))
        ]
        if self.order is not None:
            rank = {cid: i for i, cid in enumerate(self.order)}
            rows = sorted((c for c in rows if c["id"] in rank), key=lambda c: rank[c["id"]])
        top = int(query.sql.split()[2])
        return [{k: c.get(k) for k in FIELDS} for c in rows[:top]]

    async def locations(self, library_id, document_id, generation, indexes, cost):
        return [
            {"chunkIndex": c["chunkIndex"], "location": c.get("location") or {}}
            for (lib, doc, _), c in self.chunks.items.items()
            if (lib, doc) == (library_id, document_id) and c["generation"] == generation and c["chunkIndex"] in indexes
        ]

    async def document(self, library_id, document_id, generation, limit, cost):
        return [
            dict(c)
            for (lib, doc, _), c in self.chunks.items.items()
            if (lib, doc) == (library_id, document_id) and c["generation"] == generation
        ][:limit]

    async def neighbors(self, library_id, document_id, generation, indexes, cost):
        return [
            {"chunkIndex": c["chunkIndex"], "text": c["text"]}
            for (lib, doc, _), c in self.chunks.items.items()
            if (lib, doc) == (library_id, document_id) and c["generation"] == generation and c["chunkIndex"] in indexes
        ]


@dataclass
class Services:
    libraries: MemoryLibraryStore = field(default_factory=MemoryLibraryStore)
    documents: MemoryDocumentStore = field(default_factory=MemoryDocumentStore)
    queue: MemoryQueue = field(default_factory=MemoryQueue)
    blobs: MemoryBlobs = field(default_factory=MemoryBlobs)
    chunks: MemoryChunks = field(default_factory=MemoryChunks)
    embedder: FakeEmbedder = field(default_factory=FakeEmbedder)
    reader: FakeChunkReader = field(init=False)

    def __post_init__(self) -> None:
        self.reader = FakeChunkReader(self.chunks)


def settings(**overrides: str) -> Settings:
    return Settings.from_env({"ENVIRONMENT": "local", "AUTH_MODE": "dev", "DEV_API_KEY": KEY, **overrides})


def api(services: Services | None = None, **overrides: str) -> tuple[TestClient, Services]:
    s = services or Services()
    searcher = Searcher(s.libraries, s.documents, s.reader, s.embedder)
    app = create_app(
        settings(**overrides),
        libraries=s.libraries,
        documents=s.documents,
        queue=s.queue,
        blobs=s.blobs,
        searcher=searcher,
    )
    return TestClient(app, headers=AUTH), s


class FakeChat:
    """Stands in for the chat deployment: answers in order (the last repeats), recording every prompt.

    An answer that is an exception is raised instead. Never a real model call in tests.
    """

    def __init__(self, *answers) -> None:
        self.answers = list(answers) or [{}]
        self.calls: list[list[dict]] = []

    async def complete_json(self, messages):
        from fli.proposals import ChatReply

        self.calls.append(messages)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return ChatReply(answer, prompt_tokens=100, completion_tokens=20)
