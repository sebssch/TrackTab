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
    integrated_lufs REAL,
    true_peak_dbtp  REAL,
    lra_lu          REAL,
    file_hash       TEXT,
    tag_issues      TEXT
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

-- Datum, an dem ein Track laut Music.app zur Bibliothek hinzugefuegt wurde.
-- Eigener Cache wie 'rekordbox', bewusst getrennt davon (kein gemeinsamer
-- Abgleich): music_added_dates() liest die ganze Music.app-Bibliothek in
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
    fav_slot  INTEGER
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
"""

_COLUMNS = [
    "path", "size", "mtime", "analyzed_at", "status", "error", "duration_s",
    "declared_kbps", "bitrate_mode", "sample_rate", "channels", "encoder",
    "lame_lowpass_hz", "artist", "title", "album", "cutoff_hz", "steepness_db",
    "is_brickwall", "at_nyquist", "gated_blocks", "total_blocks",
    "measured_kbps", "declared_class", "class_steps", "verdict", "confidence",
    "reasons", "spectrum", "codec", "codec_family", "raw_cutoff_hz",
    "integrated_lufs", "true_peak_dbtp", "lra_lu",
    "genre", "bpm", "has_cover",
    "album_artist", "composer", "year", "comment", "track_no", "track_total",
    "file_hash", "tag_issues",
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


def is_current(cache: dict, path: str, size: int, mtime: float) -> bool:
    entry = cache.get(path)
    if entry is None:
        return False
    return entry[0] == size and abs(entry[1] - mtime) < 1.0


def save(conn: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    now = time.time()
    payload = []
    for r in rows:
        r = dict(r)
        r["analyzed_at"] = now
        r["reasons"] = json.dumps(r.get("reasons", []), ensure_ascii=False)
        r["spectrum"] = json.dumps(r.get("spectrum", []))
        r["tag_issues"] = json.dumps(r.get("tag_issues", []), ensure_ascii=False)
        payload.append(tuple(r.get(c) for c in _COLUMNS))
    placeholders = ",".join("?" * len(_COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO files ({','.join(_COLUMNS)}) VALUES ({placeholders})",
        payload,
    )
    conn.commit()


_TAG_FIELDS = ("artist", "title", "album", "album_artist", "composer",
               "genre", "year", "bpm", "comment", "track_no", "track_total")


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
    conn.execute("UPDATE files SET size = ?, mtime = ? WHERE path = ?",
                 (st.st_size, st.st_mtime, path))
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
                "rekordbox", "music_added", "cover_cache", "playlist_items",
                "dup_dismissed")


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
        try:
            st = os.stat(new_path)
            conn.execute("UPDATE files SET size = ?, mtime = ? WHERE path = ?",
                         (st.st_size, st.st_mtime, new_path))
        except OSError:
            pass
    conn.commit()
    return moved_file_row


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
_PRUNE_TABLES = ("files", "waveform", "rekordbox", "music_added")


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

PLAYLIST_KINDS = ("folder", "playlist", "smart")


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

    Was uns gehoert, wird bei jedem Start neu gesetzt: Name, Symbol, Farbe und
    Regelsatz. Der Nutzer kann daran ohnehin nichts aendern (der Server weist
    es ab), und so wirkt eine spaetere Korrektur an der Definition auch auf
    bestehende Datenbanken.

    Was dem Nutzer gehoert, bleibt unangetastet: 'parent_id' und 'seq'. Beides
    laesst sich im Baum per Drag & Drop aendern -- wuerden wir es hier
    zuruecksetzen, spraenge die Anordnung bei jedem Start zurueck.

    Ein versehentlich verlorener Knoten kommt darueber ebenfalls zurueck.
    """
    have = {r["id"] for r in conn.execute("SELECT id FROM playlists")}
    now = time.time()
    for pid, parent, kind, name, icon, color, rules, seq in _SYSTEM_PLAYLISTS:
        rules_json = json.dumps(rules, ensure_ascii=False) if rules else None
        if pid in have:
            conn.execute(
                "UPDATE playlists SET kind = ?, name = ?, icon = ?, color = ?, "
                "rules = ?, system = 1 WHERE id = ?",
                (kind, name, icon, color, rules_json, pid))
        else:
            conn.execute(
                "INSERT INTO playlists (id, parent_id, kind, name, icon, color, rules, "
                "system, seq, ts) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (pid, parent, kind, name, icon, color, rules_json, seq, now))
    conn.commit()


def playlists_all(conn: sqlite3.Connection) -> list[dict]:
    """Alle Knoten als flache Liste, Geschwister nach 'seq' und dann Name."""
    rows = conn.execute(
        "SELECT id, parent_id, kind, name, icon, color, rules, system, seq, fav_slot "
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
        "SELECT id, parent_id, kind, name, icon, color, rules, system, seq, fav_slot "
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
    }
    conn.execute(
        "INSERT OR REPLACE INTO playlists "
        "(id, parent_id, kind, name, icon, color, rules, system, seq, ts, fav_slot) "
        "VALUES (:id, :parent_id, :kind, :name, :icon, :color, :rules, "
        ":system, :seq, :ts, :fav_slot)", fields)
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
    conn.execute(f"DELETE FROM playlists WHERE id IN ({marks})", ids)
    conn.commit()
    return ids


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
    matched = {p: ts for p, ts in dates.items() if p in known}
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
    ueber einen vollen Bibliotheks-Abgleich lesen (music_added_dates(), ein
    Apple Event ueber alle Tracks). Beides liegt hoechstens Sekunden
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
