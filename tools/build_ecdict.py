"""Build the offline ECDICT dictionary database used by the vocab app.

Source data: https://github.com/skywind3000/ECDICT (MIT License)
Download ecdict.csv (~52 MB) and either:
  * pass --csv path/to/ecdict.csv
  * set the ECDICT_CSV environment variable
  * put it at tools/ecdict.csv

Usage: python tools/build_ecdict.py [--csv path/to/ecdict.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "dict" / "ecdict.db"
DOWNLOAD = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"

SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE entries (
    word TEXT PRIMARY KEY,
    phonetic TEXT,
    definition TEXT,
    translation TEXT,
    pos TEXT,
    collins TEXT,
    oxford TEXT,
    tag TEXT,
    bnc INTEGER,
    frq INTEGER,
    exchange TEXT,
    detail TEXT,
    audio TEXT
);
CREATE TABLE lemmas (form TEXT PRIMARY KEY, lemma TEXT NOT NULL);
"""


def int_or_zero(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_exchange(word: str, exchange: str):
    """Yield (form, lemma) pairs from an ECDICT `exchange` field.

    Format: key:value pairs separated by "/". Forms: p past, d past participle,
    i -ing, 3 third person, r comparative, t superlative, s plural.
    Key 0 means "this word is an inflected form of the value".
    """
    if not exchange:
        return
    for chunk in exchange.split("/"):
        if ":" not in chunk:
            continue
        key, _, value = chunk.partition(":")
        value = value.strip().lower()
        if not value:
            continue
        if key == "0":
            yield word, value
        elif key in {"p", "d", "i", "3", "r", "t", "s"}:
            yield value, word


def normalize_lemma_map(pairs):
    """Drop self-maps and conflicting chains so lookups stay one hop."""
    mapping = {}
    for form, lemma in pairs:
        if form == lemma:
            continue
        mapping.setdefault(form, lemma)
    # one level of dereference for chains like "went -> go" where a form also has 0:
    for form, lemma in list(mapping.items()):
        if lemma in mapping and mapping[lemma] != lemma:
            target = mapping[lemma]
            if target != form:
                mapping[form] = target
    return mapping


def find_csv(explicit: str | None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("ECDICT_CSV"):
        candidates.append(Path(os.environ["ECDICT_CSV"]))
    candidates.append(ROOT / "tools" / "ecdict.csv")
    candidates.append(ROOT / "data" / "dict" / "ecdict.csv")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--target", default=str(TARGET))
    args = ap.parse_args()

    csv_path = find_csv(args.csv)
    if csv_path is None:
        print("找不到 ecdict.csv。下载后放到 tools/ecdict.csv，或使用 --csv 指定：\n"
              f"  {DOWNLOAD}", file=sys.stderr)
        return 1
    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    print(f"reading {csv_path} ...")
    started = time.time()
    conn = sqlite3.connect(target)
    conn.executescript(SCHEMA)

    pair_rows = []
    n = 0
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        batch = []
        for row in reader:
            word = (row.get("word") or "").strip()
            if not word:
                continue
            batch.append((
                word,
                (row.get("phonetic") or "").strip(),
                (row.get("definition") or "").strip(),
                (row.get("translation") or "").strip(),
                (row.get("pos") or "").strip(),
                (row.get("collins") or "").strip(),
                (row.get("oxford") or "").strip(),
                (row.get("tag") or "").strip(),
                int_or_zero(row.get("bnc") or ""),
                int_or_zero(row.get("frq") or ""),
                (row.get("exchange") or "").strip(),
                (row.get("detail") or "").strip(),
                (row.get("audio") or "").strip(),
            ))
            pair_rows.extend(parse_exchange(word.lower(), (row.get("exchange") or "").strip()))
            n += 1
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)

    lemma_map = normalize_lemma_map(pair_rows)
    conn.executemany("INSERT OR REPLACE INTO lemmas VALUES (?,?)", lemma_map.items())
    conn.commit()

    # compact + indexes (word is already the primary key)
    conn.execute("CREATE INDEX idx_entries_frq ON entries(frq)")
    conn.execute("VACUUM")
    conn.commit()
    entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    lemmas = conn.execute("SELECT COUNT(*) FROM lemmas").fetchone()[0]
    conn.close()

    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "ECDICT (MIT) https://github.com/skywind3000/ECDICT",
        "entries": entries,
        "lemma_forms": lemmas,
        "seconds": round(time.time() - started, 1),
        "size": target.stat().st_size,
    }
    (target.parent / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
