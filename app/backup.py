"""
Datenbank-Backups: automatisch (hoechstens eins pro Kalendertag beim Start
jedes CLI-Befehls, dazu zusaetzlich jedes Mal beim Beenden von 'serve()')
und manuell (Export an einen beliebigen Ort, Wiederherstellung ueber die
Oberflaeche oder CLI).

Ein Backup ist ein 'VACUUM INTO'-Abzug von quality.db, kein rohes
Datei-Kopieren -- das liefert ein sauberes, konsistentes Abbild auch wenn
irgendwo noch eine Verbindung offen ist, statt eine halbgeschriebene Kopie
zu riskieren.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from . import config as cfgmod
from . import db as db_mod

_NAME_PREFIX = "quality-"
_NAME_FMT = _NAME_PREFIX + "%Y%m%d-%H%M%S.db"
_DAY_LEN = len(_NAME_PREFIX) + 8   # ".../quality-JJJJMMTT" -- Tagesteil des Namens


def backup_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or cfgmod.load()
    d = cfgmod.resolve(cfg.get("backup_path", "backup"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backups(cfg: dict | None = None) -> list[Path]:
    """Aelteste zuerst -- der Dateiname ist chronologisch sortierbar."""
    return sorted(backup_dir(cfg).glob(f"{_NAME_PREFIX}*.db"))


def _db_path(cfg: dict) -> Path:
    return cfgmod.resolve(cfg["db_path"])


def create_backup(cfg: dict | None = None, dest: str | Path | None = None) -> Path:
    """
    Erstellt einen Snapshot der aktuellen Datenbank per VACUUM INTO.
    Ohne 'dest' landet er mit Zeitstempel im Namen in backup_dir() (fuer die
    automatische Rotation); mit 'dest' ist es ein manueller Export irgendwo
    hin -- der zaehlt bewusst nicht zur automatischen 10er-Rotation.
    """
    cfg = cfg or cfgmod.load()
    src = _db_path(cfg)
    dest = Path(dest) if dest is not None else backup_dir(cfg) / time.strftime(_NAME_FMT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    return dest


def prune_backups(cfg: dict | None = None) -> int:
    """Behaelt nur die juengsten backup_keep automatischen Backups."""
    cfg = cfg or cfgmod.load()
    keep = int(cfg.get("backup_keep", 10))
    backups = list_backups(cfg)
    excess = backups[:-keep] if keep > 0 else backups
    for p in excess:
        p.unlink(missing_ok=True)
    return len(excess)


def maybe_auto_backup(cfg: dict | None = None) -> Path | None:
    """
    Hoechstens ein automatisches Backup pro Kalendertag -- sonst waeren die
    10 Plaetze bei mehreren Starts am selben Tag im Nu weg und es gaebe kaum
    Historie ueber mehrere Tage. Wird beim Start jedes CLI-Befehls geprueft.
    """
    cfg = cfg or cfgmod.load()
    if not _db_path(cfg).exists():
        return None   # nichts zu sichern (frische Installation)
    today = time.strftime("%Y%m%d")
    existing = list_backups(cfg)
    if any(p.name[len(_NAME_PREFIX):_DAY_LEN] == today for p in existing):
        return None
    path = create_backup(cfg)
    prune_backups(cfg)
    return path


def backup_on_close(cfg: dict | None = None) -> Path | None:
    """
    Backup beim Beenden von 'serve()' -- anders als maybe_auto_backup() NICHT
    auf eins pro Kalendertag begrenzt, weil genau das Schliessen (Server-Stop,
    Cmd+Q/Dock/'/api/quit' der gepackten App) der Zeitpunkt ist, an dem der
    Nutzer den aktuellen Stand gesichert haben will -- auch bei mehreren
    Sitzungen am selben Tag.
    """
    cfg = cfg or cfgmod.load()
    if not _db_path(cfg).exists():
        return None   # nichts zu sichern (frische Installation)
    path = create_backup(cfg)
    prune_backups(cfg)
    return path


def _validate(path: Path) -> None:
    """Kurzer Plausibilitaetscheck, bevor eine Datei als Datenbank eingespielt
    wird -- ein klarer Fehler ist besser als eine kaputte quality.db."""
    try:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("SELECT COUNT(*) FROM files")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ValueError(f"Keine gültige Datenbank: {exc}") from exc


def restore_backup(source: str | Path, cfg: dict | None = None) -> Path | None:
    """
    Spielt ein Backup als aktuelle Datenbank ein. Der bisherige Stand wird
    vorher selbst weggesichert (Rueckgabewert) -- eine Wiederherstellung soll
    sich noetigenfalls wieder rueckgaengig machen lassen.
    """
    cfg = cfg or cfgmod.load()
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Backup nicht gefunden: {source}")
    _validate(source)

    dest = _db_path(cfg)
    safety = None
    if dest.exists():
        try:
            safety = create_backup(cfg)
        except sqlite3.Error:
            # Die aktuelle Datenbank ist selbst nicht sauber lesbar (z.B.
            # beschaedigt) -- genau dann darf die Sicherung nicht an einem
            # VACUUM scheitern. Rohe Kopie der Bytes ist dann die einzige
            # Moeglichkeit, den alten Stand ueberhaupt noch aufzuheben.
            safety = backup_dir(cfg) / time.strftime(_NAME_FMT)
            shutil.copyfile(dest, safety)
        prune_backups(cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    # quality.db laeuft im WAL-Modus (siehe db.connect). Ein liegengebliebenes
    # -wal/-shm gehoert zur ALTEN Datei -- SQLite wuerde es sonst auf den
    # frisch eingespielten Stand anwenden und ihn damit verfaelschen.
    for suffix in ("-wal", "-shm"):
        stale = dest.with_name(dest.name + suffix)
        try:
            stale.unlink()
        except OSError:
            pass
    # Der eingespielte Stand kann ein aelteres Schema haben -- die einmalige
    # Einrichtung je Prozess muss ihn deshalb erneut sehen.
    db_mod.forget_setup(dest)
    return safety
