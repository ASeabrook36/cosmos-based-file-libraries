"""Deterministic, section-aware chunking (spec §7.4).

Documents: sentence-aware and section-aware chunks targeting 500 tokens with a 50-token
overlap; headings are carried into each chunk of their section, and chunks never span two
sections, so a chunk's heading and location (page, slide or sheet) are always its own.
Email: a message of at most 700 tokens is one chunk; longer messages use 500/50.

Chunk boundaries depend on the tokenizer. ``chunking-v1.0`` is defined with the
``cl100k_base`` encoding that text-embedding-3-large uses. Any other tokenizer produces a
visibly different ``chunkingVersion`` (``chunking-v1.0+<tokenizer>``), so chunks made with a
stand-in can never be mistaken for, or mixed with, contract-version chunks. Any change to the
algorithm must change the version.

Overlap is whole trailing sentences totalling at most 50 tokens: sentence-awareness wins over
an exact 50. A single sentence longer than the target is split on token boundaries, the only
case in which a sentence is cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .enums import CHUNKING_VERSION
from .extraction import Section

TARGET_TOKENS = 500
OVERLAP_TOKENS = 50
EMAIL_SINGLE_CHUNK_MAX = 700
CONTRACT_TOKENIZER = "cl100k_base"

DocumentKind = Literal["document", "email"]


class Tokenizer(Protocol):
    name: str

    def encode(self, text: str) -> list[int]: ...

    def decode(self, tokens: list[int]) -> str: ...


class TiktokenTokenizer:
    name = CONTRACT_TOKENIZER

    def __init__(self) -> None:
        import tiktoken

        self._enc = tiktoken.get_encoding(CONTRACT_TOKENIZER)

    def encode(self, text: str) -> list[int]:
        return self._enc.encode(text, disallowed_special=())

    def decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)


class ApproxTokenizer:
    """Stand-in for environments that cannot load cl100k_base: word and punctuation pieces.

    Deterministic, reversible, and deliberately *not* the contract tokenizer.
    """

    name = "approx-wordpiece"
    _PIECE = re.compile(r"\s+|\w+|[^\w\s]")

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}
        self._rev: list[str] = []

    def _id(self, piece: str) -> int:
        if piece not in self._vocab:
            self._vocab[piece] = len(self._rev)
            self._rev.append(piece)
        return self._vocab[piece]

    def encode(self, text: str) -> list[int]:
        # whitespace attaches to the following piece, as in BPE vocabularies
        out: list[int] = []
        pending = ""
        for piece in self._PIECE.findall(text):
            if piece.isspace():
                pending += piece
                continue
            out.append(self._id(pending + piece))
            pending = ""
        if pending:
            out.append(self._id(pending))
        return out

    def decode(self, tokens: list[int]) -> str:
        return "".join(self._rev[t] for t in tokens)


def default_tokenizer() -> Tokenizer:
    try:
        return TiktokenTokenizer()
    except Exception:  # noqa: BLE001 — encoding file unavailable (e.g. network-restricted host)
        return ApproxTokenizer()


def chunking_version_for(tokenizer: Tokenizer) -> str:
    return CHUNKING_VERSION if tokenizer.name == CONTRACT_TOKENIZER else f"{CHUNKING_VERSION}+{tokenizer.name}"


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int
    heading: str | None
    location: dict[str, Any] | None = None


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])|\n{2,}|\n(?=\s*[-*•\d]+[.)]?\s)")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text)]
    sentences: list[str] = []
    for part in parts:
        if not part:
            continue
        sentences.extend(line.strip() for line in part.split("\n") if line.strip())
    return sentences


Piece = tuple[str, "str | None", int, "dict[str, Any] | None"]


class Chunker:
    def __init__(self, tokenizer: Tokenizer, target: int = TARGET_TOKENS, overlap: int = OVERLAP_TOKENS) -> None:
        self.tokenizer = tokenizer
        self.target = target
        self.overlap = overlap
        self.version = chunking_version_for(tokenizer)

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def chunk(self, sections: list[Section], kind: DocumentKind = "document") -> list[Chunk]:
        if kind == "email":
            text = "\n\n".join(s.text for s in sections if s.text.strip()).strip()
            if not text:
                return []
            if self.count(text) <= EMAIL_SINGLE_CHUNK_MAX:
                return [Chunk(0, text, self.count(text), None)]
            return self._index(self._window([Section(None, text)]))
        if kind != "document":
            raise ValueError(f"unknown document kind {kind!r}")
        return self._index(self._window(sections))

    @staticmethod
    def _index(pieces: list[Piece]) -> list[Chunk]:
        return [Chunk(i, text, tokens, heading, location) for i, (text, heading, tokens, location) in enumerate(pieces)]

    def _window(self, sections: list[Section]) -> list[Piece]:
        out: list[Piece] = []
        for section in sections:
            sentences = [s for s in split_sentences(section.text) if s]
            if not sentences:
                continue
            prefix = f"{section.heading}\n" if section.heading else ""
            prefix_tokens = self.count(prefix) if prefix else 0
            budget = max(1, self.target - prefix_tokens)
            units: list[tuple[str, int]] = []
            for sentence in sentences:
                n = self.count(sentence)
                if n <= budget:
                    units.append((sentence, n))
                else:  # the only mid-sentence cut: a sentence longer than the whole budget
                    tokens = self.tokenizer.encode(sentence)
                    for start in range(0, len(tokens), budget):
                        piece = self.tokenizer.decode(tokens[start : start + budget]).strip()
                        if piece:
                            units.append((piece, self.count(piece)))
            current: list[tuple[str, int]] = []
            current_tokens = 0
            for unit, n in units:
                if current and current_tokens + n + 1 > budget:
                    body = " ".join(u for u, _ in current)
                    out.append((prefix + body, section.heading, self.count(prefix + body), section.location))
                    carry: list[tuple[str, int]] = []
                    carried = 0
                    for prev, pn in reversed(current):
                        if carried + pn > self.overlap:
                            break
                        carry.insert(0, (prev, pn))
                        carried += pn
                    if carried + n + 1 > budget:
                        carry, carried = [], 0
                    current, current_tokens = list(carry), carried
                current.append((unit, n))
                current_tokens += n + 1
            if current:
                body = " ".join(u for u, _ in current)
                out.append((prefix + body, section.heading, self.count(prefix + body), section.location))
        return out
