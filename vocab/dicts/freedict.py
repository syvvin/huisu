"""Free Dictionary API provider (dictionaryapi.dev)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from . import Provider, register

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ENDPOINT = "https://api.dictionaryapi.dev/api/v2/entries/en/"


class FreeDictionaryProvider(Provider):
    id = "freedict"
    name = "Free Dictionary"
    note = "Wiktionary 英英释义、例句、同义词与词源（需要联网）"
    kind = "online"
    gives = ("英英释义", "例句", "同义词", "词源")
    sections = (
        ("phonetic", "音标"),
        ("definitions_en", "英英释义"),
        ("examples", "例句"),
        ("synonyms", "同义词"),
        ("etymology", "词源"),
    )
    timeout = 7

    def fetch(self, lemma: str) -> dict[str, Any]:
        url = ENDPOINT + urllib.parse.quote(lemma)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise
        return self._parse(data)

    def _parse(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, list) or not data:
            return {}
        entry = data[0]
        out: dict[str, Any] = {}

        phonetic = entry.get("phonetic") or ""
        if not phonetic:
            for item in entry.get("phonetics") or []:
                if item.get("text"):
                    phonetic = item["text"]
                    break
        if phonetic:
            out["phonetic"] = {"default": phonetic if phonetic.startswith("/")
                               else f"/{phonetic}/"}

        definitions, examples, synonyms = [], [], []
        for meaning in entry.get("meanings") or []:
            pos = meaning.get("partOfSpeech") or ""
            for item in meaning.get("definitions") or []:
                text = (item.get("definition") or "").strip()
                if text:
                    definitions.append({"pos": pos, "text": text})
                if item.get("example"):
                    examples.append({"en": item["example"].strip(), "zh": "",
                                     "source": "Wiktionary"})
            for word in meaning.get("synonyms") or []:
                synonyms.append({"word": word, "brief": ""})
        if definitions:
            out["definitions_en"] = definitions[:8]
        if examples:
            out["examples"] = examples[:4]
        if synonyms:
            out["synonyms"] = synonyms
        if entry.get("origin"):
            out["etymology"] = entry["origin"]
        return out


register(FreeDictionaryProvider())
