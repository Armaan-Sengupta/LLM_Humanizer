import os
import stat
import sys
import textwrap

import pytest

from text_improver.llm import LLMConfig, LLMError, build_prompt, validate
from text_improver.pipeline import Options, rewrite

TEXT = "The approach shows a large gain of 94.2% on the benchmark (Smith et al., 2021). It is stable when data is noisy."


def _mock(tmp_path, body: str):
    p = tmp_path / "claude"
    p.write_text("#!/usr/bin/env python3\nimport sys, re\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    if sys.platform == "win32":  # Windows can't execute a shebang script directly
        wrapper = tmp_path / "claude.cmd"
        wrapper.write_text(f'@"{sys.executable}" "{p}" %*\n')
        return str(wrapper)
    return str(p)


GOOD = """
p = re.search(r"<passage>\\n(.*)\\n</passage>", sys.stdin.read(), re.S).group(1)
print("<out>" + p.replace("shows", "reveals") + "</out>")
"""
DROP = """
p = re.search(r"<passage>\\n(.*)\\n</passage>", sys.stdin.read(), re.S).group(1)
print("<out>" + re.sub(r"ZXQ\\d+QXZ", "", p) + "</out>")
"""


def test_validate_rules():
    validate("a ZXQ0QXZ b c d", "x ZXQ0QXZ y c d")
    with pytest.raises(LLMError):
        validate("a ZXQ0QXZ b", "a b")  # dropped placeholder
    with pytest.raises(LLMError):
        validate("a ZXQ0QXZ b", "a ZXQ0QXZ ZXQ0QXZ b")  # duplicated
    with pytest.raises(LLMError):
        validate("one two three four five six", "one")  # length
    with pytest.raises(LLMError):
        validate("one two three four", "**one** two three four")  # markdown


def test_prompt_counts():
    p = build_prompt(" ".join(["word"] * 120) + ". Second. Third. Fourth.", LLMConfig("cli", "haiku", swap_every=12))
    assert "Adjust about 10 words" in p
    assert "<passage>" in p and "ZXQ<number>QXZ" in p


def test_llm_success_used(tmp_path):
    cfg = LLMConfig(backend="cli", model="haiku", claude_bin=_mock(tmp_path, GOOD))
    r = rewrite(TEXT, Options(seed=1, llm=cfg))
    assert "reveals" in r.text and r.ok
    assert "94.2%" in r.text and "(Smith et al., 2021)" in r.text
    assert any(c.stage == "llm" for c in r.changes)


def test_llm_bad_output_is_a_failure(tmp_path):
    cfg = LLMConfig(backend="cli", model="haiku", claude_bin=_mock(tmp_path, DROP))
    r = rewrite(TEXT, Options(seed=1, llm=cfg))
    assert "94.2%" in r.text and "(Smith et al., 2021)" in r.text
    assert not r.ok and "placeholders" in r.failures[0]


def test_no_backend_is_a_failure(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = rewrite(TEXT, Options(seed=1, llm=LLMConfig(claude_bin="definitely-not-installed-xyz")))
    assert not r.ok and "unavailable" in r.failures[0]


def test_offline_never_fails():
    assert rewrite(TEXT, Options(seed=1)).ok


def test_prompt_has_meaning_rules_and_gemini_extra():
    haiku = build_prompt("one two three.", LLMConfig("cli", "haiku"))
    gemini = build_prompt("one two three.", LLMConfig("cli", "g", provider="gemini"))
    assert "hire is not pay" in haiku and "Be conservative" not in haiku
    assert "Be conservative" in gemini
