"""SQLite storage for the library, word book and review history."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS chapters (
    key        TEXT PRIMARY KEY,
    idx        INTEGER,
    label      TEXT,
    title_en   TEXT,
    title_zh   TEXT,
    sort       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_key  TEXT NOT NULL,
    number       INTEGER,
    slug         TEXT,
    dir          TEXT UNIQUE NOT NULL,
    title_en     TEXT,
    title_zh     TEXT,
    duration     REAL,
    video_path   TEXT,
    size         INTEGER,
    en_srt       TEXT,
    zh_srt       TEXT,
    bilingual_srt TEXT,
    part_json    TEXT,
    cues         INTEGER,
    word_count   INTEGER,
    voice_lines  INTEGER,
    recording    TEXT,
    generated_at TEXT,
    fingerprint  TEXT,
    missing      INTEGER DEFAULT 0,
    synced_at    TEXT
);

CREATE TABLE IF NOT EXISTS cues (
    part_id  INTEGER NOT NULL,
    idx      INTEGER NOT NULL,
    start    REAL,
    end      REAL,
    speaker  TEXT,
    en       TEXT,
    zh       TEXT,
    tokens   TEXT,
    PRIMARY KEY (part_id, idx)
);

CREATE TABLE IF NOT EXISTS part_progress (
    part_id      INTEGER PRIMARY KEY,
    position     REAL DEFAULT 0,
    previewed_at TEXT,
    completed_at TEXT,
    reviewed_at  TEXT,
    last_opened_at TEXT
);

CREATE TABLE IF NOT EXISTS words (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma       TEXT NOT NULL UNIQUE,
    display     TEXT,
    phonetic    TEXT,
    brief_zh    TEXT,
    pos         TEXT,
    box         INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'active',
    due_at      TEXT,
    reps        INTEGER DEFAULT 0,
    lapses      INTEGER DEFAULT 0,
    note        TEXT,
    created_at  TEXT,
    updated_at  TEXT,
    last_rating INTEGER
);

CREATE TABLE IF NOT EXISTS word_parts (
    word_id   INTEGER NOT NULL,
    part_id   INTEGER NOT NULL,
    first_cue INTEGER,
    added_at  TEXT,
    PRIMARY KEY (word_id, part_id)
);

CREATE TABLE IF NOT EXISTS word_contexts (
    word_id INTEGER NOT NULL,
    part_id INTEGER NOT NULL,
    cue     INTEGER NOT NULL,
    surface TEXT,
    PRIMARY KEY (word_id, part_id, cue)
);

CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id    INTEGER,
    part_id    INTEGER,
    rating     INTEGER,
    box_before INTEGER,
    box_after  INTEGER,
    elapsed_ms INTEGER,
    at         TEXT
);

CREATE TABLE IF NOT EXISTS dict_cache (
    provider   TEXT NOT NULL,
    lemma      TEXT NOT NULL,
    payload    TEXT,
    fetched_at TEXT,
    ok         INTEGER DEFAULT 1,
    PRIMARY KEY (provider, lemma)
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_parts_chapter ON parts(chapter_key, number);
CREATE INDEX IF NOT EXISTS idx_cues_part ON cues(part_id);
CREATE INDEX IF NOT EXISTS idx_words_due ON words(due_at);
CREATE INDEX IF NOT EXISTS idx_word_parts_part ON word_parts(part_id);
CREATE INDEX IF NOT EXISTS idx_contexts_word ON word_contexts(word_id);
CREATE INDEX IF NOT EXISTS idx_reviews_at ON reviews(at);
"""

_local = threading.local()


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def add_days(value: datetime, days: float) -> str:
    return (value + timedelta(days=days)).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = Path(path or config.DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connection() -> sqlite3.Connection:
    """One connection per thread; FastAPI-free and good enough for a local app."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    conn = connection()
    conn.executescript(SCHEMA)
    conn.commit()
    row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if not row:
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema','1')")
        conn.commit()


def rows(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(connection().execute(sql, tuple(params)).fetchall())


def row(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connection().execute(sql, tuple(params)).fetchone()


def scalar(sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
    r = row(sql, params)
    if r is None:
        return default
    value = r[0]
    return default if value is None else value


def get_setting(key: str, default: Any = None) -> Any:
    r = row("SELECT value FROM settings WHERE key=?", (key,))
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: Any) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def settings() -> dict[str, Any]:
    result = dict(config.DEFAULT_SETTINGS)
    for r in rows("SELECT key,value FROM settings"):
        try:
            result[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            pass
    providers = result.get("providers")
    if isinstance(providers, list):
        cleaned = [p for p in providers if isinstance(p, str) and p != "affix"]
        # One-time migration for settings created before CEFR was added.
        if "affix" in providers and "cefr" not in cleaned:
            cleaned.append("cefr")
        result["providers"] = cleaned
    if not isinstance(result.get("dict_sections"), dict):
        result["dict_sections"] = {}
    known_dicts = ["youdao", "ecdict", "freedict", "cefr"]
    order = result.get("dict_order")
    if not isinstance(order, list):
        order = list(config.DEFAULT_SETTINGS["dict_order"])
    cleaned_order = [p for p in order if p in known_dicts]
    for provider in known_dicts:
        if provider not in cleaned_order:
            cleaned_order.append(provider)
    result["dict_order"] = cleaned_order
    lookup_keys = cleaned_order + ["note", "sources"]
    lookup_order = result.get("lookup_order")
    if not isinstance(lookup_order, list):
        lookup_order = lookup_keys
    cleaned_lookup = []
    for key in lookup_order:
        if key in lookup_keys and key not in cleaned_lookup:
            cleaned_lookup.append(key)
    for key in lookup_keys:
        if key not in cleaned_lookup:
            cleaned_lookup.append(key)
    result["lookup_order"] = cleaned_lookup
    if result.get("source_order") not in ("current", "story", "recent"):
        result["source_order"] = "current"
    result["note_visible"] = bool(result.get("note_visible", True))
    if result.get("theme") not in ("dark", "light"):
        result["theme"] = "dark"
    levels = result.get("preview_cefr_levels")
    allowed = ("A1", "A2", "B1", "B2", "C1", "C2")
    if not isinstance(levels, list) or not levels:
        levels = ["B1", "B2"]
    result["preview_cefr_levels"] = [l for l in allowed if l in levels]
    if result.get("pronounce_accent") not in ("uk", "us"):
        result["pronounce_accent"] = "us"
    result["pronounce_auto"] = bool(result.get("pronounce_auto"))
    return result


def meta_get(key: str, default: Any = None) -> Any:
    r = row("SELECT value FROM meta WHERE key=?", (key,))
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except (ValueError, TypeError):
        return r["value"]


def meta_set(key: str, value: Any) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

