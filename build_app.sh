#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — App bauen                                         ║
# ╚══════════════════════════════════════════════════════════════╝
# Erzeugt "dist/TrackTab.app" — doppelklickbar, ohne Python
# und ohne Homebrew auf dem Zielrechner.
#
# Liegt ein statisch gelinktes ffmpeg/ffprobe in vendor/, wandert es mit
# ins Bundle. Sonst greift die App auf ein installiertes ffmpeg zurueck.

cd "$(dirname "$0")" || exit 1
set -e

if [ ! -x ".venv/bin/python" ]; then
  echo "FEHLER: .venv fehlt. Erst  python3 -m venv .venv  und  pip install -r requirements.txt"
  exit 1
fi
if ! ./.venv/bin/python -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller wird installiert …"
  ./.venv/bin/pip install --quiet pyinstaller
fi

if [ -d "vendor" ]; then
  echo "vendor/ gefunden — ffmpeg wird mitgeliefert:"
  ls -1 vendor
else
  echo "Hinweis: kein vendor/ — die App braucht dann ein installiertes ffmpeg."
fi

rm -rf build dist
./.venv/bin/python -m PyInstaller --noconfirm --clean tracktab.spec

echo
echo "Fertig: dist/TrackTab.app"
du -sh "dist/TrackTab.app" 2>/dev/null

# Eine Verknüpfung in /Applications zeigt auf dist/ und ist damit nach jedem
# Build automatisch aktuell — sie muss nicht neu angelegt werden. Nach einer
# Umbenennung zeigen alte Verknüpfungen ins Leere und werden durch die neue ersetzt.
OLD_LINKS=("/Applications/TrackLab.app" "/Applications/MP3 Quality Check.app" "/Applications/Audio Quality Check.app")
NEW_LINK="/Applications/TrackTab.app"
for OLD_LINK in "${OLD_LINKS[@]}"; do
  if [ -L "$OLD_LINK" ]; then
    rm "$OLD_LINK"
    echo "Alte Verknüpfung „$(basename "$OLD_LINK" .app)“ aus /Applications entfernt."
  fi
done
if [ -L "$NEW_LINK" ]; then
  echo "Verknüpfung in /Applications zeigt weiterhin hierher."
else
  ln -s "$(pwd)/dist/TrackTab.app" "$NEW_LINK"
  echo "Verknüpfung „TrackTab“ in /Applications angelegt."
fi
