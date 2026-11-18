"""Frozen Windows desktop launcher for 回溯 (native WebView2 window)."""
from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8766
APP = "回溯"
URL = f"http://{HOST}:{PORT}/"

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def data_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    folder = local / APP
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def log(message: str) -> None:
    try:
        path = data_root() / "app.log"
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.unlink()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except OSError:
        pass


def message(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, APP, 0x10)
    except Exception:
        pass


def port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def app_ready() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/state/info", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def stop_server() -> None:
    try:
        from vocab import server
        server.shutdown()
    except Exception:
        log("STOP SERVER ERROR\n" + traceback.format_exc())


def focus_existing() -> bool:
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, APP)
        if not hwnd:
            return False
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


class WindowApi:
    """Small JS bridge for the custom frameless title bar."""

    def __init__(self) -> None:
        self._window = None
        self._maximized = False
        self._fullscreen = False
        self._normal_size = None

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def toggle_maximize(self) -> bool:
        if not self._window:
            return False
        if self._maximized:
            self._window.restore()
            self._maximized = False
        else:
            self._window.maximize()
            self._maximized = True
        return self._maximized

    def close_window(self) -> None:
        stop_server()
        if self._window:
            self._window.destroy()
        # Guarantee the process does not linger holding 8766 after the window is gone.
        threading.Timer(1.0, lambda: os._exit(0)).start()

    def window_bounds(self) -> dict:
        if not self._window:
            return {}
        try:
            return {"width": self._window.width, "height": self._window.height}
        except Exception:
            return {}

    def resize_window(self, width, height, anchor: str = "se") -> bool:
        if not self._window:
            return False
        try:
            from webview.window import FixPoint
            fixes = {
                "e": FixPoint.NORTH | FixPoint.WEST,
                "s": FixPoint.NORTH | FixPoint.WEST,
                "se": FixPoint.NORTH | FixPoint.WEST,
                "w": FixPoint.EAST | FixPoint.NORTH,
                "n": FixPoint.SOUTH | FixPoint.WEST,
                "nw": FixPoint.EAST | FixPoint.SOUTH,
                "ne": FixPoint.SOUTH | FixPoint.WEST,
                "sw": FixPoint.EAST | FixPoint.NORTH,
            }
            w = max(1040, int(width))
            h = max(680, int(height))
            self._window.resize(w, h, fixes.get(anchor, fixes["se"]))
            return True
        except Exception:
            return False

    def set_fullscreen(self, flag: bool) -> bool:
        want = bool(flag)
        if not self._window:
            return False
        if want and not self._fullscreen:
            try:
                self._normal_size = (self._window.width, self._window.height)
            except Exception:
                self._normal_size = None
            self._fullscreen = True
            self._window.toggle_fullscreen()
        elif not want and self._fullscreen:
            self._fullscreen = False
            self._window.toggle_fullscreen()
            # pywebview's frameless restore can fall back to min_size; restore
            # the exact user-visible window size captured before fullscreen.
            if self._normal_size:
                if self._maximized:
                    self._window.maximize()
                else:
                    self._window.resize(*self._normal_size)
        return self._fullscreen


def run_server() -> None:
    try:
        from vocab import server
        server.serve(host=HOST, port=PORT, open_browser=False)
    except Exception:
        log("SERVER ERROR\n" + traceback.format_exc())


def main() -> int:
    base = base_dir()
    ffmpeg = base / "ffmpeg"
    if ffmpeg.is_dir():
        os.environ["PATH"] = str(ffmpeg) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("VOCAB_FROZEN", "1")

    try:
        stream = (data_root() / "app.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except OSError:
        pass

    if port_open():
        # A just-closed window can leave the server shutting down for a moment.
        # Wait for it to release the port, or bring an existing window forward.
        for _ in range(50):
            if app_ready():
                if focus_existing():
                    log("focused existing instance")
                    return 0
            if not port_open():
                break
            time.sleep(0.1)
        if port_open():
            if app_ready() and focus_existing():
                return 0
            message(f"端口 {PORT} 已被其他程序占用，无法启动 {APP}。")
            return 1

    if not app_ready():
        threading.Thread(target=run_server, name="vocab-server", daemon=True).start()
        for _ in range(240):
            if app_ready():
                break
            time.sleep(0.1)
        else:
            message(f"{APP} 后端启动失败。\n\n日志：{data_root() / 'app.log'}")
            return 1

    try:
        import webview
        screens = webview.screens
        screen_w = screens[0].width if screens else 1536
        screen_h = screens[0].height if screens else 864
        win_w = max(1040, min(1440, screen_w - 48))
        win_h = max(680, min(900, screen_h - 48))
        api = WindowApi()
        window = webview.create_window(
            APP,
            URL,
            width=win_w,
            height=win_h,
            min_size=(1040, 680),
            confirm_close=False,
            background_color="#0c1511",
            frameless=True,
            easy_drag=False,
            shadow=False,
            js_api=api,
        )
        api._window = window
        window.events.closing += lambda: stop_server()
        window.events.closed += lambda: threading.Timer(0.3, lambda: os._exit(0)).start()
        log(f"opening frameless desktop window {APP} 0.0.1")
        webview.start(debug=False, private_mode=False)
        log("window closed")
        return 0
    except Exception:
        detail = traceback.format_exc()
        log("WEBVIEW ERROR\n" + detail)
        message(f"{APP} 窗口启动失败：\n\n{detail[-1200:]}\n\n日志：{data_root() / 'app.log'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
