"""Compression ratio against a fixed reference text."""
from __future__ import annotations

import lzma
import re
from functools import lru_cache

from .resources import DATA

PRESET = 4


def clean(s: str) -> str:
    s = re.sub(" +", " ", s)
    s = re.sub("\t", "", s)
    s = re.sub("\n+", "\n", s)
    s = re.sub("\n ", "\n", s)
    s = re.sub(" \n", "\n", s)
    return re.sub(r"[^0-9A-Za-z,\.\(\) \n]", "", s)


def _ratio(s: str) -> float:
    raw = s.encode()
    c = lzma.LZMACompressor(preset=PRESET)
    return len(c.compress(raw) + c.flush()) / len(raw)


@lru_cache(maxsize=1)
def _prelude() -> tuple[str, float]:
    p = clean((DATA / "reference.txt").read_text(encoding="utf-8"))
    return p, _ratio(p)


def _score_chunk(chunk: str) -> tuple[str, float]:
    prelude, prelude_ratio = _prelude()
    delta = prelude_ratio - _ratio(prelude + chunk)
    return ("a" if delta >= 0 else "b"), abs(delta * 100)


def score(text: str, chunk_size: int = 1500) -> tuple[str, float]:
    contents = clean(text)
    chunks, start, end = [], 0, 0
    while start + chunk_size < len(contents) and end != -1:
        end = contents.rfind(" ", start, start + chunk_size + 1)
        chunks.append(contents[start:end])
        start = end + 1
    chunks.append(contents[start:])
    total = 0.0
    for c in chunks:
        label, conf = _score_chunk(c)
        w = len(c) / max(1, len(contents))
        total += -conf * w if label == "a" else conf * w
    return ("a" if total < 0 else "b"), abs(total)
