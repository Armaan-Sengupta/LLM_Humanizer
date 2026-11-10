# rewriter: a text rewriter (local rules + optional Claude Haiku)

It rewrites wordy or AI-sounding phrasing with local rules, then uses Claude Haiku for context-aware word swaps and sentence restructuring. Citations, numbers, quotes, code and LaTeX math are swapped for locked tokens before anything reaches the model, so they can't be changed. With `--offline`, everything runs locally with no network.

## Quick start

### Standalone binary (Linux x86_64, glibc ≥ 2.27)

```bash
chmod +x rewriter
./rewriter --setup                    # first run: install to ~/.local/bin and check both sign-ins
./rewriter "Moreover, it is important to note that we utilize a robust methodology."
./rewriter                            # paste text, then Ctrl+D on a new line
./rewriter essay.txt                  # writes essay.improved.txt
./rewriter essay.txt -o -             # print to stdout instead
./rewriter paper.docx                 # writes paper.improved.docx, formatting kept
./rewriter paper.tex                  # LaTeX-aware, preamble untouched
cat essay.txt | ./rewriter            # stdin -> stdout
./rewriter -t "notes.txt"             # treat the argument as text even if that file exists

./rewriter essay.txt -l               # log each improvement
./rewriter essay.txt -p "Kalman filter,LiDAR"   # protected terms
./rewriter essay.txt --offline -i 0.8 -s 42     # local rules: intensity 0..1, fixed seed

./rewriter check essay.txt            # local diagnostics (JSON)
./rewriter compare essay.txt essay.improved.txt
```

`improve` is the default command, so `./rewriter improve essay.txt` and `./rewriter essay.txt` do the same thing. If you pass text, paste it or pipe it in, the output goes to stdout. If you pass a file, the output goes to `<name>.improved.<ext>`. The status line (and with `-l`, each change) goes to stderr, so pipes stay clean.

To paste instead of making a file, run it with no input: paste, then press Ctrl+D on a new line (Ctrl+Z then Enter on Windows). On Windows you can also improve whatever is on the clipboard and put the result back:

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()   # once per window, keeps quotes and dashes
Get-Clipboard -Raw | python cli.py | Set-Clipboard
```

### From source

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python cli.py "some text"
streamlit run app.py               # optional web UI with a colour diff and a .docx tab
pytest tests                       # model calls are mocked, so no sign-in is needed
python cli.py --setup              # check that claude and agy are signed in
```

### Building the binary

The binary has to be built on Linux, for example on the server itself or in WSL:

```bash
./build_linux.sh                   # writes dist/rewriter (about 130 MB)
```

The first build takes a few minutes and creates `.buildenv/`. Building from `/mnt/c` in WSL is slow, so copy the repo into the Linux filesystem first. A binary built on an older Linux than the server is always safe, and Ubuntu 24.04 builds run on Debian 13.

## LLM mode with `--pattern off` (stages 4–5 via Claude Haiku)

This per-paragraph mode runs with `--pattern off`. Without that flag, see [Window rotation](#window-rotation-default). Here, `--llm auto` uses:
1. the **Anthropic API** if `ANTHROPIC_API_KEY` is set (model `claude-haiku-5-5`, standard library HTTP only), otherwise
2. the **`claude` CLI** (Claude Code) if it's installed and logged in (`claude -p --model haiku --tools ""`), otherwise
3. neither, which is a failure (exit code 1).

```bash
./rewriter essay.txt --pattern off            # auto
./rewriter essay.txt --pattern off --llm cli  # force the claude CLI
./rewriter essay.txt --pattern off --llm api --model claude-haiku-5-5
./rewriter essay.txt --pattern off --swap-every 10 --restructure-every 3   # more aggressive
./rewriter essay.txt --offline                # never send text anywhere
```

**What the model is asked to do** (`rewriter/llm.py`): replace about one word in every `N` (default 12, ≈8%) with a synonym that keeps the exact meaning *in that sentence*. Spread the swaps out, prefer verbs, adjectives and adverbs, never use a fancier word, and leave terms of art alone. Restructure about one sentence in four, without changing any claim. Copy every locked token exactly, and avoid a list of known AI-marker words. Each paragraph is a separate call; up to `--workers` (default 4) run in parallel.

**Why 1 in 12:** it's a starting point, not a measured optimum. Below about 1 in 20 the text barely changes. Above about 1 in 8, the swaps start pushing into words where synonyms shift meaning. Tune it with `--swap-every`, and read the `-l` log.

**Safety net:** every response is checked. All locked tokens must come back exactly once, word count must stay within −25% to +30%, and no line breaks or markdown can be added. A response that fails is retried once. If it fails again, the run fails with exit code 1 and nothing is written.

**Cost:** at $0.10 per million input tokens and $0.50 per million output tokens (Haiku 5.5, prompts under 100K tokens), a 1,000-word essay is roughly 7K tokens in and 1.5K out, so about **$0.0015**.

## Window rotation (default)

By default the tool repeats three windows across the whole document: leave 100 words unchanged, reword 150 with Haiku, then reword 150 with Gemini. No flags are needed. Use `--pattern X,Y,Z` to change the three numbers.

```bash
./rewriter essay.txt -l               # default: 100,150,150
./rewriter paper.docx --pattern 0,200,200 --swap-every 8
./rewriter essay.txt --gemini-model gemini-3.8-flash-high
./rewriter essay.txt --pattern off    # per-paragraph mode (see "LLM mode" above)
```

- **Windows follow sentences.** Each window ends on the sentence boundary closest to its word budget, and the count carries across paragraphs. Set any number to 0 to drop that window, as long as Y or Z is above 0.
- **Rewording only changes words.** Sentence order and structure stay the same, and `--swap-every` sets how many words change. The phrase bank and local rules don't run in this mode, so unchanged windows stay verbatim. The clean stage still removes invisible characters, unless you use the `Options(clean=False)` API.
- **Models see context but can't edit it.** Each call includes up to 80 words on each side as read-only context, and only the reworded span is spliced back in. Locked tokens are numbered across the whole document.
- **Failure means exit code 1.** If a model is unavailable, or its reply fails the checks twice, the run stops with exit code 1, prints nothing and writes no output file. A reply that finds nothing worth changing is not a failure: that window just stays as it was.
- **Sign-in:** Gemini runs through the `agy` (Antigravity) CLI, and Haiku through the `claude` CLI, so both must be installed and signed in on the machine running the tool. `agy` calls run in read-only plan mode from an empty folder. No Gemini key is used.
- **Offline:** `--offline` or `--llm off` skips rotation and uses the local rules. Passing an explicit `--pattern` with them exits with an error.
- **Flags that only apply with `--pattern off` or `--offline`:** `-i`, `-s`, `--no-synonyms`, `--no-structure`, `--no-phrases` and `--restructure-every`.

## Meaning-drift guard

Every model reply is checked word by word against the original, without another model call. A swap is undone, and the rest of the reply kept, when the original word:

- is a name (spaCy tags it as a proper noun),
- contains a digit,
- is rare enough to be a technical term (wordfreq Zipf frequency below 3.0, e.g. "saplings"), or
- is part of a pair on the blocklist for that model.

The blocklist is `rewriter/data/llm_blocklist.txt`. A plain `hire -> pay` line applies to every model, and `gemini: hire -> pay` applies to one. Matching uses base forms, so it also catches "hired -> paid". The pairs in `synonym_blocklist.txt` apply too. When `-l` shows a swap that shifts the meaning, add a line.

The prompts also tell both models to keep nouns for people, groups and roles, and to keep each verb's exact action. Gemini gets one extra rule: when unsure, keep the original word.

## Exit status

`0` on success. `1` if a model was unavailable or couldn't improve its part, in which case nothing is printed and no file is written. `--offline` runs never fail this way.

## Deploying on Debian

Build the binary (`./build_linux.sh`) and copy `dist/rewriter` to the server. Sign in to `claude` and `agy` as the user who will run the tool:

```bash
curl -fsSL https://claude.ai/install.sh | bash   # install the claude CLI, then run `claude` to sign in
agy                                              # sign in to Antigravity (install it from antigravity.google)
```

On a server without a browser, `claude` prints a link to open on another machine. `claude setup-token` creates a long-lived token if you'd rather not sign in interactively. Then:

```bash
./rewriter --setup              # install ~/.local/bin/rewriter and check both CLIs
./rewriter --setup --web        # also run the web app as a systemd user service
```

`--setup` copies the binary to `~/.local/bin/rewriter`. It then checks that `claude` and `agy` are installed, signed in and answering, by sending a tiny prompt through the same code a rewrite uses. It also checks that `agy` has no MCP servers configured, since they can make `agy -p` hang. It exits with 1 if any check fails.

With `--web`, the binary unpacks the web app's source into `~/.local/share/rewriter`, creates a Python environment there (this needs `python3-venv`), and starts `rewriter-web.service` on `127.0.0.1:8501` (`--port` to change). It only listens locally, so reach it with `ssh -L 8501:127.0.0.1:8501 user@server`. Run it as your normal user, not root.

## How it works

Each line or paragraph goes through five stages:

| Stage | What it does | Source |
|---|---|---|
| 1. clean | Strips zero-width and bidi characters and fixes Cyrillic/Greek look-alike letters inside Latin words | `clean.py` |
| 2. protect | Swaps citations `(Smith et al., 2021)` and `[3]`, numbers and units, acronyms, quotes, URLs, code, LaTeX math (including environments that span several lines), `\cite{}`/`\ref{}` and your terms for placeholders | `mask.py` |
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
  llm.py            Claude Haiku and Gemini (agy) backends, prompts, validation
  windows.py        sentence-aligned window rotation (--pattern)
  drift.py          undoes model swaps likely to change meaning
  setup_cmd.py      rewriter --setup (install and checks on Linux)
  checker.py zippy_lite.py
  data/             phrasebank.json, synonyms.json, blocklists, marker lists
scripts/build_data.py
rewriter.spec build_linux.sh
tests/
```

## Next steps that would improve quality most

1. **Run it on your real writing with `-l`** and tune `--swap-every` and `--restructure-every`. The prompt in `llm.py` is plain text and easy to edit.
2. **Grow the phrase bank** from your own writing domain. It runs before the LLM and costs nothing.
3. **Offline mode quality:** score synonym candidates with a small masked language model (e.g. DistilRoBERTa) instead of trusting PPDB/WordNet.
