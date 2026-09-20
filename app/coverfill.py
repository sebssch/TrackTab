"""
Automatisches Cover-Nachtragen aus Music.app -- beim Bibliothek-Scan
angestossen (app/jobs.py::run_scan()) sowie beim manuellen "neu
analysieren"-Knopf (server._post_reanalyse()/_post_analyse_path()), NICHT
beim erstmaligen Import ueber die Einzelpruefung (Drag&Drop/Dateiauswahl --
das soll schnell bleiben). Liest ausschliesslich das in Music.app
hinterlegte Artwork (media.track_artwork()) -- kein Umweg ueber eine
Online-Suche.

Drei Faelle:
  1. fill_missing_covers() -- neue Dateien ohne Cover bekommen eins, wenn
     Music.app eins fuer sie kennt.
  2. refresh_cached_covers() -- Dateien, deren Cover wir nur im eigenen
     DB-Cache halten (siehe unten), werden bei jedem Scan erneut mit
     Music.app abgeglichen. Das ist der einzige Weg, eine dortige
     Cover-Aenderung ueberhaupt zu bemerken: der dateibasierte Scan
     (Cache-Schluessel Pfad/Groesse/mtime) sieht nichts, wenn Music.app die
     Datei selbst gar nicht anfasst (WAV/AIFF).
  3. fill_cover_for_untracked() -- dasselbe fuer eine Einzelpruefungs-Zeile
     OHNE DB-Zeile (Drop), deshalb ohne jeden DB-Zugriff und ohne
     DB-Cache-Fallback.

Die ersten beiden schreiben ein gefundenes Cover bevorzugt in die Datei selbst
(tags.write_cover()) -- gelingt das nicht (TagError, z.B. nicht
unterstuetztes Format), landet es stattdessen im DB-Cache (db.cache_cover),
von dort liefert es server.py::_get_cover() weiterhin aus.
"""
from __future__ import annotations

from . import audit_log
from . import db as db_mod
from . import media
from . import tags as tags_mod


def _store(conn, path: str, data: bytes, mime: str, reason: str) -> str:
    """Schreibt ein gefundenes Cover in die Datei, bei Fehlschlag in den
    DB-Cache. Liefert 'written' oder 'cached'."""
    try:
        tags_mod.write_cover(path, data, mime)
    except tags_mod.TagError:
        db_mod.cache_cover(conn, path, mime, data)
        db_mod.set_has_cover(conn, path, True)
        audit_log.log("cover", path, f"automatisch ({reason}, Music.app, nur DB-Cache)")
        return "cached"
    db_mod.set_has_cover(conn, path, True)
    db_mod.refresh_stat(conn, path)
    db_mod.delete_cached_cover(conn, path)
    audit_log.log("cover", path, f"automatisch ({reason}, Music.app)")
    return "written"


def fill_missing_covers(conn, cfg: dict, candidate_paths: list[str], reason: str) -> dict:
    """Fuer die Teilmenge von candidate_paths ohne Cover (has_cover=0, mit
    Titel): passendes Music.app-Artwork lesen und uebernehmen. Ein
    Fehlschlag an einer Datei darf die uebrigen nicht abbrechen.
    Liefert {"checked", "written", "cached", "written_paths"}."""
    result = {"checked": 0, "written": 0, "cached": 0, "written_paths": []}
    # Music App nicht in den Einstellungen ausgewaehlt -- die gesamte
    # Integration ist dann bewusst aus, kein AppleScript-Zugriff auf
    # Music.app (siehe config.py:external_music).
    if not cfg.get("external_music"):
        return result
    wanted = set(candidate_paths)
    if not wanted:
        return result
    items = [(p, t) for p, t in db_mod.missing_cover_rows(conn) if p in wanted and t]
    if not items:
        return result
    result["checked"] = len(items)
    try:
        hits = media.track_artwork(items)
    except Exception:                                      # noqa: BLE001
        return result
    for path, hit in hits.items():
        if hit is None:
            continue
        data, mime = hit
        try:
            outcome = _store(conn, path, data, mime, reason)
        except Exception:                                  # noqa: BLE001
            continue
        result[outcome] += 1
        result["written_paths"].append(path)
    return result


def refresh_cached_covers(conn, cfg: dict, reason: str,
                          only_paths: list[str] | None = None) -> dict:
    """Fuer alle Pfade mit einem Eintrag in cover_cache (oder, falls
    only_paths gesetzt ist, nur fuer diese Teilmenge -- siehe
    _post_reanalyse(), das hier gezielt nur die gerade neu analysierten
    Pfade abgleicht statt den ganzen Cache): erneut mit Music.app
    abgleichen. Ein geaendertes Cover ueberschreibt den Cache-Eintrag (bzw.
    wird jetzt in die Datei geschrieben, falls das inzwischen moeglich ist),
    ein in Music.app entferntes Cover loescht den Cache-Eintrag wieder
    (has_cover faellt dann auf 0 zurueck). Liefert
    {"checked", "updated", "removed"}."""
    result = {"checked": 0, "updated": 0, "removed": 0}
    if not cfg.get("external_music"):
        return result
    wanted = set(only_paths) if only_paths is not None else None
    items = [(p, t) for p, t in db_mod.cached_cover_rows(conn)
             if t and (wanted is None or p in wanted)]
    if not items:
        return result
    result["checked"] = len(items)
    try:
        hits = media.track_artwork(items)
    except Exception:                                      # noqa: BLE001
        return result
    for path, hit in hits.items():
        try:
            if hit is None:
                db_mod.delete_cached_cover(conn, path)
                db_mod.set_has_cover(conn, path, False)
                audit_log.log("cover", path, f"entfernt ({reason}, in Music.app nicht mehr vorhanden)")
                result["removed"] += 1
                continue
            data, mime = hit
            _store(conn, path, data, mime, reason)
            result["updated"] += 1
        except Exception:                                  # noqa: BLE001
            continue
    return result


def fill_cover_for_untracked(path: str, title: str, has_cover: bool) -> bool:
    """Cover-Nachtrag fuer eine Einzelpruefungs-Zeile OHNE DB-Zeile (Drop) --
    Pendant zu fill_missing_covers() fuer die Haupttabelle, aber ohne jeden
    DB-Zugriff: eine Einzelpruefung hat keine files-Zeile (siehe
    server._post_analyse_path()), also auch keinen has_cover-Flag und
    keinen DB-Cache-Fallback wie _store() ihn fuer die Haupttabelle bietet.
    Schreibt ein gefundenes Cover NUR in die Datei selbst
    (tags.write_cover()); gelingt das nicht (TagError, z.B. WAV/AIFF),
    bleibt es schlicht aus.

    has_cover wird vom Aufrufer durchgereicht (aus derselben analyse_file()-
    Messung, die die Zeile ohnehin schon geliefert hat) statt hier per
    eigenem Tag-Read neu bestimmt zu werden. Liefert True nur, wenn
    tatsaechlich ein neues Cover in die Datei geschrieben wurde."""
    if has_cover or not title:
        return False
    try:
        hits = media.track_artwork([(path, title)])
    except Exception:                                      # noqa: BLE001
        return False
    hit = hits.get(path)
    if hit is None:
        return False
    data, mime = hit
    try:
        tags_mod.write_cover(path, data, mime)
    except tags_mod.TagError:
        return False
    audit_log.log("cover", path, "automatisch (Einzelpruefung, Music.app)")
    return True
