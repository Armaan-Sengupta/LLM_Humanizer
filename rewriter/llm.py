"""Stages 4-5 (context-aware word substitution + sentence restructuring) via Claude Haiku.

Two backends:
  * "cli": the `claude` command (Claude Code) in print mode, using your existing login
  * "api": the Anthropic Messages API directly, using ANTHROPIC_API_KEY (stdlib only)

The text sent is already masked: citations, numbers, quotes, formulas and protected terms
are opaque placeholders (ZXQ0QXZ), so the model never sees or edits them. Every response is
validated; if it fails twice the caller falls back to the local rule-based stages.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass

from .mask import PH_RE

DEFAULT_CLI_MODEL = "haiku"  # alias resolved by the claude CLI
DEFAULT_API_MODEL = "claude-haiku-5-5"
API_URL = os.environ.get("REWRITER_API_URL", "https://api.anthropic.com/v1/messages")

# Words the model must not introduce (the phrase bank just removed most of them)
BANNED = ("delve, crucial, pivotal, leverage, robust, seamless, tapestry, realm, underscore, "
          "showcase, foster, intricate, meticulous, comprehensive, notably, moreover, furthermore, "
          "additionally, testament, landscape, navigate, embark, multifaceted, paramount, holistic")

SYSTEM_PROMPT = """You are a meticulous copy editor. You rewrite one passage at a time and output only the rewritten passage. You never add, remove, or change facts, claims, hedges, numbers, or the author's position."""

USER_TEMPLATE = """Rewrite the passage inside <passage> tags by following every rule below.

WORD SUBSTITUTION
- Replace about {n_swaps} words in total (roughly one in every {swap_every} words). Spread the replacements evenly across the passage; do not cluster them.
- Each replacement must be a synonym or a short paraphrase that means exactly the same thing in this sentence. Read the whole sentence before choosing.
- Prefer replacing verbs, adjectives and adverbs. Replace a noun only if it is an everyday word, not a term of art.
- Use plain, common words. Never pick a rarer, fancier or more formal word than the original.
- Keep grammatical agreement (tense, number, articles a/an) correct after each replacement.
- Never replace: technical or domain terms, names, units, defined abbreviations, words inside a fixed expression, or any word whose synonym would shift the meaning even slightly.

SENTENCE STRUCTURE
- Restructure {n_restructure} of the {n_sents} sentences (roughly one in every {restructure_every}). Choose from: move a subordinate clause or prepositional phrase to the other end of the sentence; split one long sentence into two; join two short, closely related sentences; switch active/passive voice when it reads naturally.
- Aim for natural variation in sentence length. Leave the other sentences' structure as they are.
- Keep sentence order and paragraph flow. Each sentence must still make the same claim with the same strength.

LOCKED TOKENS
- Tokens of the form ZXQ<number>QXZ stand for citations, numbers, quotations, formulas and protected terms. Copy each one exactly, the same number of times, and keep it attached to the same claim, word or position in its sentence. Never translate, explain, merge or drop them.

STYLE
- Keep the same language, spelling variety (US/UK), tense, person, register and level of formality.
- No markdown, no bullet points, no new line breaks, no commentary, no quotation marks around the output.
- Do not use any of these words: {banned}.

Output the rewritten passage inside <rewritten></rewritten> tags and nothing else.

<passage>
{passage}
</passage>"""


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    backend: str = "auto"  # auto | cli | api
    model: str | None = None
    swap_every: int = 12  # one replacement per N words
    restructure_every: int = 4  # one restructured sentence per N sentences
    timeout: int = 120
    claude_bin: str = "claude"

    def resolve(self) -> "LLMConfig":
        b = self.backend
        if b == "auto":
            if os.environ.get("ANTHROPIC_API_KEY"):
                b = "api"
            elif shutil.which(self.claude_bin):
                b = "cli"
            else:
                raise LLMError("no LLM backend: set ANTHROPIC_API_KEY or install/log in to the `claude` CLI")
        model = self.model or (DEFAULT_API_MODEL if b == "api" else DEFAULT_CLI_MODEL)
        return LLMConfig(b, model, self.swap_every, self.restructure_every, self.timeout, self.claude_bin)


def _restructure_phrase(n_sents: int, every: int) -> str:
    n = round(n_sents / every)
    if n_sents < 2:
        return "none"
    if n == 0:
        return "at most one"
    return f"about {n}"


def build_prompt(passage: str, cfg: LLMConfig) -> str:
    n_words = len(re.findall(r"[A-Za-z]+", passage))
    n_sents = max(1, len(re.findall(r"[.!?](?:\s|$)", passage)))
    return USER_TEMPLATE.format(
        passage=passage,
        n_swaps=max(1, round(n_words / cfg.swap_every)),
        swap_every=cfg.swap_every,
        n_sents=n_sents,
        n_restructure=_restructure_phrase(n_sents, cfg.restructure_every),
        restructure_every=cfg.restructure_every,
        banned=BANNED,
    )


def _call_cli(prompt: str, cfg: LLMConfig) -> str:
    cmd = [cfg.claude_bin, "-p", "--model", cfg.model, "--output-format", "text",
           "--system-prompt", SYSTEM_PROMPT, "--tools", "", "--no-session-persistence"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=cfg.timeout)
    except FileNotFoundError as e:
        raise LLMError(f"`{cfg.claude_bin}` not found") from e
    except subprocess.TimeoutExpired as e:
        raise LLMError("claude CLI timed out") from e
    if p.returncode != 0:
        raise LLMError(f"claude CLI failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _call_api(prompt: str, cfg: LLMConfig) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    body = json.dumps({
        "model": cfg.model,
        "max_tokens": 4096,
        "temperature": 0.7,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise LLMError(f"API error {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"API unreachable: {e.reason}") from e
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _extract(raw: str) -> str:
    m = re.search(r"<rewritten>\s*(.*?)\s*</rewritten>", raw, re.S)
    if not m:
        raise LLMError("response missing <rewritten> tags")
    return m.group(1).strip()


def validate(original: str, rewritten: str) -> None:
    """Raise LLMError if the rewrite broke a hard constraint."""
    if not rewritten:
        raise LLMError("empty rewrite")
    if Counter(PH_RE.findall(original)) != Counter(PH_RE.findall(rewritten)):
        raise LLMError("locked placeholders were dropped, duplicated or altered")
    a = len(original.split())
    b = len(rewritten.split())
    if not (0.75 * a <= b <= 1.3 * a + 3):
        raise LLMError(f"length changed too much ({a} -> {b} words)")
    if "\n" in rewritten and "\n" not in original:
        raise LLMError("added line breaks")
    if re.search(r"\*\*|^#|^- ", rewritten, re.M):
        raise LLMError("added markdown")


def llm_rewrite(masked_text: str, cfg: LLMConfig, retries: int = 1) -> str:
    """Rewrite one masked paragraph. Raises LLMError if no valid rewrite was produced."""
    cfg = cfg if cfg.backend in ("cli", "api") and cfg.model else cfg.resolve()
    prompt = build_prompt(masked_text, cfg)
    call = _call_api if cfg.backend == "api" else _call_cli
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            out = _extract(call(prompt, cfg))
            validate(masked_text, out)
            return out
        except LLMError as e:
            last = e
            if "not found" in str(e) or "not set" in str(e):
                break
    raise LLMError(str(last))
