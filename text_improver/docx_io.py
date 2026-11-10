"""Adjust a .docx in place, keeping formatting, images and tables."""
from __future__ import annotations

import io
from typing import Iterator

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from .pipeline import Change, Options, Result, rewrite, rewrite_many

TEXT_ONLY_CHILDREN = {qn("w:rPr"), qn("w:t"), qn("w:tab"), qn("w:br")}


def _iter_paragraphs(doc) -> Iterator:
    def from_container(c):
        for p in c.paragraphs:
            yield p
        for t in getattr(c, "tables", []):
            for row in t.rows:
                for cell in row.cells:
                    yield from from_container(cell)

    yield from from_container(doc)
    for section in doc.sections:
        for part in (section.header, section.footer, section.first_page_header,
                     section.first_page_footer, section.even_page_header, section.even_page_footer):
            if part is not None and not part.is_linked_to_previous:
                yield from from_container(part)


def _rpr_key(run) -> bytes:
    rpr = run._r.find(qn("w:rPr"))
    return b"" if rpr is None else etree.tostring(rpr)


def _is_plain(run) -> bool:
    return all(child.tag in TEXT_ONLY_CHILDREN for child in run._r)


def _chunks(paragraph) -> list[list]:
    chunks, cur, key = [], [], None
    for run in paragraph.runs:
        if not _is_plain(run):
            if cur:
                chunks.append(cur)
            cur, key = [], None
            continue
        k = _rpr_key(run)
        if cur and k != key:
            chunks.append(cur)
            cur = []
        cur.append(run)
        key = k
    if cur:
        chunks.append(cur)
    return chunks


def _set_run_text(run, text: str) -> None:
    # run.text setter handles \t and \n -> <w:tab/>, <w:br/>
    run.text = text


def scrub_metadata(doc) -> None:
    cp = doc.core_properties
    for attr in ("author", "last_modified_by", "comments", "keywords", "subject",
                 "category", "content_status", "identifier", "version"):
        try:
            setattr(cp, attr, "")
        except Exception:
            pass


def rewrite_docx(data: bytes, opts: Options, *, scrub: bool = True) -> tuple[bytes, list[Change], list[str]]:
    """Return (document bytes, changes, failures). Failures are empty when every model call succeeded."""
    doc = Document(io.BytesIO(data))
    all_changes: list[Change] = []
    import dataclasses
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for p in _iter_paragraphs(doc):
        for chunk in _chunks(p):
            original = "".join(r.text for r in chunk)
            if len(original.split()) >= 3:
                jobs.append((chunk, original))

    def work(item):
        n, (chunk, original) = item
        # vary the seed per chunk so choices aren't identical everywhere
        seed = None if opts.seed is None else opts.seed * 100003 + n
        return rewrite(original, dataclasses.replace(opts, seed=seed, llm_workers=1))

    failures: list[str] = []
    if opts.rotation:
        # One document-wide word count, so the windows carry across paragraphs.
        texts, rotated, failures = rewrite_many([original for _, original in jobs], opts)
        results = [Result(text, [], {}) for text in texts]
        all_changes += rotated
    else:
        workers = opts.llm_workers if opts.llm is not None else 1
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            results = list(ex.map(work, enumerate(jobs, 1)))

    for (chunk, original), res in zip(jobs, results):
        failures += res.failures
        if res.text == original:
            continue
        _set_run_text(chunk[0], res.text)
        for r in chunk[1:]:
            _set_run_text(r, "")
        all_changes += res.changes
    if scrub:
        scrub_metadata(doc)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), all_changes, sorted(set(failures))


def docx_text(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in _iter_paragraphs(doc))
