#!/usr/bin/env bash
# PHI PDF Studio · start on Linux. The first start installs everything into a .venv folder here.
cd "$(dirname "$0")" || exit 1
here="$(pwd -P)"
command -v python3 >/dev/null 2>&1 || { echo "Install Python 3 first (sudo apt install python3 python3-venv)."; exit 1; }
# A .venv made in another folder (this folder was moved) cannot run from here: install again.
if [ -e .venv ]; then
  { read -r shebang < .venv/bin/phi-pdf; } 2>/dev/null
  case "$shebang" in "#!$here/.venv/"*) ;; *) echo "This folder was moved: installing again…"; rm -rf .venv ;; esac
fi
if [ ! -x .venv/bin/phi-pdf ]; then
  echo "First start: installing (a few minutes, once)…"
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet --upgrade pip && .venv/bin/python -m pip install --quiet ".[ai]" || exit 1
  command -v tesseract >/dev/null 2>&1 || echo "Tip: for OCR install Tesseract:  sudo apt install tesseract-ocr"
fi
# The menu entry and Desktop icon, made again when one was deleted or still opens this folder's old place.
desktop="$(xdg-user-dir DESKTOP 2>/dev/null)"
[ -n "$desktop" ] && [ "$desktop" != "$HOME" ] || desktop="$HOME/Desktop"
icon_ok=1
for entry in "${XDG_DATA_HOME:-$HOME/.local/share}/applications/phi-pdf-studio.desktop" "$desktop/phi-pdf-studio.desktop"; do
  [ "$entry" = "$desktop/phi-pdf-studio.desktop" ] && [ ! -d "$desktop" ] && continue
  grep -qF "$here/.venv/" "$entry" 2>/dev/null || icon_ok=0
done
[ "$icon_ok" = 1 ] || .venv/bin/phi-pdf shortcut || echo "(The Desktop icon could not be made; run: .venv/bin/phi-pdf shortcut)"
exec .venv/bin/phi-pdf "$@"
