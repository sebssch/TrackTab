"""
Zugriff auf Rekordbox' eigene Datenbank (master.db, SQLCipher-verschluesselt)
ueber die Drittanbieter-Bibliothek pyrekordbox. Zwei Richtungen:
  1. Lesen: welche Pfade stehen ueberhaupt in der Sammlung (Praesenz-Haken).
  2. Schreiben: Tracks zu einer bestehenden Playlist hinzufuegen.

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

_HOT_CUE_LETTERS = "ABCDEFGH"

# Rekordbox' Standard-Gruen fuer Hot Cues ohne eigene Farbe (aelteres
# "CDJ"-Farbschema, siehe Praeferenzen View->Color->HOT CUE) -- Hot Cues
# haben KEINE eigene Vorgabefarbe je Buchstabe A-H, sondern alle dieselbe.
# Bestaetigt durch Deep-Symmetry/beat-link (dieselbe Quelle, die
# pyrekordbox selbst fuer die ANLZ-Tag-Reihenfolge zitiert), Issue
# https://github.com/Deep-Symmetry/beat-link/issues/51 sowie CueList.java
# (expectedEmbeddedColor(): "the default green color used by older CDJs").
_HOT_CUE_DEFAULT_COLOR = [0x28, 0xE2, 0x14]

# DjmdCue.ColorTableIndex -> RGB, NUR fuer Hot Cues (bei Memory Cues bleibt
# das Feld leer, siehe _MEMORY_CUE_PALETTE unten fuer deren eigene, davon
# komplett unabhaengige Farbwelt). Aus Deep-Symmetry/beat-link
# (CueList.findRekordboxColor()), dort an echtem Material durch Testen
# aller Werte 0-255 gegen die Rekordbox-UI ermittelt (s.o. Issue #51).
# Werte oberhalb 0x3e sind in der Rekordbox-UI nicht waehlbar und rendern
# dort schwarz -- deshalb hier nicht mit abgedeckt.
_HOT_CUE_TABLE_COLORS = {
    0x01: [0x30, 0x5A, 0xFF], 0x02: [0x50, 0x73, 0xFF], 0x03: [0x50, 0x8C, 0xFF],
    0x04: [0x50, 0xA0, 0xFF], 0x05: [0x50, 0xB4, 0xFF], 0x06: [0x50, 0xB0, 0xF2],
    0x07: [0x50, 0xAE, 0xE8], 0x08: [0x45, 0xAC, 0xDB], 0x09: [0x00, 0xE0, 0xFF],
    0x0A: [0x19, 0xDA, 0xF0], 0x0B: [0x32, 0xD2, 0xE6], 0x0C: [0x21, 0xB4, 0xB9],
    0x0D: [0x20, 0xAA, 0xA0], 0x0E: [0x1F, 0xA3, 0x92], 0x0F: [0x19, 0xA0, 0x8C],
    0x10: [0x14, 0xA5, 0x84], 0x11: [0x14, 0xAA, 0x7D], 0x12: [0x10, 0xB1, 0x76],
    0x13: [0x30, 0xD2, 0x6E], 0x14: [0x37, 0xDE, 0x5A], 0x15: [0x3C, 0xEB, 0x50],
    0x16: [0x28, 0xE2, 0x14], 0x17: [0x7D, 0xC1, 0x3D], 0x18: [0x8C, 0xC8, 0x32],
    0x19: [0x9B, 0xD7, 0x23], 0x1A: [0xA5, 0xE1, 0x16], 0x1B: [0xA5, 0xDC, 0x0A],
    0x1C: [0xAA, 0xD2, 0x08], 0x1D: [0xB4, 0xC8, 0x05], 0x1E: [0xB4, 0xBE, 0x04],
    0x1F: [0xBA, 0xB4, 0x04], 0x20: [0xC3, 0xAF, 0x04], 0x21: [0xE1, 0xAA, 0x00],
    0x22: [0xFF, 0xA0, 0x00], 0x23: [0xFF, 0x96, 0x00], 0x24: [0xFF, 0x8C, 0x00],
    0x25: [0xFF, 0x75, 0x00], 0x26: [0xE0, 0x64, 0x1B], 0x27: [0xE0, 0x46, 0x1E],
    0x28: [0xE0, 0x30, 0x1E], 0x29: [0xE0, 0x28, 0x23], 0x2A: [0xE6, 0x28, 0x28],
    0x2B: [0xFF, 0x37, 0x6F], 0x2C: [0xFF, 0x2D, 0x6F], 0x2D: [0xFF, 0x12, 0x7B],
    0x2E: [0xF5, 0x1E, 0x8C], 0x2F: [0xEB, 0x2D, 0xA0], 0x30: [0xE6, 0x37, 0xB4],
    0x31: [0xDE, 0x44, 0xCF], 0x32: [0xDE, 0x44, 0x8D], 0x33: [0xE6, 0x30, 0xB4],
    0x34: [0xE6, 0x19, 0xDC], 0x35: [0xE6, 0x00, 0xFF], 0x36: [0xDC, 0x00, 0xFF],
    0x37: [0xCC, 0x00, 0xFF], 0x38: [0xB4, 0x32, 0xFF], 0x39: [0xB9, 0x3C, 0xFF],
    0x3A: [0xC5, 0x42, 0xFF], 0x3B: [0xAA, 0x5A, 0xFF], 0x3C: [0xAA, 0x72, 0xFF],
    0x3D: [0x82, 0x72, 0xFF], 0x3E: [0x64, 0x73, 0xFF],
}

# Rekordbox' Standard-Rot fuer Memory Cues ohne eigene Farbe -- bestaetigt
# durch Deep-Symmetry/beat-link (CueList.getNexusColor()).
_MEMORY_CUE_DEFAULT_COLOR = [0xFF, 0x00, 0x00]

# DjmdCue.Color ist bei MEMORY Cues (anders als bei Hot Cues, siehe oben)
# KEIN gepacktes RGB, sondern derselbe 8-Farben-Index wie Rekordbox'
# Track-Farben (0/-1 = keine Farbe). Bestaetigt durch Deep-Symmetry/
# beat-link (ColorItem.colorForId(), Java-AWT-Standardfarben Pink/Red/
# Orange/Yellow/Green/Cyan/Blue plus eigenem Lila) UND an echtem Material:
# eine als "Orange" erkennbare Memory-Cue-Farbe stand hier als Color=3.
_MEMORY_CUE_PALETTE = {
    1: [255, 175, 175],  # Pink
    2: [255, 0, 0],      # Red
    3: [255, 200, 0],    # Orange
    4: [255, 255, 0],    # Yellow
    5: [0, 255, 0],       # Green
    6: [0, 255, 255],     # Aqua/Cyan
    7: [0, 0, 255],       # Blue
    8: [128, 0, 128],     # Purple
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
    Stand 0.4.4 -- auch im master-Branch auf GitHub noch nicht behoben, an
    echtem Material bestaetigt). Betrifft vermutlich jede Installation mit
    angepassten Tastenkuerzeln/Tabellenspalten, nicht nur einen Einzelfall.

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
    Verhindert, dass Rekordbox6Database() beim Start intern erneut
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


def _backup_master_db(src: Path) -> Path:
    """
    Rohe Dateikopie von master.db bei JEDEM Oeffnen -- lesend wie schreibend,
    nicht nur vor einem Schreibzugriff. Behaelt die juengsten
    _BACKUP_KEEP Stueck, aeltere werden automatisch geloescht.
    """
    dest_dir = cfgmod.resolve("backup") / "rekordbox"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_BACKUP_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copyfile(src, dest)
    backups = sorted(dest_dir.glob(f"{_BACKUP_PREFIX}*.db"))
    for p in backups[:-_BACKUP_KEEP]:
        p.unlink(missing_ok=True)
    return dest


def _open(pyrekordbox_mod):
    try:
        db_path = _resolve_master_db_path()
        _backup_master_db(db_path)
        _stub_pyrekordbox_config(pyrekordbox_mod)
        return pyrekordbox_mod.Rekordbox6Database(path=db_path)
    except RekordboxUnavailable:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise RekordboxUnavailable(
            f"Rekordbox-Datenbank konnte nicht geoeffnet werden: {exc}") from exc


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
        return fn(db)
    except Exception as exc:                          # noqa: BLE001
        if not _is_stale_cache_error(exc):
            raise
        try:
            db.close()
        except Exception:                              # noqa: BLE001
            pass
        time.sleep(1.0)
        db = _open(pyrekordbox_mod)
        return fn(db)
    finally:
        try:
            db.close()
        except Exception:                              # noqa: BLE001
            pass


def collection_paths() -> set[str]:
    """Alle Dateipfade in der Rekordbox-Sammlung (read-only)."""
    pyrekordbox_mod = _import_pyrekordbox()
    return _with_malformed_retry(
        pyrekordbox_mod,
        lambda db: {c.FolderPath for c in db.get_content() if c.FolderPath})


_cached_db = None            # (Rekordbox6Database, master_db_path, mtime)
_cache_lock = threading.Lock()


def _open_cached(pyrekordbox_mod):
    """
    Haelt eine einzelne Rekordbox6Database-Verbindung offen und erneuert sie
    nur, wenn sich master.db seitdem geaendert hat -- anders als _open()
    (fuer seltene Vorgaenge wie Praesenz-Abgleich/Playlist-Schreibzugriff)
    waere ein Voll-Backup+Neuoeffnen bei JEDER einzelnen Cue-/Waveform-
    Abfrage (potenziell eine pro im Player aufgeklappter Zeile) unnoetig
    teuer.
    """
    global _cached_db
    db_path = _resolve_master_db_path()
    mtime = db_path.stat().st_mtime
    with _cache_lock:
        if _cached_db is not None and _cached_db[1] == db_path and _cached_db[2] == mtime:
            return _cached_db[0]
        if _cached_db is not None:
            try:
                _cached_db[0].close()
            except Exception:                          # noqa: BLE001
                pass
        _backup_master_db(db_path)
        _stub_pyrekordbox_config(pyrekordbox_mod)
        db = pyrekordbox_mod.Rekordbox6Database(path=db_path)
        _cached_db = (db, db_path, mtime)
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
    db = _open_cached(pyrekordbox_mod)
    try:
        return fn(db)
    except Exception as exc:                          # noqa: BLE001
        if not _is_stale_cache_error(exc):
            raise
        with _cache_lock:
            if _cached_db is not None:
                try:
                    _cached_db[0].close()
                except Exception:                      # noqa: BLE001
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
            if not path:
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


def _decode_color(color: int | None) -> list[int] | None:
    """Gepacktes 0xRRGGBB -> [r,g,b], oder None wenn keine Farbe gesetzt
    (Color == -1). Gilt NUR fuer Hot Cues/Loops (eingebettetes RGB, z.B. aus
    einem Serato-Import) -- bei Memory Cues bedeutet Color etwas anderes,
    siehe _MEMORY_CUE_PALETTE. An echtem Material bestaetigt: Color=255 bei
    einer erkennbar blauen Schleifenmarkierung ergibt (0,0,255), reines Blau,
    was zur ueblichen Rekordbox-Loop-Farbe passt."""
    if color is not None and 0 <= color <= 0xFFFFFF:
        return [(color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF]
    return None


def _hot_cue_color(color: int | None, color_table_index: int | None) -> list[int]:
    """Farbe eines Hot Cues in der Prioritaet, die Rekordbox selbst nutzt:
    ColorTableIndex (eigene Palette bis 0x3e, siehe _HOT_CUE_TABLE_COLORS)
    vor eingebettetem RGB (Color, siehe _decode_color) vor dem Standard-
    Gruen fuer noch nicht eingefaerbte Hot Cues."""
    if color_table_index is not None and color_table_index in _HOT_CUE_TABLE_COLORS:
        return list(_HOT_CUE_TABLE_COLORS[color_table_index])
    embedded = _decode_color(color)
    if embedded is not None:
        return embedded
    return list(_HOT_CUE_DEFAULT_COLOR)


def _memory_cue_color(color: int | None) -> list[int]:
    """Farbe eines Memory Cues ueber die 8-Farben-Palette (siehe
    _MEMORY_CUE_PALETTE); ohne zugewiesene Farbe Rekordbox' Standard-Rot."""
    if color is not None and color in _MEMORY_CUE_PALETTE:
        return list(_MEMORY_CUE_PALETTE[color])
    return list(_MEMORY_CUE_DEFAULT_COLOR)


def _read_waveform(db, content, style: str) -> dict | None:
    """
    Farbige Waveform-Daten aus Rekordbox' Analysedateien fuer EINEN Track,
    im gewaehlten Stil. Liefert None, wenn die passende Analysedatei/der
    passende Tag fehlt (z.B. eine alte Analyse ohne .2EX) -- eigener,
    von den Cues getrennter Fehlerpfad (siehe get_track_extras()), damit ein
    kaputtes/fehlendes .EXT/.2EX nicht auch die laengst funktionierenden
    Cues mit wegwirft.

    Werte pro Stil anhand einer visuellen Vorschau am echten Material
    geprueft (nicht nur aus der pyrekordbox-Doku uebernommen):
    - rgb (.EXT, Tag PWV4/wf_color): Hoehe aus der "back"-Spalte (glattere
      Huellkurve, max(d2,d3,d4)), Farbe aus der "front"-Spalte (col_color) --
      bewusst aus unterschiedlichen Spalten gemischt, die reine front-Hoehe
      (d5) wirkte zu klein/jittrig. Die dritte Rueckgabe von f.get("wf_color")
      (col_blues) wird NICHT verwendet -- das ist eine eigenstaendige, von
      RGB unabhaengige Blau-Rendervariante aus derselben Datei.
    - 3band (.2EX, Tag PWV6): pyrekordbox hat dafuer keinen Decoder, nur der
      rohe 3-Byte-je-Spalte-Container. Reihenfolge laut externer ANLZ-
      Referenz (Deep Symmetry, von pyrekordbox selbst zitiert): Mitten,
      Hoehen, Tief -- NICHT Bass/Mitten/Hoehen.
    - standard: erzwingt die graue Waveform -- kein Datei-Zugriff, liefert
      direkt None, identischer Fallback-Pfad wie "nicht in Rekordbox".
    """
    if style == "standard":
        return None
    if style not in ("rgb", "3band"):
        style = "rgb"
    if style == "rgb":
        f = db.read_anlz_file(content, "EXT")
        if f is None:
            return None
        heights, col_color, _col_blues = f.get("wf_color")
        if heights is None or len(heights) == 0:
            return None
        return {
            "style": "rgb",
            "height": [round(float(v) / 127.0, 4) for v in heights[:, 1]],
            "color": [[int(c) for c in col_color[i, 0].clip(0, 255)] for i in range(len(heights))],
        }
    # 3band -- Dateityp-Key ist "2EX", nicht "EX2" (Docstring-Fehler in
    # pyrekordbox selbst, siehe Plan).
    f = db.read_anlz_file(content, "2EX")
    if f is None:
        return None
    container = f.get("PWV6")
    raw = container.entries
    n = len(raw) // 3
    if n == 0:
        return None
    return {
        "style": "3band",
        "mid": [round(min(1.0, raw[i * 3] / 31.0), 4) for i in range(n)],
        "high": [round(min(1.0, raw[i * 3 + 1] / 31.0), 4) for i in range(n)],
        "low": [round(min(1.0, raw[i * 3 + 2] / 31.0), 4) for i in range(n)],
    }


def get_track_extras(path: str, style: str = "rgb") -> dict | None:
    """
    Hot Cues, Memory Cues und die farbige Waveform aus Rekordbox fuer EINEN
    Track -- nur wenn der Track dort bereits in der Sammlung UND analysiert
    ist. `style` waehlt die Waveform-Variante ("rgb"/"3band", siehe
    _read_waveform()); fehlt die passende Analysedatei, bleibt "waveform"
    None und der Aufrufer zeigt die graue Standardanzeige.

    Liefert None bei jedem Fehler (pyrekordbox fehlt, master.db nicht
    gefunden, Track unbekannt) -- der Aufrufer zeigt dann weder Cues noch
    Waveform, absichtlich ein einziger, undifferenzierter Fallback-Pfad.
    """
    try:
        pyrekordbox_mod = _import_pyrekordbox()
        return _query_cached(pyrekordbox_mod, lambda db: _track_extras(db, path, style))
    except Exception:                                  # noqa: BLE001
        return None


def _track_extras(db, path: str, style: str) -> dict | None:
    content = db.get_content(FolderPath=path).first()
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
        if c.is_hot_cue and 1 <= c.Kind <= 8:
            cues.append({
                "kind": "hot", "slot": c.Kind,
                "label": _HOT_CUE_LETTERS[c.Kind - 1],
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

    waveform = None
    try:
        waveform = _read_waveform(db, content, style)
    except Exception:                              # noqa: BLE001
        pass

    return {"cues": cues, "waveform": waveform}


def is_running() -> bool:
    """Oeffentlich (nicht nur intern vor add_tracks_to_playlist() benutzt) --
    der Server bietet darueber einen eigenen, leichten Vorab-Check an
    (GET /api/rekordbox-status), damit die Oberflaeche VOR einem Schreib-
    versuch warnen kann, statt erst nach einem fehlgeschlagenen Aufruf."""
    _import_pyrekordbox()
    from pyrekordbox.utils import get_rekordbox_pid
    return bool(get_rekordbox_pid())


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
            lambda db: db.get_content(FolderPath=path).first() is not None))
    except Exception:                                  # noqa: BLE001
        return False


def master_db_mtime() -> float | None:
    """Reiner Dateicheck (kein Oeffnen) fuer stats.is_stale() -- ob sich
    master.db seit dem letzten Statistik-Bau geaendert hat. None bei jedem
    Fehler (kein pyrekordbox, keine Installation gefunden)."""
    try:
        return _resolve_master_db_path().stat().st_mtime
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
# Kommentar: Commnt ist reiner Text, keine eigene Tabelle -- aber ein
# technisches Hex-Feld (z.B. ein beim Encodieren verschlepptes
# iTunSMPB/iTunNORM-Gapless-Tag, siehe tags.is_junk_comment()) soll dort
# genauso wenig landen wie beim eigenen Tags-Dialog. Jahr: 'year' ist bei
# fehlendem Tag 0 (siehe tags._parse_year()), kein echtes Jahr -- wie bei
# BPM nur bei einem wahren Wert setzen, sonst stuende in Rekordbox ueberall
# "1" statt eines leeren Feldes.
def _content_tag_kwargs(db, row) -> dict:
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
    return kwargs


# Attribute-Werte von DjmdPlaylist (pyrekordbox.db6.tables.PlaylistType).
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
                contents = list(db.get_playlist_contents(node))
            except Exception as exc:                       # noqa: BLE001
                if _is_stale_cache_error(exc):
                    raise
                raise SmartListUnsupported(str(exc)[:200]) from exc
        else:
            songs = sorted(db.get_playlist_songs(PlaylistID=playlist_id),
                           key=lambda s: (s.TrackNo or 0))
            # Gleicher Soft-Delete-Filter wie in playlists() -- sonst tauchen
            # laengst entfernte Tracks wieder in der Trackliste auf.
            contents = [s.Content for s in songs
                        if s.Content is not None and not getattr(s, "rb_local_deleted", 0)]
        for content in contents:
            artist = getattr(content, "Artist", None)
            rows.append({
                "path": content.FolderPath or "",
                "title": content.Title or "",
                "artist": getattr(artist, "Name", "") or "",
            })
        return rows

    return _query_cached(mod, _query)


def add_tracks_to_playlist(paths: list[str], playlist_name: str,
                            metadata: dict | None = None) -> dict:
    """
    Fuegt Tracks zu einer BESTEHENDEN Rekordbox-Playlist hinzu. Legt die
    Playlist nicht an -- ein unbekannter Name ist ein Fehler, kein Auto-Create.

    'metadata' (optional, Pfad -> unsere eigene DB-Zeile aus files.py) liefert
    Titel/Interpret/Album/Genre/BPM/Komponist/Jahr/Kommentar fuer Tracks, die
    Rekordbox noch nicht kennt -- siehe _content_tag_kwargs().

    Liefert {"added": [...], "skipped": [...], "errors": {pfad: meldung}}.
    """
    metadata = metadata or {}
    playlist_name = (playlist_name or "").strip()
    if not playlist_name:
        raise ValueError("Keine Rekordbox-Playlist eingestellt (Einstellungen -> "
                          "Externe Programme).")
    if is_running():
        raise RekordboxRunning(
            "Rekordbox ist gerade geöffnet. Bitte Rekordbox beenden und es "
            "anschließend erneut versuchen — ein Schreibzugriff während "
            "Rekordbox läuft könnte die Bibliothek beschädigen.")

    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        playlist = _find_playlist(db, playlist_name)
        if playlist is None:
            raise PlaylistNotFound(
                f"Playlist „{playlist_name}“ existiert nicht in Rekordbox. "
                "Erst dort anlegen, dann hier erneut versuchen (bei "
                "mehrdeutigen Namen den Ordnerpfad angeben, z.B. "
                "'Ordner/Playlist').")

        added: list[str] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}
        for path in paths:
            try:
                content = db.get_content(FolderPath=path).first()
                if content is None:
                    try:
                        content = db.add_content(path, **_content_tag_kwargs(db, metadata.get(path)))
                    except ValueError:
                        # Wettlauf mit einem anderen Prozess -- zwischen der
                        # Abfrage oben und hier doch schon angelegt worden.
                        content = db.get_content(FolderPath=path).first()
                        if content is None:
                            raise
                already = db.get_playlist_songs(
                    PlaylistID=playlist.ID, ContentID=content.ID).first()
                if already is not None:
                    skipped.append(path)
                    continue
                db.add_to_playlist(playlist, content)
                added.append(path)
            except Exception as exc:                     # noqa: BLE001
                errors[path] = str(exc)
        db.commit()
        return {"added": added, "skipped": skipped, "errors": errors}

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
    if is_running():
        raise RekordboxRunning(
            "Rekordbox ist gerade geöffnet. Bitte Rekordbox beenden und es "
            "anschließend erneut versuchen — ein Schreibzugriff während "
            "Rekordbox läuft könnte die Bibliothek beschädigen.")

    pyrekordbox_mod = _import_pyrekordbox()

    def _run(db):
        updated: list[str] = []
        not_found: list[str] = []
        errors: dict[str, str] = {}
        for entry in moves:
            path = entry["path"]
            prior_path = entry.get("prior_path") or path
            try:
                content = db.get_content(FolderPath=prior_path).first()
                if content is None and prior_path != path:
                    content = db.get_content(FolderPath=path).first()
                if content is None:
                    not_found.append(path)
                    continue
                if content.FolderPath != path:
                    content.FolderPath = path
                    content.FileNameL = Path(path).name
                for key, value in _content_tag_kwargs(db, metadata.get(path)).items():
                    setattr(content, key, value)
                updated.append(path)
            except Exception as exc:                      # noqa: BLE001
                errors[path] = str(exc)
        db.commit()
        return {"updated": updated, "not_found": not_found, "errors": errors}

    return _with_malformed_retry(pyrekordbox_mod, _run)
