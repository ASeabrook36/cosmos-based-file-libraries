"""The Admin page's proposal review: what to show, and the ``PATCH`` a review sends (spec §6.2).

A proposal is accepted as is, edited, dismissed or left for later. Accepted as is, it goes in
``acceptProposals`` and keeps ``source: accepted-proposal``. Edited, the person's value is sent
as an ordinary field (tags keep the document's current ones) and the original is dismissed,
so the record shows the person, not the model, chose it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

Action = Literal["accept", "dismiss", "leave"]


@dataclass(frozen=True)
class Proposal:
    item: str  # what the API names it: "key=value", "period" or "author"
    kind: Literal["tag", "period", "author"]
    value: str  # shown and edited: "key=value", "start..end" or the author


def proposals_of(document: Mapping[str, Any]) -> list[Proposal]:
    found = [
        Proposal(f"{t['key']}={t['value']}", "tag", f"{t['key']}={t['value']}")
        for t in document.get("tagProposals") or []
    ]
    if period := document.get("periodProposal"):
        found.append(Proposal("period", "period", f"{period['start']}..{period['end']}"))
    if author := document.get("authorProposal"):
        found.append(Proposal("author", "author", author))
    return found


def _period(value: str) -> tuple[str, str]:
    start, sep, end = value.partition("..")
    try:
        if not sep:
            raise ValueError
        return date.fromisoformat(start.strip()).isoformat(), date.fromisoformat(end.strip()).isoformat()
    except ValueError:
        raise ValueError("a period is written YYYY-MM-DD..YYYY-MM-DD") from None


def _tag(value: str) -> dict[str, str]:
    key, sep, val = value.partition("=")
    if not sep or not key.strip() or not val.strip():
        raise ValueError("a tag is written key=value")
    return {"key": key.strip(), "value": val.strip()}


def review_request(document: Mapping[str, Any], decisions: Mapping[str, tuple[Action, str]]) -> dict[str, Any] | None:
    """The ``PATCH`` body for ``decisions`` (item → action, value as shown), or ``None`` if nothing to send."""
    accept: list[str] = []
    dismiss: list[str] = []
    fields: dict[str, Any] = {}
    edited_tags: list[dict[str, str]] = []
    for proposal in proposals_of(document):
        action, value = decisions.get(proposal.item, ("leave", proposal.value))
        value = value.strip()
        if action == "dismiss":
            dismiss.append(proposal.item)
        elif action == "accept" and value == proposal.value:
            accept.append(proposal.item)
        elif action == "accept":
            dismiss.append(proposal.item)
            if proposal.kind == "tag":
                edited_tags.append(_tag(value))
            elif proposal.kind == "period":
                fields["periodStart"], fields["periodEnd"] = _period(value)
            else:
                fields["author"] = value or None
    if edited_tags:
        current = [{"key": t["key"], "value": t["value"]} for t in document.get("tags") or []]
        fields["tags"] = current + edited_tags
    if not (accept or dismiss or fields):
        return None
    return {**fields, "acceptProposals": accept, "dismissProposals": dismiss}


def accept_all(document: Mapping[str, Any]) -> list[str]:
    return [p.item for p in proposals_of(document)]


def summary(document: Mapping[str, Any]) -> str:
    return ", ".join(p.value if p.kind == "tag" else f"{p.kind} {p.value}" for p in proposals_of(document))
