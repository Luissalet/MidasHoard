"""One error type for every expected failure, so the API, the agent tools and the UI report it the same way."""

from __future__ import annotations

from typing import Any


class MidasError(Exception):
    """An expected, explainable failure: a stable ``code``, a human ``message`` and an actionable ``hint``.

    ``status`` is the HTTP status the REST layer uses; ``details`` carries structured extras (for
    example the per-field issues of an invalid strategy spec).
    """

    STATUS = {
        "provider_unavailable": 502,
        "symbol_not_found": 404,
        "rate_limited": 429,
        "not_found": 404,
        "no_data": 404,
        "confirm_required": 400,
    }

    def __init__(self, code: str, message: str, hint: str = "", *, status: int | None = None, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.status = status or self.STATUS.get(code, 400)
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.hint:
            body["hint"] = self.hint
        body.update(self.details)
        return body
