# Held-out form and table cases

`heldout-cases.json` contains three fresh-session cases over two public DOCX forms
that were separate from the original two assessment documents. They exercise a
printed deadline, explicit non-anonymous response instructions, a year-by-percentage
choice matrix, blank response fields, and a comparison requiring evidence from both
files. The cases use the existing `scripts.evaluate_agent_retrieval` harness; the
source facts and review criteria are evaluation data, never answering-model input.

The originals are copied byte for byte under generic filenames. Original source
paths, source-text fixtures, hashes, and upload/library receipts stay in private run
artifacts. These byte hashes bind the sanitized cases to the exact originals:

| Source | Bytes | SHA256 | Inspected text structure |
| --- | ---: | --- | --- |
| `heldout-planning-form.docx` | 109589 | `00a718302a8ad2b60bd283f6eabb2e9db4074c1d0a4d761b89cfd3758ae5dd7a` | 7 top-level paragraphs; 1 table, 23 rows |
| `heldout-deposit-questionnaire.docx` | 25314 | `f9c28875993e373d3e3af45f8cdb9e711f1c9e99abc6e2413b646662bf9a2c6f` | 59 top-level paragraphs; 3 tables, 10/3/6 rows |

Every top-level paragraph and every table cell was inspected for the ground truth.
The planning file's sole embedded image was also inspected: it is council stationery,
including printed council contact information, rather than a completed respondent
answer. The questionnaire has no embedded images. Neither document contains checked
Word checkbox controls. The cases restrict absence claims to the specified original
text/table cells; they do not infer global absence merely from a failed search.

`fixtureParagraphsZeroBased` uses the original python-docx top-level paragraph order.
`fixtureTableRowsZeroBased` uses the original document table and row order. These
are human-review locators, not API selectors, chunk IDs, page numbers, or provider
section boundaries. Merged planning cells repeat in the extraction; repeated copies
are one source assertion. The questionnaire's percentage rows and year columns form
a matrix with blank response cells. A flattened extraction must not turn them into
successive percentage/year pairs. Losing cell relationships or blankness is a
provider-fidelity limitation to report, not permission to manufacture a selection.

The corpus broadens domain and table coverage but consists of short uncompleted
forms. It does not validate long narrative documents, PDF extraction, OCR, filled
handwriting, complex diagrams, spreadsheet ingestion, or performance at library
scale. Keep this validation separate from the original longer-assessment results.
“Held-out” means these source documents were not used for the original two-case
replay; it does not mean a blinded benchmark or an independent human assessment.

Use identical source bytes for every compared profile and preserve matched source
manifests, provider configuration, chunk-size units, model version, budget, and run
order. Record complete-strategy and controlled-component comparisons separately.
Reuse the existing measurements and replay receipts, with measured usage,
estimates, and unavailable counters distinguished. A regex or filename match is
only an indicator; manually compare captured answers and original source evidence
for correctness, citation relevance, table fidelity, and appropriately limited
absence claims. No quality score or cloud validation outcome is asserted by this
case pack alone.
