"""Prepare and locally qualify a bounded granular-navigation regression pack.

This helper has NO live execution path. It neither loads Azure credentials nor
contacts cloud services. Its private plan specifies the gates a separately reviewed
live executor must satisfy. Local qualification uses the ordinary API/worker and
in-memory services; its captured tool replies are real serializations, not evidence
of model judgment, remote publication, broker behavior or search quality.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

from docx import Document

from fli.ingestion import profile
from scripts.retrieval_controls import private_path

REPO = Path(__file__).resolve().parents[1]
PROFILE = "custom-granular-v1"
MAIN = "1ddc4386361446a5122ce936cdb3ba28aa4e5919"
SOURCE_REPAIR = "33402d3bf66c5ad6ba2d0b12c0c46cba66695aff"
CONTEXT_REPAIR = "44708040dce0a6d4cd5b44205a07a5d7e54404a7"
LIMITS = {
    "libraries": 3,
    "documents": 4,
    "ingestGenerations": 5,
    "publishedChunks": 64,
    "sourceBytesEach": 1048576,
    "sourceEmbeddingTokens": 50000,
    "logicalEmbeddingRequests": 20,
    "embeddingAttemptsEach": 3,
    "modelTurns": 6,
    "logicalModelCalls": 36,
    "physicalModelAttempts": 48,
    "modelContextAllowance": 200000,
    "modelOutputAllowance": 8000,
    "inputTokensIncludingUnknownReservations": 2000000,
    "outputTokensIncludingUnknownReservations": 200000,
    "executionSeconds": 1800,
    "cleanupSeconds": 600,
    "lateAbsenceCheckSeconds": 60,
}
FILES = ("source.docx", "replacement.docx", "source.md")
LIVE_GATES = (
    "announced exclusive cloud window and existing isolated-resource authorization",
    "fresh actual container policy/incarnation binding to the passing canary",
    "fresh health check; inherited canary alone does not establish current health",
    "fresh actual embedding deployment identity/version/SKU and successful credential checks",
    "explicit protected library inventory; no scaling or completed experiment scope",
    "new exact-owned mutation ledger before every uncertain create/write",
    "reviewed failure recovery and cleanup executor; no ambiguous-create retries",
    "new source/profile/code/control hashes; never bypass frozen experiment guards",
    "for model turns only: fresh existing answering deployment/version binding and bounded usage meter",
)
CLEANUP = (
    "Archive private raw source, tool replies, execution ledger and usage before cleanup.",
    "Quiesce owned writers; never drain a shared queue or stop another worker.",
    "Verify current state against exact owned IDs, generations and immutable artifact hashes before deletes.",
    "Unknown state, unexpected rows or ambiguous writes stop automatic cleanup; retain the ledger for review.",
    "Use the normal fenced lifecycle only for owned libraries/documents; no account/container deletion.",
    "Require exact point-read 404s, owned scoped TOP 1 queries and exact owned Blob-prefix absence.",
    "Repeat absence checks after 60 seconds and verify protected scopes remain unchanged.",
    "Report soft-delete/backup retention and indefinitely late output as outside absence proof.",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, value):
    """Exclusive, flushed private artifact; no overwrite/resume ambiguity."""
    path = private_path(path)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n"
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def source_hashes():
    paths = [
        *REPO.glob("src/**/*.py"),
        *REPO.glob("cosmos/*.json"),
        REPO / "scripts/qualify_granular_navigation.py",
        REPO / "tests/fakes.py",
        REPO / "pyproject.toml",
        REPO / "requirements-local.lock",
    ]
    return {str(p.relative_to(REPO)): sha(p) for p in sorted(paths)}


def fixtures():
    """Small authored mechanics fixtures, outside the proposed confirmation pack."""
    result = {}
    for name, label in (("source.docx", "amber"), ("replacement.docx", "violet")):
        document = Document()
        document.add_heading("Operating rules", level=1)
        document.add_paragraph(f"The {label} recorder stores each inspection result.")
        document.add_heading("Sampling", level=2)
        document.add_paragraph("Collect two samples before opening the valve.")
        document.add_heading("Exceptions", level=3)
        document.add_paragraph("Do not sample a damaged vessel.")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Condition"
        table.cell(0, 1).text = "Action"
        table.cell(1, 0).text = "Cold storage"
        cell = table.cell(1, 1)
        cell.paragraphs[0].text = "Keep the vessel sealed."
        cell.add_paragraph("Record the temperature.", style="List Bullet")
        cell.add_paragraph("Notify the supervisor.", style="List Bullet")
        cell.add_paragraph("These steps apply only while the vessel is in cold storage.")
        document.add_heading("Reporting", level=1)
        document.add_paragraph("Submit the record on the next working day.")
        stream = io.BytesIO()
        document.save(stream)
        result[name] = stream.getvalue()
    result["source.md"] = (
        b"# Field notes\n\nKeep the original field record.\n\n"
        b"## Sampling\n\nCollect two samples.\n\n"
        b"### Exceptions\n\nDo not sample a damaged vessel.\n\n"
        b"## Reporting\n\nSubmit the record on the next working day.\n"
    )
    return result


def prepare(output):
    root = private_path(output)
    recipe = profile(PROFILE)
    require(not root.exists(), "prepare requires a new private directory")
    require(not (REPO / "src/fli_demo/context_budget.py").exists(), "context repair must remain a separate track")
    source_files = fixtures()
    require(set(source_files) == set(FILES), "qualification source inventory differs")
    require(all(len(data) <= LIMITS["sourceBytesEach"] for data in source_files.values()), "source byte ceiling")
    plan = {
        "schema": "fli.granular-navigation.qualification-plan.v1",
        "status": "prepared_not_executed",
        "baseline": {"main": MAIN, "sourceOnlyRepair": SOURCE_REPAIR, "excludedContextRepair": CONTEXT_REPAIR},
        "candidateCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "candidateSourceSha256": source_hashes(),
        "recipe": recipe,
        "limits": LIMITS,
        "contextAccounting": "legacy-v1",
        "searchMode": "vector",
        "selectors": "handles",
        "evidenceView": "compact",
        "modelExecution": "disabled; requires separately reviewed live executor and fresh model binding",
        "sources": [
            {"role": name, "path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in source_files.items()
        ],
        "plannedNavigationCases": [
            "DOCX paragraph selection and containing authored section expansion",
            "DOCX table cell selection preserving list items and separate qualifier paragraph",
            "DOCX section versus subtree selection with authored subsections",
            "Selected region expansion to the complete versioned document",
            "Markdown authored hierarchy and exact-source selection",
            "Unavailable page/chapter/passage mappings disclosed without guessed coordinates",
        ],
        "plannedLifecycleCases": [
            "immutable source publication and exact retry",
            "personal binding and foreign-document refusal",
            "metadata edits preserve source identity",
            "pending replacement serves old source; committed replacement stales old selectors",
            "post-I/O mutation races with independent clients",
            "deletion refuses reads and purges only owned generations/artifacts",
        ],
        "liveExecutorImplemented": False,
        "livePrerequisites": list(LIVE_GATES),
        "liveComposition": (
            "Candidate API and Worker injected with real Cosmos/Blob/embedding services plus a private owned-message "
            "ledger. No hosted application deployment, shared-queue receiver or provider switch. Broker redelivery "
            "and hosted execution remain untested by that composition."
        ),
        "cleanupContract": list(CLEANUP),
        "usageContract": {
            "providerUsage": "record returned counters; retain unknown failed-attempt reservations",
            "localTokens": "label serialization estimates separately from provider input and billing",
            "fakeCounters": "never publish fake-service RU or vectors as measured cloud performance",
        },
        "largerConfirmationLaunched": False,
        "completedExperimentsModified": False,
    }
    root.mkdir(parents=True, mode=0o700)
    for name, data in source_files.items():
        with (root / name).open("xb") as handle:
            os.chmod(root / name, 0o600)
            handle.write(data)
    save_new(root / "plan.json", plan)
    return public_plan(plan, sha(root / "plan.json"))


def public_plan(plan, plan_hash):
    return {
        "schema": "fli.granular-navigation.preparation.v1",
        "status": "prepared_not_executed",
        "planSha256": plan_hash,
        "profile": plan["recipe"]["id"],
        "sourceCount": len(plan["sources"]),
        "limits": plan["limits"],
        "liveExecutorImplemented": False,
        "cloudCalls": 0,
        "modelCalls": 0,
        "largerConfirmationLaunched": False,
    }


def verify(state):
    root = private_path(state)
    plan = json.loads((root / "plan.json").read_text())
    require(plan.get("schema") == "fli.granular-navigation.qualification-plan.v1", "unknown qualification schema")
    require(plan.get("limits") == LIMITS, "qualification ceilings changed")
    require(plan.get("recipe") == profile(PROFILE), "profile recipe changed; prepare a new qualification")
    require(plan.get("candidateSourceSha256") == source_hashes(), "candidate code changed; prepare a new qualification")
    require(plan.get("contextAccounting") == "legacy-v1", "context repair is a separate qualification")
    require(plan.get("searchMode") == "vector", "retrieval tuning is outside this qualification")
    require(plan.get("liveExecutorImplemented") is False, "this helper has no live executor")
    entries = plan.get("sources", [])
    require(len(entries) == len(FILES) and {r.get("path") for r in entries} == set(FILES), "source inventory differs")
    for source in entries:
        path = root / source["path"]
        require(path.resolve().parent == root, "source path escapes private state")
        require(path.stat().st_size == source["bytes"] <= LIMITS["sourceBytesEach"], "source size differs")
        require(sha(path) == source["sha256"], "source bytes changed; prepare a new qualification")
    return root, plan


class LocalQueueCeiling:
    """Drain only the local fake queue; never receive from a Service Bus queue."""

    def __init__(self, services, worker, owned):
        self.services, self.worker, self.owned = services, worker, owned
        self.processed = 0

    async def drain(self):
        while self.services.queue.sent:
            library, message = self.services.queue.sent[0]
            require(library in self.owned and message["libraryId"] == library, "foreign local message refused")
            require(self.processed < 100, "local message ceiling")
            self.services.queue.sent.pop(0)
            await self.worker.handle(message)
            self.processed += 1
        require(len(self.services.chunks.items) <= LIMITS["publishedChunks"], "local chunk ceiling")


async def qualify_local(state):
    """Actual tool serialization and publication mechanics with in-memory services."""
    from fli.chunking import ApproxTokenizer, Chunker
    from fli.worker.handlers import Worker
    from fli_demo.client import IndexClient
    from tests.fakes import FakeEmbedder, api

    root, plan = verify(state)
    require(not (root / "local-started.json").exists(), "local qualification is single-use; prepare a new directory")
    save_new(root / "local-started.json", {"planSha256": sha(root / "plan.json"), "live": False})
    client, services = api(AOAI_EMBEDDING_MODEL_REVISION="local-mechanics-only")
    worker = Worker(
        services.libraries,
        services.documents,
        services.chunks,
        services.blobs,
        services.queue,
        FakeEmbedder(),
        Chunker(ApproxTokenizer()),
        stale_read_pause=0,
        embedding_model_revision="local-mechanics-only",
    )
    owned, captures, checks = [], [], []
    drain = LocalQueueCeiling(services, worker, owned)
    index = IndexClient("http://testserver", "k", http=client, selector_mode="handles")

    def capture(role, payload):
        captures.append({"role": role, "response": payload})
        return payload.get("evidence", payload)

    def create(kind):
        body = {"kind": kind, "name": "Granular local qualification", "ingestionProfile": PROFILE}
        if kind == "personal":
            body["boundAgentId"] = "owner"
        response = client.post("/v1/libraries", json=body)
        response.raise_for_status()
        library = response.json()["id"]
        owned.append(library)
        require(len(owned) <= LIMITS["libraries"], "local library ceiling")
        return library

    def upload(library, name):
        response = client.post(f"/v1/libraries/{library}/documents", files={"file": (name, (root / name).read_bytes())})
        response.raise_for_status()
        return response.json()["documentId"]

    try:
        shared, personal, foreign = create("shared"), create("personal"), create("shared")
        documents = [(shared, upload(shared, name), name) for name in ("source.docx", "source.md")]
        documents.append((personal, upload(personal, "source.docx"), "source.docx"))
        await drain.drain()
        checks.append("source_publication")
        saved = []
        for library, document, name in documents:
            record = await services.documents.get(library, document)
            require(record["status"] == "ready", "source publication failed")
            require(record["fullText"]["generation"] == record["activeGeneration"] == 1, "source generation differs")
            tools = index.tools(library, "owner")
            capture(name + ":tools", {"tools": tools})
            outline = capture(
                name + ":outline",
                index.read_documents(library, "owner", mode="outline", documents=[{"documentId": document}]),
            )
            rows = outline["documents"][0]["outline"]
            require(rows and all("readSelector" in row for row in rows), "usable outline selectors unavailable")
            selected = capture(name + ":selected", index.read_documents(library, "owner", **rows[-1]["readSelector"]))
            require(selected["selectionComplete"] and not selected["complete"], "selected completeness differs")
            require(all(s.get("citation") for s in selected["documents"][0]["sections"]), "selected citations missing")
            full = capture(name + ":full", index.read_documents(library, "owner", documents=[{"documentId": document}]))
            require(full["complete"], "complete source refused")
            saved.append((library, document, rows[-1]["readSelector"], record["fullText"]))
        checks.append("actual_outline_selected_full_tool_envelopes")
        # Copy only handles returned by the normal adapter, as an agent would.
        # No text search, inferred coordinates or private structure side channel.
        library, document, _, _ = saved[0]
        outline = capture(
            "granular:root",
            index.read_documents(library, "owner", mode="outline", documents=[{"documentId": document}]),
        )
        pending = list(outline["documents"][0]["outline"])
        nodes = {}
        while pending:
            node = pending.pop(0)
            key = node["navigationNodeId"]
            require(key not in nodes and len(nodes) < 64, "outline cycle or node ceiling")
            nodes[key] = node
            require(
                node.get("sizeEstimateScope") and node["subtreeTokens"] >= node["sectionTokens"], "size scope missing"
            )
            if node["hasChildren"]:
                children = capture("granular:drill", index.read_documents(library, "owner", **node["outlineSelector"]))
                require(not children["documents"][0]["hasMore"], "tiny fixture unexpectedly needs pagination")
                pending.extend(children["documents"][0]["outline"])
        section = next(node for node in nodes.values() if node.get("title") == "Operating rules")
        own = capture("granular:section", index.read_documents(library, "owner", **section["readSelector"]))
        subtree = capture("granular:subtree", index.read_documents(library, "owner", **section["subtreeSelector"]))
        require(
            len(own["documents"][0]["sections"]) < len(subtree["documents"][0]["sections"]),
            "section and subtree scopes are not distinct",
        )
        cell = [node for node in nodes.values() if node["navigationKind"] == "tableCell"][-1]
        cell_read = capture("granular:cell", index.read_documents(library, "owner", **cell["readSelector"]))
        cell_parts = cell_read["documents"][0]["sections"]
        require(len(cell_parts) == 4, "table cell paragraph boundaries lost")
        require(
            "<list-item " in cell_parts[1]["text"]
            and "<list-item " in cell_parts[2]["text"]
            and "<list-item " not in cell_parts[-1]["text"]
            and "These steps apply only" in cell_parts[-1]["text"],
            "qualifier/list relationship lost",
        )
        paragraph = [node for node in nodes.values() if node.get("parentNodeId") == cell["navigationNodeId"]][-1]
        small = capture("granular:paragraph", index.read_documents(library, "owner", **paragraph["readSelector"]))
        require(len(small["documents"][0]["sections"]) == 1, "paragraph selection broadened")
        containing = capture(
            "granular:containing-section",
            index.read_documents(library, "owner", **paragraph["containingSectionSelector"]),
        )
        require(len(containing["documents"][0]["sections"]) > len(cell_parts), "containing section did not expand")
        whole = capture("granular:whole-document", index.read_documents(library, "owner", **paragraph["fullSelector"]))
        require(whole["complete"], "full expansion did not produce complete versioned source")
        checks.append("paragraph_cell_section_subtree_and_parent_document_expansion")
        pin = section["documentSelector"]
        passages = capture("granular:search", index.search(library, "owner", "sampling", document=pin, top=1))
        hit = passages["results"][0]
        require(hit["passageToSourceMapping"] == "recorded_containing_source_atom", "recorded mapping missing")
        require(
            hit["passageMappingVerification"] == "publication_bound; revalidated_on_expansion",
            "mapping verification boundary missing",
        )
        passage_source = capture(
            "granular:passage-source", index.read_documents(library, "owner", **hit["readSelector"])
        )
        require(passage_source["selectionComplete"], "verified passage source unavailable")
        page = client.post(
            f"/v1/libraries/{library}/documents:read",
            json={"mode": "selected", "documents": [{**pin, "pages": [1]}]},
            headers={"X-Agent-Id": "owner"},
        )
        require(page.status_code == 409 and page.json()["error"]["code"] == "source_scope_unavailable", "invented page")
        capture("granular:unavailable-page", page.json())
        # An explicitly unmapped passage must disclose the absence, even if it
        # retains other source-looking coordinates. This mutation is fake-only.
        stored_hit = next(row for row in services.chunks.items.values() if row["documentId"] == document)
        original_location = stored_hit["location"]
        stored_hit["location"] = {k: v for k, v in original_location.items() if k != "sourceAtomId"}
        try:
            missing = capture(
                "granular:unavailable-mapping", index.search(library, "owner", "sampling", document=pin, top=1)
            )
            hit = missing["results"][0]
            require(hit["passageToSourceMapping"] == "unavailable" and "readSelector" not in hit, "guessed mapping")
        finally:
            stored_hit["location"] = original_location
        checks.append("verified_passage_mapping_and_unavailable_scope_disclosure")
        refused = capture(
            "atomic-budget-refusal",
            index.read_documents(
                shared, "owner", documents=[{"documentId": d} for _, d, _ in documents[:2]], maxTokens=1
            ),
        )
        require(refused["disposition"] == "budget_exceeded", "budget refusal missing")
        require(all("sections" not in d for d in refused["documents"]), "partial source escaped atomic refusal")
        checks.append("atomic_multidocument_budget_refusal")
        library, document, old_selector, descriptor = saved[0]
        await worker.ingest(library, document, 1)
        require((await services.documents.get(library, document))["fullText"] == descriptor, "retry changed source")
        await drain.drain()
        checks.append("immutable_retry")
        response = client.patch(
            f"/v1/libraries/{library}/documents/{document}", json={"author": "Qualification author"}
        )
        response.raise_for_status()
        await drain.drain()
        require((await services.documents.get(library, document))["fullText"] == descriptor, "metadata changed source")
        checks.append("metadata_preserves_source")
        response = client.put(
            f"/v1/libraries/{library}/documents/{document}/content",
            files={"file": ("replacement.docx", (root / "replacement.docx").read_bytes())},
        )
        response.raise_for_status()
        capture("pending-old-selection", index.read_documents(library, "owner", **old_selector))
        await drain.drain()
        stale = client.post(
            f"/v1/libraries/{library}/documents:read?selectors=handles&view=compact",
            json=old_selector,
            headers={"X-Agent-Id": "owner"},
        )
        require(stale.status_code == 409, "stale selector was accepted")
        capture("stale-selector-refusal", stale.json())
        checks.append("pending_replacement_and_stale_selection")
        denied = client.post(
            f"/v1/libraries/{personal}/documents:read",
            json={"documents": [{"documentId": documents[-1][1]}]},
            headers={"X-Agent-Id": "other"},
        )
        require(denied.status_code == 403, "personal binding not enforced")
        denied = client.post(
            f"/v1/libraries/{foreign}/documents:read", json=old_selector, headers={"X-Agent-Id": "owner"}
        )
        require(denied.status_code == 404, "foreign document selection was accepted")
        checks.append("personal_and_foreign_scope_denial")
        response = client.delete(f"/v1/libraries/{library}/documents/{document}")
        require(response.status_code == 202, "deletion did not fence source")
        deleted = client.post(
            f"/v1/libraries/{library}/documents:read", json=old_selector, headers={"X-Agent-Id": "owner"}
        )
        require(deleted.status_code == 404, "deleting source remained readable")
        await drain.drain()
        require(await services.documents.get(library, document) is None, "deleted record remains")
        require(not await services.blobs.list_prefix(f"{library}/{document}/"), "deleted source artifacts remain")
        checks.append("deletion_fence_and_owned_purge")
    finally:
        # All services are in-memory. This is not a cloud cleanup implementation.
        for library in owned:
            response = client.delete(f"/v1/libraries/{library}")
            require(response.status_code in (202, 404), "local owned cleanup refused")
        await drain.drain()
        client.close()
        save_new(root / "local-tool-responses.json", captures)
    require(not services.chunks.items and not services.blobs.blobs, "local owned artifacts remain")
    receipt = {
        "schema": "fli.granular-navigation.local-qualification.v1",
        "status": "passed",
        "live": False,
        "planSha256": sha(root / "plan.json"),
        "candidateSourceSha256": plan["candidateSourceSha256"],
        "privateToolResponsesSha256": sha(root / "local-tool-responses.json"),
        "checks": checks,
        "capturedToolReplies": len(captures),
        "cloudCalls": 0,
        "modelCalls": 0,
        "providerUsage": None,
        "cloudRequestCharge": None,
        "localCleanupPassed": True,
        "liveExecutorImplemented": False,
        "interpretation": (
            "Actual candidate API/worker mechanics with in-memory services. No ranking or model-quality evidence."
        ),
        "remainingLiveGates": list(LIVE_GATES),
    }
    save_new(root / "local-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare").add_argument("--output", type=Path, required=True)
    for action in ("verify", "qualify-local"):
        sub.add_parser(action).add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.output)
    elif args.action == "verify":
        root, plan = verify(args.state)
        result = public_plan(plan, sha(root / "plan.json"))
    else:
        result = asyncio.run(qualify_local(args.state))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
