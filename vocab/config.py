"""Global paths and defaults for the vocab app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
WEB_DIR = (RESOURCE_DIR / "web") if FROZEN else (ROOT / "web")
EXTRACTOR_DIR = (RESOURCE_DIR / "extractor") if FROZEN else (ROOT / "extractor")
FFMPEG_DIR = (RESOURCE_DIR / "ffmpeg") if FROZEN else None

if FROZEN:
    _local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    DATA_DIR = Path(os.environ.get("VOCAB_DATA_DIR", _local / "回溯" / "data")).resolve()
    DEFAULT_LIBRARY_ROOT = Path(os.environ.get(
        "VOCAB_LIBRARY_ROOT", Path.home() / "Videos" / "回溯"))
    DICT_DB_PATH = RESOURCE_DIR / "data" / "dict" / "ecdict.db"
    DICT_META_PATH = RESOURCE_DIR / "data" / "dict" / "meta.json"
    CEFR_PATH = RESOURCE_DIR / "vocab" / "data" / "cefr.json.gz"
else:
    DATA_DIR = Path(os.environ.get("VOCAB_DATA_DIR", ROOT / "data")).resolve()
    DEFAULT_LIBRARY_ROOT = Path(os.environ.get(
        "VOCAB_LIBRARY_ROOT", ROOT.parent / "uttu-extractor" / "output"))
    DICT_DB_PATH = DATA_DIR / "dict" / "ecdict.db"
    DICT_META_PATH = DATA_DIR / "dict" / "meta.json"
    CEFR_PATH = ROOT / "vocab" / "data" / "cefr.json.gz"

DB_PATH = DATA_DIR / "vocab.db"
STATE_PATH = DATA_DIR / "state.json"
THUMB_DIR = DATA_DIR / "thumbs"
EXTRACTOR_DATA_DIR = DATA_DIR / "extractor"

APP_NAME = "回溯"
APP_VERSION = "0.0.1"
SYNC_VERSION = 3

DEFAULT_SETTINGS = {
    "providers": ["youdao", "cefr"],
    "dict_order": ["youdao", "ecdict", "freedict", "cefr"],
    "lookup_order": ["sources", "youdao", "ecdict", "freedict", "cefr", "note"],
    "source_order": "current",
    "note_visible": True,
    "theme": "dark",
    "dict_sections": {
        "youdao": ["phonetic", "pos_groups", "forms", "etymology", "roots", "synonyms"],
        "ecdict": [],
        "freedict": [],
        "cefr": ["level", "pos_levels", "frequency"],
    },
    "preview_cefr_levels": ["B1", "B2"],
    "pronounce_accent": "us",
    "pronounce_auto": False,
    "subtitle_mode": "both",       # both | en | zh | off
    "blur_zh": False,
    "rate": 1.0,
    "library_root": "",
}
