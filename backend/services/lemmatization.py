"""Local, conservative English inflection normalization; no model downloads."""
import re
from functools import lru_cache

from simplemma import lemmatize

# Preserve the original MVP's mappings for stable identities across upgrades.
INFLECTIONS = {
    "models": "model", "datasets": "dataset", "samples": "sample", "features": "feature",
    "predictions": "prediction", "distributions": "distribution", "hypotheses": "hypothesis",
    "algorithms": "algorithm", "parameters": "parameter", "observations": "observation",
    "examples": "example", "errors": "error", "results": "result", "methods": "method",
    "studies": "study", "was": "be", "were": "be", "is": "be", "are": "be",
    "has": "have", "had": "have",
}

# With no part-of-speech model, these common reading-domain nouns must not turn
# into verbs (e.g. overfitting -> overfit). Derivations remain separate concepts.
PROTECTED_TERMS = frozenset({
    "generalization", "overfitting", "training", "learning", "regularization",
    "distribution", "prediction", "hypothesis", "cross-validation", "unseen",
    "data", "bias", "variance", "sampling", "embedding", "reasoning", "computing",
})


@lru_cache(maxsize=20_000)
def normalize_word(word: str) -> str:
    normalized = re.sub(r"\s+", " ", word.strip().lower().replace("’", "'").replace("–", "-"))
    if not re.fullmatch(r"[a-z]+(?:[' -][a-z]+)*", normalized) or len(normalized) > 150:
        raise ValueError("请输入有效英文单词或短语（最多 150 个字符）。")
    # Phrases have their own meaning; never stem their component words.
    if " " in normalized:
        return normalized
    # Dictionary outputs are not necessarily final lemmas: meetings -> meeting
    # -> meet, and bacteria <-> bacterium. Every caller (OCR, marking, history)
    # must arrive at the same key even when given an already normalized lemma.
    path: list[str] = []
    positions: dict[str, int] = {}
    current = normalized
    for _ in range(128):
        if current in positions:
            # Use the same representative no matter where a cycle was entered.
            return min(path[positions[current]:])
        positions[current] = len(path)
        path.append(current)
        if current in PROTECTED_TERMS:
            return current
        lemma = INFLECTIONS.get(current)
        if lemma is None:
            lemma = lemmatize(current, lang="en", greedy=False)
        if lemma == current or not re.fullmatch(r"[a-z]+(?:['-][a-z]+)*", lemma):
            return current
        current = lemma
    # A truncated chain is not a stable identity. Reject it instead of storing a
    # key that changes on the next request; ordinary dictionary chains are short.
    raise ValueError("该单词无法稳定归一化，请使用其他词形。")
