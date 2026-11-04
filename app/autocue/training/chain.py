"""
Anker-Kette: Drop 1, Break 1, Break 2 und Outro -- relativ zu Drop 2.

Nachbau von tools/train_anchor_chain.py des Autocue-Projekts, Parameter unveraendert.
Jede Stufe lernt in ihrem Fenster, der Abstand zu den schon gefundenen Ankern ist
selbst ein Merkmal (learned.chain_matrix()). Labels aus learned.position_labels():

    Drop 1   Memory Cue am ersten Chorus-Phrasenstart nach Hot A
    Break 1  Hot A
    Break 2  Memory Cue am ersten Down/Up-Phrasenstart nach Drop 1
    Outro    Memory Cue nach Hot C, der +16 Takten am naechsten liegt

Drop 1 und Break 2 brauchen Phrasen fuers Label. Damit die Modelle auch ohne
Phrasen arbeiten, werden die Phrasen-Merkmale bei 40 % der Tracks ausgeblendet.
Break 1 lernt zusaetzlich auf Tracks ohne Drop-1-Label: Drop 1 ist dort der Memory
Cue, den das (Out-of-Fold-)Drop-1-Modell am hoechsten bewertet. Gemessen wird wie
zur Laufzeit: Break 1 und Break 2 relativ zum VORHERGESAGTEN Drop 1. Das Outro-
Modell gilt nur, wenn es die Regel "Drop 2 + 16 Takte" um mehr als einen
Prozentpunkt schlaegt.

Schreibt in out_dir: chain_drop1/break1/break2/outro.npz (learned.CHAIN_FILES).
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

from app.autocue.learned import (CHAIN_FEATURE_VERSION, CHAIN_FILES, EVIDENCE_COLS, MIN_PROB,
                                 OUTRO_MIN_LEVEL, build_design_matrix, chain_matrix, chain_window,
                                 level_after, position_labels)
from app.autocue.training import boost

LGB_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "max_bin": 63,
    "scale_pos_weight": 10.0,
    "verbose": -1,
}
N_ROUNDS = 1000
EARLY_STOPPING = 60
NEIGHBOUR_WEIGHT = 0.25
PHRASE_DROPOUT = 0.4
HIT_BEATS = 2.0
OUTRO_MIN_GAIN = 0.01
BREAK1_MODE = "ensemble"     # Laufzeit: Break 1 aus Kette x Grundmodell


class Track:
    __slots__ = ("key", "X", "names", "rms", "n", "beats", "a", "c", "mem", "labels", "phrases")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def hit(self, pred, truth) -> bool:
        if pred is None or truth is None:
            return False
        return abs(int(self.beats[pred]) - int(self.beats[truth])) <= HIT_BEATS


def load_tracks(files: list[str], seed: int, log=print) -> list[Track]:
    rng = np.random.default_rng(seed)
    tracks: list[Track] = []
    base_names = None
    for path in files:
        try:
            with np.load(path) as d:
                names = json.loads(str(d["names"]))
                kinds, bars = d["hot_kinds"].tolist(), d["hot_bars"].tolist()
                if 3 not in kinds or "rms" not in names:
                    continue
                a = int(bars[kinds.index(1)]) if 1 in kinds else None
                c = int(bars[kinds.index(3)])
                mem = {int(b) for b in d["mem_bars"]}
                has = bool(d["has_phrases"])
                curves = {k: d[k].copy() for k in EVIDENCE_COLS}
                if has:
                    labels = position_labels(curves["ev_drop_hint"], curves["ev_break_hint"],
                                             curves["ev_build_hint"], mem, a, c)
                else:
                    labels = position_labels(None, None, None, mem, a, c)
                visible = has
                if has and rng.random() < PHRASE_DROPOUT:
                    for k in curves:
                        curves[k][:] = 0
                    visible = False
                X, fn = build_design_matrix(d["matrix"], names, curves, d["bar_start_beats"], float(d["bpm"]))
                if base_names is None:
                    base_names = fn
                elif fn != base_names:
                    continue
                tracks.append(Track(key=os.path.basename(path), X=X, names=fn,
                                    rms=d["matrix"][:, names.index("rms")].astype(np.float64),
                                    n=int(X.shape[0]), beats=d["bar_start_beats"].astype(np.int64),
                                    a=a, c=c, mem=mem, labels=labels, phrases=visible))
        except Exception as exc:                   # noqa: BLE001
            log(f"  uebersprungen {os.path.basename(path)}: {type(exc).__name__}: {exc}")
    return tracks


def _rows(t: Track, role: str, d1, label):
    win = chain_window(role, t.n, t.c, d1)
    if not win:
        return None
    Xc, cn = chain_matrix(role, t.X, t.names, t.c, d1, t.rms)
    y = np.zeros(len(win), np.float32)
    w = np.ones(len(win), np.float32)
    if label is not None and label in win:
        j = win.index(label)
        y[j] = 1.0
        for nb in (j - 1, j + 1):
            if 0 <= nb < len(win) and abs(win[nb] - label) == 1:
                w[nb] = NEIGHBOUR_WEIGHT
    return Xc[win], y, w, win, cn


def _stack(items):
    return (np.concatenate([i[0] for i in items]), np.concatenate([i[1] for i in items]),
            np.concatenate([i[2] for i in items]))


def _train(items, val_items=None, rounds: int = N_ROUNDS):
    """None ohne ein einziges Positivbeispiel (nur bei sehr kleinem Material, --probe)."""
    if not items:
        return None
    val = _stack(val_items) if val_items else None
    return boost.train(LGB_PARAMS, *_stack(items), rounds, EARLY_STOPPING if val is not None else 0, val)


def _pick(booster, item, allowed=None):
    if booster is None:
        return None, 0.0, None
    X, _, _, win, _ = item
    p = np.asarray(booster.predict(X), dtype=np.float64)
    mask = np.ones(len(win), bool) if allowed is None else np.array([b in allowed for b in win])
    if not mask.any():
        return None, 0.0, p
    j = int(np.argmax(np.where(mask, p, -np.inf)))
    if p[j] < MIN_PROB:
        return None, float(p[j]), p
    return int(win[j]), float(p[j]), p


def _report(title: str, results, log=print) -> dict:
    if not results:
        log(f"  {title:<34} --")
        return {"n": 0}
    hits = np.array([h for _, h in results], dtype=float)
    vis = np.array([h for t, h in results if t.phrases], dtype=float)
    hid = np.array([h for t, h in results if not t.phrases], dtype=float)
    fmt = lambda a: f"{a.mean():6.1%} (n={len(a)})" if len(a) else "   --"  # noqa: E731
    log(f"  {title:<34} {hits.mean():6.1%} (n={len(hits)})   mit Phrasen {fmt(vis)}   ohne {fmt(hid)}")
    return {"n": int(len(hits)), "hit": float(hits.mean()),
            "hit_phrases": float(vis.mean()) if len(vis) else None,
            "hit_no_phrases": float(hid.mean()) if len(hid) else None}


def _positive(items):
    return [it for it in items if it is not None and it[1].sum() > 0]


def train(files: list[str], out_dir: str, n_folds: int = 5, seed: int = 42, save: bool = True,
          log=print, min_tracks: int = 20, min_labels: int = 10) -> dict:
    t_start = time.time()
    tracks = load_tracks(files, seed, log)
    n_lab = {r: sum(1 for t in tracks if t.labels[r] is not None) for r in ("drop1", "break2", "outro_start")}
    log(f"\nAnker-Kette: {len(tracks)} Tracks mit Hot C; Labels: Drop 1 {n_lab['drop1']}, "
        f"Break 2 {n_lab['break2']}, Outro {n_lab['outro_start']}, "
        f"Hot A {sum(1 for t in tracks if t.a is not None)}")
    if len(tracks) < min_tracks or min(n_lab.values()) < min_labels:
        raise RuntimeError("zu wenige Tracks bzw. Labels fuer die Anker-Kette")

    folds = boost.folds(len(tracks), n_folds, seed)
    best_iters: dict[str, list[int]] = {r: [] for r in CHAIN_FILES}
    feature_names: dict[str, list[str]] = {}
    oof: dict[str, list] = {r: [] for r in CHAIN_FILES}

    # Stufe 1: Drop 1
    d1_pred: dict[int, int | None] = {}
    d1_curve: dict[int, tuple] = {}
    for fold, (tr, va) in enumerate(folds, 1):
        t0 = time.time()
        tr_items = _positive(_rows(tracks[i], "drop1", None, tracks[i].labels["drop1"]) for i in tr)
        va_items = {int(i): _rows(tracks[i], "drop1", None, tracks[i].labels["drop1"]) for i in va}
        booster = _train(tr_items, _positive(va_items.values()))
        if booster is not None:
            best_iters["drop1"].append(boost.best_iteration(booster, N_ROUNDS))
        if tr_items:
            feature_names["drop1"] = tr_items[0][4]
        for i, it in va_items.items():
            if it is None:
                d1_pred[i] = None
                continue
            bar, p, curve = _pick(booster, it)
            d1_pred[i] = bar
            d1_curve[i] = (it[3], curve)
            t = tracks[i]
            if t.labels["drop1"] is not None:
                oof["drop1"].append((t, t.hit(bar, t.labels["drop1"]), p))
        log(f"  Faltung {fold}/{n_folds} Drop 1: {booster.best_iteration if booster else 0} Runden, "
            f"{time.time() - t0:.0f}s")

    # Drop 1 fuer Tracks ohne Label: der Memory Cue, den das Modell am hoechsten bewertet
    d1_train: dict[int, int] = {}
    for i, t in enumerate(tracks):
        if t.labels["drop1"] is not None:
            d1_train[i] = t.labels["drop1"]
        elif t.a is not None and i in d1_curve:
            win, curve = d1_curve[i]
            cands = [(curve[j], b) for j, b in enumerate(win) if b in t.mem and b > t.a]
            if cands:
                d1_train[i] = max(cands)[1]

    # Stufen 2-4: Break 1, Break 2, Outro
    rule_results = []
    for fold, (tr, va) in enumerate(folds, 1):
        t0 = time.time()
        tr_items = _positive(_rows(tracks[i], "break1", d1_train[i], tracks[i].a)
                             for i in tr if i in d1_train and tracks[i].a is not None)
        va_items = {int(i): _rows(tracks[i], "break1", d1_pred.get(int(i)), tracks[i].a)
                    for i in va if tracks[i].a is not None and d1_pred.get(int(i)) is not None}
        booster = _train(tr_items, _positive(va_items.values()))
        if booster is not None:
            best_iters["break1"].append(boost.best_iteration(booster, N_ROUNDS))
        if tr_items:
            feature_names["break1"] = tr_items[0][4]
        for i in va:
            t = tracks[i]
            if t.a is None:
                continue
            it = va_items.get(int(i))
            bar, p, _ = _pick(booster, it) if it is not None else (None, 0.0, None)
            oof["break1"].append((t, t.hit(bar, t.a), p))

        tr_items = _positive(_rows(tracks[i], "break2", tracks[i].labels["drop1"], tracks[i].labels["break2"])
                             for i in tr if tracks[i].labels["break2"] is not None)
        va_items = {int(i): _rows(tracks[i], "break2", d1_pred.get(int(i)), tracks[i].labels["break2"])
                    for i in va if tracks[i].labels["break2"] is not None and d1_pred.get(int(i)) is not None}
        booster = _train(tr_items, _positive(va_items.values()))
        if booster is not None:
            best_iters["break2"].append(boost.best_iteration(booster, N_ROUNDS))
        if tr_items:
            feature_names["break2"] = tr_items[0][4]
        for i in va:
            t = tracks[i]
            if t.labels["break2"] is None:
                continue
            it = va_items.get(int(i))
            bar, p, _ = _pick(booster, it) if it is not None else (None, 0.0, None)
            oof["break2"].append((t, t.hit(bar, t.labels["break2"]), p))

        tr_items = _positive(_rows(tracks[i], "outro_start", None, tracks[i].labels["outro_start"])
                             for i in tr if tracks[i].labels["outro_start"] is not None)
        va_idx = [int(i) for i in va if tracks[i].labels["outro_start"] is not None]
        va_items = {i: _rows(tracks[i], "outro_start", None, tracks[i].labels["outro_start"]) for i in va_idx}
        booster = _train(tr_items, _positive(va_items.values()))
        if booster is not None:
            best_iters["outro_start"].append(boost.best_iteration(booster, N_ROUNDS))
        if tr_items:
            feature_names["outro_start"] = tr_items[0][4]
        for i in va_idx:
            t = tracks[i]
            truth = t.labels["outro_start"]
            level = level_after(t.rms, t.c)
            loud = [b for b in chain_window("outro_start", t.n, t.c) if level[b] >= OUTRO_MIN_LEVEL]
            it = va_items.get(i)
            bar, p, _ = _pick(booster, it, allowed=set(loud)) if it is not None else (None, 0.0, None)
            oof["outro_start"].append((t, t.hit(bar, truth), p))
            rule = min(loud, key=lambda b: (abs(b - (t.c + 16)), b)) if loud else None
            rule_results.append((t, t.hit(rule, truth)))
        log(f"  Faltung {fold}/{n_folds} Break 1 / Break 2 / Outro: {time.time() - t0:.0f}s")

    log(f"\nOut-of-Fold Kette ({n_folds} Faltungen, Treffer = ±{HIT_BEATS:g} Beats, {time.time() - t_start:.0f}s):")
    summary = {}
    for role, title in (("drop1", "Drop 1 (Modell)"), ("break1", "Break 1 (Kette)"),
                        ("break2", "Break 2 (Modell)"), ("outro_start", "Outro (Modell)")):
        summary[role] = _report(title, [(t, h) for t, h, _ in oof[role]], log)
    summary["outro_rule"] = _report("Outro (Regel +16 Takte)", rule_results, log)
    outro_mode = ("model" if summary["outro_start"].get("hit", 0) > summary["outro_rule"].get("hit", 0)
                  + OUTRO_MIN_GAIN else "rule")
    log(f"  -> Outro zur Laufzeit per {'Modell' if outro_mode == 'model' else 'Regel'}")

    calibrators = {}
    for role in CHAIN_FILES:
        found = [(p, h) for _, h, p in oof[role] if p > 0]
        if len(found) >= 50:
            calibrators[role] = boost.Isotonic.fit([f[0] for f in found], [f[1] for f in found])

    if save:
        os.makedirs(out_dir, exist_ok=True)
        log("\nEndmodelle der Kette auf allen Tracks:")
        idx = range(len(tracks))
        sets = {
            "drop1": [_rows(tracks[i], "drop1", None, tracks[i].labels["drop1"]) for i in idx],
            "break1": [_rows(tracks[i], "break1", d1_train[i], tracks[i].a)
                       for i in idx if i in d1_train and tracks[i].a is not None],
            "break2": [_rows(tracks[i], "break2", tracks[i].labels["drop1"], tracks[i].labels["break2"])
                       for i in idx if tracks[i].labels["break2"] is not None],
            "outro_start": [_rows(tracks[i], "outro_start", None, tracks[i].labels["outro_start"])
                            for i in idx if tracks[i].labels["outro_start"] is not None],
        }
        for role, items in sets.items():
            items = _positive(items)
            if not items or not best_iters[role]:
                log(f"  {role:<12} keine Beispiele -- kein Modell")
                feature_names.pop(role, None)
                continue
            rounds = max(50, int(np.median(best_iters[role])))
            t0 = time.time()
            booster = _train(items, rounds=rounds)
            boost.save(booster, os.path.join(out_dir, CHAIN_FILES[role]))
            log(f"  {role:<12} {len(items)} Tracks, {rounds} Runden, {time.time() - t0:.0f}s")

    meta = {"feature_version": CHAIN_FEATURE_VERSION, "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_tracks": len(tracks), "labels": n_lab, "feature_names": feature_names,
            "best_iters": best_iters, "oof": summary, "outro_mode": outro_mode,
            "outro_rule_hit": summary["outro_rule"].get("hit", 0.8), "break1_mode": BREAK1_MODE,
            "params": LGB_PARAMS}
    return {"meta": meta, "calibration": {r: c.to_json() for r, c in calibrators.items()}, "summary": summary}
