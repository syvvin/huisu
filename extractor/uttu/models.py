"""Serializable data structures used by the scanner, pipeline and API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Part:
    number: int                 # raw part number shown by the site (may include 0)
    text_number: int            # textPartNumber, used as the stable key
    title_en: str
    title_zh: str = ""
    content_en: str = ""
    content_zh: str = ""
    word_count: int = 0
    gameplay_only: bool = False
    video_url: str | None = None

    @property
    def key(self) -> str:
        return str(self.text_number)

    def summary(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "text_number": self.text_number,
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            "word_count": self.word_count,
            "gameplay_only": self.gameplay_only,
            "has_en": bool(self.content_en),
            "has_zh": bool(self.content_zh),
        }


@dataclass
class Chapter:
    category: str
    slug: str
    title_en: str
    title_zh: str = ""
    label: str = ""
    subtitle: str = ""
    description: str = ""
    recording_url: str = ""
    parts: list[Part] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_urls: dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0

    @property
    def path(self) -> str:
        return f"/story/{self.category}/{self.slug}/"

    def part(self, text_number: int | str) -> Part | None:
        wanted = str(text_number)
        for item in self.parts:
            if item.key == wanted or str(item.number) == wanted:
                return item
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "slug": self.slug,
            "title_en": self.title_en,
            "title_zh": self.title_zh,
            "label": self.label,
            "subtitle": self.subtitle,
            "recording_url": self.recording_url,
            "parts_count": len(self.parts),
            "warnings": list(self.warnings),
            "parts": [part.summary() for part in self.parts],
        }


@dataclass
class Category:
    slug: str
    label: str
    chapters: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "label": self.label,
            "chapters": self.chapters,
        }
