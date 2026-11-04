"""
Ablauf von `./run.command autocue-train` und `autocue-eval` (aufgerufen aus cli.py).

Gesteuert ueber autocue-train.yaml im Entwicklungsordner (Vorlage
autocue-train.example.yaml, nicht eingecheckt): Instanz, Playlist "Training",
Playlist "Pruefliste", Ziele. Die App selbst trainiert nie -- sie bekommt das
fertige Modell mit der DMG (vendor/) oder als ZIP ueber "Modell laden".

    1  Instanz: DB, config.local.yaml und Rekordbox-Pfad kommen aus ihr
       (config.use_base_dir()).
    2  Tracks: Training = Playlist "Training", Messung = "Pruefliste", beide nur
       mit Hand-Cues Hot A + Hot C; Ueberschneidung = Abbruch.
    3  Vorbereiten: Versatz messen, fehlende Grids aus Rekordbox uebernehmen
       (schreibt in die DB der Instanz -- dieselbe Uebernahme wie in der App).
    4  Export der Takt-Merkmale (nur Geaendertes), 5 Grundmodelle, 6 Anker-Kette.
    7  Bewertung neues gegen aktives Modell auf der Pruefliste.
    8  ZIP, nach Rueckfrage in die Ziele: vendor/models/autocue
       (naechste DMG) und/oder <Instanz>/models/autocue (sofort aktiv).

Ergebnis je Lauf (train.log, model/, autocue-modell-<zeit>.zip) im Entwicklungsordner
unter autocue-training/runs/<zeit>/, Datensatz und Versatz-Cache in
<Instanz>/autocue-training/ -- beides gitignored, nie in git.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app import config as cfgmod

CONFIG_FILE = "autocue-train.yaml"
MIN_TRAINING = 20
MIN_PROBE = 3
KEEP_BACKUPS = 3


# ----------------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------------

def _app_data_dir(app: Path) -> Path:
    """Datenordner einer TrackTab.app -- dieselbe Regel wie config.base_dir() im Bundle:
    unter /Applications bzw. ~/Applications (Symlinks aufgeloest) die installierte App,
    sonst ein eigener Build."""
    if not app.is_dir():
        raise SystemExit(f"{app} gibt es nicht.")
    real = app.resolve()
    support = Path.home() / "Library" / "Application Support"
    installed = any(root in real.parents for root in (Path("/Applications"), Path.home() / "Applications"))
    return support / (cfgmod.APP_NAME if installed else cfgmod._BUILD_APP_NAME)


def _instance_dir(value: str) -> Path:
    support = Path.home() / "Library" / "Application Support"
    short = {"dev": cfgmod.PROJECT_ROOT, "installed": support / cfgmod.APP_NAME,
             "build": support / cfgmod._BUILD_APP_NAME}
    raw = Path(short.get(str(value), os.path.expanduser(str(value))))
    if raw.suffix == ".app":
        raw = _app_data_dir(raw)
    folder = raw.resolve()
    if not (folder / "data" / "quality.db").exists():
        raise SystemExit(f"Keine TrackTab-Instanz in {folder} (data/quality.db fehlt).")
    return folder


def load_config(args) -> dict:
    """autocue-train.yaml lesen; Kommandozeile geht vor."""
    import yaml
    path = Path(args.config or cfgmod.PROJECT_ROOT / CONFIG_FILE)
    if not path.exists():
        raise SystemExit(f"{path} fehlt -- {CONFIG_FILE.replace('.yaml', '.example.yaml')} kopieren und anpassen.")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    targets = args.target.split(",") if getattr(args, "target", None) else raw.get("targets") or ["vendor"]
    targets = [str(t).strip() for t in targets if str(t).strip()]
    bad = [t for t in targets if t not in ("vendor", "instance")]
    if bad:
        raise SystemExit(f"Unbekanntes Ziel in {path.name}: {', '.join(bad)} (moeglich: vendor, instance)")
    return {
        "path": path,
        "instance": _instance_dir(args.instance or raw.get("instance") or "installed"),
        "training": str(raw.get("training_playlist") or ""),
        "check": str(raw.get("check_playlist") or ""),
        "targets": targets,
        "folds": int(getattr(args, "folds", 0) or raw.get("folds") or 5),
        "workers": int(args.workers or raw.get("workers") or 0) or max(1, (os.cpu_count() or 4) - 2),
    }


def target_dirs(instance: Path) -> dict[str, Path]:
    return {"vendor": cfgmod.PROJECT_ROOT / "vendor" / "models" / "autocue",
            "instance": instance / "models" / "autocue"}


# ----------------------------------------------------------------------------
# Hilfen
# ----------------------------------------------------------------------------

class _Log:
    """Ausgabe auf die Konsole und ins Protokoll des Laufs."""

    def __init__(self, path: Path | None):
        self.fh = open(path, "a", encoding="utf-8") if path else None

    def __call__(self, text: str = "") -> None:
        print(text, flush=True)
        if self.fh:
            self.fh.write(text + "\n")
            self.fh.flush()


def _progress(log, label: str):
    state = {"last": -1, "t0": time.time()}

    def tick(done: int, total: int) -> None:
        step = int(20 * done / max(total, 1))
        if step != state["last"] or done == total:
            state["last"] = step
            left = (time.time() - state["t0"]) / max(done, 1) * (total - done)
            sys.stdout.write(f"\r  {label}: {done}/{total}  noch ~{left / 60:.0f} min   ")
            sys.stdout.flush()
            if done == total:
                sys.stdout.write("\n")
                if log.fh:
                    log.fh.write(f"  {label}: {total} in {time.time() - state['t0']:.0f}s\n")
    return tick


def _git_version() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cfgmod.PROJECT_ROOT,
                             capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "app/autocue"], cwd=cfgmod.PROJECT_ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return rev + ("-geaendert" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _gold_tasks(cfg: dict, gold_items: list[dict], leads: dict) -> list[dict]:
    from app import db as db_mod
    from app import gridstore
    from app.autocue.training import labels as labels_mod
    tasks = []
    conn = db_mod.connect(cfg)
    try:
        auto = db_mod.autocue_log_map(conn)
        for it in gold_items:
            path = it["path"]
            if path not in leads:
                continue
            own = gridstore.own_inputs(conn, path)
            if not own["beats"]:
                continue
            cues, _ = labels_mod.own_cues(it["edit_cues"], it["snap_cues"], leads.get(path))
            beat_s = labels_mod.beat_seconds(own["beats"])
            truth = labels_mod.hand_cues(cues, auto.get(path), beat_s)
            if not truth["hot"]:
                continue
            tasks.append({"path": path, "duration": it["duration"], "phrases": own["phrases"],
                          "phrases_edited": own["phrases_origin"] == "edited",
                          "beats": gridstore.beats_with_tempo(own["beats"], own["segments"] or [], 0.0, own["bpm"]),
                          "truth": truth, "beat_s": beat_s, "cfg": dict(cfg),
                          "max_seconds": 20 * 60.0})
    finally:
        conn.close()
    return tasks


def _install(staging: Path, target: Path, backup_root: Path, stamp: str, log) -> None:
    """Modellordner ersetzen; der alte Stand wandert nach backup_root (letzte 3 bleiben).
    Nie eine Sicherung in vendor/ -- das buendelt tracktab.spec komplett."""
    if target.exists() and any(target.iterdir()):
        dest = backup_root / stamp
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, dest, dirs_exist_ok=True)
        old = sorted(p for p in backup_root.iterdir() if p.is_dir())
        for p in old[:-KEEP_BACKUPS]:
            shutil.rmtree(p, ignore_errors=True)
        log(f"  alter Stand gesichert: {dest}")
    tmp = target.with_name(target.name + ".neu")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(staging, tmp)
    if target.exists():
        shutil.rmtree(target)
    tmp.rename(target)
    log(f"  installiert: {target}")


# ----------------------------------------------------------------------------
# Befehle
# ----------------------------------------------------------------------------

def _setup(args, need_lgb: bool):
    from app.autocue.training import boost
    if need_lgb:
        try:
            boost.lightgbm()
        except boost.MissingLightGBM as exc:
            raise SystemExit(str(exc)) from None
    conf = load_config(args)
    cfgmod.use_base_dir(conf["instance"])
    return conf, cfgmod.load()


def _collect(cfg: dict, conf: dict, gold_file: str | None, log, need_training: bool = True,
             probe: bool = False):
    """(Trainings-Kandidaten, Pruef-Kandidaten, Pruefpfade, Beschreibung der Pruefliste)."""
    from app import db as db_mod
    from app.autocue.training import dataset, lists
    conn = db_mod.connect(cfg)
    try:
        items = {it["path"]: it for it in dataset.candidates(conn)}
        try:
            if gold_file:
                check_name, check_paths = f"Datei {gold_file}", lists.from_file(gold_file)
            else:
                check_name, check_paths = lists.resolve(conn, conf["check"], "Prüfliste")
            train_name, train_paths = (lists.resolve(conn, conf["training"], "Training")
                                       if need_training else ("", []))
        except lists.ListError as exc:
            raise SystemExit(str(exc)) from None
    finally:
        conn.close()
    both = set(train_paths) & set(check_paths)
    if both:
        raise SystemExit(f"{len(both)} Tracks stehen in „{train_name}“ UND in „{check_name}“ -- "
                         f"die Prüfliste darf nie Lernmaterial sein. Zum Beispiel: {sorted(both)[0]}")
    train_items = [items[p] for p in dict.fromkeys(train_paths) if p in items]
    gold_items = [items[p] for p in dict.fromkeys(check_paths) if p in items]
    if need_training:
        log(f"Training „{train_name}“: {len(train_paths)} Tracks, davon {len(train_items)} mit Hand-Cues Hot A + C")
    log(f"Prüfliste „{check_name}“: {len(check_paths)} Tracks, davon {len(gold_items)} mit Hand-Cues")
    # Sofort abbrechen statt nach Vorbereitung und Export (base/chain brauchen mindestens 20).
    minimum = MIN_PROBE if probe else MIN_TRAINING
    if need_training and len(train_items) < minimum:
        raise SystemExit(f"Zu wenig Trainingsmaterial: {len(train_items)} Tracks mit Hand-Cues Hot A + C in "
                         f"„{train_name}“, nötig sind mindestens {minimum} (sinnvoll: Tausende; "
                         f"zum Ausprobieren --probe ab {MIN_PROBE}).")
    if not gold_items and not probe:
        raise SystemExit(f"In „{check_name}“ hat kein Track Hand-Cues (Hot A/B/C) -- so lässt sich nichts messen.")
    if need_training and len(train_items) < 1000:
        log("  Achtung: wenig Trainingsmaterial -- im Test lag ein Modell aus 80 Tracks deutlich "
            "unter dem aus 4.300.")
    if len(gold_items) < 10:
        log("  Achtung: weniger als 10 Prüftracks mit Hand-Cues -- die Bewertung ist kaum aussagekräftig.")
    return train_items, gold_items, check_paths, check_name


def cmd_train(args) -> int:
    from app import autocue as autocue_mod
    from app.autocue import package
    from app.autocue.training import base, chain, dataset
    from app.autocue.training import evaluate as eval_mod
    conf, cfg = _setup(args, need_lgb=True)
    instance = conf["instance"]
    probe = bool(getattr(args, "probe", False))
    if probe:
        conf["folds"] = min(conf["folds"], 2)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = cfgmod.base_dir() / "autocue-training"
    # Ergebnis (Protokoll, Modell, ZIP) im Entwicklungsordner, gitignored; Datensatz und
    # Versatz-Cache bleiben bei der Instanz, aus deren Daten sie stammen.
    run_dir = cfgmod.PROJECT_ROOT / "autocue-training" / "runs" / stamp
    staging = run_dir / "model"
    ds_dir = work / "dataset"
    for d in (run_dir, ds_dir):
        d.mkdir(parents=True, exist_ok=True)
    log = _Log(run_dir / "train.log")
    log(f"Auto-Cue-Training {stamp}, Instanz {instance}, "
        + ("PROBELAUF: nichts wird installiert" if probe else f"Ziele {', '.join(conf['targets'])}")
        + f" ({conf['path'].name})")

    train_items, gold_items, gold_paths, gold_desc = _collect(cfg, conf, args.gold_file, log, probe=probe)
    if args.limit:
        train_items = train_items[:args.limit]
    workers = conf["workers"]

    log("\nVorbereiten (Versatz zu Rekordbox, fehlende Grids aus Rekordbox uebernehmen):")
    leads = dataset.prepare(cfg, train_items + gold_items, str(work), workers, _progress(log, "vorbereitet"))

    tasks, counts = dataset.build_tasks(cfg, train_items, leads, str(ds_dir), args.force)
    log(f"\nExport: {counts['zu_rechnen']} zu rechnen, {counts['aktuell']} aktuell, "
        f"{counts['kein_grid']} ohne Grid, {counts['keine_labels']} ohne Hand-Cues A+C nach dem Filter")
    result = dataset.export(tasks, workers, _progress(log, "exportiert"))
    log(f"  {result['ok']} exportiert, {result['skip']} uebersprungen, {result['fail']} fehlgeschlagen")
    for path, msg in result["failures"][:8]:
        log(f"    {os.path.basename(path)}: {msg}")

    files = dataset.current_files(str(ds_dir), {it["path"] for it in train_items})
    log(f"\nTraining mit {len(files)} Tracks, {conf['folds']} Faltungen")
    t0 = time.time()
    try:
        bar = base.train(files, str(staging), conf["folds"], log=log, min_tracks=MIN_PROBE if probe else 20)
        chn = chain.train(files, str(staging), conf["folds"], log=log, min_tracks=MIN_PROBE if probe else 20,
                          min_labels=0 if probe else 10)
    except RuntimeError as exc:
        log(f"\nAbbruch: {exc}")
        return 1
    with open(staging / "features.json", "w") as fh:
        json.dump({"bar": bar["meta"], "chain": chn["meta"]}, fh)
    with open(staging / "calibration.json", "w") as fh:
        json.dump({"bar": bar["calibration"], "chain": chn["calibration"]}, fh)
    log(f"Training fertig in {(time.time() - t0) / 60:.0f} min")

    active = autocue_mod.model_dir()
    models = {"aktiv": active, "neu": str(staging)}
    log(f"\nBewertung auf der Prüfliste ({gold_desc}), aktives Modell: {active}")
    gold_tasks = _gold_tasks(cfg, gold_items, leads)
    results = eval_mod.evaluate(gold_tasks, models, workers, _progress(log, "bewertet"))
    eval_mod.table(results, log)

    manifest = {"trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "tracktab",
                "instance": str(instance), "git": _git_version(),
                "n_tracks": bar["meta"]["n_tracks"], "n_chain_tracks": chn["meta"]["n_tracks"],
                "dataset_version": dataset.DATASET_VERSION,
                "feature_version": bar["meta"]["feature_version"],
                "oof": {"bar": bar["summary"], "chain": chn["summary"]},
                "gold": {"source": gold_desc, "n": len(gold_tasks), "results": results["neu"],
                         "active_results": results["aktiv"], "paths": sorted(gold_paths)}}
    with open(staging / autocue_mod.MANIFEST, "w") as fh:
        json.dump(manifest, fh, indent=1)
    if probe:
        log(f"\nProbelauf fertig, nichts installiert. Modell und Protokoll: {run_dir}")
        return 0
    zip_path = package.write_zip(str(staging), str(run_dir / f"autocue-modell-{stamp}.zip"))
    log(f"\nModell zum Laden in der App: {zip_path}")

    new_hits, old_hits = eval_mod.hits(results, "neu"), eval_mod.hits(results, "aktiv")
    log(f"Hot A+B+C ±2 Schlaege: neu {new_hits}, aktiv {old_hits}")
    if not args.yes:
        from rich.prompt import Confirm
        hint = "" if new_hits >= old_hits else " [red](schlechter als das aktive)[/red]"
        if not Confirm.ask(f"Neues Modell nach {', '.join(conf['targets'])} installieren?{hint}",
                           default=new_hits >= old_hits):
            log(f"Nicht installiert. Das Modell bleibt in {staging} und {zip_path}")
            return 0
    dirs = target_dirs(instance)
    for t in conf["targets"]:
        backup = (cfgmod.resolve("backup") if t == "instance" else cfgmod.PROJECT_ROOT / "backup") / "autocue-models" / t
        _install(staging, dirs[t], backup, stamp, log)
    log("Fertig.")
    return 0


def cmd_eval(args) -> int:
    from app import autocue as autocue_mod
    from app.autocue.training import dataset
    from app.autocue.training import evaluate as eval_mod
    conf, cfg = _setup(args, need_lgb=False)
    work = cfgmod.base_dir() / "autocue-training"
    work.mkdir(parents=True, exist_ok=True)
    log = _Log(None)
    _train, gold_items, _paths, gold_desc = _collect(cfg, conf, args.gold_file, log, need_training=False)
    models = {}
    for name in (args.model or "aktiv").split(","):
        name = name.strip()
        folder = {"aktiv": autocue_mod.model_dir(), "vendor": autocue_mod.bundled_dir(),
                  "instance": autocue_mod.trained_dir()}.get(name, os.path.expanduser(name))
        if not autocue_mod.complete(folder):
            raise SystemExit(f"Kein vollstaendiges Modell in {folder}")
        models[name if len(name) < 20 else os.path.basename(folder)] = folder
    leads = dataset.prepare(cfg, gold_items, str(work), conf["workers"], _progress(log, "vorbereitet"))
    tasks = _gold_tasks(cfg, gold_items, leads)
    log(f"Prüfliste {gold_desc}: {len(tasks)} Tracks bewertbar")
    results = eval_mod.evaluate(tasks, models, conf["workers"], _progress(log, "bewertet"))
    eval_mod.table(results, log)
    return 0
