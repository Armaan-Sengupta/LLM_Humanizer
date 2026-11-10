import io
import re

import pytest
from docx import Document

import text_improver.llm as L
from text_improver.docx_io import rewrite_docx
from text_improver.llm import LLMConfig, LLMError
from text_improver.pipeline import Options, rewrite
from text_improver.windows import Phase, Span, assign_phases, context, parse_pattern, plan


def _sentence(n: int) -> str:
    return f"The team shows result number{n} and it shows a clear and steady trend today."


def _fake(tag: str, calls: list | None = None):
    def call(prompt, cfg):
        if calls is not None:
            calls.append(prompt)
        edit = re.search(r"<edit>\n(.*)\n</edit>", prompt, re.S).group(1)
        return f"<out>{edit.replace('shows', tag)}</out>"

    return call


@pytest.fixture
def rotation(monkeypatch):
    calls = {"haiku": [], "gemini": []}
    monkeypatch.setattr(L.shutil, "which", lambda name: None if "not-installed" in name else f"/bin/{name}")
    monkeypatch.setitem(L._CALLS, ("claude", "cli"), _fake("HAIKU", calls["haiku"]))
    monkeypatch.setitem(L._CALLS, ("gemini", "cli"), _fake("GEMINI", calls["gemini"]))
    phases = [
        Phase(15),
        Phase(15, LLMConfig("cli", "haiku"), "haiku"),
        Phase(15, LLMConfig("cli", "gemini-x", provider="gemini"), "gemini"),
    ]
    return phases, calls


def test_parse_pattern():
    assert parse_pattern("100,150,50") == (100, 150, 50)
    assert parse_pattern("0,150,0") == (0, 150, 0)
    for bad in ("1,2", "a,b,c", "-1,2,3", "5,0,0"):
        with pytest.raises(ValueError):
            parse_pattern(bad)


def test_assign_rotates_and_repeats():
    assert assign_phases([10] * 6, [10, 10, 10]) == [0, 1, 2, 0, 1, 2]


def test_assign_lands_on_nearest_sentence_boundary():
    assert assign_phases([10, 10, 10, 10], [24, 100]) == [0, 0, 1, 1]
    assert assign_phases([10, 10, 10, 10], [26, 100]) == [0, 0, 0, 1]


def test_assign_keeps_long_sentence_whole_and_skips_zero_budget():
    assert assign_phases([50, 5], [10, 0, 10]) == [0, 2]


def test_plan_counts_across_paragraphs():
    lines = ["One two three four five six seven eight nine ten.", "", "Alpha beta gamma delta epsilon zeta eta theta iota kappa."]
    spans = plan(lines, [Phase(10), Phase(10, object(), "m")])
    assert [(s.line, s.phase.label) for s in spans] == [(0, "keep"), (2, "m")]


def test_context_crosses_paragraphs():
    lines = ["aa bb cc", "dd ee ff", "gg hh"]
    assert context(lines, Span(1, 3, 5, Phase(1))) == ("aa bb cc dd", "ff gg hh")


def test_rotation_order_and_verbatim_keep(rotation):
    phases, calls = rotation
    text = " ".join(_sentence(i) for i in range(6))
    r = rewrite(text, Options(rotation=phases, llm_workers=1))
    parts = re.findall(r"[^.]+\.", r.text)
    assert _sentence(0) in r.text and _sentence(3) in r.text
    assert "HAIKU" in parts[1] and "HAIKU" in parts[4]
    assert "GEMINI" in parts[2] and "GEMINI" in parts[5]
    assert {c.stage for c in r.changes} == {"haiku", "gemini"}
    assert len(calls["haiku"]) == 2 and len(calls["gemini"]) == 2


def test_rotation_turns_off_local_rules(rotation):
    phases, _ = rotation
    text = "Moreover, we utilize a robust method here today. " * 3
    r = rewrite(text.strip(), Options(rotation=[Phase(1000)] + phases[1:], llm_workers=1))
    assert r.text == text.strip() and not r.changes


def test_context_is_read_only_and_tokens_are_unique_across_paragraphs(rotation):
    phases, calls = rotation
    text = "Before part shows the value 94.2% in this first sentence now.\n\nTarget part shows the value 17.5% in this second sentence now.\n\nAfter part shows the value 3.3% in this third sentence now."
    r = rewrite(text, Options(rotation=[Phase(10), phases[1], Phase(10)], llm_workers=1))
    prompt = calls["haiku"][0]
    before, edit, after = re.search(r"<before>\n(.*)\n</before>\n\n<edit>\n(.*)\n</edit>\n\n<after>\n(.*)\n</after>", prompt, re.S).groups()
    assert len(set(re.findall(r"ZXQ\d+QXZ", before + edit + after))) == 3
    assert before.startswith("Before part") and edit.startswith("Target part") and after.startswith("After part")
    assert "94.2%" in r.text and "17.5%" in r.text and "3.3%" in r.text
    assert "Before part shows" in r.text and "Target part HAIKU" in r.text and "After part shows" in r.text


def test_bad_output_leaves_window_unchanged(monkeypatch, rotation):
    phases, _ = rotation
    monkeypatch.setitem(L._CALLS, ("claude", "cli"), lambda p, c: "no tags here")
    text = " ".join(_sentence(i) for i in range(3))
    r = rewrite(text, Options(rotation=phases, llm_workers=1))
    assert _sentence(1) in r.text and "GEMINI" in r.text
    assert not r.ok and r.failures[0].startswith("helper A:")


def test_unchanged_reply_is_retried_but_not_a_failure(monkeypatch, rotation):
    phases, _ = rotation
    calls = []

    def echo(prompt, cfg):
        calls.append(1)
        return "<out>" + re.search(r"<edit>\n(.*)\n</edit>", prompt, re.S).group(1) + "</out>"

    monkeypatch.setitem(L._CALLS, ("claude", "cli"), echo)
    r = rewrite(" ".join(_sentence(i) for i in range(2)), Options(rotation=phases, llm_workers=1))
    assert len(calls) == 2 and r.ok and _sentence(1) in r.text


def test_missing_gemini_warns_and_keeps_text(monkeypatch, rotation):
    phases, _ = rotation
    gone = Phase(15, LLMConfig(provider="gemini", gemini_bin="definitely-not-installed-xyz"), "gemini")
    text = " ".join(_sentence(i) for i in range(3))
    r = rewrite(text, Options(rotation=[phases[0], phases[1], gone], llm_workers=1))
    assert _sentence(2) in r.text and "HAIKU" in r.text
    assert not r.ok and r.failures[0].startswith("helper B unavailable")


def test_docx_rotation_carries_across_paragraphs(rotation):
    phases, calls = rotation
    doc = Document()
    for i in range(3):
        doc.add_paragraph(_sentence(i))
    buf = io.BytesIO()
    doc.save(buf)
    data, changes, failures = rewrite_docx(buf.getvalue(), Options(rotation=phases, llm_workers=1))
    out = [p.text for p in Document(io.BytesIO(data)).paragraphs]
    assert out[0] == _sentence(0) and "HAIKU" in out[1] and "GEMINI" in out[2]
    assert not failures and {c.stage for c in changes} == {"haiku", "gemini"}


def test_drift_guard_runs_on_model_replies(monkeypatch, rotation):
    phases, _ = rotation

    def gemini(prompt, cfg):
        edit = re.search(r"<edit>\n(.*)\n</edit>", prompt, re.S).group(1)
        return "<out>" + edit.replace("hire", "pay").replace("quickly", "fast") + "</out>"

    monkeypatch.setitem(L._CALLS, ("gemini", "cli"), gemini)
    text = "Some departments now hire local residents quickly to check on new plantings early."
    r = rewrite(text, Options(rotation=[Phase(0), Phase(0, phases[1].cfg, "haiku"), phases[2]], llm_workers=1))
    assert "hire" in r.text and "pay" not in r.text and "fast" in r.text


def test_gemini_uses_agy_login(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw["cwd"]
        return L.subprocess.CompletedProcess(cmd, 0, stdout="<out>a</out>", stderr="")

    monkeypatch.setattr(L.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(L.subprocess, "run", run)
    cfg = LLMConfig(provider="gemini").resolve()
    assert cfg.backend == "cli" and cfg.model == L.DEFAULT_GEMINI_MODEL
    assert L._call_gemini_cli("hello", cfg) == "<out>a</out>"
    assert seen["cmd"][0] == "/bin/agy" and seen["cmd"][1] == "--print" and "hello" in seen["cmd"][2]
    assert "plan" in seen["cmd"] and cfg.model in seen["cmd"]
    assert not any("dangerously" in part for part in seen["cmd"])
    assert seen["cwd"] != L.os.getcwd()


def test_gemini_failure_is_reported(monkeypatch):
    monkeypatch.setattr(L.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(L.subprocess, "run", lambda cmd, **kw: L.subprocess.CompletedProcess(cmd, 3, stdout="", stderr="sign in"))
    with pytest.raises(LLMError, match="helper B failed: sign in"):
        L._call_gemini_cli("hello", LLMConfig("cli", "m", provider="gemini"))
