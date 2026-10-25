import pytest

from backend.services import offline_dictionary
from backend.services.dictionary import OfflineEntry
from backend.services.supplemental_dictionary import lookup_supplement


@pytest.mark.parametrize("word", ["horizontal wind vector", "leap of faith", "honourary", "microwavable"])
def test_reviewed_exact_additions_have_meanings_examples_and_traceable_sources(word, monkeypatch):
    monkeypatch.setattr(offline_dictionary, "_row", lambda _: None)
    result = OfflineEntry.model_validate(offline_dictionary.lookup(word))
    assert result.headword == word
    assert result.source == "lexi_supplement"
    assert result.chinese and result.examples and result.usage_notes
    assert result.source_url.startswith("https://")
    changed = lookup_supplement(word)
    changed["chinese"].clear()
    assert lookup_supplement(word)["chinese"]


def test_spelling_caution_never_rewrites_or_silently_corrects_a_word(monkeypatch):
    monkeypatch.setattr(offline_dictionary, "_row", lambda _: None)
    result = offline_dictionary.lookup("honourary")
    assert result["headword"] == "honourary"
    assert "拼写待核对" in result["chinese"][0]
    for unsupported in ["unverifiedwordxyz", "leap of fate", "honouraries"]:
        assert offline_dictionary.lookup(unsupported) is None


def test_ecdict_exact_entry_keeps_priority_over_supplement(monkeypatch):
    monkeypatch.setattr(offline_dictionary, "_row", lambda _: ("microwavable", "", "adj. 测试词典原释义", "", ""))
    result = offline_dictionary.lookup("microwavable")
    assert result["source"] == "ecdict"
    assert result["chinese"] == ["adj. 测试词典原释义"]
