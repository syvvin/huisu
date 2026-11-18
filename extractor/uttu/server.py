"""Small local HTTP server for the tree UI and extraction jobs."""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config, media, outputs, site
from .pipeline import JobError, manager

log = logging.getLogger(__name__)


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "UTTU/0.1"

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, _json_bytes(payload))

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                return self._serve_web("index.html")
            if route.startswith("/static/"):
                return self._serve_web(route[len("/static/") :])
            if route == "/api/health":
                return self._json({"ok": True, "version": __import__("uttu").__version__})
            if route == "/api/tree":
                refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
                if refresh:
                    site.clear_chapter_cache()
                return self._json(site.get_tree(refresh=refresh))
            if route == "/api/chapter":
                path = query.get("path", [""])[0]
                parts = path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "story":
                    return self._error("Invalid chapter path.", 400)
                refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
                chapter = site.fetch_chapter(parts[1], parts[2], refresh=refresh)
                return self._json(chapter.summary())
            if route == "/api/outputs":
                refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
                return self._json(outputs.get_outputs(refresh=refresh))
            if route == "/api/jobs":
                return self._json({"jobs": manager.list()})
            if route.startswith("/api/jobs/"):
                suffix = route[len("/api/jobs/") :].strip("/")
                if "/" in suffix:
                    return self._error("Not found.", 404)
                job = manager.get(suffix)
                if not job:
                    return self._error("Job not found.", 404)
                return self._json(job.snapshot())
            if route.startswith("/output/"):
                return self._serve_output(route[len("/output/") :])
            if route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return self._error("Not found.", 404)
        except FileNotFoundError:
            return self._error("Not found.", 404)
        except site.SiteError as exc:
            return self._error(str(exc), 502)
        except Exception as exc:
            log.exception("GET %s failed", route)
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        try:
            body = self._body_json()
            if route == "/api/jobs":
                if not isinstance(body, dict):
                    return self._error("Expected a JSON object.", 400)
                selection = body.get("parts") or body.get("selection") or []
                try:
                    max_height = int(
                        body.get("max_height")
                        or body.get("height")
                        or config.VIDEO_MAX_HEIGHT
                    )
                except (TypeError, ValueError):
                    return self._error("max_height must be an integer.", 400)
                if not 360 <= max_height <= 4320:
                    return self._error("max_height must be between 360 and 4320.", 400)
                options = {
                    "force": bool(body.get("force")),
                    "force_media": bool(body.get("force_media")),
                    "max_height": media.normalize_height(max_height),
                }
                job = manager.create(selection, options)
                return self._json({"id": job.id, "status": job.status}, 201)
            if route.startswith("/api/jobs/") and route.endswith("/cancel"):
                job_id = route[len("/api/jobs/") : -len("/cancel")].strip("/")
                if not manager.cancel(job_id):
                    return self._error("Job not found.", 404)
                return self._json({"ok": True})
            if route == "/api/tree/refresh":
                return self._json(site.get_tree(refresh=True))
            return self._error("Not found.", 404)
        except (ValueError, JobError) as exc:
            return self._error(str(exc), 400)
        except Exception as exc:
            log.exception("POST %s failed", route)
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def _serve_web(self, relative: str) -> None:
        base = config.WEB_DIR.resolve()
        path = (base / relative).resolve()
        if base not in path.parents and path != base:
            return self._error("Forbidden.", 403)
        if not path.is_file():
            return self._error("Not found.", 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix in (".js", ".css", ".html"):
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)

    def _serve_output(self, relative: str) -> None:
        base = config.OUTPUT_DIR.resolve()
        path = (base / relative).resolve()
        if base not in path.parents and path != base:
            return self._error("Forbidden.", 403)
        if not path.is_file():
            return self._error("Not found.", 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        if range_header and content_type.startswith("video/"):
            # Minimal range support so the browser can seek in previews.
            try:
                start_s, _, end_s = range_header.replace("bytes=", "").partition("-")
                start = int(start_s or 0)
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                if start > end:
                    raise ValueError
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(path, "rb") as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False) -> None:
    config.ensure_dirs()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"UTTU extractor UI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()
