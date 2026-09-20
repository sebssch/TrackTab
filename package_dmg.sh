#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — DMG packen                                        ║
# ╚══════════════════════════════════════════════════════════════╝
# Verpackt ein bereits gebautes "dist/TrackTab.app" (siehe build_app.sh)
# in eine einzelne "TrackTab-<version>.dmg" fuer die Weitergabe. Der
# Empfaenger braucht dafuer kein Terminal: DMG oeffnen, App auf die
# Programme-Verknuepfung ziehen, per Rechtsklick -> "Oeffnen" starten
# (das Bundle ist nicht signiert, siehe build_app.sh).
#
# Bewusst getrennt von build_app.sh, nicht automatisch daran gekoppelt --
# nicht jeder App-Build braucht sofort eine neue DMG.

cd "$(dirname "$0")" || exit 1
set -e

if [ ! -d "dist/TrackTab.app" ]; then
  echo "FEHLER: dist/TrackTab.app fehlt. Erst  ./build_app.sh  ausfuehren."
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "FEHLER: .venv fehlt."
  exit 1
fi

VERSION=$(./.venv/bin/python -c "from app import __version__; print(__version__)")
DMG_NAME="TrackTab-${VERSION}.dmg"
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

echo "Version: $VERSION"
echo "Baue $DMG_NAME …"

cp -R "dist/TrackTab.app" "$STAGING/TrackTab.app"
ln -s /Applications "$STAGING/Programme"

rm -f "dist/$DMG_NAME"
hdiutil create -volname "TrackTab $VERSION" \
  -srcfolder "$STAGING" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "dist/$DMG_NAME"

echo
echo "Fertig: dist/$DMG_NAME"
du -sh "dist/$DMG_NAME"
