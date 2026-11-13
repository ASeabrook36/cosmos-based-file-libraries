# E59 vector-shard ablation protocol

Frozen before the live run on 2026-11-11. The user authorized the paired run and
requested observed numerical drift rather than only pass/fail labels.

Compare two new disposable databases on the original serverless account, with
one physical partition range each. Their three containers follow `cosmos/*.json`;
the only policy difference is removal of
`indexingPolicy.vectorIndexes[0].vectorIndexShardKey` from the unsplit arm.
Production definitions and query behavior remain unchanged. Run the existing
canary on each new database before scale loading, with the experimental expected
definition selected explicitly. Preserve all canary gates.

Use E59's ordered public abstract/vector corpus, seed 20261029, assignments,
ten probe sizes (1000, 1000, 1500, 1500, 2000, 2500, 3000, 3500, 4000, 5000),
twenty source-sentence questions per probe and three ANN repeats per stage.
Historical cached question vectors are unavailable. Reconstruct sentence
selection deterministically, embed once and share the frozen vectors between
the fresh arms. Record ordered input-prefix and question-vector hashes. E59
remains an immutable historical comparator, not a simultaneous arm.

Actual stages are 10 libraries / 25000 vectors; 413 / 176599; and 1014 / 244773.
The final inventory includes the 1500-vector lag library that E59 added after
its second stage. Add the same background data without retesting lag.

Primary ANN queries use unmodified `render_search` SQL, `TOP 10`, float16 query
vectors, library equality and `partition_key_for`'s library prefix. This is the
application's vector candidate request for requested `top=5`; full API costs,
embedding and visibility lookups are outside the measurement. E59 added a score
projection; retain that distinction. Alternate fresh-arm execution order and
do not overlap queries with loads. Force exact search once per question, stage
and arm; compare all three ANN repeats' first ten results to that reference.

The negative control repeats E59's fixed first probe question three times per
stage: `TOP 10`, id-only projection, no library filter and no partition prefix.
E59 itself measured this control only once per stage. This intentional unsafe
shape is confined to the disposable experiment.

Wait at most thirty minutes for utilized-index evidence and stable readiness
probes, then settle five minutes. Do not define readiness by cheap RU or few
documents retrieved. Record every utilized vector index, type, shard keys and
missing response report. QuantizedFlat/DiskANN transitions are measured effects.
Readiness and measurement errors remain in private raw state and prevent an
unqualified conclusion. Query latency is client elapsed time, including retries.

## Predeclared interpretation

For each probe, arm and stage, take the median across questions of the median
across three repeats. Publish the absolute and signed percentage changes in RU,
documents retrieved and client latency, plus the three run medians and range.
The primary cheap/flat decision concerns RU; documents and latency receive their
own comparisons and cannot be described as equivalent if they differ materially.

Require complete metrics/results/index evidence, ten unique results, and mean
recall@10 at least 0.95 for every probe in each ANN repeat. Three repeats measure
observed stability, not a confidence interval or universal equivalence.

- Sharding is unnecessary for the measured cheap, flat RU behavior if both
  arms' later-stage RU remains within 20% of their own stage-one baseline and
  unsplit RU is at most 20% above split RU at every corresponding probe/stage.
- Evidence that sharding makes the measured workload flat requires split to
  stay flat while unsplit grows materially at both later stages. Robust growth
  requires the smallest later run median to exceed 120% of the largest baseline
  run median. An all-ten claim requires all ten probes; otherwise name the subset.
- Flat but dearer establishes a cost benefit, not a flatness dependency.
  Mixed, borderline, unstable, low-recall or incomplete results require a narrower
  or inconclusive conclusion. Control growth is diagnostic, not a validity gate.

These practical 20% bands do not assert reproduction of E59's observed under-1%
RU drift. Preserve E59's historical grade and recall coverage rather than
retroactively applying the current stricter harness.

The test removes sharding under the combined SQL-filter/partition-prefix path;
it does not isolate SQL filtering alone. It covers this one-partition workload,
small randomly assigned libraries and about 245000 vectors, not large libraries,
concurrency, semantic relevance or an entirely GA configuration. Spherical
quantization remains unchanged.

## Execution and retention

Keep config, identifiers, source locators, text, vectors, result IDs and raw logs
outside every checkout. Public receipts contain only content-free measurements,
generic arm/probe labels and hashes. Capture load/query/wait/retry charges and
explicitly mark unmeasured costs. Pause near 80000000 measured request units
(about $20 at the illustrative $0.25 per million RU), preserving progress; that
counter is not a provider-enforced billing cap. Never delete either database
without the user's separate instruction. Do not push.
