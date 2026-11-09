"""Single-process port of ZipPy's LZMA detector (Thinkst Applied Research, MIT;
https://github.com/thinkst/zippy). Same algorithm and prelude text; the
multiprocessing pool is dropped so it works inside a frozen executable.

Idea: seed LZMA with a corpus of AI-written text. If appending the sample makes
the stream compress *better* than the prelude alone, the sample resembles the
AI corpus. The output is a (label, confidence) pair; confidence is small.
"""
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
    p = clean((DATA / "zippy-ai-generated.txt").read_text(encoding="utf-8"))
    return p, _ratio(p)


def _score_chunk(chunk: str) -> tuple[str, float]:
    prelude, prelude_ratio = _prelude()
    delta = prelude_ratio - _ratio(prelude + chunk)
    return ("AI" if delta >= 0 else "Human"), abs(delta * 100)


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
        total += -conf * w if label == "AI" else conf * w
    return ("AI" if total < 0 else "Human"), abs(total)
