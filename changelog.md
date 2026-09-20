# Changelog

Alle nennenswerten Änderungen an TrackTab werden hier festgehalten.
Format lose angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/) (Added/Changed/Fixed).

## [1.0.3] - 2026-09-20

### Fixed
- Die Scan-Abschlussmeldung („Fertig — … — Seite neu laden“) bleibt nach dem Neuladen nicht mehr stehen: Der Server hält das Ergebnis des letzten Scans bis zum nächsten Scan vor, die frisch geladene Seite zeichnete es sofort wieder. Ein Tab merkt sich jetzt, welche Abschlussmeldung er schon gezeigt hat, ein neuer Scan zeigt wieder eine neue.

## [1.0.2] - 2026-09-20

### Fixed
- Der Track-Zähler einer Merkliste bzw. Playlist im Seitenbaum zählt jetzt sofort mit, wenn Tracks per Sammelaktion, Einzel-Merken, Drag & Drop oder „Aus Liste entfernen" hinzukommen oder wegfallen — bisher erst nach einem Neuladen. Schlägt der Serveraufruf fehl, springt der Zähler auf den alten Stand zurück.

## [1.0.1] - 2026-09-20

### Changed
- Quick Fix „Cover ungewöhnlich groß": übergroße eingebettete Cover werden jetzt auf ca. 1000 px (längere Kante) statt 500 px verkleinert und neu als JPEG komprimiert. Die Erkennung (> 2 MB) bleibt unverändert.

## [1.0.0] - 2026-09-20

Erste Ausgabe.

### Added
- Spektrale Cutoff-Analyse für MP3, AAC/M4A, ALAC, FLAC, WAV, AIFF — erkennt hochgerechnete/transcodierte Dateien anhand der Tiefpasskante des ersten Encodings.
- Verdikt-Logik in zwei Modi: Bitrate-Klasse via Cutoff für verlustbehaftete Formate (MP3, AAC), reine Kantenerkennung für verlustfreie Formate (ALAC/FLAC/WAV/AIFF).
- Lokale Web-Oberfläche: durchsuchbare Prüfliste, Wellenform mit Hot-Cues, Spektrum-Vorschau, Mediaplayer, Sammelaktionen, Bearbeiten- und Player-Layout mit frei konfigurierbaren Spalten.
- Benannte Spaltenansichten (Reihenfolge, Sichtbarkeit, Breite), je Liste zuweisbar, mit wählbarer Standardansicht.
- Play-, Smart- und Merklisten in einem gemeinsamen Baum; Merklisten sind markierte Playlisten und übernehmen deren Farbe und Symbol (bis zu vier). Feste, schreibgeschützte Status-Listen für Korrekt, Verdächtig, Fake, Unklar und Manuell korrigiert.
- Playlisten-Bäume aus Music.app und Rekordbox werden im selben Baum schreibgeschützt mitangezeigt (Ordner und Smart Playlists inklusive).
- Metadaten-Editor (Titel, Interpret, Album, Albumkünstler, Komponist, Genre, Jahr, BPM, Cover, Kommentar) inkl. Online-Vorschlägen (iTunes/Deezer/MusicBrainz) und Sammelbearbeitung.
- Genre-, Album- und Künstler-Clean-up: Vorschläge für zusammenzuführende Schreibweisen, verwaiste Ordner, Auffälligkeiten in den Tags.
- Bitrate-Korrektur per Re-Encoding (MP3/AAC) mit Übernahme aller Tags inkl. Serato-/Rekordbox-Frames.
- Format-Konverter (MP3 320 kbit/s CBR oder AIFF) für ausgewählte Tracks, inkl. Schutz gegen Hochrechnen, automatischem Music.app-Import und Rekordbox-Pfadkorrektur.
- Automatisches Umbenennen nach frei konfigurierbarem Muster (Vorgabe `{artist}_{title}_{bpm}_{key}_{bitrate}_{year}`), ausschließlich für Dateien außerhalb der Bibliotheksordner.
- Duplikaterkennung über Datei-Hash oder Interpret+Titel+Dauer.
- Einzeldatei-Check ohne Bibliotheksscan („Einzelprüfung"/Drops) per Drag & Drop oder Dateiauswahl, mit eigener, unabhängiger Spaltenkonfiguration; „Bitrate korrigieren" und Tag-Bearbeitung funktionieren dort auch ohne Datenbankzeile.
- Music.app-Import (inkl. Pfadkorrektur, wenn Music.app die Datei in seinen Medienordner verschiebt) und Rekordbox-Playlist-Abgleich mit Auto-Relocate für verschobene Dateien.
- Warteschlange mit „Zuletzt gehört"; ist die Liste durchgehört und „Liste wiederholen" nicht aktiv, werden automatisch 25 neue Titel aus der aktuellen Filteransicht nachgelegt.
- Statistik-Dashboard: Jahres-/Monats-Auswertung aus Änderungsprotokoll und tatsächlicher Wiedergabezeit (Aktionen pro Monat, Gesamt-Hörzeit, meistgehörte Titel/Interpreten/Genres); „Playlist aus Top 50 erstellen" legt aus den meistgehörten Titeln eines Jahres eine neue Playlist an.
- Änderungsprotokoll als JSON Lines ohne Auto-Löschung, Drag-Export als ZIP, Playlist-Export, CSV- und M3U8-Export der auffälligen Tracks.
- Scan-Optionen mit Toggles für Cutoff/Lautheit/Metadaten; `scan --covers` für einen reinen Cover-Abgleich mit Music.app ohne vollen Neu-Scan.
- Einstellung „Browser für Web-UI": legt fest, in welchem Browser sich die Oberfläche beim App-Start und per Dock-Symbol öffnet.
- Oberfläche vollständig über Sprachdateien (Deutsch, Englisch angelegt), Hell- und Dunkeldesign mit wählbarer Designfarbe.
- `calibrate`-Regressionsprüfung: testet die Cutoff-Erkennung an selbst erzeugten Transcodes (MP3, AAC, Lossless-Rewrap).
- macOS-App-Bundle per PyInstaller mit eigenem App-Symbol, Einzelinstanz-Schutz, Cmd+Q/Dock-Beenden; `package_dmg.sh` verpackt es mit eigenständig gemachtem ffmpeg/ffprobe zu einer einzelnen `.dmg` — Installation auf einem fremden Mac ganz ohne Terminal.
- README, Handbuch, Installationsanleitung und technische Referenz inkl. Screenshots zu allen Funktionen und Icon-Referenz der Knopfleiste.

### Performance
- Report-Neubau vom Anfrageweg entkoppelt: gebacken wird gebündelt in einem Hintergrund-Thread (0,35 s Sammelfenster) und spätestens beim nächsten Ausliefern der Seite. Ein Neubau kostet an echtem Material rund 600 ms (10.822 Zeilen → 10 MB HTML + CSV + M3U); eine Sammelaktion über 50 Tracks löst damit 2 statt 50 Neubauten aus.
- Server liefert Seite und Daten gepackt aus (gzip): Seitenaufruf 9,8 → 2,5 MB, Markierungslisten 1.430 → 310 KB. Der gepackte Report wird vorgehalten statt bei jedem Aufruf neu erzeugt.
- Messungen laufen außerhalb des globalen Locks: „Track neu analysieren" und der Import aus der Einzelprüfung halten die Oberfläche nicht mehr an (rund 2 s je Datei, bei 20 Tracks also etwa 40 s Stillstand). Dasselbe gilt für Abfragen an Music.app und Rekordbox.
- Wertvorschläge der Suche: ein einmal gebauter `Intl.Collator` statt eines neuen je Vergleich — **161 → 8 ms** je Tastendruck (5.606 Albumwerte, in Chrome gemessen).
- Tabellensortierung über vorberechnete Sortierschlüssel: Sortierung nach „Datei" **54 → 32 ms** bei 10.822 Zeilen, Reihenfolge an allen 17 Spalten in beide Richtungen gegengeprüft.
- Suchbereich je Zeile wird einmal gebildet und behalten: **34 → 21 ms** je Tastendruck, der einmalige Aufbau (58 ms) läuft im Leerlauf nach dem ersten Zeichnen.
- Wellenform: Balken werden einmal in eine eigene Ebene gerechnet und pro Bild nur kopiert; die Abspielposition läuft aus einer einzigen `requestAnimationFrame`-Schleife statt aus `ontimeupdate`; kein `getComputedStyle()` und keine Layout-Abfrage im Zeichenpfad; ein gemeinsamer `MutationObserver` für alle aufgeklappten Zeilen.
- `db.connect()` richtet Schema, Spaltenmigration und feste Listen einmal je Datenbankdatei und Prozess ein statt bei jeder HTTP-Anfrage; Datenbank im WAL-Modus, die reinen Lesepfade brauchen dadurch kein globales Lock mehr.
- `/api/audio` liefert per `sendfile()` aus und unterstützt `ETag`/`If-None-Match`/`If-Range`; `/api/cover` darf zwischengespeichert werden; `/api/waveform` teilt gleichzeitige Anfragen für denselben Pfad auf einen ffmpeg-Lauf auf.
- Markierungstabellen werden einmal je Aufruf gelesen statt je Ausgabezeile (bei 50 importierten Tracks vorher über 800.000 gelesene Zeilen).
- Drag & Drop schreibt blockweise in die temporäre Datei statt den gesamten Inhalt in den Arbeitsspeicher zu laden.

### Sicherheit
- Herkunftsprüfung des lokalen Servers gegen Rechnername **und** tatsächlich gebundenen Port — eine andere Seite auf `127.0.0.1` gilt damit nicht als die eigene Oberfläche (Schutz gegen CSRF und DNS-Rebinding).
- Dateizugriffe gegen die Datenbank und zusätzlich gegen eine feste Liste bekannter Audio-Endungen geprüft (`config.AUDIO_EXTENSIONS`). Freigaben aus der nativen Dateiauswahl verfallen nach 12 Stunden ohne Benutzung und sind gedeckelt.
- Beim Setzen eines Covers entscheiden die Kopfbytes über den geschriebenen Bildtyp, nicht der mitgeschickte `Content-Type`. Ausgelieferte Cover-Typen stammen aus einer festen Liste (kein SVG).
- In den Report eingebettete Daten werden für den `<script>`-Kontext escapet — ein `</script>` in einem Tag hätte sonst den Skriptblock beendet.
- Cover-Download aus dem Netz nur von öffentlich erreichbaren Adressen (kein localhost/privates Subnetz), mit Größenlimit und Prüfung des Bildformats. Programmpfade aus den Einstellungen müssen auf ein vorhandenes Programmbündel zeigen, Shop-Adressen auf `http`/`https`.
- Rekordbox-Schreibzugriffe laufen alle über eine gemeinsame eigene Sperre, mit `pgrep`-Prüfung und rohem Backup der `master.db` vor jedem Zugriff.

### Known limitations
- AAC-Bitrate-Klassifikation ist experimentell: weder ffmpegs nativer `aac`-Encoder noch Apples `aac_at` legen einen bitratenkorrelierten Tiefpass an wie LAME bei MP3 — Kalibrierung (28.08.2026) ergab nur 39 % Trefferquote (`aac`) bzw. ~6 % (`aac_at`), gegenüber 100 % bei MP3 und Lossless-Rewrap. AAC-Verdikte entsprechend mit Vorsicht behandeln.
- 224 und 256 kbit/s nutzen bei MP3 denselben LAME-Lowpass und sind spektral nicht unterscheidbar.
