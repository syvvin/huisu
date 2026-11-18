"""Offline ECDICT provider."""
from __future__ import annotations

import re
from typing import Any

from .. import ecdict
from . import Provider, register

POS_RE = re.compile(r"^([a-z]{1,6}\.)\s*(.*)$")


class EcdictProvider(Provider):
    id = "ecdict"
    name = "本地词库"
    note = "ECDICT 离线词典，77 万词条；音标、中文释义、词形、考纲与词频"
    kind = "local"
    gives = ("音标", "中文释义", "英文释义", "词形", "考纲标签", "词频")
    sections = (
        ("phonetic", "音标"),
        ("pos_groups", "中文释义"),
        ("definitions_en", "英文释义"),
        ("forms", "词形"),
        ("tags", "标签 / 考试"),
        ("rank", "词频"),
    )

    def available(self) -> bool:
        return ecdict.available()

    def fetch(self, lemma: str) -> dict[str, Any]:
        entry = ecdict.entry(lemma)
        if not entry:
            return {}
        definitions = []
        for line in entry.get("definitions_en", []):
            m = POS_RE.match(line)
            definitions.append({"pos": m.group(1) if m else "", "text": m.group(2) if m else line})
        phonetic = entry.get("phonetic") or ""
        return {
            "phonetic": {"default": phonetic} if phonetic else {},
            "pos_groups": entry.get("pos_groups", []),
            "definitions_en": definitions,
            "forms": entry.get("forms", []),
            "tags": entry.get("tags", []),
            "rank": entry.get("rank", {}),
            "collins": entry.get("collins", 0),
            "oxford": entry.get("oxford", False),
        }


register(EcdictProvider())
