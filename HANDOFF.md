# Hand-off: LLM_Humanizer (rewriter)

Paste the prompt below into a new local Claude session opened in this folder.

---

You're picking up an existing Python project in this folder: a CLI text rewriter. Read README.md first, then rewriter/pipeline.py and rewriter/llm.py.

**What exists (commit ca12687, branch main, 21 passing tests):**
- Pipeline per paragraph: 1) clean invisible/homoglyph chars (clean.py) -> 2) mask citations, numbers, quotes, LaTeX, code, protected terms as ZXQnQXZ placeholders (mask.py) -> 3) curated phrase bank, spaCy-lemma matched with re-inflection (lexical.py, data/phrasebank.json) -> 4-5) Claude Haiku rewrite of the masked text (llm.py), or offline fallback: PPDB∩WordNet synonym swaps + clause moves/splits (lexical.py, structure.py) -> unmask.
- llm.py backends: Anthropic API via stdlib urllib if ANTHROPIC_API_KEY is set (model claude-haiku-5-5), else the `claude` CLI (`claude -p --model haiku --tools "" --system-prompt ...`, prompt on stdin), else local rules with a warning. Output must be inside <rewritten> tags; validate() checks placeholders are preserved exactly, word count within -25%/+30%, and no added newlines or markdown. One retry, then fall back to rules for that paragraph. Paragraphs run in parallel (--workers, default 4).
- Prompt knobs: --swap-every N (default 12 words per swap), --restructure-every N (default 4 sentences).
- CLI (cli.py): `python cli.py "text"`, `python cli.py file.txt|.md|.tex|.docx`, stdin, `-o -`, `-l` change log, `--offline`, `check`, `compare`. .docx is edited in place, keeping formatting (docx_io.py).
- checker.py: ZipPy LZMA port (zippy_lite.py), AI-marker density, burstiness, MATTR.
- app.py: Streamlit UI. rewriter.spec + build_linux.sh: PyInstaller Linux x86_64 binary (dist/ is gitignored; one prebuilt binary is in dist/).
- Setup: `pip install -r requirements.txt && python -m spacy download en_core_web_sm`, then `pytest tests`. LLM tests use a mock `claude` script.

**Not yet verified:** real Haiku output quality. The previous session had no Claude login, so the LLM path was only tested against mocks. First task: run `python cli.py <sample> -l` with a real backend, review the changes, and tune the prompt in llm.py.

**Next feature (agreed): windowed rewriting with context.**
- Split the document into chunks of about 120-200 words, on sentence boundaries only.
- For each chunk, send the previous and next chunk as read-only context, with the editable span marked by tags. Validate that only the span came back changed.
- Keep a per-chunk swap target ("about y replacements in this x-word span") and pass the list of words already swapped, so the model avoids reusing them.
- Add an option to leave the first chunk unchanged (--skip-first).
- Make the model pluggable per chunk (e.g. Haiku/Sonnet), with Haiku as the default.

**Also pending:** create a GitHub repo and push (`gh repo create LLM_Humanizer --private --source . --push`).

Notes: Windows machine; core.fileMode=false is set for this repo, and .gitattributes forces LF. The binary is Linux-only (use WSL), so on Windows run from source.
