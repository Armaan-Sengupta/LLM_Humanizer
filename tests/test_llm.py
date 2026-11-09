import os
import stat
import textwrap

import pytest

from rewriter.llm import LLMConfig, LLMError, build_prompt, validate
from rewriter.pipeline import Options, rewrite

TEXT = "The approach shows a large gain of 94.2% on the benchmark (Smith et al., 2021). It is stable when data is noisy."


def _mock(tmp_path, body: str):
    p = tmp_path / "claude"
    p.write_text("#!/usr/bin/env python3\nimport sys, re\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


GOOD = """
p = re.search(r"<passage>\\n(.*)\\n</passage>", sys.stdin.read(), re.S).group(1)
print("<rewritten>" + p.replace("shows", "reveals") + "</rewritten>")
"""
DROP = """
p = re.search(r"<passage>\\n(.*)\\n</passage>", sys.stdin.read(), re.S).group(1)
print("<rewritten>" + re.sub(r"ZXQ\\d+QXZ", "", p) + "</rewritten>")
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
    assert "Replace about 10 words" in p
    assert "<passage>" in p and "ZXQ<number>QXZ" in p


def test_llm_success_used(tmp_path):
    cfg = LLMConfig(backend="cli", model="haiku", claude_bin=_mock(tmp_path, GOOD))
    r = rewrite(TEXT, Options(seed=1, llm=cfg))
    assert "reveals" in r.text and not r.warnings
    assert "94.2%" in r.text and "(Smith et al., 2021)" in r.text
    assert any(c.stage == "llm" for c in r.changes)


def test_llm_bad_output_falls_back(tmp_path):
    cfg = LLMConfig(backend="cli", model="haiku", claude_bin=_mock(tmp_path, DROP))
    r = rewrite(TEXT, Options(seed=1, llm=cfg))
    assert "94.2%" in r.text and "(Smith et al., 2021)" in r.text
    assert r.warnings and "placeholders" in r.warnings[0]


def test_no_backend_warns(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = rewrite(TEXT, Options(seed=1, llm=LLMConfig(claude_bin="definitely-not-installed-xyz")))
    assert r.warnings and "unavailable" in r.warnings[0]
