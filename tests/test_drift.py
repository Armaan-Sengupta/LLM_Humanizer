import pytest

import cli
from rewriter.drift import revert_risky
from rewriter.pipeline import Options, Result, rewrite


def test_blocklisted_swap_is_undone_for_that_model_only():
    original = "Some departments now hire local residents to check on plantings."
    reply = "Some departments now pay local people to check on plantings."
    text, undone = revert_risky(original, reply, "gemini")
    assert text == original and len(undone) == 2
    # "hire -> pay" and "resident -> people" are also listed for every model
    assert revert_risky(original, reply, "haiku")[0] == original


def test_safe_swaps_survive_next_to_undone_ones():
    original = "Some departments now hire residents and report problems quickly."
    reply = "Some departments now pay residents and report issues fast."
    text, _ = revert_risky(original, reply, "gemini")
    assert text == "Some departments now hire residents and report issues fast."


def test_names_rare_words_and_digits_are_put_back():
    original = "Maria planted the saplings near block B12 quickly."
    reply = "Anna planted the seedlings near block C12 fast."
    text, undone = revert_risky(original, reply, "haiku")
    assert text == "Maria planted the saplings near block B12 fast."
    assert len(undone) == 3


def test_locked_tokens_are_never_duplicated_by_an_undo():
    original = "The value ZXQ0QXZ shows a steady and clear rise today."
    reply = "The value reveals a steady and clear rise ZXQ0QXZ today."
    text, _ = revert_risky(original, reply, "haiku")
    assert text.count("ZXQ0QXZ") == 1


def test_multiline_latex_is_locked():
    tex = ("We utilize this result.\n\\begin{equation}\n  utilize = robust \\\\\n  x = y\n"
           "\\end{equation}\nMoreover, we utilize it.")
    for seed in range(5):
        out = rewrite(tex, Options(seed=seed, intensity=1.0, latex=True)).text
        assert "\\begin{equation}\n  utilize = robust \\\\\n  x = y\n\\end{equation}" in out
        assert "ZXQ" not in out


def test_cli_exits_1_and_writes_nothing_on_failure(tmp_path, monkeypatch, capsys):
    src = tmp_path / "essay.txt"
    src.write_text("Some text here.", encoding="utf-8")
    monkeypatch.setattr("rewriter.pipeline.rewrite", lambda text, opts: Result(text, [], {}, ["gemini unavailable"]))
    with pytest.raises(SystemExit) as e:
        cli.main([str(src)])
    assert e.value.code == 1
    assert not (tmp_path / "essay.improved.txt").exists()
    assert capsys.readouterr().err == ""


def test_cli_reads_piped_utf8_and_writes_utf8():
    import subprocess
    import sys
    from pathlib import Path

    text = "The team’s plan works — mostly.\n"
    p = subprocess.run([sys.executable, "cli.py", "--offline", "--no-phrases", "--no-synonyms", "--no-structure"],
                       input=text.encode("utf-8"), capture_output=True, cwd=Path(__file__).resolve().parents[1])
    assert p.returncode == 0
    assert p.stdout.decode("utf-8").replace("\r\n", "\n") == text


def test_cli_succeeds_and_writes_output(tmp_path, monkeypatch):
    src = tmp_path / "essay.txt"
    src.write_text("Some text here.", encoding="utf-8")
    monkeypatch.setattr("rewriter.pipeline.rewrite", lambda text, opts: Result("New text here.", [], {}))
    cli.main([str(src)])
    assert (tmp_path / "essay.improved.txt").read_text(encoding="utf-8") == "New text here."
