"""Lazy-loaded shared resources (spaCy model, data tables)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def nlp():
    import spacy

    return spacy.load("en_core_web_sm")


@lru_cache(maxsize=1)
def synonyms() -> dict:
    return json.loads((DATA / "lexicon.json").read_text())


@lru_cache(maxsize=1)
def synonym_blocklist() -> set[tuple[str, str]]:
    pairs = set()
    path = DATA / "pairs_b.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.split("#")[0].strip()
            if "->" in line:
                a, b = (x.strip().lower() for x in line.split("->", 1))
                pairs.add((a, b))
    return pairs


@lru_cache(maxsize=1)
def llm_blocklist() -> dict[str, set[tuple[str, str]]]:
    """Meaning-changing model swaps, keyed by model label ("all", "haiku", "gemini")."""
    pairs: dict[str, set[tuple[str, str]]] = {"all": set(synonym_blocklist())}
    path = DATA / "pairs_a.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if "->" not in line:
                continue
            label = "all"
            if ":" in line.split("->", 1)[0]:
                label, line = (x.strip() for x in line.split(":", 1))
            a, b = (x.strip().lower() for x in line.split("->", 1))
            label = {"a": "haiku", "b": "gemini"}.get(label.lower(), label.lower())
            pairs.setdefault(label, set()).add((a, b))
    return pairs


@lru_cache(maxsize=1)
def phrasebank() -> list[dict]:
    entries = json.loads((DATA / "phrasebank.json").read_text())["entries"]
    for e in entries:
        e["toks"] = e["p"].lower().split()
    # longest patterns first so "delve deeper into" beats "delve"
    return sorted(entries, key=lambda e: -len(e["toks"]))


@lru_cache(maxsize=1)
def marker_words() -> set[str]:
    words = set((DATA / "words_a.txt").read_text().split())
    for e in phrasebank():
        if e.get("src") == "k" and len(e["toks"]) == 1:
            words.add(e["toks"][0])
    return words


@lru_cache(maxsize=1)
def marker_phrases() -> list[str]:
    phrases = [l.strip().lower() for l in (DATA / "words_b.txt").read_text().splitlines() if l.strip()]
    for e in phrasebank():
        if e.get("src") == "k" and len(e["toks"]) > 1 and not e.get("sent_start"):
            phrases.append(" ".join(e["toks"]))
    return phrases
