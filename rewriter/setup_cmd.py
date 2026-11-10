"""`rewriter --setup`: install the binary and check that both model CLIs are ready (Debian/Linux).

    rewriter --setup                  install ~/.local/bin/rewriter and run the checks
    rewriter --setup --web            also run the web app as a systemd user service
    rewriter --setup --web --port 8600

Run it as the user who is signed in to `claude` and `agy`, not root: both CLIs keep their
sign-in in that user's home folder. Returns 0 only when every check passes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import llm as L

READY_PROMPT = "Reply with exactly <rewritten>READY</rewritten> and nothing else."
WEB_PACKAGES = ["spacy", "lemminflect", "wordfreq", "python-docx", "streamlit"]
SERVICE = "rewriter-web.service"


class _Report:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, msg: str) -> None:
        print(f"  ok    {msg}")

    def bad(self, msg: str) -> None:
        print(f"  FAIL  {msg}")
        self.failed = True

    def note(self, msg: str) -> None:
        print(f"  note  {msg}")


def _answers(provider: str) -> bool:
    """Send a tiny prompt through the same code path a real rewrite uses."""
    try:
        cfg = L.LLMConfig(backend="cli", provider=provider, timeout=110).resolve()
        return "READY" in L._extract(L._CALLS[(provider, "cli")](READY_PROMPT, cfg))
    except L.LLMError:
        return False


def _install_binary(r: _Report, bin_dir: Path) -> None:
    if not getattr(sys, "frozen", False):
        r.note("running from source, so there is no binary to install (build one with ./build_linux.sh)")
        return
    target = bin_dir / "rewriter"
    me = Path(sys.executable).resolve()
    if me != target.resolve():
        bin_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".new")
        shutil.copy2(me, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, target)  # replace atomically, even if the old copy is running
    r.ok(f"installed {target}")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        r.note(f"{bin_dir} is not on PATH. Add it: echo 'export PATH=\"{bin_dir}:$PATH\"' >> ~/.bashrc")


def _check_claude(r: _Report) -> None:
    if not shutil.which("claude"):
        r.bad("claude is not installed. Install: curl -fsSL https://claude.ai/install.sh | bash")
    elif _answers("claude"):
        r.ok("claude is signed in and answering")
    else:
        r.bad("claude is installed but didn't answer. Sign in by running: claude")


def _check_agy(r: _Report) -> None:
    if not shutil.which("agy"):
        r.bad("agy is not installed. See https://antigravity.google for the Antigravity CLI")
        return
    try:
        mcp = subprocess.run(["agy", "mcp", "list"], capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        listing = (mcp.stdout + mcp.stderr).strip()
    except subprocess.TimeoutExpired:
        listing = "(agy mcp list timed out)"
    if "No MCP servers configured" in listing:
        r.ok("agy has no MCP servers (they can make print mode hang)")
    else:
        r.bad("agy has MCP servers configured, which can make print mode hang. Remove each with: agy mcp remove NAME")
        for line in listing.splitlines():
            print(f"        {line}")
    if _answers("gemini"):
        r.ok("agy is signed in and answering")
    else:
        r.bad("agy is installed but didn't answer. Sign in by running: agy")


def _app_source() -> Path:
    """Folder holding app.py and the rewriter package: bundled in the binary, or the repo."""
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "webapp"
    return bundled if (bundled / "app.py").exists() else Path(__file__).resolve().parents[1]


def _setup_web(r: _Report, app_dir: Path, bin_dir: Path, port: int) -> None:
    python = shutil.which("python3")
    if not python or subprocess.run([python, "-c", "import ensurepip, venv"], capture_output=True).returncode:
        r.bad("python3-venv is missing. Install: sudo apt install python3-venv")
        return
    src = _app_source()
    app_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "app.py", app_dir / "app.py")
    shutil.copytree(src / "rewriter", app_dir / "rewriter", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    venv = app_dir / "venv"
    steps = [
        [python, "-m", "venv", str(venv)],
        [str(venv / "bin" / "pip"), "install", "-q", *WEB_PACKAGES],
        [str(venv / "bin" / "python"), "-m", "spacy", "download", "en_core_web_sm"],
    ]
    for cmd in steps:
        if subprocess.run(cmd, capture_output=True).returncode:
            r.bad(f"installing the web app failed at: {' '.join(cmd[1:4])}")
            return
    r.ok(f"web app and its Python packages installed in {app_dir}")

    # The service needs a PATH that finds claude and agy, as the checks above did.
    path = [str(bin_dir)]
    for name in ("claude", "agy"):
        found = shutil.which(name)
        if found and str(Path(found).parent) not in path:
            path.append(str(Path(found).parent))
    unit = Path.home() / ".config" / "systemd" / "user" / SERVICE
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"""[Unit]
Description=Improve text
After=network-online.target

[Service]
WorkingDirectory={app_dir}
Environment=PATH={":".join(path)}:/usr/local/bin:/usr/bin:/bin
ExecStart={venv}/bin/streamlit run app.py --server.address 127.0.0.1 --server.port {port} --server.headless true --browser.gatherUsageStats false
Restart=on-failure

[Install]
WantedBy=default.target
""", encoding="utf-8")
    systemctl = ["systemctl", "--user"]
    started = all(subprocess.run(systemctl + args, capture_output=True).returncode == 0
                  for args in (["daemon-reload"], ["enable", "--now", SERVICE]))
    if not started:
        r.bad(f"couldn't start the systemd user service (wrote {unit})")
        return
    r.ok(f"{SERVICE} is running on 127.0.0.1:{port}")
    user = os.environ.get("USER", "")
    if subprocess.run(["loginctl", "enable-linger", user], capture_output=True).returncode == 0:
        r.ok("the service keeps running after you log out")
    else:
        r.note(f"to keep it running after logout, run once: sudo loginctl enable-linger {user}")
    r.note(f"it only listens on this machine. From your laptop: ssh -L {port}:127.0.0.1:{port} {user}@<server>, "
           f"then open http://localhost:{port}")


def run(web: bool = False, port: int = 8501, bin_dir: Path | None = None, app_dir: Path | None = None) -> int:
    bin_dir = bin_dir or Path(os.environ.get("BIN_DIR", Path.home() / ".local" / "bin"))
    app_dir = app_dir or Path(os.environ.get("APP_DIR", Path.home() / ".local" / "share" / "rewriter"))
    linux = sys.platform.startswith("linux")
    if linux and os.geteuid() == 0:
        print("Run this as your normal user, not root: the CLIs' sign-ins live in that user's home.")
        return 2
    r = _Report()

    print("1. Binary")
    if linux:
        _install_binary(r, bin_dir)
    else:
        r.note("installing the binary is Linux only; checking the model CLIs only")
    print("2. Claude Haiku (claude CLI)")
    _check_claude(r)
    print("3. Gemini (agy CLI)")
    _check_agy(r)
    if web:
        print("4. Web app (systemd user service)")
        if linux:
            _setup_web(r, app_dir, bin_dir, port)
        else:
            r.bad("the web app service needs Linux with systemd")

    print()
    print("All checks passed." if not r.failed else "Some checks failed (see FAIL above).")
    return 1 if r.failed else 0
