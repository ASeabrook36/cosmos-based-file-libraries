"""The function-calling tool for one library (spec §6.4).

Phase 1 offers ``query`` only. Phase 3 adds the filters, with enums built from the library's
tag vocabulary. The library id is never a parameter: the platform binds the tool to one
library and calls ``POST …/search`` with it in the path (CLAUDE.md rule 3).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PREFIX = "search_library_"
MAX_NAME = 64  # function names: 1-64 of [A-Za-z0-9_-]


def tool_name(library_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", library_name.lower()).strip("_") or "library"
    return (PREFIX + slug)[:MAX_NAME].rstrip("_")


def tool_definition(library: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool_name(library.get("name", "")),
            "description": library.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, in natural language."},
                },
                "required": ["query"],
            },
        },
    }
