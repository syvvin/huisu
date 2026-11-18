"""Extraction jobs: acquire once, process each Part atomically, report everything."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import asr, boundaries, config, media, outputs, site, subtitles, timing, transcript, voices
from .models import Chapter, Part

log = logging.getLogger(__name__)

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_YOUTUBE_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))([A-Za-z0-9_-]{11})"
)
_CHAPTER_PATH_RE = re.compile(r"^/story/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)/?$")


class JobError(RuntimeError):
    pass


class PartError(RuntimeError):
    def __init__(self, message: str, *, stage: str = "part") -> None:
        super().__init__(message)
        self.stage = stage


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_name(value: str, fallback: str = "part", limit: int = 64) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("'", "").replace("\u2019", "")
    value = _INVALID_PATH_CHARS.sub(" ", value)
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("- .")
    if not value:
        value = fallback
    return value[:limit].rstrip("- .") or fallback


def youtube_id(url: str) -> str:
    match = _YOUTUBE_RE.search(url or "")
    if not match:
        raise JobError(f"Not a recognizable YouTube recording URL: {url!r}")
    return match.group(1)


def parse_chapter_path(path: str) -> tuple[str, str]:
    match = _CHAPTER_PATH_RE.match(path or "")
    if not match:
        raise JobError(f"Invalid chapter path: {path!r}")
    return match.group(1), match.group(2)


def chapter_dir(chapter: Chapter) -> Path:
    title = safe_name(chapter.title_en or chapter.slug, fallback=chapter.slug)
    return config.OUTPUT_DIR / safe_name(chapter.category, "story") / f"{chapter.slug}_{title}"


def part_dir(chapter: Chapter, part: Part) -> Path:
    title = safe_name(part.title_en or f"part-{part.number}", fallback=f"part-{part.number}")
    return chapter_dir(chapter) / f"part-{part.number:02d}_{title}"


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


@dataclass
class Job:
    id: str
    selection: list[dict[str, Any]]
    options: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    stage: str = "queued"
    progress: float = 0.0
    logs: list[dict[str, Any]] = field(default_factory=list)
    log_seq: int = 0
    outputs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    report_path: str | None = None
    cancel_requested: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self.log_seq += 1
            self.logs.append(
                {
                    "seq": self.log_seq,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level,
                    "message": message,
                }
            )
            if len(self.logs) > 2000:
                del self.logs[:400]

    def set(self, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)

    def error(self, message: str, *, stage: str, path: str = "", part: Any = None) -> None:
        with self._lock:
            self.errors.append(
                {
                    "stage": stage,
                    "path": path,
                    "part": part,
                    "message": message,
                    "time": _now_iso(),
                }
            )
        self.log(f"ERROR[{stage}] {message}", "error")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "progress": round(self.progress, 1),
                "selection": self.selection,
                "options": dict(self.options),
                "outputs": list(self.outputs),
                "errors": list(self.errors),
                "warnings": list(self.warnings),
                "logs": self.logs[-800:],
                "log_seq": self.log_seq,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "report_path": self.report_path,
                "cancel_requested": self.cancel_requested,
            }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        config.ensure_dirs()

    def create(self, selection: list[dict[str, Any]], options: dict[str, Any] | None = None) -> Job:
        if not isinstance(selection, list) or not selection:
            raise JobError("Select at least one Part.")
        cleaned: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in selection:
            if not isinstance(item, dict):
                raise JobError("Each selection must be an object with path and number.")
            path = str(item.get("path") or "")
            number = item.get("number", item.get("text_number"))
            parse_chapter_path(path)
            try:
                number_int = int(number)
            except (TypeError, ValueError) as exc:
                raise JobError(f"Invalid Part number: {number!r}") from exc
            key = (path, str(number_int))
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({"path": path, "number": number_int})
        if not cleaned:
            raise JobError("Select at least one Part.")

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        job = Job(id=job_id, selection=cleaned, options=options or {})
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"uttu-{job_id}")
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.snapshot() for job in jobs[:limit]]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.cancel_requested:
            return True
        job.set(cancel_requested=True)
        job.log("Cancellation requested; stopping the current step...", "warn")
        return True

    def _run(self, job: Job) -> None:
        job.set(status="running", started_at=time.time(), stage="starting")
        job.log(f"Job {job.id}: {len(job.selection)} Part(s) selected.")
        total = len(job.selection)
        done = 0
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in job.selection:
            grouped.setdefault(item["path"], []).append(item)

        try:
            for path, items in grouped.items():
                if job.cancel_requested:
                    break
                processed, chapter_errors = self._run_chapter(job, path, items, done, total)
                done += processed
                job.set(progress=done / max(1, total) * 100.0)
                if job.cancel_requested:
                    break
                if chapter_errors:
                    for item in items:
                        job.error(
                            chapter_errors,
                            stage="chapter",
                            path=path,
                            part=item["number"],
                        )
                    job.log(f"Chapter {path} failed; skipped {len(items)} Part(s).", "error")
        except Exception as exc:  # last-resort guard: never leave a job "running" forever
            log.exception("Unexpected job failure")
            job.error(f"Unexpected job failure: {exc}", stage="job")
            job.log(traceback.format_exc()[-2000:], "error")
        finally:
            outputs.invalidate()
            if job.cancel_requested:
                status = "cancelled"
            elif job.errors and job.outputs:
                status = "partial"
            elif job.errors:
                status = "failed"
            elif job.outputs:
                status = "done"
            else:
                status = "failed"
            job.set(status=status, stage=status, finished_at=time.time(), progress=100.0 if status != "cancelled" else job.progress)
            job.log(f"Finished with status {status}: {len(job.outputs)} Part(s) published, {len(job.errors)} error(s).")
            try:
                self._write_reports(job)
            except Exception as exc:
                job.error(f"Could not write the report: {exc}", stage="report")
                log.exception("Could not write report")

    def _run_chapter(
        self, job: Job, path: str, items: list[dict[str, Any]], done: int, total: int
    ) -> tuple[int, str | None]:
        category, slug = parse_chapter_path(path)
        job.set(stage=f"chapter {category}/{slug}")
        job.log(f"Loading chapter {path} ...")
        try:
            chapter = site.fetch_chapter(category, slug)
        except Exception as exc:
            return len(items), f"Could not load chapter metadata: {exc}"
        if not chapter.parts:
            return len(items), "Chapter has no Parts."
        for warning in chapter.warnings:
            job.warnings.append(f"{path}: {warning}")
            job.log(f"Chapter warning: {warning}", "warn")
        job.log(
            f"Chapter: {chapter.title_en}"
            + (f" / {chapter.title_zh}" if chapter.title_zh else "")
            + f" \u2014 {len(chapter.parts)} Part(s)."
        )
        if not chapter.recording_url:
            return len(items), "This chapter has no recording video URL."
        try:
            video_id = youtube_id(chapter.recording_url)
        except JobError as exc:
            return len(items), str(exc)

        job.set(stage="reading recording metadata")
        try:
            meta = boundaries.fetch_video_meta(video_id)
            bounds = boundaries.derive_boundaries(chapter.parts, meta)
        except Exception as exc:
            return len(items), f"Could not derive Part boundaries: {exc}"
        job.log(
            f"Recording {video_id}: {meta.duration:.0f}s, "
            f"{len(meta.chapters)} YouTube chapters, {len(bounds)} Part boundaries."
        )
        bound_by_key = {item.key: item for item in bounds}
        job.log(
            "Part boundaries: "
            + ", ".join(
                f"{item.number}:{item.start:.0f}-{item.end:.0f}s" for item in bounds
            )
            + "."
        )

        job.set(stage="acquiring video")
        max_height = media.normalize_height(
            job.options.get("max_height") or config.VIDEO_MAX_HEIGHT
        )
        job.log(f"Target video resolution: up to {max_height}p.")
        try:
            video = media.ensure_video(
                video_id,
                expected_duration=meta.duration,
                max_height=max_height,
                progress=lambda message: job.log(message),
                cancel_check=lambda: job.cancel_requested,
                force=bool(job.options.get("force_media")),
            )
        except media.MediaCancelled:
            job.log("Cancelled while acquiring the recording.", "warn")
            return 0, None
        except Exception as exc:
            return len(items), f"Could not acquire the recording: {exc}"

        job.set(stage="acquiring English timing anchors")
        words = None
        asr_source = ""
        for attempt in (1, 2):
            try:
                words, asr_source = asr.get_words(video_id, force=attempt == 2)
                job.log(f"English ASR: {len(words)} word timings from {Path(asr_source).name}.")
                break
            except Exception as exc:
                job.log(f"Caption attempt {attempt} failed: {exc}", "warn")
                if attempt == 2:
                    job.warnings.append(f"{path}: no caption track ({exc}); will try Whisper per Part.")
        if words is None:
            asr_source = "whisper-fallback"

        if job.cancel_requested:
            job.log("Cancelled before Part processing.", "warn")
            return 0, None

        processed = 0
        for item in items:
            if job.cancel_requested:
                break
            number = int(item["number"])
            part = chapter.part(number)
            if part is None:
                job.error(f"Part {number} is not present in this chapter.", stage="select", path=path, part=number)
                processed += 1
                job.set(progress=(done + processed) / max(1, total) * 100.0)
                continue
            boundary = bound_by_key.get(part.key)
            if boundary is None:
                job.error(f"Part {part.number} has no derived boundary.", stage="boundary", path=path, part=part.number)
                processed += 1
                job.set(progress=(done + processed) / max(1, total) * 100.0)
                continue
            job.set(stage=f"part {part.number}: {part.title_en}")
            job.log(f"--- Part {part.number}: {part.title_en} / {part.title_zh} ---")
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    output = self._process_part(
                        job, chapter, part, boundary, video, words, asr_source, attempt=attempt
                    )
                    job.outputs.append(output)
                    job.log(
                        f"Part {part.number} published: {output['cues']} cues, "
                        f"{output['duration']:.1f}s video."
                    )
                    last_exc = None
                    break
                except media.MediaCancelled:
                    job.log(f"Part {part.number}: cancelled during encoding.", "warn")
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt == 1:
                        job.log(f"Part {part.number} attempt 1 failed: {exc}; retrying.", "warn")
                        time.sleep(1.0)
                    else:
                        job.error(
                            f"Part {part.number} failed after retry: {exc}",
                            stage=getattr(exc, "stage", "part"),
                            path=path,
                            part=part.number,
                        )
            if last_exc is not None:
                log.warning("Part %s failed: %s", part.number, last_exc)
            processed += 1
            job.set(progress=(done + processed) / max(1, total) * 100.0)
        return processed, None

    def _process_part(
        self,
        job: Job,
        chapter: Chapter,
        part: Part,
        boundary: boundaries.Boundary,
        video: Path,
        words,
        asr_source: str,
        *,
        attempt: int,
    ) -> dict[str, Any]:
        if part.gameplay_only:
            raise PartError("This Part is marked gameplay-only on the site.", stage="transcript")
        if not part.content_en or not part.content_zh:
            raise PartError("This Part is missing one of the two transcripts.", stage="transcript")
        if part.number != boundary.number:
            raise PartError("Part/boundary number mismatch.", stage="boundary")

        final_dir = part_dir(chapter, part)
        max_height = media.normalize_height(
            job.options.get("max_height") or config.VIDEO_MAX_HEIGHT
        )
        replace_existing = bool(job.options.get("force"))
        if final_dir.exists() and not replace_existing:
            existing = self._load_existing_part(chapter, part, final_dir, boundary, max_height)
            if existing is not None:
                job.log(
                    f"Part {part.number}: existing output is valid; reusing "
                    f"{final_dir.relative_to(config.OUTPUT_DIR).as_posix()}.",
                    "warn",
                )
                return existing
            job.log(
                f"Part {part.number}: existing output is incomplete/invalid; replacing it.",
                "warn",
            )
            replace_existing = True

        job.log(f"Pairing transcripts for Part {part.number} ...")
        lines, transcript_warnings = transcript.pair_part_transcripts(
            part.number, part.content_en, part.content_zh
        )
        job.log(f"Paired {len(lines)} bilingual lines ({transcript_warnings or 'no warnings'}).")

        job.log("Loading voice-clip durations...")
        durations = voices.ensure_durations([line.voice for line in lines])
        missing = sum(1 for value in durations.values() if not value)
        job.log(
            f"Voice durations: {len(durations) - missing}/{len(durations)} available."
        )
        if missing:
            job.log(f"{missing}/{len(durations)} voice durations unavailable; using text estimates.", "warn")

        job.log(f"Aligning Part {part.number} to the recording...")
        if words is None:
            job.log("No caption words; extracting this Part's audio for Whisper...", "warn")
            audio = config.CACHE_DIR / "work" / f"{job.id}_{part.number}_{attempt}.wav"
            try:
                media.extract_audio_segment(video, boundary.start - 0.5, boundary.end + 0.5, audio)
                from . import whisper_fallback

                words = whisper_fallback.transcribe_segment(audio, max(0.0, boundary.start - 0.5))
            finally:
                audio.unlink(missing_ok=True)
        cues, alignment = timing.align_part(
            lines, words, boundary.start, boundary.end, durations
        )
        job.log(
            f"Aligned {len(cues)} cues: ASR token coverage "
            f"{alignment.match_ratio:.1%}, line match {alignment.line_match_ratio:.1%}, "
            f"mean confidence {alignment.mean_confidence:.1%}."
        )
        validation = timing.validate_cues(cues, boundary.duration, alignment)
        if not validation["ok"]:
            raise PartError(
                "Subtitle validation failed: " + "; ".join(validation["errors"]),
                stage="validation",
            )
        for warning in validation["warnings"]:
            job.log(f"Part {part.number}: {warning}", "warn")

        # The next Part's chapter marker is the safe outer bound.  A recording
        # can put tutorials/story-trails *inside* that span, and the site
        # transcript may continue through them; therefore trim after the last
        # actual subtitle cue instead of trusting an intermediate chapter label.
        if cues:
            tail = max(2.0, min(8.0, boundary.duration * 0.02))
            last_absolute = boundary.start + cues[-1].end
            if boundary.first_extra_start is None:
                # No unrelated chapter between this Part and the next one:
                # keep the full span, including gameplay after the last line.
                target_end = boundary.end
            elif last_absolute < boundary.first_extra_start:
                # The story ends before an unrelated chapter; keep gameplay up
                # to that chapter, then stop.
                target_end = boundary.first_extra_start
            else:
                # The transcript continues into a chapter labelled as an
                # "extra" (e.g. Summon Tutorial).  Follow the dialogue and
                # stop just after its last cue.
                target_end = last_absolute + tail
            trimmed_end = min(boundary.end, max(boundary.start + 15.0, target_end))
            if boundary.end - trimmed_end > 0.5:
                job.log(
                    f"Part {part.number}: trimming tail "
                    f"{trimmed_end:.1f}-{boundary.end:.1f}s "
                    f"(last cue at {last_absolute:.1f}s)."
                )
                boundary.end = trimmed_end
                validation = timing.validate_cues(cues, boundary.duration, alignment)
                if not validation["ok"]:
                    raise PartError(
                        "Subtitle validation failed after tail trim: "
                        + "; ".join(validation["errors"]),
                        stage="validation",
                    )

        en_text = timing.format_srt(cues, bilingual=False)
        zh_text = timing.format_srt_zh(cues)
        bilingual_text = timing.format_srt(cues, bilingual=True)
        if not en_text.strip() or not zh_text.strip() or not bilingual_text.strip():
            raise PartError("Subtitle generation produced an empty file.", stage="subtitle")

        staging_root = config.OUTPUT_DIR / ".staging" / job.id
        staging = staging_root / f"{chapter.slug}-{part.key}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            job.set(stage=f"part {part.number}: splitting video")
            segment = media.split_video(
                video,
                boundary.start,
                boundary.end,
                staging / "video.mp4",
                force=True,
                progress=lambda message: job.log(f"Part {part.number}: {message}"),
                cancel_check=lambda: job.cancel_requested,
            )
            if not segment["has_video"] or not segment["has_audio"]:
                raise PartError("Split segment is missing a video or audio stream.", stage="video")
            job.log(
                f"Encoded segment: {segment['duration']:.1f}s, "
                f"{segment.get('size', 0) / 1e6:.1f} MB, "
                f"{_stream_value(segment, 'video', 'width')}x"
                f"{_stream_value(segment, 'video', 'height')}."
            )

            subtitles.write_text_atomic(staging / "en.srt", en_text)
            subtitles.write_text_atomic(staging / "zh.srt", zh_text)
            subtitles.write_text_atomic(staging / "bilingual.srt", bilingual_text)
            srt_en = subtitles.validate_srt_file(
                staging / "en.srt", expected_cues=len(cues), duration=segment["duration"]
            )
            srt_zh = subtitles.validate_srt_file(
                staging / "zh.srt", expected_cues=len(cues), duration=segment["duration"]
            )
            srt_bi = subtitles.validate_srt_file(
                staging / "bilingual.srt",
                expected_cues=len(cues),
                duration=segment["duration"],
                bilingual=True,
            )
            job.log(
                f"SRT validated: EN/ZH/bilingual {len(cues)} cues each; "
                f"last cue {cues[-1].end:.1f}s / video {segment['duration']:.1f}s."
            )

            part_info = {
                "generated_at": _now_iso(),
                "tool_version": __import__("uttu").__version__,
                "requested_height": max_height,
                "source": {
                    "chapter_path": chapter.path,
                    "recording": chapter.recording_url,
                    "asr_source": asr_source,
                    "requested_height": max_height,
                },
                "chapter": {
                    "title_en": chapter.title_en,
                    "title_zh": chapter.title_zh,
                    "label": chapter.label,
                },
                "part": {
                    "number": part.number,
                    "text_number": part.text_number,
                    "title_en": part.title_en,
                    "title_zh": part.title_zh,
                    "voice_lines": len(lines),
                    "word_count": part.word_count,
                },
                "boundary": boundary.to_dict(),
                "video": {
                    "file": "video.mp4",
                    "duration": round(segment["duration"], 3),
                    "width": _stream_value(segment, "video", "width"),
                    "height": _stream_value(segment, "video", "height"),
                    "size": segment.get("size", 0),
                },
                "subtitles": {
                    "en": {**srt_en, "file": "en.srt"},
                    "zh": {**srt_zh, "file": "zh.srt"},
                    "bilingual": {**srt_bi, "file": "bilingual.srt"},
                    "alignment": alignment.to_dict(),
                    "validation": validation,
                    "transcript_warnings": transcript_warnings,
                },
                "warnings": list(validation["warnings"]) + list(transcript_warnings),
            }
            _write_json_atomic(staging / "part.json", part_info)
            if job.cancel_requested:
                raise media.MediaCancelled("cancelled before publishing Part")

            final_dir = part_dir(chapter, part)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            force = bool(job.options.get("force")) or replace_existing
            if final_dir.exists() and not force:
                job.log(f"Part {part.number}: output exists, keeping it ({final_dir}).", "warn")
                shutil.rmtree(staging, ignore_errors=True)
                return self._output_entry(chapter, part, boundary, final_dir, part_info, reused=True)

            backup = final_dir.with_name(final_dir.name + f".old-{job.id}")
            if final_dir.exists():
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                os.replace(final_dir, backup)
            try:
                os.replace(staging, final_dir)
            except Exception:
                if backup.exists() and not final_dir.exists():
                    os.replace(backup, final_dir)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            outputs.invalidate()
            job.log(
                "Published to "
                f"{final_dir.relative_to(config.OUTPUT_DIR).as_posix()}."
            )
            return self._output_entry(chapter, part, boundary, final_dir, part_info, reused=False)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            try:
                if staging_root.exists() and not any(staging_root.iterdir()):
                    staging_root.rmdir()
            except OSError:
                pass

    def _load_existing_part(
        self,
        chapter: Chapter,
        part: Part,
        final_dir: Path,
        boundary: boundaries.Boundary,
        max_height: int,
    ) -> dict[str, Any] | None:
        """Cheaply verify a previously published Part before reusing it."""
        try:
            info = json.loads((final_dir / "part.json").read_text(encoding="utf-8"))
            stored_requested = int(
                info.get("requested_height")
                or (info.get("video") or {}).get("height")
                or 0
            )
            if stored_requested and stored_requested != max_height:
                return None
            for name in ("video.mp4", "en.srt", "zh.srt", "bilingual.srt"):
                if not (final_dir / name).is_file() or (final_dir / name).stat().st_size < 100:
                    return None
            video_info = media.probe_media(final_dir / "video.mp4")
            if not video_info or not video_info["has_video"] or not video_info["has_audio"]:
                return None
            cues = int(info.get("subtitles", {}).get("en", {}).get("cues", 0) or 0)
            if cues <= 0:
                return None
            parsed_sets = {}
            for name in ("en.srt", "zh.srt", "bilingual.srt"):
                parsed = subtitles.parse_srt(
                    (final_dir / name).read_text(encoding="utf-8-sig")
                )
                if len(parsed) != cues:
                    return None
                parsed_sets[name] = parsed
            for en, zh, bi in zip(
                parsed_sets["en.srt"], parsed_sets["zh.srt"], parsed_sets["bilingual.srt"]
            ):
                if abs(en.start - zh.start) > 0.001 or abs(en.end - bi.end) > 0.001:
                    return None
                if len(bi.lines) < 2 or bi.lines[0].strip() != en.text.strip():
                    return None
                if bi.lines[1].strip() != zh.text.strip():
                    return None
            if abs(video_info["duration"] - float(info["video"]["duration"])) > 1.5:
                return None
            if (info.get("source") or {}).get("recording") != chapter.recording_url:
                return None
            existing_boundary = info.get("boundary") or {}
            if (
                "start" in existing_boundary
                and abs(float(existing_boundary["start"]) - boundary.start) > 0.01
            ):
                return None
            last_absolute = boundary.start + parsed_sets["en.srt"][-1].end
            tail = max(2.0, min(8.0, boundary.duration * 0.02))
            if boundary.first_extra_start is None:
                expected_end = boundary.end
            elif last_absolute < boundary.first_extra_start:
                expected_end = boundary.first_extra_start
            else:
                expected_end = last_absolute + tail
            expected_end = min(boundary.end, max(boundary.start + 15.0, expected_end))
            if "end" in existing_boundary and abs(
                float(existing_boundary["end"]) - expected_end
            ) > 0.75:
                return None
            return self._output_entry(chapter, part, None, final_dir, info, reused=True)
        except (OSError, ValueError, KeyError, TypeError, subtitles.SrtError) as exc:
            log.warning("Existing output %s is not reusable: %s", final_dir, exc)
            return None

    def _output_entry(
        self,
        chapter: Chapter,
        part: Part,
        boundary: boundaries.Boundary | None,
        final_dir: Path,
        part_info: dict[str, Any],
        *,
        reused: bool,
    ) -> dict[str, Any]:
        relative = final_dir.relative_to(config.OUTPUT_DIR).as_posix()
        return {
            "path": chapter.path,
            "number": part.number,
            "key": part.key,
            "title_en": part.title_en,
            "title_zh": part.title_zh,
            "dir": relative,
            "files": {
                "video": f"{relative}/video.mp4",
                "en_srt": f"{relative}/en.srt",
                "zh_srt": f"{relative}/zh.srt",
                "bilingual_srt": f"{relative}/bilingual.srt",
                "metadata": f"{relative}/part.json",
            },
            "cues": part_info["subtitles"]["en"]["cues"],
            "duration": part_info["boundary"]["duration"],
            "video_duration": part_info["video"]["duration"],
            "height": part_info["video"].get("height"),
            "requested_height": part_info.get("requested_height"),
            "match_ratio": part_info["subtitles"]["alignment"]["match_ratio"],
            "line_match_ratio": part_info["subtitles"]["alignment"]["line_match_ratio"],
            "reused": reused,
            "warnings": part_info["warnings"],
        }

    def _write_reports(self, job: Job) -> None:
        reports = config.OUTPUT_DIR / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        json_path = reports / f"{job.id}.json"
        job.set(report_path=str(json_path.relative_to(config.OUTPUT_DIR).as_posix()))
        snapshot = job.snapshot()
        _write_json_atomic(json_path, snapshot)
        latest = reports / "latest.json"
        _write_json_atomic(latest, snapshot)
        md_path = reports / f"{job.id}.md"
        md_path.write_text(self._report_markdown(job), encoding="utf-8")

    def _report_markdown(self, job: Job) -> str:
        lines = [
            f"# UTTU extraction report `{job.id}`",
            "",
            f"- Status: **{job.status}**",
            f"- Started: {datetime.fromtimestamp(job.started_at or job.created_at).isoformat(timespec='seconds')}",
            f"- Finished: {datetime.fromtimestamp(job.finished_at).isoformat(timespec='seconds') if job.finished_at else '-'}",
            f"- Target resolution: up to {job.options.get('max_height', config.VIDEO_MAX_HEIGHT)}p",
            f"- Published Parts: {len(job.outputs)}",
            f"- Errors: {len(job.errors)}",
            "",
            "## Parts",
            "",
            "| Part | Title | Resolution | Cues | Video | Match | Files |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for item in job.outputs:
            lines.append(
                f"| {item['number']} | {item['title_en']} | "
                f"{item.get('height') or '-'}p | {item['cues']} | "
                f"{item['video_duration']:.1f}s | {item['match_ratio']:.1%} | "
                f"`{item['dir']}` |"
            )
        if job.errors:
            lines += ["", "## Errors", ""]
            for error in job.errors:
                lines.append(
                    f"- **{error['stage']}** `Part {error.get('part')}`: {error['message']}"
                )
        if job.warnings:
            lines += ["", "## Warnings", ""]
            for warning in job.warnings:
                lines.append(f"- {warning}")
        lines += ["", "## Log", "", "```"]
        lines += [f"[{item['time']}] {item['message']}" for item in job.logs[-200:]]
        lines += ["```", ""]
        return "\n".join(lines)


def _stream_value(segment: dict[str, Any], kind: str, key: str):
    for stream in segment.get("streams", []):
        if stream.get("codec_type") == kind:
            return stream.get(key)
    return None


manager = JobManager()


