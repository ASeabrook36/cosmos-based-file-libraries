"""The exit-check driver runs end to end against the in-memory API, and its record holds metrics only."""

from __future__ import annotations

import json
import sys

from fli.chunking import ApproxTokenizer, Chunker
from fli.worker.handlers import Worker
from scripts import phase1_exit
from tests.fakes import AUTH, api

FILES = {
    "notes.md": b"# Margins\n\nGross margin is revenue minus cost of goods sold, divided by revenue.",
    "gdp.csv": b"country,year,value\nWales,2010,1\nUnited Kingdom,2010,2\n",
    "m.eml": b"From: a@example.org\nSubject: Build\n\nThe build broke after the upgrade.",
    "private-name.txt": b"Mr Darcy is proud. " * 50,
}


def prepare(tmp_path, monkeypatch):
    """The API with an instant worker, a corpus on disk, and the script pointed at both."""
    client, s = api()
    worker = Worker(s.libraries, s.documents, s.chunks, s.blobs, s.queue, s.embedder, Chunker(ApproxTokenizer()))
    real_send = s.queue.send

    async def send_and_process(message, *, session_id):
        await real_send(message, session_id=session_id)
        while s.queue.sent:
            await worker.handle(s.queue.sent.pop(0)[1])

    monkeypatch.setattr(s.queue, "send", send_and_process)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, data in FILES.items():
        (corpus / name).write_bytes(data)
    (tmp_path / "records").mkdir()
    monkeypatch.setattr(phase1_exit, "REPO", tmp_path)
    monkeypatch.setattr(phase1_exit.httpx, "Client", lambda **kw: client)
    monkeypatch.setattr(phase1_exit.time, "sleep", lambda _: None)
    monkeypatch.setenv("DEV_API_KEY", AUTH["Authorization"].split()[1])
    return client, corpus


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["phase1_exit.py", *map(str, args)])
    return phase1_exit.main()


def test_the_exit_script_runs_and_writes_metrics_only(tmp_path, monkeypatch, capsys):
    _, corpus = prepare(tmp_path, monkeypatch)
    assert run(monkeypatch, corpus) == 0
    [record_path] = (tmp_path / "records").glob("phase1-exit-*.json")
    text = record_path.read_text()
    record = json.loads(text)
    assert record["readyDocuments"] == 4 and record["documentsByType"]["eml"] == {"ready": 1}
    personal = record["personal"]
    assert (personal["boundAgentStatus"], personal["otherAgentStatus"]) == (200, 403)
    assert personal["otherAgentCode"] == "library_not_bound_to_agent"
    assert len(record["searches"]) == len(phase1_exit.QUESTIONS)
    assert all(q["allHaveFileName"] for q in record["searches"])  # TXT, MD, EML chunks have no location (spec 4.3)
    for secret in ("private-name", "Darcy", "Gross margin", "How is gross margin", str(tmp_path)):
        assert secret not in text
    assert "private-name.txt" in capsys.readouterr().out  # names go to the operator's terminal only


def test_resume_reuses_the_library_without_uploading_to_it(tmp_path, monkeypatch):
    client, corpus = prepare(tmp_path, monkeypatch)
    assert run(monkeypatch, corpus) == 0
    [shared] = [x for x in client.get("/v1/libraries").json()["libraries"] if x["kind"] == "shared"]
    uploads = []
    real_upload = phase1_exit.upload
    monkeypatch.setattr(phase1_exit, "upload", lambda c, lib, p: uploads.append(lib) or real_upload(c, lib, p))
    assert run(monkeypatch, corpus, "--resume") == 0
    assert [x["id"] for x in client.get("/v1/libraries").json()["libraries"] if x["kind"] == "shared"] == [shared["id"]]
    assert uploads and shared["id"] not in uploads  # only the personal-library check uploads


def test_resume_without_a_library_says_so(tmp_path, monkeypatch):
    _, corpus = prepare(tmp_path, monkeypatch)
    assert run(monkeypatch, corpus, "--resume") == 2
