"""End-to-end rewrite pipeline.

Local:  1 clean -> 2 mask -> 3 phrase bank
Then either
  LLM:  4+5 Claude Haiku (context-aware synonyms + restructuring) on the masked text
or
  rules: 4 PPDB/WordNet synonyms -> 5 clause moves/splits
followed by unmask. If the LLM output fails validation, that paragraph falls back to rules.
"""
from __future__ import annotations

import difflib
import random
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import resources as R
from .clean import clean_text
from .edits import apply_edits
from .lexical import phrase_edits, synonym_edits
from .mask import Masked, mask, mask_latex_document
from .structure import structure_edits


@dataclass
class Options:
    intensity: float = 0.5  # 0..1, scales how often each rule-based transform fires
    seed: int | None = None
    phrases: bool = True
    synonyms: bool = True
    structure: bool = True
    clean: bool = True
    latex: bool = False
    protect_quotes: bool = True
    protected_terms: list[str] = field(default_factory=list)
    llm: "object | None" = None  # rewriter.llm.LLMConfig -> use Claude for stages 4-5
    llm_workers: int = 4


@dataclass
class Change:
    stage: str
    before: str
    after: str


@dataclass
class Result:
    text: str
    changes: list[Change]
    clean_stats: dict
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Block:
    lead: str
    trail: str
    masked: Masked | None = None
    work: str = ""
    changes: list[Change] = field(default_factory=list)
    llm_text: str | None = None
    llm_error: str | None = None


def _tidy(s: str) -> str:
    s = re.sub(r"[ \t]{2,}", " ", s)
    return re.sub(r" +([,.;:!?])", r"\1", s)


def _prepare(text: str, opts: Options, rng: random.Random) -> _Block:
    """Stages 2-3 (mask + phrase bank). Leaves text in masked form."""
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :] if text.strip() else ""
    b = _Block(lead, trail)
    core = text.strip()
    if not core:
        return b
    m = mask(core, latex=opts.latex, protected_terms=opts.protected_terms, protect_quotes=opts.protect_quotes)
    b.masked, b.work = m, m.text
    if opts.phrases:
        doc = R.nlp()(b.work)
        pe, _ = phrase_edits(doc, rng, p_apply=min(1.0, 0.45 + opts.intensity * 0.55))
        b.work, done = apply_edits(b.work, pe)
        b.changes += [Change(e.stage, m.unmask(e.before), m.unmask(e.text)) for e in done]
    return b


def _rules_4_5(b: _Block, opts: Options, rng: random.Random) -> None:
    m = b.masked
    nlp = R.nlp()
    if opts.synonyms:
        doc = nlp(b.work)
        b.work, done = apply_edits(b.work, synonym_edits(doc, rng, 0.25 * opts.intensity, skip=set()))
        b.changes += [Change(e.stage, m.unmask(e.before), m.unmask(e.text)) for e in done]
    if opts.structure:
        doc = nlp(b.work)
        b.work, done = apply_edits(b.work, structure_edits(doc, rng, opts.intensity))
        b.changes += [Change(e.stage, m.unmask(e.before), m.unmask(e.text)) for e in done]


def _diff_changes(a: str, b: str, m: Masked) -> list[Change]:
    """Word-level diff turned into change-log entries (for LLM output)."""
    ta, tb = re.findall(r"\S+", a), re.findall(r"\S+", b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != "equal":
            out.append(Change("llm", m.unmask(" ".join(ta[i1:i2])), m.unmask(" ".join(tb[j1:j2]))))
    return out


def _llm_job(b: _Block, cfg) -> None:
    from .llm import LLMError, llm_rewrite

    if len(b.work.split()) < 6:  # too short to be worth a call
        return
    try:
        b.llm_text = llm_rewrite(b.work, cfg)
    except LLMError as e:
        b.llm_error = str(e)


def _rewrite_lines(lines: list[str], opts: Options, rng: random.Random) -> tuple[list[str], list[Change], list[str]]:
    blocks = [_prepare(line, opts, rng) for line in lines]
    warnings: list[str] = []
    use_llm = opts.llm is not None and (opts.synonyms or opts.structure)

    if use_llm:
        from .llm import LLMError

        try:
            cfg = opts.llm.resolve()
        except LLMError as e:
            warnings.append(f"LLM unavailable ({e}); used local rules for stages 4-5")
            use_llm = False
    if use_llm:
        todo = [b for b in blocks if b.masked is not None]
        with ThreadPoolExecutor(max_workers=max(1, opts.llm_workers)) as ex:
            list(ex.map(lambda b: _llm_job(b, cfg), todo))

    for i, b in enumerate(blocks):
        if b.masked is None:
            continue
        if use_llm and b.llm_text is not None:
            b.changes += _diff_changes(b.work, b.llm_text, b.masked)
            b.work = b.llm_text
        else:
            if use_llm and b.llm_error:
                warnings.append(f"paragraph {i + 1}: LLM output rejected ({b.llm_error}); used local rules")
            _rules_4_5(b, opts, rng)

    out, changes = [], []
    for line, b in zip(lines, blocks):
        if b.masked is None:
            out.append(line)
            continue
        out.append(b.lead + b.masked.unmask(_tidy(b.work)) + b.trail)
        changes += b.changes
    return out, changes, warnings


def rewrite(text: str, opts: Options | None = None) -> Result:
    opts = opts or Options()
    rng = random.Random(opts.seed)
    stats: dict = {}
    if opts.clean:
        text, stats = clean_text(text)
    pre = post = ""
    if opts.latex:
        pre, text, post = mask_latex_document(text)
    # Line by line so headings, list items and blank lines survive untouched.
    lines, changes, warnings = _rewrite_lines(text.split("\n"), opts, rng)
    return Result(pre + "\n".join(lines) + post, changes, stats, warnings)
