"""Sentence-level restructuring, kept deliberately conservative.

* clause_move: "X happens when Y."  <->  "When Y, X happens."
* split_semicolon: "A; B." -> "A. B."  (both halves must be full clauses)
* split_and: long "A, and B." -> "A. B." when B has its own subject
"""
from __future__ import annotations

import random

from .edits import Edit, cap, decap
from .mask import is_placeholder

MOVABLE_MARKS = {"when", "if", "because", "although", "though", "while", "once",
                 "unless", "after", "before", "until", "whereas"}
SPLIT_AND_MIN_TOKENS = 26


def _has_clause(span) -> bool:
    has_verb = any(t.pos_ in ("VERB", "AUX") for t in span)
    has_subj = any(t.dep_ in ("nsubj", "nsubjpass", "expl") for t in span)
    return has_verb and has_subj and len(span) >= 4


def _end_punct(sent):
    last = sent[-1]
    return last if last.is_punct and last.text in ".!?" else None


def clause_move(sent) -> str | None:
    punct = _end_punct(sent)
    if punct is None or any(t.text in '"“”;:' for t in sent):
        return None
    root = sent.root
    for c in root.children:
        if c.dep_ != "advcl":
            continue
        marks = [m for m in c.children if m.dep_ == "mark" and m.lower_ in MOVABLE_MARKS]
        if not marks:
            continue
        span = sent.doc[c.left_edge.i : c.right_edge.i + 1]
        if span[0].i != marks[0].i:
            continue
        # (a) trailing clause -> front
        if span.end == punct.i:
            main = sent.doc[sent.start : span.start]
            if len(main) and main[-1].text == ",":
                main = main[:-1]
            if len(main) < 3 or not _has_clause(main) or any(t.text == "," for t in main):
                return None
            clause = cap(span.text)
            return f"{clause}, {decap(main.text, main[0])}{punct.text}"
        # (b) leading clause -> back
        if span.start == sent.start and span.end < len(sent.doc) and sent.doc[span.end].text == ",":
            main = sent.doc[span.end + 1 : punct.i]
            if len(main) < 3 or len(main) > 20 or not _has_clause(main) or any(t.text == "," for t in main):
                return None
            return f"{cap(main.text)} {decap(span.text, span[0])}{punct.text}"
    return None


def split_semicolon(sent) -> str | None:
    semis = [t for t in sent if t.text == ";"]
    if len(semis) != 1 or _end_punct(sent) is None:
        return None
    s = semis[0]
    left = sent.doc[sent.start : s.i]
    right = sent.doc[s.i + 1 : sent.end]
    if not (_has_clause(left) and _has_clause(right[:-1])):
        return None
    first = right[0]
    if first.lower_ in ("and", "but", "or", "which", "whereas"):
        return None
    return f"{left.text}. {cap(right.text) if not is_placeholder(first.text) else right.text}"


def split_and(sent) -> str | None:
    if len(sent) < SPLIT_AND_MIN_TOKENS or _end_punct(sent) is None:
        return None
    root = sent.root
    for c in root.children:
        if c.dep_ != "conj" or c.pos_ not in ("VERB", "AUX"):
            continue
        if not any(ch.dep_ in ("nsubj", "nsubjpass") for ch in c.children):
            continue
        cc = next((t for t in root.children if t.dep_ == "cc" and t.lower_ == "and" and t.i < c.i), None)
        if cc is None or cc.i == 0 or sent.doc[cc.i - 1].text != ",":
            continue
        left = sent.doc[sent.start : cc.i - 1]
        right = sent.doc[cc.i + 1 : sent.end]
        if len(left) < 8 or len(right) < 6:
            return None
        return f"{left.text}. {cap(right.text)}"
    return None


TRANSFORMS = [("clause_move", clause_move, 0.5), ("split_semicolon", split_semicolon, 0.7),
              ("split_and", split_and, 0.6)]


def structure_edits(doc, rng: random.Random, intensity: float) -> list[Edit]:
    edits: list[Edit] = []
    for sent in doc.sents:
        # keep trailing whitespace intact: edit only the sentence text itself
        for name, fn, weight in TRANSFORMS:
            if rng.random() > weight * intensity:
                continue
            new = fn(sent)
            if new and new != sent.text:
                edits.append(Edit(sent.start_char, sent.end_char, new, name))
                break
    return edits
