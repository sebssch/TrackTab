"""
Trainingsdatensatz: je Track die Takt-Merkmale plus Hand-Cues als eine .npz-Datei.

Nachbau von tools/export_bar_dataset.py des Autocue-Projekts, aber auf TrackTabs
Stand statt auf Rekordbox' ANLZ-Dateien:

  Grid, Phrasen  gridstore.own_inputs() -- Bearbeitung, sonst eigene Zeile in
                 'phrases'. Wer keine hat, bekommt vorher Rekordbox' Stand als
                 eigenen (gridstore.seed_from_rekordbox(), schreibt in die DB der
                 Instanz). Genau das sieht Auto Cue spaeter zur Laufzeit.
  Merkmale       dieselbe Kette wie autocue.analyse(): lattice -> sources ->
                 features, Phrasen immer als Hinweis (wie im Original).
  Labels         labels.py (Hand-Cues ohne Auto-Cue-Eigenes, eigene Zeitachse);
                 Hand-Cues im Ausklang am Trackende (tail.music_end(), dieselbe
                 Grenze wie zur Laufzeit) zaehlen nicht.

Ablage <base_dir>/autocue-training/dataset/<sha1(pfad)>.npz, Format wie im Original
(Schluessel matrix, names, bar_start_beats, ev_*, hot_*, mem_*), dazu 'path' und
'key'. key fasst alles zusammen, wovon das Ergebnis abhaengt (Datei, Grid, Phrasen,
Labels, Versionen) -- ein erneuter Lauf rechnet nur Geaendertes. Nie in git (liegt
im Datenordner der Instanz, in der Entwicklung per .gitignore).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

DATASET_VERSION = 3          # 1 = Autocue-Projekt (Rekordbox-ANLZ, librosa), 3 = ohne Ausklang
# Laengere Tracks fallen heraus (Spektrogramm ~60 MB je 10 Minuten und Worker).
MAX_MINUTES = 20


def _init_worker(base_dir: str) -> None:
    """Jeder Worker arbeitet mit den Daten derselben Instanz wie der Hauptprozess."""
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    from app import config as cfgmod
    cfgmod.use_base_dir(base_dir)


def file_key(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------
# Kandidaten
# ----------------------------------------------------------------------------

def candidates(conn) -> list[dict]:
    """Tracks mit Hot A und Hot C (von Hand), noch ohne Versatz-Korrektur."""
    from app.autocue.training.labels import has_anchor_cues
    out = []
    rows = conn.execute(
        "SELECT f.path, f.duration_s, f.size, f.mtime, d.cues AS edit_cues, s.cues AS snap_cues "
        "FROM files f LEFT JOIN dj_track d ON d.path = f.path "
        "LEFT JOIN rb_snapshot s ON s.path = f.path "
        "WHERE d.cues IS NOT NULL OR s.cues IS NOT NULL").fetchall()
    for r in rows:
        try:
            edits = json.loads(r["edit_cues"]) if r["edit_cues"] is not None else None
            snap = json.loads(r["snap_cues"]) if r["snap_cues"] is not None else None
        except (TypeError, ValueError):
            continue
        if not has_anchor_cues(edits if edits is not None else snap):
            continue
        out.append({"path": r["path"], "duration": float(r["duration_s"] or 0),
                    "size": r["size"], "mtime": r["mtime"], "edit_cues": edits, "snap_cues": snap})
    return out


# ----------------------------------------------------------------------------
# Vorbereitung: Versatz messen, Grid aus Rekordbox uebernehmen
# ----------------------------------------------------------------------------

def _prepare_one(task: dict) -> tuple[str, float | None, list, str]:
    """(Pfad, Versatz oder None, uebernommene Felder, Fehler). Top-Level fuer den Pool."""
    from app import config as cfgmod
    from app import gridstore
    from app import wavedetail as wavedetail_mod
    path = task["path"]
    try:
        cfg = cfgmod.load()
        lead = gridstore.rekordbox_lead(path) if task["need_lead"] else None
        seeded = gridstore.seed_from_rekordbox(cfg, path) if task["need_seed"] else []
        return path, lead, seeded, ""
    except Exception as exc:                       # noqa: BLE001
        return path, None, [], f"{type(exc).__name__}: {str(exc)[:80]}"
    finally:
        wavedetail_mod.forget(path)


def prepare(cfg: dict, items: list[dict], work: str, workers: int, progress=None) -> dict:
    """Versatz je Track (zwischengespeichert in leads.json) und Uebernahme fehlender
    Grids. Rueckgabe {Pfad: Versatz oder None}."""
    from app import config as cfgmod
    from app import db as db_mod
    from app import gridstore
    cache_path = os.path.join(work, "leads.json")
    try:
        with open(cache_path) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    conn = db_mod.connect(cfg)
    try:
        tasks, leads = [], {}
        for it in items:
            path = it["path"]
            if not os.path.exists(path):
                continue
            mtime = os.path.getmtime(path)
            hit = cache.get(path)
            need_lead = bool(it["snap_cues"]) and it["edit_cues"] is None and not (hit and hit[0] == mtime)
            if hit and hit[0] == mtime:
                leads[path] = hit[1]
            own = gridstore.own_inputs(conn, path)
            need_seed = not own["beats"] or not own["phrases"]
            if need_lead or need_seed:
                tasks.append({"path": path, "need_lead": need_lead or need_seed, "need_seed": need_seed,
                              "mtime": mtime})
            elif path not in leads:
                leads[path] = None
    finally:
        conn.close()
    mtimes = {t["path"]: t["mtime"] for t in tasks}
    if tasks:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(str(cfgmod.base_dir()),)) as ex:
            futures = [ex.submit(_prepare_one, t) for t in tasks]
            for i, f in enumerate(as_completed(futures), 1):
                path, lead, _seeded, _err = f.result()
                if lead is not None:
                    leads[path] = lead
                    cache[path] = [mtimes[path], lead]
                else:
                    leads.setdefault(path, None)
                if progress:
                    progress(i, len(tasks))
                if i % 200 == 0:
                    _write_json(cache_path, cache)
        _write_json(cache_path, cache)
    return leads


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------

def build_tasks(cfg: dict, items: list[dict], leads: dict, out_dir: str, force: bool) -> tuple[list, dict]:
    """Aufgaben fuer den Export und eine Zaehlung, was warum wegfiel."""
    from app import db as db_mod
    from app import gridstore
    from app.autocue.learned import FEATURE_VERSION
    from app.autocue.training import labels as labels_mod
    counts = {"kein_grid": 0, "keine_labels": 0, "aktuell": 0, "zu_rechnen": 0}
    tasks = []
    conn = db_mod.connect(cfg)
    try:
        auto = db_mod.autocue_log_map(conn)
        for it in items:
            path = it["path"]
            if path not in leads:
                continue
            own = gridstore.own_inputs(conn, path)
            if not own["beats"]:
                counts["kein_grid"] += 1
                continue
            cues, _src = labels_mod.own_cues(it["edit_cues"], it["snap_cues"], leads.get(path))
            beat_s = labels_mod.beat_seconds(own["beats"])
            lab = labels_mod.hand_cues(cues, auto.get(path), beat_s)
            if 1 not in lab["hot"] or 3 not in lab["hot"]:
                counts["keine_labels"] += 1
                continue
            grid = gridstore.beats_with_tempo(own["beats"], own["segments"] or [], 0.0, own["bpm"])
            edited = own["phrases_origin"] == "edited"
            key = gridstore.dj_hash([DATASET_VERSION, FEATURE_VERSION, it["size"], it["mtime"],
                                     grid, own["phrases"], edited, lab, it["duration"]])
            out_path = os.path.join(out_dir, f"{file_key(path)}.npz")
            if not force and _stored_key(out_path) == key:
                counts["aktuell"] += 1
                continue
            counts["zu_rechnen"] += 1
            tasks.append({"path": path, "grid": grid, "phrases": own["phrases"], "phrases_edited": edited,
                          "duration": it["duration"],
                          "hot": lab["hot"], "memory": lab["memory"], "key": key, "out_path": out_path,
                          "max_seconds": MAX_MINUTES * 60.0})
    finally:
        conn.close()
    return tasks, counts


def _stored_key(path: str) -> str | None:
    try:
        with np.load(path) as z:
            return str(z["key"]) if "key" in z.files else None
    except (OSError, ValueError, KeyError):
        return None


def _export_one(task: dict) -> tuple[str, str, str]:
    """Einen Track rechnen und schreiben: (Pfad, ok|skip|fail, Meldung)."""
    from app.autocue.evidence import collect_evidence
    from app.autocue.features import extract_bar_features
    from app.autocue.lattice import BEATGRID_STRICTNESS, build_lattice, grid_from_beats
    from app.autocue.sources import TooLongError, load_sources
    from app.autocue.tail import music_end

    path = task["path"]
    try:
        grid = grid_from_beats(task["grid"])
        lattice = build_lattice(grid, task["duration"] or None, BEATGRID_STRICTNESS, enforce=True)
        if lattice is None:
            return path, "skip", "Beatgrid faellt durch"
        try:
            sources = load_sources(path, task["max_seconds"])
        except TooLongError:
            return path, "skip", "zu lang"
        features = extract_bar_features(lattice, sources)
        del sources
        evidence = collect_evidence(lattice, task["phrases"], use_phrases=True)

        n = lattice.n_bars
        # Hand-Cues im Ausklang sind kein Lernbeispiel: dieselbe Grenze wie zur Laufzeit.
        end_bar, _ = (music_end(features["rms"], lattice.bar_times, task["phrases"], task["phrases_edited"])
                      if features.has("rms") else (None, ""))
        limit = lattice.time_of_bar(end_bar) - 0.05 if end_bar is not None else float("inf")
        hot = {int(k): t for k, t in task["hot"].items() if t < limit}
        memory = [t for t in task["memory"] if t < limit]
        if 1 not in hot or 3 not in hot:
            return path, "skip", "Hot A/C im Ausklang"
        hot_kinds = np.asarray(sorted(hot), dtype=np.int16)
        hot_times = np.asarray([hot[int(k)] for k in hot_kinds], dtype=np.float64)
        hot_bars = np.asarray([lattice.nearest_bar(t) for t in hot_times], dtype=np.int32)
        mem_times = np.asarray(memory, dtype=np.float64)
        mem_bars = np.asarray([lattice.nearest_bar(t) for t in mem_times], dtype=np.int32)
        hot_snap_beats = np.asarray(
            [abs(t - lattice.time_of_bar(b)) / lattice.beat_duration for t, b in zip(hot_times, hot_bars)],
            dtype=np.float32)

        def curve(name: str) -> np.ndarray:
            arr = evidence.by_source.get(name)
            return np.asarray(arr, dtype=np.float32) if arr is not None else np.zeros(n, np.float32)

        def hint(arr) -> np.ndarray:
            return np.asarray(arr, dtype=np.float32) if arr is not None else np.zeros(n, np.float32)

        tmp = task["out_path"] + ".tmp.npz"
        np.savez_compressed(
            tmp, version=DATASET_VERSION, key=task["key"], path=path,
            matrix=features.matrix.astype(np.float32), names=json.dumps(features.names),
            bar_times=lattice.bar_times.astype(np.float64),
            bar_start_beats=lattice.bar_start_beats.astype(np.int32),
            bpm=float(lattice.bpm), n_bars=n,
            ev_phrases=curve("phrases"), ev_tempo_change=curve("tempo_change"),
            ev_drop_hint=hint(evidence.drop_hint), ev_break_hint=hint(evidence.break_hint),
            ev_build_hint=hint(evidence.build_hint), has_phrases=bool(evidence.phrases),
            music_end=int(end_bar) if end_bar is not None else -1,
            hot_kinds=hot_kinds, hot_times=hot_times, hot_bars=hot_bars, hot_snap_beats=hot_snap_beats,
            mem_times=mem_times, mem_bars=mem_bars)
        os.replace(tmp, task["out_path"])
        return path, "ok", f"{n} Takte"
    except Exception as exc:                       # noqa: BLE001 -- ein Track darf den Lauf nicht beenden
        return path, "fail", f"{type(exc).__name__}: {str(exc)[:80]}"


def export(tasks: list[dict], workers: int, progress=None) -> dict:
    from app import config as cfgmod
    counts = {"ok": 0, "skip": 0, "fail": 0}
    failures = []
    if not tasks:
        return {**counts, "failures": failures}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                             initargs=(str(cfgmod.base_dir()),)) as ex:
        futures = [ex.submit(_export_one, t) for t in tasks]
        for i, f in enumerate(as_completed(futures), 1):
            path, status, msg = f.result()
            counts[status] += 1
            if status != "ok":
                failures.append((path, f"{status}: {msg}"))
            if progress:
                progress(i, len(tasks))
    return {**counts, "failures": failures, "seconds": time.time() - t0}


def training_files(out_dir: str, exclude: set[str]) -> list[str]:
    """Alle Datensatz-Dateien ausser denen der Gold-Pfade (sortiert = reproduzierbar)."""
    banned = {f"{file_key(p)}.npz" for p in exclude}
    try:
        names = sorted(n for n in os.listdir(out_dir) if n.endswith(".npz") and not n.endswith(".tmp.npz"))
    except OSError:
        return []
    return [os.path.join(out_dir, n) for n in names if n not in banned]


def current_files(out_dir: str, paths: set[str]) -> list[str]:
    """Nur Dateien zu Tracks, die in diesem Lauf noch Kandidaten sind (alte bleiben liegen)."""
    keep = {f"{file_key(p)}.npz" for p in paths}
    return [f for f in training_files(out_dir, set()) if os.path.basename(f) in keep]
