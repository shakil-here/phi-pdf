#!/bin/bash
# PHI PDF Studio · double-click to start on a Mac. The first start installs everything into a .venv folder here.
cd "$(dirname "$0")" || exit 1
here="$(pwd -P)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. Install it from https://www.python.org/downloads/ and double-click this file again."
  read -r -p "Press Enter to close." _; exit 1
fi
# A .venv made in another folder (this folder was moved) cannot run from here: install again.
if [ -e .venv ]; then
  { read -r shebang < .venv/bin/phi-pdf; } 2>/dev/null
  case "$shebang" in "#!$here/.venv/"*) ;; *) echo "This folder was moved: installing again…"; rm -rf .venv ;; esac
fi
if [ ! -x .venv/bin/phi-pdf ]; then
  echo "First start: installing (a few minutes, once)…"
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet --upgrade pip && .venv/bin/python -m pip install --quiet ".[ai]" || {
    read -r -p "Installation failed (see above). Press Enter to close." _; exit 1; }
  command -v tesseract >/dev/null 2>&1 || echo "Tip: for OCR install Tesseract:  brew install tesseract"
fi
# The Desktop icon, made again when it was deleted or still opens this folder's old place.
grep -qF "$here/.venv/" "$HOME/Desktop/PHI PDF Studio.app/Contents/MacOS/phi-pdf-studio" 2>/dev/null ||
  .venv/bin/phi-pdf shortcut || echo "(The Desktop icon could not be made; run: .venv/bin/phi-pdf shortcut)"
exec .venv/bin/phi-pdf
