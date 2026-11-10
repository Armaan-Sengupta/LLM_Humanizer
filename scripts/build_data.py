"""Build the runtime data files in rewriter/data/ from the third_party/ sources.

Run once after cloning (the built files are also committed, so this is only
needed if you change the sources):

    python scripts/build_data.py

Sources (see third_party/ for licenses):
  * retext-simplify patterns (MIT)          -> simplify.json
  * PPDB 1.0 XXXL lexical synonyms (CC-BY)  -> synonyms.json  (filtered by WordNet + wordfreq)
  * Kobak et al. excess vocabulary (MIT)    -> markers_kobak.txt
  * Clarkson AI phrase list (MIT)           -> markers_phrases.txt
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TP = ROOT / "third_party"
OUT = ROOT / "text_improver" / "data"


# --------------------------------------------------------------------------- #
# 1. retext-simplify
# --------------------------------------------------------------------------- #
def build_simplify() -> None:
    js = (TP / "retext-simplify-patterns.js").read_text()
    # Turn the JS object literal into JSON with a tiny parser (no node needed).
    body = js[js.index("export const patterns = {") + len("export const patterns = ") :]
    entries = {}
    for m in re.finditer(
        r"""(?:'([^']+)'|([A-Za-z]+)):\s*\{\s*(?:omit:\s*true,\s*)?replace:\s*\[([^\]]*)\]\s*(?:,\s*omit:\s*true\s*)?\}""",
        body,
    ):
        key = m.group(1) or m.group(2)
        reps = re.findall(r"'([^']*)'", m.group(3))
        omit = "omit: true" in m.group(0)
        entries[key] = {"replace": reps, "omit": omit}
    (OUT / "simplify.json").write_text(json.dumps(entries, indent=1, sort_keys=True))
    print(f"simplify.json: {len(entries)} patterns")


# --------------------------------------------------------------------------- #
# 2. PPDB ∩ WordNet synonyms
# --------------------------------------------------------------------------- #
WN_POS = {"n": "NOUN", "v": "VERB", "a": "ADJ", "s": "ADJ", "r": "ADV"}
LI_POS = {"NOUN": "NOUN", "VERB": "VERB", "ADJ": "ADJ", "ADV": "ADV"}


def build_synonyms() -> None:
    from lemminflect import getAllLemmas
    from nltk.corpus import wordnet as wn
    from wordfreq import zipf_frequency

    stop = set(
        "a an the and or but if of to in on at by for with from as is are was were be been "
        "being have has had do does did not no yes this that these those it its they them "
        "we us our you your he she his her i me my can could will would shall should may "
        "might must also very more most less least such than then there here when where "
        "which who whom what why how all any each some many much few other another".split()
    )

    def lemmas(word: str) -> set[str]:
        out = {word}
        for forms in getAllLemmas(word).values():
            out.update(forms)
        return out

    # synset lookup is the expensive bit: cache by word
    syn_cache: dict[str, dict[str, set]] = {}

    TOP_SENSES = 3  # WordNet lists senses most-frequent first; rare senses cause bad swaps

    def synsets_by_pos(word: str) -> dict[str, set]:
        if word not in syn_cache:
            d: dict[str, list] = defaultdict(list)
            for s in wn.synsets(word):
                d[WN_POS[s.pos()]].append(s.name())
            syn_cache[word] = {p: set(v[:TOP_SENSES]) for p, v in d.items()}
        return syn_cache[word]

    from lemminflect import getLemma

    def is_base_form(word: str, pos: str) -> bool:
        return word in getLemma(word, upos=pos)

    table: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    n_in = n_kept = 0
    with open(TP / "ppdb-lexical-synonyms.tsv") as fh:
        for line in fh:
            n_in += 1
            a, _, b = line.rstrip("\n").partition("\t")
            if not (a.isalpha() and b.isalpha() and a.islower() and b.islower()):
                continue
            if a in stop or b in stop or len(a) < 3 or len(b) < 3:
                continue
            # drop inflectional variants (enhance/enhancing)
            if lemmas(a) & lemmas(b):
                continue
            za, zb = zipf_frequency(a, "en"), zipf_frequency(b, "en")
            if za < 2.5 or zb < 3.0 or zb < za - 1.0:
                continue
            # require WordNet to agree they share a sense in the same POS
            sa, sb = synsets_by_pos(a), synsets_by_pos(b)
            for pos in set(sa) & set(sb):
                if sa[pos] & sb[pos] and is_base_form(a, pos) and is_base_form(b, pos):
                    table[a][pos][b] = round(zb, 2)
                    n_kept += 1

    # keep top-6 most frequent replacements per (word, pos)
    out = {
        w: {pos: sorted(c, key=c.get, reverse=True)[:6] for pos, c in by_pos.items()}
        for w, by_pos in table.items()
    }
    (OUT / "lexicon.json").write_text(json.dumps(out, sort_keys=True, separators=(",", ":")))
    print(f"lexicon.json: {n_in} pairs -> {n_kept} kept, {len(out)} head words")


# --------------------------------------------------------------------------- #
# 3. marker lists for the checker
# --------------------------------------------------------------------------- #
def build_markers() -> None:
    rows = list(csv.DictReader(open(TP / "kobak-excess-words.csv")))
    style = sorted({r["word"] for r in rows if r["type"] == "style"})
    (OUT / "words_a.txt").write_text("\n".join(style) + "\n")
    phrases = [
        l.strip() for l in (TP / "clarkson-ai-phrases.txt").read_text().splitlines() if l.strip()
    ]
    (OUT / "words_b.txt").write_text("\n".join(phrases) + "\n")
    print(f"words: {len(style)} style words, {len(phrases)} phrases")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_simplify()
    build_markers()
    build_synonyms()
