"""Dictionary engine registry, provider cache and result merging."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import db

CACHE_TTL = {"ecdict": 60 * 60 * 24 * 30, "cefr": 60 * 60 * 24 * 30,
             "youdao": 60 * 60 * 24 * 30, "freedict": 60 * 60 * 24 * 30}
# Bump when a provider's parsing/analysis changes so stale results are ignored.
CACHE_VERSION = "v3"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dict")


def strip_tags(value: str) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub("", value)).strip()


# --------------------------------------------------------------------------- #
# provider registry
# --------------------------------------------------------------------------- #
class Provider:
    id = ""
    name = ""
    note = ""
    kind = "online"          # local | online | analysis
    gives: tuple[str, ...] = ()
    sections: tuple[tuple[str, str], ...] = ()

    def fetch(self, lemma: str) -> dict[str, Any]:
        raise NotImplementedError

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "note": self.note,
                "kind": self.kind, "gives": list(self.gives),
                "sections": [{"key": key, "label": label} for key, label in self.sections],
                "available": self.available()}


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _REGISTRY[provider.id] = provider


def providers() -> list[Provider]:
    return list(_REGISTRY.values())


def describe_all() -> list[dict[str, Any]]:
    return [p.describe() for p in _REGISTRY.values()]


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
def _cache_key(lemma: str) -> str:
    return f"{CACHE_VERSION}:{lemma}"


def _cache_get(provider: str, lemma: str) -> tuple[dict[str, Any] | None, str, bool]:
    row = db.row("SELECT payload, fetched_at, ok FROM dict_cache WHERE provider=? AND lemma=?",
                 (provider, _cache_key(lemma)))
    if row is None:
        return None, "", False
    age = time.time() - (db.parse_iso(row["fetched_at"]).timestamp()
                         if db.parse_iso(row["fetched_at"]) else 0)
    ttl = CACHE_TTL.get(provider, 86400)
    fresh = row["ok"] and age <= ttl
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    return (payload if fresh else None), row["fetched_at"], bool(row["ok"])


def _cache_put(provider: str, lemma: str, payload: dict[str, Any], ok: bool) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO dict_cache(provider,lemma,payload,fetched_at,ok) VALUES(?,?,?,?,?) "
            "ON CONFLICT(provider,lemma) DO UPDATE SET payload=excluded.payload,"
            "fetched_at=excluded.fetched_at,ok=excluded.ok",
            (provider, _cache_key(lemma), json.dumps(payload, ensure_ascii=False),
             db.now_iso(), 1 if ok else 0))


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #
EMPTY = {
    "phonetic": {}, "pos_groups": [], "definitions_en": [], "forms": [],
    "etymology": "", "roots": [], "synonyms": [], "examples": [], "exam": [],
    "tags": [], "rank": {}, "collins": 0, "oxford": False, "cefr": None,
    "notes": [],
}

# Display order also decides which provider supplies phonetics and the primary
# Chinese sense for a given part of speech.
PROVIDER_PRIORITY = {"youdao": 0, "ecdict": 1, "freedict": 2, "cefr": 3}
POS_ALIASES = {"a.": "adj.", "ad.": "adv.", "s.": "adj.", "r.": "adv.",
               "adj": "adj.", "adv": "adv.", "n": "n.", "v": "v."}


def normalize_pos(pos: str) -> str:
    value = (pos or "").strip().lower()
    return POS_ALIASES.get(value, value)


def _dedupe(items: list[dict], key: str) -> list[dict]:
    seen, out = set(), []
    for item in items:
        value = (item.get(key) or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(item)
    return out


def merge(parts: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    result = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
              for k, v in EMPTY.items()}
    phonetic: dict[str, str] = {}
    pos_groups: list[dict[str, Any]] = []
    pos_seen: set[str] = set()
    ordered = sorted(parts, key=lambda item: PROVIDER_PRIORITY.get(item[0], 9))
    for _pid, payload in ordered:
        if not payload:
            continue
        for key, value in (payload.get("phonetic") or {}).items():
            if value and not phonetic.get(key):
                phonetic[key] = value
        for group in payload.get("pos_groups") or []:
            means = [m for m in (group.get("means") or []) if m]
            if not means:
                continue
            pos = normalize_pos(group.get("pos", ""))
            # The highest-priority provider owns the common senses for a POS;
            # later providers only fill gaps so glosses do not pile up.
            if pos in pos_seen:
                continue
            pos_seen.add(pos)
            pos_groups.append({"pos": pos, "means": means[:6]})
        result["definitions_en"].extend(payload.get("definitions_en") or [])
        result["forms"].extend(payload.get("forms") or [])
        result["roots"].extend(payload.get("roots") or [])
        result["synonyms"].extend(payload.get("synonyms") or [])
        result["examples"].extend(payload.get("examples") or [])
        result["exam"].extend(payload.get("exam") or [])
        for tag in payload.get("tags") or []:
            if tag not in result["tags"]:
                result["tags"].append(tag)
        rank = payload.get("rank") or {}
        for key, value in rank.items():
            if value and not result["rank"].get(key):
                result["rank"][key] = value
        result["collins"] = max(result["collins"], payload.get("collins") or 0)
        result["oxford"] = result["oxford"] or bool(payload.get("oxford"))
        if payload.get("etymology") and not result["etymology"]:
            result["etymology"] = payload["etymology"]
        if payload.get("cefr") and not result["cefr"]:
            result["cefr"] = payload["cefr"]
        for note in payload.get("notes") or []:
            if note not in result["notes"]:
                result["notes"].append(note)
    result["phonetic"] = phonetic
    result["pos_groups"] = pos_groups
    result["definitions_en"] = _dedupe(result["definitions_en"], "text")[:8]
    result["forms"] = _dedupe(result["forms"], "value")[:10]
    result["roots"] = _dedupe(result["roots"], "word")[:20]
    result["synonyms"] = _dedupe(result["synonyms"], "word")[:14]
    result["examples"] = _dedupe(result["examples"], "en")[:8]
    result["exam"] = _dedupe(result["exam"], "en")[:6]
    return result


# --------------------------------------------------------------------------- #
# public lookup
# --------------------------------------------------------------------------- #
def lookup(lemma: str, enabled: list[str] | None = None) -> dict[str, Any]:
    lemma = (lemma or "").strip().lower()
    if not lemma or len(lemma) > 48 or not re.fullmatch(r"[a-z][a-z'’\- ]*", lemma):
        return {"lemma": lemma, "error": "无效的单词", "sources": []}
    active = [p for p in providers() if (enabled is None or p.id in enabled) and p.available()]
    results: list[tuple[str, dict[str, Any]]] = []
    sources: list[dict[str, Any]] = []

    def run_one(provider: Provider):
        started = time.perf_counter()
        cached, fetched_at, _ok = _cache_get(provider.id, lemma)
        if cached is not None:
            return provider, cached, {"id": provider.id, "name": provider.name,
                                      "ok": True, "cached": True,
                                      "ms": int((time.perf_counter() - started) * 1000),
                                      "fetched_at": fetched_at}
        try:
            payload = provider.fetch(lemma) or {}
            _cache_put(provider.id, lemma, payload, True)
            return provider, payload, {"id": provider.id, "name": provider.name,
                                       "ok": True, "cached": False,
                                       "ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:  # network / parse failures must not break the page
            message = str(exc) or exc.__class__.__name__
            _cache_put(provider.id, lemma, {"error": message}, False)
            return provider, {}, {"id": provider.id, "name": provider.name, "ok": False,
                                  "error": message,
                                  "ms": int((time.perf_counter() - started) * 1000)}

    if active:
        for provider, payload, source in _executor.map(run_one, active):
            results.append((provider.id, payload))
            sources.append(source)
            if not payload and not source.get("ok"):
                # fall back to a stale cached copy if we have one
                stale, fetched_at, ok = _cache_get_for_stale(provider.id, lemma)
                if ok and stale:
                    results[-1] = (provider.id, stale)
                    sources[-1]["ok"] = True
                    sources[-1]["stale"] = True
                    sources[-1]["fetched_at"] = fetched_at

    merged = merge(results)
    merged["lemma"] = lemma
    merged["sources"] = sources
    merged["providers"] = [p.id for p in active]
    source_map = {s["id"]: s for s in sources}
    provider_results = []
    for pid, payload in sorted(results, key=lambda item: PROVIDER_PRIORITY.get(item[0], 9)):
        provider = next((p for p in active if p.id == pid), None)
        if provider is None:
            continue
        provider_results.append({
            "id": provider.id,
            "name": provider.name,
            "kind": provider.kind,
            "note": provider.note,
            "sections": [{"key": key, "label": label} for key, label in provider.sections],
            "data": payload,
            "source": source_map.get(provider.id, {}),
        })
    merged["provider_results"] = provider_results
    merged["links"] = {
        "bing": "https://cn.bing.com/dict/search?q=" + urllib.parse.quote(lemma),
        "eudic": "https://dict.eudic.net/dicts/en/" + urllib.parse.quote(lemma),
        "youdao": "https://dict.youdao.com/result?word=" + urllib.parse.quote(lemma) + "&lang=en",
    }
    if not merged["pos_groups"] and not merged["definitions_en"] and not merged["cefr"]:
        merged["empty"] = True
    return merged


def _cache_get_for_stale(provider: str, lemma: str):
    row = db.row("SELECT payload, fetched_at, ok FROM dict_cache WHERE provider=? AND lemma=?",
                 (provider, _cache_key(lemma)))
    if row is None or not row["ok"]:
        return None, "", False
    try:
        return json.loads(row["payload"] or "{}"), row["fetched_at"], True
    except ValueError:
        return None, "", False


def cache_stats() -> dict[str, Any]:
    rows = db.rows("SELECT provider, COUNT(*) AS n FROM dict_cache WHERE ok=1 GROUP BY provider")
    return {r["provider"]: r["n"] for r in rows}


def clear_cache() -> int:
    with db.tx() as conn:
        cur = conn.execute("DELETE FROM dict_cache")
        return cur.rowcount


# Provider modules register themselves on import. Imported at the bottom to
# avoid circular imports with the registry above.
from . import local as _local          # noqa: E402,F401
from . import youdao as _youdao        # noqa: E402,F401
from . import freedict as _freedict    # noqa: E402,F401
from . import cefr as _cefr            # noqa: E402,F401
