"""Scan uttu-extractor output and keep the local library in sync."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import cefr, config, db, ecdict, text

CHAPTER_RE = re.compile(r"^chapter-(\d+)_(.+)$")
EVENT_CHAPTER_RE = re.compile(r"^(\d+)_(\d+)_(.+)$")
GENERIC_CHAPTER_RE = re.compile(r"^(\d+)[_-](.+)$")
PART_RE = re.compile(r"^part-(\d+)_(.+)$")

STORY_ORDER = ["main", "event", "character", "anecdote", "other", "cn"]
STORY_TITLES = {
    "main": ("Main Story", "主线故事"),
    "event": ("Event Story", "活动故事"),
    "character": ("Character Story", "角色故事"),
    "anecdote": ("Anecdotes", "轶事"),
    "other": ("Others", "其他"),
    "cn": ("CN Stories", "国服故事"),
}
IGNORED_STORY_DIRS = {".staging", "preview", "reports", "cache", "data"}


def _story_info(story_key: str) -> dict[str, Any]:
    title_en, title_zh = STORY_TITLES.get(
        story_key, (story_key.replace("_", " ").replace("-", " ").title() or "Other", ""))
    order = STORY_ORDER.index(story_key) if story_key in STORY_ORDER else 99
    return {"key": story_key, "title_en": title_en, "title_zh": title_zh, "order": order}


def _story_key_from_chapter(chapter_key: str) -> str:
    """Chapter keys are `main` names as-is, other stories are `event/<dir>`."""
    return chapter_key.split("/", 1)[0] if "/" in chapter_key else "main"


class LibraryError(RuntimeError):
    pass


def library_root() -> Path:
    configured = db.get_setting("library_root", "")
    if configured:
        path = Path(configured).expanduser()
    else:
        path = config.DEFAULT_LIBRARY_ROOT
    return path


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fingerprint(part_dir: Path) -> str:
    pieces = []
    for name in ("part.json", "en.srt", "zh.srt", "bilingual.srt"):
        f = part_dir / name
        if f.exists():
            st = f.stat()
            pieces.append(f"{name}:{int(st.st_mtime)}:{st.st_size}")
        else:
            pieces.append(f"{name}:-")
    return "|".join(pieces)


def scan(root: Path | None = None) -> list[dict[str, Any]]:
    root = Path(root or library_root())
    if not root.exists():
        return []
    found: list[dict[str, Any]] = []
    for part_json in sorted(root.rglob("part.json")):
        if ".staging" in part_json.parts or "cache" in part_json.parts:
            continue
        part_dir = part_json.parent
        try:
            rel = part_dir.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) < 3:
            continue
        story_key = rel.parts[0].lower()
        if story_key in IGNORED_STORY_DIRS or story_key.startswith("."):
            continue
        try:
            data = json.loads(part_json.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        chapter_name = part_dir.parent.name
        chapter_key = chapter_name
        chapter_idx = 0
        chapter_slug = chapter_name
        m = CHAPTER_RE.match(chapter_name)
        if m:
            chapter_idx = int(m.group(1))
            chapter_slug = m.group(2)
        else:
            em = EVENT_CHAPTER_RE.match(chapter_name)
            gm = GENERIC_CHAPTER_RE.match(chapter_name)
            if em:
                chapter_idx = int(em.group(1)) * 100 + int(em.group(2))
                chapter_slug = em.group(3)
            elif gm:
                chapter_idx = int(gm.group(1))
                chapter_slug = gm.group(2)
            else:
                chapter_idx = _int(chapter_name.split("_")[0], 0)
        if story_key != "main":
            chapter_key = f"{story_key}/{chapter_name}"
        story = _story_info(story_key)
        chapter_meta = data.get("chapter") or {}
        part_meta = data.get("part") or {}
        pm = PART_RE.match(part_dir.name)
        number = _int(part_meta.get("number"), int(pm.group(1)) if pm else 0)
        video = (data.get("video") or {}).get("file") or "video.mp4"
        video_path = part_dir / video
        if not video_path.exists():
            continue
        found.append({
            "dir": str(part_dir),
            "story_key": story_key,
            "story_title_en": story["title_en"],
            "story_title_zh": story["title_zh"],
            "story_order": story["order"],
            "chapter_key": chapter_key,
            "chapter_idx": chapter_idx,
            "chapter_slug": chapter_slug,
            "chapter_label": chapter_meta.get("label") or "",
            "chapter_title_en": chapter_meta.get("title_en") or chapter_slug.replace("-", " "),
            "chapter_title_zh": chapter_meta.get("title_zh") or "",
            "number": number,
            "slug": pm.group(2) if pm else part_dir.name,
            "title_en": part_meta.get("title_en") or (pm.group(2).replace("-", " ") if pm else part_dir.name),
            "title_zh": part_meta.get("title_zh") or "",
            "duration": float((data.get("video") or {}).get("duration") or 0),
            "size": _int((data.get("video") or {}).get("size")),
            "video_path": str(video_path),
            "en_srt": str(part_dir / "en.srt") if (part_dir / "en.srt").exists() else "",
            "zh_srt": str(part_dir / "zh.srt") if (part_dir / "zh.srt").exists() else "",
            "bilingual_srt": str(part_dir / "bilingual.srt") if (part_dir / "bilingual.srt").exists() else "",
            "part_json": str(part_json),
            "cues": _int((data.get("subtitles") or {}).get("bilingual", {}).get("cues")),
            "word_count": _int(part_meta.get("word_count")),
            "voice_lines": _int(part_meta.get("voice_lines")),
            "recording": (data.get("source") or {}).get("recording") or "",
            "generated_at": data.get("generated_at") or "",
            "fingerprint": _fingerprint(part_dir),
        })
    return found


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _cues_from_files(item: dict) -> list[text.Cue]:
    en_raw = _read(item["en_srt"]) if item["en_srt"] else ""
    zh_raw = _read(item["zh_srt"]) if item["zh_srt"] else ""
    en_cues = text.parse_srt(en_raw)
    zh_cues = text.parse_srt(zh_raw)
    if en_cues:
        return text.build_cues(en_cues, zh_cues)
    # fallback: two-line bilingual blocks
    bilingual = text.parse_srt(_read(item["bilingual_srt"])) if item["bilingual_srt"] else []
    en_cues, zh_cues = [], []
    for block in bilingual:
        lines = [line for line in block["text"].split("\n") if line.strip()]
        en_line = lines[0] if lines else ""
        zh_line = lines[1] if len(lines) > 1 else ""
        en_cues.append({"start": block["start"], "end": block["end"], "text": en_line})
        zh_cues.append({"start": block["start"], "end": block["end"], "text": zh_line})
    return text.build_cues(en_cues, zh_cues)


def sync(root: Path | None = None) -> dict[str, Any]:
    items = scan(root)
    existing = {r["dir"]: r for r in db.rows("SELECT id, dir, fingerprint FROM parts")}
    seen = set()
    new_parts = changed_parts = cue_rows = 0
    rebuild_ids: set[int] = set()
    now = db.now_iso()

    with db.tx() as conn:
        for item in items:
            seen.add(item["dir"])
            conn.execute(
                "INSERT INTO chapters(key,idx,label,title_en,title_zh,sort) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET idx=excluded.idx,label=excluded.label,"
                "title_en=excluded.title_en,title_zh=excluded.title_zh,sort=excluded.sort",
                (item["chapter_key"], item["chapter_idx"], item["chapter_label"],
                 item["chapter_title_en"], item["chapter_title_zh"],
                 item["story_order"] * 1000 + item["chapter_idx"]),
            )
            old = existing.get(item["dir"])
            params = (
                item["chapter_key"], item["number"], item["slug"], item["title_en"],
                item["title_zh"], item["duration"], item["video_path"], item["size"],
                item["en_srt"], item["zh_srt"], item["bilingual_srt"], item["part_json"],
                item["cues"], item["word_count"], item["voice_lines"], item["recording"],
                item["generated_at"], item["fingerprint"], now, item["dir"],
            )
            if old is None:
                conn.execute(
                    "INSERT INTO parts(chapter_key,number,slug,title_en,title_zh,duration,"
                    "video_path,size,en_srt,zh_srt,bilingual_srt,part_json,cues,word_count,"
                    "voice_lines,recording,generated_at,fingerprint,missing,synced_at,dir) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)", params)
                new_parts += 1
            else:
                conn.execute(
                    "UPDATE parts SET chapter_key=?,number=?,slug=?,title_en=?,title_zh=?,"
                    "duration=?,video_path=?,size=?,en_srt=?,zh_srt=?,bilingual_srt=?,part_json=?,"
                    "cues=?,word_count=?,voice_lines=?,recording=?,generated_at=?,fingerprint=?,"
                    "missing=0,synced_at=?,dir=? WHERE id=?",
                    params + (old["id"],))
                if old["fingerprint"] != item["fingerprint"]:
                    changed_parts += 1
                    rebuild_ids.add(old["id"])
            conn.execute("UPDATE parts SET missing=0 WHERE dir=?", (item["dir"],))

        missing_ids: list[int] = []
        if items:
            for dir_name, old in existing.items():
                if dir_name not in seen:
                    conn.execute("UPDATE parts SET missing=1 WHERE dir=?", (dir_name,))
                    missing_ids.append(old["id"])
        if missing_ids:
            marks = ",".join("?" for _ in missing_ids)
            for table in ("part_progress", "word_parts", "word_contexts", "reviews"):
                conn.execute(f"DELETE FROM {table} WHERE part_id IN ({marks})", missing_ids)

        # Rebuild cues when the source changed or the sync algorithm moved on.
        need = db.rows("SELECT id FROM parts WHERE missing=0")
        version = db.meta_get("sync_version", 0)
        rebuild_all = version != config.SYNC_VERSION
        for part in need:
            has_cues = db.scalar("SELECT COUNT(*) FROM cues WHERE part_id=?", (part["id"],), 0)
            if not (not has_cues or rebuild_all or part["id"] in rebuild_ids):
                continue
            row = db.row("SELECT * FROM parts WHERE id=?", (part["id"],))
            cues = _cues_from_files({
                "en_srt": row["en_srt"], "zh_srt": row["zh_srt"],
                "bilingual_srt": row["bilingual_srt"],
            })
            conn.execute("DELETE FROM cues WHERE part_id=?", (row["id"],))
            payload = []
            for cue in cues:
                tokens = text.tokenize(cue.en)
                payload.append((row["id"], cue.idx, cue.start, cue.end, cue.speaker,
                                cue.en, cue.zh,
                                json.dumps(tokens, ensure_ascii=False, separators=(",", ":"))))
            conn.executemany(
                "INSERT INTO cues(part_id,idx,start,end,speaker,en,zh,tokens) VALUES(?,?,?,?,?,?,?,?)",
                payload)
            cue_rows += len(payload)

    # Corpus statistics are cheap and let 预习 rank candidates by rarity.
    if rebuild_all or new_parts or changed_parts:
        _refresh_corpus_stats()
    db.meta_set("sync_version", config.SYNC_VERSION)
    db.meta_set("last_sync", db.now_iso())
    db.meta_set("library_root", str(root or library_root()))
    return {"parts": len(items), "new": new_parts, "changed": changed_parts,
            "cues": cue_rows, "root": str(root or library_root())}


def _refresh_corpus_stats() -> None:
    """Document frequency per lemma across all parts, plus proper-noun hints."""
    df: dict[str, int] = {}
    lower_seen: dict[str, int] = {}
    upper_only: dict[str, int] = {}
    for part in db.rows("SELECT id FROM parts WHERE missing=0"):
        lemmas = set()
        for cue in db.rows("SELECT en, tokens FROM cues WHERE part_id=?", (part["id"],)):
            en = cue["en"] or ""
            for start, end, lemma in json.loads(cue["tokens"] or "[]"):
                lemmas.add(lemma)
                surface = en[start:end]
                if surface and surface[0].islower():
                    lower_seen[lemma] = lower_seen.get(lemma, 0) + 1
                elif surface and surface[0].isupper():
                    upper_only[lemma] = upper_only.get(lemma, 0) + 1
        for lemma in lemmas:
            df[lemma] = df.get(lemma, 0) + 1
    proper = {lemma for lemma in upper_only
              if lower_seen.get(lemma, 0) == 0 and len(lemma) > 1}
    db.meta_set("corpus_df", df)
    db.meta_set("proper_nouns", sorted(proper))


def part_payload(part_id: int) -> dict[str, Any] | None:
    part = db.row(
        "SELECT p.*, c.label AS chapter_label, c.title_en AS chapter_title_en, "
        "c.title_zh AS chapter_title_zh, c.idx AS chapter_idx "
        "FROM parts p JOIN chapters c ON c.key = p.chapter_key WHERE p.id=?", (part_id,))
    if part is None:
        return None
    cues = []
    for cue in db.rows("SELECT * FROM cues WHERE part_id=? ORDER BY idx", (part_id,)):
        cues.append({
            "i": cue["idx"],
            "start": cue["start"],
            "end": cue["end"],
            "speaker": cue["speaker"],
            "en": cue["en"],
            "zh": cue["zh"],
            "tokens": json.loads(cue["tokens"] or "[]"),
        })
    progress = db.row("SELECT * FROM part_progress WHERE part_id=?", (part_id,))
    speakers: dict[str, int] = {}
    for cue in cues:
        speakers[cue["speaker"]] = speakers.get(cue["speaker"], 0) + 1
    top_speakers = [{"name": name, "count": count}
                    for name, count in sorted(speakers.items(), key=lambda kv: -kv[1])
                    if name]
    words = part_words(part_id)
    story_key = _story_key_from_chapter(part["chapter_key"])
    story = _story_info(story_key)
    return {
        "id": part["id"],
        "number": part["number"],
        "slug": part["slug"],
        "title_en": part["title_en"],
        "title_zh": part["title_zh"],
        "story_key": story_key,
        "story_title_en": story["title_en"],
        "story_title_zh": story["title_zh"],
        "story_order": story["order"],
        "chapter_key": part["chapter_key"],
        "chapter_label": part["chapter_label"],
        "chapter_title_en": part["chapter_title_en"],
        "chapter_title_zh": part["chapter_title_zh"],
        "chapter_idx": part["chapter_idx"],
        "duration": part["duration"],
        "cues": part["cues"],
        "word_count": part["word_count"],
        "voice_lines": part["voice_lines"],
        "recording": part["recording"],
        "generated_at": part["generated_at"],
        "video_url": f"/media/{part['id']}",
        "thumb_url": f"/thumb/{part['id']}.jpg",
        "speakers": top_speakers[:5],
        "words": words,
        "progress": {
            "position": (progress["position"] if progress else 0) or 0,
            "previewed_at": progress["previewed_at"] if progress else None,
            "completed_at": progress["completed_at"] if progress else None,
            "reviewed_at": progress["reviewed_at"] if progress else None,
        },
        "subtitles": cues,
    }


def part_words(part_id: int) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT w.*, wp.first_cue FROM word_parts wp JOIN words w ON w.id = wp.word_id "
        "WHERE wp.part_id=? ORDER BY COALESCE(wp.first_cue, 99999)", (part_id,))
    return [_word_json(r) for r in rows]


def word_sources(lemma: str, focus_part: int | None = None) -> list[dict[str, Any]]:
    """Every saved occurrence of a word, focus part first, other parts after."""
    lemma = (lemma or "").strip().lower()
    if not lemma:
        return []
    rows = db.rows(
        "SELECT wc.part_id, wc.cue, wc.surface, p.number AS part_number, "
        "p.title_en AS part_title_en, p.title_zh AS part_title_zh, "
        "ch.key AS chapter_key, ch.idx AS chapter_idx, ch.label AS chapter_label, "
        "ch.title_en AS chapter_title_en, ch.title_zh AS chapter_title_zh, "
        "c.start, c.end, c.speaker, c.en, c.zh, wp.added_at "
        "FROM word_contexts wc "
        "JOIN words w ON w.id = wc.word_id "
        "JOIN parts p ON p.id = wc.part_id "
        "LEFT JOIN chapters ch ON ch.key = p.chapter_key "
        "LEFT JOIN word_parts wp ON wp.word_id = wc.word_id AND wp.part_id = wc.part_id "
        "JOIN cues c ON c.part_id = wc.part_id AND c.idx = wc.cue "
        "WHERE w.lemma = ? AND p.missing = 0 "
        "ORDER BY ch.sort, ch.idx, p.number, wc.cue",
        (lemma,))
    items = []
    for r in rows:
        story_key = _story_key_from_chapter(r["chapter_key"] or "")
        story = _story_info(story_key)
        items.append({
            "part_id": r["part_id"],
            "part_number": r["part_number"],
            "part_title_en": r["part_title_en"],
            "part_title_zh": r["part_title_zh"],
            "story_key": story_key,
            "story_title_en": story["title_en"],
            "story_title_zh": story["title_zh"],
            "story_order": story["order"],
            "chapter_key": r["chapter_key"] or "",
            "chapter_idx": r["chapter_idx"],
            "chapter_label": r["chapter_label"],
            "chapter_title_en": r["chapter_title_en"],
            "chapter_title_zh": r["chapter_title_zh"],
            "cue": r["cue"],
            "surface": r["surface"],
            "start": r["start"],
            "end": r["end"],
            "speaker": r["speaker"],
            "en": r["en"],
            "zh": r["zh"],
            "added_at": r["added_at"],
            "video_url": f"/media/{r['part_id']}",
            "focus": bool(focus_part and r["part_id"] == focus_part),
        })
    if focus_part:
        items.sort(key=lambda s: 0 if s["part_id"] == focus_part else 1)
    return items


def _word_json(row) -> dict[str, Any]:
    now = db.now_iso()
    due = row["due_at"] or now
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
        "cue": row["first_cue"] if "first_cue" in row.keys() else None,
        "created_at": row["created_at"],
    }


def _aggregate(parts: list[dict]) -> dict[int, dict[str, int]]:
    ids = [p["id"] for p in parts]
    stats = {pid: {"added": 0, "mastered": 0, "due": 0, "boxes": 0, "reviewed_today": 0}
             for pid in ids}
    if not ids:
        return stats
    placeholders = ",".join("?" for _ in ids)
    now = db.now_iso()
    for row in db.rows(
            f"SELECT wp.part_id AS pid, COUNT(*) AS n, SUM(w.box) AS boxes, "
            f"SUM(CASE WHEN w.box >= 4 THEN 1 ELSE 0 END) AS mastered, "
            f"SUM(CASE WHEN w.status='active' AND COALESCE(w.due_at, '') <= ? THEN 1 ELSE 0 END) AS due "
            f"FROM word_parts wp JOIN words w ON w.id=wp.word_id "
            f"WHERE wp.part_id IN ({placeholders}) GROUP BY wp.part_id", [now] + ids):
        s = stats.get(row["pid"])
        if s:
            s["added"] = row["n"] or 0
            s["boxes"] = row["boxes"] or 0
            s["mastered"] = row["mastered"] or 0
            s["due"] = row["due"] or 0
    for row in db.rows(
            f"SELECT part_id AS pid, COUNT(*) AS n FROM reviews "
            f"WHERE at >= ? AND part_id IN ({placeholders}) GROUP BY part_id",
            [now[:10]] + ids):
        s = stats.get(row["pid"])
        if s:
            s["reviewed_today"] = row["n"] or 0
    return stats


def library() -> dict[str, Any]:
    chapters = []
    for chapter in db.rows("SELECT * FROM chapters ORDER BY sort, idx, key"):
        parts = db.rows(
            "SELECT p.*, pp.position, pp.previewed_at, pp.completed_at, pp.reviewed_at "
            "FROM parts p LEFT JOIN part_progress pp ON pp.part_id = p.id "
            "WHERE p.chapter_key=? AND p.missing=0 ORDER BY p.number", (chapter["key"],))
        agg = _aggregate(parts)
        part_rows = []
        for part in parts:
            s = agg.get(part["id"], {})
            added = s.get("added", 0)
            boxes = s.get("boxes", 0)
            duration = part["duration"] or 1
            position = part["position"] or 0
            watched = bool(part["completed_at"]) or (position / duration) >= 0.95
            part_rows.append({
                "id": part["id"],
                "number": part["number"],
                "title_en": part["title_en"],
                "title_zh": part["title_zh"],
                "duration": part["duration"],
                "cues": part["cues"],
                "word_count": part["word_count"],
                "thumb_url": f"/thumb/{part['id']}.jpg",
                "added": added,
                "mastered": s.get("mastered", 0),
                "due": s.get("due", 0),
                "progress": round(boxes / (5 * added), 3) if added else 0,
                "position": position,
                "watched": watched,
                "previewed": bool(part["previewed_at"]),
                "reviewed": bool(part["reviewed_at"]),
                "reviewed_today": s.get("reviewed_today", 0),
            })
        if not part_rows:
            continue
        story_key = _story_key_from_chapter(chapter["key"])
        story = _story_info(story_key)
        chapters.append({
            "key": chapter["key"],
            "idx": chapter["idx"],
            "label": chapter["label"],
            "title_en": chapter["title_en"],
            "title_zh": chapter["title_zh"],
            "story_key": story_key,
            "story_title_en": story["title_en"],
            "story_title_zh": story["title_zh"],
            "story_order": story["order"],
            "parts": part_rows,
            "words": sum(p["added"] for p in part_rows),
            "due": sum(p["due"] for p in part_rows),
        })
    story_map: dict[str, dict[str, Any]] = {}
    for chapter in chapters:
        story = story_map.setdefault(chapter["story_key"], {
            "key": chapter["story_key"],
            "title_en": chapter["story_title_en"],
            "title_zh": chapter["story_title_zh"],
            "order": chapter["story_order"],
            "chapters": 0, "parts": 0, "words": 0, "due": 0,
        })
        story["chapters"] += 1
        story["parts"] += len(chapter["parts"])
        story["words"] += chapter["words"]
        story["due"] += chapter["due"]
    stories = sorted(story_map.values(), key=lambda s: (s["order"], s["title_en"]))
    return {"chapters": chapters, "stories": stories, "totals": totals(), "root": str(library_root()),
            "last_sync": db.meta_get("last_sync")}


def totals() -> dict[str, Any]:
    now = db.now_iso()
    today = now[:10]
    total = db.scalar("SELECT COUNT(*) FROM words WHERE status='active'", (), 0)
    due = db.scalar(
        "SELECT COUNT(*) FROM words WHERE status='active' AND COALESCE(due_at, '') <= ?",
        (now,), 0)
    mastered = db.scalar(
        "SELECT COUNT(*) FROM words WHERE status='active' AND box >= 4", (), 0)
    reviewed_today = db.scalar(
        "SELECT COUNT(DISTINCT word_id) FROM reviews WHERE at >= ?", (today,), 0)
    return {
        "words": total,
        "due": due,
        "mastered": mastered,
        "reviewed_today": reviewed_today,
        "parts": db.scalar("SELECT COUNT(*) FROM parts WHERE missing=0", (), 0),
        "dict": ecdict.lookup_stats(),
    }


def candidates(part_id: int, levels: list[str] | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """CEFR-filtered candidates for one part, excluding known/function words."""
    part = db.row("SELECT * FROM parts WHERE id=?", (part_id,))
    if part is None or not cefr.available():
        return []
    if levels is None:
        levels = db.settings().get("preview_cefr_levels") or ["B1", "B2"]
    allowed = ("A1", "A2", "B1", "B2", "C1", "C2")
    selected = {level for level in allowed if level in set(levels)}
    if not selected:
        return []
    cues = db.rows("SELECT idx, en, tokens FROM cues WHERE part_id=? ORDER BY idx", (part_id,))
    known = {r["lemma"] for r in db.rows("SELECT lemma FROM words")}
    proper = set(db.meta_get("proper_nouns", []) or [])
    agg: dict[str, dict[str, Any]] = {}
    for cue in cues:
        en = cue["en"] or ""
        for start, end, lemma in json.loads(cue["tokens"] or "[]"):
            if len(lemma) < 2 or len(lemma) > 32 or not lemma.isalpha():
                continue
            surface = en[start:end]
            if lemma in text.STOPWORDS or surface.lower() in text.STOPWORDS:
                continue
            if lemma in known or lemma in proper:
                continue
            info = cefr.entry(lemma)
            if not info or info.get("level") not in selected:
                continue
            item = agg.setdefault(lemma, {
                "lemma": lemma, "surfaces": set(), "count": 0,
                "first_cue": cue["idx"], "first_surface": surface,
                "cefr": info,
            })
            item["count"] += 1
            item["surfaces"].add(surface)
    out = []
    for lemma, item in agg.items():
        info = item["cefr"]
        entry = ecdict.entry(lemma)
        phonetic, brief = text.word_brief(entry)
        out.append({
            "lemma": lemma,
            "display": item["first_surface"],
            "count": item["count"],
            "cue": item["first_cue"],
            "phonetic": phonetic,
            "brief_zh": brief,
            "level": info.get("level", ""),
            "level_num": info.get("level_num", 0),
            "frequency": info.get("frequency", 0),
            "pos_levels": info.get("pos_levels", []),
            "in_dict": entry is not None,
        })
    out.sort(key=lambda x: (-x["level_num"], -x["count"], x["lemma"]))
    return out[:limit]


def search_cues(query: str, limit: int = 40) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if len(query) < 2:
        return []
    like = f"%{query}%"
    rows = db.rows(
        "SELECT c.part_id, c.idx, c.en, c.zh, c.start, p.title_en, p.title_zh "
        "FROM cues c JOIN parts p ON p.id=c.part_id "
        "WHERE p.missing=0 AND (c.en LIKE ? OR c.zh LIKE ?) LIMIT ?",
        (like, like, limit))
    return [{
        "part_id": r["part_id"], "cue": r["idx"], "en": r["en"], "zh": r["zh"],
        "start": r["start"], "title_en": r["title_en"], "title_zh": r["title_zh"],
    } for r in rows]
