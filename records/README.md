# records/

Canary records written by `scripts/canary.py`, one per run, named `canary-<UTC timestamp>.json`. Commit them: a passing record is the Phase 0 exit check (`docs/build-plan.md`).

Phase 1 exit-check records written by `scripts/phase1_exit.py`, named `phase1-exit-<UTC timestamp>.json`:

- `phase1-exit-20261023T012726Z.json`: ingestion of 20 documents passed; every search returned the same unranked results, because hybrid queries were routed by partition key (`docs/evidence.md` E27).
- `phase1-exit-20261023T013913Z.json`: the same library searched again, vector-only, after the fix. A resumed run: it reuses the library and carries no ingestion timings.

Phase 3 exit-check records written by `scripts/phase3_exit.py <check>`, named `phase3-exit-<check>-<UTC timestamp>.json`:

- `phase3-exit-lifecycle-20261023T210242Z.json`: replacement and deletion passed. A real paper was replaced by a different one under the same file name. The replacement committed after 9 seconds, and none of the 68 searches after the commit returned the old generation. Deleting the document, then the library, removed each from search at once. One search during the write came back empty, with coverage noting the passages left out: the new generation's chunks, not yet visible, filled the whole fetch window (`docs/evidence.md` E30).

Records hold metrics only: request charges, retrieved-document counts, recall, timings, file hashes. They never hold query text, chunk text or file names (CLAUDE.md rule 12).

Adaptive retrieval records (`document-retrieval-<UTC timestamp>.json`) include service checks, role labels/public source hashes, requested operation sequences, token usage and timing. They exclude runtime environment identifiers, prompts, source text and answers. The initial throttled and pre-fix runs are retained alongside the passing smoke check `document-retrieval-20261026T165258Z.json`. A passing sequence check is not a human assessment of answer quality. Run the reference-privacy checker with an external private deny file before publishing live evidence.

`library-performance-smoke.json` records the live instrumented six-page reprocessing and three subsequent retrieval calls. It holds aggregate inventory, stage timings, actual embedding input tokens, chunk-write RU and retrieval summaries, without environment identifiers or source content.

`full-vs-mixed-retrieval-20261026.json` preserves the manual two-chat comparison: both complete files versus a complete primary file plus reference passages. It records cumulative usage separately from final-answer input. The latter fell 37.3%; cumulative tokens, retrieval RU and retrieval time did not fall. Prompts differed and there was one run per condition, so this is composition evidence rather than a controlled performance or accuracy benchmark (E37).

`retrieval-followups-20261027.json` records issue #2's four-format invented-corpus smoke check: figure reading order, unchanged section text, raw/projected response tokens, grouped-document selection/parent size, stored-page reading and two real-model image/mixed-evidence questions. It records cumulative provider usage separately from serialized response savings. No broad recall, deterministic tool selection or controlled latency improvement is established (E45).

`casual-retrieval-friction-20261027.json` records a subsequent three-turn natural-language sequence, separate from the guided paired tests. It preserves original-run cost/timing and content-free findings about repeated document grouping, budget refusal and image-selection friction. Separate read-only probes checked source availability and mode behaviour. This is a focused integration finding (E46), not a broad recall score or a pure library-layout comparison.

`library-layout-manual-20261027.json` preserves both human-run eight-question suites, per-case and aggregate answer-time cost/latency, tool-count context and the eight-copy ingestion snapshot. It compares two library configurations with independently generated image descriptions, not an implementation before/after benchmark. See E47.

`casual-image-reference-20261027.json` preserves the first two casual-reference spot tests and links the third, three-turn case in `casual-retrieval-friction-20261027.json`. These are focused intent/evidence findings with separate model and embedding token counts; this metrics record contains no source content. Sanitized questions, observed answers and selected figures are published in the portable evidence pack below. See E48/E46.

The [2026-10-27 test review](../docs/consults/2026-10-27-agent-retrieval-test-review.md) indexes all of the day's implementation, live smoke, paired and casual test evidence. The [proposed strategy](../docs/consults/2026-10-27-agent-retrieval-strategy.md) links changes to those findings and remains pending review.

The user-requested [portable evidence pack](../docs/testing/2026-10-27/README.md) adds sanitized observed transcripts/traces, exact replay questions, source fixtures and selected review figures for cloud-only continuation. Summary metrics remain separate from raw runtime state; credentials, live IDs and signed links are not committed. Start with [HANDOFF.md](../HANDOFF.md).
