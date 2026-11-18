"""Youdao dictionary provider (free public JSON endpoint, results are cached)."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

from . import Provider, register, strip_tags

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ENDPOINT = "https://dict.youdao.com/jsonapi?q="
POS_RE = re.compile(r"^([a-z]{1,6}\.)\s*(.*)$", re.S)
TAG_MAP = {"CET4": "四级", "CET6": "六级", "考研": "考研", "TOEFL": "托福",
           "IELTS": "雅思", "GRE": "GRE", "GMAT": "GMAT"}


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text_i(node: Any) -> str:
    """Extract dict entries of the {"l": {"i": "..."}} / {"l":{"i":["..."]}} shape."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_text_i(item) for item in node if item)
    if isinstance(node, dict):
        if "l" in node:
            return _text_i(node["l"])
        if "i" in node:
            return _text_i(node["i"])
        return ""
    return ""


def _add_group(groups: list[dict], pos: str, means: list[str]) -> None:
    if not means:
        return
    for group in groups:
        if group["pos"] == pos:
            for mean in means:
                if mean and mean not in group["means"]:
                    group["means"].append(mean)
            return
    groups.append({"pos": pos, "means": [m for m in means if m]})


def _parse_chinese(lines: list[str]) -> list[dict]:
    groups: list[dict] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        m = POS_RE.match(line)
        pos, body = (m.group(1), m.group(2)) if m else ("", line)
        means = [piece.strip() for piece in re.split(r"[;；]", body) if piece.strip()]
        _add_group(groups, pos, means)
    return groups


class YoudaoProvider(Provider):
    id = "youdao"
    name = "有道词典"
    note = "词源故事、同根词、近义词、柯林斯与牛津例句、四六级考研真题"
    kind = "online"
    gives = ("英美音标", "中文释义", "词源", "同根词", "近义词", "例句", "真题")
    sections = (
        ("phonetic", "音标"),
        ("pos_groups", "中文释义"),
        ("definitions_en", "英文释义"),
        ("forms", "词形"),
        ("tags", "考试 / 标签"),
        ("etymology", "词源"),
        ("roots", "同根词"),
        ("synonyms", "近义词"),
        ("examples", "例句"),
        ("exam", "真题"),
    )
    timeout = 8

    def fetch(self, lemma: str) -> dict[str, Any]:
        url = ENDPOINT + urllib.parse.quote(lemma)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://dict.youdao.com/",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        data = json.loads(raw)
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        ec = data.get("ec") or {}
        word = _first(ec.get("word")) or {}

        phonetic = {}
        if word.get("ukphone"):
            phonetic["uk"] = f"/{word['ukphone']}/"
        if word.get("usphone"):
            phonetic["us"] = f"/{word['usphone']}/"
        if phonetic:
            out["phonetic"] = phonetic

        lines: list[str] = []
        for item in word.get("trs") or []:
            for tr in (item.get("tr") or []):
                text = _text_i(tr)
                if text:
                    lines.append(text)
        groups = _parse_chinese(lines)
        if groups:
            out["pos_groups"] = groups

        forms = []
        for item in word.get("wfs") or []:
            wf = item.get("wf") or {}
            if wf.get("name") and wf.get("value"):
                forms.append({"name": wf["name"], "value": wf["value"]})
        if forms:
            out["forms"] = forms

        tags = []
        for tag in ec.get("exam_type") or []:
            name = TAG_MAP.get(tag, tag)
            if name not in tags:
                tags.append(name)
        individual = data.get("individual") or {}
        if individual.get("level") and individual["level"] not in tags:
            tags.append(individual["level"])
        if tags:
            out["tags"] = tags

        etym = data.get("etym") or {}
        zh = ((etym.get("etyms") or {}).get("zh") or [])
        for item in zh:
            value = (item.get("value") or "").strip()
            if value:
                out["etymology"] = value
                break

        roots = []
        rel = data.get("rel_word") or {}
        for item in rel.get("rels") or []:
            r = item.get("rel") or {}
            for entry in r.get("words") or []:
                if entry.get("word"):
                    roots.append({"word": entry["word"],
                                  "brief": (entry.get("tran") or "").strip(),
                                  "pos": r.get("pos", "")})
        if roots:
            out["roots"] = roots

        synonyms = []
        syno = data.get("syno") or {}
        for item in syno.get("synos") or []:
            s = item.get("syno") or {}
            for entry in (s.get("ws") or []):
                if isinstance(entry, str):
                    value = entry
                else:
                    value = (entry or {}).get("w") or ""
                if value:
                    synonyms.append({"word": value, "brief": s.get("tran", "")})
        if synonyms:
            out["synonyms"] = synonyms

        examples = []
        expand = data.get("expand_ec") or {}
        expand_words = expand.get("word") or []
        if isinstance(expand_words, dict):
            expand_words = [expand_words]
        for item in expand_words:
            if not isinstance(item, dict):
                continue
            for trans in item.get("transList") or []:
                content = trans.get("content") or {}
                for sent in content.get("sents") or []:
                    if sent.get("sentOrig"):
                        examples.append({
                            "en": strip_tags(sent["sentOrig"]),
                            "zh": strip_tags(sent.get("sentTrans", "")),
                            "source": sent.get("source") or "有道",
                        })
        if not examples:
            blng = data.get("blng_sents_part") or {}
            for pair in (blng.get("sentence-pair") or [])[:4]:
                en = strip_tags(pair.get("sentence") or pair.get("sentence-eng") or "")
                if en:
                    examples.append({"en": en,
                                     "zh": strip_tags(pair.get("sentence-translation", "")),
                                     "source": "有道例句"})
        if examples:
            out["examples"] = examples[:5]

        exam = []
        for sent in (individual.get("pastExamSents") or [])[:3]:
            if sent.get("en"):
                exam.append({"en": strip_tags(sent["en"]),
                             "zh": strip_tags(sent.get("zh", "")),
                             "source": sent.get("source") or "真题"})
        if exam:
            out["exam"] = exam

        definitions = []
        ee = data.get("ee") or {}
        for item in (ee.get("word") or {}).get("trs") or []:
            pos = item.get("pos") or ""
            for tr in item.get("tr") or []:
                text = _text_i(tr.get("l")) if isinstance(tr, dict) else ""
                if text:
                    definitions.append({"pos": pos, "text": text})
        if not definitions:
            collins = data.get("collins") or {}
            for entry in (collins.get("collins_entries") or []):
                for block in (entry.get("entries") or {}).get("entry") or []:
                    for tran in block.get("tran_entry") or []:
                        pos = ((tran.get("pos_entry") or {}).get("pos") or "")
                        text = strip_tags(tran.get("tran", ""))
                        if text:
                            definitions.append({"pos": pos, "text": text})
        if definitions:
            out["definitions_en"] = definitions[:6]

        collins = data.get("collins") or {}
        for entry in (collins.get("collins_entries") or []):
            if entry.get("star"):
                try:
                    out["collins"] = int(entry["star"])
                except (TypeError, ValueError):
                    pass
                break

        return out


register(YoudaoProvider())
