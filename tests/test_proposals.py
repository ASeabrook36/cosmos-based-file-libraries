"""Proposal logic and the chat client, without any model (spec §9.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from fli.proposals import (
    AzureOpenAIChat,
    ChatError,
    DocumentProposals,
    ProposalError,
    description_due,
    description_messages,
    document_messages,
    opening_text,
    parse_description,
    parse_document_proposals,
    prompt_vocabulary,
    sample_documents,
    settle_proposals,
)


@dataclass
class Piece:
    text: str
    token_count: int


def test_opening_text_takes_whole_chunks_up_to_the_budget_and_at_least_one():
    pieces = [Piece("a", 1200), Piece("b", 1200), Piece("c", 1200)]
    assert opening_text(pieces) == "a\n\nb"
    assert opening_text([Piece("huge", 9000), Piece("next", 10)]) == "huge"


def test_the_prompt_vocabulary_lists_the_most_used_values_first_and_is_capped():
    library = {
        "tagVocabulary": {"region": {"EMEA": 5, "APAC": 9}, **{f"k{i}": {"v": 1} for i in range(300)}},
        "filterValues": {"authors": {f"a{i}": i for i in range(80)}},
    }
    vocab = prompt_vocabulary(library)
    assert vocab["tags"]["region"] == ["APAC", "EMEA"]
    assert sum(len(v) for v in vocab["tags"].values()) == 200
    assert len(vocab["authors"]) == 50 and vocab["authors"][0] == "a79"


def test_the_document_prompt_carries_the_vocabulary_and_the_text_as_data():
    messages = document_messages("q4.pdf", "Opening words", {"tags": {"region": ["EMEA"]}, "authors": ["Jane"]})
    assert messages[0]["role"] == "system" and "Reuse the library's existing keys" in messages[0]["content"]
    assert "only when the document states or clearly implies" in messages[0]["content"]
    user = messages[1]["content"]
    assert '"region": ["EMEA"]' in user and '"Jane"' in user and user.endswith("Opening words")


def test_parsing_keeps_valid_entries_and_drops_invalid_ones_one_by_one():
    data = {
        "tags": [
            {"key": "region", "value": "EMEA"},
            {"key": "region", "value": "EMEA"},  # duplicate
            {"key": "a=b", "value": "x"},  # key with "="
            {"key": "topic", "value": ""},
            {"key": "k" * 101, "value": "x"},
            "not a tag",
            {"key": " topic ", "value": "  gross   margin "},
        ],
        "period": {"start": "2025-10-01", "end": "2025-12-31"},
        "author": "  Jane   Doe ",
    }
    p = parse_document_proposals(data)
    assert p.tags == [("region", "EMEA"), ("topic", "gross margin")]
    assert p.period == ("2025-10-01", "2025-12-31") and p.author == "Jane Doe"


@pytest.mark.parametrize(
    "period",
    [
        {"start": "2025-12-31", "end": "2025-10-01"},
        {"start": "2025-02-30", "end": "2025-03-01"},
        {"start": "Q4 2025", "end": "2025-12-31"},
        {"start": "2025-10-01"},
        "2025",
        None,
    ],
)
def test_an_invalid_period_is_dropped(period):
    assert parse_document_proposals({"period": period}).period is None


def test_parsing_caps_tags_and_tolerates_garbage():
    many = {"tags": [{"key": "k", "value": str(i)} for i in range(20)], "author": 7}
    p = parse_document_proposals(many)
    assert len(p.tags) == 8 and p.author is None
    assert parse_document_proposals({"tags": "x", "period": [1]}) == DocumentProposals()
    assert parse_document_proposals({"author": "x" * 201}).author is None


def doc(**fields):
    return {"tags": [], "periodStart": None, "periodEnd": None, "author": None, **fields}


PROPOSED = DocumentProposals(
    tags=[("region", "EMEA"), ("topic", "margin")], period=("2025-10-01", "2025-12-31"), author="Jane"
)


def test_without_auto_accept_everything_waits_and_nothing_changes():
    record = doc(tags=[{"key": "region", "value": "EMEA", "source": "user"}])
    settle_proposals(record, PROPOSED, accept=False)
    assert record["tagProposals"] == [{"key": "topic", "value": "margin"}]  # region=EMEA is already a tag
    assert record["periodProposal"] == {"start": "2025-10-01", "end": "2025-12-31"}
    assert record["authorProposal"] == "Jane"
    assert record["periodStart"] is None and record["author"] is None
    assert record["facets"] == ["region=EMEA"]


def test_auto_accept_fills_empty_fields_and_adds_tags_as_accepted_proposals():
    record = doc(tags=[{"key": "region", "value": "EMEA", "source": "user"}])
    settle_proposals(record, PROPOSED, accept=True)
    assert record["tags"] == [
        {"key": "region", "value": "EMEA", "source": "user"},
        {"key": "topic", "value": "margin", "source": "accepted-proposal"},
    ]
    assert record["facets"] == ["region=EMEA", "topic=margin"]
    assert (record["periodStart"], record["periodEnd"], record["author"]) == ("2025-10-01", "2025-12-31", "Jane")
    assert record["tagProposals"] == [] and record["periodProposal"] is None and record["authorProposal"] is None


def test_auto_accept_never_replaces_a_value_someone_set_and_leaves_it_proposed():
    record = doc(periodStart="2024-01-01", periodEnd="2024-12-31", author="Sam")
    settle_proposals(record, PROPOSED, accept=True)
    assert (record["periodStart"], record["author"]) == ("2024-01-01", "Sam")
    assert record["periodProposal"] == {"start": "2025-10-01", "end": "2025-12-31"}
    assert record["authorProposal"] == "Jane"


def test_a_proposal_equal_to_the_current_value_is_not_proposed():
    record = doc(periodStart="2025-10-01", periodEnd="2025-12-31", author="Jane")
    settle_proposals(record, PROPOSED, accept=False)
    assert record["periodProposal"] is None and record["authorProposal"] is None


def test_fresh_proposals_replace_earlier_ones():
    record = doc(tagProposals=[{"key": "old", "value": "x"}], authorProposal="Old")
    settle_proposals(record, DocumentProposals(tags=[("new", "y")]), accept=False)
    assert record["tagProposals"] == [{"key": "new", "value": "y"}] and record["authorProposal"] is None


def test_the_sample_spreads_over_upload_time():
    docs = [{"id": f"d{i:03}", "uploadedAt": f"2026-01-01T00:{i // 60:02}:{i % 60:02}"} for i in range(90)]
    sample = sample_documents(list(reversed(docs)))
    assert len(sample) == 30 and sample[0]["id"] == "d000" and sample[-1]["id"] == "d087"
    assert sample_documents(docs[:5]) == docs[:5]


def test_the_description_prompt_carries_name_description_vocabulary_and_samples():
    library = {"name": "Finance", "description": "old", "tagVocabulary": {"region": {"EMEA": 2}}, "stats": {}}
    messages = description_messages(library, [("q4.pdf", "Revenue rose")])
    assert "2 to 4 sentences" in messages[0]["content"] and "should not be used for" in messages[0]["content"]
    user = json.loads(messages[1]["content"])
    assert user["libraryName"] == "Finance" and user["currentDescription"] == "old"
    assert user["tagVocabulary"] == {"region": ["EMEA"]}
    assert user["sampleDocuments"] == [{"fileName": "q4.pdf", "openingLines": "Revenue rose"}]


def test_a_description_must_be_present_and_fit_what_patch_accepts():
    assert parse_description({"description": "  Quarterly reports.  "}) == "Quarterly reports."
    for bad in ({}, {"description": " "}, {"description": 3}, {"description": "x" * 4001}):
        with pytest.raises(ProposalError):
            parse_description(bad)


NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def proposal(count, hours_ago):
    return {
        "descriptionProposal": {
            "basedOnDocumentCount": count,
            "generatedAt": (NOW - timedelta(hours=hours_ago)).isoformat(),
        }
    }


@pytest.mark.parametrize(
    ("library", "count", "due"),
    [
        ({}, 0, False),  # nothing to describe
        ({}, 1, True),  # no proposal yet
        (proposal(5, 1), 14, False),
        (proposal(5, 1), 15, True),  # 10 documents more
        (proposal(15, 1), 5, True),  # 10 documents fewer
        (proposal(5, 25), 6, True),  # 24 hours, and something changed
        (proposal(5, 25), 5, False),  # 24 hours, nothing changed
        (proposal(5, 23), 6, False),
    ],
)
def test_a_description_is_due_after_10_documents_or_24_hours(library, count, due):
    assert description_due(library, count, NOW) is due


def make_chat(handler):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    async def token():
        return "aad-token"

    chat = AzureOpenAIChat(
        endpoint="https://aoai.example.invalid",
        deployment="chat",
        token_provider=token,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleep=sleep,
    )
    return chat, sleeps


def answer(content, status=200, **headers):
    body = {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    return httpx.Response(status, json=body, headers=headers)


async def test_the_chat_client_asks_for_json_and_returns_it_with_usage():
    seen = []

    def handler(request):
        seen.append(request)
        return answer('{"tags": []}')

    chat, _ = make_chat(handler)
    reply = await chat.complete_json([{"role": "user", "content": "x"}])
    assert (reply.data, reply.prompt_tokens, reply.completion_tokens) == ({"tags": []}, 11, 3)
    request = seen[0]
    body = json.loads(request.content)
    assert request.url.path == "/openai/deployments/chat/chat/completions"
    assert request.headers["Authorization"] == "Bearer aad-token" and request.url.params["api-version"]
    assert body["response_format"] == {"type": "json_object"} and "temperature" not in body
    assert body["max_completion_tokens"] > 0


async def test_the_chat_client_honours_retry_after_then_succeeds():
    responses = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(503), answer("{}")]
    chat, sleeps = make_chat(lambda request: responses.pop(0))
    assert (await chat.complete_json([])).data == {}
    assert sleeps[0] == 2.0 and len(sleeps) == 2


async def test_the_chat_client_gives_up_after_its_attempts():
    chat, sleeps = make_chat(lambda request: httpx.Response(429))
    with pytest.raises(ChatError) as error:
        await chat.complete_json([])
    assert error.value.throttled and len(sleeps) == 4


async def test_a_rejected_request_reports_the_status_never_the_body():
    chat, _ = make_chat(lambda request: httpx.Response(400, text="prompt echoed: SECRET-TEXT"))
    with pytest.raises(ChatError) as error:
        await chat.complete_json([])
    assert "400" in str(error.value) and "SECRET" not in str(error.value)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", None])
async def test_an_answer_that_is_not_a_json_object_is_an_error(content):
    chat, _ = make_chat(lambda request: answer(content))
    with pytest.raises(ChatError):
        await chat.complete_json([])
