# Changelog

Alle nennenswerten Änderungen an TrackTab werden hier festgehalten.
Format lose angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/) (Added/Changed/Fixed).

## [Unreleased]

## [1.9.3] - 2026-10-25

### Added
- Update-Hinweis beim Start: TrackTab fragt einmal im Hintergrund die neueste GitHub-Version ab und zeigt bei einer neueren ein Overlay mit Link zum Repository. Der Haken „Diese Version ignorieren“ unterdrückt den Hinweis (`update_ignore_version` in `config.local.yaml`), bis es eine noch höhere Version gibt. Der Hinweis (und die manuelle Prüfung) listet die Änderungen aller neueren Versionen aus `changelog.md` des Repositories. Ohne Netz erscheint nichts; die manuelle Prüfung in den Einstellungen zeigt weiter immer.
- Spektrogramm: Vor dem Cutoff-Wert steht ein Icon, das ein Overlay mit dem Spektrogramm des Tracks öffnet (Haupttabelle und Einzelprüfungen). Es wird bei jedem Klick frisch mit `ffmpeg` gerechnet (rund 1 s bei einem Track von ca. 8 Minuten) und nicht zwischengespeichert. Frequenz- und Zeitachse sind beschriftet, eine gestrichelte Cutoff-Linie mit Bitrate lässt sich per Haken ein- und ausblenden. Neuer Endpunkt `/api/spectrogram`.
- Logo und Titel „TrackTab“ in der Kopfzeile öffnen die Startansicht: Liste „Alle“, Suche und Bubble-Filter zurückgesetzt, nach oben gescrollt — ohne Seiten-Reload, Wiedergabe und Einzelprüfungen bleiben erhalten.

### Changed
- Scan-Optionen neu geordnet: Datenbank, Vollständig, Lautheit, Cutoff, Genre/Stimmung/Energie/BPM/Tonart, Auffälligkeiten, Metadaten.
- Scan-Optionen: „Metadaten“ entfernt. Der Cover-Abgleich mit Music.app läuft bei jedem Scan nach der Einstellung „Cover aus Music.app übernehmen“; erzwingen lässt er sich nur noch per `scan --covers`.
- Scan-Schritt „Genre, Stimmung, Energie, BPM & Tonart“ (vorher nur Genre, Stimmung, Energie): ergänzt zusätzlich fehlende BPM und Tonart (nur leere Felder, nie überschreibend). In die Dateien wird nur nach den Einstellungen geschrieben (BPM & Tonart, Kommentar, Stimmung); vorher schrieb dieser Schritt nie in Dateien. Neue Zähler `bpmkey_done`, `bpmkey_failed`, `tags_written` im Scan-Ergebnis, auch in der CLI (`scan --genremood`).
- Zeilenmenü (⋮) neu geordnet: BPM & Tonart, Genre & Stimmung, Track neu analysieren — Linie — Format konvertieren, Bitrate korrigieren / Tonart angleichen, Als korrekt markieren — Linie — Ausblenden — Linie — Aus Rekordbox-Sammlung entfernen, Löschen. Neu im Menü: „Format konvertieren“.
- Zeilenmenü: „BPM/Tonart analysieren“ heißt „BPM & Tonart analysieren“, „Korrigieren“ heißt „Als korrekt markieren“; bei ausgeblendeten Tracks zeigt „Einblenden“ das normale statt des durchgestrichenen Auges.
- Sammelleiste neu gruppiert (durch Linien getrennt): Warteschlange/Playlist/Rekordbox/Merklisten — Bearbeiten und Analysen — Format konvertieren, Bitrate korrigieren, Als korrekt markieren (jetzt ohne Farbe) — Ausblenden/Einblenden — Mixed In Key — Download — Rekordbox-Entfernen/Löschen. Leere Gruppen fallen samt Linie weg.
- Playlist-Symbole (Seitenleiste, Listenkopf, Auswahldialoge) sind abgerundete Quadrate (4 px) statt Kreise. Die Merken-Knöpfe sind 16 × 16 px, ihr Mitgliedschafts-Punkt 5 × 5 px.
- Wellenform: Solange die Hüllkurve lädt, läuft statt des Textes „Hüllkurve wird berechnet …“ eine langsame Animation aus grauen Halbbalken über die ganze Breite (höchstens 20 s, danach stehen die Balken still).
- Albumgruppen-Zeilen: Schriftgröße 14 px (vorher 12,5 px).

### Fixed
- Player-Balken: Die Hüllkurve des beim Start gewählten Tracks wird nun auch dann geladen, wenn der Server-Modus erst nach dem ersten Zeichnen feststeht. Vorher blieb sie leer und die Ladeanimation lief endlos; ohne Track oder nach einem Ladefehler bleibt sie jetzt stehen.
- Kopfzeile: Im Player-Modus hat die Statistik-Schaltfläche links keine Trennlinie mehr (sie stammte vom ausgeblendeten „Datei öffnen“).

## [1.9.2] - 2026-10-24

### Added
- Player-Balken: Über der Wiedergabelinie steht eine halbe, einfarbige Wellenform (36 px hoch, gespielter Teil in Akzentfarbe, ohne Rekordbox-Farben), die nahtlos mit der Linie verschmilzt. Klicken und Ziehen darauf spult wie auf der Linie.
- Playlist-Generator: Die BPM-Abweichung zwischen zwei Tracks lässt sich auch auf ±5 und ±7 stellen (bisher ±0, ±3, beliebig).

### Changed
- Player-Balken: `.pbarcenter` bis 90 % Breite, Titelbereich 260 px, Lautstärke mit 30 px Abstand, Fortschrittslinie 2 px hoch.
- Playlist-Generator: Die Waveforms der Slots zeigen ebenfalls nur die obere Hälfte als einfarbige Hüllkurve; die Rekordbox-Farbwellenform wird dort nicht mehr geladen.

### Fixed
- Playlist-Generator: Das Laden der Waveforms blockiert die Oberfläche nicht mehr. Erst erscheinen Tracks und Cover, danach werden die Hüllkurven höchstens zwei gleichzeitig nachgeladen und füllen sich nach und nach.

## [1.9.1] - 2026-10-23

### Added
- Playlist-Generator: Die erweiterte Suche des Liederpools hat jetzt den Schalter „Ausgeblendet“ (egal / ja / nein). Vorgabe ist „nein“ — ausgeblendete Tracks bleiben wie bisher draußen, lassen sich aber dazunehmen. Bestehende Generatoren werden beim Öffnen einmalig auf diese Vorgabe umgestellt.
- Kopfzeile: Neue Kombi-Leiste neben „Beenden“ mit den Symbolen Datei öffnen, Statistik, Quellen und Einstellungen. „Quellen“ (Datenbank-Symbol) öffnet ein Menü mit Bibliothek scannen, Rekordbox abgleichen und Music.app abgleichen; ein Punkt am Symbol zeigt Handlungsbedarf (aktuell: für Rekordbox vorgemerkte Änderungen), im Menü ist der betroffene Eintrag markiert.
- Einstellungen → Spaltenansichten: „Spaltenansicht der Einzelprüfung“. Die Einzelprüfung kann Reihenfolge, Sichtbarkeit und Breiten einer gespeicherten Ansicht übernehmen (Vorgabe: weiterhin eigene, unabhängige Spalten).

### Fixed
- Liste „Auffälligkeiten“: Die Bubbles im Listenkopf stehen wieder mit Abstand nebeneinander statt aneinanderzukleben.
- Playlist-Generator: Die Waveform eines Slots zeigt für Tracks aus Rekordbox wieder die farbige RGB-Darstellung statt der grauen Hüllkurve (ohne Cue-Marker, die passen nicht in die Slot-Zeile).

### Changed
- Die Werkzeugleiste unter dem Titel entfällt: Scan und Abgleiche liegen im Quellen-Menü, die Statistik in der Kombi-Leiste. „Auto-Relocate“ und „Aus der Liste entfernen“ bleiben an ihrer Stelle und erscheinen nur bei fehlenden Dateien.
- Die Einzelprüfung ist zunächst ausgeblendet und erscheint, sobald eine Datei über „Datei öffnen“ oder per Drag & Drop hinzukommt; „Liste leeren“ blendet sie wieder aus.
- Der Laufstatus von Scan und Abgleichen steht jetzt unter „TrackTab“, mit breiterem Balken, hervorgehobener Phase und Prozentangabe.
- Graue Waveform etwas weniger geglättet (Stärke 0,6 statt 0,7), Transienten bleiben dadurch schärfer.

## [1.9.0] - 2026-10-23

### Added
- Genre, Stimmung und Energie: TrackTab erkennt Stil, Stimmung und Energie (1–10) lokal mit den Essentia-Modellen der MTG (onnxruntime, Modelle über `fetch_models.sh`). Aufruf im Zeilenmenü, in den Sammelleisten und als Option im Scan-Dialog; Genre-Vorschläge im Genre-Feld des Tags-Dialogs, Energie und Stimmung auf Wunsch im Kommentar bzw. im Mood-Feld der Datei. Neue Spalten Energie und Tags, neue Liste „Tags“ mit eigenen Tags.
- Playlist-Generator (neue Listenart): baut ein Set entlang einer Energiekurve mit Übergangsregeln (BPM, halbes/doppeltes Tempo, Tonart streng/locker), Stimmungsreglern und Kohäsion; gesperrte Slots bleiben beim Neu-Würfeln stehen.
- Generator: Energiekurve mit Skala E1–E10 — ein Klick setzt die Soll-Energie eines Slots, Ziehen zeichnet über mehrere Slots. Eigene Kurven lassen sich unter einem Namen speichern und in jedem Generator wählen (passen sich an die Slotzahl an). Jeder belegte Slot zeigt die Waveform seines Tracks.
- Generator: Der Liederpool ist eine Suchabfrage in derselben Syntax wie die Suchleiste (z. B. `/Genre House /BPM 120-128 /Add 30d`), dazu optional eine eigene Playlist als Quelle. Ältere Generatoren werden beim Öffnen automatisch umgestellt.
- Erweiterte Suche: Über „Erweitert“ neben der Suchhilfe stehen alle Suchparameter als Eingabefelder bereit (mit Wertvorschlägen, Ja/Nein-Felder als Segmentauswahl). Felder und Suchtext bleiben synchron und lassen sich mischen; dasselbe Panel steht im Liederpool des Generators.
- Suche: `/Add 30d` findet Tracks, die in den letzten 30 Tagen hinzugekommen sind.
- Smart Playlists: Regel „Energie“ als Stufe 1–10 (ist, ist nicht, mindestens, höchstens) und Regel „Tags“ mit allen vorhandenen Stimmungen und eigenen Tags.
- Liste „Tags“: Eigene Tags lassen sich über einen Bearbeiten-Knopf in allen Tracks umbenennen bzw. zusammenführen.
- Rekordbox: Beim Übertragen in eine Rekordbox-Playlist werden Stimmungen und eigene Tags auf Wunsch als „My Tag“ gesetzt (Einstellung; die gewählte My-Tag-Spalte muss in Rekordbox existieren).
- Einstellungen → Kommentar-Feld: neue Option „Fehlendes ergänzen“ — vorhandene Werte bleiben stehen, nur fehlende Teile kommen nach dem gewählten Schema dazu (`8A - 128` → `8A - 128 - E7`).

### Changed
- Tracklisten (Bibliothek und Einzelprüfungen): Jede Zeile und die Kopfzeile sind eigene, abgerundete Karten mit Abstand. Titel halbfett, BPM groß mit Beschriftung, „In Music“ und „Rekordbox“ als kleine App-Symbole statt Haken, größere Auswahl-Checkboxen.
- Aufgeklappter Track: ohne Spektrum-Grafik; die Waveform nutzt die volle Breite und wird als leicht geglättete Hüllkurve gezeichnet. Die Aktionsspalte läuft neben dem aufgeklappten Bereich bis ans Kartenende durch.
- Badges für Tags, Tonart und Genre sind leicht abgerundete Rechtecke; Stimmungen füllen ihre Badge ganz in ihrer Farbe.
- Icons: Smart Playlist „book-a“, Playlist-Generator „book-headphones“, „BPM/Tonart analysieren“ Notenschlüssel, „Genre & Stimmung analysieren“ Konfetti, „Tonart-Schreibweise angleichen“ „replace“. In der Listen-Kopfzeile trennt ein Strich die Listenarten vom Ordner.
- „M3U8 Export“ heißt „Playlist Export“, im Listenmenü „Playlist exportieren“.
- „Track neu analysieren“ steht im ⋯-Menü der Zeile. In den Sammelleisten stehen BPM/Tonart, Genre & Stimmung und Neu analysieren als eigene, abgetrennte Gruppe.
- Smart Playlists: Die Feldauswahl im Regel-Editor ist alphabetisch sortiert.
- Generator: Übergänge und Stimmung als eigene Abschnitte (Beschriftung über den Feldern, Tempo als Auswahl „Aus | ½ · 2×“), Slotzahl mit −/+, Quelle ohne die festen Prüflisten, Track-Energie in der Designfarbe, Erklärung der Kohäsion per Fragezeichen.
- Abhängigkeiten aktualisiert: pyrekordbox 0.5.0 (Modul heißt jetzt `pyrekordbox.masterdb`, Code entsprechend umgestellt), libsonare 1.9.1, Pillow 13.0, numpy 2.5.4, SQLAlchemy 2.1.6, python-dateutil 3.0, pyinstaller-hooks-contrib 2026.9. Geprüft: BPM-/Tonart-Messung, Cover-Prüfung und Rekordbox-Lese- und Schreibwege liefern dieselben Ergebnisse wie vorher, `calibrate` (MP3 und Lossless 100 %) und ein Test-Build laufen durch.

## [1.8.0] - 2026-10-10

### Added
- BPM- und Tonart-Analyse: TrackTab misst Tempo und Tonart selbst (libsonare, lokal, ohne Netz) und schreibt sie in die Datei-Tags. Aufruf über „BPM/Tonart analysieren“ im Zeilenmenü und in der Sammelleiste der Tabelle sowie in der Sammelleiste der Einzelprüfungen; neu hinzugefügte Dateien der Einzelprüfungen werden auf Wunsch automatisch analysiert. Neuer Endpunkt `/api/bpmkey`, neues Modul `app/bpmkey.py`.
- Einstellungen: Neue Gruppe „BPM- & Tonart-Analyse“ (Abschnitte Tonart-Feld, BPM-Feld und Kommentar-Feld: Tonart und BPM „Ja“ / „Ja, nur wenn leer“ / „Nein“, im Kommentar bei vorhandenem Text „nur wenn leer“, „überschreiben“ oder „voranstellen“). Die Tonart folgt der Einstellung „Tonart-Schreibweise“. Dateien über einer einstellbaren Länge (Standard 10 Minuten) werden nicht analysiert.
- Einstellungen: Die „Tonart-Schreibweise“ steht jetzt im Abschnitt „Tonart-Feld“ der Gruppe „BPM- & Tonart-Analyse“; die eigene Gruppe „Tonart“ entfällt.
- libsonare ist eine optionale Abhängigkeit (`requirements.txt`; PyPI-Wheel nur für macOS Apple Silicon). Fehlt sie, blendet die Oberfläche die Funktion aus. Die gebaute App wächst dadurch um rund 10 MB.

### Changed
- „Key in Datei schreiben“ heißt jetzt „Tonart-Schreibweise angleichen“ und wird nur noch für Dateien angeboten, deren Tonart-Schreibweise von der Einstellung abweicht (gestrichelter Rahmen). Es gibt sie jetzt auch in den Einzelprüfungen.
- Sammelleisten (Tabelle und Einzelprüfungen): Zwei getrennte Icons ohne Menü: „BPM/Tonart analysieren“ (Wellenform) ist immer da, „Tonart-Schreibweise angleichen“ (Notenschlüssel) nur, wenn in der Auswahl mindestens ein Track abweicht.
- Einstellungen: Die Gruppe heißt „BPM & Tonart“. Im Abschnitt „Tonart-Feld“ erklärt der Hilfetext, dass die Schreibweise sowohl für die Analyse als auch fürs Angleichen gilt, und eine Hinweiszeile nennt, wie viele Bibliotheksdateien abweichen.
- Intern: `tags.key_file_value()` ist die einzige Stelle, die Tonart und `key_notation` zur Dateischreibweise verbindet (Tags-Dialog, Angleichen, Analyse).

## [1.7.4] - 2026-10-10

### Changed
- Listenkopf von Genre, Album und Künstler: Die Zahl oben rechts nennt jetzt die Anzahl der Genres, Alben bzw. Künstler statt der Gesamtzahl der Tracks.

## [1.7.3] - 2026-10-10

### Added
- Statistik: Neuer Reiter „Bibliothek“ mit einer Momentaufnahme des Bestands: Trackzahl, Spielzeit, Speicherbedarf, durchschnittliche Länge, Formate, Tag-Vollständigkeit (Cover, Genre, BPM, Tonart, Jahr, Albuminterpret), Jahrgänge, BPM-Verteilung, Tonarten (in den Camelot-Farben), Zugänge pro Monat und Jahr laut Music.app, Abdeckung (Rekordbox, Playlisten, Merklisten, ausgeblendet, als korrekt bestätigt) sowie Pflegehinweise (Tag-Auffälligkeiten, Duplikat-Gruppen). Neuer Endpunkt `/api/stats/library`.
- `./run.command stats` gibt dieselben Bestandskennzahlen im Terminal aus.

### Changed
- Statistik: Alle Diagramme (auch Aktionen und Wiedergabezeit pro Monat) stehen untereinander in voller Breite; die Achsenbeschriftung ist normaler Text und nicht mehr vertikal gestaucht.
- Statistik: Jahre außerhalb von 1900 bis nächstes Jahr zählen als „fehlt“ und dehnen die Jahrgangs-Achse nicht mehr.

## [1.7.2] - 2026-10-10

### Changed
- Genre-, Album- und Künstler-Liste: Die Werte im Listenkopf erscheinen als Buchstaben-Index mit A–Z-Leiste (Sprung zum Buchstaben), Abschnitten je Buchstabe samt Trennlinie und linksbündig angeordneten Bubbles; die Anzahl steht als Badge, auch bei ausgewähltem Eintrag gut lesbar.

## [1.7.1] - 2026-10-04

### Fixed
- Suche: Umlaute und Akzente werden beim Vergleich ignoriert („Kolsch“ findet „Kölsch“ und umgekehrt, ebenso é/e, ñ/n, č/c). Gilt für die normale Suche, Feldfilter, exakte Vergleiche, die unscharfe Suche und die Vorschläge im Suchfeld. Nicht abgedeckt: ß, ø, æ, ł sowie „oe“/„ae“ für ö/ä.

## [1.7.0] - 2026-10-04

### Added
- Tonart: Neue Spalte „Tonart“ mit farbigen Bubbles in den Farben des Camelot-Rads. Die Tonart wird beim Scannen aus dem Datei-Tag gelesen (ID3 `TKEY`, MP4-Freiform-Atom, Vorbis `INITIALKEY`) und für bestehende Bibliotheken beim nächsten Scan ohne `--force` nachgetragen.
- Tonart: Neuer Einstellungsabschnitt „Tonart“ mit der Schreibweise Camelot, Open Key oder Notennamen. Sie gilt für Tabelle, Tags-Dialog, geschriebene Datei-Tags und den Übertrag nach Rekordbox; gelesen werden immer alle drei Schreibweisen.
- Tonart: Weicht der Wert in der Datei von der Darstellung in TrackTab ab, hat die Bubble einen gestrichelten Rahmen; der Hinweistext nennt den Datei-Wert.
- Tonart: Im Tags-Dialog bearbeitbar (einzeln und für mehrere Tracks) mit Vorschlägen für alle 24 Tonarten in der gewählten Schreibweise.
- Tonart: „Key in Datei schreiben“ im Zeilenmenü ⋮ und in der Sammelleiste schreibt die in TrackTab geführte Tonart in den Datei-Tag (Tracks ohne Tonart und mit schon identischem Wert werden übersprungen).
- Tonart: Suche mit `/Key` bzw. `/Tonart` in jeder Schreibweise (eine reine Zahl trifft Moll und Dur), sortierbar nach der Position auf dem Rad, und als Regelfeld in Smart Playlists.
- Rekordbox: Beim Übertragen neuer Tracks wird die Tonart mit gesetzt (bestehende Rekordbox-Einträge bleiben unberührt).
- Neues Icon `clef-treble` im vendorten Lucide-Satz.

### Changed
- BPM-Spalte: Ganze Werte erscheinen ohne „,0“ (`130` statt `130,0`), echte Nachkommastellen bleiben (`127,5`).
- Tags-Dialog: BPM beginnt eine neue Zeile, die Tonart steht dahinter.
- Einstellungen → Darstellung: Designfarbe über die volle Breite, danach die beiden Schalter je zur Hälfte, dann der Browser für die Web-UI.
- Smart Playlists: „Begrenzen auf“ im Regel-Dialog ist jetzt ein Schalter (bei „aus“ sind die zugehörigen Felder gesperrt); Zahlenfeld und Auswahlfelder passen optisch zu den übrigen Formularfeldern. Als Auswahlkriterium bleiben „zuletzt hinzugefügt“ und „Zufall“ (der niedrigste Cutoff und der Künstler entfallen; bereits so gespeicherte Listen behandeln das wie „zuletzt hinzugefügt“).

## [1.6.1] - 2026-10-03

### Fixed
- Warteschlange: Mehrere Fassungen desselben Liedes (z. B. „Replay“, „Replay (Remix)“, „Replay - … Edit“ vom selben Interpreten) landen nicht mehr direkt hintereinander. Der Titelvergleich ignoriert jetzt Klammer- und „ - “-Zusätze, und die automatisch gefüllte Warteschlange nimmt nur eine Fassung auf (auch ohne Zufallswiedergabe). Weitere Fassungen rücken nur nach, wenn sonst zu wenige Titel übrig bleiben. Von Hand eingereihte Titel bleiben unberührt.

## [1.6.0] - 2026-10-03

### Added
- Suche: Exakter Vergleich mit `=` hinter einem Textfeld (`/Album =Pop` findet nur das Album „Pop", nicht „King of Pop"; auch `="Pop Hits"` und Gruppen wie `(=Pop OR =Rock)`).
- Suche: Leere Felder finden mit `=` allein oder `=""` (z. B. `/Album =`, `/Genre =""`, `/Jahr =""`).
- Suche: Neue Schalter `/Cover`, `/Music` und `/Rekordbox` (mit `/No` verneinbar) für Tracks mit/ohne Cover bzw. in Music.app/Rekordbox.
- Suche: Die Autovervollständigung zeigt hinter jedem Vorschlag, ob es ein Künstler, Titel oder Album ist.

### Changed
- Suche: Der Freitext durchsucht jetzt auch das Album (bisher nur Künstler, Titel und Pfad); Albumnamen erscheinen auch in den Vorschlägen.

## [1.5.2] - 2026-10-03

### Changed
- Statistik: Die Umbenennungen heißen jetzt „Ansicht Künstler/Album/Genre: … umbenannt" (statt „Interpret"/„Album"/„Genre umbenannt").

### Fixed
- Statistik: „Auffälligkeiten-Fix" und „Rekordbox-Tags übertragen" zeigten den rohen Schlüssel statt eines Anzeigenamens.

## [1.5.1] - 2026-10-03

### Fixed
- Music.app: Der Playlisten-Baum folgt jetzt der Reihenfolge der Seitenleiste in Music.app (Ordner und Listen gemischt, vorher erst alle Listen, dann alle Ordner).

## [1.5.0] - 2026-10-03

### Added
- Rekordbox: Tag-Änderungen aus dem Zusammenführen (Genre/Interpret/Album) werden nicht mehr sofort nach Rekordbox geschrieben, sondern vorgemerkt. Der Knopf „Rekordbox abgleichen" zeigt die Anzahl als Zähler; die neue Option „Vorgemerkte Tags übertragen" schreibt alle gesammelt in einem Durchlauf (ein Backup der `master.db` statt eines je Zusammenführung).

### Changed
- Zusammenführen: Der Music.app-Abgleich fragt nur noch einmal alle Tracks mit dem alten Wert ab, statt je Track die ganze Bibliothek nach dem Titel zu durchsuchen (deutlich schneller bei großen Bibliotheken; Titel-Abgleich bleibt als Rückfall).

## [1.4.4] - 2026-10-03

### Changed
- Aufräumen: Bei Alben zeigen die Zusammenführungs-Vorschläge und der Ziel-Dialog den Künstler in eckigen Klammern hinter dem Albumnamen (wie in den Bubbles), damit erkennbar ist, welches Album gemeint ist.
- Aufräumen: Die Box mit den Zusammenführungs-Vorschlägen wird bis 500 px hoch (vorher 240 px), danach scrollt sie.

## [1.4.3] - 2026-10-02

### Fixed
- Tags bearbeiten: Hat eine Datei kein Künstler-Tag, zeigte das Feld „Künstler" ersatzweise den Albumkünstler, und Speichern meldete „Keine Änderungen". Jetzt weist eine Info-Box auf den Ersatzwert hin, und Speichern schreibt ihn als Künstler-Tag in die Datei.
- Tags bearbeiten: Die Auffälligkeiten (z. B. „Künstler fehlt") werden nach dem Speichern neu geprüft und verschwinden sofort, nicht erst nach einem erneuten Scan.

## [1.4.2] - 2026-10-02

### Fixed
- Auffälligkeiten: Der Hinweis „Interpret fehlt" heißt jetzt „Künstler fehlt" und passt damit zur Bezeichnung des Feldes in der übrigen Oberfläche.

## [1.4.1] - 2026-10-02

### Fixed
- Rekordbox: Ordner und Playlisten erscheinen im Seitenbaum jetzt in derselben Reihenfolge wie in Rekordbox (bisher in der Reihenfolge ihres Anlegens).

## [1.4.0] - 2026-10-02

### Added
- Tracks lassen sich per Drag & Drop auf normale Rekordbox-Playlisten im Baum ziehen (Rekordbox muss geschlossen sein; Prüfung beim Ziehen, beim Ablegen und serverseitig). `/api/rekordbox-add-playlist` nimmt dafür optional `playlist_id`.
- Rekordbox-Playlisten bearbeiten: „Aus Rekordbox-Playlist entfernen" und „Aus Rekordbox-Sammlung entfernen" (wie Rekordbox' „Von der Sammlung entfernen", inkl. Sicherung der Analyseordner), Reihenfolge per Verschieben ändern. Gelöscht wird wie bei Rekordbox selbst nur als Markierung (Soft-Delete).
- Neue Spalte „#" zeigt die Position in Rekordbox-, Music-App- und eigenen Playlisten; Spaltensortierung lässt diese Reihenfolge unverändert.

### Fixed
- Rekordbox: In Rekordbox entfernte Tracks und Playlist-Einträge (dort nur als gelöscht markiert) galten in TrackTab weiter als vorhanden — beim Präsenz-Abgleich, bei Cues/Wellenform und beim Hinzufügen (ein früher entfernter Eintrag wurde als „schon vorhanden" übersprungen, ein neuer Eintrag konnte an einem unsichtbaren Track landen).
- Rekordbox: Hinzufügen hinterließ Lücken in der Track-Nummerierung der Playlist.
- Schreibzugriffe auf Rekordbox prüfen jetzt auch den Hintergrunddienst „rekordboxAgent".

## [1.3.1] - 2026-10-01

### Fixed
- Rekordbox-Smart-Playlists mit Zeitregel (z. B. „hinzugefügt in den letzten 6 Monaten") lassen sich jetzt anzeigen. `pyrekordbox` wertet solche Regeln fehlerhaft aus; TrackTab rechnet sie jetzt selbst in einen festen Stichtag um.
- Beim Verschieben in den Papierkorb wird der Track jetzt immer auch aus Music.app entfernt, sobald eine Music App eingestellt ist — nicht mehr nur, wenn TrackTab ihn selbst dorthin importiert hatte. Entfernt wird nur ein Treffer mit exakt gleichem Dateipfad; die Rückmeldung „aus Music.app entfernt" erscheint nur noch, wenn tatsächlich etwas entfernt wurde.

## [1.3.0] - 2026-09-22

### Added
- Die installierte PWA kann TrackTab jetzt direkt starten, wenn der lokale Server nicht läuft: Die Hinweisseite zeigt das TrackTab-Symbol und einen Knopf **Starten**, der die App über ein eigenes URL-Schema (`tracktab://`) öffnet und die Seite automatisch neu lädt, sobald der Server bereit ist. Funktioniert nur mit der gebauten App, nicht im Terminal-Betrieb.

### Changed
- Nach dem Beenden (Knopf **Beenden**) zeigt TrackTab jetzt denselben Bildschirm wie bei einem nicht erreichbaren Server, statt eines separaten Hinweises — die Seite lädt sich automatisch neu, sobald der Server wirklich gestoppt hat.

## [1.2.0] - 2026-09-22

### Added
- Ein eigener App-Build (`dist/TrackTab.app`, auch über die von `build_app.sh` angelegte Verknüpfung in `/Applications`) nutzt jetzt einen eigenen Datenordner (`TrackTab-Build`) und einen eigenen Portbereich statt sich mit einer per .dmg installierten Version Datenbank, Einstellungen und Port zu teilen — beide Varianten können jetzt gleichzeitig laufen, ohne sich gegenseitig zu beeinflussen.

## [1.1.0] - 2026-09-22

### Added
- TrackTab laesst sich jetzt als eigene App installieren (Progressive Web App): ueber das Installiersymbol in der Adressleiste (Chrome/Edge) oder „Zum Dock hinzufuegen" (Safari) oeffnet sich die Oberflaeche danach in einem eigenen Fenster ohne Browser-Tabs/Adressleiste statt in einem normalen Tab. Der lokale Server muss dafuer weiterhin laufen.

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
