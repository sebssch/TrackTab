"""
Zugriff auf Rekordbox' eigene Datenbank (master.db, SQLCipher-verschluesselt)
ueber die Drittanbieter-Bibliothek pyrekordbox. Zwei Richtungen:
  1. Lesen: welche Pfade stehen ueberhaupt in der Sammlung (Praesenz-Haken).
  2. Schreiben: Playlisten bearbeiten (hinzufuegen, entfernen, umsortieren)
     und Tracks aus der Sammlung entfernen -- immer als Soft-Delete wie
     Rekordbox selbst (siehe _live()/_soft_delete()).

Rekordbox selbst hat kein CLI/AppleScript-Dictionary -- pyrekordbox liest/
schreibt master.db direkt ueber SQLAlchemy. Bei JEDEM Oeffnen (lesend wie
schreibend) wird zuerst ein rohes Backup von master.db angelegt (die
juengsten 10 bleiben erhalten) -- anders als bei quality.db (backup.py)
geht kein 'VACUUM INTO', weil SQLCipher-Dateien sich nicht mit dem
Standard-sqlite3-Modul oeffnen lassen. Schreiben bei laufendem Rekordbox
riskiert zusaetzlich eine beschaedigte Datenbank, deshalb wird davor
geprueft, dass der Prozess nicht laeuft.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config as cfgmod
from . import media
from . import tags as tags_mod

_BACKUP_PREFIX = "rekordbox-master-"
_BACKUP_KEEP = 10
# Sicherungen beim Neuoeffnen der Lese-Verbindung: eigener Praefix (sonst
# verdraengen sie die vor einem Schreibzugriff), seltener, weniger behalten.
_READ_BACKUP_PREFIX = "rekordbox-read-"
_READ_BACKUP_KEEP = 3
_READ_BACKUP_EVERY = 30 * 60

_HOT_CUE_LETTERS = "ABCDEFGH"

# DjmdCue.Kind -> Hot-Cue-Platz (1 = A). Kind 4 ist KEIN Platz: an echtem Material
# steht dort ausschliesslich der Memory-Loop (561 von 561 Zeilen haben OutMsec),
# die Hot Cues laufen 1, 2, 3 und ab D bei 5 weiter.
_HOT_KIND_SLOT = {1: 1, 2: 2, 3: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8}
_SLOT_HOT_KIND = {slot: kind for kind, slot in _HOT_KIND_SLOT.items()}
_MEMORY_LOOP_KIND = 4
# Color=255: keine Farbe, Rekordbox' Vorgabe bei Loops. Dort schwarz zu sehen, auf der dunklen
# Wellenform ginge das unter, deshalb grau.
_NO_COLOR = 255
_NO_COLOR_RGB = [128, 128, 128]
# So stand "keine Farbe" bei Loops frueher in gespeicherten Bearbeitungen.
_LEGACY_LOOP_RGB = [0, 0, 255]
# Rekordbox rechnet Cue-Zeiten zusaetzlich in 1/150 s (InFrame/OutFrame).
_CUE_FRAMES_PER_S = 150

# DjmdCue.ColorTableIndex -> RGB, NUR fuer Hot Cues (bei Memory Cues siehe
# _MEMORY_CUE_PALETTE). Genau die 16 Farben, die Rekordbox 7 anbietet: je Farbe ein Hot Cue
# dort gesetzt, Index aus der master.db gelesen, RGB aus dem Screenshot gemessen. Andere
# Indizes kommen nicht vor, ausser als Altbestand (siehe _hot_cue_color()).
_HOT_CUE_TABLE_COLORS = {
    1: [58, 89, 246], 5: [107, 178, 249], 9: [102, 221, 251], 14: [78, 161, 146],
    18: [81, 174, 123], 22: [109, 223, 70], 26: [178, 223, 74], 30: [182, 190, 61],
    32: [192, 176, 57], 38: [209, 107, 50], 42: [211, 61, 52], 45: [234, 56, 123],
    49: [205, 80, 201], 56: [166, 61, 246], 60: [162, 116, 247], 62: [103, 115, 246],
}
_HOT_CUE_GREEN_INDEX = 22
# Vorgabe ohne eigene Farbe: das Gruen. Muss zu djHotColor() in dj.js passen.
_HOT_CUE_DEFAULT_COLOR = _HOT_CUE_TABLE_COLORS[_HOT_CUE_GREEN_INDEX]

# Rekordbox' Standard-Rot fuer Memory Cues ohne eigene Farbe -- bestaetigt
# durch Deep-Symmetry/beat-link (CueList.getNexusColor()).
_MEMORY_CUE_DEFAULT_COLOR = [0xFF, 0x00, 0x00]
_MEMORY_CUE_RED_INDEX = 1

# DjmdCue.Color ist bei MEMORY Cues (anders als bei Hot Cues, siehe oben) KEIN gepacktes
# RGB, sondern ein Index. Alle Werte in Rekordbox 7 abgelesen: je ein Memory Cue je Farbe,
# im Kommentar benannt (beat-link nennt dagegen 1 Pink, 2 Rot, 3 Orange). 255 =
# _NO_COLOR, -1 = ohne eigene Farbe, dort rot zu sehen. Die RGB-Werte sind eigene.
_MEMORY_CUE_PALETTE = {
    0: [255, 175, 175],  # Pink
    1: [255, 0, 0],      # Rot
    2: [255, 165, 0],    # Orange
    3: [255, 255, 0],    # Gelb
    4: [0, 255, 0],      # Gruen
    5: [0, 255, 255],    # Aqua
    6: [0, 102, 255],    # Blau
    7: [160, 32, 240],   # Violett
}


class RekordboxUnavailable(RuntimeError):
    """pyrekordbox fehlt oder die Datenbank/der Schluessel wurde nicht gefunden."""


class RekordboxRunning(RuntimeError):
    """Rekordbox laeuft noch -- Schreibzugriff abgelehnt."""


class SmartListUnsupported(Exception):
    """Der Inhalt einer Rekordbox-Smart-Playlist liess sich nicht berechnen.

    pyrekordbox 0.4.4 uebersetzt das gespeicherte Regel-XML in eine
    SQLAlchemy-Bedingung. Fuer zeitbezogene Regeln (Operator IN_LAST mit
    ValueUnit "month"/"day", also z.B. "hinzugefuegt in den letzten 6
    Monaten") schlaegt das mit AttributeError fehl -- 'DjmdContent.StockDate'
    hat kein '.month'. An echtem Material bestaetigt: eine Playlist
    "last-6-month" mit genau dieser Regel. Betrifft nur das LESEN fremder
    Smart Playlists; alles andere in dieser Datei ist davon unberuehrt.

    Zeitregeln werden inzwischen selbst ausgewertet (_smart_contents()); die
    Ausnahme bleibt fuer alles, was auch dort nicht berechenbar ist.
    """


class PlaylistNotFound(RuntimeError):
    """Die konfigurierte Playlist existiert nicht in Rekordbox."""


def _import_pyrekordbox():
    try:
        import pyrekordbox
    except ImportError as exc:
        raise RekordboxUnavailable(
            "pyrekordbox ist nicht installiert. 'pip install pyrekordbox' im "
            "Projekt-venv ausfuehren.") from exc
    return pyrekordbox


def _preferred_rekordbox_version() -> str | None:
    """
    Versionsordner-Name (z.B. 'rekordbox7') aus dem in den Einstellungen
    hinterlegten (oder automatisch gefundenen) Rekordbox-App-Pfad ableiten --
    stehen mehrere Versionen auf dem System, soll gezielt die dort gewaehlte
    benutzt werden statt blind der neuesten (siehe media.rekordbox_path()).
    """
    app_path = media.rekordbox_path(cfgmod.load())
    if not app_path:
        return None
    digits = "".join(ch for ch in Path(app_path).parent.name if ch.isdigit())
    return f"rekordbox{digits}" if digits else None


def _resolve_master_db_path() -> Path:
    """
    Ermittelt master.db selbst, ohne pyrekordbox.get_config() zu benutzen.

    pyrekordbox' eigener Scan stuerzt bei manchen echten
    rekordbox3.settings-Dateien ab: sein XML-Parser geht davon aus, dass
    jeder <VALUE>-Eintrag ohne 'val'-Attribut ein <DEVICESETUP>-Kind hat --
    Rekordbox speichert dort aber z.B. auch Tastaturkuerzel (keyMappings)
    und Tabellenspalten-Layouts (TableHeader-*) ohne DEVICESETUP, was einen
    AttributeError ausloest (pyrekordbox/config.py:read_rekordbox_settings,
    seit 0.4.4 und in 0.5.0 unveraendert, an echtem Material bestaetigt).
    Betrifft vermutlich jede Installation mit angepassten
    Tastenkuerzeln/Tabellenspalten, nicht nur einen Einzelfall.

    Eigener, toleranter Parser: liest ausschliesslich 'masterDbDirectory'
    und ueberspringt alle anderen (moeglicherweise kaputten) Eintraege.

    Reihenfolge der versuchten Versionsordner: zuerst die in den
    Einstellungen hinterlegte/automatisch gefundene Version (siehe
    _preferred_rekordbox_version()) -- erst wenn dort nichts Brauchbares
    steht, die uebrigen bekannten Ordner als Rueckfallebene.
    """
    app_support = Path.home() / "Library" / "Application Support" / "Pioneer"
    preferred = _preferred_rekordbox_version()
    names = ([preferred] if preferred else []) + \
            [n for n in ("rekordbox7", "rekordbox6") if n != preferred]
    for name in names:
        settings_path = app_support / name / "rekordbox3.settings"
        if not settings_path.is_file():
            continue
        try:
            tree = ET.parse(settings_path)
        except ET.ParseError:
            continue
        for el in tree.findall("VALUE"):
            if el.attrib.get("name") == "masterDbDirectory" and "val" in el.attrib:
                candidate = Path(el.attrib["val"]) / "master.db"
                if candidate.is_file():
                    return candidate
    raise RekordboxUnavailable(
        "Keine Rekordbox-Installation gefunden (master.db nicht auffindbar).")


def _stub_pyrekordbox_config(pyrekordbox_mod) -> None:
    """
    Verhindert, dass MasterDatabase() beim Start intern erneut
    get_config()/update_config() aufruft und denselben Fehler wie oben
    ausloest -- 'path' wird unten ohnehin immer explizit vorgegeben, der
    Inhalt dieses Platzhalters wird also nie gelesen. Nur gesetzt, wenn der
    Cache noch leer ist, damit ein spaeteres pyrekordbox-Update, das den
    Parser repariert, nicht ueberschrieben wird.
    """
    from pyrekordbox import config as pyrb_config
    for section in ("rekordbox7", "rekordbox6"):
        if not pyrb_config.__config__.get(section):
            pyrb_config.__config__[section] = {"_tracktab_stub": True}


def _db_stamp(db_path: Path | None = None) -> tuple:
    """Stand von master.db UND master.db-wal (mtime, Groesse). Rekordbox laeuft im
    WAL-Modus: was seit dem letzten Checkpoint geschrieben wurde, steht nur in der
    Nebendatei, die Hauptdatei behaelt ihren alten Zeitstempel (an echtem Material:
    Hauptdatei einen Tag aelter als die Nebendatei)."""
    db_path = db_path or _resolve_master_db_path()
    out = []
    for p in (db_path, Path(f"{db_path}-wal")):
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _prune_backups(dest_dir: Path, prefix: str, keep: int) -> None:
    backups = sorted(dest_dir.glob(f"{prefix}*.db"))
    for p in backups[:-keep] if keep else backups:
        p.unlink(missing_ok=True)
        Path(f"{p}-wal").unlink(missing_ok=True)


def _backup_master_db(src: Path, db=None, prefix: str = _BACKUP_PREFIX,
                      keep: int = _BACKUP_KEEP) -> Path:
    """
    Vollstaendige Sicherung der master.db samt dem, was nur im WAL steht. Mit
    offener Verbindung 'db' ueber VACUUM INTO (wie rbxport: eine eigenstaendige,
    weiterhin verschluesselte Datei aus einem konsistenten Lese-Stand, auch wenn
    Rekordbox gerade schreibt; an echtem Material rund 1 s fuer 110 MB). Ohne
    Verbindung oder wenn VACUUM scheitert: master.db UND master.db-wal roh kopiert
    (zusammen wieder lesbar; konsistent nur, solange niemand schreibt). Frueher
    wurde nur master.db kopiert -- der Sicherung fehlte dadurch alles seit dem
    letzten Checkpoint. Behaelt die juengsten 'keep' Stueck je Praefix.
    """
    dest_dir = cfgmod.resolve("backup") / "rekordbox"
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"{prefix}{time.strftime('%Y%m%d-%H%M%S')}"
    dest = dest_dir / f"{base}.db"
    n = 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{base}-{n}.db"
    done = False
    if db is not None:
        tmp = dest_dir / f".{dest.name}.tmp"
        tmp.unlink(missing_ok=True)
        try:
            raw = db.engine.raw_connection()
            try:
                cur = raw.cursor()
                cur.execute("VACUUM INTO ?", (str(tmp),))
                cur.close()
            finally:
                raw.close()
            os.replace(tmp, dest)
            done = True
        except Exception:                              # noqa: BLE001
            tmp.unlink(missing_ok=True)
    if not done:
        shutil.copyfile(src, dest)
        wal = Path(f"{src}-wal")
        if wal.is_file() and wal.stat().st_size:
            shutil.copyfile(wal, Path(f"{dest}-wal"))
    _prune_backups(dest_dir, prefix, keep)
    return dest


# Eine Sicherung je Nutzeraktion statt je Schreibzugriff (Sammelaktionen oeffnen
# die master.db je Abschnitt neu). Der Server klammert eine Aktion mit
# backup_session(token); innerhalb desselben Tokens wird nur dann erneut
# gesichert, wenn sich master.db seit unserem letzten Schreiben veraendert hat
# (Rekordbox zwischendurch gestartet und wieder beendet). So bleibt die Sicherung
# genau der Stand VOR der Aktion, ohne fremde Aenderungen zu verlieren.
_session = threading.local()
_session_backup = {"token": None, "stamp": None}
_session_lock = threading.Lock()


class backup_session:
    """with rekordbox.backup_session(token): ... -- leeres Token = ohne Klammer."""

    def __init__(self, token: str | None):
        self.token = str(token or "") or None

    def __enter__(self):
        self.prev = getattr(_session, "token", None)
        _session.token = self.token
        return self

    def __exit__(self, *exc):
        _session.token = self.prev
        return False


def _needs_backup() -> bool:
    token = getattr(_session, "token", None)
    if not token:
        return True
    with _session_lock:
        return not (_session_backup["token"] == token and _session_backup["stamp"] is not None
                    and _session_backup["stamp"] == _db_stamp())


def _note_session(stamp: tuple | None) -> None:
    token = getattr(_session, "token", None)
    with _session_lock:
        if not token:
            _session_backup.update(token=None, stamp=None)
        else:
            _session_backup.update(token=token, stamp=stamp)


def _open(pyrekordbox_mod, backup: bool = True):
    """Frische Verbindung fuer einen Schreibzugriff, vorher (s.o.) eine Sicherung."""
    try:
        db_path = _resolve_master_db_path()
        _stub_pyrekordbox_config(pyrekordbox_mod)
        db = pyrekordbox_mod.MasterDatabase(path=db_path)
    except RekordboxUnavailable:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise RekordboxUnavailable(
            f"Rekordbox-Datenbank konnte nicht geoeffnet werden: {exc}") from exc
    if backup and _needs_backup():
        try:
            _backup_master_db(db_path, db)
        except Exception as exc:                      # noqa: BLE001
            # Ohne Sicherung wird nicht geschrieben.
            try:
                db.close()
            except Exception:                         # noqa: BLE001
                pass
            raise RekordboxUnavailable(f"Sicherung der Rekordbox-Datenbank fehlgeschlagen: {exc}") from exc
        _note_session(None)
    return db


def _with_malformed_retry(pyrekordbox_mod, fn):
    """
    Fuehrt fn(db) auf einer FRISCHEN _open()-Verbindung aus und wiederholt
    genau einmal mit einer weiteren frischen Verbindung, wenn SQLCipher
    'malformed' meldet (siehe _is_stale_cache_error()). Anders als
    _query_cached() geht es hier nicht um eine ueber Stunden gehaltene
    Verbindung, sondern um denselben Fehler in einem anderen, ebenso an
    echtem Material bestaetigten Fall: Rekordbox schreibt/checkpointet
    master.db beim eigenen Beenden, eine Verbindung, die genau in diesem
    Moment geoeffnet wird, kann kurzzeitig denselben Fehler auf voellig
    normalen Tabellen liefern, obwohl die Datei Sekunden spaeter wieder
    fehlerfrei lesbar ist. Ein kurzer Moment Wartezeit vor dem zweiten
    Versuch gibt Rekordbox Zeit, den Checkpoint abzuschliessen.
    """
    db = _open(pyrekordbox_mod)
    try:
        result = fn(db)
    except Exception as exc:                          # noqa: BLE001
        if not _is_stale_cache_error(exc):
            raise
        try:
            db.close()
        except Exception:                              # noqa: BLE001
            pass
        time.sleep(1.0)
        db = _open(pyrekordbox_mod, backup=False)     # Sicherung von eben gilt
        result = fn(db)
    finally:
        try:
            db.close()
        except Exception:                              # noqa: BLE001
            pass
    # Stand nach UNSEREM Schreiben (nach dem Schliessen: SQLite checkpointet dabei
    # ggf. das WAL), siehe backup_session.
    _note_session(_db_stamp())
    return result


def _live(row) -> bool:
    """Rekordbox loescht nie hart, sondern setzt rb_local_deleted=1 (Cloud-
    Sync-Historie, an echtem Material bestaetigt: auch "Von der Sammlung
    entfernen" laesst die Zeilen stehen). pyrekordbox liefert solche Zeilen
    ungefiltert mit -- jede Abfrage, die den sichtbaren Stand meint, muss sie
    selbst aussortieren."""
    return not getattr(row, "rb_local_deleted", 0)


def _live_content(db, path: str):
    """Lebender DjmdContent zu einem Pfad. Nach einem Entfernen und erneuten
    Import fuehrt Rekordbox fuer denselben Pfad eine NEUE Zeile neben der
    toten (an echtem Material: 26 Pfade mit beidem) -- ein blindes .first()
    traf dort mitunter die tote und haengte z.B. neue Playlist-Eintraege an
    einen in Rekordbox unsichtbaren Track."""
    for content in db.get_content(FolderPath=path):
        if _live(content):
            return content
    return None


def _add_content_fresh(db, path: str, **kwargs):
    """
    Wie db.add_content(), aber auch dann, wenn fuer den Pfad nur noch eine
    soft-geloeschte Zeile existiert. pyrekordbox' add_content() lehnt jeden
    vorhandenen FolderPath ab, auch tote -- Rekordbox selbst legt in diesem
    Fall eine neue Zeile an (siehe _live_content()). Der Nachbau folgt
    add_content() aus pyrekordbox Feld fuer Feld (in 0.4.4 und 0.5.0 gleich).
    """
    # .aif kennt pyrekordbox nicht, Rekordbox fuehrt es als AIFF.
    if Path(path).suffix.lower() != ".aif":
        try:
            return db.add_content(path, **kwargs)
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
            live = _live_content(db, path)
            if live is not None:
                # Wettlauf: zwischendurch doch lebend angelegt worden.
                return live
    import datetime
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables

    p = Path(path)
    file_type_string = {"AIF": "AIFF"}.get(p.suffix.lstrip(".").upper(), p.suffix.lstrip(".").upper())
    try:
        file_type = getattr(tables.FileType, file_type_string)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Ungültiger Dateityp: {p.suffix}") from exc
    id_ = str(db.generate_unused_id(tables.DjmdContent))
    file_id = str(db.generate_unused_id(tables.DjmdContent, id_field_name="rb_file_id"))
    content_link = db.get_menu_items(Name="TRACK").one()
    device = db.get_device().first()
    today = datetime.date.today()
    content = tables.DjmdContent.create(
        ID=id_, UUID=str(uuid4()), ContentLink=content_link.rb_local_usn,
        DateCreated=today, DeviceID=device.ID, FileNameL=p.name,
        FileSize=p.stat().st_size, FileType=file_type.value,
        FolderPath=str(p), HotCueAutoLoad="on", MasterDBID=device.MasterDBID,
        MasterSongID=id_, StockDate=today, rb_file_id=file_id, **kwargs)
    db.add(content)
    db.flush()
    return content


def collection_paths() -> set[str]:
    """Alle Dateipfade in der Rekordbox-Sammlung (read-only)."""
    pyrekordbox_mod = _import_pyrekordbox()
    return _with_malformed_retry(
        pyrekordbox_mod,
        lambda db: {c.FolderPath for c in db.get_content() if c.FolderPath and _live(c)})


_cached_db = None            # (MasterDatabase, master_db_path, _db_stamp())
_last_read_backup = 0.0
_cache_lock = threading.Lock()
# Die gecachte Verbindung ist EINE SQLAlchemy-Session -- nicht thread-sicher.
# Der Server bedient Anfragen parallel (mehrere aufgeklappte Zeilen holen
# gleichzeitig Cues/Waveform); ohne diese Sperre kamen sporadisch Fehler,
# die als "nicht in Rekordbox" verschluckt wurden (graue Wellenform).
# RLock, falls fn() selbst wieder _query_cached() ruft.
_query_lock = threading.RLock()


def _open_cached(pyrekordbox_mod):
    """
    Haelt eine einzelne MasterDatabase-Verbindung offen und erneuert sie
    nur, wenn sich master.db seitdem geaendert hat -- anders als _open()
    (fuer seltene Vorgaenge wie Praesenz-Abgleich/Playlist-Schreibzugriff)
    waere ein Voll-Backup+Neuoeffnen bei JEDER einzelnen Cue-/Waveform-
    Abfrage (potenziell eine pro im Player aufgeklappter Zeile) unnoetig
    teuer.
    """
    global _cached_db, _last_read_backup
    db_path = _resolve_master_db_path()
    # Schluessel mit WAL: Rekordbox schreibt laufend nur in master.db-wal, ein Blick
    # auf die Hauptdatei allein sah diese Aenderungen nie. Neu geoeffnet wird auch
    # deshalb, weil eine lange offene Sitzung auf einem alten Lese-Stand stehen kann.
    stamp = _db_stamp(db_path)
    with _cache_lock:
        if _cached_db is not None and _cached_db[1] == db_path and _cached_db[2] == stamp:
            return _cached_db[0]
        if _cached_db is not None:
            try:
                _cached_db[0].close()
            except Exception:                          # noqa: BLE001
                pass
        _stub_pyrekordbox_config(pyrekordbox_mod)
        db = pyrekordbox_mod.MasterDatabase(path=db_path)
        # Lesend nur selten sichern und getrennt von den Sicherungen vor einem
        # Schreibzugriff (die sonst von Lese-Sicherungen verdraengt wuerden).
        if time.time() - _last_read_backup > _READ_BACKUP_EVERY:
            try:
                _backup_master_db(db_path, db, _READ_BACKUP_PREFIX, _READ_BACKUP_KEEP)
                _last_read_backup = time.time()
            except Exception:                          # noqa: BLE001
                pass
        _cached_db = (db, db_path, stamp)
        return db


def _is_stale_cache_error(exc: Exception) -> bool:
    """
    Eine ueber Stunden offen gehaltene _open_cached()-Verbindung kann
    SQLCipher-Fehler wie 'database disk image is malformed' auf ganz
    normalen Tabellen ausloesen, obwohl eine frische Verbindung zur exakt
    selben master.db im selben Moment fehlerfrei liest (an echtem Material
    bestaetigt: /api/rekordbox-playlists lieferte diesen Fehler stundenlang
    zuverlaessig, ein Neustart der App loeste ihn sofort). Kein Zeichen
    einer tatsaechlich beschaedigten Datei, sondern ein Zustand der
    gecachten Verbindung selbst -- _query_cached() faengt das ab.
    """
    return "malformed" in str(exc).lower()


def _query_cached(pyrekordbox_mod, fn):
    """
    Fuehrt fn(db) auf der Verbindung aus _open_cached() aus. Meldet
    SQLCipher dabei einen Fehler nach _is_stale_cache_error(), wird die
    gecachte Verbindung verworfen und fn() einmal mit einer frischen
    Verbindung wiederholt, statt den rohen Fehler an den Nutzer
    durchzureichen.
    """
    global _cached_db
    with _query_lock:
        db = _open_cached(pyrekordbox_mod)
        try:
            return fn(db)
        except Exception as exc:                      # noqa: BLE001
            if not _is_stale_cache_error(exc):
                raise
            with _cache_lock:
                if _cached_db is not None:
                    try:
                        _cached_db[0].close()
                    except Exception:                  # noqa: BLE001
                        pass
                    _cached_db = None
            db = _open_cached(pyrekordbox_mod)
            return fn(db)


def _case_correct_path(path: str) -> str | None:
    """Ermittelt die tatsaechliche Gross-/Kleinschreibung eines Pfades, der
    laut Path.is_file() existiert. macOS-Standardvolumes (APFS) sind case-
    insensitiv, aber case-preserving: Path.is_file() findet eine Datei auch
    unter falscher Schreibung, os.path.realpath() korrigiert das NICHT (kein
    Case-Canonicalizing, nur Symlink-Aufloesung). Ohne diese Funktion faellt
    ein reiner Gross-/Kleinschreibungs-Unterschied (z.B. nachdem Music.app
    einen Interpreten-Ordner nach einer Zusammenfuehrung selbst umbenannt
    hat) bei stale_content_entries() durch -- der is_file()-Check haette den
    Eintrag faelschlich als "noch gueltig" durchgehen lassen.

    Geht den Pfad Komponente fuer Komponente vom Root aus durch und gleicht
    jede gegen das tatsaechliche Verzeichnis ab (os.scandir, case-insensitiv
    nur als Fallback). Liefert None, wenn der Pfad gar nicht existiert oder
    die Schreibung bereits exakt passt -- ein echter, aber im Ergebnis
    unveraenderter String waere fuer den Aufrufer nicht von "nichts zu tun"
    zu unterscheiden.
    """
    p = Path(path)
    if not p.is_file():
        return None
    parts = p.parts
    current = Path(parts[0])
    changed = False
    for part in parts[1:]:
        try:
            names = [e.name for e in os.scandir(current)]
        except OSError:
            return None
        if part in names:
            real = part
        else:
            lower_map = {n.lower(): n for n in names}
            real = lower_map.get(part.lower())
            if real is None:
                return None
            changed = True
        current = current / real
    return str(current) if changed else None


def stale_content_entries() -> list[dict]:
    """DjmdContent-Zeilen, deren FolderPath nicht mehr (unveraendert) gueltig
    ist -- Kandidaten fuer den Pfad-Korrektur-Schritt des Rekordbox-Abgleich-
    Popups (siehe server._post_rekordbox_scan()). Zwei Faelle, unterschieden
    ueber 'case_fix':
      - Datei fehlt komplett (case_fix=None): server._post_rekordbox_scan()
        sucht sie ueber scanner.find_moved_among() unter unseren eigenen
        Pfaden.
      - Datei existiert, aber nur unter anderer Gross-/Kleinschreibung
        (case_fix=<korrekter Pfad>, siehe _case_correct_path()): der Zielpfad
        steht bereits fest, keine Fuzzy-Suche noetig.

    Ueber _open_cached() gelesen: reiner Lesevorgang, potenziell bei jedem
    Oeffnen des Popups aufgerufen -- ein Voll-Backup pro Aufruf (wie bei
    _open()/collection_paths()) waere hier unnoetig teuer, siehe
    _open_cached() fuer denselben Kompromiss bei Cue-/Waveform-Abfragen.

    ANNAHME: DjmdContent.Length wird hier 1:1 als Sekunden interpretiert --
    im pyrekordbox-Schema nicht dokumentiert (nur "The length of the
    track."). Vor Verwendung in der Praxis an einem bereits korrekt
    verlinkten Track gegen dessen eigene duration_s abgleichen.
    """
    pyrekordbox_mod = _import_pyrekordbox()

    def _query(db):
        out: list[dict] = []
        for c in db.get_content():
            path = c.FolderPath or ""
            if not path or not _live(c):
                continue
            case_fix = None
            if Path(path).is_file():
                case_fix = _case_correct_path(path)
                if case_fix is None:
                    continue
            artist = getattr(c, "Artist", None)
            out.append({
                "id": c.ID,
                "path": path,
                "size": c.FileSize or 0,
                "duration_s": float(c.Length or 0),
                "artist": getattr(artist, "Name", "") or "",
                "title": c.Title or "",
                "case_fix": case_fix,
            })
        return out

    return _query_cached(pyrekordbox_mod, _query)


def _hot_cue_color(color: int | None, color_table_index: int | None) -> list[int]:
    """Farbe eines Hot Cues. Nur die geprueften Farben: alles andere (kein Index, Altbestand
    wie 0, 21, 34, 53) gilt als Standard-Gruen und bleibt beim Schreiben unberuehrt, solange
    keine neue Farbe gewaehlt wird (_still_default_color())."""
    return list(_HOT_CUE_TABLE_COLORS.get(color_table_index, _HOT_CUE_DEFAULT_COLOR))


def _memory_cue_color(color: int | None) -> list[int]:
    """Farbe eines Memory Cues ueber die 8-Farben-Palette (siehe
    _MEMORY_CUE_PALETTE); ohne zugewiesene Farbe Rekordbox' Standard-Rot."""
    if color == _NO_COLOR:
        return list(_NO_COLOR_RGB)
    if color is not None and color in _MEMORY_CUE_PALETTE:
        return list(_MEMORY_CUE_PALETTE[color])
    return list(_MEMORY_CUE_DEFAULT_COLOR)


def track_wave_detail(path: str, style: str) -> bytes | None:
    """Rekordbox' feine Wellenform (150 Spalten/s) im Format von
    wavedetail.STYLED_RATE. None = Track oder Analysedatei fehlt. RGB aus dem Tag PWV5
    (.EXT, 2 Byte: je 3 Bit r/g/b, 5 Bit Hoehe), 3-Band aus PWV7 (.2EX, Tief/Mitten/
    Hoehen, Werte bis rund 126)."""
    import numpy as np

    def _query(db):
        content = _live_content(db, path)
        if content is None or not content.Analysed:
            return None
        if style == "3band":
            anlz = db.read_anlz_file(content, "2EX")
            if anlz is None or "PWV7" not in anlz.tag_types:
                return None
            raw = np.frombuffer(bytes(anlz.get_tag("PWV7").content.entries), dtype=np.uint8).reshape(-1, 3)
            out = np.zeros((len(raw), 4), dtype=np.uint8)
            out[:, :3] = (np.minimum(raw.astype(np.uint16), 126) * 255 // 126).astype(np.uint8)
            return out.tobytes()
        anlz = db.read_anlz_file(content, "EXT")
        if anlz is None or "PWV5" not in anlz.tag_types:
            return None
        v = np.array(list(anlz.get_tag("PWV5").content.entries), dtype=np.uint32)
        out = np.empty((len(v), 4), dtype=np.uint8)
        for k, shift in enumerate((13, 10, 7)):
            out[:, k] = ((v >> shift) & 7) * 255 // 7
        out[:, 3] = ((v >> 2) & 31) * 255 // 31
        return out.tobytes()
    try:
        return _query_cached(_import_pyrekordbox(), _query) or None
    except Exception:                                   # noqa: BLE001
        return None


def _phrase_kind(mood: int, kind: int, k1: int, k2: int, k3: int) -> tuple | None:
    """Rekordbox' PSSI-Eintrag als (Art, Nummer); 0 = ohne Nummer. Stimmung
    1 = hoch (Up/Down), 2 = mittel, 3 = niedrig (beide Verse/Bridge)."""
    if mood == 1:
        if kind == 1:
            return ("intro", 1 if k1 == 1 else 2)
        if kind == 2:
            # k2/k3 0/0 = Up 1, 0/1 = Up 2, 1/0 = Up 3 (in den Dateien kommt 1/1 nie vor)
            return ("up", 3 if k2 else (2 if k3 else 1))
        if kind == 3:
            return ("down", 0)
        if kind == 5:
            return ("chorus", 1 if k1 == 1 else 2)
        if kind == 6:
            return ("outro", 1 if k1 == 1 else 2)
        return None
    if kind == 1:
        return ("intro", 0)
    if kind == 8:
        return ("bridge", 0)
    if kind == 9:
        return ("chorus", 0)
    if kind == 10:
        return ("outro", 0)
    if 2 <= kind <= 7:
        return ("verse", kind - 1 if mood == 2 else (1 if kind <= 4 else 2))
    return None


def _track_grid(db, path: str) -> dict | None:
    content = _live_content(db, path)
    if content is None or not content.Analysed:
        return None
    dat = db.read_anlz_file(content, "DAT")
    if dat is None or "PQTZ" not in dat:
        return None
    grid = dat.get_tag("PQTZ").content.entries
    if not grid:
        return None
    result = {"beats": [[int(b.time), int(b.beat)] for b in grid], "phrases": [], "mood": None}
    ext = db.read_anlz_file(content, "EXT")
    if ext is None or "PSSI" not in ext:
        return result
    pssi = ext.get_tag("PSSI").content
    times = [b.time / 1000.0 for b in grid]
    last_beat = 6000.0 / grid[-1].tempo if grid[-1].tempo else 0.5

    def beat_time(n: int) -> float:
        # Beats zaehlen ab 1; ueber das Grid hinaus mit dem letzten Tempo weiter.
        if n < 1:
            return times[0]
        return times[n - 1] if n <= len(times) else times[-1] + (n - len(times)) * last_beat

    phrases = []
    for i, e in enumerate(pssi.entries):
        nxt = pssi.entries[i + 1].beat if i + 1 < pssi.len_entries else pssi.end_beat
        found = _phrase_kind(pssi.mood, e.kind, e.k1, e.k2, e.k3)
        if found and nxt > e.beat:
            phrases.append({"start": beat_time(e.beat), "end": beat_time(nxt),
                            "kind": found[0], "num": found[1]})
    return {**result, "phrases": phrases, "mood": pssi.mood - 1}


def get_track_extras(path: str) -> dict | None:
    """
    Hot Cues und Memory Cues aus Rekordbox fuer EINEN Track -- nur wenn der
    Track dort bereits in der Sammlung UND analysiert ist. Die Wellenform von
    Bearbeiten und Player erzeugt TrackTab immer selbst (/api/wave-color).

    Liefert None, wenn der Track dort nicht (analysiert) vorliegt. Ein
    Lesefehler (master.db gerade gesperrt, pyrekordbox fehlt, ...) kommt als
    {"error": ...} zurueck, damit der Client es erneut versuchen kann statt
    dauerhaft die graue Standardanzeige zu zeigen.
    """
    try:
        pyrekordbox_mod = _import_pyrekordbox()
        return _query_cached(pyrekordbox_mod, lambda db: _track_extras(db, path))
    except Exception as exc:                           # noqa: BLE001
        return {"error": str(exc) or exc.__class__.__name__}


def _track_extras(db, path: str) -> dict | None:
    content = _live_content(db, path)
    if content is None or not content.Analysed:
        return None

    cues = []
    for c in db.get_cue(ContentID=content.ID).all():
        position_s = (c.InMsec or 0) / 1000
        # ANNAHME, nicht von pyrekordbox dokumentiert: Kind 1..8 entspricht
        # Hot Cue A..H in dieser Reihenfolge -- aber NUR fuer reine
        # Punkt-Cues (OutMsec leer). An echtem Material bestaetigt: eine
        # Schleife (OutMsec gesetzt) mit Kind>0 ist trotz pyrekordbox'
        # eigenem is_hot_cue==True KEIN Hot Cue auf einem Pad, sondern
        # eine Loop-Markierung, die zufaellig im 1-8-Bereich liegt (siehe
        # HANDBUCH/Plan) -- sonst erscheinen Phantom-Hot-Cues.
        is_loop = c.OutMsec is not None and c.OutMsec >= 0
        if is_loop:
            # Schleifen/Bereichs-Markierungen (egal ob Kind==0 oder
            # faelschlich im 1-8-Bereich, siehe oben) sind keine vom
            # Nutzer per Memory-Cue-Taste gesetzten Punkte -- an echtem
            # Material auf Nutzerwunsch ganz ausgeblendet, nicht nur aus
            # den Hot Cues entfernt.
            continue
        if c.Kind in _HOT_KIND_SLOT:
            slot = _HOT_KIND_SLOT[c.Kind]
            cues.append({
                "kind": "hot", "slot": slot,
                "label": _HOT_CUE_LETTERS[slot - 1],
                "position_s": position_s,
                "color": _hot_cue_color(c.Color, c.ColorTableIndex),
            })
        else:
            cues.append({
                "kind": "memory", "slot": None,
                "label": c.Comment or "",
                "position_s": position_s,
                "color": _memory_cue_color(c.Color),
            })

    return {"cues": cues}


# ── DJ-Ansicht: lesen und abgleichen ───────────────────────────────────────

def _track_cues(db, content) -> list[dict]:
    """Alle lebenden Cues eines Tracks fuer den DJ-Editor, anders als
    _track_extras() auch die Loops."""
    cues: list[dict] = []
    seen_slots: set[int] = set()
    for c in db.get_cue(ContentID=content.ID).all():
        if not _live(c):
            continue
        loop = c.OutMsec / 1000 if c.OutMsec is not None and c.OutMsec >= 0 else None
        slot = _HOT_KIND_SLOT.get(c.Kind)
        if slot is not None and slot not in seen_slots:
            seen_slots.add(slot)
            color = _hot_cue_color(c.Color, c.ColorTableIndex)
            cues.append({"kind": "hot", "slot": slot, "label": _HOT_CUE_LETTERS[slot - 1],
                         "name": c.Comment or "", "position_s": (c.InMsec or 0) / 1000,
                         "loop_s": loop, "color": list(color)})
        elif slot is None:
            color = _memory_cue_color(c.Color)
            cues.append({"kind": "memory", "slot": None, "label": c.Comment or "",
                         "name": c.Comment or "", "position_s": (c.InMsec or 0) / 1000,
                         "loop_s": loop, "color": list(color),
                         # Nur wenn gesetzt: die Pruefsumme normaler Cues bleibt gleich.
                         **({"active": True} if c.Kind == _MEMORY_LOOP_KIND and loop is not None else {})})
    cues.sort(key=lambda x: x["position_s"])
    return cues


# Lesart von Beatgrid, Phrasen und Cues (_track_grid(), _phrase_kind(), _track_cues()).
# ERHOEHEN, sobald sich aendert, WIE gelesen wird (z. B. die Nummer einer Up-Phrase):
# Der beim Abgleich festgehaltene Stand (rb_snapshot) traegt sie im Stempel und wird
# dann still nachgelesen, statt jeden Track als "in Rekordbox geaendert" zu melden
# (server._rb_snapshot_reread()). 2 = Up-Nummer aus k2/k3 (Up 2 = finaler Aufbau).
READER_VERSION = 2


def track_state(path: str) -> dict:
    """Was Rekordbox gerade zu einem Track weiss. Zeiten auf SEINER Zeitachse.
    {"available": False} = Rekordbox nicht lesbar; "present" = in der Sammlung,
    "analysed" = es gibt ANLZ-Dateien, in die Beatgrid und Phrasen koennen."""
    def _query(db):
        content = _live_content(db, path)
        if content is None:
            return {"available": True, "present": False, "analysed": False,
                    "grid": [], "phrases": [], "cues": []}
        grid = _track_grid(db, path) if content.Analysed else None
        return {"available": True, "present": True,
                "analysed": bool(content.Analysed and content.AnalysisDataPath),
                "grid": grid["beats"] if grid else [],
                "phrases": grid["phrases"] if grid else [],
                "cues": _track_cues(db, content)}
    try:
        return _query_cached(_import_pyrekordbox(), _query)
    except Exception as exc:                            # noqa: BLE001
        return {"available": False, "error": str(exc) or exc.__class__.__name__,
                "present": False, "analysed": False, "grid": [], "phrases": [], "cues": []}


def track_stamp(path: str) -> str | None:
    """Aenderungsstand eines Tracks, ohne seine Analysedateien zu lesen. None = fehlt."""
    def _query(db):
        c = _live_content(db, path)
        return None if c is None else f"{c.AnalysisUpdated}|{c.CueUpdated}|{c.updated_at}"
    try:
        return _query_cached(_import_pyrekordbox(), _query)
    except Exception:                                   # noqa: BLE001
        return None


def cue_palettes() -> dict:
    """Farbauswahl im Editor: genau die in Rekordbox geprueften Farben, Hot Cues (16, als
    ColorTableIndex) in Rekordbox' Reihenfolge, Memory Cues (8, als Color 0-7) und "keine"."""
    return {"hot": [list(v) for v in _HOT_CUE_TABLE_COLORS.values()],
            "memory": [list(v) for v in _MEMORY_CUE_PALETTE.values()] + [list(_NO_COLOR_RGB)]}


def _nearest(rgb, table: dict) -> int:
    return min(table, key=lambda k: sum((a - b) ** 2 for a, b in zip(table[k], rgb)))


def _cue_fields(cue: dict) -> dict:
    """Unser Cue-Format -> DjmdCue-Spalten. Zeiten schon auf Rekordbox' Achse."""
    in_ms = int(round(cue["position_s"] * 1000))
    loop = cue.get("loop_s")
    out_ms = int(round(loop * 1000)) if loop is not None else -1
    rgb = list(cue.get("color") or [])
    if cue["kind"] == "hot":
        kind = _SLOT_HOT_KIND[int(cue["slot"])]
        color, table = -1, _nearest(rgb or _HOT_CUE_DEFAULT_COLOR, _HOT_CUE_TABLE_COLORS)
    else:
        # An einem Beispiel in Rekordbox abgelesen: Active Memory Loop = Kind 4, normaler
        # Memory Loop = Kind 0 mit Ende. Die Spalte ActiveLoop bleibt dort bei beiden 0.
        kind = _MEMORY_LOOP_KIND if loop is not None and cue.get("active") else 0
        table = 0
        if rgb == _NO_COLOR_RGB or (loop is not None and rgb == _LEGACY_LOOP_RGB):
            color = _NO_COLOR
        else:
            color = _nearest(rgb or _MEMORY_CUE_DEFAULT_COLOR, _MEMORY_CUE_PALETTE)
    return {"Kind": kind, "InMsec": in_ms, "InFrame": in_ms * _CUE_FRAMES_PER_S // 1000,
            "OutMsec": out_ms, "OutFrame": out_ms * _CUE_FRAMES_PER_S // 1000 if out_ms >= 0 else 0,
            "InMpegFrame": 0, "InMpegAbs": 0, "OutMpegFrame": 0, "OutMpegAbs": 0,
            "Color": color, "ColorTableIndex": table, "ActiveLoop": 0,
            "Comment": cue.get("name") or "", "CueMicrosec": 0,
            "BeatLoopSize": _beat_loop_size(cue, in_ms, out_ms)}


def _still_default_color(row, fields: dict) -> bool:
    """Ein Cue ohne eigene Farbe liest sich als Standard-Rot bzw. -Gruen. Kommt genau
    die zurueck, bleibt er ohne eigene Farbe: ein Update soll in Rekordbox nur
    aendern, was wirklich geaendert wurde."""
    if row.Kind == 0 or row.Kind == _MEMORY_LOOP_KIND:
        return row.Color in (None, -1) and fields["Color"] == _MEMORY_CUE_RED_INDEX
    return row.ColorTableIndex not in _HOT_CUE_TABLE_COLORS and fields["ColorTableIndex"] == _HOT_CUE_GREEN_INDEX


def _beat_loop_size(cue: dict, in_ms: int, out_ms: int) -> int:
    """Rekordbox: Schlaege << 16 | 1 (16 Schlaege = 1048577), 0 = kein ganzzahliger Loop."""
    bpm = cue.get("bpm") or 0
    if out_ms < 0 or bpm <= 0:
        return 0
    beats = (out_ms - in_ms) * bpm / 60000
    return (int(round(beats)) << 16) | 1 if abs(beats - round(beats)) < 0.02 and beats >= 0.5 else 0


def _write_cues(db, content, cues: list[dict], now) -> None:
    """Gleicht die Cues eines Tracks ab: Unveraendertes bleibt, Wegfallendes wird
    soft-geloescht, Neues angelegt, und contentCue (JSON-Kopie, die Rekordbox je
    Track fuehrt) wird nachgezogen."""
    import json
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables

    wanted = [_cue_fields(c) for c in cues]
    existing = [c for c in db.get_cue(ContentID=content.ID).all() if _live(c)]
    remaining = list(existing)
    keep: list[tuple] = []
    for fields in wanted:
        match = next((c for c in remaining
                      if c.Kind == fields["Kind"] and c.InMsec == fields["InMsec"]
                      and (c.OutMsec if c.OutMsec is not None else -1) == fields["OutMsec"]), None)
        if match is not None:
            remaining.remove(match)
        keep.append((fields, match))
    _soft_delete(db, remaining, now)
    final = []
    for fields, row in keep:
        if row is None:
            row = tables.DjmdCue(
                ID=str(db.generate_unused_id(tables.DjmdCue, is_28_bit=False)),
                ContentID=content.ID, ContentUUID=content.UUID, UUID=str(uuid4()),
                rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
                rb_local_synced=0, created_at=now, updated_at=now, **fields)
            db.add(row)
        else:
            names = ("Comment", "BeatLoopSize") if _still_default_color(row, fields) \
                else ("Color", "ColorTableIndex", "Comment", "BeatLoopSize")
            changed = {k: fields[k] for k in names if getattr(row, k) != fields[k]}
            if changed:
                _touch(db, [row], now, **changed)
        final.append(row)
    db.flush()

    def stamp(value) -> str:
        return (value or now).strftime("%Y-%m-%dT%H:%M:%S.") + f"{(value or now).microsecond // 1000:03d}+00:00"

    payload = []
    for row in final:
        item = {"ID": row.ID, "ContentID": row.ContentID}
        for col in _STEM_CUE_COLUMNS:
            value = getattr(row, col, None)
            if value is not None and value != "":
                item[col] = value
        item.update(ContentUUID=row.ContentUUID, UUID=row.UUID,
                    created_at=stamp(row.created_at), updated_at=stamp(row.updated_at))
        payload.append(item)
    model = getattr(tables, "ContentCue", None)
    if model is not None:
        cue_row = db.query(model).filter(model.ContentID == content.ID).first()
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if cue_row is not None:
            _touch(db, [cue_row], now, Cues=text)
        elif payload:
            db.add(model(ID=content.UUID, ContentID=content.ID, Cues=text, UUID=str(uuid4()),
                         rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
                         rb_local_synced=0))
    _touch(db, [content], now, CueUpdated=str(int(content.CueUpdated or 0) + 1))


def _set_pqtz(anlz, beats: list[list]) -> None:
    """beats: [[ms, Nummer, BPM], ...]. pyrekordbox' set() kann die Anzahl nicht
    aendern, die Eintraege werden deshalb direkt ersetzt."""
    from construct import Container

    tag = anlz.get_tag("PQTZ")
    tag.struct.content.entries = [
        Container(beat=int(n), tempo=int(round(bpm * 100)), time=int(ms)) for ms, n, bpm in beats]
    tag.struct.content.entry_count = len(beats)
    tag.update_len()


def _set_pqt2(anlz, beats: list[list]) -> None:
    """PQT2 (Beatgrid fuer neuere Player): Anfang und Ende stehen im Kopf, je Schlag
    zwei Bytes, deren Bedeutung nicht bekannt ist. Anfang, Ende und Anzahl werden
    nachgezogen, fehlende Eintraege wiederholen den letzten."""
    if "PQT2" not in anlz:
        return
    from construct import Container

    tag = anlz.get_tag("PQT2")
    content = tag.struct.content
    for entry, (ms, num, bpm) in zip(content.bpm, (beats[0], beats[-1])):
        entry.beat, entry.tempo, entry.time = int(num), int(round(bpm * 100)), int(ms)
    # Hat Rekordbox das PQT2 leer angelegt (entry_count 0), fehlt der Schluessel
    # "entries" ganz -- content.entries wirft dann AttributeError.
    old = list(content.get("entries") or [])
    filler = old[-1] if old else Container(beat=0, unkown=0)
    content.entries = (old + [filler] * len(beats))[:len(beats)]
    content.entry_count = len(beats)
    tag.struct.len_tag = tag.struct.len_header + 2 * len(beats)


def _pssi_kind(phrase: dict, mood: int) -> tuple[int, int, int, int]:
    """Gegenstueck zu _phrase_kind(): (kind, k1, k2, k3)."""
    kind, num = phrase["kind"], int(phrase.get("num") or 0)
    if mood == 1:
        kind = {"verse": "up", "bridge": "down"}.get(kind, kind)
        if kind == "intro":
            return 1, 1 if num <= 1 else 0, 0, 0
        if kind == "up":
            return 2, 0, 1 if num >= 3 else 0, 1 if num == 2 else 0
        if kind == "down":
            return 3, 0, 0, 0
        if kind == "chorus":
            return 5, 1 if num <= 1 else 0, 0, 0
        return 6, 1 if num <= 1 else 0, 0, 0
    kind = {"up": "verse", "down": "bridge"}.get(kind, kind)
    if kind == "intro":
        return 1, 0, 0, 0
    if kind == "bridge":
        return 8, 0, 0, 0
    if kind == "chorus":
        return 9, 0, 0, 0
    if kind == "outro":
        return 10, 0, 0, 0
    if mood == 2:
        return max(2, min(7, max(1, num) + 1)), 0, 0, 0
    return 2 if num <= 1 else 5, 0, 0, 0


def _phrase_beat(times: list[float], sec: float) -> int:
    """1-basierte Nummer des naechsten Schlags."""
    import bisect
    i = bisect.bisect_left(times, sec)
    if i >= len(times):
        return len(times)
    if i > 0 and sec - times[i - 1] <= times[i] - sec:
        return i
    return i + 1


def _number_phrases(ordered: list[dict], mood: int) -> list[dict]:
    """Nummer = laufende Zahl innerhalb der Kategorie, wie djRenumberPhrases(). Was
    Rekordbox davon darstellen kann, entscheidet _pssi_kind(): mittel Verse 1-6, niedrig
    Verse 1-2, hoch Intro/Chorus/Outro 1-2 und Up 1-3, alles andere ohne Nummer."""
    alias = {"verse": "up", "bridge": "down"} if mood == 1 else {"up": "verse", "down": "bridge"}
    count: dict[str, int] = {}
    numbered = []
    for p in ordered:
        cat = alias.get(p["kind"], p["kind"])
        count[cat] = count.get(cat, 0) + 1
        numbered.append({**p, "num": count[cat]})
    return numbered


def _set_pssi(anlz, phrases: list[dict], beat_ms: list[int]) -> None:
    """Schreibt die Phrasen. Stimmung: Verse/Bridge verlangen mittel oder niedrig,
    sonst bleibt die alte. Vorhandene Fill-Marken bleiben, wo der Schlag gleich ist."""
    import struct as struct_mod
    from construct import Container
    from pyrekordbox.anlz.tags import PSSIAnlzTag

    times = [ms / 1000 for ms in beat_ms]
    if "PSSI" not in anlz:
        raw = b"PSSI" + struct_mod.pack(">II", 32, 32) + struct_mod.pack(
            ">HHHH6sH2sB1s", 0, 24, 0, 1, b"\0" * 6, 1, b"\x01\0", 0, b"\0")
        anlz.tags.append(PSSIAnlzTag(raw))
    tag = anlz.get_tag("PSSI")
    content = tag.struct.content
    old_mood = int(content.mood)
    wants_low = any(p["kind"] in ("verse", "bridge") for p in phrases)
    wants_high = any(p["kind"] in ("up", "down") for p in phrases)
    if wants_low and not wants_high:
        mood = old_mood if old_mood in (2, 3) else 2
    elif wants_high and not wants_low:
        mood = 1
    else:
        mood = old_mood
    fills = {e.beat: (e.fill, e.beat_fill) for e in (content.entries or [])}
    ordered = _number_phrases(sorted(phrases, key=lambda p: p["start"]), mood)
    entries = []
    for i, p in enumerate(ordered, start=1):
        beat = _phrase_beat(times, p["start"])
        kind, k1, k2, k3 = _pssi_kind(p, mood)
        fill, beat_fill = fills.get(beat, (0, 0))
        entries.append(Container(index=i, beat=beat, kind=kind, u1=0, k1=k1, u2=0, k2=k2, u3=0, b=0,
                                 beat_2=0, beat_3=0, beat_4=0, u4=0, k3=k3, u5=0, fill=fill,
                                 beat_fill=beat_fill))
    content.entries = entries
    content.len_entries = len(entries)
    content.mood = mood
    content.end_beat = _phrase_beat(times, ordered[-1]["end"]) if ordered else 1
    tag.struct.len_tag = tag.struct.len_header + 24 * len(entries)


def _write_analysis(db, content, beats: list[list] | None, phrases: list[dict] | None, now) -> list[str]:
    """Schreibt Beatgrid (DAT + EXT) und Phrasen (EXT) in die ANLZ-Dateien und zieht
    die Pruefsummen in contentFile nach. Vorher landet eine Kopie unter
    backup/rekordbox/anlz/. Geschrieben wird ueber eine Temp-Datei, die sich erst
    wieder parsen lassen muss. Liefert die geschriebenen Dateinamen."""
    import hashlib
    from pyrekordbox.anlz import AnlzFile
    from pyrekordbox.masterdb import models as tables

    anlz_dir = db.get_anlz_dir(content)
    if phrases is not None and beats is None:
        grid = AnlzFile.parse_file(anlz_dir / "ANLZ0000.DAT").get_tag("PQTZ").content.entries
        beat_ms = [int(e.time) for e in grid]
    else:
        beat_ms = [int(b[0]) for b in beats or []]

    updates: list[tuple[str, object]] = []
    if beats is not None:
        dat = AnlzFile.parse_file(anlz_dir / "ANLZ0000.DAT")
        _set_pqtz(dat, beats)
        updates.append(("ANLZ0000.DAT", dat))
    ext_path = anlz_dir / "ANLZ0000.EXT"
    if ext_path.is_file():
        ext = AnlzFile.parse_file(ext_path)
        if beats is not None:
            _set_pqt2(ext, beats)
        if phrases is not None:
            _set_pssi(ext, phrases, beat_ms)
        updates.append(("ANLZ0000.EXT", ext))
    elif phrases is not None:
        raise RuntimeError("Rekordbox hat für diesen Track keine EXT-Datei (Phrasen)")

    built: list[tuple[str, bytes]] = []
    for name, anlz in updates:
        data = anlz.build()
        AnlzFile.parse(data)               # was nicht lesbar ist, geht nicht auf die Platte
        built.append((name, data))

    backup_dir = cfgmod.resolve("backup") / "rekordbox" / "anlz" / time.strftime("%Y%m%d-%H%M%S") / content.UUID
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name, data in built:
        target = anlz_dir / name
        shutil.copy2(target, backup_dir / name)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    model = getattr(tables, "ContentFile", None)
    if model is not None:
        rows = {Path(str(r.Path)).name: r for r in
                db.query(model).filter(model.ContentID == content.ID) if _live(r)}
        for name, data in built:
            row = rows.get(name)
            if row is not None:
                _touch(db, [row], now, Hash=hashlib.md5(data).hexdigest(), Size=len(data))
    return [name for name, _data in built]


# djmdContent.ContentLink merkt sich, welche Analysedaten es gibt (an 5.884 Tracks abgelesen):
# PSSI 0x010000, PVDI 0x100000, 3EX 0x200000. PSSI ohne das Bit zeigt Rekordbox nicht an, auch
# wenn die Datei stimmt; die beiden anderen setzt es beim Laden selbst.
_CONTENT_LINK_PSSI = 0x010000
_GRID_START_TOLERANCE_MS = 80
# Analyse-Sperre in djmdContent.Analysed: an einem in Rekordbox gesperrten Track abgelesen
# (233 statt 105, die uebrigen 5.883 Tracks stehen auf 105). Rekordbox kennt nur diese
# eine Sperre fuer Analyse, Tonart und Beatgrid zusammen.
_ANALYSIS_LOCK_BIT = 128


def _grid_from_start(beats: list[list], audio_start_ms: int = 0) -> list[list]:
    """Rekordbox legt den ersten Schlag auf den Audiobeginn, nicht auf 0 ms (an 258
    m4a abgelesen: Median 52 ms, nie 0; der Audiobeginn liegt dort beim Versatz aus
    playback_lead()). Faengt ein bearbeitetes Grid spaeter an, kommen die fehlenden
    Schlaege im gleichen Abstand davor dazu. Liegt der naechste Schlag knapp vor dem
    Audiobeginn, rueckt er darauf: Grid, Phrase und Cue beginnen dann an einer Stelle,
    nur der erste Abstand ist kuerzer (djSegmentsFromBeats() rechnet das wieder heraus)."""
    if not beats:
        return beats
    ms, num, bpm = beats[0]
    period = 60000.0 / bpm if bpm else 0
    if period <= 0:
        return beats
    head: list[list] = []
    t, n = float(ms), int(num)
    while True:
        t -= period
        n = n - 1 or 4
        if t < audio_start_ms - _GRID_START_TOLERANCE_MS:
            break
        head.insert(0, [max(audio_start_ms, int(round(t))), n, bpm])
    return head + beats


def _update_in(db, path: str, cues, beats, phrases, now, tags=None) -> dict:
    """Kern von update_track() in einer offenen Sitzung, ohne commit. beats schon
    ueber _grid_from_start() auf Rekordbox' Achse."""
    content = _live_content(db, path)
    if content is None:
        raise RuntimeError("Der Track steht nicht in der Rekordbox-Sammlung.")
    written: list[str] = []
    skipped: list[str] = []
    if cues is not None:
        _write_cues(db, content, cues, now)
        written.append("cues")
    if tags is not None:
        _write_tags(db, content, tags, now)
        written.append("tags")
    wants_analysis = beats is not None or phrases is not None
    if wants_analysis and not (content.Analysed and content.AnalysisDataPath):
        skipped += [f for f, v in (("grid", beats), ("phrases", phrases)) if v is not None]
    elif wants_analysis:
        _write_analysis(db, content, beats, phrases, now)
        fields: dict = {"AnalysisUpdated": str(int(content.AnalysisUpdated or 0) + 1)}
        if phrases is not None:
            fields["ContentLink"] = int(content.ContentLink or 0) | _CONTENT_LINK_PSSI
        if beats:
            # Tempo des Tracks = haeufigstes Tempo im Grid, wie Rekordbox es anzeigt.
            from collections import Counter
            fields["BPM"] = int(round(Counter(round(b[2], 2) for b in beats).most_common(1)[0][0] * 100))
        _touch(db, [content], now, **fields)
        written += [f for f, v in (("grid", beats), ("phrases", phrases)) if v is not None]
    # Sperren, damit Rekordbox das Geschriebene nicht neu analysiert. Noch nicht
    # analysierte Tracks nicht: das braeuchten sie erst noch.
    if [w for w in written if w != "tags"] and content.Analysed and not int(content.Analysed) & _ANALYSIS_LOCK_BIT:
        _touch(db, [content], now, Analysed=int(content.Analysed) | _ANALYSIS_LOCK_BIT)
    return {"written": written, "skipped": skipped}


def update_track(path: str, *, cues: list[dict] | None = None,
                 beats: list[list] | None = None, phrases: list[dict] | None = None,
                 audio_start_ms: int = 0, tags=None) -> dict:
    """
    Schreibt Bearbeitungen aus der DJ-Ansicht nach Rekordbox. Alle Zeiten schon auf
    Rekordbox' Achse; None = Feld nicht anfassen. beats: [[ms, Nummer, BPM], ...].

    Cues gehen immer (DjmdCue + contentCue). Beatgrid und Phrasen brauchen die ANLZ-
    Dateien, also eine Analyse durch Rekordbox: fehlt sie, stehen sie in "skipped".
    audio_start_ms: wo der Ton auf Rekordbox' Achse beginnt, siehe _grid_from_start().
    Viele Tracks auf einmal: update_tracks().
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    if beats:
        beats = _grid_from_start(beats, audio_start_ms)

    def _run(db):
        import datetime
        result = _update_in(db, path, cues, beats, phrases, datetime.datetime.now(), tags)
        db.commit()
        return result

    return _with_malformed_retry(pyrekordbox_mod, _run)


def update_tracks(items: list[dict]) -> dict:
    """
    update_track() fuer viele Tracks in EINER Sitzung: einmal oeffnen, einmal
    sichern, je Track eine eigene Transaktion (wie rbxport: ein Fehler kostet nur
    diesen Track, und pyrekordbox' commit() prueft vor jedem Track erneut, ob
    Rekordbox gestartet wurde). items: {path, cues, beats, phrases, audio_start_ms}.
    Liefert {"results": {pfad: {written, skipped}}, "errors": {pfad: meldung},
    "stopped": meldung oder ""} -- "stopped": Rekordbox laeuft, Rest nicht geschrieben.
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    prepared = []
    for item in items:
        beats = item.get("beats")
        if beats:
            beats = _grid_from_start(beats, item.get("audio_start_ms") or 0)
        prepared.append((item["path"], item.get("cues"), beats, item.get("phrases"), item.get("tags")))

    def _run(db):
        import datetime
        results: dict = {}
        errors: dict = {}
        stopped = ""
        for path, cues, beats, phrases, tags in prepared:
            if path in results:
                continue
            try:
                if is_running() or agent_running():
                    raise RekordboxRunning(
                        "Rekordbox wurde zwischendurch gestartet. Rekordbox beenden und erneut versuchen.")
                results[path] = _update_in(db, path, cues, beats, phrases, datetime.datetime.now(), tags)
                db.commit()
            except RekordboxRunning as exc:
                db.rollback()
                results.pop(path, None)
                stopped = str(exc)
                break
            except Exception as exc:                  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:                     # noqa: BLE001
                    pass
                results.pop(path, None)
                if "Rekordbox is running" in str(exc):
                    stopped = "Rekordbox wurde zwischendurch gestartet. Rekordbox beenden und erneut versuchen."
                    break
                errors[path] = str(exc)[:300]
        return {"results": results, "errors": errors, "stopped": stopped}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def _template_for_content_files(db):
    """Lebender, analysierter Track mit vollstaendigen contentFile-Zeilen, nur als
    Spaltenvorlage fuer _add_content_files(). None = keiner da."""
    from pyrekordbox.masterdb import models as tables

    model = getattr(tables, "ContentFile", None)
    if model is None:
        return None
    wanted = {*_STEM_ANLZ_FILES, "artwork.jpg"}
    for content in db.get_content():
        if not _live(content) or not content.Analysed or not content.ImagePath:
            continue
        names = {Path(str(r.Path)).name for r in db.query(model).filter(model.ContentID == content.ID) if _live(r)}
        if wanted <= names:
            return content
    return None


def _cover_files(path: str) -> dict[str, bytes]:
    """Cover in Rekordbox' drei Groessen. Leer ohne Cover oder ohne Pillow, Rekordbox
    legt es dann beim Laden selbst an."""
    try:
        import io
        from PIL import Image

        got = tags_mod.read_cover(path)
        if not got:
            return {}
        image = Image.open(io.BytesIO(got[0])).convert("RGB")

        def jpeg(picture) -> bytes:
            buf = io.BytesIO()
            picture.save(buf, "JPEG", quality=90)
            return buf.getvalue()

        return {"artwork.jpg": jpeg(image),
                "artwork_m.jpg": jpeg(image.resize((240, 240), Image.LANCZOS)),
                "artwork_s.jpg": jpeg(image.resize((80, 80), Image.LANCZOS))}
    except Exception:                                   # noqa: BLE001
        return {}


def _authored_analysis(path: str, beats: list[list], phrases: list[dict] | None, lead: int) -> dict:
    """Analysedateien samt Datei-Index und Phrasen, siehe anlzwrite.build()."""
    from pyrekordbox.anlz import AnlzFile
    from . import anlzwrite

    built = anlzwrite.build(path, Path(path).name, beats, lead)
    files = built["files"]
    dat = AnlzFile.parse(files["ANLZ0000.DAT"])
    _stem_pvbr(dat, path)
    files["ANLZ0000.DAT"] = bytes(dat.build())
    if phrases:
        ext = AnlzFile.parse(files["ANLZ0000.EXT"])
        _set_pssi(ext, phrases, [int(b[0]) for b in beats])
        files["ANLZ0000.EXT"] = bytes(ext.build())
    for data in files.values():
        AnlzFile.parse(data)               # was nicht lesbar ist, geht nicht auf die Platte
    return built


def _prepare_new_track(path: str, *, beats: list[list] | None = None,
                       phrases: list[dict] | None = None, lead: int | None = None,
                       audio_start_ms: int = 0) -> dict:
    """Schwerer Teil vor dem Oeffnen der master.db: eigene Analysedateien und Cover,
    nur mit beats und bekanntem lead (Samples, anlzwrite.lead_samples()). Ergebnis
    geht an _insert_track()."""
    authored = None
    if beats and lead is not None:
        beats = _grid_from_start(beats, audio_start_ms)
        authored = _authored_analysis(path, beats, phrases, lead)
    return {"beats": beats, "phrases": phrases, "authored": authored,
            "cover": _cover_files(path) if authored else {}}


def _insert_track(db, path: str, row, cues: list[dict] | None, prep: dict, made: list[Path]):
    """Legt einen der Sammlung noch unbekannten Track an: Tags aus unserer DB-Zeile,
    mit prep (_prepare_new_track()) auch Analysedateien, Cover und Cues. Ohne commit;
    angelegte Ordner landen in 'made', der Aufrufer raeumt sie bei einem Fehler weg.
    Liefert (content, written)."""
    import datetime
    from collections import Counter
    from pathlib import PurePosixPath

    beats, phrases = prep.get("beats"), prep.get("phrases")
    authored, cover = prep.get("authored"), prep.get("cover") or {}
    content = _add_content_fresh(db, path, **_content_tag_kwargs(db, row))
    if row is not None:
        if row["sample_rate"]:
            content.SampleRate = row["sample_rate"]
        if row["declared_kbps"]:
            content.BitRate = row["declared_kbps"]
    written: list[str] = []
    if authored:
        content.BPM = int(round(Counter(round(b[2], 2) for b in beats).most_common(1)[0][0] * 100))
        content.BitDepth = authored["depth"]
        content.Length = int(round((row["duration_s"] if row is not None else 0) or authored["seconds"]))
        rel = PurePosixPath("/PIONEER/USBANLZ") / _uuid_dir(content.UUID)
        content.AnalysisDataPath = str(rel / "ANLZ0000.DAT")
        content.Analysed = 105     # Rekordbox' Wert fuer vollstaendig analysiert
        if phrases:
            content.ContentLink = int(content.ContentLink or 0) | _CONTENT_LINK_PSSI
        db.flush()
        anlz_dir = Path(db.get_anlz_dir(content))
        anlz_dir.mkdir(parents=True, exist_ok=False)
        made.append(anlz_dir)
        files: list[tuple[str, Path]] = []
        for name, data in authored["files"].items():
            (anlz_dir / name).write_bytes(data)
            files.append((str(rel / name), anlz_dir / name))
        if cover:
            art_rel = PurePosixPath("/PIONEER/Artwork") / _uuid_dir(content.UUID)
            art_dir = anlz_dir.parents[2] / "Artwork" / _uuid_dir(content.UUID)
            art_dir.mkdir(parents=True, exist_ok=False)
            made.append(art_dir)
            for name, data in cover.items():
                (art_dir / name).write_bytes(data)
                files.append((str(art_rel / name), art_dir / name))
            content.ImagePath = str(art_rel / "artwork.jpg")
        template = _template_for_content_files(db)
        if template is not None:
            _add_content_files(db, template, content, files)
        written.append("grid")
        if phrases:
            written.append("phrases")
    db.flush()
    if cues:
        _write_cues(db, content, cues, datetime.datetime.now())
        written.append("cues")
    return content, written


def _new_track_skipped(prep: dict, written: list[str]) -> list[str]:
    """Felder, die mitkommen sollten, aber erst nach Rekordbox' eigener Analyse gehen."""
    return [f for f, v in (("grid", prep.get("beats")), ("phrases", prep.get("phrases")))
            if v and f not in written]


def add_track(path: str, row, cues: list[dict] | None, *, beats: list[list] | None = None,
              phrases: list[dict] | None = None, lead: int | None = None,
              audio_start_ms: int = 0) -> dict:
    """Nimmt einen Track in die Rekordbox-Sammlung auf (keine Playlist), mit Tags aus
    unserer DB-Zeile und unseren Cues. Mit beats und bekanntem lead (Samples,
    anlzwrite.lead_samples()) auch mit eigenen Analysedateien und Cover, Rekordbox muss
    dann nicht erst analysieren; ohne das koennen Beatgrid und Phrasen erst nach
    Rekordbox' eigener Analyse folgen. Zeiten schon auf Rekordbox' Achse. Liefert
    {"added", "written", "skipped"}. Derselbe Weg wie add_tracks_to_playlist(prepared=...)."""
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    prep = _prepare_new_track(path, beats=beats, phrases=phrases, lead=lead,
                              audio_start_ms=audio_start_ms)

    def _run(db):
        if _live_content(db, path) is not None:
            return {"added": False, "written": [], "skipped": []}
        made: list[Path] = []
        try:
            _, written = _insert_track(db, path, row, cues, prep, made)
            db.commit()
        except Exception:
            for folder in made:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        return {"added": True, "written": written, "skipped": _new_track_skipped(prep, written)}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def is_running() -> bool:
    """Oeffentlich (nicht nur intern vor add_tracks_to_playlist() benutzt) --
    der Server bietet darueber einen eigenen, leichten Vorab-Check an
    (GET /api/rekordbox-status), damit die Oberflaeche VOR einem Schreib-
    versuch warnen kann, statt erst nach einem fehlgeschlagenen Aufruf."""
    _import_pyrekordbox()
    from pyrekordbox.utils import get_rekordbox_pid
    return bool(get_rekordbox_pid())


def agent_running() -> bool:
    """rekordboxAgent (Cloud-Sync-Hintergrundprozess) laeuft. Er schreibt
    selbst in master.db und kann auch ohne geoeffnetes Rekordbox aktiv sein
    -- is_running() sucht nur den Prozessnamen 'rekordbox' (Version 6 und 7
    heissen beide so), den Agenten erfasst es nicht."""
    _import_pyrekordbox()
    from pyrekordbox.utils import get_rekordbox_agent_pid
    return bool(get_rekordbox_agent_pid())


def _check_writable() -> None:
    """Gemeinsamer Vorspann JEDES Schreibzugriffs auf master.db."""
    if is_running():
        raise RekordboxRunning(
            "Rekordbox ist gerade geöffnet. Bitte Rekordbox beenden und es "
            "anschließend erneut versuchen — ein Schreibzugriff während "
            "Rekordbox läuft könnte die Bibliothek beschädigen.")
    if agent_running():
        raise RekordboxRunning(
            "Der Hintergrunddienst „rekordboxAgent“ läuft noch. Bitte ihn "
            "beenden (Menüleisten-Symbol → Beenden) und es erneut versuchen — "
            "er schreibt ebenfalls in die Rekordbox-Bibliothek.")


def content_present(path: str) -> bool:
    """Leichter Praesenz-Check fuer GENAU einen Pfad -- Gegenstueck zu
    collection_paths() (liest die GESAMTE Sammlung, volles Backup+Neuoeffnen
    ueber _open()). Nutzt denselben get_content(FolderPath=path).first()-
    Zugriff wie _track_extras(), ueber die gecachte Verbindung
    (_query_cached()/_open_cached()) statt _open(): das hier wird beim
    manuellen Neu-Analysieren potenziell einmal je Datei aufgerufen, ein
    Voll-Backup pro Datei waere unnoetig teuer.

    Liefert False bei jedem Fehler (pyrekordbox fehlt, master.db nicht
    gefunden/kaputt) -- ein Reanalyse-Lauf soll nie an einem fehlenden
    Rekordbox scheitern. Der Aufrufer prueft VORHER separat is_running() und
    ruft diese Funktion bei laufendem Rekordbox gar nicht erst auf."""
    try:
        pyrekordbox_mod = _import_pyrekordbox()
        return bool(_query_cached(
            pyrekordbox_mod,
            lambda db: _live_content(db, path) is not None))
    except Exception:                                  # noqa: BLE001
        return False


def master_db_mtime() -> float | None:
    """Reiner Dateicheck (kein Oeffnen) fuer stats.is_stale() -- ob sich
    master.db seit dem letzten Statistik-Bau geaendert hat. None bei jedem
    Fehler (kein pyrekordbox, keine Installation gefunden)."""
    try:
        db_path = _resolve_master_db_path()
        times = [db_path.stat().st_mtime]
        wal = Path(f"{db_path}-wal")
        if wal.exists():
            times.append(wal.stat().st_mtime)      # Aenderungen stehen oft nur im WAL
        return max(times)
    except Exception:                                  # noqa: BLE001
        return None


def read_history_plays() -> list[dict]:
    """
    Rekordbox' eigene Wiedergabe-History (DjmdHistory/DjmdSongHistory) --
    jede Session, die Rekordbox selbst (Software oder von einem CDJ
    importiert) protokolliert hat, unabhaengig von unserer eigenen
    events-Tabelle (die nur Wiedergaben im eingebauten Player zaehlt).

    Liefert je Verlaufseintrag {path, title, artist, genre, year}. Es gibt
    keine Wiedergabedauer je Eintrag (DjmdSongHistory kennt nur, DASS ein
    Track in einer Session lief) -- die Statistik zaehlt deshalb Play-Count,
    nicht Hoerzeit. Zeilen ohne .Content (geloeschter Track), ohne .History
    oder ohne ein lesbares DateCreated werden uebersprungen; ebenso Zeilen
    mit leerem FolderPath (nicht sinnvoll zuordenbar).

    ANNAHME, nicht von pyrekordbox dokumentiert: DjmdHistory.DateCreated ist
    trotz des Namens ein reiner String ("YYYY-MM-DD HH:MM:SS", an echtem
    Material bestaetigt), kein datetime -- anders als z.B. StatsFull's
    eigene created_at/updated_at-Spalten. Nur das Datum (erste 10 Zeichen)
    wird gebraucht, ein kaputter/unerwarteter Wert ueberspringt die Zeile
    statt die ganze Statistik abzubrechen.

    Ueber _query_cached() wie playlists()/content_present() -- reiner
    Lesevorgang, der bei jedem Oeffnen der Statistik-Overlay und bei jedem
    is_stale()-Check anfallen kann, ein Voll-Backup pro Aufruf (wie bei
    _open()) waere hier unnoetig teuer.
    """
    mod = _import_pyrekordbox()

    def _query(db):
        out: list[dict] = []
        for song in db.get_history_songs():
            content = song.Content
            if content is None:
                continue
            history = song.History
            if history is None or not history.DateCreated:
                continue
            try:
                year = int(str(history.DateCreated)[:4])
            except ValueError:
                continue
            path = content.FolderPath or ""
            if not path:
                continue
            artist = getattr(content, "Artist", None)
            genre = getattr(content, "Genre", None)
            out.append({
                "path": path,
                "title": content.Title or "",
                "artist": getattr(artist, "Name", "") or "",
                "genre": getattr(genre, "Name", "") or "",
                "year": year,
            })
        return out

    return _query_cached(mod, _query)


def _find_playlist(db, spec: str):
    """
    Sucht eine Playlist per Namen oder Pfad ('Ordner/Unterordner/Playlist').

    Reiner Namens-Abgleich reicht nicht: an echtem Material bestaetigt gibt
    es Playlists mit demselben Namen in verschiedenen Ordnern (z.B. mehrfach
    'Analyse'). Ein '/'-getrennter Pfad geht vom Wurzelordner ('root' in
    Rekordbox' eigenem Schema) Ebene fuer Ebene abwaerts und ist damit
    eindeutig, auch wenn der reine Name mehrfach vorkommt.
    """
    parts = [p for p in (spec or "").split("/") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return db.get_playlist(Name=parts[0]).first()
    parent_id = "root"
    node = None
    for part in parts:
        node = db.get_playlist(Name=part, ParentID=parent_id).first()
        if node is None:
            return None
        parent_id = node.ID
    return node


def _get_or_create_artist(db, name: str):
    return db.get_artist(Name=name).first() or db.add_artist(name=name)


def _get_or_create_genre(db, name: str):
    return db.get_genre(Name=name).first() or db.add_genre(name=name)


def _get_or_create_album(db, name: str, artist_id: str | None):
    album = db.get_album(Name=name).first()
    if album is not None:
        return album
    # 'artist' hier ist der ALBUM-Interpret (AlbumArtistID) -- bei einem
    # neuen Album nehmen wir den Track-Interpreten, mangels eigenem
    # Album-Interpret-Tag in unserer DB (album_artist wird zwar gelesen,
    # aber hier bewusst nicht herangezogen, um keine zweite Quelle fuer
    # denselben Zweck zu pflegen). Nur beim ERSTEN Anlegen relevant, ein
    # bestehendes Album wird dadurch nie veraendert.
    return db.add_album(name=name, artist=artist_id)


def _get_or_create_key(db, name: str):
    """Tonart-Zeile (DjmdKey.ScaleName) zu einem Namen wie "8A", "1m" oder
    "Am". Rekordbox pflegt diese Tabelle bei Bedarf selbst (an echtem
    Material: genau die genutzten Schreibweisen leben, alle anderen stehen als
    Soft-Delete) -- eine lebende Zeile wird wiederverwendet, sonst entsteht
    eine neue, nie ein Namensdoppelgaenger. Tote Zeilen zaehlen nicht (_live())."""
    live = [k for k in db.get_key(ScaleName=name) if _live(k)]
    if live:
        return min(live, key=lambda k: k.Seq if k.Seq is not None else 1 << 30)
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables
    seqs = [k.Seq for k in db.get_key() if _live(k) and k.Seq is not None]
    key = tables.DjmdKey.create(
        ID=db.generate_unused_id(tables.DjmdKey), ScaleName=name,
        Seq=(max(seqs) + 1) if seqs else 1, UUID=str(uuid4()))
    db.add(key)
    db.flush()
    return key


# pyrekordbox' add_content() legt nur Struktur-Felder an (Pfad, Dateigroesse,
# Dateityp, IDs, Datum) -- Titel/Interpret/Album/Genre/BPM/Komponist/Jahr/
# Kommentar bleiben leer, wenn man sie nicht explizit mitgibt (steht so im
# pyrekordbox-Docstring). Die echte Rekordbox-App liest beim eigenen Import
# die Tags aus der Datei und fuellt diese Felder; das holen wir hier nach,
# und zwar aus unserer EIGENEN DB-Zeile statt die Datei ein zweites Mal zu
# lesen -- server.py uebergibt nur Pfade, die schon eine 'files'-Zeile haben
# (_known_file()-Pruefung vor dem Aufruf), unsere Tags sind also bereits
# vorhanden und aktuell.
# Interpret/Komponist/Album/Genre sind in Rekordbox eigene Tabellen
# (DjmdArtist/DjmdAlbum/DjmdGenre -- Komponist zeigt wie Interpret auf
# DjmdArtist), auf die DjmdContent nur per ID verweist -- ein gleichnamiger
# Eintrag muss wiederverwendet werden (sonst legt jeder Track mit z.B. dem
# Interpreten "Foo" einen eigenen Doppelgaenger an und Rekordbox'
# Interpreten-Browser zerfaellt in lauter Einzeltracks). Der Abgleich ist
# Name-basiert (case-sensitiv, siehe get_artist/get_album/get_genre) -- das
# ist dieselbe Regel, die pyrekordbox selbst fuer add_artist/add_album/
# add_genre dokumentiert (Name muss eindeutig sein). Komponist und Interpret
# teilen sich dabei denselben Namensraum (beide DjmdArtist) -- ein
# Komponist, der zufaellig auch als Interpret vorkommt, bekommt bewusst
# dieselbe ID statt eines Doppelgaengers.
# BPM: Rekordbox speichert intern BPM*100 als Integer (siehe pyrekordbox
# anlz/tags.py: "BPM is saved as 100 * BPM") -- ohne die Skalierung wuerde
# z.B. 128.3 BPM als 128 statt 12830 abgelegt und in Rekordbox als 1.28 BPM
# angezeigt.
# Tonart: siehe unten (KeyID, nur bei neuen Tracks).
# Kommentar: Commnt ist reiner Text, keine eigene Tabelle -- aber ein
# technisches Hex-Feld (z.B. ein beim Encodieren verschlepptes
# iTunSMPB/iTunNORM-Gapless-Tag, siehe tags.is_junk_comment()) soll dort
# genauso wenig landen wie beim eigenen Tags-Dialog. Jahr: 'year' ist bei
# fehlendem Tag 0 (siehe tags._parse_year()), kein echtes Jahr -- wie bei
# BPM nur bei einem wahren Wert setzen, sonst stuende in Rekordbox ueberall
# "1" statt eines leeren Feldes.
def _content_tag_kwargs(db, row, with_key: bool = True) -> dict:
    if row is None:
        return {}
    kwargs: dict = {}
    title = (row["title"] or "").strip()
    if title:
        kwargs["Title"] = title
    artist_name = (row["artist"] or "").strip()
    artist_id = None
    if artist_name:
        artist_id = _get_or_create_artist(db, artist_name).ID
        kwargs["ArtistID"] = artist_id
    album_name = (row["album"] or "").strip()
    if album_name:
        kwargs["AlbumID"] = _get_or_create_album(db, album_name, artist_id).ID
    genre_name = (row["genre"] or "").strip()
    if genre_name:
        kwargs["GenreID"] = _get_or_create_genre(db, genre_name).ID
    bpm = row["bpm"]
    if bpm:
        kwargs["BPM"] = round(bpm * 100)
    composer_name = (row["composer"] or "").strip()
    if composer_name:
        kwargs["ComposerID"] = _get_or_create_artist(db, composer_name).ID
    comment = (row["comment"] or "").strip()
    if comment and not tags_mod.is_junk_comment(comment):
        kwargs["Commnt"] = comment
    year = row["year"]
    if year:
        kwargs["ReleaseYear"] = year
    # Tonart (KeyID): nur beim ERSTEN Anlegen eines Tracks -- bestehende
    # Rekordbox-Zeilen behalten ihren Wert (with_key=False in
    # update_relocated_tracks()), Rekordbox liest ihn dort bei Bedarf selbst
    # neu aus der Datei ein. Der Name folgt der Einstellung key_notation, in
    # der DB steht er als Camelot.
    key = (row["key"] or "").strip()
    if with_key and key:
        name = tags_mod.format_key(key, cfgmod.load().get("key_notation", "camelot"))
        kwargs["KeyID"] = _get_or_create_key(db, name).ID
    return kwargs


# Metadaten-Abgleich TrackTab -> Rekordbox (Status "Metadaten" + "Rekordbox Update").
# BPM und Tonart bewusst nicht: das Tempo gehoert zum Beatgrid (kommt aus der Analyse),
# die Tonart liest Rekordbox selbst. Verglichen wird nur, was TrackTab wirklich hat
# (leere Felder schreibt _content_tag_kwargs() ebenfalls nie).
TAG_FIELDS = ("title", "artist", "album", "genre", "composer", "year", "comment", "key", "bpm")
# BPM-Abweichungen darunter zaehlen nicht (dieselbe Grenze wie die rote BPM in der Liste):
# Rekordbox' Analyse liefert oft 127,99 statt der 128 aus dem Tag.
_BPM_TOLERANCE = 0.05


def bpm_half_double(a: float, b: float) -> bool:
    """Halbes oder doppeltes Tempo (80 gegen 160): kein Fehler, sondern eine andere
    Zaehlweise. Wird nie geschrieben, nur angezeigt (Feld "bpm_half")."""
    return bool(a and b) and (abs(a * 2 - b) < _BPM_TOLERANCE * 2 or abs(a - b * 2) < _BPM_TOLERANCE * 2)


def _tag_norm(value) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _tag_wanted(row) -> dict:
    """Die Werte aus TrackTabs Zeile, die nach Rekordbox gehen wuerden."""
    out = {f: _tag_norm(row[f]) for f in ("title", "artist", "album", "genre", "composer")}
    comment = _tag_norm(row["comment"])
    out["comment"] = "" if tags_mod.is_junk_comment(comment) else comment
    out["year"] = str(row["year"]) if row["year"] else ""
    # Tonart immer als Camelot verglichen: die Schreibweise in Rekordbox ist egal.
    out["key"] = tags_mod.normalize_key(row["key"]) if row["key"] else ""
    out["bpm"] = round(float(row["bpm"]), 2) if row["bpm"] else 0
    return {f: v for f, v in out.items() if v}


_rb_tags_cache: dict = {}


def _rb_tag_values() -> dict:
    """{pfad: Metadaten in Rekordbox} aller lebenden Tracks, einmal je Stand der
    master.db gelesen (ein Durchlauf, kein Zugriff je Track)."""
    stamp = master_db_mtime()
    if _rb_tags_cache.get("stamp") == stamp and stamp is not None:
        return _rb_tags_cache["value"]

    def _query(db):
        artists = {a.ID: a.Name for a in db.get_artist() if _live(a)}
        albums = {a.ID: a.Name for a in db.get_album() if _live(a)}
        genres = {g.ID: g.Name for g in db.get_genre() if _live(g)}
        keys = {k.ID: k.ScaleName for k in db.get_key() if _live(k)}
        out: dict = {}
        for c in db.get_content():
            path = c.FolderPath or ""
            if not path or path in out or not _live(c):
                continue
            out[path] = {"title": _tag_norm(c.Title), "artist": _tag_norm(artists.get(c.ArtistID)),
                         "album": _tag_norm(albums.get(c.AlbumID)), "genre": _tag_norm(genres.get(c.GenreID)),
                         "composer": _tag_norm(artists.get(c.ComposerID)), "comment": _tag_norm(c.Commnt),
                         "year": str(c.ReleaseYear) if c.ReleaseYear else "",
                         "key": tags_mod.normalize_key(keys.get(c.KeyID) or ""),
                         "bpm": (c.BPM or 0) / 100}
        return out

    try:
        value = _query_cached(_import_pyrekordbox(), _query)
    except Exception:                                   # noqa: BLE001
        return {}
    _rb_tags_cache.update(stamp=stamp, value=value)
    return value


def tag_diff_map(rows: dict) -> dict:
    """{pfad: [Felder]} der Tracks, deren Metadaten in Rekordbox von TrackTab abweichen.
    rows: Pfad -> Zeile mit TAG_FIELDS. Nur Tracks in der Sammlung."""
    have = _rb_tag_values()
    out: dict = {}
    for path, row in rows.items():
        mine, theirs = _tag_wanted(row), have.get(path)
        if theirs is None:
            continue
        diff = [f for f in TAG_FIELDS if f in mine and (
            abs(mine[f] - theirs[f]) >= _BPM_TOLERANCE if f == "bpm" else mine[f] != theirs[f])]
        if "bpm" in diff and bpm_half_double(mine["bpm"], theirs["bpm"]):
            diff[diff.index("bpm")] = "bpm_half"
        if diff:
            out[path] = diff
    return out


_hot_cue_cache: dict = {}


def hot_cue_paths() -> set:
    """Pfade aller lebenden Tracks mit mindestens einem lebenden Hot Cue in Rekordbox.
    Ein Durchlauf ueber DjmdCue, gemerkt bis sich die master.db aendert."""
    stamp = master_db_mtime()
    if stamp is not None and _hot_cue_cache.get("stamp") == stamp:
        return _hot_cue_cache["value"]

    def _query(db):
        ids = {c.ContentID for c in db.get_cue() if _live(c) and c.Kind in _HOT_KIND_SLOT}
        return {c.FolderPath for c in db.get_content() if c.ID in ids and _live(c) and c.FolderPath}

    try:
        value = _query_cached(_import_pyrekordbox(), _query)
    except Exception:                                   # noqa: BLE001
        return set()
    _hot_cue_cache.update(stamp=stamp, value=value)
    return value


def _write_tags(db, content, row, now) -> None:
    """TrackTabs Metadaten in eine vorhandene Rekordbox-Zeile. Tonart und BPM nur, wenn
    sie dort anders sind (sonst bliebe Rekordbox' Schreibweise bzw. sein genauerer
    Messwert). Ein im selben Lauf geschriebenes Beatgrid setzt die BPM danach ohnehin
    aus dem Grid (_update_in()). Hat TrackTab das Grid bearbeitet, liefert der Aufrufer
    bpm = None (server._tag_rows()): dann gilt das Tempo des Grids, nicht das der Datei."""
    fields = _content_tag_kwargs(db, row, with_key=True)
    if "BPM" in fields and (abs(fields["BPM"] - (content.BPM or 0)) < _BPM_TOLERANCE * 100
                            or bpm_half_double(fields["BPM"] / 100, (content.BPM or 0) / 100)):
        fields.pop("BPM")
    if "KeyID" in fields:
        # get_key(ID=...) liefert in pyrekordbox EINE Zeile statt einer Abfrage --
        # darueber zu iterieren gaebe Zeichen. Deshalb ueber alle lebenden Tonarten.
        current = next((k.ScaleName for k in db.get_key() if k.ID == content.KeyID and _live(k)), "") \
            if content.KeyID else ""
        if tags_mod.normalize_key(current) == tags_mod.normalize_key(row["key"]):
            fields.pop("KeyID")
    _touch(db, [content], now, **fields)


# ── My Tag ──────────────────────────────────────────────────────────────────
# Aufbau an echtem Material abgelesen (DjmdMyTag, eine Tabelle fuer beides):
#   Kategorie (Spalte im My-Tag-Fenster): Attribute=1, ParentID="root",
#     ID "1".."4", UUID = ID, Seq 1..4. Rekordbox hat genau VIER feste
#     Spalten ("Genre", "Components", "Situation", "Untitled Column" ab
#     Werk) -- umbenennen ja, hinzufuegen nein. Eine fuenfte Kategorie
#     anzulegen waere in Rekordbox unsichtbar; deshalb wird die Kategorie nie
#     angelegt, sondern per Name gesucht (wie die Ziel-Playlist).
#   Tag: Attribute=0, ParentID = ID der Kategorie, ID zufaellig (wie bei
#     Interpreten), UUID uuid4, Seq fortlaufend je Kategorie. Geloeschte Tags
#     bleiben als Soft-Delete stehen, neu angelegte gleichen Namens bekommen
#     eine neue Zeile daneben.
#   Zuordnung DjmdSongMyTag: ID UND UUID je eine uuid4, TrackNo leer.
_RB_MYTAG_CATEGORY, _RB_MYTAG_TAG = 1, 0


class MyTagCategoryNotFound(RuntimeError):
    """Die eingestellte My-Tag-Kategorie gibt es in Rekordbox nicht."""


def _mytag_category(db, name: str):
    """Lebende My-Tag-Kategorie (Spalte) zu einem Namen, Gross/Klein egal --
    oder MyTagCategoryNotFound. Legt nie eine an (siehe oben: vier feste
    Spalten)."""
    wanted = (name or "").strip().casefold()
    for row in db.get_my_tag(ParentID="root"):
        if (_live(row) and row.Attribute == _RB_MYTAG_CATEGORY
                and (row.Name or "").strip().casefold() == wanted):
            return row
    raise MyTagCategoryNotFound(
        f"My-Tag-Spalte „{name}“ gibt es in Rekordbox nicht. Rekordbox hat "
        "vier feste My-Tag-Spalten — eine davon in Rekordbox umbenennen "
        "(Rechtsklick auf den Spaltenkopf im My-Tag-Fenster) und es erneut "
        "versuchen.")


def _mytag_children(db, category) -> dict[str, object]:
    """Lebende Tags einer Kategorie, Schluessel = Name in Kleinschrift. Bei
    zwei lebenden Tags gleichen Namens (von Hand in Rekordbox angelegt) gilt
    der mit der kleinsten Seq, wie in _get_or_create_key()."""
    out: dict[str, object] = {}
    rows = [r for r in db.get_my_tag(ParentID=category.ID)
            if _live(r) and r.Attribute == _RB_MYTAG_TAG and (r.Name or "").strip()]
    for row in sorted(rows, key=lambda r: r.Seq if r.Seq is not None else 1 << 30):
        out.setdefault(row.Name.strip().casefold(), row)
    return out


def _mytag_get_or_create(db, category, children: dict, name: str):
    """Tag unter der Kategorie per Name (Gross/Klein egal) oder neu -- nie ein
    Namensdoppelgaenger. 'children' (aus _mytag_children()) wird ergaenzt."""
    key = name.casefold()
    if key in children:
        return children[key]
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables
    seqs = [r.Seq for r in children.values() if r.Seq is not None]
    tag = tables.DjmdMyTag.create(
        ID=str(db.generate_unused_id(tables.DjmdMyTag)), Seq=(max(seqs) + 1) if seqs else 1,
        Name=name, Attribute=_RB_MYTAG_TAG, ParentID=category.ID, UUID=str(uuid4()))
    db.add(tag)
    db.flush()
    children[key] = tag
    return tag


def _sync_mytags(db, category, children: dict, content, names: list[str], now) -> dict:
    """Gleicht die My Tags EINES Tracks innerhalb der Kategorie auf 'names'
    ab: fehlende Zuordnungen anlegen, nicht mehr zutreffende soft-loeschen.
    Andere Kategorien bleiben unberuehrt -- die eingestellte Spalte gehoert
    TrackTab, alles andere dem Nutzer. Liefert {"added": n, "removed": n}."""
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables
    wanted: dict[str, str] = {}
    for n in names:
        n = (n or "").strip()
        if n:
            wanted.setdefault(n.casefold(), n)
    cat_tag_ids = {str(r.ID) for r in db.get_my_tag(ParentID=category.ID)}
    links = [s for s in db.get_my_tag_songs(ContentID=content.ID)
             if _live(s) and str(s.MyTagID) in cat_tag_ids]
    have: dict[str, object] = {}
    stale = []
    by_id = {str(r.ID): r for r in children.values()}
    for link in links:
        tag = by_id.get(str(link.MyTagID))
        key = (tag.Name or "").strip().casefold() if tag is not None else None
        # Zuordnung auf einen toten Tag oder ein Duplikat derselben Zuordnung
        # faellt ebenfalls weg.
        if key is None or key not in wanted or key in have:
            stale.append(link)
        else:
            have[key] = link
    added = 0
    for key, name in wanted.items():
        if key in have:
            continue
        tag = _mytag_get_or_create(db, category, children, name)
        db.add(tables.DjmdSongMyTag.create(
            ID=str(uuid4()), MyTagID=str(tag.ID), ContentID=str(content.ID),
            UUID=str(uuid4()), created_at=now, updated_at=now))
        added += 1
    _soft_delete(db, stale, now)
    return {"added": added, "removed": len(stale)}


# Attribute-Werte von DjmdPlaylist (pyrekordbox.masterdb.models.PlaylistType).
# Als eigene Konstanten statt eines Imports: der Wert steht so auch dann in
# unserem Code, wenn pyrekordbox fehlt und das Modul gar nicht laedt.
_RB_PLAYLIST, _RB_FOLDER, _RB_SMART = 0, 1, 4
_RB_KIND = {_RB_PLAYLIST: "playlist", _RB_FOLDER: "folder", _RB_SMART: "smart"}


def playlists() -> list[dict]:
    """
    Playlisten-Baum aus Rekordbox -- nur lesend, nichts wird geschrieben.
    Liefert {id, parent, kind, count, name, seq} je Knoten.

    Bewusst ueber _open_cached() statt _open(): letzteres legt bei JEDEM
    Oeffnen ein volles Backup von master.db an (siehe _backup_master_db) --
    fuer eine reine Leseabfrage, die beim Aufklappen des Astes und bei jedem
    Aktualisieren faellt, waere das eine Kopie der ganzen Datenbank je Klick.

    An echtem Material sind das knapp 1000 Knoten bei Tiefe 4; der Aufbau des
    Baums bleibt deshalb dem Aufrufer ueberlassen (der Client rendert ihn
    eingeklappt und lazy).

    ParentID ist bei Knoten der obersten Ebene der Text "root", nicht NULL --
    hier auf None normalisiert, damit die Baumform derselben Regel folgt wie
    unsere eigene playlists-Tabelle.
    """
    mod = _import_pyrekordbox()

    def _query(db):
        counts: dict[str, int] = {}
        for song in db.get_playlist_songs():
            # rb_local_deleted markiert einen aus der Playlist entfernten
            # Track nur als Soft-Delete (Cloud-Sync-Historie) -- die Zeile
            # bleibt in djmdSongPlaylist stehen. Rekordbox blendet sie in der
            # eigenen UI aus, pyrekordbox liefert sie ungefiltert mit, sonst
            # zaehlt die Playlist deutlich mehr Tracks als sie tatsaechlich
            # enthaelt.
            if getattr(song, "rb_local_deleted", 0):
                continue
            counts[song.PlaylistID] = counts.get(song.PlaylistID, 0) + 1
        out: list[dict] = []
        for node in db.get_playlist():
            # Gleicher Soft-Delete-Mechanismus wie bei den Playlist-Songs
            # oben: eine geloeschte/umbenannte Playlist bleibt als Geisterknoten
            # mit rb_local_deleted=1 in djmdPlaylist stehen, pyrekordbox liefert
            # sie ungefiltert mit -- ohne diesen Filter taucht z.B. eine intern
            # neu angelegte "Analyse"-Playlist neben ihrer Vorgaengerin (0
            # Tracks, weil deren Songs ja ebenfalls als geloescht gelten) im
            # Baum auf, obwohl Rekordbox selbst nur die aktuelle zeigt.
            if getattr(node, "rb_local_deleted", 0):
                continue
            parent = node.ParentID
            out.append({
                "id": str(node.ID),
                "parent": None if parent in (None, "", "root") else str(parent),
                "kind": _RB_KIND.get(node.Attribute, "playlist"),
                "count": counts.get(node.ID, 0),
                "name": node.Name or "",
                "seq": node.Seq or 0,
            })
        return out

    return _query_cached(mod, _query)


def _smart_contents(db, node):
    """
    Inhalt einer Smart Playlist. Wie db.get_playlist_contents(), aber mit
    eigener Auswertung der Zeitregeln (Operator IN_LAST/NOT_IN_LAST):
    pyrekordbox 0.4.4 rechnet sie falsch (siehe SmartListUnsupported). Sie
    werden vorab in einen festen Stichtag umgeschrieben ("in den letzten 6
    Monaten" = Datum > heute minus 6 Monate) und laufen dann ueber den
    normalen GREATER/LESS-Zweig der Bibliothek.
    """
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    from pyrekordbox.masterdb.smartlist import Operator, SmartList
    from pyrekordbox.masterdb.models import DjmdContent

    sl = SmartList()
    sl.parse(node.SmartList)
    now = datetime.now()
    for cond in sl.conditions:
        if cond.operator not in (Operator.IN_LAST, Operator.NOT_IN_LAST):
            continue
        n = int(cond.value_left)
        if cond.unit == "day":
            t0 = now - relativedelta(days=n)
        elif cond.unit == "month":
            t0 = now - relativedelta(months=n)
        elif cond.unit == "year":
            t0 = now - relativedelta(years=n)
        else:
            raise ValueError(f"Unbekannte Zeiteinheit '{cond.unit}'")
        cond.operator = int(Operator.GREATER if cond.operator == Operator.IN_LAST
                            else Operator.LESS)
        cond.unit = ""
        cond.value_left = t0.strftime("%Y-%m-%d")
    return db.query(DjmdContent).filter(sl.filter_clause())


def playlist_tracks(playlist_id: str) -> list[dict]:
    """
    Tracks einer Rekordbox-Playlist in ihrer Reihenfolge (TrackNo).
    Liefert {path, title, artist} je Eintrag; 'path' ist der FolderPath aus
    Rekordbox, also ein gewoehnlicher Dateipfad.

    Smart Playlists haben keine DjmdSongPlaylist-Zeilen -- ihr Inhalt wird von
    Rekordbox aus dem Regelwerk berechnet. Fuer sie liefert
    get_playlist_contents() das Ergebnis; eine Reihenfolge gibt es dort nicht.
    """
    mod = _import_pyrekordbox()

    def _query(db):
        # get_playlist(ID=...) liefert bei einem Treffer ueber den
        # Primaerschluessel das Objekt SELBST, bei jedem anderen Filter
        # dagegen eine Abfrage -- deshalb beide Formen abfangen statt blind
        # .first() zu rufen.
        node = db.get_playlist(ID=playlist_id)
        if hasattr(node, "first"):
            node = node.first()
        if node is None:
            raise PlaylistNotFound(f"Playlist {playlist_id} nicht gefunden")
        rows: list[dict] = []
        if node.Attribute == _RB_SMART:
            try:
                contents = list(_smart_contents(db, node))
            except Exception as exc:                       # noqa: BLE001
                if _is_stale_cache_error(exc):
                    raise
                raise SmartListUnsupported(str(exc)[:200]) from exc
        else:
            songs = sorted(db.get_playlist_songs(PlaylistID=playlist_id),
                           key=lambda s: (s.TrackNo or 0))
            # Gleicher Soft-Delete-Filter wie in playlists() -- sonst tauchen
            # laengst entfernte Tracks wieder in der Trackliste auf.
            # Eintrags-ID mitliefern: Entfernen/Umsortieren adressieren den
            # EINZELNEN Playlist-Eintrag, nicht den Pfad -- der wird vom
            # Server auf unsere DB-Schreibweise umgeschrieben, fehlt bei
            # Titeln ohne lokale Datei und ist bei doppelten Eintraegen
            # mehrdeutig.
            contents = [(s.Content, s.ID) for s in songs
                        if s.Content is not None and _live(s)]
        if node.Attribute == _RB_SMART:
            contents = [(c, None) for c in contents]
        for content, entry_id in contents:
            artist = getattr(content, "Artist", None)
            rows.append({
                "path": content.FolderPath or "",
                "title": content.Title or "",
                "artist": getattr(artist, "Name", "") or "",
                "entry_id": str(entry_id) if entry_id is not None else None,
                "content_id": str(content.ID),
            })
        return rows

    return _query_cached(mod, _query)


def add_tracks_to_playlist(paths: list[str], playlist_name: str,
                            metadata: dict | None = None,
                            playlist_id: str | None = None,
                            mytags: dict | None = None,
                            mytag_category: str = "",
                            prepared: dict | None = None) -> dict:
    """
    Fuegt Tracks zu einer BESTEHENDEN Rekordbox-Playlist hinzu. Legt die
    Playlist nicht an -- ein unbekannter Name ist ein Fehler, kein Auto-Create.

    'metadata' (optional, Pfad -> unsere eigene DB-Zeile aus files.py) liefert
    Titel/Interpret/Album/Genre/BPM/Komponist/Jahr/Kommentar fuer Tracks, die
    Rekordbox noch nicht kennt -- siehe _content_tag_kwargs().

    'playlist_id' (optional, z.B. vom Drag & Drop auf einen Baumknoten) hat
    Vorrang vor dem Namen: Namen sind in Rekordbox nicht eindeutig. Ordner und
    Smart Playlists werden abgelehnt (dort laesst sich nichts hinzufuegen).

    'mytags' (optional, Pfad -> Liste von Tag-Namen) gleicht fuer jeden
    uebertragenen Track -- neu hinzugefuegt wie schon vorhanden -- die My Tags
    in der Spalte 'mytag_category' ab (siehe _sync_mytags()). None = My Tags
    bleiben unberuehrt. Fehlt die Spalte, werden die Tracks trotzdem
    hinzugefuegt; der Grund steht dann in "mytag_error".

    'prepared' (optional, Pfad -> {cues, beats, phrases, lead, audio_start_ms}, alles
    schon auf Rekordbox' Achse, siehe server._dj_rb_prepare()) gilt nur fuer Tracks,
    die Rekordbox noch nicht kennt: sie kommen wie ueber add_track() mit eigenen
    Analysedateien, Cover und Cues an. Schlaegt das Anlegen eines solchen Tracks fehl,
    wird nichts geschrieben (sonst bliebe ein als analysiert markierter Track ohne
    Analysedateien zurueck).

    Liefert {"added": [...], "skipped": [...], "errors": {pfad: meldung},
    "created": [...], "written": {pfad: [felder]}, "partial": {pfad: [felder]},
    "playlist": name, "mytags": {"tracks", "added", "removed"},
    "mytag_error": meldung oder None}. "created" = neu in der Sammlung.
    """
    metadata = metadata or {}
    playlist_name = (playlist_name or "").strip()
    playlist_id = str(playlist_id or "").strip()
    if not playlist_name and not playlist_id:
        raise ValueError("Keine Rekordbox-Playlist angegeben.")
    _check_writable()

    pyrekordbox_mod = _import_pyrekordbox()

    # Schwerer Teil (Analysedateien, Cover) vor dem Oeffnen der master.db.
    preps: dict[str, dict] = {}
    for path, item in (prepared or {}).items():
        try:
            preps[path] = {**_prepare_new_track(
                path, beats=item.get("beats"), phrases=item.get("phrases"),
                lead=item.get("lead"), audio_start_ms=item.get("audio_start_ms") or 0),
                "cues": item.get("cues")}
        except Exception:                                # noqa: BLE001
            # Ohne eigene Analyse: nur Tags und Cues, Grid/Phrasen nach Rekordbox' Analyse.
            preps[path] = {"beats": item.get("beats"), "phrases": item.get("phrases"),
                           "authored": None, "cover": {}, "cues": item.get("cues")}
    made: list[Path] = []

    def _run(db):
        # Zweiter Versuch von _with_malformed_retry(): Reste des ersten wegraeumen.
        for folder in made:
            shutil.rmtree(folder, ignore_errors=True)
        made.clear()
        if playlist_id:
            # Mit ID= liefert pyrekordbox direkt das Objekt (bzw. None), nicht
            # eine Abfrage mit .first().
            playlist = db.get_playlist(ID=playlist_id)
            if playlist is not None and hasattr(playlist, "first"):
                playlist = playlist.first()
            if playlist is not None and getattr(playlist, "rb_local_deleted", 0):
                playlist = None
            if playlist is None:
                raise PlaylistNotFound(
                    "Die Playlist existiert in Rekordbox nicht mehr. "
                    "Playlist-Ansicht neu laden und erneut versuchen.")
            if playlist.Attribute != _RB_PLAYLIST:
                raise PlaylistNotFound(
                    f"„{playlist.Name}“ ist keine normale Playlist "
                    "(Ordner und Smart Playlists nehmen keine Tracks auf).")
        else:
            playlist = _find_playlist(db, playlist_name)
        if playlist is None:
            raise PlaylistNotFound(
                f"Playlist „{playlist_name}“ existiert nicht in Rekordbox. "
                "Erst dort anlegen, dann hier erneut versuchen (bei "
                "mehrdeutigen Namen den Ordnerpfad angeben, z.B. "
                "'Ordner/Playlist').")

        import datetime
        now = datetime.datetime.now()
        category = None
        children: dict = {}
        mytag_error = None
        mytag_stats = {"tracks": 0, "added": 0, "removed": 0}
        if mytags is not None:
            try:
                category = _mytag_category(db, mytag_category)
                children = _mytag_children(db, category)
            except MyTagCategoryNotFound as exc:
                mytag_error = str(exc)

        added: list[str] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}
        created: list[str] = []
        written: dict[str, list[str]] = {}
        partial: dict[str, list[str]] = {}
        for path in paths:
            content = _live_content(db, path)
            if content is None and path in preps:
                # Bewusst ohne eigenen try: ein Fehler bricht den ganzen Vorgang ab.
                prep = preps[path]
                content, fields = _insert_track(db, path, (metadata or {}).get(path),
                                                prep.get("cues"), prep, made)
                created.append(path)
                written[path] = fields
                missing = _new_track_skipped(prep, fields)
                if missing:
                    partial[path] = missing
            try:
                if content is None:
                    content = _add_content_fresh(
                        db, path, **_content_tag_kwargs(db, metadata.get(path)))
                    created.append(path)
                if category is not None:
                    # Eigener try: ein Fehler bei den My Tags soll den Track
                    # nicht aus der Playlist halten.
                    try:
                        res = _sync_mytags(db, category, children, content,
                                           mytags.get(path) or [], now)
                        mytag_stats["tracks"] += 1
                        mytag_stats["added"] += res["added"]
                        mytag_stats["removed"] += res["removed"]
                    except Exception as exc:              # noqa: BLE001
                        mytag_error = str(exc)
                # Nur LEBENDE Eintraege zaehlen: ein frueher in Rekordbox
                # entfernter Eintrag bleibt als Soft-Delete stehen und liesse
                # den Track sonst faelschlich als "schon drin" ueberspringen.
                already = any(_live(x) for x in db.get_playlist_songs(
                    PlaylistID=playlist.ID, ContentID=content.ID))
                if already:
                    skipped.append(path)
                    continue
                db.add_to_playlist(playlist, content)
                added.append(path)
            except Exception as exc:                     # noqa: BLE001
                errors[path] = str(exc)
        if added:
            # add_to_playlist() vergibt TrackNo = Anzahl ALLER Zeilen + 1,
            # soft-geloeschte eingeschlossen -- das hinterlaesst Luecken.
            db.flush()
            _renumber(db, _live_songs(db, playlist.ID), now)
        db.commit()
        return {"added": added, "skipped": skipped, "errors": errors,
                "created": created, "written": written, "partial": partial,
                "playlist": playlist.Name or playlist_name,
                "mytags": mytag_stats, "mytag_error": mytag_error}

    try:
        return _with_malformed_retry(pyrekordbox_mod, _run)
    except Exception:
        for folder in made:
            shutil.rmtree(folder, ignore_errors=True)
        raise


def _playlist_by_id(db, playlist_id: str):
    """Normale (nicht Smart, kein Ordner), lebende Playlist per ID oder
    PlaylistNotFound. get_playlist(ID=...) liefert ueber den Primaer-
    schluessel das Objekt SELBST, nicht eine Abfrage."""
    playlist = db.get_playlist(ID=str(playlist_id))
    if playlist is not None and hasattr(playlist, "first"):
        playlist = playlist.first()
    if playlist is None or not _live(playlist):
        raise PlaylistNotFound(
            "Die Playlist existiert in Rekordbox nicht mehr. "
            "Playlist-Ansicht neu laden und erneut versuchen.")
    if playlist.Attribute != _RB_PLAYLIST:
        raise PlaylistNotFound(
            f"„{playlist.Name}“ ist keine normale Playlist "
            "(Ordner und Smart Playlists lassen sich nicht bearbeiten).")
    return playlist


def _live_songs(db, playlist_id: str) -> list:
    """Lebende Eintraege einer Playlist in ihrer Reihenfolge. TrackNo ist an
    echtem Material nicht immer sauber (Luecken, Doppelte) -- die ID als
    zweiter Schluessel haelt die Reihenfolge trotzdem stabil."""
    songs = [s for s in db.get_playlist_songs(PlaylistID=str(playlist_id)) if _live(s)]
    return sorted(songs, key=lambda s: (s.TrackNo or 0, str(s.ID)))


def _touch(db, rows: list, now, **fields) -> None:
    """Setzt Felder an mehreren Zeilen als EINE Aenderung fuer Rekordbox'
    Update-Zaehler (USN) -- dasselbe Muster wie pyrekordbox'
    move_song_in_playlist(): Einzelverfolgung aus, danach on_move() mit der
    ganzen Liste, commit() vergibt allen dieselbe rb_local_usn."""
    if not rows:
        return
    with db.registry.disabled():
        for row in rows:
            for key, value in fields.items():
                setattr(row, key, value)
            if hasattr(row, "updated_at"):
                row.updated_at = now
    db.registry.on_move(list(rows))


def _soft_delete(db, rows: list, now) -> None:
    """Loeschen wie Rekordbox selbst (an echtem Material abgelesen): Zeile
    bleibt stehen, rb_local_deleted=1 und rb_data_status=2. pyrekordbox'
    remove_from_playlist() loescht dagegen hart."""
    _touch(db, [r for r in rows if _live(r)], now, rb_local_deleted=1, rb_data_status=2)


def _renumber(db, songs: list, now) -> None:
    """TrackNo lueckenlos 1..n in der Reihenfolge von 'songs'. Nur geaenderte
    Zeilen werden angefasst."""
    changed = []
    with db.registry.disabled():
        for no, song in enumerate(songs, start=1):
            if song.TrackNo != no:
                song.TrackNo = no
                song.updated_at = now
                changed.append(song)
    if changed:
        db.registry.on_move(changed)


def remove_entries_from_playlist(playlist_id: str, entry_ids: list[str]) -> dict:
    """
    Entfernt einzelne Eintraege (DjmdSongPlaylist.ID) aus einer normalen
    Playlist. Der Track bleibt in der Sammlung und in allen anderen
    Playlisten. Liefert {"removed": [...], "skipped": [...], "playlist": name}.
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    wanted = [str(e) for e in entry_ids if e]

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        playlist = _playlist_by_id(db, playlist_id)
        songs = _live_songs(db, playlist.ID)
        by_id = {str(s.ID): s for s in songs}
        hit = [by_id[e] for e in wanted if e in by_id]
        skipped = [e for e in wanted if e not in by_id]
        _soft_delete(db, hit, now)
        hit_ids = {str(s.ID) for s in hit}
        _renumber(db, [s for s in songs if str(s.ID) not in hit_ids], now)
        db.commit()
        return {"removed": sorted(hit_ids), "skipped": skipped,
                "playlist": playlist.Name or ""}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def reorder_playlist(playlist_id: str, entry_ids: list[str]) -> dict:
    """
    Setzt die Reihenfolge einer normalen Playlist neu. 'entry_ids' ist die
    gewuenschte Gesamtreihenfolge; nicht genannte lebende Eintraege (z.B. in
    Rekordbox zwischenzeitlich hinzugekommen) wandern in ihrer bisherigen
    Reihenfolge ans Ende -- es geht nie ein Eintrag verloren.
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    wanted = [str(e) for e in entry_ids if e]

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        playlist = _playlist_by_id(db, playlist_id)
        songs = _live_songs(db, playlist.ID)
        by_id = {str(s.ID): s for s in songs}
        ordered, seen = [], set()
        for e in wanted:
            if e in by_id and e not in seen:
                ordered.append(by_id[e])
                seen.add(e)
        ordered += [s for s in songs if str(s.ID) not in seen]
        _renumber(db, ordered, now)
        db.commit()
        return {"order": [str(s.ID) for s in ordered], "playlist": playlist.Name or ""}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def _node_by_id(db, node_id: str):
    """Lebender Playlist-Knoten (Playlist, Ordner, Smart) per ID oder PlaylistNotFound."""
    node = db.get_playlist(ID=str(node_id))
    if node is not None and hasattr(node, "first"):
        node = node.first()
    if node is None or not _live(node):
        raise PlaylistNotFound(
            "Die Playlist existiert in Rekordbox nicht mehr. "
            "Playlist-Ansicht neu laden und erneut versuchen.")
    return node


def _parent_folder(db, parent_id: str | None):
    """None = oberste Ebene, sonst ein lebender Ordner."""
    if not parent_id:
        return None
    parent = _node_by_id(db, parent_id)
    if parent.Attribute != _RB_FOLDER:
        raise PlaylistNotFound(f"„{parent.Name}“ ist kein Ordner.")
    return parent


def _live_children(db, parent_id: str | None) -> list:
    """Lebende Knoten einer Ebene in ihrer Reihenfolge."""
    nodes = [n for n in db.get_playlist(ParentID=str(parent_id or "root")) if _live(n)]
    return sorted(nodes, key=lambda n: (n.Seq or 0, str(n.ID)))


def _renumber_siblings(db, parent_id: str, now, nodes: list | None = None) -> None:
    """Seq der lebenden Knoten einer Ebene lueckenlos 1..n, in der Reihenfolge von
    'nodes' (sonst der bisherigen). pyrekordbox zaehlt beim Anlegen/Verschieben tote
    Knoten mit; Rekordbox selbst nummeriert lueckenlos ab 1 (an echtem Material)."""
    if nodes is None:
        nodes = _live_children(db, parent_id)
    changed = []
    with db.registry.disabled():
        for no, node in enumerate(nodes, start=1):
            if node.Seq != no:
                node.Seq = no
                node.updated_at = now
                changed.append(node)
    if changed:
        db.registry.on_move(changed)


def create_playlist(name: str, parent_id: str | None = None, folder: bool = False) -> dict:
    """Legt eine normale Playlist oder einen Ordner an, am Ende der Ebene (oberste
    Ebene oder Ordner 'parent_id'). Smart Playlists legt TrackTab nicht an.
    Liefert {"id", "name", "kind"}."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("Kein Name angegeben.")
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        parent = _parent_folder(db, parent_id)
        make = db.create_playlist_folder if folder else db.create_playlist
        node = make(name, parent=parent)
        db.flush()
        _renumber_siblings(db, parent.ID if parent is not None else "root", now)
        db.commit()
        return {"id": str(node.ID), "name": node.Name or name,
                "kind": "folder" if folder else "playlist"}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def rename_playlist(node_id: str, name: str) -> dict:
    """Benennt eine Playlist, einen Ordner oder eine Smart Playlist um."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("Kein Name angegeben.")
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        node = _node_by_id(db, node_id)
        old = node.Name or ""
        db.rename_playlist(node, name)
        db.commit()
        return {"id": str(node.ID), "name": name, "old": old}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def move_playlist(node_id: str, parent_id: str | None = None, before_id: str | None = None) -> dict:
    """Verschiebt eine Playlist, einen Ordner oder eine Smart Playlist an eine neue
    Stelle: in den Ordner 'parent_id' (None = oberste Ebene), vor den Knoten
    'before_id' (None = ans Ende). Eigene Umsetzung statt pyrekordbox'
    move_playlist(): die zaehlt tote Knoten bei Seq mit und laesst ParentId in
    masterPlaylists6.xml stehen. Liefert {"id", "name", "parent"}."""
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        node = _node_by_id(db, node_id)
        parent = _parent_folder(db, parent_id)
        new_pid = str(parent.ID) if parent is not None else "root"
        # Kein Ordner in sich selbst oder einen seiner Unterordner.
        probe = parent
        while probe is not None:
            if str(probe.ID) == str(node.ID):
                raise ValueError("Ein Ordner kann nicht in sich selbst verschoben werden.")
            up = probe.ParentID
            probe = None if up in (None, "", "root") else _node_by_id(db, up)
        old_pid = str(node.ParentID or "root")
        order = [n for n in _live_children(db, new_pid) if str(n.ID) != str(node.ID)]
        at = next((i for i, n in enumerate(order) if before_id and str(n.ID) == str(before_id)), len(order))
        order.insert(at, node)
        if old_pid != new_pid:
            _touch(db, [node], now, ParentID=new_pid)
            if db.playlist_xml is not None and db.playlist_xml.get(node.ID) is not None:
                db.playlist_xml.update(node.ID, parent_id=new_pid)
        db.flush()
        _renumber_siblings(db, new_pid, now, order)
        if old_pid != new_pid:
            _renumber_siblings(db, old_pid, now)
        db.commit()
        return {"id": str(node.ID), "name": node.Name or "",
                "parent": None if new_pid == "root" else new_pid}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def delete_playlist(node_id: str) -> dict:
    """Loescht eine Playlist oder einen Ordner samt Inhalt WEICH wie Rekordbox selbst
    (_soft_delete(): Knoten, alle Unterknoten und ihre Eintraege bleiben als tote
    Zeilen stehen), nicht ueber pyrekordbox' delete_playlist() (loescht hart). Die
    Tracks bleiben in der Sammlung. masterPlaylists6.xml fuehrt nur lebende Knoten
    (an echtem Material: Rekordbox 7.2 pflegt sie weiter), die Knoten fliegen dort
    raus. Liefert {"id", "name", "playlists", "entries"}."""
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        node = _node_by_id(db, node_id)
        nodes, todo = [], [node]
        while todo:
            current = todo.pop()
            nodes.append(current)
            todo.extend(c for c in db.get_playlist(ParentID=str(current.ID)) if _live(c))
        songs = [s for n in nodes for s in db.get_playlist_songs(PlaylistID=str(n.ID)) if _live(s)]
        _soft_delete(db, songs, now)
        _soft_delete(db, nodes, now)
        if db.playlist_xml is not None:
            for n in nodes:
                if db.playlist_xml.get(n.ID) is not None:
                    db.playlist_xml.remove(n.ID)
        db.flush()
        _renumber_siblings(db, node.ParentID or "root", now)
        db.commit()
        return {"id": str(node.ID), "name": node.Name or "", "playlists": len(nodes),
                "entries": len(songs)}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def resolve_content_ids(paths: list[str]) -> dict[str, str]:
    """Pfad -> ID des lebenden DjmdContent (read-only). Faellt auf einen
    Vergleich ohne Gross-/Kleinschreibung und Unicode-Form zurueck --
    macOS-Volumes sind case-insensitiv, Rekordbox' FolderPath und unser Scan
    koennen dieselbe Datei verschieden schreiben."""
    import unicodedata
    pyrekordbox_mod = _import_pyrekordbox()

    def norm(p: str) -> str:
        return unicodedata.normalize("NFC", p).casefold()

    def _query(db):
        out: dict[str, str] = {}
        missing = []
        for p in paths:
            c = _live_content(db, p)
            if c is not None:
                out[p] = str(c.ID)
            else:
                missing.append(p)
        if missing:
            loose = {norm(c.FolderPath): str(c.ID) for c in db.get_content()
                     if c.FolderPath and _live(c)}
            for p in missing:
                cid = loose.get(norm(p))
                if cid:
                    out[p] = cid
        return out

    return _query_cached(pyrekordbox_mod, _query)


def collection_membership(content_ids: list[str]) -> dict[str, int]:
    """Anzahl normaler Playlisten je Track (fuer den Warndialog vor dem
    Entfernen aus der Sammlung; read-only)."""
    pyrekordbox_mod = _import_pyrekordbox()
    ids = {str(c) for c in content_ids}

    def _query(db):
        live_pl = {str(n.ID) for n in db.get_playlist()
                   if _live(n) and n.Attribute == _RB_PLAYLIST}
        lists: dict[str, set] = {c: set() for c in ids}
        for s in db.get_playlist_songs():
            cid = str(s.ContentID)
            if cid in lists and _live(s) and str(s.PlaylistID) in live_pl:
                lists[cid].add(str(s.PlaylistID))
        return {c: len(v) for c, v in lists.items()}

    return _query_cached(pyrekordbox_mod, _query)


def _content_tables():
    """Alle pyrekordbox-Tabellen mit ContentID-Bezug und Soft-Delete-Spalte
    (Playlist-Eintraege, Cues, Verlauf, Mixer-Parameter, Tags, ...) -- per
    Reflektion statt fester Liste, damit eine neue Tabelle in einem
    pyrekordbox-Update nicht stillschweigend uebergangen wird."""
    from pyrekordbox.masterdb import models as tables
    out = []
    for obj in vars(tables).values():
        if (isinstance(obj, type) and issubclass(obj, tables.Base) and obj is not tables.Base
                and hasattr(obj, "__table__") and "ContentID" in obj.__table__.c
                and "rb_local_deleted" in obj.__table__.c):
            out.append(obj)
    return out


def remove_from_collection(content_ids: list[str]) -> dict:
    """
    Entfernt Tracks aus der Rekordbox-Sammlung -- wie Rekordbox' eigener
    Befehl "Von der Sammlung entfernen" (an echtem Material abgelesen):
    DjmdContent und ALLE abhaengigen Zeilen werden soft-geloescht, die
    Analyseordner (ANLZ: Beatgrid, Wellenform, Cues) verschwinden. Die
    Audiodatei selbst bleibt unangetastet. Rekordbox laesst dabei den
    Cover-Ordner liegen (106 verwaiste Ordner in einer echten Sammlung);
    hier geht er mit, ausser ein lebender Track benutzt ihn noch.

    Die ANLZ-Ordner werden nicht geloescht, sondern nach
    backup/rekordbox/anlz/<zeitstempel>/ verschoben -- zusammen mit dem
    master.db-Backup desselben Vorgangs (_open()) laesst sich so alles
    vollstaendig zurueckspielen. Verschoben wird erst NACH dem commit():
    scheitert der Datenbankteil, bleibt die Analyse an ihrem Platz.

    Liefert {"removed": [{"content_id", "path"}], "skipped": [...],
    "anlz_backup": pfad|None, "anlz_errors": {...}}.
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()
    wanted = list(dict.fromkeys(str(c) for c in content_ids if c))

    def _run(db):
        import datetime
        from pyrekordbox.masterdb import models as tables
        now = datetime.datetime.now()
        removed, skipped, anlz_dirs, art_dirs = [], [], [], []
        touched_playlists: set[str] = set()
        dep_tables = _content_tables()
        for cid in wanted:
            content = db.query(tables.DjmdContent).filter_by(ID=cid).first()
            if content is None or not _live(content):
                skipped.append(cid)
                continue
            for table in dep_tables:
                rows = [r for r in db.query(table).filter(table.ContentID == cid) if _live(r)]
                if table is tables.DjmdSongPlaylist:
                    touched_playlists.update(str(r.PlaylistID) for r in rows)
                _soft_delete(db, rows, now)
            try:
                if content.AnalysisDataPath:
                    anlz_dirs.append((cid, Path(db.get_anlz_dir(content))))
            except Exception:                            # noqa: BLE001
                pass
            if content.ImagePath:
                art_dirs.append((cid, content.ImagePath))
            _soft_delete(db, [content], now)
            removed.append({"content_id": cid, "path": content.FolderPath or ""})
        for pid in touched_playlists:
            _renumber(db, _live_songs(db, pid), now)
        db.commit()

        anlz_backup = None
        anlz_errors: dict[str, str] = {}
        existing = [(cid, d) for cid, d in anlz_dirs if d.is_dir()]
        if art_dirs:
            in_use = {Path(c.ImagePath).parent for c in db.get_content() if _live(c) and c.ImagePath}
            for cid, image in art_dirs:
                rel = Path(image.lstrip("/")).parent
                folder = Path(db._share_dir) / rel
                if Path("/" + str(rel)) not in in_use and folder.is_dir():
                    existing.append((f"{cid}-artwork", folder))
        if existing:
            anlz_backup = cfgmod.resolve("backup") / "rekordbox" / "anlz" / time.strftime("%Y%m%d-%H%M%S")
            anlz_backup.mkdir(parents=True, exist_ok=True)
            for cid, d in existing:
                try:
                    shutil.move(str(d), str(anlz_backup / cid))
                except Exception as exc:                 # noqa: BLE001
                    anlz_errors[cid] = str(exc)
        return {"removed": removed, "skipped": skipped,
                "anlz_backup": str(anlz_backup) if anlz_backup else None,
                "anlz_errors": anlz_errors}

    return _with_malformed_retry(pyrekordbox_mod, _run)


# ── Stems (Acapella / Instrumental) ─────────────────────────────────────────
# Spalten, die ein Stem vom Haupttrack uebernimmt: sie gehoeren zum Raster und
# zur Analyse, nicht zur Datei (BitRate/BitDepth/FileSize bleiben die der
# Stem-Datei; SampleRate/BitRate setzt add_stems() aus unserer DB-Zeile --
# ein Stem hat immer 44,1 kHz, auch bei einer 48-kHz-Quelle). ImagePath setzt
# _clone_artwork() auf eine eigene Kopie.
_STEM_COPY_COLUMNS = ("BPM", "Length", "Analysed", "KeyID",
                      "AnalysisUpdated", "CueUpdated")
# Beatgrid (PQTZ/PQT2), Cues (PCOB/PCO2) und Wellenformen werden 1:1
# uebernommen -- stems.write_audio() legt den Stem so an, dass Rekordbox ihn
# sample-genau wie das Original abspielt (siehe playback_lead()). Der
# Datei-Index PVBR gehoert dagegen zur Datei und wird neu gesetzt
# (_stem_pvbr()); die 3EX-Datei (Audio-Embedding fuer die Aehnlichkeitssuche)
# gehoert zum Klang und wird nicht kopiert.
_STEM_ANLZ_FILES = ("ANLZ0000.DAT", "ANLZ0000.EXT", "ANLZ0000.2EX")
# Rekordbox' Wellenformen haben 150 Spalten je Sekunde.
_WAVE_COLS_PER_S = 150


def _pwv3_heights(path: str) -> bytes | None:
    """Rohe Hoehenwerte der PWV3-Wellenform, an der playback_lead() misst. None =
    nicht analysiert oder nicht lesbar."""
    from pyrekordbox.anlz import AnlzFile

    def _heights(db):
        content = _live_content(db, path)
        if content is None or not content.Analysed:
            return None
        ext = db.get_anlz_dir(content) / "ANLZ0000.EXT"
        if not ext.is_file():
            return None
        anlz = AnlzFile.parse_file(ext)
        if "PWV3" not in anlz:
            return None
        return bytes(anlz.get_tag("PWV3").content.entries)

    return _query_cached(_import_pyrekordbox(), _heights)


def lead_stamp(path: str) -> str:
    """Pruefsumme dessen, wovon playback_lead() auf Rekordbox' Seite abhaengt (PWV3).
    Gleich = der gemessene Versatz gilt noch, auch nach eigenen Schreibzugriffen
    (die PWV3 nie anfassen); anders = Rekordbox hat neu analysiert. "" = nichts da."""
    import hashlib
    try:
        raw = _pwv3_heights(path)
    except Exception:                                   # noqa: BLE001
        return "?"
    return hashlib.md5(raw).hexdigest() if raw else ""


def playback_lead(path: str, mix, sr: int = 44100) -> int:
    """Um wie viele Samples (bei `sr`) Rekordbox den Track `path` SPAETER
    abspielt, als ffmpeg ihn dekodiert (`mix`: (2, n) bzw. (n,) wie
    stems._decode()). 0, wenn der Track nicht analysiert in Rekordbox liegt
    oder die Messung nicht eindeutig ist.

    An echtem Material gemessen (Wellenform PWV3 gegen eigene Dekodierung):
    AIFF/WAV und MP3 ohne Xing/Info-Kopf 0 ms; MP3 mit LAME-Kopf 26 oder
    53 ms (Rekordbox wertet die Encoder-Verzoegerung darin anders aus als
    ffmpeg, die beiden Gruppen liessen sich keinem Merkmal zuordnen); AAC
    (.m4a) 47 ms (Encoder-Vorlauf, den ffmpeg abschneidet). Deshalb wird je
    Track gemessen statt nach einer Regel gerechnet: die Anstiege der eigenen
    Huellkurve (Spitzenwert je Wellenform-Spalte) gegen die von PWV3,
    Kreuzkorrelation ueber -100..+200 ms, parabolisch verfeinert."""
    try:
        import numpy as np

        raw = _pwv3_heights(path)
        if not raw:
            return 0
        rb = (np.frombuffer(raw, dtype=np.uint8) & 0x1F).astype(np.float64)
        x = np.asarray(mix, dtype=np.float32)
        if x.ndim == 2:
            x = x.mean(axis=0)
        hop = sr / _WAVE_COLS_PER_S
        n = min(int(len(x) / hop), len(rb))
        if n < _WAVE_COLS_PER_S * 10:
            return 0
        edges = np.round(np.arange(n + 1) * hop).astype(np.int64)
        env = np.maximum.reduceat(np.abs(x[:edges[-1]]), edges[:-1]).astype(np.float64)
        a = np.diff(env, prepend=0.0).clip(0)
        b = np.diff(rb[:n], prepend=0.0).clip(0)
        lags = range(-15, 31)
        score = {k: float(np.dot(a[max(0, -k):n - max(0, k)],
                                 b[max(0, k):n - max(0, -k)])) for k in lags}
        best = max(score, key=score.get)
        if best in (lags[0], lags[-1]) or score[best] <= 0:
            return 0
        # Eindeutig: der beste Versatz muss alle nicht benachbarten klar
        # schlagen (an echtem Material >= Faktor 1,5, unklar bei Stille,
        # Rauschen oder einer anderen Fassung unter demselben Pfad).
        rest = max(v for k, v in score.items() if abs(k - best) > 1)
        if score[best] < 1.25 * rest:
            return 0
        y0, y1, y2 = score[best - 1], score[best], score[best + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den else 0.0
        return int(round((best + frac) * hop))
    except Exception:                                   # noqa: BLE001
        return 0


def _mp3_sample_count(path: str) -> int:
    """Zahl der Audio-Samples einer MP3-Datei aus den Frame-Koepfen (ein
    Xing/Info-Frame zaehlt nicht mit) -- so fuellt Rekordbox PVBR.u2
    (an echtem Material: Frames x 1152). 0, wenn nichts lesbar ist."""
    from .probe import _XING_MAGIC
    rates = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
    kbps = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
            2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
    data = Path(path).read_bytes()
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        i = 10 + size
    samples = 0
    first = True
    while i + 4 <= len(data):
        h = data[i:i + 4]
        if h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
            if samples:
                break                                   # ID3v1/APE am Ende
            i += 1
            continue
        version, layer = (h[1] >> 3) & 3, (h[1] >> 1) & 3
        br_idx, sr_idx, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
        if version == 1 or layer != 1 or br_idx in (0, 15) or sr_idx == 3:
            if samples:
                break
            i += 1
            continue
        rate = rates[version][sr_idx]
        per_frame = 1152 if version == 3 else 576
        length = (per_frame // 8) * kbps[3 if version == 3 else 2][br_idx] * 1000 // rate + pad
        if first and any(m in data[i + 4:i + 40] for m in _XING_MAGIC):
            pass                                        # Info-Frame, kein Audio
        else:
            samples += per_frame
        first = False
        i += length
    return samples


def _stem_pvbr(anlz, stem_path: str) -> None:
    """Setzt PVBR (Datei-Index) in einer kopierten DAT-Datei auf die Stem-Datei:
    Rekordbox schreibt den Block fuer JEDES Format (AIFF: alles 0; MP3/AAC:
    Index 0 bei CBR, u2 = Zahl der Samples). Fehlt er, verwirft Rekordbox die
    DAT-Datei samt Beatgrid (an echtem Material: Cues aus der Datenbank da,
    Grid erst nach einer neuen Analyse). Die Stems sind immer CBR-MP3 oder
    AIFF, ein Index ist nie noetig."""
    import struct
    from pyrekordbox.anlz.tags import PVBRAnlzTag

    n = _mp3_sample_count(stem_path) if stem_path.lower().endswith(".mp3") else 0
    tag = PVBRAnlzTag(struct.pack(">4sII", b"PVBR", 16, 1620)
                      + struct.pack(">402I", 0, *([0] * 400), n))
    tags = [t for t in anlz.tags if t.type != "PVBR"]
    at = next((k + 1 for k, t in enumerate(tags) if t.type == "PPTH"), 0)
    anlz.tags = tags[:at] + [tag] + tags[at:]


def _uuid_dir(uid: str) -> str:
    """Ordner, den Rekordbox fuer Analyse und Cover eines Tracks verwendet:
    die Content-UUID, nach den ersten drei Zeichen geteilt
    ('dd47f5cf-f73a-…' -> 'dd4/7f5cf-f73a-…', an echtem Material abgelesen).
    Liegt die Analyse woanders, verwirft Rekordbox 7 sie beim naechsten Start
    und analysiert den Track neu -- ohne die kopierten Cues und ohne Vorschau,
    bis die Analyse durch ist."""
    return f"{uid[:3]}/{uid[3:]}"


def _clone_analysis(db, source, content, stem_path: str,
                    cues: bool = True) -> list[tuple[str, Path]]:
    """Kopiert die ANLZ-Dateien (DAT/EXT/2EX) des Haupttracks in den Ordner
    des Stems (`_uuid_dir()` seiner Content-UUID) und biegt den Pfad (PPTH)
    auf den Stem um; setzt AnalysisDataPath. Liefert (Rekordbox-Pfad,
    lokaler Pfad) je geschriebener Datei fuer `_add_content_files()`. Das
    Original bleibt unberuehrt. Wirft bei Fehlern und raeumt den halb
    angelegten Ordner ab."""
    from pathlib import PurePosixPath
    from pyrekordbox.anlz import AnlzFile

    orig = (source.AnalysisDataPath or "").strip()
    if not orig:
        raise RuntimeError("Haupttrack hat in Rekordbox keine Analyse")
    src_dir = db.get_anlz_dir(source)
    files = [src_dir / n for n in _STEM_ANLZ_FILES if (src_dir / n).is_file()]
    if not files:
        raise RuntimeError("ANLZ-Dateien des Haupttracks nicht gefunden")

    orig_posix = PurePosixPath(orig.replace("\\", "/"))
    # '/PIONEER/USBANLZ' + 'abc/defg-…'
    rel_dir = orig_posix.parent.parent.parent / _uuid_dir(content.UUID)
    dest_dir = src_dir.parent.parent / _uuid_dir(content.UUID)
    dest_dir.mkdir(parents=True, exist_ok=False)
    written: list[tuple[str, Path]] = []
    try:
        for src_file in files:
            anlz = AnlzFile.parse_file(src_file)
            old = anlz.get("path") if "PPTH" in anlz else ""
            anlz.set_path("?/" + Path(stem_path).name if str(old).startswith("?/")
                          else stem_path)
            if src_file.suffix == ".DAT":
                _stem_pvbr(anlz, stem_path)
            if not cues:
                _empty_cue_tags(anlz)
            anlz.save(dest_dir / src_file.name)
            written.append((str(rel_dir / src_file.name), dest_dir / src_file.name))
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    content.AnalysisDataPath = str(rel_dir / orig_posix.name)
    return written


def _empty_cue_tags(anlz) -> None:
    """Leert die Cue-Listen (PCOB/PCO2) einer ANLZ-Datei, statt sie zu
    entfernen: Rekordbox schreibt sie auch fuer Tracks ohne Cues, dann mit
    count 0 (PCOB memory_count -1) und len_tag = len_header -- an echtem
    Material abgelesen. Ein fehlender Pflicht-Block kostete beim PVBR das
    Beatgrid (siehe _stem_pvbr())."""
    for tag in anlz.tags:
        if tag.type not in ("PCOB", "PCO2"):
            continue
        tag.struct.content.entries = []
        tag.struct.content.count = 0
        if tag.type == "PCOB":
            tag.struct.content.memory_count = -1
        tag.struct.len_tag = tag.struct.len_header


def _clone_artwork(db, source, content) -> list[tuple[str, Path]]:
    """Kopiert Rekordbox' Cover-Dateien (artwork.jpg, _m, _s) des Haupttracks
    in den Artwork-Ordner des Stems und setzt ImagePath. Ohne Cover beim
    Haupttrack: nichts. Ergebnis wie `_clone_analysis()`."""
    from pathlib import PurePosixPath

    image = (source.ImagePath or "").strip()
    if not image:
        return []
    img_posix = PurePosixPath(image.replace("\\", "/"))
    src_dir = db._share_dir / str(img_posix.parent).strip("/")
    if not src_dir.is_dir():
        return []
    rel_dir = img_posix.parent.parent.parent / _uuid_dir(content.UUID)
    dest_dir = src_dir.parent.parent / _uuid_dir(content.UUID)
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, Path]] = []
    for src_file in sorted(src_dir.iterdir()):
        if src_file.is_file() and not src_file.name.startswith("."):
            shutil.copyfile(src_file, dest_dir / src_file.name)
            written.append((str(rel_dir / src_file.name), dest_dir / src_file.name))
    content.ImagePath = str(rel_dir / img_posix.name)
    return written


def _add_content_files(db, source, content, written: list[tuple[str, Path]]) -> None:
    """Traegt die kopierten Analyse-/Cover-Dateien in `contentFile` ein (Pfad,
    MD5, Groesse) -- ueber diese Tabelle fuehrt Rekordbox ab 6.6 Buch ueber
    die Dateien eines Tracks. Eingetragen wird nur, was der Haupttrack dort
    selbst stehen hat (gleicher Dateiname), Spalten wie bei ihm. Aeltere
    Datenbanken ohne die Tabelle: nichts."""
    import hashlib
    from urllib.parse import quote
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables

    model = getattr(tables, "ContentFile", None)
    if model is None or not written:
        return
    try:
        src_rows = {Path(str(r.Path)).name: r for r in
                    db.query(model).filter(model.ContentID == source.ID) if _live(r)}
    except Exception:                                   # noqa: BLE001
        return
    for rel, local in written:
        tmpl = src_rows.get(local.name)
        if tmpl is None:
            continue
        data = local.read_bytes()
        db.add(model(
            ID=f"{content.UUID}_{quote(rel, safe='')}", ContentID=content.ID,
            Path=rel, Hash=hashlib.md5(data).hexdigest(), Size=len(data),
            rb_local_path=str(local), rb_file_hash_dirty=0,
            rb_local_file_status=0, rb_in_progress=0,
            rb_process_type=tmpl.rb_process_type, rb_priority=tmpl.rb_priority,
            rb_file_size_dirty=0, UUID=str(uuid4()), rb_data_status=0,
            rb_local_data_status=0, rb_local_deleted=0, rb_local_synced=0))


_STEM_CUE_COLUMNS = ("InMsec", "InFrame", "InMpegFrame", "InMpegAbs", "OutMsec",
                     "OutFrame", "OutMpegFrame", "OutMpegAbs", "Kind", "Color",
                     "ColorTableIndex", "ActiveLoop", "Comment", "BeatLoopSize",
                     "CueMicrosec", "InPointSeekInfo", "OutPointSeekInfo")


def _clone_cues(db, source, content) -> int:
    """Kopiert die lebenden Hot Cues, Memory Cues und Loops (`DjmdCue`) des
    Haupttracks auf den Stem. Rekordbox liest Cues aus dieser Tabelle, die
    PCOB/PCO2-Tags der ANLZ-Dateien sind nur die Kopie fuer die Player."""
    from uuid import uuid4
    from pyrekordbox.masterdb import models as tables

    n = 0
    for cue in db.query(tables.DjmdCue).filter(tables.DjmdCue.ContentID == source.ID):
        if not _live(cue):
            continue
        db.add(tables.DjmdCue(
            ID=str(db.generate_unused_id(tables.DjmdCue, is_28_bit=False)),
            ContentID=content.ID, ContentUUID=content.UUID, UUID=str(uuid4()),
            rb_data_status=0, rb_local_data_status=0, rb_local_deleted=0,
            rb_local_synced=0,
            **{col: getattr(cue, col) for col in _STEM_CUE_COLUMNS}))
        n += 1
    return n


def add_stems(items: list[dict], metadata: dict, cues: bool = True) -> dict:
    """
    Nimmt freigegebene Stem-Dateien in die Rekordbox-Sammlung auf: neue
    DjmdContent-Zeile (Tags aus unserer DB-Zeile, BPM/Tonart/Dauer vom
    Haupttrack), Beatgrid/Wellenform/Cues durch Kopie der ANLZ-Dateien, Cover,
    contentFile-Eintraege und DjmdCue-Zeilen des Haupttracks, und der Stem
    landet in denselben Playlists wie der Haupttrack.

    'items': [{"path": Stem-Datei, "source": Haupttrack}, ...]. 'metadata':
    Stem-Pfad -> unsere DB-Zeile (wie bei add_tracks_to_playlist()).
    'cues': Hot/Memory Cues und Loops mit uebernehmen (Einstellung
    stems_rekordbox); ohne bleiben die Cue-Listen der ANLZ-Dateien leer.

    Liefert {"added": [...], "skipped": [...], "errors": {pfad: meldung},
    "grid": {pfad: True/False}}. Ein Stem, dessen Haupttrack Rekordbox nicht
    kennt, wird trotzdem aufgenommen, nur ohne Raster (grid False).
    """
    _check_writable()
    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        import datetime
        now = datetime.datetime.now()
        added: list[str] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}
        grid: dict[str, bool] = {}
        touched: dict[str, object] = {}
        for item in items:
            path, src_path = item["path"], item.get("source") or ""
            try:
                if _live_content(db, path) is not None:
                    skipped.append(path)
                    continue
                source = _live_content(db, src_path) if src_path else None
                row = metadata.get(path)
                content = _add_content_fresh(db, path, **_content_tag_kwargs(db, row))
                if row is not None:
                    if row["sample_rate"]:
                        content.SampleRate = row["sample_rate"]
                    if row["declared_kbps"]:
                        content.BitRate = row["declared_kbps"]
                analysis: list = []
                if source is not None:
                    for col in _STEM_COPY_COLUMNS:
                        value = getattr(source, col, None)
                        if value not in (None, ""):
                            setattr(content, col, value)
                    problems: list[str] = []
                    try:
                        analysis = _clone_analysis(db, source, content, path, cues)
                        if int(source.ContentLink or 0) & _CONTENT_LINK_PSSI:
                            content.ContentLink = int(content.ContentLink or 0) | _CONTENT_LINK_PSSI
                    except Exception as exc:               # noqa: BLE001
                        problems.append(f"Raster nicht übernommen: {exc}")
                        # Ohne eigene Analyse wuerde Rekordbox die Spalten
                        # des Haupttracks fuer bare Muenze nehmen.
                        content.Analysed = 0
                        content.AnalysisUpdated = None
                    files = list(analysis)
                    try:
                        files += _clone_artwork(db, source, content)
                    except Exception as exc:               # noqa: BLE001
                        problems.append(f"Cover nicht übernommen: {exc}")
                    _add_content_files(db, source, content, files)
                    if cues:
                        _clone_cues(db, source, content)
                    else:
                        content.CueUpdated = None
                    if problems:
                        errors[path] = "; ".join(problems)
                grid[path] = bool(analysis)
                added.append(path)
                if source is not None:
                    for song in db.get_playlist_songs(ContentID=source.ID):
                        if not _live(song):
                            continue
                        playlist = db.get_playlist(ID=str(song.PlaylistID))
                        if hasattr(playlist, "first"):
                            playlist = playlist.first()
                        if (playlist is None or not _live(playlist)
                                or playlist.Attribute != _RB_PLAYLIST):
                            continue
                        if any(_live(x) for x in db.get_playlist_songs(
                                PlaylistID=playlist.ID, ContentID=content.ID)):
                            continue
                        db.add_to_playlist(playlist, content)
                        touched[str(playlist.ID)] = playlist
            except Exception as exc:                       # noqa: BLE001
                errors[path] = str(exc)
        if touched:
            db.flush()
            for pid in touched:
                _renumber(db, _live_songs(db, pid), now)
        db.commit()
        return {"added": added, "skipped": skipped, "errors": errors, "grid": grid}

    return _with_malformed_retry(pyrekordbox_mod, _run)


def update_relocated_tracks(moves: list[dict], metadata: dict) -> dict:
    """
    Schreibt Pfad- und Tag-Korrekturen auf bereits vorhandene DjmdContent-
    Zeilen zurueck -- fuer Tracks, die Music.app nach einer eigenen
    Tag-Aenderung umbenannt/verschoben hat, waehrend sie schon in Rekordbox'
    Sammlung standen (siehe server._post_relink()). Anders als
    add_tracks_to_playlist() (Tags nur bei NEUEN Zeilen) aendert diese
    Funktion bestehende Zeilen.

    'moves': [{"path": aktueller Pfad, "prior_path": zuletzt bei Rekordbox
    bekannter Pfad}, ...]. 'metadata': aktueller Pfad -> unsere DB-Zeile
    (fuer _content_tag_kwargs(), wie bei add_tracks_to_playlist()).

    Liefert {"updated": [...], "not_found": [...], "errors": {pfad: meldung}}.
    """
    _check_writable()

    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        updated: list[str] = []
        not_found: list[str] = []
        errors: dict[str, str] = {}
        for entry in moves:
            path = entry["path"]
            prior_path = entry.get("prior_path") or path
            try:
                content = _live_content(db, prior_path)
                if content is None and prior_path != path:
                    content = _live_content(db, path)
                if content is None:
                    not_found.append(path)
                    continue
                if content.FolderPath != path:
                    content.FolderPath = path
                    content.FileNameL = Path(path).name
                for key, value in _content_tag_kwargs(
                        db, metadata.get(path), with_key=False).items():
                    setattr(content, key, value)
                updated.append(path)
            except Exception as exc:                      # noqa: BLE001
                errors[path] = str(exc)
        db.commit()
        return {"updated": updated, "not_found": not_found, "errors": errors}

    return _with_malformed_retry(pyrekordbox_mod, _run)
