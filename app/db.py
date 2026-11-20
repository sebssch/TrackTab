"""
SQLite-Ergebnisspeicher mit Cache.

Der Cache-Schluessel ist (Pfad, Groesse, mtime). Ein zweiter Lauf analysiert
damit nur neue oder geaenderte Dateien.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from . import config as cfgmod

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    size            INTEGER,
    mtime           REAL,
    analyzed_at     REAL,
    status          TEXT,
    error           TEXT,
    duration_s      REAL,
    declared_kbps   INTEGER,
    bitrate_mode    TEXT,
    sample_rate     INTEGER,
    channels        INTEGER,
    encoder         TEXT,
    lame_lowpass_hz INTEGER,
    artist          TEXT,
    title           TEXT,
    album           TEXT,
    cutoff_hz       REAL,
    steepness_db    REAL,
    is_brickwall    INTEGER,
    at_nyquist      INTEGER,
    gated_blocks    INTEGER,
    total_blocks    INTEGER,
    measured_kbps   INTEGER,
    declared_class  INTEGER,
    class_steps     INTEGER,
    verdict         TEXT,
    confidence      REAL,
    reasons         TEXT,
    spectrum        TEXT,
    codec           TEXT,
    codec_family    TEXT,
    raw_cutoff_hz   REAL,
    plateau_db      REAL,
    edge_segments   TEXT,
    integrated_lufs REAL,
    true_peak_dbtp  REAL,
    lra_lu          REAL,
    file_hash       TEXT,
    tag_issues      TEXT,
    key             TEXT,
    key_raw         TEXT,
    audio_hash      TEXT,
    cover_hash      TEXT,
    compilation     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_verdict ON files(verdict);
CREATE INDEX IF NOT EXISTS idx_cutoff  ON files(cutoff_hz);

-- Manuell ausgeblendete Dateien. Bewusst eine eigene Tabelle: die
-- Entscheidung des Nutzers soll einen erneuten Scan ueberleben, der die
-- Zeilen in 'files' komplett ersetzt.
CREATE TABLE IF NOT EXISTS ignored (
    path TEXT PRIMARY KEY,
    ts   REAL
);

-- Nur noch eine Migrations-Bruecke: die frueheren Merklisten (list1..list3)
-- lebten hier, bevor sie zu markierten Playlisten wurden (playlists.fav_slot,
-- siehe _migrate_favorites_to_playlists()). Bleibt in der Schema-Erstellung
-- stehen, weil die noch aeltere 'solved'-Migration (_migrate_solved_to_favorites,
-- fuer DBs von vor den Merklisten) hier hineinschreibt, bevor der Inhalt
-- weiter nach 'playlists' wandert -- danach wird die Tabelle geloescht.
CREATE TABLE IF NOT EXISTS favorites (
    path    TEXT,
    list_id TEXT,
    ts      REAL,
    PRIMARY KEY (path, list_id)
);

-- Manuell als Fehlalarm bestaetigte Faelle: die Messung lag daneben, der
-- Track ist in Ordnung. Eigene Tabelle wie 'ignored' — schliesst sich mit
-- diesem aus, ein Track ist hoechstens eines von beiden.
CREATE TABLE IF NOT EXISTS corrected (
    path TEXT PRIMARY KEY,
    ts   REAL
);

-- Manuell als "kein Duplikat" bestaetigte Pfade innerhalb einer
-- Duplikat-Gruppe (z.B. derselbe Track auf zwei Alben). Eigene Tabelle wie
-- 'corrected', aber ohne Exklusivitaet zu ignored/favorites/corrected -- ein
-- bestaetigter Nicht-Duplikat-Track bleibt unabhaengig davon ausblendbar
-- oder korrigierbar. Gruppen-Zugehoerigkeit (r.dg in app.js) ist ephemer
-- (pro Seitenladung neu vergeben), deshalb pfadbasiert statt gruppenbasiert:
-- eine Gruppe gilt im Client nur als bestaetigt, wenn ALLE ihre aktuell
-- lebenden Mitgliedspfade hier stehen.
CREATE TABLE IF NOT EXISTS dup_dismissed (
    path TEXT PRIMARY KEY,
    ts   REAL
);

-- Einzeln ignorierte Auffaelligkeiten je Track (Code = Auffaelligkeits-Kennung
-- wie 'album_cover_mismatch'). Reine Nutzer-Entscheidung, mehrere Zeilen je Pfad.
CREATE TABLE IF NOT EXISTS ignored_issues (
    path TEXT,
    code TEXT,
    ts   REAL,
    PRIMARY KEY (path, code)
);

-- Dauerhaft ausgeblendete Zusammenfuehrungs-Vorschlaege (Genre/Album/
-- Interpret-Uebersicht, "aehnliche Werte zusammenfuehren?"). Nicht
-- pfadbasiert wie dup_dismissed, sondern je Werte-Paar -- value_a/value_b
-- werden vom Aufrufer kanonisch sortiert uebergeben, damit ein Paar
-- unabhaengig von der Reihenfolge dieselbe Zeile trifft.
CREATE TABLE IF NOT EXISTS merge_dismissed (
    field    TEXT,
    value_a  TEXT,
    value_b  TEXT,
    ts       REAL,
    PRIMARY KEY (field, value_a, value_b)
);

-- Huellkurven fuer den Player. Reiner Zwischenspeicher: die Berechnung
-- kostet je Track rund eine Sekunde, das soll nicht bei jedem Abspielen
-- erneut anfallen.
CREATE TABLE IF NOT EXISTS waveform (
    path  TEXT PRIMARY KEY,
    mtime REAL,
    peaks TEXT,
    ts    REAL
);

-- Kleinstes und groesstes Tempo im Beatgrid eines Tracks, beim Oeffnen im DJ-Editor
-- gemerkt. Grundlage fuer die rote BPM-Spalte: das Grid selbst liegt in Rekordbox'
-- Analysedateien, die sich nicht fuer die ganze Liste lesen lassen.
CREATE TABLE IF NOT EXISTS grid_tempo (
    path TEXT PRIMARY KEY,
    lo   REAL,
    hi   REAL,
    ts   REAL,
    seq  TEXT
);

-- Farbige Gesamtansicht fuer Bearbeiten und Player (wavedetail.overview()), wie
-- 'waveform' ein reiner Cache. Nur der eingestellte Stil.
CREATE TABLE IF NOT EXISTS waveform_color (
    path  TEXT PRIMARY KEY,
    mtime REAL,
    style TEXT,
    data  BLOB,
    ts    REAL
);

-- Bulk-Cache: welche Pfade zuletzt in der Rekordbox-Sammlung gefunden
-- wurden. Kein Nutzer-Toggle wie ignored/favorites/corrected, sondern ein
-- Abgleichsergebnis -- wird bei jedem vollen Abgleich komplett ersetzt
-- (sync_rekordbox_presence) und nach einem erfolgreichen Playlist-Push
-- punktuell ergaenzt (mark_rekordbox_present), damit der Haken sofort
-- erscheint statt erst beim naechsten Abgleich.
CREATE TABLE IF NOT EXISTS rekordbox (
    path TEXT PRIMARY KEY,
    ts   REAL
);

-- Tracks, deren Tags (Genre/Interpret/Album) beim Zusammenfuehren geaendert
-- wurden und in Rekordbox noch auf dem alten Stand stehen. Der Abgleich laeuft
-- gebuendelt ueber den Knopf "Rekordbox abgleichen" (ein Oeffnen, ein Backup
-- von master.db), nicht je Zusammenfuehrung.
-- 'prior_path': bei umbenannten Dateien der Pfad, unter dem Rekordbox den
-- Track noch kennt (NULL = Pfad unveraendert, nur Tags nachzuziehen).
CREATE TABLE IF NOT EXISTS rekordbox_pending (
    path       TEXT PRIMARY KEY,
    ts         REAL,
    prior_path TEXT
);

-- Freigegebene Stem-Dateien (Acapella/Instrumental), die noch nicht in
-- Rekordbox stehen, weil es beim Freigeben lief. 'path' ist die Stem-Datei,
-- 'source_path' der Haupttrack, dessen Beatgrid sie bekommt. Abgearbeitet wird
-- nur ueber "Rekordbox abgleichen" (kein Hintergrund-Watcher).
CREATE TABLE IF NOT EXISTS rekordbox_stem_pending (
    path        TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    ts          REAL
);

-- In den Papierkorb gelegte Tracks, die noch in Rekordbox' Sammlung stehen,
-- weil Rekordbox beim Loeschen lief. Abgearbeitet ueber "Rekordbox abgleichen".
-- Bewusst weder in _PATH_TABLES noch in _PRUNE_TABLES: die Datei ist weg, der
-- Eintrag soll genau deshalb stehen bleiben. Liegt die Datei beim Abarbeiten
-- wieder da (aus dem Papierkorb zurueckgeholt), wird nichts entfernt.
CREATE TABLE IF NOT EXISTS rekordbox_remove_pending (
    path TEXT PRIMARY KEY,
    ts   REAL
);

-- Datum, an dem ein Track laut Music.app zur Bibliothek hinzugefuegt wurde.
-- Eigener Cache wie 'rekordbox', bewusst getrennt davon (kein gemeinsamer
-- Abgleich): media.music_file_tracks() liest die ganze Music.app-Bibliothek in
-- einem Bulk-Aufruf, sync_music_added() ersetzt den Inhalt komplett bei
-- jedem Abgleich. added_ts ist ein Unix-Timestamp, ts das Abgleichsdatum.
CREATE TABLE IF NOT EXISTS music_added (
    path     TEXT PRIMARY KEY,
    added_ts REAL,
    ts       REAL
);

-- Fallback-Ablage fuer ein aus Music.app gelesenes Cover, wenn write_cover()
-- es nicht in die Datei einbetten kann (z.B. WAV/AIFF, wo Music.app selbst
-- die Datei nicht anfasst). /api/cover faellt darauf zurueck, wenn die
-- Datei selbst kein Cover hat, siehe coverfill.py.
CREATE TABLE IF NOT EXISTS cover_cache (
    path TEXT PRIMARY KEY,
    mime TEXT,
    data BLOB,
    ts   REAL
);

-- Genre-Vorschlaege, Stimmung und Energie (app/genremood.py). Abgeleitete
-- Messung wie 'waveform', kein Datei-Inhalt -- deshalb eigene Tabelle statt
-- Spalten in 'files' (die spiegeln die Datei wider und werden je Scan neu
-- gelesen). genres = JSON [[Stil, Wahrscheinlichkeit], ...] absteigend,
-- moods = JSON {aggressive, happy, party, relaxed, sad, danceability},
-- energy = 1..10. Kostet je Track mehrere Sekunden -- laeuft nur auf
-- Anforderung, nie im Scan.
CREATE TABLE IF NOT EXISTS genremood (
    path   TEXT PRIMARY KEY,
    genres TEXT,
    moods  TEXT,
    energy REAL,
    model  TEXT,
    ts     REAL
);

-- Beatgrid und Phrasen aus der eigenen Analyse (app/phrases.py). Abgeleitete
-- Messung wie 'genremood', nie in der Datei. Rekordbox-Phrasen stehen hier
-- nicht: die liest rekordbox.track_state() bei Bedarf frisch.
-- changes = JSON [[Sekunde, BPM], ...], phrases = JSON [{start, end, kind, num}],
-- beats = JSON [[Millisekunde, Schlagnummer], ...].
CREATE TABLE IF NOT EXISTS phrases (
    path       TEXT PRIMARY KEY,
    mode       TEXT,
    used       TEXT,
    bpm        REAL,
    confidence REAL,
    first_beat REAL,
    downbeat   REAL,
    changes    TEXT,
    phrases    TEXT,
    mood       INTEGER,
    uncertain  INTEGER,
    version    TEXT,
    ts         REAL,
    beats      TEXT,
    grid_origin    TEXT,
    phrases_origin TEXT
);

-- Was Auto Cue zuletzt je Track gesetzt hat (app/autocue/, server._post_autocue()):
-- cues = JSON wie dj_track.cues. Nur Herkunftsnachweis -- das Training
-- (app/autocue/training/) laesst Cues an diesen Stellen als Labels aus, sonst lernte
-- das Modell aus seinen eigenen Ergebnissen, sobald sie in Rekordbox stehen. Nicht in
-- _PRUNE_TABLES (wie dj_track: Nutzerdaten).
CREATE TABLE IF NOT EXISTS autocue_log (
    path TEXT PRIMARY KEY,
    cues TEXT,
    ts   REAL
);

-- Bearbeitungen aus der DJ-Ansicht (app/webui/dj.js): Beatgrid, Phrasen und Cues
-- als JSON, je Feld NULL = unbearbeitet (dann gilt Rekordbox bzw. die eigene
-- Analyse). Nutzerdaten, deshalb nicht in _PRUNE_TABLES. Geht nie in die Datei,
-- nach Rekordbox nur auf ausdruecklichen Knopfdruck. Alle Zeiten liegen auf der
-- eigenen Zeitachse (ffmpeg). rb_base = JSON {"rb": {Feld: Pruefsumme}, "pushed":
-- {Feld: Pruefsumme}}: Stand von Rekordbox beim letzten Abgleich und zuletzt dorthin
-- geschriebener eigener Stand, daran haengt die Rueckfrage bei Abweichungen.
-- Stand von Rekordbox, festgehalten beim Abgleich (je Feld JSON auf Rekordbox' Zeitachse,
-- NULL = dort noch nichts). Danach gilt er in der DJ-Ansicht statt des Live-Stands, spaetere
-- Aenderungen dort kommen nur per Knopf herueber. seen: Pruefsummen je Feld, was der Nutzer
-- zuletzt gesehen hat; changed: Felder, die dort seither abweichen; stamp: Aenderungsstand
-- des Tracks in Rekordbox, spart das Lesen unveraenderter Tracks.
CREATE TABLE IF NOT EXISTS rb_snapshot (
    path    TEXT PRIMARY KEY,
    grid    TEXT,
    phrases TEXT,
    cues    TEXT,
    seen    TEXT,
    changed TEXT,
    stamp   TEXT,
    ts      REAL
);

CREATE TABLE IF NOT EXISTS dj_track (
    path    TEXT PRIMARY KEY,
    grid    TEXT,
    phrases TEXT,
    cues    TEXT,
    ts      REAL,
    rb_base TEXT,
    grid_lock INTEGER NOT NULL DEFAULT 0,
    phrases_lock INTEGER NOT NULL DEFAULT 0
);

-- Eigene Tags des Nutzers (frei benannt, z. B. "Warm-up", "Peak Time"),
-- vergeben im Tags-Dialog. Reine Nutzer-Angabe wie favorites -- deshalb eigene
-- Tabelle, nie in der Datei. Mehrere Zeilen je Pfad (eine je Tag).
CREATE TABLE IF NOT EXISTS user_tags (
    path TEXT NOT NULL,
    tag  TEXT NOT NULL,
    ts   REAL,
    PRIMARY KEY (path, tag)
);

-- Rohes Ereignisprotokoll fuer die spaetere Statistik-Ansicht (Issue #15/#16):
-- aktuell nur qualifizierende Wiedergaben (>= 30s tatsaechliche Hoerzeit) und
-- Neuzugaenge in die Music-Bibliothek, siehe app.js/server.py. 'kind' ist ein
-- freies Textfeld statt einer Tabelle je Ereignisart, damit sich spaeter
-- weitere Ereignisse (Cover, Tags, Papierkorb, Rekordbox, ...) ohne Schema-
-- Aenderung ergaenzen lassen. Kein Eintrag in _PATH_TABLES: das dortige
-- Delete-vor-Update wuerde bei mehreren Zeilen je Pfad Verlauf vernichten,
-- move_path() schreibt den Pfad hier separat ohne Delete um. Kein Eintrag in
-- _PRUNE_TABLES: Verlauf soll eine spaeter geloeschte Datei ueberleben.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL,
    kind       TEXT,
    path       TEXT,
    duration_s REAL,
    in_music   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE INDEX IF NOT EXISTS idx_events_path ON events(path);

-- Playlisten und Ordner in EINER Tabelle, unterschieden ueber 'kind'
-- ('folder' | 'playlist' | 'smart') -- so wie Rekordbox es selbst macht
-- (DjmdPlaylist.Attribute 0/1/4). Drei getrennte Tabellen wuerden bei jedem
-- Baum-Durchlauf ein UNION erzwingen. 'parent_id' NULL = oberste Ebene unter
-- dem TrackTab-Wurzelknoten. 'rules' haelt das Regel-JSON der Smart
-- Playlists, 'color' ein Token (fl1..fl12) wie bei den Merklisten statt eines
-- freien Hex-Werts -- nur so bleibt der Kontrast in Hell und Dunkel
-- garantiert. 'system' markiert feste, weder loesch- noch umbenennbare
-- Knoten. 'fav_slot' markiert eine normale Playlist als Merkliste (NULL =
-- keine, sonst fester Slot 1..4 fuer eine stabile Reihenfolge in Schnell-
-- Filter-Tabs und an den Zeilen-Knoepfen -- Nachfolger der frueheren, fest
-- benannten Merklisten-Slots list1..list3 in der inzwischen entfernten
-- 'favorites'-Tabelle).
-- Alle uebrigen Spalten stehen bewusst schon jetzt hier, auch die noch
-- ungenutzten: 'CREATE TABLE IF NOT EXISTS' legt eine Spalte auf einer
-- bestehenden Datenbank spaeter NICHT nach, und die Migration in
-- _MIGRATIONS deckt nur die 'files'-Tabelle ab -- 'fav_slot' kam SPAETER
-- dazu und braucht deshalb eine eigene ALTER-TABLE-Migration, siehe
-- _ensure_playlist_columns().
CREATE TABLE IF NOT EXISTS playlists (
    id        TEXT PRIMARY KEY,
    parent_id TEXT,
    kind      TEXT NOT NULL,
    name      TEXT NOT NULL,
    icon      TEXT,
    color     TEXT,
    rules     TEXT,
    system    INTEGER DEFAULT 0,
    seq       INTEGER DEFAULT 0,
    ts        REAL,
    fav_slot  INTEGER,
    locked    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_playlists_parent ON playlists(parent_id, seq);

-- Zuordnung Track -> Playlist mit manueller Reihenfolge ('pos'). Die gibt es
-- sonst nirgends im Tool: die Haupttabelle kennt nur Spaltensortierung, und
-- 'favorites' hat bewusst keine Ordnung. Der Aufbau (playlist_id, path)
-- spiegelt 'favorites' (path, list_id) -- deshalb genuegt in move_path() der
-- Standardweg, ein UPDATE ... WHERE path = ? verschiebt alle Zeilen eines
-- Pfades auf einmal, ohne Sonderfall wie bei 'events'.
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL,
    path        TEXT NOT NULL,
    pos         INTEGER NOT NULL,
    ts          REAL,
    PRIMARY KEY (playlist_id, path)
);
CREATE INDEX IF NOT EXISTS idx_playlist_items_path ON playlist_items(path);

-- Verknuepfte Playlisten (playlistsync.py): je TrackTab-Liste hoechstens eine
-- Music.app- und eine Rekordbox-Playlist. state: ok / pending (Rekordbox lief)
-- / broken (fremde Playlist weg).
CREATE TABLE IF NOT EXISTS playlist_links (
    playlist_id TEXT NOT NULL,
    source      TEXT NOT NULL,
    ext_id      TEXT NOT NULL,
    ext_name    TEXT,
    state       TEXT DEFAULT 'ok',
    error       TEXT,
    synced_ts   REAL,
    PRIMARY KEY (playlist_id, source)
);
-- Letzter abgeglichener Stand je Seite (side = tracktab / music / rekordbox).
-- Als Zeilen statt JSON, damit move_path() die Pfade mitnimmt (_PATH_TABLES):
-- sonst saehe ein von Music.app umsortierter Track wie "entfernt + neu" aus.
CREATE TABLE IF NOT EXISTS playlist_link_items (
    playlist_id TEXT NOT NULL,
    side        TEXT NOT NULL,
    path        TEXT NOT NULL,
    PRIMARY KEY (playlist_id, side, path)
);
CREATE INDEX IF NOT EXISTS idx_playlist_link_items_path ON playlist_link_items(path);
"""

_COLUMNS = [
    "path", "size", "mtime", "analyzed_at", "status", "error", "duration_s",
    "declared_kbps", "bitrate_mode", "sample_rate", "channels", "encoder",
    "lame_lowpass_hz", "artist", "title", "album", "cutoff_hz", "steepness_db",
    "is_brickwall", "at_nyquist", "gated_blocks", "total_blocks",
    "measured_kbps", "declared_class", "class_steps", "verdict", "confidence",
    "reasons", "spectrum", "codec", "codec_family", "raw_cutoff_hz",
    "plateau_db", "edge_segments",
    "integrated_lufs", "true_peak_dbtp", "lra_lu",
    "genre", "bpm", "has_cover",
    "album_artist", "composer", "year", "comment", "track_no", "track_total",
    "file_hash", "tag_issues", "key", "key_raw", "audio_hash", "cover_hash",
    "compilation",
]

# Spalten, die nach dem urspruenglichen Release der 'files'-Tabelle
# hinzukamen. 'CREATE TABLE IF NOT EXISTS' legt sie auf einer bereits
# existierenden DB-Datei NICHT nach -- anders als bei reinen
# Parameteraenderungen reicht ein 'scan --force' hier nicht aus.
_MIGRATIONS = [
    ("codec", "TEXT"),
    ("codec_family", "TEXT"),
    ("raw_cutoff_hz", "REAL"),
    ("integrated_lufs", "REAL"),
    ("true_peak_dbtp", "REAL"),
    ("lra_lu", "REAL"),
    ("genre", "TEXT"),
    ("bpm", "REAL"),
    ("has_cover", "INTEGER"),
    ("album_artist", "TEXT"),
    ("composer", "TEXT"),
    ("year", "INTEGER"),
    ("comment", "TEXT"),
    ("track_no", "INTEGER"),
    ("track_total", "INTEGER"),
    ("file_hash", "TEXT"),
    ("tag_issues", "TEXT"),
    ("key", "TEXT"),
    ("key_raw", "TEXT"),
    ("plateau_db", "REAL"),
    ("edge_segments", "TEXT"),
    ("audio_hash", "TEXT"),
    ("cover_hash", "TEXT"),
    ("compilation", "INTEGER"),
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(files)")}
    for name, sqltype in _MIGRATIONS:
        if name not in existing:
            conn.execute(f"ALTER TABLE files ADD COLUMN {name} {sqltype}")
    conn.commit()


def _ensure_playlist_columns(conn: sqlite3.Connection) -> None:
    """'fav_slot' kam nach dem urspruenglichen Release von 'playlists' dazu --
    wie bei _ensure_columns()/'files' legt 'CREATE TABLE IF NOT EXISTS' sie auf
    einer bestehenden Datenbank nicht nach."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(playlists)")}
    if "fav_slot" not in existing:
        conn.execute("ALTER TABLE playlists ADD COLUMN fav_slot INTEGER")
    if "locked" not in existing:
        # Vom Nutzer gesetzte Sperre (Name, Ort, Inhalt), nicht zu verwechseln
        # mit 'system' (feste Listen).
        conn.execute("ALTER TABLE playlists ADD COLUMN locked INTEGER DEFAULT 0")
    dj_track = {row["name"] for row in conn.execute("PRAGMA table_info(dj_track)")}
    if "rb_base" not in dj_track:
        conn.execute("ALTER TABLE dj_track ADD COLUMN rb_base TEXT")
    if "seq" not in {row["name"] for row in conn.execute("PRAGMA table_info(grid_tempo)")}:
        conn.execute("ALTER TABLE grid_tempo ADD COLUMN seq TEXT")
    if "grid_lock" not in dj_track:
        conn.execute("ALTER TABLE dj_track ADD COLUMN grid_lock INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE dj_track ADD COLUMN phrases_lock INTEGER NOT NULL DEFAULT 0")
        # Frueher gab es eine Sperre fuer beides, sie gilt jetzt fuer beide.
        if "analysis_lock" in dj_track:
            conn.execute("UPDATE dj_track SET grid_lock = analysis_lock, phrases_lock = analysis_lock")
    pending = {row["name"] for row in conn.execute("PRAGMA table_info(rekordbox_pending)")}
    if "prior_path" not in pending:
        conn.execute("ALTER TABLE rekordbox_pending ADD COLUMN prior_path TEXT")
    phrases = {row["name"] for row in conn.execute("PRAGMA table_info(phrases)")}
    if "beats" not in phrases:
        conn.execute("ALTER TABLE phrases ADD COLUMN beats TEXT")
    # Herkunft je Feld (app/gridstore.py): NULL = eigene Messung von vor der Spalte.
    for col in ("grid_origin", "phrases_origin"):
        if col not in phrases:
            conn.execute(f"ALTER TABLE phrases ADD COLUMN {col} TEXT")
    conn.commit()


def _migrate_solved_to_favorites(conn: sqlite3.Connection) -> None:
    """Einmalige Uebernahme: die alte 'solved'-Tabelle (Erledigt) wird zu
    Slot 'list1' der Merklisten-Bruecke 'favorites'. Laeuft nur, solange
    'solved' noch existiert -- danach ist die Tabelle entfernt und die
    Funktion wird beim naechsten connect() zum No-Op. 'favorites' selbst
    wandert gleich im Anschluss ueber _migrate_favorites_to_playlists()
    weiter nach 'playlists'."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "solved" not in tables:
        return
    conn.execute(
        "INSERT OR IGNORE INTO favorites (path, list_id, ts) "
        "SELECT path, 'list1', ts FROM solved")
    conn.execute("DROP TABLE solved")
    conn.commit()


def _migrate_favorites_to_playlists(conn: sqlite3.Connection) -> None:
    """Einmalige Uebernahme: die drei frueheren, fest benannten Merklisten-
    Slots (Tabelle 'favorites', Config 'favorite_lists') werden zu normalen,
    als Merkliste markierten Playlisten (playlists.fav_slot). Laeuft nur,
    solange 'favorites' noch existiert -- danach ist die Tabelle entfernt und
    die Funktion wird beim naechsten connect() zum No-Op.

    'BEGIN IMMEDIATE' serialisiert das gegen einen zweiten Prozess (z.B. der
    laufende Server UND ein parallel gestartetes 'report'), der im selben
    Moment ebenfalls zum ersten Mal connect()t -- ohne eigene Transaktion
    kann Python's sqlite3-Modul die vorherige DROP TABLE eines anderen
    Prozesses unter WAL sonst noch nicht sehen und legt Playlisten doppelt an.
    Zusaetzlich je Slot ein Blick, ob der fav_slot nicht schon vergeben ist --
    zweite Sicherung, falls doch zwei Prozesse gleichzeitig durchkommen."""
    conn.execute("BEGIN IMMEDIATE")
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "favorites" not in tables:
        conn.commit()
        return
    by_list: dict[str, set[str]] = {}
    for r in conn.execute("SELECT path, list_id FROM favorites"):
        by_list.setdefault(r["list_id"], set()).add(r["path"])
    # Der Vorgabewert selbst ist mit den Merklisten-Einstellungen aus
    # config.py verschwunden -- ein noch nicht angepasster Nutzer hatte ihn
    # nie in config.local.yaml stehen. Fester Rueckfall auf den historischen
    # Standard (list1 = die einst umbenannte 'Erledigt'-Liste), eine
    # config.local.yaml-Anpassung geht trotzdem vor.
    fallback_slots = [
        {"id": "list1", "name": "Merken", "color": "fl1"},
        {"id": "list2", "name": "", "color": "fl2"},
        {"id": "list3", "name": "", "color": "fl3"},
    ]
    slots = cfgmod.load().get("favorite_lists") or fallback_slots
    used_slots = {r["fav_slot"] for r in conn.execute(
        "SELECT fav_slot FROM playlists WHERE fav_slot IS NOT NULL")}
    now = time.time()
    for i, slot in enumerate(slots):
        name = str(slot.get("name") or "").strip()
        if not name or (i + 1) in used_slots:
            continue
        paths = by_list.get(str(slot.get("id") or ""), set())
        pid = "pl" + uuid.uuid4().hex[:10]
        conn.execute(
            "INSERT INTO playlists (id, parent_id, kind, name, icon, color, "
            "rules, system, seq, ts, fav_slot) VALUES "
            "(?, NULL, 'playlist', ?, NULL, ?, NULL, 0, 0, ?, ?)",
            (pid, name, slot.get("color") or None, now, i + 1))
        conn.executemany(
            "INSERT OR IGNORE INTO playlist_items (playlist_id, path, pos, ts) "
            "VALUES (?, ?, ?, ?)",
            [(pid, path, pos, now) for pos, path in enumerate(sorted(paths))])
    conn.execute("DROP TABLE favorites")
    conn.commit()


# Einrichtungsarbeit (Schema, Spaltenmigration, Seeds) genuegt einmal je
# Datenbankdatei und Prozess. Vorher lief sie bei JEDEM connect() -- und
# connect() steht in jedem einzelnen HTTP-Endpunkt, auch in denen, die der
# Player waehrend der Wiedergabe im Sekundentakt anfasst. Das waren pro
# Anfrage 18 DDL-Anweisungen, ein PRAGMA table_info, die Solved-Pruefung und
# 6 UPDATE auf playlists samt drei commit(), also Schreib-I/O fuer einen
# reinen Lesezugriff. Gemessen an einer echten quality.db (10.822 Zeilen):
# 0,44 ms gegen 0,16 ms fuer sqlite3.connect + SELECT.
_SETUP_DONE: set[str] = set()
_SETUP_LOCK = threading.Lock()


def _setup_once(conn: sqlite3.Connection, db_path: str) -> None:
    if db_path in _SETUP_DONE:
        return
    with _SETUP_LOCK:
        if db_path in _SETUP_DONE:
            return
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        _ensure_playlist_columns(conn)
        _migrate_solved_to_favorites(conn)
        _migrate_favorites_to_playlists(conn)
        _seed_system_playlists(conn)
        _SETUP_DONE.add(db_path)


def forget_setup(path: str | Path | None = None) -> None:
    """Vergisst, dass eine Datei bereits eingerichtet wurde. Pflicht, wenn
    die Datei unter uns ausgetauscht wird (backup.restore_backup) -- der
    eingespielte Stand kann ein aelteres Schema haben, das die Migration
    braucht. Ohne Argument wird alles vergessen."""
    with _SETUP_LOCK:
        if path is None:
            _SETUP_DONE.clear()
        else:
            _SETUP_DONE.discard(str(path))


def connect(cfg: dict | None = None, path: str | None = None) -> sqlite3.Connection:
    cfg = cfg or cfgmod.load()
    db_path = Path(path) if path else cfgmod.resolve(cfg["db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL: Leser blockieren Schreiber nicht mehr und umgekehrt. Erst damit
    # duerfen die Lesepfade im Server den globalen Handler-Lock ablegen --
    # der hat dort nichts geschuetzt, sondern nur alle Endpunkte
    # gegeneinander serialisiert (Audio-Range gegen Cover gegen Waveform).
    # synchronous=NORMAL ist unter WAL der uebliche Kompromiss: kein fsync je
    # commit, crash-sicher bis auf die jeweils letzte Transaktion.
    # Achtung: WAL legt quality.db-wal/-shm neben die Datei. Wer die Datei
    # roh ersetzt, muss beide mit entfernen (siehe backup.restore_backup).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _setup_once(conn, str(db_path))
    return conn


def cached_keys(conn: sqlite3.Connection) -> dict[str, tuple[int, float]]:
    """Alle bereits analysierten Dateien als {pfad: (groesse, mtime)}."""
    cur = conn.execute("SELECT path, size, mtime FROM files WHERE status = 'ok'")
    return {r["path"]: (r["size"], r["mtime"]) for r in cur}


def measurement_gaps(conn: sqlite3.Connection, min_duration_s: float,
                     spectral_marker: str) -> dict[str, tuple[bool, bool]]:
    """Pfad -> (cutoff_fehlt, lautheit_fehlt) fuer fertige Zeilen, denen eine
    Messung fehlt: Cutoff im Scan abgewaehlt (Grund = spectral_marker) bzw. kein
    Lautheitswert (NULL oder 0.0 -- die Vorgabe in analyse_file(); eine echte
    Messung ist nie genau 0 LUFS). Zu kurze Dateien bekommen nie Lautheit (analyse_file() endet
    vorher) und zaehlen deshalb nicht. Ein fehlgeschlagener Lautheitswert wird
    bewusst mitgezaehlt -- lieber erneut versuchen als still fehlen lassen."""
    cur = conn.execute(
        "SELECT path, instr(reasons, ?) > 0 AS cut,"
        " (COALESCE(integrated_lufs, 0) = 0 AND COALESCE(duration_s, 0) >= ?) AS loud"
        " FROM files WHERE status = 'ok'"
        " AND (instr(reasons, ?) > 0 OR (COALESCE(integrated_lufs, 0) = 0 AND COALESCE(duration_s, 0) >= ?))",
        (spectral_marker, min_duration_s, spectral_marker, min_duration_s))
    return {r["path"]: (bool(r["cut"]), bool(r["loud"])) for r in cur}


def is_current(cache: dict, path: str, size: int, mtime: float) -> bool:
    entry = cache.get(path)
    if entry is None:
        return False
    return entry[0] == size and abs(entry[1] - mtime) < 1.0


# Messungen am Audioinhalt, die keinen eigenen Dateistempel tragen (bzw. den
# der alten Datei): sie gelten nur fuer genau die Aufnahme, an der gemessen
# wurde. Bewusst NICHT dabei: dj_track und autocue_log (Nutzerdaten),
# rb_snapshot (Rekordbox' Stand, nicht unserer) und alles, was am Pfad statt
# am Inhalt haengt (rekordbox, music_added, Playlisten, Markierungen).
_AUDIO_TABLES = ("genremood", "phrases", "waveform", "waveform_color", "grid_tempo")


def drop_replaced_measurements(conn: sqlite3.Connection, rows: list[dict]) -> list[str]:
    """Verwirft die Messungen von Dateien, deren Audioinhalt sich geaendert hat.

    Anlass: wird eine Datei im Finder durch eine andere Fassung gleichen
    Namens ersetzt, sieht der Scan nur "neue Groesse/mtime" und misst die
    files-Zeile neu -- Genre/Stimmung und Phrasen/Beatgrid blieben aber die
    der alten Datei, und weil dort schon eine Zeile steht, mass sie niemand
    neu. Groesse/mtime taugen als Ausloeser nicht: jeder Tag-Edit aendert
    sie, und die Messungen kosten je Datei Sekunden.

    Verglichen wird deshalb files.audio_hash (media.audio_fingerprint()).
    Verworfen wird nur, wenn alte UND neue Kennung bekannt sind und sich
    unterscheiden -- eine Zeile aus der Zeit vor diesem Feature (NULL) oder
    ein gescheiterter ffmpeg-Lauf ('') loest nichts aus. Muss VOR dem
    Speichern der neuen Zeilen laufen. Ohne commit, das macht save().
    """
    fresh = {r["path"]: r.get("audio_hash") for r in rows if r.get("audio_hash")}
    if not fresh:
        return []
    paths = list(fresh)
    replaced: list[str] = []
    for i in range(0, len(paths), 500):
        chunk = paths[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT path, audio_hash FROM files WHERE path IN ({marks})", chunk):
            if r["audio_hash"] and r["audio_hash"] != fresh[r["path"]]:
                replaced.append(r["path"])
    for table in _AUDIO_TABLES:
        conn.executemany(f"DELETE FROM {table} WHERE path = ?", [(p,) for p in replaced])
    return replaced


def paths_without_audio_hash(conn: sqlite3.Connection) -> list[str]:
    """Fertige Zeilen, deren Audio-Kennung noch nie gebildet wurde (vor diesem
    Feature gescannt). '' heisst "versucht, ging nicht" und zaehlt nicht."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE audio_hash IS NULL AND status = 'ok'")]


def set_audio_hashes(conn: sqlite3.Connection, hashes: dict[str, str]) -> None:
    conn.executemany("UPDATE files SET audio_hash = ? WHERE path = ?",
                     [(h, p) for p, h in hashes.items()])
    conn.commit()


def save(conn: sqlite3.Connection, rows: list[dict],
         drop_replaced: bool = False) -> list[str]:
    """Zeilen speichern. drop_replaced=True (Scan, Neu-Analyse) verwirft vorher
    die Messungen ersetzter Dateien, siehe drop_replaced_measurements().
    Liefert deren Pfade."""
    if not rows:
        return []
    replaced = drop_replaced_measurements(conn, rows) if drop_replaced else []
    now = time.time()
    payload = []
    for r in rows:
        r = dict(r)
        r["analyzed_at"] = now
        r["reasons"] = json.dumps(r.get("reasons", []), ensure_ascii=False)
        r["spectrum"] = json.dumps(r.get("spectrum", []))
        r["edge_segments"] = json.dumps(r.get("edge_segments", []))
        r["tag_issues"] = json.dumps(r.get("tag_issues", []), ensure_ascii=False)
        payload.append(tuple(r.get(c) for c in _COLUMNS))
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO files ({','.join(_COLUMNS)}) VALUES ({placeholders})",
        payload,
    )
    conn.commit()
    return replaced


_TAG_FIELDS = ("artist", "title", "album", "album_artist", "composer",
               "genre", "year", "bpm", "key", "key_raw", "comment", "track_no", "track_total",
               "compilation")


def update_tags(conn: sqlite3.Connection, path: str, fields: dict) -> None:
    """Gezieltes Update nach einem Tag-Edit -- kein voller Analyse-Datensatz
    noetig, nur die per Editor geaenderten Felder."""
    cols = [c for c in _TAG_FIELDS if c in fields]
    if not cols:
        return
    values = [fields[c] for c in cols] + [path]
    conn.execute(
        f"UPDATE files SET {', '.join(c + ' = ?' for c in cols)} WHERE path = ?",
        values,
    )
    conn.commit()


def paths_without_key(conn: sqlite3.Connection) -> list[str]:
    """Zeilen, bei denen die Tonart noch nie gelesen wurde (key_raw IS NULL,
    z.B. vor diesem Feature gescannt). Ein leerer Text heisst "gelesen, keine
    da"."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE key_raw IS NULL")]


def paths_without_cover_hash(conn: sqlite3.Connection) -> list[str]:
    """Zeilen mit Cover, deren Bild-Hash noch nie gelesen wurde (NULL, vor
    diesem Feature gescannt) oder noch im alten Format steht (MD5 der Bytes,
    32 Zeichen statt der 384 des Mini-Bilds, siehe coverhash.py). Ohne Cover steht sofort ''
    (siehe set_cover_hashes)."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE cover_hash IS NULL "
        "OR (cover_hash != '' AND length(cover_hash) != 384)")]


def paths_without_compilation(conn: sqlite3.Connection) -> list[str]:
    """Zeilen, deren Sampler-Markierung (compilation) noch nie gelesen wurde
    (NULL, vor diesem Feature gescannt)."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE compilation IS NULL")]


def set_compilations(conn: sqlite3.Connection, flags: dict[str, int]) -> int:
    changed = 0
    for path, flag in flags.items():
        changed += conn.execute(
            "UPDATE files SET compilation = ? WHERE path = ?", (int(flag), path)).rowcount
    conn.commit()
    return changed


def set_cover_hashes(conn: sqlite3.Connection, hashes: dict[str, str]) -> int:
    changed = 0
    for path, h in hashes.items():
        changed += conn.execute(
            "UPDATE files SET cover_hash = ? WHERE path = ?", (h, path)).rowcount
    conn.commit()
    return changed


def set_keys(conn: sqlite3.Connection, keys: dict[str, tuple[str, str]]) -> int:
    """Tonart fuer mehrere Pfade schreiben: {pfad: (camelot, roh)}. 'roh' ist
    der Tag-Wert, wie er in der Datei steht. Gibt die Zahl der Zeilen zurueck."""
    changed = 0
    for path, (key, raw) in keys.items():
        changed += conn.execute(
            "UPDATE files SET key = ?, key_raw = ? WHERE path = ?",
            (key, raw, path)).rowcount
    conn.commit()
    return changed


def update_tag_issues(conn: sqlite3.Connection, path: str, issues: list[dict]) -> None:
    """Gezieltes Update nach einem Auffaelligkeiten-Fix oder recheck-tags --
    kein voller Analyse-Datensatz noetig, nur die neu bewertete Liste."""
    conn.execute("UPDATE files SET tag_issues = ? WHERE path = ?",
                 (json.dumps(issues, ensure_ascii=False), path))
    conn.commit()


def refresh_stat(conn: sqlite3.Connection, path: str) -> None:
    """Groesse und mtime aus der Datei nachziehen, nachdem wir sie selbst
    geschrieben haben (Tags, Cover).

    Zwei Gruende: der Cache-Schluessel ist (Pfad, Groesse, mtime) -- ohne das
    haelt der naechste Scan die Datei fuer veraendert und misst sie ohne Not
    neu, obwohl ein Tag-Edit den Audiostrom nicht anfasst. Und die Groesse ist
    eins der Merkmale, an denen scanner.find_moved() eine verschobene Datei
    wiedererkennt; eine veraltete Groesse macht dieses Merkmal wertlos.
    """
    try:
        st = os.stat(path)
    except OSError:
        return
    # Cover kann sich gerade geaendert haben (Tags-Dialog, Online-Vorschlag,
    # Verkleinern): Hash fuer die Auffaelligkeit duplicate_cover mitziehen.
    from . import tags as tags_mod
    conn.execute("UPDATE files SET size = ?, mtime = ?, cover_hash = ? WHERE path = ?",
                 (st.st_size, st.st_mtime, tags_mod.read_cover_hash(path), path))
    conn.commit()


def set_has_cover(conn: sqlite3.Connection, path: str, flag: bool) -> None:
    conn.execute("UPDATE files SET has_cover = ? WHERE path = ?", (1 if flag else 0, path))
    conn.commit()


def paths_by_genre(conn: sqlite3.Connection, genre: str) -> list[str]:
    """Exakter, fallsensitiver Abgleich -- 'genre' wird nirgends normalisiert
    (siehe auch rekordbox._get_or_create_genre())."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE genre = ?", (genre,))]


def paths_by_artist(conn: sqlite3.Connection, artist: str) -> list[str]:
    """Exakter, fallsensitiver Abgleich, analog zu paths_by_genre()."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE artist = ?", (artist,))]


def paths_by_album(conn: sqlite3.Connection, album: str, group_artist: str) -> list[str]:
    """Exakter Abgleich auf Album UND den Gruppen-Interpreten -- ein
    Umbenennen darf nie zwei gleichnamige Alben verschiedener Interpreten
    zusammenwerfen. 'group_artist' ist album_artist, ersatzweise artist bei
    leerem album_artist -- exakt dieselbe Regel wie albumGroupKey() in
    app.js, damit der Server denselben Gruppen-Schluessel trifft, den die
    Tabelle anzeigt."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE album = ? "
        "AND (CASE WHEN album_artist != '' THEN album_artist ELSE artist END) = ?",
        (album, group_artist))]


def missing_cover_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(Pfad, Titel) aller Zeilen ohne Cover -- Kandidaten fuer
    coverfill.fill_missing_covers()."""
    return [(r["path"], r["title"] or "") for r in
            conn.execute("SELECT path, title FROM files WHERE has_cover = 0")]


def cached_cover_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(Pfad, Titel) aller Zeilen mit einem Eintrag in cover_cache -- der
    Titel kommt aus 'files', nicht aus cover_cache selbst. Kandidaten fuer
    coverfill.refresh_cached_covers()."""
    return [(r["path"], r["title"] or "") for r in conn.execute(
        "SELECT f.path AS path, f.title AS title "
        "FROM cover_cache c JOIN files f ON f.path = c.path")]


def cache_cover(conn: sqlite3.Connection, path: str, mime: str, data: bytes) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cover_cache (path, mime, data, ts) VALUES (?, ?, ?, ?)",
        (path, mime, data, time.time()))
    conn.commit()


def cached_cover(conn: sqlite3.Connection, path: str) -> tuple[bytes, str] | None:
    row = conn.execute(
        "SELECT data, mime FROM cover_cache WHERE path = ?", (path,)).fetchone()
    return (row["data"], row["mime"]) if row else None


def delete_cached_cover(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM cover_cache WHERE path = ?", (path,))
    conn.commit()


# Jede Tabelle, die einen Dateipfad als Schluessel traegt. Wandert eine Datei,
# muessen sie alle mit -- sonst blieben die Nutzer-Entscheidungen
# (ignored/corrected/playlist_items) und die Caches
# (waveform/rekordbox/music_added) am alten, nicht mehr existierenden Pfad
# haengen. 'playlist_items' hat mehrere Zeilen pro Pfad (eine je Playlist) --
# UPDATE ... WHERE path = ? trifft und verschiebt sie alle in einem
# Statement, kein Sonderfall noetig. Anders als bei 'events' geht auch das
# Delete-vor-Update auf: dort ist der Pfad allein der Schluessel-Anteil, der
# wandert, die zweite Haelfte (playlist_id) bleibt.
_PATH_TABLES = ("files", "ignored", "corrected", "waveform",
                "rekordbox", "rekordbox_pending", "rekordbox_stem_pending",
                "music_added", "cover_cache", "playlist_items", "dup_dismissed", "ignored_issues", "genremood", "user_tags",
                "phrases", "dj_track", "waveform_color", "grid_tempo", "rb_snapshot", "autocue_log",
                "playlist_link_items")


def move_path(conn: sqlite3.Connection, old_path: str, new_path: str) -> bool:
    """Schreibt einen Datensatz auf einen neuen Pfad um -- fuer Dateien, die
    Music.app beim Import in seinen eigenen Medienordner verschoben hat
    (Einstellung 'Dateien beim Hinzufuegen kopieren', siehe
    media.add_to_music_library()). Ohne das steht die Datei beim naechsten
    Scan als fehlend in der Liste und wird unter dem neuen Pfad ein zweites
    Mal analysiert.

    Verschiebt **keine** Datei -- das hat Music.app bereits getan, hier wird
    nur die Datenbank nachgezogen.

    Je Tabelle wird nur umgeschrieben, wenn dort ueberhaupt eine Zeile am
    alten Pfad haengt; eine schon vorhandene Zeile am Zielpfad weicht dann.
    Sie beschreibt denselben, gerade verschobenen Track, aber mit dem
    aelteren Stand -- gemessen wurde die Datei unter ihrem alten Pfad.

    Liefert True, wenn es zum alten Pfad eine Zeile in 'files' gab.
    """
    if not old_path or not new_path or old_path == new_path:
        return False
    moved_file_row = False
    for table in _PATH_TABLES:
        has_old = conn.execute(
            f"SELECT 1 FROM {table} WHERE path = ?", (old_path,)).fetchone()
        if not has_old:
            continue
        conn.execute(f"DELETE FROM {table} WHERE path = ?", (new_path,))
        conn.execute(f"UPDATE {table} SET path = ? WHERE path = ?",
                     (new_path, old_path))
        if table == "files":
            moved_file_row = True
    # 'events' bewusst nicht in _PATH_TABLES: das Delete-vor-Update darueber
    # wuerde bei mehreren Zeilen je Pfad Verlauf am Zielpfad vernichten.
    conn.execute("UPDATE events SET path = ? WHERE path = ?", (new_path, old_path))
    if moved_file_row:
        # Groesse/mtime nachziehen: der Cache-Schluessel ist (Pfad, Groesse,
        # mtime), und beim Kopieren aendert Music.app die mtime -- sonst
        # misst der naechste Scan die unveraenderte Datei ohne Not neu.
        refresh_external(conn, new_path)
    conn.commit()
    return moved_file_row


def refresh_external(conn: sqlite3.Connection, path: str) -> bool:
    """Eine Datei, die ein anderes Programm (Music.app) angefasst hat, ohne die
    Audio-Messung nachziehen: Groesse/mtime neu, und nur wenn die sich geaendert
    haben, Tags und Cover-Fingerabdruck frisch aus der Datei lesen (mutagen, ms).

    Grund: Music.app schreibt beim Umsortieren und beim Setzen von Tags/Artwork
    eigene Werte in die Datei. Wuerden nur Groesse/mtime uebernommen, stuende
    der alte Tag-Stand in der Zeile fest -- der naechste Scan haelt die Datei
    fuer unveraendert (so blieb ein altes Cover als "weicht vom Album ab"
    stehen). True = Datei hatte sich geaendert."""
    row = conn.execute("SELECT size, mtime FROM files WHERE path = ?", (path,)).fetchone()
    if row is None:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    if row["size"] == st.st_size and row["mtime"] == st.st_mtime:
        return False
    conn.execute("UPDATE files SET size = ?, mtime = ? WHERE path = ?",
                 (st.st_size, st.st_mtime, path))
    _reread_tags(conn, path)
    conn.commit()
    return True


def _reread_tags(conn: sqlite3.Connection, path: str) -> None:
    """Tag-Spalten, Cover-Kennzeichen und Cover-Fingerabdruck einer Zeile aus
    der Datei nachlesen, ohne die Audio-Messung anzufassen (siehe move_path()).
    Liest die Datei nicht (kein Audio-Objekt), bleibt alles unveraendert --
    ein Lesefehler soll keine vorhandenen Werte leeren."""
    from . import tags as tags_mod
    ident = tags_mod.read_identity(path)
    if ident["duration_s"] <= 0:
        return
    extra = tags_mod.read_extra(path)
    fields = {k: extra[k] for k in _TAG_FIELDS if k in extra}
    for k in ("artist", "title", "album"):
        if ident.get(k):
            fields[k] = ident[k]
    update_tags(conn, path, fields)
    conn.execute("UPDATE files SET has_cover = ?, cover_hash = ? WHERE path = ?",
                 (1 if extra.get("has_cover") else 0, extra.get("cover_hash", ""), path))


def path_norm_key(path: str) -> str:
    """Schluessel fuer 'derselbe Pfad, nur andere Gross-/Kleinschreibung oder
    Unicode-Form' -- das Standard-Volume (APFS) ist beim Datei-Zugriff
    unempfindlich dagegen, ein reiner String-Vergleich wie sonst ueberall in
    dieser Datei dagegen nicht."""
    return unicodedata.normalize("NFC", path).casefold()


def reconcile_case_renames(conn: sqlite3.Connection,
                            found: list[tuple[str, int, float]]) -> list[tuple[str, str]]:
    """Erkennt Dateien, die sich seit dem letzten Scan nur in Gross-
    /Kleinschreibung oder Unicode-Normalisierung veraendert haben, und
    schreibt ihre Zeile per move_path() auf den neuen Pfad um.

    Anlass: Music.app benennt Dateien in seinem Medienordner nach einem
    Tag-Edit selbststaendig um (siehe scanner.find_moved()) -- trifft das
    nur die Schreibweise, sieht der naechste Scan am exakten String-Vergleich
    trotzdem "eine neue Datei" und legt eine zweite Zeile an. Die alte bleibt
    liegen: os.path.isfile() darauf liefert wegen der Case-Insensitivitaet
    weiterhin True, die Zeile gilt also nirgends als fehlend. Erst ein
    --prune findet sie ueber den exakten Pfad-Abgleich in prune_missing()
    wieder -- bis dahin zaehlt dieselbe physische Datei doppelt.

    `found` sind die von scanner.iter_files() aktuell gemeldeten
    (Pfad, Groesse, mtime)-Tripel. Bewusst vorsichtig: ein Kandidat zaehlt
    nur bei uebereinstimmender Groesse UND wenn sein Normalisierungs-
    Schluessel auf beiden Seiten eindeutig ist (kein zweiter bekannter oder
    aktuell gefundener Pfad faellt auf denselben Schluessel) -- sonst lieber
    nichts anfassen statt zwei tatsaechlich verschiedene Dateien zu
    verschmelzen (moeglich auf einem case-sensitiven externen Volume).
    Gibt die durchgefuehrten (alter Pfad, neuer Pfad) zurueck.
    """
    known = conn.execute("SELECT path, size FROM files").fetchall()
    if not known:
        return []
    exact = {p for p, _s, _m in found}

    norm_counts: dict[str, int] = {}
    for p, _s, _m in found:
        k = path_norm_key(p)
        norm_counts[k] = norm_counts.get(k, 0) + 1

    by_norm: dict[str, tuple[str, int] | None] = {}
    for row in known:
        if row["path"] in exact:
            continue
        k = path_norm_key(row["path"])
        by_norm[k] = None if k in by_norm else (row["path"], row["size"])

    moved: list[tuple[str, str]] = []
    for new_path, size, _mtime in found:
        k = path_norm_key(new_path)
        if norm_counts.get(k) != 1:
            continue
        old = by_norm.get(k)
        if not old or old[0] == new_path or old[1] != size:
            continue
        if move_path(conn, old[0], new_path):
            moved.append((old[0], new_path))
    return moved


# Tabellen, die beim Aufraeumen mit abgeraeumt werden: rein abgeleitete
# Daten. Ohne 'files'-Zeile sind sie unerreichbarer Ballast, und sie lassen
# sich jederzeit neu berechnen bzw. abgleichen.
_PRUNE_TABLES = ("files", "waveform", "rekordbox", "rekordbox_pending",
                  "rekordbox_stem_pending", "music_added", "genremood", "phrases", "waveform_color", "grid_tempo", "rb_snapshot")


def prune_missing(conn: sqlite3.Connection, existing: set[str]) -> list[str]:
    """Eintraege loeschen, deren Datei nicht mehr existiert (nur DB, nie Dateien).

    Liefert die entfernten Pfade (nicht nur deren Anzahl) -- der Server gibt
    sie an den Client weiter, damit der die Zeilen sofort aus der Liste
    streichen kann, statt auf ein Neuladen zu warten.

    Raeumt die Caches aus `_PRUNE_TABLES` an denselben Pfaden mit ab. Die
    Nutzer-Entscheidungen (`ignored`/`favorites`/`corrected`) bleiben dagegen
    bewusst stehen: sie liessen sich nicht wiederherstellen, und
    `scan --prune` trifft auch Dateien, die nur gerade nicht erreichbar sind
    (nicht eingehaengtes Laufwerk). Kommt die Datei zurueck, gilt die
    Markierung wieder. Der Preis sind Karteileichen fuer wirklich geloeschte
    Dateien -- unsichtbar, weil ohne `files`-Zeile keine Zeile in der Liste
    steht.
    """
    cur = conn.execute("SELECT path FROM files")
    gone = [r["path"] for r in cur if r["path"] not in existing]
    if gone:
        args = [(p,) for p in gone]
        for table in _PRUNE_TABLES:
            conn.executemany(f"DELETE FROM {table} WHERE path = ?", args)
        conn.commit()
    return gone


def forget_file(conn: sqlite3.Connection, path: str) -> None:
    """Alles zu einer Datei abraeumen, die der Nutzer selbst entfernt hat
    (Papierkorb): ihre Zeile, saemtliche Caches und Messungen aus
    `_PRUNE_TABLES`, ein zwischengespeichertes Cover und die beiden
    Markierungen, die sich auf den Befund dieser einen Datei beziehen.

    Wichtig fuer den ueblichen Ablauf "Fake loeschen, bessere Fassung unter
    demselben Namen ablegen": Genre/Stimmung, Phrasen/Beatgrid, Farbwellenform
    und Rekordbox-Snapshot haben keinen Dateistempel. Bleiben sie stehen,
    erbt die neue Datei die Messungen der alten, und der Scan misst nicht neu.

    Stehen bleibt, was der Nutzer von Hand angelegt hat (Playlisten, eigene
    Tags, DJ-Bearbeitungen) -- wie bei prune_missing().
    """
    for table in _PRUNE_TABLES + ("cover_cache", "ignored", "corrected", "ignored_issues"):
        conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
    conn.commit()


def ignored_paths(conn: sqlite3.Connection) -> set[str]:
    return {r["path"] for r in conn.execute("SELECT path FROM ignored")}


def set_ignored(conn: sqlite3.Connection, path: str, flag: bool) -> bool:
    """Datei aus- oder wieder einblenden. Beruehrt die Datei selbst nie."""
    if flag:
        conn.execute(
            "INSERT OR REPLACE INTO ignored (path, ts) VALUES (?, ?)",
            (path, time.time()),
        )
        conn.execute("DELETE FROM corrected WHERE path = ?", (path,))
    else:
        conn.execute("DELETE FROM ignored WHERE path = ?", (path,))
    conn.commit()
    return flag


def corrected_paths(conn: sqlite3.Connection) -> set[str]:
    return {r["path"] for r in conn.execute("SELECT path FROM corrected")}


def set_corrected(conn: sqlite3.Connection, path: str, flag: bool) -> bool:
    """
    Als manuell korrigiert markieren oder zurueckholen: die Messung lag
    daneben, der Track ist tatsaechlich in Ordnung. Schliesst sich mit
    'ignored' aus.
    """
    if flag:
        conn.execute("INSERT OR REPLACE INTO corrected (path, ts) VALUES (?, ?)",
                     (path, time.time()))
        conn.execute("DELETE FROM ignored WHERE path = ?", (path,))
    else:
        conn.execute("DELETE FROM corrected WHERE path = ?", (path,))
    conn.commit()
    return flag


def genremood_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """Alle gespeicherten Genre-/Stimmungs-Analysen: Pfad -> {"g": [[Stil, p],
    ...], "m": {Stimmung: p}, "e": Energie}. Kaputte JSON-Werte zaehlen als leer."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT path, genres, moods, energy FROM genremood"):
        try:
            genres = json.loads(r["genres"] or "[]")
        except (TypeError, ValueError):
            genres = []
        try:
            moods = json.loads(r["moods"] or "{}")
        except (TypeError, ValueError):
            moods = {}
        out[r["path"]] = {"g": genres, "m": moods, "e": float(r["energy"] or 0)}
    return out


def paths_needing_genremood(conn: sqlite3.Connection, max_seconds: float,
                            version: str) -> list[str]:
    """Erfolgreich gescannte Dateien ohne Genre-/Stimmungs-Analyse oder mit
    einem aelteren Analysestand (Spalte model != version, z. B. vor einem neu
    hinzugekommenen Modell), nicht laenger als max_seconds -- Kandidaten fuer
    den Scan-Schritt jobs._analyse_genremood(). Zu lange Dateien bleiben schon
    hier draussen, sonst kaeme jede bei jedem Scan erneut als Kandidat."""
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE status = 'ok' AND duration_s <= ? "
        "AND path NOT IN (SELECT path FROM genremood WHERE model = ?) ORDER BY path",
        (max_seconds, version))]


def paths_needing_bpmkey(conn: sqlite3.Connection, max_seconds: float,
                         need_bpm: bool, need_key: bool) -> list[str]:
    """Erfolgreich gescannte Dateien, denen BPM und/oder Tonart fehlt (nicht
    laenger als max_seconds) -- Kandidaten fuer jobs._analyse_genremood()."""
    cond = []
    if need_bpm:
        cond.append("bpm IS NULL OR bpm <= 0")
    if need_key:
        cond.append("key IS NULL OR key = ''")
    if not cond:
        return []
    return [r["path"] for r in conn.execute(
        "SELECT path FROM files WHERE status = 'ok' AND duration_s <= ? AND ("
        + " OR ".join(cond) + ") ORDER BY path", (max_seconds,))]


def user_tags_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Pfad -> eigene Tags (alphabetisch)."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT path, tag FROM user_tags ORDER BY tag COLLATE NOCASE"):
        out.setdefault(r["path"], []).append(r["tag"])
    return out


def set_user_tags(conn: sqlite3.Connection, path: str, tags: list[str]) -> None:
    """Ersetzt die eigenen Tags eines Pfads komplett."""
    conn.execute("DELETE FROM user_tags WHERE path = ?", (path,))
    now = time.time()
    conn.executemany("INSERT OR IGNORE INTO user_tags (path, tag, ts) VALUES (?, ?, ?)",
                     [(path, t, now) for t in tags])
    conn.commit()


def change_user_tags(conn: sqlite3.Connection, paths: list[str],
                     add: list[str], remove: list[str]) -> None:
    """Sammelaktion: Tags zu mehreren Pfaden hinzufuegen bzw. entfernen,
    alle uebrigen Tags der Pfade bleiben."""
    now = time.time()
    conn.executemany("INSERT OR IGNORE INTO user_tags (path, tag, ts) VALUES (?, ?, ?)",
                     [(p, t, now) for p in paths for t in add])
    conn.executemany("DELETE FROM user_tags WHERE path = ? AND tag = ?",
                     [(p, t) for p in paths for t in remove])
    conn.commit()


def genremood_energy(conn: sqlite3.Connection, path: str) -> float:
    """Gespeicherte Energie (1..10) eines Pfads, 0.0 ohne Analyse."""
    row = conn.execute("SELECT energy FROM genremood WHERE path = ?", (path,)).fetchone()
    return float(row["energy"] or 0) if row else 0.0


def save_genremood(conn: sqlite3.Connection, path: str, genres: list,
                   moods: dict, energy: float, model: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO genremood (path, genres, moods, energy, model, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (path, json.dumps(genres), json.dumps(moods), float(energy), model,
         time.time()),
    )
    conn.commit()


def save_phrases(conn: sqlite3.Connection, path: str, result: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO phrases (path, mode, used, bpm, confidence, first_beat, "
        "downbeat, changes, phrases, mood, uncertain, version, ts, beats, grid_origin, phrases_origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (path, result["mode"], result["used"], result["bpm"], result["confidence"],
         result["first_beat"], result["downbeat"], json.dumps(result["changes"]),
         json.dumps(result["phrases"]), result["mood"], int(bool(result["uncertain"])),
         result["version"], time.time(), json.dumps(result["beats"]),
         # Eine Messung bringt keine Herkunft mit: dann ist sie eigene.
         result.get("grid_origin") or "tracktab" if result["beats"] else None,
         result.get("phrases_origin") or "tracktab" if result["phrases"] else None),
    )
    conn.commit()


def get_phrases(conn: sqlite3.Connection, path: str) -> dict | None:
    """Gespeicherte Analyse eines Pfads oder None. Kaputte JSON-Werte zaehlen als leer."""
    r = conn.execute("SELECT * FROM phrases WHERE path = ?", (path,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    for key, empty in (("changes", []), ("phrases", []), ("beats", [])):
        try:
            out[key] = json.loads(out[key] or "[]")
        except (TypeError, ValueError):
            out[key] = empty
    out["uncertain"] = bool(out["uncertain"])
    return out


def save_autocue_log(conn: sqlite3.Connection, path: str, cues: list) -> None:
    conn.execute("INSERT OR REPLACE INTO autocue_log (path, cues, ts) VALUES (?, ?, ?)",
                 (path, json.dumps(cues), time.time()))
    conn.commit()


def autocue_log_map(conn: sqlite3.Connection) -> dict[str, list]:
    """Pfad -> zuletzt von Auto Cue gesetzte Cues (fuer das Training)."""
    out = {}
    for r in conn.execute("SELECT path, cues FROM autocue_log"):
        try:
            out[r["path"]] = json.loads(r["cues"] or "[]")
        except (TypeError, ValueError):
            out[r["path"]] = []
    return out


DJ_TRACK_FIELDS = ("grid", "phrases", "cues")
_DJ_LOCK_FIELDS = ("grid", "phrases")


def get_dj_track(conn: sqlite3.Connection, path: str) -> dict:
    """Bearbeitungen eines Pfads: {"grid": ..., "phrases": ..., "cues": ...},
    je Feld None = unbearbeitet. Kaputte JSON-Werte zaehlen als unbearbeitet."""
    r = conn.execute("SELECT grid, phrases, cues FROM dj_track WHERE path = ?", (path,)).fetchone()
    out: dict = dict.fromkeys(DJ_TRACK_FIELDS)
    if r is None:
        return out
    for key in DJ_TRACK_FIELDS:
        try:
            out[key] = json.loads(r[key]) if r[key] is not None else None
        except (TypeError, ValueError):
            out[key] = None
    return out


def dj_edited_bpms(conn: sqlite3.Connection) -> dict:
    """{Pfad: Tempo} aller bearbeiteten Beatgrids, je Track das des laengsten Abschnitts."""
    out: dict = {}
    for row in conn.execute("SELECT path, grid FROM dj_track WHERE grid IS NOT NULL"):
        try:
            grid = json.loads(row["grid"]) or {}
            segs = grid.get("segments") or []
            beats = grid.get("beats") or []
            if not segs:
                continue
            ends = [s["t"] for s in segs[1:]] + [beats[-1][0] / 1000 if beats else segs[-1]["t"]]
            # Eine Rampe (dj.js) ist der Weg zwischen zwei Tempi, kein eigenes.
            steady = [p for p in zip(segs, ends) if not (p[0].get("ramp") or {}).get("beats")] or list(zip(segs, ends))
            out[row["path"]] = max(steady, key=lambda p: p[1] - p[0]["t"])[0]["bpm"]
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return out


def dj_track_edits(conn: sqlite3.Connection) -> dict:
    """{Pfad: (Bearbeitungen, Abgleich-Stand)} aller Tracks mit Bearbeitungen."""
    paths = [r["path"] for r in conn.execute(
        "SELECT path FROM dj_track WHERE grid IS NOT NULL OR phrases IS NOT NULL OR cues IS NOT NULL")]
    return {p: (get_dj_track(conn, p), get_dj_base(conn, p)) for p in paths}


def grid_tempo_map(conn: sqlite3.Connection) -> dict:
    return {r["path"]: [r["lo"], r["hi"]] for r in conn.execute("SELECT path, lo, hi FROM grid_tempo")}


def grid_tempo_seq_map(conn: sqlite3.Connection) -> dict:
    """{Pfad: Tempi in Track-Reihenfolge}, nur wo die Folge schon bekannt ist."""
    out: dict = {}
    for r in conn.execute("SELECT path, seq FROM grid_tempo WHERE seq IS NOT NULL"):
        try:
            out[r["path"]] = json.loads(r["seq"])
        except (TypeError, ValueError):
            continue
    return out


def set_grid_tempo(conn: sqlite3.Connection, path: str, lo: float | None, hi: float | None,
                   seq: list | None = None) -> None:
    """None = der Track hat kein Beatgrid. seq = Tempowechsel in Track-Reihenfolge."""
    if lo is None or hi is None:
        conn.execute("DELETE FROM grid_tempo WHERE path = ?", (path,))
    else:
        conn.execute("INSERT OR REPLACE INTO grid_tempo (path, lo, hi, ts, seq) VALUES (?, ?, ?, ?, ?)",
                     (path, lo, hi, time.time(), json.dumps(seq) if seq is not None else None))
    conn.commit()


def get_rb_snapshot(conn: sqlite3.Connection, path: str) -> dict | None:
    """Festgehaltener Rekordbox-Stand eines Pfads oder None."""
    r = conn.execute("SELECT * FROM rb_snapshot WHERE path = ?", (path,)).fetchone()
    if r is None:
        return None
    out = {"stamp": r["stamp"]}
    for key, empty in (("grid", None), ("phrases", None), ("cues", None), ("seen", {}), ("changed", [])):
        try:
            out[key] = json.loads(r[key]) if r[key] is not None else empty
        except (TypeError, ValueError):
            out[key] = empty
    return out


def save_rb_snapshot(conn: sqlite3.Connection, path: str, snap: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO rb_snapshot (path, grid, phrases, cues, seen, changed, stamp, ts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, *(None if snap[k] is None else json.dumps(snap[k]) for k in DJ_TRACK_FIELDS),
         json.dumps(snap["seen"]), json.dumps(snap["changed"]), snap.get("stamp"), time.time()))
    conn.commit()


def _has_hot_cue(raw) -> bool:
    try:
        return any(c.get("kind") == "hot" for c in json.loads(raw) or [])
    except (TypeError, ValueError, AttributeError):
        return False


def hot_cue_maps(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """({Pfad: hat Hot Cue} der in TrackTab bearbeiteten Cues, dasselbe fuer den
    festgehaltenen Rekordbox-Stand). Nur Zeilen, in denen Cues stehen."""
    edited = {r["path"]: _has_hot_cue(r["cues"])
              for r in conn.execute("SELECT path, cues FROM dj_track WHERE cues IS NOT NULL")}
    snap = {r["path"]: _has_hot_cue(r["cues"])
            for r in conn.execute("SELECT path, cues FROM rb_snapshot WHERE cues IS NOT NULL")}
    return edited, snap


def rb_snapshot_changed_map(conn: sqlite3.Connection) -> dict:
    """{Pfad: Felder} aller Tracks, die sich in Rekordbox seit dem Festhalten geaendert haben."""
    out: dict = {}
    for r in conn.execute("SELECT path, changed FROM rb_snapshot WHERE changed IS NOT NULL AND changed != '[]'"):
        try:
            out[r["path"]] = json.loads(r["changed"])
        except (TypeError, ValueError):
            continue
    return out


def get_dj_base(conn: sqlite3.Connection, path: str) -> dict:
    """Abgleich-Stand eines Pfads: {"rb": {...}, "pushed": {...}}."""
    r = conn.execute("SELECT rb_base FROM dj_track WHERE path = ?", (path,)).fetchone()
    try:
        base = json.loads(r["rb_base"]) if r is not None and r["rb_base"] else {}
    except (TypeError, ValueError):
        base = {}
    return {"rb": dict(base.get("rb") or {}), "pushed": dict(base.get("pushed") or {})}


def save_dj_track(conn: sqlite3.Connection, path: str, fields: dict, base: dict | None = None,
                  auto_lock: bool = True) -> None:
    """Schreibt nur die uebergebenen Felder, None setzt eines zurueck. Sind danach
    alle leer und ist nichts gesperrt, verschwindet die Zeile samt Abgleich-Stand.
    base = neuer Abgleich-Stand, None laesst ihn stehen.

    Analyse-Sperre je Beatgrid und Phrasen: eine Bearbeitung sperrt das jeweilige
    Feld, zuruecksetzen gibt es wieder frei. Cues beruehrt keine Analyse. auto_lock=False
    laesst die Sperren stehen: das Ergebnis einer Analyse ist keine Bearbeitung."""
    merged = get_dj_track(conn, path)
    merged.update({k: v for k, v in fields.items() if k in DJ_TRACK_FIELDS})
    keep_base = base if base is not None else get_dj_base(conn, path)
    locks = dj_locks(conn, path)
    for key in _DJ_LOCK_FIELDS if auto_lock else ():
        if key in fields:
            locks[key] = fields[key] is not None
    _write_dj_track(conn, path, merged, keep_base, locks)
    conn.commit()


def dj_locks(conn: sqlite3.Connection, path: str) -> dict:
    r = conn.execute("SELECT grid_lock, phrases_lock FROM dj_track WHERE path = ?", (path,)).fetchone()
    return {"grid": bool(r and r["grid_lock"]), "phrases": bool(r and r["phrases_lock"])}


def set_dj_locks(conn: sqlite3.Connection, path: str, changes: dict) -> None:
    """Sperren von Hand setzen oder freigeben, die Bearbeitungen bleiben dabei stehen."""
    locks = dj_locks(conn, path)
    locks.update({k: bool(v) for k, v in changes.items() if k in _DJ_LOCK_FIELDS})
    _write_dj_track(conn, path, get_dj_track(conn, path), get_dj_base(conn, path), locks)
    conn.commit()


def _write_dj_track(conn: sqlite3.Connection, path: str, merged: dict, base: dict, locks: dict) -> None:
    if not any(locks.values()) and all(merged[k] is None for k in DJ_TRACK_FIELDS):
        conn.execute("DELETE FROM dj_track WHERE path = ?", (path,))
        return
    conn.execute(
        "INSERT OR REPLACE INTO dj_track (path, grid, phrases, cues, ts, rb_base, grid_lock, phrases_lock)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, *(None if merged[k] is None else json.dumps(merged[k]) for k in DJ_TRACK_FIELDS),
         time.time(), json.dumps(base), int(locks["grid"]), int(locks["phrases"])))


def dup_dismissed_paths(conn: sqlite3.Connection) -> set[str]:
    return {r["path"] for r in conn.execute("SELECT path FROM dup_dismissed")}


def set_dup_dismissed_bulk(conn: sqlite3.Connection, paths: list[str], flag: bool) -> int:
    """
    Alle aktuell lebenden Mitgliedspfade einer Duplikat-Gruppe auf einen
    Schlag als "kein Duplikat" markieren oder zuruecknehmen (Knopf im
    Gruppenkopf der Duplikate-Ansicht, sowie der automatische Reset, wenn
    eine neue Kopie zu einer bereits bestaetigten Gruppe dazukommt).
    """
    if not paths:
        return 0
    if flag:
        ts = time.time()
        conn.executemany(
            "INSERT OR REPLACE INTO dup_dismissed (path, ts) VALUES (?, ?)",
            [(p, ts) for p in paths])
    else:
        conn.executemany("DELETE FROM dup_dismissed WHERE path = ?",
                          [(p,) for p in paths])
    conn.commit()
    return len(paths)


def ignored_issues_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Pfad -> Liste der einzeln ignorierten Auffaelligkeits-Codes."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT path, code FROM ignored_issues"):
        out.setdefault(r["path"], []).append(r["code"])
    return out


def set_issues_ignored(conn: sqlite3.Connection, items: list[tuple[str, str]], flag: bool) -> int:
    """Auffaelligkeiten (Pfad, Code) ignorieren bzw. wieder zulassen."""
    if flag:
        ts = time.time()
        conn.executemany("INSERT OR REPLACE INTO ignored_issues (path, code, ts) VALUES (?, ?, ?)",
                         [(p, c, ts) for p, c in items])
    else:
        conn.executemany("DELETE FROM ignored_issues WHERE path = ? AND code = ?", items)
    conn.commit()
    return len(items)


def clear_ignored_issue_codes(conn: sqlite3.Connection, codes: list[str]) -> int:
    """Alle Ignorierungen der genannten Codes entfernen (alle Tracks), z. B.
    'rule:<id>' einer geloeschten oder inhaltlich geaenderten Ersetzungsregel."""
    if not codes:
        return 0
    cur = conn.executemany("DELETE FROM ignored_issues WHERE code = ?", [(c,) for c in codes])
    conn.commit()
    return cur.rowcount


def merge_dismissed_pairs(conn: sqlite3.Connection, field: str) -> set[tuple[str, str]]:
    """Ausgeblendete Zusammenfuehrungs-Vorschlaege eines Feldes (genre/album/
    artist), als Menge kanonisch sortierter Paare."""
    return {(r["value_a"], r["value_b"]) for r in conn.execute(
        "SELECT value_a, value_b FROM merge_dismissed WHERE field = ?", (field,))}


def set_merge_dismissed(conn: sqlite3.Connection, field: str, a: str, b: str, flag: bool) -> None:
    """a/b werden hier -- nicht beim Aufrufer -- kanonisch sortiert, damit ein
    Aufrufer nie selbst auf die Reihenfolge achten muss."""
    value_a, value_b = sorted((a, b))
    if flag:
        conn.execute(
            "INSERT OR REPLACE INTO merge_dismissed (field, value_a, value_b, ts) "
            "VALUES (?, ?, ?, ?)", (field, value_a, value_b, time.time()))
    else:
        conn.execute(
            "DELETE FROM merge_dismissed WHERE field = ? AND value_a = ? AND value_b = ?",
            (field, value_a, value_b))
    conn.commit()


# --------------------------------------------------------------- Playlisten
#
# Ordner, regulaere Playlisten und Smart Playlists liegen in einer Tabelle
# (siehe _SCHEMA). Der Baum wird erst im Client aus 'parent_id' aufgebaut --
# serverseitig zu verschachteln haette nur eine zweite, gleichwertige
# Darstellung erzeugt, die mit der ersten synchron gehalten werden muesste.
#
# Bewusst NICHT in _PRUNE_TABLES: eine Playlist-Zuordnung ist eine
# Nutzer-Entscheidung wie 'ignored'/'favorites'/'corrected' und soll eine
# voruebergehend nicht erreichbare Datei (externe Platte) ueberleben.

# 'generator': Playlist-Generator (app/webui/generator.js). Die Einstellungen
# (Kurve, Regler, Gates, Slots) stehen wie das Regelwerk einer Smart Playlist
# in 'rules', die Belegung aber in 'playlist_items' wie bei einer normalen
# Playlist -- move_path() zieht sie dadurch ohne Sonderfall mit.
PLAYLIST_KINDS = ("folder", "playlist", "smart", "generator")


# Feste Listen, die TrackTab selbst mitbringt: ein Ordner mit je einer Smart
# Playlist pro Status. Sie loesen die frueheren Verdikt-Schaltflaechen ueber
# der Suche ab -- dieselbe Auswahl, nur an einer Stelle statt an zweien.
#
# 'system': 1 macht sie unveraenderlich (der Server weist Umbenennen,
# Verschieben und Loeschen ab). Die hier hinterlegten Namen sind nur ein
# lesbarer Rueckfall fuer einen Blick in die Datenbank -- angezeigt wird im
# Client die uebersetzte Fassung (SYSTEM_PLAYLIST_LABELS in app.js), sonst
# waere die Beschriftung an die Sprache gebunden, in der sie einmal angelegt
# wurden.
#
# 'seq': -1 beim Ordner sortiert ihn vor die selbst angelegten Listen (deren
# Vorgabe ist 0).
#
# Die Regel fuer "Manuell korrigiert" prueft 'mc' statt 'v': das ist kein
# Verdikt, sondern die eigene Markierung (Tabelle 'corrected').
_SYSTEM_PLAYLIST_FOLDER = "sys_verdicts"
# Je Status ein eigenes Lucide-Symbol (app/webui/lucide-icons.js), "sym:"-
# codiert wie jeder andere Playlist-Icon-Wert (siehe playlistIconValue() in
# app.js) -- unterschieden werden die Listen zusaetzlich ueber die Farbe,
# dieselben Token, die auch die Verdikt-Abzeichen in der Tabelle benutzen
# (siehe _Handler._PLAYLIST_COLORS in server.py).
_SYSTEM_PLAYLISTS = [
    (_SYSTEM_PLAYLIST_FOLDER, None, "folder", "Prüflisten", None, None, None, -1),
    ("sys_v_ok", _SYSTEM_PLAYLIST_FOLDER, "smart", "Korrekt", "sym:badge-check", "ok",
     {"match": "all", "rules": [{"field": "v", "op": "is", "value": "OK"}]}, 0),
    ("sys_v_suspect", _SYSTEM_PLAYLIST_FOLDER, "smart", "Verdächtig", "sym:badge-alert", "susp",
     {"match": "all", "rules": [{"field": "v", "op": "is", "value": "VERDAECHTIG"}]}, 1),
    ("sys_v_fake", _SYSTEM_PLAYLIST_FOLDER, "smart", "Fake", "sym:badge-x", "fake",
     {"match": "all", "rules": [{"field": "v", "op": "is", "value": "FAKE"}]}, 2),
    ("sys_v_unknown", _SYSTEM_PLAYLIST_FOLDER, "smart", "Unklar", "sym:badge-question-mark", "unk",
     {"match": "all", "rules": [{"field": "v", "op": "is", "value": "UNKLAR"}]}, 3),
    ("sys_v_corrected", _SYSTEM_PLAYLIST_FOLDER, "smart", "Manuell korrigiert", "sym:badge-info", "accent",
     {"match": "all", "rules": [{"field": "mc", "op": "is", "value": "1"}]}, 4),
]


def _seed_system_playlists(conn: sqlite3.Connection) -> None:
    """Haelt die festen Status-Listen auf Stand. Laeuft bei jedem connect().

    Was uns gehoert, wird bei jedem Start neu gesetzt: Art, Symbol und
    Regelsatz, bei den Status-Listen auch Name und Farbe. Der Nutzer kann
    daran nichts aendern (der Server weist es ab), und so wirkt eine spaetere
    Korrektur an der Definition auch auf bestehende Datenbanken.

    Was dem Nutzer gehoert, bleibt unangetastet: 'parent_id', 'seq' und beim
    Ordner Name und Farbe (im Bearbeiten-Dialog aenderbar). Wuerden wir es
    hier zuruecksetzen, spraenge es bei jedem Start zurueck.

    Ein versehentlich verlorener Knoten kommt darueber ebenfalls zurueck.
    """
    have = {r["id"] for r in conn.execute("SELECT id FROM playlists")}
    now = time.time()
    for pid, parent, kind, name, icon, color, rules, seq in _SYSTEM_PLAYLISTS:
        rules_json = json.dumps(rules, ensure_ascii=False) if rules else None
        if pid in have:
            conn.execute(
                "UPDATE playlists SET kind = ?, icon = ?, rules = ?, system = 1, "
                "name = CASE WHEN ? = 'folder' AND TRIM(COALESCE(name, '')) != '' "
                "THEN name ELSE ? END, "
                "color = CASE WHEN ? = 'folder' THEN color ELSE ? END WHERE id = ?",
                (kind, icon, rules_json, kind, name, kind, color, pid))
        else:
            conn.execute(
                "INSERT INTO playlists (id, parent_id, kind, name, icon, color, rules, "
                "system, seq, ts) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (pid, parent, kind, name, icon, color, rules_json, seq, now))
    conn.commit()


def playlists_all(conn: sqlite3.Connection) -> list[dict]:
    """Alle Knoten als flache Liste, Geschwister nach 'seq' und dann Name."""
    rows = conn.execute(
        "SELECT id, parent_id, kind, name, icon, color, rules, system, seq, fav_slot, locked "
        "FROM playlists ORDER BY seq ASC, name ASC")
    return [dict(r) for r in rows]


def playlist_items_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """{playlist_id: [pfad, ...]} in der manuellen Reihenfolge ('pos')."""
    out: dict[str, list[str]] = {}
    for r in conn.execute(
            "SELECT playlist_id, path FROM playlist_items "
            "ORDER BY playlist_id ASC, pos ASC"):
        out.setdefault(r["playlist_id"], []).append(r["path"])
    return out


def playlist_by_id(conn: sqlite3.Connection, playlist_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, parent_id, kind, name, icon, color, rules, system, seq, fav_slot, locked "
        "FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    return dict(row) if row else None


def playlist_descendants(conn: sqlite3.Connection, playlist_id: str) -> list[str]:
    """Der Knoten selbst plus alle Nachfahren. Ordner duerfen Ordner
    enthalten -- an echtem Rekordbox-Material kommt Tiefe 4 vor --, deshalb
    ein vollstaendiger Durchlauf statt nur einer Ebene."""
    by_parent: dict[str | None, list[str]] = {}
    for r in conn.execute("SELECT id, parent_id FROM playlists"):
        by_parent.setdefault(r["parent_id"], []).append(r["id"])
    out: list[str] = []
    stack = [playlist_id]
    while stack:
        cur = stack.pop()
        if cur in out:                      # Schutz vor einem zyklisch
            continue                        # gewordenen Baum (sollte nicht
        out.append(cur)                     # vorkommen, waere aber sonst eine
        stack.extend(by_parent.get(cur, []))  # Endlosschleife)
    return out


def playlist_can_reparent(conn: sqlite3.Connection, playlist_id: str,
                          parent_id: str | None) -> bool:
    """Verhindert, dass ein Ordner unter sich selbst oder einen eigenen
    Nachfahren geschoben wird -- der Teilbaum waere danach unerreichbar."""
    if not parent_id:
        return True
    if parent_id == playlist_id:
        return False
    return parent_id not in playlist_descendants(conn, playlist_id)


def save_playlist(conn: sqlite3.Connection, node: dict) -> dict:
    """Legt einen Knoten an oder aktualisiert ihn (ueber die id).

    Die id vergibt der Aufrufer (server._playlist_id, auf Basis von
    settings._slugify) -- so entsteht hier keine zweite Namensregel.
    """
    fields = {
        "id": str(node["id"]),
        "parent_id": node.get("parent_id") or None,
        "kind": node.get("kind") or "playlist",
        "name": str(node.get("name") or ""),
        "icon": node.get("icon") or None,
        "color": node.get("color") or None,
        "rules": node.get("rules") or None,
        "system": 1 if node.get("system") else 0,
        "seq": int(node.get("seq") or 0),
        "ts": time.time(),
        "fav_slot": node.get("fav_slot") or None,
        "locked": 1 if node.get("locked") else 0,
    }
    conn.execute(
        "INSERT OR REPLACE INTO playlists "
        "(id, parent_id, kind, name, icon, color, rules, system, seq, ts, fav_slot, locked) "
        "VALUES (:id, :parent_id, :kind, :name, :icon, :color, :rules, "
        ":system, :seq, :ts, :fav_slot, :locked)", fields)
    conn.commit()
    return fields


def delete_playlist(conn: sqlite3.Connection, playlist_id: str) -> list[str]:
    """Loescht den Knoten mitsamt allen Nachfahren und deren Zuordnungen.
    Liefert die entfernten ids. Beruehrt keine Datei und keine 'files'-Zeile.
    """
    ids = playlist_descendants(conn, playlist_id)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM playlist_items WHERE playlist_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM playlist_links WHERE playlist_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM playlist_link_items WHERE playlist_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM playlists WHERE id IN ({marks})", ids)
    conn.commit()
    return ids


# ── Verknuepfte Playlisten (siehe playlistsync.py) ─────────────────────────
LINK_SOURCES = ("music", "rekordbox")
LINK_SIDES = ("tracktab",) + LINK_SOURCES


def playlist_links(conn: sqlite3.Connection, playlist_id: str | None = None) -> list[dict]:
    """Alle Verknuepfungen (oder die einer Liste) als dicts."""
    sql = "SELECT playlist_id, source, ext_id, ext_name, state, error, synced_ts FROM playlist_links"
    args: tuple = ()
    if playlist_id is not None:
        sql += " WHERE playlist_id = ?"
        args = (playlist_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY playlist_id, source", args)]


def set_playlist_link(conn: sqlite3.Connection, playlist_id: str, source: str,
                      ext_id: str | None, ext_name: str = "") -> None:
    """Verknuepft (ext_id) oder trennt (None). Eine neue oder geaenderte Verknuepfung
    beginnt ohne gemerkten Stand dieser Seite -- die erste Runde vereinigt nur.
    Ohne jede Verknuepfung faellt auch der TrackTab-Stand weg."""
    if source not in LINK_SOURCES:
        raise ValueError(f"Unbekannte Quelle: {source}")
    old = conn.execute("SELECT ext_id FROM playlist_links WHERE playlist_id = ? AND source = ?",
                       (playlist_id, source)).fetchone()
    if ext_id:
        if old is None or old["ext_id"] != str(ext_id):
            conn.execute("DELETE FROM playlist_link_items WHERE playlist_id = ? AND side = ?",
                         (playlist_id, source))
        conn.execute(
            "INSERT OR REPLACE INTO playlist_links (playlist_id, source, ext_id, ext_name, state, error, synced_ts) "
            "VALUES (?, ?, ?, ?, 'ok', NULL, ?)",
            (playlist_id, source, str(ext_id), ext_name or "",
             None if old is None or old["ext_id"] != str(ext_id) else time.time()))
    else:
        conn.execute("DELETE FROM playlist_links WHERE playlist_id = ? AND source = ?", (playlist_id, source))
        conn.execute("DELETE FROM playlist_link_items WHERE playlist_id = ? AND side = ?", (playlist_id, source))
    if not conn.execute("SELECT 1 FROM playlist_links WHERE playlist_id = ?", (playlist_id,)).fetchone():
        conn.execute("DELETE FROM playlist_link_items WHERE playlist_id = ?", (playlist_id,))
    conn.commit()


def set_link_state(conn: sqlite3.Connection, playlist_id: str, source: str, state: str,
                   error: str | None = None, synced: bool = False) -> None:
    if synced:
        conn.execute("UPDATE playlist_links SET state = ?, error = ?, synced_ts = ? "
                     "WHERE playlist_id = ? AND source = ?",
                     (state, error, time.time(), playlist_id, source))
    else:
        conn.execute("UPDATE playlist_links SET state = ?, error = ? WHERE playlist_id = ? AND source = ?",
                     (state, error, playlist_id, source))
    conn.commit()


def link_baselines(conn: sqlite3.Connection, playlist_id: str) -> dict[str, set[str]]:
    """{side: {pfad, ...}} -- fehlende Seiten als leere Menge."""
    out: dict[str, set[str]] = {side: set() for side in LINK_SIDES}
    for r in conn.execute("SELECT side, path FROM playlist_link_items WHERE playlist_id = ?", (playlist_id,)):
        out.setdefault(r["side"], set()).add(r["path"])
    return out


def set_link_baseline(conn: sqlite3.Connection, playlist_id: str, side: str, paths) -> None:
    conn.execute("DELETE FROM playlist_link_items WHERE playlist_id = ? AND side = ?", (playlist_id, side))
    conn.executemany("INSERT OR IGNORE INTO playlist_link_items (playlist_id, side, path) VALUES (?, ?, ?)",
                     [(playlist_id, side, p) for p in paths if p])
    conn.commit()


def set_playlist_items(conn: sqlite3.Connection, playlist_id: str,
                       paths: list[str]) -> int:
    """Ersetzt den Inhalt einer Playlist vollstaendig -- 'pos' ergibt sich aus
    der uebergebenen Reihenfolge. Der Weg fuer Umsortieren und fuer das
    Zuruecknehmen einer Aenderung (Undo), wo der ganze Zustand zaehlt."""
    now = time.time()
    seen: set[str] = set()
    rows = []
    for path in paths:
        if not path or path in seen:        # ein Track steht hoechstens
            continue                        # einmal in derselben Playlist
        seen.add(path)
        rows.append((playlist_id, path, len(rows), now))
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    conn.executemany(
        "INSERT INTO playlist_items (playlist_id, path, pos, ts) VALUES (?, ?, ?, ?)",
        rows)
    conn.commit()
    return len(rows)


def add_playlist_items(conn: sqlite3.Connection, playlist_id: str,
                       paths: list[str]) -> int:
    """Haengt Tracks hinten an und liefert die Zahl der tatsaechlich neuen.
    Bereits enthaltene Tracks bleiben an ihrer Stelle stehen, statt ans Ende
    zu wandern -- ein Drop auf eine Playlist soll eine bestehende Reihenfolge
    nicht durcheinanderbringen."""
    row = conn.execute(
        "SELECT MAX(pos) AS m FROM playlist_items WHERE playlist_id = ?",
        (playlist_id,)).fetchone()
    pos = (row["m"] + 1) if row and row["m"] is not None else 0
    have = {r["path"] for r in conn.execute(
        "SELECT path FROM playlist_items WHERE playlist_id = ?", (playlist_id,))}
    now = time.time()
    rows = []
    for path in paths:
        if not path or path in have:
            continue
        have.add(path)
        rows.append((playlist_id, path, pos, now))
        pos += 1
    conn.executemany(
        "INSERT INTO playlist_items (playlist_id, path, pos, ts) VALUES (?, ?, ?, ?)",
        rows)
    conn.commit()
    return len(rows)


def remove_playlist_items(conn: sqlite3.Connection, playlist_id: str,
                          paths: list[str]) -> int:
    """Entfernt Tracks aus einer Playlist (nie die Datei) und schliesst die
    entstandene Luecke in 'pos', damit die Reihenfolge lueckenlos bleibt."""
    if not paths:
        return 0
    marks = ",".join("?" * len(paths))
    cur = conn.execute(
        f"DELETE FROM playlist_items WHERE playlist_id = ? AND path IN ({marks})",
        [playlist_id, *paths])
    removed = cur.rowcount or 0
    rest = [r["path"] for r in conn.execute(
        "SELECT path FROM playlist_items WHERE playlist_id = ? ORDER BY pos ASC",
        (playlist_id,))]
    conn.executemany(
        "UPDATE playlist_items SET pos = ? WHERE playlist_id = ? AND path = ?",
        [(i, playlist_id, p) for i, p in enumerate(rest)])
    conn.commit()
    return removed


def rekordbox_paths(conn: sqlite3.Connection) -> set[str]:
    return {r["path"] for r in conn.execute("SELECT path FROM rekordbox")}


def sync_rekordbox_presence(conn: sqlite3.Connection, present_paths: set[str]) -> int:
    """
    Voller Abgleich: ersetzt den Cache-Inhalt durch alle Pfade aus
    present_paths, die auch in 'files' bekannt sind. Gibt die Trefferzahl
    zurueck.
    """
    known = {r["path"] for r in conn.execute("SELECT path FROM files")}
    matched = present_paths & known
    now = time.time()
    conn.execute("DELETE FROM rekordbox")
    conn.executemany(
        "INSERT OR REPLACE INTO rekordbox (path, ts) VALUES (?, ?)",
        [(p, now) for p in matched],
    )
    conn.commit()
    return len(matched)


def mark_rekordbox_present(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Punktuelles Update nach einem erfolgreichen Playlist-Push -- der Haken
    erscheint sofort, ohne auf den naechsten vollen Abgleich zu warten."""
    now = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO rekordbox (path, ts) VALUES (?, ?)",
        [(p, now) for p in paths],
    )
    conn.commit()


def rekordbox_pending_add(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Merkt Tracks fuer den naechsten gebuendelten Tag-Abgleich mit Rekordbox vor."""
    now = time.time()
    conn.executemany(
        # Kein REPLACE: eine schon vorgemerkte Pfadkorrektur (prior_path) bliebe sonst weg.
        "INSERT INTO rekordbox_pending (path, ts) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET ts = excluded.ts",
        [(p, now) for p in paths],
    )
    conn.commit()


def rekordbox_pending_add_rename(conn: sqlite3.Connection, new_path: str,
                                 old_path: str) -> None:
    """Merkt einen umbenannten Track vor: Rekordbox kennt ihn noch unter
    'old_path'. Nach move_path() aufrufen (zieht eine schon vorhandene
    Vormerkung auf den neuen Pfad). Bei mehrfachem Umbenennen bleibt der
    ERSTE Pfad erhalten -- nur der steht in Rekordbox."""
    row = conn.execute("SELECT prior_path FROM rekordbox_pending WHERE path = ?",
                       (new_path,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO rekordbox_pending (path, ts, prior_path) VALUES (?, ?, ?)",
                     (new_path, time.time(), old_path))
    elif not row["prior_path"]:
        conn.execute("UPDATE rekordbox_pending SET prior_path = ? WHERE path = ?",
                     (old_path, new_path))
    conn.commit()


def rekordbox_pending_list(conn: sqlite3.Connection) -> list[dict]:
    """[{path, prior_path}] -- prior_path ist der Pfad, den Rekordbox kennt."""
    return [{"path": r["path"], "prior_path": r["prior_path"] or r["path"]}
            for r in conn.execute(
                "SELECT path, prior_path FROM rekordbox_pending ORDER BY ts, path")]


def rekordbox_pending_count(conn: sqlite3.Connection) -> int:
    """Vorgemerkte Tag-/Pfadkorrekturen plus vorgemerktes Entfernen (Papierkorb)."""
    return (conn.execute("SELECT COUNT(*) FROM rekordbox_pending").fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM rekordbox_remove_pending").fetchone()[0])


def rekordbox_remove_pending_add(conn: sqlite3.Connection, paths: list[str]) -> None:
    now = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO rekordbox_remove_pending (path, ts) VALUES (?, ?)",
        [(p, now) for p in paths])
    conn.commit()


def rekordbox_remove_pending_list(conn: sqlite3.Connection) -> list[str]:
    return [r["path"] for r in conn.execute(
        "SELECT path FROM rekordbox_remove_pending ORDER BY ts, path")]


def rekordbox_remove_pending_clear(conn: sqlite3.Connection, paths: list[str]) -> None:
    conn.executemany("DELETE FROM rekordbox_remove_pending WHERE path = ?",
                     [(p,) for p in paths])
    conn.commit()


def rekordbox_pending_clear(conn: sqlite3.Connection, paths: list[str]) -> None:
    conn.executemany("DELETE FROM rekordbox_pending WHERE path = ?",
                     [(p,) for p in paths])
    conn.commit()


def rekordbox_stem_pending_add(conn: sqlite3.Connection, stem_path: str,
                               source_path: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO rekordbox_stem_pending (path, source_path, ts) "
        "VALUES (?, ?, ?)", (stem_path, source_path, time.time()))
    conn.commit()


def rekordbox_stem_pending_list(conn: sqlite3.Connection) -> list[dict]:
    return [{"path": r["path"], "source": r["source_path"]} for r in conn.execute(
        "SELECT path, source_path FROM rekordbox_stem_pending ORDER BY ts, path")]


def rekordbox_stem_pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM rekordbox_stem_pending").fetchone()[0]


def rekordbox_stem_pending_clear(conn: sqlite3.Connection, paths: list[str]) -> None:
    conn.executemany("DELETE FROM rekordbox_stem_pending WHERE path = ?",
                     [(p,) for p in paths])
    conn.commit()


def forget_rekordbox_tracks(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Raeumt alles ab, was nach einem Entfernen aus der Rekordbox-Sammlung noch an deren
    Stand haengt: Praesenz, Warteschlangen, festgehaltener Stand, Abgleich-Pruefsummen und
    gemerkte Tempo-Spanne. Eigene Bearbeitungen in 'dj_track' und die eigene Analyse in
    'phrases' bleiben. Eine Sperre bleibt nur, wo eine Bearbeitung dahintersteht, sonst
    stammt sie vom Schreiben nach Rekordbox."""
    for path in paths:
        for table in ("rekordbox", "rekordbox_pending", "rekordbox_stem_pending", "rb_snapshot", "grid_tempo"):
            conn.execute(f"DELETE FROM {table} WHERE path = ?", (path,))
        edits = get_dj_track(conn, path)
        locks = dj_locks(conn, path)
        locks = {f: locks[f] and edits[f] is not None for f in locks}
        _write_dj_track(conn, path, edits, {"rb": {}, "pushed": {}}, locks)
    conn.commit()


def is_music_added(conn: sqlite3.Connection, path: str) -> bool:
    """Steht dieser eine Pfad in der Music.app-Bibliothek? Fuer Einzelabfragen
    -- music_added_map() liest dafuer die ganze Tabelle."""
    return conn.execute("SELECT 1 FROM music_added WHERE path = ?",
                        (path,)).fetchone() is not None


def music_added_map(conn: sqlite3.Connection) -> dict[str, float]:
    return {r["path"]: r["added_ts"] for r in conn.execute(
        "SELECT path, added_ts FROM music_added")}


def sync_music_added(conn: sqlite3.Connection, dates: dict[str, float]) -> int:
    """
    Voller Abgleich: ersetzt den Cache-Inhalt durch alle Pfade aus dates, die
    auch in 'files' bekannt sind. Gibt die Trefferzahl zurueck. Analog zu
    sync_rekordbox_presence(), aber mit einem Datumswert statt nur Anwesenheit.
    """
    known = {r["path"] for r in conn.execute("SELECT path FROM files")}
    # Music.app-Pfade kommen NFC (server.MUSIC_INDEX), die DB fuehrt den Pfad so,
    # wie der Scan ihn fand -- Rueckfall ueber path_norm_key(), frueheres Datum gewinnt.
    by_key = {path_norm_key(p): p for p in known}
    matched: dict[str, float] = {}
    for p, ts in dates.items():
        hit = p if p in known else by_key.get(path_norm_key(p))
        if hit is not None:
            matched[hit] = min(matched.get(hit, ts), ts)
    now = time.time()
    conn.execute("DELETE FROM music_added")
    conn.executemany(
        "INSERT OR REPLACE INTO music_added (path, added_ts, ts) VALUES (?, ?, ?)",
        [(p, ts, now) for p, ts in matched.items()],
    )
    conn.commit()
    return len(matched)


def mark_music_added(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Punktuelles Update nach einem eigenen Import in die Music-Bibliothek
    (siehe media.add_to_music_library) -- Gegenstueck zu
    mark_rekordbox_present(). added_ts ist bewusst der Zeitpunkt des Imports
    und nicht das von Music.app gefuehrte 'date added': das laesst sich nur
    ueber einen vollen Bibliotheks-Abgleich lesen (media.music_file_tracks(),
    eine Sammelabfrage ueber alle Tracks). Beides liegt hoechstens Sekunden
    auseinander; der naechste 'Music.app abgleichen' ersetzt den Wert
    ohnehin durch den echten.

    Ueberschreibt einen vorhandenen Eintrag absichtlich: wer eine Datei
    erneut importiert, will sie in der nach 'Hinzugefügt' sortierten Liste
    oben sehen.
    """
    now = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO music_added (path, added_ts, ts) VALUES (?, ?, ?)",
        [(p, now, now) for p in paths],
    )
    conn.commit()


def set_rekordbox_present(conn: sqlite3.Connection, path: str, present: bool) -> None:
    """Punktueller Praesenz-Check fuer GENAU einen Pfad -- Gegenstueck zu
    sync_rekordbox_presence() (voller Abgleich, ersetzt den kompletten
    Cache und ist deshalb fuer einen Einzel-Check ungeeignet, siehe dort).
    Schreibt/loescht nur die eine Zeile, alle anderen Pfade bleiben
    unangetastet."""
    if present:
        conn.execute(
            "INSERT OR REPLACE INTO rekordbox (path, ts) VALUES (?, ?)",
            (path, time.time()))
    else:
        conn.execute("DELETE FROM rekordbox WHERE path = ?", (path,))
    conn.commit()


def set_music_added(conn: sqlite3.Connection, path: str, added_ts: float | None) -> None:
    """Punktueller Music.app-Check fuer GENAU einen Pfad -- Gegenstueck zu
    sync_music_added() (voller Abgleich), analog zu set_rekordbox_present().
    added_ts=None (kein Treffer/Titel unbekannt/Track nicht mehr gefunden)
    loescht den Eintrag statt einer leeren Zeile."""
    if added_ts:
        conn.execute(
            "INSERT OR REPLACE INTO music_added (path, added_ts, ts) VALUES (?, ?, ?)",
            (path, added_ts, time.time()))
    else:
        conn.execute("DELETE FROM music_added WHERE path = ?", (path,))
    conn.commit()


def log_event(conn: sqlite3.Connection, kind: str, path: str, *,
              duration_s: float | None = None, in_music: bool = False) -> None:
    """Haengt eine Zeile ans Ereignisprotokoll (Tabelle 'events') an --
    Rohdaten fuer die spaetere Statistik-Ansicht, siehe Issue #15/#16."""
    conn.execute(
        "INSERT INTO events (ts, kind, path, duration_s, in_music) VALUES (?, ?, ?, ?, ?)",
        (time.time(), kind, path, duration_s, int(in_music)),
    )
    conn.commit()


def waveform_get(conn: sqlite3.Connection, path: str, mtime: float) -> list | None:
    """Gespeicherte Huellkurve, sofern die Datei sich nicht geaendert hat."""
    row = conn.execute(
        "SELECT mtime, peaks FROM waveform WHERE path = ?", (path,)
    ).fetchone()
    if row is None or abs((row["mtime"] or 0) - mtime) > 1.0:
        return None
    try:
        return json.loads(row["peaks"] or "[]")
    except (TypeError, ValueError):
        return None


def waveform_color_get(conn: sqlite3.Connection, path: str, mtime: float, style: str) -> bytes | None:
    row = conn.execute("SELECT mtime, style, data FROM waveform_color WHERE path = ?", (path,)).fetchone()
    if row is None or row["style"] != style or abs((row["mtime"] or 0) - mtime) > 1.0:
        return None
    return row["data"]


def waveform_color_put(conn: sqlite3.Connection, path: str, mtime: float, style: str, data: bytes) -> None:
    conn.execute("INSERT OR REPLACE INTO waveform_color (path, mtime, style, data, ts) VALUES (?, ?, ?, ?, ?)",
                 (path, mtime, style, data, time.time()))
    conn.commit()


def waveform_put(conn: sqlite3.Connection, path: str, mtime: float, peaks: list) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO waveform (path, mtime, peaks, ts) VALUES (?, ?, ?, ?)",
        (path, mtime, json.dumps(peaks), time.time()),
    )
    conn.commit()


def row_for_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """Eine analysierte Datei nachschlagen — zugleich die Freigabepruefung
    fuer alle Endpunkte, die auf Dateien zugreifen."""
    return conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()


def fetch(conn: sqlite3.Connection, verdicts: list[str] | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM files"
    args: list = []
    if verdicts:
        sql += f" WHERE verdict IN ({','.join('?' * len(verdicts))})"
        args = verdicts
    sql += " ORDER BY cutoff_hz ASC, path ASC"
    return list(conn.execute(sql, args))


def summary(conn: sqlite3.Connection) -> dict:
    out = {"total": 0, "by_verdict": {}, "errors": 0, "ignored": 0,
           "favorites": 0, "corrected": 0, "rekordbox": 0}
    out["total"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    out["ignored"] = conn.execute("SELECT COUNT(*) FROM ignored").fetchone()[0]
    # Tracks in mindestens einer als Merkliste markierten Playlist (Nachfolger
    # der frueheren, fest benannten Merklisten-Slots).
    out["favorites"] = conn.execute(
        "SELECT COUNT(DISTINCT path) FROM playlist_items WHERE playlist_id IN "
        "(SELECT id FROM playlists WHERE fav_slot IS NOT NULL)").fetchone()[0]
    out["corrected"] = conn.execute("SELECT COUNT(*) FROM corrected").fetchone()[0]
    out["rekordbox"] = conn.execute("SELECT COUNT(*) FROM rekordbox").fetchone()[0]
    out["errors"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE status != 'ok'"
    ).fetchone()[0]
    for row in conn.execute(
        "SELECT verdict, COUNT(*) c FROM files GROUP BY verdict ORDER BY c DESC"
    ):
        out["by_verdict"][row["verdict"] or "?"] = row["c"]
    return out
