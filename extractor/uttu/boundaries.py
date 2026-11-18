"""Derive Part boundaries from the recording's YouTube chapters.

A chapter is usually one long recording.  Its YouTube chapters mark the
individual Parts, sometimes with extra battle/continuation chapters and
sometimes with unrelated extras (Story Trails, Daily Moon, credits).  We
anchor each Part by number or title, then extend it across battle/continuation
chapters and stop at the first unrelated extra or the next Part.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from . import config
from .models import Part

log = logging.getLogger(__name__)

_EXTRAS_RE = re.compile(
    r"(?i)\b("
    r"story\s*trails?|daily\s*moon|summon\s*tutorial|gacha|tuning\s*tutorial|"
    r"opportunism|credits?|trailer|teaser|recap|advertisement|interlude|"
    r"start|intro|outro|pv"
    r")\b"
)
_PART_RE = re.compile(r"(?i)\bpart\s*#?\s*0*(\d+)\b")
_TIMESTAMP_RE = re.compile(
    r"^\s*(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,2}):(?P<s>\d{2})\s*"
    r"(?:[-–—]\s*)?(?P<title>.+?)\s*$"
)


class BoundaryError(RuntimeError):
    """The recording's chapters cannot be mapped safely onto Parts."""


@dataclass
class VideoMeta:
    video_id: str
    title: str
    duration: float
    chapters: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    uploader: str = ""
    webpage_url: str = ""
    source: str = "yt-dlp"


@dataclass
class Boundary:
    key: str
    number: int
    title: str
    start: float
    end: float
    anchor_title: str
    start_chapter: int
    end_chapter: int
    first_extra_start: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "number": self.number,
            "title": self.title,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "anchor_title": self.anchor_title,
            "first_extra_start": (
                round(self.first_extra_start, 3)
                if self.first_extra_start is not None
                else None
            ),
        }


def _meta_cache(video_id: str):
    path = config.CACHE_DIR / "yt" / f"{video_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_meta_cache(video_id: str) -> VideoMeta | None:
    path = _meta_cache(video_id)
    if not path.exists() or time.time() - path.stat().st_mtime > config.YT_CACHE_TTL:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return VideoMeta(**data)
    except (OSError, ValueError, TypeError):
        return None


def _save_meta_cache(meta: VideoMeta) -> None:
    path = _meta_cache(meta.video_id)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(meta.__dict__, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(path)


def _parse_description(description: str, duration: float) -> list[dict[str, Any]]:
    entries: list[tuple[float, str]] = []
    for line in (description or "").splitlines():
        match = _TIMESTAMP_RE.match(line.strip())
        if not match:
            continue
        hours = int(match.group("h") or 0)
        minutes = int(match.group("m"))
        seconds = int(match.group("s") or 0)
        if minutes > 59 or seconds > 59:
            continue
        entries.append(
            (hours * 3600 + minutes * 60 + seconds, match.group("title").strip())
        )
    entries.sort()
    if len(entries) < 3:
        return []
    chapters = []
    for index, (start, title) in enumerate(entries):
        end = entries[index + 1][0] if index + 1 < len(entries) else duration
        if end > start:
            chapters.append({"start_time": start, "end_time": end, "title": title})
    return chapters


def fetch_video_meta(video_id: str, *, refresh: bool = False) -> VideoMeta:
    if not refresh:
        cached = _load_meta_cache(video_id)
        if cached and cached.chapters:
            return cached

    info: dict[str, Any] | None = None
    try:
        from yt_dlp import YoutubeDL

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except Exception as exc:  # yt-dlp raises many exception types
        log.warning("yt-dlp Python API failed for %s (%s); retrying as subprocess.", video_id, exc)

    if info is None:
        cmd = [
            *config.find_ytdlp(),
            "--skip-download",
            "--no-warnings",
            "--no-playlist",
            "-J",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=config.NO_WINDOW,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            raise BoundaryError(
                f"Could not read YouTube metadata for {video_id}: " + " | ".join(tail)
            )
        try:
            info = json.loads(proc.stdout)
        except ValueError as exc:
            raise BoundaryError(f"yt-dlp returned invalid JSON for {video_id}.") from exc

    chapters = []
    for item in info.get("chapters") or []:
        try:
            start = float(item["start_time"])
            end = float(item["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(item.get("title") or "").strip()
        if end > start:
            chapters.append({"start_time": start, "end_time": end, "title": title})
    duration = float(info.get("duration") or 0)
    if not chapters:
        chapters = _parse_description(info.get("description") or "", duration)
    chapters.sort(key=lambda item: item["start_time"])

    meta = VideoMeta(
        video_id=video_id,
        title=str(info.get("title") or ""),
        duration=duration,
        chapters=chapters,
        description=str(info.get("description") or ""),
        uploader=str(info.get("uploader") or ""),
        webpage_url=str(info.get("webpage_url") or ""),
        source="yt-dlp",
    )
    _save_meta_cache(meta)
    return meta


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"(?i)\bpart\s*#?\s*0*\d+\b", " ", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _similarity(a: str, b: str) -> float:
    a_norm = _normalise(a)
    b_norm = _normalise(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    if a_norm in b_norm or b_norm in a_norm:
        return 0.9
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def is_extra(title: str) -> bool:
    return bool(_EXTRAS_RE.search(title or ""))


def derive_boundaries(parts: list[Part], meta: VideoMeta) -> list[Boundary]:
    chapters = [item for item in meta.chapters if float(item.get("end_time", 0)) > float(item.get("start_time", 0))]
    if not chapters:
        raise BoundaryError(
            f"Recording {meta.video_id} has no YouTube chapters and no timestamp list "
            "in its description."
        )
    if meta.duration <= 0:
        meta.duration = float(chapters[-1]["end_time"])

    anchors: dict[str, int] = {}
    used: set[int] = set()
    last_index = -1
    for part in parts:
        best: tuple[float, int] | None = None
        for index, chapter in enumerate(chapters):
            if index <= last_index or index in used:
                continue
            title = str(chapter.get("title") or "")
            score = 0.0
            match = _PART_RE.search(title)
            if match:
                number = int(match.group(1))
                if number in (part.number, part.text_number):
                    score = 100.0
            if score == 0.0:
                similarity = _similarity(title, part.title_en)
                if similarity >= 0.62:
                    score = similarity * 50.0
            if score and (best is None or score > best[0]):
                best = (score, index)
        if best is None:
            continue
        anchors[part.key] = best[1]
        used.add(best[1])
        last_index = best[1]

    if len(anchors) < len(parts):
        # Fallback: if there are exactly as many non-extra chapters as Parts,
        # and they are all in order, map them 1:1.
        non_extra = [
            index for index, chapter in enumerate(chapters)
            if not is_extra(str(chapter.get("title") or ""))
        ]
        if len(non_extra) == len(parts) and len(anchors) < len(parts):
            anchors = {part.key: index for part, index in zip(parts, non_extra)}
            log.warning(
                "Part anchors were not title-matchable; mapped %s Parts 1:1 to "
                "non-extra chapters by order.",
                len(parts),
            )
        else:
            missing = [part.number for part in parts if part.key not in anchors]
            titles = [f"{index}: {chapter.get('title')}" for index, chapter in enumerate(chapters)]
            raise BoundaryError(
                f"Could not find a YouTube chapter for Part(s) {missing}. "
                f"Chapters were: {titles}"
            )

    ordered = sorted(parts, key=lambda item: anchors[item.key])
    boundaries: list[Boundary] = []
    for position, part in enumerate(ordered):
        anchor_index = anchors[part.key]
        anchor = chapters[anchor_index]
        start = float(anchor["start_time"])
        if position + 1 < len(ordered):
            next_index = anchors[ordered[position + 1].key]
            limit = float(chapters[next_index]["start_time"])
            end_index = next_index - 1
        else:
            next_index = len(chapters)
            limit = meta.duration
            end_index = len(chapters) - 1

        first_extra: float | None = None
        for index in range(anchor_index + 1, next_index):
            chapter = chapters[index]
            if is_extra(str(chapter.get("title") or "")):
                first_extra = float(chapter["start_time"])
                break

        # Do not stop at chapters whose titles look like extras.  The site's
        # transcript for this Part can continue through a "Summon Tutorial",
        # "Story Trails" or similar chapter.  We use the next Part anchor as
        # the outer bound, then trim the segment after the last subtitle cue
        # (see pipeline._process_part).  This keeps the whole Part's dialogue
        # while still excluding trailing unrelated footage.
        if end_index < anchor_index:
            end_index = anchor_index

        if limit - start < 15:
            raise BoundaryError(
                f"Part {part.number} would be {limit - start:.1f}s long; boundaries are wrong."
            )
        if limit > meta.duration + 1:
            limit = meta.duration
        boundaries.append(
            Boundary(
                key=part.key,
                number=part.number,
                title=part.title_en,
                start=start,
                end=limit,
                anchor_title=str(anchor.get("title") or ""),
                start_chapter=anchor_index,
                end_chapter=end_index,
                first_extra_start=first_extra,
            )
        )

    total = sum(item.duration for item in boundaries)
    if total > meta.duration * 1.02 + 2:
        raise BoundaryError("Derived Part boundaries exceed the recording duration.")
    return boundaries
