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


def test_the_exit_script_runs_and_writes_metrics_only(tmp_path, monkeypatch, capsys):
    client, s = api()
    worker = Worker(s.libraries, s.documents, s.chunks, s.blobs, s.queue, s.embedder, Chunker(ApproxTokenizer()))
    real_send = s.queue.send

    async def send_and_process(message, *, session_id):  # an instant worker
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
    monkeypatch.setattr(sys, "argv", ["phase1_exit.py", str(corpus)])

    assert phase1_exit.main() == 0
    [record_path] = (tmp_path / "records").glob("phase1-exit-*.json")
    text = record_path.read_text()
    record = json.loads(text)
    assert record["readyDocuments"] == 4 and record["documentsByType"]["eml"] == {"ready": 1}
    assert record["personal"] == {
        "boundAgentStatus": 200,
        "otherAgentStatus": 403,
        "otherAgentCode": "library_not_bound_to_agent",
    }
    assert len(record["searches"]) == len(phase1_exit.QUESTIONS)
    assert all(q["allHaveFileName"] for q in record["searches"])  # TXT, MD, EML chunks have no location (spec 4.3)
    for secret in ("private-name", "Darcy", "Gross margin", "How is gross margin", str(tmp_path)):
        assert secret not in text
    assert "private-name.txt" in capsys.readouterr().out  # names go to the operator's terminal only
