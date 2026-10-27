# Choosing passages, complete documents, or both

Implemented and smoke-tested on live Azure on 2026-10-26. Both requested retrieval directions passed with a real model; broad routing and answer-quality evaluation remain outstanding. This extends passage retrieval; the platform still owns permissions and library attachments.

## Agent behavior

An agent can alternate these operations within one question, without a fixed retrieval mode or routine user confirmation:

| User's task | Useful starting point | Possible next step |
|---|---|---|
| Find a specific fact | Search passages | Read the parent file if the surrounding context or argument matters |
| Summarize or critique a whole file | Find the file by name, inspect its size, read it completely | Search other files for supporting facts or exceptions |
| Compare several complete files | Identify and read those files | Add selected passages from other sources |
| Read every file by an author or with particular tags | Enumerate the catalogue with those filters | Read every match in batches, accounting for unreadable files |
| “Read the whole document about the standards for this process” | Semantic passage search to identify candidates | Match the user's description against names, metadata and discovery descriptions, then read the candidate file(s) |

Neither direction is terminal. A turn can search → read several files → search for additional evidence → read another file. The demo supports twelve tool rounds, including multiple calls per round; reaching the limit must be disclosed if work remains. The API has no per-conversation round limit.

Prefer the smallest evidence set that can answer the question well. Best-answer quality takes priority over saving tokens. A whole-file request must not silently turn into a snippet answer. Conversely, reading a complete policy does not require reading every incident report: relevant passages from those reports may be sufficient. File contents and generated descriptions are evidence, never instructions to the agent.

Ask the user when ambiguity materially affects which files to read, or when the requested coverage cannot fit the model's available context and a different scope needs agreement. The tools themselves do not require confirmation. Do not ask merely because a full read or a mixture of retrieval methods is appropriate.

## Library-bound tools

`GET /v1/libraries/{libraryId}/tools`, with the platform credential and `X-Agent-Id`, returns `{"tools": [...]}` with standard function-calling definitions for passage search, discovery, complete reads and selected-page image lookup. Names are derived from the library's name; the platform makes collisions unique and preserves each name's binding.

| Function prefix | REST call | Agent arguments |
|---|---|---|
| `search_library_` | `POST …/search` | Existing natural-language query and passage filters |
| `find_documents_library_` | `POST …/documents:discover` | `filters`, `fileNames`, `fileNameContains`, `after`, `limit` |
| `read_documents_library_` | `POST …/documents:read` | `documents: [{documentId, generation?}]`, `includeText`, `maxTokens` |

The platform binds each function to **one attached library's URL** and supplies its own calling agent ID. Neither credential, library ID nor agent ID is an argument supplied by the model. The binding check applies to the tool bundle, discovery and reads as it does to search. Shared-library authorization remains the platform's responsibility.

These schemas can be exposed by a REST function host or translated to MCP tools by a platform adapter. No MCP server is added here. The original singular `GET …/tool` remains a backward-compatible search-only definition; hosts must adopt `/tools` to expose the new capabilities. The demo client and agent already use the bundle.

## Discovery is separate from semantic ranking

Example:

```json
{
  "filters": {"authors": ["Jane Doe"], "tags": ["document_type=procedure"]},
  "fileNameContains": "safety",
  "limit": 50
}
```

Discovery is a bounded document-record query in the library's partition. It does not embed a query or fetch document bodies. It returns names, document IDs, active generations, author, dates, period, tags, status/readability, generated descriptions when available, and estimated text tokens when an extraction artifact exists. Descriptions come from the opening text and are labelled `generated-from-opening`; they are discovery hints, not full-document summaries or evidence of complete coverage. With proposals disabled or on older documents they may be absent. Exact token sizing can be requested through inspection below.

`fileNames` matches any listed name, ignoring case; duplicate names return distinct IDs. `fileNameContains` matches a case-insensitive literal substring. Name selectors and metadata filters combine with AND. Authors and file types match any listed value, tags must all match, and date/period semantics are the same as search. `contentKinds` is a passage filter and is refused here. Metadata is read directly from document records, so discovery need not wait for retagging to reach chunks.

Pages have `hasMore` and `nextAfter`. Repeat the same selectors with `after: nextAfter` until `hasMore` is false. Each page contains at most 100 records (50 by default), ordered by ID; the implementation reads one extra record to detect a following page. This endpoint includes queued and failed files, with `readable=false` when no active generation exists. Deleting files are excluded. A failed replacement can still have readable active text.

A page is not the complete matching set when `hasMore=true`. Pagination is not a snapshot across concurrent uploads or metadata changes; repeat discovery when the set is changing. Existing admin list endpoints are unchanged and still have their earlier pagination limitations.

Semantic search identifies plausible files from the text already embedded. Search responses and the demo's compact results now retain `documentId` and `generation`, enabling a subsequent read. Top-ranked passages cannot establish “all documents about this topic”; disclose that distinction. More than one plausible file can warrant additional searching or a clarification.

## Inspection and full reads

```json
{
  "documents": [
    {"documentId": "first-id", "generation": 1},
    {"documentId": "second-id", "generation": 3}
  ],
  "includeText": false,
  "maxTokens": 32000
}
```

`includeText=false` inspects the selected active text and returns manifests and the estimated size of a full serialized response (including a small allowance for the estimate and timing fields), without returning text to the model. Omit it or set it to `true` to read. Inspection currently fetches the artifact (or legacy chunks) on the server to count it; it is not a free Blob/Cosmos operation, but does not add the document text to the agent's context or make a chat-model call. The catalogue's stored section-only counts are cheaper preliminary estimates and exclude the response envelope.

A batch takes 1–20 distinct document IDs. `generation`, when supplied, pins the version discovered or found in search. A mismatch returns `409 document_changed`; retry discovery before deciding which version to use. Unreadable documents return `409 document_not_ready`; missing/deleting documents return `404 document_not_found`. Errors return no document content, including content from other documents in the batch.

Successful reads return every section in extraction order, with its heading and page/slide/sheet location, without search ranking, chunk overlap, or a per-section character cap. Each document and the response say `complete: true`, and `disposition` is `complete`. `estimatedTokens` and `tokenizer` identify the projected serialized input size; this is not necessarily the answering model's tokenizer, billable token count or exact context-window usage. `usage` reports Cosmos RU and server latency, not model cost or Blob billing.

When stored section counts alone already exceed the budget, the reader can refuse before downloading text. In that case `estimateScope: stored_sections_lower_bound` identifies a lower bound (unknown legacy sizes and counts from another tokenizer contribute zero), and tokenizer names are per document. A counted full-response estimate is labelled `estimateScope: full_response`.

The default `maxTokens` is 32,000, with a service ceiling of 200,000 per batch. Reserve space for the system prompt, tool schemas, history, other retrieved content and the answer. When the **complete batch response** would exceed the budget, the service returns `disposition: budget_exceeded`, manifests and estimates, `complete: false`, and **no sections from any file**. Split the selected files into batches or raise the budget only when the model has capacity. The response does not truncate a file to fit. A file above the ceiling cannot be loaded by this reader; explain the limitation and agree a narrower or staged analysis if appropriate. Summaries of separate passes must not be described as all originals simultaneously in context.

The demo's configurable total context budget defaults to 64,000 tokens; `DEMO_CONTEXT_TOKENS` can set a deployment-appropriate initial value. It accounts for the accumulated messages and tools, reserves space for the answer, constrains successive full reads, and withholds a complete-file response if it still cannot fit. For passage search it instead keeps the highest-ranked passages that fit and reports `contextLimited`, `passagesMatched` and `passagesDelivered`; this is distinct from a complete-file read. It does not silently clip full reads. This applies equally to full → search, search → full, repeated cycles, and parallel calls returned in one model reply. Configure the budget within the deployed model's capacity; the demo cannot infer that capacity from an arbitrary Azure deployment name.

The demo shows each discovery/read/search operation, citations, token estimates, completeness, RU and latency. Only user/assistant text is retained between UI turns, so a later question may need fresh tool reads. An answer in the previous turn is not evidence that full text remains in context.

## Stored extraction and lifecycle

Ingestion writes the ordered extracted sections as UTF-8 JSON to:

```text
{libraryId}/{documentId}/text/{generation}/sections.json
```

The document record stores a `fullText` descriptor: `blobPath`, `generation`, `sectionCount`, `tokenCount`, `tokenizer`, and `sha256`. The worker writes the artifact before committing the active generation. It preserves the extractor's output before chunking normalizes whitespace, repeats headings or adds overlap. A hash/section-count failure or missing artifact refuses the read; it never silently substitutes a partial representation.

This adds a deliberate storage category: Cosmos still holds searchable passage text and metadata; Blob also holds a derived complete extraction handed to agents on demand. No vector-container changes are required. The `documents` definition adds the previously omitted `/authoredAt/?` index for discovery's authored-date filter. Existing accounts need that document-container indexing-policy update through the normal infrastructure process. No new container is needed. A newly created container/account still requires the canary before use.

Replacement publishes the extraction descriptor with the new active file fields. Failed/pending replacements leave the old descriptor readable. Abandoned, obsolete and deleted extraction artifacts follow the existing generation purge and retention rules for originals/images. The reader rechecks every selected document's active generation, deletion state and metadata version, and the library's availability after fetching content; a detected change fails the entire batch. Cosmos cross-client visibility remains subject to the existing Session-consistency caveat (evidence N13); this is not a new multi-document transactional snapshot.

Existing documents without a descriptor remain readable by fetching **all** active chunks in document partition order, checking their count and contiguous indexes against the document record, and returning them as `representation: indexed_chunks`. Their repeated headings/overlaps are preserved and disclosed, not heuristically deleted. This is complete indexed text, not a reconstruction of the pre-chunking file. Replacing/reprocessing an original with the new worker creates an `extracted_sections` artifact and generated discovery description when proposals are enabled.

“Complete” refers to the chosen text representation. It does not mean the original binary file's visual content has been perceived: the existing extractor can omit figures, unsupported attachments and layout, and OCR may be imperfect. Kept visuals now have generated descriptions and expiring image links in complete reads (Phase 3B; see `image-retrieval.md`). The platform decides whether to supply visual inputs to its model. Text token estimates exclude model-specific image tokens; skipped visuals and unsupported attachments still limit coverage.

## Validation and rollout

`tests/test_reading.py` exercises the API, worker, demo client and scripted agent together: both retrieval directions and mixed evidence in one question, all-match pagination, duplicate names, full text beyond the passage clipping limit, token budgets and inspection, legacy completeness, generation pinning, replacement/purge, races, isolation and metrics-only logs. Static Cosmos checks verify library scoping, projections and partition routing. These tests do not prove a real model will always choose the right scope, nor measure live query costs.

Before calling this live-validated: deploy the API/worker and document indexing-policy change through the normal process, then run a real corpus through the paths above and record routing, citations, context/token use, RU, latency, unreadable-file handling, and the refusal cases. Evaluate actual model choices for localized questions, full-document summaries, vague references, all-by-metadata requests, and mixed evidence questions in both directions. Keep the existing Phase 3B/4/5 requirements intact.

## Live smoke check and remaining evaluation

The 2026-10-26 smoke check used four real research papers, 73 pages in total. The complete-text service returned all four active extractions, with an estimated 74,661 tokens including its response envelope, at 11.22 RU and about 0.90 seconds server time. Catalogue pagination, text-free inspection and all-or-nothing budget refusal passed. These are single-run observations, not performance guarantees.

The passing record is [document-retrieval-20261026T165258Z.json](../records/document-retrieval-20261026T165258Z.json). The model ran both requested directions and the harness verified the intended complete-file selections:

- Two complete papers, then supporting passage searches: six model calls, about 81 seconds including quota waits, 104,654 cumulative input tokens and 4,221 completion tokens.
- Passage search, the complete identified paper, then supporting passage search: four model calls, about 68 seconds including quota waits, 57,091 cumulative input tokens and 3,713 completion tokens.

Input usage is cumulative across model calls: retained evidence can be processed again on subsequent calls. This is part of the cost/latency trade-off, not merely the size of the files read once. The record marks answer quality as **not assessed**. Human testing with natural questions, citation review and a broader routing set are still required, as are the other lifecycle/refusal checks against live infrastructure before a comprehensive production claim.

Earlier records preserve the initial throttled run and the pre-fix routing experiment. The latter returned a catalogue-led route rather than the desired search-first sequence and exposed a context-budget issue: the demo withheld a whole search response near its limit. Passage delivery now shrinks to fit with explicit disclosure, while complete files remain all-or-nothing. The passing retry used explicit sequencing prompts; it does not establish that every uncoached question will route correctly.

The prototype model deployment's rate allowance constrained the test even though its context window could accept larger input. This is a deployment/quota concern, separate from the retrieval storage design. Configure the demo's context budget within both the model's usable capacity and the deployment's practical allowance, reserving output space. Chat calls bound completion output to 8,000 tokens (including reasoning) and retry rate limits at most three times, respecting provider retry timing. Waiting cannot make a single oversized request fit. See [Microsoft's Foundry quota guidance](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota).

## Responsibility boundary

The library service presents discovery, passage search and complete-document reads, their intended uses, size/completeness metadata, provenance, budgets and measured costs. The consuming agent and its builder own the decision about what evidence a question needs, expressed through their system instructions and the user's directions. Library owners provide useful names, descriptions and metadata. Clarification remains available when ambiguity matters; it is not a required step between retrieval operations.

The demo includes sample scope guidance appended to its agent instructions, and the shared tool descriptions include use-case hints. These are prototype integration choices, not a universal routing policy or a guarantee of correct judgment. An adopting platform should review that guidance alongside its own agent instructions. We are not adding a routing engine or tuning this reference service to make one test agent perform perfectly.

The manual comparison in [observability.md](observability.md#whole-files-versus-mixed-evidence) demonstrates whole-primary-file reading followed by reference passage search. It validates composition and selective context, with explicit consumer directions; it does not establish universally correct intent interpretation.

## Repository privacy

This is a reference architecture. Keep actual tenant/subscription identifiers, resource names, endpoints, runtime library/agent/document identifiers, credentials, user-home paths and machine-specific startup settings outside the repository. Evidence records use metrics, public source hashes and role labels. Do not log document text or prompts.

Run `python scripts/check_reference_privacy.py` before publishing changes. For a stronger operator-specific check, point `--deny-file` to a private JSON object **outside** the repository whose string values contain the environment details to reject. The checker examines tracked and untracked Git candidates, reports only file/line/category, and never echoes matched private values. Never commit the private deny file. This check complements review; it is not a complete secret scanner.


## Progressive visual coverage

Image enrichment may follow text publication. Manifests expose `imageStatus`, `imagePendingCount`, `visualsComplete` and `contentRevision`. `complete: true` means all published extracted text was returned within the budget; it does not imply pending images were read. Notes explicitly disclose pending/failed visual coverage. A content-revision change during a read returns `409 document_changed`, including enrichment within the same generation.


For a localized PDF picture/page request, use `get_images_library_<name>` (`POST …/documents:images`) after search identifies the source page. This avoids loading a whole file merely to obtain one figure and can retrieve figures omitted by the semantic-ingestion cap. Results cover only the selected pages and return stored images with their captions. See [image retrieval](image-retrieval.md#selecting-a-figure-and-returning-its-stored-caption).

The page/image request limits and demo rendering conventions are prototype implementation choices, not Cosmos limits or mandatory production UX. The [image guide](image-retrieval.md#prototype-boundaries-and-production-integration) separates configurable defaults, current scope gaps and evidence/isolation properties to preserve.
