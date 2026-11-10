"""End-to-end rewrite pipeline.

Local:  1 clean -> 2 mask -> 3 phrase bank
Then either
  LLM:  4+5 Claude Haiku (context-aware synonyms + restructuring) on the masked text
or
  rules: 4 PPDB/WordNet synonyms -> 5 clause moves/splits
followed by unmask.

With Options.rotation set, only the LLM stage runs: each window of the document is either left
alone or reworded by its assigned model.

Every LLM reply passes through drift.revert_risky, which undoes swaps likely to change meaning.
Anything that stops a model from rewriting its part (a missing CLI, a reply that fails
validation) is recorded in Result.failures; the CLI turns that into exit code 1.
"""
from __future__ import annotations

import difflib
import random
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import resources as R
from .clean import clean_text
from .drift import revert_risky
from .edits import apply_edits
from .lexical import phrase_edits, synonym_edits
from .mask import Masked, mask, mask_latex_document, mask_multiline_latex
from .structure import structure_edits
from .windows import Phase, context, plan


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
    rotation: list[Phase] | None = None  # repeating word-count windows; replaces stages 3-5


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
    failures: list[str] = field(default_factory=list)  # why a model couldn't rewrite its part

    @property
    def ok(self) -> bool:
        return not self.failures


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


def _prepare(text: str, opts: Options, rng: random.Random, originals: list[str]) -> _Block:
    """Stages 2-3 (mask + phrase bank). Leaves text in masked form."""
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :] if text.strip() else ""
    b = _Block(lead, trail)
    core = text.strip()
    if not core:
        return b
    m = mask(core, latex=opts.latex, protected_terms=opts.protected_terms,
             protect_quotes=opts.protect_quotes, originals=originals)
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


def _diff_changes(a: str, b: str, m: Masked, stage: str = "llm") -> list[Change]:
    """Word-level diff turned into change-log entries (for LLM output)."""
    ta, tb = re.findall(r"\S+", a), re.findall(r"\S+", b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != "equal":
            out.append(Change(stage, m.unmask(" ".join(ta[i1:i2])), m.unmask(" ".join(tb[j1:j2]))))
    return out


def _llm_job(b: _Block, cfg) -> None:
    from .llm import LLMError, llm_rewrite

    if len(b.work.split()) < 6:  # too short to be worth a call
        return
    try:
        b.llm_text, _ = revert_risky(b.work, llm_rewrite(b.work, cfg), "haiku")
    except LLMError as e:
        b.llm_error = str(e)


def _rewrite_lines(lines: list[str], opts: Options, rng: random.Random,
                   originals: list[str]) -> tuple[list[str], list[Change], list[str]]:
    blocks = [_prepare(line, opts, rng, originals) for line in lines]
    failures: list[str] = []
    use_llm = opts.llm is not None and (opts.synonyms or opts.structure)

    if use_llm:
        from .llm import LLMError

        try:
            cfg = opts.llm.resolve()
        except LLMError as e:
            failures.append(f"haiku unavailable: {e}")
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
                failures.append(f"haiku: paragraph {i + 1}: {b.llm_error}")
            _rules_4_5(b, opts, rng)

    out, changes = [], []
    for line, b in zip(lines, blocks):
        if b.masked is None:
            out.append(line)
            continue
        out.append(b.lead + b.masked.unmask(_tidy(b.work)) + b.trail)
        changes += b.changes
    return out, changes, failures


def _rewrite_rotation(lines: list[str], opts: Options,
                      originals: list[str] | None = None) -> tuple[list[str], list[Change], list[str]]:
    from .llm import LLMError, llm_reword

    shared = Masked("", [] if originals is None else originals)
    masked = [mask(line, latex=opts.latex, protected_terms=opts.protected_terms,
                   protect_quotes=opts.protect_quotes, originals=shared.originals).text for line in lines]
    failures: list[str] = []
    resolved: dict[str, object] = {}
    for phase in opts.rotation:
        if phase.cfg is None or phase.words == 0:
            continue
        try:
            resolved[phase.label] = phase.cfg.resolve()
        except LLMError as e:
            failures.append(f"{phase.label} unavailable: {e}")

    spans = plan(masked, opts.rotation)
    jobs = [s for s in spans if s.phase.label in resolved and len(masked[s.line][s.start:s.end].split()) >= 6]

    def work(span):
        before, after = context(masked, span)
        old = masked[span.line][span.start:span.end]
        try:
            new = llm_reword(old, before, after, resolved[span.phase.label])
        except LLMError as e:
            return None, str(e)
        return revert_risky(old, new, span.phase.label)[0], None

    with ThreadPoolExecutor(max_workers=max(1, opts.llm_workers)) as ex:
        results = list(ex.map(work, jobs))

    failures += [f"{s.phase.label}: paragraph {s.line + 1}: {error}" for s, (_, error) in zip(jobs, results) if error]
    changes: list[Change] = []
    out = list(masked)
    for span, (new, _) in reversed(list(zip(jobs, results))):  # back to front keeps offsets valid
        if new is None:
            continue
        line = out[span.line]
        changes = _diff_changes(line[span.start:span.end], new, shared, span.phase.label) + changes
        out[span.line] = line[:span.start] + new + line[span.end:]
    return [shared.unmask(line) for line in out], changes, failures


def rewrite_many(texts: list[str], opts: Options) -> tuple[list[str], list[Change], list[str]]:
    """Rotate windows across separate texts as one document (used for .docx paragraphs)."""
    cleaned = [clean_text(t)[0] for t in texts] if opts.clean else texts
    return _rewrite_rotation(cleaned, opts)


def rewrite(text: str, opts: Options | None = None) -> Result:
    opts = opts or Options()
    rng = random.Random(opts.seed)
    stats: dict = {}
    if opts.clean:
        text, stats = clean_text(text)
    pre = post = ""
    originals: list[str] = []  # one numbering for the whole document
    if opts.latex:
        pre, text, post = mask_latex_document(text)
        # Equations and other environments that span lines become one locked token first,
        # because everything after this point works line by line.
        text = mask_multiline_latex(text, originals)
    # Line by line so headings, list items and blank lines survive untouched.
    if opts.rotation:
        lines, changes, failures = _rewrite_rotation(text.split("\n"), opts, originals)
    else:
        lines, changes, failures = _rewrite_lines(text.split("\n"), opts, rng, originals)
    return Result(pre + "\n".join(lines) + post, changes, stats, failures)
