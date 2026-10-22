# File Library Index

*Working name.*

A search service that stores agent file libraries in Azure Cosmos DB instead of Azure AI Search.

- Admins and users upload files into libraries.
- The service extracts the text and describes the images, embeds both, and stores everything in one Cosmos container.
- Agents search a library through a tool the platform attaches to them.
- Personal agents each get their own library, which no other agent can search.

It removes the limit that prompted it. Azure AI Search allows 200 indexes per service on the S2 and S3 tiers, and the current design uses one index per library. Here a library is a value stored on each record, not an object the service counts, so the number of libraries has no design limit.

**Status:** specification only; nothing is built yet. The design rests on earlier measurements of these Cosmos features on live Azure (2026). `docs/evidence.md` traces each decision to them and lists what is not yet proven.

## What's here

| Path | What it is | For |
|---|---|---|
| `docs/brief.md` | The case for the change, in two pages | Anyone deciding whether to adopt it |
| `docs/spec.md` | What to build: components, data model, permissions, API, ingestion, search | Whoever builds it |
| `docs/build-plan.md` | Build order, demo-first, with an exit check per phase and the demo script | Whoever builds it |
| `docs/evidence.md` | Each decision, the measurement behind it, and what is not proven | Reviewers, and anyone challenging a decision |
| `cosmos/*.json` | The three Cosmos container definitions, ready to deploy | Infrastructure code |
| `inherited/` | Tested building blocks carried over from the earlier work: extraction, OCR, chunking, embeddings, the search query rules, Bicep | Whoever builds Phase 0 and Phase 1 |
| `CLAUDE.md` | Rules for coding agents working in this repo | Coding agents |

## Getting started

1. Read `docs/spec.md`.
2. Start `docs/build-plan.md` Phase 0. Its canary proves the Cosmos configuration before any application code depends on it.

`inherited/` holds tested code to start from (`docs/spec.md` §13; `inherited/README.md`). Run its tests with `python -m pytest inherited/tests` after `pip install -r inherited/requirements.txt`.
