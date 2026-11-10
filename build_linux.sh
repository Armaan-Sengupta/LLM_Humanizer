#!/usr/bin/env bash
# Build a standalone Linux x86_64 executable at dist/text-improver.
# Uses a clean venv so unrelated packages don't bloat the binary.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .buildenv
. .buildenv/bin/activate
pip install -q spacy lemminflect wordfreq python-docx pyinstaller
python -m spacy download en_core_web_sm
pyinstaller rewriter.spec --noconfirm
echo "Built: dist/text-improver  ($(du -h dist/text-improver | cut -f1))"
