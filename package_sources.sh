#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — Quellcode der GPL-/LGPL-Teile fuer ein Release     ║
# ╚══════════════════════════════════════════════════════════════╝
# Die mitgelieferten Programme/Bibliotheken in vendor/ (FFmpeg, x264, x265,
# LAME, mpg123) stehen unter GPL bzw. LGPL. Wer sie weitergibt, muss den
# Quellcode GENAU dieser Versionen bereitstellen (siehe THIRD_PARTY_LICENSES.md).
# Dieses Skript laedt die Quell-Archive nach dist/sources/ -- sie gehoeren als
# zusaetzliche Dateien an dasselbe GitHub-Release wie die .dmg.
#
# Die Versionen unten muessen zu vendor/ passen. Vor jedem Release pruefen:
#   vendor/ffmpeg -version | head -1      (FFmpeg-Version)
#   strings vendor/libs/libmp3lame.0.dylib | grep LAME
#   strings vendor/libs/libmpg123.0.dylib | grep -m1 '1\.[0-9]*\.[0-9]'
#   brew list --versions x264 x265        (x264/x265 tragen keine Version im Binary;
#                                          Build-Nummer: libx264.165 / libx265.217)
# Wurde vendor/ aus einer anderen Homebrew-Version gebaut, hier anpassen.

cd "$(dirname "$0")" || exit 1
set -e

FFMPEG_VERSION="9.0.2"
X264_COMMIT="b35605ace3ddf7c1a5d67a2eb553f034aef41d55"   # Homebrew-Formel x264 r3222
X265_VERSION="4.3"
LAME_VERSION="4.0"
MPG123_VERSION="1.33.7"

DEST="dist/sources"
mkdir -p "$DEST"

# name|url
SOURCES=(
  "ffmpeg-${FFMPEG_VERSION}.tar.xz|https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
  "x264-${X264_COMMIT}.tar.bz2|https://code.videolan.org/videolan/x264/-/archive/${X264_COMMIT}/x264-${X264_COMMIT}.tar.bz2"
  "x265_${X265_VERSION}.tar.gz|https://github.com/Multicorewareinc/x265/releases/download/${X265_VERSION}/x265_${X265_VERSION}.tar.gz"
  "lame-${LAME_VERSION}.tar.gz|https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz"
  "mpg123-${MPG123_VERSION}.tar.bz2|https://www.mpg123.de/download/mpg123-${MPG123_VERSION}.tar.bz2"
)

for entry in "${SOURCES[@]}"; do
  name="${entry%%|*}"
  url="${entry#*|}"
  if [ -s "$DEST/$name" ]; then
    echo "vorhanden: $name"
    continue
  fi
  echo "Lade $name …"
  curl -fsSL -o "$DEST/$name.part" "$url"
  mv "$DEST/$name.part" "$DEST/$name"
done

# Beschreibung des Builds und Pruefsummen -- gehoert mit ins Release.
{
  echo "TrackTab — Quellcode der GPL-/LGPL-Teile dieses Releases"
  echo "Stand: $(date +%Y-%m-%d)"
  echo
  echo "Mitgelieferte FFmpeg-Version und Konfiguration (vendor/ffmpeg):"
  if [ -x vendor/ffmpeg ]; then
    vendor/ffmpeg -version | sed -n '1p;/^configuration:/p'
  else
    echo "(vendor/ffmpeg nicht gefunden)"
  fi
  echo
  echo "Mitgelieferte Bibliotheken (vendor/libs):"
  ls -1 vendor/libs 2>/dev/null | sed 's/^/  /'
  echo
  echo "Lizenzen: siehe LICENSE, NOTICE.md und THIRD_PARTY_LICENSES.md."
} > "$DEST/BUILD-INFO.txt"
cp LICENSE NOTICE.md THIRD_PARTY_LICENSES.md "$DEST/"
( cd "$DEST" && shasum -a 256 *.tar.* > SHA256SUMS )

echo "Fertig: $DEST ($(du -sh "$DEST" | cut -f1)). Diese Dateien an das GitHub-Release hängen."
