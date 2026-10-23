"""Diagnose search ranking against the live account. Read-only: queries only, no writes.

    python scripts/diagnose_search.py

For a few questions, runs the same search several ways against the "Phase 1 exit check"
library and prints, for each, the top files and pages, the request charge and how many HTTP
responses the SDK made. A ranked search should differ between questions; a search that
returns the same items at the same cost whatever the question is not ranking.

Variants:
  hybrid+pk    ORDER BY RANK RRF(...), partition_key=[libraryId]   (what the API does)
  hybrid       the same SQL, library only in WHERE (the SDK fetches a query plan)
  vector+pk    ORDER BY VectorDistance(...), partition_key=[libraryId]
  fulltext+pk  ORDER BY RANK FullTextScore(...), partition_key=[libraryId]
  fulltext     the same, library only in WHERE

Prints to the terminal only; writes nothing.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fli.query import (  # noqa: E402
    MAX_LEXICAL_TERMS,
    PROJECTION,
    SearchFilters,
    lexical_terms,
    render_search,
    to_float16_query,
)
from fli.search import QueryCost  # noqa: E402

LIBRARY_NAME = "Phase 1 exit check"
QUESTIONS = [
    "What were the main drivers of cash flow from operations this year?",
    "What concerns did respondents raise about taxi licensing across local authorities?",
    "Who is Mr Darcy and how does Elizabeth first regard him?",
    "How is gross margin calculated?",
]
TOP = 10


def fulltext_sql(terms: list[str]) -> tuple[str, list[dict[str, Any]]]:
    names = [f"@t{i}" for i in range(len(terms))]
    sql = (
        f"SELECT TOP {TOP} {', '.join(PROJECTION)} FROM c WHERE c.libraryId = @libraryId "
        f"ORDER BY RANK FullTextScore(c.text, {', '.join(names)})"
    )
    return sql, [{"name": n, "value": t} for n, t in zip(names, terms, strict=True)]


async def run(container: Any, sql: str, params: list[dict[str, Any]], pk: list[str] | None) -> tuple[list, QueryCost]:
    cost = QueryCost()
    kwargs: dict[str, Any] = {"raw_response_hook": cost}
    if pk is not None:
        kwargs["partition_key"] = pk
    rows = [row async for row in container.query_items(sql, parameters=params, **kwargs)]
    return rows, cost


def describe(rows: list[dict[str, Any]]) -> str:
    return "; ".join(f"{r.get('fileName')} {r.get('location')}" for r in rows[:3]) or "(none)"


async def main() -> int:
    for name in ("COSMOS_ENDPOINT", "AOAI_ENDPOINT", "AOAI_EMBEDDING_DEPLOYMENT"):
        if not os.environ.get(name):
            print(f"{name} must be set (eval scripts/deployment_env.py first)", file=sys.stderr)
            return 2
    import httpx
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from fli.docintel import SCOPE
    from fli.embedding import AzureOpenAIEmbedder
    from fli.tokens import CachedToken

    async with (
        DefaultAzureCredential() as credential,
        CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential) as cosmos,
        httpx.AsyncClient() as http,
    ):
        database = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE") or "fli")
        libraries = database.get_container_client("libraries")
        chunks = database.get_container_client("chunks")
        found = [
            row
            async for row in libraries.query_items(
                "SELECT c.id, c.createdAt FROM c WHERE c.name = @name AND c.status = 'active'",
                parameters=[{"name": "@name", "value": LIBRARY_NAME}],
            )
        ]
        if not found:
            print(f"no active library named {LIBRARY_NAME!r}", file=sys.stderr)
            return 2
        library_id = sorted(found, key=lambda r: r["createdAt"])[-1]["id"]
        embedder = AzureOpenAIEmbedder(
            endpoint=os.environ["AOAI_ENDPOINT"],
            deployment=os.environ["AOAI_EMBEDDING_DEPLOYMENT"],
            token_provider=CachedToken(credential, SCOPE),
            http=http,
        )
        pk = [library_id]
        for question in QUESTIONS:
            print(f"\nQ: {question}")
            [vector] = await embedder.embed([question])
            v16 = to_float16_query(vector)
            terms = lexical_terms(question)[:MAX_LEXICAL_TERMS]
            hybrid = render_search(library_id, v16, terms, SearchFilters(), TOP)
            vector_only = render_search(library_id, v16, [], SearchFilters(), TOP)
            ft_sql, ft_params = fulltext_sql(terms)
            ft_params = [{"name": "@libraryId", "value": library_id}, *ft_params]
            variants = [
                ("hybrid+pk", hybrid.sql, hybrid.parameters, pk),
                ("hybrid", hybrid.sql, hybrid.parameters, None),
                ("vector+pk", vector_only.sql, vector_only.parameters, pk),
                ("fulltext+pk", ft_sql, ft_params, pk),
                ("fulltext", ft_sql, ft_params, None),
            ]
            for label, sql, params, key in variants:
                try:
                    rows, cost = await run(chunks, sql, params, key)
                    print(
                        f"  {label:12} {cost.request_charge:8.2f} RU  {cost.responses:3} responses  "
                        f"{len(rows):2} rows  {describe(rows)}"
                    )
                except Exception as exc:  # report and carry on: each variant is independent
                    print(f"  {label:12} ERROR {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
