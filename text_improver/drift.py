"""Put back wording that is likely to change the meaning."""
from __future__ import annotations

import difflib
import re
from collections import Counter
from functools import lru_cache

from . import resources as R
from .mask import PH_RE

RARE_ZIPF = 3.0  # below this, an English word is uncommon enough to treat as a term of art
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def _core(token: str) -> str:
    m = _WORD.search(token)
    return m.group(0).lower() if m else ""


@lru_cache(maxsize=8192)
def _lemmas(word: str) -> frozenset[str]:
    from lemminflect import getAllLemmas

    out = {word}
    for forms in getAllLemmas(word).values():
        out.update(f.lower() for f in forms)
    return frozenset(out)


@lru_cache(maxsize=8192)
def _rare(word: str) -> bool:
    from wordfreq import zipf_frequency

    return zipf_frequency(word, "en") < RARE_ZIPF


def _proper_noun_starts(text: str) -> set[int]:
    """Character offsets of whitespace tokens that contain a proper noun."""
    starts = {m.start(): m.end() for m in re.finditer(r"\S+", text)}
    flagged = set()
    for tok in R.nlp()(text):
        if tok.pos_ == "PROPN" and not PH_RE.search(tok.text):
            for s, e in starts.items():
                if s <= tok.idx < e:
                    flagged.add(s)
    return flagged


def _risky_token(token: str, is_name: bool) -> bool:
    if PH_RE.search(token):
        return False  # locked tokens are already checked by validate()
    if is_name or re.search(r"\d", token):
        return True
    word = _core(token)
    return bool(word) and _rare(word)


def _blocked(old: list[str], new: list[str], label: str) -> bool:
    lists = R.llm_blocklist()
    pairs = lists.get("all", set()) | lists.get(label.lower(), set())
    if not pairs:
        return False
    for a in filter(None, map(_core, old)):
        for b in filter(None, map(_core, new)):
            la, lb = _lemmas(a), _lemmas(b)
            if any(x in la and y in lb for x, y in pairs):
                return True
    return False


def revert_risky(original: str, adjusted: str, label: str) -> tuple[str, list[tuple[str, str]]]:
    """Return the rewrite with risky swaps put back, and the (before, after) pairs that were undone."""
    ta = [m for m in re.finditer(r"\S+", original)]
    tb = re.findall(r"\S+", adjusted)
    words_a = [m.group(0) for m in ta]
    names = _proper_noun_starts(original)
    out: list[str] = []
    undone: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, words_a, tb, autojunk=False).get_opcodes():
        old, new = words_a[i1:i2], tb[j1:j2]
        if op == "equal":
            out += new
            continue
        # Same number of words on both sides: judge each pair on its own, so one risky word
        # doesn't undo a safe swap next to it. Otherwise judge the edit as a whole.
        pairs = list(zip(ta[i1:i2], new)) if len(old) == len(new) else [(None, None)]
        for m, b in pairs:
            src = [m] if m is not None else ta[i1:i2]
            olds = [x.group(0) for x in src]
            news = [b] if b is not None else new
            if any(_risky_token(x.group(0), x.start() in names) for x in src) or _blocked(olds, news, label):
                out += olds
                undone.append((" ".join(olds), " ".join(news)))
            else:
                out += news
    text = " ".join(out)
    # Undoing one edit must never duplicate or drop a locked token that moved elsewhere.
    if Counter(PH_RE.findall(text)) != Counter(PH_RE.findall(original)):
        return original, [(original, adjusted)]
    return text, undone
