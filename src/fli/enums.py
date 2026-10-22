"""Shared constants for extraction and chunking."""

from __future__ import annotations

from enum import StrEnum

# Recorded on every chunk. Any change to the chunking algorithm or its parameters must
# change this value, so chunks made by different algorithms are never mixed unknowingly.
CHUNKING_VERSION = "chunking-v1.0"


class ExtractionMethod(StrEnum):
    """How a document's text was obtained; recorded on the document for diagnosis."""

    NATIVE_TEXT = "native_text"
    DOCUMENT_INTELLIGENCE_LAYOUT = "document_intelligence_layout"
    DOCUMENT_INTELLIGENCE_READ = "document_intelligence_read"
    UNSUPPORTED = "unsupported"
