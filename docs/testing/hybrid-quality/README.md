# Hybrid-quality v1: original-source confirmation pack

**Consumed on 2026-11-03.** The [completed comparison](../../hybrid-quality-results-20261103.md)
contains all 96 direct searches and 192 answer turns. This unchanged pack is now
development/regression evidence; new confirmation requires new frozen questions.
Manifest, tasks and plan remain byte-for-byte as frozen.

`manifest.json` freezes source bytes, metadata, source-based fact anchors, task
bytes and the deterministic private-gold digest. `tasks.json` freezes 48 questions.
At freeze this was an entirely new confirmation set; no task was a development
probe. No post-outcome tuning or task deletion is allowed. The coordinator's frozen run plan
binds these files and sets the actual comparison schedule.

## Sources and scope

All sources are Cabinet Office / Government Legal Department original public DOCX
contract templates. Original published download filenames and bytes are unchanged. Public landing and direct acquisition URLs are in the manifest. Natural titles
are also frozen as supported `metadata.tags` entries with key `document-title`;
there is no unsupported top-level metadata title field. Published files retain
their original extension casing, including `.DOCX`. Ingest the manifest inventory,
not a case-sensitive `*.docx` glob.
Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

| Source | Original document | Local body token estimate |
| --- | --- | ---: |
| hq-01 | Model Services Contract, Core Terms, England and Wales, v2.2A | 37,154 |
| hq-03 | Short Form Contract, v1.5A | 38,370 |
| hq-04 | Mid-Tier Core Terms, v1.3A | 14,083 |
| hq-05 | Mid-Tier Schedule 1, Definitions, v1.3A | 13,811 |
| hq-06 | Mid-Tier Schedule 28, ICT Services, v1.3 | 1,826 |
| hq-07 | Mid-Tier Schedule 20, Processing Data, v1.3 | 9,037 |
| hq-08-1 | Mid-Tier Schedule 16, Security, Buyer-led Assurance, v1.3 | 24,663 |
| hq-09 | Mid-Tier Schedule 30, Exit Management, v1.3A | 9,919 |

These counts visit each original OOXML body text node once, including nested
controls, so merged table cells are not counted repeatedly. Counts use
`cl100k_base`; the later completed ingestion produced 36 Microsoft layout chunks,
with seven multi-chunk documents. These local estimates are not provider counters.
The complete MSC Combined Schedules candidate was excluded before retrieval
because its initial local estimate exceeded 255k tokens for one source, beyond
the fixed 200k conversation allowance. Other Security schedule variants were
excluded to limit scope and avoid mixing alternative contractual regimes. No
source is truncated, rewritten, generated or padded. All eight selected sources
contain scored evidence and also supply authentic surrounding distractors.

There are 40 answerable cases and eight missing-value cases. Family counts overlap:
exact-term 24, exception 20, concept 19, paraphrase 16, identifier 12, name 9,
acronym 7, section-reference 7, cross-document 5, unanswerable 8. The five
cross-document cases are hq-36 through hq-40. Contract family names disambiguate
conflicting template regimes; most questions do not name an exact filename.

## Reconstruct without cloud access

From a checkout with `sh scripts/dev setup` completed, choose a private directory
outside any checkout. The variable is intentionally operator supplied:

```sh
export HYBRID_CORPUS_PRIVATE=/path/outside/repository/hybrid-quality
mkdir -p "$HYBRID_CORPUS_PRIVATE"
chmod 700 "$HYBRID_CORPUS_PRIVATE"
.venv/bin/python scripts/hybrid_quality_corpus.py fetch \
  --source-dir "$HYBRID_CORPUS_PRIVATE/sources" \
  --gold-output "$HYBRID_CORPUS_PRIVATE/gold.json"
```

`fetch` downloads only HTTPS originals listed in the manifest, verifies byte size
and SHA-256 before creating a file, reuses matching existing bytes, and refuses
changed bytes or replacement. URLs have no credentials or query strings. Source
files and gold are created with mode 0600. Gold destinations must be new files.
No Azure resource, model deployment, application resource or credential is used.

For existing sources, run the read-only verification command:

```sh
.venv/bin/python scripts/hybrid_quality_corpus.py verify \
  --source-dir "$HYBRID_CORPUS_PRIVATE/sources"
.venv/bin/pytest -q tests/test_hybrid_quality_corpus.py tests/test_retrieval_r0_corpus.py
.venv/bin/ruff check scripts/hybrid_quality_corpus.py tests/test_hybrid_quality_corpus.py
.venv/bin/ruff format --check scripts/hybrid_quality_corpus.py tests/test_hybrid_quality_corpus.py
```

The verification summary contains counts and hashes only. Originals and gold must
not be committed. The normal repository setup remains local dependency setup;
source downloading is a separate explicit action.

## Scoring contract

`tasks.sourceFacts` uses the R0 source/region shape. `requiredRegionIds` is its exact
set of required original regions. `requiredFactIds` maps to `manifest.facts`, where
`sourceRegionIds` and `anchorSpans` describe opaque original text coordinates.
Paragraph and top-level table/cell indices are zero based in `python-docx`
collections. Region text is reconstructed from original OOXML descendant `w:t`
values, inserting whitespace at paragraph, tab and line-break boundaries; this
includes inline content controls. It is never obtained from API extraction,
chunks or retrieval results. The normalization is Unicode NFKC and collapsed
whitespace, preserving case and punctuation. Anchor offsets are Python Unicode
code-point offsets in normalized region text. This version uses complete required
regions as anchors so support cannot be credited from a filename alone.

Private `gold.json` has:

- `regions`: original source identity, locator, expected text and normalized hash;
- `facts`: required original region IDs, reconstructible `expectedFact`, and
  `requiredAnchorTexts`;
- `tasks`: required fact IDs and `expectedAnswer`, or explicit `abstentionCriteria`.

The private expected answer concatenates original fact excerpts as review material;
it is not a required wording or a model-generated ideal answer. Reviewers grade
all requested components, constraints, exceptions and citation support. Additional
source context is not an additional answer requirement. Conforming paraphrases
can pass even when mechanical exact-region delivery diagnostics miss them.

Unanswerable tasks have `sourceFacts: []`, `requiredRegionIds: []`,
`requiredFactIds: []`, and `expectedSourceIds: []`. Their direct source recall is
**null**, not one. A task passes answer review when it acknowledges the missing
actual information without turning placeholders, illustrative values or generic
obligations into actual completed-contract facts. These cases measure appropriate
abstention, not proving that no conceivable answer exists outside the library.

Initial candidate recall and expansion recall must remain separate. Required gold
supports recall and positive relevance labels; absence from gold never proves a
hit irrelevant. Report unlabeled hit judgments as unknown until independent
source review. Answering receives only each task's `case` and `question`; it never
receives the expected source IDs, facts, labels, families or answerability flag.

## Review and remaining gates

Two agents other than the source/question author reviewed hq-01–24 and hq-25–48
against original DOCX bytes before retrieval or answering. They verified all eight
source hashes, original coordinates, requested exceptions and all eight missing
actual-value cases. Neither found a blocking ambiguity. Both noted that gold can
contain context beyond what the question requests; the semantic-review rule above
addresses that distinction. See `source-review.json` for sanitized receipts.

Local validation proves reconstruction and label integrity. Actual Microsoft
extraction fidelity, actual published chunk count, cloud readiness, matched direct
recall and answering quality were separately evaluated in the linked result; the
shared definition-structure limitation remains explicit. No cloud
provisioning, ingestion, performance measurement or model answering is performed
by this package.
