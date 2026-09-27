"""Tag validation and normalization rules."""

from __future__ import annotations

from typing import Iterable

MAX_TAG_LENGTH = 50
MAX_TAGS_PER_DOCUMENT = 20


def normalize_tag(value: str) -> str:
    """Collapse and trim whitespace in a tag."""
    return " ".join(value.split())


def tag_key(name: str) -> str:
    """Case-insensitive sort key for a tag name."""
    return name.casefold()


def normalize_tags(values: Iterable[str]) -> list[str]:
    """Normalize and deduplicate tags, preserving order and first spelling.

    - Trim and collapse whitespace on each tag
    - Drop empty strings
    - Remove duplicates (case-insensitive), keeping first occurrence
    - Preserve input order
    """
    seen_keys: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_tag(value)
        if not normalized:
            continue
        key = tag_key(normalized)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(normalized)
    return result
