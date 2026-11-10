"""Slightly augment phrasing.

    text-improver "A sentence to adjust."
    text-improver essay.txt
    text-improver essay.txt -o -
    cat essay.txt | text-improver
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
                from text_improver.docx_io import docx_text

                return docx_text(p.read_bytes()), p
            return p.read_text(encoding="utf-8"), p
    return value, None


def _opts(a):
    from text_improver.llm import LLMConfig
    from text_improver.pipeline import Options

    llm = None
    if a.llm != "off":
        llm = LLMConfig(backend=a.llm, model=a.model, swap_every=a.swap_every,
                        restructure_every=a.restructure_every)
    rotation = None
    if llm is None and a.pattern not in (None, "off"):
        sys.exit("error: --span cannot be combined with --local or --engine off")
    if llm is not None and a.pattern != "off":
        from text_improver.windows import DEFAULT_PATTERN, Phase, parse_pattern

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
    from text_improver.pipeline import rewrite

    opts = _opts(a)
    text, path = _resolve(a.input, a.text)

    if path is not None and path.suffix.lower() == ".docx":
        from text_improver.docx_io import rewrite_docx

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
    from text_improver.checker import analyze

    text, _ = _resolve(a.input, a.text)
    print(json.dumps(analyze(text).as_dict(), indent=2))


def cmd_compare(a) -> None:
    from text_improver.checker import analyze, change_ratio

    x, _ = _resolve(a.original, False)
    y, _ = _resolve(a.improved, False)
    rx, ry = analyze(x).as_dict(), analyze(y).as_dict()
    print(f"{'metric':18}{'original':>14}{'improved':>14}")
    for k in ["words", "reading", "weight", "notes_per_1k", "spread", "variety"]:
        print(f"{k:18}{str(rx[k]):>14}{str(ry[k]):>14}")
    print(f"{'words improved':18}{'':>14}{change_ratio(x, y):>14.1%}")


class _Help(argparse.RawDescriptionHelpFormatter):
    def _format_action(self, action):
        if action.help is argparse.SUPPRESS:
            return ""
        return super()._format_action(action)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="text-improver", description="Slightly augment phrasing.",
                                 formatter_class=_Help, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{improve}")

    r = sub.add_parser("improve", help="slightly augment phrasing in text or a file (default command)")
    r.add_argument("input", nargs="?", help="text, file path, or - for stdin (default: stdin)")
    r.add_argument("-t", "--text", action="store_true", help="treat INPUT as text even if a file has that name")
    r.add_argument("-o", "--output", help="output file, or - for stdout")
    r.add_argument("-i", "--intensity", type=float, default=0.5, help=argparse.SUPPRESS)
    r.add_argument("-s", "--seed", type=int, help=argparse.SUPPRESS)
    r.add_argument("-p", "--protect", help=argparse.SUPPRESS)
    r.add_argument("--latex", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--skip-a", dest="no_synonyms", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--skip-b", dest="no_structure", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--skip-c", dest="no_phrases", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--keep", dest="keep_metadata", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("-l", "--log", action="store_true", help=argparse.SUPPRESS)
    g = r.add_argument_group("options")
    g.add_argument("--engine", dest="llm", choices=["auto", "cli", "api", "off"], default="auto", help=argparse.SUPPRESS)
    g.add_argument("--local", dest="llm", action="store_const", const="off", help=argparse.SUPPRESS)
    g.add_argument("--name", dest="model", help=argparse.SUPPRESS)
    g.add_argument("--n1", dest="swap_every", type=int, default=12, metavar="N", help=argparse.SUPPRESS)
    g.add_argument("--n2", dest="restructure_every", type=int, default=4, metavar="N", help=argparse.SUPPRESS)
    g.add_argument("--workers", type=int, default=4, help=argparse.SUPPRESS)
    g.add_argument("--span", dest="pattern", metavar="X,Y,Z", help=argparse.SUPPRESS)
    g.add_argument("--name-b", dest="gemini_model", help=argparse.SUPPRESS)
    r.set_defaults(fn=cmd_rewrite)

    c = sub.add_parser("check", help=argparse.SUPPRESS)
    c.add_argument("input", nargs="?")
    c.add_argument("-t", "--text", action="store_true")
    c.set_defaults(fn=cmd_check)

    m = sub.add_parser("compare", help=argparse.SUPPRESS)
    m.add_argument("original")
    m.add_argument("improved")
    m.set_defaults(fn=cmd_compare)
    return ap


def build_setup_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="text-improver --setup",
                                 description="Install text-improver and check that both helpers are signed in. "
                                             "Run as your normal user.")
    ap.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--web", action="store_true", help="also run the web app as a systemd user service")
    ap.add_argument("--port", type=int, default=8501, help="web app port, on 127.0.0.1 only (default 8501)")
    return ap


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--setup" in argv:
        from text_improver.setup_cmd import run

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
