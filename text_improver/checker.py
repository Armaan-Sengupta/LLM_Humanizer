"""Local text readings."""
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
    reading: str | None
    weight: float | None
    notes: int
    notes_per_1k: float
    frequent: list[tuple[str, int]]
    sent_len_mean: float
    sent_len_stdev: float
    spread: float  # stdev / mean
    variety: float

    def as_dict(self) -> dict:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _mattr(words: list[str], window: int = 50) -> float:
    if len(words) < window:
        return len(set(words)) / max(1, len(words))
    ratios = [len(set(words[i : i + window])) / window for i in range(len(words) - window + 1)]
    return sum(ratios) / len(ratios)


def reading_score(text: str) -> tuple[str | None, float | None]:
    try:
        from .ratio import score

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
    label, conf = reading_score(text) if len(words) >= 40 else (None, None)
    return Report(
        words=len(words),
        sentences=len(sents),
        reading=label,
        weight=conf,
        notes=hits,
        notes_per_1k=round(1000 * hits / max(1, len(words)), 1),
        frequent=sorted(counts.items(), key=lambda kv: -kv[1])[:12],
        sent_len_mean=round(mean, 1),
        sent_len_stdev=round(sd, 1),
        spread=round(sd / mean, 3) if mean else 0.0,
        variety=round(_mattr(words), 3),
    )


def change_ratio(original: str, adjusted: str) -> float:
    """Fraction of words that differ between the two texts (0 = identical)."""
    a, b = WORD.findall(original.lower()), WORD.findall(adjusted.lower())
    return round(1 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def word_diff_html(original: str, adjusted: str) -> str:
    """Inline HTML diff: deletions struck through in red, insertions in green."""
    import html

    a, b = re.findall(r"\S+|\s+", original), re.findall(r"\S+|\s+", adjusted)
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
