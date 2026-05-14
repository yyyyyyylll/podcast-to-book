"""Shared presentation helpers for direction editing-value labels."""
from __future__ import annotations


def public_editing_value(value: str | None) -> str:
    """Map internal/legacy editing values to positive public wording.

    Historical outputs used 高 / 中 / 低. Public-facing artifacts now avoid
    中 / 低 and preserve relative priority as 极高 / 高.
    """
    normalized = (value or "").strip()
    if normalized == "极高":
        return "极高"
    if normalized == "高":
        return "极高"
    if normalized in {"中", "低"}:
        return "高"
    return normalized or "高"
