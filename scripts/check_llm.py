"""Verify the configured LLM using two synthetic requests, without database access.

From the project root: python scripts/check_llm.py
Reads the same .env/environment settings as the backend. Explicit invocation
may incur provider charges. Importing this module never makes a request.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agents.vocabulary import VocabularyAgent  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.services.llm import LLMService  # noqa: E402

WORD = "curious"
SENTENCE = "A curious student explores the garden and asks how plants grow."
ARTICLE = SENTENCE + " The teacher explains how sunlight and water help plants."


def fail(category: str, message: str, code: int = 1) -> int:
    # Only fixed diagnostic text is printed: never settings or provider output.
    print(f"FAIL [{category}]: {message}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(
        "Check the configured model with synthetic explanation and recommendation "
        "requests (provider charges may apply). No personal data or database access."))
    parser.parse_args(argv)
    try:
        settings = Settings()
    except Exception:
        return fail("configuration", "Unable to load settings; check .env values.", 2)

    if settings.llm_provider == "offline":
        return fail("offline", "Set LLM_PROVIDER=openai_compatible to check a model connection.", 2)
    if settings.llm_provider != "openai_compatible":
        return fail("configuration", "Only LLM_PROVIDER=openai_compatible is supported.", 2)
    if not settings.llm_api_key.strip() or not settings.llm_model.strip():
        return fail("configuration", "Set both LLM_API_KEY and LLM_MODEL before running this check.", 2)

    try:
        service = LLMService(settings)
        analysis = service.analyze_word(WORD, WORD, SENTENCE, ARTICLE)
        if analysis.source != "llm":
            return fail("fallback", "Explanation fell back to offline output; model connection is unverified.")
        generated = service.recommend_related_words(WORD, SENTENCE, ARTICLE, [], [])
        if generated.source != "llm":
            return fail("fallback", "Recommendations fell back to offline output; model connection is unverified.")
        # Use only the pure ranking method. recommend() would write learning events.
        selected = VocabularyAgent(None, service).rank(generated.candidates, WORD, ARTICLE, [], [])
        if len(selected) > 3:
            return fail("ranking", "Related-word output exceeded the application limit of 3.")
    except Exception:
        # The adapter deliberately hides provider exceptions. Do not guess whether
        # failures are authentication, transport, or schema errors, or expose them.
        return fail("runtime", "Check could not finish; verify configuration and installed dependencies.")

    print("PASS: explanation and recommendations both use source=llm; JSON validated; "
          f"{len(selected)} related words after ranking (limit 3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
