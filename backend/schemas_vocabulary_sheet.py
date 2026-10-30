"""Read-only article vocabulary sheets for printing and portable study."""

from pydantic import Field

from backend.schemas import Schema


class VocabularySheetItem(Schema):
    word: str = Field(min_length=1, max_length=200)
    lemma: str = Field(min_length=1, max_length=200)
    article_word: str = Field(min_length=1, max_length=200)
    phonetic: str | None = Field(default=None, max_length=500)
    meanings: list[str] = Field(default_factory=list)
    count: int = Field(ge=1)
    first_occurrence_id: int = Field(gt=0)


class VocabularySheetOut(Schema):
    document_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=250)
    item_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)
    items: list[VocabularySheetItem]
    dictionary_warning: str | None = None
