"""One JSON file for all user state: settings, progress, words and reviews."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from . import config, db

_LOCK = threading.Lock()

WORD_KEYS = (
    "lemma", "display", "phonetic", "brief_zh", "pos", "box", "status", "due_at",
    "reps", "lapses", "note", "created_at", "updated_at", "last_rating",
)
PROGRESS_KEYS = ("position", "previewed_at", "completed_at", "reviewed_at", "last_opened_at")


def _root() -> Path:
    configured = db.get_setting("library_root", "")
    return Path(configured).expanduser() if configured else config.DEFAULT_LIBRARY_ROOT


def _rel(value: str, root: Path) -> str:
    try:
        return Path(value).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return value


def _dirs() -> dict[int, str]:
    root = _root()
    return {r["id"]: _rel(r["dir"], root) for r in db.rows("SELECT id, dir FROM parts WHERE missing=0")}


def build() -> dict[str, Any]:
    dirs = _dirs()
    word_parts: dict[int, list[dict]] = {}
    for r in db.rows("SELECT word_id, part_id, first_cue, added_at FROM word_parts"):
        if r["part_id"] in dirs:
            word_parts.setdefault(r["word_id"], []).append({
                "part": dirs[r["part_id"]],
                "first_cue": r["first_cue"],
                "added_at": r["added_at"],
            })
    contexts: dict[int, list[dict]] = {}
    for r in db.rows("SELECT word_id, part_id, cue, surface FROM word_contexts"):
        if r["part_id"] in dirs:
            contexts.setdefault(r["word_id"], []).append({
                "part": dirs[r["part_id"]],
                "cue": r["cue"],
                "surface": r["surface"],
            })
    reviews: dict[int, list[dict]] = {}
    for r in db.rows("SELECT word_id, part_id, rating, box_before, box_after, elapsed_ms, at FROM reviews"):
        if r["part_id"] in dirs:
            reviews.setdefault(r["word_id"], []).append({
                "part": dirs[r["part_id"]],
                "rating": r["rating"],
                "box_before": r["box_before"],
                "box_after": r["box_after"],
                "elapsed_ms": r["elapsed_ms"],
                "at": r["at"],
            })

    words = []
    for w in db.rows("SELECT * FROM words ORDER BY id"):
        item = {k: w[k] for k in WORD_KEYS}
        item["parts"] = word_parts.get(w["id"], [])
        item["contexts"] = contexts.get(w["id"], [])
        item["reviews"] = reviews.get(w["id"], [])
        words.append(item)

    progress = {}
    for r in db.rows("SELECT * FROM part_progress"):
        key = dirs.get(r["part_id"])
        if key:
            progress[key] = {k: r[k] for k in PROGRESS_KEYS}

    return {
        "version": 1,
        "updated_at": db.now_iso(),
        "settings": db.settings(),
        "progress": progress,
        "words": words,
    }


def save() -> None:
    with _LOCK:
        payload = build()
        target = config.STATE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load() -> bool:
    target = config.STATE_PATH
    if not target.exists():
        save()
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return False

    # Safety: never let a stale/partial state file destroy a richer runtime
    # index. This happened when a 2-word state was loaded over a 12-word DB.
    # If the DB has more words, rebuild the state file from the DB instead.
    try:
        db_word_count = db.scalar("SELECT COUNT(*) FROM words", (), 0)
    except Exception:
        db_word_count = 0
    if db_word_count > len(payload.get("words") or []):
        save()
        return False

    parts = {r["dir"]: r["id"] for r in db.rows("SELECT id, dir FROM parts WHERE missing=0")}
    root = _root()
    for r in db.rows("SELECT id, dir FROM parts WHERE missing=0"):
        parts.setdefault(_rel(r["dir"], root), r["id"])
    now = db.now_iso()
    with db.tx() as conn:
        conn.execute("DELETE FROM reviews")
        conn.execute("DELETE FROM word_contexts")
        conn.execute("DELETE FROM word_parts")
        conn.execute("DELETE FROM words")
        conn.execute("DELETE FROM part_progress")

        for w in payload.get("words") or []:
            if not isinstance(w, dict):
                continue
            lemma = str(w.get("lemma") or "").strip()
            if not lemma:
                continue
            box = max(0, min(5, _int(w.get("box"), 0)))
            values = (
                lemma, w.get("display"), w.get("phonetic"), w.get("brief_zh"), w.get("pos"),
                box, w.get("status") or "active", w.get("due_at"),
                _int(w.get("reps")), _int(w.get("lapses")), w.get("note"),
                w.get("created_at") or now, w.get("updated_at") or now,
                w.get("last_rating"),
            )
            cur = conn.execute(
                f"INSERT INTO words({','.join(WORD_KEYS)}) VALUES({','.join('?' for _ in WORD_KEYS)})",
                values,
            )
            wid = cur.lastrowid
            for p in w.get("parts") or []:
                pid = parts.get(p.get("part")) if isinstance(p, dict) else None
                if pid:
                    conn.execute(
                        "INSERT OR IGNORE INTO word_parts(word_id,part_id,first_cue,added_at) VALUES(?,?,?,?)",
                        (wid, pid, p.get("first_cue"), p.get("added_at") or now),
                    )
            for c in w.get("contexts") or []:
                pid = parts.get(c.get("part")) if isinstance(c, dict) else None
                if pid and c.get("cue") is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO word_contexts(word_id,part_id,cue,surface) VALUES(?,?,?,?)",
                        (wid, pid, _int(c.get("cue")), c.get("surface")),
                    )
            for rv in w.get("reviews") or []:
                if not isinstance(rv, dict):
                    continue
                pid = parts.get(rv.get("part"))
                if not pid:
                    continue
                conn.execute(
                    "INSERT INTO reviews(word_id,part_id,rating,box_before,box_after,elapsed_ms,at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (wid, pid, rv.get("rating"), rv.get("box_before"), rv.get("box_after"),
                     rv.get("elapsed_ms"), rv.get("at")),
                )

        for key, prog in (payload.get("progress") or {}).items():
            pid = parts.get(key)
            if pid and isinstance(prog, dict):
                conn.execute(
                    "INSERT OR REPLACE INTO part_progress(part_id,position,previewed_at,completed_at,reviewed_at,last_opened_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (pid, prog.get("position") or 0, prog.get("previewed_at"), prog.get("completed_at"),
                     prog.get("reviewed_at"), prog.get("last_opened_at")),
                )

        conn.execute("DELETE FROM settings WHERE key != 'library_root'")
        for key, value in (payload.get("settings") or {}).items():
            if key == "library_root":
                continue
            conn.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    save()
    return True


def info() -> dict[str, Any]:
    target = config.STATE_PATH
    updated = None
    if target.exists():
        try:
            updated = json.loads(target.read_text(encoding="utf-8")).get("updated_at")
        except (OSError, ValueError):
            updated = None
    return {
        "path": str(target),
        "exists": target.exists(),
        "size": target.stat().st_size if target.exists() else 0,
        "updated_at": updated,
        "words": db.scalar("SELECT COUNT(*) FROM words", default=0),
    }
