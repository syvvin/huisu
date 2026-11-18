"""Scanner and parser for the UTTU story archive.

The site is an Astro app. Every story chapter page embeds a JSON blob in
``<script id="chaptersData">`` that contains the chapter frontmatter plus all
of its Parts, in one locale. The same page exists under ``/en/`` and
``/chs/``; Voice IDs are shared by both locales, which is what lets us pair
English and Chinese line-by-line without guessing.

If that JSON ever moves, the fallbacks below look for any JSON script with
the expected shape before giving up with a descriptive error.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bs4 import BeautifulSoup

from . import config
from .http import FetchError, NotFound, client
from .models import Chapter, Part

log = logging.getLogger(__name__)

FALLBACK_CATEGORIES: list[tuple[str, str]] = [
    ("main", "Main Story"),
    ("event", "Event Story"),
    ("character", "Character Story"),
    ("anecdote", "Anecdotes"),
    ("other", "Others"),
    ("cn", "CN Stories"),
]

_CHAPTER_PATH_RE = re.compile(r"^/story/([^/]+)/([^/]+)/?$")
_CHAPTER_JSON_RE = re.compile(
    r'<script[^>]*id=["\']chaptersData["\'][^>]*>(.*?)</script>', re.S | re.I
)


class SiteError(RuntimeError):
    """The site could not be scanned or a chapter could not be parsed."""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def _cache_path(name: str):
    path = config.CACHE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path, ttl: float | None = None) -> Any | None:
    try:
        if not path.exists():
            return None
        if ttl is not None and time.time() - path.stat().st_mtime > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable cache %s: %s", path, exc)
        return None


def _write_json_atomic(path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def chapter_to_dict(chapter: Chapter) -> dict[str, Any]:
    return {
        "category": chapter.category,
        "slug": chapter.slug,
        "title_en": chapter.title_en,
        "title_zh": chapter.title_zh,
        "label": chapter.label,
        "subtitle": chapter.subtitle,
        "description": chapter.description,
        "recording_url": chapter.recording_url,
        "warnings": chapter.warnings,
        "source_urls": chapter.source_urls,
        "fetched_at": chapter.fetched_at,
        "parts": [
            {
                "number": part.number,
                "text_number": part.text_number,
                "title_en": part.title_en,
                "title_zh": part.title_zh,
                "content_en": part.content_en,
                "content_zh": part.content_zh,
                "word_count": part.word_count,
                "gameplay_only": part.gameplay_only,
                "video_url": part.video_url,
            }
            for part in chapter.parts
        ],
    }


def chapter_from_dict(data: dict[str, Any]) -> Chapter:
    parts = [
        Part(
            number=int(item.get("number", item.get("text_number", 0))),
            text_number=int(item.get("text_number", item.get("number", 0))),
            title_en=_clean_text(item.get("title_en", "")),
            title_zh=_clean_text(item.get("title_zh", "")),
            content_en=item.get("content_en", "") or "",
            content_zh=item.get("content_zh", "") or "",
            word_count=int(item.get("word_count", 0) or 0),
            gameplay_only=bool(item.get("gameplay_only", False)),
            video_url=item.get("video_url") or None,
        )
        for item in data.get("parts", [])
    ]
    parts.sort(key=lambda item: (item.number, item.text_number))
    return Chapter(
        category=data["category"],
        slug=data["slug"],
        title_en=_clean_text(data.get("title_en", "")),
        title_zh=_clean_text(data.get("title_zh", "")),
        label=_clean_text(data.get("label", "")),
        subtitle=_clean_text(data.get("subtitle", "")),
        description=_clean_text(data.get("description", "")),
        recording_url=data.get("recording_url", "") or "",
        parts=parts,
        warnings=list(data.get("warnings", [])),
        source_urls=dict(data.get("source_urls", {})),
        fetched_at=float(data.get("fetched_at", 0) or 0),
    )


def extract_chapters_data(html: str) -> dict[str, Any]:
    """Find and parse the chapter payload, with conservative fallbacks."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="chaptersData")
    raw: str | None = None
    if tag is not None:
        raw = tag.string or tag.get_text()
    if not raw:
        match = _CHAPTER_JSON_RE.search(html)
        if match:
            raw = match.group(1)
    if not raw:
        # Last resort: any JSON script that has the two signature keys.
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            if '"chapters"' in text and '"frontmatter"' in text:
                raw = text
                break
    if not raw:
        raise SiteError(
            "Could not find the chaptersData JSON on this page. "
            "The site layout may have changed."
        )
    raw = raw.strip().lstrip("\ufeff")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SiteError(f"chaptersData is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise SiteError("chaptersData has an unexpected shape (missing 'chapters').")
    return data


def parse_chapter_page(html: str, locale: str) -> dict[str, Any]:
    data = extract_chapters_data(html)
    frontmatter = data.get("frontmatter") or {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}

    parts: list[dict[str, Any]] = []
    for index, item in enumerate(data.get("chapters", []), start=1):
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("number", index))
        except (TypeError, ValueError):
            number = index
        try:
            text_number = int(item.get("textPartNumber", number))
        except (TypeError, ValueError):
            text_number = number
        content = item.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        parts.append(
            {
                "number": number,
                "text_number": text_number,
                "title": _clean_text(item.get("title") or item.get("englishTitle") or ""),
                "english_title": _clean_text(item.get("englishTitle") or item.get("title") or ""),
                "content": content,
                "word_count": int(item.get("wordCount", 0) or 0),
                "gameplay_only": bool(item.get("gameplayOnly", False)),
                "video_url": item.get("videoUrl") or None,
            }
        )

    parts.sort(key=lambda item: (item["number"], item["text_number"]))
    return {
        "locale": data.get("locale") or locale,
        "frontmatter": frontmatter,
        "parts": parts,
    }


def _title_for(frontmatter: dict[str, Any], locale: str) -> str:
    for key in ("localeTitle", "title", "englishTitle"):
        value = _clean_text(frontmatter.get(key))
        if value:
            return value
    return ""


def _recording_url(frontmatter: dict[str, Any]) -> str:
    value = frontmatter.get("recording") or frontmatter.get("recordingUrl") or ""
    return value if isinstance(value, str) else ""


def _source_url(category: str, slug: str, locale: str) -> str:
    return f"{config.BASE_URL}/story/{category}/{slug}/{locale}/"


def _fetch_locale(category: str, slug: str, locale: str) -> tuple[str, str] | None:
    urls = [_source_url(category, slug, locale)]
    if locale == "en":
        urls.append(f"{config.BASE_URL}/story/{category}/{slug}/")
    last_error: Exception | None = None
    for url in urls:
        try:
            response = client.get(url)
            return response.url, response.text
        except NotFound as exc:
            last_error = exc
            continue
        except FetchError as exc:
            last_error = exc
            continue
    log.warning("No %s page for %s/%s: %s", locale, category, slug, last_error)
    return None


def fetch_chapter(category: str, slug: str, *, refresh: bool = False) -> Chapter:
    cache_file = _cache_path(f"chapters/{category}__{slug}.json")
    if not refresh:
        cached = _load_json(cache_file, config.CHAPTER_CACHE_TTL)
        if cached:
            try:
                chapter = chapter_from_dict(cached)
                if chapter.parts:
                    return chapter
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Bad chapter cache %s: %s", cache_file, exc)

    en_result = _fetch_locale(category, slug, "en")
    if en_result is None:
        raise SiteError(f"Could not load chapter /story/{category}/{slug}/ in English.")
    en_url, en_html = en_result
    zh_result = _fetch_locale(category, slug, "chs")

    warnings: list[str] = []
    try:
        en_data = parse_chapter_page(en_html, "en")
    except SiteError:
        raise
    if not en_data["parts"]:
        raise SiteError(f"Chapter {category}/{slug} has no Parts in its EN payload.")

    front = en_data["frontmatter"]
    title_en = _title_for(front, "en") or slug
    chapter = Chapter(
        category=category,
        slug=slug,
        title_en=title_en,
        label=_clean_text(front.get("label", "")),
        subtitle=_clean_text(front.get("subtitle", "")),
        description=_clean_text(front.get("description", "")),
        recording_url=_recording_url(front),
        warnings=warnings,
        source_urls={"en": en_url},
        fetched_at=time.time(),
    )

    for item in en_data["parts"]:
        content = item["content"]
        if content and len(content) < config.MIN_PART_CONTENT:
            warnings.append(f"Part {item['number']} has suspiciously little EN text.")
        chapter.parts.append(
            Part(
                number=item["number"],
                text_number=item["text_number"],
                title_en=item["english_title"] or item["title"] or f"Part {item['number']}",
                content_en=content,
                word_count=item["word_count"],
                gameplay_only=item["gameplay_only"],
                video_url=item["video_url"],
            )
        )

    if zh_result is not None:
        zh_url, zh_html = zh_result
        try:
            zh_data = parse_chapter_page(zh_html, "chs")
        except SiteError as exc:
            warnings.append(f"Chinese transcript page could not be parsed: {exc}")
            zh_data = None
        if zh_data is not None:
            chapter.title_zh = _title_for(zh_data["frontmatter"], "chs")
            chapter.source_urls["chs"] = zh_url
            zh_parts = {str(item["text_number"]): item for item in zh_data["parts"]}
            for part in chapter.parts:
                other = zh_parts.pop(part.key, None)
                if other is None:
                    warnings.append(
                        f"Part {part.number} has no Chinese counterpart (textPartNumber={part.key})."
                    )
                    continue
                part.title_zh = other["title"] or other["english_title"]
                part.content_zh = other["content"]
            for leftover in zh_parts.values():
                warnings.append(
                    f"Chinese Part {leftover['number']} has no English counterpart."
                )
            if len(zh_data["parts"]) != len(en_data["parts"]):
                warnings.append(
                    "EN/ZH Part counts differ: "
                    f"{len(en_data['parts'])} vs {len(zh_data['parts'])}."
                )
    else:
        warnings.append("No Chinese transcript page was available.")

    if not chapter.recording_url:
        for part in chapter.parts:
            if part.video_url:
                chapter.recording_url = part.video_url
                break
    if not chapter.recording_url:
        warnings.append("This chapter has no recording video URL.")

    # Stable, validated order.
    chapter.parts.sort(key=lambda item: (item.number, item.text_number))
    seen: set[str] = set()
    for part in chapter.parts:
        if part.key in seen:
            warnings.append(f"Duplicate textPartNumber {part.key} was de-duplicated.")
        seen.add(part.key)

    _write_json_atomic(cache_file, chapter_to_dict(chapter))
    return chapter


def discover_categories(*, refresh: bool = False) -> list[dict[str, str]]:
    cache_file = _cache_path("categories.json")
    if not refresh:
        cached = _load_json(cache_file, config.TREE_CACHE_TTL)
        if isinstance(cached, list) and cached:
            return cached

    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        html = client.get_text("/")
        soup = BeautifulSoup(html, "lxml")
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            match = _CHAPTER_PATH_RE.match(href.split("?")[0])
            if not match:
                continue
            slug = match.group(1)
            if slug in seen:
                continue
            label = anchor.get_text(" ", strip=True)
            if not label:
                label = dict(FALLBACK_CATEGORIES).get(slug, slug.title())
            categories.append({"slug": slug, "label": label})
            seen.add(slug)
    except (FetchError, NotFound) as exc:
        log.warning("Could not read the home page while discovering categories: %s", exc)

    for slug, label in FALLBACK_CATEGORIES:
        if slug not in seen:
            categories.append({"slug": slug, "label": label})
            seen.add(slug)

    _write_json_atomic(cache_file, categories)
    return categories


def discover_chapters(category: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    cache_file = _cache_path(f"tree/{category}.json")
    if not refresh:
        cached = _load_json(cache_file, config.TREE_CACHE_TTL)
        if isinstance(cached, list) and cached:
            return cached

    try:
        html = client.get_text(f"/story/{category}/")
    except (FetchError, NotFound) as exc:
        raise SiteError(f"Could not load category '{category}': {exc}") from exc

    soup = BeautifulSoup(html, "lxml")
    chapters: list[dict[str, Any]] = []
    seen: set[str] = set()
    prefix = f"/story/{category}/"
    for anchor in soup.select(f'a[href^="{prefix}"]'):
        href = anchor.get("href", "").split("?")[0]
        match = _CHAPTER_PATH_RE.match(href)
        if not match or match.group(1) != category:
            continue
        slug = match.group(2)
        if slug in seen:
            continue
        title_node = anchor.select_one(".story-chapter-title")
        title = _clean_text(
            (title_node.get_text(" ", strip=True) if title_node else "")
            or anchor.get_text(" ", strip=True)
            or slug
        )
        chapters.append(
            {
                "slug": slug,
                "path": f"/story/{category}/{slug}/",
                "title_en": title,
                "title_zh": "",
            }
        )
        seen.add(slug)

    if not chapters:
        # Regex fallback for markup changes.
        for slug in re.findall(rf'href=["\']?{re.escape(prefix)}([^/"\'\s>]+)/?', html):
            if slug not in seen:
                chapters.append(
                    {
                        "slug": slug,
                        "path": f"/story/{category}/{slug}/",
                        "title_en": slug,
                        "title_zh": "",
                    }
                )
                seen.add(slug)

    _write_json_atomic(cache_file, chapters)
    return chapters


_tree_lock = threading.Lock()


def get_tree(*, refresh: bool = False) -> dict[str, Any]:
    """Return categories with their chapter stubs (Parts are loaded lazily)."""
    with _tree_lock:
        categories = discover_categories(refresh=refresh)
        out = []
        errors = []
        for cat in categories:
            try:
                chapters = discover_chapters(cat["slug"], refresh=refresh)
            except SiteError as exc:
                errors.append({"category": cat["slug"], "error": str(exc)})
                chapters = []
            out.append({**cat, "chapters": chapters})
        return {"categories": out, "errors": errors, "scanned_at": time.time()}


def clear_chapter_cache() -> int:
    """Drop cached chapter payloads so the next expand re-reads the site."""
    removed = 0
    for path in (config.CACHE_DIR / "chapters").glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def fetch_chapters_parallel(    paths: list[str], *, refresh: bool = False, workers: int = 4
) -> list[tuple[str, Chapter | None, str | None]]:
    """Fetch several chapter detail payloads, used by the UI's bulk actions."""

    def work(path: str):
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "story":
            return path, None, f"Invalid chapter path: {path}"
        try:
            return path, fetch_chapter(parts[1], parts[2], refresh=refresh), None
        except (SiteError, FetchError) as exc:
            return path, None, str(exc)

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, path): path for path in paths}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: paths.index(item[0]))
    return results
