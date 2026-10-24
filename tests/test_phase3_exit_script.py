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


# -- filters ---------------------------------------------------------------------------------------


def filters_manifest(tmp_path):
    texts = {
        "private-emea.txt": OLD,
        "private-apac.txt": NEW,
        "private-notes.txt": b"Planning notes for the offsite agenda and travel. " * 100,
    }
    for name, data in texts.items():
        (tmp_path / name).write_bytes(data)
    manifest = {
        "documents": [
            {
                "path": "private-emea.txt",
                "author": "Secret Author",
                "periodStart": "2025-10-01",
                "periodEnd": "2025-12-31",
                "tags": [{"key": "region", "value": "secret-emea"}],
            },
            {"path": "private-apac.txt", "tags": [{"key": "region", "value": "secret-apac"}]},
            {"path": "private-notes.txt"},
        ],
        "searches": [
            {
                "question": "secret margin question",
                "filters": {"period": {"from": "2025-10-01", "to": "2025-12-31"}, "tags": ["region=secret-emea"]},
            },
            {"question": "secret author question", "filters": {"authors": ["Secret Author"]}},
        ],
        "retag": {"document": 2, "tag": {"key": "topic", "value": "secret-offsite"}, "question": "secret offsite"},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def run_filters(monkeypatch, manifest, *extra):
    monkeypatch.setattr(sys, "argv", ["phase3_exit.py", "filters", str(manifest), *map(str, extra)])
    return phase3_exit.main()


def test_the_filters_check_passes_and_writes_metrics_only(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    assert run_filters(monkeypatch, filters_manifest(tmp_path)) == 0
    [path] = (tmp_path / "records").glob("phase3-exit-filters-*.json")
    text = path.read_text()
    record = json.loads(text)
    assert record["passed"] and record["documents"] == {"uploaded": 3, "ready": 3}
    first, second = record["searches"]
    assert first["filterKeys"] == ["period", "tags"] and first["mismatches"] == 0 and first["documentsReturned"] == 1
    assert first["missingNotesExpected"] == first["missingNotesFound"] == ["period"]
    assert second["missingNotesFound"] == ["authors"] and first["unfilteredResultCount"] >= first["resultCount"]
    assert record["retag"]["secondsToAppear"] is not None and record["retag"]["secondsToDisappear"] is not None
    for private in ("private-", "secret", "Secret", "Gross margin", "offsite"):
        assert private not in text  # CLAUDE.md rule 12


def test_results_that_ignore_the_filters_fail_the_check(tmp_path, monkeypatch):
    s, _ = prepare(tmp_path, monkeypatch)
    s.reader.apply_filters = False  # the tag never leaves search, so the wait must give up
    assert run_filters(monkeypatch, filters_manifest(tmp_path), "--timeout", 0.5) == 1
    [path] = (tmp_path / "records").glob("phase3-exit-filters-*.json")
    assert json.loads(path.read_text())["searches"][0]["mismatches"] > 0


def test_retag_costs_are_read_from_the_worker_log(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text(
        '2026-10-24 10:00:00,000 INFO fli.worker worker started {"lanes": 8}\n'
        "2026-10-24 10:00:05,000 INFO fli.worker retag done "
        '{"chunks": 42, "documentId": "d-1", "requestCharge": 512.5}\n'
        '2026-10-24 10:00:06,000 INFO fli.worker retag done {"chunks": 9, "documentId": "d-2", "requestCharge": 90.0}\n'
    )
    assert phase3_exit.retag_charges(log, "d-1") == [{"chunks": 42, "requestCharge": 512.5}]
    assert phase3_exit.retag_charges(None, "d-1") == []
