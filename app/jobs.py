"""
Der Scan-Lauf — einmal geschrieben, von Kommandozeile und Oberflaeche genutzt.

`run_scan` meldet Fortschritt ueber einen Callback und laesst sich abbrechen.
`ScanJob` haengt das an einen Hintergrund-Thread, damit der Server waehrend
des Laufs weiter antworten kann.
"""
from __future__ import annotations

import itertools
import os
import threading
import time
from concurrent.futures import (FIRST_COMPLETED, ProcessPoolExecutor,
                                ThreadPoolExecutor, as_completed, wait)
from concurrent.futures.process import BrokenProcessPool

from . import config as cfgmod
from . import db as db_mod
from . import scanner
from . import tags as tags_mod
from .analyzer import SPECTRAL_SKIPPED_REASON, analyse_file, blank_row

_BATCH = 200

# Schutz vor Massenloeschung beim Aufraeumen (siehe _safe_prune): fehlen mehr
# Dateien als das, wird nicht automatisch aufgeraeumt. Gleiche Idee wie
# playlistsync.GUARD_MIN/GUARD_SHARE.
_PRUNE_GUARD_MIN = 25
_PRUNE_GUARD_SHARE = 0.25


def _safe_prune(conn, cfg: dict, all_files: list, result: dict) -> None:
    """Raeumt Zeilen verschwundener Dateien ab -- aber nicht, wenn die Dateien
    wahrscheinlich nur gerade nicht erreichbar sind.

    Anlass: iter_files() ueberspringt einen fehlenden Bibliotheksordner still
    (externe Platte nicht eingehaengt, Ordnerzugriff entzogen). Ohne diesen
    Schutz galten dann alle Dateien darunter als geloescht, und prune_missing()
    entfernte ihre Zeilen samt allem, was Stunden an Messung gekostet hat
    (Genre/Stimmung, Phrasen/Beatgrid, Rekordbox-Snapshot).

    Zwei Regeln:
    - Liegt eine fehlende Datei unter einem Bibliotheksordner, der nicht da
      ist oder in dem der Scan keine einzige Datei gefunden hat, bleibt ihre
      Zeile stehen.
    - Fehlen ausserhalb davon mehr als max(_PRUNE_GUARD_MIN, 25 %) der
      bekannten Dateien wirklich auf der Platte, bleibt auch das stehen.
    Stehengebliebene Zeilen zeigt die Oberflaeche als "Datei fehlt"; dort
    raeumt der Knopf der Hinweisleiste (/api/prune) sie ausdruecklich ab.
    Zeilen, deren Datei noch existiert, aber nicht mehr zum Scan gehoert
    (Ordner aus den Einstellungen genommen), fallen wie bisher heraus.
    """
    existing = {p for p, _, _ in all_files}
    known = [r["path"] for r in conn.execute("SELECT path FROM files")]
    gone = [p for p in known if p not in existing]
    result["prune_kept"] = 0
    if not gone:
        return

    dead: list[str] = []
    for root in cfg.get("library_paths") or []:
        base = os.path.expanduser(str(root))
        prefix = base.rstrip(os.sep) + os.sep
        if not os.path.isdir(base) or not any(p.startswith(prefix) for p in existing):
            dead.append(prefix)
    keep = {p for p in gone if any(p.startswith(d) for d in dead)}

    missing = [p for p in gone if p not in keep and not os.path.exists(p)]
    if len(missing) > max(_PRUNE_GUARD_MIN, _PRUNE_GUARD_SHARE * len(known)):
        keep.update(missing)

    result["removed"] = len(db_mod.prune_missing(conn, existing | keep))
    result["prune_kept"] = len(keep)


def run_scan(cfg: dict, paths: list[str] | None = None, force: bool = False,
             limit: int = 0, prune: bool = False,
             on_progress=None, should_cancel=None, cover_scan: bool = False,
             skip_spectral: bool = False, skip_loudness: bool = False,
             recheck_tag_issues: bool = False,
             genremood: bool = False) -> dict:
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

    genremood=True haengt als letzten Schritt die Genre-/Stimmungs-Analyse
    fuer alle Dateien ohne Ergebnis an (siehe _analyse_genremood()).
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
        # Nachholen: unveraenderte Dateien, denen eine Messung fehlt, weil sie
        # in einem frueheren Scan abgewaehlt war (Cutoff) bzw. fehlt (Lautheit),
        # werden mitgemessen, sobald dieser Scan die Messung macht -- sonst
        # blieben sie fuer immer "unbekannt" bzw. ohne LUFS, der Cache-
        # Schluessel (Groesse, mtime) aendert sich ja nicht. Eine vorhandene
        # Messung wird dabei nie verworfen: was die Zeile schon hat, wird auch
        # bei abgewaehltem Schalter mitgemessen (analyse_file() ersetzt die
        # ganze Zeile).
        gaps = {} if force else db_mod.measurement_gaps(
            conn, float(cfg["min_duration_s"]), SPECTRAL_SKIPPED_REASON)
        todo = []
        catchup = 0
        for p, s, m in all_files:
            if not db_mod.is_current(cache, p, s, m):
                todo.append((p, s, m, job_cfg))
                continue
            gap = gaps.get(p)
            if not gap:
                continue
            cut_missing, loud_missing = gap
            if not ((cut_missing and not skip_spectral) or (loud_missing and not skip_loudness)):
                continue
            todo.append((p, s, m, {**cfg, "_skip_spectral": skip_spectral and cut_missing,
                                   "_skip_loudness": skip_loudness and loud_missing}))
            catchup += 1
        if limit:
            todo = todo[:limit]

        total = len(todo)
        result = {"found": len(all_files), "todo": total, "done": 0,
                  "cancelled": False, "removed": 0, "renamed": len(renamed),
                  "catchup": catchup}
        report(0, total, "analysieren")
        if not total:
            if prune and not limit and not paths:
                _safe_prune(conn, cfg, all_files, result)
            if not paths and not limit:
                _backfill_audio_hashes(conn, result, report, cancelled)
            _backfill_keys(conn, result)
            _fill_covers(conn, cfg, result)
            _backfill_cover_hashes(conn, result)
            _backfill_compilations(conn)
            _recheck_tag_issues(conn, cfg, result, enabled=recheck_tag_issues)
            _analyse_genremood(conn, cfg, result, genremood, report, cancelled)
            return result

        batch: list[dict] = []
        done = 0
        replaced = 0
        # Bewusst as_completed statt map: der Fortschritt soll mit jedem
        # fertigen Track weiterzaehlen und nicht blockweise springen, und ein
        # Abbruch soll sofort greifen statt erst am Ende eines Blocks.
        # Auftrag je Future merken: scheitert ein Worker, gehoert der Fehler
        # an den Pfad der Datei. Frueher landete dafuer eine Zeile mit dem
        # Pfad "?" in der Datenbank.
        broken = False
        with ProcessPoolExecutor(max_workers=cfgmod.worker_count()) as pool:
            futures = {pool.submit(analyse_file, job): job for job in todo}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    batch.append(future.result())
                except BrokenProcessPool:
                    # Ein Worker-Prozess ist gestorben (Speichermangel,
                    # Absturz in einer nativen Bibliothek). Der Pool ist damit
                    # unbrauchbar, ALLE offenen Auftraege scheitern sofort --
                    # sie als Fehlerzeilen zu speichern hiesse, die halbe
                    # Bibliothek als kaputt zu melden.
                    broken = True
                    break
                except Exception as exc:            # noqa: BLE001
                    row = blank_row(job[0], job[1], job[2])
                    row["error"] = f"Worker: {exc}"
                    row["reasons"] = [row["error"]]
                    batch.append(row)
                done += 1
                if len(batch) >= _BATCH:
                    replaced += len(db_mod.save(conn, batch, drop_replaced=True))
                    batch = []
                report(done, total, "analysieren")
                if cancelled():
                    result["cancelled"] = True
                    break
            if result["cancelled"]:
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
        replaced += len(db_mod.save(conn, batch, drop_replaced=True))
        result["done"] = done
        # Dateien, unter deren Pfad jetzt eine andere Aufnahme liegt: ihre
        # Messungen sind verworfen (db.drop_replaced_measurements()).
        result["replaced"] = replaced
        if broken:
            raise RuntimeError(
                "Ein Analyse-Prozess wurde unerwartet beendet (z. B. durch "
                f"Speichermangel). {done} von {total} Dateien sind gespeichert, "
                "ein erneuter Scan macht beim Rest weiter.")

        if prune and not limit and not paths and not result["cancelled"]:
            _safe_prune(conn, cfg, all_files, result)
        # Nur beim vollen Scan: eine Neu-Analyse einzelner Tracks (paths) soll
        # nicht bis zu 45 s auf das Nachtragen fuer den Rest warten.
        if not result["cancelled"] and not paths and not limit:
            _backfill_audio_hashes(conn, result, report, cancelled)
        _backfill_keys(conn, result)
        _fill_covers(conn, cfg, result)
        _backfill_cover_hashes(conn, result)
        _backfill_compilations(conn)
        _recheck_tag_issues(conn, cfg, result, enabled=recheck_tag_issues)
        if not result["cancelled"]:
            _analyse_genremood(conn, cfg, result, genremood, report, cancelled)
        return result
    finally:
        conn.close()


# Zeitbudget je Scan fuer das Nachtragen der Audio-Kennung. Rund 0,1 s je
# Datei bei vier parallelen ffmpeg-Laeufen: eine grosse, vor diesem Feature
# gescannte Bibliothek ist nach wenigen Scans vollstaendig, ohne dass einer
# davon spuerbar laenger dauert.
_AUDIO_HASH_BUDGET_S = 45.0


def _backfill_audio_hashes(conn, result: dict, report, cancelled) -> None:
    """Audio-Kennung fuer Zeilen nachtragen, die vor diesem Feature gescannt
    wurden (audio_hash IS NULL) -- erst mit einer gemerkten Kennung faellt
    spaeter auf, dass die Datei ersetzt wurde (db.drop_replaced_measurements()).
    Liest nur (ffmpeg ohne Dekodieren), schreibt nie in Dateien. Hoert nach
    _AUDIO_HASH_BUDGET_S auf, der naechste Scan macht weiter. Best effort wie
    _backfill_keys(): ein Fehler darf den Scan nicht kippen."""
    from . import media
    result["audio_hashes_filled"] = 0
    try:
        todo = db_mod.paths_without_audio_hash(conn)
        if not todo:
            return
        deadline = time.monotonic() + _AUDIO_HASH_BUDGET_S
        done: dict[str, str] = {}
        pending = iter(todo)
        with ThreadPoolExecutor(max_workers=4) as pool:
            running = {pool.submit(media.audio_fingerprint, p): p
                       for p in itertools.islice(pending, 8)}
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    path = running.pop(fut)
                    try:
                        done[path] = fut.result()
                    except Exception:                      # noqa: BLE001
                        done[path] = ""
                report(len(done), len(todo), "Audio-Kennung")
                if cancelled() or time.monotonic() > deadline:
                    continue                # laufende noch abholen, nichts Neues
                for p in itertools.islice(pending, len(finished)):
                    running[pool.submit(media.audio_fingerprint, p)] = p
        db_mod.set_audio_hashes(conn, done)
        result["audio_hashes_filled"] = sum(1 for h in done.values() if h)
    except Exception:                                      # noqa: BLE001
        return


def _backfill_keys(conn, result: dict) -> None:
    """Tonart fuer Zeilen nachtragen, die vor diesem Feature gescannt wurden
    (key_raw IS NULL) -- reiner Tag-Read (mutagen, ~1 ms je Datei), kein
    --force-Rescan mit voller Spektralanalyse noetig. Eine Datei ohne Tonart
    bekommt '' statt NULL und faellt damit beim naechsten Lauf heraus. Best
    effort wie _fill_covers(): ein Fehler darf den Scan nicht kippen."""
    result["keys_filled"] = 0
    try:
        keys = {}
        for p in db_mod.paths_without_key(conn):
            raw = tags_mod.read_key(p).strip()
            keys[p] = (tags_mod.normalize_key(raw), raw)
        db_mod.set_keys(conn, keys)
    except Exception:                                      # noqa: BLE001
        return
    result["keys_filled"] = sum(1 for k, _ in keys.values() if k)


def _backfill_cover_hashes(conn, result: dict) -> None:
    """Bild-Hash des Covers fuer Zeilen nachtragen, die vor diesem Feature
    gescannt wurden (cover_hash IS NULL) -- reiner Tag-Read wie
    _backfill_keys(), kein --force noetig. Best effort."""
    result["cover_hashes_filled"] = 0
    try:
        hashes = {p: tags_mod.read_cover_hash(p) for p in db_mod.paths_without_cover_hash(conn)}
        db_mod.set_cover_hashes(conn, hashes)
    except Exception:                                      # noqa: BLE001
        return
    result["cover_hashes_filled"] = sum(1 for h in hashes.values() if h)


def _backfill_compilations(conn) -> None:
    """Sampler-Markierung fuer Zeilen nachtragen, die vor diesem Feature
    gescannt wurden (compilation IS NULL) -- reiner Tag-Read, kein --force."""
    try:
        flags = {p: tags_mod.read_compilation(p)
                 for p in db_mod.paths_without_compilation(conn)}
        db_mod.set_compilations(conn, flags)
    except Exception:                                      # noqa: BLE001
        return


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


def _analyse_genremood(conn, cfg: dict, result: dict, enabled: bool,
                       report, cancelled) -> None:
    """Genre, Stimmung, Energie sowie BPM und Tonart fuer alle erfolgreich
    gescannten Dateien nachtragen, denen etwas davon fehlt -- nur auf
    ausdruecklichen Wunsch (Schalter im Scan-Dialog, 'genremood' im
    /api/scan-Aufruf), weil es rund eine Sekunde je Datei kostet.

    - Genre/Stimmung/Energie: Dateien ohne Ergebnis oder mit aelterem
      Analysestand (Tabelle 'genremood', genremood.ANALYSIS_VERSION).
    - BPM/Tonart: nur Dateien, bei denen das jeweilige Feld LEER ist ("ergaenzen",
      nie ueberschreiben) und nur, wenn die Einstellung bpmkey_write_bpm bzw.
      bpmkey_write_key es erlaubt -- sonst haette ein Messwert keinen Ort.
    Ob etwas in die DATEI geschrieben wird, entscheiden allein die
    Einstellungen (genremood_write_*, bpmkey_write_*, bpmkey_comment_*), genau
    wie bei den Aktionen in der Tabelle (plan_fields()). Das Ergebnis der
    Genre-/Stimmungs-Analyse geht immer in die Datenbank.

    Ein Abbruch behaelt alles bis dahin Gemessene; der naechste Lauf macht
    beim Rest weiter. Zwei Threads: waehrend einer rechnet (beide Analysen
    halten dafuer ihren eigenen Lock), dekodiert der andere schon die naechste
    Datei. Einzelfehler (kaputte Datei) zaehlen nur mit, wie bei
    _fill_covers() darf hier nichts den Scan kippen.
    """
    result["genremood_done"] = 0
    result["genremood_failed"] = 0
    result["bpmkey_done"] = 0
    result["bpmkey_failed"] = 0
    result["tags_written"] = 0
    if not enabled:
        return
    from . import audit_log, bpmkey, genremood, taganomaly
    gm_ok = genremood.available() and bool(
        cfg.get("measure_genre", True) or cfg.get("measure_mood", True))
    bk_ok = bpmkey.available() and bool(
        cfg.get("measure_bpm", True) or cfg.get("measure_key", True))
    if not (gm_ok or bk_ok):
        return
    # Gemeinsames Laengenlimit beider Analysen.
    max_seconds = float(cfg.get("bpmkey_max_minutes") or 10) * 60
    gm_todo = {p for p in db_mod.paths_needing_genremood(
        conn, max_seconds, genremood.ANALYSIS_VERSION)} if gm_ok else set()
    # Nur ergaenzen: "if_empty" statt der eingestellten Ueberschreib-Variante.
    bk_cfg = dict(cfg)
    need_bpm = need_key = False
    if bk_ok:
        for field, flag in (("bpmkey_write_bpm", "bpm"), ("bpmkey_write_key", "key")):
            mode = bpmkey._mode(cfg.get(field, "yes"))
            if not cfg.get("measure_" + flag, True):
                mode = "no"
            bk_cfg[field] = "no" if mode == "no" else "if_empty"
            if mode != "no":
                if flag == "bpm":
                    need_bpm = True
                else:
                    need_key = True
    bk_todo = set(db_mod.paths_needing_bpmkey(
        conn, max_seconds, need_bpm, need_key)) if (need_bpm or need_key) else set()
    todo = [p for p in sorted(gm_todo | bk_todo) if os.path.isfile(p)]
    total = len(todo)
    label = "genre, stimmung, bpm & tonart"
    report(0, total, label)

    def measure(path):
        gm = bk = None
        gm_err = bk_err = False
        if path in gm_todo:
            try:
                gm = genremood.restrict(genremood.analyse(path, max_seconds), cfg)
            except Exception:                              # noqa: BLE001
                gm_err = True
        if path in bk_todo:
            try:
                bk = bpmkey.analyse(path, max_seconds,
                                    cfg.get("measure_bpm", True),
                                    cfg.get("measure_key", True))
            except bpmkey.TooLongError:
                pass
            except Exception:                              # noqa: BLE001
                bk_err = True
        return path, gm, bk, gm_err, bk_err

    def apply(path, gm, bk):
        """Ergebnis eintragen und, falls die Einstellungen es vorsehen, in die
        Datei schreiben. Fehler beim Schreiben zaehlen nicht als Messfehler."""
        if gm is not None:
            db_mod.save_genremood(conn, path, gm["genres"], gm["moods"],
                                  gm["energy"], gm["model"])
        if gm is None and bk is None:
            return
        try:
            existing = tags_mod.read_extra(path)
            fields: dict = {}
            if gm is not None:
                fields.update(genremood.plan_fields(
                    cfg, gm, existing, tags_mod.read_mood(path)))
            if bk is not None:
                energy = gm["energy"] if gm is not None \
                    else db_mod.genremood_energy(conn, path)
                # Der Kommentar der BPM-/Tonart-Planung enthaelt die Energie
                # bereits und ersetzt den der Genre-Planung.
                fields.update(bpmkey.plan_fields(bk_cfg, bk, existing, energy))
            if not fields:
                return
            tags_mod.write_tags(path, fields)
        except Exception:                                  # noqa: BLE001
            return
        audit_log.log("scan-analyse", path, ", ".join(sorted(fields)))
        db_fields = dict(fields)
        if "key" in fields and bk is not None:
            db_fields["key"] = bk["camelot"]
            db_fields["key_raw"] = fields["key"]
        row = conn.execute("SELECT codec_family FROM files WHERE path = ?",
                           (path,)).fetchone()
        db_mod.update_tags(conn, path, db_fields)
        db_mod.update_tag_issues(conn, path, taganomaly.detect(
            path, (row["codec_family"] if row else "") or ""))
        db_mod.refresh_stat(conn, path)
        result["tags_written"] += 1

    done = 0
    pending = iter(todo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = {pool.submit(measure, p) for p in itertools.islice(pending, 2)}
        while running:
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                path, gm, bk, gm_err, bk_err = future.result()
                done += 1
                if gm is not None:
                    result["genremood_done"] += 1
                if bk is not None:
                    result["bpmkey_done"] += 1
                result["genremood_failed"] += int(gm_err)
                result["bpmkey_failed"] += int(bk_err)
                apply(path, gm, bk)
                report(done, total, label)
            if cancelled():
                result["cancelled"] = True
                for future in running:
                    future.cancel()
                break
            for p in itertools.islice(pending, len(finished)):
                running.add(pool.submit(measure, p))


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
