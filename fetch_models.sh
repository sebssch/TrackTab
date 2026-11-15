#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — Modelle laden (Genre/Stimmung/Energie, Phrasen) ║
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

# ── Phrasenmodell (Beatgrid & Phrasen, app/phrases.py) ─────────────────────
# Unveraendert aus GlideMix (glidemix.com, GPL): im Quellarchiv des Releases
# als src/mixhouse/models/glidemix_phrases.onnx. Einzeln gibt es die Datei
# nicht, deshalb wird das Archiv (~70 MB) einmalig geladen und nur das Modell
# (1,2 MB) herausgezogen. Die Pruefsumme ist festgeschrieben: app/phrases.py
# rechnet genau die Eingangswerte dieser Fassung. Wer GLIDEMIX_VERSION aendert,
# passt die Pruefsumme an und prueft phrases.py gegen den neuen Quellcode.
# package_sources.sh liest GLIDEMIX_VERSION von hier (Quellcode zum Release).
# Ein Fehler bricht nichts ab: ohne Modell blendet die App die Funktion aus.
GLIDEMIX_VERSION="1.3.0"
PHRASES_SHA256="a0c0b5d90d8f3899c29499b0d080334f1efbeebdb4578f3780f0cfd91181b89b"
PHRASES_DEST="vendor/models/phrases"
PHRASES_FILE="$PHRASES_DEST/phrases.onnx"

fetch_phrases() {
  if [ -s "$PHRASES_FILE" ]; then
    if [ "$(shasum -a 256 "$PHRASES_FILE" | cut -d' ' -f1)" = "$PHRASES_SHA256" ]; then
      return 0
    fi
    echo "Hinweis: $PHRASES_FILE ist nicht das Modell aus GlideMix $GLIDEMIX_VERSION — bleibt unverändert."
    echo "         Herkunft und Lizenz dieser Datei vor einer Weitergabe selbst klären."
    return 0
  fi
  local archive="GlideMix-${GLIDEMIX_VERSION}-source.tar.xz"
  local url="https://github.com/cristitm76/glidemix-releases/releases/download/v${GLIDEMIX_VERSION}/${archive}"
  local tmp
  tmp="$(mktemp -d)" || return 1
  echo "Lade Phrasenmodell aus $archive (~70 MB) …"
  # Liegt das Archiv schon fuer das Release bereit, kein zweiter Download.
  if [ -s "dist/sources/$archive" ]; then
    cp "dist/sources/$archive" "$tmp/$archive"
  elif ! curl -fsSL -o "$tmp/$archive" "$url"; then
    rm -rf "$tmp"; return 1
  fi
  if ! tar -xJf "$tmp/$archive" -C "$tmp" \
       "GlideMix-${GLIDEMIX_VERSION}-source/src/mixhouse/models/glidemix_phrases.onnx"; then
    rm -rf "$tmp"; return 1
  fi
  local got="$tmp/GlideMix-${GLIDEMIX_VERSION}-source/src/mixhouse/models/glidemix_phrases.onnx"
  if [ "$(shasum -a 256 "$got" | cut -d' ' -f1)" != "$PHRASES_SHA256" ]; then
    echo "FEHLER: Prüfsumme des Phrasenmodells stimmt nicht — nicht übernommen."
    rm -rf "$tmp"; return 1
  fi
  mkdir -p "$PHRASES_DEST"
  mv "$got" "$PHRASES_FILE"
  ( cd "$PHRASES_DEST" && shasum -a 256 phrases.onnx > SHA256SUMS )
  rm -rf "$tmp"
  echo "Phrasenmodell liegt in $PHRASES_FILE."
}
fetch_phrases || echo "Hinweis: Phrasenmodell ließ sich nicht laden — die App kommt ohne Phrasenerkennung."

# Stem-Trennung (Demucs): bewusst NICHT hier -- die Gewichte werden nicht
# mit der App ausgeliefert, der Nutzer laedt sie in den Einstellungen
# (app/stems.py, Lizenz siehe THIRD_PARTY_LICENSES.md).
