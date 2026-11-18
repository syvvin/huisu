"""Compact CEFR word-level lookup backed by the bundled Words CEFR Dataset.

Source: https://github.com/Maximax67/Words-CEFR-Dataset (MIT).
The bundled file is a gzip-compressed JSON map built by tools/build_cefr.py.
"""
from __future__ import annotations

import gzip
import json
import threading
from typing import Any

from . import config

_DATA_PATH = config.CEFR_PATH
_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
_lock = threading.Lock()
_data: dict[str, dict[str, Any]] | None = None


def levels() -> tuple[str, ...]:
    return _LEVELS


def available() -> bool:
    return _DATA_PATH.exists()


def _load() -> dict[str, dict[str, Any]]:
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is not None:
            return _data
        try:
            with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
            _data = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            _data = {}
        return _data


def entry(lemma: str) -> dict[str, Any] | None:
    value = (lemma or "").strip().lower()
    if not value:
        return None
    return _load().get(value)


def level(lemma: str) -> str:
    item = entry(lemma)
    return (item or {}).get("level", "")


def level_num(lemma: str) -> int:
    item = entry(lemma)
    return int((item or {}).get("level_num") or 0)


def stats() -> dict[str, Any]:
    if not available():
        return {"available": False, "words": 0, "size": 0}
    data = _load()
    return {"available": True, "words": len(data), "size": _DATA_PATH.stat().st_size,
            "levels": list(_LEVELS)}
