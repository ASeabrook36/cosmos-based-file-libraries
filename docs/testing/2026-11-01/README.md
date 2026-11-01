# Retrieval recovery and grounding regression cases

This versioned pack retains the two 29 October cases and adds eight cases over the
same two public DOCX assessments. The ten cases produce eleven turns because the
consultation case deliberately shares history across two questions. Every other
case starts fresh. This broadens task coverage within a fixed corpus; it is not
representative coverage across document domains or formats. Held-out documents and
cases, when present, are documented separately.

`source-1.docx` is the draft integrated impact assessment; `source-2.docx` is the
draft regulatory impact assessment. Source byte hashes and actual library IDs
belong in private run snapshots. `fixtureParagraphsZeroBased` refers to the retained
source-text fixture's original paragraph sequence, not an API selector, provider
section ID or chunk index. Microsoft output may combine many such paragraphs into
one section. Ground truth is the source assertion, independent of chunk boundaries.

Use `scripts.evaluate_agent_retrieval` with the existing current library and the
complete-strategy and controlled-component pairs. Match model/version, instructions,
context budget and case order; repeat independent sessions. Keep corrected zero
character/token overlap controls, source hashes and provider-text digests frozen.
Run the original cases first at the original 24,000-token budget for comparability;
record any separate larger-budget experiment explicitly. Use functional-smoke mode
unless each library's index readiness is established. No source-fact list is sent
to the answering model.

Automatic indicators are deliberately limited: regexes find expected wording in
an answer, file-name mentions find possible citations, and tool receipts establish
whether original body text from the requested files reached the model. These
indicators **do not** establish semantic correctness, quote attribution, source
relevance, exhaustive absence, complete-file coverage or task success. For example,
“the later date is not specified” contains `later` while failing the deadline case.
Read the captured answer and supporting original text before assigning those
judgments. Metadata-only catalogue tasks legitimately require no body evidence.

Absence cases target explicit source statements that a forecast or count is
unavailable; they are not claims that searching some passages proves global absence.
The child-participation case tests an explicit “none undertaken” statement, while
the false-premise and follow-up cases distinguish proposals/conditions from automatic
entitlements. Both-file comparison/monitoring cases require relevant body evidence
from both files; merely listing or quoting generated descriptions does not qualify.
All private captures stay outside the repository. Publish only sanitized receipts
and distinguish assistant review from independent human quality assessment.

Each new run must use an empty output directory. The harness freezes its cases and
captures document records (including tags/descriptions/versions), library settings,
agent instructions and tool definitions before and after replay. Any change makes
the comparison confounded. This includes baseline and follow-up runs, not just
paired runs. Preserve interrupted directories: `run-status.json` records failure
and completed turns (including explicit `interrupted` status for `KeyboardInterrupt`), while failed-attempt model usage remains unavailable unless
separately measured. Attachment URLs and expiry credentials are removed from tool
captures as well as traces. Snapshots and raw answers remain private review material.

The original two cases exercise all five configurations: current processing,
complete custom, complete Microsoft, controlled custom and controlled Microsoft.
The initial expanded and held-out runs target current processing and the two
complete strategies. Extending those packs to controlled pairs remains useful
follow-up work; do not imply that an unrun configuration passed. The held-out pack
uses its own matched source corpus and fresh sessions. Do not combine disjoint
corpora in a library or call the expanded corpus a repeat of the original
two-question timing benchmark.

## Controlled answering-model follow-up

Install the optional `comparison` extra for the tested Responses SDK, for example
`pip install -e '.[comparison]'`. The ordinary demo transport remains compatible
with its existing dependency range. Cloud replay used OpenAI SDK 3.37.0.

`scripts.compare_agent_models` reuses this replay harness with a private model and
library configuration and fresh private output directory. It runs eight repeated
mini Chat Completions bridge turns, then 52 Responses turns comparing mini and a
stronger model at 24k, then eight mini-only Responses turns at 64k. The bridge and
context subsets use both Microsoft configurations; the main comparison uses all
five original configurations plus the three held-out complete strategies.

Both model arms use medium reasoning, an 8,000-token output cap, identical prompts,
tools and source snapshots. Model order reverses on the second repeat. Source and
code hashes are checked globally and per run. The bridge measures the API plus
reasoning-continuity effect; it is not a wire-protocol-only comparison. Provider
cache state and actual inference region remain uncontrolled. Report measured
reasoning/cache usage and quota waits separately; equal reasoning labels or token
rate caps do not establish equal compute or latency. Raw captures remain private.

The completed follow-up added 32 turns at 100,000 and 200,000 total-context
allowances: each budget × both models × both Microsoft configurations × two
independent repeats × the two original questions. This was a separate serial
driver around the same `replay`, `default_chat_factory`, `snapshot` and
`code_hashes` functions, after the 68-turn schedule finished. Its SHA-256 and the
parent controls hash are in the
[sanitized record](../../../records/model-context-comparison-20261101.json);
the private driver retains environment bindings and is not a repository entry
point. To reproduce, retain the same private configuration and frozen original
case pack, loop over that matrix with fresh output directories, reverse model
order on repeat two, and enforce the same global/per-run snapshots. Do not change
tools, prompts, per-call read defaults, output reserve or source generations when
changing the conversation allowance. No expected source facts enter model prompts.

Record the allowance separately from maximum provider-reported input/total tokens.
The 100-turn experiment peaked at 81,769 input tokens; 200k was an allowance, not
a genuinely 200k input test. No 500k workload was run. A separate direct complete
read with a 100k evidence allowance followed all model runs and did not warm their
sources. Its token counts estimate serialized evidence JSON, not model input.

Review task completion, decisive evidence delivery, factual support and citation
attribution separately. A correct core answer can fail a quotation/source check;
a safe refusal has null support/citation scores, not automatic passes. Review
packages omitted model labels, but reviewer coordination exposed some stage
context; report these as assistant judgments, not blinded human validation.

For the next ingestion comparison, run all three strategies and both controlled
members with adequate shared context on representative longer/multi-file tasks.
Use the constrained-budget runs to assess efficiency separately. The larger-context
follow-up here covers only the two Microsoft configurations and cannot establish
a custom/Microsoft winner. Lost original table-cell/visual information also cannot
be restored by increasing context.

Capacity must include response overhead: the direct custom two-file read estimates
92,900 evidence-JSON tokens across 627 sections, versus 20,086 across two Microsoft
sections. A 100k conversation allowance cannot fit the custom response plus the
existing prompt and answer reserve. Choose sufficient shared capacity for the
comparison; evaluate response compaction separately. Both Microsoft variants
reconstruct two full-read sections despite different search chunk sizes, so include
selective-retrieval tasks alongside full-document synthesis to test chunk behavior.
