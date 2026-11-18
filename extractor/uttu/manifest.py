"""Checksum manifest for a delivered output tree."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict:
    root = root.resolve()
    entries = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if parts and parts[0] in (".staging", "reports", "preview"):
            continue
        if relative.name.startswith("validation") or relative.name == "MANIFEST.json":
            continue
        if path.suffix.lower() not in (".mp4", ".srt", ".json"):
            continue
        size = path.stat().st_size
        total += size
        entries.append(
            {
                "path": relative.as_posix(),
                "size": size,
                "sha256": _sha256(path),
            }
        )
    return {
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "files": len(entries),
        "total_bytes": total,
        "entries": entries,
    }


def write_manifest(root: Path, output: Path | None = None) -> Path:
    manifest = build_manifest(root)
    target = output or (root.resolve() / "MANIFEST.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return target
