# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the installable 回溯 app (version 0.0.1)."""
from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent
DIST = ROOT / "dist"
WORK = ROOT / "build"

import webview as _webview
import pythonnet as _pythonnet

hook_dirs = [
    str(Path(_webview.__file__).resolve().parent / "__pyinstaller"),
    str(Path(_pythonnet.__file__).resolve().parent / "_pyinstaller"),
]

datas = collect_data_files('webview') + [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "vocab" / "data"), "vocab/data"),
    (str(ROOT / "extractor"), "extractor"),
    (str(ROOT / "README.md"), "."),
]
if (ROOT / "data" / "dict").is_dir():
    datas.append((str(ROOT / "data" / "dict"), "data/dict"))

binaries = []
for name in ("ffmpeg", "ffprobe"):
    tool = shutil.which(name)
    if tool:
        binaries.append((tool, "ffmpeg"))
ytdlp = ROOT / "packaging" / "bin" / "yt-dlp.exe"
if ytdlp.is_file():
    binaries.append((str(ytdlp), "ffmpeg"))
binaries += collect_dynamic_libs('webview')
binaries += collect_dynamic_libs('pythonnet')

hiddenimports = [
    "requests", "bs4", "lxml", "yt_dlp",
    "webview", "webview.platforms.edgechromium", "webview.platforms.winforms",
    "webview.platforms.mshtml", "pythonnet", "clr_loader", "clr", "bottle",
    "proxy_tools", "cffi",
] + collect_submodules("yt_dlp")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "extractor")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=hook_dirs,
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "faster_whisper", "torch", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="回溯",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "web" / "favicon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="回溯",
)
