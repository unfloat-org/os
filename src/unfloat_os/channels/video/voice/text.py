"""Narration text: paragraphs, sentences, the lexicon and caption parts. No audio here."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

CAPTION_LIMIT = 84  # characters; a longer sentence is shown in parts

# Words whose full stop does not end a sentence ("Dr. Smith", "Fig. 2", "e.g. this").
ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "mt", "vs", "cf", "fig", "figs"}
    | {"eq", "eqs", "approx", "ca", "inc", "ltd", "co", "al", "e.g", "i.e"}
)
_NUMBERED = frozenset({"no", "nos"})  # "No. 5" but "No. We"
_OPENERS = "\"'\u201c\u2018([{"
_CLOSERS = "\"'\u201d\u2019)]}"


@dataclass(frozen=True)
class Sentence:
    text: str  # as written, white space collapsed
    paragraph: int  # index of the paragraph it belongs to


def _core(token: str) -> str:
    return token.lstrip(_OPENERS).rstrip(_CLOSERS)


def _starts_new_sentence(token: str) -> bool:
    word = token.lstrip(_OPENERS)
    return bool(word) and (word[0].isupper() or word[0].isdigit())


def _ends_sentence(token: str, following: str) -> bool:
    core = _core(token)
    if not core or core[-1] not in ".!?":
        return False
    if not _starts_new_sentence(following):
        return False
    if core[-1] != ".":
        return True
    base = core.rstrip(".")
    low = base.lower()
    if core.endswith("..."):
        return True
    if low in ABBREVIATIONS or (low in _NUMBERED and following[:1].isdigit()):
        return False
    return not (len(base) == 1 and base.isupper())  # an initial ("J. Smith") does not end a sentence


def split_paragraphs(text: str) -> list[str]:
    """Paragraphs of a narration: blocks separated by a blank line, white space collapsed."""
    blocks = re.split(r"\n\s*\n", text.strip())
    return [" ".join(block.split()) for block in blocks if block.strip()]


def split_sentences(paragraph: str) -> list[str]:
    """Sentences of one paragraph; decimals ("0.4"), titles ("Dr.") and "e.g." do not end one."""
    tokens = paragraph.split()
    sentences: list[str] = []
    current: list[str] = []
    for index, token in enumerate(tokens):
        current.append(token)
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if index + 1 == len(tokens) or _ends_sentence(token, following):
            sentences.append(" ".join(current))
            current = []
    return sentences


def split_text(text: str) -> list[Sentence]:
    out: list[Sentence] = []
    for number, paragraph in enumerate(split_paragraphs(text)):
        out.extend(Sentence(sentence, number) for sentence in split_sentences(paragraph))
    return out


def apply_lexicon(text: str, lexicon: Mapping[str, str]) -> str:
    """Spoken replacements: whole words, matched in the case given, longest key first, one pass."""
    keys = [key for key in lexicon if key]
    if not keys:
        return text
    keys.sort(key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(key) for key in keys) + r")(?!\w)")
    return pattern.sub(lambda match: lexicon[match.group(0)], text)


def _cut(text: str) -> int | None:
    """Where to cut a long caption: near the middle, at a clause boundary if one is close."""
    middle = len(text) / 2
    low, high = len(text) * 0.2, len(text) * 0.8
    candidates: list[tuple[float, int]] = []
    for match in re.finditer(r"[,;:](?=\s)|\s[\u2014\u2013]\s", text):
        candidates.append((0.0, match.end()))
    for match in re.finditer(r"\s(?=(?:and|but|which|so|because|while|that|with)\s)", text):
        candidates.append((0.1 * len(text), match.start()))
    for match in re.finditer(r"\s", text):
        candidates.append((0.2 * len(text), match.start()))
    scored = [(penalty + abs(pos - middle), pos) for penalty, pos in candidates if low <= pos <= high]
    if not scored:
        scored = [(abs(pos - middle), pos) for _, pos in candidates if 0 < pos < len(text)]
    return min(scored)[1] if scored else None


def caption_parts(text: str, limit: int = CAPTION_LIMIT) -> list[str]:
    """A sentence in caption-sized parts (at most `limit` characters when it has places to cut)."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    cut = _cut(text)
    if cut is None:
        return [text]
    left, right = text[:cut].rstrip(), text[cut:].lstrip()
    if not left or not right:
        return [text]
    return caption_parts(left, limit) + caption_parts(right, limit)
