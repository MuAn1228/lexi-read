"""Read only approved text fields from an incomplete model JSON object.

This parser never repairs JSON or treats a key-looking substring as a field.
The returned string is a draft, not a validated generation or cache candidate.
"""
import json
import re
import unicodedata
from typing import Literal


class _Incomplete(Exception):
    pass


class _Invalid(Exception):
    pass


_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}
_SPACE = " \t\r\n"


class _Reader:
    def __init__(self, content: str, target: tuple[str, ...]):
        self.content = content
        self.target = target
        self.index = 0
        self.text = ""
        self.recognized = None

    def space(self):
        while self.index < len(self.content) and self.content[self.index] in _SPACE:
            self.index += 1

    def expect(self, char: str):
        self.space()
        if self.index >= len(self.content):
            raise _Incomplete
        if self.content[self.index] != char:
            raise _Invalid
        self.index += 1

    def string(self, path: tuple[str, ...] | None = None) -> str:
        self.expect('"')
        decoded = []

        def publish():
            if path == self.target:
                self.text = "".join(decoded)

        while self.index < len(self.content):
            char = self.content[self.index]
            self.index += 1
            if char == '"':
                publish()
                return "".join(decoded)
            if char == "\\":
                if self.index >= len(self.content):
                    publish()
                    raise _Incomplete
                escape = self.content[self.index]
                self.index += 1
                if escape == "u":
                    # Hold a half escape or high surrogate until a complete
                    # Unicode scalar is available; neither can reach the UI.
                    if len(self.content) - self.index < 4:
                        publish()
                        raise _Incomplete
                    digits = self.content[self.index:self.index + 4]
                    if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                        raise _Invalid
                    code = int(digits, 16)
                    self.index += 4
                    if 0xD800 <= code <= 0xDBFF:
                        remaining = self.content[self.index:]
                        if len(remaining) < 6 and "\\u".startswith(remaining[:2]):
                            publish()
                            raise _Incomplete
                        if not remaining.startswith("\\u") or len(remaining) < 6:
                            raise _Invalid
                        low_digits = remaining[2:6]
                        if not re.fullmatch(r"[0-9a-fA-F]{4}", low_digits):
                            raise _Invalid
                        low = int(low_digits, 16)
                        if not 0xDC00 <= low <= 0xDFFF:
                            raise _Invalid
                        self.index += 6
                        char = chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00)
                    elif 0xDC00 <= code <= 0xDFFF:
                        raise _Invalid
                    else:
                        char = chr(code)
                elif escape in _ESCAPES:
                    char = _ESCAPES[escape]
                else:
                    raise _Invalid
            elif ord(char) < 0x20:
                raise _Invalid
            if unicodedata.category(char) == "Cs":
                raise _Invalid
            decoded.append(char)
        publish()
        raise _Incomplete

    def value(self, path: tuple[str, ...], depth: int = 0):
        if depth > 32:
            raise _Invalid
        self.space()
        if self.index >= len(self.content):
            raise _Incomplete
        char = self.content[self.index]
        if char == '"':
            return self.string(path)
        if char == "{":
            self.index += 1
            self.space()
            if self.index == len(self.content):
                raise _Incomplete
            if self.content[self.index] == "}":
                self.index += 1
                return None
            keys = set()
            while True:
                key = self.string()
                if key in keys:
                    raise _Invalid
                keys.add(key)
                self.expect(":")
                value = self.value(path + (key,), depth + 1)
                if path == () and key == "recognized":
                    self.recognized = value
                self.space()
                if self.index >= len(self.content):
                    raise _Incomplete
                end = self.content[self.index]
                self.index += 1
                if end == "}":
                    return None
                if end != ",":
                    raise _Invalid
        if char == "[":
            self.index += 1
            self.space()
            if self.index == len(self.content):
                raise _Incomplete
            if self.content[self.index] == "]":
                self.index += 1
                return None
            while True:
                self.value(path + ("[]",), depth + 1)
                self.space()
                if self.index >= len(self.content):
                    raise _Incomplete
                end = self.content[self.index]
                self.index += 1
                if end == "]":
                    return None
                if end != ",":
                    raise _Invalid
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if char == literal[0]:
                remaining = self.content[self.index:]
                if len(remaining) < len(literal) and literal.startswith(remaining):
                    raise _Incomplete
                if not remaining.startswith(literal):
                    raise _Invalid
                self.index += len(literal)
                if self.index < len(self.content) and self.content[self.index] not in _SPACE + ",}]":
                    raise _Invalid
                return value
        if char in "-0123456789":
            start = self.index
            while self.index < len(self.content) and self.content[self.index] not in _SPACE + ",}]":
                self.index += 1
            number = self.content[start:self.index]
            if not _NUMBER.fullmatch(number):
                raise _Invalid
            return json.loads(number)
        raise _Invalid


def progressive_text(content: str, kind: Literal["dictionary", "assistant"]) -> str:
    """Return a safe received prefix of detail.summary or the top-level answer.

    Dictionary drafts require an already received, literal recognized=true.
    Callers must disable this function entirely for assistant quiz mode. Drafts
    stay bounded and are plain text; callers must render them as text.
    """
    if kind not in {"dictionary", "assistant"} or not isinstance(content, str) or len(content) > 30_000:
        return ""
    reader = _Reader(content, ("detail", "summary") if kind == "dictionary" else ("answer",))
    try:
        reader.space()
        if reader.index == len(content):
            return ""
        if content[reader.index] != "{":
            return ""
        reader.value(())
        reader.space()
        if reader.index != len(content):
            return ""
    except _Incomplete:
        pass
    except (_Invalid, ValueError, RecursionError):
        return ""
    if kind == "dictionary" and reader.recognized is not True:
        return ""
    if any(unicodedata.category(char) in {"Cs", "Cc"} and char not in "\n\t"
           for char in reader.text):
        return ""
    return reader.text[:300 if kind == "dictionary" else 3000].strip()
