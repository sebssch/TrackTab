# TrackTab — Drittanbieter-Lizenzen

TrackTab selbst steht unter der **GNU General Public License, Version 3 oder später** ([LICENSE](LICENSE), Hinweise und Geltungsbereich in [NOTICE.md](NOTICE.md)). Diese Datei listet alle Programme, Bibliotheken und Modelle von Dritten, die TrackTab nutzt oder mitliefert, mit ihrer Lizenz und einer Quelle. Die Lizenztexte der Komponenten liegen im Ordner [licenses/](licenses/) (erzeugt mit `collect_licenses.py`); maßgeblich sind jeweils die von den Projekten veröffentlichten Texte.

> Stand: Oktober 2026. Die Lizenzangaben der Python-Pakete stammen aus deren Paket-Metadaten (installierte Versionen unten), die der FFmpeg-Bibliotheken aus dem mitgelieferten Build bzw. den üblichen Projektangaben. Vor jedem Release gegenprüfen.

**Kurz gefasst**
- **Nicht-kommerziell:** Die Modelle für Stem-Trennung (Demucs) und für Genre/Stimmung/Energie (Essentia) dürfen nur nicht-kommerziell genutzt werden. TrackTab darf mit ihnen weder verkauft noch kommerziell eingesetzt werden.
- **GPL:** Der mitgelieferte FFmpeg-Build (GPL-3.0-oder-später, mit x264/x265) und die Python-Bibliothek mutagen (GPL-2.0-oder-später) sind Copyleft. Wer die `.dmg` weitergibt, gibt damit GPL-Software weiter und muss deren Bedingungen erfüllen (Lizenztext, Quellcode am selben Release, siehe unten).
- Alles übrige steht unter freizügigen Lizenzen (MIT, BSD, Apache-2.0, ISC, LGPL) und ist mit der GPL-3.0 vereinbar.

---

## 1. Modelle (werden nicht im Repository mitgeliefert)

Die Essentia-Gewichte lädt `fetch_models.sh` beim Einrichten nach `vendor/models/` (Ordner ist von Git ausgeschlossen); die `.dmg` kann sie enthalten. Das Demucs-Modell liefert TrackTab nicht mit, der Nutzer lädt es in den Einstellungen.

| Modell | Verwendung | Lizenz | Quelle |
|---|---|---|---|
| **Demucs v4 (htdemucs)**, Meta / A. Défossez et al. | Stem-Trennung (Acapella/Instrumental) | Code MIT. **Wird nicht mit TrackTab ausgeliefert**, sondern vom Nutzer in den Einstellungen geladen. Gewichte: laut der ONNX-Quelle nicht-kommerziell, **CC BY-NC 4.0** (das Meta-Repository selbst nennt für die Gewichte keine Lizenz; es ist seit Januar 2025 archiviert) | [facebookresearch/demucs](https://github.com/facebookresearch/demucs) · ONNX-Export: [HRSadeghi/demucs-onnx](https://huggingface.co/HRSadeghi/demucs-onnx) · [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.de) |
| **Essentia-Modelle** (discogs-effnet, mood_*, danceability, mtg_jamendo_moodtheme), Music Technology Group, Universitat Pompeu Fabra | Genre, Stimmung, Energie | **CC BY-NC-SA 4.0** (nicht-kommerziell, Weitergabe unter gleichen Bedingungen) | [essentia.upf.edu/models](https://essentia.upf.edu/models.html) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.de) |

Namensnennung: *Demucs © Meta Platforms, Inc. and affiliates; ONNX-Export von HRSadeghi. Essentia-Modelle © Music Technology Group, Universitat Pompeu Fabra.* TrackTab verändert die Modelle nicht; es führt sie nur aus.

## 2. Mitgelieferte Programme und Bibliotheken (`vendor/`)

### FFmpeg 9.0.2 / ffprobe — **GPL-3.0-oder-später**

Der mitgelieferte Build stammt aus Homebrew und ist mit `--enable-gpl --enable-version3` gebaut (u. a. libx264, libx265, libmp3lame, libopus, libvpx, libdav1d, libSvtAv1, libvmaf). Damit gilt für die mitgelieferten FFmpeg-Programme und -Bibliotheken die GNU General Public License, Version 3 oder später. TrackTab ruft ffmpeg/ffprobe nur als eigenständige Programme auf (eigener Prozess).

- Projekt und Lizenzhinweise: <https://ffmpeg.org/legal.html>
- GPL-3.0-Text: <https://www.gnu.org/licenses/gpl-3.0.html>
- Quellcode zu diesem Build: am Release (siehe unten), Original <https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz>; Build-Rezept (Homebrew-Formel `ffmpeg`): <https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/f/ffmpeg.rb>

Mitgelieferte Bibliotheken in `vendor/libs/` (Lizenzen laut Projektangaben):

| Bibliothek | Lizenz | Quelle |
|---|---|---|
| libavcodec, libavformat, libavutil, libavfilter, libavdevice, libswresample, libswscale | GPL-3.0+ (in diesem Build) | <https://ffmpeg.org> |
| libx264 | GPL-2.0+ | <https://www.videolan.org/developers/x264.html> |
| libx265 | GPL-2.0+ | <https://www.videolan.org/developers/x265.html> |
| libmp3lame (LAME) | LGPL-2.0+ | <https://lame.sourceforge.io> |
| libmpg123 | LGPL-2.1 | <https://www.mpg123.de> |
| libopus | BSD-3-Clause | <https://opus-codec.org> |
| libvpx | BSD-3-Clause | <https://www.webmproject.org> |
| libdav1d | BSD-2-Clause | <https://code.videolan.org/videolan/dav1d> |
| libSvtAv1Enc | BSD-3-Clause-Clear (mit AOM-Patentlizenz) | <https://gitlab.com/AOMediaCodec/SVT-AV1> |
| libvmaf | BSD-2-Clause-Patent | <https://github.com/Netflix/vmaf> |
| OpenSSL (libssl, libcrypto) 4.x | Apache-2.0 | <https://www.openssl.org> |
| liblzma (xz) | 0BSD / gemeinfrei | <https://tukaani.org/xz/> |

**Quellcode-Zugang (GPL/LGPL):** Zu jeder `.dmg` gehört der Quellcode der darin enthaltenen GPL-/LGPL-Komponenten genau dieser Versionen. Er liegt als Download am **selben GitHub-Release** wie die `.dmg`, zusammengefasst in `TrackTab-<Version>-third-party-sources.zip` (darin `ffmpeg-<Version>.tar.xz`, `x264-<Commit>.tar.bz2`, `x265_<Version>.tar.gz`, `lame-<Version>.tar.gz`, `mpg123-<Version>.tar.bz2`, `mutagen-<Version>.tar.gz`, dazu `BUILD-INFO.txt` mit der FFmpeg-Konfiguration, `SHA256SUMS` und die Lizenztexte). Die Datei erstellt `package_sources.sh`. Der Quellcode von TrackTab selbst liegt im Repository. Die Adressen der Originalprojekte in der Tabelle sind nur zusätzliche Information. Die übrigen Bibliotheken stehen unter freizügigen Lizenzen ohne Pflicht zur Quellcode-Herausgabe.

## 3. Python-Pakete (in der App gebündelt)

| Paket | Version | Lizenz | Verwendung |
|---|---|---|---|
| mutagen | 1.48.1 | **GPL-2.0-oder-später** | Tags und Cover lesen/schreiben, MP3-Details — Quellcode: am Release (siehe oben), Original <https://github.com/quodlibet/mutagen> · <https://pypi.org/project/mutagen/1.48.1/> |
| numpy | 2.5.4 | BSD-3-Clause (u. a. 0BSD, MIT, Zlib, CC0 für Teile) | FFT, Stem-Trennung |
| onnxruntime | 1.31.1 | MIT | Ausführung der Modelle |
| libsonare | 1.10.0 | Apache-2.0 | BPM- und Tonart-Messung |
| pyrekordbox | 0.5.0 | MIT | Rekordbox-Datenbank |
| Pillow | 13.0.0 | MIT-CMU | Cover-Prüfung |
| PyYAML | 6.0.3 | MIT | Konfiguration |
| rich | 15.0.0 | MIT | Terminal-Ausgabe |
| PyObjC (Cocoa, Core) | 13.0 | MIT | macOS-App-Hülle |

Dazu kommen mitgebündelte Abhängigkeiten dieser Pakete (u. a. SQLAlchemy, sqlcipher3-wheels, construct, bidict, protobuf, flatbuffers, psutil, Pygments, markdown-it-py, python-dateutil, six, typing_extensions, tzdata) mit ihren Lizenztexten in [licenses/python/](licenses/python/).

Die Laufzeit des Pakets (CPython) steht unter der PSF-Lizenz 2.0; zum Bündeln dient **PyInstaller** (GPL-2.0-oder-später mit Ausnahme, die die Weitergabe der damit gebauten Programme erlaubt; reines Build-Werkzeug, nicht Teil der App).

## 4. Oberfläche

| Bestandteil | Lizenz | Quelle |
|---|---|---|
| Lucide-Icons (`app/webui/lucide-icons.js`) | ISC (Text in [licenses/ui/](licenses/ui/lucide-LICENSE.txt)) | <https://lucide.dev> · <https://github.com/lucide-icons/lucide/blob/main/LICENSE> |

## 5. Hinweise

- Die Modell-Einordnung (insbesondere Demucs-Gewichte) beruht auf den Angaben der jeweiligen Quellseiten und ist nicht rechtlich geprüft. Wer TrackTab weitergibt oder gewerblich einsetzen will, sollte sie vorher selbst klären.
- Die genannten Namen und Marken gehören ihren jeweiligen Inhabern.
