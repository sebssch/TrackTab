"""
Jahres-/Monats-Statistik aus dem Aenderungsprotokoll (audit_log.py) und der
events-Tabelle (Wiedergabezeit, siehe db.py) -- lazy generiert nach
data/stats.json, siehe is_stale(). Getrennt von report.py: kein HTML, 'files'
dient hier nur als Zusatz-Join fuer Top-Track-/Top-Interpret-/Top-Genre-
Metadaten (Artist/Titel/Album/Genre/Cover-Flag), nicht als eigene Quelle.

Zwei komplementaere, unabhaengige Rohquellen:
- Aktionszaehler (reencode/convert/tags/cover/...) stehen NUR im taeglichen
  JSON-Lines-Audit-Log (audit_log.py). Das Kalenderdatum kommt aus dem
  Dateinamen ("%Y-%m-%d.json"), nicht aus der Zeile selbst (die traegt nur
  die Uhrzeit).
- Wiedergabezeit/Top-Tracks kommen NUR aus der DB-Tabelle 'events'
  (kind='play', >= 30s qualifizierende Hoerzeit, siehe app.js/server.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import audit_log
from . import config as cfgmod
from . import rekordbox as rekordbox_mod

# Die UI zeigt nur die ersten 10 Top-Tracks als Kacheln, aber alle 50 werden
# fuer "Playlist aus Top 50 erstellen" gebraucht -- ein Datensatz, zwei
# Verwendungen, keine zweite Abfrage.
_TOP_TRACKS_LIMIT = 50
_TOP_GROUP_LIMIT = 10


def stats_path(cfg: dict | None = None) -> Path:
    cfg = cfg or cfgmod.load()
    return cfgmod.resolve(cfg["stats_path"])


def _newest_log_mtime(cfg: dict) -> float:
    """Billiger Vergleichswert fuer is_stale(): nur stat(), keine Inhalte."""
    newest = 0.0
    for f in audit_log.log_dir(cfg).glob("*.json"):
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            continue
    return newest


def _max_event_ts(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT MAX(ts) AS m FROM events").fetchone()
    return float(row["m"]) if row and row["m"] is not None else 0.0


def is_stale(cfg: dict, conn: sqlite3.Connection) -> bool:
    """True bei fehlender/kaputter Datei oder wenn eine der Quellen seit dem
    letzten Bau gewachsen ist. Absichtlich '>' statt '!=' fuer die ersten
    beiden: die Vergleichswerte koennen nur wachsen, nie schrumpfen (ausser
    durch manuelles Loeschen alter Logs, was hoechstens einen unnoetigen
    Rebuild ausloest, nie einen ausbleibenden). Die Rekordbox-master.db-mtime
    (nur relevant, wenn eine Rekordbox-Playlist verknuepft ist, siehe
    _iter_rekordbox_plays()) ist dagegen NICHT monoton -- ein wiederherge-
    stelltes aelteres Backup kann sie verkleinern -- deshalb dort '!='."""
    try:
        data = json.loads(stats_path(cfg).read_text(encoding="utf-8"))
        covers = data["covers_through"]
    except (OSError, ValueError, KeyError):
        return True
    if _newest_log_mtime(cfg) > covers.get("newest_log_mtime", 0.0):
        return True
    if _max_event_ts(conn) > covers.get("max_event_ts", 0.0):
        return True
    if cfg.get("rekordbox_playlist"):
        if (rekordbox_mod.master_db_mtime() or 0.0) != covers.get("rekordbox_db_mtime", 0.0):
            return True
    return False


def _iter_log_actions(cfg: dict) -> Iterator[tuple[int, int, str]]:
    """(jahr, monat_0basiert, action) je Log-Zeile. Kaputte Zeilen werden
    einzeln uebersprungen, nicht die ganze Datei verworfen."""
    for f in sorted(audit_log.log_dir(cfg).glob("*.json")):
        try:
            day = datetime.strptime(f.stem, "%Y-%m-%d")
        except ValueError:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            action = entry.get("action")
            if action:
                yield day.year, day.month - 1, str(action)


def _iter_play_events(conn: sqlite3.Connection) -> Iterator[tuple[int, int, str, float]]:
    """(jahr, monat_0basiert, path, duration_s) je qualifizierende Wiedergabe."""
    rows = conn.execute("SELECT ts, path, duration_s FROM events WHERE kind = 'play'")
    for r in rows:
        if not r["ts"] or not r["path"] or not r["duration_s"]:
            continue
        dt = datetime.fromtimestamp(r["ts"])
        yield dt.year, dt.month - 1, r["path"], float(r["duration_s"])


def _empty_year() -> dict:
    return {
        "actions": defaultdict(int),
        "listen_seconds": 0.0,
        "months": [{"actions": defaultdict(int), "listen_seconds": 0.0} for _ in range(12)],
    }


def _aggregate(cfg: dict, conn: sqlite3.Connection):
    """Ein Pass je Quelle -> {jahr: {...}}, plus die Rohliste (pfad, dauer)
    je Jahr fuer _top_tracks()/_top_grouped() (spart eine zweite Abfrage)."""
    years: dict[int, dict] = defaultdict(_empty_year)
    raw_plays: dict[int, list[tuple[str, float]]] = defaultdict(list)

    for year, month, action in _iter_log_actions(cfg):
        y = years[year]
        y["actions"][action] += 1
        y["months"][month]["actions"][action] += 1

    for year, month, path, duration_s in _iter_play_events(conn):
        y = years[year]
        y["listen_seconds"] += duration_s
        y["months"][month]["listen_seconds"] += duration_s
        raw_plays[year].append((path, duration_s))

    return years, raw_plays


def _files_meta(conn: sqlite3.Connection, paths: list[str]) -> dict[str, dict]:
    """Ein SELECT fuer die Vereinigungsmenge aller betroffenen Pfade ueber
    alle Jahre, statt einer Abfrage je Track/Jahr."""
    if not paths:
        return {}
    marks = ",".join("?" * len(paths))
    rows = conn.execute(
        f"SELECT path, artist, title, album, genre, has_cover FROM files "
        f"WHERE path IN ({marks})", paths)
    return {r["path"]: dict(r) for r in rows}


def _top_tracks(meta: dict[str, dict], raw_plays: list[tuple[str, float]],
                limit: int = _TOP_TRACKS_LIMIT) -> list[dict]:
    totals: dict[str, list] = {}
    for path, duration_s in raw_plays:
        entry = totals.setdefault(path, [0.0, 0])
        entry[0] += duration_s
        entry[1] += 1
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    out = []
    for path, (seconds, plays) in ranked:
        m = meta.get(path, {})
        out.append({
            "path": path,
            "artist": m.get("artist") or "",
            "title": m.get("title") or Path(path).stem,
            "album": m.get("album") or "",
            "has_cover": int(m.get("has_cover") or 0),
            "seconds": round(seconds),
            "plays": plays,
        })
    return out


def _top_grouped(raw_plays: list[tuple[str, float]], meta: dict[str, dict],
                 field: str, limit: int = _TOP_GROUP_LIMIT) -> list[dict]:
    """Top 'limit' nach Hoerzeit, gruppiert nach files.<field> (artist/genre).
    Pfade ohne Wert in diesem Feld werden ausgeschlossen statt als
    'Unbekannt' gefuehrt -- verzerrt sonst bei duennem Tag-Bestand die
    Spitze der Liste."""
    totals: dict[str, list] = {}
    for path, duration_s in raw_plays:
        value = (meta.get(path, {}).get(field) or "").strip()
        if not value:
            continue
        entry = totals.setdefault(value, [0.0, 0, set()])
        entry[0] += duration_s
        entry[1] += 1
        entry[2].add(path)
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    return [
        {field: value, "seconds": round(seconds), "plays": plays, "tracks": len(track_paths)}
        for value, (seconds, plays, track_paths) in ranked
    ]


def _iter_rekordbox_plays(cfg: dict) -> list[dict]:
    """Rekordbox' eigene History (siehe rekordbox.read_history_plays()) --
    NUR wenn eine Rekordbox-Playlist in den Einstellungen verknuepft ist
    (dieselbe Bedingung, die im Rest der App schon alle Rekordbox-Knoepfe
    gated). Ohne Verknuepfung wird master.db erst gar nicht angefasst.
    Jeder Fehler (pyrekordbox fehlt, master.db nicht gefunden/kaputt) ist
    fuer den Rest der Statistik nicht fatal -- leere Liste statt Absturz."""
    if not cfg.get("rekordbox_playlist"):
        return []
    try:
        return rekordbox_mod.read_history_plays()
    except Exception:                                  # noqa: BLE001
        return []


def _rb_top_tracks(plays: list[dict], meta: dict[str, dict],
                   limit: int = _TOP_TRACKS_LIMIT) -> list[dict]:
    """Wie _top_tracks(), aber nach Play-Count statt Hoerzeit (Rekordbox'
    History kennt keine Dauer je Eintrag) und mit Titel/Interpret/Genre aus
    der Rekordbox-Zeile selbst statt aus 'files' -- viele History-Tracks
    liegen ausserhalb der gescannten Ordner. 'meta' liefert nur has_cover
    fuer Titel, die zufaellig auch bei uns gescannt sind."""
    totals: dict[str, list] = {}
    for p in plays:
        entry = totals.setdefault(p["path"], [0, p])
        entry[0] += 1
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    out = []
    for path, (plays_count, sample) in ranked:
        m = meta.get(path, {})
        out.append({
            "path": path,
            "artist": sample["artist"],
            "title": sample["title"] or Path(path).stem,
            "has_cover": int(m.get("has_cover") or 0),
            "plays": plays_count,
        })
    return out


def _rb_top_grouped(plays: list[dict], field: str,
                    limit: int = _TOP_GROUP_LIMIT) -> list[dict]:
    """Wie _top_grouped(), aber nach Play-Count und direkt auf den
    Rekordbox-Feldern (artist/genre) statt ueber 'files' gruppiert."""
    totals: dict[str, list] = {}
    for p in plays:
        value = (p.get(field) or "").strip()
        if not value:
            continue
        entry = totals.setdefault(value, [0, set()])
        entry[0] += 1
        entry[1].add(p["path"])
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    return [
        {field: value, "plays": count, "tracks": len(track_paths)}
        for value, (count, track_paths) in ranked
    ]


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".stats-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build(cfg: dict, conn: sqlite3.Connection) -> dict:
    """Kompletter Aufbau, atomar nach stats_path() geschrieben. Synchron im
    Request-Thread: bei der aktuellen und absehbaren Datengroesse (wenige
    tausend Log-Zeilen/events-Zeilen) ein Bruchteil einer Sekunde -- kein
    Hintergrund-Worker noetig (bewusste Vorgabe, siehe Plan)."""
    years, raw_plays = _aggregate(cfg, conn)

    all_paths = sorted({path for plays in raw_plays.values() for path, _ in plays})
    meta = _files_meta(conn, all_paths)

    rb_plays = _iter_rekordbox_plays(cfg)
    rb_by_year: dict[int, list[dict]] = defaultdict(list)
    for p in rb_plays:
        rb_by_year[p["year"]].append(p)
    rb_meta = _files_meta(conn, sorted({p["path"] for p in rb_plays}))

    all_years = sorted(set(years.keys()) | set(rb_by_year.keys()), reverse=True)

    out_years: dict[str, dict] = {}
    for year in all_years:
        y = years.get(year) or _empty_year()
        plays = raw_plays.get(year, [])
        rb_year_plays = rb_by_year.get(year, [])
        out_years[str(year)] = {
            "actions": dict(y["actions"]),
            "listen_seconds": round(y["listen_seconds"]),
            "months": [
                {"actions": dict(m["actions"]), "listen_seconds": round(m["listen_seconds"])}
                for m in y["months"]
            ],
            "top_tracks": _top_tracks(meta, plays),
            "top_artists": _top_grouped(plays, meta, "artist"),
            "top_genres": _top_grouped(plays, meta, "genre"),
            "rekordbox_top_tracks": _rb_top_tracks(rb_year_plays, rb_meta),
            "rekordbox_top_artists": _rb_top_grouped(rb_year_plays, "artist"),
            "rekordbox_top_genres": _rb_top_grouped(rb_year_plays, "genre"),
        }

    data = {
        "generated_at": time.time(),
        "generated_at_human": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "covers_through": {
            "newest_log_mtime": _newest_log_mtime(cfg),
            "max_event_ts": _max_event_ts(conn),
            "rekordbox_db_mtime": (rekordbox_mod.master_db_mtime() or 0.0)
                                  if cfg.get("rekordbox_playlist") else 0.0,
        },
        "available_years": sorted(out_years.keys(), reverse=True),
        "years": out_years,
    }
    _write_atomic(stats_path(cfg), data)
    return data


def read(cfg: dict | None = None) -> dict:
    cfg = cfg or cfgmod.load()
    return json.loads(stats_path(cfg).read_text(encoding="utf-8"))
