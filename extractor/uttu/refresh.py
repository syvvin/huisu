"""Re-align existing Parts without re-encoding their videos.

Alignment fixes can change subtitle timing while the split video stays valid.
This command recomputes the three SRT tracks and updates ``part.json`` in
place, with per-Part staging and rollback.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import asr, config, media, site, subtitles, timing, transcript, voices

log = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    dir: str
    part: int
    ok: bool
    changed: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dir": self.dir,
            "part": self.part,
            "ok": self.ok,
            "changed": self.changed,
            "error": self.error,
            "warnings": self.warnings,
        }


def _parse_srt_times(path: Path) -> list[tuple[float, float]]:
    try:
        cues = subtitles.parse_srt(path.read_text(encoding="utf-8-sig"))
        return [(cue.start, cue.end) for cue in cues]
    except (OSError, subtitles.SrtError):
        return []


def _count_changes(old: list[tuple[float, float]], new_cues: list[timing.Cue]) -> int:
    changed = 0
    for (old_start, old_end), cue in zip(old, new_cues):
        if abs(old_start - cue.start) > 0.05 or abs(old_end - cue.end) > 0.05:
            changed += 1
    changed += abs(len(old) - len(new_cues))
    return changed


def refresh_part(
    part_json: Path,
    *,
    chapters: dict[str, Any],
    words_cache: dict[str, list],
) -> RefreshResult:
    folder = part_json.parent
    try:
        data = json.loads(part_json.read_text(encoding="utf-8"))
        source = data.get("source") or {}
        chapter_path = str(source.get("chapter_path") or "")
        part_num = int((data.get("part") or {}).get("number"))
        boundary = data.get("boundary") or {}
        start = float(boundary.get("start"))
        end = float(boundary.get("end"))
        video = folder / "video.mp4"
        if not video.is_file():
            raise RuntimeError("video.mp4 is missing")
        info = media.probe_media(video)
        if not info or not info["has_video"] or not info["has_audio"]:
            raise RuntimeError("video.mp4 cannot be probed")

        if chapter_path not in chapters:
            category, slug = chapter_path.strip("/").split("/")[1:3]
            chapters[chapter_path] = site.fetch_chapter(category, slug)
        chapter = chapters[chapter_path]
        part = chapter.part(part_num)
        if part is None:
            raise RuntimeError(f"Part {part_num} not found on the site")

        recording = str(source.get("recording") or chapter.recording_url)
        match = re.search(r"(?:youtu\.be/|v=)([A-Za-z0-9_-]{11})", recording)
        if not match:
            raise RuntimeError(f"unrecognized recording URL: {recording}")
        video_id = match.group(1)
        if video_id not in words_cache:
            words_cache[video_id] = asr.get_words(video_id)[0]

        lines, transcript_warnings = transcript.pair_part_transcripts(
            part.number, part.content_en, part.content_zh
        )
        durations = voices.ensure_durations([line.voice for line in lines])
        cues, alignment = timing.align_part(
            lines, words_cache[video_id], start, end, durations
        )
        validation = timing.validate_cues(cues, float(info["duration"]), alignment)
        if not validation["ok"]:
            raise RuntimeError("subtitle validation failed: " + "; ".join(validation["errors"]))

        en_text = timing.format_srt(cues, bilingual=False)
        zh_text = timing.format_srt_zh(cues)
        bi_text = timing.format_srt(cues, bilingual=True)

        old_times = _parse_srt_times(folder / "en.srt")
        changed = _count_changes(old_times, cues)

        tmp = folder / f".refresh-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=False)
        try:
            (tmp / "en.srt").write_text(en_text, encoding="utf-8-sig", newline="\n")
            (tmp / "zh.srt").write_text(zh_text, encoding="utf-8-sig", newline="\n")
            (tmp / "bilingual.srt").write_text(bi_text, encoding="utf-8-sig", newline="\n")
            srt_en = subtitles.validate_srt_file(
                tmp / "en.srt", expected_cues=len(cues), duration=float(info["duration"])
            )
            srt_zh = subtitles.validate_srt_file(
                tmp / "zh.srt", expected_cues=len(cues), duration=float(info["duration"])
            )
            srt_bi = subtitles.validate_srt_file(
                tmp / "bilingual.srt",
                expected_cues=len(cues),
                duration=float(info["duration"]),
                bilingual=True,
            )

            data["subtitles"] = {
                "en": {**srt_en, "file": "en.srt"},
                "zh": {**srt_zh, "file": "zh.srt"},
                "bilingual": {**srt_bi, "file": "bilingual.srt"},
                "alignment": alignment.to_dict(),
                "validation": validation,
                "transcript_warnings": transcript_warnings,
            }
            data["warnings"] = list(validation["warnings"]) + list(transcript_warnings)
            data["alignment_revision"] = 2
            data["subtitles_refreshed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            (tmp / "part.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )

            targets = ["en.srt", "zh.srt", "bilingual.srt", "part.json"]
            backup = tmp / "backup"
            backup.mkdir()
            for name in targets:
                target = folder / name
                if target.exists():
                    shutil.copy2(target, backup / name)
            replaced: list[str] = []
            try:
                for name in targets:
                    os.replace(tmp / name, folder / name)
                    replaced.append(name)
            except Exception:
                for name in replaced:
                    shutil.copy2(backup / name, folder / name)
                raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        return RefreshResult(
            dir=str(folder),
            part=part_num,
            ok=True,
            changed=changed,
            warnings=validation["warnings"] + transcript_warnings,
        )
    except Exception as exc:
        return RefreshResult(
            dir=str(folder), part=0, ok=False, error=f"{type(exc).__name__}: {exc}"
        )


def refresh_tree(root: Path, *, limit: int | None = None) -> dict[str, Any]:
    part_files = sorted(root.glob("**/part.json"))
    if limit:
        part_files = part_files[:limit]
    chapters: dict[str, Any] = {}
    words_cache: dict[str, list] = {}
    results: list[RefreshResult] = []
    started = time.time()
    for index, part_json in enumerate(part_files, 1):
        log.info("Refreshing subtitles %s/%s: %s", index, len(part_files), part_json.parent)
        results.append(refresh_part(part_json, chapters=chapters, words_cache=words_cache))
    report = {
        "root": str(root),
        "started_at": datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parts": [result.to_dict() for result in results],
        "ok": all(result.ok for result in results),
        "checked": len(results),
        "failed": sum(1 for result in results if not result.ok),
        "changed_parts": sum(1 for result in results if result.changed),
        "changed_cues": sum(result.changed for result in results),
    }
    return report


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
