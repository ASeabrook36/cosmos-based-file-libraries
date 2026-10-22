# Build plan

*Version 0.1, 2026-10-22.*

The plan is ordered to reach something demonstrable as early as possible:

- Phases 0–2 are the critical path to a working demo.
- Phases 3–4 make the demo convincing: images, filters, and proof that the library ceiling is gone.
- Phase 5 is the road to production.

Each phase ends with an exit check that must pass before the next phase starts.

## Phase 0: Foundations and the canary

The first build of this exact Cosmos configuration happens here (`evidence.md` N1), so this phase proves the configuration works before any application code depends on it.

**Build**

- Repository scaffold:
  - Python 3.12, one package with two entry points (`api`, `worker`);
  - FastAPI, `azure-cosmos` 4.9 or later, `azure-identity`, `azure-storage-blob`, `azure-servicebus`;
  - `pytest`, `ruff`.
- Bicep for one organization's deployment:
  - Cosmos account: serverless for the demo, `EnableNoSQLVectorSearch`, `disableLocalAuth`;
  - database `fli` and the three containers, loaded from `cosmos/*.json`;
  - storage account with the `files` container;
  - Service Bus Standard with the session-enabled `ingest` queue;
  - Azure OpenAI deployments: `text-embedding-3-large` at 3,072 dimensions, plus a vision-capable chat model;
  - Document Intelligence and Application Insights;
  - one user-assigned managed identity with the roles in `spec.md` §3, including the Cosmos data-plane role assignment.
- `scripts/canary.py`, run against a freshly deployed account:
  1. Write, read and query a record in each container.
  2. Load about 1,500 chunks of real text, embedded with the real model, into one test library. Use real text: random vectors are invalid test data (`evidence.md` E14).
  3. Run 20 vector-only and 20 hybrid searches using the float16 query path (`spec.md` §8 step 3).
  4. Assert:
     - no null scores;
     - the index is engaged: request charge in tens of RU, and the retrieved-document count from the query metrics far below 1,500;
     - recall@10 of at least 0.95 against exact search (`VectorDistance(…, true, …)` forces brute force).
  5. Run one search that declares `float32` and record that it scans. This documents the trap.
  6. Write the results to `records/canary-<UTC timestamp>.json`.

**Exit check:** the canary passes on the deployed account and its record is committed.

**If it fails:**
- Recreate the account and rerun. Replacing an account is a normal recovery step (E17).
- If the spherical quantizer or float16 is the cause, fall back to the product quantizer (generally available) and record the change in `spec.md`.

## Phase 1: The core path

**Build**

- **Authentication:** Entra bearer validation with the app role, plus a `dev` mode that refuses to start outside `ENVIRONMENT=local`.
- **Libraries:** the endpoints in `spec.md` §6.1, with the personal-library rules of §5.
- **Documents:** upload → Blob → queue → worker → `ready`, for PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, HTML and EML. Start from the extraction, OCR, chunking, embedding and query modules in `inherited/` (`spec.md` §13), and move them into the application package with their tests. Generations and deterministic chunk ids from the start.
- **Search:** `spec.md` §8 in full. That includes the float16 query conversion, hybrid SQL, filters, the generation check, neighbours and coverage.
- **Tool:** `GET …/tool` (`spec.md` §6.4).

**Unit tests that must exist**

- Every rendered vector or hybrid query has `TOP`, declares `float16`, filters `c.libraryId` by equality on one value, and never selects `c.embedding`. Build these as static checks on the query builder; `inherited/query.py` `check_query` already does.
- float16 conversion: rounding, and flushing values below 2⁻¹⁴ to 0.
- A personal library searched with another agent's id returns `403`. `PATCH` of `kind` or `boundAgentId` returns `400`.
- Chunk ids are deterministic. A retried ingest overwrites rather than duplicates.
- The generation check drops chunks of replaced and deleted documents.

**Exit check**

- Twenty real documents uploaded to a shared library reach `ready`.
- REST searches return relevant chunks with file names and locations.
- A personal-library search with the wrong agent id is refused.

## Phase 2: The demo platform and its agents

The demo needs something playing the platform's role. Build a small Streamlit app that does exactly what the real platform will do, and nothing the index should do.

**Build**

- **Admin page:** create shared libraries, upload files, watch status, edit description and tags.
- **Agents page:**
  - official agents with any number of shared libraries attached;
  - personal agents, each with its own personal library.
  - The attachment list lives in the demo app, as it will live in the real platform. The index never stores it.
  - The attach screen for official agents lists only shared libraries.
  - A clearly labelled "force attach via API" control demonstrates that the index refuses a personal library anyway.
- **Chat page:**
  - pick an agent;
  - the agent loop gives the chat model one tool per attached library, from `GET …/tool`;
  - show which library tool was called with which filters, the request charge and latency, the citations, and any images.
  - The demo uses an Azure OpenAI chat deployment with function calling. In production the platform's own model does this.

**Exit check:** demo script steps 2, 3, 5 and 6 (below) run end to end.

## Phase 3: Images and metadata

**Build**

- **Images:** standalone uploads and images embedded in DOCX and PPTX; skip rules and cap; vision descriptions; image chunks; short-lived read links. Render images in the chat, and pass them into the model's context when it accepts images.
- **Proposals (enhancement 1):** per-document tag, period and author proposals, with an accept or edit screen. Per-library description proposals.
- **Filters:** all filters in search and in the tool definition (`period`, `authoredBetween`, `uploadedBetween`, `authors`, `tags`, `fileTypes`, `contentKinds`). Add the `retag` job.
- **Lifecycle:** document replacement and deletion; library deletion.

**Exit check**

- Demo script step 4 works.
- A period-plus-tag filtered question returns only matching documents, and the coverage notes say what was excluded.
- After replacing a document, its old content is never returned.

## Phase 4: Scale proof and routing telemetry

**Build**

- `scripts/scale_demo.py`:
  - Create 1,000 personal libraries, five times AI Search's 200-index limit on S2 and S3, each bound to a synthetic agent and holding 1–5 small real documents (30–150 chunks).
  - Create three large shared libraries of 10,000+ chunks.
  - Measure per-search request charge and latency on a sample of small and large libraries, before and after loading the 1,000.
  - Write a one-page report.
  - At about 100,000 chunks, expect roughly $2–3 of Cosmos writes on serverless plus embedding cost, and about 75 minutes of loading, because a serverless container writes about 22 chunks per second.
- **Routing telemetry (enhancement 3):** `library_search` and `search_usage` events, the usage endpoint, the saved Log Analytics query, and `GET …/routing-report`. Show it in the demo app.

**Exit check**

- The report shows more than 200 libraries in one container.
- Per-search cost on a given library is the same, within run-to-run variation, before and after the 1,000 were added.

## Phase 5: Toward production

These come after the demo, in roughly this order:

1. **Infrastructure:**
   - Provisioned autoscale. Convert the demo account (one-way) or deploy a fresh production account.
   - Deploy to Container Apps.
   - App registration and app role for the real platform.
2. **Platform integration:**
   - Replace the Streamlit stand-in with calls from the real platform: library lifecycle, uploads, tool attachment, search, usage reports.
   - Run the personal-agent lifecycle end to end: create a personal agent, create its library, then delete both.
3. **Quality:**
   - An evaluation set of 50–100 real questions with known answers over a realistic corpus.
   - Tune `top`, the over-fetch factor and the RRF weights against it.
   - Measure hybrid against vector-only (`evidence.md` N3).
4. **Open measurements:**
   - Library-only versus class-plus-library sharding on large libraries (N2).
   - A multi-partition container test (N4).
   - Microsoft's confirmation for vector search on containers with hierarchical partition keys (N6).
5. **Coverage:** PDF figure extraction (Document Intelligence layout), email attachments as child documents, conversion of legacy Office formats.
6. **Operations:**
   - Dead-letter alerting.
   - A job that verifies purges actually happened.
   - A backup and restore drill.
   - A rebuild-from-originals script for future embedding-model changes (E16).

## The demo script

About ten minutes. Each step names the phase that delivers it.

1. **The ceiling is gone** (Phase 4). "Azure AI Search allows 200 indexes per service on S2 and S3, and we use one per library. Here are 1,000 libraries in one Cosmos container, and a search costs the same with 10 libraries or 1,000."
2. **An admin builds a library** (Phase 2, proposals in Phase 3).
   - Create "Finance — Quarterly Reports" and upload a dozen PDFs, decks and spreadsheets.
   - Watch them reach `ready`.
   - The index proposes a description and tags; the admin accepts them.
3. **An official agent uses it** (Phase 2).
   - The "Finance Assistant" agent has three shared libraries attached.
   - Ask: "What drove the change in gross margin in EMEA in Q4 2025?"
   - Show that the agent chose the finance library's tool, applied the period and region filters, and answered with citations (file and slide). Show the search's request charge and latency.
4. **Images** (Phase 3). Ask: "Show me the regional margin chart." The answer includes the chart from the deck, shown in the chat.
5. **A personal agent** (Phase 2). A user builds a personal agent, uploads their own notes, and asks a question only those notes can answer.
6. **Personal stays personal** (Phase 2).
   - The admin's attach screen does not offer the personal library.
   - Forced through the API, the index refuses it.
7. **Which libraries earn their keep** (Phase 4). The routing report: searches, empty results and cited results per library.

## Demo corpus

Use 30–60 real documents in one believable domain:

- PDFs and decks with charts;
- at least one spreadsheet;
- a few emails (`.eml`);
- standalone images;
- 3–5 personal notes for the personal agent.

Use material you are allowed to use, such as public company reports, public statistics publications, or documents written for the demo. Do not use placeholder or generated filler text: it makes search quality meaningless (E14) and the demo unconvincing.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| The spherical quantizer or float16 fails to create, or fails the canary | Recreate the account. Use the product quantizer and float32, record the change, and keep going |
| Hybrid ranking is worse than vector-only on the evaluation set | A configuration flag for vector-only search; tune RRF weights later |
| Throttling during demo-day uploads on serverless | Load the demo corpus beforehand, or run the demo on a provisioned account |
| Azure OpenAI quota (embedding tokens per minute) slows bulk loads | Batch, back off on 429, request more quota |
| Image description cost | The skip rules and per-document cap in `spec.md` §7.5 |
