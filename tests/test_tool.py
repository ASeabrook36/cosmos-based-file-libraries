"""The tool definition offers the filters a library can use (spec §6.4)."""

from __future__ import annotations

from fli.tool import LISTED, MAX_ENUM, tool_definition
from fli.vocabulary import filter_values, tag_vocabulary


def library(documents, description="Quarterly finance reports."):
    return {
        "name": "Finance — Quarterly Reports",
        "description": description,
        "tagVocabulary": tag_vocabulary(documents),
        "filterValues": filter_values(documents),
        "stats": {"documentCount": len(documents)},
    }


def document(**fields):
    return {"activeGeneration": 1, "status": "ready", "chunkCount": 10, "fileType": "pdf", "facets": [], **fields}


DOCUMENTS = [
    document(
        facets=["region=EMEA", "topic=margin"],
        author="Jane Doe",
        periodStart="2024-01-01",
        periodEnd="2024-03-31",
        authoredAt="2024-04-02T09:00:00+00:00",
    ),
    document(
        facets=["region=EMEA", "region=APAC"],
        author="Sam Lee",
        fileType="pptx",
        periodStart="2025-10-01",
        periodEnd="2025-12-31",
    ),
    document(facets=["region=NA"], fileType="xlsx"),
]


def test_a_new_library_offers_query_only():
    fn = tool_definition(library([]))["function"]
    assert list(fn["parameters"]["properties"]) == ["query"] and fn["parameters"]["required"] == ["query"]
    assert fn["description"] == "Quarterly finance reports."


def test_filters_with_values_are_offered_with_the_real_values():
    fn = tool_definition(library(DOCUMENTS))["function"]
    props = fn["parameters"]["properties"]
    assert list(props) == ["query", "tags", "authors", "period", "authoredBetween", "fileTypes"]
    assert props["tags"]["items"]["enum"] == ["region=APAC", "region=EMEA", "region=NA", "topic=margin"]
    assert props["authors"]["items"]["enum"] == ["Jane Doe", "Sam Lee"]
    assert props["fileTypes"]["items"]["enum"] == ["pdf", "pptx", "xlsx"]
    assert set(props["period"]["properties"]) == {"from", "to"}
    assert "uploadedBetween" not in props and "libraryId" not in props  # rule 3
    assert fn["description"] == (
        "Quarterly finance reports.\n\nFilters available in this library: "
        "tags region=(EMEA, APAC, NA), topic=(margin); authors (Jane Doe, Sam Lee); "
        "documents cover periods 2024-01-01 to 2025-12-31; file types pdf, pptx, xlsx. "
        "Use 'period' for what a document is about, 'authoredBetween' for when it was written."
    )


def test_a_filter_that_cannot_narrow_is_not_offered():
    single = [document(), document()]  # one file type, one content kind, no tags, authors or dates
    assert list(tool_definition(library(single))["function"]["parameters"]["properties"]) == ["query"]


def test_content_kinds_are_offered_once_a_library_holds_images():
    with_images = [document(), document(chunkCount=5, imageCount=5)]
    props = tool_definition(library(with_images))["function"]["parameters"]["properties"]
    assert props["contentKinds"]["items"]["enum"] == ["image", "text"]


def test_too_many_tags_drop_the_enum_and_list_the_most_used():
    many = [document(facets=[f"code=c{i:03}"] * (1 + (i < 3))) for i in range(MAX_ENUM + 1)]
    fn = tool_definition(library(many))["function"]
    assert "enum" not in fn["parameters"]["properties"]["tags"]["items"]
    listed = fn["description"].split("most used: ")[1].split(")")[0].split(", ")
    assert len(listed) == LISTED and listed[:3] == ["code=c000", "code=c001", "code=c002"]


def test_documents_not_yet_searchable_do_not_count():
    from fli.vocabulary import searchable

    pending = document(activeGeneration=None, facets=["region=EMEA"])
    deleting = document(status="deleting", facets=["region=APAC"])
    assert tag_vocabulary(searchable([pending, deleting, document(facets=["region=NA"])])) == {"region": {"NA": 1}}


def test_filter_values_count_what_is_missing():
    values = filter_values(DOCUMENTS)
    assert values["missing"] == {"period": 1, "author": 1, "authoredAt": 2}
    assert values["period"] == {"from": "2024-01-01", "to": "2025-12-31"}
    assert values["authors"] == {"Jane Doe": 1, "Sam Lee": 1} and values["contentKinds"] == {"text": 3}
