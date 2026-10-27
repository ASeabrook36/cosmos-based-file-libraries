"""The demo platform's logic: the attachment registry and the agent loop, against fakes."""

from __future__ import annotations

import json

import httpx
import pytest

from fli_demo.agent import Reply, ToolCall, build_tools, plain_dollars, run_turn
from fli_demo.client import IndexClient, IndexError_
from fli_demo.proposals import accept_all, proposals_of, review_request, summary
from fli_demo.registry import PERSONAL, Registry, create_personal_agent


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "d", "parameters": {"type": "object"}}}


class FakeIndex:
    def __init__(self, names=None, refuse=()):
        self.names = names or {"lib-a": "search_library_finance", "lib-b": "search_library_hr"}
        self.refuse = set(refuse)
        self.searches = []
        self.filters = []

    def tool(self, library_id):
        return tool(self.names[library_id])

    def search(self, library_id, agent_id, query, top=8, filters=None):
        self.searches.append((library_id, agent_id, query))
        self.filters.append(filters)
        if library_id in self.refuse:
            raise IndexError_(403, "library_not_bound_to_agent", "no")
        return {
            "library": {"libraryId": library_id, "name": library_id.upper()},
            "coverage": {
                "disposition": "results",
                "filtersApplied": dict(filters or {}),
                "notes": ["1 of 2 documents have no period set, so filtering by period left them out."]
                if filters and "period" in filters
                else [],
            },
            "usage": {"requestCharge": 12.5, "totalMs": 80},
            "results": [
                {"fileName": "q4.pdf", "location": {"page": 3}, "heading": None, "text": "margin fell"},
                {"fileName": "q4.pdf", "location": {"page": 3}, "heading": None, "text": "again"},
            ],
        }


def scripted(*replies):
    seen = []
    it = iter(replies)

    def chat(messages, tools):
        seen.append((list(messages), tools))
        return next(it)

    chat.seen = seen
    return chat


AGENT = {"id": "agent-7", "instructions": "", "libraryIds": ["lib-a", "lib-b"]}


def test_a_tool_call_searches_the_attached_library_with_the_agents_id():
    index = FakeIndex()
    chat = scripted(
        Reply(None, [ToolCall("c1", "search_library_hr", json.dumps({"query": "leave policy", "libraryId": "lib-a"}))]),
        Reply("Answer."),
    )
    turn = run_turn(chat, index, AGENT, [], "What is the leave policy?")
    assert index.searches == [("lib-b", "agent-7", "leave policy")]  # path from the tool name, never from arguments
    assert turn.answer == "Answer."
    [trace] = turn.searches
    assert (trace.tool_name, trace.request_charge, trace.result_count) == ("search_library_hr", 12.5, 2)
    assert [c.label() for c in trace.citations] == ["q4.pdf (page 3)"]  # de-duplicated
    tool_message = chat.seen[1][0][-1]
    assert tool_message["role"] == "tool" and "margin fell" in tool_message["content"]
    assert "chunkId" not in tool_message["content"]


def test_unknown_tools_and_bad_arguments_go_back_to_the_model_not_the_index():
    index = FakeIndex()
    chat = scripted(
        Reply(None, [ToolCall("c1", "search_library_nope", "{}"), ToolCall("c2", "search_library_hr", "not json")]),
        Reply("Sorry."),
    )
    turn = run_turn(chat, index, AGENT, [], "q")
    assert index.searches == [] and turn.searches == []
    assert "unknown tool" in chat.seen[1][0][-2]["content"]
    assert "query" in chat.seen[1][0][-1]["content"]


def test_a_refusal_is_traced_and_shown_to_the_model():
    index = FakeIndex(refuse={"lib-a"})
    chat = scripted(Reply(None, [ToolCall("c1", "search_library_finance", '{"query": "x"}')]), Reply("No access."))
    turn = run_turn(chat, index, AGENT, [], "q")
    assert "403 library_not_bound_to_agent" in turn.searches[0].error
    assert turn.answer == "No access."


def test_the_loop_stops_after_the_round_limit_and_asks_for_an_answer_without_tools():
    index = FakeIndex()
    again = Reply(None, [ToolCall("c", "search_library_finance", '{"query": "x"}')])
    chat = scripted(again, again, again, again, Reply("Final."))
    turn = run_turn(chat, index, AGENT, [], "q", max_rounds=4)
    assert turn.answer == "Final." and len(turn.searches) == 4
    assert chat.seen[-1][1] == []


def test_duplicate_tool_names_are_made_unique():
    index = FakeIndex(names={"lib-a": "search_library_x", "lib-b": "search_library_x"})
    tools, owners = build_tools(index, ["lib-a", "lib-b"])
    assert [t["function"]["name"] for t in tools] == ["search_library_x", "search_library_x_2"]
    assert owners == {"search_library_x": "lib-a", "search_library_x_2": "lib-b"}


def test_registry_round_trip_and_attachments(tmp_path):
    reg = Registry(tmp_path / "s" / "state.json")
    agent = reg.create("official", "Finance Assistant")
    reg.attach(agent["id"], "lib-a")
    reg.attach(agent["id"], "lib-a")
    reg.attach(agent["id"], "lib-b")
    assert reg.get(agent["id"])["libraryIds"] == ["lib-a", "lib-b"]
    reg.detach(agent["id"], "lib-a")
    assert Registry(tmp_path / "s" / "state.json").get(agent["id"])["libraryIds"] == ["lib-b"]
    reg.delete(agent["id"])
    assert reg.agents() == []


def test_a_personal_agent_gets_one_library_bound_to_its_own_id(tmp_path):
    created = []

    class Client:
        def create_library(self, kind, name, description="", bound_agent_id=None):
            created.append((kind, bound_agent_id))
            return {"id": "lib-p"}

    agent = create_personal_agent(Registry(tmp_path / "state.json"), Client(), "Ann")
    assert agent["kind"] == PERSONAL and agent["libraryIds"] == ["lib-p"]
    assert created == [("personal", agent["id"])]


def test_the_client_sends_the_key_and_parses_error_bodies():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        if request.url.path.endswith("/search"):
            assert request.headers["x-agent-id"] == "a1"
            return httpx.Response(403, json={"error": {"code": "library_not_bound_to_agent", "message": "nope"}})
        return httpx.Response(200, json={"libraries": [{"id": "l"}]})

    client = IndexClient(
        "http://index", "k", http=httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler))
    )
    assert client.list_libraries("shared") == [{"id": "l"}]
    with pytest.raises(IndexError_) as err:
        client.search("l", "a1", "q")
    assert (err.value.status, err.value.code) == (403, "library_not_bound_to_agent")


def test_the_client_replaces_and_deletes_through_the_documented_paths():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PUT":
            assert b'filename="v2.pdf"' in request.content
            return httpx.Response(202, json={"documentId": "d", "status": "queued", "generation": 2})
        return httpx.Response(202)

    client = IndexClient(
        "http://index", "k", http=httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler))
    )
    assert client.replace_document("l", "d", "v2.pdf", b"%PDF")["generation"] == 2
    assert client.delete_document("l", "d") is None and client.delete_library("l") is None
    assert seen == [
        ("PUT", "/v1/libraries/l/documents/d/content"),
        ("DELETE", "/v1/libraries/l/documents/d"),
        ("DELETE", "/v1/libraries/l"),
    ]


def test_the_tools_filters_reach_the_search_and_the_trace_and_nothing_else_does():
    index = FakeIndex()
    arguments = {
        "query": "margin",
        "period": {"from": "2025-10-01", "to": "2025-12-31"},
        "tags": ["region=EMEA"],
        "authors": [],  # empty: not sent
        "libraryId": "lib-b",  # never a filter (rule 3)
        "top": 20,  # not a tool argument
    }
    chat = scripted(Reply(None, [ToolCall("c1", "search_library_finance", json.dumps(arguments))]), Reply("A."))
    turn = run_turn(chat, index, AGENT, [], "q")
    assert index.searches == [("lib-a", "agent-7", "margin")]
    assert index.filters == [{"period": {"from": "2025-10-01", "to": "2025-12-31"}, "tags": ["region=EMEA"]}]
    [trace] = turn.searches
    assert trace.filters == trace.filters_applied == index.filters[0]
    assert trace.notes == ["1 of 2 documents have no period set, so filtering by period left them out."]
    seen_by_model = json.loads(chat.seen[1][0][-1]["content"])
    assert seen_by_model["notes"] == trace.notes and seen_by_model["filtersApplied"] == index.filters[0]


def test_a_search_without_filters_sends_none():
    index = FakeIndex()
    chat = scripted(Reply(None, [ToolCall("c1", "search_library_finance", json.dumps({"query": "q"}))]), Reply("A."))
    run_turn(chat, index, AGENT, [], "q")
    assert index.filters == [None]


def test_the_client_sends_filters_and_metadata_updates():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"results": []})

    client = IndexClient(
        "http://index", "k", http=httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler))
    )
    client.search("l", "a1", "q", filters={"tags": ["region=EMEA"]})
    client.search("l", "a1", "q")
    client.update_document("l", "d", tags=[{"key": "region", "value": "EMEA"}])
    assert seen[0][2]["filters"] == {"tags": ["region=EMEA"]} and "filters" not in seen[1][2]
    assert seen[2][:2] == ("PATCH", "/v1/libraries/l/documents/d")
    assert seen[2][2] == {"tags": [{"key": "region", "value": "EMEA"}]}


def test_the_client_reaches_the_proposal_endpoints():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(202) if request.method == "POST" else httpx.Response(200, json={})

    client = IndexClient(
        "http://index", "k", http=httpx.Client(base_url="http://index", transport=httpx.MockTransport(handler))
    )
    assert client.propose_description("l") is True
    client.update_library("l", autoAcceptTagProposals=True)
    client.resolve_proposals("l", "d", ["region=EMEA"], ["author"])
    assert seen == [
        ("POST", "/v1/libraries/l/description:propose", None),
        ("PATCH", "/v1/libraries/l", {"autoAcceptTagProposals": True}),
        ("PATCH", "/v1/libraries/l/documents/d", {"acceptProposals": ["region=EMEA"], "dismissProposals": ["author"]}),
    ]


PROPOSED_DOC = {
    "tags": [{"key": "type", "value": "report", "source": "user"}],
    "tagProposals": [{"key": "region", "value": "EMEA"}, {"key": "topic", "value": "margin"}],
    "periodProposal": {"start": "2025-10-01", "end": "2025-12-31"},
    "authorProposal": "Jane Doe",
}


def test_the_review_lists_every_proposal():
    assert [p.item for p in proposals_of(PROPOSED_DOC)] == ["region=EMEA", "topic=margin", "period", "author"]
    assert accept_all(PROPOSED_DOC) == ["region=EMEA", "topic=margin", "period", "author"]
    assert summary(PROPOSED_DOC) == "region=EMEA, topic=margin, period 2025-10-01..2025-12-31, author Jane Doe"
    assert proposals_of({"tagProposals": [], "periodProposal": None}) == []


def test_unchanged_acceptances_dismissals_and_edits_become_one_patch():
    decisions = {
        "region=EMEA": ("accept", "region=Europe"),  # edited
        "topic=margin": ("accept", "topic=margin"),
        "period": ("accept", "2025-07-01..2025-09-30"),  # edited
        "author": ("dismiss", "Jane Doe"),
    }
    assert review_request(PROPOSED_DOC, decisions) == {
        "tags": [{"key": "type", "value": "report"}, {"key": "region", "value": "Europe"}],
        "periodStart": "2025-07-01",
        "periodEnd": "2025-09-30",
        "acceptProposals": ["topic=margin"],
        "dismissProposals": ["region=EMEA", "period", "author"],
    }


def test_leaving_everything_sends_nothing_and_a_bad_edit_is_refused():
    assert review_request(PROPOSED_DOC, {}) is None
    assert review_request(PROPOSED_DOC, {"author": ("leave", "x")}) is None
    with pytest.raises(ValueError):
        review_request(PROPOSED_DOC, {"period": ("accept", "Q3 2025")})
    with pytest.raises(ValueError):
        review_request(PROPOSED_DOC, {"region=EMEA": ("accept", "Europe")})


def test_dollar_amounts_render_as_text_not_latex():
    answer = "Revenue was $179,465 thousand (Analytics $169,253)."
    assert plain_dollars(answer) == r"Revenue was \$179,465 thousand (Analytics \$169,253)."
    assert plain_dollars(r"already \$5") == r"already \$5"  # never escaped twice
    assert plain_dollars("no amounts") == "no amounts"


@pytest.mark.parametrize(
    "headers,waits",
    [
        ({"retry-after": "7"}, [7.0]),
        ({"retry-after-ms": "1500"}, [1.5]),
        ({"retry-after": "120"}, [60.0, 60.0]),
        ({}, [60.0]),
    ],
)
def test_azure_chat_respects_rate_limit_headers_and_bounds_response_tokens(headers, waits):
    from types import SimpleNamespace

    from openai import RateLimitError

    from fli_demo.agent import azure_chat

    error = RateLimitError(
        "quota exceeded",
        response=httpx.Response(
            429, headers=headers, request=httpx.Request("POST", "https://example.openai.azure.com")
        ),
        body=None,
    )
    replies = iter(
        [error, SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Answer", tool_calls=[]))])]
    )
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        item = next(replies)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    slept = []
    chat = azure_chat("endpoint", "deployment", client=client, sleep=slept.append)
    assert chat([{"role": "user", "content": "question"}], []).content == "Answer"
    assert slept == waits and len(seen) == 2
    assert all(x["max_completion_tokens"] == 8000 for x in seen)


def test_azure_chat_exhausts_a_bounded_number_of_rate_limit_attempts():
    from types import SimpleNamespace

    from openai import RateLimitError

    from fli_demo.agent import azure_chat

    error = RateLimitError(
        "quota exceeded",
        response=httpx.Response(
            429, headers={"retry-after": "1"}, request=httpx.Request("POST", "https://example.openai.azure.com")
        ),
        body=None,
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        raise error

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    slept = []
    with pytest.raises(RateLimitError):
        azure_chat("endpoint", "deployment", client=client, sleep=slept.append)([], [])
    assert len(calls) == 3 and slept == [1.0, 1.0]


def test_passages_can_be_reduced_to_fit_remaining_context_without_discarding_all_search_evidence():
    from fli.chunking import ApproxTokenizer
    from fli_demo.agent import _run_search

    index = FakeIndex()
    original = index.search

    def many_passages(*args, **kwargs):
        response = original(*args, **kwargs)
        response["results"] = [
            {"fileName": f"source-{i}.md", "location": {}, "text": "Relevant evidence. " * 100} for i in range(8)
        ]
        return response

    index.search = many_passages
    traces = []
    tokenizer = ApproxTokenizer()
    content, _ = _run_search(
        ToolCall("s", "search_library_finance", '{"query":"supporting facts"}'),
        index,
        AGENT,
        {"search_library_finance": "lib-a"},
        {},
        traces,
        lambda: 0,
        1000,
        tokenizer,
    )
    result = json.loads(content)
    assert 0 < result["passagesDelivered"] < result["passagesMatched"] == 8
    assert result["contextLimited"] and result["disposition"] == "context_limited"
    assert len(tokenizer.encode(content)) <= 1000
    assert result["results"][0]["file"] == "source-0.md"
    assert traces[0].result_count == result["passagesDelivered"]
    assert len(traces[0].citations) == result["passagesDelivered"]


def test_routing_guidance_distinguishes_coverage_from_attention_and_specialized_model_features():
    chat = scripted(Reply("Answer"))
    run_turn(chat, FakeIndex(), AGENT, [], "question")
    guidance = chat.seen[0][0][0]["content"]
    assert "position does not require a full read" in guidance
    assert "More context can impair attention" in guidance
    assert "Do not turn a desired" in guidance
    assert "never claim exhaustive coverage" in guidance
