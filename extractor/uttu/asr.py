"""Fetch and parse YouTube's English caption track.

The recording uploads have auto-generated English captions.  Their ``json3``
form contains word-level timings, which we use as timing anchors for the
site's much cleaner script text.  Rolling duplicate windows are de-duplicated
before alignment.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


class AsrError(RuntimeError):
    """No usable English timing source is available."""


@dataclass
class Word:
    text: str
    start: float
    end: float


def _norm_word(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = value.replace("’", "'")
    return re.sub(r"[^a-z0-9']+", "", value)


def captions_path(video_id: str) -> Path:
    return config.CACHE_DIR / "asr" / f"{video_id}.en.json3"


def download_captions(video_id: str, *, force: bool = False) -> Path:
    target = captions_path(video_id)
    if target.exists() and target.stat().st_size > 100 and not force:
        try:
            parse_json3(target)
            return target
        except AsrError:
            log.warning("Cached captions for %s are unusable; re-downloading.", video_id)

    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *config.find_ytdlp(),
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*",
        "--sub-format",
        "json3",
        "-o",
        str(target.parent / f"{video_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    log.info("Fetching English captions for %s", video_id)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=config.NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise AsrError(
            f"yt-dlp could not fetch English captions for {video_id}: " + " | ".join(tail)
        )
    if not target.exists():
        candidates = sorted(target.parent.glob(f"{video_id}*.json3"))
        candidates = [item for item in candidates if item.name.startswith(f"{video_id}.en")]
        if not candidates:
            candidates = sorted(target.parent.glob(f"{video_id}*.json3"))
        if candidates:
            target = candidates[0]
        else:
            raise AsrError(
                f"YouTube did not return an English json3 caption track for {video_id}."
            )
    parse_json3(target)  # validate immediately
    return target


def parse_json3(path: Path) -> list[Word]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AsrError(f"Could not read caption JSON {path}: {exc}") from exc
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise AsrError(f"Caption JSON {path} has no events list.")

    raw: list[tuple[float, str]] = []
    for event in events:
        if not isinstance(event, dict) or "segs" not in event:
            continue
        base_ms = float(event.get("tStartMs", 0) or 0)
        for seg in event.get("segs") or []:
            text = str(seg.get("utf8", "")).strip()
            if not text:
                continue
            if not _norm_word(text):
                continue
            start = (base_ms + float(seg.get("tOffsetMs", 0) or 0)) / 1000.0
            raw.append((start, text))

    raw.sort(key=lambda item: item[0])
    deduped: list[tuple[float, str]] = []
    for start, text in raw:
        if deduped:
            prev_start, prev_text = deduped[-1]
            if _norm_word(text) == _norm_word(prev_text) and abs(start - prev_start) < 0.15:
                continue
        deduped.append((start, text))

    if len(deduped) < 20:
        raise AsrError(f"Caption track {path} contains too few words ({len(deduped)}).")

    words: list[Word] = []
    for index, (start, text) in enumerate(deduped):
        end = deduped[index + 1][0] if index + 1 < len(deduped) else start + 0.6
        if end <= start:
            end = start + 0.25
        words.append(Word(text=text, start=start, end=end))
    return words


def get_words(video_id: str, *, force: bool = False) -> tuple[list[Word], str]:
    path = download_captions(video_id, force=force)
    return parse_json3(path), str(path)
