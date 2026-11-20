#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  TrackTab — Quellcode der GPL-/LGPL-Teile fuer ein Release     ║
# ╚══════════════════════════════════════════════════════════════╝
# Die mitgelieferten Programme/Bibliotheken in vendor/ (FFmpeg, x264, x265,
# LAME, mpg123) stehen unter GPL bzw. LGPL. Wer sie weitergibt, muss den
# Quellcode GENAU dieser Versionen bereitstellen (siehe THIRD_PARTY_LICENSES.md).
# Das gilt auch fuer mutagen (GPL), das als Python-Paket in der App steckt, und
# fuer das Phrasenmodell aus GlideMix (GPL, vendor/models/phrases/phrases.onnx):
# steckt es im Build, gehoert das GlideMix-Quellarchiv dazu (samt Trainingsskript).
# Ebenso fuer libquadmath (LGPL-2.1+), die scipy (Abhaengigkeit von LightGBM) mit
# seiner GCC-Laufzeit mitbringt: dazu gehoeren die GCC-Quellen samt dem Darwin-
# Patch, mit dem Homebrew gcc@13 gebaut wird (scipys arm64-Wheels nehmen dessen
# gfortran-13, siehe .github/workflows/wheels.yml im scipy-Repository).
# Dieses Skript laedt die Quell-Archive nach dist/sources/ und packt sie samt
# Lizenztexten in dist/TrackTab-<Version>-third-party-sources.zip -- diese eine
# Datei gehoert an dasselbe GitHub-Release wie die .dmg.
#
# Die Versionen unten muessen zu vendor/ passen. Vor jedem Release pruefen:
#   vendor/ffmpeg -version | head -1      (FFmpeg-Version)
#   strings vendor/libs/libmp3lame.0.dylib | grep LAME
#   vendor/lame --version | head -1       (Konverter-Programm, gleiche LAME-Version)
#   strings vendor/libs/libmpg123.0.dylib | grep -m1 '1\.[0-9]*\.[0-9]'
#   brew list --versions x264 x265        (x264/x265 tragen keine Version im Binary;
#                                          Build-Nummer: libx264.165 / libx265.217)
# Wurde vendor/ aus einer anderen Homebrew-Version gebaut, hier anpassen.
#
# GCC (libquadmath aus scipy): Die Version liest das Skript aus scipys Build-
# Konfiguration und bricht ab, wenn sie nicht zu GCC_VERSION passt. Bei einer
# neuen scipy-Version also pruefen, mit welchem gfortran sie gebaut ist, und
# GCC_PATCH_COMMIT auf den homebrew-core-Stand von gcc@<Major>.rb zum
# Upload-Datum des Wheels setzen (welche Patches die Formel damals hatte).

cd "$(dirname "$0")" || exit 1
set -e

FFMPEG_VERSION="9.0.2"
X264_COMMIT="b35605ace3ddf7c1a5d67a2eb553f034aef41d55"   # Homebrew-Formel x264 r3222
X265_VERSION="4.3"
LAME_VERSION="4.0"
MPG123_VERSION="1.33.7"
# scipy 1.18.1 (macosx_14_0_arm64, hochgeladen 2026-08-21): gfortran 13.4.0 aus
# Homebrew gcc@13, Formel-Stand d17f3939 (einziger Patch: gcc-13.4.0.diff).
GCC_VERSION="13.4.0"
GCC_PATCH_COMMIT="d17f3939dc55c4a42a6e5833a8353d0948bbd416"
# Aus dem .venv, damit es immer die gebuendelte Version ist.
MUTAGEN_VERSION=$(./.venv/bin/python -c "import importlib.metadata as m; print(m.version('mutagen'))")
# Einzige Quelle der GlideMix-Version ist fetch_models.sh (dort haengt auch die Pruefsumme).
GLIDEMIX_VERSION=$(sed -n 's/^GLIDEMIX_VERSION="\(.*\)"$/\1/p' fetch_models.sh)
TRACKTAB_VERSION=$(./.venv/bin/python -c "from app import __version__; print(__version__)")

DEST="dist/sources"
mkdir -p "$DEST"

# name|url
SOURCES=(
  "ffmpeg-${FFMPEG_VERSION}.tar.xz|https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
  "x264-${X264_COMMIT}.tar.bz2|https://code.videolan.org/videolan/x264/-/archive/${X264_COMMIT}/x264-${X264_COMMIT}.tar.bz2"
  "x265_${X265_VERSION}.tar.gz|https://github.com/Multicorewareinc/x265/releases/download/${X265_VERSION}/x265_${X265_VERSION}.tar.gz"
  "lame-${LAME_VERSION}.tar.gz|https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz"
  "mpg123-${MPG123_VERSION}.tar.bz2|https://www.mpg123.de/download/mpg123-${MPG123_VERSION}.tar.bz2"
  "mutagen-${MUTAGEN_VERSION}.tar.gz|https://files.pythonhosted.org/packages/source/m/mutagen/mutagen-${MUTAGEN_VERSION}.tar.gz"
)

# GlideMix nur, wenn das Phrasenmodell wirklich mit ausgeliefert wird.
if [ -s vendor/models/phrases/phrases.onnx ]; then
  if [ -z "$GLIDEMIX_VERSION" ]; then
    echo "FEHLER: GLIDEMIX_VERSION nicht in fetch_models.sh gefunden."; exit 1
  fi
  SOURCES+=("GlideMix-${GLIDEMIX_VERSION}-source.tar.xz|https://github.com/cristitm76/glidemix-releases/releases/download/v${GLIDEMIX_VERSION}/GlideMix-${GLIDEMIX_VERSION}-source.tar.xz")
else
  echo "Hinweis: kein Phrasenmodell in vendor/ — GlideMix-Quellcode wird nicht beigelegt."
fi

# GCC nur, wenn scipy (und damit libquadmath) im .venv steckt -- tracktab.spec
# buendelt es dann ueber LightGBM mit.
SCIPY_QUADMATH=$(ls ./.venv/lib/python3.*/site-packages/scipy/.dylibs/libquadmath* 2>/dev/null | head -1 || true)
if [ -n "$SCIPY_QUADMATH" ]; then
  SCIPY_GCC=$(./.venv/bin/python -c "import scipy.__config__ as c; print(c.CONFIG['Compilers']['fortran']['version'])")
  if [ "$SCIPY_GCC" != "$GCC_VERSION" ]; then
    echo "FEHLER: scipy ist mit gfortran $SCIPY_GCC gebaut, GCC_VERSION ist $GCC_VERSION — oben anpassen."
    exit 1
  fi
  GCC_MAJOR="${GCC_VERSION%%.*}"
  SOURCES+=(
    "gcc-${GCC_VERSION}.tar.xz|https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/gcc-${GCC_VERSION}.tar.xz"
    "gcc-${GCC_VERSION}-darwin.diff|https://raw.githubusercontent.com/Homebrew/homebrew-core/${GCC_PATCH_COMMIT}/Patches/gcc/gcc-${GCC_VERSION}.diff"
    "gcc@${GCC_MAJOR}.rb|https://raw.githubusercontent.com/Homebrew/homebrew-core/${GCC_PATCH_COMMIT}/Formula/g/gcc@${GCC_MAJOR}.rb"
  )
else
  echo "Hinweis: kein scipy im .venv — GCC-Quellcode (libquadmath) wird nicht beigelegt."
fi

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
  if [ -s vendor/models/phrases/phrases.onnx ]; then
    echo "Phrasenmodell (vendor/models/phrases/phrases.onnx) aus GlideMix ${GLIDEMIX_VERSION}:"
    echo "  SHA-256 $(shasum -a 256 vendor/models/phrases/phrases.onnx | cut -d' ' -f1)"
    echo "  im Quellarchiv: src/mixhouse/models/glidemix_phrases.onnx, Training: tools/trainer/"
    echo
  fi
  if [ -n "$SCIPY_QUADMATH" ]; then
    echo "GCC-Laufzeit aus scipy (libquadmath LGPL-2.1+, libgfortran/libgcc_s GPL-3.0 mit"
    echo "GCC-Runtime-Ausnahme), gebaut mit gfortran ${GCC_VERSION} (Homebrew gcc@${GCC_MAJOR}):"
    echo "  gcc-${GCC_VERSION}.tar.xz        GNU-Quellen (libquadmath/, libgfortran/, libgcc/)"
    echo "  gcc-${GCC_VERSION}-darwin.diff   Patch fuer Apple Silicon (https://github.com/iains/gcc-${GCC_MAJOR}-branch)"
    echo "  gcc@${GCC_MAJOR}.rb              Homebrew-Bauanleitung, Stand ${GCC_PATCH_COMMIT:0:12}"
    echo
  fi
  echo "Lizenzen: siehe LICENSE, NOTICE.md und THIRD_PARTY_LICENSES.md."
} > "$DEST/BUILD-INFO.txt"
cp LICENSE NOTICE.md THIRD_PARTY_LICENSES.md "$DEST/"
( cd "$DEST" && shasum -a 256 *.tar.* $(ls *.diff *.rb 2>/dev/null) > SHA256SUMS )

ZIP="TrackTab-${TRACKTAB_VERSION}-third-party-sources.zip"
rm -f "dist/$ZIP"
( cd dist && zip -qrX "$ZIP" sources -x '*/.DS_Store' )

echo "Fertig: dist/$ZIP ($(du -sh "dist/$ZIP" | cut -f1)). Diese Datei an das GitHub-Release hängen."
