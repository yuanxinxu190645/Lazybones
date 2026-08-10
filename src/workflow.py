"""Pure workflow helpers used by the UI and regression tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


SUPPORTED_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".doc"}


def normalize_pending_files(
        candidates: Iterable[object],
        existing: Iterable[object] = ()) -> list[str]:
    """Return existing, supported, case-insensitively unique documents."""
    known = {
        os.path.normcase(os.path.abspath(str(path)))
        for path in existing
    }
    result = []
    for candidate in candidates:
        path = os.path.abspath(str(candidate))
        key = os.path.normcase(path)
        if key in known:
            continue
        file_path = Path(path)
        if (not file_path.is_file() or
                file_path.suffix.lower() not in SUPPORTED_DOCUMENT_SUFFIXES):
            continue
        known.add(key)
        result.append(path)
    return result


def can_auto_continue_pairs(raw_pairs: dict, orphan_sis: list,
                            enabled: bool) -> bool:
    """Only clean, non-empty matches may bypass the correction dialog."""
    return bool(enabled and raw_pairs and not orphan_sis)


def next_item_index(current_index: int, remaining_count: int) -> int:
    """Keep position after removing an item, clamped to the new last row."""
    if remaining_count <= 0:
        return 0
    return max(0, min(int(current_index), remaining_count - 1))
