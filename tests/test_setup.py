import subprocess

import pytest

import cli
import rewriter.llm as L
import rewriter.setup_cmd as S


@pytest.fixture
def tools(monkeypatch):
    """Pretend claude and agy are installed and answering, with no MCP servers."""
    state = {"mcp": "No MCP servers configured.", "answer": "<rewritten>READY</rewritten>"}
    monkeypatch.setattr(L.shutil, "which", lambda name: f"/usr/bin/{name}")
    for key in (("claude", "cli"), ("gemini", "cli")):
        monkeypatch.setitem(L._CALLS, key, lambda prompt, cfg: state["answer"])

    def fake_run(cmd, **kw):
        if cmd[:3] == ["agy", "mcp", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=state["mcp"], stderr="")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    monkeypatch.setattr(S.sys, "platform", "win32")  # skip the Linux-only install and service steps
    return state


def test_all_checks_pass(tools, tmp_path, capsys):
    assert S.run(bin_dir=tmp_path, app_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "claude is signed in" in out and "agy is signed in" in out and "All checks passed" in out


def test_missing_clis_fail(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(L.shutil, "which", lambda name: None)
    monkeypatch.setattr(S.sys, "platform", "win32")
    assert S.run(bin_dir=tmp_path, app_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "FAIL  claude is not installed" in out and "FAIL  agy is not installed" in out


def test_signed_out_cli_fails(tools, tmp_path, capsys):
    tools["answer"] = "please log in"
    assert S.run(bin_dir=tmp_path, app_dir=tmp_path) == 1
    assert "didn't answer" in capsys.readouterr().out


def test_agy_mcp_servers_fail_and_are_listed(tools, tmp_path, capsys):
    tools["mcp"] = "NAME  TYPE   STATUS\nwix   stdio  enabled"
    assert S.run(bin_dir=tmp_path, app_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "agy has MCP servers configured" in out and "wix" in out


def test_web_needs_linux(tools, tmp_path, capsys):
    assert S.run(web=True, bin_dir=tmp_path, app_dir=tmp_path) == 1
    assert "needs Linux" in capsys.readouterr().out


def test_cli_setup_flag_passes_options_and_exit_code(monkeypatch):
    seen = {}

    def fake_run(web, port):
        seen.update(web=web, port=port)
        return 1

    monkeypatch.setattr(S, "run", fake_run)
    with pytest.raises(SystemExit) as e:
        cli.main(["--setup", "--web", "--port", "9000"])
    assert e.value.code == 1 and seen == {"web": True, "port": 9000}


def test_app_source_found_when_running_from_source():
    src = S._app_source()
    assert (src / "app.py").exists() and (src / "rewriter" / "pipeline.py").exists()
