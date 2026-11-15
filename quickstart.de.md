# Schnellstart

*English version: [quickstart.md](quickstart.md)*

Die fünf Schritte vom Download bis zur ersten geprüften Bibliothek. Details zu jedem Schritt stehen im [Handbuch](manual.de.md) bzw. in [installation.de.md](installation.de.md).

---

## 1. Installieren

Wähle einen der drei Wege — die fertige App ist der einfachste Einstieg, ganz ohne Terminal:

- **Fertige App (.dmg)** — [installation.de.md](installation.de.md#1-installation-via-dmg-datei-macos)
- **Eigener App-Build** — [installation.de.md](installation.de.md#2-eigener-app-build-kompilierung-aus-dem-quellcode)
- **Terminal** — [installation.de.md](installation.de.md#3-ausführung--betrieb-über-das-terminal)

## 2. Starten & Einstellungen

Beim ersten Start ist die Bibliothek noch leer. Öffne unter dem Zahnrad-Symbol die [Einstellungen](manual.de.md#einstellungen) und lege mindestens fest:

- **Bibliothek** — deine Musikordner (Gruppe „Bibliothek").
![Statistik-Dashboard](docs/quickstart/02-1-quick-start-bibliothek-ordner.png)


- **Externe Programme** — falls du Music.app und/oder Rekordbox nutzt: hier auswählen, damit TrackTab beide abgleichen und Buttons dafür anzeigen kann. Nutzt du Music.app, aktiviere dort zusätzlich „Medienordner automatisch verwalten" und „Beim Hinzufügen zur Mediathek Dateien in den Medienordner kopieren" (Details im [Handbuch](manual.de.md#einstellungen)).
![Statistik-Dashboard](docs/quickstart/02-2-quick-start-externe-programme.png)

## 3. Verdikt, Lautheit etc. anpassen oder Standard belassen

Die Analyse-Schwellwerte (Cutoff-/Klassengrenzen, LUFS-Referenzbereich) sind ab Werk sinnvoll kalibriert — für den Einstieg reicht es, sie unangetastet zu lassen. Wer will, passt sie in den Gruppen **Analyse** und **Lautheit** an; siehe [Ist die Bitrate korrekt?](manual.de.md#ist-die-bitrate-korrekt) und [Lautheit der Tracks](manual.de.md#lautheit-der-tracks). Änderungen hier wirken erst nach einem erneuten Scan.

![Statistik-Dashboard](docs/quickstart/03-quick-start-analyse.png)

## 4. Bibliothek scannen

Der Button „Bibliothek scannen" analysiert alle noch nicht bekannten oder seither geänderten Dateien in den konfigurierten Musikordnern. Details zu den Scan-Optionen: [Bibliothek scannen](manual.de.md#bibliothek-scannen).

![Statistik-Dashboard](docs/quickstart/04-quick-start-scan.png)


## 5. Loslegen

Ergebnis ist eine Prüfliste, kein Urteil — nach dem Scan siehst du in den festen **Prüflisten** die Tracks je Verdikt (Korrekt/Verdächtig/Fake/Unklar). Von hier aus:

- Auffällige Tracks anhören und einordnen — siehe [Ist die Bitrate korrekt?](manual.de.md#ist-die-bitrate-korrekt) und [Anhören mit Wellenform](manual.de.md#anhören-mit-wellenform).
- Falsche Treffer [ausblenden](manual.de.md#tracks-ausblenden) oder [manuell korrigieren](manual.de.md#die-buttonleiste).
- Verlustbehaftete Tracks direkt [auf eine ehrliche Bitrate umkodieren](manual.de.md#bitrate-korrigieren).

Für alles Weitere (Playlisten, Tags bearbeiten, Konverter, Statistik, …) ist das [Handbuch](manual.de.md) die vollständige Referenz.
