"""
Der Scan-Lauf — einmal geschrieben, von Kommandozeile und Oberflaeche genutzt.

`run_scan` meldet Fortschritt ueber einen Callback und laesst sich abbrechen.
`ScanJob` haengt das an einen Hintergrund-Thread, damit der Server waehrend
des Laufs weiter antworten kann.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import config as cfgmod
from . import db as db_mod
from . import scanner
from .analyzer import analyse_file

_BATCH = 200


def run_scan(cfg: dict, paths: list[str] | None = None, force: bool = False,
             limit: int = 0, prune: bool = False,
             on_progress=None, should_cancel=None, cover_scan: bool = False,
             skip_spectral: bool = False, skip_loudness: bool = False,
             force_cover_fill: bool = False,
             recheck_tag_issues: bool = False) -> dict:
    """
    Analysiert die Bibliothek inkrementell.

    on_progress(done, total, phase) wird waehrend des Laufs aufgerufen,
    should_cancel() bricht nach dem naechsten Ergebnis ab.

    cover_scan=True ueberspringt die eigentliche Audio-Analyse komplett --
    der normale Scan-Lauf unten prueft laengst JEDE bekannte Datei ohne
    Cover (nicht nur neu gefundene) gegen Music.app, cover_scan=True ist der
    schnelle Weg dahin, wenn nur der Cover-Abgleich interessiert und keine
    Audiodateien neu vermessen werden sollen (kein Dateisystem-Durchlauf,
    arbeitet rein aus der DB, siehe app/coverfill.py). Uebergeht dabei
    zusaetzlich die 'cover_auto_fill'-Einstellung, da explizit angefordert.
    """
    report = on_progress or (lambda *a: None)
    cancelled = should_cancel or (lambda: False)

    if cover_scan:
        report(0, 0, "cover")
        conn = db_mod.connect(cfg)
        try:
            result = {"found": 0, "todo": 0, "done": 0, "cancelled": False, "removed": 0}
            _fill_covers(conn, cfg, result, force=True)
            return result
        finally:
            conn.close()

    report(0, 0, "suchen")
    all_files = list(scanner.iter_files(cfg, paths))
    conn = db_mod.connect(cfg)
    try:
        # Vor dem Cache-Abgleich: reine Gross-/Kleinschreibungs- oder
        # Unicode-Umbenennungen (Music.app raeumt seinen Medienordner nach
        # einem Tag-Edit selbststaendig um) auf die bestehende Zeile
        # umschreiben -- sonst legt der folgende, exakte String-Vergleich
        # dafuer eine zweite Zeile an, waehrend die alte liegen bleibt und
        # die Gesamtzahl bis zum naechsten --prune verfaelscht.
        renamed = db_mod.reconcile_case_renames(conn, all_files)
        cache = {} if force else db_mod.cached_keys(conn)
        job_cfg = cfg
        if skip_spectral or skip_loudness:
            job_cfg = {**cfg, "_skip_spectral": skip_spectral,
                       "_skip_loudness": skip_loudness}
        todo = [(p, s, m, job_cfg) for p, s, m in all_files
                if not db_mod.is_current(cache, p, s, m)]
        if limit:
            todo = todo[:limit]

        total = len(todo)
        result = {"found": len(all_files), "todo": total, "done": 0,
                  "cancelled": False, "removed": 0, "renamed": len(renamed)}
        report(0, total, "analysieren")
        if not total:
            if prune and not limit and not paths:
                result["removed"] = len(db_mod.prune_missing(
                    conn, {p for p, _, _ in all_files}))
            _fill_covers(conn, cfg, result, force=force_cover_fill)
            _recheck_tag_issues(conn, cfg, result, enabled=recheck_tag_issues)
            return result

        batch: list[dict] = []
        done = 0
        # Bewusst as_completed statt map: der Fortschritt soll mit jedem
        # fertigen Track weiterzaehlen und nicht blockweise springen, und ein
        # Abbruch soll sofort greifen statt erst am Ende eines Blocks.
        with ProcessPoolExecutor(max_workers=cfgmod.worker_count()) as pool:
            futures = [pool.submit(analyse_file, job) for job in todo]
            for future in as_completed(futures):
                try:
                    batch.append(future.result())
                except Exception as exc:            # noqa: BLE001
                    batch.append({"path": "?", "status": "error",
                                  "error": f"Worker: {exc}"})
                done += 1
                if len(batch) >= _BATCH:
                    db_mod.save(conn, batch)
                    batch = []
                report(done, total, "analysieren")
                if cancelled():
                    result["cancelled"] = True
                    break
            if result["cancelled"]:
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
        db_mod.save(conn, batch)
        result["done"] = done

        if prune and not limit and not paths and not result["cancelled"]:
            result["removed"] = len(db_mod.prune_missing(
                conn, {p for p, _, _ in all_files}))
        _fill_covers(conn, cfg, result, force=force_cover_fill)
        _recheck_tag_issues(conn, cfg, result, enabled=recheck_tag_issues)
        return result
    finally:
        conn.close()


def _fill_covers(conn, cfg: dict, result: dict, force: bool = False) -> None:
    """Cover aus Music.app nachtragen -- fuer JEDE bekannte Datei ohne Cover
    (nicht nur neu gefundene: hat_cover=0 bleibt sonst dauerhaft haengen,
    wenn eine Bibliothek schon vor diesem Feature gescannt wurde oder das
    Cover erst nachtraeglich in Music.app auftaucht) und fuer bereits
    bekannte Dateien, deren Cover nur im DB-Cache liegt (siehe coverfill.py,
    warum das noetig ist). Ein wiederholter Blindgaenger (Datei ohne
    Music.app-Gegenstueck) kostet dabei nur eine erneute, schnelle
    Namens-Anfrage -- keine unbegrenzt wachsende Liste, denn ein Treffer
    setzt has_cover sofort auf 1 und faellt danach aus den Kandidaten heraus.
    Best effort: ein Fehler hier darf den bereits abgeschlossenen Scan nicht
    als fehlgeschlagen melden. force=True uebergeht die
    'cover_auto_fill'-Einstellung -- fuer einen explizit angeforderten Lauf
    (scan --covers), die Einstellung steuert nur den automatischen Trigger
    beim normalen Scan.
    """
    result["covers_filled"] = 0
    result["covers_updated"] = 0
    if not force and not cfg.get("cover_auto_fill", True):
        return
    from . import coverfill
    try:
        candidates = [p for p, _ in db_mod.missing_cover_rows(conn)]
        filled = coverfill.fill_missing_covers(
            conn, cfg, candidates, "Scan") if candidates else \
            {"written": 0, "cached": 0}
        updated = coverfill.refresh_cached_covers(conn, cfg, "Scan")
    except Exception:                                      # noqa: BLE001
        return
    result["covers_filled"] = filled["written"] + filled["cached"]
    result["covers_updated"] = updated["updated"] + updated["removed"]


def _recheck_tag_issues(conn, cfg: dict, result: dict, enabled: bool) -> None:
    """Auffaelligkeiten (Tag-Qualitaet, app/taganomaly.py) fuer ALLE bereits
    bekannten Dateien neu bewerten, nicht nur die in diesem Lauf neu
    analysierten -- derselbe Grund wie bei _fill_covers() oben: eine
    Verbesserung am Detector soll auch fuer laengst gescannte, unveraenderte
    Dateien greifen, ohne dafuer einen teuren --force-Rescan (volle
    Spektralanalyse) zu erzwingen. Reiner Tag-Read (mutagen), kein
    ffprobe/ffmpeg, siehe taganomaly.recheck_all(). Best effort wie
    _fill_covers(): ein Fehler hier darf den bereits abgeschlossenen Scan
    nicht als fehlgeschlagen melden. Nur auf expliziten Wunsch (Schalter
    "Auffaelligkeiten neu pruefen" im Scan-Dialog bzw. 'recheck_tags' im
    /api/scan-Aufruf) -- anders als bei Covern gibt es hier keine impliziten
    Vorgabe-Einstellung, die dafuer spricht, es standardmaessig mitlaufen zu
    lassen.
    """
    result["tag_issues_rechecked"] = 0
    if not enabled:
        return
    from . import taganomaly as taganomaly_mod
    try:
        stats = taganomaly_mod.recheck_all(conn, cfg)
    except Exception:                                      # noqa: BLE001
        return
    result["tag_issues_rechecked"] = stats["total"]


class ScanJob:
    """Ein laufender Scan im Hintergrund, abfragbar ueber den Server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = False
        self.state: dict = {"running": False, "done": 0, "total": 0,
                            "phase": "bereit", "started": 0.0,
                            "finished": None, "error": ""}

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, cfg: dict, **kwargs) -> bool:
        with self._lock:
            if self.is_running():
                return False
            self._cancel = False
            self.state = {"running": True, "done": 0, "total": 0,
                          "phase": "suchen", "started": time.time(),
                          "finished": None, "error": ""}
            self._thread = threading.Thread(
                target=self._run, args=(cfg,), kwargs=kwargs, daemon=True)
            self._thread.start()
            return True

    def _run(self, cfg: dict, **kwargs) -> None:
        def progress(done, total, phase):
            self.state["done"] = done
            self.state["total"] = total
            self.state["phase"] = phase

        try:
            result = run_scan(cfg, on_progress=progress,
                              should_cancel=lambda: self._cancel, **kwargs)
            self.state["finished"] = result
            self.state["phase"] = "abgebrochen" if result["cancelled"] else "fertig"
        except Exception as exc:                     # noqa: BLE001
            self.state["error"] = str(exc)
            self.state["phase"] = "fehler"
        finally:
            self.state["running"] = False

    def cancel(self) -> None:
        self._cancel = True
        self.state["phase"] = "wird abgebrochen"
