from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.models import Base, LearningEvent, User, Vocabulary
from backend.repositories.vocabulary import VocabularyRepository


def test_word_history_is_user_scoped_and_ties_use_event_id():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add_all([User(id=1), User(id=2)])
        session.flush()
        session.add_all([
            Vocabulary(user_id=1, word="model", lemma="model", status="UNKNOWN"),
            Vocabulary(user_id=2, word="model", lemma="model", status="KNOWN"),
            LearningEvent(user_id=1, word="Models", lemma="model", event_type="MARK_UNKNOWN", created_at=timestamp),
            LearningEvent(user_id=1, word="model", lemma="model", event_type="LOOKUP", created_at=timestamp),
            LearningEvent(user_id=2, word="model", lemma="model", event_type="MARK_KNOWN", created_at=timestamp),
        ])
        session.commit()
        own = VocabularyRepository(session, user_id=1)
        other = VocabularyRepository(session, user_id=2)
        assert own.get("model").status == "UNKNOWN"
        assert [event.event_type for event in own.word_history("model")] == ["LOOKUP", "MARK_UNKNOWN"]
        assert own.last_mark("model").word == "Models"
        assert len(other.word_history("model")) == 1
        assert other.last_mark("model").event_type == "MARK_KNOWN"
        assert own.word_history("missing") == [] and own.last_mark("missing") is None
    engine.dispose()
