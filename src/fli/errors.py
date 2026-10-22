"""API errors: ``{"error": {"code", "message"}}`` (spec §6, CLAUDE.md conventions)."""

from __future__ import annotations


class ApiError(Exception):
    """An error with a stable machine-readable ``code`` and an HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def library_not_found(library_id: str) -> ApiError:
    return not_found("library_not_found", f"library {library_id} does not exist")


def document_not_found(document_id: str) -> ApiError:
    return not_found("document_not_found", f"document {document_id} does not exist")
