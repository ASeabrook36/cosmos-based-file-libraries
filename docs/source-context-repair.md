# Separately qualified source and context repairs

This work starts from completed experiment commit
`4ad228d05ed2517cda743e95470599e09898d4f9`. The completed SDK/model comparisons,
their controls, answers, judgments and private archives remain immutable. No new
confirmation question is selected or answered, and the proposed larger study is
not launched. Main, deployed applications and application defaults are unchanged.

## Independent implementation tracks

| Track | Isolated branch | Intended change | Fixed controls |
| --- | --- | --- | --- |
| Source fidelity | `codex/source-structure-repair` | Opt-in validated paragraph/table/list source representation | Existing Microsoft passage text, embeddings, query policy, model and context behavior |
| Context accounting | `codex/context-accounting-repair` | Opt-in accounting for complete tool-message envelopes and subsequent protocol messages | Existing representation, sources, selectors, model, instructions and context/output limits |

The coordinator branch is `codex/source-context-repair`. Each track has a separate
worktree, local dependency environment and private runtime directory. Integration
keeps separate commits and standalone qualification receipts. Old experiment
artifacts are read-only inputs; results from repaired code cannot be relabeled as
results of the completed experiments.

## Qualification boundaries

Source qualification uses the already-consumed hq-05 table cell implicated in
hq-40, retained original bytes and provider annotations. Preserve ordered original
paragraphs, cell topology, numbering properties and source identity without
inventing qualifier scope. Unsupported or ambiguous mappings must fail explicitly.
Complete, selected and passage-expanded source reads must keep permission,
snapshot, metadata, budget and lifecycle guarantees. Re-embedding and the full
custom hierarchy remain separate experiments.

Context qualification uses synthetic boundary cases and offline reconstruction
of the archived local context stops, including hq-38. Count the full projected
message envelope, remaining tool-call replies, image delivery metadata and
reminders while retaining the output reserve. Provider input usage remains measured;
local estimates and unknown usage remain labeled. A local fit check does not prove
provider-token equivalence, context adequacy, model recovery or better answers.

Both tracks require independent code/evidence review, focused regression tests,
the repository suite, lint and reference-privacy checks. Lifecycle checks with
local substitutes establish application contracts, not distributed Azure behavior.
Initial work makes no cloud/model calls or resource changes. Any later live step
must be separately bounded and announced, with exact owned resources, usage limits
and cleanup specified before execution. The larger confirmation requires its own
future launch decision.

## Acceptance and final reporting

Report each repair's tested behavior separately, including default-path
compatibility, source/vector invariance, historical-failure reconstruction,
negative and lifecycle cases, observed limitations and executable reproduction.
Preserve raw source, transcripts, credentials and environment identifiers outside
Git. Publish content-free, hash-bound receipts. State exact untested live and
quality gates without claiming that local mechanics settle them.

The [source-fidelity proposal](source-structure-confirmation-proposal.md) defines
the larger study's prerequisites. The [completed comparison report](hybrid-client-model-pilot-results.md)
remains the authoritative account of the prior measurements.
