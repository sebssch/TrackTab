# TrackTab — Technische Funktionen

> Kurzer Einstieg mit Screenshot, Voraussetzungen und Installation: [readme.md](readme.md). Reine Bedienungsanleitung ohne technische Details: [handbuch.md](handbuch.md). Installation/Build/Terminal-Betrieb: [installation.md](installation.md).

> **⚠️ Das Ergebnis ist eine Prüfliste, kein Urteil.** Ein niedriger Cutoff kann auch von einem bandbegrenzten Master, einem Vinyl-Rip oder einer alten Aufnahme stammen. Hör dir auffällige Tracks an, bevor du etwas löschst. Die Analyse selbst liest nur — deine Audiodateien werden ausschließlich angefasst, wenn du es ausdrücklich anstößt: [Tags bearbeiten](#tags-bearbeiten), Bitrate korrigieren oder Papierkorb.

---

## Schnellstart

```bash
cd ~/Sites/tools/tracktab && ./run.command
```

Das analysiert die Bibliothek, erzeugt den Report und öffnet ihn im Browser. Der erste Lauf über ~9.500 Dateien dauert etwa 20 Minuten, jeder weitere nur noch Sekunden — analysiert wird nur, was neu ist oder sich geändert hat.

Der Report läuft dabei über einen kleinen lokalen Server (nur auf `127.0.0.1`, nichts davon ist im Netz erreichbar). Der ist nötig, damit ausgeblendete Tracks gespeichert werden können — eine reine HTML-Datei kann das nicht. Beenden mit `Strg+C` im Terminal oder mit dem Knopf **Beenden** rechts oben in der Oberfläche.

Wer gar kein Terminal will, baut sich die App (siehe [Als App](#als-app)) und startet sie per Doppelklick. Details zu allen drei Betriebsarten: [installation.md](installation.md).

---

## Befehle

| Befehl | Zweck |
|---|---|
| `./run.command` | Kompletter Durchlauf: Analyse, Report, Browser |
| `./run.command scan` | Nur analysieren (inkrementell) |
| `./run.command scan --limit 200` | Testlauf über 200 Dateien |
| `./run.command scan --force` | Cache ignorieren, alles neu messen |
| `./run.command scan --prune` | Einträge zu gelöschten Dateien aus der DB entfernen |
| `./run.command scan --covers` | Nur Cover-Abgleich mit Music.app für alle bekannten Dateien ohne Cover, kein Neu-Scan |
| `./run.command scan "/Pfad/zum/Ordner"` | Nur einen bestimmten Ordner prüfen |
| `./run.command check "/Pfad/Datei.mp3"` | Eine Datei im Detail, direkt im Terminal |
| `./run.command report` | HTML, CSV und M3U neu erzeugen |
| `./run.command serve` | Oberfläche öffnen (Player, Drag & Drop, Einstellungen) |
| `./run.command serve --port 9000` | Anderer Port, falls 8756 belegt ist |
| `./run.command reclassify` | Nach geänderten Grenzen neu bewerten, ohne Neuanalyse |
| `./run.command recheck-tags` | Auffälligkeiten (Tag-Probleme) für ALLE bekannten Dateien neu bewerten, ohne Neuanalyse (siehe [Auffälligkeiten](#auffälligkeiten-metadaten-probleme)) |
| `./run.command stats` | Zusammenfassung anzeigen |
| `./build_app.sh` | Doppelklickbare App nach `dist/` bauen |
| `./run.command calibrate` | Schwellwerte nachmessen (siehe unten) |
| `./run.command backup create` | Backup der Datenbank jetzt erstellen |
| `./run.command backup list` | Vorhandene automatische Backups anzeigen |
| `./run.command backup restore "/pfad/quality-….db"` | Datenbank aus einem Backup wiederherstellen |

---

## Backup

Jeder `./run.command`-Aufruf prüft beim Start, ob für den heutigen Tag schon ein Backup existiert — falls nicht, wird `data/quality.db` per SQLite `VACUUM INTO` in `backup/` gesichert (Dateiname mit Zeitstempel), höchstens eins pro Kalendertag. Zusätzlich wird beim Beenden von `./run.command serve` bzw. der App (Strg+C, „Beenden" in der Oberfläche, Cmd+Q/Dock) **immer** ein Backup erstellt, unabhängig vom Kalendertag — so bleibt auch bei mehreren Sitzungen am selben Tag jeweils der aktuelle Stand gesichert. In beiden Fällen werden nur die letzten `backup_keep` (Vorgabe: 10) aufbewahrt — ältere fliegen automatisch raus.

In der Oberfläche gibt es unter *Einstellungen → Backup* zusätzlich:
- **Backup jetzt erstellen …** — Export an einen beliebigen Ort deiner Wahl (zählt nicht zur automatischen Rotation).
- **Wiederherstellen …** — nativer Dateidialog, startet direkt im `backup/`-Ordner. Der bisherige Stand wird davor selbst weggesichert, eine Wiederherstellung lässt sich also nötigenfalls rückgängig machen.

Betroffen ist ausschließlich `quality.db` (Messwerte, Ausgeblendet/Merken/Korrigiert-Markierungen) — deine Musikdateien fasst das Tool wie immer nicht an.

---

## Wie die Erkennung funktioniert

Es gibt zwei Modi, je nachdem ob das Format verlustbehaftet ist oder nicht:

- **Modus A (MP3, AAC/M4A):** wie unten beschrieben — gemessener Cutoff vs. deklarierte Bitrate.
- **Modus B (ALAC, FLAC, WAV, AIFF):** keine deklarierte Bitrate vorhanden, deshalb kein Vergleich. Verdikt allein darüber, ob eine harte Encoder-Kante existiert — ihr Vorhandensein in einem eigentlich verlustfreien Container ist der Beweis, nicht ein fester kHz-Wert. ALAC wird dabei über den tatsächlichen Codec von AAC unterschieden, nicht über die (bei beiden identische) `.m4a`-Endung.

**1. Container-Daten.** `ffprobe` liefert deklarierte Bitrate, Modus und Samplerate. Zusätzlich wird der Xing/LAME-Header gelesen: LAME trägt dort seinen eigenen Tiefpass ein. Steht im Header 20,5 kHz, gemessen werden aber 16,8 kHz, stammt die Kante aus einem früheren Encoding — der stärkste Einzelhinweis auf einen Transcode.

**2. Dekodierung.** ffmpeg dekodiert nach Mono in nativer Samplerate. Kein Resampling, das würde eigene Artefakte an der Bandgrenze erzeugen.

**3. Blockauswahl.** Das Signal wird in Blöcke zerlegt; leise Blöcke fliegen raus. Das ist der wichtigste Schritt gegen Fehlalarme — leise Passagen haben von Natur aus keine Höhen und würden sonst einen Tiefpass vortäuschen. Anfang und Ende werden ausgespart (Intro-Stille, Fade-Out).

**4. Spektrum.** Pro Frequenzbin das 95. Perzentil über alle verbleibenden Blöcke. Ein Encoder-Tiefpass bleibt darin als harte Kante stehen, während inhaltlich höhenarme Stellen herausgemittelt werden.

**5. Kantensuche.** Gesucht wird die stärkste Abwärtskante im oberen Spektrum, gemessen als Pegelunterschied zwischen dem Kilohertz darunter und darüber. Das ist bewusst der einzige Maßstab: ein fester Pegelschwellwert scheitert an verlustfreiem Material (dort liegt oben echter Inhalt statt Stille) und an bandbegrenzten Mastern (die weich ausrollen statt abzubrechen). Die Kante trennt beides sauber — nur ein Encoder schneidet senkrecht ab.

Findet sich keine Kante über 15 dB, hat die Datei gar keinen Tiefpass und gilt als bis zur Bandgrenze reichend.

---

## Die Schwellwerte und woher sie kommen

Die Klassengrenzen sind nicht geschätzt, sondern an dieser Bibliothek gemessen: vier verlustfreie Tracks wurden mit LAME auf jede Stufe kodiert und der entstehende Tiefpass gemessen.

| Bitrate | gemessener Cutoff | Kantenhöhe |
|---:|---:|---:|
| verlustfrei | 22,05 kHz (keine Kante) | 5 dB |
| 128 kbps | 16,76 kHz | 66 dB |
| 160 kbps | 17,46 kHz | 63 dB |
| 192 kbps | 18,84 kHz | 61 dB |
| 224 kbps | 19,52 kHz | 61 dB |
| 256 kbps | 19,53 kHz | 61 dB |
| 320 kbps | 20,21 kHz | 58 dB |

Die Streuung über vier völlig verschiedene Tracks lag jeweils unter 0,05 kHz. Die Klassengrenzen in `config.yaml` liegen mittig zwischen diesen Werten:

```
≥ 19,85 kHz → 320er-Klasse
≥ 19,20 kHz → 256er-Klasse
≥ 17,80 kHz → 192er-Klasse
≥ 15,50 kHz → 128er-Klasse
```

Zwei Dinge fallen dabei auf: **224 und 256 kbps benutzen denselben Tiefpass** und sind spektral nicht unterscheidbar. Und **LAME schneidet bei 192 kbps höher als die verbreitete Faustregel** (18,84 statt 18–19 kHz am unteren Rand) — deshalb die gemessenen statt der überlieferten Grenzen.

`./run.command calibrate` erzeugt jederzeit neue Transcodes aus deinen eigenen 320ern und prüft, ob die Erkennung sie zurückfindet. Beim Aufsetzen lag die Trefferquote bei 9 von 9.

---

## Verdikt und Konfidenz

| Verdikt | Bedeutung |
|---|---|
| **OK** | Cutoff passt zur deklarierten Bitrate |
| **VERDÄCHTIG** | Eine Klasse darunter — oder der LAME-Header widerspricht der Messung |
| **FAKE** | Zwei oder mehr Klassen darunter, praktisch sicher hochgerechnet |
| **UNKLAR** | Zu kurz, zu leise oder nicht lesbar |

Die Konfidenz steigt mit der Zahl auswertbarer Blöcke, mit einer harten Kante und mit einem widersprüchlichen LAME-Header; sie sinkt bei weichem Übergang. Ein **FAKE mit hoher Konfidenz und harter Kante** ist so sicher, wie es ohne die Originaldatei geht. Ein **VERDÄCHTIG mit weichem Übergang** ist eher ein bandbegrenztes Master als ein Transcode — genau hinhören.

---

## Lautheit

Neben dem Tiefpass misst jede Datei auch ihre Lautheit nach EBU R128 / ITU-R BS.1770 (ffmpeg-Filter `loudnorm`, Ein-Pass-Analyse): **Integrated Loudness** (LUFS), **True Peak** (dBTP) und **Loudness Range/LRA** (LU).

Anders als beim Tiefpass gibt es hier **kein Verdikt**. Ob −6 oder −14 LUFS "richtig" sind, hängt vom Genre und vom Erscheinungsjahr ab (Loudness War, wechselnde Mastering-Konventionen) — ein leiser gemastertes altes Stück ist nicht falsch. Die Spalte *Lautheit* zeigt deshalb nur den Messwert plus einen weichen Vergleich mit einem konfigurierbaren DJ/Club-Referenzband (Vorgabe: −9 bis −6 LUFS), als Hinweis fürs Gain-Staging vor dem Mix — nicht als Qualitätsurteil.

Die einzige Ausnahme ist die **Clip-Warnung**: liegt der True Peak über einem Schwellwert (Vorgabe: −1 dBTP), droht Intersample-Clipping bei der Wiedergabe oder einer weiteren Umkodierung — das ist unabhängig von Stil oder Epoche immer ein technischer Fehler. Beide Schwellwerte lassen sich unter *Einstellungen → Lautheit* anpassen.

---

## Ausgabe

Alles landet in `data/`:

- **`report.html`** — die eigentliche Oberfläche. Sortier- und filterbar, mit Verdikt, Cutoff, Konfidenz und klickbaren Pfaden. Ein Klick auf eine Zeile klappt das gemessene Spektrum auf, mit der erkannten Kante als roter Linie: die Sichtprüfung zum Messwert. Oben eine Verteilungskurve über die ganze Sammlung.
- **`report.csv`** — dieselben Daten für Numbers oder Excel.
- **`verdaechtig.m3u8`** — Playlist aller auffälligen Tracks zum Gegenhören in rekordbox.
- **`quality.db`** — SQLite mit allen Messwerten, Basis für die Reports.

Im Report kopiert `⧉` einen einzelnen Pfad.

---

## Die Oberfläche

### Ansichten: Bearbeiten und Player

Oben rechts neben `⚙ Einstellungen` schaltet ein zweigeteilter Knopf zwischen zwei Ansichten um:

- **Bearbeiten** — die vollständige Oberfläche wie bisher (Vorgabe).
- **Player** — eine reduzierte Ansicht fürs reine Anhören: Werkzeugleiste (Scan/Rekordbox-/Music.app-Abgleich), Einzelprüfungen samt Drag & Drop sowie CSV-/M3U-Export sind ausgeblendet. Der Seitenbaum bleibt vollständig sichtbar und behält seine Auswahl. In der Knopfleiste jeder Zeile bleiben nur Anhören, Zur Warteschlange hinzufügen, „Musik öffnen" und Merken übrig; die Sammelaktionsleiste bei Mehrfachauswahl bleibt aus denselben Gründen ausgeblendet. Solange ein Scan läuft, lässt sich nicht in die Player-Ansicht wechseln (Knopf deaktiviert, Hinweis-Toast bei einem trotzdem ausgelösten Wechsel).

Ein Wechsel nach Player merkt sich die aktuelle Verdikt-Auswahl sowie die aktive Liste und setzt sie beim Zurückwechseln nach Bearbeiten wieder ein — in Player selbst gilt immer „Alle Verdikte" auf der Liste „Alle".

### Der globale Mediaplayer

Am unteren Bildschirmrand sitzt ein fest angedockter Player — er ist in **beiden** Ansichten dauerhaft sichtbar, unabhängig von einer laufenden Wiedergabe, und ein Wechsel zwischen Bearbeiten und Player unterbricht weder ihn noch die Warteschlange. Er zeigt Cover, Titel, Interpret sowie einen klickbaren Fortschrittsbalken (bewusst ohne Wellenform — die steckt stattdessen in der aufgeklappten Zeile, siehe [Anhören mit Waveform](#anhören-mit-waveform)) und bietet:

- Wiedergabe/Pause, Vorheriger/Nächster Titel (auch über Leertaste bzw. ←/→ — ↑/↓ bewegen stattdessen einen Tastatur-Cursor durch die Tabelle, siehe [Anhören mit Waveform](#anhören-mit-waveform))
- Zufallswiedergabe — ohne geladenen Track startet ein Klick darauf sofort einen zufaelligen Titel aus der aktuell gefilterten Ansicht (siehe [Der globale Mediaplayer — Interna](#der-globale-mediaplayer--interna))
- Titel wiederholen und Liste wiederholen — zwei unabhängige Schalter, kein gemeinsamer Dreizustands-Knopf
- Lautstärkeregler mit eigenem Stummschalten-Knopf, rechts neben dem Fortschrittsbalken — beides übersteht ein Neuladen der Seite

Ein zu langer, abgeschnittener Titel läuft beim Hovern langsam horizontal durch (nur der Titel, nicht der Interpret).

In der **Player-Ansicht** ersetzt ein Klick auf eine Zeile oder ihren `▶`-Knopf sofort die komplette Warteschlange durch die ab dieser Zeile folgenden, zum Klickzeitpunkt gefilterten/sortierten Tracks, auf 25 Titel gedeckelt (Performance) — spätere Filteränderungen wirken sich nicht mehr auf eine schon gestartete Warteschlange aus. In der **Bearbeiten-Ansicht** klappt ein Zeilenklick dagegen nur die Wellenform auf, ohne zu spielen (siehe [Anhören mit Waveform](#anhören-mit-waveform)); erst ein Klick auf `▶` oder den Abspielen-Knopf in der aufgeklappten Wellenform startet dieselbe, hier ebenfalls neu erzeugte Warteschlange. Zwei weitere Knöpfe je Zeile (Öffnen-Spalte, direkt nach Anhören, in **beiden** Ansichten sichtbar) reihen gezielt ein, ohne die laufende Wiedergabe zu unterbrechen — beide mit demselben Linien-Symbol wie der Warteschlange-Knopf, für Wiedererkennungswert: „Als nächstes abspielen" (Linien + Play-Dreieck) setzt den Track direkt hinter den gerade laufenden, „Zur Warteschlange hinzufügen" (Linien + Plus) hängt ihn ans Ende. Beide lassen sich beliebig oft auf denselben Track klicken — auch auf einen bereits laufenden oder gerade erst gehörten, es wird jedes Mal wirklich neu eingereiht, keine Dublettenprüfung. Eine so von Hand ergänzte Warteschlange verliert die 25er-Grenze. Bei mehreren per Checkbox ausgewählten Zeilen bietet die Sammelleiste in der Player-Ansicht denselben „Hinzufügen"-Knopf für die gesamte Auswahl an, daneben die Merklisten-Knöpfe (die übrigen, QC-bezogenen Sammelaktionen bleiben dort ausgeblendet); in der Bearbeiten-Ansicht steht der Knopf nicht in der Sammelleiste, nur an der einzelnen Zeile.

Technisch gibt es dafür nur **einen** gemeinsamen Audio-Knoten (`queueAudio`) für beide Ansichten. Die aufgeklappte Wellenform in Bearbeiten besitzt kein eigenes Audio-Objekt mehr, sondern zeigt lediglich den Fortschritt dieses einen Players an, solange dessen Track mit der aufgeklappten Zeile übereinstimmt — Zuklappen der Zeile stoppt die Wiedergabe deshalb nicht. Die Einzelprüfungen (Drops, siehe [Einzelprüfungen per Drag & Drop](#einzelprüfungen-per-drag--drop)) bleiben davon ausgenommen: Sie haben mangels Datenbank-Zeile keine Warteschlangen-Anbindung und laufen weiterhin über ihr eigenes, unabhängiges Audio-Objekt je Zeile — startet eine Einzelprüfung, pausiert das automatisch den globalen Player und umgekehrt, sodass nie beide gleichzeitig laufen.

Der Knopf „Warteschlange" öffnet ein Popup mit zwei gleich hohen Reitern (kein Höhensprung beim Umschalten): **Warteschlange** (aktueller Track hervorgehoben, plus alles noch Kommende — umsortierbar per Drag & Drop, entfernbar über ✕, per Klick direkt anspielbar) und **Zuletzt gehört** (Hörverlauf, neuester zuerst, gedeckelt auf `QUEUE_HISTORY_LIMIT = 200` — ohne Deckel wüchse er unbegrenzt und würde bei **jedem** Trackwechsel komplett nach `localStorage` serialisiert; ein Klick spielt den Titel erneut und schiebt den bisher laufenden zurück an den Anfang der Warteschlange — einzelne Einträge lassen sich hier bewusst **nicht** entfernen, nur „Leeren" räumt den kompletten Verlauf). „Leeren" leert beide Reiter auf einmal. Beides übersteht ein Neuladen der Seite (Wiedergabe bleibt dabei pausiert, bis der Nutzer sie aktiv fortsetzt).

### Listen und Filter

Die Filter arbeiten auf zwei Ebenen. Links die **Liste** im Seitenbaum — sie legt fest, welche Tracks überhaupt in Frage kommen, und es ist immer genau eine aktiv. Unter dem Wurzelknoten **TrackTab** stehen die festen Listen:

| Liste | Inhalt |
|---|---|
| **Ausgeblendet** | die als Fehlalarm markierten |
| *(Name deiner Merkliste)* | ein Knoten je in den Einstellungen benannter Merkliste, siehe [Merken](#merken) |
| **Datei fehlt** | Einträge, deren Datei nicht mehr da ist |
| **Alle** | ohne Einschränkung |
| **Duplikate** | Tracks mit erkannter Kopie — gleicher Datei-Hash oder gleicher Interpret+Titel+Dauer |

Darunter im selben Ast deine eigenen Playlisten, Ordner und Smart Playlists; daneben die schreibgeschützten Wurzelknoten **Music App** und **Rekordbox**. Siehe [Playlisten und Seitenbaum](#playlisten-und-seitenbaum).

Die Liste **Duplikate** gruppiert die betroffenen Tracks wie „nach Album gruppieren", aber nach Song statt Album. Zwei Wege führen in diese Liste: ein identischer Datei-Inhalt (echte Kopie, egal unter welchem Namen oder Pfad) oder gleicher Interpret+Titel mit fast gleicher Dauer (z. B. derselbe Song einmal als MP3 128 kbps und einmal als FLAC). Bereits gescannte Bibliotheken brauchen für die Hash-Erkennung einmal `./run.command scan --force` — die Tag-basierte Erkennung wirkt sofort. Ein Duplikat-Fall verschwindet aus der Liste, sobald du eine der Kopien in irgendeiner Merkliste ablegst.

Darunter das **Verdikt** als Mehrfachauswahl. Für „nur verdächtige" also *keine* klicken und dann *Verdächtig*; für „nur falsche" entsprechend *Fake*. Jede Schaltfläche trägt ihre Anzahl, sodass du den Umfang siehst, bevor du klickst.

Darunter Suche, deklarierte Bitrate, Konfidenz und „nach Album gruppieren" — die wirken innerhalb der gewählten Liste.

**Die Filterwahl wird gespeichert** und ist beim nächsten Start wieder da. Nur der Suchtext nicht, der gilt immer für den Moment.

Unter `⚙ Einstellungen → Darstellung` lässt sich **Filterleiste anheften** einschalten: dann hält diese ganze Box — Listen, Verdikt, Suche, Filter und die Sammelaktionen — beim Scrollen fest, statt nach oben wegzuschieben. Sie schwebt dabei mit einem Fingerbreit Abstand zum oberen Fensterrand; der weiche Schatten erscheint erst, sobald sie wirklich klebt, und blendet sanft ein und aus. Praktisch, um in einer langen Liste weiter unten noch die Suche oder die Sammelaktionen zu erreichen. Je nach Fensterbreite bricht die Leiste auf mehrere Zeilen um und kostet dann einiges an Höhe — deshalb ist es eine Option und keine feste Vorgabe. Sie gilt sofort, ohne Neuladen.

Unterhalb der Tabelle lädt der große, zentrierte Knopf „weitere N laden" den nächsten Batzen Treffer nach, „alle laden" daneben den kompletten Rest auf einmal — neu geladene Zeilen faden dabei weich ein statt abrupt aufzupoppen. Wer stattdessen lieber einfach weiterscrollt: Unter `⚙ Einstellungen → Darstellung` schaltet **Endlos-Scrollen** das manuelle Klicken ab und lädt beim Erreichen des unteren Listenendes automatisch 50 weitere Treffer nach, mit demselben sanften Fade-in. Auch diese Einstellung wirkt sofort, ohne Neuladen.

Die **Tabellen-Kopfzeile** (Status, Cutoff, Deklariert, …) heftet sich beim Scrollen ebenfalls an — ohne Abstand direkt unter der angehefteten Filterleiste, sodass beide optisch zu einem Block verschmelzen, oder, falls die Filterleiste nicht angeheftet ist, ganz oben am Fensterrand. So bleiben die Spaltenüberschriften auch in einer langen Liste immer sichtbar.

„Nach Album gruppieren" fasst Tracks mit gleichem Album und gleichem Albumkünstler (oder Interpret, falls kein Albumkünstler-Tag gesetzt ist) unter einem gemeinsamen Kopf zusammen — wie in Music.app. Tracks ohne Album-Tag bleiben einzeln. Ein Klick auf eine Spaltenüberschrift schaltet die Gruppierung wieder ab.

### Spalten anpassen

Jede Spalte lässt sich in der Breite ziehen: Maus an den rechten Rand einer Überschrift, der Griff färbt sich, dann ziehen. Die Breite gilt sofort und bleibt gespeichert. Werden die Spalten zusammen breiter als das Fenster, scrollt die Tabelle horizontal — die Auswahl-Checkbox bleibt dabei links stehen, „Öffnen" rechts, beide abgesetzt durch eine senkrechte Trennlinie und ihren Schattenrand. Bleibt Platz übrig, füllt ihn eine leere Zone vor „Öffnen", damit keine Spalte ungefragt mitwächst.

Die Reihenfolge änderst du, indem du eine Überschrift greifst und an ihre neue Stelle ziehst (nur mit laufendem Server — sie liegt in `config.local.yaml`, nicht im Browser). **Auch „Datei" ist eine ganz normale Spalte:** verschiebbar, in der Breite anpassbar, über „Spalten ▾" ausblendbar und per Klick auf die Überschrift nach dem angezeigten Namen sortierbar. Nur die Auswahl-Checkbox links und „Öffnen" rechts bleiben fest — in Position wie in Breite; „Öffnen" richtet seine Breite selbst nach den gerade sichtbaren Aktionen. **„Status" lässt sich ebenfalls über „Spalten ▾" ausblenden** — Position und Breite bleiben dabei fest (nicht per Drag verschiebbar, wie Checkbox und „Öffnen").

Unter „Spalten ▾" liegen die Spalten zur Auswahl alphabetisch sortiert (unabhängig von ihrer tatsächlichen Reihenfolge in der Tabelle), außerdem `Breiten zurücksetzen` und `Reihenfolge zurücksetzen` — beides stellt die Vorgabe wieder her, ohne die Filter anzufassen.

Reihenfolge, Sichtbarkeit und Breite der **Standard-Spalten** werden je Ansicht (Bearbeiten/Player, siehe oben) getrennt gespeichert — eine Änderung in der Player-Ansicht wirkt sich nicht auf die Bearbeiten-Ansicht aus und umgekehrt. Sie gelten in jeder Liste, der keine eigene Spaltenansicht zugeordnet ist.

### Gespeicherte Spaltenansichten

Eine Spaltenansicht ist eine benannte Zusammenstellung aus Reihenfolge, Sichtbarkeit und Breite. Welche Spalten eine Liste zeigt, entscheidet sich in dieser Reihenfolge:

1. die Spaltenansicht, die dieser Liste zugeordnet ist,
2. sonst die **Standard-Spaltenansicht** aus *Einstellungen → Spaltenansichten*,
3. sonst die Standard-Spalten, die zuletzt im Spalten-Menü eingestellt wurden — je Ansicht (Bearbeiten/Player) getrennt.

Das Spalten-Menü hat dafür drei Zeilen: oben ein Auswahlfeld, welche Ansicht die gerade offene Liste verwendet (erster Eintrag = keine eigene), darunter `Ansicht speichern`, `Breiten zurücksetzen` und `Reihenfolge zurücksetzen`, darunter die Spaltenauswahl. `Ansicht speichern` fragt nach einem Namen: ein bereits vergebener überschreibt die betreffende Ansicht, ein neuer legt eine an (höchstens 20). Gespeichert wird immer das, was gerade in der Tabelle steht; die Ansicht übernimmt anschließend die offene Liste, sofern sie dort nicht ohnehin schon gilt.

Umbenennen, Löschen und die Wahl der Standard-Spaltenansicht liegen gesammelt im Einstellungs-Dialog (eigener Block „Spaltenansichten", wie Merklisten und Shops mit eigenem Speichern-Knopf). Eine gelöschte Ansicht nimmt die Zuordnungen darauf mit — die betroffenen Listen fallen auf den Standard zurück.

Zuordnen lässt sich eine Ansicht an zwei Stellen, die dieselbe Zuordnung ändern: im Spalten-Menü für die gerade offene Liste, oder — für jede Liste im Baum, unabhängig davon ob gerade geöffnet — über deren Kontextmenü-Eintrag „Spaltenansicht …" (ein Dialog mit genau diesem einen Feld). Der Bearbeiten-Dialog eigener und Smart Playlists trägt dieses Feld bewusst NICHT mehr — sonst gäbe es zwei Stellen für dieselbe Einstellung.

**Solange eine Ansicht gilt, ändert jede Anpassung an den Spalten diese Ansicht** (und damit jede Liste, die sie ebenfalls verwendet) statt der Standard-Spalten. Anders als die Standard-Spalten sind Spaltenansichten **nicht** nach Bearbeiten/Player getrennt: eine Ansicht ist nur ein Satz Spalten, und eine Zuordnung gilt in beiden Layouts.

### Die Knopfleiste

| Symbol | Aktion |
|---|---|
| ▶ | Abspielen — startet den globalen Mediaplayer, klappt in der Bearbeiten-Ansicht dabei zusätzlich die Waveform auf, siehe [Anhören mit Waveform](#anhören-mit-waveform) |
| Linien + Play-Dreieck | Als nächstes abspielen — siehe [Der globale Mediaplayer](#der-globale-mediaplayer) |
| Linien + Plus | Zur Warteschlange hinzufügen |
| Finder-Symbol | Im Finder zeigen |
| Stift-Symbol | [Tags bearbeiten](#tags-bearbeiten) |
| RX-Symbol | Im Audio-Editor öffnen |
| DAW-Symbol | [In der DAW öffnen](#in-der-daw-öffnen) — nur sichtbar, wenn in den Einstellungen eine DAW ausgewählt ist |
| grünes **b** | Beatport-Suche |
| orange Balken | SoundCloud-Suche |
| Music-Symbol | iTunes Store (kaufen) |
| Kopie | Pfad in die Zwischenablage |
| farbiges Quadrat je Merkliste | [Zu dieser Merkliste hinzufügen/entfernen](#merken) |
| drei Punkte (⋮) „Weitere Optionen" | öffnet ein Menü mit Bitrate korrigieren (Wrench-Symbol, nur wenn `isFixable(r)`), Ausblenden/Einblenden (Auge), Korrigieren/Korrektur zurücknehmen (Haken) und Löschen (roter Papierkorb) — siehe unten |

Grün und Rot sind bewusst vergeben: die Wirkung einer Aktion soll vor dem Klick ablesbar sein. Finder und RX zeigen die **echten Programmsymbole**, die dafür vom jeweils installierten App-Bundle gelesen werden — änderst du den Editor in den Einstellungen, ändert sich das Symbol mit. Für Beatport ist es eine eigene Marke in deren Signalgrün, kein nachgebautes Logo.

**„Weitere Optionen"-Menü (`data-more`, seit dem Buendeln der vier QC-Aktionen hinter einem Knopf):** EIN wiederverwendetes `#rowMoreMenu`-Element (`index.html`) statt eines je Zeile — `openRowMoreMenu(r, btn)` (`app.js`) baut `rowMoreMenuItems(r)` (dieselbe Vorbedingungslogik wie vorher inline: Fix nur bei `isFixable(r)` und `apiMode`/`!r.gone`, Papierkorb nur bei `apiMode`/`!r.gone`, Ausblenden/Korrigieren immer) neu in das Menue, positioniert es per `getBoundingClientRect()` des Knopfs als `position:fixed` und blendet es ein. Rein `position:fixed`-basiert statt in der Zelle verankert, weil `td.links` (sticky + von `th,td{overflow:hidden}` geerbt) ein absolut in der Zelle positioniertes Menue abschneiden wuerde. Schliesst sich bei Klick ausserhalb, Escape, Scroll (`capture:true` auf `window`, faengt auch das nicht bubbelnde Scroll-Event der horizontal scrollenden Tabelle ab) oder Resize, sowie am Anfang jedes `render()`-Laufs (die Zeile, zu der es gehoert, existiert danach ggf. nicht mehr im DOM). In der **Player-Ansicht** ist der Knopf selbst per `body[data-layout="player"] [data-more] { display:none }` ausgeblendet (`app.css`), wie zuvor die vier Einzel-Icons.

Gestartet wird sie über `./run.command serve` oder die gebaute App. Dahinter läuft ein kleiner Server auf `127.0.0.1` — nötig, weil eine reine HTML-Datei weder speichern noch Musik ausliefern kann. Nichts davon ist im Netz erreichbar, und geschrieben wird ausschließlich in die Datenbank, nie an deine Audiodateien.

### Anhören mit Waveform

Gilt für die Ansicht Bearbeiten — die Player-Ansicht klappt Zeilen nicht auf, siehe [Der globale Mediaplayer](#der-globale-mediaplayer).

Ein Klick auf eine Zeile klappt sie auf und zeigt neben dem gemessenen Spektrum die Hüllkurve des Tracks — **noch ohne zu spielen**. Erst ein Klick auf den Abspielen-Knopf innerhalb der Wellenform oder auf `▶` in der Öffnen-Spalte lädt den Track in den globalen Mediaplayer (siehe [Der globale Mediaplayer](#der-globale-mediaplayer)) und startet ihn; die Wellenform zeigt danach live dessen Fortschritt, solange die Zeile aufgeklappt bleibt — ein Klick hinein springt an die Stelle (laedt den Track bei Bedarf zuerst). Es läuft global immer nur ein Track, ein neuer Start pausiert den vorherigen. Ist ein Track zu Ende, spielt automatisch der nächste aus der beim Start gebildeten Warteschlange weiter (dieselbe Warteschlange wie in der Player-Ansicht, siehe dort).

Die **Leertaste** hält den laufenden Track an und lässt ihn weiterlaufen. Läuft noch gar kein Track, startet sie stattdessen den ersten der aktuellen (gefilterten) Liste. Die **Pfeiltasten ←/→** springen zum vorherigen/nächsten Titel in der Warteschlange — unabhängig von der Ansicht. Die Taste **f** springt direkt ins Suchfeld. Diese greifen nicht, während du in ein Eingabefeld tippst.

Unabhängig davon bewegen die **Pfeiltasten ↑/↓** einen rein visuellen Tastatur-Cursor zeilenweise durch die Tabelle (Klasse `.kbcursor`) — rührt an der Checkbox-Mehrfachauswahl nicht an, läuft gerade ein Track, startet der Cursor bei dessen Zeile. Die **Eingabetaste** startet die Wiedergabe der Cursor-Zeile.

Die Hüllkurve wird beim Aufklappen einmalig berechnet (rund zwei Sekunden) und danach serverseitig gespeichert — beim zweiten Mal ist sie sofort da.

Ist der Track bereits in Rekordbox vorhanden **und dort analysiert**, werden zusätzlich Hot Cues (farbige Fähnchen mit Buchstabe) und Memory Cues (farbige Linien mit Dreieck an der Spitze, in der jeweils in Rekordbox eingestellten Farbe) über der (weiterhin grauen) Hüllkurve eingeblendet. Ein Klick auf ein Fähnchen oder eine Memory-Cue-Linie springt im globalen Mediaplayer exakt an diese Stelle und startet die Wiedergabe (laedt den Track bei Bedarf zuerst). Die Zifferntasten **1–8** springen bei einer aufgeklappten Zeile mit geladenen Cues direkt zu Hot Cue A–H (sofern gesetzt) und starten die Wiedergabe dort — ohne Klick. Ist der Track nicht in Rekordbox oder dort nicht analysiert, bleibt es bei der Hüllkurve ohne Cues.

### Im Audio-Editor öffnen

Der Knopf `RX` schickt den Track an einen externen Audio-Editor zum Gegenprüfen im Spektrogramm. Infrage kommt jedes Programm mit Spektrogramm-Ansicht, z. B.:

- [iZotope RX](https://www.izotope.com/en/products/rx.html) — kostenpflichtig, wird automatisch gefunden, wenn installiert.
- [Audacity](https://www.audacityteam.org/) — kostenlos; die Spektrogramm-Ansicht lässt sich pro Spur über *Spurname ▾ → Spektrogramm* aktivieren, siehe das [Audacity-Handbuch](https://manual.audacityteam.org/man/spectrogram_view.html).

Welches Programm der Knopf öffnet, legt *Einstellungen → Externe Programme → Audio-Editor* fest. Ist dort nichts eingetragen und wird auch keines automatisch gefunden, bleibt der Knopf ohne Wirkung — ein Hinweis verweist dann auf die Einstellungen. Der Editor braucht nach dem Klick je nach Programm einige Sekunden zum Starten.

### In der DAW öffnen

Der Knopf öffnet den Track in einer DAW (Digital Audio Workstation), z. B. [Logic Pro](https://www.apple.com/logic-pro/), [Ableton Live](https://www.ableton.com/) oder Cubase — zum direkten Weiterbearbeiten statt nur zum Gegenprüfen wie beim Audio-Editor. Anders als Audio-Editor, Mixed In Key und Rekordbox gibt es hier **keine automatische Suche**: DAWs haben keinen einheitlichen Programmnamen zum Erraten. Deshalb bleibt der Knopf komplett ausgeblendet (nicht nur ausgegraut), solange unter *Einstellungen → Externe Programme → DAW* kein Programm ausgewählt ist. Das Symbol ist wie bei Audio-Editor/Mixed In Key/Rekordbox das echte Programmsymbol der gewählten App.

**Sonderfall Ableton Live:** Anders als Logic oder Cubase importiert Ableton eine per `open -a` übergebene Datei nicht in ein bereits offenes Set — es wird nur das Fenster nach vorne geholt, der Track bleibt draußen (Ableton hat kein AppleScript-Dictionary/CLI, über das sich das erzwingen ließe). Ist DAW auf ein Ableton-Programm gesetzt, erscheint darunter zusätzlich *Ableton-Vorlage* (sonst ausgeblendet, siehe `show_if` in `settings.GROUPS`/`applyShowIf()` in `app.js`) — dort lässt sich ein selbst gebautes Projekt mit der gewünschten FX-Kette auf Spur 1, aber **ohne** eigenen Clip dort, hinterlegen. Der Knopf erzeugt dann bei jedem Klick ein frisches, temporäres Projekt: die Zieldatei wird als Clip **im Arrangement-Fenster** von Spur 1 eingesetzt (nicht in die Session-Ansicht — beide sind in der `.als`-XML zwei komplett getrennte Strukturen; ein früherer Versuch setzte fälschlich dort ein), neu gepackt und geöffnet — ein normaler Projekt-Load, kein Sonderverhalten wie beim `openFile`-Event. Der eingesetzte Clip ist bewusst **nicht** beatgenau gewarpt (`IsWarped` aus, keine Loop-/Warp-Marker-Berechnung) — Ziel ist ein hörbar geladener Track auf Spur 1, kein fertiges Beatgrid. Leeres Feld = die mitgelieferte Standard-Vorlage (`resources/ableton_template.als`, `config.default_daw_template_path()`) wird verwendet, nicht die rohe Audiodatei — nur ein Nicht-Ableton-Programm als DAW fällt auf das einfache `open -a` zurück (siehe `app/ableton.py`, `media.open_in_daw()`).

### Tags bearbeiten

Das Stift-Symbol öffnet einen Bearbeiten-Dialog mit diesen Feldern, in dieser Reihenfolge: **Cover, Titel, Künstler, Album, Track (Nr./von), Albumkünstler, Komponist, Genre, Jahr, BPM, Kommentare.** Geschrieben wird direkt in die Datei, unter demselben Namen am selben Ort (kein Neukodieren wie bei der Bitrate-Korrektur). Cue-Punkte und Analyse-Daten von Serato/Rekordbox bleiben dabei unangetastet.

Titel, Künstler, Album, Albumkünstler, Komponist und Genre schlagen beim Klick ins Feld alle in der Bibliothek bereits vergebenen Werte vor (scrollbare Liste); Weitertippen filtert die Liste auf passende Anfänge. So lassen sich Mehrfachschreibweisen (z. B. „Rock" vs. „rock") vermeiden, ohne die Werte auswendig zu kennen.

`◀ Vorheriger` / `Nächster ▶` blättern innerhalb der aktuellen Liste, ohne den Dialog zu schließen — praktisch, um mehrere Tracks nacheinander durchzugehen. Sind vorher mehrere Tracks per Checkbox markiert, blättern die beiden Knöpfe stattdessen nur zwischen genau diesen markierten Tracks, egal welche anderen Zeilen dazwischenliegen. Ein neues Cover — per Dateiauswahl, Zwischenablage oder per Drag & Drop, auch direkt aus dem Browser (z. B. ein Bild von einer Webseite auf die Cover-Fläche ziehen) — wird erst beim Klick auf „Speichern" übernommen, zusammen mit den Textfeldern.

**In der Tabelle** stehen Cover (60×60-Vorschaubild, Klick öffnet eine Großansicht), Album, Albumkünstler, Komponist, Genre, Jahr, BPM und Kommentar als eigene, über „Spalten ▾" ein-/ausblendbare Spalten — alles direkt aus der Datei gelesen.

**Mehrfachbearbeitung:** Sind mehrere Tracks über die Checkboxen ausgewählt, öffnet dasselbe Stift-Symbol in der Werkzeugleiste den Dialog für alle Ausgewählten (ein neues Cover — auch per Drag & Drop aus dem Browser — gilt dann für alle). Felder, die bei allen gleich sind, werden vorbefüllt; unterschiedliche Felder bleiben leer mit dem Hinweis „· mehrere Werte ·". Nur Felder, die du tatsächlich änderst, werden beim Speichern bei allen Ausgewählten überschrieben — leer gelassene Felder behalten den individuellen Wert jeder Datei. So lässt sich z. B. das Album bei zehn Tracks auf einmal korrigieren, ohne die unterschiedlichen Titel zu verlieren.

**Online-Vorschläge** (Button im Dialog) fragen bei Bedarf iTunes, Deezer und — nur falls beide nichts finden — MusicBrainz nach Interpret/Titel/Album/Genre/Jahr/Cover ab, alle drei ohne Account oder API-Key. BPM liefert nur Deezer, und auch dort nicht für jeden Track. Ein Klick auf einen Vorschlag füllt nur die Formularfelder; gespeichert wird ausschließlich durch den separaten Klick auf „Speichern" — nichts davon läuft automatisch oder wird zwischengespeichert. Findet die Suche über Interpret/Titel nichts, erscheint ein zusätzliches Suchfeld für einen frei wählbaren Suchbegriff (z. B. nur den Interpreten oder eine abweichende Schreibweise). Über den Knopf „Manuelle Suche" daneben lässt sich dasselbe Suchfeld auch ohne vorherigen Online-Versuch direkt öffnen — praktisch, wenn von vornherein klar ist, dass Interpret+Titel nichts finden würden.

**Music.app bemerkt eine Tag-Änderung nicht von selbst:** ein bereits importierter Track zeigt dort weiter die alten Metadaten, bis Music.app ihn selbst aktualisiert — und räumt ihn dabei oft gleich in einen neuen Ordner mit neuem Dateinamen um (siehe „Verschobene Dateien wiederfinden" weiter unten). Der Editor selbst verschiebt oder benennt die Datei nie um. Speicherst du danach an genau diesem Track erneut etwas (z. B. weitere Online-Vorschläge), findet das Tool die inzwischen umsortierte Datei automatisch an ihrem neuen Ort wieder, bevor es scheitert — ohne diese automatische Suche käme sonst die Meldung, die Datei existiere nicht mehr. Ein zuvor in Rekordbox eingetragener Pfad wird dabei nicht mitkorrigiert; dafür hilft weiterhin nur der manuelle Weg unten.

### Auffälligkeiten (Metadaten-Probleme)

Eigene, vom Qualitäts-Verdikt (Korrekt/Verdächtig/Fake/Unklar) unabhängige Prüfung: `app/taganomaly.py` liest bei jeder Analyse zusätzlich die rohen Tag-Frames (ID3-Version, Mehrfach-Frames, Steuerzeichen, Encoding-Artefakte, Cover-Größe/-Gültigkeit, …) und speichert das Ergebnis als JSON-Liste in der neuen DB-Spalte `tag_issues` — pro Fund ein `{code, field, value, suggestion}`-Objekt, siehe [Modulreferenz](#apptaganomalypy). 18 mögliche Codes, unterteilt in **sicher automatisch behebbar** (z. B. Steuerzeichen entfernen, ID3-Version anheben, Genre-Code auflösen) und **nur manuell** (z. B. Mojibake, fehlende Felder, Cover) — die vollständige Tabelle mit Erklärung steht im [Handbuch](handbuch.md#auffälligkeiten-metadaten-probleme).

**Liste „Auffälligkeiten"** liegt im Ordner „Prüflisten" der Seitenleiste. Im Kopf der Liste erscheint dieselbe Bubble-Reihe wie bei Genre/Album/Künstler (`renderIssueBubbles()` in `app.js`, eigenständig neben `renderPlaylistHeaderValueBox()`), hier aber über **Codes** statt Feldwerten: ein Klick filtert (`state.tagIssueFilter`), der Knopf daneben ruft `editAllWithIssueCode(code)` — bei einem automatisch behebbaren Code ein Bulk-Quick-Fix über alle betroffenen Zeilen (3-Worker-Warteschlange wie bei der Bitrate-Korrektur), sonst öffnet er den bestehenden Sammel-Tags-Dialog (`openTagsPopupBulk()`) für genau diese Zeilen.

**Drei gleichwertige Einstiegspunkte** in der Tabelle, alle bedienen denselben Server-Endpunkt: das Warn-Symbol neben dem Dateinamen (`openTagIssuesPopup()`), die über „Spalten ▾" einblendbare Spalte „Auffälligkeiten" (`CELL_RENDERERS.ti`, zeigt Klartext + Knöpfe direkt in der Zelle, ohne Popup) und die Bubble-Leiste oben.

`POST /api/fix-tag-issues` (`{path}`) ruft `taganomaly.fix_safe(path)` — ein einziger `tags.write_tags()`-Aufruf mit bereinigten Feldwerten behebt dabei **alle** sicher behebbaren Probleme dieser Datei auf einmal (nicht nur den einen angeklickten Code), weil `write_tags()` beim Speichern ohnehin jedes ID3v1/v2.2 auf v2.3 anhebt und `setall()` Mehrfach-Frames dedupliziert — siehe Gotcha unten. Danach werden sowohl die DB-Spalten (`db.update_tags()`) als auch `tag_issues` (`db.update_tag_issues()`) aktualisiert, damit die Tabelle nicht bis zum nächsten Scan einen veralteten Wert zeigt.

**Erneute Prüfung:** läuft automatisch bei jeder Analyse mit (`analyse_file()`, siehe unten) — Scan, „Track neu analysieren", Einzelprüfungen, nach einer Bitrate-Korrektur. Für eine reine Verbesserung der Erkennung selbst, ohne dass sich an den Dateien etwas geändert hat, gibt es den deutlich billigeren `recheck-tags`-Weg (CLI-Befehl oder Schalter „Auffälligkeiten neu prüfen" im Scan-Optionen-Dialog, siehe [Scannen aus der Oberfläche](#scannen-aus-der-oberfläche)) — reiner Tag-Read über alle bekannten Dateien, kein ffprobe/Spektral-Durchlauf.

**Gotcha:** `fix_safe()` behebt bewusst nicht nur den einen angefragten Code, sondern alle auto-behebbaren Codes einer Datei gleichzeitig — ein Bulk-Lauf über „alle Dateien mit Fehler X" kann deshalb Zeilen als „übersprungen" melden, obwohl sie tatsächlich (durch einen anderen, gleichzeitig behobenen Code) schon sauber waren, bevor sie an der Reihe waren.

### In den Shops suchen

Drei Knöpfe öffnen die Suche nach dem Track in einem neuen Tab — praktisch, um bei einem Verdachtsfall gleich nach einer sauberen Version zu sehen:

| Marke | Dienst |
|---|---|
| grünes **b** | Beatport |
| orange Balken | SoundCloud |
| Music-Symbol | iTunes Store — zum **Kaufen** |

Sie stehen in der Knopfleiste als eigene Gruppe zwischen zwei Trennstrichen, abgesetzt von den Datei-Aktionen links und rechts.

Die Suchanfrage entsteht aus Artist und Titel, ersatzweise aus dem Dateinamen. DJ-Pool-Zusätze werden dabei entfernt, weil sie im Shop nicht vorkommen und die Suche ins Leere laufen ließen: `(Acapella)`, `[Clean]`, `(Dirty)`, `(Intro)`, `(Instrumental)`, angehängte BPM-Angaben und führende Titelnummern. **Remix- und Mashup-Bezeichnungen bleiben stehen**, denn genau die machen auf Beatport den Unterschied.

Aus `Black Or White (Studio Acapella) [Clean].mp3` wird so `Michael Jackson Black Or White`, aus `Girls$ vs. It Goes Like (WeDamnz Mashup) [134 BPM].mp3` dagegen `Dom Dolla vs. Peggy Gou Girls$ vs. It Goes Like (WeDamnz Mashup)`.

Alle drei benutzen denselben aufbereiteten Suchtext. Es sind reine Links und funktionieren deshalb auch ohne laufenden Server; bei den Einzelprüfungen sitzen sie ebenfalls.

Beatport und SoundCloud öffnen sich als normale Links im Browser. Der **iTunes Store** ist ein Sonderfall und läuft in zwei Schritten:

1. Der Server fragt Apples öffentliche Such-API nach dem Titel. Gesendet wird dabei nur der Suchtext, sonst nichts.
2. Mit der so ermittelten **direkten Adresse des Titels** wird die Music-App geöffnet — ergänzt um `app=itunes`, damit der Aufruf im Store landet und nicht auf der Apple-Music-Seite, von der aus man erst noch weiterklicken müsste.

Der Umweg hat einen Grund: Ein Such-Deeplink lässt die Music-App selbst suchen und landet unzuverlässig; die direkte Titeladresse geht zielsicher auf. Nebenbei liefert die API den **Kaufpreis**, der nach dem Klick in der Statuszeile steht — etwa *„iTunes Store: AFROJACK & Steve Aoki — No Beef (feat. Miss Palmer) [Vocal Mix] · 1,29 EUR"*. Findet die API nichts, sucht die Music-App ersatzweise selbst, und die Statuszeile sagt das.

Bewusst der Store und nicht Apple Music: Apple Music ist Streaming, der iTunes Store ist der Ort zum Kaufen.

### Löschen

Der Knopf `🗑` fragt vorher nach und zeigt dabei nochmal Verdikt, Cutoff, deklarierte Bitrate, Konfidenz und Dateigröße — damit die Entscheidung auf dem Messwert beruht und nicht auf der Zeilenposition. Steht der Track in der Music.app-Bibliothek, heißt der Bestätigen-Knopf „In den Papierkorb verschieben und aus Music Library löschen", sonst nur „In den Papierkorb". Bei mehreren markierten Tracks fragt dieselbe Rückfrage nach der Anzahl statt nach einem einzelnen Namen. **Cmd+Löschen** löst dieselbe Sammelaktion für die per Checkbox markierten Tracks aus, samt derselben Rückfrage.

**Gelöscht wird nie hart, sondern in den Papierkorb.** Ein Fehlgriff bleibt damit umkehrbar, solange du den Papierkorb nicht leerst. Der Eintrag verschwindet zugleich aus der Datenbank; stellst du die Datei später wieder her, taucht sie beim nächsten Scan erneut auf.

Beim ersten Mal fragt macOS um Erlaubnis, den Finder steuern zu dürfen — das ist der Weg, auf dem Dateien in den Papierkorb wandern.

### Im Finder zeigen

Der Knopf `📁` öffnet den Finder mit der markierten Datei. Das geht nur mit laufendem Server: ein `file://`-Link auf einen Ordner landet im Browser bestenfalls in einer Verzeichnisauflistung, deshalb hat das vorher nicht funktioniert.

### Einzelprüfungen per Drag & Drop

Zieh eine oder mehrere Audiodateien irgendwo auf die Seite — sie werden sofort gemessen und in derselben Tabellenstruktur wie die Haupttabelle angezeigt: dieselben Spalten (Cutoff, Konfidenz, Flanke, Lautheit, Album, Genre, …), in derselben Reihenfolge, Breite und Sichtbarkeit — Änderungen unter „Spalten ▾" wirken auf beide Tabellen gleichzeitig, es gibt keine zweite, unabhängige Einstellung dafür. Praktisch, um einen Neuzugang zu prüfen, bevor er in die Bibliothek wandert. Bis zu drei Dateien laufen gleichzeitig; eine 6-MB-Datei dauert etwa eine halbe Sekunde.

Diese Ergebnisse landen **nicht** in der Datenbank und verfälschen damit weder Statistik noch CSV oder M3U. Nicht unterstützte Dateien werden übersprungen und gezählt.

Klick auf eine Zeile klappt sie auf — genau wie in der Haupttabelle — und zeigt Spektrum, Gründe und den Player mit Waveform. Die Aktionsspalte rechts sitzt an derselben Stelle und mit denselben Symbolen wie in der Haupttabelle: Anhören (klappt bei Bedarf gleich mit auf), Stift-Symbol für [Metadaten bearbeiten](#tags-bearbeiten), RX-Symbol zum Gegenprüfen im Spektrogramm, Kreispfeil zum **neu analysieren** — und rechts ein Papierkorb-Symbol, das den Eintrag nur aus dieser Liste entfernt, die Datei selbst bleibt unangetastet. Über `Dateien öffnen` gewählte Dateien haben zusätzlich Stift, RX und Kreispfeil; per Drag & Drop gezogene Dateien nicht, weil der Server ihre Kopie sofort nach der Messung wieder löscht — es gibt also keinen dauerhaften Pfad dafür. Anhören läuft dort stattdessen direkt aus dem Browser-Speicher.

Der Kreispfeil misst die Datei an ihrem Ort noch einmal und liest dabei auch die Tags frisch ein. Vor allem nach „In Mixed In Key öffnen" (siehe Mehrfachauswahl unten) gedacht: Mixed In Key schreibt Key, BPM und Energy erst nach seiner eigenen Analyse in die Datei, also lange nachdem die Zeile hier steht — ein Klick auf den Kreispfeil holt sie herein. Das Ergebnis landet wie jede Einzelprüfung **nicht** in der Datenbank. Zusätzlich prüft er dabei — rein informativ, ohne DB-Schreibzugriff — live, ob die Datei in Music.app bekannt ist (inkl. echtem „Hinzugefügt"-Datum) und, sofern Rekordbox gerade nicht läuft, ob sie dort in der Sammlung liegt; ein fehlendes Cover wird bei einem Treffer in Music.app direkt in die Datei geschrieben. Läuft Rekordbox, erscheint stattdessen ein Hinweis-Toast, dass der Rekordbox-Status übersprungen wurde.

**Mehrfachauswahl.** Über die Kästchen links (und das Kästchen in der Kopfzeile für alle auf einmal) lassen sich mehrere Zeilen markieren; `Shift`-Klick wählt einen ganzen Block. Darüber erscheint dieselbe Leiste wie unter der Haupttabelle, hier mit den Aktionen, die ohne Datenbankeintrag möglich sind: **Tags bearbeiten** für alle Ausgewählten auf einmal (nur geänderte Felder werden geschrieben, alles Unangetastete bleibt je Datei erhalten — genau wie in der Bibliotheksliste), **neu analysieren**, **in Mixed In Key öffnen** (ausgegraut mit Tooltip-Hinweis, solange Mixed In Key nicht in den Einstellungen verknüpft ist) und **aus der Liste entfernen**. Auswählbar sind nur Zeilen mit echtem, dauerhaftem Pfad, also die über `Dateien öffnen` geladenen.

**In Music-Bibliothek importieren.** Ausgewählte Dateien wandern per Knopfdruck wirklich in die Music.app-Bibliothek (nicht nur „In Music öffnen", das den Track lediglich abspielt). Steht in Music.app unter *Einstellungen → Dateien* die Option „Dateien beim Hinzufügen zur Mediathek in den Musik-Media-Ordner kopieren" auf **an**, legt Music.app die Datei dabei in seinen eigenen Medienordner — der Pfad ändert sich also. Music.app benennt die Kopie dabei nach den Tags — aus `SGT-Basic-mastered.mp3` auf dem Schreibtisch wird also z. B. `Musik/SGT/TEST/Flussbühne Intro.mp3`. Das Original bleibt liegen, kopiert heisst nicht verschoben.

**Das Tool zieht den Pfad automatisch nach:** es fragt Music.app, wo der Track jetzt liegt, und schreibt den Eintrag in der eigenen Datenbank auf den neuen Pfad um — samt Ausgeblendet-/Merken-/Korrigiert-Markierungen und den zwischengespeicherten Waveforms. Ohne das stünde der Track beim nächsten Scan doppelt in der Liste: einmal als „Datei fehlt" unter dem alten Pfad und einmal als vermeintlicher Neuzugang. Und liegt die Quelldatei ausserhalb der Bibliothekspfade — Schreibtisch, Downloads —, verschwände die Zeile beim nächsten `--prune` ganz.

**Der Import ist der Übergang in die Bibliotheksliste.** Ab hier liegt die Datei dauerhaft am endgültigen Ort, deshalb wird sie genau dort noch einmal gemessen, bekommt ihre Zeile in der Datenbank und einen Eintrag im Hinzugefügt-Datum. Die Zeile verschwindet danach aus den Einzelprüfungen und steht stattdessen in der grossen Liste — die springt dafür auf die Ansicht „Alle" mit allen Status-Chips, sortiert nach **Hinzugefügt** absteigend und hat die gerade importierten Tracks bereits angehakt. Es kann also direkt eine Sammelaktion darauf laufen (Rekordbox-Playlist, ausblenden, Tags …), ohne sie in elftausend Zeilen zu suchen. Ein neuer Scan ist dafür nicht nötig.

Als Hinzugefügt-Datum steht dabei der Zeitpunkt des Imports; das von Music.app geführte `date added` lässt sich nur über einen vollen Abgleich lesen (`Hinzugefügt-Datum abgleichen`), der den Wert später durch den echten ersetzt. Beides liegt höchstens Sekunden auseinander.

Das greift nur beim Import aus diesem Tool heraus. Wer direkt in Music.app importiert, muss danach neu scannen. Und es setzt voraus, dass der Music-Medienordner unter den Bibliothekspfaden steht — sonst räumt der nächste Scan mit `--prune` diese Zeilen wieder ab. Dieselbe Voraussetzung gilt ohnehin schon für das Nachziehen des Pfades oben.

**Gelöschte Dateien aus der Liste entfernen.** Der Knopf **Aus der Liste entfernen** in der gelben Hinweisleiste räumt die Einträge zu Dateien weg, die es nicht mehr gibt — nur die Datenbank, auf der Platte liegt ohnehin nichts mehr. Die Zeilen verschwinden sofort, ein Neuladen ist nicht nötig. Mit abgeräumt werden die zwischengespeicherten Waveforms und die Rekordbox-/Hinzugefügt-Vermerke dieser Pfade; die Markierungen *Ausgeblendet/Merken/Manuell korrigiert* bleiben dagegen erhalten — falls dieselbe Datei je zurückkommt, gilt die Entscheidung wieder.

**Verschobene Dateien wiederfinden.** Music.app räumt seinen Medienordner nach den Tags auf. Wer die Metadaten hier ändert und den Track danach in Music.app abspielt, findet ihn hinterher unter *Interpret/Album* wieder — neuer Ordner **und** neuer Dateiname. Der Track steht dann in der Liste als „Datei fehlt", obwohl er noch da ist.

Der Knopf **Verschobene suchen** in der gelben Hinweisleiste sucht alle fehlenden Dateien in den Bibliotheksordnern und verknüpft sie neu; in der Zeile selbst tut das Kettensymbol dasselbe für einen einzelnen Track. Erkannt wird an vier Merkmalen: Dateigröße, Dateiname, Interpret + Titel aus den Tags, und Spieldauer. **Mindestens zwei davon müssen stimmen, und der Treffer muss eindeutig sein** — sonst meldet das Tool „mehrdeutig" oder „nicht gefunden", statt zu raten. Ein falsch verknüpfter Pfad wäre schlimmer als gar keiner. Verschoben wird dabei nichts, nur der Eintrag in der Datenbank nachgezogen — samt Ausgeblendet-/Merken-/Korrigiert-Markierungen.

Gesucht wird nur unter Dateien, die die Datenbank noch nicht kennt; was bereits einen eigenen Eintrag hat, ist ja schon verknüpft. Bei einer Bibliothek, die noch nie gescannt wurde, dauert das entsprechend länger (rund 1,5 ms je Datei).

**Automatisch beim nächsten Speichern:** Diesen Abgleich musst du für einen einzelnen Track nicht mehr von Hand anstoßen. Speicherst du Metadaten oder ein Cover an einem Track, den Music.app zwischenzeitlich umsortiert hat, sucht das Tool die Datei zuerst automatisch an ihrem neuen Ort (derselbe Abgleich wie oben, aber nur für den einen betroffenen, in der Music.app-Bibliothek stehenden Track) und aktualisiert die Datenbank im Hintergrund, bevor es die eigentliche Änderung schreibt. Ohne das käme sonst die Meldung, die Datei existiere nicht mehr — genau der Fall, wenn du z. B. gerade Online-Vorschläge übernommen hast und Music.app die Datei danach an den neuen Tags neu einsortiert.

### Dateien automatisch umbenennen

Der Knopf **Dateien automatisch umbenennen** links neben „In Music-Bibliothek importieren" baut für alle ausgewählten Einzelprüfungen aus den Metadaten der Datei einen einheitlichen Dateinamen. Gedacht für frisch gekaufte Tracks: die Dateinamen unterscheiden sich je Shop erheblich, die Tags in der Datei sind dagegen einheitlich.

Das Muster steht in den Einstellungen unter **Dateinamen → Namensmuster**, Vorgabe `{artist}_{title}_{bpm}_{key}_{bitrate}_{year}`. Verfügbar sind `{artist}`, `{title}`, `{album}`, `{albumartist}`, `{composer}`, `{genre}`, `{bpm}`, `{key}`, `{bitrate}`, `{year}`, `{samplerate}` und `{track}`.

**Die Konvention:** Unterstrich trennt die Kategorien, Bindestrich die Wörter innerhalb einer Kategorie — `daft-punk_get-lucky_116bpm_8a_320kbps_2013.mp3`. Alles klein, Umlaute ausgeschrieben (`ä` → `ae`, `ß` → `ss`), Akzente entfernt (`é` → `e`), alles Übrige wird zum Bindestrich, Mehrfach-Bindestriche fallen zusammen.

**Ein Platzhalter ohne Wert lässt seine Kategorie ersatzlos entfallen** — es bleibt keine Lücke (`__`) zurück. Darüber löst sich auch die Qualitätsangabe ohne Sonderregel im Muster: verlustfreie Formate (FLAC/WAV/AIFF/ALAC) haben keine aussagekräftige Bitrate (sie hängt dort nur an Bittiefe und Abtastrate, nicht an einer Kompressionsstufe), `{bitrate}` bleibt leer und die Kategorie verschwindet: `bicep_glue_130bpm_4a_2017.flac`.

Die Tonart `{key}` wird direkt aus der Datei gelesen (ID3 `TKEY`, das MP4-Freiform-Atom, Vorbis `INITIALKEY`) — dort steht sie, wenn Mixed In Key den Track analysiert hat. Sie ist bewusst keine eigene Datenbankspalte: außerhalb des Dateinamens wird sie nirgends angezeigt oder weitergegeben.

**Übersprungen wird** aus drei Gründen, alle mit eigener Meldung im Abschluss-Hinweis:

- **Datei liegt schon in einem Bibliotheksordner** (`library_paths`). Dort hängen Music.app, Rekordbox und Playlisten am Pfad — ein neuer Name würde diese Verweise zerreißen. Das ist die im Feature ausdrücklich verlangte Grenze: umbenannt wird nur, was noch nicht in der Bibliothek ist.
- **Keine identifizierende Angabe** (Interpret, Titel, Album, Albuminterpret, Komponist — soweit das Muster überhaupt danach fragt). Ein Name aus lauter technischen Werten wie `320kbps.mp3` sagt weniger aus als der vorhandene Dateiname und lässt sich nicht zurückholen.
- **Die Datei heißt bereits genau so.**

Existiert der Zielname schon (eine andere Datei mit denselben Tags), wird `-2`, `-3` … angehängt. Der Ordner bleibt in jedem Fall unverändert — es wird nur umbenannt, nie verschoben. Jede Umbenennung landet im [Änderungsprotokoll](#backup).

**Die Zuordnung wird sofort mitgezogen.** Unter dem alten Namen gäbe es die Datei nicht mehr; Abspielen, Tags bearbeiten, Cover, neu messen und der Import würden alle mit „Datei existiert nicht mehr" abbrechen. Serverseitig wandert die Autorisierung auf den neuen Pfad, und falls doch eine Datenbankzeile existiert, wird sie umgeschrieben; im Browser zeigen die Zeilen danach direkt auf den neuen Pfad. Ein Fortschritts-Hinweis läuft mit („3 von 12 umbenannt"), am Ende steht die Bilanz.


### Einstellungen

Unter `⚙ Einstellungen` lassen sich Bibliothekspfade, Analyse-Parameter, Klassengrenzen und die Zahl paralleler Prozesse ändern. Gespeichert wird in `config.local.yaml`; die dokumentierte `config.yaml` mit den Kalibrier-Messwerten bleibt unangetastet und dient als Rückfallebene. Je Gruppe gibt es „Auf Vorgabe", und ein Löschen von `config.local.yaml` setzt alles zurück.

Änderungen an den **Klassengrenzen wirken sofort ohne neue Messung** — Cutoff und Flankenhöhe stehen ja schon in der Datenbank. Die Neubewertung aller 9.500 Dateien dauert unter einer Sekunde. Nur Änderungen an den Analyse-Parametern erfordern einen erzwungenen Scan.

**Rekordbox-Qualitätsprüfung.** Unter *Einstellungen → Rekordbox* liegen jetzt alle Rekordbox-Einstellungen gebündelt: das verknüpfte Programm, die Ziel-Playlist sowie die neue Qualitätsprüfung selbst. Standardmäßig aktiv geprüft wird beim Hinzufügen zu einer Rekordbox-Playlist der bereits beim Scan gemessene Cutoff jedes Tracks gegen eine einstellbare Schwelle (Vorgabe 19 kHz) — unabhängig vom Verdikt und unabhängig davon, ob der Track als „Manuell korrigiert" markiert ist. Es findet dabei **keine neue Analyse** statt, es wird nur der ohnehin vorliegende Messwert verglichen; Tracks ohne verwertbaren Cutoff (z. B. zu kurze Dateien) gelten ebenfalls als Warnung, nicht als automatisch bestanden. Liegt ein einzelner Track darunter, fragt ein Dialog vor dem Hinzufügen noch einmal nach („Abbrechen" / „Trotzdem hinzufügen"); bei einer Mehrfachauswahl mit mehreren betroffenen Tracks listet der Dialog alle Kollisionen (Titel, Interpret, gemessener Cutoff) und bietet zusätzlich „Probleme überspringen und Rest hinzufügen" an. Die Prüfung lässt sich in den Einstellungen jederzeit abschalten.

### Scannen aus der Oberfläche

`Bibliothek scannen` startet den Lauf im Hintergrund, mit Fortschrittsbalken und Abbruch. Danach wird der Report neu erzeugt und die Seite bietet „Neu laden" an.

Der Schalter **„Auffälligkeiten neu prüfen"** im Scan-Optionen-Dialog (`scanOptTagIssues`) läuft unabhängig von „Vollständig": `/api/scan` reicht `recheck_tags` an `jobs.run_scan()` durch, das am Ende (egal ob Dateien zu analysieren waren oder nicht) `taganomaly.recheck_all()` für ALLE bekannten Dateien aufruft — dieselbe Funktion wie beim CLI-Befehl `recheck-tags`, best effort wie `_fill_covers()` (ein Fehler lässt den bereits abgeschlossenen Scan nicht als fehlgeschlagen gelten). Die Statuszeile nennt danach `f.tag_issues_rechecked`.

### Music.app-Abgleich

Der Knopf `Hinzugefügt-Datum abgleichen` fragt bei Music.app das echte „Datum hinzugefügt" für die ganze Bibliothek ab (per AppleScript, dauert bei einigen tausend Tracks gut eine bis zwei Minuten) und füllt damit zwei Spalten — über „Spalten ▾" wie jede andere ein-/ausblendbar:

- **In Music** — Häkchen, ob der Track überhaupt in der Music.app-Bibliothek vorhanden ist. Praktisch, um auf einen Blick Abweichungen zwischen dieser Datenbank und Music.app zu erkennen (z. B. ein Track, der hier analysiert ist, aber nie importiert wurde, oder umgekehrt eine Lücke nach einem Umzug/Backup-Restore).
- **Hinzugefügt** — das Datum selbst.

ID3/MP4-Tags kennen dieses Datum nicht, es steckt ausschließlich in Music.apps eigener Bibliothek; Tracks ohne lokale Datei dort (z. B. rein per Apple-Music-Abgleich in der Cloud) bleiben in beiden Spalten leer. Unabhängig vom Rekordbox-Abgleich — beide laufen getrennt und schreiben in eigene Tabellen.

### Gelöschte Dateien

Löschst du außerhalb des Tools eine Datei, bleibt ihr Messwert zunächst in der Liste und wird als **Datei gelöscht** markiert; Abspielen und Finder sind dort ausgeblendet. Ein Knopf oben räumt diese Einträge aus der Datenbank — die Dateien selbst sind ja bereits weg.

### Verwaiste Ordner finden und löschen

Music.app und Rekordbox räumen ihre Medienordner nach dem Verschieben oder Löschen von Tracks nicht immer vollständig auf — übrig bleiben oft leere Album- oder Interpreten-Ordner, meist mit nichts als einer vom Finder angelegten `.DS_Store`-Datei darin.

Der Knopf **Verwaiste Ordner** unter *Einstellungen → Bibliothek*, direkt unter „Ordner", durchsucht genau diese konfigurierten Bibliotheksordner danach und verschiebt Treffer in den Papierkorb. „Leer" zählt auch mit nichts als `.DS_Store` darin; verschachtelte leere Ordnerketten (ein leerer Ordner, der nur einen weiteren leeren Ordner enthält) werden dabei als Ganzes erkannt — es reicht ein Klick, kein wiederholter Durchlauf. Symlinks werden nie angefasst. Vor dem eigentlichen Verschieben fragt eine Rückfrage noch einmal nach; jeder gefundene Ordner landet einzeln im Papierkorb und lässt sich von dort wiederherstellen, ein permanentes Löschen findet nicht statt.

---

## Als App

```bash
./build_app.sh
```

Erzeugt `dist/TrackTab.app` — doppelklickbar, ohne Python, venv oder Homebrew auf dem Zielrechner. Datenbank, Report und Konfiguration liegen dann unter `~/Library/Application Support/TrackTab/`, weil in ein App-Bundle nicht geschrieben werden darf.

Liegt ein statisch gelinktes `ffmpeg`/`ffprobe` in `vendor/`, wandert es mit ins Bundle und die App ist vollständig eigenständig. Sonst greift sie auf ein installiertes ffmpeg zurück und weist per Dialog darauf hin, falls keines da ist.

Das Bundle ist nicht signiert. Auf deinem Mac startet es ohne Meldung; auf einem fremden Mac hilft beim ersten Mal Rechtsklick → Öffnen.

`build_app.sh` legt zusätzlich eine Verknüpfung `TrackTab` in `/Applications` an. Sie zeigt auf `dist/` und ist damit nach jedem Build automatisch aktuell — sie muss nie erneuert werden.

### Starten und beenden

Die App ist der bequemste Weg, das Tool ganz ohne Terminal zu benutzen.

**Starten:** Doppelklick — auf `dist/TrackTab.app` oder auf die Verknüpfung, die `build_app.sh` in `/Applications` anlegt (sie zeigt auf `dist/` und ist damit nach jedem Build automatisch aktuell). Die App startet den Server auf Port 8756 und öffnet den Report im Standardbrowser.

Läuft sie bereits, startet ein zweiter Doppelklick **keine** zweite Instanz, sondern holt nur den Browser-Tab der laufenden wieder nach vorn. Dasselbe macht ein Klick auf das Dock-Symbol, wenn der Tab versehentlich geschlossen wurde.

**Beenden — drei gleichwertige Wege:**

| Weg | |
|---|---|
| Knopf **Beenden** rechts oben in der Oberfläche | schliesst den Server und beendet die App |
| `Cmd+Q`, solange die App vorn ist | Menü *TrackTab → Beenden* |
| Rechtsklick auf das Dock-Symbol → *Beenden* | |

Alle drei fahren den Server geordnet herunter statt ihn abzuschiessen. Läuft gerade ein Scan, wird vorher gefragt; bereits gemessene Dateien bleiben in jedem Fall gespeichert, der Rest wird beim nächsten Scan nachgeholt.

**Nur das Browser-Fenster zu schliessen beendet die App nicht** — sie läuft dann ohne sichtbares Fenster weiter (das Dock-Symbol bleibt). Ein Klick darauf holt die Oberfläche zurück, `Cmd+Q` beendet sie.

Nach einem Neubau mit `./build_app.sh` backt die App den Report beim nächsten Start automatisch neu, wenn sich an der Oberfläche etwas geändert hat — sonst zeigte sie weiter die alte Version, denn `report` lässt sich aus einem Bundle heraus nicht von Hand aufrufen.

---

## Merken

Bis zu 3 selbst benannte, farbige Merklisten — zum Beispiel eine für „Neu kaufen", eine für „Im Warenkorb", eine für „Zeigen". In `⚙ Einstellungen → Merklisten` legst du für jede der 3 Listen einen Namen (max. 24 Zeichen) und eine Farbe aus 12 Vorgabefarben fest (immer sichtbare Farbreihe, Klick auf eine Farbfläche wählt sie); ein leerer Name blendet die Liste überall aus — du musst also nicht alle 3 nutzen.

Jede Zeile trägt dafür ein farbiges, abgerundetes Quadrat je konfigurierter Liste, direkt neben den übrigen Aktions-Knöpfen. Ein Klick fügt den Track dieser Liste hinzu (ein Häkchen erscheint im Quadrat), ein erneuter Klick entfernt ihn wieder — jeweils mit einer kurzen Bestätigung. Ein Track kann gleichzeitig in mehreren Listen stehen: klickst du zwei Quadrate an, bekommt die Zeile zwei farbige Badges neben dem Dateinamen. Bei einer Mehrfachauswahl erscheinen dieselben farbigen Knöpfe in der Sammelleiste und fügen alle markierten Zeilen auf einmal hinzu.

Das ist bewusst etwas anderes als Ausblenden oder Manuell korrigieren:

| | Bedeutung | Wirkung |
|---|---|---|
| `✕` **ausgeblendet** | Fehlalarm — die Datei ist in Ordnung, ich will sie nicht mehr sehen | raus aus Kennzahlen, CSV, M3U |
| farbiges Quadrat **gemerkt** | reine Kennzeichnung, z. B. für einen späteren Neukauf | bleibt überall sichtbar, auch in CSV/M3U |

Merken schließt sich mit nichts aus — ein Track kann gleichzeitig ausgeblendet oder manuell korrigiert UND in einer oder mehreren Merklisten sein. Über den Listen-Tab deiner Merkliste (erscheint neben „Duplikate", benannt wie in den Einstellungen festgelegt) siehst du nur die Tracks dieser einen Liste.

Praktisch für den Einkauf: den Tab deiner Merkliste öffnen, alle anderen Filter zurücksetzen und dann *CSV Export* — das ergibt eine CSV genau dieser Tracks.

Beim ersten Start nach einem Update übernimmt die neue erste Merkliste automatisch alle bisher als „Erledigt" markierten Tracks und heißt dabei weiterhin „Merken".

---

## Statistik

Über den Knopf **Statistik** in der Kopfzeile öffnet sich eine Jahres-/Monats-Auswertung: wie viele Aktionen (Bitrate-Korrektur, Konvertierung, Tag-Änderungen, Cover, Papierkorb, Umbenennungen, Rekordbox- und Music.app-Abgleich, …) in welchem Monat passiert sind, wie viel insgesamt gehört wurde, und welche Titel/Interpreten/Genres am meisten Wiedergabezeit hatten. Ein Dropdown wechselt zwischen den Jahren, für die es Daten gibt.

Die Zahlen kommen aus zwei bereits vorhandenen, aber bisher ungenutzten Quellen: dem täglichen Änderungsprotokoll (`logs/`, siehe [Änderungsprotokoll](#datenhaltung--oberfläche-glossar)) für die Aktionszähler, und einer Aufzeichnung tatsächlicher Wiedergaben (mindestens 30 Sekunden am Stück gehört) für Hörzeit und Top-Titel. Beide sammeln erst seit Kurzem — je länger TrackTab im Einsatz ist, desto aussagekräftiger wird die Statistik.

Die Auswertung wird beim Öffnen automatisch aktualisiert, wenn seither etwas dazugekommen ist; ein Knopf „Neu berechnen" erzwingt das bei Bedarf auch direkt.

Bei den meistgehörten Titeln steht ein Knopf **Playlist aus Top 50 erstellen** — legt aus den bis zu 50 meistgehörten Titeln des gewählten Jahres eine neue, ganz normale Playlist im Seitenbaum an (Name frei wählbar, erscheint sofort links in der Liste).

---

## Aufräumen: Genre, Album, Künstler

Der feste Seitenleisten-Ordner **Aufräumen** enthält vier Listen: **Genre**, **Album**, **Künstler** und **Duplikate**. Die ersten drei zeigen die komplette Bibliothek gruppiert nach dem jeweiligen Tag-Wert (exakte Gross-/Kleinschreibung, kein Trim — siehe [Aufräumen — Interna](#aufräumen--interna)) statt gefiltert; Duplikate wandert lediglich an diesen Ort, ihre Funktion (siehe [Tracks ausblenden](#tracks-ausblenden) und `computeDuplicateGroups()`) bleibt unverändert. Alle vier unterstützen wie jede andere Liste Spaltenansicht, Warteschlange und Export.

Im Kopf der drei Wert-Listen (`#playlistHeader`/`.plhead`, dieselbe Kopfzeile wie bei jeder anderen Liste) erscheint zusätzlich eine Bubble-Reihe mit allen vorkommenden Werten und ihrer Trackzahl, standardmässig 200px hoch, über einen zentrierten „ausklappen“-Link darunter auf 550px erweiterbar. Das Stift-Symbol an einer Bubble oder an einer Gruppenüberschrift in der Tabelle öffnet einen Umbenennen-Dialog (Titel inkl. Icon und Listenname, z. B. „🏷 Genre: „Acid“ umbenennen“) und benennt den Wert für ALLE betroffenen Tracks auf einmal um: geschrieben wird in die Datei-Tags selbst und — sofern zutreffend — zusätzlich in Music.app (falls der Track dort importiert ist) und Rekordbox (falls dort bekannt), damit alle drei Orte im Gleichschritt bleiben. Läuft Rekordbox gerade, wird die Übernahme dort übersprungen und die Meldung weist darauf hin; die Änderung an Datei und Datenbank erfolgt trotzdem. Bei Album grenzt der Künstler (Album-Künstler, ersatzweise Künstler) die Gruppe zusätzlich ein, damit zwei gleichnamige Alben verschiedener Künstler nie vermischt werden. Ein Klick auf eine Bubble filtert die Tabelle zusätzlich auf genau diesen Wert (`state.valueFilter`).

Der Knopf **Vorschläge** blendet automatisch erkannte Zusammenführungs-Kandidaten ein (vier Arten absteigender Konfidenz: Leerzeichen, Gross-/Kleinschreibung, Tippfehler per Levenshtein-Distanz, Varianten wie Single/Remix — siehe `findMergeSuggestions()`), je mit Grund und einem Augen-Symbol, das die betroffenen Tracks vorab in der Tabelle zeigt (`state.mergePreview`). **Ziel auswählen** öffnet einen Dialog mit beiden Werten zur Wahl (unsichtbarer Leerraum wird dort farblich markiert, siehe `visualizeMergeValue()`); „×“ blendet einen Vorschlag dauerhaft aus (`merge_dismissed`-Tabelle).

---

## Tracks ausblenden

Jede Zeile hat rechts ein `✕`. Damit verschwindet der Track aus der Liste — für Fälle, die du geprüft und für in Ordnung befunden hast. Mit jedem Durchgang wird die Übersicht dadurch schärfer.

Die Entscheidung landet in der Datenbank, nicht im Browser. Sie überlebt damit einen neuen `report`-Lauf und sogar einen kompletten Neu-Scan derselben Datei. Ausgeblendete Tracks zählen nirgends mehr mit: nicht in den Kennzahlen, nicht im Histogramm, nicht in CSV und M3U.

Über *ausgeblendete zeigen* holst du sie zur Kontrolle zurück; dort steht dann `↩` zum Wiedereinblenden. Rückgängig geht also alles — **ausgeblendet heißt ausgeblendet, nicht gelöscht.** Die Audiodatei wird zu keinem Zeitpunkt angefasst.

Wird der Report stattdessen direkt per Doppelklick als Datei geöffnet, fehlt der Server. Dann weicht die Seite auf den Browser-Speicher aus und sagt das oben auch an — die Ausblendungen gelten dann nur in diesem einen Browser und wirken sich nicht auf CSV und M3U aus. Player, Drag & Drop, Finder und Einstellungen brauchen den Server ebenfalls.

---

## Konfiguration

Alles in `config.yaml`, nichts ist hartkodiert. Die wichtigsten Schalter:

```yaml
library_paths: ["~/Music/Music"]   # Wo gesucht wird
extensions: [".mp3"]               # Weitere: .m4a .aac .flac .wav .aiff/.aif (Einstellungen)
min_duration_s: 30.0               # Kürzeres wird nicht beurteilt
max_analysis_s: 420                # Nur die ersten N Sekunden (kappt DJ-Sets)
cliff_min_db: 15.0                 # Darunter gilt: kein Tiefpass vorhanden
rename_pattern: "{artist}_{title}_{bpm}_{key}_{bitrate}_{year}"   # Automatisches Umbenennen
workers: 0                         # 0 = alle Kerne minus einer
```

---

## Grenzen

- **Die AAC-Klassengrenzen sind Platzhalter.** Anders als bei MP3 wurden sie noch nicht an echtem Material kalibriert — `./run.command calibrate` nachziehen, bevor AAC-Verdikte als belastbar gelten.
- **Verlustfrei-Verpackungen (ALAC/FLAC/WAV/AIFF) haben eine eigene, niedrigere Flankenschwelle als MP3.** 16-Bit-PCM quantisiert nahezu stille Höhen oberhalb eines Cutoffs grob, was die messbare Kante gegenüber einer direkt dekodierten MP3 deutlich verflacht (an echtem Material gemessen: ~13–16 dB statt >50 dB). `lossless_min_steepness_db` trägt dem Rechnung, ist aber empfindlicher für Fehlalarme/-negative als die MP3-Erkennung.
- **224 und 256 kbps sind nicht unterscheidbar** — gleicher Tiefpass.
- **Ein bandbegrenztes Master sieht einem Transcode ähnlich.** Die Kantenhöhe trennt beide Fälle gut, aber nicht perfekt. Deshalb die Prüfliste.
- **Der Papierkorb lässt sich nicht aus dem Tool leeren.** Das ist Absicht: endgültiges Löschen bleibt bei dir und beim Finder.
- **Tags bearbeiten aktualisiert Music.app nicht automatisch.** Ein bereits importierter Track zeigt dort weiter die alten Metadaten, bis Music.app ihn selbst neu einliest.
- **Online-Vorschläge liefern selten ein BPM.** Nur Deezer hat dieses Feld überhaupt, und auch dort nicht für jeden Track — iTunes und MusicBrainz liefern gar keins. BPM bleibt meist manuelle Eingabe.
- **Nur der Tiefpass wird bewertet.** Andere Transcode-Spuren (Vorecho, Stereo-Kollaps in den Höhen) fließen nicht ein.
- **Die Lautheit hat bewusst kein Verdikt.** Das Club-Referenzband ist eine grobe Orientierung, kein kalibrierter Wert wie die Bitrate-Klassen — Genre und Erscheinungsjahr sind nicht berücksichtigt.

---

## Web-Oberfläche und Server — Implementierungsdetails

Vertiefung zu den Abschnitten oben, auf Code-Ebene: CSS-/JS-Mechanik, Datenbankfelder, Server-Endpunkte und dokumentierte Bugfixes/Workarounds. Wer nur die Bedienung nachschlagen will, findet die reine Anwendersicht in [handbuch.md](handbuch.md) bzw. weiter oben in diesem Dokument.

### Listen und Filter — Interna

**Filterleiste anheften** ist eine Einstellung (`⚙ Einstellungen → Darstellung`, `cfg["pin_filter_bar"]`), kein eigener Knopf in der Leiste. `state.pinned` wird beim Start aus `/api/settings` übernommen und schaltet `body.pinned`; damit klebt `#filterPanel` per `position:sticky; top:1rem` beim Scrollen am oberen Fensterrand. Der Rahmen bleibt wie bei jedem anderen Block; der Schatten kommt **nur**, solange die Leiste wirklich klebt (`body.pinstuck`), mit einer `transition` von 0,18 s als weichem Übergang. „Klebt gerade" lässt sich in CSS nicht abfragen — `syncPinStuck()` misst dafür beim Scrollen `getBoundingClientRect().top` gegen `PIN_TOP_PX` (16 = 1rem), rAF-gedrosselt auf eine Messung je Bild. Speichern in den Einstellungen wirkt sofort (`postSettings()` ruft `applyPinned()`), ein Neuladen ist nicht nötig.

Unterhalb der Tabelle bauen `#loadmore`/`#loadall` (`render()`, `app/webui/app.js`) auf `beginLoadMore(rows, targetShown)`: die Funktion merkt sich den bisherigen `state.shown`-Stand in der Modul-Variable `freshFrom`, setzt `state.shown` neu und rendert — im Row-Mapping bekommen nur Zeilen mit Slice-Index `>= freshFrom` die CSS-Klasse `fresh` (kurze Fade-in-Keyframe-Animation `rowFadeIn`, unter `@media (prefers-reduced-motion: reduce)` deaktiviert), direkt danach wird `freshFrom` auf `-1` zurückgesetzt, damit ein unabhängiger Render (Sortierung, Ignorieren-Klick, …) keine bereits sichtbaren Zeilen erneut einfaden lässt. **Endlos-Scrollen** ist eine Einstellung (`⚙ Einstellungen → Darstellung`, `cfg["infinite_scroll"]`, `state.infiniteScroll`): `maybeAutoLoadMore()` hängt sich in den bereits vorhandenen, rAF-gedrosselten Scroll-Listener weiter unten ein (ursprünglich nur für `syncPinStuck()`/`syncStickyHeadPosition()`), prüft dort die Nähe zum Dokumentenende und ruft bei aktivierter Einstellung `beginLoadMore(filtered(), state.shown + INFINITE_SCROLL_CHUNK)` auf (`INFINITE_SCROLL_CHUNK = 50`, unabhängig von `PAGE = 150` des manuellen Knopfs). `state.totalFiltered` wird dafür am Anfang jedes `render()`-Laufs aus `rows.length` gecacht, damit der Scroll-Handler nicht bei jedem Frame `filtered()` neu berechnen muss.

**Die Tabellen-Kopfzeile heftet sich beim Scrollen ebenfalls an** — ist die Filterleiste angeheftet, verbindet sie sich ohne Abstand direkt mit deren Unterkante (`stickyHeadTargetTop()` gibt dafür `panel.getBoundingClientRect().bottom` ohne Zuschlag zurück), sonst an den oberen Bildschirmrand (`return 0`). Solange beide wirklich verbunden sind, werden die unteren Ecken der Filterleiste per CSS eckig (`body.pinned.headconnected #filterPanel { border-radius:10px 10px 0 0 }`, die Klasse setzt `syncStickyHeadPosition()` bei jedem Scroll-Frame) — sonst bliebe zwischen der runden Unterkante der Leiste (`.panel`, `border-radius:10px`) und der eckigen Oberkante von `#stickyHead` (`border-radius:0 0 8px 8px`, bewusst ohne `border-top`) ein kleiner Rundungs-Spalt trotz `gap:0`. Ihr eigenes `th { position:sticky; top:0 }` greift dafür nie (`.tablewrap` ist durch `overflow-x:auto` ein eigener Scrollbereich, der vertikal nie scrollt), deshalb pflegt `syncStickyHeadContent()` in `app.js` einen `position:fixed`-Nachbau der Kopfzeile in `#stickyHead`, den `syncStickyHeadPosition()` zeigt/versteckt und plaziert (rAF-gedrosselt beim Scrollen, siehe `stickyHeadTargetTop()`). Der Nachbau bekommt einen eigenen horizontalen Scrollbereich (`#stickyHeadScroll`, unsichtbare Scrollleiste, 2-seitig mit `.tablewrap` gekoppelt über `linkHorizontalScroll()`) statt eines `transform`, damit die eingeklebten Randspalten (`th.sel`/`th.links`) ihr eigenes `position:sticky` behalten. `id`-Attribute (z.B. die Checkbox „selAll") werden beim Klonen entfernt, sonst gäbe es sie zweimal im Dokument; interaktiv bleiben nur die Sortier-Spalten (Klick auf eine geklonte `th[data-k]` löst per `.click()` denselben Klick auf der echten Kopfzelle aus) — das Original bleibt die einzige echte Bedienoberfläche.

„Nach Album gruppieren" clustert Tracks mit gleichem Album **und** gleichem Albumkünstler (Fallback: Interpret, wenn kein Albumkünstler-Tag gesetzt ist) unter einem gemeinsamen Gruppenkopf mit Titelzähler — wie in Music.app. Tracks ohne Album-Tag bleiben einzeln, ein Album mit aktuell nur einem sichtbaren Track bekommt trotzdem einen Kopf. Innerhalb einer Gruppe sortiert die Track-Nummer (aus dem Datei-Tag, sonst ans Gruppenende); ohne Nummer alphabetisch nach Titel. Ein Klick auf einen Spalten-Header schaltet die Gruppierung wieder ab — Spaltensortierung und Albumgruppierung schließen sich gegenseitig aus.

**Duplikate** ist reine Filter-Logik in `app.js`, keine eigene Liste in der Datenbank und kein eigener Scan-Schritt. `computeDuplicateGroups()` läuft einmalig nach dem Laden der Seite (nicht bei jedem `render()`, das wäre bei einer grossen Bibliothek spürbar) und verschmilzt Zeilen über Union-Find zu Gruppen (`r.dg`, Gruppen-ID oder `undefined`), nach zwei Signalen: identischer Datei-Hash (`hs`, SHA-256 über den kompletten Dateiinhalt, berechnet in `analyzer.analyse_file()` und in der neuen DB-Spalte `file_hash` gespeichert) — das erkennt echte Kopien, auch unter anderem Namen/Pfad — sowie normalisiert Interpret+Titel mit Dauer-Toleranz ±1 s (wie `scanner._norm()`/`_DURATION_TOLERANCE_S`) — das erkennt Qualitäts-Varianten desselben Songs (z. B. MP3 128 + FLAC), die nie denselben Hash haben. Fehlt Interpret oder Titel, gruppiert der Tag-Weg nicht (sonst würden alle taglosen Dateien fälschlich in eine Riesengruppe fallen) — solche Zeilen bekommen nur über einen Hash-Treffer eine Gruppe, wie bei `albumGroupKey()` ohne Album-Tag. Die Liste **Duplikate** gruppiert wie „nach Album gruppieren", aber immer nach `r.dg` statt Album, unabhängig vom Checkbox-Zustand; sortiert wird innerhalb der Gruppe nach bester Qualität zuerst (verlustfrei vor verlustbehaftet, dann höhere deklarierte Bitrate). Ein sauber kodiertes Duplikat taucht dort ebenfalls auf — die Liste ist quer zum Status. Ein Fall verschwindet, sobald eine der Kopien in irgendeiner Merkliste landet (`r.f1`/`r.f2`/`r.f3`) — kein eigener Aktions-Knopf nötig, jede Zeile trägt schon die volle Aktionsleiste. Bestehende Bibliotheken brauchen einmalig `scan --force`, damit `file_hash` für bereits gescannte Dateien nachträglich befüllt wird; die Tag-basierte Erkennung wirkt sofort.

### Suchsyntax — Interna

Das Suchfeld ist eine kleine Sprache, kein Freitextvergleich. Sie hat vier Bausteine, und alles Nebeneinanderstehende ist **UND**-verknüpft:

| Baustein | Beispiel | Bedeutung |
|---|---|---|
| Wort | `guetta` | unscharfer Treffer (`fuzzyIncludes()`, Toleranz aus den Einstellungen) |
| Phrase | `"radio edit"` | wortgetreuer Teilstring, **keine** Toleranz |
| ODER-Gruppe | `(house OR dance)` | eine der Alternativen genügt |
| Parameter | `/genre house` | schränkt auf ein Feld ein |

**Ein Parameter nimmt genau ein Element** — ein Wort, eine Phrase oder eine Gruppe. Mehrwortige Werte brauchen deshalb Anführungszeichen (`/genre "deep house"`); ohne sie wäre `house` der Genre-Wert und `deep` ein zweiter, freier Suchbegriff. Das ist die eine bewusste Verhaltensänderung gegenüber der früheren Syntax, in der ein Parameterwert bis zum nächsten `/Parameter` reichte: eine Reichweite, die sich nicht mit Verneinung und Gruppen verträgt (`/No Acapella "David Guetta"` hätte sonst *einen* Ausschluss über beide Begriffe gebildet statt eines Ausschlusses plus einer Suche).

**`/No` ist die einzige Verneinung** und wirkt ebenfalls nur auf das unmittelbar folgende Element: ein Wort, eine Phrase, eine Gruppe oder einen ganzen `/Parameter Wert`-Ausdruck (`/No /Genre (Acapella OR Instrumental)`). Ein zweites `/No` davor kollabiert absichtlich zu einem einzelnen Ausschluss statt zu einer doppelten Verneinung — ein Tippfehler soll den Filter nicht ins Gegenteil kippen.

**Aufbau** (`app/webui/app.js`): `tokenizeSearch()` zerlegt den Text in Elemente (`{type: "param"|"value", start, end, values}`), `splitOrGroup()` löst den Klammerinhalt in ODER-Alternativen auf, `parseSearchQuery()` setzt daraus `{free, filters}` zusammen (Ein-Slot-Cache, sonst würde `matchesSearch()` pro Zeile neu parsen). Jeder Filter trägt `values` (die ODER-Alternativen), `negate` und `start`/`end` — Letzteres, damit ein Chip unter der Suchleiste beim Entfernen genau seinen Abschnitt aus dem Suchtext schneidet. Typografische Anführungszeichen zählen wie gerade (`QUOTE_CHARS`): macOS ersetzt sie beim Tippen, kopierter Text bringt sie ohnehin mit.

**Filterarten** (`applyFilters()`): `field` (Textfeld), `numeric` (`>`/`<`/Bereich über `parseOperatorRange()` — Dauer, deklarierte Bitrate, Konfidenz **sowie Jahr und BPM**; beide sind in der DB ganzzahlig, `0` = kein Tag), `date` (`/Add`, `parseDateRange()`), `verdict` (`/Status`, siehe unten), `ext` (`/Datei`), `hidden` (`/Ausgeblendet`), `exclude` (verneinter Freitext) und `contains` (nur aus den Standard-Suchfiltern der Einstellungen). Mehrere `values` eines Filters sind immer ODER-verknüpft; das implizite UND wirkt ausschließlich zwischen Elementen.

**`/Ausgeblendet`** ist der einzige Parameter **ohne** Wert: er zeigt nur ausgeblendete Tracks (`r.ig`), mit `/No` nur die eingeblendeten. Ein Wert wäre hier sinnlos, deshalb schluckt der Parser nach ihm kein Element — das nächste Wort bleibt normaler Suchtext. Die Liste **Ausgeblendet** im Baum bleibt daneben bestehen; der Parameter ergänzt sie um die Kombination mit anderen Filtern (`/Ausgeblendet /Genre House`) und um die Gegenrichtung innerhalb einer beliebigen Liste.

**Autovervollständigung.** Sie benutzt denselben `tokenizeSearch()` wie die Filterung, statt einer zweiten, leicht abweichenden Regex-Grenzziehung — so gilt für Vorschläge dieselbe Elementgrenze. Parameternamen erscheinen sofort, Werte aus `DATA` erst ab `state.searchAcMinChars` (Vorgabe 3) und entprellt. Ausnahme ist `/Status` (`INSTANT_VALUE_FIELDS`): dessen Werteliste ist kurz und fest (Korrekt, Verdächtig, Fake, Unklar, Manuell korrigiert) und erscheint vollständig, sobald der Parameter steht. Ein mehrwortiger Vorschlag wird **in Anführungszeichen** eingesetzt (`insertText()`) — ohne sie fiele nach der Elementgrenze alles ab dem zweiten Wort aus dem Filter heraus.

### Playlisten und Seitenbaum

Der Seitenbaum ersetzt die frühere Tab-Reihe über der Tabelle (`#viewsRow`) **und** die Verdikt-Schaltflächen darunter (`#statusRow`). Er steuert unverändert `state.view` und damit `currentView()`/`filtered()`. Die Verdikt-Auswahl (`state.verdicts`) ist damit ersatzlos entfallen: sie ist in die festen Status-Listen gewandert (siehe unten), und für eine Einschränkung **innerhalb** einer beliebigen Liste gibt es den Suchparameter `/Status`. Zwei Bedienwege für dieselbe Sache — Chips und Baumknoten — hätten sich sonst gegenseitig überlagert. Aus demselben Grund ist die frühere Liste „Prüfliste" entfallen; Vorgabe-Ansicht ist jetzt „Alle".

**Feste Listen.** `db._SYSTEM_PLAYLISTS` hält beim ersten `connect()` je Prozess (`_setup_once()`) einen Ordner „Prüflisten" mit je einer Smart Playlist pro Status auf Stand — Korrekt, Verdächtig, Fake, Unklar und Manuell korrigiert. Alle tragen denselben Punkt (`●`) als Symbol und unterscheiden sich über die Farbe: die Token `ok`/`susp`/`fake`/`unk`/`accent`, also genau die CSS-Variablen, die auch die Verdikt-Abzeichen in der Tabelle benutzen. Damit zeigen Baum und Abzeichen dieselbe Farbe, in beiden Themes.

Sie tragen `system: 1`. Die Aufteilung dabei ist bewusst: **Name, Symbol, Farbe und Regelsatz gehören dem Programm** — sie werden bei jedem Start neu gesetzt, sodass eine Korrektur an der Definition auch bestehende Datenbanken erreicht, und der Server weist Änderungen daran (`update`) sowie das Löschen ab. **Position und Ordner gehören dem Nutzer** — `parent_id` und `seq` fasst das Seeding nie an, und `move`/`reorder` sind für feste Listen ausdrücklich erlaubt; sonst spränge eine selbst gewählte Anordnung bei jedem Start zurück. Der Client bietet im Menü nur die nicht verändernden Punkte (Warteschlange, M3U8) — ein eigenes Schloss-Symbol zeigt er dafür bewusst nicht (`playlistChildren()` setzt `locked: false`; die Sperre selbst prüft `nodeMenuItems()` weiterhin direkt über `node.system`), damit sich „Prüflisten" optisch nicht von „Aufräumen" unterscheidet, das ebenso gesperrt ist, aber nie eins hatte.

**„Aufräumen“ — ein Ordner ohne Datenbankzeile.** Anders als „Prüflisten" hat der Ordner „Aufräumen" (Genre/Album/Künstler/Duplikate, siehe [Aufräumen: Genre, Album, Künstler](#aufräumen-genre-album-künstler)) keine `playlists`-Zeile — es gibt (noch) keinen Mechanismus für einen DB-losen Ordner um reine `VIEWS`-Einträge, deshalb baut `treeRoots()` ihn rein clientseitig zusammen (`cleanupKids`, dieselbe Knotenform wie `sys`/`own`, `top: true` für Drag & Drop und das „…“-Menü). „Duplikate" zieht dafür aus `VIEW_TABS` heraus (bleibt aber in `VIEWS`, sonst fände `currentView()` sie beim Umschalten nicht mehr) und wird stattdessen Teil von `cleanupKids`.

**Reihenfolge im Baum — zwei Mechanismen, ein Grund.** Innerhalb eines Ordners sind alle Knoten Datenbankzeilen, dort zählt `playlists.seq` (geschrieben über `op: "reorder"`, das die ganze Ebene neu durchnummeriert — einzelne `seq`-Werte zu setzen hinterließe Lücken und Dubletten). Auf der **obersten Ebene** stehen dagegen die festen Ansichten (Alle, Ausgeblendet, Datei fehlt, Merklisten) neben den eigenen Listen und dem Ordner „Aufräumen", und erstere haben keine Datenbankzeile, in der ein `seq` Platz hätte. Deren Anordnung liegt deshalb in `state.treeOrder` (Liste von Baum-Kennungen, `localStorage`, wie die übrigen Ansichts-Einstellungen). Gezogen wird darum die Baum-Kennung (`view:all`, `node:pl123`) und nicht die Datenbank-id — nur so lässt sich beim Ablegen unterscheiden, welche der beiden Sorten unterwegs ist. Die Regel für „Manuell korrigiert" prüft `mc` statt `v` — das ist kein Verdikt, sondern die Markierung aus der Tabelle `corrected`. Die Namen in der Datenbank sind nur ein lesbarer Rückfall; angezeigt wird die übersetzte Fassung über `SYSTEM_PLAYLIST_LABELS` in `app.js`, sonst hinge die Beschriftung an der Sprache, in der sie einmal angelegt wurden.

**Datenmodell.** Zwei Tabellen in `quality.db` (`db._SCHEMA`):

| Tabelle | Spalten | Zweck |
|---|---|---|
| `playlists` | `id` PK, `parent_id`, `kind`, `name`, `icon`, `color`, `rules`, `system`, `seq`, `ts` | Ordner, reguläre Playlisten und Smart Playlists in **einer** Tabelle, unterschieden über `kind` — wie Rekordbox es selbst macht (`DjmdPlaylist.Attribute` 0/1/4). Drei Tabellen würden bei jedem Baum-Durchlauf ein UNION erzwingen. |
| `playlist_items` | `playlist_id`, `path`, `pos`, `ts`, PK `(playlist_id, path)` | Zuordnung mit **manueller Reihenfolge** (`pos`) — die gibt es sonst nirgends im Tool: die Haupttabelle kennt nur Spaltensortierung, `favorites` hat bewusst keine Ordnung. |

`playlist_items` steht in `db._PATH_TABLES`: wandert eine Datei (Music.app-Import, `move_path()`), wandern die Zuordnungen mit. Der Aufbau `(playlist_id, path)` spiegelt `favorites` `(path, list_id)` — deshalb genügt dort der Standardweg, ein `UPDATE ... WHERE path = ?` verschiebt alle Zeilen eines Pfades auf einmal, ohne den Sonderfall, den `events` braucht. Bewusst **nicht** in `_PRUNE_TABLES`: eine Zuordnung ist eine Nutzer-Entscheidung wie `ignored`/`favorites`/`corrected` und soll eine vorübergehend nicht erreichbare Datei (externe Platte) überleben.

`id` ist ein undurchsichtiger Zufallswert (`"pl" + uuid4().hex[:10]`), kein aus dem Namen abgeleiteter Slug wie bei den Shops: Playlisten werden oft umbenannt, und eine ID, die dann nicht mehr zum Namen passt, wäre in Datenbank und Protokollen irreführend.

**Kein `_rebuild()`.** Playlisten gehören zur dokumentierten Ausnahme der Rebuild-Regel (wie `ignored`/`favorites`/`corrected`/`rekordbox`/`music_added`): `report.build_html()` backt die Definitionen als `META.playlists`/`META.playlistItems` ein, damit Baum und zuletzt geöffneter Knoten schon vor dem ersten Fetch stehen; jede Änderung danach zieht der Client live über `GET /api/playlists` nach (`syncMarks()` holt es im selben Bündel). Ein voller Report-Neubau je Umsortierung wäre bei über 10.000 Zeilen und knapp 9 MB `report.html` unbrauchbar.

**Views.** `rebuildPlaylistViews()` erweitert `VIEWS` in-place um einen Eintrag je Liste — `"pl:<id>"` für reguläre, `"sm:<id>"` für Smart Playlists. Getrennte Präfixe, damit die manuelle Reihenfolge in `filtered()` nicht versehentlich für eine berechnete Liste greift. Ordner bekommen keinen View (ein Klick klappt nur auf und zu). Dasselbe In-Place-Verfahren wie `rebuildFavoriteViews()`: `VIEWS` behält seine Array-Referenz. Playlisten stehen bewusst **nicht** in `VIEW_TABS` — der Baum baut sie aus `PLAYLISTS` auf, nicht aus der Tab-Reihenfolge.

**Reihenfolge.** `filtered()` hat einen vierten Sortier-Zweig für `"pl:"`-Views, der nach `PLAYLIST_ITEMS[id]` ordnet. Er greift nur ohne aktive Spaltensortierung: `selectView()` legt `state.sort` beim Wechsel in eine Playlist in `state.sortBeforePlaylist` beiseite und setzt es auf `null`; ein Klick auf einen Spaltenkopf sortiert dann wie überall und hat Vorrang (wie in Music.app), das Verlassen der Liste stellt die vorherige Sortierung wieder her.

**`/Status` in der Suche.** Eigene Filterart (`kind: "verdict"`, `matchesVerdictFilter()`), weil in der Zeile der interne Schlüssel steht (`OK`/`VERDAECHTIG`/…), getippt aber die übersetzte Beschriftung wird — ein Freitextvergleich auf `r.v` träfe nie. Verglichen wird als Anfangsvergleich gegen beides, sodass `/Status verd` genügt und ein englischsprachiger Nutzer `/status susp` tippen kann. „Manuell korrigiert" wird dort wie ein Status behandelt und prüft `mc`, passend zur festen Liste im Baum.

**Drag & Drop.** Natives HTML5-DnD mit zwei eigenen Datentypen (`application/x-tracktab-tracks`, `application/x-tracktab-node`) statt `text/plain`. `initDropzone()`s fensterweite Listener prüfen auf `dataTransfer.types` „Files" — ein interner Zug trägt keine Dateien und löst die Datei-Überlagerung deshalb nicht aus. Zwei getrennte Typen, weil beim `dragover` nur die Typliste lesbar ist, nicht der Inhalt. `treeDropTarget()` entscheidet aus der Mausposition zwischen „hinein" (Ordner/Playlist), „davor" und „dahinter". Wird eine Zeile gezogen, die Teil von `state.selected` ist, wandert die ganze Auswahl mit.

**Tracks innerhalb einer Playlist umsortieren** hing zunächst daran, dass keine Spaltensortierung aktiv war — wer einmal auf einen Spaltenkopf geklickt hatte, konnte danach nichts mehr ziehen und bekam dafür keine Erklärung. Die Ablage-Handler hängen jetzt unabhängig davon an den Zeilen; `reorderInPlaylist()` setzt beim Ablegen `state.sort` zurück, damit das Ergebnis auch sichtbar wird. Gerechnet wird dabei auf der vollständigen Reihenfolge aus `PLAYLIST_ITEMS`, nicht auf den sichtbaren Zeilen: eine Suche oder ein noch nicht nachgeladener Rest der Liste würde sonst beim Speichern verschwinden.

**Rückgängig/Wiederherstellen** folgt dem Entwurfsmuster *Befehl*: jede Änderung wird als Objekt mit `undo()`/`redo()` in `undoStacks` (`Map` je Playlist-ID) abgelegt, der Kopfbereich ruft nur diese Methoden. Jeder Befehl trägt den Zustand **vor** und **nach** der Änderung als vollständige Pfadliste, statt seine Umkehrung aus einem Diff zu rechnen: eine positionsbasierte Umkehrung („Track wieder an Index 4") wird falsch, sobald die Liste zwischendurch von woanders geändert wurde (zweites Fenster, `syncMarks()`, Import), und der Server nimmt mit `op: "set"` ohnehin die ganze Liste entgegen. Bewusst **nicht** persistiert und nach `scan`/`prune`/`relink` verworfen (`clearUndoStacks()`): ein Schnappschuss zeigte sonst auf Pfade, die es nicht mehr gibt. Tastenkürzel Cmd/Strg+Z und Cmd/Strg+Shift+Z, beide nur außerhalb von Eingabefeldern und bei geschlossenen Überlagerungen.

### Smart Playlists — Interna

Regelwerk als JSON in `playlists.rules`: `{match: "all"|"any", rules: [{field, op, value}], limit: {enabled, kind, value, by}}`. Der Server prüft nur die **Form** (`_clean_playlist_rules`: Objekt, höchstens 50 Regeln, höchstens 20 kB) — kein Feld- oder Operatornamen-Abgleich, der sonst bei jeder neuen Regelart an zwei Stellen gepflegt werden müsste.

**Ausgewertet wird ausschließlich im Client.** `DATA` liegt vollständig im Browser; ein Umweg über die Datenbank wäre ein Rundlauf je Klick ohne jeden Gewinn und würde zusätzlich an der `_rebuild()`-Frage hängen. Die Feldliste (`smartFields()`) ist bewusst dieselbe wie in der Suche (`FIELD_LABELS`/`SEARCH_FIELD_ALIASES`), erweitert um das, was die Suche nicht kennt: Verdikt, Format und die Markierungs-Schalter (`mc`/`ig`/`im`/`rb`). Zwei getrennte Vokabulare würden bei jeder neuen Spalte auseinanderlaufen. „enthält" nutzt dieselbe unscharfe Suche wie das Suchfeld (`fuzzyIncludes`, Toleranz aus den Einstellungen); für den genauen Fall gibt es daneben „ist".

**Neuberechnung** läuft nicht bei jedem `render()`, sondern genau an zwei Stellen: einmal beim Start (`recomputeAllSmart()` im Boot-Block) und jedes Mal, wenn der Knoten im Baum angeklickt wird (`selectView()` → `recomputeSmart()`). Bei über 10.000 Zeilen und mehreren Listen wäre ein Durchlauf je Tabellen-Neuaufbau spürbar, und der Inhalt ändert sich ohnehin nur, wenn sich Messwerte oder Markierungen ändern. Ergebnis je Liste ist eine `Set` von Pfaden in `SMART_CACHE`; die `VIEWS`-Prädikate lesen nur daraus.

Die **Begrenzung** (Anzahl / Minuten / MB, ausgewählt nach zuletzt hinzugefügt, niedrigstem Cutoff, Künstler oder Zufall) sortiert **vor** dem Abschneiden — sonst hinge das Ergebnis an der zufälligen Reihenfolge in `DATA`.

**Format-Feld (`fam`) — feiner als `r.fam`.** Die kompakte Zeile kennt nur die grobe `codec_family` (`lossy_mp3`/`lossy_aac`/`lossless`/`lossy_other`) — WAV/AIFF/FLAC/ALAC sind darin alle `"lossless"`, nicht unterscheidbar. `smartFormatOf(r)` löst das für die Regel zusätzlich über die Dateiendung auf (`mp3`/`aac`/`wav`/`aiff`/`flac`/`alac`/`other`), `evalSmartRule()` nutzt diese Funktion statt `r.fam` direkt, sobald `rule.field === "fam"`. Bereits gespeicherte Regeln aus der Zeit vor dieser Aufteilung trugen noch die rohen `codec_family`-Werte als `rule.value` — `SMART_FORMAT_LEGACY` bildet `lossy_mp3`/`lossy_aac`/`lossy_other` weiterhin auf die neuen Werte ab, `"lossless"` wird als Sonderfall direkt gegen `r.fam` geprüft (matcht WAV/AIFF/FLAC/ALAC gemeinsam, wie früher) — ohne das liefe eine bestehende Smart Playlist nach dem Update leer.

**Genre-Regelwert mit Autovorschlägen.** Das Wertfeld einer `ge`-Regel (Text-Input, `.ruleval`) bindet bei jedem `draw()` frisch `attachAutocomplete(el, "ge")` — dieselbe Funktion wie im Tags-Dialog, Vorschläge aus bereits vergebenen Genre-Werten in `DATA`. Eine per Klick/Enter übernommene Vorschlagszeile setzt `el.value` und feuert zusätzlich ein `input`-Event (`pick()` in `attachAutocomplete`) — sonst bliebe `rules[i].value` (nur über `oninput`/`onchange`-Listener synchron gehalten) auf dem alten Stand, weil eine reine Skript-Zuweisung von `.value` keine Events auslöst. Das Dropdown selbst (`.ac-list`) hängt an `.ruleval-wrap` (`position:relative`) statt am `.rulerow` oder gar `.overlay` — sonst würde die absolute Positionierung an den nächsten tatsächlich positionierten Vorfahren binden (`.overlay` deckt den ganzen Bildschirm ab).

Der Regel-Editor arbeitet auf einer **Kopie** des Regelwerks; erst „Speichern" schreibt. Die Live-Vorschau schiebt dafür kurzzeitig ein Probe-Regelwerk durch `recomputeSmart()` und stellt `SMART_CACHE` beim Schließen auf den gespeicherten Stand zurück. Ein Feldwechsel setzt Operator und Wert zurück — ein Textoperator auf einem Zahlenfeld wäre sonst stillschweigend wirkungslos. Die letzte Regelzeile lässt sich nicht entfernen: ein Regelwerk ohne Zeile träfe alles, was als Ergebnis eines Klicks auf „−" niemand erwartet.

### Music.app- und Rekordbox-Playlisten — Interna

Beide Äste sind **schreibgeschützt**: angezeigt wird, was dort steht, geändert wird dort nichts. Geladen wird erst beim Aufklappen — der Music-Ast läuft über AppleScript und würde Music.app starten, der Rekordbox-Ast öffnet `master.db`; beides soll nicht als Nebenwirkung eines Seitenaufrufs passieren. Ordner der Fremdsysteme starten zugeklappt: an echtem Material bringt Rekordbox 993 Knoten in Tiefe 4 mit, alles aufgeklappt ergäbe eine über 30.000 px hohe Leiste.

**Music.app** (`media.music_playlists()` / `media.music_playlist_tracks()`) — drei Eigenheiten des Dictionarys, an echtem Material bestätigt:

1. `every user playlist` enthält die **Ordner nicht**, obwohl `folder playlist` laut Dictionary von `user playlist` erbt. Sie müssen über `every folder playlist` getrennt geholt und über die `persistent ID` mit dem `parent` der Playlisten verknüpft werden. `class of p is folder playlist` bricht mit Fehler **-1731** ab.
2. `location of every track` bricht mit Fehler **-1728** für die *ganze* Liste ab, sobald ein Track keine lokale Datei hat (gemessen: 116 Tracks, davon 10 aus der Cloud). `location of every file track` funktioniert, liefert aber nur die Datei-Tracks und ist damit **nicht positionsgleich** zu `every track`. Deshalb werden drei parallele Listen geholt (`persistent ID of every track`, `persistent ID of every file track`, `location of every file track`) und in Python über die ID verbunden: das erhält die Reihenfolge **und** markiert die Tracks ohne lokale Datei, ohne ein Apple Event je Track (eine 728er-Playlist braucht so 1,6 s statt Minuten).
3. `special kind` unterscheidet die eingebaute Mediathek (`Music`) von eigenen Listen (`none`). Die Mediathek bleibt außen vor — sie entspricht dem eigenen Knoten „Alle".

`parent` und `smart` sind laut Dictionary **schreibgeschützt**, und es gibt **keinen** Befehl, Tracks innerhalb einer Playlist umzusortieren (`move` nimmt als Direktparameter `playlist`, nicht `track`). Das ist der Grund, warum ein späterer Schreibzugriff dort an harte Grenzen stößt.

**Rekordbox** (`rekordbox.playlists()` / `rekordbox.playlist_tracks()`) läuft über `_open_cached()` statt `_open()`: letzteres legt bei jedem Öffnen ein volles Backup von `master.db` an — für eine reine Leseabfrage wäre das eine Kopie der ganzen Datenbank je Klick. `ParentID` ist bei Knoten der obersten Ebene der Text `"root"`, nicht `NULL`, und wird auf `None` normalisiert. Smart Playlists haben keine `DjmdSongPlaylist`-Zeilen; für sie liefert `get_playlist_contents()` das Ergebnis — allerdings **nicht immer**: für zeitbezogene Regeln (`IN_LAST` mit `ValueUnit` „month"/„day") schlägt pyrekordbox 0.4.4 mit `AttributeError` fehl (`DjmdContent.StockDate` hat kein `.month`). Das wird als `SmartListUnsupported` gefangen und als gültige, leere Antwort mit Hinweis gemeldet, damit der Baum nicht wie kaputt wirkt. An echtem Material betrifft das 2 von 12 geprüften Smart Playlists.

**Ersatzzeilen.** Tracks, die TrackTab nicht kennt, bekommen eine Zeile in `DATA` mit dem Merkmal `ext: 1`. Ohne sie ließe sich die Reihenfolge der Liste nicht zeigen — `render()` geht über `DATA` und kennt nur Zeilen mit Index. Diese Zeilen sind überall sonst ausgeschlossen (`live()`, `counted()` und ein eigener Zweig in `filtered()`), damit sie weder Kennzahlen noch Suche noch Duplikaterkennung verfälschen, und tragen im Namen ein Abzeichen: *keine lokale Datei* (Cloud-Track) oder *nicht in TrackTab* (Datei außerhalb der gescannten Ordner). Sie lassen sich weder aufklappen noch abspielen, und „Auto-Relocate" wird für sie nicht angeboten.

Der Abgleich Fremdpfad ↔ eigene Datenbank normalisiert beide Unicode-Formen (`unicodedata.normalize` NFC/NFD): macOS liefert Umlaute je nach Quelle unterschiedlich, ohne den Abgleich über beide Formen gälte „Grüße.mp3" fälschlich als unbekannt.

### Ansichten — Interna

`state.layout` (`"edit"`/`"player"`) treibt `document.body.setAttribute("data-layout", ...)` — dieselbe Mechanik wie `applyTheme()`/`data-theme`. `app.css` blendet über `body[data-layout="player"] ...`-Selektoren die betroffenen Bereiche aus (`#dropsPanel`, `.toolbar`, `#csvsel`/`#m3usel`, sowie in der Öffnen-Spalte `[data-tags]`/`[data-rescan]`/`[data-more]`/`[data-relink]`/`.reveal-link` und die Aktionsgruppen `[data-group="external"]`/`[data-group="search"]`) — rein visuell. **Weder Listenauswahl noch Filter werden beim Wechsel angetastet**: der Seitenbaum steht in beiden Ansichten sichtbar daneben, eine erzwungene Rücksetzung wäre dort nicht nachvollziehbar. Die frühere Sonderbehandlung des Verdikt-Filters (`state.verdicts`, `state.editFilterSnapshot`) ist mit den Verdikt-Schaltflächen entfallen. `applyLayout()` leert ausserdem in **jede** Richtung `state.selected` (die Checkbox-Mehrfachauswahl) — eine Auswahl aus Bearbeiten haette in Player keinen erkennbaren Bezug mehr (andere Spalten/Aktionen) und wuerde dort nur verwirren; die Auswahl ist damit bewusst je Ansicht eigenstaendig statt ansichtsuebergreifend, wie auch schon Spaltenreihenfolge/-breite/-sichtbarkeit (siehe oben). `renderBulkBar()` zeigt bei `state.layout === "player"` eine eigene, reduzierte Sammelleiste (`renderPlayerBulkBar()`, nur „Zur Warteschlange hinzufügen" + Merklisten) statt der vollen — siehe [Der globale Mediaplayer — Interna](#der-globale-mediaplayer--interna). `initDropzone()`s vier fensterweite Drag-Listener (`dragenter`/`dragover`/`dragleave`/`drop`) prüfen `state.layout` selbst als erstes und tun sonst nichts — das Ausblenden von `#dropsPanel` allein würde die Listener nicht stoppen. Während eines Scans (`scanRunning`, von `pollScan()` bei jedem `/api/scan/status`-Aufruf aktuell gehalten) ist der Player-Knopf über `disabled` gesperrt; `applyLayout()` bricht zusätzlich serverseitig ab und zeigt einen Toast, falls der Wechsel trotzdem ausgelöst wird.

`state.layout` liegt wie `state.view` nur in `localStorage` (`saveFilters()`/`loadFilters()`, Schlüssel `tracktab.filters`), nicht in `config.local.yaml` — dieselbe Begründung wie bei anderen rein clientseitigen Filterzuständen.

### Designfarbe — Interna

Settings-Feld `accent_color` (Gruppe „Darstellung", Typ `color` — neu neben `select`/`bool`/… in `settings.py::validate()`, `settingField()` in `app.js`). Zwölf feste Vorgabefarben (`red`/`orange`/`yellow`/`green`/`skyblue`/`blue`/`indigo`/`crimson`/`purple`/`brown`/`slate`/`dustyrose`) statt freiem Farbwähler, gleiches Prinzip wie die 12 Merklisten-Farben (siehe [Merken](#merken)): garantierter Kontrast in Hell/Dunkel ohne Theme-Wissen im Einstellungen-Dialog. `settingField()` rendert eine Schwatch-Reihe (`.colorswatches`/`.colorswatch`, optisch wie `.flswatches`/`.flswatch`) plus ein verstecktes `<input type="hidden" data-key="accent_color">` darunter — die Schwatches selbst tragen keinen `data-key` und schreiben beim Klick nur in dieses versteckte Feld, `collectSettings()` liest es dadurch wie jedes andere Textfeld, ohne eigene Sonderbehandlung dort.

Jede der 8 Farben liegt als CSS-Variablenpaar `--dc-<name>`/`--dc-<name>-bg` in `app.css` (`:root`, `@media(prefers-color-scheme:dark)` und `:root[data-theme="dark"]`, dieselben drei Stellen wie die uebrigen Theme-Variablen). `applyAccentColor(v)` (`app.js`, Pendant zu `applyTheme()`) setzt `data-accent="<name>"` auf `<html>`; `:root[data-accent="..."] { --accent: var(--dc-...); --accent-bg: var(--dc-...-bg); }`-Regeln legen sich darueber `--accent`/`--accent-bg` um — wirkt dadurch ueberall, wo diese beiden bereits verwendet werden (aktive Knoepfe, Player-Fortschritt, Chips, …), ohne dass jede Stelle einzeln auf `--dc-*` umgestellt werden muesste. Kein `data-accent`-Attribut fuer `"blue"`: das ist unveraendert die Vorgabe von `--accent`/`--accent-bg` selbst, die bisherige, fest verdrahtete Farbe bleibt so ohne Sonderfall das Verhalten ohne diese Einstellung. Aufgerufen wie `applyTheme()`/`applyFontSize()` einmal beim Laden (`initStorage()`) und erneut nach dem Speichern (`postSettings()`), beides gegen `data.values.accent_color`.

Spalten-Reihenfolge/-Sichtbarkeit/-Breite liegen je Ansicht getrennt in `state.columnsByLayout = {edit: {order, hidden, widths}, player: {order, hidden, widths}}`. `state.colOrder`/`state.hiddenCols`/`state.colWidths` sind seit diesem Feature keine echten Felder mehr, sondern `Object.defineProperty`-Getter/Setter, die auf `state.columnsByLayout[state.layout]` durchgereicht werden — jede bestehende Stelle, die diese drei Namen liest/schreibt (Spalten-Menü, Drag-Resize, Drag-Reorder, `render()`, `revealImported()`, …), funktioniert dadurch unverändert weiter und muss die aktive Ansicht nicht selbst kennen. `saveColumns()` schickt zusätzlich `layout: state.layout` an `POST /api/columns`; `initStorage()` befüllt beim Laden `state.columnsByLayout.edit` **und** `.player` aus der (jetzt verschachtelten) Server-Antwort, nicht nur die gerade aktive Ansicht. In `localStorage` liegen Breite und Sichtbarkeit (nicht die Reihenfolge, die bleibt serverseitig) ebenfalls je Ansicht verschachtelt unter `columnsByLayout` statt als flache Felder.

Serverseitig sind `column_order`/`hidden_columns` in `config.local.yaml` seit diesem Feature `{edit: [...], player: [...]}` statt einer flachen Liste. `config.py::_normalize_columns()` (Vorbild: `_normalize_cutoff_classes()`) wandelt eine noch alte, flache Liste beim Laden in `{"edit": alte_liste, "player": []}` um — Altbestand geht dadurch nicht verloren. `settings.save_columns(layout, order, hidden, widths)` schreibt gezielt nur den Eintrag der übergebenen Ansicht (`_as_layout_map()`/`_as_width_map()` normalisieren dafür den evtl. noch alten Bestand aus `config.local.yaml` genauso wie `config.py`, bevor der neue Wert eingesetzt wird) und validiert `layout` gegen `("edit", "player")`.

### Spaltenansichten — Interna

`COLUMN_VIEWS` (`[{id, name, order, hidden, widths}]`), `COLUMN_ASSIGN` (`{viewId: columnViewId}`) und `COLUMN_VIEW_DEFAULT` (die Standard-Spaltenansicht aus den Einstellungen) liegen in `app.js` neben `state.columnsByLayout`. `activeColumnStore()` löst die dreistufige Kette auf: Zuordnung dieser Liste (`columnViewFor()`) → Standard-Spaltenansicht → `state.columnsByLayout[state.layout]`. Die Unterscheidung zwischen `columnViewFor()` (nur die eigene Zuordnung) und `activeColumnView()` (was tatsächlich gilt) zieht sich durch die Oberfläche: die Auswahlfelder zeigen die eigene Zuordnung (leerer Eintrag = keine), gerendert wird nach dem, was gilt. Die drei Getter/Setter `state.colOrder`/`hiddenCols`/`colWidths` gehen seit diesem Feature über `activeColumnStore()` statt direkt über `columnsByLayout` — dadurch schreibt **jede** bestehende Stelle (Spalten-Menü, Ziehgriff, Drag am Spaltenkopf, `render()`, `dropVisibleCols()`) von sich aus in den richtigen Topf, ohne die Fallunterscheidung selbst zu kennen. Aus demselben Grund entscheidet auch nur `saveColumns()`, wohin gespeichert wird: `postColumnViews()` (`POST /api/column-views`, immer die ganze Liste) bei aktiver Spaltenansicht, sonst `postStandardColumns()` (`POST /api/columns`).

`columnStoreKey()` (`"cv:<id>"` bzw. `"std:<layout>"`) benennt den aktiven Topf; `syncColumnsForView()` baut Kopfzeile und Spalten-Menü beim Listenwechsel nur dann neu, wenn sich dieser Schlüssel ändert — aufgerufen in `selectView()`/`openExtPlaylist()` **vor** deren `render()`, damit die Tabelle nicht zweimal gezeichnet wird. Das Spalten-Menü selbst wird bei jedem Öffnen neu gebaut (`btnCols`), weil sein Inhalt an der gerade gewählten Liste hängt.

Das Spalten-Menü kennt nur zwei schreibende Wege: das Auswahlfeld (`assignColumnView()` → `setColumnAssign()` + `repaintColumns()`) und `saveCurrentColumnsAsView()` hinter dem einen Speichern-Knopf. Dieser sucht zuerst nach einer Ansicht gleichen Namens (Vergleich ohne Groß-/Kleinschreibung) und überschreibt sie, statt eine zweite gleichen Namens anzulegen — so deckt ein Knopf „anlegen" und „aktualisieren" ab. Verwalten (umbenennen, löschen, Standardansicht) liegt im Einstellungs-Dialog: `renderColumnViewsBlock()`/`redrawColumnViewsBlock()` arbeiten auf einem Entwurf (`colViewDraft`/`colViewDefaultDraft`) wie der Merklisten-Block und schicken beim Speichern die ganze Liste plus `default` an denselben Endpunkt. `adoptColumnViewsFromServer()` ist die einzige Stelle, die eine Server-Antwort in die drei Globalen übernimmt (aufgerufen von `initStorage()` und vom Einstellungs-Block) — Spalten normalisieren und verwaiste Zuordnungen verwerfen steht dadurch nur einmal da.

Jede spielbare Liste im Baum bekommt denselben Kontextmenü-Punkt „Spaltenansicht …", unabhängig davon ob sie eine echte `playlists`-Zeile hat oder nur ein reines `VIEWS`-Element ist (Alle/Ausgeblendet/Datei fehlt/Duplikate/Merklisten) — zwei parallele, aber gleich aufgebaute Wege:
- **Playlist-Knoten** (eigene, Smart- und feste Listen): `nodeMenuItems()` fügt ihn für jeden `spielbar`en Knoten ein (`kind === "playlist"`/`"smart"`, egal ob `locked`) → `setNodeColumnView(node)`. Der Bearbeiten-Dialog trägt das Feld bewusst NICHT mehr — sonst gäbe es zwei Stellen für dieselbe Einstellung.
- **View-only-Einträge ohne Playlist-Zeile**: `sysMenuItems(view)` → `setViewColumnView(view)`, aufgerufen über einen zweiten Kebab-Knopf in `renderTree()` (`data-viewmenu` statt `data-nodemenu`, an `n.view && !n.node && n.top` erkannt — `n.top` grenzt sie von den schreibgeschützten Musik.app/Rekordbox-Fremdästen ab, die ebenfalls `.view` aber kein `.top` tragen und deshalb keinen Kebab bekommen).

Beide Wege landen im selben `askPlaylistProps()`-Dialog, nur mit `withName = false`: das Namensfeld wird ausgeblendet (`setNodeColumnView`: der bestehende Name kommt unverändert zurück, damit die Rückfrage „leerer Name = Abbruch" nicht fälschlich greift; `setViewColumnView` übergibt gar keinen `current`, da View-Einträge ohnehin keinen editierbaren Namen haben) und `setColumnAssign(viewId, ...)` aufgerufen — bei `setNodeColumnView` mit der über `nodeViewId(node)` gebildeten ID, bei `setViewColumnView` direkt mit `view.id` (derselbe Schlüsselraum wie `state.view`/`COLUMN_ASSIGN`).

Serverseitig: `column_views`, `column_view_assign`, `column_view_default` und `column_widths` in `config.local.yaml`, geschrieben von `settings.save_column_views()`/`save_column_assign()`/`save_columns()` über den gemeinsamen Helfer `_write_local()`. `save_column_views()` nimmt die Standardansicht als optionales zweites Argument: `None` heißt „unverändert lassen" (der Weg aus dem Spalten-Menü schickt sie nicht mit, der Einstellungs-Dialog schon). Es wirft Zuordnungen auf gelöschte Ansichten gleich mit weg — und eine Standardansicht, die es nicht mehr gibt, ebenso; `save_column_assign()` weist eine Zuordnung auf eine unbekannte Ansicht ab — deshalb wartet `saveCurrentColumnsAsView()` in `app.js` das `POST /api/column-views` ab, bevor es die Zuordnung schickt (beide Anfragen laufen in eigenen Server-Threads und könnten sich sonst überholen). Die drei Spalten-Endpunkte in `server.py` nehmen als einzige Einstellungs-Endpunkte `self.lock`: sie kommen unbeaufsichtigt und schnell hintereinander (jedes Loslassen des Ziehgriffs, jedes Häkchen), und zwei parallele Lese-Ändere-Schreibe-Folgen auf `config.local.yaml` könnten sich sonst überholen. Die Kennung einer Ansicht (`cv<zeit><zufall>`) bleibt beim Umbenennen stehen — `column_view_assign` hängt daran.

Ohne Server (Report direkt per `file://` geöffnet) liegen Ansichten, Zuordnung und Standardansicht wie Breite und Sichtbarkeit in `localStorage` (`columnViews`/`columnAssign`/`columnViewDefault` im `tracktab.filters`-Eintrag). Das ist zugleich die Überbrückung beim Laden mit Server: `loadFilters()` stellt den letzten Stand sofort her, `initStorage()` ersetzt ihn durch den Serverstand, sobald `/api/settings` zurück ist — sonst blitzte die Kopfzeile kurz mit den Standard-Spalten auf.

### Spalten — Interna

„Titel" (`data-k="t"`) und „Künstler" (`data-k="a"`) sind gewöhnliche `OPTIONAL_COLUMNS`/`CELL_RENDERERS`-Einträge wie jede andere ausblendbare Spalte (`r.t`/`r.a` sind auf jeder Zeile ohnehin vorhanden, siehe `displayName(r)`) — sie nutzen die bereits vorhandenen i18n-Schlüssel `field.title`/`field.artist` aus dem Tags-Dialog mit, keine eigenen Übersetzungen nötig. Weil `OPTIONAL_COLUMNS` layoutübergreifend eine einzige Liste ist (siehe unten), sind beide automatisch in Bearbeiten **und** Player über „Spalten ▾" verfügbar.

Der Tastatur-Cursor (`.kbcursor`, siehe [Anhören mit Waveform](#anhören-mit-waveform)) reiht sich in dieselbe „fixierte Spalten deckend halten"-Mechanik weiter unten ein wie `.picked`/`.expanded`: eigene `--stickytint`-Regel für `td.sel`/`td.links` (per `color-mix(in srgb, var(--accent) 14%, transparent)`, weil `--accent` von der Designfarbe abhängt und sich nicht als fixes `rgba()` vorschreiben lässt) plus Aufnahme in den zusammenfassenden Block am Dateiende — sonst bliebe der Cursor unter den beiden fixierten Spalten unsichtbar.

Breite: Griff am rechten Rand jeder Überschrift ziehen (`.colresize`, `initColumnResize()`), gespeichert in `state.colWidths` (`column_widths` in `config.local.yaml`, localStorage als Rückfallebene). Reihenfolge: Überschrift per Drag verschieben (`attachColumnDrag()`) — nur mit Server, die Liste liegt als `column_order` in `config.local.yaml`. Sichtbarkeit: „Spalten ▾" (`state.hiddenCols`, localStorage/`hidden_columns` in `config.local.yaml`). Wohin diese drei tatsächlich geschrieben werden, hängt an der gerade geöffneten Liste — siehe [Spaltenansichten — Interna](#spaltenansichten--interna). Die Checkbox-Liste in „Spalten ▾" steht alphabetisch nach angezeigtem Label sortiert (`renderColsMenu()`, `Array.prototype.sort` mit `localeCompare(..., "de")`) — unabhängig von der tatsächlichen Spaltenreihenfolge in der Tabelle, die weiterhin allein aus `state.colOrder` kommt. „Breiten zurücksetzen" / „Reihenfolge zurücksetzen" im selben Menü.

„Datei" (`data-k="n"`) ist eine gewöhnliche Spalte aus `OPTIONAL_COLUMNS`/`CELL_RENDERERS` — verschiebbar, anpassbar, ausblendbar. „Status" (`data-k="v"`) ist zwar seit Kurzem ebenfalls Teil von `OPTIONAL_COLUMNS` (für Auswahlmenü und Persistenz der Sichtbarkeit) und damit über „Spalten ▾" ausblendbar, hat aber bewusst **keinen** Eintrag in `CELL_RENDERERS`: Kopfzelle (`index.html`) und Körperzelle (`render()`) stehen statisch im Code statt datengetrieben über `state.colOrder` — eine zweite, dynamisch aus `OPTIONAL_COLUMNS` eingefügte Kopfzelle würde die Spalte sonst doppelt anzeigen. `renderHead()` filtert deshalb nicht mehr über `OPTIONAL_COLUMNS`-Mitgliedschaft, sondern über `CELL_RENDERERS[k]`, um genau das auszuschließen. Position und Breite von „Status" bleiben dadurch fest (kein Drag), nur die Sichtbarkeit ist wählbar — dieselbe Umschaltung ist in `renderDrops()` für die Einzelprüfungen-Tabelle separat nachgezogen (eigene `statusHidden`-Prüfung für Kopf-/Körperzelle und die `colspan`-Berechnung der aufgeklappten Detailzeile, da `dropVisibleCols()` „v" mangels `CELL_RENDERERS`-Eintrag ohnehin nie mitzählt). Sortiert wird sie über `displayName(r)` („Interpret — Titel", ersatzweise Dateiname), weil die Zeilen kein Feld `n` haben. Fest bleiben nur die Auswahl-Checkbox (`td.sel`, `position:sticky; left:0`) und „Öffnen" (`td.links`, `right:0`) — beide bleiben beim horizontalen Scrollen stehen. Ihre senkrechte Trennlinie zum durchscrollenden Rest kommt als 1px-Kästchen **innerhalb** der Zelle (`td.sel::after` rechts, `td.links::before` links, `position:absolute; top:0; bottom:0; width:1px`, Farbe `--stickyline` — ein eigener, etwas kräftigerer Ton als `--line`, das neben den Zeilenrahmen untergeht). Weder `border` noch `box-shadow` taugen dafür: bei `border-collapse:collapse` malt die Nachbarzelle die gemeinsame Kante, und die scrollt mit weg; ein äusserer `box-shadow` wird an der **rechts** fixierten Spalte gar nicht erst gezeichnet — an echtem Material geprüft, links sichtbar, rechts auch mit 40px Breite unsichtbar. Das `overflow:hidden` der Zelle schneidet nichts ab, weil das Kästchen innerhalb der Polsterbox liegt, und die Abblend-Regeln am Dateiende (`td.sel > *`) treffen es nicht — Pseudoelemente sind keine Kind-Elemente im Sinne des Selektors. „Öffnen" misst sich selbst nach der breitesten Aktionszeile (`syncLinksWidth()`, dafür hat `.links .actions` im CSS `width:max-content`). Unter beiden scrollt der Rest der Zeile durch, sie brauchen also einen deckenden Hintergrund — und der ist überraschend leicht kaputtzumachen: die Zeilen-Hervorhebungen (`tr.row:hover`, `.picked`, `.expanded`) sind halbtransparent und spezifischer als ein blosses `td.links`, die Zustands-Abblendungen (`tr.ign`/`tr.slv`) arbeiten mit `opacity` auf der Zelle — `tr.corr` (manuell korrigiert) bewusst nicht, ein korrigierter Track gilt als korrekt und soll genauso lesbar bleiben wie ein normaler Treffer. Die Abblendung macht die fixierten Spalten durchscheinend, der Zeileninhalt scrollt dann sichtbar durch die Icons. Der Block **am Ende von `app.css`** dreht das für `tr.ign`/`tr.slv` um: die Tönung liegt als `background-image`-Ebene (`--stickytint`) über `background-color:var(--card)` statt sie zu ersetzen, und die Abblendung trifft `td.sel > *`/`td.links > *` statt der Zelle. Er steht bewusst zuletzt und wiederholt die Zeilen-Selektoren — sonst gewinnen die Regeln weiter oben wieder. Wer dort eine neue Zeilen-Hervorhebung ergänzt, muss sie in diesem Block mit aufnehmen.

**Der aufgeklappte Bereich einer Zeile bleibt beim horizontalen Scrollen stehen.** Spektrum, Gründe und der Player mit Waveform gehören zur Zeile, nicht zu einer Spalte — sie sollen deshalb nicht mit der Tabelle wegrutschen und auch nicht so breit werden wie die ganze Tabelle. Das `.grid` in `tr.detail td` klebt darum wie die fixierten Spalten (`position:sticky; left:var(--detailleft)`) und bekommt eine feste `width:var(--detailw)` — genau der Platz zwischen der Trennlinie der Auswahlspalte und der vor „Öffnen". Beide Masse setzt `syncDetailWidth()` (aufgerufen aus `syncTableWidths()` und bei `resize`) je `.tablewrap` als CSS-Variablen, weil sie erst zur Laufzeit feststehen: „Öffnen" misst sich nach den gerade sichtbaren Aktionen, der Sichtbereich hängt an der Fensterbreite. Die Einzelprüfungen haben eine eigene `.tablewrap` mit demselben `th.sel`/`th.links`-Aufbau und werden von derselben Schleife miterfasst.

Innerhalb des Bereichs hat das Spektrum eine **feste Spalte** (`.detail .spectrum { flex:0 0 340px }`, 340px = die SVG-Breite aus `spectrumSVG()`) — auch dann, wenn für die Zeile keine Spektraldaten gespeichert sind und nur der Hinweis „Kein Spektrum gespeichert." dasteht. Ohne das rutscht der Textblock daneben nach links und die Waveform wäre je Zeile unterschiedlich breit. `max-width:100%` lässt die Spalte in einem schmalen Fenster trotzdem schrumpfen.

**Fallstrick dabei:** `tr.detail td` braucht zwingend `overflow:visible`. Die allgemeine Regel `th, td { overflow:hidden }` macht die Zelle sonst selbst zum Scrollbereich — das darin klebende `.grid` hat dann keinen Scrollweg mehr und wandert einfach mit, obwohl `position:sticky` gesetzt ist (an echtem Material bestätigt: Breite stimmte, das Kleben nicht).

Technisch hängt das an einer echten festen Tabellenbreite: `table-layout:fixed` greift nur, wenn die Tabelle eine `width` hat — bei `width:auto` rechnet der Browser trotzdem automatisch, und dann bestimmt der Zellinhalt die Spaltenbreite statt der eingestellte Wert. `syncTableWidths()` summiert deshalb die sichtbaren Kopfzellen und schreibt die Summe als `table.style.width`; `min-width:100%` (CSS) hält die Tabelle mindestens containerbreit, den Überhang nimmt die leere Füllzelle (`th.filler`/`td.filler`) vor „Öffnen" auf. Ohne diese Füllzelle würde der freie Platz proportional auf alle Spalten verteilt und jede eingestellte Breite wieder verbiegen.

### Die Knopfleiste — Interna

| Symbol | Aktion | Serverendpunkt |
|---|---|---|
| ▶ | Anhören mit Waveform | `GET /api/audio`, `GET /api/waveform` |
| Finder-Symbol | Im Finder zeigen | `POST /api/reveal` |
| RX-Symbol | In externem Audio-Editor öffnen (Standard: iZotope RX) | `POST /api/open-in` |
| DAW-Symbol | In eingestellter DAW öffnen — nur gerendert, wenn `external_daw` gesetzt ist (kein Auto-Fallback wie bei RX/MIK/Rekordbox) | `POST /api/open-daw` |
| Stift-Symbol | Tags bearbeiten im Tool selbst | `POST /api/tags`, `GET/POST /api/cover`, `POST /api/lookup` |
| grünes „b" | Beatport-Suche | Client-seitiger Link |
| oranger Balken | SoundCloud-Suche | Client-seitiger Link |
| Music-Symbol | iTunes Store (kaufen) | `POST /api/open-store` |
| Kopie | Pfad in die Zwischenablage | rein clientseitig |
| Auge | Ausblenden (Fehlalarm) | `POST /api/ignore` |
| farbiges Quadrat je Merkliste | Zu dieser Merkliste hinzufügen/entfernen | `POST /api/favorite` |
| roter Papierkorb | In den Papierkorb | `POST /api/trash` |

Finder-, RX- und DAW-Symbol zeigen die **echten Programm-Icons** (aus dem `.icns` der installierten App gelesen, `GET /api/appicon`) — ändert sich der konfigurierte Editor/die DAW, ändert sich das Icon mit.

Papierkorb (Einzelzeile wie Sammelaktion) fragt immer erst per `confirmOverlay` nach (`askTrash()`/`askTrashBulk()` in `app/webui/app.js`) — Track-Name bzw. Anzahl, Pfad/Gesamtgröße, ein Hinweis, falls der Track dabei auch aus der Music.app-Bibliothek entfernt wird, sowie „Abbrechen"/„In den Papierkorb …"-Knöpfe; der Bestätigen-Knopf heißt „In den Papierkorb verschieben und aus Music Library löschen", sobald mindestens einer der betroffenen Tracks in der Bibliothek steht, sonst nur „In den Papierkorb". **Cmd+Löschen** löst dieselbe Sammelaktion wie der Papierkorb-Knopf der Sammelaktionsleiste aus — bezieht sich auf die per Checkbox ausgewählten Tracks (`state.selected`), nicht auf die gesamte dargestellte Liste, und greift nur, wenn wirklich etwas ausgewählt ist.

### Tags bearbeiten — Interna

Geschrieben wird direkt in die Datei (`app/tags.py`, mutagen-basiert), nicht nur in die Datenbank. GEOB-/PRIV-Frames (Serato-Analyse, Rekordbox-Cues) bleiben dabei unangetastet, weil bei MP3/WAV/AIFF das komplette ID3-Objekt geladen und nur die betroffenen Frames per `setall()`/`delall()` ersetzt werden — derselbe Grundsatz wie bei [`rewrite.py`](#apprewritepy).

**Track (Nr./von)** schreibt/liest das TRCK-Frame (bzw. `trkn`-Atom bei MP4, `tracknumber`/`tracktotal` bei FLAC) als Zahlenpaar. Wird nur eines der beiden Felder geändert, liest `tags.py` den jeweils anderen Teil zuerst aus dem vorhandenen Tag zurück (`_parse_track_no()`/`_parse_track_total()`), statt ihn zu verwerfen — sonst würde z. B. das nachträgliche Eintragen der Gesamtzahl eine bereits gesetzte Tracknummer löschen. Sind beide Felder leer/0, wird das Frame komplett entfernt statt „0" zu schreiben.

**Autovorschläge:** Titel, Künstler, Album, Albumkünstler, Komponist und Genre zeigen beim Klick/Fokus ins Feld alle in der Bibliothek bereits vergebenen Werte dieses Feldes (aus `DATA`, client-seitig, kein eigener Server-Endpunkt) — auch wenn das Feld schon einen Wert enthält, denn Klick/Fokus ignoriert bewusst den aktuellen Inhalt (`openAll()` in `attachAutocomplete()`, `app/webui/app.js`). Weitertippen filtert die Liste stattdessen per Präfix auf passende Werte (`openFiltered()`), wie schon zuvor.

„◀ Vorheriger“/„Nächster ▶“ blättern innerhalb der aktuell gefilterten Liste, ohne den Dialog zu schließen — die Felder werden für den Nachbar-Track neu geladen. Sind über die Checkboxen mehrere Tracks markiert und wird der Dialog für einen davon geöffnet (z. B. per Stift-Symbol in der Zeile selbst, nicht über die Mehrfachauswahl-Werkzeugleiste), blättern die beiden Knöpfe stattdessen ausschließlich zwischen den markierten Tracks — unabhängig davon, ob dazwischen weitere, nicht markierte Zeilen in der gefilterten Liste liegen (`openTagsPopup()`, `app/webui/app.js`). Eine Cover-Änderung (Datei auswählen, Zwischenablage, Drag&Drop aus dem Finder **oder** aus dem Browser — ein Bild aus einer Webseite auf die Cover-Fläche gezogen liefert meist nur eine Bild-URL statt einer Datei, erkannt über `text/html`/`text/uri-list`/`text/plain` im Drop-Event, siehe `extractDroppedImage()`; noch nicht fertig geladene Vorschaubilder liefern stattdessen eine inline `data:`-URI, die `dataUriToFile()` direkt in eine Datei umwandelt —, „Cover löschen“) wird clientseitig nur als Object-URL bzw. Bild-URL vorgemerkt (`pendingCover`) und erst beim Klick auf „Speichern“ geschrieben — direkt nach den Textfeldern, als eigener Request an `POST /api/cover`, `POST /api/lookup-cover` (bei einer Bild-URL) bzw. `POST /api/cover-delete`. Ein vorhandenes Cover wird komplett ersetzt, nicht ergänzt. Der Dialog bleibt danach offen, es lässt sich also mehrfach hintereinander am selben Track speichern. **Google-Bildersuche liefert keine direkte Bild-URL, sondern einen Zwischenseiten-Link** (`/imgres?imgurl=<echte-url>&...`) — `unwrapRedirectUrl()` erkennt `google.*/imgres`- bzw. `/url`-Links an Host und Pfad und entpackt den `imgurl`/`url`-Parameter, sonst würde der Server die Google-Zwischenseite selbst als "Cover" laden (kaputtes Vorschaubild, kaputte Datei nach dem Speichern). Aus dem Browser gezogene URLs lässt `lookup.fetch_cover()` nur mit `http`/`https`-Schema zu — die URL stammt von einer beliebigen, nicht vertrauenswürdigen Webseite, anders als bei den Online-Vorschlägen (iTunes/Deezer/MusicBrainz), die denselben Weg nutzen.

**Cover-Vorschaubilder brauchen einen Revisionszähler.** Der Browser hält jedes einmal geladene Bild pro Dokument unter seiner URL fest („list of available images“ im HTML-Standard) — diese Ebene liegt **über** dem HTTP-Cache, und selbst ein `Cache-Control: no-store` ändert daran nichts. (`GET /api/cover` schickt inzwischen bewusst das Gegenteil: `ETag` plus `max-age=604800`, siehe [Ausliefer-Endpunkte](#ausliefer-endpunkte-caching--streaming) — der Revisionszähler ist damit erst recht die Stelle, an der eine Änderung sichtbar wird.) Baut `render()` die Tabelle neu auf, liefert eine unveränderte URL also weiter das alte Bild: ein frisch geschriebenes Cover blieb in der Liste unsichtbar. Auffällig wurde das erst ab der **zweiten** Änderung an derselben Datei — beim ersten Cover gab es vorher gar kein `<img>`, die URL war also noch nie geladen. Jeder Schreibvorgang zählt deshalb `bumpCoverRev(pfad)` hoch, und `coverUrl(pfad)` hängt den Stand als `&v=N` an (der Server ignoriert den Parameter). Wer eine neue Stelle ergänzt, die ein Cover schreibt, muss `bumpCoverRev()` mitrufen — sonst zeigt die Liste weiter das alte Bild. Die Einzelbilder im Dialog und in der Großansicht nutzen stattdessen `&_=${Date.now()}`: dort ist es nur ein Bild, und so wird auch eine ausserhalb des Tools geänderte Datei sicher frisch geladen.

**Mehrfachbearbeitung:** Beim Speichern wird pro Feld geprüft, ob sich der Wert gegenüber der Vorbefüllung geändert hat — **nur tatsächlich bearbeitete Felder werden bei allen Ausgewählten überschrieben**, unangetastete Felder behalten den individuellen Wert jeder einzelnen Datei. Serverseitig ändert sich nichts — `/api/tags` wird pro Datei einzeln mit nur den geänderten Feldern aufgerufen, denn `update_tags()`/`write_tags()` überschreiben ohnehin nur Felder, die im Request vorkommen.

„Online-Vorschläge suchen“ fragt `POST /api/lookup` (`app/lookup.py`) ab — **rein manuell angestoßen, nie automatisch, nichts wird zwischengespeichert.** Quellen, alle ohne API-Key/Login:

| Quelle | liefert | Einschränkung |
|---|---|---|
| iTunes Search API | Interpret, Titel, Album, Genre, Cover | kein BPM |
| Deezer | Interpret, Titel, Album, Cover, teils BPM | BPM nur, wenn Deezer den Track selbst analysiert hat (Feld dann leer statt einer irreführenden 0) |
| MusicBrainz | Interpret, Titel, Album | nur Rückfallebene, wenn iTunes UND Deezer nichts finden (strengeres Rate-Limit) |

Shazam wurde geprüft und verworfen (keine Public API für Drittanbieter); ebenso Beatport (Suchseite hinter Cloudflare-Bot-Check, offizielle API `api.beatport.com/v4` verlangt Login/Client-Key — beides ohne Account nicht nutzbar).

Der Editor selbst verschiebt oder benennt eine Datei nie um. Räumt aber Music.app eine bereits importierte Datei zwischen zwei Bearbeitungen eigenständig in seinen Medienordner um, würde ein späteres Speichern (Tags, Cover, Online-Cover) sonst mit „Datei existiert nicht mehr" scheitern — genau das fängt die automatische Wiedererkennung (siehe [Gelöschte Dateien — Interna](#gelöschte-dateien--interna)) ab, bevor einer der vier Speicher-Endpunkte aufgibt.

**Weiterhin bewusst nicht umgesetzt:** Music.app selbst zum sofortigen Neueinlesen der Tags zu bewegen — dafür gibt es keinen bekannten zuverlässigen AppleScript-Weg — sowie eine Rekordbox-Pfadkorrektur: verschiebt Music.app die Datei, bleibt ein zuvor in Rekordbox eingetragener Pfad ungültig, die automatische Wiedererkennung korrigiert nur die eigene Datenbank.

### Player mit Waveform — Interna

Die Waveform wird beim ersten Abspielen berechnet (`media.waveform_peaks`, ca. 2 s) und danach im Waveform-Cache gespeichert (siehe [Glossar](#datenhaltung--oberfläche-glossar)).

**Rekordbox-Cues und farbige Waveform:** Ist der Track in Rekordbox vorhanden und dort analysiert, liefert `GET /api/rekordbox-extras` (`app/rekordbox.py:get_track_extras()`) Hot Cues und Memory Cues über der Hüllkurve sowie — sofern die passende Analysedatei existiert — die Hüllkurve selbst in einem von zwei Stilen aus Rekordbox' eigenen Analysedaten statt der grauen Standardanzeige. Die Einstellung **Waveform-Stil** (Kategorie Rekordbox) wählt serverseitig zwischen:
- **RGB** (`.EXT`, Tag `PWV4`): Höhe aus der glatteren Hüllkurven-Spalte, Farbe aus der Frequenz-Farbspalte derselben Datei, clientseitig auf eine Mindest-Sättigung/-Helligkeit angehoben (`_vividize()` in `app.js`) — die rohen PWV4-Werte wirken für sich genommen zu matt für den leuchtenden Rekordbox-Look.
- **3-Band** (`.2EX`, Tag `PWV6`): Tief/Mitten/Hoch als gestapelte (nicht überlagerte) Segmente von der Mittellinie nach außen, Byte-Reihenfolge Mitten/Hoch/Tief (Quelle: Deep Symmetry ANLZ-Referenz, von `pyrekordbox` selbst zitiert — die Bibliothek hat für dieses Tag keinen fertigen Decoder, nur den rohen 3-Byte-je-Spalte-Container).

Fehlt der Track in Rekordbox, ist er dort nicht analysiert, oder existiert die zum gewählten Stil passende Analysedatei nicht, bleibt es automatisch bei der grauen Standard-Waveform — ohne Fehleranzeige, das ist der erwartete Normalfall. Die farbige Darstellung ersetzt nur die Balkenfarbe; der Wiedergabefortschritt wird stattdessen als dünne schwarze Linie eingezeichnet (`drawPlayhead()`, bewusst schwarz statt `--accent`: das hält gegen jede Balkenfarbe Kontrast), statt wie bei der grauen Waveform die Balkenfarbe hart zu tauschen — das würde die Rekordbox-Farben zunichtemachen. Eine frühere farbige Rekonstruktion aus PWV4 war zunächst probiert und wieder verworfen worden (siehe Git-Historie); Ursache war ein Skalierungsproblem, keine grundsätzliche Ablehnung.

Schleifen-/Bereichsmarkierungen (`DjmdCue.OutMsec` gesetzt) werden weder als Hot Cue noch als Memory Cue angezeigt — an echtem Material bestätigt, dass diese nicht den vom Nutzer per Memory-Cue-Taste gesetzten Punkten entsprechen (auch wenn `Kind` zufällig im 1–8-Bereich liegt, siehe unten) und den Nutzer beim Zählen seiner tatsächlichen Memory Cues verwirrt haben.

Die Zuordnung `DjmdCue.Kind` → Buchstabe A–H ist von `pyrekordbox` nicht dokumentiert (nur `is_hot_cue`/`is_memory_cue`, `Kind > 0`/`== 0`); sie beruht auf der üblichen Konvention (Kind 1–8 = A–H). **Wichtig:** `Kind > 0` allein reicht nicht als Hot-Cue-Kriterium — an echtem Material bestätigt, dass eine Schleifen-Markierung (`OutMsec` gesetzt) mit `Kind` im 1–8-Bereich fälschlich als zusätzlicher Hot Cue erschien, obwohl in Rekordbox kein entsprechendes Pad belegt war. `get_track_extras()` zählt deshalb nur Zeilen mit **leerem `OutMsec`** (reiner Punkt, keine Schleife) als Hot Cue; alles mit gesetztem `OutMsec` — ob `Kind=0` oder nicht — wird als Memory-Cue/Loop-Markierung behandelt. Das kann in seltenen Fällen eine echte, auf ein Pad gelegte Hot-Cue-Schleife fälschlich als Memory-Marker zeigen; dafür gibt es bisher keinen bestätigten Gegenfall. Um master.db nicht bei jeder aufgeklappten Zeile neu zu öffnen/sichern, hält `_open_cached()` eine einzelne Verbindung offen und erneuert sie nur, wenn sich master.db seitdem geändert hat.

**Cue-Farben:** `DjmdCue.Color` bedeutet bei Hot Cues etwas anderes als bei Memory Cues — beide Interpretationen waren zunächst falsch geraten (gepacktes RGB für beide, dazu eine erfundene 8-Farben-Leiter A–H als Hot-Cue-Vorgabe) und wurden anhand einer Kombination aus echtem Material (ein Track mit Hot Cues A/B/C ohne eigene Farbe, E/F mit `ColorTableIndex` gesetzt, und Memory Cues fast ausschließlich ohne Farbe bis auf eine) und der Reverse-Engineering-Quelle [Deep-Symmetry/beat-link, Issue #51](https://github.com/Deep-Symmetry/beat-link/issues/51) (`CueList.java`/`ColorItem.java`, dieselbe Bibliothek, die `pyrekordbox` bereits für die ANLZ-Tag-Reihenfolge zitiert) korrigiert:
- **Hot Cues:** Priorität `ColorTableIndex` (eigene, ca. 62 Werte breite Palette, `_HOT_CUE_TABLE_COLORS`) vor eingebettetem `Color` als gepacktes `0xRRGGBB` (z. B. aus einem Serato-Import, `_decode_color()`) vor Standard-Grün (`_HOT_CUE_DEFAULT_COLOR`) für noch nicht eingefärbte Hot Cues — Rekordbox hat **keine** unterschiedliche Vorgabefarbe je Buchstabe A–H, alle unbelegten Hot Cues sind gleich grün.
- **Memory Cues:** `Color` ist hier kein RGB, sondern derselbe 8-Farben-Index wie Rekordbox' Track-Farben (`_MEMORY_CUE_PALETTE`, 1=Pink...8=Purple); ohne zugewiesene Farbe zeigt Rekordbox den Standard-Rot-Ton (`_MEMORY_CUE_DEFAULT_COLOR`), nicht Grau.

Beide Vorgabefarben (Grün für Hot Cues, Rot für Memory Cues) gelten unabhängig vom in Rekordbox eingestellten Farbschema (Einstellungen → Farbe → Hot Cue: CDJ/COLD1/COLD2/COLORFUL) — nur das `CDJ`-Schema zeigt sie tatsächlich so an, die anderen weisen unbelegten Hot Cues serverseitig ohnehin keine unterscheidbare Farbe zu, weshalb hier bewusst nicht danach unterschieden wird.

### Der globale Mediaplayer — Interna

Ein einziges geteiltes `Audio`-Objekt (`queueAudio`) fuer beide Ansichten, gesteuert ueber den Zustand `queueState` — komplett getrennt vom eigenstaendigen Player der Einzelpruefungen (`players`-Map/`mountDropPlayer()`/`currentDropPlayer()`, siehe unten). `render()` verzweigt beim Zeilen-/`[data-play]`-Klick auf `startQueueFrom(r)` statt der Aufklapp-Logik, sobald `state.layout === "player"`; in der Bearbeiten-Ansicht klappt der Klick stattdessen wie gehabt die Zeile auf (`mountLibraryPlayer()`, siehe unten) — beide Pfade fuettern am Ende denselben `queueAudio`.

**Layoutuebergreifend, seit Issue #16:** `applyLayout()` pausiert `queueAudio` beim Verlassen der Player-Ansicht nicht mehr und schliesst auch `#queuePopup` nicht mehr erzwungen — Wiedergabe und Popup bleiben ueber einen Ansichtswechsel hinweg unangetastet. `#playerBar` traegt entsprechend keine `body[data-layout="player"]`-Sichtbarkeitsregel mehr (`app.css`), sondern ist unconditional `display:flex`; dieselbe Umstellung gilt fuer das kompensierende `body{padding-bottom:96px}`/`.totop{bottom:100px}`/`#toasts{bottom:100px}`. Die Zeilen-Knoepfe `data-play-next`/`data-queue-add` (Öffnen-Spalte) sind seitdem nicht mehr auf `state.layout === "player"` gated (vorher `(r.gone || state.layout !== "player") ? "" : ...` im Zeilen-Template), erscheinen also jetzt in beiden Ansichten. `renderPlayerBulkBar()` (reduzierte Sammelleiste) bleibt dagegen weiterhin exklusiv der Player-Ansicht vorbehalten. Die volle Sammelleiste (`renderBulkBar()`, Bearbeiten-Ansicht) bekam denselben „Zur Warteschlange hinzufügen"-Knopf (`bulkApply("queue-add-all")`) nachtraeglich dazu — vorher stand er dort nur an der einzelnen Zeile, was bei einer groesseren Mehrfachauswahl unnoetig viele Einzelklicks bedeutete.

**Wellenform einer Bibliothekszeile ⇄ globaler Player (`mountLibraryPlayer()`).** Anders als der fruehere, komplett ersetzte `mountPlayer()` besitzt diese Funktion **kein eigenes** `Audio`-Objekt mehr: ein Eintrag `{r, peaks, cues, canvas, btn, timeEl, sync(), syncLabels(), onDispose()}` landet in der Map `waveViews` (Schluessel `r.i`) **und** in der gemeinsamen Registry `waveEntries` (siehe [Zeichenebene](#zeichenebene-statische-ebene--eine-bildschleife)), die Wellenform ist nur eine Ansicht auf `queueAudio`, solange `queueState.current?.i === r.i` gilt. Peaks (`peaksForLibrary`/`peaksFromFile`) und Rekordbox-Cues (`rekordboxExtrasFor`) werden wie zuvor beim Aufklappen geladen. Toggle-Knopf und Wellenform-Klick pruefen zuerst, ob die Zeile bereits der aktuelle Track ist: wenn ja, wird direkt an `queueAudio` getoggelt/geseekt; wenn nein, startet `startQueueFrom(r)` eine neue Warteschlange (dieselbe Funktion wie beim Zeilen-Klick in der Player-Ansicht). Ein Klick auf eine Cue-Markierung oder eine Position in der Wellenform einer noch NICHT aktuellen Zeile legt das Klickziel vorher in der Modulvariable `pendingSeek` ab; da `startQueueFrom()`/`loadQueueTrack()` die Dauer des neuen Tracks erst nach dem Laden kennen, wendet der ohnehin vorhandene `queueAudio.onloadedmetadata`-Handler `pendingSeek` an und setzt sie zurueck. Das Zuklappen der Zeile bemerkt der **eine, gemeinsame** `MutationObserver` `_waveUnmountObserver` (frueher legte jede aufgeklappte Zeile einen eigenen an, der auf `document.body` mit `subtree:true` lauschte — bei n Zeilen also n Rueckrufe mit je einem `contains()`-Baumlauf pro DOM-Aenderung, und ein volles `render()` der Tabelle loest Tausende aus); er ruft `disposeWaveEntry()`, das den `waveViews`-Eintrag entfernt und die vorgerechnete Ebene freigibt, aber **nicht** `queueAudio` pausiert — die globale Wiedergabe soll das Zuklappen ueberleben. Bei `mountDropPlayer()` haengt dagegen ein `onDispose()` daran, das die eigene Wiedergabe beendet.

Welchen Fortschritt eine Zeile zeigt, entscheidet ihr eigenes `sync()`: nur die Zeile mit `queueState.current?.i === r.i` uebernimmt `queueAudio.currentTime`/`.duration`, alle anderen bleiben bei `0`/`r.du` (unbespielter Ausgangszustand, Play-Symbol statt Pause). Mehrere gleichzeitig aufgeklappte Zeilen sind dabei weiterhin ausdruecklich erlaubt (siehe [Spalten — Interna](#spalten--interna) zu `expandedByIdx`) — nur maximal eine davon kann je „aktuell" sein. `repaintWaveViews()` ist seit dem Umbau der Zeichenebene nur noch ein benannter Einstieg auf `requestWavePaint()`.

`cueHitAt(entry, canvas, clientX, duration)` nimmt reine Zahlenwerte statt eines `Audio`-Objekts entgegen, damit dieselbe Funktion sowohl vom eigenstaendigen Einzelpruefungs-Player (`mountDropPlayer()`, uebergibt `audio.currentTime`/`.duration`) als auch von einer Bibliothekszeile (bedingt `queueAudio`s Werte oder `0`/`r.du`) genutzt werden kann. Das fruehere `drawWave(canvas, entry, currentTime, duration)` ist in `paintWaveEntry(entry)` + `buildWaveLayer(entry, dpr)` aufgeteilt; die Werte holt `entry.sync()`.

### Zeichenebene: statische Ebene + eine Bildschleife

Die Balken aendern sich waehrend der Wiedergabe nicht — nur die Abspielposition wandert. `buildWaveLayer(entry, dpr)` rechnet sie deshalb **einmal** in ein eigenes, unsichtbares Canvas (`entry.layerCanvas`); `paintWaveEntry(entry)` kopiert es pro Bild nur noch. Pro Bild bleiben damit `clearRect`, `drawImage`, ein Rechteck fuer den gespielten Teil, die Cue-Marker und eine Linie — konstanter Aufwand, unabhaengig von der Zahl der Rohspalten.

Vorher lief bei jedem `ontimeupdate`-Tick (~4/s **je aufgeklappter Zeile**) das komplette Downsampling ueber die 1200 Rohspalten der Rekordbox-Wellenform samt Glaettung, HSL-Umrechnung und einem `rgb()`-String je Balken. Das war die groesste einzelne Quelle von Rechenlast und kurzlebigen Objekten in der Oberflaeche.

- **Wann neu gerechnet wird** entscheidet `waveLayerKey(entry, dpr)` = Breite × Hoehe × `devicePixelRatio` × `THEME_REV` × `entry.dataRev`. Aendert sich nichts davon, wird nicht neu gebaut. Neue Daten (Peaks, Cues, Rekordbox-Wellenform) laufen ueber `setWaveData(entry, patch)`, das `dataRev` hochzaehlt.
- **Gespielter Teil ohne Neuzeichnen.** Die graue Standardwellenform wird einfarbig (`--wave`) gebacken; beim Kopieren faerbt `globalCompositeOperation = "source-atop"` innerhalb eines auf `frac * w` beschnittenen Bereichs genau die bereits deckenden Pixel in `--accent` um. Die bunten Rekordbox-Stile (`rgb`/`3band`) duerfen so nicht eingefaerbt werden — sie behalten ihre echten Farben und bekommen stattdessen `drawPlayhead()` obenauf (`entry.layerRecolorable` unterscheidet die beiden Faelle).
- **Die Cue-Marker gehoeren NICHT in die Ebene**, obwohl auch sie stillstehen. `source-atop` erwischt jedes deckende Pixel im beschnittenen Bereich — also auch Cue-Marker links vom Abspielpunkt samt der weissen Buchstaben auf den Hot-Cue-Faehnchen. Beim ersten Anlauf steckten sie in der Ebene und wurden dadurch alle einheitlich in der Designfarbe eingefaerbt; nur Marker rechts vom Abspielpunkt behielten ihre Rekordbox-Farbe. `drawCueMarkers()` laeuft deshalb pro Bild, **nach** dem Einfaerben und **vor** `drawPlayhead()` — dieselbe Reihenfolge wie vor dem Umbau. Das kostet nichts: Cues sind eine Handvoll Striche, teuer waren die bis zu 1200 Balken. Weil damit nichts in der Ebene mehr von der Dauer abhaengt, steckt sie auch nicht mehr im Schluessel.
- **Eine Bildschleife statt `ontimeupdate`.** `wavePump()` laeuft aus einem einzigen `requestAnimationFrame`, holt die Position direkt aus `audio.currentTime` und zeichnet alle Registry-Eintraege. Damit sitzt die Abspielposition pro Bild am Ton — das fruehere Zeichnen aus `ontimeupdate` heraus lief unregelmaessig (~4 Hz, browserabhaengig) und ausserhalb des Bildtakts, was die Position sichtbar ruckeln liess. `ontimeupdate` ist seitdem nur noch fuer die Hoerzeit-Statistik (`listenTrackTick()`) zustaendig. `scheduleWavePaint()` prueft bewusst **nicht** `document.hidden`: ein angefordertes Bild wird im Hintergrund vom Browser nur zurueckgestellt, nicht verworfen — eine eigene Sperre wuerde genau die Anforderungen verschlucken, die waehrend der Unsichtbarkeit anfallen, und die Wellenform bliebe danach leer.
- **Keine Layout-Reads im Zeichenpfad.** Die Canvas-Groesse liefert `_waveResizeObserver` ueber `contentRect` (kein erzwungener Reflow); die Themenfarben liest `readThemeTokens()` einmal je Themenwechsel in `_theme` (frueher zwei bis drei `getComputedStyle(document.body)` **pro Zeichenvorgang pro Zeile**). `applyTheme()`/`applyAccentColor()` und ein `matchMedia("(prefers-color-scheme: dark)")`-Listener rufen dafuer `invalidateWaveTheme()`.
- **Sichtbarkeit.** `_waveVisibilityObserver` (`IntersectionObserver`, `rootMargin: 150px`) setzt `entry.visible`; weggescrollte Zeilen werden uebersprungen. Der frueher genutzte `repaintWaveViews()` zeichnete ausnahmslos alle.
- **Typisierte Puffer.** `_resampleSeries`/`_movingAverage` liefern `Float32Array`, `_resampleColorSeries` einen flachen `[r,g,b,r,g,b,…]`-Puffer statt eines Feldes von `[r,g,b]`-Feldern — ein Puffer statt tausender kleiner Objekte je Neubau.

**Wechselseitige Pausierung Einzelpruefung ⇄ globaler Player.** Da beide Systeme unabhaengig voneinander eine Wiedergabe starten koennen, verhindert eine beidseitige Kopplung, dass gleichzeitig zwei Tracks laufen: `queueAudio.onplay` pausiert alle Eintraege der `players`-Map (Einzelpruefungen); umgekehrt rufen die Play-Startpunkte in `mountDropPlayer()` (`btn.onclick`, Cue-/Wellenform-Klick) zusaetzlich `queueAudio.pause()` auf.

**`stopPlayerFor(path)`** (aufgerufen vor `reanalyse()`/`rewriteOne()`/aus `applyMovedPaths()`, siehe [Bitrate korrigieren](#apprewritepy)) setzt zusaetzlich `queueState.current = null` und pausiert `queueAudio`, falls dessen aktueller Track genau diesen Pfad streamt — ohne das wuerde ein Resume nach einer Neu-Messung/Bitrate-Korrektur/Verschiebung stumm die inzwischen ungueltige `queueAudio.src` weiterspielen.

**Tastaturkuerzel sind seitdem layoutunabhaengig.** `currentPlayer()` heisst jetzt `currentDropPlayer()` und erfasst ausschliesslich noch die Einzelpruefungen (Bibliothekszeilen landen nie mehr in `players`). Leertaste, Pfeiltasten ←/→ und die Ziffern 1–8 pruefen zuerst `currentDropPlayer()` (Vorrang, falls gerade eine Einzelpruefung explizit laeuft) und steuern sonst immer `queueAudio`/`queueState` — unabhaengig von `state.layout`. Der fruehere, nur fuer Bearbeiten geltende Zweig (`stepActiveTrack()`, `activeKey`-basiert) ist komplett entfallen, ebenso `playFirstVisibleRow()` — beide sind durch denselben, schon zuvor fuer die Player-Ansicht vorhandenen Pfad ersetzt (kein Track geladen → `startQueueFrom(filtered().find(...))`). Der Ziffern-1–8-Handler prueft bei fehlendem Drop-Player `waveViews.get(queueState.current.i)?.cues` — wirkt also wie zuvor nur, wenn die betreffende Bibliothekszeile gerade aufgeklappt ist (nur dann sind ihre Cues geladen). `currentActiveRowForCursor()` (Startpunkt des Tastatur-Cursors ohne vorhandenen Cursor) liefert seitdem unconditional `currentQueueTrack()`.

**Datenmodell: Pool + aktueller Track + Historie statt eines Arrays mit Positionszeiger.** `queueState` haelt drei getrennte Teile: `tracks` (Pool der noch nicht gespielten Titel, Anzeige-/Drag&Drop-Reihenfolge), `current` (der gerade geladene Track oder `null`) und `history` (bereits gespielte Titel, chronologisch aufsteigend). Eine fruehere Version hielt das alles in einem einzigen Array mit Positionszeiger (`pos`) plus einer separaten Shuffle-Permutation (`shuffleOrder`/`shufflePos`) — funktionierte, wurde aber unhandlich, sobald Mutationen (hinzufuegen/entfernen/umsortieren) UND Shuffle UND ein "bereits gespielt"-Konzept gleichzeitig konsistent bleiben mussten (Indizes verschieben sich bei jeder Aenderung, Shuffle-Positionen muessten mitwandern). Mit drei getrennten Arrays entfaellt das komplett: der Pool enthaelt **nur** noch nicht Gespieltes, Shuffle zieht bei jedem Schritt einfach zufaellig EIN Element aus dem Pool (`advanceFromPool()`) statt eine Permutation zu pflegen, und „bereits gespielt" ist einfach „steht in `history`" statt einer berechneten Position.

`startQueueFrom(r)` ersetzt **nur den Pool** (auch einen manuell bearbeiteten) durch `filtered().filter(x => !x.gone).slice(idx + 1, idx + QUEUE_AUTO_LIMIT)` (Konstante `QUEUE_AUTO_LIMIT = 25`, `r` selbst wird `current`) und setzt `manual = false` — die Historie bleibt bewusst unangetastet und waechst ueber mehrere Warteschlangen-Starts hinweg weiter, wie ein echter Hoerverlauf (Tab „Zuletzt gehört", siehe unten) — begrenzt durch `QUEUE_HISTORY_LIMIT = 200`, ueber `pushQueueHistory()` statt eines direkten `history.push()`. `addToQueue(r)` (Knopf `data-queue-add`) haengt hinten an (`tracks.push`), `playNext(r)` (Knopf `data-play-next`, „als Naechstes abspielen") reiht vorne ein (`tracks.unshift`) — direkt hinter dem aktuellen Track. Beide setzen `manual = true` und pruefen bewusst **nicht**, ob `r` schon im Pool oder in der Historie steht: mehrfaches Einreihen desselben Tracks ist erlaubt (Absprache mit dem Nutzer), Pool und Historie sind ohnehin getrennte Arrays, ein Historien-Treffer blockiert ein erneutes Einreihen also gar nicht erst technisch.

**Vorruecken/Zurueck:** `advanceFromPool()` zieht bei Shuffle ein zufaelliges, sonst das vorderste Pool-Element, schiebt den bisherigen `current` auf `history` und laedt das gezogene Element (`loadQueueTrack()`, reine Zuweisung + Wiedergabestart, OHNE selbst Pool/Historie anzufassen — das entscheiden die Aufrufer). `queueNext(manual)` ruft das auf, wenn der Pool nicht leer ist; ist er leer und `repeatList` aktiv, wird die komplette Historie (+ `current`) wieder zum Pool (Liste von vorn); manual=true (Knopf/Pfeiltaste) ueberspringt ein aktives `repeatTrack` bewusst (Konvention wie bei den meisten Playern), manual=false (natuerliches Ende, `queueAudio.onended`) wiederholt dabei denselben Track. `queuePrev()` ist ein reiner Historien-Pop: `history.pop()` wird `current`, der bisherige `current` wandert zurueck an den Pool-Anfang — ohne Historie (Sitzungsanfang) passiert nichts, „Vorheriger" hat dann schlicht nichts, wohin es zurueckgehen koennte.

`saveFilters()`/`loadFilters()` persistieren `shuffle`/`repeatTrack`/`repeatList` wie `groupAlbums` in `localStorage`.

**`toggleShuffle()` startet bei leerem Player selbst eine Wiedergabe.** Ohne geladenen Track (`queueState.current === null`) wirkte ein Klick auf den Zufallswiedergabe-Knopf zuvor wie ein totes Steuerelement — der Modus wurde zwar umgeschaltet, ohne laufenden Pool aendert `advanceFromPool()` aber nichts sichtbar. Jetzt prueft `toggleShuffle()` zuerst genau diesen Fall: ist nichts geladen, setzt sie `shuffle = true` fest (statt zu togglen) und ruft `startQueueFrom(r)` mit einem zufaellig aus `filtered().filter(x => !x.gone)` gezogenen `r` auf — dieselbe Quelle wie ein normaler Zeilen-Klick, der restliche Pool fuellt sich von dort aus wie gewohnt. Ist bereits ein Track geladen, bleibt es beim reinen Umschalten des Flags. Kein Server (`apiMode`) oder eine leere gefilterte Ansicht melden sich ueber `note()` statt stillschweigend nichts zu tun.

**Warteschlange übersteht ein Neuladen** (eigener `localStorage`-Key `tracktab.queue`, getrennt von `saveFilters()`): `saveQueueState()` haengt an `renderPlayerBar()` (nicht an jeder einzelnen Mutation extra, jede Aenderung an `queueState` ruft `renderPlayerBar()` ohnehin auf) und schreibt `{poolPaths, historyPaths, currentPath, manual}` — Pfade statt Zeilenindizes, weil `r.i` nur fuer die Lebensdauer des aktuell geladenen Reports gilt. `loadQueueState()` (aus dem `DOMContentLoaded`-Handler unten) loest alle drei gegen `DATA` auf, verwirft dabei nicht mehr vorhandene Pfade stumm (statt die ganze Warteschlange zu verwerfen) und setzt `queueAudio.src` — **ohne** `play()`, ein Neuladen der Seite soll nicht ungefragt Ton machen.

**Warteschlange leeren** (`clearQueue()`, Knopf im Popup-Kopf, leert Pool **und** Historie) und das Entfernen des aktuellen Tracks ueber `playFromPool()`/`playFromHistory()` (der neue `current` ersetzt den alten sofort) teilen sich bei echter Leere `resetQueueAudio()`: `pause()` + `removeAttribute("src")` **+ `load()`** — ohne das abschliessende `load()` blieben `duration`/`currentTime` (und damit der Fortschrittsbalken) auf dem Stand des zuletzt geladenen Tracks stehen, weil der Browser die Ressourcen-Auswahl des `<audio>`-Elements sonst nicht neu anlaufen laesst (an echtem Material beobachtet).

**Warteschlange-Popup mit zwei Tabs:** `#queuePopup`, `position:fixed` über dem Balken verankert. `queuePopupTab` (modul-lokal, reine Anzeige-Auswahl, NICHT Teil von `queueState`/der Persistenz) steuert, welcher der beiden `[data-qptab]`-Knoepfe aktiv ist; `renderQueuePopup()` verzweigt entsprechend auf `renderQueueUpcomingTab()` (aktueller Track + Pool, Klick auf die laufende Zeile schaltet nur Play/Pause um) oder `renderQueueHistoryTab()` (Historie **umgekehrt** chronologisch gerendert — `queueState.history` selbst bleibt aufsteigend, das haelt `push()`/`pop()` in `advanceFromPool()`/`queuePrev()` einfach). Beide teilen sich `queueRowHTML()` fuer Cover/Titel/Interpret. Drag & Drop zum Umsortieren (`reorderQueue()`, nur im Pool-Tab, die laufende Zeile traegt kein `data-qidx`) verwendet dasselbe native HTML5-Muster wie `attachColumnDrag()` (Spalten-Kopfzeile weiter oben): modul-lokale Variable für den gezogenen Index, `ondragstart`/`ondragover`/`ondrop`.

**Layout:** `.pbarcenter` ordnet Transport-Knoepfe (`.pbarbtns`), Fortschrittsbalken (`.pbarseek`) und Lautstaerke (`.pbarvolume`) als EINE Zeile nebeneinander (`flex-direction:row`) statt gestapelt — die Knoepfe liegen bewusst links vom Zeitstrahl, `max-width:700px` (statt der Fensterbreite) haelt die Zeile auf breiten Fenstern kompakt. Der Warteschlange-Knopf traegt zusaetzlich zum Text das gestapelte-Linien-Symbol `ICONS.queue`; die beiden Zeilen-Knoepfe zum Einreihen teilen sich dieselbe Linien-Basis (fuer Wiedererkennungswert, Absprache mit dem Nutzer): `ICONS.queueAdd` kombiniert die gestapelten Linien mit einem Plus, `ICONS.playNext` dieselben Linien mit einem gefuellten Play-Dreieck an derselben Stelle (`fill="currentColor"` auf dem `<svg>` faerbt nur das Dreieck, die `<line>`-Elemente haben ohnehin keine fuellbare Flaeche).

**Sammelleiste bei Mehrfachauswahl:** `renderBulkBar()` zweigt bei `state.layout === "player"` auf `renderPlayerBulkBar()` ab, statt die Leiste dort komplett zu verstecken — zeigt nur „Zur Warteschlange hinzufügen" (`bulkApply("queue-add-all")`, ruft `addToQueue()` je ausgewaehlter Zeile) und die Merklisten-Knoepfe (`flStateOfSelection()`, aus der vollen Sammelleiste herausgezogen, damit beide Varianten dieselbe Haken/Fragezeichen/leer-Logik teilen). Die QC-Sammelaktionen (Tags/Fix/Rescan/Ignore/Correct/Trash) bleiben dort ausgeblendet, aus demselben Grund wie die Zeilen-Knoepfe. Kein Sammel-Pendant zu `playNext()` — bei mehreren Tracks waere die Zielreihenfolge direkt nach dem aktuellen Track uneindeutig.

**DOM-Reihenfolge:** `#playerBar`/`#queuePopup` stehen wie `#toTopBtn` und die Dialog-Overlays im HTML nach dem `<script>`-Block und existieren zur Skriptausführung selbst noch nicht — Icon-Zuweisung und Knopf-Verdrahtung laufen deshalb in einem eigenen `DOMContentLoaded`-Handler, nicht top-level; ein top-level `document.getElementById(...)` würde zu diesem Zeitpunkt noch ins Leere zeigen. Aus demselben Grund darf `#pbarTitle` in `index.html` **kein** `data-i18n` tragen (nur den literalen deutschen Platzhaltertext als Vor-JS-Fallback) — `applyStaticI18n()` ueberschreibt jedes `data-i18n`-Element blind, auch bei ihrem zweiten Aufruf aus `initStorage()` nach dem Laden der Einstellungen, und wuerde einen laengst per `renderPlayerBar()` gesetzten Titel wieder auf den Platzhalter zuruecksetzen (an echtem Material beobachtet: restaurierte Warteschlange korrekt geladen, Balken zeigte trotzdem "Kein Titel ausgewaehlt").

**Durchlaufender Titel bei Hover:** `#pbarTitle` umschliesst einen inneren `#pbarTitleInner`-Span, der per CSS-Animation verschoben wird (`@keyframes pbarMarquee`, `translateX(0)` zu `translateX(var(--marquee-distance))` mit Halte-Phasen an beiden Enden fuer den Ping-Pong-Effekt). `setupTitleMarquee()` misst bei `mouseenter` `inner.scrollWidth - outer.clientWidth`; passt der Titel (Wert ≤ 0), passiert nichts — die normale `text-overflow:ellipsis` bleibt. Sonst werden `--marquee-distance`/`--marquee-duration` (mindestens 3s, sonst ~30px/s -- lang genug zum Mitlesen) gesetzt und die Klasse `marqueeing` ergaenzt (schaltet zusaetzlich `text-overflow:clip`, sonst ueberlagern sich Ellipsis-Punkte und Animation); `mouseleave` entfernt sie wieder. `renderPlayerBar()` entfernt `marqueeing` bei JEDEM Trackwechsel vorsorglich selbst (die Distanz war fuer den alten Titel berechnet und liefe sonst falsch weiter, falls die Maus beim Wechsel schon auf dem Titel stand). Respektiert `prefers-reduced-motion` (keine Animation, wie beim bestehenden `rowFadeIn`-Muster).

**Lautstaerke:** Regler und Stumm-Taste spiegeln sich gegenseitig (Absprache mit dem Nutzer) — `applyVolume(v)` setzt `queueAudio.volume` **und** leitet `queueAudio.muted = (volume === 0)` daraus ab, `toggleMute()` kehrt nur `.muted` um und laesst `.volume` unangetastet (das native `muted`-Flag haelt den Lautstaerkewert selbst fest, kein manuelles Zwischenspeichern noetig). `renderVolumeUI()` ist die einzige Stelle, die den Regler zeichnet, und tut das ueber einen "wirksamen" Wert (`queueAudio.muted ? 0 : queueAudio.volume`) statt des rohen `volume` -- dadurch zeigt der Regler waehrend Stummschaltung immer 0, unabhaengig vom gemerkten Lautstaerkewert, und springt beim Aufheben zurueck auf dessen Stand. Eigener `localStorage`-Key `tracktab.volume` (`{volume, muted}`, `loadVolumeState()`/`saveVolumeState()`), unabhaengig von `saveQueueState()` — gilt geraeteweit, nicht pro Warteschlange.

**Tastatur-Cursor (Pfeil hoch/runter):** siehe [Anhören mit Waveform](#anhören-mit-waveform) fuer das Nutzerverhalten. `cursorRowI` (modul-lokal, haelt `r.i`) plus `moveCursor(dir)`/`setCursorRow(i)`/`currentActiveRowForCursor()`. `setCursorRow()` toggelt die Klasse `kbcursor` direkt auf dem alten/neuen `<tr>` (kein voller `render()`) und haelt die Zeile im Sichtfenster — bewusst **ohne** `tr.scrollIntoView()`: an echtem Material bestaetigt, dass das bei einem groesseren Sprung (weit entfernte Ausgangs-Scrollposition) auf dieser Tabelle zuverlaessig an der falschen Stelle landet, statt zu zentrieren (`<tr>` ist kein normaler Block, seine Geometrie kommt vom Tabellenlayout). Stattdessen Zielposition per `getBoundingClientRect()` selbst ausrechnen, nur scrollen wenn die Zeile wirklich ausserhalb des Sichtfensters liegt (`rect.top < 0` bzw. `rect.bottom > innerHeight`). Der Zeilen-Template-String bekommt zusaetzlich `${r.i === cursorRowI ? " kbcursor" : ""}`, damit die Markierung auch einen durch einen ANDEREN Anlass ausgeloesten vollen `render()` uebersteht (Filter-/Suchaenderung etc.) — gleiches Prinzip wie `state.selected.has(r.i)` fuer `.picked`. Der ArrowLeft/ArrowRight-Handler ("vorherigen/naechsten Track") steuert seit [Issue #16](#der-globale-mediaplayer--interna) `queuePrev()`/`queueNext(true)` unconditional statt nur in der Player-Ansicht.

### Drag & Drop — Einzelprüfung — Interna

`POST /api/analyse` misst eine gezogene Datei sofort und zeigt sie in einer zweiten `<table>` (`renderDrops()` in `app.js`), strukturell identisch zur Haupttabelle. Die Datenspalten kommen aus denselben `CELL_RENDERERS`-Funktionen wie die Haupttabelle, in der Reihenfolge/Breite/Sichtbarkeit von `state.colOrder`/`state.colWidths`/`state.hiddenCols` (`dropVisibleCols()` filtert dieselbe Liste). Es gibt bewusst **keine eigene** Spalten-Konfiguration fuer diese Tabelle — Drag-Umsortieren und Breiten-Ziehen bleiben exklusiv der Haupttabelle vorbehalten (die Kopfzellen hier tragen kein `data-k`, `bindHeaderSort()`/`attachColumnDrag()` greifen also nicht), Änderungen unter „Spalten ▾" wirken aber sofort auf beide.

`Dateien öffnen` (`POST /api/open-file-pick`) nutzt denselben nativen macOS-Dialog wie beim Import, analysiert aber direkt am echten Pfad (kein Upload/keine Kopie) und landet — wie die Drag-&-Drop-Messung — bewusst nicht in der DB. Ihre Zeilen bekommen dieselbe Aktionsspalte wie eine Tabellenzeile, inklusive Kreispfeil zum Neu-Analysieren (siehe unten) und Papierkorb-Symbol (`data-dropremove`, ruft `removeDrop()` auf: Eintrag aus dem `drops`-Array entfernt, laufenden Player pausiert/entfernt, `blobUrl` freigegeben — kein `POST /api/trash`, die Datei selbst bleibt unangetastet).

**Neu analysieren (`POST /api/analyse-path`).** Bewusst **nicht** `/api/reanalyse`: das verlangt eine DB-Zeile (`_known_file`) und schreibt das Ergebnis zurück, während eine Einzelprüfung nie in der Datenbank landet. `_post_analyse_path()` autorisiert über `_authorize_path()` (kennt also auch die noch ungescannten Pfade aus `open-file-pick`), ruft `analyse_file()` direkt am Ort auf und antwortet nur dem Client. `reanalyseDrops()` in `app.js` ordnet die Antwort **über den Pfad** zu (nie über die Position) und schreibt sie mit `fromServerRow(row, null)` in den bestehenden Eintrag; `_id`, `file`, `blobUrl`, `nativePath`, `mikPick` und `libAdded` werden dabei ausdrücklich erhalten — `_id` ist der Auswahlschlüssel, und ein neu erzeugter `blobUrl` würde den alten lecken lassen.

Der Knopf setzt dabei zusätzlich `extra_checks: true` (`analysePathsSequential()`s dritter Parameter, NUR von `reanalyseDrops()` gesetzt, nicht beim erstmaligen `open-file-pick`/Drag&Drop, damit der schnelle Erstimport keine zusätzlichen Aufrufe kostet). `_post_analyse_path()` prüft dann je Zeile live `media.music_added_date_for()` (Music.app-„date added", Titel-Vorfilter wie `remove_from_music_library()`) und — sofern `rekordbox.is_running()` `False` ist — `rekordbox.content_present()`, beide rein informativ in der Antwortzeile (`music_added_ts`/`rekordbox_present`, fehlt das Feld ganz, wenn kein Treffer). Ein Cover wird bei Bedarf über `coverfill.fill_cover_for_untracked()` direkt in die Datei geschrieben (kein DB-Zugriff, da keine `files`-Zeile existiert). Läuft Rekordbox, liefert die Antwort `rekordbox_running: true`, und der Client zeigt einen Hinweis-Toast statt den Status stillschweigend zu überspringen.

**Mehrfachauswahl.** `dropSelected` (Set von `_id`, nicht von Array-Indizes — neue Treffer kommen per `unshift` vorn dazu, ein Index wäre danach für ältere Zeilen falsch) trägt die Auswahl; `dropSelectable()` beschränkt sie auf Zeilen mit `nativePath`. `renderDropBulkBar()` baut dieselbe Leiste wie `renderBulkBar()`, aber nur mit den Aktionen, die ohne DB-Zeile möglich sind. `openTagsPopupBulk()` bekommt dafür — wie `openTagsPopup()` — ein `isDrop`-Flag. Für einen noch ungescannten Pfad liefert `/api/tags` `row: null` (es gibt nichts zu aktualisieren), deshalb übernimmt der Client dort das, was er selbst verschickt hat: `fieldsToDropRow()` übersetzt die Payload-Feldnamen (`album_artist`, `year`, …) zurück in die kompakten Zeilenschlüssel (`aa`, `yr`, …) über dieselbe `TAGS_BULK_FIELD_MAP`, die auch die Formularfelder verdrahtet.

**„In Music-Bibliothek importieren" zieht den Pfad nach.** `POST /api/add-to-library` importiert die ausgewählten Dateien per AppleScript `add` (nicht `open` wie „In Music öffnen", das nur abspielt). `media.add_to_music_library()` liest je Track die `location`-Eigenschaft, die Music.app dem importierten Track zuweist, und gibt sie **positionsgleich zu den übergebenen Pfaden** zurück (eine Datei ohne lokale `location` — `missing value` — steht als leerer Eintrag an ihrer Stelle; würde man diese Leereinträge herausfiltern, rutschten alle folgenden Pfade um eins nach vorn und die Datenbank bekäme den Pfad einer fremden Datei).

**Zwei Koerzierungen müssen ausserhalb des `tell application "Music"`-Blocks stehen**, beide aus demselben Grund: innerhalb des Blocks geht eine Koerzierung als Apple-Event an Music.app, und Music.app kann sie nicht ausführen. Das betrifft `POSIX file p as alias` (sonst Fehler -1700) **und** `POSIX path of <location>` (sonst Fehler -1728). Das Zweite war lange falsch platziert und hat die Pfadkorrektur komplett wirkungslos gemacht: der Fehler verschwand im `try` und kam als leerer Pfad zurück, `_post_add_to_library()` sah also nie eine Abweichung und liess die DB-Zeile auf der Quelldatei stehen. Die früher hier notierte Vermutung, -1728 träfe nur Cloud-Tracks ohne lokale Datei, war falsch — es traf **jeden** Track. Der `tell`-Block holt deshalb nur noch die `location` selbst (ein Datei-Objekt), umgewandelt wird draussen, in zwei Durchgängen (erst alle `add`, dann die Orte lesen), weil der Ort **nach** dem Kopieren in den Medienordner interessiert. Weicht `location` vom übergebenen Pfad ab und existiert dort eine Datei, schreibt `db.move_path()` den Datensatz um — in **allen** pfadbasierten Tabellen (`_PATH_TABLES`: `files`, `ignored`, `favorites`, `corrected`, `waveform`, `rekordbox`, `music_added`).

Zwei Feinheiten in `move_path()`: je Tabelle wird nur umgeschrieben, wenn dort überhaupt eine Zeile am alten Pfad hängt — sonst würde eine bereits am Zielpfad vorhandene, fremde Zeile ohne Not gelöscht. Und `size`/`mtime` werden frisch von der Datei gelesen, weil Music.app beim Kopieren die mtime ändert und der Cache-Schlüssel `(Pfad, Größe, mtime)` die Datei sonst beim nächsten Scan ohne Not neu vermessen würde. Der Client bekommt die Paare als `moved: [{old, new, row}]` zurück und zieht `drops`- wie `DATA`-Zeilen nach (`applyMovedPaths()`), damit Anhören/Tags/Finder nicht auf die weggezogene Datei zeigen.

**Der Import ist zugleich der Übergang in die Bibliotheksliste.** Ab hier liegt die Datei dauerhaft am endgültigen Ort — also legt `_post_add_to_library()` genau dort auch ihre DB-Zeile an: für jeden Pfad ohne `files`-Zeile läuft `analyse_file()` **am endgültigen Pfad** (nicht das Ergebnis vom alten Ort übernehmen: Music.app kann die Datei beim Kopieren umschreiben, und der Cache-Schlüssel `(Pfad, Größe, mtime)` muss zum Ziel passen), danach `db.save()`. Anschliessend merkt `db.mark_music_added()` die Pfade im `music_added`-Cache vor — mit `time.time()` als `added_ts` statt des von Music.app geführten `date added`: das liesse sich nur über einen vollen Bibliotheks-Abgleich lesen (`media.music_added_dates()`), liegt höchstens Sekunden daneben und wird beim nächsten „Hinzugefügt-Datum abgleichen" ohnehin ersetzt.

**Reihenfolge ist hier wichtig:** `mark_music_added()` muss **vor** dem Bauen der kompakten Zeilen laufen, denn `rows_to_payload()` liest `music_added` und füllt daraus `im`/`da` — und genau nach `da` sortiert der Client danach. Die Antwort trägt `rows` positionsgleich zu den übergebenen Pfaden (Unbekanntes als `null`). Im Client hängt `adoptLibraryRows()` sie an `DATA` an und `revealImported()` macht sie sichtbar: Ansicht „Alle", Suche/Schwellwerte zurückgesetzt, Spalte `da` eingeblendet, `state.sort = "da"` mit `dir = -1` und die neuen Indizes in `state.selected`. Ohne das Zurücksetzen wären die frisch importierten Tracks trotz Auswahl unsichtbar, sobald gerade eine andere Liste offen ist.

**Grenzen:** Das greift nur beim Import *aus diesem Tool heraus*. Und die neu angelegten Zeilen überleben nur, wenn der Music-Medienordner unter `cfg["library_paths"]` fällt: `scan --prune` löscht jede `files`-Zeile, die `scanner.iter_files()` nicht findet.

All das funktioniert, obwohl noch keine DB-Zeile existiert. Der Server erlaubt `POST /api/tags`, `GET/POST /api/cover`, `POST /api/lookup-cover`, `GET /api/audio`, `GET /api/waveform`, `GET /api/rekordbox-extras` und `POST /api/open-in` für einen Pfad ohne DB-Zeile nur, wenn er in der aktuellen Server-Session gerade über `open-file-pick` ausgewählt wurde UND noch existiert (`_Handler._unscanned_paths`, geprüft in `_authorize_path()`) — der übliche `_known_file()`-Datenbankabgleich (siehe [Datenhaltung & Oberfläche](#datenhaltung--oberfläche-glossar)) greift hier bewusst nicht, weil es noch keine DB-Zeile gibt. Schreibende Endpunkte speichern dann nur in die Datei, nicht in die DB (`row: null` in der Antwort); der Client pflegt seine lokale Kopie im `drops`-Array selbst nach.

### Dateien automatisch umbenennen — Interna

`app/rename.py` baut den Namen, `POST /api/rename` benennt um, `renameDrops()` in `app.js` steuert und zieht die Oberfläche nach.

**Namensbau.** `values_for(meta)` formatiert alle Platzhalter zu fertigen Strings (Freitext durch `_slug()`, `{bpm}` → `116bpm`, `{bitrate}` → `320kbps` bzw. `""` bei `codec_family == "lossless"`, `{samplerate}` → `44.1khz`, `{track}` → `08`). `render_name()` teilt das Muster an `_` in Kategorien, ersetzt darin die Platzhalter, räumt zurückgebliebene Bindestriche weg und lässt leer gebliebene Kategorien **komplett** aus — deshalb entsteht nie ein `__` und deshalb braucht die Lossless-Regel keinen Sonderfall. In `_slug()` müssen die Umlaut-Ersetzungen (`_TRANSLITERATE`) **vor** der NFKD-Zerlegung laufen: danach wäre aus `ü` bereits `u` geworden statt `ue`, und `ß` wäre ganz verschwunden.

**Metadaten kommen aus der Datei, nicht aus der Datenbank** (`read_meta()`) — eine Einzelprüfung hat keine DB-Zeile. Interpret/Titel/Album sowie `declared_kbps`/`codec_family`/`sample_rate` liefert `probe.probe()`, also dasselbe ffprobe wie beim Scan: die Bitrate im Dateinamen soll zu der in der Tabelle passen, eine zweite Quelle (z. B. mutagens gemittelte `info.bitrate`) könnte um ein paar kbps abweichen. Genre/Jahr/BPM/Albuminterpret/Komponist/Tracknummer kommen aus `tags.read_extra()`, die Tonart aus `tags.read_key()`.

**Zwei Schutzregeln**, beide in `plan()`: `is_in_library()` vergleicht den aufgelösten Pfad gegen die aufgelösten `library_paths` (Status `in_library`), `has_identity()` prüft, ob mindestens einer der im Muster verwendeten identifizierenden Platzhalter (`_IDENTITY_KEYS`) einen Wert hat (Status `no_data`). Fragt ein Muster gar nicht nach einer Identität, greift die zweite Regel nicht.

**Kollisionen** löst `_unique_target()` mit `-2`, `-3` … auf. Die Quelldatei selbst zählt dabei nicht als besetzt — auf dem großschreibungsunempfindlichen macOS-Standarddateisystem meldet `exists()` sonst auch die eigene Datei, und eine reine Groß-/Kleinschreibungs-Änderung (`Foo.MP3` → `foo.mp3`) bekäme grundlos ein `-2`.

**`_post_rename()` zieht die Zuordnung nach — das ist der kritische Teil.** Nach `os.rename()`:

1. `_unscanned_paths` verliert den alten und bekommt den neuen Pfad. Ohne das verweigert `_authorize_path()` jeden Folgezugriff (`/api/audio`, `/api/tags`, `/api/cover`, `/api/analyse-path`, `/api/add-to-library`) mit „Unbekannter Pfad" — die Zeile stünde dann zwar noch da, wäre aber tot.
2. Gibt es doch eine `files`-Zeile (möglich, wenn die Bibliothekspfade nach einem Scan geändert wurden), schreibt `db.move_path()` sie samt allen pfadbasierten Tabellen um; nur dann läuft anschließend `_rebuild()`, weil der Server `data/report.html` ausliefert und nicht die Datenbank.
3. `audit_log.log("umbenannt", …)` hält alten und neuen Namen fest.

Autorisiert wird vorab über `_authorize_path()` wie bei `/api/analyse-path` — ein fremder Pfad bekommt 404/410, bevor irgendetwas angefasst wird.

**Client.** `renameDrops()` ruft `/api/rename` **einmal je Pfad** auf, aus demselben Grund wie `analysePathsSequential()`: nur so lässt sich „x von y" anzeigen, und je Datei läuft serverseitig ein ffprobe. Der Endpunkt nimmt trotzdem eine Liste entgegen. `applyDropRename(r, newPath)` setzt `r.p` um, wirft einen laufenden `players`-Eintrag am alten Pfad weg (er lüde sonst weiter die nicht mehr existierende URL) und hängt den `coverRev`-Cachebuster um. Der Abschluss-Hinweis ist grün bei mindestens einer Umbenennung, rot nur bei echten Fehlschlägen und gelb (`"soft"`, siehe `toastLevel()`), wenn es schlicht nichts zu tun gab.

**Muster-Prüfung.** `validate_pattern()` läuft aus `settings.validate()` heraus und lehnt ein leeres Muster, ein Muster ohne Platzhalter, unbekannte Platzhalter und `/`, `\`, `:` mit einem direkt anzeigbaren Text ab. Ungültiges landet damit nie in `config.local.yaml`.


### Einstellungen — Interna

`⚙ Einstellungen` zeigt eine generisch aus [`settings.GROUPS`](#appsettingspy) gerenderte Formularstruktur: Bibliothekspfade, Analyse-Parameter, Klassengrenzen (getrennt für MP3, AAC, verlustfrei), Lautheit-Referenzband, externe Programme, Anzahl paralleler Prozesse. Gespeichert wird ausschließlich in `config.local.yaml`; `config.yaml` bleibt unangetastet. Je Gruppe gibt es „Auf Vorgabe" (setzt nur diese Gruppe zurück); ein Löschen von `config.local.yaml` setzt alles zurück.

Zwei Feldern liegt zusätzliches, rein clientseitiges Verhalten bei, ohne eigenen `settings.GROUPS`-Feldtyp zu brauchen (beide bleiben `type: "text"`):
- **`show_if`** (z. B. bei `external_daw_template`): blendet das Feld nur ein, wenn ein anderes Feld (hier `external_daw`) einen bestimmten Teilstring enthält. `applyShowIf()` (`app.js`) wertet das bei jeder Eingabe im Dialog neu aus (Event-Delegation auf `#settingsBody`), nicht nur beim Öffnen — sonst bliebe das Feld nach einem Wechsel der DAW-Auswahl fälschlich sichtbar/versteckt.
- **`pick`** (z. B. bei `external_daw_template`, Wert = Server-Endpunkt wie `/api/pick-daw-template`): rendert einen zusätzlichen „Datei wählen …"-Knopf neben dem Textfeld (`.filepick` in `app.css`). Der Knopf ruft den angegebenen Endpunkt auf, der serverseitig `media.choose_single_file()` nutzt (nativer macOS-Dialog, echter POSIX-Pfad — ein Browser-Dateidialog gibt aus Sicherheitsgründen nie den echten Pfad preis, siehe `choose_audio_files()`-Kommentar in `media.py`) und `{ok, path}` liefert; `pickFile()` (`app.js`) trägt den Pfad ins Textfeld ein. Da der zugrunde liegende Feldwert weiterhin ein einfacher String im selben `data-key`-Textfeld ist, brauchen `settings.validate()`/`collectSettings()` dafür keine Sonderbehandlung — nur `settingField()` weiß von `pick`.

Eigener Abschnitt **Shops**: benutzerdefinierte Such-Links (über Beatport/SoundCloud/iTunes hinaus), gespeichert unter dem Top-Level-Schlüssel `shops`, jeder Eintrag braucht einen `{q}`-Platzhalter in der URL.

### Scannen aus der Oberfläche — Interna

„Bibliothek scannen" startet den Lauf im Hintergrund (`POST /api/scan`) mit Fortschrittsbalken und Abbruch-Möglichkeit (`POST /api/scan/cancel`). Danach wird der Report automatisch neu erzeugt.

### Ausgeblendet / Merken / Korrigiert — wann was?

Alle drei überleben Report-Neubau und Rescan, weil sie in eigenen DB-Tabellen liegen (nicht in `files`). Sie sind **nicht** alle drei gegenseitig exklusiv:

| Zustand | Bedeutung | Wirkung |
|---|---|---|
| **Ausgeblendet** (`✕`) | Fehlalarm — die Datei ist in Ordnung, nicht mehr anzeigen | raus aus Kennzahlen, CSV, M3U; über die Liste „Ausgeblendet" mit `↩` rückgängig machbar |
| **Merken** (farbige Quadrate) | Track gehört einer oder mehreren selbst benannten Merklisten an | reine Kennzeichnung — bleibt in Kennzahlen, CSV und M3U sichtbar |
| **Korrigiert** | Datei wurde per Bitrate-Korrektur im Tool selbst neu kodiert bzw. manuell als behoben bestätigt | ebenfalls aus CSV/M3U ausgeschlossen |

**Ausgeblendet** und **Korrigiert** schließen sich weiterhin gegenseitig aus (markiert man einen ausgeblendeten Track als korrigiert, wandert er von der einen Liste auf die andere). **Merken** ist von beidem unabhängig — ein Track kann gleichzeitig ausgeblendet oder korrigiert UND in einer oder mehreren Merklisten stehen; keine der drei Aktionen rührt die jeweils anderen an.

Anders als Ausgeblendet bleibt eine „Manuell korrigiert"- oder „gemerkte" Zeile in der Liste **voll lesbar** (keine Abblendung) — bei „Manuell korrigiert", weil der Track fachlich als korrekt gilt (`verdictOf(r)` liefert für `r.mc` immer `"OK"`), nur eben durch Nutzerentscheidung statt durch Messung; bei Merken, weil es reine Kennzeichnung ist. Die Anzahl „Manuell korrigiert" steht als eigenes Feld direkt neben „Korrekt" in der Status-Zeile (`#chips`) und lässt sich per Klick darauf filtern.

### Merken — Interna

Ersetzt die frühere, einzelne „Erledigt"-Markierung durch bis zu 3 frei benennbare, farbige Listen (`⚙ Einstellungen → Merklisten`: Name, max. 24 Zeichen, plus eine von 12 Vorgabefarben je Liste über eine immer sichtbare Farbreihe — leerer Name deaktiviert den Slot überall). Jede Zeile trägt in der Spalte „Öffnen" ein farbiges, abgerundetes Quadrat je konfigurierter Liste; ein Klick fügt den Track hinzu (Häkchen erscheint), ein erneuter Klick entfernt ihn wieder — jeweils mit Bestätigungs-Toast. Die Mehrfachauswahl trägt dieselben farbigen Knöpfe — ein Klick fügt alle markierten Zeilen einheitlich hinzu (kein Umschalten bei gemischtem Vorzustand). Beim ersten Start nach dem Update wandert eine bestehende „Erledigt"-Markierung automatisch in die erste Liste, die dabei „Merken" heißt.

### Gelöschte Dateien — Interna

Löscht man eine Datei außerhalb des Tools, bleibt ihr Messwert in der Liste, markiert als „Datei fehlt" (Payload-Feld `gone`); Abspielen/Finder sind dort ausgeblendet. Ein Knopf räumt diese Einträge per `POST /api/prune` aus der DB.

**`gone` steht fest im gebackenen Report** — anders als `ignored`/`favorites`/`corrected`/`rekordbox`/`music_added` gibt es dafür keinen Live-Endpunkt, den `syncMarks()` nachziehen könnte. `/api/prune` **muss** deshalb `_rebuild()` aufrufen; das fehlte lange als einziger zeilenlöschender Endpunkt. Folge: die DB stimmte sofort, `data/report.html` behielt die Zeilen — und weil der Knopf danach zum Neuladen aufforderte, kam genau der alte Stand samt gelber Hinweisleiste zurück. Das Aufräumen sah wirkungslos aus, obwohl es funktioniert hatte.

Zusätzlich liefert `/api/prune` die entfernten Pfade zurück (`paths`), nicht nur deren Anzahl. `pruneMissing()` setzt darauf `removed = 1` in den betroffenen `DATA`-Zeilen und zeichnet neu — `live()`/`counted()`/`filtered()` blenden sie damit sofort aus, ein Neuladen ist nicht mehr nötig.

**Sie kann aber auch nur verschoben sein.** `POST /api/relink` (Knopf „Verschobene suchen" in der Hinweisleiste, Kettensymbol je Zeile) sucht solche Dateien über `scanner.find_moved()` wieder und schreibt die Zeile per `db.move_path()` um. Kandidaten sind ausschliesslich Dateien, die auf der Platte liegen und zu **keiner** DB-Zeile gehören. Verglichen wird über vier Merkmale, von denen **keins allein trägt**:

| Merkmal | überlebt den Umzug | überlebt die Tag-Änderung | Schwäche |
|---|---|---|---|
| Dateigröße | ja | nur seit `db.refresh_stat()` (s.u.) | kollidiert bei Duplikaten |
| Dateiname | ja | ja | Music.app benennt beim Einsortieren um |
| Interpret + Titel | ja | ja (die DB kennt die neuen Werte aus `update_tags()`) | eine zweite Kopie trägt dieselben |
| Spieldauer (±1 s) | ja | ja | völlig unspezifisch |

Akzeptiert wird nur, wenn **mindestens zwei** Merkmale stimmen **und** der beste Kandidat eindeutig besser ist als der zweitbeste; sonst meldet der Endpunkt `ambiguous` bzw. `unmatched`, statt zu raten. Tags und Dauer kommen aus `tags.read_identity()` — bewusst über mutagen statt `probe.py`/ffprobe, gemessen an echtem Material: **rund 1,3 ms je Datei**. Ab 50.000 Kandidaten bricht die Suche mit `truncated: true` ab.

**`db.refresh_stat()` gehört dazu:** `update_tags()` zieht `size`/`mtime` bewusst nicht mit, die Zeile war nach einem Tag-Edit also veraltet — womit die Dateigröße als Merkmal wertlos gewesen wäre. `server.py` ruft `refresh_stat()` deshalb nach jedem eigenen Schreibvorgang (`/api/tags`, `/api/cover`, `/api/cover-delete`, `/api/lookup-cover`).

**Automatisch beim nächsten Speichern:** `server._Handler._authorize_or_relocate()` fängt eine fehlgeschlagene Pfadprüfung ab: `_try_relocate()` versucht denselben Abgleich wie oben, aber automatisch für nur den einen betroffenen Pfad, und **nur** für Zeilen aus `music_added_map()`. Ein prozessweiter, 30 Sekunden gültiger negativer Cache (`_relocate_negative_cache`) verhindert dabei, dass mehrere Anfragen für denselben weiterhin fehlenden Pfad je einen eigenen Scan starten.

### Verwaiste Ordner — Interna

Knopf „Verwaiste Ordner" unter *Einstellungen → Bibliothek* — eigener Feldtyp `"button"` in [`settings.GROUPS`](#appsettingspy), kein Wert zum Speichern, nur eine Aktion. Fragt per `confirmOverlay` noch einmal nach, ruft danach `POST /api/orphan-cleanup` auf.

Der Endpunkt durchsucht jeden Pfad aus `cfg["library_paths"]` über `media.find_empty_folders()` und verschiebt jeden Treffer per `media.move_to_trash()` einzeln in den Papierkorb — ein Fehlschlag bei einem Ordner bricht die übrigen nicht ab (`errors`-Liste in der Antwort). Ändert keine DB-Zeile: ein leerer Ordner enthält per Definition keine Audiodatei, `_rebuild()` ist deshalb anders als bei `/api/prune` nicht nötig.

„Leer" heißt: nichts außer optional einer `.DS_Store`-Datei, rekursiv. `find_empty_folders()`/die interne `_scan_empty()` werten das bottom-up aus. Zurückgegeben werden nur die **äußersten** Treffer: eine verschachtelte Kette leerer Ordner (`A/B/C`, nur `C` enthält `.DS_Store`) liefert nur `A`. Symlinks werden nie als Treffer gewertet und nie durchquert.

### Fallback ohne Server

Wird `report.html` per Doppelklick geöffnet statt über `serve`, weicht die Seite auf `localStorage` aus und zeigt das oben an. Ausblendungen gelten dann nur im jeweiligen Browser und wirken sich nicht auf CSV/M3U aus; Player, Drag & Drop, Finder und Einstellungen brauchen zwingend den Server.

### Server — Endpunktreferenz

Lokaler HTTP-Server (`ThreadingHTTPServer`, ausschließlich `127.0.0.1`). Handler-Klasse `_Handler`; Modul-Singleton `SCAN = jobs.ScanJob()` verfolgt den Hintergrund-Scan über Requests hinweg.

#### Herkunftsprüfung (`_same_origin()`)

Die Bindung an `127.0.0.1` hält das Netz draußen, aber nicht den Browser: **jede** Seite, die der Nutzer geöffnet hat, kann Anfragen an `127.0.0.1` schicken. Zwei Angriffe folgen daraus, und beide beantwortet `_same_origin()`, das als erste Zeile in `do_GET()` und `do_POST()` steht:

- **CSRF.** Eine fremde Seite schickt Schreib-Anfragen. Da `_body()` den `Content-Type` nicht prüft, sondern direkt `json.loads()` aufruft, genügt dafür eine „simple request", die der Browser ohne Vorabfrage (Preflight) durchlässt. Ohne Gegenmaßnahme wäre alles von `/api/trash` bis `/api/settings` von außen auslösbar. Abgewehrt über den `Origin`-Header.
- **DNS-Rebinding.** Ein fremder Name, dessen Adresse nach dem ersten Laden auf `127.0.0.1` wechselt. Aus Browsersicht ist das derselbe Origin — die fremde Seite dürfte die **Antworten lesen** (Bibliothek, Audiodateien, Einstellungen). Dagegen hilft nur der `Host`-Abgleich: die Anfrage kam dann unter einem fremden Namen herein.

Erlaubt sind `127.0.0.1`, `localhost` und `::1` (`_LOCAL_HOSTS`). Ein fehlender `Origin` bedeutet „kommt nicht aus einem Browser" (curl, eigenes Skript) und ist nur lesend erlaubt — **ein POST per `curl` braucht deshalb `-H "Origin: http://127.0.0.1:<port>"`.** Die Ping-Probe aus `desktop._existing_instance()` ist ein GET und davon unberührt.

#### Zwei Riegel vor dem Dateizugriff

Die Freigabe eines Pfades hängt daran, dass er als Zeile in der Datenbank steht (`_known_file`/`_authorize_path`/`_authorize_fast`). Das trägt aber nur, solange die Menge dieser Zeilen nicht selbst über die API bestimmbar ist — sonst ließe sich per `extensions` plus einem Scan jede beliebige Datei des Benutzerkontos einlesbar machen. Deshalb gilt zusätzlich:

- `extensions` nimmt nur Endungen aus `config.AUDIO_EXTENSIONS` an (`settings.validate()`), und `_get_audio()` liefert nur solche Endungen aus — die Prüfung steht also an beiden Enden.
- `POST /api/scan` akzeptiert `paths` nur innerhalb der eingestellten `library_paths` (`_roots_in_library()`). Die Oberfläche schickt ohnehin nie `paths`; der CLI-Weg (`./run.command scan <pfad>`) läuft an `server.py` vorbei und bleibt unverändert frei.

#### Antwort-Header (`_security_headers()`)

Hängt an jeder Antwort, auch an den von Hand gebauten (Audio-Range-Streaming, ZIP-Bündel): `X-Content-Type-Options: nosniff` (der MIME-Typ eines Covers ist freier Text aus der Datei), `X-Frame-Options: DENY` und `frame-ancestors 'none'` (die Oberfläche hat Knöpfe, die Dateien in den Papierkorb legen — eingebettet wären die per Clickjacking bedienbar), `Referrer-Policy: no-referrer` sowie eine CSP mit `default-src 'none'`. `'unsafe-inline'` für Skript und Stil bleibt nötig, weil CSS und JS bewusst in die eine Report-Datei eingebettet sind; `img-src` erlaubt zusätzlich `https:` für die Cover-Vorschauen der Online-Suche und `blob:` für die Vorschau eines noch nicht gespeicherten Covers. `manifest-src 'self'` und `worker-src 'self'` sind fuer die PWA-Installierbarkeit noetig (siehe [PWA / Installierbarkeit — Interna](#pwa--installierbarkeit--interna)) — ohne eigene Angabe fallen beide Ressourcentypen auf `default-src 'none'` zurueck und der Browser verweigert Manifest-Fetch bzw. Service-Worker-Registrierung, ohne dass das im UI sichtbar würde.

**GET-Routen:**

| Pfad | Zweck |
|---|---|
| `/`, `/report.html`, `/index.html` | liefert die vorgebaute `data/report.html` aus — **nicht** `app/webui/` direkt |
| `/manifest.json` | Web-App-Manifest fuer PWA-Installierbarkeit, live aus `app/pwa.py` gebaut (nicht in `data/report.html` eingebacken) — siehe [PWA / Installierbarkeit — Interna](#pwa--installierbarkeit--interna) |
| `/sw.js` | Service Worker, frisch von `app/webui/sw.js` gelesen |
| `/icon-192.png`, `/icon-512.png`, `/apple-touch-icon.png` | PWA-Icons als echte Bilddateien aus `app/pwa.py` (nicht als data:-URI im Manifest) |
| `/api/ping` | Health-Check, prüft `media.available()` |
| `/api/check-update` | Manuell angestoßene Update-Prüfung (Link im Einstellungen-Fuß) gegen `lookup.check_update()` — vergleicht `__version__` mit dem neuesten GitHub-Release von `sebssch/TrackTab`. Liefert `{reachable: false}`, wenn das Repo (noch) nicht öffentlich erreichbar ist oder es keinen Release gibt — kein Fehlerfall, derselbe stille Fallback wie bei den Online-Metadatenquellen |
| `/api/ignored` / `/api/corrected` | Liste der jeweiligen Pfade |
| `/api/favorites` | `{list1: [...], list2: [...], list3: [...]}` — Merklisten-Inhalt aller drei Slots in einem Aufruf |
| `/api/audio` | Audio-Streaming mit Range-Support |
| `/api/waveform` | Waveform-Peaks, cache-durchgereicht |
| `/api/rekordbox-extras` | Rekordbox-Cues (Hot/Memory) fuer einen Track (`{"available": false}`, wenn nicht in Rekordbox/nicht analysiert) |
| `/api/rekordbox` | `{paths: [...]}` — Cache-Inhalt von `rekordbox_paths()` |
| `/api/rekordbox-status` | `{running: bool}` — leichter Vorab-Check (`rekordbox.is_running()`), den die Oberfläche vor jedem Schreibversuch abfragt |
| `/api/music-added` | `{added: {path: unix_ts, ...}}` — Cache-Inhalt von `music_added_map()` |
| `/api/merge-dismissed` | `{dismissed: {genre: [{a,b}, ...], artist: [...], album: [...]}}` — dauerhaft ausgeblendete Zusammenführungs-Vorschläge je Feld (`db.merge_dismissed_pairs()`). Die Werte-/Zähllisten selbst kommen NICHT vom Server — die komplette Bibliothek liegt bereits als `DATA` im Client, `groupCounts()`/`albumGroupCounts()` berechnen sie dort |
| `/api/appicon` | PNG-Icon einer externen App (`?which=finder\|music\|mik\|rekordbox\|daw`) |
| `/api/settings` | Einstellungsschema + aktuelle Werte |
| `/api/scan/status` | Fortschritt des laufenden Scans |
| `/api/cover` | Eingebettetes Cover einer Datei (`?path=`), 404 wenn keins vorhanden |
| `/api/playlists` | `{playlists: [...], items: {id: [pfad, ...]}}` — eigener Baum samt Zuordnungen; gehört wie die Markierungen zur `_rebuild()`-Ausnahme |
| `/api/music-playlists` | Playlisten-Baum aus Music.app (`{playlists: [{id, parent, kind, count, name}]}`) — startet Music.app, deshalb erst beim Aufklappen |
| `/api/music-playlist` | Tracks einer Music.app-Playlist (`?id=<persistent ID>`) mit `known`-Abgleich gegen die eigene Datenbank |
| `/api/rekordbox-playlists` | Playlisten-Baum aus Rekordbox, über `_open_cached()` (kein Backup je Leseabfrage) |
| `/api/rekordbox-playlist` | Tracks einer Rekordbox-Playlist (`?id=`); bei nicht übersetzbarer Smart-Regel `{ok: true, tracks: [], unsupported: true}` |
| `/api/stats` | Jahres-/Monats-Statistik aus Änderungsprotokoll + `events`-Tabelle; baut `data/stats.json` lazy neu, wenn eine der beiden Quellen seit dem letzten Bau gewachsen ist (`stats.is_stale()`), siehe [Statistik — Interna](#statistik--interna) |

**POST-Routen:**

| Pfad | Zweck |
|---|---|
| `/api/ignore` / `/api/correct` | Zustand umschalten |
| `/api/favorite` | Merklisten-Zugehörigkeit setzen (`{path, list_id, flag}`) |
| `/api/playlist` | Baumknoten anlegen/ändern/verschieben/umsortieren/löschen (`{op: "create"\|"update"\|"move"\|"reorder"\|"delete", ...}`); **kein** `_rebuild()`, der Client zieht über `/api/playlists` nach |
| `/api/playlist-items` | Tracks einer Playlist setzen (`{op: "add"\|"remove"\|"set", id, paths}`); `set` trägt die ganze Reihenfolge und ist damit der Weg für Umsortieren und Rückgängig. Unbekannte Pfade werden als `unknown` gemeldet statt still übergangen |
| `/api/reveal` | `open -R <path>` — im Finder zeigen |
| `/api/analyse` | Drag-&-Drop-Einzelprüfung; Ergebnis **nicht** in der DB gespeichert |
| `/api/analyse-path` | Einzelprüfung an ihrem Ort neu messen; autorisiert über `_authorize_path`, Ergebnis **nicht** in der DB gespeichert. Mit `extra_checks: true` (nur vom „neu analysieren"-Knopf) zusätzlich rein informativ: Music.app-„date added" (`music_added_ts`), Rekordbox-Präsenz (`rekordbox_present`, nur bei `rekordbox_running: false`), Cover-Nachtrag direkt in die Datei |
| `/api/rename` | Einzelprüfung nach `cfg["rename_pattern"]` umbenennen; autorisiert über `_authorize_path`, überspringt Dateien in den `library_paths`. Zieht `_unscanned_paths` und (falls vorhanden) die DB-Zeile auf den neuen Pfad nach. Antwort positionsgleich zu `paths`, je Eintrag `status` `renamed`/`unchanged`/`in_library`/`no_data`/`error` |
| `/api/reanalyse` | Erzwungenes Neu-Messen bekannter Dateien (`force=True`); gleicht dabei je Datei Cover (immer, unabhängig von `cover_auto_fill`), Music.app-„date added" und — sofern Rekordbox nicht läuft — Rekordbox-Präsenz live ab (`db.set_music_added`/`set_rekordbox_present`, je Pfad, kein voller Cache-Ersatz). Antwort trägt zusätzlich `rekordbox: {running}` |
| `/api/prune` | DB-Zeilen zu fehlenden Dateien entfernen; **backt den Report neu** und liefert die entfernten Pfade als `paths` |
| `/api/open-in` | Externen Audio-Editor öffnen |
| `/api/open-daw` | Eingestellte DAW öffnen (kein Auto-Fallback, siehe `media.daw_path`) |
| `/api/orphan-cleanup` | Verwaiste Ordner in den Bibliotheksordnern in den Papierkorb verschieben |
| `/api/open-store` | iTunes-Store-Suche/Deeplink öffnen |
| `/api/trash` | Datei in den Papierkorb, zugehörige `files`/`ignored`/`favorites`/`waveform`-Zeilen löschen |
| `/api/rewrite` | Bitrate-Korrektur durchführen |
| `/api/convert` | Format konvertieren (`{paths, target: "mp3_320"\|"aiff", trash_original}`, `trash_original` Standard `true`) — autorisiert über `_authorize_path` (deckt Haupttabelle UND Einzelprüfung ab). Antwort positionsgleich zu `paths`, je Eintrag `status` `converted`/`skip`/`error` (`reason` `upscale`/`already_target` bei `skip`), `original_kept` (= `!trash_original`), plus `music_relinked`/`music_error`/`rekordbox` bei einer DB-Zeile. **Backt den Report neu**, nur wenn mindestens eine DB-Zeile betroffen war |
| `/api/tags` | Tags bearbeiten — Titel/Interpret/Album/Albumkünstler/Komponist/Genre/Jahr/BPM/Kommentar/Tracknummer/-gesamtzahl in Datei + DB schreiben (bei einem noch nicht gescannten, per `open-file-pick` autorisierten Pfad nur in die Datei) |
| `/api/fix-tag-issues` | Sicher automatisch behebbare Auffälligkeiten einer Datei beheben (`{path}`) — `taganomaly.fix_safe()`, schreibt bei DB-Zeile sowohl die betroffenen Tag-Spalten als auch `tag_issues` zurück; ohne DB-Zeile (Einzelprüfung) liefert die Antwort `fields`/`issues` statt einer kompakten Zeile, der Client pflegt seine lokale Kopie selbst nach |
| `/api/genre-rename` / `/api/artist-rename` | Benennt ein Genre/einen Künstler für ALLE betroffenen Tracks um (`{old, new}`) — dünne Wrapper um `_rename_tag_value()`. Siehe [Aufräumen — Interna](#aufräumen--interna) |
| `/api/album-rename` | Wie oben, aber für Album (`{album, group_artist, new}`) — `group_artist` (Album-Künstler, ersatzweise Künstler) grenzt auf genau die angeklickte Gruppe ein, sonst würden zwei gleichnamige Alben verschiedener Künstler zusammenfallen |
| `/api/merge-dismiss` | Blendet einen Zusammenführungs-Vorschlag dauerhaft aus/wieder ein (`{field, a, b, flag}`) — reine Sync-Mark wie `ignored`/`favorites`, deshalb kein `_rebuild()` |
| `/api/cover` | Cover ersetzen — Rohbytes im Body, Pfad im Query-String (wie `/api/audio`), MIME-Typ im `Content-Type`-Header |
| `/api/lookup` | Online-Metadatenvorschläge (iTunes/Deezer/MusicBrainz) — liefert nur Kandidaten, schreibt nichts |
| `/api/settings` / `/api/shops` / `/api/favorite-lists` / `/api/settings/reset` | Einstellungen speichern/zurücksetzen |
| `/api/columns` | Standard-Spalten einer Ansicht speichern (`{layout: "edit"\|"player", order, hidden, widths}`) — je Ansicht (Bearbeiten/Player) getrennt, siehe [Ansichten — Interna](#ansichten--interna) |
| `/api/column-views` | Gespeicherte Spaltenansichten am Stück ersetzen (`{column_views: [{id, name, order, hidden, widths}], default?}`; ohne `default` bleibt die Standard-Spaltenansicht stehen), siehe [Spaltenansichten — Interna](#spaltenansichten--interna) |
| `/api/column-assign` | Einer Liste eine Spaltenansicht zuordnen (`{view, id}`; leere `id` = zurück auf die Standard-Spalten) |
| `/api/reclassify` | Neubewertung ohne Neuanalyse |
| `/api/scan` / `/api/scan/cancel` | Hintergrund-Scan starten/abbrechen |
| `/api/quit` | Server geordnet beenden (Knopf **Beenden**). Läuft ein Scan, antwortet er `409` mit `scan_running: true`, damit der Client nachfragen kann; `{"force": true}` bricht den Scan ab und beendet trotzdem |
| `/api/stats/rebuild` | Erzwingt den Statistik-Neubau unabhängig vom Staleness-Vergleich (Knopf „Neu berechnen" im Statistik-Dialog) |
| `/api/rekordbox-sync` | Voller Abgleich gegen Rekordbox' Sammlung (`db.sync_rekordbox_presence`); ein Fehlschlag laesst den zuletzt bekannten Stand stehen |
| `/api/music-added-sync` | Voller Abgleich gegen Music.app (`media.music_added_dates()` + `db.sync_music_added`); dieselbe Fehlschlag-Absicherung wie beim Rekordbox-Abgleich |

**Interne Hilfsmethoden:** `_same_origin` (Herkunftsgate, erste Zeile beider Dispatcher — siehe oben), `_security_headers` (Antwort-Header, auch für die von Hand gebauten Antworten), `_roots_in_library` (Scan-Wurzeln gegen `library_paths`), `_known_file`/`_reject_missing` (Sicherheitsgate — jeder Endpunkt, der einen echten Dateisystempfad anfasst, prüft zuerst, ob der Pfad als Zeile in der DB existiert UND die Datei noch auf der Platte liegt), `_authorize_path` (Obermenge davon, akzeptiert zusätzlich Pfade aus `_post_open_file_pick`), `_authorize_fast` (nur die Ja/Nein-Freigabe, mit kurzem Zwischenspeicher — siehe unten), `_send`/`_json`/`_fail`/`_query`/`_body` (Low-Level-Response-/Request-Helfer), `_rebuild(cfg)` (baut Report nach jeder DB-Änderung neu **und verwirft den Freigabe-Cache**), `_after_scan(cfg)` (Poll-Thread, wartet auf Scan-Ende und rebuiled).

**Gotchas:** Jeder Endpunkt mit Dateizugriff läuft über `_known_file`/`_authorize_path`. SQLite-Verbindungen werden pro Request geöffnet, nicht geteilt; seit der Umstellung auf WAL laufen die reinen Lesepfade **ohne** das `threading.Lock` (der Lock hat dort nichts geschützt, sondern nur alle Endpunkte gegeneinander serialisiert — Audio-Range gegen Cover gegen Waveform). `/api/trash` bereinigt beim Löschen alle pfadbasierten Tabellen mit, inklusive `corrected` und `favorites`.

### Ausliefer-Endpunkte: Caching & Streaming

Die fünf reinen Ausliefer-Endpunkte (`/api/audio`, `/api/cover`, `/api/waveform`, `/api/rekordbox-extras`, `/api/download-zip`) fassen die DB-Zeile nicht an und brauchen nur die Ja/Nein-Freigabe. Sie nutzen deshalb `_authorize_fast()`:

- **`/api/download-zip`** dient zwei Aufrufern: dem `DownloadURL`-Mechanismus beim Ziehen einer Mehrfachauswahl aus dem Browserfenster hinaus (`ondragstart` in der Zeilen-Tabelle, `app/webui/app.js`) und dem Export-Symbol der Sammelleiste (`openExportZipPopup()`). Nur Letzteres setzt die zwei optionalen Query-Parameter `rename=1` + `pattern=<Namensmuster>`: gesetzt, baut der Endpunkt den Archivnamen je Datei über `rename.values_for()`/`render_name()` (Fehler je Datei → Fallback auf den Originalnamen, das Original auf der Platte bleibt so oder so unangetastet); ein ungültiges Muster liefert `400` mit der Meldung aus `rename.validate_pattern()`.
- **Freigabe-Cache** (`_AuthCache`, `_AUTH_TTL_S = 3.0`, prozesslokal, gedeckelt auf 256 Einträge). Anlass: ein Sprung im Track erzeugt eine Range-Anfrage, ein Zug über den Fortschrittsbalken Dutzende — alle für denselben Pfad, und jede ging vorher einzeln durch die Datenbank. Bewusst **nicht** für Endpunkte, die die zurückgegebene Zeile weiterverwenden (`_post_rename`, `_post_rewrite`, …): dort wäre eine veraltete Zeile ein echter Fehler, keine Unschärfe. Verworfen wird der Cache in `_rebuild()` (also nach jeder zeilenändernden Aktion) sowie direkt in `_try_relocate()`, das an `_rebuild()` vorbeiläuft.
- **`/api/audio`** liefert per `socket.sendfile()` aus — der Kernel schaufelt direkt vom Dateideskriptor in den Socket, statt je 256-KB-Häppchen ein `bytes`-Objekt in Python zu erzeugen. Dazu ein `ETag` aus Inode, Größe und `st_mtime_ns`: `If-None-Match` beantwortet ein erneutes Laden mit `304`, und `If-Range` sorgt dafür, dass ein Teilbereich nach einer Bitratenkorrektur nicht auf die alte Fassung zeigt (der Server antwortet dann mit der ganzen Datei statt mit `206`).
- **`/api/cover`** schickt `ETag` + `Cache-Control: private, max-age=604800`. Das ist gefahrlos, weil der Client bei jeder eigenen Cover-Änderung ohnehin ein neues `&v=<rev>` an die URL hängt (siehe `bumpCoverRev()`). Vorher stand dort `no-store`: jede Neuzeichnung der Tabelle und jeder Trackwechsel holte damit jedes Bild erneut, und serverseitig hieß das jedes Mal die komplette Datei durch mutagen parsen.
- **`_send()`** setzt `no-store` nur noch als **Vorgabe**: ein eigener `Cache-Control`-Wert in `extra` ersetzt sie. Zwei Header gleichen Namens fasst der Browser zu einer Liste zusammen, in der `no-store` immer gewinnt — das eigene `max-age` blieb dadurch wirkungslos (betraf bisher schon `/api/appicon`).
- **`/api/waveform`** teilt gleichzeitige Anfragen für denselben Pfad auf **einen** ffmpeg-Lauf auf (`_waveform_shared()` mit `threading.Event`). Vorher startete jede gleichzeitig aufgeklappte Zeile ihren eigenen Unterprozess, der die Datei komplett nach f32 dekodiert (bei 8 kHz mono rund 10 MB je 5-Minuten-Track, vollständig im Speicher). Der dauerhafte Speicher bleibt der DB-Cache (`db.waveform_put`).

### PWA / Installierbarkeit — Interna

`/manifest.json` und `/sw.js` machen die Oberflaeche installierbar (Chrome/Edge-Installiersymbol, Safaris „Zum Dock hinzufuegen") — danach laeuft sie in einem eigenen Fenster ohne Tabs/Adresszeile statt in einem Browser-Tab. Weder die gebaute `.app` noch `./run.command serve` boten das vorher: `app/macapp.py`s `NSApplication`-Huelle ist reine Lifecycle-Plumbing (Cmd+Q/Dock-Quit), kein `NSWindow`/`WKWebView` — beide Wege oeffneten schon immer nur den System-Browser via `media.open_url()`.

**CSP-Voraussetzung:** `manifest-src 'self'; worker-src 'self'` in `_CSP` (`server.py`) ist zwingend, siehe [Antwort-Header](#antwort-header-_security_headers). Ohne diese beiden Direktiven faellt der Browser auf `default-src 'none'` zurueck und verweigert Manifest-Fetch bzw. Service-Worker-Registrierung — das ganze Feature waere wirkungslos, ohne dass irgendwo ein Fehler im UI sichtbar wuerde.

**Icons:** `app/pwa.py` haelt drei base64-PNGs (180/192/512 px), generiert von `build_assets/make_icon.py::patch_pwa_module()` aus der gepolsterten App-Zeichnung (wie `appicon.icns`), **nicht** der randlosen `flat`-Fassung, die fuer die Favicons in `index.html` verwendet wird — eine installierte PWA bekommt auf macOS ein eigenes Dock-Icon und soll wie das App-Symbol aussehen. `build_assets/` selbst ist im gebuendelten Bundle zur Laufzeit nicht verfuegbar (`config.bundle_dir()` deckt nur `app/webui`, `config.yaml`, `vendor/` ab), deshalb die base64-Konstanten als versionierter Python-Quellcode statt eines Laufzeit-Dateizugriffs. Dieselbe 192px-Fassung wandert zusaetzlich als bare Base64 in `app/webui/sw.js`s `OFFLINE_ICON_B64`-Konstante (`build_assets/make_icon.py::patch_sw_js()`, gleiches Prinzip: Variablenname als Anker, Treffer-Anzahl geprueft statt stillschweigend nichts zu ersetzen) — die Offline-Seite zeigt sie zentriert ueber dem Text.

Anders als die Favicons in `index.html` haengen diese drei Icons **nicht** als data:-URI im Manifest bzw. im `apple-touch-icon`-Link, sondern werden ueber echte Bild-Routen ausgeliefert (`/icon-192.png`, `/icon-512.png`, `/apple-touch-icon.png` — `server.py`, direkt aus `pwa_mod.ICON_*_PNG`). Grund: Safaris „Zum Dock hinzufuegen" ignoriert data:-URI-Icons sowohl im Manifest als auch in `apple-touch-icon` nachweislich unzuverlaessig und faellt sonst auf ein generisches Buchstaben-Icon zurueck ([Apple Developer Forum](https://developer.apple.com/forums/thread/738535); [Bericht zu favicon.ico-Fallback](https://coywolf.com/guides/how-to-create-pwa-icons-that-look-correct-on-all-platforms-and-devices/)). `<link rel="apple-touch-icon">` (`index.html`) ist zusaetzlich zum Manifest gesetzt, weil es laut Apple-Dokumentation Vorrang vor den Manifest-Icons hat — die dokumentierte Prioritaet ist apple-touch-icon > Manifest-Icons > `favicon.ico`.

**Kein echtes Offline-Caching:** TrackTab haengt fuer alles (Datenbank, ffmpeg/ffprobe, AppleScript-Automation, Audio-Streaming) am laufenden lokalen Server — eine gecachte Kopie waere immer veraltet. `app/webui/sw.js` nutzt deshalb bewusst nirgends `caches.match()`/`caches.put()`. Der einzige `fetch`-Handler faengt Seitenaufrufe (`mode === "navigate"`) bei einem Verbindungsfehler ab und zeigt eine kurze eingebettete „Server nicht erreichbar"-Seite statt der nackten Browser-Fehlermeldung — reine Fehlerbehandlung fuer den wahrscheinlichsten Fehlerfall (Dock-Icon angeklickt, bevor der Server laeuft), kein Offline-Modus. Alles andere (`/api/*`, Audio-Streaming, Waveform, Cover) reicht der Handler unveraendert durch.

**Start-Knopf auf der Offline-Seite:** Eine PWA kann als Web-Technik keinen nativen Prozess starten — das ist eine Sandbox-Grenze von macOS/Browser, keine Einschraenkung von TrackTab selbst. Die „Server nicht erreichbar"-Seite in `sw.js` traegt deshalb einen Knopf mit `href="tracktab://start"`, einem eigenen URL-Schema, das nur die gebaute `.app` registriert (`tracktab.spec`, `CFBundleURLTypes`; im Terminal-Betrieb `./run.command serve` gibt es kein Bundle dafuer, der Knopf bleibt dort wirkungslos). Ein Klick laesst macOS die App starten (oder in den Vordergrund holen, falls sie schon laeuft) und liefert den Apple Event an `app/macapp.py::application_openURLs_`, das denselben `_focus_or_open()`-Weg wie ein Dock-Klick nimmt. Ein Inline-Skript auf der Seite pollt danach `/api/ping` alle 1,5 s (bis zu 40 Versuche) und laedt die Seite bei Erfolg automatisch neu — ohne das muesste manuell neu geladen werden, sobald der Server tatsaechlich steht. Der eigentliche Serverstart (samt automatischem Oeffnen eines Browser-Tabs ueber `open_browser`) laeuft unveraendert ueber den normalen Kaltstart-Weg (`desktop.main()`/`server.serve()`) — der Knopf ersetzt nur den manuellen Wechsel zu Finder/Spotlight.

**Dieselbe Seite ist auch das Ziel nach einem bewussten Beenden** (Knopf „Beenden" in der Oberflaeche) — ein fruehers, eigenes `#quitveil`-Overlay in `app.js`/`app.css` gibt es nicht mehr, es existiert nur noch diese eine Implementierung. Grund fuer den Umweg statt eines sofortigen `location.reload()`: `POST /api/quit` (`server.py::_post_quit`) beantwortet die Anfrage mit `{"ok": true}`, **bevor** der Server wirklich herunterfaehrt — `request_stop(delay=0.4)` wartet in einem eigenen Thread erst 0.4 s, ehe `httpd.shutdown()` laeuft, und `serve_forever()`s eigener Poll-Takt (0.5 s) braucht danach nochmal bis zu 0.5 s, um das zu bemerken; erst dann schliesst `serve()`s `finally`-Block mit `httpd.server_close()` den Socket wirklich. Ein `reload()` direkt nach der Quit-Antwort traefe also fast immer noch den lebenden Server. `app.js::reloadAfterQuit()` pollt deshalb `/api/ping` alle 200 ms und wartet auf den **ersten Fehlschlag** — die Kehrseite des Erfolgs-Polls hier auf der Offline-Seite — und ruft erst dann `location.reload()`; eine Obergrenze von 30 Versuchen laedt notfalls trotzdem neu. Im Browser beobachtet (Netzwerk-Log): auf `POST /api/quit` folgen typischerweise 2-3 erfolgreiche `/api/ping`-Anfragen, bevor die naechste mit Verbindungsfehler scheitert und der Reload greift — ohne den Poll waere in diesem Fenster fast immer nochmal kurz die normale Seite aufgeblitzt.

**Zwei bewusste Ausnahmen von der i18n-Pflicht** (siehe CLAUDE.md „Sprache & Konventionen"): `name`/`short_name` im Manifest sind OS-Chrome (Dock-Tooltip, Installations-Dialog), werden serverseitig in `app/pwa.py` erzeugt und beruehren nie `app.js`s `t()`/`data-i18n` — behandelt wie das bestehende `<title>TrackTab</title>` und `CFBundleDisplayName` (`tracktab.spec`), ein fixer, sprachunabhaengiger Produktname. Der Text auf der „Server nicht erreichbar"-Seite in `sw.js` (nur `TrackTab` unter dem Icon plus das Knopf-Label „Starten") laeuft in einem eigenen Kontext ohne Zugriff auf `I18N_DE`/`I18N_EN` — bewusst kurz und ohne eigene i18n-Pipeline gehalten, statt dafuer extra Schluessel in beide Sprachdateien aufzunehmen.

`app/webui/sw.js` ist bewusst **nicht** Teil von `_UI_FILES` (`report.py`) — ein Service Worker kann nicht in die Single-File-`report.html` eingebettet werden, er braucht eine eigene, erreichbare URL. Aenderungen an `sw.js` brauchen deshalb anders als am uebrigen `app/webui/` **keinen** `./run.command report`-Neubau, nur einen Serverneustart — dasselbe gilt fuer `/manifest.json`, das live aus `app/pwa.py` erzeugt wird.

### Verbindungsaufbau: `_setup_once` & WAL

`db.connect()` trennt den Verbindungsaufbau von der Einrichtung. Schema (`_SCHEMA`), Spaltenmigration (`_ensure_columns`), `_migrate_solved_to_favorites` und `_seed_system_playlists` laufen über `_setup_once()` **einmal je Datenbankdatei und Prozess**.

Vorher lief das bei jedem `connect()` — und `connect()` steht in jedem einzelnen HTTP-Endpunkt, auch in denen, die der Player während der Wiedergabe im Sekundentakt anfasst. Das waren pro Anfrage 18 DDL-Anweisungen, ein `PRAGMA table_info`, die Solved-Prüfung und 6 `UPDATE` auf `playlists` samt drei `commit()`, also Schreib-I/O für einen reinen Lesezugriff. An einer echten `quality.db` (10.822 Zeilen, 56 MB) gemessen: **0,35 ms gegen 0,19 ms**; mit Treffer im Freigabe-Cache entfällt der Datenbankzugriff ganz.

Dazu `PRAGMA journal_mode=WAL`, `synchronous=NORMAL` und `busy_timeout=5000`. **Zwei Fallstricke, die daran hängen:**

- WAL legt `quality.db-wal` und `-shm` neben die Datei. Wer die Datei roh ersetzt, muss beide mit entfernen — sonst wendet SQLite ein liegengebliebenes WAL der **alten** Datei auf den frisch eingespielten Stand an. `backup.restore_backup()` tut genau das. (`backup.create_backup()` ist unkritisch: `VACUUM INTO` liest durch die Verbindung und sieht das WAL.)
- Ein wiederhergestelltes Backup kann ein älteres Schema haben. `db.forget_setup(pfad)` lässt die einmalige Einrichtung erneut greifen; `restore_backup()` ruft es auf.

### Ausgabedateien — Interna

| Datei | Inhalt |
|---|---|
| `report.html` | die eigentliche Oberfläche — sortier-/filterbar, mit aufklappbarem Spektrum je Zeile |
| `report.csv` | dieselben Daten für Numbers/Excel (semikolonfrei, `utf-8-sig`, deutsche Dezimalkommas) |
| `verdaechtig.m3u8` | Playlist aller auffälligen (FAKE/VERDÄCHTIG) Tracks zum Gegenhören |
| `quality.db` | SQLite mit allen Messwerten — die Grundlage aller Reports |
| `stats.json` | Jahres-/Monats-Statistik (Aktionszähler, Wiedergabezeit, Top-Tracks/-Interpreten/-Genres), lazy gebaut — siehe [Statistik — Interna](#statistik--interna) |

### Statistik — Interna

Jahres-/Monats-Auswertung aus zwei unabhängigen, komplementären Rohquellen — keine davon war vor diesem Feature ausgelesen:

- **Aktionszähler** (`reencode`/`convert`/`tags`/`cover`/… — die vollständige Liste steht bei [Änderungsprotokoll](#datenhaltung--oberfläche-glossar)) kommen ausschließlich aus dem täglichen JSON-Lines-Änderungsprotokoll (`audit_log.py`). Das Kalenderdatum steckt nur im Dateinamen (`logs/YYYY-MM-DD.json`), nicht in der Zeile selbst (die trägt nur `time` als Uhrzeit) — `stats._iter_log_actions()` parst deshalb den Dateinamen, nicht den Zeileninhalt.
- **Wiedergabezeit/Top-Tracks** kommen ausschließlich aus der DB-Tabelle `events` (`kind = "play"`, siehe [Datenhaltung & Oberfläche](#datenhaltung--oberfläche-glossar)) — im Änderungsprotokoll taucht Wiedergabe nirgends auf.

`app/stats.py` (neu) aggregiert beide Quellen in einem Durchlauf je Quelle zu `{jahr: {actions, listen_seconds, months: [12 Einträge], top_tracks, top_artists, top_genres}}` und schreibt das Ergebnis atomar (Temp-Datei + `os.replace()`, wie `convert.py`/`rewrite.py`) nach `data/stats.json`. `top_tracks` hält bis zu 50 Einträge (nicht nur die in der Oberfläche gezeigten 10) — ein Datensatz, zwei Verwendungen: die Kachelansicht zeigt die ersten 10, „Playlist aus Top 50 erstellen" (siehe unten) nutzt alle 50, ohne eine zweite Abfrage zu brauchen. `top_artists`/`top_genres` gruppieren dieselbe Rohliste zusätzlich nach `files.artist`/`files.genre` (Top 10 nach Hörzeit); Pfade ohne Wert in diesem Feld fallen aus der Rangliste, statt als „Unbekannt" zu erscheinen — verzerrt sonst bei dünnem Tag-Bestand die Spitze.

**Eigene Datei statt DB-Tabelle:** Die Aggregation ist tief verschachtelt (Jahr → Monat → Aktionszähler-Dict + Top-Listen) — genau das Modell, für das JSON gemacht ist; eine Tabelle bräuchte entweder mehrere Zusatztabellen oder ein JSON-Blob-Feld und hätte dann keinen Vorteil gegenüber einer Datei. Größenordnung bleibt klein (deutlich unter `report.csv`), und das Muster entspricht bereits `report_path`/`csv_path`/`m3u_path`: eine abgeleitete, jederzeit neu berechenbare Ausgabedatei statt einer weiteren Tabelle in `quality.db`, die bei jedem `backup` mitgesichert würde, obwohl sie sich aus Log + `events` komplett neu bauen lässt.

**Lazy statt Hintergrund-Rebuild:** Anders als `report.html` (dirty-Flag + Hintergrund-Worker, siehe [Ausliefer-Endpunkte](#ausliefer-endpunkte-caching--streaming)) baut `GET /api/stats` nur dann neu, wenn `stats.is_stale()` das für nötig hält — ein billiger Vergleich aus der jüngsten Log-Datei-`mtime` und `MAX(events.ts)` gegen die beim letzten Bau in `stats.json` gespeicherten Werte (`covers_through`). Bewusst kein Hook in die rund 15 `audit_log.log()`/`log_event()`-Aufrufstellen — bei der bisherigen Datenmenge (wenige tausend Log-Zeilen) ist ein Neubau ein Bruchteil einer Sekunde, ein Dialog-Öffnen-Klick reicht als Auslöser. Zusätzlich ein expliziter „Neu berechnen"-Knopf im Dialog (`POST /api/stats/rebuild`, erzwingt den Bau ohne den Staleness-Vergleich).

**Web-UI:** Eigenes Vollbild-Overlay (`#statsOverlay`, Knopf `#btnStats` neben „Einstellungen") statt eines Sidebar-Eintrags — die Statistik filtert keine Tabellenzeilen wie die übrigen `VIEWS`, sie fasst sie zusammen. Zwei Balkendiagramme (Aktionen/Monat, Hörzeit/Monat) als reines inline-SVG (`statsBarChartSVG()`) statt der Canvas-/RAF-Infrastruktur der Wellenform ([Zeichenebene](#zeichenebene-statische-ebene--eine-bildschleife)): nur 12 Datenpunkte, kein Echtzeit-Update — die RAF-Schleife wäre dafür reiner Overhead. Bei 16 Aktionstypen wäre ein gestapeltes Balkendiagramm mit 16 Farben kaum lesbar; stattdessen eine sortierte Liste „Aktionen im Jahr" darunter. Monatsnamen fest über `toLocaleDateString("de-DE", {month: "short"})` — das Projekt formatiert Zahlen/Daten durchgängig hart auf `de-DE`, unabhängig von `ui_language` (spart 24 zusätzliche i18n-Schlüssel).

**„Playlist aus Top 50 erstellen":** braucht keinen eigenen Server-Endpunkt — nutzt die bestehende Playlist-API (`POST /api/playlist` mit `op: "create"`, danach `POST /api/playlist-items` mit `op: "set"`) über denselben Client-Weg wie `createPlaylistNode()` (Namens-/Symbol-/Farb-Abfrage über `askPlaylistProps()`/`#nameOverlay`, dann `reloadPlaylists()` + `selectView()`). Die Reihenfolge der neuen Playlist entspricht der Rangfolge in `top_tracks` (`set_playlist_items()` übernimmt eine übergebene Liste 1:1 als `pos`).

Rohe Aktions-Strings aus dem Änderungsprotokoll werden nirgends serverseitig umbenannt — `actions` in `stats.json` trägt sie 1:1. Übersetzung/Anzeige-Label passiert ausschließlich clientseitig über `t("stats.action." + slug)` (`statsActionKey()`), Slug = Aktionsstring mit `-`/Leerzeichen durch `_` ersetzt.

### Aufräumen — Interna

`genre`/`artist`/`album` sind flache `TEXT`-Spalten in `files` (aus den Datei-Tags gelesen, `tags.read_extra()`), nirgends normalisiert — weder Groß-/Kleinschreibung noch Leerraum. „Hip Hop", „hip hop" und „Hip Hop " sind drei verschiedene Werte. Bubble-Reihe, Tabellen-Gruppierung UND Umbenennen matchen deshalb überall bewusst exakt, genau wie `rekordbox._get_or_create_genre()`/`_get_or_create_artist()`/`_get_or_create_album()` das für den Rekordbox-Abgleich ohnehin schon tun:

- **Werte + Zählung kommen NICHT vom Server.** Die komplette Bibliothek liegt als `DATA` bereits im Client (siehe die Performance-Hinweise zu Suche/Sortierung weiter oben) — `groupCounts(key)` (Genre/Künstler, ein Feld) und `albumGroupCounts()` (Album, Schlüssel `Künstler + " " + Album` wegen gleichnamiger Alben verschiedener Künstler) reduzieren `DATA` einmal, gecacht bis `invalidateFieldValueCache()` (nach jedem Tag-Schreibvorgang) es verwirft. Kein `GET /api/genres`-Äquivalent nötig.
- **Tabellen-Gruppierung** (`groupMode` in `filtered()`/`render()`): `grp:genre`/`grp:artist` gruppieren nach dem rohen Feldwert (kein `trim()`/`toLowerCase()`), `grp:album` über `albumGroupKeyExact()` — eine bewusst *exaktere* Variante von `albumGroupKey()` (das bleibt unverändert für die allgemeine Checkbox „nach Album gruppieren“ andernorts, dort weiterhin großzügig fallunempfindlich).
- **Umbenennen, EIN gemeinsamer Kern:** `_rename_tag_value(field, old, paths_fn, new, media_setter)` in `server.py` — schreibt Datei-Tag (`tags.write_tags()`) und DB-Zeile (`db.update_tags()`/`db.refresh_stat()`) für jeden Pfad aus `paths_fn(conn)` (`db.paths_by_genre()`/`paths_by_artist()`/`paths_by_album(conn, album, group_artist)`), einzelne fehlschlagende Dateien brechen den Rest nicht ab. Erst danach, best-effort: **Music.app** (nur Pfade aus `music_added_map()`, `media._set_tracks_field()` — ein `osascript`-Aufruf für beliebig viele Tracks per Titel-dann-Pfad-Matching, Thin Wrapper `set_tracks_genre()`/`_artist()`/`_album()`) und **Rekordbox** (nur Pfade aus `rekordbox_paths()`, `rekordbox.update_relocated_tracks()` mit `path == prior_path` — kein echter Umzug, löst aber den vollen Tag-Abgleich aus; läuft Rekordbox, wird `RekordboxRunning` abgefangen und als `rekordbox.running: true` gemeldet statt die Anfrage fehlschlagen zu lassen). `old`/`new` werden serverseitig bewusst NICHT getrimmt (siehe `_post_genre_rename()`s Docstring) — sonst würde ausgerechnet der Leerzeichen-Unterschied, den ein Zusammenführungs-Vorschlag beheben soll, den Gleichheits-Check `old == new` fälschlich auslösen.
- **Zusammenführungs-Vorschläge** (`findMergeSuggestions()`): vier Kategorien absteigender Konfidenz — Leerzeichen/Groß-Klein über einen gemeinsamen kleingeschriebenen, getrimmten Schlüssel (`byNorm`-Map statt paarweisem Vergleich), Tippfehler über `levenshteinWithin()` mit Längen-Bucketing als Vorfilter (bei tausenden Künstlernamen sonst O(n²)), Varianten über feste Suffix-Muster (Single/Remix/„!“). Dauerhaft ausgeblendete Paare liegen in `merge_dismissed` (`field, value_a, value_b` kanonisch sortiert) und werden über `/api/merge-dismissed`/`/api/merge-dismiss` synchronisiert wie `ignored`/`favorites` — ohne `_rebuild()`, reine Sync-Mark. Ein Klick auf den Vorschlagstext setzt `state.mergePreview` (schränkt `filtered()` auf genau die zwei Werte ein, damit vor dem Umbenennen sichtbar ist, welche Tracks betroffen sind); „Ziel auswählen“ (`askMergeTarget()`) lässt den Zielwert bestimmen statt ihn automatisch aus der höheren Trackzahl abzuleiten, und hebt unsichtbaren Leerraum in den beiden Optionen farblich hervor (`visualizeMergeValue()`).

**Web-UI:** Die Bubble-Reihe + Vorschläge-Fläche stecken direkt im Kopf der geöffneten Liste (`#playlistHeader`/`.plhead`, `.plhead-row` für die ursprüngliche Icon/Name/Anzahl-Zeile, `#plHeadBubblesRow`/`#plHeadMergeSuggestions` darunter) statt in einem eigenen Block — dieselben Elemente werden für alle drei Felder wiederverwendet (`renderPlaylistHeaderValueBox()`, `GRP_VIEW_FIELD` löst `state.view` auf ein Feld auf oder `undefined` für jede andere Liste, dann bleibt alles ausgeblendet). Zwei unabhängige Zustände je Feld (`valueBoxState`): `expanded` (Bubble-Höhe 200/550px über den zentrierten `#plHeadBubblesExpand`-Link) und `mergeOpen` (Vorschläge-Sichtbarkeit über `#plHeadMergeToggle`) — kein gemeinsamer „ganze Box zuklappen“-Schalter. Die Bubbles selbst sind neutral eingefärbt (`.genrebubble`, an `.chip` angelehnt) statt farbcodiert wie `.fltag` bei den Merklisten. Das Umbenennen-Dialogfeld ist keine neue Komponente, sondern derselbe `askPlaylistProps()`/`#nameOverlay`, den Playlisten/Spaltenansichten schon nutzen (`withStyle=false`, zusätzlich ein optionales Icon vor dem Titel und — neu — ein Autocomplete, das anders als beim Tags-Dialog erst beim Tippen öffnet statt sofort bei Fokus, da das Feld ja schon einen gültigen Wert enthält).

**Wichtig für `[hidden]`:** ein Element mit `hidden`-Attribut UND einer eigenen `display:...`-Regel (z. B. `.chipsrow{display:flex}`, `button.act{display:inline-flex}`) bleibt trotz gesetztem Attribut sichtbar, weil Autoren-Regeln UA-Vorgaben unabhängig von der Selektor-Spezifität schlagen. `app.css` hat deshalb global `[hidden] { display:none !important; }` — ohne das blieben Bubble-Reihe/Vorschläge-Knopf beim Wechsel auf eine andere Liste sichtbar stehen.

### Als App bauen — Interna

Liegt ein statisch gelinktes `ffmpeg`/`ffprobe` in `vendor/`, wandert es mit ins Bundle (siehe [Modulreferenz zu `config.py`](#appconfigpy)).

**Starten.** Ein zweiter Doppelklick bei bereits laufender App startet **keine** zweite Instanz: die App erkennt die laufende (sie fragt die Ports 8756–8775 der Reihe nach über `/api/ping` ab) und holt nur deren Browser-Tab wieder nach vorn. Ohne diese Prüfung liefen zwei Server auf derselben Datenbank, und die zweite Oberfläche bekäme Änderungen der ersten nicht mit.

Hat sich seit dem letzten Start etwas an der Oberfläche geändert (typisch nach `./build_app.sh`), backt die App den Report vorher neu. Aus einem Bundle heraus lässt sich `report` nicht von Hand aufrufen; ohne diese Prüfung zeigte eine frisch gebaute App weiter die alte Oberfläche, ohne dass irgendwo ein Fehler erschiene.

**Beenden.** Läuft gerade ein Scan, kommt vorher eine Rückfrage — über den Knopf als Dialog im Browser, bei `Cmd+Q` als Systemmeldung. Wird bestätigt, bricht der Scan ab; bereits gemessene Dateien bleiben in der Datenbank, der Rest wird beim nächsten Scan nachgeholt.

Damit `Cmd+Q` und *Beenden* im Dock überhaupt ankommen, läuft der Server in der gebauten App unter einer echten macOS-Anwendung (`app/macapp.py`, auf Basis von pyobjc); ohne die beantwortet der Prozess den Quit-Befehl des Systems gar nicht und macOS bietet nach kurzer Wartezeit nur noch „Sofort beenden" an — was den Server mitten in einem Schreibvorgang treffen könnte. Fehlt pyobjc, startet die App trotzdem; dann bleibt der Beenden-Knopf in der Oberfläche der einzige Weg.

**App-Symbol.** Das Symbol wird nicht in einem Grafikprogramm gepflegt, sondern aus `build_assets/make_icon.py` (Pillow) gezeichnet: drei Karteireiter über einer hellen Karte mit Wellenform, in den Farben der Oberfläche (`--dc-green`/`--dc-blue`/`--dc-red` aus `app.css`, fürs Symbol leicht aufgehellt). Ein Aufruf `./.venv/bin/python build_assets/make_icon.py` schreibt alles, was davon abhängt: `appicon.iconset` samt `appicon.icns` fürs Bundle, `docs/app-icon.png` für die README und die drei `data:`-URIs in `app/webui/index.html` (Favicon in 32 und 128 px, Symbol in der Kopfzeile). Bis 32 px wird eine vereinfachte Fassung mit vier statt sechs Balken gezeichnet — verkleinert man die feine Zeichnung, verschmelzen die Balken zu einer grauen Fläche. Für die Oberfläche entsteht eine zweite Ausprägung ohne den Rand, den macOS für Dock-Symbole verlangt: bei 16–34 px wäre er verschenkte Fläche. Nach einem Lauf `./run.command report` nicht vergessen, sonst zeigt `data/report.html` weiter das alte Symbol.

---

## Glossar

Technische Begriffe aus dem Code und der Datenhaltung.

### Spektralanalyse (Glossar)

- **Cutoff (`cutoff_hz`)** — die Frequenz, ab der ein Encoder-Tiefpass einsetzt; der zentrale Messwert des Tools. Berechnet als der Halbwertspunkt der stärksten gefundenen Abwärtskante (`spectral._find_edge`).
- **raw_cutoff_hz** — die Kantenposition *vor* dem `cliff_min_db`-Signifikanzfilter. `cutoff_hz` fällt auf Nyquist zurück, wenn die Kante zu schwach ist, um als echter Tiefpass zu gelten; `raw_cutoff_hz` tut das nie — deshalb greift Modus B (kein MP3-kalibrierter Schwellwert vorhanden) immer auf dieses Feld zu.
- **Flanke / Kante / Steepness (`steepness_db`)** — der Pegelunterschied in dB zwischen dem Frequenzband knapp unter und knapp über dem gefundenen Cutoff. Ein echter Encoder-Tiefpass ist eine fast senkrechte „Brickwall“-Flanke (30–60 dB bei MP3); ein natürlich auslaufendes Master oder eine 16-Bit-quantisierte verlustfreie Kante ist deutlich flacher.
- **Brickwall (`is_brickwall`)** — `steepness_db >= steep_brickwall_db` (Vorgabe 25 dB). MP3-kalibrierter Schwellwert; Modus B nutzt stattdessen die eigene, niedrigere `lossless_min_steepness_db` (siehe Klassifikation).
- **cliff_min_db** — Mindest-Kantenhöhe (Vorgabe 15 dB), unterhalb derer eine Kante gar nicht erst als real gilt und `cutoff_hz` auf Nyquist zurückfällt.
- **Perzentil (`block_percentile`, `gate_rms_percentile`)** — zwei verschiedene Verwendungen: (1) pro Frequenz-Bin über alle Zeitblöcke hinweg das `block_percentile` (Vorgabe 95.) als robuster „Peak-Hold“, der eine Tiefpasskante überleben lässt, während leise Momente herausgemittelt werden; (2) ein relatives RMS-Perzentil (`gate_rms_percentile`) als Teil des Lautheits-Gates zur Blockauswahl.
- **Lautheits-Gate** — der Blockauswahl-Schritt in `spectral.analyse`, der zu leise Blöcke verwirft (unter einem relativen RMS-Perzentil UND einem absoluten dBFS-Boden `gate_abs_dbfs`), bevor die Spektralanalyse läuft — leise Passagen hätten sonst von Natur aus keine Höhen und würden einen Tiefpass vortäuschen. Nicht zu verwechseln mit dem EBU-R128-„Gating“ in `loudness.py` (das ist internes ffmpeg/loudnorm-Verhalten, ein anderer Mechanismus).
- **Nyquist (`nyquist_hz`)** — `sample_rate / 2`, die theoretisch höchste darstellbare Frequenz bei gegebener Samplerate; obere Grenze der Spektralanalyse.
- **at_nyquist** — ob der gemessene Cutoff praktisch am Nyquist-Limit liegt (innerhalb 98,5 %) — Inhalt reicht bis an die theoretische Grenze, kein Tiefpass gefunden. Höchste Konfidenz für „echt voll bandbreitig“.
- **Xing/LAME-Header** — ein von LAME (und anderen Xing-kompatiblen Encodern) in MP3-Frames eingebettetes Metadatenfeld, das u. a. den vom Encoder tatsächlich angewandten Tiefpass festhält (`probe.read_lame_header`). Der Abgleich zwischen diesem deklarierten Wert und dem unabhängig *gemessenen* Cutoff ist der stärkste Einzelhinweis auf einen Transcode.
- **Payload-Bitrate** — Bitrate berechnet aus `(Dateigröße − ID3-Tag-Größe) / Dauer`, nur als Fallback wenn ffprobe keine Stream-Bitrate liefert; verhindert, dass großes eingebettetes Cover-Art-Bild die scheinbare Bitrate über die Container-Bitrate aufbläht.
- **ID3v2-Offset / Syncsafe-Integer** — ID3v2-Tags speichern ihre Größe als „syncsafe“ 4-Byte-Integer (7 nutzbare Bits pro Byte, oberstes Bit immer 0, damit kein MPEG-Frame-Sync-Muster in den Tag-Daten entsteht); `probe._id3_offset` dekodiert das, um den tatsächlichen Audio-Payload-Beginn zu finden.
- **Lavc/Lavf** — Präfixe von ffmpegs internen Bibliotheken (libavcodec/libavformat), die im `encoder`-Tag von per ffmpeg neu gemuxten/-kodierten Dateien auftauchen; in `classify.py` als Hinweis „diese Datei wurde irgendwann von ffmpeg angefasst“ genutzt.
- **Bitrate-Modus (CBR/VBR/ABR)** — Constant/Variable/Average Bitrate, MP3/LAME-spezifisches Konzept, per `mutagen` gelesen (`probe._mutagen_info`); hat kein Äquivalent für AAC/FLAC/WAV/AIFF, daher nur bei `lossy_mp3` befüllt.
- **AppleDouble-Dateien (`._`-Präfix)** — macOS-Finder-Resource-Fork-Begleitdateien, die beim Kopieren auf Nicht-HFS+-Dateisysteme entstehen; werden in `scanner.iter_files` explizit herausgefiltert.

### Klassifikation (Glossar)

- **codec_family** — einer von `lossy_mp3 | lossy_aac | lossless | lossy_other`, ausschließlich aus dem ffprobe-`codec_name` abgeleitet (nie aus der Dateiendung). Entscheidet, welcher Modus (A/B) und welche `cutoff_classes`-Leiter greift.
- **Modus A / Modus B** — die zwei Zweige in `classify.py`. Modus A (lossy: mp3/aac) vergleicht eine *gemessene* Bitrate-Klasse (aus dem Cutoff) gegen die *deklarierte* Klasse aus den Metadaten; die Differenz in Leiter-Stufen (`class_steps`) treibt OK/VERDÄCHTIG/FAKE. Modus B (`_classify_lossless`, verlustfreie Container) hat keine deklarierte Bitrate — das Verdikt basiert allein darauf, ob überhaupt eine harte Kante existiert und wie nah an Nyquist sie liegt.
- **cutoff_classes / Leiter** — `cfg["cutoff_classes"]` ist pro Codec-Familie eine absteigend nach `min_khz` sortierte Liste von `{min_khz, kbps}`-Stufen, die einen gemessenen Cutoff auf eine Bitrate-Klasse abbildet. MP3 und AAC haben je eigene Leitern.
- **class_steps** — wie viele Leiter-Stufen die gemessene Bitrate-Klasse unter der deklarierten liegt (`ladder.index(measured) - ladder.index(declared)`); diese Stufenzahl, nicht eine rohe kHz/kbps-Differenz, treibt die Schwellwerte `verdict_suspect_steps`/`verdict_fake_steps`.
- **lossless_min_steepness_db** — eigene, niedrigere Flankenschwelle für Modus B, weil 16-Bit-PCM nahezu stille Höhen oberhalb des Cutoffs grob quantisiert und die messbare Kante gegenüber MP3 deutlich verflacht (an echtem Material: ~13–16 dB statt >50 dB) — kein Bug, ein Quantisierungseffekt.
- **DJ/Club-Referenzband** — konfigurierbares LUFS-Band (Vorgabe −9 bis −6 LUFS), rein deskriptiver Vergleichswert für Gain-Staging (`loudness.club_hint`), ausdrücklich kein Qualitätsurteil — Genre und Erscheinungsjahr fließen nicht ein.

### Lautheit (Glossar)

- **LUFS / dBTP / LRA** — Integrated Loudness (Loudness Units Full Scale, ITU-R BS.1770), True Peak in dB (Intersample-Peak-Schätzung), Loudness Range (Dynamikmaß in LU). Alle drei stammen aus ffmpegs `loudnorm`-Filter im Ein-Pass-Analyse-Modus (`input_i`, `input_tp`, `input_lra`).
- **Intersample-Clipping** — Clipping, das zwischen digitalen Samples nach der DAC-Rekonstruktion entsteht, für einfaches Sample-Peak-Metering unsichtbar, aber durch True-Peak-Messung (dBTP) erfasst; die einzige Lautheits-bezogene Prüfung, die im Code als unbedingter technischer Fehler behandelt wird (`loudness.clip_risk`).

### Datenhaltung & Oberfläche (Glossar)

- **Cache-Key `(path, size, mtime)`** — die Identität für „bereits analysiert und unverändert“ in `db.py`/`jobs.py`: Größe muss exakt stimmen, `mtime` darf bis zu 1 Sekunde abweichen (toleriert Dateisystem-Rundungsunterschiede, z. B. bei Netzlaufwerken).
- **Korrigiert / corrected** — zu Ausgeblendet exklusiver DB-Zustand (Tabelle `corrected`, `POST /api/correct`); markiert einen Track, der über die Bitrate-Korrektur neu kodiert oder anderweitig manuell als behoben bestätigt wurde. Unabhängig von Merken — beide können gleichzeitig aktiv sein.
- **Merken / favorites** — bis zu 3 benutzerdefinierte, farbige Listen (Tabelle `favorites`, `POST /api/favorite`), unabhängig von Ausgeblendet/Korrigiert; ein Track kann in 0 bis 3 Listen gleichzeitig stehen. Ersetzt die frühere, einzelne „Erledigt“-Markierung. Von den Playlisten unterschieden: eine Merkliste ist eine reine Kennzeichnung an der Zeile (drei feste Slots, keine Reihenfolge, keine Verschachtelung), eine Playlist eine eigenständige, geordnete Liste im Seitenbaum.
- **Feste Listen / `system`-Flag** — die von TrackTab selbst mitgebrachten Knoten (`db._SYSTEM_PLAYLISTS`): der Ordner „Prüflisten" mit je einer Smart Playlist pro Status. `system: 1` sperrt Umbenennen, Verschieben und Löschen serverseitig und nimmt sie im Client als Drag-&-Drop-Ziel aus. Sie haben die früheren Verdikt-Schaltflächen über der Suche abgelöst.
- **Playlisten / playlists** — Ordner, reguläre Playlisten und Smart Playlists in einer Tabelle (`playlists`, unterschieden über `kind`), Zuordnungen mit manueller Reihenfolge in `playlist_items` (`pos`). Bearbeitet über `POST /api/playlist` und `POST /api/playlist-items`, gelesen über `GET /api/playlists`; **kein** `_rebuild()` — siehe [Playlisten und Seitenbaum](#playlisten-und-seitenbaum).
- **Smart Playlist** — Playlist ohne festen Inhalt: `playlists.rules` hält ein Regel-JSON (`{match, rules, limit}`), ausgewertet ausschließlich im Client über `DATA`. Neu berechnet beim Programmstart und beim Anklicken des Knotens, nicht laufend im Hintergrund.
- **Ersatzzeile / `ext`-Flag** — Zeile in `DATA` für einen Track aus einer Music.app- oder Rekordbox-Playlist, zu dem TrackTab keine Datenbankzeile hat (Cloud-Track ohne lokale Datei oder Datei außerhalb der gescannten Ordner). Trägt `ext: 1`, erscheint nur in der Fremdliste, zu der sie gehört, und ist aus `live()`/`counted()`/`filtered()` ausgeschlossen — sie darf weder Kennzahlen noch Suche noch Duplikaterkennung verfälschen.
- **Bitrate-Korrektur / rewrite** — `POST /api/rewrite` + `rewrite.rewrite_bitrate()`: kodiert eine lossy Datei in-place auf eine gewählte Ziel-Bitrate neu, das Original wandert vorher in den Papierkorb (nie Hard-Delete). Neben Papierkorb und Tags bearbeiten eine von drei Funktionen, die eine Audiodatei tatsächlich verändern.
- **Konverter / convert** — `POST /api/convert` + `convert.convert_format()`: wandelt Dateien nach MP3 320 kbit/s CBR (`-compression_level 0`, maximale LAME-Qualitätsstufe) oder AIFF (verlustfrei, Bittiefe der Quelle über `probe.source_bit_depth()` erhalten — AIFF ist Big-Endian-PCM, `pcm_s16be`/`pcm_s24be`, nicht `*le` wie WAV). Anders als `rewrite.py` ändert sich die Dateiendung, die neue Datei landet also unter einem kollisionsfrei ermittelten neuen Pfad statt am Platz des Originals. `convert.decide()` blockt Upscaling (verlustbehaftete Quelle → AIFF; eine Quelle mit weniger als 320 kbit deklariert → MP3 320) und Nichts-zu-gewinnen-Fälle (Quelle bereits im Zielformat) — dieselbe Tabelle läuft client-seitig als Vorfilter (`convertDecision()` in `app.js`) und serverseitig autoritativ. Nur bei der Kombination WAV→AIFF (beide ID3-Container) wird zusätzlich das komplette Tag-Objekt der Quelle übernommen, damit GEOB/PRIV (Serato-/Rekordbox-Cuepunkte) erhalten bleiben — bei jeder anderen Quelle/Ziel-Kombination existieren diese Frames im Quellcontainer gar nicht oder das Ziel ist ohnehin durch die Upscale-Regel blockiert. Sichtbar ab einem ausgewählten Track (1+), sowohl in der Haupttabelle als auch in der Einzelprüfung; dort ohne DB-/Music.app-/Rekordbox-Behandlung (keine DB-Zeile vorhanden).

  **Music.app: immer, ausser bei der Einzelpruefung.** Anders als jede andere Verknuepfung in diesem Endpunkt gilt hier NICHT "nur anfassen, was vorher schon dort war" -- jede erfolgreich konvertierte Haupttabellen-Zeile wird unconditional in Music.app aufgenommen (`media.add_to_music_library([new_path])`), unabhaengig davon, ob `path in db_mod.music_added_map(conn)` vor der Konvertierung wahr war. `remove_from_music_library()` laeuft nur als Optimierung, wenn dort tatsaechlich etwas zu entfernen ist (`trash_original and was_music_added`) -- sonst waere es ein wirkungsloser, aber trotzdem kostenpflichtiger Apple-Event-Leerlauf. Fuer die Einzelpruefung (kein DB-Row) gilt weiterhin die entgegengesetzte Regel: Music.app wird dort nie angefasst.

  **`trash_original`-Schalter im Popup** (Standard an) entscheidet zwischen zwei grundverschiedenen Ablaeufen, beide in `_post_convert()`: **an (ersetzen)** — Original in den Papierkorb, `db.move_path()` (alle `_PATH_TABLES`) + frische `db.save()`-Zeile am neuen Pfad, Waveform-/Cover-Cache dort invalidiert (stammt sonst noch vom alten Format/alten Audiodaten); war der alte Pfad in Music.app bekannt, wird er zusaetzlich zum Import dort entfernt (`remove_from_music_library()` — kein Ersetzen-Primitive vorhanden, Music.app-Playlist-Zugehörigkeiten gehen dabei bewusst verloren); war er in Rekordbox bekannt, korrigiert `rekordbox.update_relocated_tracks()` den Pfad direkt mit der bekannten alt/neu-Zuordnung (kein Fuzzy-Matching noetig, ein Formatwechsel ist hier deshalb — anders als beim automatischen Relink-Scan — kein Hindernis). **aus (behalten)** — `convert_format(..., trash_original=False)` ueberspringt `media.move_to_trash()` komplett, das Original bleibt unangetastet liegen; serverseitig **kein** `move_path()` (die alte Zeile bleibt exakt, wie sie war), stattdessen nur ein zusaetzliches `db.save()` fuer den neuen Pfad (echte Ergaenzung, kein Umzug) — kein Waveform-/Cover-Cache-Delete noetig, dort existiert am neuen Pfad ja noch gar kein Eintrag. Music.app-Import laeuft trotzdem (siehe oben, unconditional), nur eben ohne vorheriges `remove`. Rekordbox bleibt in diesem Fall bewusst unberuehrt (ohne echten Umzug gibt es dort nichts zu korrigieren, ein automatischer Zusatz-Import waere eine eigene, nicht angefragte Aktion). Client: `applyRowUpdate()`/`applyDropConvert()` (ersetzen) vs. `adoptLibraryRows()`/`addDropConvertResult()` (behalten, gleiches Prinzip wie nach einem Music.app-Import — neue Zeile mit frischem Index statt Ueberschreiben).
- **GEOB/PRIV-Tags** — ID3v2-Frame-Typen (General Encapsulated Object / Private Frame), über die Serato/Rekordbox Cue-Points und Analysedaten in MP3s einbetten. `rewrite.py` erhält sie, indem das komplette Tag-Objekt vom Original per `mutagen` übernommen statt feldweise neu aufgebaut wird; [`app/tags.py`](#apptagspy) folgt demselben Prinzip, laedt beim Bearbeiten ebenfalls das komplette ID3-Objekt und aendert nur die betroffenen Frames per `setall()`/`delall()`.
- **Tags bearbeiten** — `POST /api/tags` + `tags.write_tags()`: schreibt Titel/Interpret/Album/Albumkuenstler/Komponist/Genre/Jahr/BPM/Kommentar/Tracknummer/-gesamtzahl direkt in die Datei (kein Neukodieren, Pfad bleibt unveraendert) und per `db.update_tags()` in die DB. Cover getrennt ueber `GET/POST /api/cover` + `tags.read_cover()`/`write_cover()`, in der Tabelle als 60×60-Vorschaubild mit Klick-Großansicht. Dritte Funktion (nach Papierkorb und Bitrate-Korrektur), die eine Audiodatei tatsaechlich veraendert.
- **`genre`/`bpm`/`has_cover`/`album_artist`/`composer`/`year`/`comment`-Spalten** — wie `artist`/`title`/`album` analyzer-eigene `files`-Spalten, bei jedem Scan aus der Datei gelesen (`tags.read_extra()`) und daher Teil von `_COLUMNS`/`db.save()` statt einer eigenen Mutationsfunktion — sie sollen ja stets die aktuellen Datei-Tags widerspiegeln, ein Rescan darf sie also ueberschreiben.
- **`track_no`/`track_total`-Spalten** — ebenfalls bei jedem Scan aus dem Datei-Tag gelesen (`tags.read_extra()`, TRCK/`trkn`/`TRACKNUMBER`+`TRACKTOTAL` je nach Format); `track_no` zusaetzlich interne Sortiergrundlage für „nach Album gruppieren". Beide sind Teil von `_TAG_FIELDS` und im Tags-Dialog als „Track (Nr./von)" editierbar, aber (anders als Genre/Jahr/BPM/Kommentar) **keine** eigene, ein-/ausblendbare Tabellenspalte.
- **`file_hash`-Spalte / Duplikat-Erkennung** — analyzer-eigene `files`-Spalte wie `artist`/`title`, SHA-256 ueber den kompletten Dateiinhalt (`analyzer._hash_file()`, gestreamt in 1-MiB-Bloecken), bei jedem Scan neu berechnet. Wird im Report-Payload als `hs` an den Client geliefert; `app.js` clustert daraus zusammen mit normalisiert Interpret+Titel+Dauer (±1 s) client-seitig zu Duplikat-Gruppen (`computeDuplicateGroups()`, `r.dg`) fuer die Liste „Duplikate". Keine eigene DB-Tabelle, keine eigene Nutzer-Markierung — reine Filter-/Gruppierungslogik ueber vorhandene Zeilen.
- **Online-Metadaten-Lookup** — `POST /api/lookup` + `lookup.search()`: fragt iTunes Search API und Deezer immer ab (nur Deezer liefert teils BPM), MusicBrainz nur als Rueckfallebene wenn beide leer bleiben. Rein manuell angestossen (Button im Tags-Dialog), nichts wird serverseitig zwischengespeichert; ein Treffer fuellt beim Anklicken nur die Formularfelder, gespeichert wird erst durch den separaten "Speichern"-Klick. Bleibt die Suche ueber Interpret/Titel ergebnislos, kann ein manueller `query`-Suchbegriff denselben Endpunkt erneut mit einem selbst gewaehlten Text statt Interpret+Titel abfragen (Fallback-Feld im Dialog).
- **Waveform-Cache** — DB-Tabelle `waveform`, Schlüssel `(path, mtime)`, speichert vorberechnete Peak-Hüllkurven (`media.waveform_peaks()`), damit die Player-Waveform nicht bei jedem Abspielen neu berechnet werden muss (~2 s Kosten). `GET /api/waveform` liest diesen Cache durch.
- **`_known_file()` / `_reject_missing()`** — das serverseitige Sicherheitsgate: jeder Endpunkt, der einen echten Dateisystempfad anfasst, prüft zuerst, ob der Pfad als Zeile in der DB existiert UND die Datei noch auf der Platte liegt — die Regel „Datei-Endpunkte MÜSSEN Pfade gegen DB prüfen", konkret umgesetzt. Ergänzt um die Endungs- und Ordner-Allowlist, siehe [Zwei Riegel vor dem Dateizugriff](#zwei-riegel-vor-dem-dateizugriff).
- **`_same_origin()`** — das vorgelagerte Gate: `Host` und `Origin` jeder Anfrage müssen auf den eigenen Loopback zeigen, sonst 403. Schützt gegen CSRF und DNS-Rebinding, siehe [Herkunftsprüfung](#herkunftsprüfung-_same_origin).
- **Range-Request-Streaming** — die HTTP-`Range: bytes=...`-Unterstützung in `_get_audio()`, nötig damit der `<audio>`-Player im Browser innerhalb eines Tracks springen kann, ohne ihn komplett zu laden; unterstützt explizite (`start-end`) und Suffix-Ranges (`-N`, „letzte N Bytes“), liefert 206/416 nach HTTP-Spezifikation.
- **App-Icon-Symbol** — echte macOS-App-Icons (kein Nachbau) für die Finder/RX/Music-Knöpfe, zur Laufzeit aus dem `.icns` der Ziel-App extrahiert (`Info.plist` → `sips`-PNG-Konvertierung, `media.app_icon_png`), ausgeliefert über `GET /api/appicon`.
- **iTunes-Store-Deeplink (`itmss://`)** — eigenes URL-Schema, um Music.app direkt im **Store**-Tab bei einem bestimmten Track zu öffnen (statt Apple-Music-Streaming). Gebaut durch Umschreiben der `trackViewUrl` aus Apples Such-API von `https://` auf `itmss://` plus `app=itunes`-Anhang, um die Apple-Music-Zwischenseite zu überspringen.
- **Shops (benutzerdefinierte Such-Links)** — konfigurierbare Liste zusätzlicher externer Suchdienste über die eingebauten Beatport/SoundCloud/iTunes-Knöpfe hinaus, gespeichert unter dem Top-Level-Konfigurationsschlüssel `shops`, bearbeitet über `POST /api/shops`; jeder Eintrag braucht einen `{q}`-Platzhalter in der URL-Vorlage.
- **Auf Vorgabe / Reset** — Pro-Gruppe-„Zurücksetzen“-Aktion (`POST /api/settings/reset`, `settings.reset()`), entfernt nur die Schlüssel dieser Gruppe aus `config.local.yaml` und fällt auf `config.yaml`/Code-Defaults zurück — im Gegensatz zum kompletten Löschen der lokalen Override-Datei.
- **Datei fehlt / „gone“-Flag** — Report-Payload-Feld `gone` (1/0): eine Zeile, deren DB-Eintrag noch existiert, deren Datei aber beim Report-Bau bzw. API-Aufruf nicht mehr auf der Platte gefunden wird (`os.path.isfile`-Prüfung). Anders als der Papierkorb (löscht den DB-Eintrag sofort mit) — tritt auf, wenn eine Datei außerhalb des Tools gelöscht wurde.
- **aac_encoder-Fallback** — Einstellungsfeld für einen bevorzugten ffmpeg-AAC-Encoder (z. B. `libfdk_aac` für bessere Qualität); `media.resolved_aac_encoder()` fällt still auf den immer verfügbaren nativen `aac`-Encoder zurück, falls der konfigurierte nicht im lokalen ffmpeg-Build enthalten ist.
- **Backup** — `backup.py`: automatisches Backup von `quality.db` per SQLite `VACUUM INTO`, abgelegt in `backup/` mit Zeitstempel im Dateinamen, älteste über `backup_keep` (Vorgabe 10) hinaus werden gelöscht. Zwei Auslöser: `maybe_auto_backup()` beim Start jedes CLI-Befehls (`cli.main()`), höchstens eins pro Kalendertag; `backup_on_close()` beim Beenden von `server.serve()` (Strg+C, „Beenden" in der Oberfläche, Cmd+Q/Dock/`/api/quit`), **ohne** Tages-Begrenzung — jedes Schließen sichert den aktuellen Stand. Manuelle Backups (Einstellungen → Backup, oder `./run.command backup create`) landen an einem frei wählbaren Ort und zählen nicht zur Rotation. Wiederherstellen sichert den bisherigen Stand vorher selbst weg (auch wenn dieser beschädigt ist — dann als rohe Dateikopie statt `VACUUM INTO`) und validiert die einzuspielende Datei vorab mit einer einfachen Testabfrage.
- **Änderungsprotokoll** — `audit_log.py`: protokolliert, WAS an Dateien geändert wurde (Tags, Cover, Neukodierung, Konvertierung, Papierkorb, Verschiebungen/Relink, Rekordbox-Import, Music-Import) — nicht Bedienschritte in der Oberfläche. Eine Log-Datei pro Kalendertag unter `logs/` (Einstellung `logs_path`), Format JSON Lines (`logs/YYYY-MM-DD.json`, ein JSON-Objekt pro Zeile: `{"time", "action", "path"}`, optional `"detail"`). `audit_log.log()` wird direkt nach jeder erfolgreichen Dateiänderung in `server.py` aufgerufen und schluckt eigene Fehler (ein nicht schreibbarer Log-Ordner darf die bereits abgeschlossene Aktion nicht nachträglich scheitern lassen). Log-Dateien werden nicht automatisch gelöscht. „Log-Ordner öffnen" in Einstellungen öffnet `logs/` im Finder (`POST /api/log-folder`), analog zum Backup-Ordner-Knopf.
- **`events`-Tabelle** — rohes Ereignisprotokoll in `quality.db` (`id, ts, kind, path, duration_s, in_music`), Gegenstück zum Änderungsprotokoll für alles, was **nicht** über `audit_log.py` läuft: aktuell qualifizierende Wiedergaben (`kind="play"`, ≥ 30 s tatsächliche Hörzeit, gemeldet über `POST /api/play`), Music.app-Neuzugänge (`kind="track_added"`) und Konvertierungen (`kind="converted"`). `kind` ist bewusst ein freies Textfeld statt einer Tabelle je Ereignisart. Einzige Quelle für Wiedergabezeit/Top-Tracks in der [Statistik](#statistik--interna) — im Änderungsprotokoll taucht Wiedergabe nirgends auf.
- **Toast** — `toast()` in `app.js`: kurze, nach ~4 Sekunden von selbst verschwindende Meldung unten am Bildschirmrand (`#toasts`), für Erfolgsbestätigungen zu abgeschlossenen Aktionen (Music-Import, Tags gespeichert, Neukodierung, Rekordbox-Import). Ergänzt die dauerhafte, aber leicht zu übersehende `#syncnote`-Zeile (`note()`) — beide werden an denselben Stellen aufgerufen, `toast()` bewusst nur bei selteneren, bedeutsamen Aktionen und nicht bei den häufigen Ausblenden/Korrigiert-Klicks (deren sofortige visuelle Rückmeldung schon die Zeilenänderung selbst ist) — Merken-Klicks sind eine bewusste Ausnahme und lösen trotzdem einen Toast aus, da eine Zeile dabei mehrere unabhängige Zustände tragen kann und die reine Häkchen-Änderung leichter zu übersehen ist.

---

## Modulreferenz

Funktion-für-Funktion-Referenz jedes Python-Moduls, für Weiterentwicklung. Reihenfolge folgt der Analysekette: `scanner.iter_files` → `jobs.run_scan` → `analyzer.analyse_file` (Worker-Prozess, ruft `probe`/`tags`/`spectral`/`classify`/`loudness` auf) → `db.save`. Danach die UI-/Server-Schicht.

### Analyse-Pipeline

#### `app/probe.py`

ffprobe-Metadaten-Wrapper + roher Xing/LAME-Header-Parser + Codec-Familien-Klassifikator.

| Funktion/Klasse | Zweck |
|---|---|
| `classify_codec_family(codec_name)` | Bildet ffprobes `codec_name` auf `lossy_mp3\|lossy_aac\|lossless\|lossy_other` ab. `"aac"` → lossy_aac; `"alac"`/`"flac"`/`pcm_*` → lossless; `"mp3"` → lossy_mp3; sonst lossy_other. Case-insensitiv, leer-sicher. |
| `ProbeResult` (dataclass) | Felder: `path, ok, error, duration_s, declared_kbps, sample_rate, channels, codec, codec_family, bitrate_mode, encoder, lame_lowpass_hz, artist, title, album, tags` |
| `_ffprobe(path)` | Ruft `ffprobe -show_entries format=...:stream=...` als JSON auf; 60-s-Timeout; wirft `RuntimeError` bei Fehler |
| `_id3_offset(fh)` | Länge eines vorangestellten ID3v2-Tags über den syncsafe-4-Byte-Größenwert |
| `read_lame_header(path)` | Sucht `Xing`/`Info`-Magic-Bytes, läuft die optionale Flag-Bitmaske ab, liefert `(encoder_string, lowpass_hz)`; Lowpass wird auf 1000–24000 Hz plausibilisiert, sonst 0 |
| `_payload_bitrate(path, duration_s)` | Fallback-Bitrate aus `(Dateigröße − ID3-Offset) × 8 / Dauer` |
| `_mutagen_info(path, res)` | Nur für `lossy_mp3`: befüllt `bitrate_mode`/Fallback-`encoder` über `mutagen`; No-op bei fehlendem mutagen oder Parse-Fehler |
| `probe(path)` | Orchestriert alles Obige zu einem vollständigen `ProbeResult`; wählt den Audio-Stream explizit (überspringt eingebettetes Cover-Art als mjpeg/png-Stream); `ok = duration_s > 0 and sample_rate > 0` |
| `source_bit_depth(path)` | Für Konverter-Ziel AIFF: liefert 16 oder 24. Zuerst `bits_per_raw_sample`/`bits_per_sample` aus ffprobe (vertrauenswürdigste Quelle für FLAC/ALAC/PCM), sonst aus dem PCM-Codec-Namen abgeleitet (`pcm_s24le` → 24 usw.); alles > 16 wird auf 24 abgebildet, unbestimmbar fällt konservativ auf 16 zurück |

**Gotcha:** muss synchron pro Datei laufen (subprocess-basiert), das Rückgabeobjekt ist ein reines, picklebares Dataclass — Voraussetzung für den Einsatz im `ProcessPoolExecutor`.

#### `app/spectral.py`

Dekodiert Audio und findet die stärkste Abwärtskante im Spektrum.

| Funktion/Klasse | Zweck |
|---|---|
| `SpectralResult` (dataclass) | Felder: `ok, error, cutoff_hz, raw_cutoff_hz, steepness_db, is_brickwall, nyquist_hz, at_nyquist, ref_level_db, noise_floor_db, threshold_db, gated_blocks, total_blocks, spectrum` |
| `_downmix_filter(channels)` | Baut einen expliziten ffmpeg-`pan`-Filter (Gewichtung `1/channels`) statt `-ac 1` — mindestens ein ffmpeg-Build downmixt PCM-Quellen (WAV/AIFF/FLAC) über `-ac 1` grob falsch (bis 0,4 Amplitudenabweichung), was eine echte Tiefpasskante verdecken könnte; MP3 ist davon nicht betroffen |
| `decode_mono(path, sample_rate, max_seconds, channels=2)` | Dekodiert via ffmpeg nach Mono in **nativer Samplerate** (kein Resampling — vermeidet Artefakte an der Bandgrenze); 300-s-Timeout |
| `_smooth(spec_db, bin_hz, width_hz)` | Boxcar-Glättung des dB-Spektrums |
| `_window_means(spec_db, n)` | Mittelwert der `n` Bins unter/über jedem Bin, per Cumsum-Trick in O(n) |
| `_find_edge(freqs, smooth_db, nyquist, cfg)` | Findet die stärkste Abwärtskante im oberen Spektrum als `lo_avg − hi_avg`; liefert `(cutoff_hz, drop_db, half_level_db)` |
| `analyse(path, sample_rate, duration_s, cfg, channels=2)` | Volle Pipeline: dekodieren → kappen (`max_analysis_s`) → Anfang/Ende trimmen (`skip_head_pct`/`skip_tail_pct`) → in Blöcke (`fft_size`) zerlegen → Lautheits-Gate → Hann-gefenstertes FFT-Leistungsspektrum je Block → `block_percentile` über alle Blöcke → glätten → `_find_edge` → Verdikt-Flags (`is_brickwall`, `at_nyquist`) setzen |
| `_downsample_spectrum(freqs, spec_db, points)` | Bucket-Max-Downsampling für die kompakte Spektrum-Vorschau im Report |

**Gotcha:** subprocess- und numpy-lastig, für den Einsatz im `ProcessPoolExecutor` gedacht.

#### `app/loudness.py`

EBU R128 / ITU-R BS.1770-Lautheitsmessung über ffmpegs `loudnorm`-Filter, Ein-Pass-Analyse. **Kein Verdikt** außer der Clip-Warnung.

| Funktion/Klasse | Zweck |
|---|---|
| `LoudnessResult` (dataclass) | Felder: `ok, error, integrated_lufs, true_peak_dbtp, lra_lu` |
| `analyse(path, duration_s, cfg)` | Führt `ffmpeg -af loudnorm=...:print_format=json -f null -` aus (die Ziel-Parameter von loudnorm sind irrelevant, nur die gemessenen `input_*`-Felder aus dem stderr-JSON werden gelesen); validiert alle drei Werte auf Endlichkeit (Schutz vor Stille → NaN/-inf); 300-s-Timeout |
| `clip_risk(true_peak_dbtp, cfg)` | `true_peak_dbtp > cfg["loudness_clip_dbtp"]` — die einzige absolute Schwellwert-Prüfung in diesem Modul |
| `club_hint(integrated_lufs, cfg)` | Weiche Kategorie-Zeichenkette relativ zum DJ/Club-Referenzband |

#### `app/classify.py`

Bildet aus Probe- und Spektral-Ergebnis das Verdikt (Modus A/B) sowie die reine DB-Neubewertung.

| Konstante/Funktion/Klasse | Zweck |
|---|---|
| `VERDICT_OK/SUSPECT/FAKE/UNKNOWN`, `VERDICT_ORDER` | Verdikt-Strings und Sortier-Rangfolge (FAKE < VERDÄCHTIG < UNKLAR < OK) |
| `Verdict` (dataclass) | Felder: `verdict, measured_kbps, declared_class, class_steps, confidence, reasons` |
| `_FAMILY_LADDER` | Bildet `codec_family` auf die zu nutzende `cutoff_classes`-Leiter ab (`lossy_other` nutzt konservativ die MP3-Leiter) |
| `_class_ladder(cfg, ladder="mp3")` | Liefert die kbps-Liste der benannten Leiter |
| `measured_class(cutoff_hz, cfg, ladder="mp3")` | Bildet eine Cutoff-Frequenz auf eine Bitrate-Klasse ab |
| `declared_class(declared_kbps, cfg, ladder="mp3")` | Rundet die deklarierte Bitrate **nach unten** auf die nächste Leiter-Stufe (schützt legitime VBR-Dateien) |
| `classify(probe_res, spec_res, cfg)` | Modus-A-Einstiegspunkt (delegiert an `_classify_lossless` bei `codec_family == "lossless"`). Berechnet `measured_kbps`/`declared_class`/`class_steps`, wendet Verdikt-Schwellwerte an, prüft LAME-Gegenprobe (Diskrepanz zwischen `lame_lowpass_hz` und gemessenem Cutoff), erkennt „mit ffmpeg kodiert“ über `Lavc/Lavf`-Encoder-Präfix, berechnet `confidence` |
| `_classify_lossless(probe_res, spec_res, cfg)` | Modus B: nutzt `raw_cutoff_hz` (nicht `cutoff_hz`), `has_edge = steepness_db >= lossless_min_steepness_db`; ohne Kante → OK, mit Kante → Vergleich gegen `lossless_fake_khz`/`lossless_suspect_khz`; `approx_kbps` ist rein deskriptiv, nie Verdikt-Basis |
| `_confidence(spec_res, v, lame_mismatch, cfg, has_edge=None)` | Heuristischer Score `[0.05, 1.0]`, Basis 0,45; Zu-/Abschläge nach Blockanzahl, Kantenvorhandensein, LAME-Widerspruch, Stufen-Abstand, `at_nyquist` |
| `from_row(row, cfg)` | Baut aus einer DB-Zeile `(probe, spec)`-Duck-Typing-Objekte (`SimpleNamespace`) ohne Neu-Dekodierung; setzt Defaults für Felder, die in alten Zeilen fehlen (`codec_family` → `lossy_mp3`, `raw_cutoff_hz` → `cutoff_hz`); rechnet `is_brickwall` frisch aus dem aktuellen `steep_brickwall_db` |
| `reclassify_all(conn, cfg)` | Lädt alle `files`-Zeilen, klassifiziert neu, schreibt per `executemany` zurück; reine DB-Operation, kein Datei-/Audiozugriff — deshalb unter einer Sekunde für tausende Dateien |

**Gotcha:** jedes neue Feld in `classify()` muss auch in `from_row()` einen sinnvollen Default bekommen, sonst bricht die Neubewertung alter Zeilen still.

#### `app/taganomaly.py`

Erkennung von Metadaten-Auffälligkeiten (Tag-Qualität) — eigenständig neben `classify.py`, das ausschließlich Audioqualität bewertet. `mutagen` + `Pillow` (Cover-Prüfung/-Verkleinerung), kein `ffmpeg`, deshalb deutlich billiger als eine Neuanalyse.

| Funktion | Zweck |
|---|---|
| `AUTO_FIXABLE` / `MANUAL_ONLY` | Zwei Mengen von Codes — welche `fix_safe()` selbst schreibt und welche nur angezeigt werden (siehe Tabelle im [Handbuch](handbuch.md#auffälligkeiten-metadaten-probleme)) |
| `detect(path, codec_family="")` | Alle Funde einer Datei als `[{code, field, value, suggestion}, …]`. Best effort wie `tags.read_extra()` — `[]` bei jedem Lesefehler statt Abbruch. Routing nach Endung wie `tags.py` (eigene, dortselbst NICHT wiederverwendete Konstanten, um nicht auf private Unterstrich-Namen eines fremden Moduls zuzugreifen), eigene `_detect_id3/_detect_mp4/_detect_flac`-Helfer mit rohem Container-Zugriff — ID3-Version, Mehrfach-Frames und Genre-Code brauchen die Rohdaten, nicht die bereits verstringten Werte aus `tags.read_extra()` |
| `_check_string(field, value)` | Steuerzeichen/Leerraum/Mojibake/NFC für EIN Feld, containerformat-unabhängig |
| `_is_id3v1_only(path)` | Kein ID3v2-Header UND letzte 128 Bytes beginnen mit `b"TAG"` (die öffentliche ID3v1-Signatur — mutagen bietet dafür keine eigene Klasse) |
| `fix_safe(path)` | Behebt alle `AUTO_FIXABLE`-Funde, liefert `(behobene Codes, geschriebene Feldwerte)`. Text-/Frame-Probleme laufen gebündelt über einen einzigen `tags.write_tags()`-Aufruf — nutzt aus, dass `write_tags()` beim Speichern ohnehin jedes ID3v1/v2.2 auf v2.3 anhebt (`_id3_tags()` in `tags.py`) und `setall()` vorhandene Mehrfach-Frames überschreibt, eine gezielte „nur Code X reparieren"-Logik ist deshalb unnötig. `OVERSIZED_COVER` läuft separat sofort über `tags.write_cover()` (eigener Schreibweg, siehe `_resize_cover()`) |
| `_resize_cover(data)` | Verkleinert ein Cover auf `_COVER_MAX_SIDE` (500px, längere Kante, Seitenverhältnis bleibt erhalten) und speichert es als JPEG (`_COVER_JPEG_QUALITY`, 85) neu — deckt beide Ursachen von `OVERSIZED_COVER` ab (zu große Abmessungen UND ein ineffizientes Ausgangsformat wie unkomprimiertes PNG, da das JPEG-Resave immer greift, auch wenn `thumbnail()` bei einem bereits kleinen Bild nichts tut). `None` bei nicht lesbaren Bilddaten (das wäre eigentlich `CORRUPT_COVER`) |
| `recheck_all(conn, cfg)` | Liest bei jeder bekannten Datei die Tags frisch und aktualisiert nur `tag_issues` — kein `ProcessPoolExecutor` (I/O-gebunden, kein `ffmpeg`-Decode, ca. 1 ms/Datei) |

**Gotcha:** `duplicate_frames` schließt COMM-Frames aus (ID3 erlaubt dort mehrere, über Sprache+Beschreibung unterschiedene Frames — an echtem Material bestätigt, sonst 6.110 von 10.822 Dateien fälschlich gemeldet) und meldet bei Vorbis-Mehrfachwerten (FLAC erlaubt das absichtlich, z. B. mehrere `ARTIST`) nur bei tatsächlich abweichenden Werten, nicht bei reiner Mehrfachnennung.

#### `app/analyzer.py`

Klebstoff, der Probe → Spektral → Klassifikation → Lautheit für eine Datei ausführt und zu einer flachen DB-Zeile zusammenfasst. **Muss modul-level und picklebar bleiben** — läuft direkt im `ProcessPoolExecutor`.

| Funktion | Zweck |
|---|---|
| `analyse_file(job)` | `job = (path, size, mtime, cfg)`. Baut eine vorbefüllte `row` mit sicheren Defaults, damit auch ein Teilfehler eine vollständige Zeile zurückgibt. Ablauf: Probe (Fehler → sofortiger Rückgabewert) → `tags.read_extra()` für `genre`/`bpm`/`has_cover` → `taganomaly.detect()` für `tag_issues` (läuft auch bei fehlgeschlagener Probe noch mit, solange die Tags selbst lesbar sind — deshalb VOR dem `pr.ok`-Check) → Kurz-Check gegen `min_duration_s` (zu kurz → UNKLAR ohne Spektral-/Lautheitsanalyse) → Spektralanalyse → Klassifikation → **Lautheitsanalyse in eigenem try/except**, damit ein Lautheits-Fehler das bereits berechnete Verdikt nie entwertet |

**Gotcha:** läuft in Worker-Prozessen — kein geteilter Zustand (DB-Verbindungen, Locks); alles Nötige kommt über das `job`-Tupel, der Rückgabewert ist ein reines Dict aus Primitiven/Listen. Da `analyse_file()` der einzige gemeinsame Weg für Scan, „Track neu analysieren", Einzelprüfungen und die Neuanalyse nach einer Bitrate-Korrektur ist, deckt dieser eine Aufruf von `taganomaly.detect()` automatisch alle vier Fälle ab — keine separate Verdrahtung nötig.

#### `app/scanner.py`

| Funktion | Zweck |
|---|---|
| `iter_files(cfg, roots=None)` | Generator über `(path, size, mtime)` für passende Audiodateien. Nutzt `os.walk(followlinks=False)`, kürzt `dirnames` gegen `exclude_dirs`/versteckte Ordner, filtert AppleDouble-Dateien, dedupliziert per absolutem Pfad, überspringt Dateien mit fehlgeschlagenem `os.stat` |
| `count_files(cfg, roots=None)` | `sum(1 for _ in iter_files(...))` — vollständiger Durchlauf nur zum Zählen, nicht günstig für sehr große Bibliotheken |

#### `app/jobs.py`

Der eigentliche Scan-Lauf, gemeinsam genutzt von CLI und Web-Server; für den Server in einen abbrechbaren Hintergrund-Thread gekapselt.

| Funktion/Klasse | Zweck |
|---|---|
| `run_scan(cfg, paths=None, force=False, limit=0, prune=False, on_progress=None, should_cancel=None)` | Inkrementeller Bibliotheks-Scan. `scanner.iter_files` → Cache-Filter (`db.cached_keys`/`db.is_current`, außer bei `force`) → optionales `limit` → alle Jobs auf einmal an `ProcessPoolExecutor(max_workers=worker_count())`, ausgewertet über `as_completed` (nicht `pool.map`) für Fortschritt pro Track und sofortige Abbrechbarkeit. Jede 200 fertige Analysen (`_BATCH`) wird ein Zwischenstand per `db.save` geschrieben. Worker-Ausnahmen werden pro Future zu einer Fehlerzeile statt den ganzen Scan abzubrechen. `prune` (nur ohne `limit`/`paths`, nicht bei Abbruch) entfernt DB-Zeilen zu nicht mehr vorhandenen Dateien |
| `ScanJob` | Abstraktion für einen hintergrundlaufenden Scan, vom Server per Polling abgefragt. `is_running()`, `start(cfg, **kwargs)` (No-op wenn schon aktiv), `_run()` (Thread-Ziel, aktualisiert `self.state` per Progress-Closure), `cancel()` (kooperativer Abbruch über `should_cancel`) |

#### `app/calibrate.py`

Regressionstest-Werkzeug — Ersatz für eine Unit-Test-Suite, Pflicht nach Änderungen an `spectral.py`/`probe.py`/`classify.py`. Erzeugt bekannt-schlechte Dateien aus bekannt-guten Bibliotheks-Tracks und prüft, ob die Verdikt-Maschinerie sie korrekt erkennt.

| Funktion | Zweck |
|---|---|
| `_STAGES = [128, 192, 256]` / `_STAGES_AAC = [96, 128, 192]` | Downcode-Stufen vor dem Wieder-Hochkodieren auf 320 bzw. 256 |
| `_encode(src, dst, kbps, codec="libmp3lame")` | Re-Encode auf feste Ziel-Bitrate |
| `_wrap_lossless(src, dst, codec)` | Verlustfreies Neu-Muxen/-Kodieren in einen anderen Container (z. B. FLAC, WAV) ohne Bitrate-Ziel — simuliert „lossy Quelle als FLAC/WAV exportiert“ für Modus-B-Tests |
| `_pick_sources(cfg, count)` | Wählt „saubere“ Referenz-Tracks aus der DB (`status='ok' AND verdict=OK AND declared_kbps>=315 AND cutoff_hz>=20000 AND duration_s BETWEEN 120 AND 420`) als Ground Truth |
| `_run_lossy_chain(console, sources, cfg, work, keep, *, label, stages, final_kbps, codec, ext)` | Pro Quelle: Original direkt analysieren (Baseline), dann je Stufe herunter- und wieder hochkodieren, `measured_kbps == stage` UND `verdict in (FAKE, SUSPECT)` prüfen |
| `_run_lossless_wrap(console, sources, cfg, work, keep)` | Pro Quelle: auf 128 kbps herunterkodieren, dann in FLAC und WAV verpacken; prüft `codec_family == lossless` UND geflaggt |
| `_summarize(console, label, hits, total)` | Farbige Trefferquoten-Zeile (grün ≥90 %, gelb ≥70 %, rot darunter) |
| `run(console, args)` | CLI-Einstiegspunkt: lädt Quellen (explizit oder automatisch), fährt MP3-Kette, dann AAC-Kette (`media.resolved_aac_encoder`), dann Lossless-Wrap-Test. **Exit-Code hängt nur an der MP3-Trefferquote (≥70 % → 0)** — AAC/Lossless-Ergebnisse sind rein informativ, da ihre Schwellwerte noch nicht an echtem Material kalibriert sind |

#### `app/db.py`

SQLite-Persistenzschicht: Schema, Migrationen, Cache-Lookups, Speichern, sowie die drei sich gegenseitig ausschließenden Nutzer-Entscheidungstabellen.

| Funktion | Zweck |
|---|---|
| `_SCHEMA`, `_COLUMNS`, `_MIGRATIONS`, `_ensure_columns(conn)` | Schema-Definition (`files` mit 45 Spalten + Indizes; `ignored`/`favorites`/`corrected`/`waveform`/`rekordbox`/`music_added`/`cover_cache`/`events`/`playlists`/`playlist_items` als separate Tabellen); `_ensure_columns` fährt `ALTER TABLE ADD COLUMN` für alles in `_MIGRATIONS`, was auf einer bestehenden DB noch fehlt — läuft über `_setup_once()` einmal je Datenbankdatei und Prozess (siehe [Verbindungsaufbau](#verbindungsaufbau-setup_once--wal)). `genre`/`bpm`/`has_cover`/`album_artist`/`composer`/`year`/`comment`/`track_no`/`track_total` sind Teil von `_COLUMNS` (analyzer-eigen), ebenso `tag_issues` (JSON-Liste, siehe `app/taganomaly.py`) |
| `update_tag_issues(conn, path, issues)` | Gezieltes Update von `tag_issues` nach einem Auffälligkeiten-Fix oder `recheck_all()` — kein voller Analyse-Datensatz nötig, analog zu `update_tags()` |
| `connect(cfg=None, path=None)` | Öffnet die Verbindung (`row_factory = sqlite3.Row`, WAL + `synchronous=NORMAL` + `busy_timeout`); Schema/Migrationen und die einmalige Übernahme einer alten `solved`-Tabelle nach `favorites`/`list1` laufen über `_setup_once()` **einmal je Datenbankdatei und Prozess**, nicht bei jedem Aufruf (siehe [Verbindungsaufbau](#verbindungsaufbau-setup_once--wal)); erzeugt DB-Datei/Verzeichnisse bei Bedarf |
| `_setup_once(conn, db_path)`, `forget_setup(path=None)` | Die einmalige Einrichtung und ihr Vergessen — Pflicht, wenn die Datei unter uns ausgetauscht wird (`backup.restore_backup()`), weil der eingespielte Stand ein älteres Schema haben kann |
| `cached_keys(conn)` | `{path: (size, mtime)}` aller `status='ok'`-Zeilen — die Basis des inkrementellen Scans |
| `is_current(cache, path, size, mtime)` | Cache-Treffer-Test (siehe Cache-Key im Glossar) |
| `save(conn, rows)` | Batch-Upsert (`INSERT OR REPLACE`), stempelt `analyzed_at`, JSON-serialisiert `reasons`/`spectrum` |
| `update_tags(conn, path, fields)` | Gezieltes `UPDATE` einzelner Tag-Felder (`_TAG_FIELDS`: `artist`/`title`/`album`/`album_artist`/`composer`/`genre`/`year`/`bpm`/`comment`) nach einem Tags-bearbeiten-Speichern — kein voller Analyse-Datensatz nötig |
| `set_has_cover(conn, path, flag)` | Setzt das `has_cover`-Flag nach einem Cover-Upload, ohne die anderen Analyzer-Spalten anzufassen |
| `paths_by_genre(conn, genre)` / `paths_by_artist(conn, artist)` | Alle Pfade mit exakt diesem Wert (fallsensitiv, wie `rekordbox._get_or_create_genre()`/`_get_or_create_artist()`) — Grundlage für `/api/genre-rename`/`/api/artist-rename` |
| `paths_by_album(conn, album, group_artist)` | Wie oben, zusätzlich eingegrenzt auf den Gruppen-Künstler (Album-Künstler, ersatzweise Künstler — dieselbe Regel wie `albumGroupKeyExact()` in app.js), damit zwei gleichnamige Alben verschiedener Künstler nie zusammenfallen |
| `merge_dismissed_pairs(conn, field)` / `set_merge_dismissed(conn, field, a, b, flag)` | Dauerhaft ausgeblendete Zusammenführungs-Vorschläge je Feld (`merge_dismissed`-Tabelle, `value_a`/`value_b` beim Schreiben kanonisch sortiert, damit ein Paar unabhängig von der Reihenfolge dieselbe Zeile trifft) |
| `move_path(conn, old_path, new_path)` | Schreibt einen Datensatz auf einen neuen Pfad um, nachdem Music.app die Datei beim Import in seinen Medienordner verschoben hat. Trifft **alle** Tabellen aus `_PATH_TABLES` (`files`, `ignored`, `favorites`, `corrected`, `waveform`, `rekordbox`, `music_added`, `cover_cache`, `playlist_items`) — eine neue pfadbasierte Tabelle gehört dort eingetragen, sonst bleibt sie beim Umzug zurück. `favorites` und `playlist_items` können mehrere Zeilen je Pfad haben (eine je `list_id` bzw. je Playlist), `UPDATE ... WHERE path = ?` verschiebt sie alle in einem Schritt; anders als bei `events` geht dort auch das Delete-vor-Update auf, weil der Pfad nur die eine Hälfte des Schlüssels ist. Je Tabelle nur, wenn dort eine Zeile am alten Pfad hängt (sonst würde eine fremde Zeile am Zielpfad gelöscht); liest `size`/`mtime` frisch von der Datei nach, weil Music.app beim Kopieren die mtime ändert und der Cache-Schlüssel sie sonst neu vermessen liesse. Verschiebt **nie** eine Datei. Liefert `True`, wenn es eine `files`-Zeile am alten Pfad gab |
| `refresh_stat(conn, path)` | Zieht `size`/`mtime` aus der Datei nach, nachdem wir sie selbst geschrieben haben (Tags/Cover). Nötig, weil `update_tags()` sie bewusst nicht anfasst: sonst gilt die Datei beim nächsten Scan als verändert und wird ohne Not neu gemessen, und die Größe taugt nicht mehr als Merkmal für `scanner.find_moved()` |
| `prune_missing(conn, existing)` | Löscht `files`-Zeilen zu nicht mehr vorhandenen Pfaden und räumt die reinen Caches an denselben Pfaden mit ab (`_PRUNE_TABLES`: `files`, `waveform`, `rekordbox`, `music_added`) — ohne `files`-Zeile sind das unerreichbare Reste, und sie lassen sich jederzeit neu berechnen. Die Nutzer-Entscheidungen (`ignored`/`favorites`/`corrected`) bleiben **bewusst** stehen: sie liessen sich nicht wiederherstellen, und `scan --prune` trifft auch Dateien, die nur gerade nicht erreichbar sind (nicht eingehängtes Laufwerk) — kommt die Datei zurück, gilt die Markierung wieder. Liefert die entfernten **Pfade** (nicht die Anzahl); `jobs.run_scan()` nimmt davon nur `len()` |
| `ignored_paths`/`set_ignored`, `corrected_paths`/`set_corrected` | Lesen/Schreiben der beiden exklusiven Entscheidungstabellen; jedes `set_*(flag=True)` räumt passende Zeilen aus der jeweils anderen Tabelle (Exklusivität wird beim Schreiben erzwungen, nicht per Constraint) |
| `favorites_map(conn)`/`set_favorite(conn, path, list_id, flag)` | Lesen/Schreiben der Merklisten (`FAVORITE_LIST_IDS = ("list1","list2","list3")`). `favorites_map()` liefert `{list_id: {pfad, ...}}` für alle drei Slots in einer Abfrage; `set_favorite()` rührt `ignored`/`corrected` nie an — Merken ist unabhängig davon, ein Track kann in mehreren Listen gleichzeitig stehen |
| `playlists_all(conn)`/`playlist_items_map(conn)`/`playlist_by_id(...)` | Lesen des Baums: flache Knotenliste nach `seq`/Name, Zuordnungen als `{playlist_id: [pfad, ...]}` in `pos`-Reihenfolge. Verschachtelt wird erst im Client — serverseitig zu verschachteln hätte nur eine zweite, gleichwertige Darstellung erzeugt |
| `save_playlist(conn, node)`/`delete_playlist(conn, id)` | Knoten anlegen/aktualisieren (`INSERT OR REPLACE` über die id) bzw. samt allen Nachfahren und deren Zuordnungen löschen. Die id vergibt der Aufrufer (`server._playlist_apply`), damit hier keine zweite Namensregel entsteht |
| `playlist_descendants(conn, id)`/`playlist_can_reparent(conn, id, parent)` | Vollständiger Teilbaum (Ordner dürfen Ordner enthalten, an echtem Rekordbox-Material kommt Tiefe 4 vor) und die Zyklusprüfung beim Verschieben — ein Ordner darf nicht unter sich selbst landen, der Teilbaum wäre danach unerreichbar |
| `set_playlist_items(...)`/`add_playlist_items(...)`/`remove_playlist_items(...)` | Inhalt einer Playlist vollständig ersetzen (der Weg für Umsortieren und Rückgängig), hinten anhängen (bereits enthaltene Tracks bleiben an ihrer Stelle) oder entfernen; `remove` schließt die entstandene Lücke in `pos`, damit die Reihenfolge lückenlos bleibt |
| `waveform_get(conn, path, mtime)`/`waveform_put(...)` | Lesen/Schreiben des Waveform-Cache, invalidiert bei abweichender `mtime` |
| `rekordbox_paths(conn)`/`sync_rekordbox_presence(conn, present_paths)`/`mark_rekordbox_present(conn, paths)` | Praesenz-Cache `rekordbox`: `sync_...` ersetzt den ganzen Inhalt bei einem vollen Abgleich (nur Pfade, die auch in `files` bekannt sind), `mark_...` ergaenzt punktuell nach einem erfolgreichen Playlist-Push |
| `music_added_map(conn)`/`sync_music_added(conn, dates)`/`mark_music_added(conn, paths)` | Analog zum Rekordbox-Cache, aber eigene Tabelle `music_added` (Pfad → `added_ts`) statt reiner Praesenz — `sync_...` ersetzt den ganzen Inhalt bei einem vollen Abgleich gegen `media.music_added_dates()`, bewusst getrennt vom Rekordbox-Abgleich; `mark_...` ergaenzt punktuell nach einem eigenen Import (`/api/add-to-library`) mit `time.time()` als `added_ts` und ueberschreibt einen vorhandenen Eintrag absichtlich — wer erneut importiert, will den Track in der nach „Hinzugefügt" sortierten Liste oben sehen |
| `row_for_path(conn, path)` | Einzelzeilen-Lookup — dient zugleich als Autorisierungs-/Allowlist-Prüfung für Server-Endpunkte |
| `fetch(conn, verdicts=None)` | Alle (ggf. gefilterten) `files`-Zeilen, sortiert nach `cutoff_hz ASC, path ASC` |
| `summary(conn)` | `{total, by_verdict, errors, ignored, favorites: {list1, list2, list3}, corrected}` — Basis für `stats` und den UI-Kopf |

#### `app/config.py`

3-Ebenen-Konfigurationshierarchie, Dev-/Bundle-Pfadauflösung.

| Funktion | Zweck |
|---|---|
| `is_frozen()` | `True` innerhalb eines PyInstaller-Bundles |
| `bundle_dir()` | Read-only-Ressourcenwurzel (`sys._MEIPASS` bei Bundle, sonst Projekt-Root); enthält `app/webui`, Standard-`config.yaml`, `vendor/` |
| `base_dir()` — Pfade: Dev vs. Bundle | Beschreibbare Datenwurzel: Dev = Projekt-Root; Bundle = `~/Library/Application Support/TrackTab/`. **Seiteneffekt:** benennt beim ersten Aufruf im Bundle-Modus einen alten Ordner (`APP_NAME`-Historie in `_OLD_APP_NAMES`: „TrackLab", davor „Audio Quality Check", davor „MP3 Quality Check") in den neuen Namen um, falls vorhanden (App-Umbenennung, Datenerhalt) |
| `vendor_dir()`/`webui_dir()` | Unterordner von `bundle_dir()` |
| `default_daw_template_path()` | Mitgelieferte Ableton-Vorlage `resources/ableton_template.als` (nur lesend, bleibt im Bundle — anders als `config_path()` nie nach `base_dir()` kopiert); Fallback in `media.open_in_daw()`, wenn `external_daw_template` leer ist |
| `config_path()` | Pfad zur beschreibbaren `config.yaml`; kopiert im Bundle-Modus beim ersten Aufruf die mitgelieferte Default-`config.yaml` nach `base_dir()` |
| `local_config_path()` | Pfad zu `config.local.yaml` |
| `resolve(path_value)` | Zentrale Pfadauflösung: expandiert `~`, verankert relative Pfade an `base_dir()`. **Regel:** alle Pfade im Projekt müssen hierüber laufen, nie `PROJECT_ROOT` oder rohe relative Pfade direkt |
| `defaults()` | Referenz aller Standardwerte (Spektral-Schwellwerte, Lautheits-Referenzband, `cutoff_classes` je Codec, Pfade, Shop-URL-Vorlagen, …) |
| `_read_yaml(path)` | Sicherer YAML-Loader, wirft nie |
| `_normalize_cutoff_classes(cfg)` | Back-Compat: wandelt eine alte flache `cutoff_classes`-Liste in `{"mp3": [...]}` um, ergänzt fehlende Leitern aus `defaults()`, sortiert jede Leiter absteigend |
| `load()` (`@lru_cache`) | Schichtet `defaults()` < `config.yaml` < `config.local.yaml` (nur Nicht-`None`-Werte überschreiben) |
| `reload()` | `load.cache_clear()` + `load()` — **einzige Methode, um nach einem Schreibvorgang aktuelle Werte zu erhalten** |
| `worker_count()` | `cfg["workers"]` falls `>0`, sonst `max(1, cpu_count()-1)` |

### UI / Server / App-Shell

#### `app/server.py`

Siehe [Server — Endpunktreferenz](#server--endpunktreferenz) weiter oben für alle GET-/POST-Routen und Sicherheitsgates.

#### `app/pwa.py`

Web-App-Manifest fuer die PWA-Installierbarkeit (`MANIFEST_JSON`, einmal beim Import gebaut, ausgeliefert unter `/manifest.json`) plus drei rohe Icon-PNGs (`ICON_192_PNG`/`ICON_512_PNG`/`ICON_APPLE_TOUCH_PNG`, ausgeliefert als echte Bilddateien unter `/icon-192.png`/`/icon-512.png`/`/apple-touch-icon.png` — bewusst keine data:-URIs, siehe unten). Icon-Konstanten werden von `build_assets/make_icon.py::patch_pwa_module()` ersetzt. Siehe [PWA / Installierbarkeit — Interna](#pwa--installierbarkeit--interna).

#### `app/report.py`

Erzeugt HTML/CSV/M3U aus DB-Zeilen.

| Funktion | Zweck |
|---|---|
| `rows_to_payload(rows, cfg, ignored=None, favorites=None, corrected=None, rekordbox=None, music_added=None)` | Baut die kompakte JSON-Struktur für den HTML-Report. `favorites` ist `{list1: {...}, list2: {...}, list3: {...}}`, gibt drei Keys `f1`/`f2`/`f3` in die Zeile. Nutzt bewusst kurze Ein-/Zwei-Buchstaben-Schlüssel (`p`=Pfad, `v`=Verdikt, `co`=Cutoff, `bw`=is_brickwall, `al`=Album, `aa`=Albumkünstler, `cp`=Komponist, `ge`=Genre, `yr`=Jahr, `bp`=BPM, `cm`=Kommentar, `tn`=Track-Nummer (nur intern für „nach Album gruppieren" genutzt, keine eigene Spalte), `cv`=has_cover, `rb`=Rekordbox-Praesenz, `im`=Music.app-Praesenz, `da`=Music.app-„Datum hinzugefügt" als Unix-Timestamp (0, wenn unbekannt), `ti`=Auffälligkeiten (`tag_issues`-JSON, siehe `app/taganomaly.py` — anders als `rs`/`sp` IMMER befüllt, auch leer, weil unabhängig vom Audio-Verdikt), …), um die Payload bei tausenden Zeilen klein zu halten; Dateiname/Ordner/`file://`-URL werden **nicht** mitgeschickt, sondern clientseitig aus `p` rekonstruiert |
| `build_csv(rows, cfg, hidden=None)` | Schreibt `report.csv`, deutsche Spaltenköpfe, Dezimalkommas, überspringt `hidden`-Pfade (Ausgeblendet ∪ Korrigiert — Merken hält Tracks bewusst **nicht** mehr aus dem Export fern) |
| `build_m3u(rows, cfg, hidden=None)` | Schreibt `verdaechtig.m3u8`, nur FAKE/VERDÄCHTIG, sortiert nach aufsteigendem Cutoff |
| `build_html(rows, cfg, ignored=None, favorites=None, corrected=None, rekordbox=None, music_added=None, playlists=None, playlist_items=None)` | Baut `meta` (Zeitstempel, Gesamtzahl, live aus der Konfiguration gelesene Klassengrenzen, Lautheits-Referenzband, `favoriteLists` aus `cfg["favorite_lists"]`) und injiziert `meta`+Payload als JSON in die Platzhalter `/*__META__*/null`/`/*__DATA__*/null` |
| `build_all(conn, cfg)` | Orchestriert alle drei Ausgaben; `hidden = ignored ∪ corrected` für CSV/M3U (ohne Favoriten), während die HTML `favorites` getrennt für clientseitige Filter/Listen erhält |
| *(META-Schlüssel)* | Neben Zeitstempel/Schwellwerten/`favoriteLists` werden auch `playlists` und `playlistItems` eingebacken — damit Seitenbaum und zuletzt geöffneter Knoten schon vor dem ersten `/api/playlists`-Fetch stehen. Aktualisiert wird danach **nicht** über einen Rebuild, sondern live über den Endpunkt (dasselbe Hybrid-Muster wie bei den Merklisten) |
| `_template()` | Fügt `index.html` + `app.css` (an `<!--__CSS__-->`) + `app.js` (an `<!--__JS__-->`) zu einer einzigen, komplett eigenständigen Datei zusammen |
| `is_stale(cfg)` | Ob `report.html` älter ist als eine der drei Quelldateien in `app/webui/`. Gebraucht von `desktop.main()`: aus einem Bundle heraus lässt sich `report` nicht von Hand aufrufen, eine frisch gebaute App zeigte sonst stumm die alte Oberfläche |

**Gotcha:** Kennzahlen/Histogramm werden bewusst **clientseitig im Browser** berechnet, nicht serverseitig — damit sie sich sofort aktualisieren, wenn ein Track ausgeblendet oder korrigiert wird, ohne Neuladen.

#### `app/stats.py`

Jahres-/Monats-Statistik aus dem Änderungsprotokoll + der `events`-Tabelle, siehe [Statistik — Interna](#statistik--interna) für die Architektur.

| Funktion | Zweck |
|---|---|
| `stats_path(cfg=None)` | `cfgmod.resolve(cfg["stats_path"])`, analog `report_path`/`csv_path` |
| `is_stale(cfg, conn)` | Vergleicht die jüngste Log-Datei-`mtime` und `MAX(events.ts)` gegen die in `stats.json` gespeicherten `covers_through`-Werte; `True` bei fehlender/kaputter Datei |
| `_iter_log_actions(cfg)` | `(jahr, monat_0basiert, action)` je Log-Zeile — Datum aus dem Dateinamen, nicht aus dem Zeileninhalt; kaputte Zeilen werden einzeln übersprungen |
| `_iter_play_events(conn)` | `(jahr, monat_0basiert, path, duration_s)` je `events`-Zeile mit `kind = "play"` |
| `_aggregate(cfg, conn)` | Ein Durchlauf je Quelle → `{jahr: {...}}` plus die Rohliste `(path, duration_s)` je Jahr für `_top_tracks()`/`_top_grouped()` |
| `_top_tracks(meta, raw_plays, limit=50)` | Top-Tracks nach Hörzeit, `meta` kommt aus einer einzigen `files`-Abfrage über alle betroffenen Pfade (kein Join je Jahr) |
| `_top_grouped(raw_plays, meta, field, limit=10)` | Gruppiert dieselbe Rohliste nach `files.artist`/`files.genre`; leere Werte fallen aus der Rangliste |
| `build(cfg, conn)` | Kompletter Aufbau, atomar geschrieben (Temp-Datei + `os.replace()`) |
| `read(cfg=None)` | Liest `stats.json` unverändert ein (nur wenn `is_stale()` bereits `False` war) |

#### `app/settings.py`

Backt das Einstellungen-Panel: Schema (`GROUPS`), aktuelle Werte, Validierung, Persistenz nach `config.local.yaml`, Pro-Gruppe-Reset.

`GROUPS` ist eine Liste von Gruppen-Dicts (`id, title, note?, fields`); jedes Feld hat `key, type, label` und optional `unit, min, max, step, help, placeholder`. `type` ist eines von `paths | list | number | text | app | select | bool | button` — die Oberfläche rendert das Formular generisch allein aus dieser Struktur, ohne das Layout hart zu kodieren. `button` ist ein Sonderfall ohne Wert: reine Aktion (aktuell nur „Verwaiste Ordner") statt eines zu speichernden Feldes — `settingField()` in `app.js` rendert dafür keinen `data-key`, also ignorieren `collectSettings()`/`validate()` es automatisch, ohne eigene Sonderfälle dort zu brauchen.

Reihenfolge der Tabelle = Reihenfolge in `GROUPS` = Reihenfolge im Einstellungen-Panel (`display` bewusst zuerst, direkt gefolgt von der eigenen `search`-Gruppe für Tippfehler-Toleranz und Autovervollständigung).

| Gruppe | Titel | Felder (Auswahl) |
|---|---|---|
| `display` | Darstellung | `ui_language`, `theme`, `font_size`, `pin_filter_bar`, `infinite_scroll` |
| `search` | Suche | `search_typo_tolerance`, `search_autocomplete_min_chars` |
| `library` | Bibliothek | `library_paths`, `orphan_cleanup` (Knopf, kein Wert), `extensions`, `exclude_dirs`, `min_duration_s` |
| `analysis` | Analyse | `max_analysis_s`, `cliff_min_db`, `steep_brickwall_db`, `gate_rms_percentile` |
| `classes` | Klassifikation (MP3) | `class_320_khz` … `class_128_khz`, `verdict_suspect_steps`, `verdict_fake_steps` |
| `classes_aac` | Klassifikation (AAC) | `aac_class_256_khz` … `aac_class_128_khz` |
| `classes_lossless` | Klassifikation (verlustfrei) | `lossless_suspect_khz`, `lossless_fake_khz`, `lossless_min_steepness_db` |
| `loudness` | Lautheit | `loudness_ref_low_lufs`, `loudness_ref_high_lufs`, `loudness_clip_dbtp` |
| `tools` | Externe Programme | `external_editor`, `external_daw`, `external_mik`, `aac_encoder` |
| `rekordbox` | Rekordbox | `external_rekordbox`, `rekordbox_playlist`, `rekordbox_quality_check`, `rekordbox_min_cutoff_khz` |
| `performance` | Leistung | `workers` |

| Funktion | Zweck |
|---|---|
| `current(cfg=None)` | Flacht die aktuelle Konfiguration in `{field_key: value}`, entfaltet die Klassen-Leitern in flache Schlüssel |
| `describe()` | Payload für `GET /api/settings`: `{groups, values, defaults, local_file, detected_editor, detected_mik, detected_rekordbox, apps, shops}` |
| `_defaults_cfg()` | „Werksangaben" = Code-Defaults + `config.yaml` **ohne** `config.local.yaml` — Vergleichsbasis für „Auf Vorgabe" |
| `_clean_number(field, raw)` / `_clean_list(raw)` | Validierung/Clamping numerischer bzw. Listen-Felder |
| `validate(incoming)` | Validiert einen ganzen Settings-Payload; unbekannte Schlüssel werden stillschweigend verworfen; erzwingt absteigende Reihenfolge der Klassengrenzen (`_check_descending`) und `loudness_ref_low_lufs < loudness_ref_high_lufs` |
| `save(incoming)` | Validiert, merged in `config.local.yaml`, baut `cutoff_classes`-Leitern aus den flachen Feldern neu zusammen, schreibt YAML, ruft `config.reload()` |
| `_slugify(name, taken)` / `validate_shops(items)` / `save_shops(items)` | Verwaltung der Shops-Liste unter dem Top-Level-Schlüssel `shops` |
| `reset(group_id=None)` | Setzt eine Gruppe (oder bei `None` die komplette `config.local.yaml`) zurück |

#### `app/media.py`

Externe Werkzeug-Integration: ffmpeg/ffprobe-Lokalisierung, Waveform-Berechnung, externe Apps (Editor/Finder), App-Icons, Papierkorb, iTunes-Store-Suche.

| Funktion | Zweck |
|---|---|
| `tool_path(name)` (`@lru_cache`) | Sucht `ffmpeg`/`ffprobe`: `MP3QC_FFMPEG_DIR`-Umgebungsvariable → gebündeltes `vendor/` → `PATH` → feste Homebrew-Pfade |
| `ffmpeg_path()`/`ffprobe_path()` | Dünne Wrapper über `tool_path` |
| `_encoder_names()`/`encoder_available(name)` | Liste verfügbarer ffmpeg-Encoder (`ffmpeg -encoders`) |
| `resolved_aac_encoder(cfg)` | aac_encoder-Fallback (siehe Glossar) |
| `available()` | Start-Check für beide Tools |
| `brew_path()`/`forget()` | Homebrew-Binary finden; `tool_path`-Cache leeren (nach Installation mitten in der Session) |
| `waveform_peaks(path, buckets=1000)` | Dekodiert auf 8 kHz Mono, bildet pro Bucket `max(abs())`, normalisiert — die Rohberechnung hinter dem Waveform-Cache |
| `find_audio_editor()` | Findet die neueste installierte iZotope-RX-Version per Glob/Sortierung |
| `editor_path(cfg=None)` | Konfigurierter Audio-Editor, mit Auto-Fallback auf die neueste installierte iZotope-RX-Version |
| `find_mixed_in_key()`/`mik_path(cfg=None)` | Analog zu `find_audio_editor()`/`editor_path()`, für Mixed In Key |
| `find_rekordbox()`/`rekordbox_path(cfg=None)` | Analog, für Rekordbox — mehrere installierte Versionen liegen versioniert (`rekordbox 6`, `rekordbox 7`, …); `rekordbox_path()` legt über `external_rekordbox` auch fest, welche Version `rekordbox._resolve_master_db_path()` für den Datenbankzugriff verwendet |
| `daw_path(cfg=None)` | **Kein** Auto-Fallback wie bei Editor/MIK/Rekordbox — DAWs (Logic, Ableton, Cubase, …) haben keinen einheitlichen Namen zum Erraten; liefert `None` ohne `external_daw`, der Knopf in der Oberfläche bleibt dann komplett ausgeblendet statt nur ausgegraut |
| `open_in_app(path, app_path, missing_hint)` | `open -a <app> <path>`, wirft `MediaToolMissing` mit Hinweistext |
| `open_in_editor` | Wrapper über `open_in_app` |
| `open_in_daw(path, cfg=None)` | Wie `open_in_editor`, außer bei erkanntem Ableton (`ableton.is_ableton()`): dann wird per `ableton.build_temp_project()` erst ein temporäres Projekt mit dem Track auf Spur 1 erzeugt (Vorlage aus `external_daw_template`, ohne eigene Angabe die mitgelieferte `config.default_daw_template_path()`) und dieses statt der rohen Audiodatei an `open_in_app()` übergeben (siehe `app/ableton.py` — Ableton importiert eine per `open -a` übergebene Datei sonst nicht in ein offenes Set) |
| `find_empty_folders(root)` | Verwaiste Ordner unter `root` — äußerste Ordner, deren gesamter Inhalt rekursiv aus nichts als `.DS_Store` besteht; nutzt intern `_scan_empty()` (bottom-up, sammelt vorläufige Treffer erst beim Rückweg aus der Rekursion, verwirft sie wieder, falls der Elternordner selbst schon komplett leer ist) |
| `list_apps()` | Alle installierten macOS-Apps via `mdfind kMDItemContentType == 'com.apple.application-bundle'` |
| `app_icon_png(app_path, size=64)` | `.icns` → PNG via `sips`, In-Memory-Cache; Basis für App-Icon-Symbol |
| `move_to_trash(path)` | Papierkorb via Finder-AppleScript (`osascript`); Pfad als argv-Item übergeben (sicher gegen Anführungszeichen/Umlaute im Dateinamen) |
| `itunes_lookup(query, country="DE", limit=8)` | Ruft Apples öffentliche Such-API auf; sendet nur den Suchtext; bevorzugt Ergebnisse mit `trackPrice` |
| `itunes_search_url(query)` | Fallback-Such-Deeplink (`itmss://`) |
| `open_itunes_store(query)` | Öffnet die direkte Titel-Store-Seite (oder Such-Fallback) in Music.app |
| `music_added_dates()` | `{POSIX-Pfad: Unix-Timestamp}` für „Datum hinzugefügt" aus der ganzen Music.app-Bibliothek. `date added` kommt als Bulk-Abfrage (`... of every track of library playlist 1`, ein Apple Event); `location` dagegen MUSS Track für Track mit `try` gelesen werden — sowohl der Bulk-Getter als auch ein `whose location is not missing value`-Filter brechen mit Fehler -1728 für die GESAMTE Liste ab, sobald auch nur ein Track keine lokale Datei hat (z. B. nur per Apple-Music-Abgleich in der Cloud, an echter iCloud-Musikmediathek bestätigt), entsprechend langsamer bei grossen Bibliotheken (~10.800 Tracks ≈ 90 s). Die Schleife muss dabei in einem eigenen `tell application "Music"` laufen, sonst verliert `item i of trackList` den Bezug auf das Music.app-Objekt (-10001); die Epoch-Konstruktion (1.1.1970, als Referenzpunkt für die Sekundendifferenz statt lokalisierten Datumstexts) muss dagegen AUSSERHALB jedes `tell application "Music"` passieren, sonst interpretiert Music.app `set year/month/day/time of epoch to ...` als eigenes Apple Event und wirft -1731. osascript liefert die Sekundendifferenz zudem lokalisiert zurück (Komma als Dezimaltrenner, ggf. Exponentialschreibweise) — Python ersetzt das Komma vor `float()` |
| `music_playlists()` | Playlisten-Baum aus Music.app als flache Knotenliste `{id, parent, kind, count, name}`. `every user playlist` enthält die **Ordner nicht** (obwohl `folder playlist` laut Dictionary davon erbt) — sie kommen über `every folder playlist` und werden über die `persistent ID` mit dem `parent` verknüpft; `class of p is folder playlist` bricht mit -1731 ab. `special kind` hält die eingebaute Mediathek (`Music`) heraus, sie entspricht dem eigenen Knoten „Alle". Gemessen: 28 Knoten in 1,7 s |
| `music_playlist_tracks(persistent_id)` | Tracks einer Playlist in ihrer Reihenfolge, je Eintrag `{id, name, artist, path}` (`path` leer bei Cloud-Tracks). Holt **drei parallele Listen** (`persistent ID of every track`, `persistent ID of every file track`, `location of every file track`) und verbindet sie in Python über die ID: `location of every track` bricht mit -1728 für die ganze Liste ab, sobald ein Track keine lokale Datei hat, und `every file track` allein ist nicht positionsgleich zu `every track`. So bleiben Reihenfolge und „ohne lokale Datei"-Markierung erhalten, ohne ein Apple Event je Track (728 Tracks ≈ 1,6 s) |
| `set_tracks_genre(items, genre)` / `set_tracks_artist(items, artist)` / `set_tracks_album(items, album)` | Setzt Genre/Künstler/Album bereits importierter Tracks direkt — dünne Wrapper um `_set_tracks_field(items, value, prop)` (das AppleScript ist bis auf die Music.app-Eigenschaft identisch). `items` = `(pfad, titel)`-Paare, beliebig viele in EINEM `osascript`-Aufruf (Pfade/Titel zeilenweise verbunden statt einzelner argv-Einträge, da ein Umbenennen hunderte Tracks treffen kann). Titel-Vorfilter (`every track of library playlist 1 whose name is theTitle`), dann `location` je Kandidat mit `try` lesen und die Koerzierung zu `POSIX path of theLoc` AUSSERHALB von `tell application "Music"` durchführen (sonst -1728, siehe `add_to_music_library()`), erst nach exaktem Pfadvergleich die Eigenschaft setzen innerhalb eines frischen `tell`-Blocks. Aufgerufen von `server._rename_tag_value()` für alle Pfade aus `music_added_map()` |

#### `app/ableton.py`

Erzeugt das temporäre Ableton-Projekt hinter dem Vorlagen-Mechanismus von `media.open_in_daw()` (siehe [In der DAW öffnen](#in-der-daw-öffnen)). `.als` ist gzip-komprimiertes XML — es wird nichts an Ableton selbst angesteuert (kein AppleScript-Dictionary/CLI vorhanden), sondern nur eine neue Projektdatei gebaut und per `open -a` geöffnet.

| Funktion/Klasse | Zweck |
|---|---|
| `AbletonTemplateError` | Vorlage fehlt, ist keine gültige `.als`-Datei, Spur 1 ist keine Audiospur, oder ihr Arrangement-Fenster ist nicht frei |
| `is_ableton(app_path)` | Grobe Ja/Nein-Erkennung über den App-Namen (`"ableton" in Path(app_path).stem.lower()`) — keine Auto-Suche wie bei Editor/MIK/Rekordbox, nur ob die vom Nutzer gewählte DAW Ableton ist |
| `_CLIP_TEMPLATE` | Hardcodiertes `AudioClip`/`SampleRef`-XML-Skelett, abgeleitet aus einem echten, per Drag&Drop ins Arrangement-Fenster in Ableton 12.3 erzeugten Referenzprojekt. Bewusst **ohne** Warp-Genauigkeit: `IsWarped` aus, `Loop`/`CurrentEnd` sind reine Anzeigewerte (120 BPM angenommen) ohne Bezug zum tatsächlichen Tempo der Datei — Ziel ist ein hörbar geladener Track auf Spur 1, kein fertiges Beatgrid |
| `_track1_main_sequencer_span(xml)` | Grenzt den zulässigen Einsetzbereich strikt auf die ECHTE DeviceChain von Spur 1 ein — zwei Fallstricke, beide an einer vom Nutzer eingereichten, bereits belegten Vorlage entdeckt: (1) jede Spur (auch Return/Master/PreHear) hat ihr eigenes `<Sample><ArrangerAutomation><Events>`, ein unbegrenzter Regex fände bei belegter Spur 1 einfach den nächsten freien Slot irgendeiner anderen Spur; (2) jede Spur hält zusätzlich zur echten DeviceChain (`<MainSequencer>`) eine strukturgleiche Kopie für den eingefrorenen Zustand (`<FreezeSequencer>`, direkt danach) — **auch die** hat ein eigenes, meist leeres `<Events>`. Ohne Eingrenzung auf `<MainSequencer>…</MainSequencer>` (Ende = Beginn von `<FreezeSequencer>`) hätte eine belegte Spur 1 den leeren Freeze-Slot getroffen und wäre fälschlich als "frei" durchgegangen |
| `validate_template(template_path)` | Wie `build_project()`, aber ohne Zieldatei — nur die Struktur. Lässt Fehler schon beim Speichern in den Einstellungen auffallen (`settings.validate()`) statt erst beim ersten „In DAW öffnen"-Klick |
| `build_project(track_path, template_path)` | Lädt die Vorlage (gzip+XML), löst `track_path` **absolut** auf (ein relativer Pfad im `Path`-Feld führt bei Ableton zu „Datei konnte nicht geöffnet werden"), füllt `_CLIP_TEMPLATE` mit `probe.probe()`-Daten der Zieldatei (Name, absoluter Pfad, Dateigröße, mtime, `duration_s * sample_rate` als `DefaultDuration`) und setzt ihn per Regex in die erste leere `<Events />` innerhalb von `_track1_main_sequencer_span()` ein — das ist das Arrangement-Fenster der Spur, **nicht** `<ClipSlotList>` (Session-Ansicht, komplett getrennte XML-Struktur); liefert die neu gzip-komprimierten Bytes |
| `build_temp_project(track_path, template_path)` | Wie `build_project()`, schreibt das Ergebnis aber in eine `tempfile.mkstemp(suffix=".als")`-Datei und liefert deren Pfad — das, was `media.open_in_daw()` tatsächlich an `open_in_app()` übergibt |

**Gotcha:** Die Vorlage selbst darf auf Spur 1 keinen eigenen Arrangement-Clip haben (sonst ist `<Events />` dort nicht mehr leer) — sie liefert nur die FX-Kette (z. B. Reverb, Delay), der Clip kommt bei jedem Aufruf frisch aus dem Code. Ein Clip in der Session-Ansicht (`ClipSlotList`) der Vorlage wäre dagegen unproblematisch, da dort nichts eingesetzt wird. Eine beliebige, bereits benutzte Ableton-Projektdatei als eigene Vorlage einzutragen schlägt dadurch kontrolliert fehl (klare deutsche Fehlermeldung beim Speichern), statt den Track auf einer falschen Spur oder im Freeze-Schatten einer Spur landen zu lassen.

#### `app/rewrite.py`

Bitrate-Neukodierung — eine von drei Funktionen (neben Papierkorb und `tags.py`), die Audiodateien verändert.

| Funktion | Zweck |
|---|---|
| `RewriteError` | Fehlerklasse; garantiert, dass das Original bei Fehlern unangetastet bleibt |
| `rewrite_bitrate(path, target_kbps, cfg)` | Nur für lossy Formate (`codec_family == lossless` → sofortiger `RewriteError`). Ablauf: Original-Tags laden → Muell-Kommentar bereinigen → auf temporäre Datei kodieren → Tags zurückschreiben (inkl. GEOB/PRIV) → Dauer der neuen Datei gegen das Original plausibilisieren (>2 %/>1 s Abweichung → Fehler) → erst dann Original in den Papierkorb, temporäre Datei an dessen Stelle → gibt eine frische `analyzer.analyse_file`-Zeile zurück |
| `_strip_junk_comment(tags_obj)` | Entfernt ein erkanntes iTunSMPB-artiges Muell-Kommentarfeld aus dem geladenen Original, bevor es auf die neu kodierte Datei übernommen wird — sonst würde jede Neukodierung es unverändert weiterschleppen |

#### `app/convert.py`

Format-Konvertierung (MP3 320 kbit/s CBR oder AIFF) — anders als `rewrite.py` ändert sich dabei die Dateiendung, die neue Datei landet also unter einem neuen, kollisionsfrei ermittelten Pfad statt am Platz des Originals. Gleiches Sicherheitsprinzip: komplett bauen und prüfen, bevor das Original angefasst wird.

| Funktion | Zweck |
|---|---|
| `ConvertError` | Fehlerklasse; Original bleibt bei Fehlern in jedem Fall unangetastet |
| `ConvertSkip(reason, message)` | Kein Fehler, sondern eine bewusste Ablehnung — `reason` ist `"upscale"` (Ziel wäre eine Qualitätsverbesserung) oder `"already_target"` (Datei liegt schon im Zielformat) |
| `decide(codec_family, declared_kbps, suffix, target)` | Reine Entscheidungsfunktion ohne I/O — Ziel AIFF nur erlaubt für bereits verlustfreie Quellen (sonst `"upscale"`, bereits `.aiff`/`.aif` → `"already_target"`); Ziel MP3 320 immer erlaubt für verlustfreie Quellen, für eine bereits verlustbehaftete Quelle nur wenn `declared_kbps >= 320` (sonst `"upscale"`, bei `lossy_mp3` mit `>= 320` → `"already_target"`). Dieselbe Tabelle läuft client-seitig als Vorfilter (`convertDecision()`, `app.js`) — der Server prüft sie hier trotzdem autoritativ erneut |
| `convert_format(path, target, cfg, db_fallback=None, trash_original=True)` | Ablauf: `probe.probe()` → `decide()` (wirft `ConvertSkip` bei Ablehnung) → Zielpfad per `_unique_target()` (neue Endung, `-2`/`-3`-Suffix bei Namenskollision) → Encode auf `.aqc-<uuid>`-Tempdatei → Dauer der Tempdatei gegen das Original plausibilisieren (>2 %/>1 s Abweichung → Fehler) → `_transfer_tags()` → `trash_original` steuert, ob jetzt noch `media.move_to_trash()` auf dem Original laeuft (Standard) oder es unangetastet liegen bleibt → Tempdatei an den Zielpfad verschoben (immer, unabhaengig von `trash_original`) → gibt eine frische `analyzer.analyse_file`-Zeile zurück, plus `"trashed": trash_original`. `db_fallback` (optionale DB-Zeile) liefert Metadaten für Felder, die die Datei selbst nicht trägt — `None` für Einzelprüfungs-Pfade ohne DB-Zeile |
| `_unique_target(folder, stem, suffix)` | Wie `rename._unique_target()`, aber für einen Endungswechsel statt eines Namenswechsels |
| `_encode_mp3_320(src, dst)` | `-c:a libmp3lame -b:a 320k -compression_level 0 -id3v2_version 0` — `-compression_level 0` ist LAME's langsamster/qualitativ bester Modus. Kein explizites Joint-Stereo-Flag: an echtem Material geprüft, LAME kodiert bei 320 kbit/s CBR grundsätzlich in "Stereo" statt "Joint Stereo" (per `mutagen.mp3.MP3().info.mode`), auch mit `-joint_stereo 1` erzwungen — LAME selbst entscheidet bei dieser Bitrate gegen Joint Stereo (kein Qualitätsgewinn mehr durch M/S-Codierung bei maximaler Bitrate), das lässt sich über ffmpegs `libmp3lame`-Wrapper nicht überstimmen |
| `_encode_aiff(src, dst, bit_depth)` | `-c:a pcm_s24be` oder `pcm_s16be` je nach `probe.source_bit_depth()` — AIFF ist Big-Endian, `*le`-Codecs (wie bei WAV) liefern einen Muxer-Fehler ("Could not write header") |
| `_transfer_tags(src, dst, orig_probe, db_fallback)` | Metadaten über `tags.py`s formatunabhängige Feld-API (`read_extra`/`write_tags`/`write_cover`) statt eines Tag-Objekt-Kopie-Tricks wie bei `rewrite.py` — Konverter überbrückt beliebige Format-Paare, nicht nur MP3↔MP3/MP4↔MP4. Je Feld: Datei-Wert, sonst `db_fallback`. Sonderfall WAV→AIFF (siehe `_bridge_wav_to_aiff`) |
| `_bridge_wav_to_aiff(src, dst)` | Nur für WAV-Quelle + AIFF-Ziel: übernimmt das komplette ID3-Tag-Objekt der Quelle (`mutagen.wave.WAVE` → `mutagen.aiff.AIFF`) wortwörtlich, bevor die generischen Feld-Writes greifen, damit unbekannte Frames (GEOB/PRIV) erhalten bleiben — dieselbe Container-Familie beidseitig, einzige Kombination, bei der ein Bridging überhaupt etwas rettet |

#### `app/tags.py`

Tags lesen/schreiben für Tags bearbeiten: Titel, Interpret, Album, Albumkünstler, Komponist, Genre, Jahr, BPM, Kommentar, Cover, Tracknummer/-gesamtzahl. Anders als `rewrite.py` wird der Audio-Stream nie angefasst, deshalb funktioniert es auch für verlustfreie Formate (ALAC/FLAC/WAV/AIFF) — die Bitrate-Korrektur ist die einzige Funktion mit einer Lossy-Beschränkung, Tag-Edits nicht. Titel/Interpret/Album kommen weiterhin aus `probe.py`/ffprobe (unveraendert); alle uebrigen Felder hier.

**`is_junk_comment(text)`** erkennt ein Kommentarfeld, das nur aus mindestens zwei durch Leerzeichen getrennten Hex-Gruppen (je mindestens 6 Ziffern) besteht — die Form eines iTunSMPB-Gapless-Felds (eigentlich ein MP4-Atom für Encoder-Delay/Padding/Samplezahl), das manche Konverter beim Wandeln von AAC/M4A nach MP3 unverändert in ein COMM-Frame kopieren, statt es zu verwerfen oder passend zu übersetzen — an echtem Material bestätigt (z. B. `00000000 00000210 00000AE0 000000000002B110 …`). Erkennung rein an der Form, nicht am genauen Wert, deckt damit auch verwandte Varianten (z. B. iTunNORM) ab. Alle drei `_write_tags_*`-Funktionen leeren ein solches Feld automatisch beim Speichern, statt es zu übernehmen; `rewrite.py` nutzt dieselbe Erkennung beim Neukodieren.

| Funktion | Zweck |
|---|---|
| `TagError` | Fehlerklasse; anders als in `probe.py` wird ein Schreibfehler hier nicht verschluckt, sondern bis zum Server-Endpunkt durchgereicht |
| `_id3_tags(path, suffix)` | Liefert `(tags_obj, save_fn)` für die drei ID3-basierten Container: `ID3(path)` direkt bei MP3, `mutagen.wave.WAVE`/`mutagen.aiff.AIFF` bei WAV/AIFF (eigene Container-Klassen, da ihr `.tags` erst über den RIFF/IFF-Wrapper erreichbar ist, kein roher ID3-Header wie bei MP3) |
| `_parse_year(raw)` | Erste 4-stellige Zahl aus einem beliebigen Datumsstring (`"2005-06-01"`, `"2005"`, je nach Tagger) — 0, wenn keine gefunden wird |
| `read_extra(path)` | `{genre, bpm, has_cover, album_artist, composer, year, comment, track_no, track_total}` für den Scan, mutagen-basiert statt ffprobes uneinheitlicher generischer Tags; best effort, liefert bei jedem Fehler leere Defaults. `track_no`/`track_total` genutzt für die Sortierung bei „nach Album gruppieren" UND als Vorbefüllung im Tags-Dialog |
| `read_key(path)` | Tonart („initial key", z. B. `8A`) aus `TKEY` (ID3), dem MP4-Freiform-Atom `----:com.apple.iTunes:initialkey` bzw. Vorbis `INITIALKEY`/`KEY`. Bewusst **nicht** Teil von `read_extra()` und ohne eigene DB-Spalte: gebraucht wird sie einzig für den Platzhalter `{key}` beim automatischen Umbenennen (siehe `rename.py`), angezeigt oder weitergegeben wird sie nirgends. Geschrieben wird sie ohnehin von außen, in der Regel von Mixed In Key |
| `write_tags(path, fields)` | Schreibt eine Teilmenge von `artist`/`title`/`album`/`album_artist`/`composer`/`genre`/`year`/`bpm`/`comment`/`track_no`/`track_total`. Bei ID3 gezieltes `setall()`/`delall()` auf einzelne Frames (`TPE1`/`TIT2`/`TALB`/`TPE2`/`TCOM`/`TCON`/`TYER`/`TBPM`/`COMM`/`TRCK`) — GEOB/PRIV bleiben unangetastet, da nie gelesen/verändert. Jahr bewusst als `TYER` (ID3v2.3, passend zu `save(v2_version=3)`) statt `TDRC`; beim Lesen zusätzlich `TDRC` als Fallback für Fremd-Tags (z. B. von iTunes mit v2.4 beschriftete Dateien). `COMM` mit `lang="eng"`/`desc=""` — die von den meisten Taggern als "das eine" Kommentarfeld erkannte Konvention. `track_no`/`track_total` werden zusammen als ein `TRCK`-Frame im Format `"n/N"` geschrieben (nur `"n"`, wenn keine Gesamtzahl bekannt ist) — wird nur eines der beiden Felder im Request mitgeschickt, liest `_write_tags_id3()` den jeweils anderen Teil zuerst aus dem vorhandenen `TRCK` zurück, statt ihn zu verwerfen. MP4 über eigene Atom-Namen (`©ART`/`©nam`/`©alb`/`aART`/`©wrt`/`©gen`/`©day`/`tmpo`/`©cmt`/`trkn`), FLAC über Vorbis-Kommentare (`ALBUMARTIST`/`COMPOSER`/`DATE`/`COMMENT`/`TRACKNUMBER`/`TRACKTOTAL`) |
| `read_cover(path)`/`write_cover(path, data, mime)` | `APIC`-Frame (ID3) / `covr`-Atom (MP4) / `Picture` (FLAC) |

#### `app/rename.py`

Baut aus den Metadaten einer Datei einen Dateinamen nach `cfg["rename_pattern"]` und benennt sie um — der Knopf „Dateien automatisch umbenennen" in den Einzelprüfungen. Verschiebt nie in einen anderen Ordner und rührt den Audio-Stream nicht an; die vierte und letzte Stelle, an der überhaupt eine Musikdatei verändert wird (neben `media.move_to_trash()`, `rewrite.py` und `tags.py`).

| Funktion | Zweck |
|---|---|
| `RenameError` | Fehlerklasse mit anzeigbarem Text; wie in `tags.py` wird ein Fehlschlag nicht verschluckt |
| `PLACEHOLDERS` / `PLACEHOLDER_KEYS` | Die zwölf erlaubten Platzhalter samt Beschreibung — treibt zugleich den Hilfetext des Einstellungsfelds und die Fehlermeldung bei einem unbekannten Platzhalter |
| `_slug(text)` | Freitext → Namensteil: klein, nur `a–z0–9`, Bindestrich als Wortgrenze. `_TRANSLITERATE` (ä→ae, ß→ss, ø→oe …) läuft **vor** der NFKD-Zerlegung, sonst würde daraus `u` bzw. gar nichts |
| `values_for(meta)` | Alle Platzhalter zu fertigen Strings; `{bitrate}` ist bei `codec_family == "lossless"` leer, ein fehlender Wert ist überall `""` |
| `render_name(values, pattern)` | Teilt an `_` in Kategorien, ersetzt darin, verwirft leer gebliebene Kategorien **ganz** (kein `__`), kappt bei 200 Zeichen |
| `build_name(meta, pattern)` | `render_name(values_for(meta), pattern)` — der bequeme Weg für Aufrufer mit rohen Metadaten |
| `has_identity(values, pattern)` | Hat mindestens einer der im Muster verwendeten identifizierenden Platzhalter (`_IDENTITY_KEYS`) einen Wert? Fragt das Muster gar nicht danach, immer `True` |
| `validate_pattern(pattern)` | Prüfung für den Einstellungs-Dialog (aufgerufen aus `settings.validate()`): leer, ohne Platzhalter, unbekannter Platzhalter, `/ \\ :` → `ValueError` mit anzeigbarem Text |
| `read_meta(path)` | Metadaten **aus der Datei**, nicht aus der DB (Einzelprüfungen haben keine Zeile): `probe.probe()` für Interpret/Titel/Album + `declared_kbps`/`codec_family`/`sample_rate` (dasselbe ffprobe wie beim Scan, damit die Bitrate im Namen zur Tabelle passt), `tags.read_extra()` für den Rest, `tags.read_key()` für die Tonart |
| `is_in_library(path, cfg)` | Liegt der Pfad unterhalb eines aufgelösten `library_paths`-Eintrags? Solche Dateien werden nie umbenannt |
| `_unique_target(folder, stem, suffix, source)` | Freier Zielpfad mit `-2`, `-3` …; die Quelle selbst zählt nicht als besetzt (`samefile`), sonst bekäme eine reine Groß-/Kleinschreibungs-Änderung auf APFS grundlos ein `-2` |
| `plan(path, cfg)` | Was passieren würde, ohne die Datei anzufassen: `rename` / `unchanged` / `in_library` / `no_data` |
| `rename_file(path, cfg)` | `plan()` + `os.rename()`. Aufrufer müssen ab da mit `new_path` weiterarbeiten — unter dem alten Pfad ist die Datei weg |

`server._get_download_zip()` (siehe unten) nutzt für den ZIP-Export der Mehrfachauswahl dieselben reinen, lesenden Funktionen (`read_meta()`/`values_for()`/`has_identity()`/`render_name()`) für den Namen **innerhalb des Archivs** — bewusst ohne `plan()`/`rename_file()`/`is_in_library()`, da dort nie `os.rename()` läuft und die Bibliotheksprüfung (Music.app/Rekordbox-Verknüpfung) hier keine Rolle spielt.

#### `app/lookup.py`

Online-Metadatenvorschläge für Tags bearbeiten — rein manuell angestoßen, siehe dort für die Quellenauswahl und die Begründung, warum Shazam/Beatport nicht nutzbar waren.

| Funktion | Zweck |
|---|---|
| `search_itunes(artist, title, query="")` | `itunes.apple.com/search`, kein Key/Login |
| `search_deezer(artist, title, query="")` | `api.deezer.com/search` + ein `/track/{id}`-Folgeaufruf je Treffer für das `bpm`-Feld (0 → `None` im Ergebnis statt einer irreführenden Nullangabe) |
| `search_musicbrainz(artist, title, query="")` | `musicbrainz.org/ws/2/recording`, Pflicht-`User-Agent`-Header (kein Credential) |
| `search(artist, title, query="")` | Orchestriert iTunes + Deezer immer, MusicBrainz nur als Rückfallebene bei leerem Ergebnis; jede Quelle einzeln `try/except`-gekapselt, ein Ausfall verhindert nie die anderen. `query` (manueller Suchbegriff aus dem Fallback-Feld im Dialog) ersetzt, wenn gesetzt, in allen drei Quellen den sonst aus `artist`+`title` zusammengesetzten Suchbegriff |
| `check_update(current_version)` | Manuell angestoßen (Link im Einstellungen-Dialog): fragt `api.github.com/repos/sebssch/TrackTab/releases/latest` ab und vergleicht `tag_name` (SemVer, `X.Y.Z`) gegen `current_version`. `None` bei jedem Fehler (Repo nicht öffentlich, kein Release, unerwartete Antwort) — derselbe stille Fallback wie bei den Suchquellen |
| `_load_tags(src)` | Lädt bestehende Tags via `mutagen` (`MP4` für `.m4a/.mp4/.aac`, sonst `ID3`); `None` bei fehlendem mutagen oder Lesefehler |
| `_encode(src, dst, kbps, codec="libmp3lame")` | ffmpeg-Re-Encode; `-map_metadata -1` entfernt alle Metadaten (Tags kommen separat zurück) |
| `_cleanup(tmp)` | Best-Effort-Löschung der temporären Datei |

#### `app/desktop.py` + `desktop_main.py`

PyInstaller-Einstiegspunkt und native Dialoge.

`desktop_main.py` ruft `multiprocessing.freeze_support()` als **allererste Anweisung** (Pflicht — sonst startet jeder Worker-Subprozess im gebündelten Zustand die ganze App neu statt nur die Analysefunktion auszuführen).

| Funktion/Klasse (`app/desktop.py`) | Zweck |
|---|---|
| `dialog(text, title=..., stop=True, buttons=None)` | Nativer macOS-Dialog via AppleScript `display dialog`; Fallback auf `stderr`-Print, falls `osascript` selbst fehlschlägt |
| `ensure_tools()` | Prüft ffmpeg/ffprobe; bietet bei Fehlen sichtbare Installation via Homebrew in einem Terminal-Fenster an (bewusst nicht still im Hintergrund) |
| `_run_in_terminal(command)` | Öffnet Terminal.app und führt den Befehl sichtbar per AppleScript aus |
| `_existing_instance(first=8756)` | Sucht eine bereits laufende Instanz (GET `/api/ping` über 8756–8775) und liefert deren Port. Verhindert, dass ein zweiter Doppelklick einen zweiten Server auf derselben Datenbank startet |
| `_free_port(first=8756)` | Sucht einen freien TCP-Port über bis zu 20 Kandidaten. Setzt dabei `SO_REUSEADDR`, weil `ThreadingHTTPServer` das auch tut — ohne das scheiterte die Probe an den TIME_WAIT-Resten der eben geschlossenen Verbindungen und jeder Neustart wanderte eine Portnummer weiter |
| `_Quiet` | Ersatz für die `rich`-Console, wenn kein Terminal existiert (Fenster-App hat kein stdout) |
| `main()` | Einstiegspunkt: laufende Instanz? → nur deren Tab öffnen, fertig. Sonst `ensure_tools()` → Config laden → `report.html` bauen, falls sie fehlt **oder älter als die Oberfläche ist** (`report.is_stale()`) → freien Port suchen → `macapp.run()`, ersatzweise `server.serve()`; `MP3QC_NO_BROWSER=1` unterdrückt Auto-Browser-Öffnen |

#### `app/macapp.py`

macOS-Hülle um den Server: eine echte `NSApplication` auf dem Hauptthread, der Server in einem Hintergrund-Thread. **Nur damit beantwortet die App den Quit-Apple-Event** — also `Cmd+Q`, *Beenden* im Dock-Menü und Abmelden/Neustart. Ohne die Hülle hat der Prozess zwar ein Dock-Symbol, aber keine Event-Loop; macOS wartet vergeblich auf die Antwort und bietet danach nur noch „Sofort beenden" (SIGKILL) an — was den Server mitten in einem Schreibvorgang treffen kann.

`pyobjc-framework-Cocoa` ist bewusst eine optionale Abhängigkeit (nur Cocoa, nicht das ~40 Frameworks grosse Metapaket). Fehlt sie, fällt `desktop.main()` auf das nackte `server.serve()` zurück — dann eben ohne `Cmd+Q`.

| Funktion | Zweck |
|---|---|
| `available()` | Ob `AppKit` und `PyObjCTools.AppHelper` importierbar sind |
| `run(console, port, open_browser=True)` | Baut `NSApplication` + Menüleiste + Delegate, startet `server.serve()` im Thread, dreht die Event-Loop; liefert den Rückgabewert von `serve()` |
| `_build_menu(AppKit, app)` | Menü *Report öffnen* (`Cmd+R`), *Ausblenden*, *Beenden* (`Cmd+Q`). Ohne Menüeintrag gibt es kein `Cmd+Q` — der Kurzbefehl hängt am Eintrag, nicht an der Anwendung |

**Gotchas:**

- `applicationShouldTerminate_` hält den Server über `server.request_stop()` an und wartet auf den Thread, statt den Prozess abzuschiessen. Läuft ein Scan, kommt vorher ein `NSAlert`; „Abbrechen" liefert `NSTerminateCancel` und die App bleibt stehen.
- Die Delegate-Referenz muss **festgehalten** werden (hier in einem Dict). `setDelegate_` hält das Objekt nicht am Leben; ohne eigene Referenz räumt der Python-GC es ab und der erste `Cmd+Q` läuft ins Leere.
- Endet der Server von sich aus (Knopf **Beenden** in der Oberfläche, oder Startfehler), beendet der Worker-Thread die App über `AppHelper.callAfter(NSApp().terminate_, None)` — `terminate_` gehört auf den Hauptthread.
- `tracktab.spec` setzt dazu `NSPrincipalClass = NSApplication` und listet `app.macapp` unter `hiddenimports` (das Modul wird nur dynamisch importiert).

#### `app/cli.py`

argparse-Dispatcher; Subcommands entsprechen 1:1 der Befehlsübersicht oben.

| Funktion | Zweck |
|---|---|
| `_progress()` | Baut eine `rich.progress.Progress`-Leiste (Spinner, Balken, M-von-N, Prozent, Zeit) |
| `cmd_scan(args)` | Verdrahtet `jobs.run_scan(on_progress=...)` mit der Fortschrittsleiste; Balken erscheint erst in der „analysieren"-Phase, sobald `total` bekannt ist |
| `cmd_check(args)` | Ruft `analyzer.analyse_file` direkt auf, **komplett am DB-/Scan-Pfad vorbei** — reines Ad-hoc-Diagnosewerkzeug, Ergebnis wird nie gespeichert |
| `_print_detail(row)` | Rendert eine vollständige Analyse als `rich.Table`; unterscheidet lossless-/lossy-Darstellung nach `codec_family`; Lautheitszeile ausdrücklich als „Hinweis, kein Urteil" beschriftet |
| `_print_summary(conn)` | Verdikt-Zusammenfassungstabelle (genutzt von `scan`, `stats`, `reclassify`) |
| `cmd_stats`, `cmd_report`, `cmd_serve`, `cmd_reclassify`, `cmd_calibrate` | Dünne Wrapper über die jeweiligen Modulfunktionen |
| `cmd_recheck_tags(args)` | Wrapper über `taganomaly.recheck_all()`, analog zu `cmd_reclassify` — Zusammenfassung nach Code sortiert, danach `report.build_all()` |
| `build_parser()` | Kompletter argparse-Baum aller Subcommands |
| `main(argv=None)` | Einstiegspunkt (`python -m app`); `KeyboardInterrupt` → Exit-Code 130 |

#### `app/webui/`

Vier Quelldateien, von `report._template()` per reiner String-Ersetzung (kein Build-Schritt/Bundler) zu einer einzigen Datei zusammengefügt:

| Datei | Rolle |
|---|---|
| `index.html` | Seiten-Grundgerüst mit den Platzhaltern `<!--__CSS__-->`, `<!--__JS__-->`, `/*__META__*/null`, `/*__DATA__*/null` |
| `app.css` | Gesamtes Styling, wird in den `<head>` der finalen HTML inline eingefügt |
| `i18n/de.js` | Deutsche UI-Texte als `I18N_DE`-Objekt, vor `app.js` in denselben `<script>`-Block eingebettet (muss vor `app.js` geladen sein) |
| `app.js` | Gesamte Client-Logik: Tabellen-Rendering/Sortierung/Filter, Wellenform je Bibliothekszeile (Bearbeiten-Ansicht, `waveViews`) + eigenstaendiger Einzelpruefungs-Player (`players`), globaler Mediaplayer + Warteschlange (`queueState`, beide Ansichten, siehe [Der globale Mediaplayer — Interna](#der-globale-mediaplayer--interna)), Einstellungen-Rendering (konsumiert das `GROUPS`-Schema), Drag-&-Drop, `localStorage`-Fallback ohne Server, Shop-Suchtext-Aufbereitung (DJ-Pool-Zusatz-Entfernung), Scan-Steuerung, Übersetzungen (`t()`/`resolveLang()`, siehe Einstellung `ui_language`), Fortschritts-Toasts (`progressToast()`, u. a. bei „Dateien öffnen", Sammel-Neuanalyse, Rekordbox-/Music-Abgleich) |

Nicht im Detail dokumentiert (JS/CSS-Interna außerhalb des Scopes dieses Dokuments) — wer den `localStorage`-Fallback oder die Shop-Suchtext-Logik ändern will, muss direkt in `app.js` nachsehen, dort existiert keine Python-Entsprechung.

`app/webui/sw.js` (Service Worker fuer PWA-Installierbarkeit) gehoert bewusst **nicht** zu den obigen Quelldateien — es wird unter `/sw.js` live von der Platte gelesen statt in die Single-File-`report.html` eingebettet. Siehe [PWA / Installierbarkeit — Interna](#pwa--installierbarkeit--interna).

---

## Bekannte Unstimmigkeiten

Aktuell keine offenen Punkte. Weitere hier ergänzen, sobald sie beim Arbeiten am Code auffallen — dieser Abschnitt ist bewusst ein lebendes To-do, kein abgeschlossener Review.


