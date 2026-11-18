"""Align the site's clean script to word-level ASR timings.

The script is the source of truth for text; the recording's auto-captions are
the source of truth for *when* something is spoken.  We align the two token
streams with a fuzzy Needleman-Wunsch pass, then turn matched word positions
into per-line cues.  Voice-clip durations keep cue lengths honest and place
lines that ASR missed.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from .asr import Word
from .transcript import DialogueLine

log = logging.getLogger(__name__)

_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
}
_CONTRACTION_RE = re.compile(r"[’']")


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text_en: str
    text_zh: str
    speaker_en: str | None
    speaker_zh: str | None
    match_ratio: float = 0.0
    matched_tokens: int = 0
    total_tokens: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.match_ratio

    def display_en(self) -> str:
        text = self.text_en.strip()
        if self.speaker_en and not text.lower().startswith(self.speaker_en.lower()):
            return f"{self.speaker_en}: {text}"
        return text

    def display_zh(self) -> str:
        text = self.text_zh.strip()
        if self.speaker_zh and not text.startswith(self.speaker_zh):
            return f"{self.speaker_zh}：{text}"
        return text

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.end - self.start, 3),
            "en": self.display_en(),
            "zh": self.display_zh(),
            "speaker_en": self.speaker_en,
            "speaker_zh": self.speaker_zh,
            "confidence": round(self.match_ratio, 3),
            "matched_tokens": self.matched_tokens,
            "total_tokens": self.total_tokens,
            "warnings": self.warnings,
        }


@dataclass
class AlignmentReport:
    cues: int
    script_tokens: int
    asr_words: int
    matched_pairs: int
    exact_pairs: int
    matched_lines: int
    match_ratio: float
    line_match_ratio: float
    mean_confidence: float
    window_start: float
    window_end: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cues": self.cues,
            "script_tokens": self.script_tokens,
            "asr_words": self.asr_words,
            "matched_pairs": self.matched_pairs,
            "exact_pairs": self.exact_pairs,
            "matched_lines": self.matched_lines,
            "match_ratio": round(self.match_ratio, 3),
            "line_match_ratio": round(self.line_match_ratio, 3),
            "mean_confidence": round(self.mean_confidence, 3),
            "window_start": round(self.window_start, 3),
            "window_end": round(self.window_end, 3),
            "warnings": self.warnings,
        }


class AlignmentError(RuntimeError):
    """The script and the recording could not be aligned confidently."""


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = _CONTRACTION_RE.sub("'", value)
    value = re.sub(r"(\d+):(\d+)", r"\1 \2", value)
    value = re.sub(r"[^a-z0-9']+", " ", value)
    return " ".join(value.split())


def tokens_for(text: str) -> list[str]:
    result: list[str] = []
    for token in _normalise(text).split():
        if token in _NUM_WORDS:
            token = _NUM_WORDS[token]
        token = token.strip("'")
        if token:
            result.append(token)
    return result


def _similarity(a: str, b: str, cache: dict[tuple[str, str], float]) -> float:
    key = (a, b)
    if key in cache:
        return cache[key]
    if a == b:
        score = 2.0
    elif a.startswith(b) or b.startswith(a):
        score = 1.2 if min(len(a), len(b)) >= 3 else 0.25
    else:
        ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
        if ratio >= 0.82:
            score = 1.0
        elif ratio >= 0.55:
            score = 0.25
        else:
            score = -0.6
    cache[key] = score
    return score


def _dp_align(
    script: list[str], words: list[str]
) -> tuple[list[tuple[int, int]], float]:
    n, m = len(script), len(words)
    if n * m > 6_000_000:
        raise AlignmentError(
            f"Alignment is too large ({n}x{m}); the transcript or video is unexpected."
        )
    neg = -1e9
    scores = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        scores[i][0] = scores[i - 1][0] - 0.55
        back[i][0] = 1
    for j in range(1, m + 1):
        scores[0][j] = scores[0][j - 1] - 0.35
        back[0][j] = 2

    sim_cache: dict[tuple[str, str], float] = {}
    for i in range(1, n + 1):
        a = script[i - 1]
        row = scores[i]
        prev = scores[i - 1]
        row_back = back[i]
        prev_back = back[i - 1]
        for j in range(1, m + 1):
            diag = prev[j - 1] + _similarity(a, words[j - 1], sim_cache)
            up = prev[j] - 0.55
            left = row[j - 1] - 0.35
            if diag >= up and diag >= left:
                row[j] = diag
                row_back[j] = 0
            elif up >= left:
                row[j] = up
                row_back[j] = 1
            else:
                row[j] = left
                row_back[j] = 2

    i, j = n, m
    pairs: list[tuple[int, int]] = []
    while i > 0 or j > 0:
        direction = back[i][j]
        if i > 0 and j > 0 and direction == 0:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif i > 0 and direction == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs, scores[n][m]


def _best_cluster(indices: list[int], words: list[Word], max_gap: float = 4.0) -> list[int]:
    if not indices:
        return []
    clusters: list[list[int]] = [[indices[0]]]
    for index in indices[1:]:
        if words[index].start - words[clusters[-1][-1]].start > max_gap:
            clusters.append([index])
        else:
            clusters[-1].append(index)
    return max(clusters, key=lambda group: (len(group), -group[0]))


def _estimate_duration(text_en: str, text_zh: str) -> float:
    # ~14 English characters or ~7 Chinese characters per second is a
    # conservative reading speed.  Used only when a voice clip is missing.
    estimate = max(len(text_en) / 14.0, len(text_zh) / 7.0)
    return max(1.0, min(8.0, estimate + 0.3))


def align_part(
    lines: list[DialogueLine],
    words: list[Word],
    window_start: float,
    window_end: float,
    voice_durations: dict[str, float | None] | None = None,
) -> tuple[list[Cue], AlignmentReport]:
    voice_durations = voice_durations or {}
    window = [
        word for word in words if window_start - 1.0 <= word.start < window_end + 1.0
    ]
    if len(window) < 5:
        raise AlignmentError(
            f"Only {len(window)} ASR words fall inside {window_start:.1f}-{window_end:.1f}s."
        )

    script_tokens: list[str] = []
    token_line: list[int] = []
    token_total: list[int] = []
    for line in lines:
        toks = tokens_for(line.text_en) or tokens_for(line.text_zh)
        token_total.append(len(toks))
        for token in toks:
            script_tokens.append(token)
            token_line.append(line.index)
    if not script_tokens:
        raise AlignmentError("The script produced no alignable tokens.")

    pairs, score = _dp_align(
        script_tokens,
        ["".join(_normalise(word.text).split()) for word in window],
    )
    exact = 0
    by_line: dict[int, list[int]] = {}
    for script_index, word_index in pairs:
        if script_tokens[script_index] == "".join(_normalise(window[word_index].text).split()):
            exact += 1
        by_line.setdefault(token_line[script_index], []).append(word_index)

    cue_data: list[dict] = []
    for line in lines:
        indices = sorted(by_line.get(line.index, []))
        cluster = _best_cluster(indices, window)
        total = max(1, token_total[line.index] if line.index < len(token_total) else 1)
        matched = len(indices)
        ratio = min(1.0, matched / total + (0.05 if matched else 0.0))
        if cluster:
            start = window[cluster[0]].start
            raw_end = window[cluster[-1]].end + 0.55
        else:
            start = None
            raw_end = None
        voice_duration = voice_durations.get(line.voice)
        estimate = _estimate_duration(line.text_en, line.text_zh)
        cue_data.append(
            {
                "line": line,
                "start": start,
                "raw_end": raw_end,
                "voice_duration": voice_duration,
                "estimate": estimate,
                "matched": matched,
                "total": total,
                "ratio": ratio,
            }
        )

    # Establish starts for every line, interpolating the ones ASR missed.
    first_matched = next((item for item in cue_data if item["start"] is not None), None)
    if first_matched is None:
        raise AlignmentError("No script line could be matched to the recording.")
    last_start = None
    last_duration = None
    for item in cue_data:
        if item["start"] is None:
            if last_start is None:
                item["start"] = window_start + 0.4
            else:
                # Place the missing line after the *previous* line has had
                # time to finish.  Using this line's own duration (often a
                # short "Whoa..." / "...") made the previous long cue end
                # prematurely.
                item["start"] = last_start + (last_duration or 1.0) + 0.25
        last_start = item["start"]
        last_duration = item["voice_duration"] or item["estimate"]

    # Pull interpolated lines back so they do not run into the next anchor.
    for index in range(len(cue_data) - 2, -1, -1):
        item = cue_data[index]
        nxt = cue_data[index + 1]
        if item["start"] >= nxt["start"] - 0.25:
            item["start"] = max(window_start + 0.1, nxt["start"] - 0.28)

    # Forward monotonic pass.
    for index in range(1, len(cue_data)):
        prev = cue_data[index - 1]
        item = cue_data[index]
        minimum = prev["start"] + 0.22
        if item["start"] < minimum:
            item["start"] = minimum

    cues: list[Cue] = []
    for index, item in enumerate(cue_data):
        line: DialogueLine = item["line"]
        start = max(window_start + 0.05, item["start"])
        if index + 1 < len(cue_data):
            max_end = cue_data[index + 1]["start"] - 0.06
        else:
            max_end = window_end - 0.08
        voice = item["voice_duration"]
        desired = start + max(
            item["estimate"],
            (voice + 0.35) if voice else 0.0,
            (item["raw_end"] - start) if item["raw_end"] else 0.0,
            max(1.0, len(line.text_en) / 18.0 + len(line.text_zh) / 10.0),
        )
        # Do not hold a line unnaturally long when ASR matched a distant
        # repeated word.
        ceiling = start + max(1.4, (voice + 1.8) if voice else item["estimate"] + 1.4)
        desired = min(desired, ceiling)
        end = max(start + 0.35, min(desired, max_end))
        if end <= start + 0.2:
            end = min(max_end, start + 0.2)
        warnings: list[str] = []
        if item["matched"] == 0:
            warnings.append("no ASR match; time interpolated")
        elif item["ratio"] < 0.35:
            warnings.append("low ASR match; check this cue")
        rel_start = max(0.02, start - window_start)
        rel_end = max(rel_start + 0.2, end - window_start)
        cues.append(
            Cue(
                index=line.index,
                start=round(rel_start, 3),
                end=round(rel_end, 3),
                text_en=line.text_en,
                text_zh=line.text_zh,
                speaker_en=line.speaker_en,
                speaker_zh=line.speaker_zh,
                match_ratio=item["ratio"],
                matched_tokens=item["matched"],
                total_tokens=item["total"],
                warnings=warnings,
            )
        )

    matched_lines = sum(1 for item in cue_data if item["matched"] > 0)
    total_pairs = max(1, len(pairs))
    _repair_timeline(cues, max(0.1, window_end - window_start))
    report = AlignmentReport(
        cues=len(cues),
        script_tokens=len(script_tokens),
        asr_words=len(window),
        matched_pairs=len(pairs),
        exact_pairs=exact,
        matched_lines=matched_lines,
        match_ratio=len(pairs) / max(1, len(script_tokens)),
        line_match_ratio=matched_lines / max(1, len(cues)),
        mean_confidence=sum(cue.match_ratio for cue in cues) / max(1, len(cues)),
        window_start=window_start,
        window_end=window_end,
    )
    report.warnings.append(f"alignment score {score:.1f}; exact token pairs {exact}/{total_pairs}")
    return cues, report


def _repair_timeline(cues: list[Cue], duration: float) -> None:
    """Remove small overlaps introduced by interpolated short lines."""
    if not cues:
        return
    gap = 0.06
    minimum = 0.18
    count = len(cues)

    # Clamp ends before the following cue's start.
    for index, cue in enumerate(cues):
        max_end = duration - 0.08 if index + 1 == count else cues[index + 1].start - gap
        if cue.end > max_end:
            cue.end = max_end

    # Forward pass: a cue may not start before the previous cue ends.
    for index, cue in enumerate(cues):
        if index > 0:
            cue.start = max(cue.start, cues[index - 1].end + gap)
        max_end = duration - 0.08 if index + 1 == count else cues[index + 1].start - gap
        if cue.end <= cue.start + minimum:
            cue.end = min(max_end, cue.start + minimum + 0.02)
        if cue.end <= cue.start:
            cue.end = cue.start + minimum

    # Final backward clamp, now that all starts are monotonic.
    for index in range(count - 1, -1, -1):
        cue = cues[index]
        max_end = duration - 0.08 if index + 1 == count else cues[index + 1].start - gap
        if cue.end > max_end:
            cue.end = max_end
        if cue.end <= cue.start:
            cue.end = cue.start + minimum


def validate_cues(
    cues: list[Cue], duration: float, report: AlignmentReport | None = None
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not cues:
        errors.append("no cues generated")
        return {"ok": False, "errors": errors, "warnings": warnings}

    previous_end = -1.0
    for cue in cues:
        if not cue.text_en.strip() or not cue.text_zh.strip():
            errors.append(f"cue {cue.index}: empty language text")
        if not (math.isfinite(cue.start) and math.isfinite(cue.end)):
            errors.append(f"cue {cue.index}: non-finite timestamp")
            continue
        if cue.start < -0.01 or cue.end > duration + 0.6:
            errors.append(
                f"cue {cue.index}: outside segment ({cue.start:.2f}-{cue.end:.2f}s of {duration:.2f}s)"
            )
        if cue.end <= cue.start:
            errors.append(f"cue {cue.index}: end <= start")
        if cue.start < previous_end - 0.06:
            errors.append(f"cue {cue.index}: overlaps previous cue")
        if cue.end - cue.start > 20:
            warnings.append(f"cue {cue.index}: unusually long ({cue.end - cue.start:.1f}s)")
        if cue.end - cue.start < 0.2:
            warnings.append(f"cue {cue.index}: unusually short ({cue.end - cue.start:.2f}s)")
        previous_end = max(previous_end, cue.end)

    if report is not None:
        if report.match_ratio < 0.55:
            errors.append(f"alignment coverage too low ({report.match_ratio:.0%})")
        if report.line_match_ratio < 0.70:
            errors.append(f"too few lines matched to ASR ({report.line_match_ratio:.0%})")
        if report.mean_confidence < 0.40:
            errors.append(f"mean line confidence too low ({report.mean_confidence:.0%})")
        low = [cue.index for cue in cues if cue.match_ratio < 0.35]
        if low:
            warnings.append(f"{len(low)} cue(s) have low ASR confidence: {low[:12]}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(cues: Iterable[Cue], *, bilingual: bool = False) -> str:
    blocks: list[str] = []
    for number, cue in enumerate(cues, start=1):
        if bilingual:
            text = f"{cue.display_en()}\n{cue.display_zh()}"
        else:
            text = cue.display_en()
        blocks.append(
            f"{number}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{text.strip()}\n"
        )
    return "\n".join(blocks)


def format_srt_zh(cues: Iterable[Cue]) -> str:
    blocks: list[str] = []
    for number, cue in enumerate(cues, start=1):
        blocks.append(
            f"{number}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n"
            f"{cue.display_zh().strip()}\n"
        )
    return "\n".join(blocks)
