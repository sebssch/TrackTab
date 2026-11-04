"""
Bewertung auf der Pruefliste (Gold-Set): die volle Kette (autocue.analyse) gegen die Hand-Cues.

Je Modellordner dieselben Tracks mit denselben Eingaben (TrackTabs Grid und Phrasen,
wie zur Laufzeit), damit alt und neu nebeneinander stehen. Gezaehlt wird, was der
Nutzer sieht -- die gesetzten Cues nach der Standard-Slot-Belegung (slots.defaults():
Hot A Break 1, Hot B 8 Takte vor Drop 2, Hot C Drop 2), damit Ergebnisse zwischen
Laeufen vergleichbar bleiben, auch wenn die eigene Belegung sich aendert:

  Hot A/B/C   gesetzter Cue hoechstens 2 (bzw. 8) Schlaege neben dem Hand-Cue;
              fehlt er, zaehlt das als Fehlgriff
  Memory      Anteil gesetzter Memory Cues, die auf einem Hand-Memory-Cue liegen
              (+-2 Schlaege), wie "auf Memory" im Original (eval_golden.py)
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed

HOT = ((1, "A"), (2, "B"), (3, "C"))


def _eval_one(task: dict) -> dict:
    """Ein Gold-Track gegen alle Modellordner. Top-Level fuer den Pool."""
    from app import autocue as autocue_mod
    from app.autocue.slots import defaults as slot_defaults
    from app.autocue.sources import TooLongError
    cfg = {**task["cfg"], "autocue_slots": slot_defaults()}
    out = {"path": task["path"], "models": {}}
    for name, folder in task["models"].items():
        try:
            res = autocue_mod.analyse(task["path"], task["beats"], task["phrases"], task["duration"], cfg,
                                      task["max_seconds"], models=folder,
                                      phrases_edited=bool(task.get("phrases_edited")))
        except TooLongError:
            out["models"][name] = {"status": "skipped"}
            continue
        except Exception as exc:                   # noqa: BLE001
            out["models"][name] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
            continue
        out["models"][name] = {"status": res["status"],
                               "cues": [[c["position_s"], c["slot"] or 0] for c in res["cues"]]}
    return out


def score(truth: dict, cues: list, beat_s: float) -> dict:
    """Je Hot A/B/C Abstand in Schlaegen (None = nicht gesetzt), Memory auf Hand-Memory."""
    out = {}
    for slot, label in HOT:
        hand = truth["hot"].get(slot)
        if hand is None:
            continue
        mine = [t for t, s in cues if s == slot]
        out[label] = abs(mine[0] - hand) / beat_s if mine else None
    mem = [t for t, s in cues if s == 0]
    on = sum(1 for t in mem if any(abs(t - h) / beat_s <= 2.0 for h in truth["memory"]))
    out["memory"] = (on, len(mem))
    return out


def evaluate(tasks: list[dict], models: dict[str, str], workers: int, progress=None) -> dict:
    """tasks: je Gold-Track {path, beats, phrases, duration, truth, beat_s, cfg, max_seconds}.
    Rueckgabe {Modellname: {"A": {...}, "B": ..., "C": ..., "memory": ..., "n": Tracks}}."""
    from app import config as cfgmod
    from app.autocue.training.dataset import _init_worker
    results = {name: {label: {"n": 0, "hit2": 0, "hit8": 0, "found": 0} for _, label in HOT}
               | {"memory": {"on": 0, "set": 0}, "n": 0, "done": 0}
               for name in models}
    if not tasks:
        return results
    by_path = {t["path"]: t for t in tasks}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                             initargs=(str(cfgmod.base_dir()),)) as ex:
        futures = [ex.submit(_eval_one, {**t, "models": models}) for t in tasks]
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            task = by_path[res["path"]]
            for name, r in res["models"].items():
                agg = results[name]
                agg["n"] += 1
                if r["status"] == "done":
                    agg["done"] += 1
                s = score(task["truth"], r.get("cues") or [], task["beat_s"])
                for _, label in HOT:
                    if label not in s:
                        continue
                    agg[label]["n"] += 1
                    off = s[label]
                    if off is not None:
                        agg[label]["found"] += 1
                        agg[label]["hit2"] += off <= 2.0
                        agg[label]["hit8"] += off <= 8.0
                agg["memory"]["on"] += s["memory"][0]
                agg["memory"]["set"] += s["memory"][1]
            if progress:
                progress(i, len(tasks))
    return results


def table(results: dict, log=print) -> None:
    names = list(results)
    log(f"  {'':<14}" + "".join(f"{n:>22}" for n in names))
    for _, label in HOT:
        row = f"  Hot {label} ±2 / ±8 "
        for n in names:
            a = results[n][label]
            row += f"{a['hit2']:>9}/{a['n']:<3} {a['hit8']:>4}/{a['n']:<3}" if a["n"] else f"{'--':>22}"
        log(row)
    row = f"  {'Memory auf Hand':<14}"
    for n in names:
        m = results[n]["memory"]
        row += f"{m['on']:>13}/{m['set']:<8}" if m["set"] else f"{'--':>22}"
    log(row)
    row = f"  {'Cues gesetzt':<14}"
    for n in names:
        row += f"{results[n]['done']:>13}/{results[n]['n']:<8}"
    log(row)


def hits(results: dict, name: str) -> int:
    """Summe der Hot-Treffer +-2 Schlaege -- der eine Wert fuer "besser oder schlechter"."""
    return sum(results[name][label]["hit2"] for _, label in HOT)


def model_paths(folder: str) -> bool:
    from app import autocue as autocue_mod
    return os.path.isdir(folder) and autocue_mod.complete(folder)
