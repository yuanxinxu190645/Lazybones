"""Small, UI-independent helpers for paginated data views."""

from __future__ import annotations

import math
from typing import Sequence, TypeVar


T = TypeVar("T")
PAGE_SIZE_OPTIONS = (50, 100, 200, 500)
DEFAULT_PAGE_SIZE = 100


def normalize_page_size(value, default: int = DEFAULT_PAGE_SIZE) -> int:
    """Return a supported page size while accepting old/string settings."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    return size if size in PAGE_SIZE_OPTIONS else default


def paginate_rows(rows: Sequence[T], page: int, page_size: int):
    """Return ``(page_rows, safe_page, total_pages)`` for any sequence."""
    size = normalize_page_size(page_size)
    total_pages = max(1, math.ceil(len(rows) / size))
    try:
        safe_page = int(page)
    except (TypeError, ValueError):
        safe_page = 1
    safe_page = min(total_pages, max(1, safe_page))
    start = (safe_page - 1) * size
    return list(rows[start:start + size]), safe_page, total_pages
