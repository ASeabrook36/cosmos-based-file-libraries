# Retrieval tests and evidence review — 2026-10-27

This index preserves all test families from the issue #2 implementation and the subsequent human-run comparisons and casual references. It separates completed mechanisms, observed user-facing failures, inference and proposed work. The [proposed agent strategy](2026-10-27-agent-retrieval-strategy.md) is for review and is not implemented.

## Evidence map

| Test family | Scope and evidence | What it establishes | What it does not establish |
|---|---|---|---|
| Implementation checks | 600 local tests; E45 and [follow-up record](../../records/retrieval-followups-20261027.json) | Deterministic completeness, budgets, source order, projection, bounded loading/cancellation, visibility/isolation and accounting contracts passed at the recorded checkpoint. | Real-model intent interpretation, broad recall or production readiness. |
| Four-format live smoke | PDF, DOCX, PPTX and standalone image; same [follow-up record](../../records/retrieval-followups-20261027.json) | New figure order, unchanged section text, size hints/grouping, stored reads and two real-model image/mixed-evidence paths. | A controlled before/after latency or total-cost improvement. |
| Human-run paired configurations | Eight questions each; [paired record](../../records/library-layout-manual-20261027.json) | Both configurations delivered requested visual placements; actual costs and tool paths differ. Includes the eight-copy ingestion snapshot. | A general separate-versus-combined library verdict; no counterbalancing/repeated distribution. |
| Casual remembered diagram | [casual record](../../records/casual-image-reference-20261027.json), case 1 | Right image found from informal clues; plausible comparison correct; ambiguity/phrasing/caption qualification needs review. | Confirmed intended referent or universal handling of vague questions. |
| Casual ambiguous map | Same [casual record](../../records/casual-image-reference-20261027.json), case 2 | Correct map facts recovered after text searches/full read; plausible plot-number interpretation was not addressed. | Proof of actual plumbing/water access or fully resolved intent. |
| Casual meeting brief and image follow-ups | Three turns; [friction record](../../records/casual-retrieval-friction-20261027.json), E46 | Grouped-search trap, budget refusal, unnecessary image-permission loop and weak claim/figure relevance; relevant stored evidence verified by separate diagnostics. | A measured ANN recall score, absence of all relevant research, or proof that ungrouping alone fixes answers. |

## Paired configurations

Both used the same original file bytes, agent instructions, deployed model, 48,000-token context budget and enabled image inputs. Each suite was one consecutive conversation; prior user/assistant text accumulated. The separate run came first. Vision descriptions were independently generated for the two copies, and tool/filter vocabulary differed. All copies were ready at the baseline.

| Metric | Four separate libraries | One combined library |
|---|---:|---:|
| Model input tokens | 310,458.00 | 129,764.00 |
| Model output tokens | 13,505.00 | 12,928.00 |
| Query embedding tokens | 149.00 | 148.00 |
| Retrieval RU | 135.78 | 198.82 |
| Summed response seconds | 226.22 | 150.31 |
| Retrieval seconds | 8.99 | 8.38 |
| Quota wait seconds | 60.00 | 0.00 |
| Model calls | 26.00 | 21.00 |
| Tool calls | 18.00 | 13.00 |

Both runs showed all seven requested inline image placements (four distinct pictures) and matched the principal fixture facts. Caption attribution and extra complete supporting-file reads remained issues; a rendering count is not a complete quality grade. The separate agent exposed sixteen tools, the combined agent four. First-request input was 8,701 versus 3,562 tokens before retrieval. Summed response time excluding recorded waits was about 166.22 versus 150.31 seconds; subtracting waits is arithmetic, not a controlled no-throttling benchmark.

The combined run used 58.2% fewer model input tokens and 46.4% more retrieval RU. These are observations for this implementation and these model paths, not a reason to merge libraries irrespective of access boundaries or subject organization. Model input includes cached input; cached tokens must not be added again. Query embeddings and one-time ingestion embedding/vision tokens are separate.

## Ingestion snapshot

The paired record includes original hashes, counts and stage/token/write-RU measurements for all eight copies. Both copies of the long report published 48 image descriptions. The Word, presentation and standalone-chart copies each published one. One visual candidate per report was recorded skipped. All eight source files matched their counterpart bytes.

The report vision stages took about 8.8–9.0 minutes. Small-file vision stages took about 8–12 seconds. Some combined-library Office files had much longer total elapsed completion because deferred image enrichment waited behind the report in the same library session. This is consistent with the documented per-library scheduling policy/text-first design; total elapsed time must not be mistaken for active vision work. The snapshot is not a controlled serial-versus-concurrent ingestion benchmark.

## Casual references

| Case/turn | Model input | Model output | Embedding tokens | RU | Response s | Retrieval s | Quota wait s | Model/tool calls |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Casual 1 | 9,881 | 1,225 | 26 | 21.50 | 14.64 | 0.88 | 0.00 | 2/1 |
| Casual 2 | 28,488 | 2,139 | 50 | 29.37 | 29.31 | 1.83 | 0.00 | 5/4 |
| Casual 3, turn 1 | 101,715 | 5,248 | In case total | 321.88 | 105.96 | 9.82 | 30.00 | 13/12 |
| Casual 3, turn 2 | 14,864 | 1,837 | In case total | 13.73 | 16.27 | 1.04 | 0.00 | 2/1 |
| Casual 3, turn 3 | 16,444 | 1,985 | In case total | 13.92 | 20.30 | 0.99 | 0.00 | 2/1 |

Case 3 used 495 query-embedding tokens across its three original turns. Its first turn repeatedly used document-grouped search and obtained one passage per search. Read-only probes, explicitly excluded from these totals, verified mode behaviour and stored relevant evidence. The full projected report response was about 98k tokens, exceeding the configured model budget; refusal was correct. Repeated searches and asking to fetch images already requested were not necessary consequences of that refusal.

## Findings and limits to preserve

- Retain the successful content/provenance, image delivery and lifecycle mechanisms. Source/caption metadata existed before retrieval; the natural-test failures do not establish lost storage or require a new vector store.
- Fix the grouping-mode interface and recovery path. Document finding and evidence gathering need distinct, visible semantics. The calling model made poor choices, but the interface made those choices possible and did not help it recover.
- Add explicit known-document passage scope and bounded source/selected-image access. The need is supported by the long-report and Office-map cases.
- Keep scientific relevance and uncertainty explicit. Correctly retrieving a related figure is not equivalent to evidence for the user’s proposed thesis. Source caption inference and visible-image text must remain distinguishable.
- Treat model quota, tool-schema size, sequential decisions, evidence retention and selected-image input as consuming-platform factors. Measure them; do not call every such cost intrinsic to Cosmos.
- Raw tool/model exchanges were not retained in routine telemetry. The user authorized private visible-chat/image captures; this supports review but does not prove every image was supplied as a native model input.
- User feedback on experience and reviewer source checks are different evidence. No comprehensive user grade or universal answer-quality claim is assigned to these records.

## Documentation and retention

Public records contain role labels, original hashes, counts, booleans, metrics and content-free findings. The user subsequently requested cloud portability. Sanitized questions/answers, query traces, source fixtures and selected review figures are now committed in the [portable evidence pack](../testing/2026-10-27/README.md). Original unredacted captures, live library/agent/document IDs, deployment names, signed links and environment details remain outside the repository. Routine application telemetry remains content-free. The private reference-privacy checker is required before publication.

Existing earlier image-readiness/caption/figure checks remain linked from E40–E44 and the [readiness review](2026-10-27-image-readiness-review.md); they were not silently reclassified as new evidence for this strategy. Future implementation must add its own validation record.
