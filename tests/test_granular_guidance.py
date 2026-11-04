"""Local guidance mitigation after live qualification; not proof of model behavior."""

import hashlib
import io
import json

import pytest
from docx import Document

from fli_demo.client import IndexClient
from tests.test_granular_navigation import CHILD_TEXT, QUALIFIER, read, ready, text_of
from tests.test_modular_ingestion import setup


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tools(profile):
    client, _, library, _ = setup(profile)
    return IndexClient("http://testserver", "k", http=client).tools(library, "reader")


# Captured through IndexClient.tools before the granular-only guidance mitigations.
BASELINE_OTHER_PROFILES = "2a2680c2db8c34cc0cc3dbe0bd9d6bc295e17dd894a6c6ead8f2cbfc338ad605"
BASELINE_GRANULAR_OTHER_TOOLS = "e0df6b3ce5d8e01ac383097c013bf45edb354c41ec6aa9305c3db5bb5e8428eb"
# Only the granular includeText parameter's description changed: true/default
# delivers an outline with labeled navigation previews; false remains inspection.
GRANULAR_READ_SCHEMA = "3a0114e445c6145084231a34904314e206e3acf011e9070d098ad0630ff5ba74"


@pytest.mark.parametrize(
    "profile",
    ["current-v1", "custom-structured-v1", "microsoft-indexer-layout-v1", "microsoft-indexer-source-fidelity-v1"],
)
def test_other_profile_model_visible_tool_bundles_match_pre_mitigation_baseline(profile):
    assert digest(tools(profile)) == BASELINE_OTHER_PROFILES


def test_normal_client_exposes_granular_guidance_without_changing_other_tools():
    bundle = tools("custom-granular-v1")
    read_tool = next(item for item in bundle if item["function"]["name"].startswith("read_documents_"))
    description = read_tool["function"]["description"]
    assert "complete table cell, copy that cell's readSelector or a cell paragraph's parentSelector" in description
    assert "Search hits or bulk paragraph selections do not prove cell completeness." in description
    assert "sectionId identifies a versioned evidence unit" in description
    assert "containingSectionId identifies its containing authored section node" in description
    assert "or the document root when no containing authored section exists" in description
    assert "document root when no authored section contains the source" in description
    assert "Never substitute these IDs" in description
    assert "cite the returned citation string" in description
    assert "Omit internal IDs unless asked" in description
    assert "copy each named field and value verbatim" in description
    assert "Use includeText=true (the default) to receive outline entries." in description
    assert "includeText=false is size/coverage inspection only" in description
    assert "nor outline entries in any mode" in description
    assert "copy the returned readSelector with includeText=true" in description
    assert read_tool["function"]["parameters"]["properties"]["includeText"] == {
        "type": "boolean",
        "description": (
            "True (default): deliver source text in full/selected mode, or outline entries with bounded navigation "
            "previews (not source evidence). False: size/coverage inspection only, "
            "with no source text or outline entries."
        ),
    }
    assert digest(read_tool["function"]["parameters"]) == GRANULAR_READ_SCHEMA
    assert digest([item for item in bundle if item is not read_tool]) == BASELINE_GRANULAR_OTHER_TOOLS


async def test_real_selected_read_distinguishes_evidence_handle_and_authored_parent_and_cell_scope():
    env = await ready()
    source = read(env)
    qualifier = next(part for part in source["documents"][0]["sections"] if QUALIFIER in part["text"])
    selected = read(env, qualifier["readSelector"])
    paragraph = selected["documents"][0]["sections"][0]
    assert len(selected["documents"][0]["sections"]) == 1
    assert paragraph["sectionId"].startswith("ev1.")
    assert paragraph["containingSectionId"] == paragraph["location"]["nodeId"]
    assert paragraph["sectionId"] != paragraph["containingSectionId"]
    assert paragraph["containingSectionSelector"]["documents"][0]["nodeIds"] == [paragraph["containingSectionId"]]
    assert paragraph["parentSelector"]["documents"][0]["nodeIds"] == [paragraph["parentNodeId"]]
    assert paragraph["parentNodeId"] != paragraph["containingSectionId"]

    cell = read(env, paragraph["parentSelector"])
    parts = cell["documents"][0]["sections"]
    assert len(parts) == 4 and cell["selectionComplete"] and not cell["documentComplete"]
    assert all(part["parentNodeId"] == paragraph["parentNodeId"] for part in parts)
    assert "<list-item " in parts[1]["text"] and "<list-item " not in parts[-1]["text"]
    assert QUALIFIER in parts[-1]["text"] and CHILD_TEXT not in text_of(cell)
    assert paragraph["citation"].startswith("procedure.docx (source table 1, row 2, column 2, source paragraph ")
    assert paragraph["sectionId"] not in paragraph["citation"]
    assert paragraph["containingSectionId"] not in paragraph["citation"]


async def test_leading_docx_paragraph_uses_document_root_fallback_without_inventing_an_authored_section():
    document = Document()
    document.add_paragraph("Prefatory source text.")
    document.add_heading("Scope", level=1)
    document.add_paragraph("Authored section text.")
    source = io.BytesIO()
    document.save(source)
    env = await ready(source=source.getvalue(), name="preamble.docx")
    leading = read(env)["documents"][0]["sections"][0]
    selected = read(env, leading["readSelector"])
    paragraph = selected["documents"][0]["sections"][0]
    assert paragraph["text"] == "<p>Prefatory source text.</p>"
    assert paragraph["containingSectionId"] == "n0"
    assert paragraph["containingSectionScope"] == "document_root_no_containing_authored_section"
    assert paragraph["containingSectionSelector"]["documents"][0]["nodeIds"] == ["n0"]
    assert paragraph["sectionId"].startswith("ev1.") and paragraph["sectionId"] != "n0"
    assert text_of(read(env, paragraph["containingSectionSelector"])) == text_of(
        read(env, selected["documents"][0]["fullSelector"])
    )
