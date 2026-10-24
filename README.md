# File Library Index

*Working name.*

A search service that stores agent file libraries in Azure Cosmos DB instead of Azure AI Search.

- Admins and users upload files into libraries.
- The service extracts the text and describes the images, embeds both, and stores everything in one Cosmos container.
- Agents search a library through a tool the platform attaches to them.
- Personal agents each get their own library, which no other agent can search.

It removes the limit that prompted it. Azure AI Search allows 200 indexes per service on the S2 and S3 tiers, and the current design uses one index per library. Here a library is a value stored on each record, not an object the service counts, so the number of libraries has no design limit.

**Status:** Phases 0–2 of `docs/build-plan.md` have passed on live Azure.

- Phase 0: the Cosmos configuration deployed and passed its canary at small scale (`records/`).
- Phase 1: the API, ingestion worker and vector search work end to end, and a personal library refuses other agents.
- Phase 2: a demo platform (Streamlit) runs official and personal agents that search their libraries through tools.

Phase 3 (in progress) adds document replacement, metadata filters and proposals; images follow in Phase 3B. The design rests on earlier measurements of these Cosmos features on live Azure (2026). `docs/evidence.md` traces each decision to them and lists what is not yet proven.

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
