# Python / .NET 3.63.0 comparison results

**Status: independently reviewed and accepted by the coordinator.** This reviews
the complete second run. The first run remains preserved separately as an invalid client comparison
because an observation-layer header bug prevented .NET data queries. No
application default, ingestion representation, SDK query option or benchmark
question was changed between runs.

The controlled comparison finds a real client difference: .NET 3.63.0 executes
library-prefix hybrid ranking, while Python 4.17.1 still returns the same list
for every question with that routing option. The observed .NET hybrid ordering matches its WHERE-only route. That does not
make hybrid as inexpensive as vector search or resolve routing at larger scale.

## What was verified

All 576 measured queries completed, with six separate warmups and 384 complete
per-document exact-vector reference queries. The consumed corpus has eight
original documents, 36 stored chunks and 48 questions, repeated twice. This is
exploratory/regression evidence, not fresh confirmation.

Independent review verified all 4,685 finalized JSON artifact hashes, frozen
inputs, unchanged before/after source and container snapshots, and the same
logical SQL and parameter objects on the wire for every measured query. All
9,216 projected result rows—including text and metadata—equal the corresponding
payload returned by the full-document reference queries. Both vector clients
achieved tie-aware exact recall of 1.0 at 8 and at 16.

| Client and route | Distinct lists per 48 questions | Mean captured RU/query | Median instrumented latency |
| --- | ---: | ---: | ---: |
| Python vector, library prefix | 48 | 11.2671 | 90.4 ms |
| .NET vector, library prefix | 48 | 11.4781 | 138.9 ms |
| Python hybrid, library prefix | 1 | 9.6800 | 87.1 ms |
| Python hybrid, WHERE only | 48 | 82.2890 | 798.1 ms |
| .NET hybrid, library prefix | 48 | 71.0596 | 627.2 ms |
| .NET hybrid, WHERE only | 48 | 70.3196 | 631.7 ms |

The cheap Python prefix-hybrid row is the reproduced ranking failure. It is not
an acceptable efficiency result.

.NET prefix and WHERE hybrid have identical top16 order in all 96 matched pairs.
Python and .NET vector do too. Python versus .NET WHERE hybrid has the same top16
set in all 96 pairs and the same order in 92. The four order differences are
adjacent swaps for two questions, repeated in both measured repetitions:
positions 8/9 for hq-19 and 10/11 for hq-24. Their cause remains unresolved; this
review does not label them score ties or claim an independent exact-RRF oracle.

On the 40 answerable questions, mean expected-document presence at 8 is 93.75%
for vector and 98.75% for the functioning hybrid cells; all reach 100% at 16.
This measures document presence, not required-passage coverage, answer quality,
citation correctness or abstention. Python's broken prefix-hybrid cell reaches
16.25% at 8 and 37.5% at 16.

## Ranking, routing and RU are separate findings

The raw plans and data requests show different client pipelines. Python WHERE
hybrid sends the original request, receives HTTP 400/substatus 1004, fetches a plan
and partition-range metadata, then executes global statistics plus two ranking
components: six wire requests per measured query. .NET goes directly through a
plan and the three component requests: four. Those extra Python responses have
no reported RU charge, so they explain request/latency overhead but cannot alone
explain the measured RU difference.

| Hybrid pipeline component | Python WHERE RU/query | .NET prefix RU/query | .NET WHERE RU/query |
| --- | ---: | ---: | ---: |
| Global full-text statistics | 4.1608 | 4.1608 | 4.1608 |
| Vector component | 29.4796 | 28.6996 | 28.3296 |
| Text component | 48.6485 | 38.1992 | 37.8292 |

The largest observed charge difference is the text component. Python receives
component plans exposing non-streaming ORDER BY metadata and executes wrapped
`orderByItems` projections; .NET receives different component-plan metadata and
executes native `componentScores` projections. Both ranking components request
TOP 120 internally and return all 36 owned chunks in this small corpus. Native
rewrite and response-format differences were not independently ablated, so the
review does not attribute the cost reduction to one internal mechanism.

.NET prefix hybrid retains library effective-partition-key bounds on both
ranking components. Its global full-text-statistics request has neither a
library WHERE filter nor library EPK bounds. The WHERE-only hybrid requests have
no library EPK bounds. Thus prefix routing improves the observed bounds of the
ranking components, while service-default statistics remain container-wide.

The captured physical partition inventories—101 responses across the SDKs—show
one range covering the complete keyspace. All eight documents and all measured
successful data responses resolve to that range. This cannot establish absence
of unnecessary fan-out on a split container, and it does not overturn R1's
previous two-range observation. On this one-range target, .NET prefix hybrid
costs slightly more than .NET WHERE hybrid, despite retaining those bounds.

The .NET prefix hybrid cell uses about 13.6% less captured RU than Python WHERE
hybrid, with about 21.4% lower median instrumented latency. It still uses roughly
6.2 times the .NET vector RU. These are matched serial observations on this
corpus, not production latency/concurrency or architecture-wide guarantees.

## Accounting and observation limits

The accepted run's measured queries report 24,584.96 known wire RU. References,
warmups and snapshot/metadata work add 2,202.37, for 26,787.33 observed RU in run 2.
The failed first run adds 11,957.61 observed Python RU. Its .NET metadata requests
failed during header observation, so their charges are unavailable. Across both
SDK runs the observed lower bound is 38,744.94 RU. Provisioning/health checks,
ingestion, lifecycle smoke, archival work, model usage and other services belong
to separate coordinator accounting; these numbers do not include them.

For every .NET measured query, SDK page RU equals the captured wire charge sum
within floating-point tolerance. They must not be added. Missing response charges
remain unknown, and observed RU is not total billed cost.

There were no measured-query transport errors or HTTP 429s. One Python account-root
GET timed out after 40.002 seconds during preparation and finished 2.127 seconds
before the measured matrix began. Its precise network cause is unresolved.
The repeated preparation data-request signature is the deliberate before/after
snapshot query, not evidence of a retry. Configured throttle retries were zero;
total hidden SDK retries remain unknown.

All 384 measured query-plan bodies parse as JSON. However, the collector decoded
.NET data-response bytes as UTF8 text. All 672 measured .NET data responses are
non-JSON text, and 679 responses including warmups contain replacement characters.
The original-byte SHA256 hashes remain, but those original bytes cannot be
reconstructed from the captured strings. Binary serialization is a plausible
explanation; the necessary serialization-format headers were not captured.
The SDK-parsed result rows, charges, routing headers and JSON plans remain
available and independently checked. Do not call this lossless wire-body
archival. Before another client study, preserve non-text bodies losslessly in
private storage and capture supported-query-feature/serialization headers.

Latency includes SDK/runtime behavior and synchronous capture overhead. Both
clients use Gateway, a single connection per host and serial logical queries,
but their internal pipelines and response encoding differ. David Ames's exact
SQL, request options and partition layout remain unknown, so this is a controlled
SDK comparison rather than a replication of his environment.

The result supports .NET prefix ranking on the current foundation and preserves
the observed gain in expected-document presence. It leaves large-container routing costs,
exact hybrid ordering and final answer gains to their separately controlled
experiments. It does not justify changing application defaults by itself.

The committed [derived matrix receipt](../records/hybrid-sdk-comparison-20261104.json)
and [independent review receipt](../records/hybrid-sdk-comparison-review-20261104.json)
record the accepted findings. The [invalid first-run receipt](../records/hybrid-sdk-comparison-invalid-run-20261104.json)
retains its status and known cost without treating missing .NET observations as
zero. Raw evidence and environment identifiers remain in private archives.
