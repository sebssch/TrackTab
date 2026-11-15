# Installation

Diese Anleitung beschreibt, wie du TrackTab einrichtest — in drei unabhängigen Varianten. Du brauchst nur eine davon; welche für dich passt, hängt davon ab, ob du überhaupt mit dem Terminal arbeiten willst. Ein allgemeiner Überblick über die Bedienung nach der Einrichtung steht im [Handbuch](handbuch.md).

---

## 1. Installation via .dmg-Datei (macOS)

Die einfachste Variante: eine fertig gepackte App, kein Terminal nötig. Voraussetzung ist ein Mac mit Apple-Silicon-Prozessor (M1 oder neuer) — auf einem Intel-Mac läuft diese Variante nicht, dort Abschnitt 2 oder 3 verwenden.

1. Die `.dmg`-Datei doppelklicken — öffnet automatisch ein Fenster mit der App und einer Verknüpfung „Programme".
2. `TrackTab.app` auf die Verknüpfung „Programme" ziehen.
3. `TrackTab.app` im Programme-Ordner öffnen — dafür **einmalig** mit der rechten Maustaste (oder zwei Finger auf dem Trackpad) darauf klicken und *Öffnen* wählen, dann im Dialog nochmal *Öffnen* bestätigen. Zeigt macOS trotzdem eine Sicherheitswarnung, muss die App einmalig manuell unter **Systemeinstellungen → Datenschutz & Sicherheit** freigegeben werden (Button *Trotzdem öffnen* neben dem Hinweis zu TrackTab).

ffmpeg/ffprobe sind in dieser Variante bereits enthalten — im Gegensatz zu Abschnitt 2 muss nichts zusätzlich installiert werden.

Die BPM-/Tonart-Analyse ist in der App enthalten (libsonare), ebenso die Genre-/Stimmungs-Analyse samt Modellen (onnxruntime, Essentia-Modelle; benötigt macOS 14) während das Stem-Trennmodell (Demucs, ~170 MB) aus Lizenzgründen nicht enthalten ist: Es wird in den Einstellungen unter „Stems“ einmalig geladen, bis dahin sind die Stem-Funktionen ausgeblendet.

---

## 2. Eigener App-Build (Kompilierung aus dem Quellcode)

Diese Variante erzeugt eine doppelklickbare App, mit der du TrackTab danach komplett ohne Terminal benutzen kannst.

### Voraussetzungen für den Build

- macOS 13 oder neuer
- Python 3.12 oder neuer
- `ffmpeg`/`ffprobe` im PATH (per Homebrew: `brew install ffmpeg`)

### Vorgehen

1. Repository herunterladen bzw. klonen.
2. Im Terminal in den Projektordner wechseln und den Build-Befehl ausführen:

   ```bash
   ./build_app.sh
   ```

3. Das erzeugt `dist/TrackTab.app` — eigenständig, ohne dass Python, eine virtuelle Umgebung oder Homebrew auf dem Zielrechner installiert sein müssen. Zusätzlich legt der Build eine Verknüpfung `TrackTab` in `/Applications` an.
4. Die App per Doppelklick starten. Sie sucht sich automatisch einen freien Port, startet ihren eigenen Server im Hintergrund und öffnet den Report im Standardbrowser.

Das Bundle ist nicht signiert. Auf einem fremden Mac hilft beim allerersten Start ein Rechtsklick auf die App → *Öffnen*, statt sie einfach zu doppelklicken.

Ein eigener Build nutzt einen eigenen Datenordner (`TrackTab-Build` statt `TrackTab` unter `~/Library/Application Support/`) und einen eigenen Port-Bereich — auch wenn er über die Verknüpfung in `/Applications` gestartet wird. Er läuft also unabhängig von und gleichzeitig zu einer per .dmg installierten Version (siehe oben), mit eigener, zunächst leerer Bibliothek.

**Beenden:** über den Button **Beenden** in der Oberfläche, per `Cmd+Q` oder per Rechtsklick auf das Dock-Symbol → *Beenden*. Nur das Browser-Fenster zu schließen beendet die App **nicht** — sie läuft dann im Hintergrund weiter (erkennbar am Dock-Symbol) und ein Scan wird dadurch nicht unterbrochen.

Nach einer neuen Version des Quellcodes einfach `./build_app.sh` erneut ausführen — die App erkennt eine geänderte Oberfläche automatisch und aktualisiert sich beim nächsten Start selbst.

---

## 3. Ausführung & Betrieb über das Terminal

### Voraussetzungen

- macOS 13 oder neuer
- Python 3.12 oder neuer
- `ffmpeg`/`ffprobe` im PATH
- Optional: `libsonare` für die BPM-/Tonart-Analyse (kommt über `requirements.txt` automatisch mit; das PyPI-Wheel gibt es nur für Apple Silicon — auf einem Intel-Mac läuft TrackTab ohne diese Funktion)
- Optional: `onnxruntime` und die Essentia-Modelle für Genre, Stimmung und Energie. `onnxruntime` kommt über `requirements.txt` mit (nur Apple Silicon, macOS 14+), die Modelle (~21 MB) lädt einmalig `./fetch_models.sh` nach `vendor/models/essentia/`; `./build_app.sh` erledigt das selbst. Ohne beides läuft TrackTab ohne diese Funktion.
- Optional: das Demucs-Modell für die Stem-Trennung (Acapella/Instrumental, ~170 MB, nutzt dasselbe `onnxruntime`). Es wird in den Einstellungen unter „Stems“ geladen (Ablage in `models/demucs/` im Datenordner, nicht im Bundle); ein vorhandenes `vendor/models/demucs/` wird im Entwicklungsbetrieb weiter genutzt. Ohne das Modell sind die Stem-Funktionen ausgeblendet.
- Auto Cue bringt kein Modell mit: Jede Installation trainiert ihr eigenes aus den eigenen Hand-Cues (in der App oder mit `./run.command autocue-train`, siehe „Auto Cue neu trainieren“) oder importiert eines als ZIP. Ein früher genutztes `vendor/models/autocue/` liest TrackTab nicht mehr; liegt es noch dort, gehört es weg, sonst packt `build_app.sh` es unnötig mit in die App.
- Optional: das Phrasenmodell `vendor/models/phrases/phrases.onnx` für „Beatgrid & Phrasen“ (nutzt dasselbe `onnxruntime`). Es stammt aus GlideMix (GPL) und liegt nicht im Repository (`vendor/` ist ausgeschlossen); `./fetch_models.sh` holt es einmalig aus dem GlideMix-Quellarchiv (Download rund 70 MB, das Modell selbst 1,2 MB), `./build_app.sh` erledigt das selbst. Liegt die Datei dort, wird sie beim Bauen mit in die App gepackt. Ohne das Modell ist die Funktion ausgeblendet.
- `osascript` (in macOS bereits enthalten; wird für Papierkorb, Finder-Integration, externe Programme und Dialoge genutzt)

### Einrichtung

1. Repository herunterladen bzw. klonen.
2. Im Terminal in den Projektordner wechseln und starten:

   ```bash
   ./run.command
   ```

   Beim ersten Start wird automatisch eine eigene, isolierte Python-Umgebung (`.venv`) angelegt und alle benötigten Python-Pakete werden installiert.
3. Fehlt `ffmpeg`, bricht `run.command` mit einem Hinweis ab. Nachinstallieren per Homebrew:

   ```bash
   brew install ffmpeg
   ```

   Danach `./run.command` erneut starten.
4. Ohne weitere Angaben läuft jetzt der komplette Durchlauf: die Bibliothek wird analysiert, der Report erzeugt und im Browser geöffnet.

### Befehle

Alle Befehle laufen über `./run.command <Befehl>`:

| Befehl | Zweck |
|---|---|
| *(ohne Angabe)* | Kompletter Durchlauf: Analyse, Report, Browser |
| `scan` | Bibliothek analysieren (inkrementell — nur Neues/Geändertes wird gemessen) |
| `scan --limit 200` | Testlauf über nur 200 Dateien |
| `scan --force` | Cache ignorieren, alle Dateien neu messen |
| `scan --prune` | Einträge zu inzwischen gelöschten Dateien aus der Datenbank entfernen |
| `scan --covers` | Nur Cover-Abgleich mit Music.app für alle bekannten Dateien ohne Cover, kein Neu-Scan der Audiodateien |
| `scan "/Pfad/zum/Ordner"` | Nur einen bestimmten Ordner prüfen |
| `check "/Pfad/Datei.mp3"` | Eine einzelne Datei im Detail direkt im Terminal prüfen |
| `report` | Report-Dateien (HTML, CSV, M3U) aus dem aktuellen Datenbankstand neu erzeugen |
| `serve` | Weboberfläche öffnen |
| `serve --port 9000` | Weboberfläche auf einem anderen Port öffnen, falls 8756 belegt ist |
| `serve --no-open` | Weboberfläche starten, ohne automatisch den Browser zu öffnen |
| `reclassify` | Bewertungen anhand geänderter Einstellungen neu bilden, ohne die Dateien erneut zu messen |
| `stats` | Zusammenfassung der Bewertungen anzeigen |
| `calibrate` | Regressionsprüfung: erzeugt Testdateien aus der eigenen Bibliothek und prüft, ob die Erkennung sie korrekt wiedererkennt |
| `backup create` | Backup der Analysedaten jetzt erstellen |
| `backup list` | Vorhandene automatische Backups anzeigen |
| `backup restore <Datei>` | Analysedaten aus einem Backup wiederherstellen (der bisherige Stand wird davor selbst gesichert) |
| `autocue-train` | Auto Cue neu trainieren (siehe unten) |
| `autocue-eval` | Auto-Cue-Modelle auf der Prüfliste bewerten, ohne zu trainieren |

Beenden der Weboberfläche: `Strg+C` im Terminal, oder der Button **Beenden** rechts oben in der Oberfläche.

### Auto Cue neu trainieren

In der App geht das unter Einstellungen → Auto Cue → **Training** (siehe [Handbuch](handbuch.md#auto-cue)). Im Entwicklungsordner gibt es denselben Ablauf als Befehl, der mit den Daten jeder TrackTab-Instanz arbeiten kann, auch mit denen der installierten App. LightGBM steht in `requirements.txt` und braucht die OpenMP-Bibliothek aus Homebrew:

```bash
brew install libomp
```

Dann die Vorlage kopieren und anpassen (die eigene Datei wird nicht eingecheckt):

```bash
cp autocue-train.example.yaml autocue-train.yaml
```

Darin stehen der Datenordner der TrackTab-Instanz, deren Tracks und Hand-Cues gelernt werden (`installed` für die installierte App, `dev` für den Entwicklungsordner oder ein Pfad), die Playlist **Training** (daraus wird gelernt, je mehr Tracks, desto besser) und die Playlist **Prüfliste** (daran wird gemessen, nie gelernt). Beides sind normale TrackTab-Playlisten dieser Instanz und dürfen keinen Track gemeinsam haben. Was gelernt wird, kommt wie in der App aus der Slot-Belegung der Instanz; `label_slots` setzt zum Ausprobieren eine feste Hot-Cue-Zuordnung, `name` den Namen des Modells. Drop 2 und Break 1 müssen als Rolle auf einem Hot Cue liegen. Das neue Modell kommt nach Rückfrage in die Modellliste dieser Instanz und wird dort ausgewählt (der frühere Eintrag `targets` mit dem Ziel `vendor` entfällt, ein mitgeliefertes Modell gibt es nicht mehr). Dann:

```bash
./run.command autocue-train
```

Gelernt wird aus den von Hand gesetzten Hot Cues (nach der Slot-Belegung) und den Memory Cues (aus der DJ-Ansicht bzw. aus Rekordbox nach „Rekordbox abgleichen“ → Laden), auf TrackTabs Beatgrid und Phrasen. Fehlt ein eigenes Beatgrid, übernimmt der Lauf das aus Rekordbox in die Datenbank der Instanz. Cues im Ausklang am Trackende zählen nicht.

Der erste Lauf über rund 4.000 Tracks dauert etwa eine Stunde; Protokoll, Modell und ZIP jedes Laufs liegen im Entwicklungsordner unter `autocue-training/runs/<zeit>/`, Datensatz und Zwischenstände in `autocue-training/` im Datenordner der Instanz; beides ist von git ausgeschlossen. Danach rechnet der Export nur Geändertes neu. Am Ende stehen neues und aktives Modell auf der Prüfliste nebeneinander (beim ersten Modell der Instanz nur das neue); zusätzlich entsteht `autocue-modell-<zeit>.zip`, die sich in jeder TrackTab-App unter Einstellungen → Auto Cue → **Modell importieren …** einspielen lässt. Übernommen wird nach Rückfrage. `./run.command autocue-train --probe` prüft schon mit wenigen Tracks (ab 3), ob alles durchläuft, und installiert nie. `./run.command autocue-eval` bewertet nur, ohne zu trainieren. Weitere Schalter zeigt `./run.command autocue-train --help`.

Für die Bedeutung der Analyseergebnisse und eine Erklärung der Weboberfläche siehe das [Handbuch](handbuch.md).
