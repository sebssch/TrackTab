#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — Modelle fuer Genre/Stimmung/Energie laden          ║
# ╚══════════════════════════════════════════════════════════════╝
# Laedt die Essentia-Modelle (MTG, Universitat Pompeu Fabra) einmalig nach
# vendor/models/essentia/. Lizenz der Modelle: CC BY-NC-SA 4.0 -- nur
# nicht-kommerzielle Nutzung (https://essentia.upf.edu/models.html).
#
# Vorhandene, unveraenderte Dateien werden nicht erneut geladen (Pruefsumme).
# build_app.sh ruft dieses Skript auf; im Terminal-Betrieb genuegt ein
# einmaliger Aufruf, danach laeuft die Analyse ohne Netzwerk.

cd "$(dirname "$0")" || exit 1
set -e

DEST="vendor/models/essentia"
BASE="https://essentia.upf.edu/models"
mkdir -p "$DEST"

# Pfade relativ zu $BASE
FILES=(
  "feature-extractors/discogs-effnet/discogs-effnet-bsdynamic-1.onnx"
  "feature-extractors/discogs-effnet/discogs-effnet-bsdynamic-1.json"
  "classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.onnx"
  "classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1.json"
  "classification-heads/mood_happy/mood_happy-discogs-effnet-1.onnx"
  "classification-heads/mood_happy/mood_happy-discogs-effnet-1.json"
  "classification-heads/mood_party/mood_party-discogs-effnet-1.onnx"
  "classification-heads/mood_party/mood_party-discogs-effnet-1.json"
  "classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.onnx"
  "classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1.json"
  "classification-heads/mood_sad/mood_sad-discogs-effnet-1.onnx"
  "classification-heads/mood_sad/mood_sad-discogs-effnet-1.json"
  "classification-heads/danceability/danceability-discogs-effnet-1.onnx"
  "classification-heads/danceability/danceability-discogs-effnet-1.json"
  "classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1.onnx"
  "classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1.json"
)

SUMS="$DEST/SHA256SUMS"
for rel in "${FILES[@]}"; do
  name="$(basename "$rel")"
  target="$DEST/$name"
  if [ -s "$target" ] && [ -f "$SUMS" ] && grep -q " $name\$" "$SUMS"; then
    expected="$(grep " $name\$" "$SUMS" | cut -d' ' -f1)"
    actual="$(shasum -a 256 "$target" | cut -d' ' -f1)"
    if [ "$expected" = "$actual" ]; then
      continue
    fi
    echo "Prüfsumme weicht ab, lade neu: $name"
  fi
  echo "Lade $name …"
  # Der Server bricht grosse Downloads gelegentlich ab -- weiterladen statt
  # von vorn (-C -), auch ueber mehrere Versuche hinweg.
  for attempt in 1 2 3 4 5 6; do
    if curl -fsSL -C - --speed-limit 2000 --speed-time 60 \
         -o "$target.part" "$BASE/$rel"; then
      break
    fi
    [ "$attempt" = 6 ] && { echo "FEHLER: $name ließ sich nicht laden."; exit 1; }
    echo "  Abbruch, setze fort (Versuch $((attempt + 1))) …"
    sleep 2
  done
  mv "$target.part" "$target"
done

# Pruefsummen festhalten -- ein spaeterer Lauf erkennt so beschaedigte Dateien.
( cd "$DEST" && shasum -a 256 *.onnx *.json > SHA256SUMS )
echo "Modelle liegen in $DEST ($(du -sh "$DEST" | cut -f1))."
