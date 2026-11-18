"""Paths, endpoints and tunables.

Everything is overridable with environment variables so the tool can be run
from another directory without editing code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_ROOT.parent

BASE_URL = os.environ.get("UTTU_BASE_URL", "https://uttu.merui.net").rstrip("/")
VOICE_BASE_URL = os.environ.get("UTTU_VOICE_BASE_URL", "https://voice.merui.net").rstrip("/")

DATA_DIR = Path(os.environ.get("UTTU_DATA_DIR", APP_ROOT / "data")).resolve()
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = Path(os.environ.get("UTTU_OUTPUT_DIR", APP_ROOT / "output")).resolve()
EXTRA_MEDIA_DIRS = tuple(
    Path(item).resolve()
    for item in os.environ.get("UTTU_EXTRA_MEDIA_DIRS", "").split(os.pathsep)
    if item.strip()
)
WEB_DIR = APP_ROOT / "web"

USER_AGENT = os.environ.get(
    "UTTU_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

HTTP_TIMEOUT = (10, 40)
HTTP_RETRIES = 4
MIN_REQUEST_INTERVAL = 0.12  # seconds between requests, per process

CHAPTER_CACHE_TTL = 7 * 24 * 3600
TREE_CACHE_TTL = 24 * 3600
YT_CACHE_TTL = 14 * 24 * 3600

VIDEO_MAX_HEIGHT = int(os.environ.get("UTTU_VIDEO_HEIGHT", "1080"))
VIDEO_HEIGHT_CHOICES = (720, 1080, 1440, 2160)

# Keep console child processes invisible when running inside the windowed app.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Chapter/part parse sanity limits.
MIN_PARTS_PER_CHAPTER = 1
MAX_PARTS_PER_CHAPTER = 100
MIN_PART_CONTENT = 40


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError(
            "ffmpeg was not found on PATH. Install ffmpeg and make sure "
            "`ffmpeg -version` works."
        )
    return found


def find_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if not found:
        raise RuntimeError(
            "ffprobe was not found on PATH. It ships with ffmpeg."
        )
    return found


def find_ytdlp() -> list[str]:
    """Return a command prefix for yt-dlp.

    Prefer the executable, then the Python module so a venv install works
    even when its scripts directory is not on PATH.
    """
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    try:
        import yt_dlp  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "yt-dlp was not found. Install it with `pip install yt-dlp` "
            "or put the yt-dlp executable on PATH."
        ) from exc
    import sys

    return [sys.executable, "-m", "yt_dlp"]
