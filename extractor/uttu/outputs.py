"""Index of extracted Parts currently on disk.

The UI uses this to mark tree nodes as already local, instead of making the
user guess whether a Part has been extracted before.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import config

_lock = threading.RLock()
_cache: dict[str, Any] | None = None
_cache_at = 0.0
_CACHE_TTL = 1.2

_EXPECTED_FILES = ("video.mp4", "en.srt", "zh.srt", "bilingual.srt", "part.json")


def _read_part(part_json: Path, root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(part_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    chapter_path = str((data.get("source") or {}).get("chapter_path") or "")
    part = data.get("part") or {}
    if not chapter_path or not part:
        return None
    try:
        number = int(part.get("number"))
    except (TypeError, ValueError):
        return None
    folder = part_json.parent
    try:
        relative_dir = folder.relative_to(root).as_posix()
    except ValueError:
        relative_dir = folder.name
    files: dict[str, str] = {}
    missing: list[str] = []
    for name in _EXPECTED_FILES:
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            files[name] = f"{relative_dir}/{name}"
        else:
            missing.append(name)
    video_size = 0
    try:
        video_size = (folder / "video.mp4").stat().st_size
    except OSError:
        pass
    subtitle = data.get("subtitles") or {}
    cues = int((subtitle.get("en") or {}).get("cues", 0) or 0)
    boundary = data.get("boundary") or {}
    video = data.get("video") or {}
    return {
        "path": chapter_path,
        "number": number,
        "text_number": part.get("text_number"),
        "title_en": part.get("title_en", ""),
        "title_zh": part.get("title_zh", ""),
        "dir": relative_dir,
        "status": "complete" if not missing else "partial",
        "missing": missing,
        "cues": cues,
        "duration": boundary.get("duration"),
        "video_duration": video.get("duration"),
        "height": video.get("height"),
        "requested_height": data.get("requested_height")
        or (data.get("source") or {}).get("requested_height"),
        "size": video_size,
        "generated_at": data.get("generated_at", ""),
        "match_ratio": (subtitle.get("alignment") or {}).get("match_ratio"),
        "files": files,
    }


def scan_outputs(root: Path | None = None) -> dict[str, Any]:
    root = (root or config.OUTPUT_DIR).resolve()
    parts: dict[str, dict[str, Any]] = {}
    chapters: dict[str, dict[str, Any]] = {}
    categories: dict[str, dict[str, Any]] = {}
    if root.exists():
        for part_json in sorted(root.glob("**/part.json")):
            if ".staging" in part_json.parts:
                continue
            info = _read_part(part_json, root)
            if info is None:
                continue
            key = f"{info['path']}#{info['number']}"
            previous = parts.get(key)
            if previous is None or info.get("generated_at", "") > previous.get("generated_at", ""):
                parts[key] = info
    for info in parts.values():
        chapter = chapters.setdefault(
            info["path"],
            {
                "path": info["path"],
                "parts": {},
                "complete": 0,
                "partial": 0,
                "bytes": 0,
            },
        )
        chapter["parts"][str(info["number"])] = info
        if info["status"] == "complete":
            chapter["complete"] += 1
        else:
            chapter["partial"] += 1
        chapter["bytes"] += int(info.get("size") or 0)
        category = chapter["path"].strip("/").split("/")
        slug = category[1] if len(category) > 2 else "unknown"
        cat = categories.setdefault(slug, {"complete": 0, "partial": 0, "bytes": 0})
        if info["status"] == "complete":
            cat["complete"] += 1
        else:
            cat["partial"] += 1
        cat["bytes"] += int(info.get("size") or 0)
    return {
        "parts": parts,
        "chapters": chapters,
        "categories": categories,
        "scanned_at": time.time(),
        "total_complete": sum(1 for info in parts.values() if info["status"] == "complete"),
        "total_partial": sum(1 for info in parts.values() if info["status"] != "complete"),
    }


def get_outputs(*, refresh: bool = False, root: Path | None = None) -> dict[str, Any]:
    global _cache, _cache_at
    with _lock:
        now = time.monotonic()
        if not refresh and _cache is not None and now - _cache_at < _CACHE_TTL:
            return _cache
        _cache = scan_outputs(root)
        _cache_at = now
        return _cache


def invalidate() -> None:
    global _cache, _cache_at
    with _lock:
        _cache = None
        _cache_at = 0.0
