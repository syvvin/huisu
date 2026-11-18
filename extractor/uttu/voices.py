"""Voice-clip durations.

The site's voice IDs point at the exact per-line audio on voice.merui.net.
We do not need the clips themselves, only their durations: they keep subtitle
cue lengths honest and let the aligner place a line that ASR happened to miss.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_durations: dict[str, float | None] | None = None


def _cache_file() -> Path:
    path = config.CACHE_DIR / "voices" / "durations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _clip_file(voice_id: str) -> Path:
    digest = hashlib.sha1(voice_id.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", voice_id)[-40:]
    return config.CACHE_DIR / "voices" / f"{digest}_{safe}.ogg"


def _load_cache() -> dict[str, float | None]:
    global _durations
    if _durations is not None:
        return _durations
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
        _durations = {str(k): (float(v) if v else None) for k, v in data.items()}
    except (OSError, ValueError):
        _durations = {}
    return _durations


def _save_cache() -> None:
    if _durations is None:
        return
    path = _cache_file()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_durations, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(path)


def _probe_duration(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                config.find_ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=config.NO_WINDOW,
        )
        value = float((proc.stdout or "").strip())
        return value if value > 0 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _fetch_one(voice_id: str) -> tuple[str, float | None]:
    clip = _clip_file(voice_id)
    if clip.exists() and clip.stat().st_size > 100:
        duration = _probe_duration(clip)
        if duration:
            return voice_id, duration
        clip.unlink(missing_ok=True)

    url = f"{config.VOICE_BASE_URL}/story/en/{voice_id}.ogg"
    last_error = ""
    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": config.USER_AGENT, "Referer": config.BASE_URL + "/"},
                timeout=config.HTTP_TIMEOUT,
            )
            if response.status_code == 404:
                return voice_id, None
            response.raise_for_status()
            clip.parent.mkdir(parents=True, exist_ok=True)
            tmp = clip.with_name(clip.name + ".part")
            tmp.write_bytes(response.content)
            tmp.replace(clip)
            return voice_id, _probe_duration(clip)
        except (requests.RequestException, OSError) as exc:
            last_error = str(exc)
            log.debug("Voice %s attempt %s failed: %s", voice_id, attempt, exc)
    log.warning("Could not download voice clip %s: %s", voice_id, last_error)
    return voice_id, None


def ensure_durations(voice_ids: list[str], *, refresh: bool = False) -> dict[str, float | None]:
    cache = _load_cache()
    missing = sorted({vid for vid in voice_ids if refresh or vid not in cache})
    if not missing:
        return {vid: cache.get(vid) for vid in voice_ids}

    log.info("Fetching %s voice-clip durations...", len(missing))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_one, vid): vid for vid in missing}
        for future in as_completed(futures):
            vid, duration = future.result()
            with _lock:
                cache[vid] = duration
    with _lock:
        global _durations
        _durations = cache
        _save_cache()
    return {vid: cache.get(vid) for vid in voice_ids}
