# Cloud development handoff

This repository contains the implementation, fixtures, captured evidence and proposed next strategy from the 2026-10-27 retrieval work. A cloud agent does not need previous chats, local temporary files or access to the original laptop to understand or develop the next change.

## Read first

1. [CLAUDE.md](CLAUDE.md): coding instructions, query/isolation/privacy invariants.
2. [Specification](docs/spec.md) and [build plan](docs/build-plan.md): current implemented contract and remaining phases.
3. [Issue #2 decisions](docs/consults/2026-10-27-retrieval-followups.md): what was implemented and what was deferred.
4. [All test findings](docs/consults/2026-10-27-agent-retrieval-test-review.md) and [the evidence pack](docs/testing/2026-10-27/README.md): matched runs, natural questions, actual answers, query/filter traces, metrics, figures and diagnostic responses.
5. [Proposed strategy](docs/consults/2026-10-27-agent-retrieval-strategy.md): next capability/adapter changes for review. The user subsequently authorized implementation. The [current contract](docs/agent-retrieval.md) and E49 distinguish implemented capabilities, measured replays and remaining answer-quality limits.

## Current implementation

Issue #2 follow-ups add compact model-facing evidence with independent image attachments, interleaved enriched reading order, parent document size hints, optional document-grouped search, bounded concurrent image descriptions, off-loop read tokenization and provider-usage context accounting. Complete-read text is preserved. Existing source generations remain immutable; container/vector policies and the embedding model are unchanged.

The issue #2 implementation validation passed 600 tests. The subsequent capability package passes 611 tests, Ruff lint/format and private reference-privacy checks. This is mechanism validation, not a blanket quality signoff. Natural tests subsequently revealed an important problem: a real model repeatedly selected document grouping while gathering evidence inside a known long file. It also used unnecessary text-only searches/full reads, under-addressed ambiguous referents and made users repeat requests for useful images. Relevant source text and figures were verified stored and retrievable. See E45–E48 in [evidence.md](docs/evidence.md).

Priorities are best supported user result first, then retrieval/response time, then cost in model/embedding/vision tokens and Cosmos RU. Do not make a smaller/faster incomplete answer the optimization target. Do not hard-code the source pages, remembered questions or expected fixture answers into production retrieval.

The natural-test capability package is implemented in [agent-retrieval.md](docs/agent-retrieval.md). Read E49 and the [iterative replay evidence](docs/testing/2026-10-27/agent-fixes/README.md) before making quality or speed claims: the tool mechanisms pass, while referent interpretation and causal claim strength still vary.

## Work without Azure

Use Python 3.12 or later:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
python -m pytest -q
python scripts/check_reference_privacy.py
```

Unit/API tests use in-memory services; no Azure credentials or remote fleet is needed. The live Cosmos ranking cannot be reproduced with the emulator; use static query checks locally. Inspect [tests/test_retrieval_followups.py](tests/test_retrieval_followups.py), the reading/image/progressive-ingestion tests, and the saved observed outputs when extending the implemented capabilities.

## Reproduce the human scenarios

The four exact source files and hashes are in [tests/fixtures/retrieval-review](tests/fixtures/retrieval-review/README.md). The matched prompts, reviewer checks and exact casual questions are in the [evidence pack](docs/testing/2026-10-27/README.md). Sanitized observed answers/traces and source-region responses are committed, so source comparison and failure analysis work offline.

For a future live comparison, provision/use an authorized deployment through the existing [infrastructure guide](infra/README.md) and [demo setup](README.md). Supply that environment's own managed-identity/auth settings outside Git. Upload the fixture bytes; create one library per file and a combined library for the paired experiment. New runtime document/image IDs are expected: resolve them from tools/API responses, never copy the sanitized historical placeholders. Both agents used default instructions, the same deployed chat model, a 48,000-token context budget and enabled image inputs. Each matched suite was one consecutive conversation; the separate variant ran first. The casual sequence was a new conversation that retained preceding casual turns. Exact source bytes match across variants, but their independently generated descriptions differ; do not treat the pair as an isolated database-layout benchmark.

Run `python -m scripts.phase3b_exit --help` for the existing four-format live harness. Its `--state` must be external to the repository. Future raw captures belong in a separately authorized external directory; routine metrics remain content-free. Publish sanitized evidence and explain what each measurement establishes.

## Boundaries and continuity

The repo includes sanitized transcripts/case questions because the user requested a portable development record. That explicit test corpus is not permission to enable routine query, document or answer logging. Runtime identifiers, credentials, signed URLs, local paths and unrelated chats are excluded. The original unredacted machine state is neither needed nor appropriate for a cloud replay.

Do not rebuild containers, change embedding inputs, raise caps/quotas, bulk reprocess user libraries or silently change whole-file completeness to solve the observed agent-integration failures. Document-scoped search, bounded reads, cross-format image inspection and evidence-aware recovery are now implemented; broader model-quality/recall validation remains. Keep the existing one-library binding, generation/revision checks, personal isolation, ingestion-time captions and text-first publication.

## Scope clarification after E49

Read [the library/agent boundary decision](docs/consults/2026-10-27-library-agent-scope.md), E50. The user rejected expanding typo handling into a large task and questioned the scope of agent-side subject/claim judgments. Experimental interpretation/critic passes and special homophone handling were removed. They are not shipped capabilities or approved next work. Keep library/tool improvements separate from developing the consuming agent; the retained follow-up is a small study-scope instruction correction with no extra model calls.
