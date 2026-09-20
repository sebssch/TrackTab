#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — Starter                                           ║
# ╚══════════════════════════════════════════════════════════════╝
# Ohne Argumente: kompletter Durchlauf (scan + report).
# Mit Argumenten: direkt an die CLI durchgereicht, z.B.
#   ./run.command scan --limit 200
#   ./run.command check "/Pfad/zur/Datei.mp3"
#   ./run.command calibrate

cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "Richte die Python-Umgebung ein …"
  python3 -m venv .venv || exit 1
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt || exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg fehlt. Installieren mit:  brew install ffmpeg"
  exit 1
fi

if [ $# -eq 0 ]; then
  ./.venv/bin/python -m app scan || exit 1
  ./.venv/bin/python -m app report || exit 1
  # serve statt open: nur mit laufendem Server bleiben ausgeblendete
  # Tracks dauerhaft gespeichert.
  ./.venv/bin/python -m app serve
else
  ./.venv/bin/python -m app "$@"
fi
