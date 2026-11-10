"""Split a document into rotating, sentence-aligned windows.

A rotation is a repeating list of phases, each with a word budget and an engine:
x words left alone, then y words for one model, then z words for another, and so on.
The word count runs across the whole document, not per paragraph. Windows only ever
end on a sentence boundary, so each one lands as close to its budget as sentences allow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import resources as R

DEFAULT_PATTERN = "100,150,150"  # words to keep, words for Haiku, words for Gemini
CONTEXT_WORDS = 80  # read-only words sent on each side of an edited span
_CONTEXT_CHARS = 2000  # upper bound on the text scanned to find them


@dataclass(frozen=True)
class Phase:
    words: int
    cfg: object | None = None  # text_improver.llm.LLMConfig; None leaves the text untouched
    label: str = "keep"


@dataclass(frozen=True)
class Span:
    line: int
    start: int
    end: int
    phase: Phase


def parse_pattern(spec: str) -> tuple[int, int, int]:
    """Parse "X,Y,Z" (words to keep, words for model 1, words for model 2)."""
    try:
        values = tuple(int(p) for p in spec.split(","))
    except ValueError:
        raise ValueError(f"span must be three whole numbers like 100,150,150, got {spec!r}") from None
    if len(values) != 3 or min(values) < 0:
        raise ValueError(f"span must be three non-negative numbers like 100,150,150, got {spec!r}")
    if values[1] + values[2] == 0:
        raise ValueError("span must give at least one part some words")
    return values  # type: ignore[return-value]


def assign_phases(sentence_words: list[int], budgets: list[int]) -> list[int]:
    """Return the phase index for each sentence.

    A window closes when adding the next sentence would land further from its budget
    than stopping would. A window always takes at least one sentence, so a sentence
    longer than the budget stays whole. Phases with a zero budget are skipped.
    """
    active = [i for i, w in enumerate(budgets) if w > 0]
    if not active:
        raise ValueError("at least one phase needs a positive word budget")
    out: list[int] = []
    pos = filled = 0
    for n in sentence_words:
        budget = budgets[active[pos]]
        if filled and abs(filled + n - budget) > abs(filled - budget):
            pos = (pos + 1) % len(active)
            filled = 0
        out.append(active[pos])
        filled += n
    return out


def _sentences(doc) -> list[tuple[int, int]]:
    spans = []
    for sent in doc.sents:
        text = sent.text
        if text.strip():
            start = sent.start_char + len(text) - len(text.lstrip())
            spans.append((start, sent.start_char + len(text.rstrip())))
    return spans


def plan(lines: list[str], phases: list[Phase]) -> list[Span]:
    """Cover every sentence with a Span, one Span per run of same-phase sentences in a line."""
    found: list[tuple[int, int, int]] = []
    for i, doc in enumerate(R.nlp().pipe(lines)):
        found += [(i, a, b) for a, b in _sentences(doc)]
    counts = [len(lines[i][a:b].split()) for i, a, b in found]
    chosen = assign_phases(counts, [p.words for p in phases])

    spans: list[Span] = []
    last = None
    for (i, a, b), idx in zip(found, chosen):
        if spans and last == idx and spans[-1].line == i:
            spans[-1] = Span(i, spans[-1].start, b, phases[idx])
        else:
            spans.append(Span(i, a, b, phases[idx]))
        last = idx
    return spans


def context(lines: list[str], span: Span, n_words: int = CONTEXT_WORDS) -> tuple[str, str]:
    """Return up to n_words of text on each side of the span, crossing paragraphs."""
    before = "\n".join(lines[: span.line] + [lines[span.line][: span.start]])
    after = "\n".join([lines[span.line][span.end :]] + lines[span.line + 1 :])
    head = re.findall(r"\S+", before[-_CONTEXT_CHARS:])[-n_words:]
    tail = re.findall(r"\S+", after[:_CONTEXT_CHARS])[:n_words]
    return " ".join(head), " ".join(tail)
