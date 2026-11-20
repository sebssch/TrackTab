# TrackTab — Drittanbieter-Lizenzen

TrackTab selbst steht unter der **GNU General Public License, Version 3 oder später** ([LICENSE](LICENSE), Hinweise und Geltungsbereich in [NOTICE.md](NOTICE.md)). Diese Datei listet alle Programme, Bibliotheken und Modelle von Dritten, die TrackTab nutzt oder mitliefert, mit ihrer Lizenz und einer Quelle. Die Lizenztexte der Komponenten liegen im Ordner [licenses/](licenses/) (erzeugt mit `collect_licenses.py`); maßgeblich sind jeweils die von den Projekten veröffentlichten Texte.

> Stand: November 2026. Die Lizenzangaben der Python-Pakete stammen aus deren Paket-Metadaten (installierte Versionen unten), die der FFmpeg-Bibliotheken aus dem mitgelieferten Build bzw. den üblichen Projektangaben. Vor jedem Release gegenprüfen.

**Kurz gefasst**
- **Nicht-kommerziell:** Die Modelle für Stem-Trennung (Demucs) und für Genre/Stimmung/Energie (Essentia) dürfen nur nicht-kommerziell genutzt werden. TrackTab darf mit ihnen weder verkauft noch kommerziell eingesetzt werden.
- **GPL:** Der mitgelieferte FFmpeg-Build (GPL-3.0-oder-später, mit x264/x265) und die Python-Bibliothek mutagen (GPL-2.0-oder-später) sind Copyleft; ebenso stammen Teile von TrackTabs eigenem Code aus rbxport (GPL-2.0-oder-später) und die Phrasenerkennung samt Modell aus GlideMix (GPL, siehe Abschnitte 1 und 4). Wer die `.dmg` weitergibt, gibt damit GPL-Software weiter und muss deren Bedingungen erfüllen (Lizenztext, Quellcode am selben Release, siehe unten).
- Alles übrige steht unter freizügigen Lizenzen (MIT, BSD, Apache-2.0, ISC, PSF-2.0, zlib) oder schwachem Copyleft (LGPL, MPL-2.0) und ist mit der GPL-3.0 vereinbar.

---

## 1. Modelle (werden nicht im Repository mitgeliefert)

Die Essentia-Gewichte lädt `fetch_models.sh` beim Einrichten nach `vendor/models/` (Ordner ist von Git ausgeschlossen); die `.dmg` kann sie enthalten. Das Demucs-Modell liefert TrackTab nicht mit, der Nutzer lädt es in den Einstellungen. Das Phrasenmodell holt `fetch_models.sh` aus dem Quellarchiv von GlideMix (Prüfsumme festgeschrieben) nach `vendor/models/phrases/`; es kommt in die `.dmg`, wenn es dort liegt.

| Modell | Verwendung | Lizenz | Quelle |
|---|---|---|---|
| **Demucs v4 (htdemucs)**, Meta / A. Défossez et al. | Stem-Trennung (Acapella/Instrumental) | Code MIT. **Wird nicht mit TrackTab ausgeliefert**, sondern vom Nutzer in den Einstellungen geladen. Gewichte: laut der ONNX-Quelle nicht-kommerziell, **CC BY-NC 4.0** (das Meta-Repository selbst nennt für die Gewichte keine Lizenz; es ist seit Januar 2025 archiviert) | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) · ONNX-Export: [HRSadeghi/demucs-onnx](https://huggingface.co/HRSadeghi/demucs-onnx) · [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.de) |
| **Essentia-Modelle** (discogs-effnet, mood_*, danceability, mtg_jamendo_moodtheme), Music Technology Group, Universitat Pompeu Fabra | Genre, Stimmung, Energie | **CC BY-NC-SA 4.0** (nicht-kommerziell, Weitergabe unter gleichen Bedingungen) | [essentia.upf.edu/models](https://essentia.upf.edu/models.html) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.de) |
| **Phrasenmodell** (`vendor/models/phrases/phrases.onnx`, in GlideMix `src/mixhouse/models/glidemix_phrases.onnx`), GlideMix-Autoren | Phrasen in der DJ-Ansicht (Testfunktion, `app/phrases.py`) | **GPL** als Teil von GlideMix 1.3.0 (Gesamtwerk GPL-3.0, aufgebaut auf Audacity; für Dateien ohne eigene Angabe nennt dessen `LICENSE.txt` GPL-2.0-oder-später). Unverändert übernommen, SHA-256 `a0c0b5d9…81b89b`. Das Trainingsskript (`tools/trainer/glidemix_trainer.py`) liegt im selben Quellarchiv. Der Name „GlideMix“ und das Logo sind von der GPL ausgenommen; TrackTab nennt GlideMix nur als Quelle. | [glidemix.com](https://glidemix.com) · Quellcode: am Release (siehe Abschnitt 2), Original <https://github.com/cristitm76/glidemix-releases/releases/tag/v1.3.0> |

Namensnennung: *Demucs © Meta Platforms, Inc. and affiliates; ONNX-Export von HRSadeghi. Essentia-Modelle © Music Technology Group, Universitat Pompeu Fabra.* TrackTab verändert die Modelle nicht; es führt sie nur aus.

## 2. Mitgelieferte Programme und Bibliotheken (`vendor/`)

### FFmpeg 9.0.2 / ffprobe — **GPL-3.0-oder-später**

Der mitgelieferte Build stammt aus Homebrew und ist mit `--enable-gpl --enable-version3` gebaut (u. a. libx264, libx265, libmp3lame, libopus, libvpx, libdav1d, libSvtAv1, libvmaf). Damit gilt für die mitgelieferten FFmpeg-Programme und -Bibliotheken die GNU General Public License, Version 3 oder später. TrackTab ruft ffmpeg/ffprobe nur als eigenständige Programme auf (eigener Prozess).

- Projekt und Lizenzhinweise: <https://ffmpeg.org/legal.html>
- GPL-3.0-Text: <https://www.gnu.org/licenses/gpl-3.0.html>
- Quellcode zu diesem Build: am Release (siehe unten), Original <https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz>; Build-Rezept (Homebrew-Formel `ffmpeg`): <https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/f/ffmpeg.rb>

### LAME 4.0 (Programm `vendor/lame`) — **LGPL-2.0-oder-später**

Der Konverter kodiert MP3 mit 320 kbit/s über das eigenständige Programm `lame` (eigener Prozess, unverändert aus der Homebrew-Formel `lame`, gleiche Version wie die in FFmpeg eingebundene `libmp3lame`). Es ist gegen die ohnehin mitgelieferte `libmpg123` gelinkt; im Programm selbst wurde nur der Ladepfad dieser Bibliothek auf `@executable_path/libs/` umgestellt, der Quellcode ist nicht verändert. *Dieses Produkt enthält LAME, ein MP3-Kodierer des LAME-Projekts: <https://lame.sourceforge.io>.*

- Lizenztext: `licenses/ffmpeg-libs/lame/` (`COPYING` = LGPL, `LICENSE` = Hinweise des Projekts)
- Quellcode: `lame-4.0.tar.gz` im `third-party-sources.zip` am Release, Original <https://downloads.sourceforge.net/project/lame/lame/4.0/lame-4.0.tar.gz>
- Ohne `lame` kodiert TrackTab über ffmpeg weiter (mit festem Lowpass von etwa 20 kHz bei 320 kbit/s).

Mitgelieferte Bibliotheken in `vendor/libs/` (Lizenzen laut Projektangaben):

| Bibliothek | Lizenz | Quelle |
|---|---|---|
| libavcodec, libavformat, libavutil, libavfilter, libavdevice, libswresample, libswscale | GPL-3.0+ (in diesem Build) | <https://ffmpeg.org> |
| libx264 | GPL-2.0+ | <https://www.videolan.org/developers/x264.html> |
| libx265 | GPL-2.0+ | <https://www.videolan.org/developers/x265.html> |
| libmp3lame (LAME), dazu das Programm `vendor/lame` (gleiche Version, Konverter) | LGPL-2.0+ | <https://lame.sourceforge.io> |
| libmpg123 | LGPL-2.1 | <https://www.mpg123.de> |
| libopus | BSD-3-Clause | <https://opus-codec.org> |
| libvpx | BSD-3-Clause | <https://www.webmproject.org> |
| libdav1d | BSD-2-Clause | <https://code.videolan.org/videolan/dav1d> |
| libSvtAv1Enc | BSD-3-Clause-Clear (mit AOM-Patentlizenz) | <https://gitlab.com/AOMediaCodec/SVT-AV1> |
| libvmaf | BSD-2-Clause-Patent | <https://github.com/Netflix/vmaf> |
| OpenSSL (libssl, libcrypto) 4.x | Apache-2.0 | <https://www.openssl.org> |
| liblzma (xz) | 0BSD / gemeinfrei | <https://tukaani.org/xz/> |

**Quellcode-Zugang (GPL/LGPL):** Zu jeder `.dmg` gehört der Quellcode der darin enthaltenen GPL-/LGPL-Komponenten genau dieser Versionen. Er liegt als Download am **selben GitHub-Release** wie die `.dmg`, zusammengefasst in `TrackTab-<Version>-third-party-sources.zip` (darin `ffmpeg-<Version>.tar.xz`, `x264-<Commit>.tar.bz2`, `x265_<Version>.tar.gz`, `lame-<Version>.tar.gz`, `mpg123-<Version>.tar.bz2`, `mutagen-<Version>.tar.gz`, `GlideMix-<Version>-source.tar.xz` (nur wenn das Phrasenmodell im Build steckt), für `libquadmath` aus scipy `gcc-<Version>.tar.xz` samt dem Apple-Silicon-Patch und der Homebrew-Bauanleitung, mit der dessen gfortran gebaut ist (`gcc-<Version>-darwin.diff`, `gcc@<Major>.rb`), dazu `BUILD-INFO.txt` mit der FFmpeg-Konfiguration, `SHA256SUMS` und die Lizenztexte). Die Datei erstellt `package_sources.sh`. Der Quellcode von TrackTab selbst liegt im Repository. Die Adressen der Originalprojekte in der Tabelle sind nur zusätzliche Information. Die übrigen Bibliotheken stehen unter freizügigen Lizenzen ohne Pflicht zur Quellcode-Herausgabe.

## 3. Python-Pakete (in der App gebündelt)

| Paket | Version | Lizenz | Verwendung |
|---|---|---|---|
| mutagen | 1.48.1 | **GPL-2.0-oder-später** | Tags und Cover lesen/schreiben, MP3-Details — Quellcode: am Release (siehe oben), Original <https://github.com/quodlibet/mutagen> · <https://pypi.org/project/mutagen/1.48.1/> |
| numpy | 2.5.5 | BSD-3-Clause (u. a. 0BSD, MIT, Zlib, CC0 für Teile) | FFT, Stem-Trennung |
| onnxruntime | 1.32.0 | MIT | Ausführung der Modelle |
| libsonare | 1.10.0 | Apache-2.0 | BPM- und Tonart-Messung |
| pyrekordbox | 0.5.0 | MIT | Rekordbox-Datenbank |
| Pillow | 13.0.0 | MIT-CMU | Cover-Prüfung |
| PyYAML | 6.0.3 | MIT | Konfiguration |
| rich | 15.0.0 | MIT | Terminal-Ausgabe |
| PyObjC (Cocoa, Core) | 13.0 | MIT | macOS-App-Hülle |
| LightGBM | 4.7.0 | MIT | Auto-Cue-Training in der App (nur beim Training geladen; Auto Cue selbst wertet die Bäume ohne LightGBM aus, `app/autocue/trees.py`) |
| SciPy (nur `scipy.sparse` und Grundmodule) | 1.18.1 | BSD-3-Clause; BLAS/LAPACK über Apples Accelerate (Teil von macOS, nicht gebündelt). Gebündelt ist die GCC-Laufzeit aus gfortran 13.4.0: `libgfortran`/`libgcc_s` (GPL-3.0 mit GCC-Runtime-Ausnahme) und **`libquadmath` (LGPL-2.1-oder-später)** — Quellcode: am Release (siehe Abschnitt 2), Texte in `licenses/python/scipy-<Version>/LICENSE.txt` | Abhängigkeit von LightGBM |
| narwhals | 2.29.0 | MIT | Abhängigkeit von LightGBM |

LightGBM braucht die OpenMP-Laufzeit **libomp** (LLVM-Projekt, Homebrew-Formel `libomp`), die über den Ladepfad von `lib_lightgbm.dylib` mit ins Bundle kommt: Apache-2.0 mit LLVM-Ausnahme, Text in [licenses/native/libomp/](licenses/native/libomp/), Quelle <https://openmp.llvm.org>. Sie wird nur beim Training geladen; ist das macOS älter als das, für das diese Bibliothek gebaut ist, bietet die App kein Training an.

Dazu kommen mitgebündelte Abhängigkeiten dieser Pakete (u. a. SQLAlchemy, sqlcipher3-wheels, construct, bidict, packaging, protobuf, flatbuffers, psutil, Pygments, markdown-it-py, mdurl, python-dateutil, six, typing_extensions, tzdata) mit ihren Lizenztexten in [licenses/python/](licenses/python/). Vier davon verdienen einen eigenen Hinweis:

- **bidict** steht unter der **MPL-2.0** (schwaches Copyleft, mit der GPL-3.0 vereinbar). TrackTab nutzt es unverändert; Quellcode: <https://github.com/jab/bidict>.
- **sqlcipher3-wheels** (zlib-Lizenz) enthält fest eingebunden **SQLCipher** (Zetetic LLC, BSD-3-Clause, Text in [licenses/other/](licenses/other/sqlcipher-LICENSE.md)) und **OpenSSL 3.x** (Apache-2.0, derselbe Text wie in `licenses/ffmpeg-libs/openssl@4/`).
- **onnxruntime** bringt die Hinweise zu seinen eigenen Bestandteilen in `ThirdPartyNotices.txt` mit (liegt neben der Lizenz in `licenses/python/`).
- **Pillow** bringt eigene native Bibliotheken mit (u. a. libjpeg, libpng, libtiff, libwebp, libavif, FreeType, HarfBuzz, Little CMS, OpenJPEG, Brotli, zlib-ng, zstd, libxcb); deren Lizenztexte stehen gesammelt in `licenses/python/pillow-<Version>/LICENSE`.

Texte, die ein Paket nicht selbst mitbringt (flatbuffers, SQLCipher), liegen von Hand gepflegt in [licenses/other/](licenses/other/); `collect_licenses.py` legt sie zum jeweiligen Paket.

Die Laufzeit des Pakets (CPython) steht unter der PSF-Lizenz 2.0 (Text samt Copyright-Vermerken in `licenses/python/CPython-<Version>/`). Sie stammt aus Homebrew (`python@3.14`) und bringt drei Bibliotheken mit, die nicht in CPythons eigenem Lizenztext stehen: **mpdecimal** (BSD-2-Clause, Modul `decimal`, Text in [licenses/native/mpdecimal/](licenses/native/mpdecimal/)), **zstd** (BSD-3-Clause, wahlweise GPL-2.0, Modul `compression.zstd`, Text in [licenses/native/zstd/](licenses/native/zstd/)) und **SQLite** (gemeinfrei, Modul `sqlite3`, <https://sqlite.org/copyright.html>). Zum Bündeln dient **PyInstaller** (GPL-2.0-oder-später mit Ausnahme, die die Weitergabe der damit gebauten Programme erlaubt; reines Build-Werkzeug, nicht Teil der App).

## 4. Oberfläche und übernommener Code

| Bestandteil | Lizenz | Quelle |
|---|---|---|
| Lucide-Icons (`app/webui/lucide-icons.js`) | ISC (Text in [licenses/ui/](licenses/ui/lucide-LICENSE.txt)) | <https://lucide.dev> · <https://github.com/lucide-icons/lucide/blob/main/LICENSE> |
| Spektralmerkmale für Auto Cue (`app/autocue/dsp.py`), Nachbau von Funktionen aus librosa 0.11 (STFT, Spektralform, Onset, Mel, Chroma mit Stimmungsschätzung); librosa selbst ist nicht gebündelt | ISC (Text in [licenses/ui/](licenses/ui/librosa-LICENSE.md)) | <https://github.com/librosa/librosa> |
| Phrasenerkennung (`app/phrases.py`), Python-Nachbau von `src/mixhouse/mixhousephrasemodel.cpp` und `mixhousephrasedetector.cpp` aus GlideMix 1.3.0 (Copyright der GlideMix-Autoren; Eingangswerte und Schwellwerte wie im Original, damit das Modell dieselben Zahlen sieht). Weitergabe unter GPL-3.0-oder-später. | GPL (GlideMix: Gesamtwerk GPL-3.0, Dateien ohne eigene Angabe GPL-2.0-oder-später) | <https://github.com/cristitm76/glidemix-releases> |
| Beatgrid-Analyse (`app/beatgrid.py`) und Rekordbox-Analysedateien (`app/anlzwrite.py`), Python-Nachbauten von `crates/rbl-analysis` und `crates/rbl-anlz` aus rbxport (Copyright der rbxport-Autoren; Schwellwerte und Konstanten unverändert übernommen, Änderungen in den Dateiköpfen beschrieben). TrackTab gibt diese Teile nach der „oder später“-Klausel unter GPL-3.0-oder-später weiter. | GPL-2.0-oder-später (Text in [licenses/gnu/](licenses/gnu/COPYING.GPLv2)) | <https://github.com/chrisle/rbxport> |

## 5. Hinweise

- Die Modell-Einordnung (insbesondere Demucs-Gewichte) beruht auf den Angaben der jeweiligen Quellseiten und ist nicht rechtlich geprüft. Wer TrackTab weitergibt oder gewerblich einsetzen will, sollte sie vorher selbst klären.
- Die genannten Namen und Marken gehören ihren jeweiligen Inhabern.
