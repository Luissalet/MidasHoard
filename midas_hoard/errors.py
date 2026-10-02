"""One error type for every expected failure, so the API, the agent tools and the UI report it the same way."""

from __future__ import annotations

from typing import Any

from .hoard_link.agentkit import AppError


class MidasError(AppError):
    """An expected, explainable failure: a stable ``code``, a human ``message`` and an actionable ``hint``.

    A Hoard Link ``AppError``: ``status`` is the HTTP status the REST layer uses; ``details`` carries structured extras (for
    example the per-field issues of an invalid strategy spec) and ``to_dict()`` is the JSON body.
    """

    STATUS = {
        **AppError.STATUS,
        "provider_unavailable": 502,
        "symbol_not_found": 404,
        "rate_limited": 429,
        "not_found": 404,
        "no_data": 404,
        "confirm_required": 400,
    }

    def __init__(self, code: str, message: str, hint: str = "", *, status: int | None = None, **details: Any):
        super().__init__(code, message, hint=hint, status=status, details=details)
