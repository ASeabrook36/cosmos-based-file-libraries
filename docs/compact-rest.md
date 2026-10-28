# Compact REST evidence and image attachments

`fli.compact.v1` is a supported opt-in presentation over the existing retrieval services. Add `?view=compact` to any of these POST operations:

| Operation | Supported modes | Evidence |
|---|---|---|
| `/v1/libraries/{libraryId}/search` | Passage search, optional document scope; compatibility document grouping | Complete returned passages, neighbors, citations, references, scope and coverage |
| `…/documents:discover` | `catalogue`, `semantic_candidates` | File manifests, size hints, readability, selectors, paging or explicit candidate-window limits |
| `…/documents:read` | `full`, `selected`, `outline`; `includeText=false` inspection | Exact extracted text or legacy indexed text, selected-region provenance, outline handles, completeness and omissions |
| `…/documents:images` | PDF pages or selected retained image IDs across supported formats | Stored captions/descriptions, original body context, source association qualifications and selected-image coverage |

Omitting `view`, or using `view=raw`, preserves existing raw response shapes and read budgets. Invalid views/budgets use the existing `400 invalid_request` error envelope. Authentication and all existing retrieval errors remain unchanged. `/tools`, the singular compatibility `/tool`, administration endpoints and ingestion are unchanged. The compact response is also described in OpenAPI as an alternative to the raw operation object.

## Response parts

```json
{
  "contract": "fli.compact.v1",
  "evidence": {
    "mode": "image_inspection",
    "resultUnit": "images",
    "documentComplete": false,
    "selectionComplete": true,
    "results": [
      {
        "imageId": "returned-image-id",
        "documentId": "returned-document-id",
        "generation": 1,
        "contentRevision": 2,
        "fileName": "report.pdf",
        "location": {"page": 4},
        "text": "Generated description, unchanged.",
        "textSource": "generated_image_description",
        "citation": "report.pdf (page 4)",
        "image": {
          "kind": "pdf_figure",
          "caption": "Figure 1. Original source caption.",
          "captionStatus": "identified",
          "captionAssociation": "adjacent_inferred"
        }
      }
    ]
  },
  "attachments": [
    {
      "imageId": "returned-image-id",
      "documentId": "returned-document-id",
      "generation": 1,
      "contentRevision": 2,
      "fileName": "report.pdf",
      "location": {"page": 4, "figure": "internal-layout-locator"},
      "image": {"url": "<short-lived HTTPS read link>", "expiresAt": "<UTC timestamp>"}
    }
  ],
  "metadata": {
    "usage": {"requestCharge": 6, "totalMs": 100},
    "budget": {
      "scope": "evidence_json", "tokenizer": "cl100k_base", "maxTokens": 12000,
      "requiredTokens": 500, "returnedTokens": 500, "fits": true,
      "nativeImageTokensIncluded": false
    }
  }
}
```

This abbreviated example omits other operation fields; its token counts are illustrative. Serialize **only `evidence`** into the model's tool message. It remains untrusted source material, never instructions. Operation-specific fields retain their existing meanings: `coverage`, `filtersApplied`, `documentScope`, `scope`, `pages`, `requestedImageIds`, `candidateWindow`, `exhaustive`, pagination, limitations, extraction representation and generation/revision/reference handles. Search also exposes convenience `disposition`, `notes` and `filtersApplied` fields from coverage. A catalogue page or semantic candidate set does not establish that all matching files were read.

Original source section, passage, neighbor, caption and page-context strings are preserved without character clipping. The existing neighbor query does not return neighbor locations: their file-level citations explicitly disclose that limitation rather than inheriting the hit's page. Expand source evidence when a precise neighbor location is needed. Search's **generated image wrapper** can be removed because filename/location are separately cited; its description and nearby source text remain intact. Generated descriptions are labelled; a missing generated description is labelled `unavailable_image_description`. Search image headings are contextual, not printed figure titles: `contextHeading` and `headingAssociation` retain that qualification, and `sectionContext` is offered only when the heading also appears in the nearby source text. This does not assert section membership. Internal layout figure locators are removed from model-facing locations, never from original source prose. A real figure number in original text/captions stays intact.

Model-facing `image` contains only kind, source caption, caption status and association. An inferred adjacent caption remains inferred. Geometry, caption bounding regions, MIME, dimensions, expiry and signed links stay in each attachment's full `image` object. Attachments carry document/generation/revision and source location, keyed by `imageId`, and are deduplicated within a response. Source-region `candidateImages` retain proximity qualifications and omissions; availability is not evidence of relevance. Discovery/outline need not return image bytes/links.

Operational metadata holds `usage`, search `telemetryId` and `references` when present (mapping model-facing evidence IDs to raw chunk IDs for usage reporting), `budget` and per-document stored token hints (`documentEstimates`, including tokenizer or null). In evidence, `documentTokens` is a stored size hint; outline `sectionTokens` is the current tokenizer's section-text estimate. Neither is the serialized response size or an image-token estimate. Unknown legacy sizes stay unknown. No credentials, storage paths or runtime deployment details are introduced into evidence.

## Budget and refusal contract

`maxEvidenceTokens` is an optional query parameter, integer 1–200,000. Search, discovery and image inspection default to 32,000. Compact document reads use body `maxTokens` (default 32,000, maximum 200,000); if `maxEvidenceTokens` is also provided, the smaller limit applies. These query-budget semantics apply only to compact responses. Raw reads continue counting their original response envelope and retain their stored-section early-refusal optimization.

The compact budget counts **only the full evidence JSON** using the named service tokenizer (`cl100k_base` normally) and this serialization:

```python
json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
```

If the tokenizer encoding is unavailable, the existing `approx-wordpiece` fallback is explicitly named in metadata; its counts are approximate model-token estimates. `metadata.budget.requiredTokens` counts the candidate evidence before any refusal. `returnedTokens` counts the evidence actually returned; `fits` compares the candidate with `maxTokens`. Attachments, metadata, the outer envelope, model message framing, tools, history, answers and native-image inputs are excluded. A host using another tokenizer/serialization must also check its actual model input and reserve answer space. Tokenization runs off the API event loop. Compact reads do not apply the raw artifact-size shortcut: stored JSON may contain substantial attachment metadata that is absent from evidence.

If the evidence does not fit, return HTTP 200 with `disposition: budget_exceeded`, false completeness/selection flags, no source bodies, outline entries, image candidates or attachments, and explicit next actions. **Every selected full/selected read is all-or-nothing across the batch**. No source string is clipped. Search/discovery/image-inspection batches are likewise refused as a whole by this service view; it never silently drops lower-ranked results. The refusal/inspection control message can exceed a very small budget: request scope, document manifests, coverage/omission notes and recovery instructions remain available. It is not a promise to fit arbitrary control messages into one token. `includeText=false` returns `disposition: inspected`, manifests and the candidate estimate without bodies/attachments even when it would fit.

There is no implicit source-text continuation cursor for a refused full/selected read. Raise the budget only when model capacity allows, choose fewer documents/regions, use a smaller search/catalogue page, page an outline, or use existing selected reads. Follow `nextAfter`/`nextAfterSection` only on successful catalogue/outline pages and keep selectors unchanged. Staged analysis must disclose that all originals were not simultaneously present.

## Native images and display are separate platform actions

1. Bind the URL library and `X-Agent-Id` from the platform's authorized tool mapping. Models never supply these identities or platform credentials.
2. Send evidence as tool text. Keep attachments in authorized conversation state, outside model-facing JSON/logs.
3. Resolve selected IDs only against returned attachments. For later/expired links or selected-region candidates, call `documents:images?view=compact` with returned document ID, generation, revision and selected `imageIds`. The server revalidates membership and active versions. On stale references, rediscover; do not silently discard version pins.
4. Fetch/display selected images before expiry, preserving source captions and inferred-association labels. Read links are bearer capabilities with the existing 15-minute lifetime; already issued links can remain usable until expiry after deletion/replacement. Authorized conversation caching or a platform proxy is the host's choice. Do not publish/persist signed links.
5. Text-only models may choose/display images from descriptions and captions. **Display does not establish that the model received pixels.** A URL in tool JSON is not native visual input.
6. For image-capable models, explicitly construct native image content parts for selected images, reserve model-specific image input cost in addition to the text budget, and record supplied/already-supplied/withheld IDs. Provider usage establishes actual model charges. The REST service neither sends pixels to an answering model nor claims native delivery.

The independent [Python integration example](../examples/compact_rest.py) retrieves evidence, refreshes selected images, calls a display callback and optionally constructs native inputs. It uses any authenticated `httpx.Client`, with credentials supplied by the host. `tests/test_compact_rest.py` runs it against the actual routes, alongside the demo client, for text-only display and explicit native inputs. The demo uses this supported envelope and retains its own context reserve, progress annotations, native delivery ledger and UI selection syntax; those are consumer behavior, not new service retrieval policy.

## Access, compatibility and deployment

Presentation runs after the existing authorization, integrity, generation, content-revision, metadata-version and lifecycle checks. References remain snapshot locators, not permission capabilities. Library binding comes from the path; personal-library isolation is unchanged. Raw responses and error envelopes are compatible. Existing Session-consistency limitations still apply; this does not add a transactional snapshot or revoke already-issued links.

Upgrade the API and refresh the demo/client process together. No worker, ingestion, container/index migration or re-upload is required. Existing valid artifacts and legacy indexed text remain usable with their existing limitations; missing legacy image manifests are still explicitly unavailable. Generated section summaries and document-summary vectors remain later roadmap items.

[Local measurements](../records/compact-rest-responses-20261027.json) compare representative invented-fixture response text using the real tokenizer and in-memory retrieval services. Run `python -m scripts.measure_compact_responses` to repeat. All source text, descriptions and captions were retained; image-bearing evidence used about 18–34% fewer text tokens in this record, with smaller reductions for discovery/outlines. IDs/timing can cause small repeat differences. This is **not** measured cumulative model-token, real Cosmos RU, dollar-cost or latency savings. No extra Cosmos query is introduced by presentation; it does not inherently reduce RU. Repeated model calls, caching, routing, native images and host message formatting determine actual integration costs. No new live-model/Azure validation or broad answer-quality claim is made for this change.
