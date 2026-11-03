# Hybrid versus vector-only source and answer quality

This experiment starts from reviewed integration commit
`3980333ee8453af48c7133cab24522868701d1cc` on
`codex/hybrid-quality-comparison`. Recall is the primary objective. No further
format, representation, model or efficiency tuning belongs in this comparison.
R0/I1 questions are consumed development/regression evidence; confirmation uses
new original sources and questions frozen before measured retrieval or answering.

## Controlled comparison

Both arms read one newly owned immutable library using the retained
`microsoft-indexer-layout-v1` profile, current physical policies and stored vectors.
Both use compact evidence and the accepted selector handles. Extraction, source
bytes, metadata, chunk boundaries, source generations, embedding model/revision,
filters, result limits and neighbor expansion remain equal. The original I1
18.8542783% input benefit remains accepted and is not retested as a selection gate.

Only search mode and its required working route differ. Vector search uses the
library partition prefix; hybrid omits the explicit partition option while
retaining the same single-library equality and TOP bound. Both retain the current
float16 query conversion, no embedding projection and default RRF/index settings.

The existing client lexical extractor lowercases the question, extracts ASCII
alphanumeric runs, removes duplicate terms in first-occurrence order and keeps
at most sixteen. It does not remove stopwords, preserve phrases or punctuation in
identifiers, or perform client-side stemming. Full-text policy is en-US; the
provider performs its own text analysis. RRF weights are omitted/default. The
16-term cap can omit later terms. These behaviors are frozen; they cannot be
changed after examining confirmation outcomes. No preparatory query correction
was identified in the initial audit.

## Measurement order and interpretation

Direct retrieval precedes answering. The primary direct comparison uses top 8
with neighbors enabled, identical cached question embeddings and alternating
arm-first order by question. The application's candidate window remains 16,
followed by active-generation visibility checks and the requested top 8. Initial
hits and neighboring source evidence are scored separately. Required original
region/fact recall, complete task evidence and useful evidence are separate
measures; ANN agreement with exact vectors is not semantic source recall.
Unlabeled material is unjudged, not automatically irrelevant. Unanswerable tasks
have no required-answer-region recall denominator; appropriate abstention and
absence-context usefulness are reviewed separately.

Answering uses frozen Sol `2026-09-29`, Responses, medium reasoning, 8,000 output
tokens, three attempts and a 200,000-token allowance per independent turn, with
storage and automatic truncation disabled. Two repeats interleave both arms per
question and reverse order in repeat two. Actual tool choices may differ in
response to retrieved evidence; they are preserved, not normalized away. Report
full-read recovery of a search miss separately from initial search recall.

Source/answer judgments cover completion, factual support, citation accuracy,
required-source coverage and appropriate abstention. Arm labels are hidden in
review packets where feasible; report the actual blinding and independent review
limitations. Each question is the paired unit; repeats are not new independent
questions. Aggregate and family-level wins/losses/ties retain their denominators.
The final recommendation follows source recall and answer quality, with measured
usage and incomplete cost/latency counters alongside it. No 20% efficiency veto
applies. Conditional recommendations must name the observed evidence and remain
explicit about any need for prospective confirmation.

## Cloud and safety boundaries

The coordinator owns cloud reservations and serial measurement windows. Existing
isolated validation services may host the new library; no frozen R0/P1/I1 scope,
ongoing scaling resource, container policy or existing worker configuration is
modified. New containers require a new canary. Reused containers require matching
incarnation/policy receipts. Actual-corpus readiness and meaningful hybrid ranking
must be inspected before measurement; missing readiness evidence forbids
steady-state performance claims rather than becoming an invented passing result.

The subsequent M1 report at `46ae45a` found scheduled orphan discovery targeting
a literal Blob container instead of its configured container. M1 changed no
application code and did not close that gate. This comparison does not repair or
waive it and makes no production lifecycle acceptance claim. Preparation waits
for completed source publication; owned-scope cleanup is independently verified,
including late-result checks, without changing shared scheduled jobs or claiming
manual cleanup proves scheduled discovery. Ordinary retention remains in force.

Raw sources, gold facts, provider messages, identifiers and credentials stay in
private runtime directories. Public artifacts contain reviewed hashes, benchmark
identifiers, counters, judgments, commands and limitations only. Frozen manifests,
actual runtime/deployment bindings and a single-use execution ledger prevent
selective reruns or silent control changes. All failed attempts remain evidence.

## Frozen preparation and ownership

The inherited container canary still matches actual policies and incarnation.
The deployed ingestion worker configuration is unchanged. Read-only snapshots of
all three R0 libraries (15 documents), the P1 library (five documents), and the
I1 library (four documents) match exactly. The [prospective plan](testing/hybrid-quality/plan.json) binds all eight original
source hashes, 48 questions, 104 original regions, 62 requested facts, the private
gold hash, and the query/answering controls. The source pack and both independent
original-source reviews precede all confirmation retrieval and answering.
Meaningful original filenames and supported title tags are retained as normal
metadata. The corpus is a purposeful procurement-contract pilot, not a broad
enterprise accuracy estimate.

Two distinct preparation queries are frozen separately. Actual publication must
yield more than 16 chunks, with at least six of eight documents having multiple
chunks. These gates prevent top eight from trivially covering the entire library.
The local estimate of 148,863 source tokens and 32 five-thousand-token units is
not a provider chunk count. Missing index-execution evidence prevents a
steady-state performance claim.

The coordinator owns one new evaluation library and, only for answering, one
exact-revision temporary Sol deployment. An external ownership ledger precedes
creation/uploads. Cleanup uses ordinary deletion and verifies scoped records,
chunks and live Blob prefix absence twice after ingestion has completed; soft
delete retention remains. Existing libraries, deployments, worker and scheduled
jobs remain unchanged. Raw sources and failed attempts remain private.

The historical Phase 5 suggestion to add a stop list is deliberately deferred:
this experiment compares the existing implementation under the user-requested
frozen controls. No stop list, weights, physical index, representation or
application-default change is included. Final results and reproducible commands
will be appended after the serial execution and independent source review.
