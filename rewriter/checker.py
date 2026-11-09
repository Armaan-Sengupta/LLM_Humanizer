"""Local text diagnostics. These are rough signals, not a Turnitin/GPTZero substitute.

* zippy: Thinkst's compression-based detector (LZMA ratio vs. an AI-text prelude)
* markers: AI-marker words/phrases per 1,000 words (Kobak et al. + phrase bank)
* burstiness: variation in sentence length (humans vary more)
* lexical diversity: moving-average type/token ratio
* change: how much of the original survived (word-level)
"""
from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import asdict, dataclass

from . import resources as R

WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


@dataclass
class Report:
    words: int
    sentences: int
    zippy_label: str | None
    zippy_confidence: float | None
    marker_hits: int
    markers_per_1k: float
    top_markers: list[tuple[str, int]]
    sent_len_mean: float
    sent_len_stdev: float
    burstiness: float  # stdev / mean
    mattr: float

    def as_dict(self) -> dict:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _mattr(words: list[str], window: int = 50) -> float:
    if len(words) < window:
        return len(set(words)) / max(1, len(words))
    ratios = [len(set(words[i : i + window])) / window for i in range(len(words) - window + 1)]
    return sum(ratios) / len(ratios)


def zippy_score(text: str) -> tuple[str | None, float | None]:
    try:
        from .zippy_lite import score

        label, conf = score(text)
        return label, round(float(conf), 4)
    except Exception:
        return None, None


def analyze(text: str) -> Report:
    words = [w.lower() for w in WORD.findall(text)]
    sents = _sentences(text)
    lens = [len(WORD.findall(s)) for s in sents] or [0]
    mean = statistics.mean(lens)
    sd = statistics.pstdev(lens) if len(lens) > 1 else 0.0

    counts: dict[str, int] = {}
    mw = R.marker_words()
    for w in words:
        if w in mw:
            counts[w] = counts.get(w, 0) + 1
    low = " " + " ".join(words) + " "
    for ph in R.marker_phrases():
        n = low.count(" " + " ".join(WORD.findall(ph.lower())) + " ")
        if n:
            counts[ph] = counts.get(ph, 0) + n
    hits = sum(counts.values())
    label, conf = zippy_score(text) if len(words) >= 40 else (None, None)
    return Report(
        words=len(words),
        sentences=len(sents),
        zippy_label=label,
        zippy_confidence=conf,
        marker_hits=hits,
        markers_per_1k=round(1000 * hits / max(1, len(words)), 1),
        top_markers=sorted(counts.items(), key=lambda kv: -kv[1])[:12],
        sent_len_mean=round(mean, 1),
        sent_len_stdev=round(sd, 1),
        burstiness=round(sd / mean, 3) if mean else 0.0,
        mattr=round(_mattr(words), 3),
    )


def change_ratio(original: str, rewritten: str) -> float:
    """Fraction of words that differ between the two texts (0 = identical)."""
    a, b = WORD.findall(original.lower()), WORD.findall(rewritten.lower())
    return round(1 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def word_diff_html(original: str, rewritten: str) -> str:
    """Inline HTML diff: deletions struck through in red, insertions in green."""
    import html

    a, b = re.findall(r"\S+|\s+", original), re.findall(r"\S+|\s+", rewritten)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
            continue
        if op in ("delete", "replace"):
            out.append(f'<del style="color:#b42318">{html.escape("".join(a[i1:i2]))}</del>')
        if op in ("insert", "replace"):
            out.append(f'<ins style="color:#067647;text-decoration:none;font-weight:600">{html.escape("".join(b[j1:j2]))}</ins>')
    return "".join(out).replace("\n", "<br>")
