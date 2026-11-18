"""Run the bundled UTTU extractor web UI beside the vocab app.

The extractor is copied into ``extractor/``. It always writes into the same
library root the vocab app scans, so a finished extraction only needs a normal
library refresh.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import config, db

HOST = "127.0.0.1"
PORT = 8765
_URL = f"http://{HOST}:{PORT}/"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_error = ""


def _port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{_URL}api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


_COMPLETE_SUFFIXES = {".mkv", ".mp4", ".webm"}


def _library_root() -> Path:
    return Path(db.get_setting("library_root", "") or config.DEFAULT_LIBRARY_ROOT).expanduser()


def _legacy_media_dirs() -> list[Path]:
    """Old standalone extractor cache next to the configured output directory."""
    root = _library_root()
    candidates = [root.parent / "data" / "cache" / "media", root.parent / "cache" / "media"]
    result: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in result:
            result.append(resolved)
    return result


def _scan_media(directory: Path) -> tuple[list[Path], list[Path]]:
    complete: list[Path] = []
    partial: list[Path] = []
    if not directory.is_dir():
        return complete, partial
    for path in directory.iterdir():
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.name.endswith(".part"):
            partial.append(path)
        elif path.suffix.lower() in _COMPLETE_SUFFIXES:
            complete.append(path)
    return complete, partial


def cache_info() -> dict[str, Any]:
    primary = config.EXTRACTOR_DATA_DIR / "cache" / "media"
    primary_complete, primary_partial = _scan_media(primary)
    primary_names = {path.name for path in primary_complete + primary_partial}
    legacy_complete: list[Path] = []
    legacy_partial: list[Path] = []
    for directory in _legacy_media_dirs():
        complete, partial = _scan_media(directory)
        for path in complete:
            if path.name not in primary_names:
                primary_names.add(path.name)
                legacy_complete.append(path)
        for path in partial:
            if path.name not in primary_names:
                primary_names.add(path.name)
                legacy_partial.append(path)

    def size(paths: list[Path]) -> int:
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    primary_bytes = size(primary_complete + primary_partial)
    legacy_bytes = size(legacy_complete + legacy_partial)
    legacy_dirs = _legacy_media_dirs()
    return {
        "ok": True,
        "path": str(primary),
        "primary_bytes": primary_bytes,
        "legacy_bytes": legacy_bytes,
        "legacy_path": str(legacy_dirs[0]) if legacy_dirs else "",
        "total_bytes": primary_bytes + legacy_bytes,
        "videos": len(primary_complete) + len(legacy_complete),
        "partials": len(primary_partial) + len(legacy_partial),
        "files": len(primary_complete) + len(primary_partial) + len(legacy_complete) + len(legacy_partial),
    }


def clear_cache() -> dict[str, Any]:
    """Remove the app-owned source cache. Legacy standalone cache is untouched."""
    primary = config.EXTRACTOR_DATA_DIR / "cache" / "media"
    complete, partial = _scan_media(primary)
    removed = 0
    freed = 0
    hardlinked = 0
    skipped = 0
    errors: list[str] = []
    now = time.time()
    for path in complete + partial:
        try:
            stat = path.stat()
            if now - stat.st_mtime < 300:
                skipped += 1
                continue
            amount = stat.st_size
            path.unlink()
            removed += 1
            if stat.st_nlink > 1:
                hardlinked += amount
            else:
                freed += amount
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    return {
        "ok": True,
        "removed": removed,
        "freed": freed,
        "hardlinked_bytes": hardlinked,
        "skipped": skipped,
        "errors": errors[:8],
        "path": str(primary),
    }


def _prepare_env() -> Path:
    library_root = Path(db.get_setting("library_root", "") or config.DEFAULT_LIBRARY_ROOT).expanduser()
    library_root.mkdir(parents=True, exist_ok=True)
    data_dir = config.EXTRACTOR_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["UTTU_OUTPUT_DIR"] = str(library_root)
    os.environ["UTTU_DATA_DIR"] = str(data_dir)
    os.environ["UTTU_EXTRA_MEDIA_DIRS"] = os.pathsep.join(str(path) for path in _legacy_media_dirs())
    if config.FFMPEG_DIR and config.FFMPEG_DIR.is_dir():
        os.environ["PATH"] = str(config.FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")
    extractor = str(config.EXTRACTOR_DIR)
    if extractor not in sys.path:
        sys.path.insert(0, extractor)
    return library_root


def _run() -> None:
    global _error
    try:
        from uttu import config as extractor_config
        from uttu.server import serve

        extractor_config.OUTPUT_DIR = Path(os.environ["UTTU_OUTPUT_DIR"]).resolve()
        extractor_config.DATA_DIR = Path(os.environ["UTTU_DATA_DIR"]).resolve()
        extractor_config.CACHE_DIR = extractor_config.DATA_DIR / "cache"
        extractor_config.ensure_dirs()
        serve(host=HOST, port=PORT)
    except Exception as exc:  # pragma: no cover - environment specific
        _error = str(exc)
        import traceback
        traceback.print_exc()


def start() -> dict[str, Any]:
    """Start the extractor once and return its URL and output root."""
    global _thread, _error
    with _lock:
        if _port_open():
            if _healthy():
                root = Path(os.environ.get("UTTU_OUTPUT_DIR", config.DEFAULT_LIBRARY_ROOT))
                return {"ok": True, "url": _URL, "root": str(root)}
            return {"ok": False, "error": f"端口 {PORT} 已被其他程序占用", "url": _URL}
        root = _prepare_env()
        _error = ""
        _thread = threading.Thread(target=_run, name="uttu-extractor", daemon=True)
        _thread.start()
        for _ in range(80):
            if _port_open() and _healthy():
                return {"ok": True, "url": _URL, "root": str(root)}
            if _error:
                return {"ok": False, "error": _error, "url": _URL}
            threading.Event().wait(0.1)
        return {"ok": False, "error": _error or "资源提取器启动超时", "url": _URL}
