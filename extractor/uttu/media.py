"""Video acquisition, probing and exact Part splitting."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
CancelCheck = Callable[[], bool]


class MediaError(RuntimeError):
    """A video could not be downloaded, validated or split."""


class MediaCancelled(MediaError):
    """The user cancelled during a long-running media operation."""


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=config.NO_WINDOW,
    )


def _kill_process(proc: subprocess.Popen) -> None:
    """Terminate a child and its descendants (yt-dlp may spawn ffmpeg)."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
                creationflags=config.NO_WINDOW,
            )
        else:
            proc.kill()
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _start_cancel_watch(
    proc: subprocess.Popen, cancel_check: CancelCheck | None
) -> threading.Event | None:
    """Kill a child process promptly even if it is quiet for a while."""
    if cancel_check is None:
        return None
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.25):
            if proc.poll() is not None:
                return
            try:
                cancelled = cancel_check()
            except Exception:
                return
            if cancelled:
                _kill_process(proc)
                return

    threading.Thread(target=watch, daemon=True).start()
    return stop


def probe_media(path: Path) -> dict | None:
    try:
        proc = _run(
            [
                config.find_ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,bit_rate",
                "-show_entries",
                "stream=index,codec_name,codec_type,width,height,channels",
                "-of",
                "json",
                str(path),
            ],
            timeout=60,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        duration = float(data.get("format", {}).get("duration") or 0)
        streams = data.get("streams") or []
        if duration <= 0 or not streams:
            return None
        return {
            "duration": duration,
            "size": int(data.get("format", {}).get("size") or 0),
            "streams": streams,
            "has_video": any(item.get("codec_type") == "video" for item in streams),
            "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        }
    except (OSError, ValueError, subprocess.SubprocessError, TypeError):
        return None


def normalize_height(value) -> int:
    try:
        height = int(value)
    except (TypeError, ValueError):
        height = config.VIDEO_MAX_HEIGHT
    return max(360, min(4320, height))


def video_cache_path(video_id: str, max_height: int = config.VIDEO_MAX_HEIGHT) -> Path:
    return config.CACHE_DIR / "media" / f"{video_id}_{normalize_height(max_height)}p.mkv"


def _video_height(info: dict | None) -> int | None:
    if not info:
        return None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                return int(stream.get("height") or 0) or None
            except (TypeError, ValueError):
                return None
    return None


def _candidate_video_paths(video_id: str, max_height: int) -> list[Path]:
    base = config.CACHE_DIR / "media" / f"{video_id}_{max_height}p"
    paths = [base.with_suffix(ext) for ext in (".mkv", ".mp4", ".webm")]
    # Legacy 720p downloads used <id>.mp4.
    paths.append(config.CACHE_DIR / "media" / f"{video_id}.mp4")
    # Standalone extractor cache next to the configured output directory.
    for extra in config.EXTRA_MEDIA_DIRS:
        paths.extend(extra / f"{video_id}_{max_height}p{ext}" for ext in (".mkv", ".mp4", ".webm"))
        paths.append(extra / f"{video_id}.mp4")
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _reusable_video(path: Path, max_height: int, expected_duration: float) -> tuple[Path, dict] | None:
    if not path.exists():
        return None
    info = probe_media(path)
    if not info or not info["has_video"] or not info["has_audio"]:
        return None
    height = _video_height(info)
    if height and height > max_height + 1:
        return None
    if expected_duration > 0 and abs(info["duration"] - expected_duration) > max(
        10.0, expected_duration * 0.02
    ):
        return None
    return path, info


def ensure_video(
    video_id: str,
    *,
    expected_duration: float,
    max_height: int = config.VIDEO_MAX_HEIGHT,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
    force: bool = False,
) -> Path:
    if cancel_check and cancel_check():
        raise MediaCancelled("cancelled before video acquisition")
    max_height = normalize_height(max_height)
    base = config.CACHE_DIR / "media" / f"{video_id}_{max_height}p"
    base.parent.mkdir(parents=True, exist_ok=True)

    if not force:
        for candidate in _candidate_video_paths(video_id, max_height):
            reusable = _reusable_video(candidate, max_height, expected_duration)
            if reusable is not None:
                path, info = reusable
                if path.name == f"{video_id}.mp4":
                    # Legacy cache has no resolution in its name; only reuse
                    # it when the user asked for exactly that resolution.
                    if _video_height(info) != max_height:
                        continue
                height = _video_height(info)
                primary_dir = (config.CACHE_DIR / "media").resolve()
                try:
                    outside_primary = path.resolve().parent != primary_dir
                except OSError:
                    outside_primary = False
                if outside_primary:
                    # Keep the app cache self-contained without copying multi-GB
                    # recordings. NTFS hardlinks share the same bytes.
                    target = base.with_suffix(path.suffix)
                    try:
                        if target.exists():
                            target.unlink()
                        os.link(path, target)
                        log.info("Linked cached recording %s -> %s", path, target)
                        path = target
                    except OSError as exc:
                        log.warning("Could not hardlink cached recording %s: %s", path, exc)
                if progress:
                    progress(
                        f"Reusing cached video {path.name} "
                        f"({info['duration']:.0f}s, {height or '?'}p)"
                    )
                return path
            if candidate.exists():
                log.warning("Cached video %s is unusable or the wrong resolution.", candidate)
                if candidate.name.startswith(base.name + "."):
                    candidate.unlink(missing_ok=True)

    if progress:
        progress(f"Downloading recording {video_id} (up to {max_height}p)...")
    cmd = [
        *config.find_ytdlp(),
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--retries",
        "6",
        "--fragment-retries",
        "8",
        "--continue",
        "--merge-output-format",
        "mkv",
        "-f",
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "-S",
        f"res:{max_height},codec:h264,fps",
        "-o",
        str(base) + ".%(ext)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    if force:
        cmd.insert(1, "--force-overwrites")

    last_tail = ""
    for attempt in range(1, 3):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=config.NO_WINDOW,
        )
        tail: list[str] = []
        last_percent = -1
        watch_stop = _start_cancel_watch(proc, cancel_check)
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_check and cancel_check():
                _kill_process(proc)
                proc.wait()
                raise MediaCancelled("cancelled while downloading")
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-12:]
            if progress and ("[download]" in line or "[Merger]" in line):
                match = re.search(r"(\d+(?:\.\d+)?%)", line)
                if match:
                    percent = int(float(match.group(1).rstrip("%")))
                    if percent < last_percent:
                        last_percent = -1
                    if percent == 100 or percent >= last_percent + 5:
                        progress(f"Downloading: {percent}%")
                        last_percent = percent
                elif "Merger" in line:
                    progress("Merging audio and video...")
        if watch_stop:
            watch_stop.set()
        code = proc.wait()
        if cancel_check and cancel_check():
            raise MediaCancelled("cancelled while downloading")
        last_tail = " | ".join(tail[-6:])
        if code == 0:
            break
        log.warning("yt-dlp download attempt %s failed: %s", attempt, last_tail)
        if attempt == 1:
            time.sleep(2.0)

    downloaded: list[Path] = []
    for ext in (".mkv", ".mp4", ".webm"):
        candidate = base.with_suffix(ext)
        if candidate.exists():
            downloaded.append(candidate)
    downloaded.extend(
        path for path in base.parent.glob(base.name + ".*") if path not in downloaded
    )
    downloaded = [path for path in downloaded if path.suffix != ".part"]
    for candidate in sorted(downloaded, key=lambda item: item.stat().st_size, reverse=True):
        reusable = _reusable_video(candidate, max_height, expected_duration)
        if reusable is None:
            continue
        path, info = reusable
        height = _video_height(info)
        if progress:
            progress(
                f"Video ready: {height or '?'}p, {info['duration']:.0f}s, "
                f"{info['size'] / 1e6:.0f} MB"
            )
        return path

    raise MediaError(
        f"Could not obtain a usable video for {video_id} at up to {max_height}p: {last_tail}"
    )


def _encode_segment(
    src: Path,
    dst: Path,
    start: float,
    duration: float,
    codec: str,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> subprocess.CompletedProcess:
    if codec == "nvenc":
        video_args = [
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
            "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    else:
        video_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
    cmd = [
        config.find_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        *video_args,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-avoid_negative_ts",
        "make_zero",
        "-y",
        str(dst),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=config.NO_WINDOW,
    )
    last_pct = -10
    deadline = time.monotonic() + max(900.0, duration * 12.0)
    watch_stop = _start_cancel_watch(proc, cancel_check)
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel_check and cancel_check():
            _kill_process(proc)
            proc.wait()
            raise MediaCancelled("cancelled while encoding")
        if time.monotonic() > deadline:
            _kill_process(proc)
            break
        line = line.strip()
        if line.startswith("out_time_ms=") and duration > 0 and progress is not None:
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000.0
            except (TypeError, ValueError):
                continue
            pct = max(0, min(99, int(seconds / duration * 100)))
            if pct >= last_pct + 5:
                last_pct = pct
                progress(f"Encoding {codec}: {pct}%")
        elif line == "progress=end" and progress is not None:
            progress(f"Encoding {codec}: 100%")
    if watch_stop:
        watch_stop.set()
    stderr = proc.stderr.read() if proc.stderr is not None else ""
    code = proc.wait()
    if cancel_check and cancel_check():
        raise MediaCancelled("cancelled while encoding")
    return subprocess.CompletedProcess(cmd, code, "", stderr)


def split_video(
    src: Path,
    start: float,
    end: float,
    dst: Path,
    *,
    force: bool = False,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    if cancel_check and cancel_check():
        raise MediaCancelled("cancelled before encoding")
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    if duration <= 0:
        raise MediaError(f"Invalid segment {start}-{end}")
    if dst.exists() and not force:
        info = probe_media(dst)
        if info and abs(info["duration"] - duration) <= max(1.0, duration * 0.01):
            return info

    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    tmp.unlink(missing_ok=True)
    last_error = ""
    for codec in ("nvenc", "x264"):
        if progress:
            progress(f"Encoding segment {start:.1f}-{end:.1f}s ({codec})...")
        try:
            proc = _encode_segment(
                src, tmp, start, duration, codec,
                progress=progress, cancel_check=cancel_check,
            )
        except MediaCancelled:
            tmp.unlink(missing_ok=True)
            raise
        except subprocess.SubprocessError as exc:
            last_error = str(exc)
            tmp.unlink(missing_ok=True)
            continue
        if proc.returncode == 0 and tmp.exists():
            info = probe_media(tmp)
            if info and abs(info["duration"] - duration) <= max(1.0, duration * 0.01):
                if cancel_check and cancel_check():
                    tmp.unlink(missing_ok=True)
                    raise MediaCancelled("cancelled before publishing segment")
                os.replace(tmp, dst)
                return info
            last_error = f"encoded duration was {info['duration'] if info else 'unknown'}s"
        else:
            last_error = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [
                f"ffmpeg returned {proc.returncode}"
            ]
            last_error = " | ".join(last_error) if isinstance(last_error, list) else str(last_error)
        tmp.unlink(missing_ok=True)
    raise MediaError(f"Could not split {src.name} at {start:.1f}-{end:.1f}s: {last_error}")


def extract_audio_segment(src: Path, start: float, end: float, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [
            config.find_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{max(0.1, end - start):.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(dst),
        ],
        timeout=max(120.0, (end - start) * 3.0),
    )
    if proc.returncode != 0 or not dst.exists():
        raise MediaError(f"Could not extract audio: {(proc.stderr or '').strip()[-300:]}")
    return dst
