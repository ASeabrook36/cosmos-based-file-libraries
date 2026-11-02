# R0 frozen retrieval pilot

`manifest.json` freezes twelve real, original DOCX files and `tasks.json` freezes
48 source-grounded tasks: 32 development and 16 held-out. Documents cover housing
impact assessments, planning/deposit forms, a long transport consultation-response
compilation, and environmental enforcement policies, annexes and forms. No source
file or extracted source body is committed. Public acquisition links and byte
hashes identify the originals; filenames are generic aliases, with no content
conversion. This is a purposeful public-policy pilot, not an accuracy estimate
for arbitrary enterprise libraries. Seven files share one consultation and are
not independent samples.

Eight files are development inputs. The four DOCX files from E62/E63 are retained
as regression cases; the two filenames beginning `heldout-` are **development
regressions now**, not R0 held-out evidence. The four new held-out files are EA
annexes 3/4/5 and its response form. They are inspected to author source labels,
but receive no answering replay or extraction-performance scoring in R0. The
`sanction-case-classification` task family occurs only in held-out tasks; those
questions must not select development candidates. The old history-sharing
consultation regression remains in its previous pack. R0 separates its two
questions into independent tasks and makes this adaptation explicit.

The baseline is deliberately bounded to five documents (`d01`, `d02`, `d03`,
`d04`, `d06`) and six tasks (`r0-01`, `02`, `03`, `11`, `17`, `32`) across current,
custom and Microsoft profiles, twice each. The second repeat reverses profile
order. This covers a buried date exception, cross-file reasoning, numerical
qualification, table/form comparison, an explicit complete-section request, and
filtered catalogue enumeration. The other development tasks are frozen but are
not implied to have passed live. The local extraction baseline uses all eight
development originals. The taxi compilation is a genuine large-source selective
retrieval stress case, excluded from this bounded cloud ingestion run. An allowed
200k conversation does not establish a tested 200k input.

The model is fixed independently of the Codex implementation model: GPT-6.1 Sol
revision `2026-09-29`, Azure Responses, medium reasoning, 8,000 output tokens,
three provider attempts, 200,000 conversation tokens per run/member, no provider
conversation storage or automatic truncation. Actual prompts, tools, model and
embedding revisions, profile recipes, generations, metadata versions, query
arguments, retry usage and SDK versions belong in runtime snapshots/receipts.
The manifest defines the baseline defaults; an agent's actual tool choices and
budget recovery must also be measured. Complete-strategy chunk targets differ
intentionally (500/50 tokens for current/custom, 5,000 tokens/200 **characters**
for Microsoft). Controlled-component work uses matched layout extraction,
500-token targets and zero overlap. Measure actual distributions in both modes.

## Reconstruct and verify

Set `R0_CORPUS` to a private directory outside every checkout. Originals already
available locally can be copied there under the manifest filenames. Verification
checks exact bytes, every region hash and split integrity without network access:

```sh
python -m scripts.retrieval_r0_corpus verify --source-dir "$R0_CORPUS"
python -m scripts.retrieval_r0_corpus verify --source-dir "$R0_CORPUS" \
  --gold-output "$R0_PRIVATE/gold-reconstructed.json"
```

The output gold file must be new and private. It can be regenerated from the
originals and committed labels; no undocumented runtime fixture is necessary.
For explicit public-source acquisition into a fresh private corpus directory:

```sh
python -m scripts.retrieval_r0_corpus fetch --source-dir "$R0_CORPUS" \
  --gold-output "$R0_PRIVATE/gold-reconstructed.json"
```

Downloads are opt-in and never part of local environment setup. The command
refuses changed upstream bytes and never overwrites an existing original. Some
public hosts may reject downloads; a matching preserved original remains valid,
whereas an HTML error page, PDF substitute or updated DOCX does not. Preserve
failed acquisition receipts privately. The full task pack hash is pinned in the
manifest. Record any correction as a new benchmark version before comparison.

## Source-based scoring

Labels address original DOCX top-level paragraphs or original visible table-cell
coordinates, all zero-based, and include source-byte and normalized-text SHA-256.
Normalization is Unicode NFKC and whitespace collapse only: case and punctuation
are retained. These are **not** API section selectors, chunk IDs or rendered page
numbers. Merged-cell aliases are deduplicated; table row/column relationships and
blankness remain explicit. E62's source-1 fixture counted tables in its paragraph
sequence; R0 re-resolves its labels against the original paragraph sequence rather
than copying those incompatible offsets.

Score four dimensions separately, per task and repeat:

| Dimension | Rule |
| --- | --- |
| Relevant-source coverage | Determine whether each labeled source assertion or relationship reached the model as original evidence from the correct file/revision. Required complete-section tasks need every nonempty source region in order. Catalogue tasks use exhaustive matching metadata with pagination, not body-text coverage. |
| Task completion | All requested deliverables and qualifications in `successCriteria` are met. A correct date with the wrong deadline rule fails. A partial section read fails a complete-section request. |
| Factual support | Every material factual claim is entailed by delivered original evidence; preserve proposal status, uncertainty, exceptions and the scope of absence. Distinguish advice added by the model from source statements. |
| Citation accuracy | Each material claim/quotation points to the correct supplied file and source location. Mere filename occurrence is insufficient. Quote changes or attribution to the wrong file fail independently of task completion. |

For task completion, support and citation review, record `true`, `false`, or
`null` (not assessable), plus evidence-based reasons and reviewer identity/type.
A pure safe refusal fails completion and has null support/citation scores; it is
not three passes. An answer can pass completion while failing attribution.
Report applicable counts and paired failures, not a single pilot accuracy claim.
Review is assistant assessment unless an independent human actually performs it.

Exact normalized text matches are a reproducible **delivery indicator**, not a
semantic relevance judgment. They can miss equivalent punctuation, fragmented
delivery or headings represented separately. Short cells such as “Yes” can match
the wrong place. Never count an empty string as delivered evidence. Table tasks
require manual relationship/blankness review; if extraction loses that information,
record unavailable or failed fidelity rather than manufacture a selected choice.
Checks against original source and checks against what reached the model answer
different questions. Keep both in the scorecard.

Expected assertions and source labels remain evaluator data and never enter the
answering model's question, instructions, tools, messages or retrieved documents.
The existing replay sends only questions and captured authorized tool evidence.
Do not adapt prompts after seeing held-out results. PDF, visuals, handwritten
responses, Office charts, spreadsheets and attachments are capability gaps outside
this matched DOCX run, not silently excluded product capabilities.

Classify failures separately as source/extraction loss, retrieval miss,
incomplete selection, selector misuse, context limitation, model factual error,
citation error, permission/lifecycle violation, or infrastructure failure. Usage
must include failed attempts and retries where observed; keep known subtotals and
unavailable totals distinct. Serialized-response/source token counts are estimates;
provider counters are measured usage. Neither is a complete currency bill.

Before later comparisons, retain the manifest's provisional efficiency gate:
20% improvement in the targeted cost or latency measure with no observed
source-quality regression, followed by held-out confirmation. This is not proof
of statistical equivalence. Permission, metadata, provenance, generation,
replacement/deletion and retry failures block promotion. Retain the simpler
baseline when evidence is inconclusive.
