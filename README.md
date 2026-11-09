# rewriter: a text rewriter (local rules + optional Claude Haiku)

It rewrites wordy or AI-sounding phrasing with local rules, then uses Claude Haiku for context-aware word swaps and sentence restructuring. Citations, numbers, quotes, code and LaTeX math are swapped for locked tokens before anything reaches the model, so they can't be changed. With `--offline`, everything runs locally with no network.

## Quick start

### Standalone binary (Linux x86_64, glibc ≥ 2.27)

```bash
chmod +x rewriter
./rewriter "Moreover, it is important to note that we utilize a robust methodology."
./rewriter essay.txt                  # writes essay.rewritten.txt
./rewriter essay.txt -o -             # print to stdout instead
./rewriter paper.docx                 # writes paper.rewritten.docx, formatting kept
./rewriter paper.tex                  # LaTeX-aware, preamble untouched
cat essay.txt | ./rewriter            # stdin -> stdout
./rewriter -t "notes.txt"             # treat the argument as text even if that file exists

./rewriter essay.txt -i 0.8 -s 42 -l  # intensity 0..1, fixed seed, log every change
./rewriter essay.txt -p "Kalman filter,LiDAR"   # protected terms
./rewriter essay.txt --no-synonyms    # phrase bank + restructuring only (safest)

./rewriter check essay.txt            # local diagnostics (JSON)
./rewriter compare essay.txt essay.rewritten.txt
```

If you pass text or pipe it in, the output goes to stdout. If you pass a file, the output goes to `<name>.rewritten.<ext>`. The change count (and with `-l`, each change) goes to stderr, so pipes stay clean.

### From source

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python cli.py "some text"
streamlit run app.py      # optional web UI with a colour diff and a .docx tab
pytest tests              # 21 tests (LLM tests use a mock `claude`)
./build_linux.sh          # rebuild dist/rewriter
```

## LLM mode (stages 4–5 via Claude Haiku)

By default (`--llm auto`) the tool uses:
1. the **Anthropic API** if `ANTHROPIC_API_KEY` is set (model `claude-haiku-5-5`, standard library HTTP only), otherwise
2. the **`claude` CLI** (Claude Code) if it's installed and logged in (`claude -p --model haiku --tools ""`), otherwise
3. the local rules, with a warning.

```bash
./rewriter essay.txt                          # auto
./rewriter essay.txt --llm cli                # force the claude CLI
./rewriter essay.txt --llm api --model claude-haiku-5-5
./rewriter essay.txt --swap-every 10 --restructure-every 3   # more aggressive
./rewriter essay.txt --offline                # never send text anywhere
```

**What the model is asked to do** (`rewriter/llm.py`): replace about one word in every `N` (default 12, ≈8%) with a synonym that keeps the exact meaning *in that sentence*. Spread the swaps out, prefer verbs, adjectives and adverbs, never use a fancier word, and leave terms of art alone. Restructure about one sentence in four, without changing any claim. Copy every locked token exactly, and avoid a list of known AI-marker words. Each paragraph is a separate call; up to `--workers` (default 4) run in parallel.

**Why 1 in 12:** it's a starting point, not a measured optimum. Below about 1 in 20 the text barely changes. Above about 1 in 8, the swaps start pushing into words where synonyms shift meaning. Tune it with `--swap-every`, and read the `-l` log.

**Safety net:** every response is checked. All locked tokens must come back exactly once, word count must stay within −25% to +30%, and no line breaks or markdown can be added. A response that fails is retried once, and if it fails again that paragraph uses the local rules. Warnings go to stderr.

**Cost:** at $0.10 per million input tokens and $0.50 per million output tokens (Haiku 5.5, prompts under 100K tokens), a 1,000-word essay is roughly 7K tokens in and 1.5K out, so about **$0.0015**.

## How it works

Each line or paragraph goes through five stages:

| Stage | What it does | Source |
|---|---|---|
| 1. clean | Strips zero-width and bidi characters and fixes Cyrillic/Greek look-alike letters inside Latin words | `clean.py` |
| 2. protect | Swaps citations `(Smith et al., 2021)` and `[3]`, numbers and units, acronyms, quotes, URLs, code, LaTeX math, `\cite{}`/`\ref{}` and your terms for placeholders | `mask.py` |
| 3. phrase bank | About 150 curated rewrites of AI-marker words and wordy phrases. Matches on spaCy lemmas and re-inflects verbs, fixes a/an and capitalisation | `data/phrasebank.json` |
| 4–5 (LLM) | Claude Haiku: context-aware swaps and restructuring on the masked text (see above) | `llm.py` |
| 4. synonyms (offline) | Occasional verb/adjective/adverb swaps from PPDB ∩ WordNet (top-3 senses, frequency-filtered). Nouns and very common words are skipped | `data/synonyms.json` |
| 5. structure (offline) | "X when Y." ↔ "When Y, X.", splits "A; B." and long "A, and B." sentences | `structure.py` |

`intensity` scales how often stages 3–5 fire. The same `seed` always gives the same output.

**Quality note:** stage 4 is the weak link. Automatic synonym data is noisy even after filtering. When you spot a bad swap with `-l`, add it to `rewriter/data/synonym_blocklist.txt` as `word -> bad_replacement`. The phrase bank is plain JSON, so adding your own entries is the best way to improve output.

### The checker

`check` and `compare` report:
- **ZipPy**: Thinkst's open-source compression-based detector (LZMA ratio against an AI-text sample). The confidence is a small number; a lower AI confidence or a flip to Human is the direction you want.
- **markers_per_1k**: AI-marker words and phrases per 1,000 words.
- **burstiness**: the stdev/mean of sentence length. Human writing usually varies more.
- **mattr**: lexical diversity.

These are rough local signals. They don't predict Turnitin, GPTZero or any commercial detector, which use trained classifiers. Turnitin has also had specific "AI bypasser" detection since Aug 2025.

## Reused resources and licenses

| Resource | Used for | License |
|---|---|---|
| [retext-simplify](https://github.com/retextjs/retext-simplify) (Titus Wormer) | Plain-English entries in the phrase bank (`src: retext`) | MIT |
| [PPDB 1.0](http://paraphrase.org) lexical subset (via NLTK `mwa_ppdb`) | Synonym candidates | CC-BY 3.0 |
| WordNet (via NLTK, build time only) | Sense filtering of PPDB pairs | WordNet license |
| [Kobak et al., excess vocabulary](https://github.com/berenslab/llm-excess-vocab) | 407 "style" AI-marker words for the checker | MIT |
| [Clarkson AI phrases](https://github.com/mpclarkson/stereotypical-ai-words-and-phrases) | Marker phrases for the checker | MIT |
| [ZipPy](https://github.com/thinkst/zippy) (Thinkst) | `zippy_lite.py` port and AI-text sample | MIT |
| spaCy `en_core_web_sm`, lemminflect, wordfreq, python-docx | Parsing, inflection, frequencies, Word I/O | MIT / Apache-2.0 / Apache-2.0+CC-BY-SA data / MIT |

The source files and license texts are in `third_party/`. `scripts/build_data.py` regenerates `rewriter/data/` from them (the PPDB ∩ WordNet step needs `nltk` with the `wordnet` corpus).

Considered but not bundled: [gptslop](https://github.com/AlpinDale/gptslop) (AGPL-3.0, which would force this project to AGPL), and the slop-forensics and antislop lists (MIT, but tuned for fiction, e.g. "voice barely above a whisper", not academic prose). The Moby thesaurus is too loose (related terms, not synonyms).

## Layout

```
cli.py              command-line entry (also the binary's entry point)
app.py              Streamlit UI
rewriter/
  pipeline.py       orchestrates the stages, Options, Result
  clean.py mask.py lexical.py structure.py edits.py
  docx_io.py        in-place .docx rewriting + metadata scrub
  llm.py            Claude Haiku backend (CLI or API), prompt, validation
  checker.py zippy_lite.py
  data/             phrasebank.json, synonyms.json, blocklist, marker lists
scripts/build_data.py
rewriter.spec build_linux.sh
tests/
```

## Next steps that would improve quality most

1. **Run it on your real writing with `-l`** and tune `--swap-every` and `--restructure-every`. The prompt in `llm.py` is plain text and easy to edit.
2. **Grow the phrase bank** from your own writing domain. It runs before the LLM and costs nothing.
3. **Offline mode quality:** score synonym candidates with a small masked language model (e.g. DistilRoBERTa) instead of trusting PPDB/WordNet.
