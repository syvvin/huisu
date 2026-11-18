"""Subtitle parsing, speaker extraction, tokenisation and lemma helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import ecdict

TIMING_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
EN_SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z0-9.'’\- ]{0,38}):\s+(.+)$", re.S)
ZH_SPEAKER_RE = re.compile(r"^([^:：]{1,24})[：:]\s*(.+)$", re.S)
TOKEN_RE = re.compile(r"[A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?")

STOPWORDS = set("""
a about above after again against ago ah all almost along already also although always am among an and
another any anybody anyone anything anyway anywhere are aren't around as at away back be because been
before behind being below beside besides between both but by can can't cannot could couldn't did didn't
do does doesn't doing don't done down during each either else enough even ever every everybody everyone
everything everywhere except far few for from further get gets getting go goes going gone got gotta
had hadn't has hasn't have haven't having he he'd he'll he's hello her here here's hers herself hey hi
him himself his how how's however i i'd i'll i'm i've if in into is isn't it it'd it'll it's its itself
just let let's like little lots lot may maybe me might mine more most much must mustn't my myself near
need neither never no nobody none nor not nothing now nowhere of off often oh ok okay on once one only or
other ought our ours ourselves out over own perhaps please quite rather really right said say says shall
she she'd she'll she's should shouldn't since so some somebody someone something sometimes somewhere
still such sure take than that that's the their theirs them themselves then there there's these they
they'd they'll they're they've thing things this those though through thus till to too toward towards
under until up upon us very via was wasn't way we we'd we'll we're we've well went were weren't what
what's whatever when when's where where's whether which while who who's whom whose why why's will with
within without won't would wouldn't yeah yes yet you you'd you'll you're you've your yours yourself
yourselves
""".split())

# Words that are worth learning are at least this long and not in the stop list.
MIN_CANDIDATE_LEN = 4


@dataclass
class Cue:
    idx: int
    start: float
    end: float
    speaker: str
    en: str
    zh: str


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(raw: str) -> list[dict]:
    text = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = re.split(r"\n{2,}", text)
    out: list[dict] = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        m = TIMING_RE.search(lines[timing_index])
        if not m:
            continue
        g = m.groups()
        start = _seconds(*g[:4])
        end = _seconds(*g[4:])
        body = "\n".join(lines[timing_index + 1:]).strip()
        out.append({"start": start, "end": end, "text": body})
    return out


def split_speaker(text: str, lang: str) -> tuple[str, str]:
    """Return (speaker, content). Falls back to no speaker."""
    text = text.strip()
    if not text:
        return "", ""
    pattern = EN_SPEAKER_RE if lang == "en" else ZH_SPEAKER_RE
    m = pattern.match(text)
    if not m:
        return "", text
    speaker, content = m.group(1).strip(), m.group(2).strip()
    if lang == "en":
        # A dialogue label never starts with a quote and never ends with punctuation.
        if not speaker or speaker[-1] in ".,!?;:":
            return "", text
        if speaker.split()[0].lower() in {"it", "he", "she", "they", "we", "you", "there", "that", "this"}:
            return "", text
    if not content:
        return "", text
    return speaker, content


def clean_text(text: str) -> str:
    text = text.replace("\u200b", " ").replace("\ufeff", "")
    text = re.sub(r"<[^>]{1,12}>", "", text)          # stray italic tags
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[list]:
    """[[start, end, lemma], ...] where the surface is text[start:end]."""
    tokens = []
    for m in TOKEN_RE.finditer(text):
        surface = m.group(0)
        tokens.append([m.start(), m.end(), ecdict.lemma(surface)])
    return tokens


def build_cues(en_cues: list[dict], zh_cues: list[dict]) -> list[Cue]:
    cues: list[Cue] = []
    last_speaker = ""
    for i, en in enumerate(en_cues):
        zh_raw = zh_cues[i]["text"] if i < len(zh_cues) else ""
        en_speaker, en_text = split_speaker(clean_text(en["text"]), "en")
        zh_speaker, zh_text = split_speaker(clean_text(zh_raw), "zh")
        speaker = en_speaker or zh_speaker or last_speaker
        if speaker:
            last_speaker = speaker
        cues.append(Cue(
            idx=i,
            start=round(en["start"], 3),
            end=round(max(en["end"], en["start"] + 0.3), 3),
            speaker=speaker,
            en=en_text,
            zh=zh_text,
        ))
    return cues


def word_brief(entry: dict | None) -> tuple[str, str]:
    """(phonetic, short Chinese gloss) used in compact word rows."""
    if not entry:
        return "", ""
    phonetic = entry.get("phonetic", "")
    gloss = ""
    for group in entry.get("pos_groups", []):
        if group.get("means"):
            gloss = group["means"][0]
            if group.get("pos"):
                gloss = f"{group['pos']} {gloss}"
            break
    if not gloss and entry.get("definitions_en"):
        gloss = entry["definitions_en"][0]
    return phonetic, gloss


def is_candidate(surface: str, lemma: str) -> bool:
    if len(lemma) < MIN_CANDIDATE_LEN or len(lemma) > 24:
        return False
    if lemma in STOPWORDS or surface.lower() in STOPWORDS:
        return False
    if not lemma.isalpha():
        return False
    return True
