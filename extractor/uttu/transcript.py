"""Turn the site's transcript HTML into ordered, paired dialogue lines.

The site marks every spoken line with ``data-voice``.  English and Chinese
pages use *the same Voice IDs in the same order*, so pairing by ID is exact
and does not depend on line lengths or translations.  Stage directions,
choices, images and music cues deliberately do not carry ``data-voice`` and
are therefore excluded from the subtitle track.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

log = logging.getLogger(__name__)

_BLOCK_TAGS = {
    "div",
    "p",
    "img",
    "hr",
    "table",
    "ul",
    "ol",
    "section",
    "figure",
    "video",
    "iframe",
}
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_SPACE_RE = re.compile(r"[ \t\r\n\f\v\u00a0]+")
_SPEAKER_RE = re.compile(r"^(?P<name>[^:：]{1,60})[:：]\s*$")


class TranscriptError(RuntimeError):
    """Transcript HTML is not usable or cannot be paired across locales."""


@dataclass
class VoiceLine:
    voice: str
    speaker: str | None
    text: str


@dataclass
class DialogueLine:
    index: int
    voice: str
    speaker_en: str | None
    text_en: str
    speaker_zh: str | None
    text_zh: str
    warnings: list[str] = field(default_factory=list)

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


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def _clean_speaker(value: str | None) -> str | None:
    text = clean_text(value)
    text = text.strip(" :：")
    return text or None


def _inline_text(el: Tag) -> str:
    """Collect the dialogue text that follows a ``<b>Speaker:</b>`` tag.

    Stops at a line break, a block element, the next voiced element, or
    another speaker label.  Inline emphasis such as ``<b>I'm gonna kill
    you!!</b>`` is kept because it is part of the same line.
    """
    parts: list[str] = []
    node = el.next_sibling
    while node is not None:
        if isinstance(node, Tag):
            if node.name in ("br", "hr") or node.name in _BLOCK_TAGS:
                break
            if node.get("data-voice"):
                break
            if node.name == "b":
                inner = clean_text(node.get_text(" ", strip=True))
                if _SPEAKER_RE.match(inner):
                    break
                parts.append(inner)
            elif node.name == "i":
                parts.append(clean_text(node.get_text(" ", strip=True)))
            else:
                parts.append(clean_text(node.get_text(" ", strip=True)))
        else:
            parts.append(str(node))
        node = node.next_sibling
    return clean_text(" ".join(parts))


def extract_voice_records(content_html: str) -> list[VoiceLine]:
    """Return every voiced line in document order, with its speaker/text."""
    if not content_html or not content_html.strip():
        return []
    soup = BeautifulSoup(content_html, "lxml")
    records: list[VoiceLine] = []
    for el in soup.select("[data-voice]"):
        voice = el.get("data-voice")
        if not voice:
            continue
        if el.name == "b":
            speaker = _clean_speaker(el.get_text(" ", strip=True))
            text = _inline_text(el)
            # Very occasionally the site puts the line in the following
            # paragraph instead of as a sibling text node.
            if not text and el.parent is not None:
                parent_text = clean_text(el.parent.get_text(" ", strip=True))
                if speaker and parent_text.startswith(speaker):
                    parent_text = parent_text[len(speaker) :].lstrip(" :：")
                if parent_text and parent_text != speaker:
                    text = parent_text
            if speaker and text.lower().startswith(speaker.lower() + ":"):
                text = text[len(speaker) + 1 :].strip()
            records.append(VoiceLine(voice=str(voice), speaker=speaker, text=text))
        else:
            records.append(
                VoiceLine(
                    voice=str(voice),
                    speaker=None,
                    text=clean_text(el.get_text(" ", strip=True)),
                )
            )
    return records


def _pair_by_voice(
    en: list[VoiceLine], zh: list[VoiceLine]
) -> tuple[list[tuple[VoiceLine, VoiceLine]], list[str]]:
    warnings: list[str] = []
    if [item.voice for item in en] == [item.voice for item in zh]:
        return list(zip(en, zh)), warnings

    en_keys = [item.voice for item in en]
    zh_keys = [item.voice for item in zh]
    if set(en_keys) == set(zh_keys) and len(en_keys) == len(zh_keys):
        by_key = {item.voice: item for item in zh}
        warnings.append("Voice IDs were in a different order across locales; reordered by ID.")
        return [(item, by_key[item.voice]) for item in en], warnings

    # Same IDs but duplicated/missing occurrences: pair the longest common
    # subsequence of IDs and refuse the rest rather than guessing.
    import difflib

    matcher = difflib.SequenceMatcher(a=en_keys, b=zh_keys, autojunk=False)
    pairs: list[tuple[VoiceLine, VoiceLine]] = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            pairs.append((en[block.a + offset], zh[block.b + offset]))
    if len(pairs) < max(len(en), len(zh)) * 0.9:
        raise TranscriptError(
            "EN/ZH Voice IDs do not line up "
            f"({len(pairs)} matched of {len(en)}/{len(zh)})."
        )
    warnings.append(
        f"EN/ZH Voice IDs only partially matched ({len(pairs)} pairs); "
        "unmatched lines were dropped."
    )
    return pairs, warnings


def pair_part_transcripts(
    part_number: int, content_en: str, content_zh: str
) -> tuple[list[DialogueLine], list[str]]:
    en_records = extract_voice_records(content_en)
    zh_records = extract_voice_records(content_zh)
    if not en_records:
        raise TranscriptError(f"Part {part_number}: English transcript has no voiced lines.")
    if not zh_records:
        raise TranscriptError(f"Part {part_number}: Chinese transcript has no voiced lines.")

    pairs, warnings = _pair_by_voice(en_records, zh_records)
    lines: list[DialogueLine] = []
    for index, (en, zh) in enumerate(pairs):
        text_en = clean_text(en.text)
        text_zh = clean_text(zh.text)
        line_warnings: list[str] = []
        if not text_en:
            line_warnings.append("Empty English text; the site's markup may have changed.")
        if not text_zh:
            line_warnings.append("Empty Chinese text; the site's markup may have changed.")
        if not text_en and not text_zh:
            continue
        lines.append(
            DialogueLine(
                index=len(lines),
                voice=en.voice,
                speaker_en=en.speaker,
                text_en=text_en,
                speaker_zh=zh.speaker,
                text_zh=text_zh,
                warnings=line_warnings,
            )
        )
    if len(lines) < len(pairs):
        warnings.append(
            f"Dropped {len(pairs) - len(lines)} voiced line(s) with no text in either locale."
        )
    if not lines:
        raise TranscriptError(f"Part {part_number}: no usable bilingual dialogue lines.")
    return lines, warnings


def transcript_stats(lines: list[DialogueLine]) -> dict[str, Any]:
    return {
        "lines": len(lines),
        "en_chars": sum(len(line.text_en) for line in lines),
        "zh_chars": sum(len(line.text_zh) for line in lines),
        "speakers": len({line.speaker_en for line in lines if line.speaker_en}),
        "warnings": sum(len(line.warnings) for line in lines),
    }
