"""Word book, spaced repetition and per-part progress."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from . import db, ecdict, text

# Leitner-style boxes. index = box after a successful recall.
INTERVALS = [0.007, 1, 2, 4, 8, 16]   # days; box 0 comes back in ~10 minutes
RATING_AGAIN, RATING_HARD, RATING_GOOD = 1, 2, 3


def _now() -> datetime:
    return datetime.now().astimezone()


def _due_in(now: datetime, days: float) -> str:
    return (now + timedelta(days=days)).replace(microsecond=0).isoformat()


def _cue_tokens(en: str) -> list[list]:
    return json.loads(json.dumps(text.tokenize(en or "")))


def find_occurrences(part_id: int, lemma: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for cue in db.rows("SELECT idx, en, tokens FROM cues WHERE part_id=? ORDER BY idx", (part_id,)):
        en = cue["en"] or ""
        for start, end, tok_lemma in json.loads(cue["tokens"] or "[]"):
            if tok_lemma == lemma:
                hits.append((cue["idx"], en[start:end]))
    return hits


def add_word(part_id: int, lemma: str, cue_idx: int | None = None, surface: str = "") -> dict[str, Any] | None:
    lemma = (lemma or "").strip().lower()
    if not lemma or not lemma.replace("'", "").replace("-", "").isalpha():
        return None
    entry = ecdict.entry(lemma)
    phonetic, brief = text.word_brief(entry)
    pos = ""
    if entry and entry.get("pos_groups"):
        pos = entry["pos_groups"][0].get("pos", "")
    now = db.now_iso()
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM words WHERE lemma=?", (lemma,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO words(lemma,display,phonetic,brief_zh,pos,box,status,due_at,"
                "reps,lapses,note,created_at,updated_at) VALUES(?,?,?,?,?,0,'active',?,0,0,'',?,?)",
                (lemma, surface or lemma, phonetic, brief, pos, now, now, now))
            word_id = cur.lastrowid
        else:
            word_id = row["id"]
            if row["status"] == "ignored":
                conn.execute("UPDATE words SET status='active', updated_at=? WHERE id=?", (now, word_id))
            if not row["phonetic"] and phonetic:
                conn.execute("UPDATE words SET phonetic=?, brief_zh=? WHERE id=?",
                             (phonetic, brief, word_id))
        occurrences = find_occurrences(part_id, lemma)
        if cue_idx is not None:
            first = cue_idx
            surface = surface or next((s for i, s in occurrences if i == cue_idx), "") or lemma
        else:
            first = occurrences[0][0] if occurrences else None
            surface = surface or (occurrences[0][1] if occurrences else lemma)
        conn.execute(
            "INSERT INTO word_parts(word_id,part_id,first_cue,added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(word_id,part_id) DO UPDATE SET first_cue=COALESCE(word_parts.first_cue, excluded.first_cue)",
            (word_id, part_id, first, now))
        if first is not None:
            cue_exists = conn.execute(
                "SELECT 1 FROM cues WHERE part_id=? AND idx=?", (part_id, first)).fetchone()
            if cue_exists:
                conn.execute(
                    "INSERT OR IGNORE INTO word_contexts(word_id,part_id,cue,surface) VALUES(?,?,?,?)",
                    (word_id, part_id, first, surface or lemma))
    return get_word(word_id, part_id)


def get_word(word_id: int, part_id: int | None = None) -> dict[str, Any] | None:
    row = db.row("SELECT * FROM words WHERE id=?", (word_id,))
    if row is None:
        return None
    data = _word(row)
    if part_id:
        link = db.row("SELECT first_cue FROM word_parts WHERE word_id=? AND part_id=?",
                      (word_id, part_id))
        data["cue"] = link["first_cue"] if link else None
    data["parts"] = [r["part_id"] for r in db.rows(
        "SELECT part_id FROM word_parts WHERE word_id=? ORDER BY part_id", (word_id,))]
    return data


def _word(row) -> dict[str, Any]:
    now = db.now_iso()
    return {
        "id": row["id"],
        "lemma": row["lemma"],
        "display": row["display"] or row["lemma"],
        "phonetic": row["phonetic"] or "",
        "brief_zh": row["brief_zh"] or "",
        "pos": row["pos"] or "",
        "box": row["box"] or 0,
        "status": row["status"] or "active",
        "due_at": row["due_at"],
        "due": bool(row["due_at"] and row["due_at"] <= now),
        "reps": row["reps"] or 0,
        "lapses": row["lapses"] or 0,
        "note": row["note"] or "",
        "created_at": row["created_at"],
    }


def remove_word(word_id: int, part_id: int | None = None, forget: bool = False) -> dict[str, Any]:
    with db.tx() as conn:
        if part_id is not None and not forget:
            conn.execute("DELETE FROM word_parts WHERE word_id=? AND part_id=?", (word_id, part_id))
            conn.execute("DELETE FROM word_contexts WHERE word_id=? AND part_id=?", (word_id, part_id))
            conn.execute("DELETE FROM reviews WHERE word_id=? AND part_id=?", (word_id, part_id))
            remaining = conn.execute("SELECT COUNT(*) FROM word_parts WHERE word_id=?",
                                     (word_id,)).fetchone()[0]
            if remaining == 0:
                conn.execute("DELETE FROM reviews WHERE word_id=?", (word_id,))
                conn.execute("DELETE FROM words WHERE id=?", (word_id,))
                return {"deleted": True}
            return {"deleted": False, "remaining": remaining}
        conn.execute("DELETE FROM word_parts WHERE word_id=?", (word_id,))
        conn.execute("DELETE FROM word_contexts WHERE word_id=?", (word_id,))
        conn.execute("DELETE FROM reviews WHERE word_id=?", (word_id,))
        conn.execute("DELETE FROM words WHERE id=?", (word_id,))
    return {"deleted": True}


def batch_words(ids: list[Any], action: str) -> dict[str, Any]:
    if action not in ("ignore", "restore", "delete"):
        raise ValueError("未知的批量操作")
    clean: list[int] = []
    for raw in ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            clean.append(value)
    clean = sorted(set(clean))[:500]
    if not clean:
        return {"updated": 0, "action": action}
    marks = ",".join("?" for _ in clean)
    now = db.now_iso()
    with db.tx() as conn:
        if action in ("ignore", "restore"):
            status = "ignored" if action == "ignore" else "active"
            cur = conn.execute(
                f"UPDATE words SET status=?, updated_at=? WHERE id IN ({marks})",
                [status, now] + clean)
            return {"updated": cur.rowcount, "action": action}
        conn.execute(f"DELETE FROM reviews WHERE word_id IN ({marks})", clean)
        conn.execute(f"DELETE FROM word_parts WHERE word_id IN ({marks})", clean)
        conn.execute(f"DELETE FROM word_contexts WHERE word_id IN ({marks})", clean)
        cur = conn.execute(f"DELETE FROM words WHERE id IN ({marks})", clean)
        return {"updated": cur.rowcount, "action": action}


def update_word(word_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"status", "note"}
    sets, params = [], []
    for key in allowed:
        if key in fields:
            sets.append(f"{key}=?")
            params.append(fields[key])
    if not sets:
        return get_word(word_id)
    params.extend([db.now_iso(), word_id])
    with db.tx() as conn:
        conn.execute(f"UPDATE words SET {', '.join(sets)}, updated_at=? WHERE id=?", params)
    return get_word(word_id)


def rate(word_id: int, rating: int, part_id: int | None = None,
         elapsed_ms: int = 0) -> dict[str, Any] | None:
    row = db.row("SELECT * FROM words WHERE id=?", (word_id,))
    if row is None:
        return None
    now = _now()
    box_before = row["box"] or 0
    if rating == RATING_AGAIN:
        box, due = 0, _due_in(now, INTERVALS[0])
        lapses = (row["lapses"] or 0) + 1
    elif rating == RATING_HARD:
        box = min(5, max(1, box_before - 1))
        due = _due_in(now, max(0.5, INTERVALS[box] / 2))
        lapses = row["lapses"] or 0
    else:
        box = min(5, box_before + 1)
        due = _due_in(now, INTERVALS[box])
        lapses = row["lapses"] or 0
    with db.tx() as conn:
        conn.execute(
            "UPDATE words SET box=?, due_at=?, reps=reps+1, lapses=?, last_rating=?, "
            "updated_at=? WHERE id=?",
            (box, due, lapses, rating, now.isoformat(timespec="seconds"), word_id))
        conn.execute(
            "INSERT INTO reviews(word_id,part_id,rating,box_before,box_after,elapsed_ms,at) "
            "VALUES(?,?,?,?,?,?,?)",
            (word_id, part_id, rating, box_before, box, max(0, int(elapsed_ms or 0)),
             now.isoformat(timespec="seconds")))
        if part_id:
            conn.execute(
                "UPDATE part_progress SET reviewed_at=? WHERE part_id=?",
                (now.isoformat(timespec="seconds"), part_id))
    return get_word(word_id)


def _context_for(word_id: int, part_id: int | None) -> dict[str, Any] | None:
    if part_id:
        row = db.row(
            "SELECT wc.part_id, wc.cue, wc.surface, c.start, c.end, c.en, c.zh, c.speaker, "
            "p.title_en, p.title_zh, p.duration, p.number AS part_number "
            "FROM word_contexts wc JOIN cues c ON c.part_id=wc.part_id AND c.idx=wc.cue "
            "JOIN parts p ON p.id=wc.part_id "
            "WHERE wc.word_id=? AND wc.part_id=? ORDER BY wc.cue LIMIT 1", (word_id, part_id))
    else:
        row = db.row(
            "SELECT wc.part_id, wc.cue, wc.surface, c.start, c.end, c.en, c.zh, c.speaker, "
            "p.title_en, p.title_zh, p.duration, p.number AS part_number "
            "FROM word_contexts wc JOIN cues c ON c.part_id=wc.part_id AND c.idx=wc.cue "
            "JOIN parts p ON p.id=wc.part_id "
            "WHERE wc.word_id=? ORDER BY wc.part_id, wc.cue LIMIT 1", (word_id,))
    if row is None:
        return None
    return {
        "part_id": row["part_id"],
        "cue": row["cue"],
        "surface": row["surface"],
        "start": row["start"],
        "end": row["end"],
        "en": row["en"],
        "zh": row["zh"],
        "speaker": row["speaker"],
        "part_title_en": row["title_en"],
        "part_title_zh": row["title_zh"],
        "part_number": row["part_number"],
        "duration": row["duration"],
        "video_url": f"/media/{row['part_id']}",
    }


def queue(part_id: int | None = None, scope: str = "part", limit: int = 40) -> dict[str, Any]:
    now = db.now_iso()
    if scope == "part" and part_id:
        rows = db.rows(
            "SELECT w.*, wp.first_cue, wp.part_id AS link_part FROM words w "
            "JOIN word_parts wp ON wp.word_id=w.id "
            "WHERE wp.part_id=? AND w.status='active' "
            "ORDER BY CASE WHEN COALESCE(w.due_at,'') <= ? THEN 0 ELSE 1 END, "
            "COALESCE(w.due_at,''), w.box, w.id LIMIT ?", (part_id, now, limit))
        total = db.scalar(
            "SELECT COUNT(*) FROM words w JOIN word_parts wp ON wp.word_id=w.id "
            "WHERE wp.part_id=? AND w.status='active'", (part_id,), 0)
    else:
        cond = "w.status='active'"
        params: list[Any] = []
        if scope == "due":
            cond += " AND COALESCE(w.due_at,'') <= ?"
            params.append(now)
        rows = db.rows(
            f"SELECT w.*, NULL AS link_part FROM words w WHERE {cond} "
            "ORDER BY COALESCE(w.due_at,''), w.box, w.id LIMIT ?", params + [limit])
        total = db.scalar(f"SELECT COUNT(*) FROM words w WHERE {cond}", params, 0)
    items = []
    for row in rows:
        ctx_part = row["link_part"] if "link_part" in row.keys() else part_id
        data = _word(row)
        data["context"] = _context_for(row["id"], ctx_part)
        data["part_id"] = ctx_part
        items.append(data)
    return {"items": items, "total": total, "scope": scope, "part_id": part_id}


def wordbook(query: str = "", status: str = "all", chapter: str = "",
             limit: int = 60, offset: int = 0) -> dict[str, Any]:
    now = db.now_iso()
    where = ["1=1"]
    params: list[Any] = []
    if query:
        where.append("(w.lemma LIKE ? OR w.brief_zh LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if status == "due":
        where.append("w.status='active' AND COALESCE(w.due_at,'') <= ?")
        params.append(now)
    elif status == "new":
        where.append("w.status='active' AND w.box=0")
    elif status == "learning":
        where.append("w.status='active' AND w.box BETWEEN 1 AND 3")
    elif status == "mastered":
        where.append("w.status='active' AND w.box >= 4")
    elif status == "ignored":
        where.append("w.status='ignored'")
    elif status != "all":
        where.append("w.status='active'")
    if chapter:
        where.append(
            "EXISTS (SELECT 1 FROM word_parts wp JOIN parts p ON p.id=wp.part_id "
            "WHERE wp.word_id=w.id AND p.chapter_key=?)")
        params.append(chapter)
    total = db.scalar("SELECT COUNT(*) FROM words w WHERE " + " AND ".join(where), params, 0)
    rows = db.rows(
        "SELECT w.*, (SELECT COUNT(*) FROM word_parts wp WHERE wp.word_id=w.id) AS part_count, "
        "(SELECT COUNT(*) FROM word_contexts wc WHERE wc.word_id=w.id) AS hit_count, "
        "(SELECT MAX(at) FROM reviews r WHERE r.word_id=w.id) AS last_at "
        "FROM words w WHERE " + " AND ".join(where) +
        " ORDER BY CASE WHEN COALESCE(w.due_at,'') <= ? THEN 0 ELSE 1 END, "
        "COALESCE(w.due_at,''), w.id DESC LIMIT ? OFFSET ?",
        params + [now, limit, offset])
    items = []
    for row in rows:
        data = _word(row)
        data["part_count"] = row["part_count"]
        data["hit_count"] = row["hit_count"]
        data["last_at"] = row["last_at"]
        items.append(data)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def progress(part_id: int, position: float | None = None, event: str = "") -> dict[str, Any]:
    now = db.now_iso()
    with db.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO part_progress(part_id) VALUES(?)", (part_id,))
        if position is not None:
            conn.execute(
                "UPDATE part_progress SET position=?, last_opened_at=? WHERE part_id=?",
                (max(0.0, float(position)), now, part_id))
        if event == "preview":
            conn.execute("UPDATE part_progress SET previewed_at=? WHERE part_id=?", (now, part_id))
        elif event == "complete":
            conn.execute("UPDATE part_progress SET completed_at=? WHERE part_id=?", (now, part_id))
        elif event == "open":
            conn.execute("UPDATE part_progress SET last_opened_at=? WHERE part_id=?", (now, part_id))
        elif event == "review":
            conn.execute("UPDATE part_progress SET reviewed_at=? WHERE part_id=?", (now, part_id))
    row = db.row("SELECT * FROM part_progress WHERE part_id=?", (part_id,))
    return dict(row) if row else {}


def recent_activity(limit: int = 8) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT r.at, r.rating, w.lemma, w.display, p.title_en, p.title_zh, r.part_id "
        "FROM reviews r JOIN words w ON w.id = r.word_id "
        "LEFT JOIN parts p ON p.id = r.part_id ORDER BY r.at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def export_rows() -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT w.*, GROUP_CONCAT(p.title_en, ' / ') AS parts "
        "FROM words w LEFT JOIN word_parts wp ON wp.word_id=w.id "
        "LEFT JOIN parts p ON p.id=wp.part_id "
        "GROUP BY w.id ORDER BY w.id")
    out = []
    for row in rows:
        data = _word(row)
        data["word"] = data.get("display") or data.get("lemma") or ""
        data["parts"] = row["parts"] or ""
        data["due_date"] = (row["due_at"] or "")[:10]
        data["created_date"] = (row["created_at"] or "")[:10]
        out.append(data)
    return out
