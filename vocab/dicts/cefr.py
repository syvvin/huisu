"""CEFR word-level provider backed by the bundled Words CEFR Dataset."""
from __future__ import annotations

from typing import Any

from .. import cefr
from . import Provider, register


class CefrProvider(Provider):
    id = "cefr"
    name = "CEFR 词表"
    note = "CEFR-J / Words CEFR Dataset：A1–C2 词级、词性级别与词频"
    kind = "local"
    gives = ("CEFR 级别", "词性级别", "词频")
    sections = (
        ("level", "CEFR 级别"),
        ("pos_levels", "词性级别"),
        ("frequency", "词频"),
    )

    def available(self) -> bool:
        return cefr.available()

    def fetch(self, lemma: str) -> dict[str, Any]:
        item = cefr.entry(lemma)
        if not item:
            return {}
        return {"cefr": item}


register(CefrProvider())
