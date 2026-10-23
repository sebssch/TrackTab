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

# Modelle fuer Genre/Stimmung/Energie (einmalig laden, danach nur Pruefsumme).
# Schlaegt der Download fehl, entsteht ein Bundle ohne diese Funktion.
# Laeuft schon ein Download (z. B. in einem zweiten Fenster), nicht parallel
# in dieselbe .part-Datei laden -- dann ohne die fehlenden Modelle bauen.
if pgrep -f "fetch_models.sh" >/dev/null; then
  echo "Hinweis: Modell-Download läuft bereits — Build ohne die noch fehlenden Modelle."
elif ! ./fetch_models.sh; then
  echo "Hinweis: Modelle fehlen — die App kommt ohne Genre-/Stimmungs-Analyse."
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
TARGET="$(pwd)/dist/TrackTab.app"
# Zeigt schon irgendeine Verknuepfung in /Applications hierher (auch unter
# anderem Namen, z. B. "TrackTab build.app"), ist nichts zu tun.
EXISTING=""
for entry in /Applications/*.app; do
  if [ -L "$entry" ] && [ "$(readlink "$entry")" = "$TARGET" ]; then
    EXISTING="$entry"; break
  fi
done
if [ -n "$EXISTING" ]; then
  echo "Verknüpfung „$(basename "$EXISTING" .app)“ in /Applications zeigt weiterhin hierher."
else
  # Liegt unter dem Namen eine ECHTE App (per .dmg installiert), nie daneben
  # oder hinein verlinken -- ln -s wuerde einem Ordner-Ziel folgen und den
  # Link IN das installierte Bundle legen. Dann unter eigenem Namen.
  if [ -e "$NEW_LINK" ]; then
    NEW_LINK="/Applications/TrackTab build.app"
  fi
  if [ -e "$NEW_LINK" ]; then
    echo "Hinweis: $NEW_LINK existiert bereits und zeigt nicht hierher — keine Verknüpfung angelegt."
  else
    ln -s "$TARGET" "$NEW_LINK"
    echo "Verknüpfung „$(basename "$NEW_LINK" .app)“ in /Applications angelegt."
  fi
fi
