"""Small read-only suggestions from stored offline dictionary entries.

Suggestions never normalize to a lemma, call a model, or touch learning data.
Spelling search deliberately stops at one insertion/deletion/substitution or
adjacent transposition; uncertain, distant matches remain absent.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.services.offline_dictionary import (
    OfflineDictionaryUnavailable, normalize_spelling, prefix_rows, spelling_rows,
)


_ERROR = "请输入有效英文单词或短语（最多 150 个字符）。"
_ALPHABET = "abcdefghijklmnopqrstuvwxyz' -"
_MAX_SPELLING_LENGTH = 48
_MEANING_LENGTH = 140


class SuggestionItem(BaseModel):
    word: str
    meaning: str


class DictionarySuggestions(BaseModel):
    query: str
    mode: Literal["prefix", "spelling"]
    items: list[SuggestionItem] = Field(default_factory=list, max_length=8)
    warning: str | None = None


def _edits(word: str) -> dict[str, int]:
    """At most 2,900 candidates, with common dropped doubles/swaps first."""
    candidates: dict[str, int] = {}

    def add(candidate: str, priority: int = 1) -> None:
        if candidate != word and normalize_spelling(candidate) == candidate:
            candidates[candidate] = min(priority, candidates.get(candidate, priority))

    for position in range(len(word)):
        left, character, right = word[:position], word[position], word[position + 1:]
        doubled = (left.endswith(character) or right.startswith(character))
        add(left + right, 0 if doubled else 1)
        for replacement in _ALPHABET:
            add(left + replacement + right)
        if right:
            add(left + right[0] + character + right[1:], 0)
    for position in range(len(word) + 1):
        left, right = word[:position], word[position:]
        for insertion in _ALPHABET:
            doubled = left.endswith(insertion) or right.startswith(insertion)
            add(left + insertion + right, 0 if doubled else 1)
    return candidates


def _item(row: tuple[str, str, str, int]) -> SuggestionItem | None:
    meaning = "；".join(part.strip() for part in row[2].splitlines() if part.strip())
    if not meaning:
        return None
    if len(meaning) > _MEANING_LENGTH:
        meaning = meaning[:_MEANING_LENGTH - 1].rstrip() + "…"
    return SuggestionItem(word=row[1], meaning=meaning)


def read_suggestions(query: str = "", mode: str = "prefix") -> DictionarySuggestions:
    if mode not in ("prefix", "spelling"):
        raise ValueError("候选词模式必须是 prefix 或 spelling。")
    if not isinstance(query, str):
        raise ValueError(_ERROR)
    if not query.strip():
        return DictionarySuggestions(query="", mode=mode)
    key = normalize_spelling(query)
    if key is None:
        raise ValueError(_ERROR)
    result = DictionarySuggestions(query=key, mode=mode)
    if sum(character.isalpha() for character in key) < 2:
        return result
    try:
        if mode == "prefix":
            # Rank only a bounded indexed pool. Entries with pronunciation and
            # English definitions outrank sparse abbreviations/name fragments;
            # these source fields are a quality hint, not an invented frequency.
            rows = sorted(prefix_rows(key), key=lambda row: (
                row[0] != key, not row[3], row[1] != row[1].lower(),
                " " in row[0], len(row[0]), row[0],
            ))
        else:
            # Very short spellings are ambiguous; long input must not create an
            # unbounded edit pool. Prefix search still supports all 150 chars.
            if not 3 <= len(key) <= _MAX_SPELLING_LENGTH:
                return result
            candidates = _edits(key)
            rows = sorted(spelling_rows(tuple(candidates)), key=lambda row: (
                not row[3], row[1] != row[1].lower(), not row[0].startswith(key[:2]),
                candidates[row[0]],
                abs(len(row[0]) - len(key)), row[0],
            ))
        result.items = [item for row in rows if (item := _item(row)) is not None][:8]
    except OfflineDictionaryUnavailable:
        result.warning = "离线候选词暂时不可用，请稍后重试。"
    return result
