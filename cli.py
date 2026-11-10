"""Command-line interface.

Input can be literal text, a file path, or stdin:

    rewriter "Moreover, we utilize a robust method."      # text -> stdout
    rewriter essay.txt                                     # -> essay.improved.txt
    rewriter essay.txt -o -                                # file -> stdout
    rewriter paper.docx -o paper.out.docx                  # Word, formatting kept
    rewriter paper.tex                                     # LaTeX-aware
    cat essay.txt | rewriter                               # stdin -> stdout
    rewriter essay.txt --offline                           # never call an LLM
    rewriter essay.txt --pattern 50,200,200                # keep 50 words, Haiku 200, Gemini 200, repeat
    rewriter essay.txt --pattern off                       # per-paragraph Haiku + local rules instead
    rewriter -t "essay.txt"                                # force: treat as text, not a path

    rewriter check "some text"   |  rewriter check essay.txt
    rewriter compare original.txt improved.txt

    rewriter --setup                     install ~/.local/bin/rewriter, check claude and agy (Linux)
    rewriter --setup --web [--port N]    also run the web app as a systemd user service

Exit status: 0 on success. 1 if a model was unavailable or couldn't improve its part; nothing
is written in that case.
"""
from __future__ import annotations

import argparse
import json
import locale
import sys
from pathlib import Path

COMMANDS = {"improve", "check", "compare"}


def _resolve(value: str | None, force_text: bool) -> tuple[str, Path | None]:
    """Return (text, path_or_None). Reads stdin when value is None or '-'."""
    if value is None or value == "-":
        if sys.stdin.isatty():
            end = "Ctrl+Z then Enter" if sys.platform == "win32" else "Ctrl+D"
            print(f"Paste the text, then press {end} on a new line:", file=sys.stderr)
            return sys.stdin.read(), None
        data = sys.stdin.buffer.read()  # piped: decode ourselves so UTF-8 survives on Windows
        try:
            return data.decode("utf-8-sig"), None
        except UnicodeDecodeError:
            return data.decode(locale.getpreferredencoding(False), errors="replace"), None
    if not force_text:
        p = Path(value)
        try:
            is_file = len(value) < 4096 and "\n" not in value and p.is_file()
        except OSError:
            is_file = False
        if is_file:
            if p.suffix.lower() == ".docx":
                from rewriter.docx_io import docx_text

                return docx_text(p.read_bytes()), p
            return p.read_text(encoding="utf-8"), p
    return value, None


def _opts(a):
    from rewriter.llm import LLMConfig
    from rewriter.pipeline import Options

    llm = None
    if a.llm != "off":
        llm = LLMConfig(backend=a.llm, model=a.model, swap_every=a.swap_every,
                        restructure_every=a.restructure_every)
    rotation = None
    if llm is None and a.pattern not in (None, "off"):
        sys.exit("error: --pattern needs the LLMs, so it can't be combined with --offline or --llm off")
    if llm is not None and a.pattern != "off":
        from rewriter.windows import DEFAULT_PATTERN, Phase, parse_pattern

        try:
            keep, haiku, gemini = parse_pattern(a.pattern or DEFAULT_PATTERN)
        except ValueError as e:
            sys.exit(f"error: {e}")
        gemini_cfg = LLMConfig(provider="gemini", model=a.gemini_model, swap_every=a.swap_every)
        rotation = [Phase(keep), Phase(haiku, llm, "haiku"), Phase(gemini, gemini_cfg, "gemini")]
    return Options(
        rotation=rotation,
        intensity=a.intensity, seed=a.seed, synonyms=not a.no_synonyms,
        structure=not a.no_structure, phrases=not a.no_phrases,
        protected_terms=[t for t in (a.protect or "").split(",") if t.strip()],
        latex=a.latex, llm=llm, llm_workers=a.workers,
    )


def cmd_rewrite(a) -> None:
    from rewriter.pipeline import rewrite

    opts = _opts(a)
    text, path = _resolve(a.input, a.text)

    if path is not None and path.suffix.lower() == ".docx":
        from rewriter.docx_io import rewrite_docx

        if a.output == "-":
            sys.exit("error: .docx output can't go to stdout; use -o FILE.docx")
        data, changes, failures = rewrite_docx(path.read_bytes(), opts, scrub=not a.keep_metadata)
        if failures:
            sys.exit(1)
        out = Path(a.output or path.with_name(path.stem + ".improved.docx"))
        out.write_bytes(data)
        _report(changes, out, a.log)
        return

    if path is not None and path.suffix.lower() == ".tex":
        opts.latex = True
    res = rewrite(text, opts)
    if res.failures:
        sys.exit(1)
    # literal text / stdin default to stdout; files default to <name>.improved.<ext>
    if a.output == "-" or (a.output is None and path is None):
        if not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8")  # piped output (e.g. to the clipboard) stays UTF-8
        sys.stdout.write(res.text if res.text.endswith("\n") else res.text + "\n")
        _report(res.changes, None, a.log)
    else:
        out = Path(a.output or path.with_name(path.stem + ".improved" + path.suffix))
        out.write_text(res.text, encoding="utf-8")
        _report(res.changes, out, a.log)


def _report(changes, out, log: bool) -> None:
    print(f"improved ({len(changes)})" + (f" -> {out}" if out else ""), file=sys.stderr)
    if log:
        for c in changes:
            print(f"  improved {c.before!r} -> {c.after!r}", file=sys.stderr)


def cmd_check(a) -> None:
    from rewriter.checker import analyze

    text, _ = _resolve(a.input, a.text)
    print(json.dumps(analyze(text).as_dict(), indent=2))


def cmd_compare(a) -> None:
    from rewriter.checker import analyze, change_ratio

    x, _ = _resolve(a.original, False)
    y, _ = _resolve(a.improved, False)
    rx, ry = analyze(x).as_dict(), analyze(y).as_dict()
    print(f"{'metric':18}{'original':>14}{'improved':>14}")
    for k in ["words", "zippy_label", "zippy_confidence", "markers_per_1k", "burstiness", "mattr"]:
        print(f"{k:18}{str(rx[k]):>14}{str(ry[k]):>14}")
    print(f"{'words improved':18}{'':>14}{change_ratio(x, y):>14.1%}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rewriter", description="Improve text (local rules + optional Claude Haiku). "
                                 "INPUT may be literal text, a file path (.txt/.md/.tex/.docx), or '-' / stdin.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("improve", help="improve text or a file (default command)")
    r.add_argument("input", nargs="?", help="text, file path, or - for stdin (default: stdin)")
    r.add_argument("-t", "--text", action="store_true", help="treat INPUT as text even if a file has that name")
    r.add_argument("-o", "--output", help="output file, or - for stdout")
    r.add_argument("-i", "--intensity", type=float, default=0.5, help="0..1 (default 0.5)")
    r.add_argument("-s", "--seed", type=int, help="fix the random choices for repeatable output")
    r.add_argument("-p", "--protect", help="comma-separated terms never to change")
    r.add_argument("--latex", action="store_true", help="LaTeX-aware (automatic for .tex files)")
    r.add_argument("--no-synonyms", action="store_true")
    r.add_argument("--no-structure", action="store_true")
    r.add_argument("--no-phrases", action="store_true")
    r.add_argument("--keep-metadata", action="store_true", help="(.docx) keep author/comments metadata")
    r.add_argument("-l", "--log", action="store_true", help="print each improvement to stderr")
    g = r.add_argument_group("LLM (optional Claude Haiku and Gemini)")
    g.add_argument("--llm", choices=["auto", "cli", "api", "off"], default="auto",
                   help="auto (default): API if ANTHROPIC_API_KEY is set, else the `claude` CLI, "
                        "else local rules. off: local rules only (nothing leaves the machine)")
    g.add_argument("--offline", dest="llm", action="store_const", const="off", help="same as --llm off")
    g.add_argument("--model", help="model (default: 'haiku' for the CLI, claude-haiku-5-5 for the API)")
    g.add_argument("--swap-every", type=int, default=12, metavar="N",
                   help="improve about one word in every N words (default 12)")
    g.add_argument("--restructure-every", type=int, default=4, metavar="N",
                   help="improve about one sentence in every N (default 4)")
    g.add_argument("--workers", type=int, default=4, help="parallel LLM calls (default 4)")
    g.add_argument("--pattern", metavar="X,Y,Z",
                   help="repeat across the document: leave X words unchanged, improve Y words with Haiku, "
                        "improve Z words with Gemini (default 100,150,150). Windows end on sentence boundaries. "
                        "Only those words are improved: the phrase bank and local rules are off. "
                        "Use 'off' for per-paragraph Haiku plus local rules")
    g.add_argument("--gemini-model", help="Gemini model, as listed by `agy models` (default gemini-3.8-flash-low)")
    r.set_defaults(fn=cmd_rewrite)

    c = sub.add_parser("check", help="local diagnostics: ZipPy, AI-marker density, burstiness")
    c.add_argument("input", nargs="?")
    c.add_argument("-t", "--text", action="store_true")
    c.set_defaults(fn=cmd_check)

    m = sub.add_parser("compare", help="metrics for original vs improved")
    m.add_argument("original")
    m.add_argument("improved")
    m.set_defaults(fn=cmd_compare)
    return ap


def build_setup_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rewriter --setup",
                                 description="Install the program and check that the claude and agy "
                                             "CLIs are signed in (Debian/Linux). Run as your normal user.")
    ap.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--web", action="store_true", help="also run the web app as a systemd user service")
    ap.add_argument("--port", type=int, default=8501, help="web app port, on 127.0.0.1 only (default 8501)")
    return ap


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--setup" in argv:
        from rewriter.setup_cmd import run

        a = build_setup_parser().parse_args(argv)
        sys.exit(run(web=a.web, port=a.port))
    # `rewrite` remains accepted; help and other output only say "improve"
    if argv[:1] == ["rewrite"]:
        argv[0] = "improve"
    # `rewriter "text"` / `rewriter file.txt` / `cat x | rewriter` default to improve
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["improve"] + argv
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
