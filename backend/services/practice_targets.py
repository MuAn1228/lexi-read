"""Read-only practice selection and real, Unicode-aware target coverage."""
from datetime import datetime
from typing import Literal
import re
import unicodedata

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from backend.models import ReviewSchedule, Vocabulary
from backend.models.entities import utcnow
from backend.ocr.service import ENGLISH_WORD
from backend.schemas import Schema, Status
from backend.services.lemmatization import normalize_word
from backend.services.scheduling import utc

WORD_RANGES = {"short": (80, 120), "medium": (150, 200), "long": (250, 300)}


class PracticeTarget(Schema):
    id: int
    word: str
    lemma: str
    status: Status
    reason: Literal["due", "unknown", "uncertain"]
    mastery_score: float | None
    due_at: datetime | None


def select_practice_targets(session: Session, user_id: int, limit: int = 5,
                            vocabulary_ids: list[int] | None = None,
                            now: datetime | None = None) -> list[PracticeTarget]:
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("Invalid target limit")
    if vocabulary_ids is not None and (not 1 <= len(vocabulary_ids) <= 5
                                      or len(set(vocabulary_ids)) != len(vocabulary_ids)):
        raise ValueError("Invalid target selection")
    current = utc(now) if now is not None else utcnow()
    due = ReviewSchedule.due_at <= current
    eligible = or_(Vocabulary.status.in_(("UNKNOWN", "UNCERTAIN")),
                   and_(Vocabulary.status == "KNOWN", due))
    with session.no_autoflush:
        if vocabulary_ids is not None:
            owned = set(session.scalars(select(Vocabulary.id).where(
                Vocabulary.user_id == user_id, Vocabulary.id.in_(vocabulary_ids))))
            if owned != set(vocabulary_ids):
                raise HTTPException(404, "未找到所选练习词，请刷新目标词。")
        # Select columns, not ORM objects: callers' unsaved changes are neither
        # flushed nor overwritten, and only stored personal learning data is used.
        query = select(Vocabulary.id, Vocabulary.word, Vocabulary.lemma, Vocabulary.status,
                       Vocabulary.mastery_score, ReviewSchedule.due_at,
                       case((due, "due"), (Vocabulary.status == "UNKNOWN", "unknown"),
                            else_="uncertain").label("reason")).outerjoin(
            ReviewSchedule, and_(ReviewSchedule.vocabulary_id == Vocabulary.id,
                                 ReviewSchedule.user_id == user_id),
        ).where(Vocabulary.user_id == user_id, eligible)
        if vocabulary_ids is not None:
            query = query.where(Vocabulary.id.in_(vocabulary_ids))
        rows = list(session.execute(query.order_by(
            case((due, 0), (Vocabulary.status == "UNKNOWN", 1), else_=2),
            case((due, ReviewSchedule.due_at), else_=None).asc(),
            func.coalesce(Vocabulary.mastery_score, .5).asc(),
            Vocabulary.lookup_count.desc(), Vocabulary.seen_count.desc(), Vocabulary.id.asc(),
        ).limit(limit)).mappings())
        if vocabulary_ids is not None and len(rows) != len(vocabulary_ids):
            raise HTTPException(409, "目标词的学习状态已变化，请刷新目标词后重新生成。")
        result = []
        for row in rows:
            try:
                if normalize_word(row["lemma"]) != row["lemma"]:
                    raise ValueError("Unstable lemma")
            except ValueError:
                if vocabulary_ids is not None:
                    raise HTTPException(409, "目标词无法用于练习，请刷新目标词。") from None
                continue
            result.append(PracticeTarget(**row))
        return result


def _english_text(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and sum(char.isascii() for char in letters) / len(letters) >= .95


def validate_practice_text(title: str, text: str, targets: list[PracticeTarget], length: str) -> dict:
    """Reject missing targets; never append words to repair a generated article."""
    if length not in WORD_RANGES or not targets or len(targets) > 5:
        raise ValueError("Invalid practice parameters")
    if (not isinstance(title, str) or not title.strip() or len(title) > 120
            or any(unicodedata.category(char) in {"Cc", "Cs"} for char in title)
            or not _english_text(title)):
        raise ValueError("Invalid English title")
    if (not isinstance(text, str) or not text.strip() or len(text) > 10000
            or any(unicodedata.category(char) == "Cs" or
                   (unicodedata.category(char) == "Cc" and char not in "\n\t") for char in text)
            or not _english_text(text)
            or re.search(r"<[/!]?[A-Za-z][^>]*>|```|^\s*#", title + "\n" + text, re.MULTILINE)):
        raise ValueError("Invalid English passage")
    tokens = list(ENGLISH_WORD.finditer(text))
    minimum, maximum = WORD_RANGES[length]
    if not minimum <= len(tokens) <= maximum:
        raise ValueError("Passage word count is outside the chosen range")
    try:
        lemmas = [normalize_word(token.group()) for token in tokens]
    except ValueError:
        raise ValueError("Unsupported passage token") from None
    matches = []
    for target in targets:
        target_tokens = list(ENGLISH_WORD.finditer(target.lemma))
        wanted = [normalize_word(token.group()) for token in target_tokens]
        if not wanted or " ".join(token.group() for token in target_tokens) != target.lemma:
            # Hyphenated/apostrophe tokens remain one complete token.
            raise ValueError("Invalid target lemma")
        found = []
        for index in range(len(tokens) - len(wanted) + 1):
            if lemmas[index:index + len(wanted)] != wanted:
                continue
            selected = tokens[index:index + len(wanted)]
            if any(not text[left.end():right.start()].isspace()
                   for left, right in zip(selected, selected[1:])):
                continue
            start, end = selected[0].start(), selected[-1].end()
            found.append({"vocabulary_id": target.id, "word": text[start:end],
                          "text_start": start, "text_end": end})
        if not found:
            raise ValueError("Passage does not cover every requested target")
        matches.extend(found)
    return {"word_count": len(tokens),
            "matches": sorted(matches, key=lambda item: (item["text_start"], item["text_end"], item["vocabulary_id"]))}
