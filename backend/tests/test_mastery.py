import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.models import Base, LearningEvent, User, Vocabulary
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.mastery import MasteryService
from backend.services.vocabulary import VocabularyService


@pytest.mark.parametrize("event,expected", [
    ("MARK_KNOWN", .65), ("MARK_UNKNOWN", .30), ("MARK_UNCERTAIN", .42), ("LOOKUP", .47),
])
def test_first_action_uses_neutral_baseline(event, expected):
    assert MasteryService.apply(None, event) == expected


def test_repeated_known_increases_and_unknown_lowers_with_clamps():
    score = MasteryService.apply(None, "MARK_KNOWN")
    assert MasteryService.apply(score, "MARK_KNOWN") == .8
    for _ in range(10):
        score = MasteryService.apply(score, "MARK_KNOWN")
    assert score == 1
    assert MasteryService.apply(score, "MARK_UNKNOWN") == .8
    for _ in range(10):
        score = MasteryService.apply(score, "MARK_UNKNOWN")
    assert score == 0
    with pytest.raises(ValueError):
        MasteryService.apply(None, "OBSERVE")


def test_marks_lookups_share_one_entry_and_observation_never_changes_score():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1))
        session.commit()
        service = VocabularyService(VocabularyRepository(session))
        entry = service.observe("Generalized", "generalized")
        assert entry.mastery_score is None and entry.status is None
        service.mark("generalizing", Status.KNOWN)
        assert entry.mastery_score == .65
        service.mark("generalizes", Status.KNOWN)
        assert entry.mastery_score == .8
        service.mark("generalize", Status.UNKNOWN)
        assert entry.mastery_score == .6
        # No document is needed for this service-level test.
        service.lookup("Generalized", None, "offline")
        assert entry.mastery_score == .57 and entry.status == "UNKNOWN"
        assert entry.lookup_count == 1 and entry.seen_count == 1
        service.observe("generalizes", "generalize", 2)
        session.commit()
        assert entry.mastery_score == .57 and entry.seen_count == 3
        assert len(list(session.scalars(select(Vocabulary)))) == 1
        events = list(session.scalars(select(LearningEvent).order_by(LearningEvent.id)))
        assert len(events) == 4
        assert [event.details["mastery_after"] for event in events] == [.65, .8, .6, .57]
        assert events[0].details["mastery_before"] is None
        assert events[2].details["previous_status"] == "KNOWN"
        assert events[3].details == {
            "source": "offline", "intentional": True, "mastery_before": .6,
            "mastery_after": .57, "rule_version": "heuristic_v1",
        }
    engine.dispose()


def test_event_failure_rolls_back_score_status_and_counter(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1))
        session.commit()
        repo = VocabularyRepository(session)
        service = VocabularyService(repo)
        entry = service.mark("model", Status.KNOWN)
        identifier = entry.id

        def fail(*args, **kwargs):
            raise RuntimeError("event unavailable")

        monkeypatch.setattr(repo, "event", fail)
        with pytest.raises(RuntimeError):
            service.lookup("models", None, "offline")
        session.rollback()
        recovered = session.get(Vocabulary, identifier)
        assert recovered.mastery_score == .65 and recovered.lookup_count == 0
        assert len(list(session.scalars(select(LearningEvent)))) == 1
    engine.dispose()
