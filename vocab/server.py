"""Local HTTP server: JSON API, static frontend, video range streaming."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import cefr, config, db, dicts, ecdict, extractor_bridge, library, state, study

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

_thumb_locks: dict[int, threading.Lock] = {}
_thumb_guard = threading.Lock()
_ffmpeg = shutil.which("ffmpeg")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _thumb_lock(part_id: int) -> threading.Lock:
    with _thumb_guard:
        lock = _thumb_locks.get(part_id)
        if lock is None:
            lock = threading.Lock()
            _thumb_locks[part_id] = lock
        return lock


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# static files
# --------------------------------------------------------------------------- #
def _safe_static(rel: str) -> Path | None:
    rel = urllib.parse.unquote(rel).lstrip("/")
    if not rel:
        rel = "index.html"
    target = (config.WEB_DIR / rel).resolve()
    try:
        target.relative_to(config.WEB_DIR.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


# --------------------------------------------------------------------------- #
# video / thumbnails
# --------------------------------------------------------------------------- #
def _part_video(part_id: int) -> Path | None:
    row = db.row("SELECT video_path FROM parts WHERE id=? AND missing=0", (part_id,))
    if not row:
        return None
    path = Path(row["video_path"])
    return path if path.is_file() else None


def _thumb_path(part_id: int) -> Path:
    return config.THUMB_DIR / f"{part_id}.jpg"


def _build_thumb(part_id: int) -> Path | None:
    target = _thumb_path(part_id)
    if target.exists() and target.stat().st_size > 0:
        return target
    video = _part_video(part_id)
    if not video or not _ffmpeg:
        return None
    with _thumb_lock(part_id):
        if target.exists() and target.stat().st_size > 0:
            return target
        row = db.row("SELECT duration FROM parts WHERE id=?", (part_id,))
        duration = float((row["duration"] if row else 0) or 0)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Sample a few points and keep the frame that carries the most detail;
        # fades and title cards compress to almost nothing and look black.
        ratios = [0.35, 0.5, 0.65, 0.2, 0.8]
        best: Path | None = None
        best_size = 0
        for ratio in ratios:
            stamp = max(2.0, duration * ratio) if duration else 5.0
            if duration and stamp > duration - 2:
                stamp = max(1.0, duration * 0.5)
            candidate = target.with_name(f"{part_id}.{int(ratio * 100)}.jpg")
            cmd = [_ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{stamp:.2f}",
                   "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2",
                   "-q:v", "5", "-y", str(candidate)]
            try:
                subprocess.run(cmd, timeout=30, capture_output=True, creationflags=_NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                continue
            if candidate.exists():
                size = candidate.stat().st_size
                if size > best_size:
                    if best and best != candidate:
                        best.unlink(missing_ok=True)
                    best, best_size = candidate, size
                else:
                    candidate.unlink(missing_ok=True)
            if best_size > 4000:
                break
        if best is None or best_size < 1200:
            if best:
                best.unlink(missing_ok=True)
            return None
        best.replace(target)
        return target


# --------------------------------------------------------------------------- #
# API handlers
# --------------------------------------------------------------------------- #
def api_library(_q: dict, _body: Any) -> dict:
    return library.library()


def api_part(q: dict, _body: Any) -> dict:
    part_id = int(q.get("id", ["0"])[0])
    payload = library.part_payload(part_id)
    if payload is None:
        raise ApiError("找不到这个 Part", 404)
    return payload


def api_candidates(q: dict, _body: Any) -> dict:
    part_id = int(q.get("id", ["0"])[0])
    part = db.row("SELECT id FROM parts WHERE id=?", (part_id,))
    if not part:
        raise ApiError("找不到这个 Part", 404)
    settings = db.settings()
    levels = settings.get("preview_cefr_levels") or ["B1", "B2"]
    return {"part_id": part_id, "items": library.candidates(part_id, levels),
            "levels": levels, "available": cefr.available()}


def api_dict(q: dict, _body: Any) -> dict:
    lemma = (q.get("lemma", [""])[0] or "").strip().lower()
    part_id = int(q.get("part", ["0"])[0] or 0)
    cue = int(q.get("cue", ["-1"])[0] or -1)
    settings = db.settings()
    payload = dicts.lookup(lemma, settings.get("providers"))
    word = db.row("SELECT * FROM words WHERE lemma=?", (lemma,))
    if word:
        payload["status"] = study.get_word(word["id"], part_id or None)
    context = None
    if part_id >= 0 and cue >= 0:
        row = db.row(
            "SELECT c.idx, c.start, c.end, c.en, c.zh, c.speaker, p.title_en, p.title_zh, p.id, "
            "p.number AS part_number "
            "FROM cues c JOIN parts p ON p.id=c.part_id WHERE c.part_id=? AND c.idx=?",
            (part_id, cue))
        if row:
            context = {"part_id": row["id"], "cue": row["idx"], "start": row["start"],
                       "end": row["end"], "en": row["en"], "zh": row["zh"],
                       "speaker": row["speaker"], "part_title_en": row["title_en"],
                       "part_title_zh": row["title_zh"], "part_number": row["part_number"]}
    payload["context"] = context
    payload["word_sources"] = library.word_sources(lemma, part_id if part_id > 0 else None)
    payload["focus"] = {"part_id": part_id if part_id > 0 else 0,
                        "cue": cue if cue >= 0 else None}
    return payload


def _resolve_word(body: dict) -> tuple[int, str, int | None, str]:
    part_id = int(body.get("part_id") or 0)
    if not part_id:
        raise ApiError("缺少 part_id")
    lemma = (body.get("lemma") or "").strip().lower()
    cue = body.get("cue")
    cue = int(cue) if cue is not None and str(cue) != "" else None
    surface = (body.get("surface") or "").strip()
    if not lemma and cue is not None:
        start, end = body.get("start"), body.get("end")
        row = db.row("SELECT en FROM cues WHERE part_id=? AND idx=?", (part_id, cue))
        if row is None or start is None or end is None:
            raise ApiError("无法定位这个单词")
        surface = (row["en"] or "")[int(start):int(end)]
        lemma = ecdict.lemma(surface)
    if not lemma:
        raise ApiError("缺少单词")
    return part_id, lemma, cue, surface


def _save_state() -> None:
    try:
        state.save()
    except Exception:  # pragma: no cover - never break a write request
        traceback.print_exc()


def api_add_word(_q: dict, body: Any) -> dict:
    body = body or {}
    part_id, lemma, cue, surface = _resolve_word(body)
    word = study.add_word(part_id, lemma, cue, surface)
    if word is None:
        raise ApiError("这个单词无法收录")
    _save_state()
    return {"word": word, "words": library.part_words(part_id)}


def api_word_batch(_q: dict, body: Any) -> dict:
    body = body or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise ApiError("ids 必须是数组")
    try:
        result = study.batch_words(ids, str(body.get("action") or ""))
    except ValueError as exc:
        raise ApiError(str(exc))
    _save_state()
    return result


def api_remove_word(q: dict, body: Any, word_id: int) -> dict:
    part_id = int((body or {}).get("part_id") or (q.get("part_id", ["0"])[0]) or 0)
    forget = bool((body or {}).get("forget"))
    result = study.remove_word(word_id, part_id or None, forget)
    _save_state()
    return {"result": result,
            "words": library.part_words(part_id) if part_id else []}


def api_update_word(_q: dict, body: Any, word_id: int) -> dict:
    body = body or {}
    word = study.update_word(word_id, body)
    if word is None:
        raise ApiError("找不到词条", 404)
    part_id = int(body.get("part_id") or 0)
    _save_state()
    return {"word": word, "words": library.part_words(part_id) if part_id else []}


def api_review_word(_q: dict, body: Any, word_id: int) -> dict:
    body = body or {}
    rating = int(body.get("rating") or 0)
    if rating not in (1, 2, 3):
        raise ApiError("rating 必须是 1 / 2 / 3")
    part_id = int(body.get("part_id") or 0)
    word = study.rate(word_id, rating, part_id or None, int(body.get("elapsed_ms") or 0))
    if word is None:
        raise ApiError("找不到词条", 404)
    _save_state()
    return {"word": word, "totals": library.totals()}


def api_queue(q: dict, _body: Any) -> dict:
    part_id = int(q.get("part_id", ["0"])[0] or 0)
    scope = q.get("scope", ["part"])[0]
    limit = min(120, int(q.get("limit", ["60"])[0] or 60))
    return study.queue(part_id or None, scope, limit)


def api_wordbook(q: dict, _body: Any) -> dict:
    return study.wordbook(
        query=(q.get("q", [""])[0] or "").strip(),
        status=q.get("status", ["all"])[0],
        chapter=q.get("chapter", [""])[0],
        limit=min(200, int(q.get("limit", ["60"])[0] or 60)),
        offset=max(0, int(q.get("offset", ["0"])[0] or 0)),
    )


def api_stats(_q: dict, _body: Any) -> dict:
    return {"totals": library.totals(), "activity": study.recent_activity(8),
            "dict": ecdict.lookup_stats(), "cache": dicts.cache_stats()}


def api_settings(q: dict, body: Any) -> dict:
    if body:
        allowed = set(config.DEFAULT_SETTINGS)
        for key, value in body.items():
            if key in allowed:
                db.set_setting(key, value)
        _save_state()
    payload = db.settings()
    payload["version"] = config.APP_VERSION
    payload["app_name"] = config.APP_NAME
    payload["providers_meta"] = dicts.describe_all()
    payload["dict"] = ecdict.lookup_stats()
    payload["cefr"] = cefr.stats()
    return payload


def api_extractor_start(_q: dict, _body: Any) -> dict:
    return extractor_bridge.start()


def api_extractor_cache(_q: dict, _body: Any) -> dict:
    return extractor_bridge.cache_info()


def api_extractor_cache_clear(_q: dict, _body: Any) -> dict:
    return extractor_bridge.clear_cache()


def api_sync(_q: dict, _body: Any) -> dict:
    result = library.sync()
    _save_state()
    return {"sync": result, "library": library.library()}


def api_state_info(_q: dict, _body: Any) -> dict:
    return state.info()


def api_progress(_q: dict, body: Any) -> dict:
    body = body or {}
    part_id = int(body.get("part_id") or 0)
    if not part_id:
        raise ApiError("缺少 part_id")
    result = study.progress(part_id, body.get("position"), body.get("event") or "")
    _save_state()
    return {"progress": result}


def api_search(q: dict, _body: Any) -> dict:
    return {"items": library.search_cues((q.get("q", [""])[0] or ""))}


def api_dict_clear(_q: dict, _body: Any) -> dict:
    return {"removed": dicts.clear_cache()}


def api_export(_q: dict, _body: Any) -> str:
    rows = study.export_rows()
    header = ["word", "phonetic", "brief_zh", "box", "status", "due_date",
              "created_date", "parts", "note"]
    lines = [",".join(header)]
    for row in rows:
        values = []
        for key in header:
            value = str(row.get(key, "") or "").replace('"', "'").replace("\n", " ")
            values.append(f'"{value}"')
        lines.append(",".join(values))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "VocabStudio/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------- #
    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("VOCAB_DEBUG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def handle_one_request(self) -> None:
        # Browsers abort range requests all the time while seeking; that is not
        # an error worth printing a traceback for.
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        if length > 1_000_000:
            raise ApiError("请求体过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ApiError("请求体不是合法 JSON") from exc

    def _query(self) -> dict[str, list[str]]:
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parsed.query)

    # -- routing ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/media/"):
                self._serve_media(int(path.split("/")[2]))
                return
            if path.startswith("/thumb/"):
                part_id = int(Path(path).stem)
                self._serve_thumb(part_id)
                return
            if path.startswith("/api/"):
                self._route_api(method, path, self._query())
                return
            if method != "GET":
                raise ApiError("不支持的请求", 405)
            self._serve_static(path)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()
            try:
                self._json({"error": "服务器内部错误，请查看终端日志"}, 500)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

    def _route_api(self, method: str, path: str, q: dict) -> None:
        body = self._read_body() if method in ("POST", "PATCH", "DELETE") else None
        if method == "GET":
            if path == "/api/library":
                return self._json(api_library(q, None))
            if path == "/api/stats":
                return self._json(api_stats(q, None))
            if path == "/api/settings":
                return self._json(api_settings(q, None))
            if path == "/api/dict":
                return self._json(api_dict(q, None))
            if path == "/api/review":
                return self._json(api_queue(q, None))
            if path == "/api/wordbook":
                return self._json(api_wordbook(q, None))
            if path == "/api/search":
                return self._json(api_search(q, None))
            if path == "/api/export.csv":
                return self._text(api_export(q, None), 200, "text/csv; charset=utf-8")
            if path == "/api/state/info":
                return self._json(api_state_info(q, None))
            if path == "/api/extractor/cache":
                return self._json(api_extractor_cache(q, None))
            if path == "/api/dict/providers":
                return self._json({"providers": dicts.describe_all(),
                                   "enabled": db.settings().get("providers")})
            m = re.fullmatch(r"/api/part/(\d+)", path)
            if m:
                q["id"] = [m.group(1)]
                return self._json(api_part(q, None))
            m = re.fullmatch(r"/api/part/(\d+)/candidates", path)
            if m:
                q["id"] = [m.group(1)]
                return self._json(api_candidates(q, None))
            raise ApiError("接口不存在", 404)

        if method == "POST":
            if path == "/api/words/batch":
                return self._json(api_word_batch(q, body))
            if path == "/api/words":
                return self._json(api_add_word(q, body))
            if path == "/api/sync":
                return self._json(api_sync(q, body))
            if path == "/api/progress":
                return self._json(api_progress(q, body))
            if path == "/api/settings":
                return self._json(api_settings(q, body))
            if path == "/api/dict/cache/clear":
                return self._json(api_dict_clear(q, body))
            if path == "/api/extractor/start":
                return self._json(api_extractor_start(q, body))
            if path == "/api/extractor/cache/clear":
                return self._json(api_extractor_cache_clear(q, body))
            m = re.fullmatch(r"/api/words/(\d+)/review", path)
            if m:
                return self._json(api_review_word(q, body, int(m.group(1))))
            raise ApiError("接口不存在", 404)

        m = re.fullmatch(r"/api/words/(\d+)", path)
        if m:
            word_id = int(m.group(1))
            if method == "DELETE":
                return self._json(api_remove_word(q, body, word_id))
            if method == "PATCH":
                return self._json(api_update_word(q, body, word_id))
        raise ApiError("接口不存在", 404)

    # -- static / media --------------------------------------------------- #
    def _serve_static(self, path: str) -> None:
        target = _safe_static(path)
        if target is None:
            self._text("Not Found", 404)
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_media(self, part_id: int) -> None:
        path = _part_video(part_id)
        if path is None:
            self._json({"error": "视频文件不存在"}, 404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_thumb(self, part_id: int) -> None:
        target = _thumb_path(part_id)
        if not target.exists():
            target = _build_thumb(part_id) or target
        if not target.exists():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


_httpd: "Server | None" = None
_httpd_lock = threading.Lock()


def shutdown() -> None:
    """Stop the local HTTP server so a relaunch can bind port 8766 immediately."""
    with _httpd_lock:
        httpd = _httpd
    if httpd is not None:
        try:
            httpd.shutdown()
        except Exception:
            traceback.print_exc()


def serve(host: str = "127.0.0.1", port: int = 8766, open_browser: bool = False,
          sync_first: bool = True) -> None:
    global _httpd
    db.init_db()
    if sync_first:
        try:
            result = library.sync()
            print(f"[library] {result['parts']} parts · {result['cues']} cues · {result['root']}")
        except Exception:  # pragma: no cover - startup should still work
            traceback.print_exc()
    state.load()
    httpd = Server((host, port), Handler)
    with _httpd_lock:
        _httpd = httpd
    url = f"http://{host}:{port}/"
    print(f"\n  回溯 · 视频精读词卡  ->  {url}\n")
    if open_browser:
        threading.Timer(0.8, lambda: __import__("webbrowser").open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        with _httpd_lock:
            if _httpd is httpd:
                _httpd = None
        httpd.server_close()
