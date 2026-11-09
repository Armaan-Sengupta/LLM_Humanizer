"""Small helpers shared by the transform stages."""
from __future__ import annotations

from dataclasses import dataclass

from lemminflect import getInflection

from .mask import is_placeholder

VOWEL_SOUND_EXCEPTIONS_AN = {"hour", "honest", "honour", "honor", "heir", "herb"}
CONSONANT_SOUND_EXCEPTIONS_A = ("uni", "use", "usu", "uti", "eu", "one", "once", "ure", "ubiq")


@dataclass
class Edit:
    start: int
    end: int
    text: str
    stage: str
    before: str = ""

    def overlaps(self, other: "Edit") -> bool:
        return self.start < other.end and other.start < self.end


def apply_edits(text: str, edits: list[Edit]) -> tuple[str, list[Edit]]:
    """Apply non-overlapping edits (first one wins on overlap)."""
    accepted: list[Edit] = []
    for e in sorted(edits, key=lambda e: (e.start, -(e.end - e.start))):
        if any(e.overlaps(a) for a in accepted):
            continue
        e.before = text[e.start : e.end]
        accepted.append(e)
    out = text
    for e in sorted(accepted, key=lambda e: e.start, reverse=True):
        out = out[: e.start] + e.text + out[e.end :]
    return out, accepted


def article_for(word: str) -> str:
    w = word.lower()
    if w.startswith(tuple(VOWEL_SOUND_EXCEPTIONS_AN)):
        return "an"
    if w.startswith(CONSONANT_SOUND_EXCEPTIONS_A):
        return "a"
    return "an" if w[:1] in "aeiou" else "a"


def match_case(src: str, rep: str) -> str:
    if not rep:
        return rep
    if src.isupper() and len(src) > 1:
        return rep.upper()
    if src[:1].isupper():
        return rep[:1].upper() + rep[1:]
    return rep


def inflect_like(word: str, tag: str) -> str:
    """Inflect a base-form word to a Penn tag (VBD, NNS, ...). Multi-word: first word."""
    head, sep, rest = word.partition(" ")
    if tag in {"VB", "VBP", "NN", "JJ", "RB", ""} or not head:
        return word
    forms = getInflection(head, tag=tag)
    if forms:
        head = forms[0]
    return head + sep + rest


def decap(text: str, first_token) -> str:
    """Lower-case the first letter unless it is a proper noun / 'I' / acronym / placeholder."""
    if not text or first_token is None:
        return text
    t = first_token.text
    if (
        first_token.pos_ == "PROPN"
        or t == "I"
        or (t.isupper() and len(t) > 1)
        or is_placeholder(t)
    ):
        return text
    return text[:1].lower() + text[1:]


def cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
