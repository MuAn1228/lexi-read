"""Versioned, explainable learning heuristic; this score is not a probability."""


class MasteryService:
    RULE_VERSION = "heuristic_v1"
    INITIAL_SCORE = 0.5
    DELTAS = {
        "MARK_KNOWN": 0.15,
        "MARK_UNKNOWN": -0.20,
        "MARK_UNCERTAIN": -0.08,
        "LOOKUP": -0.03,
    }

    @classmethod
    def apply(cls, score: float | None, event_type: str) -> float:
        if event_type not in cls.DELTAS:
            raise ValueError(f"Unsupported mastery event: {event_type}")
        baseline = cls.INITIAL_SCORE if score is None else score
        return round(min(1.0, max(0.0, baseline + cls.DELTAS[event_type])), 6)

    @classmethod
    def event_details(cls, score: float | None, event_type: str) -> dict:
        return {
            "mastery_before": score,
            "mastery_after": cls.apply(score, event_type),
            "rule_version": cls.RULE_VERSION,
        }
