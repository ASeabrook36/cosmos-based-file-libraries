# Continue the structured retrieval work

Updated 2026-10-28. This checkpoint accompanies the packaged experimental code.
Start with the [lab guide](../structure-lab.md), [research assessment](2026-10-28-document-understanding-research.md),
[proposal](2026-10-28-document-understanding-proposal.md), and
[sanitized live measurements](../../records/structure-lab-20261028.json).

## What is available

- The normal API, worker, demo, three application containers and main deployment
  remain the default. The new module has no imports from the normal application
  or demo. The README retains the existing run commands. No Microsoft preview
  service is required to run the normal demo.
- The opt-in lab has lossless Markdown heading trees, supplied genre/role hints,
  source-subtree reads, generated summaries with dependency caches, inventory
  overviews and matched shared/split Cosmos layouts. It does not yet preserve
  PDF/DOCX hierarchy or expose its new operations through REST or Streamlit.
- Three experimental containers were deployed and passed the standard canary.
  Small synthetic structured runs completed 156 retrieval measurements without
  errors. No layout winner or general answer-quality improvement was established.
- The last full suite passed 666 tests; lint, format and privacy checks passed.
  Fourteen existing TestClient deprecation warnings remain. See the record for
  precise measurement scope and excluded costs.

## Current decision and next work

The user wants to explore reuse of Microsoft products, including previews, before further
custom ingestion development. They want the full agent-facing feature set kept
visible when comparing products. A bounded compatibility experiment has been defined:

1. Establish a supported external source-evidence retrieval path for KBaaS.
   Its documented integration is with Logic Apps agent loops; management REST
   endpoints do not themselves establish a general retrieval API.
2. Inspect a real Document Indexer workflow and its emitted schema. Determine
   whether Document Intelligence section/element relationships survive parsing.
3. Run a nested PDF with figures, structured DOCX with an embedded image, scan,
   paper, play and opinion through the candidate paths. Test chapter/section
   selection, complete source reads, images/captions, add/replace/delete, library
   isolation and overview refresh. Record supported extensions versus missing or
   unverified capabilities, and actual integration/operating costs.
4. Select reusable components based on those results, then integrate the proven
   contract into the existing API. Retain the frozen-artifact harness as a control.

Document Intelligence is the existing extraction component. Document Indexer is
customizable ingestion orchestration and can call Document Intelligence. KBaaS
is a managed ingestion/retrieval alternative; do not assume a supported chain
through all three products. Its embedded chunk summaries do not establish our
planned section/chapter/document summary hierarchy. Text-only parsing and default
chunking are documented preview limits; other desired features need verification.
Our native Office image handling remains useful. Per-agent authorization and
revision-aware source reading remain requirements regardless of provider.

## Azure readiness checked, without provisioning previews

Read-only checks confirmed Azure CLI access, permission to create hosting/storage
and identities, assign Azure/Cosmos roles, deploy models, and register providers.
Workflow Standard was listed for the existing deployment region. There was no
Logic Apps Standard host. Microsoft.Web and Microsoft.Logic were unregistered.
Existing models included text-embedding-3-large and gpt-5-mini. The latter is not
on the documented Standard agent-loop model list. Regional unused quota was
available for gpt-4o-mini on GlobalStandard/DataZoneStandard, making it a candidate
listed model; deployment capacity and end-to-end compatibility still need testing.

Next setup: register providers, provision an isolated Logic Apps Standard host
with a system-assigned managed identity and the required roles, configure a
supported model, and verify KBaaS preview availability in the portal. Use isolated
service-owned storage for KBaaS schemas. No Logic Apps host, new model deployment,
Document Indexer workflow or KBaaS instance was created in this session.

## Private runtime state

No operator environment identifiers belong in this repository. Runtime settings,
full canary receipts, summaries, source snapshots and caches were kept in private
OS temporary storage. Generic scratch names included fli-structure-lab.env,
fli-structure-results, fli-structure-demo, fli-structure-corpus and
fli-structure-deny.json. They may survive locally but are not durable handoff
artifacts. Recover configuration using scripts/deployment_env.py and the
operator-provided resource group if necessary; never commit its output.

The lab work directory is single-writer. Each freeze produces a fresh library
snapshot; use an explicit artifact filename to revisit a loaded snapshot. Small
structured snapshots may still exist in the experimental containers. Canary
libraries were cleaned up. Use the lab cleanup command with the relevant artifact
and passing receipts when removing experimental rows. Consult CLAUDE.md before
changes; all fleet access, if needed, goes through nervus rather than SSH.
