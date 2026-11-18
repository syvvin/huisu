"""Independent post-flight validation of extracted Parts.

This does not trust the pipeline's own bookkeeping.  It re-opens every video,
re-parses every SRT, compares the three subtitle tracks against each other,
checks the segment's first/last frames against the source recording, and can
decode the whole video to catch truncation.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import config, media, subtitles

log = logging.getLogger(__name__)

_YOUTUBE_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))([A-Za-z0-9_-]{11})"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CJK_PUNCT_RE = re.compile(r"[\u3000-\u303f\uff00-\uffef]")
_LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass
class PartValidation:
    dir: str
    part: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _ffmpeg_frame(path: Path, at: float, size: str = "96:54") -> bytes | None:
    try:
        proc = subprocess.run(
            [
                config.find_ffmpeg(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, at):.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={size},format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            timeout=60,
            creationflags=config.NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def _frame_mae(a: bytes | None, b: bytes | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    total = sum(abs(x - y) for x, y in zip(a, b))
    return total / len(a)


def _decode_check(path: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [
                config.find_ffmpeg(),
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            creationflags=config.NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or "").strip()[-800:]
    errors = [line for line in (proc.stderr or "").splitlines() if line.strip()]
    return True, "; ".join(errors[:5])


def validate_part(part_json: Path, *, decode: bool = False, frames: bool = True) -> PartValidation:
    data = json.loads(part_json.read_text(encoding="utf-8"))
    folder = part_json.parent
    result = PartValidation(dir=str(folder), part=int(data.get("part", {}).get("number", 0)))

    expected_files = {"part.json", "video.mp4", "en.srt", "zh.srt", "bilingual.srt"}
    actual_files = {item.name for item in folder.iterdir() if item.is_file()}
    if actual_files != expected_files:
        result.errors.append(f"unexpected file set: {sorted(actual_files)}")
    if any(name.endswith(".tmp") for name in actual_files):
        result.errors.append("temporary files were left behind")

    video = folder / "video.mp4"
    info = media.probe_media(video)
    if not info:
        result.errors.append("video.mp4 is missing or unreadable")
        return result
    if not info["has_video"] or not info["has_audio"]:
        result.errors.append("video.mp4 lacks a video or audio stream")
    duration = float(info["duration"])
    declared = float(data.get("video", {}).get("duration") or 0)
    boundary = float(data.get("boundary", {}).get("duration") or 0)
    result.checks["video_duration"] = round(duration, 3)
    result.checks["declared_duration"] = round(declared, 3)
    result.checks["boundary_duration"] = round(boundary, 3)
    if declared and abs(duration - declared) > 1.0:
        result.errors.append(f"video duration {duration:.2f}s != declared {declared:.2f}s")
    if boundary and abs(duration - boundary) > 1.5:
        result.errors.append(f"video duration {duration:.2f}s != boundary {boundary:.2f}s")
    if duration < 15:
        result.errors.append(f"video is suspiciously short ({duration:.1f}s)")

    try:
        en_cues = subtitles.parse_srt((folder / "en.srt").read_text(encoding="utf-8-sig"))
        zh_cues = subtitles.parse_srt((folder / "zh.srt").read_text(encoding="utf-8-sig"))
        bi_cues = subtitles.parse_srt((folder / "bilingual.srt").read_text(encoding="utf-8-sig"))
    except (OSError, subtitles.SrtError) as exc:
        result.errors.append(f"subtitle parse failed: {exc}")
        return result

    counts = {"en": len(en_cues), "zh": len(zh_cues), "bilingual": len(bi_cues)}
    result.checks["cues"] = counts
    declared_cues = int(data.get("subtitles", {}).get("en", {}).get("cues", 0) or 0)
    if not (len(en_cues) == len(zh_cues) == len(bi_cues)):
        result.errors.append(f"cue-count mismatch: {counts}")
    if declared_cues and len(en_cues) != declared_cues:
        result.errors.append(f"cue count {len(en_cues)} != declared {declared_cues}")
    if len(en_cues) < 10:
        result.errors.append(f"too few cues ({len(en_cues)})")

    previous_end = -1.0
    for index, (en, zh, bi) in enumerate(zip(en_cues, zh_cues, bi_cues), start=1):
        if abs(en.start - zh.start) > 0.001 or abs(en.end - zh.end) > 0.001:
            result.errors.append(f"cue {index}: EN/ZH times differ")
            break
        if abs(en.start - bi.start) > 0.001 or abs(en.end - bi.end) > 0.001:
            result.errors.append(f"cue {index}: bilingual times differ")
            break
        if en.end > duration + 0.5:
            result.errors.append(f"cue {index}: ends beyond video")
            break
        if en.start < previous_end - 0.05:
            result.errors.append(f"cue {index}: overlaps previous cue")
            break
        previous_end = en.end
        if len(bi.lines) < 2:
            result.errors.append(f"cue {index}: bilingual cue has fewer than 2 lines")
            break
        if bi.lines[0].strip() != en.text.strip() or bi.lines[1].strip() != zh.text.strip():
            result.errors.append(f"cue {index}: bilingual text differs from single-language tracks")
            break
        if not _LATIN_RE.search(en.text):
            result.warnings.append(f"cue {index}: English line has no Latin letters")
        if not _CJK_RE.search(zh.text) and not _CJK_PUNCT_RE.search(zh.text):
            result.warnings.append(f"cue {index}: Chinese line has no CJK characters")
        if "\ufffd" in en.text or "\ufffd" in zh.text:
            result.errors.append(f"cue {index}: replacement character found")
            break

    # Verify first and last frames against the source recording.
    if frames:
        source = config.CACHE_DIR / "media" / f"{data.get('source', {}).get('recording_id', '')}.mp4"
        if not source.exists():
            # Older metadata only stores the URL.
            match = _YOUTUBE_RE.search(data.get("source", {}).get("recording", ""))
            if match:
                source = config.CACHE_DIR / "media" / f"{match.group(1)}.mp4"
        boundary_start = float(data.get("boundary", {}).get("start") or 0)
        if source.exists() and boundary_start >= 0:
            for label, offset in (("start", 2.0), ("end", max(1.0, duration - 2.0))):
                segment_frame = _ffmpeg_frame(video, offset)
                source_frame = _ffmpeg_frame(source, boundary_start + offset)
                mae = _frame_mae(segment_frame, source_frame)
                result.checks[f"frame_mae_{label}"] = round(mae, 2) if mae is not None else None
                if mae is None:
                    result.warnings.append(f"could not compare {label} frame with source")
                elif mae > 22:
                    result.errors.append(
                        f"{label} frame differs from source (MAE {mae:.1f}); split may be offset"
                    )
        else:
            result.warnings.append("source recording not in cache; skipped frame-boundary check")

    if decode:
        ok, detail = _decode_check(video)
        result.checks["full_decode"] = ok
        if not ok:
            result.errors.append(f"full decode failed: {detail}")

    result.checks["size_mb"] = round(info["size"] / 1e6, 1)
    return result


def validate_tree(root: Path, *, decode: bool = False, frames: bool = True) -> dict:
    parts: list[PartValidation] = []
    for part_json in sorted(root.glob("**/part.json")):
        log.info("Validating %s", part_json.parent)
        try:
            parts.append(validate_part(part_json, decode=decode, frames=frames))
        except Exception as exc:  # a broken file should be reported, not abort the sweep
            parts.append(
                PartValidation(
                    dir=str(part_json.parent),
                    part=0,
                    errors=[f"validator crashed: {type(exc).__name__}: {exc}"],
                )
            )
    report = {
        "root": str(root),
        "parts": [
            {
                "dir": item.dir,
                "part": item.part,
                "ok": item.ok,
                "errors": item.errors,
                "warnings": item.warnings,
                "checks": item.checks,
            }
            for item in parts
        ],
        "ok": all(item.ok for item in parts) and bool(parts),
        "parts_checked": len(parts),
        "parts_failed": sum(1 for item in parts if not item.ok),
        "cues_checked": sum(int(item.checks.get("cues", {}).get("en", 0)) for item in parts),
    }
    return report


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# Extraction validation",
        "",
        f"- Parts checked: {report['parts_checked']}",
        f"- Parts failed: {report['parts_failed']}",
        f"- Cues checked: {report['cues_checked']}",
        f"- Overall: {'PASS' if report['ok'] else 'FAIL'}",
        "",
        "| Part | Folder | Cues | Video | Start MAE | End MAE | Result |",
        "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["parts"]:
        checks = item["checks"]
        lines.append(
            f"| {item['part']} | `{Path(item['dir']).name}` | "
            f"{checks.get('cues', {}).get('en', '-')} | {checks.get('video_duration', '-')}s | "
            f"{checks.get('frame_mae_start', '-')} | {checks.get('frame_mae_end', '-')} | "
            f"{'PASS' if item['ok'] else 'FAIL'} |"
        )
    errors = [f"- `{Path(item['dir']).name}`: {error}" for item in report["parts"] for error in item["errors"]]
    if errors:
        lines += ["", "## Errors", ""] + errors
    warnings = [f"- `{Path(item['dir']).name}`: {warning}" for item in report["parts"] for warning in item["warnings"]]
    if warnings:
        lines += ["", "## Warnings", ""] + warnings[:50]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
