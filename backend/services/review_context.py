"""Map a real occurrence into its displayed sentence without guessing repeats."""
import re

from backend.ocr.service import ENGLISH_WORD


def review_target_range(document, occurrence) -> tuple[int, int] | None:
    """Return Unicode code point offsets in the saved, normalized sentence.

    Text and PDF imports carry authoritative document offsets. OCR imports do
    not, so only an unambiguous complete token in their saved context is usable.
    This helper is pure: opening a review never updates learning records.
    """
    word, sentence = occurrence.word, occurrence.sentence
    if not word or not sentence:
        return None
    start, end = occurrence.text_start, occurrence.text_end
    if start is None and end is None:
        if document.source not in {"upload", "demo"}:
            return None
        matches = [match for match in ENGLISH_WORD.finditer(sentence)
                   if match.group() == word]
        return matches[0].span() if len(matches) == 1 else None
    content = document.ocr_text
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(content)
            or content[start:end] != word):
        return None
    region_start, region_end = 0, len(content)
    if document.source == "pdf":
        page = next((page for page in (document.pages or [])
                     if type(page.get("text_start")) is int
                     and type(page.get("text_end")) is int
                     and 0 <= page["text_start"] <= start < end
                     <= page["text_end"] <= len(content)), None)
        if page is None:
            return None
        region_start, region_end = page["text_start"], page["text_end"]
    # Verify a complete token, including hyphens/apostrophes, rather than
    # accepting an offset that points into another word.
    tokens = ENGLISH_WORD.finditer(content, region_start, region_end)
    if not any(match.span() == (start, end) for match in tokens):
        return None
    for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", content[region_start:region_end]):
        raw_start = region_start + match.start()
        raw_end = region_start + match.end()
        if not raw_start <= start < end <= raw_end:
            continue
        raw_sentence = match.group()
        normalized = re.sub(r"\s+", " ", raw_sentence).strip()
        if normalized != sentence:
            return None
        prefix = re.sub(r"\s+", " ", content[raw_start:start]).lstrip()
        target_start = len(prefix)
        target_end = target_start + len(word)
        if sentence[target_start:target_end] != word:
            return None
        return target_start, target_end
    return None
