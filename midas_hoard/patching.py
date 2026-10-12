"""Merge-patch for JSON documents: only the provided fields change, so a concurrent edit to other fields survives."""

from __future__ import annotations

from typing import Any


def merge_patch(current: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Nested objects merge, arrays and scalars replace, ``None`` removes a key. Returns a new dict."""
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict):
            merged[key] = merge_patch(merged.get(key), value)
        else:
            merged[key] = value
    return merged
