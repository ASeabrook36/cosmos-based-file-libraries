# Separately qualified source and context repairs

The known DOCX qualifier boundary is preserved by an opt-in source profile, and
the context admission mismatch has a separately tested opt-in repair. These are
local regression results, not new model-quality measurements. The
[completion receipt](../records/source-context-repair-20261104.json) binds the
implementation, tests, independent reviews and private archive.

This work starts from completed experiment commit
`4ad228d05ed2517cda743e95470599e09898d4f9`. The completed SDK/model comparisons,
their controls, answers, judgments and private archives remain immutable. No new
confirmation question is selected or answered, and the proposed larger study is
not launched. Main, deployed applications and application defaults are unchanged.

## Independent implementation tracks

| Track | Isolated branch | Implemented change | Fixed controls |
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

## Measured local findings

### Source representation

The [source repair report](source-structure-repair.md) and
[qualification receipt](../records/source-fidelity-qualification-20261104.json)
record all 682 original main-body paragraphs, 474 table cells, 47 numbering
definitions and five explicitly distinguished provider-only paragraphs. The known
qualifier cell retains ten paragraphs, eight with numbering properties, and a final
separate unnumbered paragraph. Independent ZIP/XML review checked original text,
runs and cell coordinates. The old artifact still reproduces the original loss.

The new profile keeps all archived passage text, locations and vectors identical.
Full/selected source reads use the same immutable, hash-bound sections artifact;
permission, metadata, snapshot and lifecycle regressions pass with local substitutes.
Independent review also exercised six atomic two-document race cases. The usable
navigation route is search → outline → pinned section selection. Direct passage
`evidenceId` expansion remains unavailable because the existing Microsoft passage
locations are empty; no guessed mapping was introduced.

The complete compact source in the fixed generic envelope is **86,603 local tokens**;
the selected qualifier cell is **876**. The full file therefore still exceeds the
pilot's 64k operating context. These are serialization measurements, not measured
provider tokens, latency or billing. Printed list ordinals remain unresolved;
qualifier meaning is not inferred. Supported main-body structure is preserved,
with explicit rejection or scope labels for unsupported features. This does not
implement the full custom document hierarchy.

### Context admission

The [context repair report](context-accounting-repair.md) and
[qualification receipt](../records/context-accounting-qualification-20261104.json)
reproduce all 18 historical local stops exactly. Applying `envelope-v2` to the fixed
recorded candidates permits a bounded next continuation in **six** cases; **twelve**
still cannot fit the minimum tool replies. Across the six cases, one recorded
candidate is admitted and six are wholly refused. Earlier received evidence is
retained; oversized new source bodies are never silently truncated.

For hq-38, the historical guard was **64,843/64,000**. The search candidate measured
29,358 tokens by itself but added **31,215** inside its message envelope. Retaining
the outline and returning a whole-body refusal for that search gives **33,703/64,000**,
including the unchanged 8,000-token output reserve. This creates room for a future
model response; it does not demonstrate recovery or a correct answer.

Independent review verified 72 archived artifact bindings, all guard calculations,
default transcript compatibility and image/grounding edge cases. Full-history
resumption has a conservative limitation: after an unscoped withholding marker,
explicit multi-file synthesis requires a fresh retrieval session, even if later
reads occur in that resumed transcript. The mode is not ready for default promotion
or a claim of general same-chat recovery.

## Reproduction and frozen evidence

Final stable local checks passed on each branch:

| Branch | Repository tests | Implementation commit |
| --- | ---: | --- |
| `codex/context-accounting-repair` | 1,434 | `44708040dce0a6d4cd5b44205a07a5d7e54404a7` |
| `codex/source-structure-repair` | 1,472 | `33402d3bf66c5ad6ba2d0b12c0c46cba66695aff` |
| `codex/source-context-repair` | 1,507 | `4cef2d01bfdca53fd9d68cf146ae65827a8768a7` |

Lint, formatting and reference-privacy checks also passed. All suites reported
14 existing Starlette/httpx test-client deprecation warnings. The combined total
includes 71 source regressions, 35 context regressions and two synthetic frozen
workflow-drift cases. Independent final review accepted the production-guard
invariance and the defensive refusal when an artifact identifies itself as
structured but its profile and coverage metadata are missing.

Use `sh scripts/dev setup` once in each checkout, then `sh scripts/dev test -q`,
`sh scripts/dev lint` and `sh scripts/dev privacy --deny-file "$PRIVATE_DENY_FILE"`.
Each worktree has its own locked local dependency environment. Setup neither loads
credentials nor provisions cloud services. The dedicated reports give the private
input schemas and focused test commands.

Both offline qualification commands use retained inputs and exclusive new outputs:

```sh
.venv/bin/python -m scripts.qualify_source_fidelity \
  --inputs "$RETAINED_PROBE_INPUTS" \
  --output "$PRIVATE_RUNTIME/source-qualification.json" \
  > "$PRIVATE_RUNTIME/source-qualification-public.json"
.venv/bin/python -m scripts.qualify_context_envelope \
  --inputs "$PRIVATE_CONTEXT_INPUTS" \
  --output "$PRIVATE_RUNTIME/context-qualification.json" \
  > "$PRIVATE_RUNTIME/context-qualification-public.json"
```

The first is a counterfactual local rebind of the existing provider envelope to a
new profile/job, not a new provider execution. The second reconstructs the final
recorded continuation only, not changed model decisions. They remain separately
attributable on the coordinator branch. No consumed question becomes fresh evidence.

The completed checkout's 112 producer files and 13 published control/report
artifacts are checked against their starting hashes. Completed transcripts,
scores and durable archives are preserved. Old live harnesses intentionally still
reject changed measured code: only synthetic test fingerprints were isolated from
the frozen deployment, with negative tests for recipe/configuration drift. A future
live run needs a new versioned control envelope; bypassing those guards is not a
valid reproduction method.

Private runtime receipts, failed drafts and independent checks are archived outside
Git after writers stop. The public completion receipt carries archive/manifest
hashes, not private identifiers or raw source. The new archive supplements the
existing frozen private archives; it does not replace them or provide offsite backup.

## Next gate — prepared, not launched

The recommended next step is **one bounded live qualification with two separate
tracks**, before designing or launching the larger confirmation:

1. **Source track:** freeze new controls for the source-only profile; use isolated
   owned resources and a small supported DOCX regression pack. Verify actual provider
   output, immutable publication and search→outline→selected reads. Exercise
   permissions, replacement, metadata, retry, deletion and absence checks against
   Azure. Keep `legacy-v1` context behavior fixed and measure rendered-source sizes.
2. **Context track:** hold the original representation, client, model version,
   prompts, tools and common adequate context/output allowances fixed. Compare
   `legacy-v1` and `envelope-v2` on a small consumed regression pack, with fresh
   reference turns. Measure actual input, refusals, recovery searches, final quality,
   citations, useful latency and usage. Treat this as regression evidence, not fresh
   confirmation. Decide whether bounded persistent scope state is required before
   testing resumed multi-file conversations.

Before execution, inventory resources, confirm authorization/quotas, announce cloud
steps, set call/token/ingestion ceilings, serialize performance runs and specify
exact-owned cleanup. No deployment, resources or spending were incurred here.
Provider capacity, model recovery/quality, live distributed lifecycle, broader DOCX
coverage, printed ordinals and the full custom hierarchy remain open gates. The
proposed 40-document/180-question study is still only a proposal; none of its new
documents or questions was selected, authored or consumed by this work.
