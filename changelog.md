# Changelog

Alle nennenswerten Änderungen an TrackTab werden hier festgehalten.
Format lose angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/) (Added/Changed/Fixed).

## [2.0.0] - 2026-11-01

### Added
- Shops: YouTube als neuer Standard-Shop. Alle Shops zeigen ihr Favicon; TrackTab holt es einmal von der Seite und speichert es lokal (nur Hosts aus der Shop-Liste, nur öffentliche Adressen, kein SVG). Lädt es nicht, bleibt das bisherige Symbol bzw. der Buchstabe. Wer die Shop-Liste schon einmal gespeichert hat, trägt YouTube von Hand nach.
- Neue Spalte „Track – Interpret“ (Titel und Interpret in einer Zelle, darunter Format und Bitrate mit „Pfad kopieren“ und „Im Finder zeigen“) wie in der DJ-Ansicht, jetzt in jeder Ansicht über „Spalten ▾“ wählbar; im Player die Vorgabe.
- Standard-Spaltenansichten wie in Apple Music: „Standard – Bearbeiten“ und „Standard – Player“ gelten für alle Listen ohne eigene Spaltenansicht in der jeweiligen Ansicht. Eine eigene Spaltenansicht einer Liste gilt in Bearbeiten und Player. Die frühere globale „Standard-Spaltenansicht“ entfällt; ihre Spalten werden einmalig nach „Standard – Bearbeiten“ übernommen, die Ansicht selbst bleibt gespeichert.
- Player-Zeile: kein „…“-Menü mehr; in einer offenen Playlist (TrackTab, Rekordbox, Music.app) steht „Aus Playlist entfernen“ als Knopf neben „Zur Playlist“.
- Zeilen-Play-Knopf wird beim Abspielen zum Pausensymbol; im Player pausiert ein Klick auf den laufenden Track, statt ihn neu zu starten.
- Seitenbaum überarbeitet: TrackTab, Music App und Rekordbox sind Abschnittsköpfe, die sich per Klick ein- und ausblenden lassen. Jede Liste trägt ein farbiges Quadrat (ohne Farbe hellgrau), Ordner zeigen offen/zu. Neu anlegen und M3U-Import liegen im „…“-Menü von TrackTab (die Knöpfe im Kopf entfallen), die Menüs sind in beschriftete Gruppen gegliedert. Ein aufgeklappter Music-/Rekordbox-Ast lädt seine Listen von selbst, auch beim Start.
- Feste Ansichten (Alle, Genre, Album, Duplikate …) und der Ordner Prüflisten verhalten sich wie eigene Listen: verschiebbar auch in Ordner, Farbe änderbar; der Ordner Prüflisten lässt sich zudem umbenennen und nimmt eigene Playlisten auf. Status-Listen behalten Name, Symbol und Statusfarbe.
- Neue feste Ansicht „Zuletzt hinzugefügt“ (alle Tracks, nach „Hinzugefügt“ absteigend).
- Rechtsklick auf Tracks und Listen im Baum öffnet dasselbe Menü wie „…“.
- Spaltenbreite per Doppelklick auf den Rand der Überschrift an den breitesten Eintrag anpassen.
- Ladeplatzhalter passend zur Ansicht (Bearbeiten, Player, DJ) für Tabelle und Baum; Nachladen und Wechsel der Playlist zeigen Platzhalter statt einer leeren Fläche.
- Statistik, Quellen und Datei öffnen sind in allen drei Ansichten erreichbar; „Datei öffnen“ und „Bibliothek scannen“ wechseln dafür nach Bearbeiten.
- Eigene Tooltips im Stil von Tippy.js (Titel fett, Erklärung darunter); Einstellungen: kurze Hilfetexte als ?-Symbol am Label.
- Einstellungen als Seitennavigation mit Suche statt Akkordeons.
- Suchen und Sortieren in großen Tabellen deutlich schneller (vorberechnete Sortierschlüssel, zwischengespeicherter Suchbereich je Zeile, gemessen an 10.822 Zeilen: Wertvorschläge der Suche 161 → 8 ms, Sortierung nach Datei 54 → 32 ms).
- Wellenform: Zeichnen aus einer gemeinsamen Bildschleife, Balken in einer zwischengespeicherten Ebene, Cue-Marker behalten beim Abspielen ihre Farbe.
- Server: gzip für große Antworten (Report 9,8 → 2,5 MB), Freigabe-Cache für reine Ausliefer-Endpunkte, Report-Neubau gebündelt im Hintergrund.
- Neue Auffälligkeit „Tonart-Schreibweise weicht von der Einstellung ab“ mit Quick Fix (gleicht die Schreibweise in der Datei an). Der Hinweis unter Einstellungen → Tonart-Feld hat dazu einen Link „Anzeigen“, der die betroffenen Dateien in der Liste Auffälligkeiten öffnet.
- DJ-Ansicht, Sammelleiste: „BPM & Tonart“ und „Genre & Stimmung“ analysieren für markierte Tracks (dieselben Analysen wie in Bearbeiten).
- Smart Playlists: neue Regel „Playlist ist in / ist nicht in …“, z. B. „alle House-Tracks, die noch nicht in Set Freitag stehen“. Wählbar sind alle eigenen Listen, auch Merklisten, andere Smart Playlists und Ordner.
- DJ-Ansicht: Spalten „#“ (Reihenfolge in der Playlist, nur in eigenen Playlisten) und „Hinzugefügt“, beide sortierbar. Markierte Tracks lassen sich über die Sammelleiste aus der geöffneten Playlist entfernen (mit Rückgängig, die Dateien bleiben).
- Auto Cue (aus dem Projekt rekordbox-autocue-tool übernommen): setzt in der DJ-Ansicht Hot Cues und Memory Cues für den offenen Track (Cue/Loop und Info, mit Rückgängig) oder für markierte Tracks (Sammelleiste). Grundlage sind Beatgrid und Phrasen von TrackTab; fehlen sie, werden die aus Rekordbox übernommen, sonst wird vorher gemessen. Die Cues ersetzen die bisherigen und liegen in TrackTab, nach Rekordbox gehen sie mit Rekordbox Update. Eigene Einstellungsseite „Auto Cue“ mit Phrasen-Schalter, Memory-Budget, Konfidenz je Stelle und Slot-Belegung (Hot A–H, Memory 1–10: Rolle, Regel oder automatisch, Name, Rekordbox-Farbe). Läuft ohne librosa und LightGBM (eigener Nachbau, gegen das Original geprüft: 99 % gleiche Cues bei gleichem Grid). In Rekordbox selbst noch nicht geprüft.
- Auto Cue selbst trainieren, direkt in der App: Einstellungen → Auto Cue → „Training“ lernt aus den eigenen Hand-Cues einer Playlist und misst an einer zweiten (Prüfliste), mit Fortschritt je Schritt, Restzeit und Abbrechen; der Stand steht auch bei geschlossenen Einstellungen unter dem Titel. Was gelernt wird, kommt aus der Slot-Belegung – derselben, nach der Auto Cue setzt: Hot Cues mit Rolle werden aus den eigenen Hot Cues gelernt, Drop 1, Break 2 und Outro auf Memory-Zeilen aus den eigenen Memory Cues, Regeln werden gerechnet. Pflicht sind nur der letzte Drop (Drop 2) und Break 1 als Hot Cue; was sonst nicht gecuet ist, lernt das Modell nicht, Auto Cue sucht es dann über die Strukturanalyse (Outro per Regel). Ohne Memory Cues bricht das Training nicht mehr ab. Abstände wie „Break 1 mindestens 40 Takte vor Drop 2“ lernt ein solches Training aus den Daten. Dasselbe im Entwicklungsordner per `./run.command autocue-train` (`autocue-train.yaml`, neu `label_slots` und `name`). LightGBM ist dafür jetzt in der App enthalten (wird nur beim Training geladen); wo es sich nicht laden lässt (älteres macOS), bietet die App kein Training an. Von Auto Cue gesetzte Cues fließen nie als Hand-Cues zurück ins Training. Ein Training in der App ist bisher nur an einer Kopie der Bibliothek und mit wenigen Tracks durchgelaufen.
- Auto-Cue-Training: Playlisten-Auswahl wird bei jedem Öffnen der Einstellungen neu aufgebaut (eben angelegte stehen sofort drin) und enthält auch smarte Playlisten.
- Einstellungen → Auto Cue: Slot-Belegung in zwei Blöcke geteilt (Hot Cues, Memory Cues), je mit eigenem Speichern und „Standard wiederherstellen“.
- Auto Cue: Sicherheit je Stelle als fünf Stufen (Sehr sicher, Sicher, Normal, Riskant, Sehr riskant) statt einer Zahl; gespeicherte Werte werden übernommen. „Memory Cues höchstens“ entfällt, es sind fest 10 wie in Rekordbox.
- Auto Cue: Von Hand bearbeitete Phrasen gelten als fester Indikator – Drops, Breaks und Outro rasten auf ihre Grenzen ein, wenn diese sauber auf einer Takt-1 liegen. Der Schalter „Phrasen als Hinweis“ betrifft nur noch automatisch gemessene Phrasen.
- Auto Cue: Das Outro wird bis kurz vor dem Ausklang gesucht statt höchstens 32 Takte nach dem letzten Drop; ist der Rest kurz, wird es kurz oder entfällt. Ein eigenes Training lernt den üblichen Abstand aus den eigenen Outro-Cues.
- Auto-Cue-Modelle verwalten: Einstellungen → Auto Cue → „Modelle“ listet beliebig viele eigene (Name, Datum, Tracks, Ergebnis auf der Prüfliste, gelernte Cue-Konvention). Ein mitgeliefertes Modell gibt es nicht: Jede Installation trainiert ihr eigenes nach den eigenen Cues oder importiert eines als ZIP; bis dahin ist Auto Cue gesperrt, Slot-Belegung und Training bleiben bedienbar. Es gilt das ausgewählte, das erste wird automatisch ausgewählt; umbenennen, als ZIP exportieren, ZIP importieren, löschen. Eine frühere Wahl „mitgeliefertes Modell“ wird durch das neueste eigene ersetzt. „Slot-Belegung des Modells übernehmen“ passt die Slot-Belegung an die Konvention eines Modells an. Ein früher geladenes eigenes Modell wird einmalig in die Liste übernommen und bleibt ausgewählt, wenn es galt.
- Auto Cue setzt im Ausklang am Trackende (Hall-/Delay-Rest, erkannt am Pegel, bei von Hand bearbeiteten Phrasen an deren Grenze) keine Cues mehr; das Training zählt Hand-Cues dort nicht.
- Beatgrid aus Rekordbox übernehmen: Fehlt TrackTabs eigenes Beatgrid, wird das aus Rekordbox samt Phrasen als eigenes gespeichert (beim Öffnen im DJ-Editor, vor Auto Cue und nach „Rekordbox abgleichen“ im Hintergrund). Eine eigene Messung ersetzt es später.
- Behoben: Nach dem Rekordbox-Abgleich meldeten rund drei Viertel der Tracks beim Öffnen „Phrasen in Rekordbox geändert“, obwohl sich dort nichts geändert hatte. Ursache war eine geänderte Lesart der Up-Nummern in TrackTab; der festgehaltene Stand wird jetzt still auf die aktuelle Lesart gebracht.
- DJ-Ansicht, Sammelleiste: „Zu Rekordbox hinzufügen“ für mehrere Tracks (schon vorhandene werden übersprungen) und „Beatgrid + Phrasen“ in einem Durchgang.
- Einstellungen: Beatgrid & Phrasen stehen jetzt unter DJ-Ansicht.
- „Zu Rekordbox hinzufügen“ in der DJ-Ansicht schreibt Beatgrid, Phrasen, Cues, Wellenformen und Cover direkt, Rekordbox muss den Track nicht erst analysieren. In Rekordbox und per USB-Export geprüft (AIF, FLAC, M4A, WAV, MP3); Flex-Tempo noch nicht gesondert. Bei seltenen MP3-Köpfen bleibt es bei Tags und Cues.
- DJ-Ansicht: feste Phrasenfarben je Art und Nummer, für Rekordbox-Phrasen und die eigene Analyse.
- DJ-Ansicht: Zoom und Gesamtansicht reichen einen Takt über Anfang und Ende hinaus. Das Beatgrid bleibt im Track; ein Schlag knapp vor dem Ton steht am Anfang, sodass sich ein Cue auf den ersten Schlag setzen lässt.
- DJ-Ansicht: Sammelaktionen für markierte Tracks: Rekordbox laden, Rekordbox Update, Beatgrid-Analyse und Phrasen-Analyse (gesperrte überspringen).
- „Rekordbox Update“ sperrt den Track für die Analyse, in TrackTab (Beatgrid und Phrasen) und in Rekordbox. Noch nicht in Rekordbox selbst geprüft.
- Der Rekordbox-Status (Logo mit Umrandung) steht auch in der Spalte Rekordbox von Bearbeiten und Player. Bei einer Abweichung steht darunter, was abweicht (Beatgrid, Phrasen, Cues).
- Neue Auffälligkeit: Tempowechsel im Beatgrid steht nicht im Titel, mit Autofix (z. B. „Titel (130 → 150 → 110 BPM)“, alle Wechsel in Track-Reihenfolge). Greift bei Tracks, die einmal im DJ-Editor geöffnet waren.
- DJ-Editor, Info: Beatgrid-Analyse, Phrasen-Analyse und beides zusammen als eigene Knöpfe.
- Phrasen-Analyse misst auf dem vorhandenen Beatgrid (Bearbeitung, Übernahme aus Rekordbox oder eigene Messung), statt ein eigenes zu suchen. Damit gibt es Phrasen auch für Tracks ohne eindeutiges Tempo (Übergänge, Tempowechsel), sobald das Grid von Hand gesetzt ist; auch „Beatgrid + Phrasen“ fällt dann darauf zurück und meldet es. Bei gleichmäßigem Tempo an drei Tracks geprüft: dieselben Phrasen wie vorher.
- DJ-Editor, Phrasen ohne vorhandene Phrasen: „Teilen“ legt beide Hälften an (vorher nur ab der Abspielposition, der Anfang blieb leer), die leere Liste bietet „Phrase anlegen“ und „Phrasen-Analyse“ an.
- Phrasen rasten am Beatgrid ein: bei einer Analyse gegen ein gesperrtes Grid, bei einer Grid-Analyse auch gesperrte Phrasen, und nach Änderungen von Hand. In der DJ-Ansicht gilt eine Analyse sofort als Stand des Tracks.
- DJ-Ansicht, Memory Cues: zehn feste Plätze, nach dem Löschen rückt der nächste nach. Ein Hot Cue an derselben Stelle wird angezeigt. Aus einem aktiven Loop entsteht ein Active Memory Loop (rot), ein Klick aufs Symbol wechselt zum normalen (orange). Läuft der Track über einen Active Memory Loop, greift er von selbst. Der Cue an der Abspielposition (ein Schlag davor bis ein Takt danach) ist in der Liste mit einem dunkleren Ring um den Farbpunkt markiert. Nach Rekordbox gehen beide Varianten so, wie Rekordbox sie selbst speichert (an je einem Beispiel abgelesen).
- Rekordbox abgleichen lädt Beatgrid, Phrasen und Cues nach TrackTab, soweit dort noch nichts steht. Spätere Änderungen in Rekordbox überschreiben nichts mehr automatisch: sie werden angezeigt und nur per „Rekordbox laden“ übernommen.
- Spalte Music: Logo wie bei Rekordbox, farbig in Music.app, sonst grau.
- Spalte Rekordbox: Sortieren in vier Stufen (in Rekordbox, grün, rot, nicht in Rekordbox jeweils zuerst).
- Listen: eigene Farbe über das Plus an letzter Stelle der Farbauswahl.
- Rekordbox-Sicherung vollständig: Sie enthält jetzt auch, was Rekordbox noch nicht in die Hauptdatei übernommen hat (die Nebendatei `master.db-wal`). Vorher fehlte darin alles seit dem letzten Abgleich in die Hauptdatei. Eine Sammelaktion sichert einmal statt je Track.
- „Rekordbox Update“ für viele Tracks deutlich schneller: ein Aufruf je zehn Tracks statt zwei je Track, der Versatz zu Rekordbox wird gemerkt statt jedes Mal neu gemessen. An 20 Tracks gemessen (Kopie der Bibliothek): vorher rund 2 s je Track, jetzt 0,7 s beim ersten und 0,2 s bei jedem weiteren Lauf.
- Ein Weg nach Rekordbox: Ziehen auf eine Rekordbox-Playlist (und der Knopf für die Ziel-Playlist) bringt bei neuen Tracks Beatgrid, Phrasen, Cues und Cover aus TrackTab mit. Stehen Tracks schon in Rekordbox, wählt man in einer Liste, welche TrackTabs Fassung bekommen; vorab angehakt sind die abweichenden. Dieselbe Liste gibt es als „Mit TrackTab abgleichen …“ je Rekordbox-Playlist. „Zu Rekordbox hinzufügen“ in der DJ-Ansicht bleibt (nur Sammlung) und erscheint in der Leiste nur noch, wenn ein ausgewählter Track dort fehlt. An einer Kopie der Bibliothek geprüft, nicht in Rekordbox selbst.
- Verknüpfte Playlisten: Eine TrackTab-Playlist oder Smart Playlist lässt sich mit je einer Playlist in Music.app und Rekordbox verknüpfen (oder dort neu anlegen); die Ziele wählt man im Playlist-Baum des jeweiligen Programms. Hinzufügen und Entfernen gilt dann überall, die Reihenfolge bestimmt TrackTab. Abgleich beim Öffnen, nach Änderungen, beim Start und per Knopf; Rekordbox wird nachgeholt, wenn es lief. Smart Playlists füllen die verknüpften Listen nur nach außen. Rückfrage bei fehlenden Music-Tracks und bei vielen Entfernungen. Rekordbox an einer Kopie geprüft, Music.app mit einer Test-Playlist an der echten Mediathek (der Import selbst nicht, nur die Rückfrage und das Weglassen).
- Playlisten und Ordner in Rekordbox und Music.app per Drag & Drop im Baum verschieben (in Ordner, davor/dahinter, in Rekordbox auch zurück auf die oberste Ebene). Während einer Änderung bleibt der Baum aufgeklappt, der Ast ist ausgegraut und gesperrt, die betroffene Playlist pulsiert. Rekordbox an einer Kopie, Music.app mit Test-Ordnern geprüft.
- Playlisten in Rekordbox und Music.app direkt in TrackTab anlegen, umbenennen und löschen (auch Ordner), Tracks per Drag & Drop hinzufügen und über die Leiste entfernen, in Bearbeiten wie in der DJ-Ansicht. Rekordbox löscht dabei wie Rekordbox selbst (die Einträge bleiben als gelöscht markiert stehen). Smart Playlists bleiben nur zum Ansehen. Rekordbox an einer Kopie geprüft, Music.app an der echten Mediathek mit einer Test-Playlist (anlegen im Ordner, umbenennen, hinzufügen, entfernen, löschen). Die Oberfläche für Music.app (Ziehen, Menü, Import-Rückfrage) ist noch nicht im Browser durchgeklickt.
- Ansicht „DJ“ neben Bearbeiten und Player: zeigt TrackTabs eigene Listen und die aus Music.app und Rekordbox (dort nicht in TrackTab vorhandene Titel markiert wie in Bearbeiten, ohne Editor) in einer festen Liste (Cover, Track und Interpret, Dauer, Tonart, BPM, Energie, Tags). Beim Aufklappen öffnet sich ein Editor mit Gesamtansicht und Zoom-Wellenform, acht Hot Cues, bis zu zehn Memory Cues, Farbwahl, Loops bis 64 Schläge ohne Lücke (Web Audio), Beatgrid- und Phrasen-Bearbeitung per Ziehen, Cue-Taste wie am Player, Quantisieren, Metronom, Rückgängig und Tastenkürzeln. Ausgangspunkt sind die Daten aus Rekordbox oder der eigenen Analyse; Änderungen liegen in der TrackTab-Datenbank, nie in der Datei.
- Waveform der DJ-Ansicht: eigene Einstellungen für die Quelle (Rekordbox mit TrackTab als Rückfall oder nur TrackTab) und die Farbe (Standard, RGB, 3-Band), die tatsächliche Quelle zeigt ein Symbol im Zoom. Die Fläche ist einstellbar (dunkel, hell, UI-Farbe), die Kontur feiner, und bei tiefem Zoom erscheinen die einzelnen Schwingungen des Tons.
- Bearbeiten und Player erzeugen die farbige Wellenform (RGB, 3-Band) jetzt immer selbst, also auch für Tracks, die Rekordbox nicht kennt. Die Farbe wird in der Form der grauen Wellenform weich eingeblendet. Die Einstellung „Waveform-Farbe“ liegt unter Darstellung, Beatgrid, Phrasen und Zoom gibt es dort nicht mehr.
- Loops mit 1/2, 1/4 und 1/8 Schlag und den Tasten I, O, P. Metronom hörbar gemacht.
- Phrasen hängen am Beatgrid: Verschieben oder Dehnen nimmt sie mit.
- DJ-Ansicht: Taste X teilt die Phrase am nächsten Beatgrid-Marker (mit Shift am nächsten Takt 1, per Knopf mit Wahltaste genau an der Abspielposition), auch ohne Q. Hält man eine Beatjump-Taste (A, S, D, F, G, H) einen Moment, erscheint die Sprungweite leicht durchscheinend über der Listenspalte, Wellenform und Tracks bleiben frei.
- DJ-Ansicht: Dateien öffnen wie in Bearbeiten, per Ordner-Symbol oder Drag&Drop. Die Einzelprüfung steht dort mit allen Funktionen zur Verfügung.
- Beatjumps im eigenen Reiter, per Tastatur Buchstabe halten und Pfeiltaste (A 64, S 32, D 16, F 8, G 4, H 2). TAP geht nur bei laufendem Track. Die Spalte BPM der DJ-Liste ändert sich nur bei einem wirklich abweichenden bearbeiteten Tempo und wird rot, wenn das Tempo der Datei nicht zum Beatgrid passt. Neue Spalte Rekordbox: Logo farbig oder grau je nach Sammlung, grün oder rot umrandet je nachdem, ob TrackTab und Rekordbox übereinstimmen.
- Analyse-Sperre in der DJ-Ansicht, je Beatgrid und Phrasen: Eine Bearbeitung sperrt das jeweilige Feld für die Analyse, die Beatgrid-Sperre auch für BPM und Tonart. Die Sperren lassen sich von Hand freigeben und setzen. Sie gilt nur in TrackTab, Rekordbox' eigene Analyse-Sperre wird noch nicht gelesen oder geschrieben.
- Abgleich mit Rekordbox in der DJ-Ansicht: „Rekordbox laden“, „Rekordbox Update“ (schreibt Cues, Loops, Beatgrid und Phrasen nach Rekordbox, mit Sicherung) und „Zu Rekordbox hinzufügen“. Ändert sich ein bearbeiteter Teil zwischendurch in Rekordbox, fragt TrackTab nach. Das Ergebnis ist bisher nur an einer Kopie der Bibliothek geprüft, nicht in Rekordbox selbst.
- Beatgrid & Phrasen (Testfunktion): TrackTab erkennt Tempo, Takt und Abschnitte eines Tracks selbst, wahlweise mit festem Tempo (Normal), mit Tempowechseln (Flex) oder automatisch (Auto). Beatgrid und Abschnitte erscheinen in der Wellenform und lassen sich getrennt ein- und ausblenden; als Quelle ist „Rekordbox, sonst TrackTab“, „Nur Rekordbox“ oder „Nur TrackTab“ wählbar. Es wird nichts in die Datei oder nach Rekordbox geschrieben.
- Wellenform-Zoom im Player: bis 64-fach, die Breite bleibt, der Track läuft unter der mittigen Abspielposition durch. Die Lage gegenüber dem hörbaren Ton ist im echten Abspielen noch nicht geprüft.
- Neu laden während der Wiedergabe: Cmd+R, Cmd+Shift+R und F5 sowie der Link „Seite neu laden“ nach einem Scan oder einer Backup-Wiederherstellung fragen erst nach, solange ein Track oder eine Stem-Hörprobe läuft; „Abbrechen“ lässt die Wiedergabe ungestört weiterlaufen. Beim Reload-Knopf des Browsers, beim Schließen des Tabs und bei Cmd+R über einem offenen Dialog erscheint stattdessen der Hinweis des Browsers (dieser Weg ist noch nicht im echten Browser geprüft). Läuft nichts, wird nie gefragt. Das automatische Neuladen nach dem Speichern von Einstellungen und beim Beenden fragt nicht nach.
- Das Neuladen durch Ziehen über den oberen Fensterrand ist abgestellt, damit es die Wiedergabe in Safari nicht ohne Rückfrage beendet. Noch nicht in Safari geprüft.

- Sammelleisten in Bearbeiten, Player, DJ und den Einzelprüfungen sind in beschriftete Gruppen geordnet (Warteschlange, Playlisten, Merken, Rekordbox, Analyse, Bearbeiten, Prüfung, Öffnen, Export, Löschen), das Menü ⋮ einer Zeile in dieselben Gruppen mit Überschriften. Die DJ-Leiste zeigt Symbole statt Textknöpfen wie in Bearbeiten.
- Eigene Tooltips statt der Browser-Hinweise: erscheinen schneller, beim Gleiten über eine Leiste sofort, bei vielen Aktionen mit kurzer Erklärung in einer zweiten Zeile.
- Bearbeiten: Rekordbox laden, Rekordbox Update und Zu Rekordbox hinzufügen jetzt auch hier (Sammelleiste und Menü ⋮), „Aus Rekordbox entfernen“ steht in der Gruppe Rekordbox. Beatgrid, Phrasen und beides als drei eigene Aktionen. Neu: „Als Nächstes abspielen“ für mehrere Tracks.
- DJ-Ansicht: Menü ⋮ am Zeilenende (Playlisten, Rekordbox, Analyse, Tags bearbeiten, Datei, Löschen), Sammelleiste zusätzlich mit „Zur Playlist hinzufügen“ und „Aus Rekordbox entfernen“; die Warteschlange entfällt dort.
- Player: Menü ⋮ und Sammelleiste können Tracks aus der geöffneten Playlist entfernen.
- Aus einer Music-Playlist entfernen geht auch über das Menü ⋮ einer Zeile, mit eigenem Symbol. Tracks, die nur in Rekordbox stehen, haben in Bearbeiten jetzt ebenfalls ein Menü ⋮ (aus der Playlist bzw. aus Rekordbox entfernen).
- „Aus Rekordbox-Sammlung entfernen …“ heißt jetzt „Aus Rekordbox entfernen …“.
- Sammelleisten: Gruppen-Überschriften stehen auf einer Höhe, Symbole größer und mit mehr Abstand. Neue Symbole: Beatgrid (Spalten), Phrasen (Zeilen), Beatgrid & Phrasen (Raster), Bitrate korrigieren, Format konvertieren und Track neu analysieren.
- Tracks lassen sich auf den Knopf Warteschlange ziehen und werden hinten angehängt.
- Titelzeile: Der Zähler der Auffälligkeiten neben dem Titel entfällt (dafür gibt es die Spalte Auffälligkeiten); die Merklisten-Badges haben leicht abgerundete Ecken wie die Stimmungs-Badges statt Pillenform.
- Scan-Dialog: „Lautheit Scan“ und „Cutoff Scan“ sind standardmäßig ausgeschaltet.
- Konverter übernimmt jetzt auch Tonart und Stimmung in die neue Datei (bisher gingen beide verloren). Die Tonart bleibt in der Schreibweise des Originals; fehlt sie dort, kommt sie aus TrackTabs Datenbank.
- Konverter: neues Ziel **AAC 256 kbit/s** (`.m4a`, Apples Encoder wie Music.app) neben MP3 320 und AIFF. Erlaubt aus verlustfreien Dateien und aus verlustbehafteten ab 256 kbit/s; Tags und Cover werden übernommen.
- AAC-Neukodierung (Bitrate korrigieren, Konverter, calibrate) nutzt automatisch Apples Encoder wie Music.app, über `afconvert` aus einer verlustfreien Zwischenstufe in Gleitkomma (ffmpegs eigene Anbindung an Apples Encoder nimmt nur 16 Bit an und übersteuert laute Quellen hörbar), mit Rückfall auf ffmpegs `aac`. Die neue Datei trifft die Zielbitrate genau. Die Einstellung „AAC-Encoder“ entfällt.
- AAC-Erkennung an Apples Encoder kalibriert (`cutoff_classes.aac`, vorher ungeprüfte Platzhalter): calibrate erkennt AAC-Zweitkodierungen jetzt in 18 von 18 Fällen (vorher 1 bzw. 7 von 18). Nie geänderte alte Platzhalterwerte in den eigenen Einstellungen werden automatisch durch die neuen ersetzt.
- Scan holt ausgelassene Messungen nach: Tracks, die ohne Cutoff bzw. ohne Lautheit gescannt wurden (Verdikt „unbekannt“, kein LUFS-Wert), misst der nächste Scan mit eingeschaltetem Schalter nach, auch wenn sich die Datei nicht geändert hat. Fehlgeschlagene Lautheitsmessungen werden erneut versucht. Bisher blieben solche Tracks bis zu einem vollständigen Scan ohne Wert.
- Markierte Tracks bleiben beim Wechsel zwischen Bearbeiten, Player und DJ markiert (bisher wurde die Auswahl verworfen).
- DJ-Ansicht: Sammelleiste mit denselben Aktionen wie Bearbeiten (Tags, Konvertieren, Bitrate, Umbenennen, Prüfung, Mixed In Key, ZIP, Löschen), ohne Warteschlange, mit Auto Cue.
- Entfernt: feste Rekordbox-Ziel-Playlist (Einstellung „Rekordbox-Playlist“ und der Rekordbox-Knopf je Zeile und in der Sammelleiste). In eine Rekordbox-Playlist kommen Tracks per Drag & Drop, in die Sammlung über „Zu Rekordbox hinzufügen“. Die Rekordbox-History in der Statistik hängt jetzt an der Auswahl von Rekordbox statt an der Ziel-Playlist.
- Playlist-Generator: Die Waveform je Slot ist farbig nach der Einstellung Wellenform (RGB oder 3-Band), zeigt Hot Cues und Memory Cues (eigene Bearbeitung vor Rekordbox) und darunter eine Leiste mit TrackTabs Phrasen. Etwas höher als bisher (40 statt 34 px).
- Einstellungen ruhiger: Kurze Hinweise zu einem Feld stehen hinter einem ? am Namen und erscheinen als Tooltip (auch per Klick und Tastatur); die Suche findet sie weiter. Lange Erklärungen, Beispiele, Warnungen und Voraussetzungen bleiben sichtbar. „Standard wiederherstellen“ ist ein schlichter Knopf „Zurücksetzen“ ohne Rahmen.

### Fixed
- DJ-Ansicht: In Genre, Album und Künstler fehlte die Spalte Track, die Zeilen wurden sehr hoch. Ursache war der Gruppenkopf, der über 100 Spalten reichte und der Track-Spalte die ganze Breite nahm.
- Music.app: Eigene Tracks, die Music nur noch als Cloud-Eintrag ohne lokale Datei führt (passiert z. B., wenn man sie am iPhone zu einer Playlist hinzufügt), gelten in Music-Playlisten nicht mehr als unbekannt und lassen sich wieder in Music-Playlisten eintragen, auch über verknüpfte Playlisten. Zugeordnet wird über Titel, Interpret und Dateigröße (sonst Dauer), nur bei eindeutigem Treffer. Geprüft nur lesend an der echten Mediathek, das Eintragen eines solchen Tracks in eine Playlist nicht.
- Die App lief ohne eigenes Fenster fast immer gedrosselt (App Nap von macOS: nur Effizienzkerne, niedrigste Priorität). Scan, Stems, Analysen und vor allem das Auto-Cue-Training rechnen dadurch ein Vielfaches schneller, das Training an echtem Material etwa neunmal. Während eines Trainings geht der Mac außerdem nicht von selbst in den Ruhezustand.
- Phrasen beginnen frühestens am ersten Beat, nicht mehr in der Stille davor; gilt für neue Messungen, bestehende Analysen und das Schreiben nach Rekordbox.
- Einstellungen: Der Punkt in der Navigation zeigt nur noch ungespeicherte Änderungen statt jede Abweichung von der Vorgabe.
- Shop-Links öffnen im Standardbrowser des Systems, nicht im Browser der Oberfläche.
- Touchpad-Zoom vergrößert nicht mehr die Seite; die Geste zoomt nur noch die Waveform im DJ-Editor, auch in Safari.
- DJ-Editor: Die Seitenleiste behält die Höhe der zehn Memory Cues, die anderen Reiter scrollen darin.
- Die Waveform-Farbe für Bearbeiten und Player sprang in der App nach dem Speichern immer auf den alten Wert zurück, wenn die einmal herauskopierte `config.yaml` noch den früheren Einstellungsnamen trug.
- Beim Entfernen aus der Rekordbox-Sammlung blieben der Cover-Ordner und in TrackTab der festgehaltene Rekordbox-Stand zurück; Player und DJ-Ansicht zeigten danach weiter dessen Cues und Grid.
- Phrasen, die TrackTab in einen Track ohne vorhandene Phrasen schrieb, zeigte Rekordbox nicht an (fehlendes Bit in `ContentLink`).
- Up 2 und Up 3 wurden beim Lesen und Schreiben der Rekordbox-Phrasen falsch zugeordnet.
- `.aif`-Dateien ließen sich nicht zu Rekordbox hinzufügen.
- Das Beatgrid bekam beim Schreiben nach Rekordbox je Schlag leicht schwankende Tempi.
- Hover-Kontrast: Aktive Knöpfe wie Q wurden beim Überfahren weiß auf hell und waren kaum lesbar. Text wechselt beim Hover nicht mehr in die Designfarbe, Text auf der Designfarbe ist je nach Design hell oder dunkel, Beschriftungen auf farbigen Flächen wählen die besser lesbare Farbe.
- Programm-Symbole (Music, Finder, Rekordbox, Mixed In Key) werden ohne transparenten Rand ausgeliefert und wirken dadurch gleich groß.
- Hot-Cue-Farben: nur noch die 16 in Rekordbox geprüften Farben, mit den dort gemessenen Farbwerten, auch im Farbwähler.
- Memory-Cue-Farben: Die Zuordnung zu Rekordbox war um eins verschoben, Rot erschien dort orange. Alle Farben sind jetzt in Rekordbox bestätigt, auch „keine Farbe“. Bereits übertragene Cues ändern sich beim nächsten Update.
- Player: In der Spalte Auffälligkeiten und deren Dialog führt kein Weg mehr zum Bearbeiten der Metadaten.
- DJ-Ansicht, Rekordbox Update: Das Beatgrid beginnt jetzt auf dem Audiobeginn statt mit dem ersten späteren Schlag, die erste Phrase und ein Cue am Anfang liegen damit an derselben Stelle (noch nicht in Rekordbox selbst geprüft).
- DJ-Ansicht: Phrasen werden je Kategorie automatisch durchnummeriert und so an Rekordbox übergeben, soweit Rekordbox die Nummer darstellen kann; die Auswahl von Hand entfällt.
- Rekordbox-Hot-Cues ab D wurden um einen Buchstaben verschoben angezeigt (D als E, E als F), H fehlte: Rekordbox zählt sie als 1, 2, 3, 5, 6, 7, 8, 9.

### Changed
- Zeilenknöpfe „Als Nächstes spielen“ und „Zur Warteschlange“ neutral gefärbt, nur Play behält die Designfarbe.
- Icons: DJ-Ansicht „turntable“, Liste „Album“ „disc-3“ (wie „nach Album gruppieren“).
- Konverter, MP3 320: kodiert über das mitgelieferte `lame` (`-V0 -b 320 -B 320`, jeder Frame 320 kbit/s, Kopf-Kennung `Info`) statt über ffmpeg-CBR. Damit bleibt der Frequenzbereich über 20 kHz erhalten, den LAME bei CBR 320 sonst abschneidet. In Rekordbox und auf einem XDJ-RX3 geprüft. Ohne `lame` Rückfall auf ffmpeg. Bitrate korrigieren und Stems bleiben unverändert.
- Reihenfolge der Ansichten: Player, DJ, Bearbeiten.
- Lizenzen und Bündelung: LightGBM (MIT) ist für das Auto-Cue-Training jetzt im Bundle (vorher nur im Entwicklungsordner, `requirements-train.txt` entfällt), samt SciPy (nur `scipy.sparse`, BSD-3-Clause) und narwhals (MIT); die OpenMP-Laufzeit libomp (Apache-2.0 mit LLVM-Ausnahme) kommt aus Homebrew mit ins Bundle. `THIRD_PARTY_LICENSES.md`, `collect_licenses.py` (neuer Ordner `licenses/native/`) und `NOTICE.md` sind nachgezogen. Auto Cue selbst braucht LightGBM weiterhin nicht. Wo es sich nicht laden lässt (älteres macOS), fehlt nur das Training.
- Der Rekordbox-Bestand (Anwesenheit der Tracks, Statistik) wird nur noch gelesen, wenn Rekordbox in den Einstellungen ausgewählt ist, nicht mehr abhängig von einer verknüpften Rekordbox-Playlist (die Einstellung entfällt).
- Das Abgleich-Menü (Bibliothek scannen, Rekordbox und Music.app abgleichen) steht auch in der DJ-Ansicht zur Verfügung.
- ZIP-Export startet den Download über einen Download-Link statt über eine Navigation der Seite, damit er während der Wiedergabe keinen Hinweis des Browsers auslöst (noch nicht mit einem echten Download geprüft).

## [1.10.0] - 2026-10-30

### Added
- Stem-Trennung: Menü ⋮ → „Stems generieren …“ trennt einen Track lokal (Demucs v4 über onnxruntime auf Grafikchip/Neural Engine per CoreML, ein 4-Minuten-Track in rund 15 s; ohne CoreML auf der CPU rund 40 s) in Acapella und/oder Instrumental. Fortschritt in Prozent; der Dialog lässt sich minimieren und blockiert dann die Oberfläche nicht (Fortschritts-Hinweis unten, bei Abschluss ein klickbarer Hinweis, der die Hörprobe öffnet). Die Hörprobe zeigt je Stem eine Wellenform mit Play/Pause, Stopp und Zeitanzeige; ein Klick oder Ziehen in der Wellenform spielt ab dieser Stelle vor. Erst „Speichern“ (Symbol Diskette; daneben „Löschen“ verwirft die Hörprobe) legt die Datei an: Datei neben dem Original (Name nach Namensmuster, AIFF bei verlustfreier, MP3 320 kbps bei verlustbehafteter Quelle), Tags und Cover vom Haupttrack, Titel mit Zusatz und Genre „Acapella“/„Instrumental“ (Einstellungen → Stems), Eintrag in TrackTab und Music.
- Stem-Hörprobe: Knopf „Herunterladen“ lädt den Stem mit Tags, Cover und Namen wie beim Speichern als Datei herunter, ohne ihn neben das Original zu legen oder in TrackTab/Music/Rekordbox einzutragen; die Hörprobe bleibt (`GET /api/stems/download`).
- Einstellungen → Rekordbox → „Stems in Rekordbox übernehmen“: Ja, mit Cues (Standard) / Ja, ohne Cues / Nein (`stems_rekordbox`). Ohne geladenes Stem-Modell ist die Einstellung ausgegraut und gesperrt, ein Hinweis verlinkt auf Einstellungen → Stems (neues Feld-Attribut `requires` in `settings.GROUPS`). Ebenso gesperrt: die übrigen Stem-Einstellungen ohne Modell, die BPM-/Tonart-Einstellungen ohne libsonare und die Genre-/Stimmungs-Einstellungen ohne Essentia-Modelle (Längenlimit und Kommentar-Feld erst, wenn beide fehlen), jeweils mit Hinweis auf den Grund. Ohne ausgewähltes Rekordbox (Einstellungen → Rekordbox → Rekordbox) sind alle Rekordbox-Einstellungen gesperrt; die Auswahl gibt sie sofort frei, noch vor dem Speichern. Der Hilfetext dort sagte bisher, ohne Auswahl werde die neueste Version verwendet — die Oberfläche schaltet die Integration ohne Auswahl aber ab. Ebenso gesperrt, sobald der zugehörige Schalter aus ist: BPM-/Tonart-Feld schreiben (BPM bzw. Tonart messen), Genre-Vorschläge ab % und Mood-Tag (Genre bzw. Stimmung messen), die Auto-Läufe der Einzelprüfungen, das Kommentar-Feld (Optionen einzeln nach Tonart/Tempo/Energie), die My-Tag-Spalte und der Mindest-Cutoff. Im Rekordbox-Reiter steht der Mindest-Cutoff jetzt unter „Qualitätsprüfung beim Import“, und der Knopf „Rekordbox-Backup-Ordner öffnen“ ist aus Backup & Protokoll dorthin umgezogen. „Parallele Prozesse“ heißt jetzt „Gleichzeitig analysierte Dateien“, steht im Abschnitt Bibliothek (statt als eigene Gruppe „Leistung“ unter Backup & Protokoll) und erklärt, wofür ein kleinerer Wert gut ist. Übernommen wird nur, wenn Haupttrack und Stem dasselbe Format haben (MP3 → MP3, verlustfrei → AIFF); aus AAC wird ein MP3-Stem, dessen Beatgrid nicht passte, er wird nicht nach Rekordbox übertragen.
- Stems in Rekordbox: neuer Track mit BPM, Tonart, Beatgrid, Wellenform, Cover und allen Hot Cues/Memory Cues/Loops des Haupttracks (kopierte ANLZ-Dateien im Ordnerschema der Content-UUID, `contentFile`-Einträge, `DjmdCue`-Zeilen), in denselben Playlists. Die erste Fassung legte die Analyse in einen falsch benannten Ordner ohne `contentFile`-Einträge: Rekordbox verwarf sie beim Start und analysierte neu, der Stem hatte bis dahin keine Vorschau und nie Cues. Die zweite entfernte den Datei-Index `PVBR` aus der DAT-Datei, woraufhin Rekordbox das Beatgrid verwarf; außerdem stand die Samplerate des Haupttracks statt 44,1 kHz in Rekordbox. MP3-Stems werden jetzt ohne Info-Kopf und auf Rekordbox' Zeitachse des Originals geschrieben (gemessener Versatz: 0 ms bei AIFF/WAV, 26–53 ms bei MP3 mit LAME-Kopf, 47 ms bei AAC), damit Grid und Cues genau sitzen. Läuft Rekordbox, wird der Stem vorgemerkt (Tabelle `rekordbox_stem_pending`) und über „Rekordbox abgleichen“ → „Vorgemerktes übertragen“ übernommen. Die Übernahme von Beatgrid und Cues ist noch nicht in Rekordbox selbst geprüft.
- Stem-Trennung nutzt CoreML (GPU + Neural Engine) statt nur der CPU: rund 2,9× schneller bei unhörbarer Abweichung. Beim ersten Mal wird das Modell rund eine Minute lang übersetzt (Cache `models/demucs/coreml-cache/`, ~635 MB, wird mit „Modell entfernen“ gelöscht), danach je App-Start rund 10 s; der Dialog zeigt dabei „Trennmodell wird vorbereitet …“. Scheitert CoreML, rechnet die CPU wie bisher.
- Das Demucs-Modell (~170 MB) wird nicht mit der App ausgeliefert (Lizenz der Gewichte): Einstellungen → Stems → „Modell laden“ (mit Lizenzhinweis und Bestätigung) legt es unter `models/demucs/` im Datenordner ab. Ohne Modell sind alle Stem-Funktionen ausgeblendet. `fetch_models.sh` lädt es nicht mehr.
- Statistik: Bei „Am vollständigsten gehört“ gibt es den Knopf „Playlist aus vollständig Gehörten erstellen“ (bis zu 50 Titel, Standardname „Vollständig gehört · {Jahr}“); die Rangliste liefert dafür bis zu 50 statt 10 Einträge (angezeigt werden weiter 10).
- Stem-Dialog: Auswahl „Qualität“ (Schnell: etwa 15 % kürzere Rechenzeit, an den Abschnittsübergängen selten leichte Artefakte; Genau: volle Überlappung), die letzte Wahl bleibt gemerkt. Eine laufende Trennung lässt sich abbrechen. Dateien über „Längere Dateien überspringen“ (Einstellungen → Stems, `stems_max_minutes`, Standard 15 Minuten) werden nicht getrennt.
- Bibliothekstracks umbenennen: Zeilenmenü „Datei umbenennen“ und Knopf in der Sammelleiste benennen Dateien nach dem Namensmuster um (`POST /api/rename-library`). Datenbank, Playlisten und Merklisten laufen mit. Der Music.app-Track wird auf die neue Datei umgebogen (Playlisten und Wiedergabezähler bleiben); lehnt Music.app das ab, wird die Umbenennung zurückgenommen. In Rekordbox wird der Pfad korrigiert; läuft Rekordbox, wird er vorgemerkt und über „Rekordbox abgleichen“ geschrieben (neue Spalte `rekordbox_pending.prior_path`, Migration automatisch). Das Umbiegen in Music.app (`set location`) ist noch nicht an echtem Material geprüft.
- Einstellungen → Analyse: Teilanalysen lassen sich einzeln abschalten: „BPM messen“, „Tonart messen“, „Genre-Vorschläge“, „Stimmung & Energie“ (`measure_bpm`, `measure_key`, `measure_genre`, `measure_mood`). Das gilt für Knöpfe, Scan und Einzelprüfungen. BPM und Tonart werden dann gar nicht gemessen; Genre und Stimmung teilen sich einen Modelllauf, das abgeschaltete Ergebnis wird nur nicht übernommen. Sind beide Teile einer Analyse aus, verschwinden auch deren Knöpfe.
- Statistik: Neue Rangliste „Am vollständigsten gehört“. Sie zählt Wiedergaben ab 90 % der Tracklänge, bei Gleichstand die Hörzeit in Vielfachen der Tracklänge, sodass ein einmal gehörter 60-Minuten-Mix keinen fünfmal gehörten Track schlägt. Tracks ohne vollständige Wiedergabe oder ohne bekannte Länge fehlen (`top_tracks_relative`).
- Fußzeile: Link „Lizenzen & Drittanbieter“ öffnet einen Dialog mit den genutzten Programmen und Modellen, ihren Lizenzen und dem Hinweis auf die nicht-kommerziellen Modelle.
- CLI `check`: Die Zeile „Flanke“ nennt zusätzlich den Abstand zum Band über der Kante, die neue Zeile „Abschnitte“ die Kante je 20-s-Abschnitt.

### Changed
- Lizenz: TrackTab steht jetzt unter GPL-3.0-oder-später statt MIT, weil die `.dmg` GPL-Software enthält (FFmpeg-Build mit x264/x265, LAME, mpg123, mutagen). Versionen bis einschließlich 1.9.x bleiben unter MIT nutzbar. Neu: `NOTICE.md`, `THIRD_PARTY_LICENSES.md` und Lizenztexte in `licenses/` (auch im App-Bundle), `collect_licenses.py` sammelt die Lizenztexte, `package_sources.sh` packt die Quell-Archive für das GitHub-Release.
- Einstellungs-Dialog neu aufgebaut: Seitennavigation links (Allgemein, Analyse, Verbindungen, Daten, Erweitert) statt Accordions, dazu eine Suche über Bezeichnungen und Hilfetexte. Ein Punkt in der Navigation markiert Seiten mit geänderten Werten. Schalter, Auswahl und Zahlen stehen als Zeile mit dem Bedienelement rechts. Der Speicherort steht nur noch als Tooltip im Fußfeld.
- Einstellungen: Neue Seite „Music.app“ mit der Music-App-Auswahl (vorher Externe Programme) und „Cover aus Music.app übernehmen“ (vorher Bibliothek). „Verwaiste Ordner“ steht direkt neben „Ordner hinzufügen“. Die Hilfe zum Namensmuster ist in Platzhalter, Regeln und Beispiel gegliedert.
- Statistik: Die Aktionen sind nach Bereichen gruppiert (Analyse, Dateien, Tags & Cover, Music.app, Rekordbox, Sonstiges), je Bereich mit Summe. Neu benannt sind unter anderem Scan-Analyse, BPM/Tonart, Genre/Stimmung/Energie, Stems und Papierkorb durch Music.app; unbekannte Aktionen erscheinen mit ihrem Rohnamen.
- Ein offener Dialog sperrt das Scrollen der Seite dahinter.
- Abhängigkeiten aktualisiert: FFmpeg 9.0.2 (bringt OpenSSL 4 statt 3 mit), libsonare 1.10.0, onnxruntime 1.31.1, PyObjC 13.0.
- Cutoff-Analyse: Eine Kante zählt nur noch, wenn das Spektrum darüber bis ganz oben leise bleibt (Plateau-Prüfung, `plateau_min_ratio`). Zusätzlich wird die Kante je 20-s-Abschnitt gemessen (`edge_segments`): Steht sie in fast jedem Abschnitt, steigt die Konfidenz; springt sie zwischen Bitrate-Klassen, nennt die Begründung „vermutlich aus mehreren Quellen zusammengesetzt“; steht sie in einer verlustfreien Datei durchgehend bei ~20 kHz, heißt es „vermutlich aus einer ~320 kbps Quelle exportiert“. Neue DB-Spalten `plateau_db`/`edge_segments` (Migration automatisch); die Werte entstehen erst mit `scan --force`. Angeregt durch einen Vergleich mit whatsmybitrate.

### Fixed
- Cutoff-Analyse: 320er mit einer Lücke im Spektrum (bei 19,2–20,2 kHz, darüber voller Inhalt bis 22 kHz) galten als FAKE oder VERDÄCHTIG. In einer Stichprobe von 538 Dateien betraf das 14 Tracks eines Albums; alle anderen Verdikte blieben unverändert.
- „In den Papierkorb“ scheiterte mit „Datei existiert nicht mehr“, wenn die Datei schon fehlte (z. B. weil Music.app sie beim Entfernen aus der Mediathek selbst in den Papierkorb gelegt hatte) — die Zeile ließ sich danach nicht mehr entfernen. Jetzt wird der Eintrag trotzdem abgeräumt. Verschwindet die Datei erst während des Vorgangs (Music.app legt sie zeitversetzt in den Papierkorb), gilt das ebenfalls nicht mehr als Fehler.
- Statistik „Meistgehörte Titel“: Die Rangliste war nach gesamter Hörzeit sortiert, sodass ein lang gehörter Einzeltrack (eine Wiedergabe) vor oft gehörten Titeln stehen konnte. Jetzt zählt zuerst die Anzahl der Wiedergaben (ab 30 s tatsächlicher Hörzeit), bei Gleichstand die Hörzeit. Die Statistik wird erst nach „Neu berechnen“ neu sortiert.

## [1.9.5] - 2026-10-26

### Added
- Playlist-Builder: Tracks lassen sich aus der Tabelle auf einen Builder im Baum ziehen (auch wenn er nicht offen ist) oder auf das Fenster eines offenen Builders. Sie werden als neuer Slot am Ende angehängt und sofort gesperrt, damit „Alle neu würfeln“ sie nicht ersetzt. Bereits enthaltene Tracks, fehlende Dateien und Einträge aus Music.app/Rekordbox werden übersprungen; die Liste ist auf 200 Slots begrenzt.

### Changed
- Der gebackene Report heißt jetzt `data/tracktab.html` (statt `report.html`), die CSV `data/tracktab.csv`. Die Oberfläche öffnet unter `/tracktab.html`; `/report.html` bleibt als Alias gültig. Alte `report.*`-Dateien in `data/` werden nicht mehr beschrieben und können gelöscht werden. Eigene Werte für `report_path`/`csv_path` bleiben unverändert.

### Fixed
- Farbige Rekordbox-Wellenform blieb gelegentlich schwarz oder grau, während die Hot Cues erschienen. Ursachen: (1) Rekordbox-Abfragen liefen parallel über eine einzige, nicht thread-sichere Datenbankverbindung (jetzt nacheinander, `_query_lock` in `rekordbox.py`); (2) ein Lesefehler wurde still als „nicht in Rekordbox“ gewertet (jetzt meldet der Server `retry`, der Client versucht es bis zu dreimal); (3) nur aus Nullen bestehende Farbdaten ergaben eine schwarze Wellenform. Unbrauchbare Rekordbox-Wellenformen fallen jetzt auf die graue Standard-Wellenform zurück, die Cues bleiben erhalten.

## [1.9.4] - 2026-10-25

### Added
- Sammelleiste: Neuer Knopf „Korrektur zurücknehmen“ neben „Als korrekt markieren“ — hebt die Markierung für alle Ausgewählten auf. Das Icon (Lucide `circle-dashed-check`) steht auch im Zeilenmenü bei „Korrektur zurücknehmen“.

### Changed
- Listenmenü (Seitenleiste): „Spaltenansicht …“ steht in einer eigenen Gruppe mit Trennlinie davor.
- „Aus Rekordbox-Sammlung entfernen“ hat im Zeilenmenü und in der Sammelleiste das Lucide-Icon „delete“.
- Zeilenmenü: Nach „Aus Playlist entfernen“ steht eine Trennlinie.
- Merk-Knöpfe in der Sammelleiste haben dieselbe Deckkraft wie in der Zeile: gedämpft, solange keine Zeile der Auswahl in der Liste steht; voll deckend bei allen (mit Punkt) oder einem Teil (ohne Punkt).

### Fixed
- In den Papierkorb legen: Fehlt die Datei schon auf der Festplatte, scheiterte das mit „kann nicht in Typ alias umgewandelt werden (-1700)“ und der Eintrag blieb stehen. Häufigster Anlass: Music.app legt die Datei beim Entfernen aus seiner Bibliothek selbst in den Papierkorb. Jetzt wird nur noch der Eintrag entfernt; der Hinweis lautet „liegt im Papierkorb“, wenn Music.app es war, sonst „war schon nicht mehr vorhanden“.

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
