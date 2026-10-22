# inherited/

Building blocks carried over from earlier work that measured these Cosmos features on live Azure. They are adapted to this project: library-specific logic is removed, page, slide and sheet locations are added for citations, and the float16 query rule is corrected. Each piece has tests.

This folder is **reference and starting material**, not the application. Phase 1 (`docs/build-plan.md`) moves what it uses into the application package, with these tests, and the folder can then be deleted.

## What's here

| File | What it does | Spec |
|---|---|---|
| `extraction.py` | Native-first text extraction for PDF, DOCX, PPTX, XLSX, TXT, MD, CSV and HTML. Rights-protected and password-protected files are classified, not retried. Bounded OCR fallback for scanned PDFs. Every section carries its page, slide or sheet | §7.3 |
| `docintel.py` | Document Intelligence `prebuilt-read` client: page and byte bounds, polling with `Retry-After`, a timeout, error mapping to retry or fail, and a guard that never follows a poll URL off the configured endpoint | §7.3 |
| `chunking.py` | The section-aware 500-token chunker with whole-sentence 50-token overlap, the email rule, and a version string that names the tokenizer | §7.4 |
| `embedding.py` | Batched Azure OpenAI embeddings with `Retry-After` handling, bounded attempts and a dimension check | §7.2, §8 |
| `governor.py` | Per-workload concurrency limits and pacing with cooldown on repeated throttling | §7.6 |
| `http.py` | `Retry-After` parsing and jittered backoff | — |
| `query.py` | The search SQL of spec §8 with its invariants enforced, the float16 query-vector conversion, and `check_query` for use in tests and at runtime | §8 |
| `../infra/modules/cosmos-*.bicep` | Moved to `infra/` in Phase 0: one organization's account, database and containers | §3, §4 |
| `tests/` | 63 tests | — |

## Status

- **Python modules:** tested against fakes (`python -m pytest inherited/tests` from the repo root; dependencies in `requirements.txt`). Extraction, OCR, chunking and embeddings were exercised in their earlier form; the location fields, speaker notes and `query.py` are new here and covered only by these tests. Nothing here has run against live Azure in this form.
- **Bicep:** moved to `infra/modules/` in Phase 0 and compiled there; not yet deployed in this form. Phase 0 deploys it and the canary proves it.

## What changed in adaptation, and why

- **Locations.** PDFs now produce one section per page, and chunks never span pages or slides, so search results can cite page, slide or sheet (`spec.md` §4.3 `location`). PowerPoint speaker notes are now extracted.
- **One document model.** The chunker takes `kind="document"` or `kind="email"`; chat-message rules from the earlier work are gone.
- **float16 query rule corrected** (`query.py`; `docs/evidence.md` E8). An earlier rule said to declare `float32` for the query vector. The later measurement showed that declaring `float32` against a float16 index makes Cosmos scan instead of using the index. The correct path is to round to float16, flush subnormals to zero, and declare `float16`.
- **Governor** reports through an `on_event` callback instead of a telemetry dependency.

## Not here yet (Phase 1 builds them)

- Images embedded in DOCX and PPTX, and the image pipeline (`spec.md` §7.5). `extraction.py` still sends standalone images to OCR; the application should send them to the image pipeline instead.
- EML and MSG extraction, and author and date metadata (`spec.md` §7.3).
- The Cosmos client code: writing chunks, generations, the generation check, and neighbours (`spec.md` §7–§8).
