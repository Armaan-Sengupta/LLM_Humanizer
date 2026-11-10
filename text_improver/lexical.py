"""Word- and phrase-level adjustments."""
from __future__ import annotations

import random

from wordfreq import zipf_frequency

from . import resources as R
from .edits import Edit, article_for, inflect_like, match_case
from .mask import is_placeholder

MODALS = {"can", "must", "may", "might", "will", "should", "could", "would"}
PLURAL_PRONOUNS = {"they", "we", "these", "those", "you"}
# Nouns are left out by default: in technical writing they are usually terms of art.
CONTENT_POS = {"VERB", "ADJ", "ADV"}
MAX_SYNONYM_ZIPF = 5.3  # very common words are too polysemous to swap safely


def _be_form(verb_tok, tag: str) -> str:
    """Pick is/are/was/were for a replacement starting with 'be'."""
    subj = next((c for c in verb_tok.children if c.dep_ in ("nsubj", "nsubjpass")), None)
    plural = subj is not None and (subj.tag_ in ("NNS", "NNPS") or subj.lower_ in PLURAL_PRONOUNS)
    if tag == "VBD":
        return "were" if plural else "was"
    if tag == "VBZ":
        return "is"
    if tag == "VBP":
        return "are"
    return {"VBG": "being", "VBN": "been"}.get(tag, "be")


def _render(rep: str, first_tok, entry: dict) -> str:
    if not rep:
        return rep
    head, sep, rest = rep.partition(" ")
    if entry.get("sent_start"):
        return rep
    if entry.get("no_inflect"):
        return match_case(first_tok.text, rep)
    if first_tok.pos_ in ("VERB", "AUX") and head not in MODALS:
        if head == "be":
            rep = _be_form(first_tok, first_tok.tag_) + sep + rest
        else:
            rep = inflect_like(rep, first_tok.tag_)
    elif first_tok.pos_ == "NOUN" and first_tok.tag_ == "NNS" and not sep:
        rep = inflect_like(rep, "NNS")
    return match_case(first_tok.text, rep)


def _token_matches(pw: str, tok) -> bool:
    return pw == tok.lower_ or pw == tok.lemma_.lower()


def phrase_edits(doc, rng: random.Random, p_apply: float) -> tuple[list[Edit], set[int]]:
    edits: list[Edit] = []
    used: set[int] = set()
    bank = R.phrasebank()
    i = 0
    while i < len(doc):
        hit = None
        for e in bank:
            n = len(e["toks"])
            if i + n > len(doc):
                continue
            span = doc[i : i + n]
            if any(t.i in used or is_placeholder(t.text) for t in span):
                continue
            if not all(_token_matches(pw, t) for pw, t in zip(e["toks"], span)):
                continue
            if "pos" in e and doc[i].pos_ != e["pos"]:
                continue
            if "tag_only" in e and doc[i].tag_ not in e["tag_only"]:
                continue
            if e.get("sent_start") and not doc[i].is_sent_start:
                continue
            hit = (e, span)
            break
        if hit is None:
            i += 1
            continue
        e, span = hit
        if rng.random() > p_apply:
            # skip the whole matched phrase so a shorter sub-pattern can't half-rewrite it
            used.update(t.i for t in span)
            i += len(span)
            continue
        options = list(e["r"])
        if span.end < len(doc):  # avoid "Beyond this, this ..." style echoes
            nxt_word = doc[span.end].lower_
            options = [r for r in options if not r or r.rstrip(",").split()[-1].lower() != nxt_word] or options
        rep = _render(rng.choice(options), doc[i], e)
        start, end = span[0].idx, span[-1].idx + len(span[-1].text)
        nxt = doc[span.end] if span.end < len(doc) else None
        if not rep:
            if nxt is None:
                i += len(span)
                continue
            # delete phrase (+ a trailing comma) and the following space;
            # re-capitalise the next word if we were at sentence start
            if nxt.text == "," and nxt.i + 1 < len(doc):
                nxt = doc[nxt.i + 1]
            end = nxt.idx
            if span[0].is_sent_start and nxt.text[:1].islower():
                end, rep = nxt.idx + 1, nxt.text[:1].upper()
        elif i > 0 and doc[i - 1].lower_ in ("a", "an") and not e["toks"][0] in ("a", "an"):
            art = doc[i - 1]
            start = art.idx
            rep = match_case(art.text, article_for(rep)) + " " + rep
        edits.append(Edit(start, end, rep, "phrase"))
        used.update(t.i for t in span)
        i += len(span)
    return edits, used


def synonym_edits(doc, rng: random.Random, p_apply: float, skip: set[int]) -> list[Edit]:
    syn = R.synonyms()
    block = R.synonym_blocklist()
    edits: list[Edit] = []
    for sent in doc.sents:
        sent_words = {t.lower_ for t in sent} | {t.lemma_.lower() for t in sent}
        for tok in sent:
            if (
                tok.i in skip
                or tok.pos_ not in CONTENT_POS
                or not tok.is_alpha
                or tok.is_stop
                or tok.ent_type_
                or tok.dep_ in ("compound", "prt")
                or any(c.dep_ == "prt" for c in tok.children)
                or (tok.text[:1].isupper() and not tok.is_sent_start)
            ):
                continue
            lemma = tok.lemma_.lower()
            cands = syn.get(lemma, {}).get(tok.pos_)
            if not cands or zipf_frequency(tok.lower_, "en") > MAX_SYNONYM_ZIPF:
                continue
            cands = [c for c in cands[:3] if (lemma, c) not in block and c not in sent_words]
            if not cands or rng.random() > p_apply:
                continue
            c = rng.choice(cands)
            if tok.tag_ not in ("VB", "VBP", "NN", "JJ", "RB"):
                from lemminflect import getInflection

                forms = getInflection(c, tag=tok.tag_)
                if not forms:
                    continue
                c = forms[0]
            rep = match_case(tok.text, c)
            start = tok.idx
            if tok.i > 0 and doc[tok.i - 1].lower_ in ("a", "an") and (tok.i - 1) not in skip:
                art = doc[tok.i - 1]
                start = art.idx
                rep = match_case(art.text, article_for(c)) + " " + rep
            edits.append(Edit(start, tok.idx + len(tok.text), rep, "synonym"))
            sent_words.add(c)
    return edits
