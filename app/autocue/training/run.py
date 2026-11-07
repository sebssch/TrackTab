"""
Ablauf von `./run.command autocue-train` und `autocue-eval` (aufgerufen aus cli.py).

Gesteuert ueber autocue-train.yaml im Entwicklungsordner (Vorlage
autocue-train.example.yaml, nicht eingecheckt): Instanz, Playlist "Training",
Playlist "Pruefliste", Cue-Zuordnung. Die App trainiert ueber denselben
Kern (train_core(), aufgerufen aus job.py) mit ihren eigenen Daten.

    1  Instanz: DB, config.local.yaml und Rekordbox-Pfad kommen aus ihr
       (config.use_base_dir()).
    2  Tracks: Training = Playlist "Training", Messung = "Pruefliste", beide nur
       mit Hand-Cues Hot A + Hot C; Ueberschneidung = Abbruch.
    3  Vorbereiten: Versatz messen, fehlende Grids aus Rekordbox uebernehmen
       (schreibt in die DB der Instanz -- dieselbe Uebernahme wie in der App).
    4  Export der Takt-Merkmale (nur Geaendertes), 5 Grundmodelle, 6 Anker-Kette.
    7  Bewertung neues gegen aktives Modell auf der Pruefliste (ohne aktives
       nur das neue).
    8  ZIP, nach Rueckfrage in die Modellbibliothek der Instanz (dort gewaehlt).
       Ein mitgeliefertes Modell (frueher Ziel "vendor") gibt es nicht mehr.

Ergebnis je Lauf (train.log, model/, autocue-modell-<zeit>.zip) im Entwicklungsordner
unter autocue-training/runs/<zeit>/, Datensatz und Versatz-Cache in
<Instanz>/autocue-training/ -- beides gitignored, nie in git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app import config as cfgmod

CONFIG_FILE = "autocue-train.yaml"
MIN_TRAINING = 20
MIN_PROBE = 3


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
    from app.autocue.training.labels import clean_label_slots
    label_slots = None                     # None = aus der Slot-Belegung der Instanz
    if raw.get("label_slots") is not None:
        try:
            label_slots = clean_label_slots(raw.get("label_slots"))
        except ValueError as exc:
            raise SystemExit(f"{path.name}, label_slots: {exc}") from None
    return {
        "path": path,
        "name": str(raw.get("name") or ""),
        "label_slots": label_slots,
        "instance": _instance_dir(args.instance or raw.get("instance") or "installed"),
        "training": str(raw.get("training_playlist") or ""),
        "check": str(raw.get("check_playlist") or ""),
        "folds": int(getattr(args, "folds", 0) or raw.get("folds") or 5),
        "workers": int(args.workers or raw.get("workers") or 0) or max(1, (os.cpu_count() or 4) - 2),
    }


class TrainError(RuntimeError):
    """Anzeigbarer Grund, warum nicht trainiert werden kann (CLI: SystemExit, App: Meldung)."""


def _palettes() -> dict:
    from app import rekordbox
    return rekordbox.cue_palettes()


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


def collect(cfg: dict, training: str, check: str, gold_file: str | None, log, need_training: bool = True,
            probe: bool = False, label_slots: dict | None = None, memory_ok: bool = False,
            training_paths: list | None = None, check_paths_given: list | None = None):
    """(Trainings-Kandidaten, Pruef-Kandidaten, Pruefpfade, Beschreibung der Pruefliste).
    training/check: Playlist per Name, "Ordner/Name" oder "id:<id>" (nur normale);
    training_paths/check_paths_given: schon aufgeloeste Pfade (App, auch smarte
    Playlisten), training/check sind dann nur der Anzeigename.
    TrainError mit anzeigbarem Text, wenn so nicht trainiert werden kann."""
    from app import db as db_mod
    from app.autocue.training import dataset, labels, lists
    conn = db_mod.connect(cfg)
    try:
        items = {it["path"]: it for it in dataset.candidates(conn, label_slots, memory_ok)}
        try:
            if gold_file:
                check_name, check_paths = f"Datei {gold_file}", lists.from_file(gold_file)
            elif check_paths_given is not None:
                check_name, check_paths = lists.given(check, check_paths_given, "Prüfliste")
            else:
                check_name, check_paths = lists.resolve(conn, check, "Prüfliste")
            if not need_training:
                train_name, train_paths = "", []
            elif training_paths is not None:
                train_name, train_paths = lists.given(training, training_paths, "Training")
            else:
                train_name, train_paths = lists.resolve(conn, training, "Training")
        except lists.ListError as exc:
            raise TrainError(str(exc)) from None
    finally:
        conn.close()
    ls = label_slots if label_slots is not None else labels.DEFAULT_LABEL_SLOTS
    req = sorted("ABCDEFGH"[s - 1] for s in labels.required_slots(ls))
    need = " + ".join(f"Hot {c}" for c in req) if req else "zum Lernen"
    both = set(train_paths) & set(check_paths)
    if both:
        raise TrainError(f"{len(both)} Tracks stehen in „{train_name}“ UND in „{check_name}“ -- "
                         f"die Prüfliste darf nie Lernmaterial sein. Zum Beispiel: {sorted(both)[0]}")
    train_items = [items[p] for p in dict.fromkeys(train_paths) if p in items]
    gold_items = [items[p] for p in dict.fromkeys(check_paths) if p in items]
    if need_training:
        log(f"Training „{train_name}“: {len(train_paths)} Tracks, davon {len(train_items)} mit Hand-Cues {need}")
    log(f"Prüfliste „{check_name}“: {len(check_paths)} Tracks, davon {len(gold_items)} mit Hand-Cues")
    # Sofort abbrechen statt nach Vorbereitung und Export (base/chain brauchen mindestens 20).
    minimum = MIN_PROBE if probe else MIN_TRAINING
    if need_training and len(train_items) < minimum:
        raise TrainError(f"Zu wenig Trainingsmaterial: {len(train_items)} Tracks mit Hand-Cues {need} in "
                         f"„{train_name}“, nötig sind mindestens {minimum} (sinnvoll: Tausende"
                         + (f"; zum Ausprobieren --probe ab {MIN_PROBE})." if not probe else ")."))
    if not gold_items and not probe:
        raise TrainError(f"In „{check_name}“ hat kein Track Hand-Cues {need} -- so lässt sich nichts messen.")
    if need_training and len(train_items) < 1000:
        log("  Achtung: wenig Trainingsmaterial -- im Test lag ein Modell aus 80 Tracks deutlich "
            "unter dem aus 4.300.")
    if len(gold_items) < 10:
        log("  Achtung: weniger als 10 Prüftracks mit Hand-Cues -- die Bewertung ist kaum aussagekräftig.")
    return train_items, gold_items, check_paths, check_name, train_name


def _collect(cfg: dict, conf: dict, gold_file: str | None, log, need_training: bool = True,
             probe: bool = False):
    try:
        from app.autocue.training.labels import learn_plan
        plan = learn_plan(cfg.get("autocue_slots"), conf.get("label_slots"))
        train_items, gold_items, gold_paths, gold_desc, _ = collect(
            cfg, conf["training"], conf["check"], gold_file, log, need_training, probe, plan["hot"],
            bool(plan["memory"]))
    except TrainError as exc:
        raise SystemExit(str(exc)) from None
    return train_items, gold_items, gold_paths, gold_desc


# Phasen fuer den Fortschritt (job.py zeigt sie an); Reihenfolge = Ablauf.
PHASES = ("collect", "prepare", "export", "base", "chain", "eval", "package")


def train_core(cfg: dict, params: dict, staging: Path, work: Path, log,
               progress=None, cancel=None) -> dict:
    """
    Der gemeinsame Ablauf von CLI und App: sammeln, vorbereiten, exportieren,
    trainieren, bewerten, Manifest schreiben. Modell landet in staging/.

    params: training, check (Playlist-Namen), slots (Slot-Belegung, aus der gelernt
            wird; Vorgabe die der Instanz), label_slots (optionale feste Hot-Zuordnung
            statt der Belegung, nur CLI), folds, workers, probe, limit, force,
            gold_file, name, instance (nur fuers Manifest), source.
    progress(phase, done, total), cancel() -> True bricht ab (base.Cancelled).
    TrainError mit anzeigbarem Text. Rueckgabe {manifest, results, new_hits, old_hits}.
    """
    from app import autocue as autocue_mod
    from app.autocue import slots as slots_mod
    from app.autocue.training import base, chain, dataset
    from app.autocue.training import evaluate as eval_mod
    from app.autocue.training import labels as labels_mod

    def phase(name: str):
        def tick(done: int, total: int) -> None:
            if progress:
                progress(name, done, total)
        tick(0, 0)
        return tick

    slots = params.get("slots") or cfg.get("autocue_slots") or slots_mod.defaults()
    plan = labels_mod.learn_plan(slots, params.get("label_slots"))
    if plan["error"]:
        raise TrainError(plan["error"])
    label_slots, memory_roles = plan["hot"], plan["memory"]
    probe = bool(params.get("probe"))
    folds = int(params.get("folds") or 5)
    if probe:
        folds = min(folds, 2)
    workers = int(params.get("workers") or max(1, (os.cpu_count() or 4) - 2))
    ds_dir = work / "dataset"
    ds_dir.mkdir(parents=True, exist_ok=True)

    phase("collect")
    train_items, gold_items, gold_paths, gold_desc, train_name = collect(
        cfg, params.get("training", ""), params.get("check", ""), params.get("gold_file"), log,
        probe=probe, label_slots=label_slots, memory_ok=bool(memory_roles),
        training_paths=params.get("training_paths"), check_paths_given=params.get("check_paths"))
    if params.get("limit"):
        train_items = train_items[:int(params["limit"])]
    letters = "ABCDEFGH"
    log("Gelernt wird: " + ", ".join(
        [f"{r} = Hot {letters[s - 1]}" for r, s in label_slots.items()]
        + [f"{r} aus Memory Cues" for r in memory_roles]))
    if memory_roles:
        n_mem = labels_mod.count_with_memory(train_items)
        log(f"  {n_mem} von {len(train_items)} Trainingstracks haben Memory Cues")

    log("\nVorbereiten (Versatz zu Rekordbox, fehlende Grids aus Rekordbox uebernehmen):")
    leads = dataset.prepare(cfg, train_items + gold_items, str(work), workers, phase("prepare"), cancel)

    tasks, counts = dataset.build_tasks(cfg, train_items, leads, str(ds_dir), bool(params.get("force")),
                                        label_slots, bool(memory_roles))
    log(f"\nExport: {counts['zu_rechnen']} zu rechnen, {counts['aktuell']} aktuell, "
        f"{counts['kein_grid']} ohne Grid, {counts['keine_labels']} ohne Pflicht-Hand-Cues nach dem Filter")
    result = dataset.export(tasks, workers, phase("export"), cancel)
    log(f"  {result['ok']} exportiert, {result['skip']} uebersprungen, {result['fail']} fehlgeschlagen")
    for path, msg in result["failures"][:8]:
        log(f"    {os.path.basename(path)}: {msg}")

    files = dataset.current_files(str(ds_dir), {it["path"] for it in train_items})
    log(f"\nTraining mit {len(files)} Tracks, {folds} Faltungen")
    if not files:
        raise TrainError("Kein Track taugt zum Lernen (Hand-Cues fehlen oder liegen nur im Ausklang).")
    t0 = time.time()
    try:
        bar = base.train(files, str(staging), folds, log=log, min_tracks=MIN_PROBE if probe else 20,
                         label_slots=label_slots, progress=phase("base"), cancel=cancel)
        chn = chain.train(files, str(staging), folds, log=log, min_tracks=MIN_PROBE if probe else 20,
                          min_labels=0 if probe else 10, label_slots=label_slots,
                          progress=phase("chain"), cancel=cancel, memory_roles=memory_roles)
    except base.Cancelled:
        raise
    except RuntimeError as exc:
        raise TrainError(str(exc)) from None
    with open(staging / "features.json", "w") as fh:
        json.dump({"bar": bar["meta"], "chain": chn["meta"]}, fh)
    with open(staging / "calibration.json", "w") as fh:
        json.dump({"bar": bar["calibration"], "chain": chn["calibration"]}, fh)
    log(f"Training fertig in {(time.time() - t0) / 60:.0f} min")

    # Die Belegung, nach der gelernt wurde, gehoert zum Modell (Vorschlag beim Wechsel,
    # Grundlage der Bewertung). Mit fester Zuordnung (CLI) die daraus abgeleitete.
    suggested = (slots_mod.for_label_slots(label_slots) if params.get("label_slots") is not None
                 else slots_mod.validate(slots, _palettes()))
    # Ohne geltendes Modell (erstes Training) gibt es nichts zum Vergleichen.
    active = autocue_mod.model_dir()
    models = {"aktiv": active, "neu": str(staging)} if active else {"neu": str(staging)}
    log(f"\nBewertung auf der Prüfliste ({gold_desc}), aktives Modell: {active or 'keins'}")
    gold_tasks = _gold_tasks(cfg, gold_items, leads)
    results = eval_mod.evaluate(gold_tasks, models, workers, phase("eval"), suggested, cancel)
    eval_mod.table(results, log)

    phase("package")
    manifest = {"trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": params.get("source") or "tracktab",
                "name": str(params.get("name") or ""), "instance": str(params.get("instance") or ""),
                "git": _git_version(), "training_playlist": train_name,
                "n_tracks": bar["meta"]["n_tracks"], "n_chain_tracks": chn["meta"]["n_tracks"],
                "dataset_version": dataset.DATASET_VERSION,
                "feature_version": bar["meta"]["feature_version"],
                "label_slots": labels_mod.letters(label_slots), "memory_roles": list(memory_roles),
                "suggested_slots": suggested, "skipped": chn["meta"].get("skipped", []),
                "own_roles": sorted(set(bar["meta"].get("own_roles", []))
                                    | {r for r in ("drop1", "break1", "break2", "outro_start")
                                       if r in chn["meta"].get("feature_names", {})}
                                    | ({"outro_start"} if "outro_start" in label_slots
                                       or "outro_start" in memory_roles else set())),
                "oof": {"bar": bar["summary"], "chain": chn["summary"]},
                "gold": {"source": gold_desc, "n": len(gold_tasks), "results": results["neu"],
                         "active_results": results.get("aktiv"), "paths": sorted(gold_paths)}}
    with open(staging / autocue_mod.MANIFEST, "w") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    return {"manifest": manifest, "results": results,
            "new_hits": eval_mod.hits(results, "neu"),
            "old_hits": eval_mod.hits(results, "aktiv") if "aktiv" in results else None}


def cmd_train(args) -> int:
    from app.autocue import library, package
    from app.autocue.training import base
    conf, cfg = _setup(args, need_lgb=True)
    instance = conf["instance"]
    probe = bool(getattr(args, "probe", False))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = cfgmod.base_dir() / "autocue-training"
    # Ergebnis (Protokoll, Modell, ZIP) im Entwicklungsordner, gitignored; Datensatz und
    # Versatz-Cache bleiben bei der Instanz, aus deren Daten sie stammen.
    run_dir = cfgmod.PROJECT_ROOT / "autocue-training" / "runs" / stamp
    staging = run_dir / "model"
    run_dir.mkdir(parents=True, exist_ok=True)
    log = _Log(run_dir / "train.log")
    log(f"Auto-Cue-Training {stamp}, Instanz {instance}, "
        + (", PROBELAUF: nichts wird installiert" if probe else "") + f" ({conf['path'].name})")
    bars: dict = {}

    def progress(phase: str, done: int, total: int) -> None:
        if total and phase in ("prepare", "export", "eval"):
            bars.setdefault(phase, _progress(log, {"prepare": "vorbereitet", "export": "exportiert",
                                                   "eval": "bewertet"}[phase]))(done, total)

    params = {"training": conf["training"], "check": conf["check"], "label_slots": conf["label_slots"],
              "slots": cfg.get("autocue_slots"),
              "folds": conf["folds"], "workers": conf["workers"], "probe": probe, "limit": args.limit,
              "force": args.force, "gold_file": args.gold_file, "name": conf["name"],
              "instance": str(instance), "source": "tracktab"}
    try:
        out = train_core(cfg, params, staging, work, log, progress)
    except TrainError as exc:
        log(f"\nAbbruch: {exc}")
        return 1
    except base.Cancelled:
        return 1
    if probe:
        log(f"\nProbelauf fertig, nichts installiert. Modell und Protokoll: {run_dir}")
        return 0
    zip_path = package.write_zip(str(staging), str(run_dir / f"autocue-modell-{stamp}.zip"))
    log(f"\nModell zum Laden in der App: {zip_path}")

    new_hits, old_hits = out["new_hits"], out["old_hits"]
    better = old_hits is None or new_hits >= old_hits
    log(f"Hot-Treffer ±2 Schlaege: neu {new_hits}, "
        + ("kein aktives Modell zum Vergleichen" if old_hits is None else f"aktiv {old_hits}"))
    if not args.yes:
        from rich.prompt import Confirm
        hint = "" if better else " [red](schlechter als das aktive)[/red]"
        if not Confirm.ask(f"Neues Modell in die Modellbibliothek der Instanz übernehmen und auswählen?{hint}",
                           default=better):
            log(f"Nicht übernommen. Das Modell bleibt in {staging} und {zip_path}")
            return 0
    # Eigener Eintrag in der Modellbibliothek der Instanz, dort gewaehlt.
    from app import settings
    model_id = library.add_folder(str(staging), conf["name"], "trained")
    settings.save_autocue_model(model_id)
    log(f"  in die Modellbibliothek der Instanz: {model_id} (aktiv)")
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
        from app.autocue import library
        if name == "aktiv":
            folder = autocue_mod.model_dir()
            if not folder:
                raise SystemExit("Die Instanz hat kein aktives Modell -- erst trainieren oder eines auswählen.")
        else:
            folder = library.folder_of(name) or os.path.expanduser(name)
        if not autocue_mod.complete(folder):
            raise SystemExit(f"Kein vollstaendiges Modell in {folder}")
        models[name if len(name) < 20 else os.path.basename(folder)] = folder
    leads = dataset.prepare(cfg, gold_items, str(work), conf["workers"], _progress(log, "vorbereitet"))
    tasks = _gold_tasks(cfg, gold_items, leads)
    log(f"Prüfliste {gold_desc}: {len(tasks)} Tracks bewertbar")
    from app.autocue.slots import for_label_slots
    results = eval_mod.evaluate(tasks, models, conf["workers"], _progress(log, "bewertet"),
                                for_label_slots(conf["label_slots"]) if conf["label_slots"] is not None
                                else (cfg.get("autocue_slots") or None))
    eval_mod.table(results, log)
    return 0
