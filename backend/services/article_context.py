"""Bounded article excerpts and verifiable citations using Unicode positions."""
from collections import Counter
from dataclasses import dataclass
from math import log1p
import re

from backend.ocr.service import ENGLISH_WORD
from backend.services.lemmatization import normalize_word

CHUNK_CHARS = 1800
CONTEXT_CHARS = 24000
MAX_ARTICLE_CHARS = 200000
_STOPWORDS = frozenset("a an and are as at be been being but by can could did do does for from had has have how i if in into is it its me my not of on or our should so some than that the their them there these they this those to us was we were what when where which who why will with would you your article author explain mean meaning please summarize summary tell".split())


@dataclass(frozen=True)
class ArticleChunk:
    id: str
    text: str
    text_start: int
    text_end: int
    page_number: int | None


@dataclass(frozen=True)
class ArticleContext:
    chunks: tuple[ArticleChunk, ...]
    limited: bool
    total_chars: int
    selected_chars: int


def _regions(document):
    content = document.ocr_text
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_ARTICLE_CHARS:
        raise ValueError("Article is empty or too large")
    if document.source != "pdf":
        return [(0, len(content), None)]
    pages = document.pages
    if not isinstance(pages, list) or not pages:
        raise ValueError("PDF has no valid page boundaries")
    regions, previous_end = [], 0
    for index, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            raise ValueError("Invalid PDF page")
        start, end, number = page.get("text_start"), page.get("text_end"), page.get("number")
        if (type(start) is not int or type(end) is not int or type(number) is not int
                or number != index or not previous_end <= start <= end <= len(content)
                or content[previous_end:start].strip()):
            raise ValueError("Invalid PDF page boundaries")
        regions.append((start, end, number))
        previous_end = end
    if content[previous_end:].strip():
        raise ValueError("PDF boundaries omit article content")
    return regions


def _chunks(document) -> tuple[ArticleChunk, ...]:
    content = document.ocr_text
    chunks = []
    for region_start, region_end, number in _regions(document):
        start = region_start
        while start < region_end:
            end = min(start + CHUNK_CHARS, region_end)
            if end < region_end:
                # Prefer paragraph/sentence boundaries; never normalize source
                # whitespace or lose the absolute position of a citation.
                middle = start + CHUNK_CHARS // 2
                endings = list(re.finditer(r"\n\s*\n|[.!?][ \t\r\n]+|\s+", content[middle:end]))
                paragraphs = [match for match in endings if "\n" in match.group()]
                boundary = (paragraphs or endings)
                if boundary:
                    end = middle + boundary[-1].end()
            text = content[start:end]
            if text.strip():
                chunks.append(ArticleChunk(f"chunk-{len(chunks) + 1}", text, start, end, number))
            start = end
    if not chunks:
        raise ValueError("Article contains no usable text")
    return tuple(chunks)


def _terms(text: str) -> Counter:
    terms = Counter()
    for match in ENGLISH_WORD.finditer(text):
        surface = match.group().lower()
        if len(surface) < 2 or surface in _STOPWORDS:
            continue
        try:
            lemma = normalize_word(surface)
        except ValueError:
            continue
        terms.update({surface, lemma})
    return terms


def prepare_article_context(document, question: str, history_questions: list[str] | None = None) -> ArticleContext:
    """Use the whole short article, or relevant excerpts plus spread-out context.

    This is lexical selection, not a claim of semantic search across every page.
    ``limited`` explicitly tells callers when the model has not seen the whole
    article. Prior questions help resolve follow-ups; prior answers are not
    treated as article evidence.
    """
    chunks = _chunks(document)
    total = sum(len(chunk.text) for chunk in chunks)
    if total <= CONTEXT_CHARS:
        return ArticleContext(chunks, False, len(document.ocr_text), total)
    query = _terms(question)
    weights = {term: 2 for term in query}
    for previous in (history_questions or [])[-3:]:
        for term in _terms(previous):
            weights.setdefault(term, 1)
    frequencies = [_terms(chunk.text) for chunk in chunks] if weights else []
    document_frequency = {term: sum(term in counts for counts in frequencies) for term in weights}
    scores = [(sum(weight * min(counts[term], 3)
                   * log1p((len(chunks) + 1) / (document_frequency[term] + 1))
                   for term, weight in weights.items()), index)
              for index, counts in enumerate(frequencies)]
    chosen, remaining = set(), CONTEXT_CHARS

    def add(index):
        nonlocal remaining
        size = len(chunks[index].text)
        if index not in chosen and size <= remaining:
            chosen.add(index)
            remaining -= size

    # Keep introduction/conclusion and the best query matches, then cover gaps.
    add(0)
    add(len(chunks) - 1)
    for score, index in sorted(scores, key=lambda item: (-item[0], item[1]))[:8]:
        if score > 0:
            add(index)
    for _ in range(len(chunks)):
        candidates = [index for index, chunk in enumerate(chunks)
                      if index not in chosen and len(chunk.text) <= remaining]
        if not candidates:
            break
        index = max(candidates, key=lambda candidate: (min(abs(candidate - other) for other in chosen), -candidate))
        add(index)
    selected = tuple(chunks[index] for index in sorted(chosen))
    selected_chars = sum(len(chunk.text) for chunk in selected)
    return ArticleContext(selected, selected_chars < total, len(document.ocr_text), selected_chars)


def resolve_article_quote(context: ArticleContext, chunk_id: str, quote: str) -> dict:
    """Only quotes copied exactly from the supplied excerpts can become evidence."""
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 1200:
        raise ValueError("Invalid citation quote")
    chunk = next((chunk for chunk in context.chunks if chunk.id == chunk_id), None)
    if chunk is None:
        raise ValueError("Unknown citation chunk")
    relative = chunk.text.find(quote)
    if relative < 0:
        raise ValueError("Citation is not an exact source substring")
    start = chunk.text_start + relative
    return {"chunk_id": chunk.id, "quote": quote, "text_start": start,
            "text_end": start + len(quote), "page_number": chunk.page_number}
