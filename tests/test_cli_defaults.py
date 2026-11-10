import pytest

import cli


def _opts(*args):
    return cli._opts(cli.build_parser().parse_args(["improve", "text", *args]))


def test_rotation_is_the_default():
    rotation = _opts().rotation
    assert [(p.words, p.label) for p in rotation] == [(100, "keep"), (150, "haiku"), (150, "gemini")]
    assert rotation[2].cfg.provider == "gemini"


def test_pattern_overrides_default():
    assert [p.words for p in _opts("--pattern", "10,20,30").rotation] == [10, 20, 30]


def test_pattern_off_uses_paragraph_mode():
    opts = _opts("--pattern", "off")
    assert opts.rotation is None and opts.llm is not None


def test_offline_skips_rotation_without_error():
    opts = _opts("--offline")
    assert opts.rotation is None and opts.llm is None


def test_explicit_pattern_with_offline_is_an_error():
    with pytest.raises(SystemExit, match="--pattern needs the LLMs"):
        _opts("--offline", "--pattern", "10,20,30")
