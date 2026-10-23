"""The Phase 3 exit-check driver runs against the in-memory API, and its record holds metrics only."""

from __future__ import annotations

import json
import sys

from fli.chunking import ApproxTokenizer, Chunker
from fli.worker.handlers import Worker
from scripts import phase3_exit
from tests.fakes import AUTH, api

OLD = b"Gross margin in EMEA fell two points in Q4 2025. " * 200
NEW = b"Headcount in APAC grew by forty people in Q1 2026. " * 200


def prepare(tmp_path, monkeypatch):
    """The API with an instant worker, two documents on disk, and the script pointed at both."""
    client, s = api()
    worker = Worker(s.libraries, s.documents, s.chunks, s.blobs, s.queue, s.embedder, Chunker(ApproxTokenizer()))
    real_send = s.queue.send

    async def send_and_process(message, *, session_id):
        await real_send(message, session_id=session_id)
        while s.queue.sent:
            await worker.handle(s.queue.sent.pop(0)[1])

    monkeypatch.setattr(s.queue, "send", send_and_process)
    (tmp_path / "private-old.txt").write_bytes(OLD)
    (tmp_path / "private-new.txt").write_bytes(NEW)
    (tmp_path / "records").mkdir()
    monkeypatch.setattr(phase3_exit, "REPO", tmp_path)
    monkeypatch.setattr(phase3_exit.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(phase3_exit.time, "sleep", lambda _: None)
    monkeypatch.setenv("DEV_API_KEY", AUTH["Authorization"].split()[1])
    return s, worker


def run(tmp_path, monkeypatch):
    argv = [
        "phase3_exit.py",
        "lifecycle",
        tmp_path / "private-old.txt",
        tmp_path / "private-new.txt",
        "--old-question",
        "secret margin question",
        "--new-question",
        "secret headcount question",
        "--after",
        "0",
    ]
    monkeypatch.setattr(sys, "argv", list(map(str, argv)))
    return phase3_exit.main()


def test_the_lifecycle_check_passes_and_writes_metrics_only(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    assert run(tmp_path, monkeypatch) == 0
    [path] = (tmp_path / "records").glob("phase3-exit-lifecycle-*.json")
    text = path.read_text()
    record = json.loads(text)
    assert record["passed"] and record["replaceGeneration"] == 2
    assert record["replacement"]["oldGenerationAfterCommit"] == 0 and record["replacement"]["searchesAfterCommit"]
    assert record["afterReplace"]["blobPathHasGeneration"] and record["afterReplace"]["sha256MatchesNew"]
    assert record["deletion"] == {
        "documentResultsAfterDelete": 0,
        "libraryStatusAfterDelete": 404,
        "libraryCodeAfterDelete": "library_not_found",
    }
    for private in ("private-old", "secret", "Gross margin", "Headcount"):
        assert private not in text  # CLAUDE.md rule 12


def test_old_content_returned_after_the_commit_fails_the_check(tmp_path, monkeypatch):
    _, worker = prepare(tmp_path, monkeypatch)

    async def no_purge(*args, **kwargs):
        return None

    monkeypatch.setattr(worker, "purge_document", no_purge)
    monkeypatch.setattr("fli.search.filter_visible", lambda chunks, documents, limit: list(chunks)[:limit])
    assert run(tmp_path, monkeypatch) == 1
    [path] = (tmp_path / "records").glob("phase3-exit-lifecycle-*.json")
    record = json.loads(path.read_text())
    assert not record["passed"] and record["replacement"]["oldGenerationAfterCommit"] > 0
