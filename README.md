# File Library Index

*Working name.*

A search service that stores agent file libraries in Azure Cosmos DB instead of Azure AI Search.

- Admins and users upload files into libraries.
- The service extracts the text and describes the images, embeds both, and stores everything in one Cosmos container.
- Agents search a library through a tool the platform attaches to them.
- Personal agents each get their own library, which no other agent can search.

It removes the limit that prompted it. Azure AI Search allows 200 indexes per service on the S2 and S3 tiers, and the current design uses one index per library. Here a library is a value stored on each record, not an object the service counts, so the number of libraries has no design limit.

**Status:** Phase 0 (`docs/build-plan.md`) passed: the Cosmos configuration deployed and passed its canary at small scale (`records/`; findings in `docs/evidence.md`). The application is not built yet. The design rests on earlier measurements of these Cosmos features on live Azure (2026). `docs/evidence.md` traces each decision to them and lists what is not yet proven.

## What's here

| Path | What it is | For |
|---|---|---|
| `docs/brief.md` | The case for the change, in two pages | Anyone deciding whether to adopt it |
| `docs/spec.md` | What to build: components, data model, permissions, API, ingestion, search | Whoever builds it |
| `docs/build-plan.md` | Build order, demo-first, with an exit check per phase and the demo script | Whoever builds it |
| `docs/evidence.md` | Each decision, the measurement behind it, and what is not proven | Reviewers, and anyone challenging a decision |
| `cosmos/*.json` | The three Cosmos container definitions, ready to deploy | Infrastructure code |
| `infra/` | Bicep for one organization's deployment, and how to deploy it | Whoever deploys it |
| `src/fli/` | The application package: `fli-api` and `fli-worker` entry points | Whoever builds it |
| `scripts/canary.py` | Proves a newly deployed account's vector and hybrid search work (CLAUDE.md rule 11) | Whoever deploys it |
| `records/` | Canary records, committed | Reviewers |
| `src/fli/` | The application package. `extraction`, `chunking`, `docintel`, `embedding`, `governor`, `http` and `query` were moved here from the earlier work in Phase 1 | Whoever builds Phase 1 onwards |
| `CLAUDE.md` | Rules for coding agents working in this repo | Coding agents |

## Getting started

1. Read `docs/spec.md`.
2. Start `docs/build-plan.md` Phase 0. Its canary proves the Cosmos configuration before any application code depends on it.

Set up and run every test (Python 3.12 or later):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

To deploy and run the canary, follow `infra/README.md`.
