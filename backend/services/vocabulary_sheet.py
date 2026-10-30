"""Build a compact, textbook-style glossary from one article.

Document import observes every lemma so that encounter counts remain useful.
An observed vocabulary row with a NULL status is therefore the project's
existing representation of a word that has not yet been formally assessed.
This reader deliberately has no learning side effects.
"""

from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Vocabulary, WordOccurrence
from backend.schemas_vocabulary_sheet import VocabularySheetItem, VocabularySheetOut
from backend.services.documents import require_document
from backend.services.offline_dictionary import OfflineDictionaryUnavailable, lookup


def read_vocabulary_sheet(session: Session, document_id: int, user_id: int) -> VocabularySheetOut:
    document = require_document(session, document_id, user_id)
    assessed = set(session.scalars(select(Vocabulary.lemma).join(
        WordOccurrence, WordOccurrence.lemma == Vocabulary.lemma,
    ).where(
        Vocabulary.user_id == user_id,
        Vocabulary.status.is_not(None),
        WordOccurrence.document_id == document.id,
    ).distinct()))

    # Relationship order is WordOccurrence.id, which is also the article token
    # order for every current importer and proofreading replacement.
    grouped: OrderedDict[str, dict] = OrderedDict()
    for occurrence in document.words:
        if occurrence.lemma in assessed:
            continue
        item = grouped.get(occurrence.lemma)
        if item is None:
            grouped[occurrence.lemma] = {"occurrence": occurrence, "count": 1}
        else:
            item["count"] += 1

    items: list[VocabularySheetItem] = []
    dictionary_unavailable = False
    for lemma, grouped_item in grouped.items():
        occurrence = grouped_item["occurrence"]
        dictionary = None
        if not dictionary_unavailable:
            try:
                dictionary = lookup(lemma, lemma)
            except OfflineDictionaryUnavailable:
                # A missing or corrupt asset affects the whole bundled
                # dictionary. Retrying it for every word repeats expensive
                # verification/extraction work without a chance of recovery.
                dictionary_unavailable = True
        items.append(VocabularySheetItem(
            word=dictionary["headword"] if dictionary else lemma,
            lemma=lemma,
            article_word=occurrence.word,
            phonetic=(dictionary["phonetic"] or None) if dictionary else None,
            meanings=list(dictionary["chinese"]) if dictionary else [],
            count=grouped_item["count"],
            first_occurrence_id=occurrence.id,
        ))

    return VocabularySheetOut(
        document_id=document.id,
        title=document.title,
        item_count=len(items),
        occurrence_count=sum(item.count for item in items),
        items=items,
        dictionary_warning=("本机离线词典暂时不可用，已保留文章中的单词与原句。"
                            if dictionary_unavailable else None),
    )
