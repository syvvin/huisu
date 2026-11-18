"""Build the compact CEFR map bundled at vocab/data/cefr.json.gz.

Usage:
  python tools/build_cefr.py --words words.csv --word-pos word_pos.csv \
      --pos-tags pos_tags.csv --out vocab/data/cefr.json.gz

The three CSVs come from:
https://github.com/Maximax67/Words-CEFR-Dataset (MIT).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


def bucket(level: float) -> int:
    if level < 1.5:
        return 1
    if level < 2.5:
        return 2
    if level < 3.5:
        return 3
    if level < 4.5:
        return 4
    if level < 5.5:
        return 5
    return 6


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--words", required=True)
    parser.add_argument("--word-pos", required=True)
    parser.add_argument("--pos-tags", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    pos_tags: dict[str, str] = {}
    with open(args.pos_tags, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pos_tags[row["tag_id"]] = row["tag"]

    words: dict[str, str] = {}
    with open(args.words, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            word = (row.get("word") or "").strip().lower()
            if word:
                words[row["word_id"]] = word

    by_word: dict[str, list[dict]] = defaultdict(list)
    with open(args.word_pos, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            word = words.get(row.get("word_id", ""))
            if not word:
                continue
            try:
                level = float(row.get("level") or 0)
                frequency = int(row.get("frequency_count") or 0)
            except ValueError:
                continue
            if level <= 0:
                continue
            by_word[word].append({
                "pos": pos_tags.get(row.get("pos_tag_id", ""), "?"),
                "level": round(level, 3),
                "frequency": frequency,
            })

    out: dict[str, dict] = {}
    for word, entries in by_word.items():
        posmap: dict[str, dict] = {}
        for item in entries:
            old = posmap.get(item["pos"])
            if old is None or item["frequency"] > old["frequency"]:
                posmap[item["pos"]] = item
        parts = sorted(posmap.values(), key=lambda item: (-item["frequency"], item["level"], item["pos"]))
        dominant = parts[0]
        level_num = bucket(dominant["level"])
        out[word] = {
            "level": LEVELS[level_num - 1],
            "level_num": level_num,
            "level_raw": dominant["level"],
            "frequency": sum(item["frequency"] for item in parts),
            "pos_levels": [{
                "pos": item["pos"],
                "level": LEVELS[bucket(item["level"]) - 1],
                "level_num": bucket(item["level"]),
                "level_raw": item["level"],
                "frequency": item["frequency"],
            } for item in parts[:6]],
        }

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9))
    print(f"{len(out)} words -> {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
