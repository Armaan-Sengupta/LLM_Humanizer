"""Optional helpers that slightly adjust wording."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, replace

from .mask import PH_RE

DEFAULT_CLI_MODEL = "haiku"  # alias resolved by the claude CLI
DEFAULT_API_MODEL = "claude-haiku-5-5"
API_URL = os.environ.get("REWRITER_API_URL", "https://api.anthropic.com/v1/messages")
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash-low"  # as listed by `agy models`
RATE_LIMIT_WAIT = 5  # seconds before retrying a 429 or 5xx response

# Words the model must not introduce (the phrase bank just removed most of them)
BANNED = ("delve, crucial, pivotal, leverage, robust, seamless, tapestry, realm, underscore, "
          "showcase, foster, intricate, meticulous, comprehensive, notably, moreover, furthermore, "
          "additionally, testament, landscape, navigate, embark, multifaceted, paramount, holistic")

SYSTEM_PROMPT = """You are a meticulous copy editor. You slightly adjust the wording of one passage at a time and output only that passage. You never add, remove, or change facts, claims, hedges, numbers, or the author's position."""

_WORD_RULES = """WORDING
- Adjust about {n_swaps} words in total (roughly one in every {swap_every} words). Spread the adjustments evenly across the passage; do not cluster them.
- Each replacement must be a synonym or a short paraphrase that means exactly the same thing in this sentence. Read the whole sentence before choosing.
- Prefer replacing verbs, adjectives and adverbs. Replace a noun only if it is an everyday word, not a term of art.
- Use plain, common words. Never pick a rarer, fancier or more formal word than the original.
- Keep grammatical agreement (tense, number, articles a/an) correct after each replacement.
- Never replace: technical or domain terms, names, units, defined abbreviations, words inside a fixed expression, or any word whose synonym would shift the meaning even slightly.
- Keep nouns that name people, groups, roles or organisations (for example residents, planners, departments) exactly as they are.
- Keep each verb's exact action. Never swap in a verb that is broader, narrower or different (hire is not pay; report is not tell).{extra}"""

# Per-provider additions to the word rules
_EXTRA_RULES = {
    "gemini": "\n- Be conservative: if you are not certain a replacement means exactly the same thing here, keep the original word.",
}

_LOCKED_AND_STYLE = """LOCKED TOKENS
- Tokens of the form ZXQ<number>QXZ stand for citations, numbers, quotations, formulas and protected terms. Copy each one exactly, the same number of times, and keep it attached to the same claim, word or position in its sentence. Never translate, explain, merge or drop them.

STYLE
- Keep the same language, spelling variety (US/UK), tense, person, register and level of formality.
- No markdown, no bullet points, no new line breaks, no commentary, no quotation marks around the output.
- Do not use any of these words: {banned}."""

USER_TEMPLATE = """Slightly adjust the wording of the passage inside <passage> tags by following every rule below.

""" + _WORD_RULES + """

SENTENCE STRUCTURE
- Adjust {n_restructure} of the {n_sents} sentences (roughly one in every {restructure_every}). Choose from: move a subordinate clause or prepositional phrase to the other end of the sentence; split one long sentence into two; join two short, closely related sentences; switch active/passive voice when it reads naturally.
- Aim for natural variation in sentence length. Leave the other sentences' structure as they are.
- Keep sentence order and paragraph flow. Each sentence must still make the same claim with the same strength.

""" + _LOCKED_AND_STYLE + """

Output the passage inside <out></out> tags and nothing else.

<passage>
{passage}
</passage>"""

WINDOW_TEMPLATE = """Slightly adjust the wording inside <edit> tags so that it keeps exactly the same meaning.

CONTEXT
- <before> and <after> hold the text around the passage. Read them to understand the passage. Do not adjust them or include them in your output.

""" + _WORD_RULES + """

SENTENCE STRUCTURE
- Only the words change. Keep every sentence, in the same order and with the same structure. Do not split, join or reorder sentences.

""" + _LOCKED_AND_STYLE + """

Output only the passage inside <out></out> tags and nothing else.

<before>
{before}
</before>

<edit>
{passage}
</edit>

<after>
{after}
</after>"""


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
    provider: str = "claude"  # claude | gemini
    gemini_bin: str = "agy"

    def resolve(self) -> "LLMConfig":
        if self.provider == "gemini":
            if not shutil.which(self.gemini_bin):
                raise LLMError("helper B is not available")
            return replace(self, backend="cli", model=self.model or DEFAULT_GEMINI_MODEL)
        b = self.backend
        if b == "auto":
            if os.environ.get("ANTHROPIC_API_KEY"):
                b = "api"
            elif shutil.which(self.claude_bin):
                b = "cli"
            else:
                raise LLMError("no helper is available")
        model = self.model or (DEFAULT_API_MODEL if b == "api" else DEFAULT_CLI_MODEL)
        return replace(self, backend=b, model=model)


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
        extra=_EXTRA_RULES.get(cfg.provider, ""),
    )


def build_window_prompt(passage: str, before: str, after: str, cfg: LLMConfig) -> str:
    n_words = len(re.findall(r"[A-Za-z]+", passage))
    return WINDOW_TEMPLATE.format(
        passage=passage,
        before=before or "(start of document)",
        after=after or "(end of document)",
        n_swaps=max(1, round(n_words / cfg.swap_every)),
        swap_every=cfg.swap_every,
        banned=BANNED,
        extra=_EXTRA_RULES.get(cfg.provider, ""),
    )


def _call_cli(prompt: str, cfg: LLMConfig) -> str:
    cmd = [cfg.claude_bin, "-p", "--model", cfg.model, "--output-format", "text",
           "--system-prompt", SYSTEM_PROMPT, "--tools", "", "--no-session-persistence"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=cfg.timeout)
    except FileNotFoundError as e:
        raise LLMError(f"`{cfg.claude_bin}` not found") from e
    except subprocess.TimeoutExpired as e:
        raise LLMError("helper A timed out") from e
    if p.returncode != 0:
        raise LLMError(f"helper A failed: {(p.stderr or p.stdout).strip()[:300]}")
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


def _call_gemini_cli(prompt: str, cfg: LLMConfig) -> str:
    exe = shutil.which(cfg.gemini_bin)
    if not exe:
        raise LLMError(f"`{cfg.gemini_bin}` not found")
    cmd = [exe, "--print", f"{SYSTEM_PROMPT}\n\n{prompt}", "--model", cfg.model, "--mode", "plan",
           "--output-format", "text", "--print-timeout", f"{cfg.timeout}s"]
    # An empty working directory keeps project files out of the model's context. It is permanent
    # because agy's background process keeps it open, which would break deleting a temporary one.
    cwd = os.path.join(tempfile.gettempdir(), "text-improver")
    os.makedirs(cwd, exist_ok=True)
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=cfg.timeout + 15, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise LLMError("helper B timed out") from e
    if p.returncode != 0:
        raise LLMError(f"helper B failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


_CALLS = {
    ("claude", "api"): _call_api,
    ("claude", "cli"): _call_cli,
    ("gemini", "cli"): _call_gemini_cli,
}


def _extract(raw: str) -> str:
    m = re.search(r"<out>\s*(.*?)\s*</out>", raw, re.S)
    if not m:
        raise LLMError("response missing the expected tags")
    return m.group(1).strip()


def validate(original: str, adjusted: str) -> None:
    """Raise LLMError if the rewrite broke a hard constraint."""
    if not adjusted:
        raise LLMError("empty improvement")
    if Counter(PH_RE.findall(original)) != Counter(PH_RE.findall(adjusted)):
        raise LLMError("locked placeholders were dropped, duplicated or altered")
    a = len(original.split())
    b = len(adjusted.split())
    if not (0.75 * a <= b <= 1.3 * a + 3):
        raise LLMError(f"length changed too much ({a} -> {b} words)")
    if "\n" in adjusted and "\n" not in original:
        raise LLMError("added line breaks")
    markdown = r"\*\*|^#|^- "
    if re.search(markdown, adjusted, re.M) and not re.search(markdown, original, re.M):
        raise LLMError("added markdown")


def _generate(prompt: str, original: str, cfg: LLMConfig, retries: int, must_change: bool = False) -> str:
    call = _CALLS[(cfg.provider, cfg.backend)]
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            out = _extract(call(prompt, cfg))
            validate(original, out)
            if must_change and out.split() == original.split():
                raise _Unchanged("returned the passage unchanged")
            return out
        except LLMError as e:
            last = e
            if "not found" in str(e) or "not set" in str(e):
                break
            if attempt < retries and re.search(r"API error (429|5\d\d)", str(e)):
                time.sleep(RATE_LIMIT_WAIT)
    if isinstance(last, _Unchanged):
        return original  # a valid reply that found nothing to change is not a failure
    raise LLMError(str(last))


class _Unchanged(LLMError):
    pass


def _resolved(cfg: LLMConfig) -> LLMConfig:
    return cfg if cfg.backend in ("cli", "api") and cfg.model else cfg.resolve()


def llm_rewrite(masked_text: str, cfg: LLMConfig, retries: int = 1) -> str:
    """Rewrite one masked paragraph. Raises LLMError if no valid rewrite was produced."""
    cfg = _resolved(cfg)
    return _generate(build_prompt(masked_text, cfg), masked_text, cfg, retries)


def llm_reword(masked_span: str, before: str, after: str, cfg: LLMConfig, retries: int = 1) -> str:
    """Swap words in one masked span, with the surrounding text as read-only context."""
    cfg = _resolved(cfg)
    return _generate(build_window_prompt(masked_span, before, after, cfg), masked_span, cfg, retries, must_change=True)
