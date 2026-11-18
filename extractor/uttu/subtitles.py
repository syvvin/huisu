"""SRT writing and independent re-parsing for validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIME_RE = re.compile(
    r"^(?P<h>\d{2,}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})\s*-->\s*"
    r"(?P<h2>\d{2,}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})$"
)


class SrtError(RuntimeError):
    pass


@dataclass
class SrtCue:
    number: int
    start: float
    end: float
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text: str) -> list[SrtCue]:
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise SrtError("SRT is empty")
    blocks = re.split(r"\n{2,}", text)
    cues: list[SrtCue] = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        try:
            number = int(lines[0].strip())
        except ValueError as exc:
            raise SrtError(f"Bad cue number: {lines[0]!r}") from exc
        if len(lines) < 2:
            raise SrtError(f"Cue {number} has no timestamp line")
        match = _TIME_RE.match(lines[1].strip())
        if not match:
            raise SrtError(f"Cue {number} has a malformed timestamp: {lines[1]!r}")
        start = _seconds(match.group("h"), match.group("m"), match.group("s"), match.group("ms"))
        end = _seconds(match.group("h2"), match.group("m2"), match.group("s2"), match.group("ms2"))
        if end <= start:
            raise SrtError(f"Cue {number} ends before it starts")
        cues.append(SrtCue(number=number, start=start, end=end, lines=lines[2:]))
    if not cues:
        raise SrtError("SRT contains no cues")
    for index, cue in enumerate(cues, start=1):
        if cue.number != index:
            raise SrtError(f"Cue numbering jumps at {cue.number} (expected {index})")
        if not cue.text.strip():
            raise SrtError(f"Cue {cue.number} is empty")
        if index > 1 and cue.start < cues[index - 2].end - 0.05:
            raise SrtError(f"Cue {cue.number} overlaps the previous cue")
    return cues


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        import os

        os.fsync(handle.fileno())
    tmp.replace(path)


def validate_srt_file(
    path: Path,
    *,
    expected_cues: int | None = None,
    duration: float | None = None,
    bilingual: bool = False,
) -> dict:
    """Parse a written SRT and check the properties a player will rely on."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        cues = parse_srt(text)
    except (OSError, SrtError) as exc:
        raise SrtError(f"{path.name}: {exc}") from exc
    errors: list[str] = []
    if expected_cues is not None and len(cues) != expected_cues:
        errors.append(f"expected {expected_cues} cues, found {len(cues)}")
    if duration is not None and cues[-1].end > duration + 1.0:
        errors.append(f"last cue ends at {cues[-1].end:.2f}s beyond {duration:.2f}s")
    if bilingual:
        for cue in cues:
            if len([line for line in cue.lines if line.strip()]) < 2:
                errors.append(f"cue {cue.number} is missing one language line")
                break
    if errors:
        raise SrtError(f"{path.name}: " + "; ".join(errors))
    return {
        "file": path.name,
        "cues": len(cues),
        "first": round(cues[0].start, 3),
        "last": round(cues[-1].end, 3),
    }
