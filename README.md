# File Library Index

*Working name.*

A search service that stores agent file libraries in Azure Cosmos DB instead of Azure AI Search.

- Admins and users upload files into libraries.
- The service extracts the text and describes the images, embeds both, and stores everything in one Cosmos container.
- Agents search a library through a tool the platform attaches to them.
- Personal agents each get their own library, which no other agent can search.

It removes the limit that prompted it. Azure AI Search allows 200 indexes per service on the S2 and S3 tiers, and the current design uses one index per library. Here a library is a value stored on each record, not an object the service counts, so the number of libraries has no design limit.

## Why one Cosmos container

Today every library is its own search index, and the search service counts indexes. Here every library lives in one Cosmos DB container: each passage of each document is stored with its text, its meaning as a vector, and the ID of its library. Cosmos keeps each library's passages together and builds a separate vector index for each library inside that one container. A search names one library, so it reaches only that library's passages and that library's index.

```mermaid
flowchart LR
    subgraph today["Today: Azure AI Search, one index per library"]
        direction TB
        svc["Search service<br/>S2 or S3 tier"]
        svc --> t1["Index for library 1"]
        svc --> t2["Index for library 2"]
        svc --> t3["…"]
        svc --> t200["Index for library 200"]
        t200 -.- cap["Library 201 has nowhere to go:<br/>the service allows 200 indexes"]
    end

    subgraph proposed["Proposed: one Cosmos DB container for every library"]
        direction TB
        question["An agent searches one library<br/>by meaning, optionally filtered by<br/>period, author, tags or file type"]
        subgraph container["One container. Each passage stores its text, its vector and its library ID"]
            direction LR
            la["<b>Library A</b><br/>its passages, stored together<br/>its own vector index"]
            lb["<b>Library B</b><br/>its passages, stored together<br/>its own vector index"]
            ln["<b>Library 10,000</b><br/>a new library is new records,<br/>not a new index"]
        end
        answer["The best-matching passages,<br/>with file and page,<br/>from current versions only"]
        question -- "the library ID sends the search<br/>to that library's data only" --> la
        la -- "one query ranks by meaning<br/>and applies the filters" --> answer
    end

    today ~~~ proposed
```

What this arrangement buys, with the measurements behind it (`docs/evidence.md`):

| Goal | How the container delivers it | Measured |
|---|---|---|
| **Accurate semantic search** | Each library has its own vector index, so a search compares the question's meaning against that library's passages only. Filters run inside the same query, and a replaced document's old version is never returned. | Per-owner vector indexes kept recall@10 at 0.991 against exact search (E2). Half-precision vectors lost no accuracy: 0.980 against 0.980 (E7). |
| **Fast** | The library ID leads the container's partition key and is its vector shard key, so a search goes to one library's data and one index, in one round trip. | 97–127 ms server latency at the 95th percentile, against 143–295 ms without per-owner indexes (E2). |
| **Cheap** | A search's cost follows the size of the library searched, not of the container. Half-precision vectors and serverless billing keep the rest small. | 54–69 RU per search at the 95th percentile, against 407–1,063 (E2). 8–16 RU per search on a 5,535-passage library here, 4–7 RU filtered: about $4 per million searches at serverless prices (E10). |
| **No limit on libraries** | A library is a value on each record. Cosmos counts containers (500 per account), never values, so adding a library creates nothing it counts. | Azure AI Search allows 50 indexes on S1 and 200 on S2 and S3 (E1). Phase 4 puts 1,000 libraries in one container and checks search cost is unchanged. |

The arrangement pays off only when every search names exactly one library. Searching without the library ID cost about 220 times more, and one query across several libraries about 300 times more (E4, E5). So the service always takes the library ID from the URL the platform called, and searches several libraries as several queries.

The latency and per-owner figures come from earlier measurements of the same Cosmos features on a container of 2,000 owners' content, which this design applies to libraries. That libraries behave like those owners is an inference until Phase 4 measures it (N2).

## Status

Phases 0–3 of `docs/build-plan.md` have passed on live Azure.

- Phase 0: the Cosmos configuration deployed and passed its canary at small scale (`records/`).
- Phase 1: the API, ingestion worker and vector search work end to end, and a personal library refuses other agents.
- Phase 2: a demo platform (Streamlit) runs official and personal agents that search their libraries through tools.
- Phase 3: metadata filters (period, dates, authors, tags, file types), AI proposals of tags, periods, authors and library descriptions for an admin to accept, and document replacement and deletion.

Next are Phase 3B (images) and Phase 4 (a thousand libraries in one container, measured). The design rests on earlier measurements of these Cosmos features on live Azure (2026). `docs/evidence.md` traces each decision to them and lists what is not yet proven.

## What's here

| Path | What it is | For |
|---|---|---|
| `docs/brief.md` | The case for the change, in two pages | Anyone deciding whether to adopt it |
| `docs/spec.md` | What to build: components, data model, permissions, API, ingestion, search | Whoever builds it |
| `docs/build-plan.md` | Build order, demo-first, with an exit check per phase and the demo script | Whoever builds it |
| `docs/evidence.md` | Each decision, the measurement behind it, and what is not proven | Reviewers, and anyone challenging a decision |
| `docs/review-response.md` | Each finding of the independent architecture review, its class, and what was done | Reviewers |
| `cosmos/*.json` | The three Cosmos container definitions, ready to deploy | Infrastructure code |
| `infra/` | Bicep for one organization's deployment, and how to deploy it | Whoever deploys it |
| `src/fli/` | The application package: `fli-api` and `fli-worker` entry points | Whoever builds it |
| `src/fli_demo/`, `demo/` | The demo platform: the index client, the agent–library attachments (kept outside the index) and the agent loop; the Streamlit pages | Anyone running the demo |
| `scripts/canary.py` | Proves a newly deployed account's vector and hybrid search work (CLAUDE.md rule 11) | Whoever deploys it |
| `scripts/load_demo_corpus.py` | Loads a folder of documents into shared libraries through the API | Anyone running the demo |
| `scripts/deployment_env.py` | Prints a deployment's outputs as `export` lines, for the demo commands below | Anyone running the demo |
| `scripts/phase1_exit.py`, `scripts/phase3_exit.py` | Exit checks driven through the running API; each writes a record to `records/` | Whoever runs an exit check |
| `records/` | Canary and exit-check records, committed | Reviewers |
| `CLAUDE.md` | Rules for coding agents working in this repo | Coding agents |

## Getting started

1. Read `docs/spec.md`.
2. Read `docs/build-plan.md` for what each phase built and which phase is current.

Set up and run every test (Python 3.12 or later; developed on 3.14):

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,demo]'
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

To deploy and run the canary, follow `infra/README.md`.

## Prototype setup

How the prototype is built and run for the demo: every component, what runs where, and the exact Cosmos layout. The section above says why; this one says precisely what.

### Components and workloads

The index is two processes, an API and an ingestion worker, in front of Azure services. Both authenticate with Entra ID only: a user-assigned managed identity when deployed, your `az login` when run locally. Keys are disabled on Cosmos, Blob Storage, Service Bus, Azure OpenAI and Document Intelligence. The platform keeps agents and which libraries they may use (in the demo, Streamlit stands in for it), so the index stores no permissions beyond who owns a personal library.

```mermaid
flowchart TB
    subgraph platform["Agent platform: owns agents and attachments (demo: Streamlit, demo/)"]
        direction LR
        admin["Admin and user pages<br/>create libraries, upload,<br/>review proposals"]
        agent["Agent loop<br/>one search tool<br/>per attached library"]
        registry[("Agents and attachments<br/>~/.fli-demo/state.json")]
        agent -.- registry
    end

    subgraph index["File Library Index (runs locally today; Container Apps in Phase 5)"]
        direction LR
        api["<b>fli-api</b> (FastAPI)<br/>libraries, documents,<br/>search, tool definitions"]
        bus[["<b>Service Bus</b> queue ingest<br/>sessions: one lane per library<br/>ingest, retag, purge, propose"]]
        worker["<b>fli-worker</b><br/>extract, chunk, propose,<br/>embed, write, retag, purge"]
        api -- enqueue --> bus -- "up to 8 libraries<br/>at a time" --> worker
    end

    subgraph data["Azure data (infra/main.bicep)"]
        direction LR
        cosmos[("<b>Cosmos DB for NoSQL</b><br/>serverless, vector search,<br/>continuous backup<br/>libraries, documents, chunks")]
        blob[("<b>Blob Storage</b><br/>container files<br/>originals, per generation")]
    end

    subgraph ai["Azure AI services"]
        direction LR
        embed["<b>Azure OpenAI</b><br/>text-embedding-3-large<br/>3,072 dimensions"]
        chat["<b>Azure OpenAI</b><br/>chat deployment<br/>proposals; demo agent"]
        ocr["<b>Document Intelligence</b><br/>prebuilt-read OCR for<br/>PDFs without a text layer"]
    end

    insights["<b>Application Insights</b><br/>deployed; search events<br/>arrive in Phase 4"]

    admin -- "REST, Bearer key<br/>(Entra in Phase 5)" --> api
    agent -- "POST …/search<br/>with X-Agent-Id" --> api
    agent -- "answers with<br/>tool calls" --> chat

    api -- "store original" --> blob
    api -- "records; one query<br/>per search" --> cosmos
    api -- "embed the question" --> embed

    worker -- "read original" --> blob
    worker -- "write chunks,<br/>commit, retag" --> cosmos
    worker -- "embed chunks" --> embed
    worker -- "tags, period, author;<br/>library description" --> chat
    worker -- OCR --> ocr

    api -.-> insights
```

**Upload.** The API stores the original in Blob Storage under its own generation's path, writes the document record as `queued`, and puts an `ingest` message on the queue with the library as its session. One library's messages run one at a time, so a user dropping 5,000 files occupies one lane, while up to 8 libraries ingest in parallel.

**Ingest.** The worker reads the original and extracts its text (OCR only when a PDF has no text layer), chunks it into about 500 tokens per section, and asks the chat model for tags, a period and an author. It embeds the chunks, writes them under a new generation, and commits: from that moment search returns the new generation and ignores older ones, which a `purgeDocument` message then deletes. The library's tag vocabulary and filter values are recomputed, and a library description is proposed when enough has changed.

**Search.** An agent's tool call carries only a question and filters. The platform maps the tool to the library it attached and calls `POST /v1/libraries/{id}/search` with the agent's id. The API point-reads the library and refuses a personal library to any other agent, embeds the question, and runs one Cosmos query scoped to that library. It drops chunks of replaced or deleted documents and returns passages with file, page or slide, and a coverage note saying what the filters left out.

**Metadata changes.** Accepting a proposal or editing a document's tags updates the document record at once and queues `retag`, which copies the metadata onto every chunk so filters run inside the vector query.

### The Cosmos containers in detail

Three containers in one database. Everything is keyed by library first, because every operation names exactly one library: a search, an upload, a permission check. A library is a value on each record, not a container or index, so there is no count to run out of.

```mermaid
flowchart TB
    subgraph db["Cosmos database fli (definitions: cosmos/*.json)"]
        direction TB
        libs["<b>libraries</b><br/>partition key /libraryId<br/>one record per library:<br/>kind shared or personal, boundAgentId,<br/>description and its proposal,<br/>tag vocabulary, filter values, stats"]
        docs["<b>documents</b><br/>partition key /libraryId<br/>one record per document:<br/>status, generations, file fields,<br/>author, period, tags, proposals"]
        subgraph chunks["<b>chunks</b>: partition key /libraryId then /documentId, vector shard key /libraryId"]
            direction LR
            subgraph libA["library A: its own DiskANN graph"]
                a1["document 1<br/>chunks 0..n"]
                a2["document 2<br/>chunks 0..n"]
            end
            subgraph libB["library B: its own DiskANN graph"]
                b1["document 3<br/>chunks 0..n"]
            end
        end
    end

    chunk["<b>A chunk</b><br/>id documentId:generation:chunkIndex<br/>text, heading, page or slide<br/>embedding: 3,072 float16, cosine<br/>copied for filters: author, period,<br/>facets, fileType, contentKind, dates"]
    q["<b>One search</b><br/>SELECT TOP 2 x top ...<br/>WHERE c.libraryId = @libraryId AND filters<br/>ORDER BY VectorDistance float16<br/>sent with partition key libraryId"]

    q -. "1. point-read the library,<br/>check access" .-> libs
    q -- "2. routed to one library's data,<br/>searched with that library's graph" --> libA
    q -. "3. read the hits' documents,<br/>drop replaced generations" .-> docs
    a1 -.- chunk
```

| What libraries need | How the containers provide it |
|---|---|
| Any number of libraries | A library is a partition-key value. Adding one creates no container, index or account object. |
| A search touches one library only, at a cost that does not grow with the others | `/libraryId` leads the `chunks` key, so the query goes straight to that library's data, and the vector shard key gives each library its own DiskANN graph. Searching several libraries is several queries, never `IN` or `OR` (E4, E5). |
| Personal stays personal | The library record holds `kind` and `boundAgentId`, both immutable. A personal library is searched only with its bound agent's id, checked on every search. |
| Cheap work per document | `/documentId` second in the key makes reprocessing, deleting or reading neighbouring chunks a single-partition operation, and keeps each logical partition far below Cosmos's 20 GB limit. |
| Filters inside the vector search | Each chunk carries a copy of its document's author, period, tags (`facets`) and file type, and those paths are indexed, so one query filters and ranks together. `retag` keeps the copies current. |
| Replace a document without a gap or stale answers | Chunk ids carry a generation. The old generation stays searchable until the new one commits; search keeps only the document's active generation, and the purge deletes the rest. |
| Keep costs low on serverless | float16 vectors cut storage by 46% and query cost by about 41% at equal recall (E7), provided the query vector is declared float16 too (E8). A search cost 8–16 RU on a 5,535-chunk library in Phase 1. Writes are the expensive side, about 144 RU per chunk, so the worker caps them across all libraries (E10). |

Vector settings (dimensions, data type, quantizer, shard key) cannot change after a container is created, so the chunk text and original files are kept: a change means a new container, rebuilt from them (E16).

## Run the demo

The demo runs locally against a deployed environment, signed in with `az login`. Pick any string as the local API key; it only protects the API on your machine. Run each command in its own terminal, from the repo root, after setting `RG` (the deployment's resource group) and `DEV_API_KEY` in that terminal.

```bash
eval "$(.venv/bin/python scripts/deployment_env.py -g "$RG")" && ENVIRONMENT=local AUTH_MODE=dev .venv/bin/fli-api
```

```bash
eval "$(.venv/bin/python scripts/deployment_env.py -g "$RG")" && ENVIRONMENT=local AUTH_MODE=dev .venv/bin/fli-worker
```

```bash
.venv/bin/python scripts/load_demo_corpus.py <corpus folder>
```

```bash
eval "$(.venv/bin/python scripts/deployment_env.py -g "$RG")" && .venv/bin/streamlit run demo/app.py
```

The corpus folder holds `groups/<library name>/` subfolders, one shared library each. Agents and their attachments are stored in `~/.fli-demo/state.json` (`DEMO_STATE_FILE` to change it), never in the index. Load documents a few minutes before searching: the vector index engages minutes after writes (`docs/evidence.md` E25).
