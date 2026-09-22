# Installation

Diese Anleitung beschreibt, wie du TrackTab einrichtest — in drei unabhängigen Varianten. Du brauchst nur eine davon; welche für dich passt, hängt davon ab, ob du überhaupt mit dem Terminal arbeiten willst. Ein allgemeiner Überblick über die Bedienung nach der Einrichtung steht im [Handbuch](handbuch.md).

---

## 1. Installation via .dmg-Datei (macOS)

Die einfachste Variante: eine fertig gepackte App, kein Terminal nötig. Voraussetzung ist ein Mac mit Apple-Silicon-Prozessor (M1 oder neuer) — auf einem Intel-Mac läuft diese Variante nicht, dort Abschnitt 2 oder 3 verwenden.

1. Die `.dmg`-Datei doppelklicken — öffnet automatisch ein Fenster mit der App und einer Verknüpfung „Programme".
2. `TrackTab.app` auf die Verknüpfung „Programme" ziehen.
3. `TrackTab.app` im Programme-Ordner öffnen — dafür **einmalig** mit der rechten Maustaste (oder zwei Finger auf dem Trackpad) darauf klicken und *Öffnen* wählen, dann im Dialog nochmal *Öffnen* bestätigen. Zeigt macOS trotzdem eine Sicherheitswarnung, muss die App einmalig manuell unter **Systemeinstellungen → Datenschutz & Sicherheit** freigegeben werden (Button *Trotzdem öffnen* neben dem Hinweis zu TrackTab).

ffmpeg/ffprobe sind in dieser Variante bereits enthalten — im Gegensatz zu Abschnitt 2 muss nichts zusätzlich installiert werden.

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

Beenden der Weboberfläche: `Strg+C` im Terminal, oder der Button **Beenden** rechts oben in der Oberfläche.

Für die Bedeutung der Analyseergebnisse und eine Erklärung der Weboberfläche siehe das [Handbuch](handbuch.md).
