"""The demo platform's logic: the attachment registry and the agent loop, against fakes."""

from __future__ import annotations

import json

import httpx
import pytest

from fli_demo.agent import Reply, ToolCall, build_tools, run_turn
from fli_demo.client import IndexClient, IndexError_
from fli_demo.registry import PERSONAL, Registry, create_personal_agent


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "d", "parameters": {"type": "object"}}}


class FakeIndex:
    def __init__(self, names=None, refuse=()):
        self.names = names or {"lib-a": "search_library_finance", "lib-b": "search_library_hr"}
        self.refuse = set(refuse)
        self.searches = []

    def tool(self, library_id):
        return tool(self.names[library_id])

    def search(self, library_id, agent_id, query, top=8):
        self.searches.append((library_id, agent_id, query))
        if library_id in self.refuse:
            raise IndexError_(403, "library_not_bound_to_agent", "no")
        return {
            "library": {"libraryId": library_id, "name": library_id.upper()},
            "coverage": {"disposition": "results"},
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
    turn = run_turn(chat, index, AGENT, [], "q")
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
