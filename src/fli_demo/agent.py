"""The agent loop: one tool per attached library, each call mapped to ``POST …/search``.

The model never supplies a library id. A tool call is mapped, by tool name, to the library the
platform attached, and the agent's own id goes in ``X-Agent-Id`` (CLAUDE.md rule 3). The filters the
tool offers (spec §6.4) are passed through as the search's ``filters``; any other argument is
dropped. Query text and chunk text are not logged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_ROUNDS = 4
TOOL_RESULT_CHARS = 1500  # per chunk, as handed to the model
FILTER_ARGUMENTS = ("period", "authoredBetween", "authors", "tags", "fileTypes", "contentKinds")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Reply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


Chat = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Reply]


class Index(Protocol):
    def tool(self, library_id: str) -> dict[str, Any]: ...
    def search(
        self, library_id: str, agent_id: str, query: str, top: int = 8, filters: Mapping[str, Any] | None = None
    ) -> dict[str, Any]: ...


@dataclass
class Citation:
    file_name: str
    location: dict[str, Any]
    heading: str | None = None

    def label(self) -> str:
        where = ", ".join(f"{k} {v}" for k, v in self.location.items())
        return f"{self.file_name} ({where})" if where else self.file_name


@dataclass
class SearchTrace:
    tool_name: str
    library_id: str
    library_name: str
    query: str
    latency_ms: float
    request_charge: float | None
    result_count: int
    disposition: str | None
    citations: list[Citation]
    error: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)  # what the model asked for
    filters_applied: dict[str, Any] = field(default_factory=dict)  # what the index says it applied
    notes: list[str] = field(default_factory=list)  # coverage notes: what a filter left out
    images: list[dict[str, Any]] = field(default_factory=list)  # {url, caption}, for image results


@dataclass
class Turn:
    answer: str
    searches: list[SearchTrace]
    messages: list[dict[str, Any]]


def build_tools(index: Index, library_ids: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Tool definitions from ``GET …/tool`` and the name → library id map. Duplicate names get a suffix."""
    tools: list[dict[str, Any]] = []
    owners: dict[str, str] = {}
    for library_id in library_ids:
        tool = json.loads(json.dumps(index.tool(library_id)))
        name = base = tool["function"]["name"]
        n = 2
        while name in owners:
            name = f"{base[: 64 - len(str(n)) - 1]}_{n}"
            n += 1
        tool["function"]["name"] = name
        owners[name] = library_id
        tools.append(tool)
    return tools, owners


def compact(results: Mapping[str, Any]) -> dict[str, Any]:
    """What the model sees: what was filtered and left out, then file, location and text. No ids, no scores."""
    coverage = results["coverage"]
    return {
        "disposition": coverage["disposition"],
        "filtersApplied": coverage.get("filtersApplied") or {},
        "notes": coverage.get("notes") or [],
        "results": [
            {
                "file": r["fileName"],
                "location": r["location"],
                "heading": r.get("heading"),
                "text": r["text"][:TOOL_RESULT_CHARS],
            }
            for r in results["results"]
        ],
    }


def run_turn(
    chat: Chat,
    index: Index,
    agent: Mapping[str, Any],
    history: Sequence[dict[str, Any]],
    question: str,
    library_names: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> Turn:
    tools, owners = build_tools(index, agent["libraryIds"])
    system = (
        agent.get("instructions")
        or "You answer from the libraries you are given. Search before answering. Cite file names and pages."
    ) + "\nAnswer only from search results. If the results do not contain the answer, say so."
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history]
    messages.append({"role": "user", "content": question})
    searches: list[SearchTrace] = []
    names = library_names or {}

    for _ in range(MAX_ROUNDS):
        reply = chat(messages, tools)
        if not reply.tool_calls:
            return Turn(reply.content or "", searches, messages + [{"role": "assistant", "content": reply.content}])
        messages.append(
            {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in reply.tool_calls
                ],
            }
        )
        for call in reply.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _run_search(call, index, agent, owners, names, searches, clock),
                }
            )
    # out of rounds: ask for an answer without tools
    messages.append({"role": "user", "content": "Answer now with what you have found."})
    final = chat(messages, [])
    return Turn(final.content or "", searches, messages + [{"role": "assistant", "content": final.content}])


def _run_search(call, index, agent, owners, names, searches, clock) -> str:
    library_id = owners.get(call.name)
    if library_id is None:
        return json.dumps({"error": f"unknown tool {call.name}"})
    try:
        arguments = json.loads(call.arguments or "{}")
        query = str(arguments["query"])
    except (ValueError, KeyError, TypeError):
        return json.dumps({"error": "the tool needs a JSON object with a string 'query'"})
    filters = {k: arguments[k] for k in FILTER_ARGUMENTS if arguments.get(k) not in (None, [], {})}
    started = clock()
    try:
        response = index.search(library_id, agent["id"], query, filters=filters or None)
    except Exception as exc:  # the index's refusals (403, 404, 400) go back to the model and to the trace
        searches.append(
            SearchTrace(call.name, library_id, names.get(library_id, library_id), query, (clock() - started) * 1000,
                        None, 0, None, [], error=str(exc), filters=filters)
        )  # fmt: skip
        return json.dumps({"error": str(exc)})
    latency = (clock() - started) * 1000
    seen: list[Citation] = []
    for r in response["results"]:
        c = Citation(r["fileName"], r["location"], r.get("heading"))
        if c.label() not in {x.label() for x in seen}:
            seen.append(c)
    coverage = response["coverage"]
    images = [
        {"url": r["image"]["url"], "caption": Citation(r["fileName"], r["location"]).label()}
        for r in response["results"]
        if r.get("image")
    ]
    searches.append(
        SearchTrace(
            call.name, library_id, response["library"]["name"], query, latency,
            (response.get("usage") or {}).get("requestCharge"),
            len(response["results"]), coverage["disposition"], seen,
            filters=filters, filters_applied=coverage.get("filtersApplied") or {},
            notes=coverage.get("notes") or [], images=images,
        )
    )  # fmt: skip
    return json.dumps(compact(response))


def azure_chat(endpoint: str, deployment: str) -> Chat:
    """The Azure OpenAI chat deployment with function calling, authenticated by the signed-in identity."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_version="2024-10-21",
        azure_ad_token_provider=get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        ),
    )

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        kwargs: dict[str, Any] = {"tools": tools} if tools else {}
        message = client.chat.completions.create(model=deployment, messages=messages, **kwargs).choices[0].message
        return Reply(
            message.content,
            [ToolCall(c.id, c.function.name, c.function.arguments) for c in message.tool_calls or []],
        )

    return chat
