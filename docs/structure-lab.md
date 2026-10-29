# Structured retrieval: executable Cosmos experiment

This opt-in lab tests a common document representation in two Cosmos layouts.
It does not change the API, worker or compact REST contract. The initial live
results are in [the sanitized experiment record](../records/structure-lab-20261028.json).
The broader direction is in the [research assessment](consults/2026-10-28-document-understanding-research.md)
and [implementation proposal](consults/2026-10-28-document-understanding-proposal.md).
The follow-on [Microsoft reuse assessment](consults/2026-10-28-microsoft-reuse-compatibility.md)
records a pinned template inspection and six live extraction comparisons, including
a chapter-boundary failure. See its [runbook](microsoft-reuse-lab.md). The subsequent
[deployed Indexer assessment](consults/2026-10-29-indexer-workflow-experiment.md)
and [workflow runbook](indexer-workflow-lab.md) cover actual output, connector,
publication, lifecycle and isolation tests. KBaaS external evidence remains an open gate.

## What runs now

The first adapter preserves Markdown ATX heading ancestry and exact Unicode text
blocks. It recognizes fenced code and treats pages as source locations rather
than hierarchy levels. Plain text becomes a document with no inferred sections.
Genre is supplied explicitly; heading-based role hints are deterministic and
fall back to `unknown`. This is not an automatic genre classifier or a general
Markdown parser: Setext headings, embedded HTML and complex nesting need adapters.

Each node receives a generated discovery summary. Parent summaries reuse child
summaries; bounded intermediate reductions cover long text without clipping it.
Generated summaries are lossy representations, so processing every source block
does not guarantee that every fact survives. Source passage retrieval remains
available independently. Invalid or oversized summaries get at most two retries;
failed validation prevents freezing an artifact. Original text stays intact.

The cache includes model configuration/revision, tokenizer, context, source and
child dependencies. Successful model calls and embedding batches checkpoint to
private files. The library overview depends on the current inventory; it never
overwrites the owner description. An empty library uses a deterministic empty
overview. Removing a document can reuse a previously cached inventory overview.

Every freeze creates a new isolated library snapshot. Source blocks, hierarchy,
summaries, rows and query vectors are hashed together. The same frozen records
are copied to both layouts:

| Layout | Container | Contents |
|---|---|---|
| Shared | `lab-shared` | Passage and summary vectors |
| Split | `lab-passages` | The identical passage vectors |
| Split | `lab-summaries` | The identical section, chapter and document summary vectors |

All use `/libraryId` then `/documentId` as partition keys, `/libraryId` as vector
shard key, and the existing 3,072-dimensional float16 vector policy. No container
per level, genre or library is needed. Container definitions live in `cosmos/`.

Typed queries select a result level and record kind, bind one library, optionally
scope to a document, and retain `TOP`, float16 conversion and correct hybrid
routing. Summary and passage IDs both resolve to source node IDs. A subtree read
returns the original blocks in order, deduplicates overlapping selections and
refuses the whole selection if its serialized evidence exceeds the budget.

## Run the experiment

Install the normal repository dependencies. Keep runtime configuration, corpus
artifacts, generated summaries and receipts outside the checkout. The driver
refuses a work directory inside the repository. Use a different work directory
for concurrent runs; the private JSON caches are single-writer files.

Deploy the opt-in `infra/structure-lab.bicep` into an existing reference account
and database, setting `accountName`, `databaseName` and the actual `capacityMode`.
This is a separate deployment from `infra/main.bicep`. Provisioned mode adds
dedicated throughput for each experimental container; do not confuse the cost
of running both variants with the cost of choosing one production variant.

Load the existing private Azure environment settings and authenticate with Entra.
The lab uses `DefaultAzureCredential`, never account keys. Then run the standard
canary for **each** experimental container, with real source text sufficient for
at least 1,200 chunks:

```sh
python scripts/canary.py --corpus "$CANARY_CORPUS" \
  --index-container lab-shared --records-dir "$LAB_RECEIPTS/shared"
python scripts/canary.py --corpus "$CANARY_CORPUS" \
  --index-container lab-passages --records-dir "$LAB_RECEIPTS/passages"
python scripts/canary.py --corpus "$CANARY_CORPUS" \
  --index-container lab-summaries --records-dir "$LAB_RECEIPTS/summaries"
```

These canaries clean up their test libraries by default. They validate each
container using ordinary real passages, not the later structured workload.
The driver verifies stored definitions and requires passing receipts bound to
the account/database/container incarnation before any live load or evaluation.

```sh
python scripts/structure_lab.py freeze \
  --manifest tests/fixtures/structure-lab/manifest.json --work-dir "$LAB_WORK"
python scripts/structure_lab.py load \
  --work-dir "$LAB_WORK" --canary-records "$LAB_RECEIPTS"
python scripts/structure_lab.py evaluate \
  --work-dir "$LAB_WORK" --canary-records "$LAB_RECEIPTS" --repeats 3 --hybrid
python scripts/structure_lab.py read \
  --work-dir "$LAB_WORK" --document research --nodes n5 --max-tokens 32000
```

`current.json` points to the latest `frozen-<digest>.json`. Supply `--artifact`
with that filename to load, evaluate, read or clean up an earlier snapshot.
Reads save private `selected-source.json`; console output contains no source
text. Evaluation records contain IDs and measurements, not query/summary text.

For incremental ingestion, freeze the original manifest again, then
`manifest-added.json`, then the original manifest. All three use the same work
directory. Existing summaries/embeddings are reused. The added manifest includes
a grant letter. Each freeze has its own isolated library ID; this demonstrates
dependency reuse, not concurrent publication into a live mutable library.

`--model-revision` must change if a deployment's underlying model version changes.
Endpoint/deployment identity alone does not detect such an in-place change.
Usage files report the current invocation, including completed validation
retries; they do not reconstruct usage from earlier aborted invocations.

Cleanup removes only the frozen artifact's known rows from the lab containers:

```sh
python scripts/structure_lab.py cleanup \
  --work-dir "$LAB_WORK" --artifact "$FROZEN_FILENAME" --canary-records "$LAB_RECEIPTS"
```

## Demonstrate the value to an agent consumer

The fixtures are deliberately small and synthetic. They demonstrate operations,
not relevance improvements or scientific/legal facts.

| User intent | Retrieval and source read to demonstrate |
|---|---|
| Find a report comparing drainage and salt exposure | Search document summaries; read the selected report or its outline |
| Read the chapter about vegetation and salinity | Search chapter summaries; expand `research/n5` to its exact source subtree |
| Compare the study's two conclusions | Read `research/n4` and `research/n8`; avoid presenting summaries as quotations |
| Identify what an opinion orders disclosed | Search section summaries in the opinion; read the returned source sections |
| Retrieve a specific measured duration | Search ordinary passages; hierarchical navigation is optional |
| Add a grant letter to an established library | Generate the new document's summaries and refresh the overview using cached summaries |

The current harness resolves candidates locally from its frozen source artifact.
Serving these operations to a real agent requires the existing authorized REST
boundary and revision checks. It has no independent public HTTP service or agent
answering loop. The current UI does not expose these lab operations.

## Measurements and interpretation

The three index canaries passed on 1,500 real book passages each, with mean
vector recall@10 of 0.985. Index engagement took 215–400 seconds. Those runs
validate the configuration; they do not compare hierarchical retrieval quality.

The structured smoke run used 23 passages and 23 summaries across three files,
six queries, both layouts, vector/hybrid modes and three repetitions: 72 measured
retrievals with no errors. Both layouts retrieved the fixture's labelled documents.
Some questions already scope to the correct document, making those document
labels a plumbing check rather than a useful relevance test. No storage winner
is selected, and these small candidate sets are not a scaled ANN benchmark.

The selected salinity chapter required 311 serialized evidence tokens versus
688 for the whole report. This is a source-scope demonstration, not a general
token-savings claim. Repeating ingestion used zero model/embedding calls. Adding
the letter required five summary calls and one embedding request, while reusing
the 23 existing document/section summaries. The owner description was preserved.

The four-document snapshot was also loaded and searched: 84 additional matched
retrieval measurements completed without errors, and all 12 measurements of the
new letter query found that document across layouts, modes and repetitions.

Evaluation alternates which layout runs first and retains raw per-query RU,
elapsed time, retrieved-document count, exact-vector reference cost, ANN recall,
labelled document recall and expanded-source token count. Hybrid has no claimed
exact reference. Source expansion is local in this slice, so its Blob/REST cost
and independent-client visibility are not measured. Inspect readiness on the
actual corpus before making performance conclusions; the driver labels those
conclusions unassessed instead of treating a passing canary as permanent readiness.

## Next build steps and acceptance

1. **Microsoft reuse experiment first.** Evaluate Document Indexer and Logic Apps
   KBaaS on the same files in isolated resources. Preview status is acceptable.
   Inspect actual schemas, retained source links, update/delete behavior and
   extensibility. Adopt working components through an adapter; write custom
   ingestion only for identified gaps. See the proposal's reuse checklist.
2. **Preserve real structure.** Retain Document Intelligence section/element
   relationships before flattening. Add a DOCX heading-level adapter and figure
   membership. Prove source coverage across page batches, correct ancestry and
   explicit ambiguity on a multi-chapter PDF and a native Office file.
3. **Run a meaningful A/B evaluation.** Assemble 40–60 diverse real documents and
   150–250 independently authored, held-out tasks. Include vague file finding,
   rare facts, section comparisons, cross-document comparisons and images.
   Judge source regions and grounded answers, not just document IDs. Compare
   passage-only versus structural retrieval separately from shared versus split
   storage, with fixed model/context budgets and repeated trials.
4. **Measure density and scale.** Include small libraries and sufficiently large
   real corpora for summary indexes to engage. Vary passage-to-summary ratio and
   selective level/role filters. Record index readiness, recall, p50/p95 latency,
   total RU, writes/deletes, storage, model usage and workflow overhead. Estimate
   each chosen layout independently of the duplicated A/B deployment.
5. **Integrate the proven contract.** Add typed candidates and source expansion
   behind compact REST, preserving library binding and personal-library checks.
   Test replacements, deletes, stale handles, interrupted enrichment and
   independent-client consistency before changing the normal worker or UI.

Record the selected storage layout only after these results. Microsoft preview
components may replace parts of the lab pipeline; the frozen-artifact comparison
and source/summary distinction remain useful regardless of ingestion provider.
