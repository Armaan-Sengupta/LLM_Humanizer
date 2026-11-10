"""Keep protected spans unchanged while the rest of the text is adjusted."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PH_RE = re.compile(r"ZXQ(\d+)QXZ")


def is_placeholder(s: str) -> bool:
    return "ZXQ" in s


# Order matters: bigger structures first.
LATEX_PATTERNS = [
    r"(?s)\\begin\{(equation|align|gather|multline|eqnarray|verbatim|lstlisting|minted|tabular|figure|table|tikzpicture|algorithm|algorithmic)\*?\}.*?\\end\{\1\*?\}",
    r"(?s)\$\$.+?\$\$",
    r"(?s)\\\[.+?\\\]",
    r"(?s)\\\(.+?\\\)",
    r"(?<!\\)\$[^$\n]+?(?<!\\)\$",
    r"\\(?:cite[a-z]*|ref|eqref|autoref|cref|Cref|label|url|href|input|include|includegraphics|bibliography|bibliographystyle|usepackage|documentclass)\*?(?:\[[^\]]*\])*\{[^}]*\}(?:\{[^}]*\})?",
    r"\\begin\{[^}]*\}(?:\[[^\]]*\])?",
    r"\\end\{[^}]*\}",
    r"\\[A-Za-z@]+\*?",  # any other command name (args stay editable)
    r"(?m)(?<!\\)%.*$",  # comments
]

GENERAL_PATTERNS = [
    r"```.*?```",  # fenced code
    r"`[^`\n]+`",  # inline code
    r"https?://\S+|www\.\S+",
    r"[\w.+-]+@[\w-]+\.[\w.-]+",
    r"\[\d+(?:\s*[-–,]\s*\d+)*\]",  # [1], [2-4], [1, 5]
    # (Smith, 2020) / (Smith et al., 2020; Jones & Lee, 2019a) / (Smith 2020, p. 4)
    r"\((?:[A-Z][A-Za-z'’\-]+(?:\s+(?:et\s+al\.|and|&)\s*(?:[A-Z][A-Za-z'’\-]+)?)?,?\s+\d{4}[a-z]?(?:,\s*pp?\.\s*[\d\-–]+)?;?\s*)+\)",
    r"[\"“][^\"”\n]{1,400}[\"”]",  # direct quotations
    r"\b\d+(?:[.,]\d+)*\s?(?:%|percent|mg|kg|g|ml|mL|m|km|cm|mm|s|ms|Hz|kHz|MHz|GHz|K|°C|°F)?\b",  # numbers (+units)
    r"\b[A-Z]{2,}[A-Za-z0-9\-]*\b",  # acronyms / codes (DNA, GPT-4, BERT)
    r"\b\w*\d\w*\b",  # tokens with digits
]


@dataclass
class Masked:
    text: str
    originals: list[str] = field(default_factory=list)

    def unmask(self, text: str) -> str:
        # Placeholders can nest (a quote containing an acronym), so loop.
        for _ in range(4):
            new = PH_RE.sub(lambda m: self.originals[int(m.group(1))], text)
            if new == text:
                break
            text = new
        return text


def mask(text: str, *, latex: bool = False, protected_terms: list[str] | None = None,
         protect_quotes: bool = True, originals: list[str] | None = None) -> Masked:
    """Mask protected spans. Pass a shared ``originals`` list to number tokens across calls."""
    m = Masked(text, [] if originals is None else originals)
    patterns = (LATEX_PATTERNS if latex else []) + GENERAL_PATTERNS
    if not protect_quotes:
        patterns = [p for p in patterns if not p.startswith('[\\"“]')]
    terms = sorted({t.strip() for t in (protected_terms or []) if t.strip()}, key=len, reverse=True)
    if terms:
        patterns = [r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b"] + patterns

    def repl(match: re.Match) -> str:
        if PH_RE.fullmatch(match.group(0)):
            return match.group(0)  # already a placeholder
        m.originals.append(match.group(0))
        return f"ZXQ{len(m.originals) - 1}QXZ"

    out = text
    for pat in patterns:
        flags = re.DOTALL if pat.startswith("```") else 0
        out = re.sub(pat, repl, out, flags=flags)
    m.text = out
    return m


_MULTILINE_LATEX = LATEX_PATTERNS[:4]  # environments, $$...$$, \[...\], \(...\)


def mask_multiline_latex(text: str, originals: list[str]) -> str:
    """Lock LaTeX math and environments that span several lines, before text is split into lines."""
    def repl(match: re.Match) -> str:
        if "\n" not in match.group(0):
            return match.group(0)  # single-line spans are masked later, line by line
        originals.append(match.group(0))
        return f"ZXQ{len(originals) - 1}QXZ"

    for pat in _MULTILINE_LATEX:
        text = re.sub(pat, repl, text)
    return text


def mask_latex_document(text: str) -> tuple[str, str, str]:
    """Split a .tex file into (preamble, body, tail) so only the body is adjusted."""
    b = text.find("\\begin{document}")
    e = text.rfind("\\end{document}")
    if b == -1:
        return "", text, ""
    b += len("\\begin{document}")
    e = e if e != -1 else len(text)
    return text[:b], text[b:e], text[e:]
