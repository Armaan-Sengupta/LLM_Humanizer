#!/usr/bin/env bash
# Install the rewriter on Debian and check that both model CLIs are ready to use.
#
#   deploy/setup_debian.sh              install dist/rewriter and run the checks
#   deploy/setup_debian.sh --web        also run the web app as a systemd user service
#   deploy/setup_debian.sh --web --port 8600
#
# Run it as the user who is signed in to `claude` and `agy` (not root): both CLIs keep their
# sign-in in that user's home folder. Exit status is 0 only when every check passes.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${APP_DIR:-$HOME/.local/share/rewriter}"
PORT=8501
WEB=0
FAILED=0

while [ $# -gt 0 ]; do
  case "$1" in
    --web) WEB=1 ;;
    --port) PORT="${2:?--port needs a number}"; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAILED=1; }
note() { printf '  note  %s\n' "$1"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not root: the CLIs' sign-ins live in that user's home." >&2
  exit 2
fi

echo "1. Binary"
if [ ! -x "$REPO/dist/rewriter" ]; then
  echo "  FAIL  $REPO/dist/rewriter not found. Build it first with ./build_linux.sh" >&2
  exit 1
fi
install -Dm755 "$REPO/dist/rewriter" "$BIN_DIR/rewriter" && ok "installed $BIN_DIR/rewriter"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) note "$BIN_DIR is not on PATH. Add it: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc" ;;
esac

echo "2. Claude Haiku (claude CLI)"
if ! command -v claude >/dev/null; then
  bad "claude is not installed. Install: curl -fsSL https://claude.ai/install.sh | bash"
elif timeout 120 claude -p --model haiku --tools "" --no-session-persistence \
       "Reply with the single word READY." </dev/null 2>/dev/null | grep -q READY; then
  ok "claude is signed in and answering"
else
  bad "claude is installed but didn't answer. Sign in by running: claude"
fi

echo "3. Gemini (agy CLI)"
if ! command -v agy >/dev/null; then
  bad "agy is not installed. See https://antigravity.google for the Antigravity CLI"
else
  mcp="$(timeout 60 agy mcp list 2>&1)"
  if printf '%s' "$mcp" | grep -q "No MCP servers configured"; then
    ok "agy has no MCP servers (they can make print mode hang)"
  else
    bad "agy has MCP servers configured, which can make print mode hang. Remove each with: agy mcp remove NAME"
    printf '%s\n' "$mcp" | sed 's/^/        /'
  fi
  if timeout 120 agy -p "Reply with the single word READY." --output-format text --print-timeout 100s \
       </dev/null 2>/dev/null | grep -q READY; then
    ok "agy is signed in and answering"
  else
    bad "agy is installed but didn't answer. Sign in by running: agy"
  fi
fi

if [ "$WEB" -eq 1 ]; then
  echo "4. Web app (systemd user service)"
  if ! python3 -c "import ensurepip, venv" 2>/dev/null; then
    bad "python3-venv is missing. Install: sudo apt install python3-venv"
  else
    mkdir -p "$APP_DIR"
    cp -r "$REPO/app.py" "$REPO/rewriter" "$APP_DIR/"
    find "$APP_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
    python3 -m venv "$APP_DIR/venv" \
      && "$APP_DIR/venv/bin/pip" install -q spacy lemminflect wordfreq python-docx streamlit \
      && "$APP_DIR/venv/bin/python" -m spacy download en_core_web_sm >/dev/null \
      && ok "app and its Python packages installed in $APP_DIR" \
      || bad "installing the web app's Python packages failed"

    # The service needs the same PATH the checks used, so it can find claude and agy.
    svc_path="$BIN_DIR"
    for c in claude agy; do
      d="$(dirname "$(command -v "$c" 2>/dev/null || echo /usr/bin/x)")"
      case ":$svc_path:" in *":$d:"*) ;; *) svc_path="$svc_path:$d" ;; esac
    done
    unit="$HOME/.config/systemd/user/rewriter-web.service"
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<UNIT
[Unit]
Description=Rewriter web app
After=network-online.target

[Service]
WorkingDirectory=$APP_DIR
Environment=PATH=$svc_path:/usr/local/bin:/usr/bin:/bin
ExecStart=$APP_DIR/venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port $PORT --server.headless true --browser.gatherUsageStats false
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
    if systemctl --user daemon-reload 2>/dev/null && systemctl --user enable --now rewriter-web.service 2>/dev/null; then
      ok "rewriter-web.service is running on 127.0.0.1:$PORT"
      if loginctl enable-linger "$USER" 2>/dev/null; then
        ok "service keeps running after you log out"
      else
        note "to keep it running after logout, run once: sudo loginctl enable-linger $USER"
      fi
      note "it only listens on this machine. From your laptop: ssh -L $PORT:127.0.0.1:$PORT $USER@<server>, then open http://localhost:$PORT"
    else
      bad "couldn't start the systemd user service (wrote $unit)"
    fi
  fi
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "All checks passed."; else echo "Some checks failed (see FAIL above)."; fi
exit "$FAILED"
