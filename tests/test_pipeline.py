import re

import pytest

from text_improver.checker import analyze, change_ratio
from text_improver.clean import clean_text
from text_improver.mask import mask
from text_improver.pipeline import Options, rewrite

SAMPLE = (
    "In today's fast-paced world, artificial intelligence plays a crucial role in shaping how "
    "organizations operate. It is important to note that these systems leverage vast amounts of data. "
    "Moreover, researchers have delved into the intricate mechanisms (Smith et al., 2021). "
    "The method achieves 94.2% accuracy on the benchmark [3]; this represents a large improvement."
)


def test_deterministic_with_seed():
    a = rewrite(SAMPLE, Options(seed=1)).text
    b = rewrite(SAMPLE, Options(seed=1)).text
    assert a == b


def test_protected_spans_survive():
    for seed in range(15):
        out = rewrite(SAMPLE, Options(seed=seed, intensity=1.0)).text
        assert "(Smith et al., 2021)" in out
        assert "94.2%" in out
        assert "[3]" in out
        assert "ZXQ" not in out


def test_quotes_and_terms_protected():
    text = 'The author wrote "we utilize robust methods" and we utilize Kalman filters robustly.'
    for seed in range(10):
        out = rewrite(text, Options(seed=seed, intensity=1.0, protected_terms=["Kalman filters"])).text
        assert '"we utilize robust methods"' in out
        assert "Kalman filters" in out


def test_ai_markers_reduced():
    before = analyze(SAMPLE).notes_per_1k
    after = analyze(rewrite(SAMPLE, Options(seed=3, intensity=1.0)).text).notes_per_1k
    assert after < before


def test_sentence_start_deletion_recapitalises():
    out = rewrite("It is important to note that the model fails.", Options(seed=0, intensity=1.0, synonyms=False, structure=False)).text
    assert out[0].isupper()


def test_inflection():
    out = rewrite("She utilized the tool and delved into it.", Options(seed=0, intensity=1.0, synonyms=False, structure=False)).text
    assert "utilized" not in out and "delved" not in out
    assert re.search(r"\bused\b", out)


def test_article_agreement():
    for seed in range(10):
        out = rewrite("This is an intricate problem.", Options(seed=seed, intensity=1.0, synonyms=False, structure=False)).text
        assert not re.search(r"\ban (complex|detailed)\b", out)
        assert not re.search(r"\ba elaborate\b", out)


def test_latex_math_and_commands_untouched():
    tex = r"We utilize $\alpha + \beta$ as shown in \cite{smith21} and Eq.~\eqref{eq:1}."
    out = rewrite(tex, Options(seed=0, intensity=1.0, latex=True)).text
    for frag in (r"$\alpha + \beta$", r"\cite{smith21}", r"\eqref{eq:1}"):
        assert frag in out


def test_clean_removes_invisible_and_homoglyphs():
    txt, stats = clean_text("hel​lo wоrld")  # Cyrillic o
    assert txt == "hello world"
    assert stats["invisible"] == 1


def test_mask_roundtrip():
    t = 'See [1], (Lee, 2019) and "a quote" at https://x.org with GPT-4.'
    m = mask(t)
    assert m.unmask(m.text) == t


def test_change_ratio():
    assert change_ratio("a b c", "a b c") == 0
    assert change_ratio("a b c", "x y z") == 1


@pytest.mark.parametrize("seed", range(5))
def test_blank_lines_preserved(seed):
    text = "First paragraph here is short.\n\nSecond paragraph utilizes data."
    out = rewrite(text, Options(seed=seed)).text
    assert out.count("\n\n") == 1
