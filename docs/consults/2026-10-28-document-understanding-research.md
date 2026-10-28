# Structured document understanding: research assessment

Research date: 2026-10-28. **Status: research and design recommendation, not an implemented capability or a rollout decision.** Companion: [implementation proposal](2026-10-28-document-understanding-proposal.md), updated with the shared-versus-split Cosmos experiment and its initial prototype status. The external research below is distinct from evidence produced by that experiment.

## Assessment

There is substantial precedent for preserving document structure, retrieving at several levels, and using generated descriptions to help select original evidence. The evidence supports combining these mechanisms. It does not establish a universal pipeline that reliably understands every genre, nor predict the gains on this repository's corpus.

The proposed capability is broader than the existing section-summary and document-summary roadmap items. It adds a common representation of source structure, document purpose and regional roles, versioned summaries, navigation, and continuously maintained library overviews. The two earlier improvements become useful deliveries within that larger design.

Evidence below is classified as **measured research**, **documented implementation**, or **vendor-reported capability**. These categories establish different things: a supported product feature is evidence of feasibility; a benchmark is evidence for its particular tasks and conditions; neither is our validation.

## Findings and implications

### 1. Anthropic: contextualize chunks before indexing

Anthropic generates short contextual explanations for chunks and prepends them for both embeddings and lexical indexing. Its September 2024 experiments report top-20 retrieval failure falling from 5.7% to 2.9% with contextual embeddings plus BM25, and to 1.9% with reranking. Those are relative failure reductions of 49% and 67%, not corresponding increases in answer accuracy. **Measured vendor research.** [Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval)

**Application:** evaluate concise document/section context in passage and figure search representations. Store generated context separately from original source. Reusing one section summary is our proposed optimization; it is not identical to Anthropic's chunk-specific method and does not inherit its result.

### 2. Stanford RAPTOR: searchable summaries at multiple levels

RAPTOR recursively clusters, summarizes and embeds text, then retrieves across the resulting hierarchy. It evaluates NarrativeQA, QASPER and QuALITY. Its selected retrieval approach searches across levels; on its small QASPER comparison, this outperformed fixed top-down traversal. The hierarchy is based on semantic clusters, which need not correspond to authored chapters or sections. **Peer-reviewed research, ICLR 2024.** [RAPTOR paper](https://arxiv.org/html/2401.18059v1)

**Application:** retain both high-level discovery and direct passage access. Start with source structure as the authoritative navigation tree; consider thematic clusters as an additional view. Do not require every query to pass through a document summary first.

### 3. Google: structural parsing and contextual chunks are product features

Google's Document AI layout parser preserves elements in a document tree and produces chunks augmented with ancestral headings. Its documentation also describes visual annotations; the listed Gemini-powered parser variants include preview/release-candidate versions, so availability and maturity depend on the processor. **Documented implementation; qualitative product claims are not an independent retrieval benchmark.** [Google layout parser](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)

**Application:** preserve heading ancestry and tables before chunking. This validates the architectural pattern without requiring a switch from our Azure/native extraction stack. Benchmark alternative parsers through a common output contract if our extraction quality proves insufficient.

### 4. Microsoft: layout extraction and document-type routing are separate capabilities

Document Intelligence provides section/subsection element relationships and figure provenance. Azure AI Search's Document Layout skill demonstrates using this structure in an indexing pipeline. These are useful extraction precedents even though our search backend is Cosmos. [Document Intelligence sections](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0#sections), [Document Layout skill](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-document-intelligence-layout)

Content Understanding can classify and split files, then route segments to different analyzers. Its classifier documents a page-level minimum, so it does not by itself solve mixed roles inside a page. **Documented implementations.** [Classification and segmentation](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/concepts/classifier)

**Application:** separate format extraction from genre interpretation. Evaluate Content Understanding as an optional adapter, with cost and coverage comparisons, rather than assuming it replaces our native parsers or infers every rhetorical role correctly.

### 5. OpenAI: hierarchical navigation is demonstrated, not a documented universal file model

An OpenAI-hosted partner cookbook demonstrates recursive navigation through a long document: select relevant large regions, subdivide them, then retrieve paragraphs. This is a worked agentic example, not a production service guarantee or a broad controlled comparison. [Model-selection cookbook, document navigation example](https://developers.openai.com/cookbook/examples/partners/model_selection_guide/model_selection_guide)

Official OpenAI documentation also describes chunked retrieval, metadata filtering, ranking and hybrid lexical/semantic search. It does not establish that hosted file search exposes the complete genre-aware hierarchy proposed here. [Retrieval documentation](https://developers.openai.com/api/docs/guides/retrieval)

**Application:** expose compact outlines and bounded source expansion to consuming agents. Precompute reusable descriptions during ingestion instead of repeatedly asking the answerer to reconstruct the document's organization.

### 6. AWS and LlamaIndex: retrieve small, expand to a parent

Amazon Bedrock implements parent/child chunking: child hits are replaced by broader parent content during retrieval. Its documented hierarchy has two configured levels, and merging can reduce the number of returned results. **Documented managed feature.** [Bedrock chunking](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html)

LlamaIndex's document-summary example selects documents through summaries and retrieves their associated nodes. Its auto-merging retriever consolidates retrieved children into parent context. **Open-source implementations/examples, not a universal performance result.** [Document Summary Index](https://developers.llamaindex.ai/python/examples/index_structs/doc_summary/docsummary/), [retriever implementation reference](https://developers.llamaindex.ai/python/framework-api-reference/retrievers/summary/)

**Application:** separate the unit that matched from the unit the agent reads. Preserve our explicit selection and token budgets instead of always expanding every hit to a large parent or entire document.

### 7. Docling, GROBID and TEI: rich representations need not be invented from scratch

Docling's hierarchical chunker uses its structured document representation; its hybrid chunker adds token-aware splitting and merging, carrying headings and captions. **Open-source implementation.** [Docling chunking](https://github.com/docling-project/docling/blob/main/docs/concepts/chunking.md)

GROBID specializes in scholarly documents and uses a cascade of models for structures at different granularities. This is a concrete precedent for adapting interpretation to a document family. [GROBID principles](https://grobid.readthedocs.io/en/latest/Principles/)

TEI's performance-text vocabulary represents acts, scenes, speeches, speakers, stage directions and embedded structures. It supplies a mature conceptual reference, not an automatic extraction solution. [TEI performance texts](https://tei-c.org/release/doc/tei-p5-doc/en/html/DR.html)

**Application:** use a small common core plus extensible roles and relationships. Borrow useful concepts without importing an entire XML vocabulary or adding a different database schema for every genre.

### 8. Microsoft GraphRAG: an additional hierarchy for corpus-wide questions

GraphRAG builds entity relationships and hierarchical community reports. Microsoft evaluated dynamic community selection on 50 global AP News questions: it reported 77% lower token cost than static global search at the compared level, with similar LLM-judged quality. Deeper selection could improve some quality measures while increasing cost. These results compare GraphRAG query strategies, not GraphRAG with our current service. **Measured vendor research.** [Dynamic community selection](https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/)

**Application:** distinguish authored structure from cross-document thematic relationships. A library contents overview can initially aggregate document summaries without entity extraction or graph community detection. Add a thematic graph if corpus-wide relationship questions justify its indexing and maintenance costs.

### 9. Jina: contextual embeddings without generated prefixes

Late chunking encodes a longer text before pooling token representations into chunk vectors. This preserves surrounding context differently from independently embedding each chunk. **Research and an open implementation.** [Late Chunking paper](https://arxiv.org/abs/2409.04701), [implementation](https://github.com/jina-ai/late-chunking)

**Application:** an experiment for a later embedding comparison. Our current embedding interface returns completed text vectors, so this is not a local chunker switch. A new embedding space would require coordinated query/document embedding changes and a validated index rebuild, even if dimensions happened to match. It would not supply readable summaries or source hierarchy by itself.

### 10. Contextual AI and PageIndex: document navigation is an active product direction

Contextual AI describes document-wide hierarchy inference combined with OCR, vision and table extraction. **Vendor-reported capability; its product description does not establish our expected accuracy.** [Document parser announcement](https://contextual.ai/blog/document-parser-for-rag)

PageIndex exposes tree indexing and reasoning-based document retrieval, with an open implementation and vendor benchmarks. **Implementation and vendor evaluation.** [PageIndex repository](https://github.com/VectifyAI/PageIndex)

**Application:** evaluate tree navigation as a retrieval route. These examples do not establish that we should replace vector retrieval; source trees, embeddings and lexical search can complement one another. A wrong branch selection must not make a passage permanently undiscoverable.

### 11. Research directly addresses discourse and incremental updates

The ACL 2026 paper *Beyond Chunking* uses discourse parsing, enriched relation nodes and hierarchical retrieval, reporting improvements across four datasets and several genres/languages. This is close to the idea of interpreting the function of regions, beyond heading size alone. **Research; reviewed here for architectural direction, not independently reproduced.** [Paper](https://arxiv.org/html/2506.06313v5)

Its analysis also reports different useful granularities for research papers and narrative material: short dialogue fragments benefited from more composition, while scientific sentences were more self-contained in the evaluated data. That is particularly relevant to the proposed genre-aware design, although it does not validate our specific role taxonomy.

A separate 2024 paper explicitly studies maintaining recursive summary trees when documents are added or removed. **Research proposal with reported experiments; no production maturity claim.** [Dynamic-dataset summary trees](https://arxiv.org/abs/2410.01736)

**Application:** design dependency tracking and incremental rebuilding immediately. Begin with explicit document ancestry and stable aggregation membership, which are easier to update than global semantic reclustering. Consider discourse relations after basic structural and genre annotations are evaluated.

### 12. More elaborate chunking is not automatically better

*Is Semantic Chunking Worth the Computational Cost?* evaluates document retrieval, evidence retrieval and answer generation, finding no consistent gains sufficient to justify its tested semantic-chunking costs. This concerns the evaluated segmentation strategies; it does not refute source-preserving hierarchy or summary retrieval. **Empirical counterevidence to assuming complexity guarantees improvement.** [Paper](https://arxiv.org/abs/2410.13070)

**Application:** retain a simple baseline and compare each addition separately. A combined improvement cannot tell us whether genre roles, summaries, reranking or a parser change caused the gain.

## Cosmos-specific implications

Microsoft documents vector search alongside operational records, and filtering vector queries by ordinary properties. This supports our proposed source/summary record types. It is an inference from those primitives that our hierarchy fits; the documentation is not a ready-made implementation of our publication and summary lifecycle. [Cosmos RAG guidance](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/rag), [vector search](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search)

Our `/libraryId` vector shard key matches Microsoft's documented pattern for searches confined to a particular tenant/category value, including the first level of a hierarchical partition key. A document's semantic hierarchy is application data; it does not require additional partition-key levels. [Sharded DiskANN](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/sharded-diskann)

Keep the current vector space initially and add queried scalar fields through explicit indexing-policy updates. Wait for index transformation and measure the changed workload. Separately measure selective summary/role filters, small libraries, and shared hybrid full-text statistics; sparse summary types do not automatically make an existing vector graph cheap to search. [Index-policy management](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-manage-indexing-policy), [performance guidance](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/vector-search-performance-tips)

Cosmos transactional batches are confined to one container and partition key. Our document metadata, search records, library overview and Blob artifacts cannot be committed in one such transaction. Use immutable artifacts, conditional publication pointers, idempotent jobs and reconciliation. These remain subject to the repository's known cross-client Session-consistency limits. [Transactional batches](https://learn.microsoft.com/en-us/azure/cosmos-db/transactional-batch)

Microsoft also publishes CosmosAIGraph guidance for combining graph relationships with vector retrieval in Cosmos NoSQL. It establishes a possible later direction, not a requirement for following explicit section-parent references. [Cosmos AI knowledge graphs](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/cosmos-ai-graph)

**Documentation drift to review before implementation:** current vector documentation lists `float16` and describes dropping/re-adding vector policies/indexes to change settings. Repository E16/N10 preserve older, stricter assumptions. This proposal uses the already validated vector policy and does not rely on those newer mutation capabilities. Updating the evidence notes requires separate verification against the deployed account/SDK; no infrastructure setting changed during this research.

## Recommended synthesis

Build a source-grounded document tree with optional typed relationships, genre-aware interpretation, readable summaries and searchable representations at useful levels. Preserve broad passage discovery alongside summary navigation. Use the same representation to generate document previews and incrementally refresh a library contents overview while retaining the owner's description.

Adopt immediately in the design: source provenance, flexible depth, explicit generated/source distinction, summary dependencies, bounded navigation and evaluation by task family. Evaluate contextual passage embeddings and reranking as independent variants. Reserve global thematic clustering, discourse parsing and alternative embedding models for later experiments with demonstrated demand.

The [implementation proposal](2026-10-28-document-understanding-proposal.md) maps these recommendations to the inspected repository, including the concrete incompatibilities that must be addressed before new indexed record types are written.
