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
    if normalized in INFLECTIONS:
        return INFLECTIONS[normalized]
    # Phrases have their own meaning; never stem their component words.
    if " " in normalized or normalized in PROTECTED_TERMS:
        return normalized
    lemma = lemmatize(normalized, lang="en", greedy=False)
    return lemma if re.fullmatch(r"[a-z]+(?:['-][a-z]+)*", lemma) else normalized
