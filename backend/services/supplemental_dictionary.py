"""Small, reviewed exact-spelling additions; never presented as ECDICT or AI.

Definitions and examples below are authored for Lexi Read. Source links support
the terminology and spelling notes, rather than claiming copied dictionary text.
"""
from copy import deepcopy
from hashlib import sha256
import json

_ENTRIES = {
    "horizontal wind vector": {
        "chinese": ["名词短语〔气象〕：水平风矢量；风速矢量在水平面上的分量。"],
        "english": ["The horizontal component of the wind velocity vector."],
        "examples": [{"english": "A horizontal wind vector of (3, 4) m/s has a magnitude of 5 m/s.",
                      "chinese": "分量为（3，4）米每秒的水平风矢量，其模长为 5 米每秒。"}],
        "usage_notes": ["通常以东向分量 u 和北向分量 v 表示，模长是水平风速。",
                        "矢量指向气流吹向的方向；气象中的风向通常表示风吹来的方向，注意区分。"],
        "source_url": "https://www.eol.ucar.edu/content/wind-direction-quick-reference",
    },
    "leap of faith": {
        "chinese": ["习语／名词短语：凭信念迈出的一步；在缺乏充分把握时作出的信任或冒险。"],
        "english": ["A decision to trust or act without being certain of the outcome."],
        "examples": [{"english": "Without knowing whether anyone would join, we took a leap of faith and started the reading club.",
                      "chinese": "虽然不知道是否有人参加，我们还是抱着信念迈出一步，成立了读书会。"}],
        "usage_notes": ["常见搭配：take / make a leap of faith。", "比喻用法，通常不是字面上的跳跃。"],
        "source_url": "https://dictionary.cambridge.org/dictionary/english/leap-of-faith",
    },
    "honourary": {
        "chinese": ["拼写待核对：疑似 honorary 的拼写变体或误拼。",
                    "若原意为 honorary：形容词，荣誉的、名誉的；（职务）无报酬的。"],
        "english": ["Check the spelling: if honorary is intended, it describes an honour or an unpaid position."],
        "examples": [{"english": "The university gave her an honorary degree for her work in education.",
                      "chinese": "这所大学授予她荣誉学位，以表彰她在教育方面的贡献。"}],
        "usage_notes": ["词库保留你的原拼写 honourary，没有自动更正或合并学习记录。",
                        "词典中核实的标准拼写是 honorary；上述例句使用 honorary。请结合原文核对。"],
        "source_url": "https://dictionary.cambridge.org/dictionary/english/honorary",
    },
    "microwavable": {
        "chinese": ["形容词：可用微波炉加热或烹调的；可放入微波炉使用的。"],
        "english": ["Suitable for heating, cooking, or use in a microwave oven."],
        "examples": [{"english": "Check that the container is microwavable before heating your lunch.",
                      "chinese": "加热午餐前，先确认这个容器可以放进微波炉。"}],
        "usage_notes": ["既可以形容食品，也可以形容适用于微波炉的容器。",
                        "microwavable 是美式拼写；英式常写作 microwaveable。"],
        "source_url": "https://dictionary.cambridge.org/dictionary/english/microwavable",
    },
}


def source_revision() -> str:
    """Content-address the reviewed additions used to create sense snapshots."""
    payload = json.dumps(_ENTRIES, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def lookup_supplement(spelling: str) -> dict | None:
    entry = _ENTRIES.get(spelling)
    if entry is None:
        return None
    return {"headword": spelling, "phonetic": "", "forms": [],
            "source": "lexi_supplement", **deepcopy(entry)}
