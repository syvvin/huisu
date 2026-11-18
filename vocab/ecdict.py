"""Offline ECDICT store: fast local lookup, lemmatisation and word forms."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import config

_local = threading.local()
_meta_lock = threading.Lock()
_meta_cache: dict[str, Any] | None = None

TAG_NAMES = {
    "zk": "中考", "gk": "高考", "cet4": "四级", "cet6": "六级",
    "ky": "考研", "toefl": "托福", "ielts": "雅思", "gre": "GRE",
}

FORM_NAMES = {
    "p": "过去式", "d": "过去分词", "i": "现在分词", "3": "第三人称单数",
    "r": "比较级", "t": "最高级", "s": "复数",
}


def available() -> bool:
    return config.DICT_DB_PATH.exists()


def meta() -> dict[str, Any]:
    global _meta_cache
    with _meta_lock:
        if _meta_cache is None:
            try:
                _meta_cache = json.loads(config.DICT_META_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _meta_cache = {}
    return dict(_meta_cache or {})


def connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        if not available():
            raise RuntimeError("离线词库尚未构建，请运行 tools/build_ecdict.py")
        conn = sqlite3.connect(f"file:{config.DICT_DB_PATH.as_posix()}?mode=ro", uri=True,
                               timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def _norm_phonetic(value: str) -> str:
    if not value:
        return ""
    text = value.strip().strip("/[]")
    text = (text.replace("ә", "ə").replace("’", "'")
                .replace(":", "ː").replace("ɡ", "g").replace("'", "ˈ"))
    return f"/{text}/" if text else ""


def _split_lines(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace("\\n", "\n").split("\n") if part.strip()]


def _pos_means(lines: list[str]) -> list[dict[str, str]]:
    """Group "a. 奇特的, 罕见的" style lines into pos buckets."""
    buckets: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for line in lines:
        m = re.match(r"^([a-z]{1,5}\.)\s*(.*)$", line)
        pos, body = (m.group(1), m.group(2)) if m else ("", line)
        if pos and pos in index:
            bucket = index[pos]
        else:
            bucket = {"pos": pos, "means": []}
            buckets.append(bucket)
            if pos:
                index[pos] = bucket
        for piece in re.split(r"[;；]", body):
            piece = piece.strip()
            if piece:
                bucket["means"].append(piece)
    return [b for b in buckets if b["means"]]


def _forms(exchange: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for chunk in (exchange or "").split("/"):
        if ":" not in chunk:
            continue
        key, _, value = chunk.partition(":")
        if key in FORM_NAMES and value:
            out.append({"name": FORM_NAMES[key], "value": value})
    return out


def _tags(tag: str) -> list[str]:
    return [TAG_NAMES[t] for t in (tag or "").split() if t in TAG_NAMES]


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    tags = _tags(row["tag"])
    return {
        "lemma": row["word"],
        "phonetic": _norm_phonetic(row["phonetic"]),
        "pos_groups": _pos_means(_split_lines(row["translation"])),
        "definitions_en": _split_lines(row["definition"]),
        "forms": _forms(row["exchange"]),
        "tags": tags,
        "collins": int(row["collins"]) if row["collins"].isdigit() else 0,
        "oxford": bool(row["oxford"]),
        "rank": {
            "bnc": row["bnc"] or 0,
            "coca": row["frq"] or 0,
        },
    }


def entry(word: str) -> dict[str, Any] | None:
    if not available() or not word:
        return None
    cur = connection().execute("SELECT * FROM entries WHERE word=?", (word.lower().strip(),))
    row = cur.fetchone()
    return _row_to_entry(row) if row else None


def known(word: str) -> bool:
    return entry(word) is not None


def _exists(form: str) -> bool:
    if not available():
        return False
    row = connection().execute("SELECT 1 FROM entries WHERE word=?", (form,)).fetchone()
    return row is not None


def _suffix_candidates(form: str):
    """Cheap morphological fallbacks, only used when the lemma map misses."""
    if len(form) < 4:
        return
    if form.endswith("ies") and len(form) > 4:
        yield form[:-3] + "y"
    if form.endswith("ves"):
        yield form[:-3] + "f"
        yield form[:-3] + "fe"
    if form.endswith(("ses", "xes", "zes", "ches", "shes", "oes")):
        yield form[:-2]
    if form.endswith("s") and not form.endswith("ss"):
        yield form[:-1]
    if form.endswith("ied"):
        yield form[:-3] + "y"
    if form.endswith("ed"):
        yield form[:-2]
        yield form[:-1]
    if form.endswith("ing"):
        yield form[:-3]
        yield form[:-3] + "e"
        if len(form) > 4 and form[-4] == form[-5]:
            yield form[:-4]
    if form.endswith("ier"):
        yield form[:-3] + "y"
    if form.endswith("iest"):
        yield form[:-4] + "y"
    if form.endswith("er"):
        yield form[:-2]
    if form.endswith("est"):
        yield form[:-3]
    if form.endswith("ly"):
        yield form[:-2]
        yield form[:-2] + "le"


_lemma_cache: dict[str, str] = {}
_lemma_cache_lock = threading.Lock()


def lemma(form: str) -> str:
    form = (form or "").lower().strip()
    if not form:
        return ""
    with _lemma_cache_lock:
        cached = _lemma_cache.get(form)
    if cached:
        return cached
    result = form
    if available():
        row = connection().execute("SELECT lemma FROM lemmas WHERE form=?", (form,)).fetchone()
        if row:
            result = row["lemma"]
        elif _exists(form):
            result = form
        else:
            for cand in _suffix_candidates(form):
                if cand and _exists(cand):
                    result = cand
                    break
    with _lemma_cache_lock:
        if len(_lemma_cache) > 60000:
            _lemma_cache.clear()
        _lemma_cache[form] = result
    return result


def similar_words(root: str, limit: int = 12) -> list[dict[str, Any]]:
    """Words that contain `root`, ordered by frequency (used by the affix panel)."""
    if not available() or len(root) < 3:
        return []
    pattern = f"%{root}%"
    cur = connection().execute(
        "SELECT word, translation, phonetic, frq FROM entries "
        "WHERE word LIKE ? AND length(word) <= ? "
        "ORDER BY CASE WHEN frq=0 THEN 999999 ELSE frq END LIMIT ?",
        (pattern, len(root) + 6, max(limit * 3, 18)),
    )
    out = []
    for row in cur.fetchall():
        if row["word"] == root:
            continue
        brief = ""
        for line in _split_lines(row["translation"]):
            m = re.match(r"^[a-z]{1,5}\.\s*(.*)$", line)
            brief = (m.group(1) if m else line)
            break
        out.append({
            "word": row["word"],
            "brief": brief,
            "phonetic": _norm_phonetic(row["phonetic"]),
        })
        if len(out) >= limit:
            break
    return out


def lookup_stats() -> dict[str, Any]:
    if not available():
        return {"available": False}
    info = meta()
    try:
        total = connection().execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    except sqlite3.Error:
        total = info.get("entries", 0)
    return {"available": True, "entries": total, "size": info.get("size", 0)}
