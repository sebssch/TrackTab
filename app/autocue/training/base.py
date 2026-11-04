"""
Grundmodelle: welcher Takt ist Hot C (drop2), welcher Hot A (break1)?

Nachbau von tools/train_bar_model.py des Autocue-Projekts, Parameter unveraendert.
Gemessen wird nicht AUC oder Logloss, sondern das, was zaehlt: nach der
Decodierung (bester Takt je Track, learned.decode()) -- wie oft liegt er innerhalb
von +-2 Beats an der Hand-Cue? Faltung nach Tracks, nie nach Takten (Takte
desselben Tracks in Training UND Test waeren Betrug). Die Kalibrierung bildet die
rohe Wahrscheinlichkeit des gewaehlten Takts auf die tatsaechliche Trefferquote ab
(isotonisch, auf Out-of-Fold-Vorhersagen): Konfidenz 0,8 heisst dann wirklich, in
vier von fuenf Faellen sitzt der Cue.

Schreibt in out_dir: drop2.npz, break1.npz; Kalibrierung und Metadaten gibt
train() zurueck (run.py fuegt sie mit denen der Kette zusammen).
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

from app.autocue.learned import (EVIDENCE_COLS, FEATURE_VERSION, HOT_KIND_FOR_ROLE, ROLES,
                                 build_design_matrix, decode)
from app.autocue.training import boost

HIT_BEATS = 2.0
NEAR_BEATS = 8.0
# Nachbartakte der Hand-Cue sind NEGATIV (ein Takt daneben = 4 Beats daneben), aber
# mit kleinem Gewicht: Hand-Cues sitzen nicht immer exakt.
NEIGHBOUR_WEIGHT = 0.25

LGB_PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "max_bin": 63,
    "scale_pos_weight": 20.0,
    "verbose": -1,
}
N_ROUNDS = 1500
EARLY_STOPPING = 80


class Track:
    __slots__ = ("key", "X", "beats", "bpm", "targets", "n")

    def __init__(self, key, X, beats, bpm, targets):
        self.key, self.X, self.beats, self.bpm, self.targets = key, X, beats, bpm, targets
        self.n = int(X.shape[0])


def load_tracks(files: list[str], log=print) -> tuple[list[Track], list[str]]:
    tracks: list[Track] = []
    feature_names: list[str] | None = None
    t0 = time.time()
    for i, path in enumerate(files):
        try:
            with np.load(path) as d:
                names = json.loads(str(d["names"]))
                curves = {k: d[k] for k in EVIDENCE_COLS}
                X, fn = build_design_matrix(d["matrix"], names, curves, d["bar_start_beats"], float(d["bpm"]))
                if feature_names is None:
                    feature_names = fn
                elif fn != feature_names:
                    continue                       # andere Merkmalsspalten passen nicht dazu
                kinds, bars = d["hot_kinds"].tolist(), d["hot_bars"].tolist()
                targets = {role: int(bars[kinds.index(kind)]) for role, kind in HOT_KIND_FOR_ROLE.items()
                           if kind in kinds}
                if len(targets) < len(ROLES):
                    continue
                tracks.append(Track(os.path.basename(path), X, d["bar_start_beats"].astype(np.int64),
                                    float(d["bpm"]), targets))
        except Exception as exc:                   # noqa: BLE001
            log(f"  uebersprungen {os.path.basename(path)}: {type(exc).__name__}: {exc}")
        if (i + 1) % 500 == 0:
            log(f"  {i + 1}/{len(files)} geladen ({time.time() - t0:.0f}s)")
    return tracks, feature_names or []


def stack(tracks: list[Track], role: str):
    Xs, ys, ws = [], [], []
    for t in tracks:
        y = np.zeros(t.n, np.float32)
        w = np.ones(t.n, np.float32)
        tb = t.targets[role]
        y[tb] = 1.0
        for nb in (tb - 1, tb + 1):
            if 0 <= nb < t.n:
                w[nb] = NEIGHBOUR_WEIGHT
        Xs.append(t.X); ys.append(y); ws.append(w)
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(ws)


def _beats_off(track: Track, role: str, bar: int) -> float:
    return float(abs(track.beats[bar] - track.beats[track.targets[role]]))


def evaluate(tracks: list[Track], curves_per_track: list[dict], calibrators: dict | None = None):
    rows: list[dict] = []
    for t, curves in zip(tracks, curves_per_track):
        dec = decode(curves, t.n, calibrators)
        row = {"key": t.key}
        for role in ROLES:
            d = dec.get(role)
            if d is None:
                row[role] = None
                continue
            off = _beats_off(t, role, d.bar)
            row[role] = {"bar": d.bar, "prob": d.prob, "conf": d.confidence, "off": off,
                         "hit": off <= HIT_BEATS, "near": off <= NEAR_BEATS,
                         "raw_off": _beats_off(t, role, int(np.argmax(curves[role])))}
        rows.append(row)
    summary = {}
    for role in ROLES:
        found = [r[role] for r in rows if r[role] is not None]
        if not found:
            summary[role] = {"n": 0}
            continue
        offs = np.array([f["off"] for f in found])
        summary[role] = {
            "n": len(found), "found": len(found) / len(rows),
            "hit2": float(np.mean(offs <= HIT_BEATS)), "hit8": float(np.mean(offs <= NEAR_BEATS)),
            "median_off": float(np.median(offs)), "mae": float(np.mean(offs)),
            "raw_hit2": float(np.mean([f["raw_off"] <= HIT_BEATS for f in found])),
            "phrase_multiple": float(np.mean([(o > HIT_BEATS) and (abs(o) % 32 <= 2 or abs(o) % 32 >= 30)
                                              for o in offs])),
        }
    return summary, rows


def print_summary(title: str, summary: dict, log=print) -> None:
    log(f"\n{title}")
    log(f"  {'Rolle':<8}{'n':>6}{'gefunden':>10}{'+-2 Beats':>11}{'+-8 Beats':>11}"
        f"{'Median':>8}{'MAE':>7}{'roh +-2':>9}{'32er-Fehler':>13}")
    for role in ROLES:
        s = summary[role]
        if s.get("n", 0) == 0:
            log(f"  {role:<8}{0:>6}")
            continue
        log(f"  {role:<8}{s['n']:>6}{s['found']:>9.0%}{s['hit2']:>10.1%}{s['hit8']:>10.1%}"
            f"{s['median_off']:>8.1f}{s['mae']:>7.1f}{s['raw_hit2']:>9.1%}{s['phrase_multiple']:>12.0%}")


def _predict_tracks(booster, tracks: list[Track]) -> list[np.ndarray]:
    p = booster.predict(np.concatenate([t.X for t in tracks]))
    out, pos = [], 0
    for t in tracks:
        out.append(np.asarray(p[pos:pos + t.n])); pos += t.n
    return out


def train(files: list[str], out_dir: str, n_folds: int = 5, seed: int = 42, save: bool = True,
          log=print, min_tracks: int = 20) -> dict:
    """Kreuzvalidierung, Kalibrierung, Endmodelle. Rueckgabe {meta, calibration, summary}."""
    log(f"Grundmodelle: lade {len(files)} Tracks ...")
    tracks, feature_names = load_tracks(files, log)
    n_rows = sum(t.n for t in tracks)
    log(f"{len(tracks)} Tracks, {n_rows} Takte, {len(feature_names)} Merkmale je Takt")
    if len(tracks) < min_tracks:
        raise RuntimeError(f"zu wenige Tracks fuer die Grundmodelle ({len(tracks)})")
    order = np.random.default_rng(seed).permutation(len(tracks))
    tracks = [tracks[i] for i in order]

    oof_curves: list[dict] = [dict() for _ in tracks]
    best_iters: dict[str, list[int]] = {r: [] for r in ROLES}
    t_start = time.time()
    for fold, (tr_idx, va_idx) in enumerate(boost.folds(len(tracks), n_folds), 1):
        tr = [tracks[i] for i in tr_idx]
        va = [tracks[i] for i in va_idx]
        for role in ROLES:
            t0 = time.time()
            booster = boost.train(LGB_PARAMS, *stack(tr, role), N_ROUNDS, EARLY_STOPPING, stack(va, role))
            best_iters[role].append(boost.best_iteration(booster, N_ROUNDS))
            for i, curve in zip(va_idx, _predict_tracks(booster, va)):
                oof_curves[i][role] = curve
            log(f"  Faltung {fold}/{n_folds} {role}: {booster.best_iteration} Runden, {time.time() - t0:.0f}s")

    summary, rows = evaluate(tracks, oof_curves)
    print_summary(f"Out-of-Fold ({n_folds} Faltungen, {len(tracks)} Tracks, {time.time() - t_start:.0f}s)",
                  summary, log)

    calibrators: dict = {}
    for role in ROLES:
        found = [r[role] for r in rows if r[role] is not None]
        if len(found) >= 50:
            calibrators[role] = boost.Isotonic.fit([f["prob"] for f in found], [f["hit"] for f in found])
    summary_cal, rows_cal = evaluate(tracks, oof_curves, calibrators)
    log("\nKalibrierung (Out-of-Fold): Konfidenz -> Trefferquote")
    for role in ROLES:
        found = [r[role] for r in rows_cal if r[role] is not None]
        line = []
        for lo, hi in ((0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)):
            sel = [f for f in found if lo <= f["conf"] < hi]
            if sel:
                line.append(f"{lo:.1f}-{min(hi, 1.0):.1f}: {np.mean([f['hit'] for f in sel]):4.0%} (n={len(sel)})")
        log(f"  {role:<8}" + "   ".join(line))

    meta = {"feature_version": FEATURE_VERSION, "feature_names": feature_names, "roles": list(ROLES),
            "n_tracks": len(tracks), "n_rows": n_rows, "folds": n_folds,
            "oof": {r: dict(summary_cal[r]) for r in ROLES},
            "params": LGB_PARAMS, "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if save:
        os.makedirs(out_dir, exist_ok=True)
        log("\nEndmodelle auf allen Tracks:")
        for role in ROLES:
            rounds = int(np.median(best_iters[role])) if best_iters[role] else N_ROUNDS
            t0 = time.time()
            booster = boost.train(LGB_PARAMS, *stack(tracks, role), rounds)
            boost.save(booster, os.path.join(out_dir, f"{role}.npz"))
            log(f"  {role}: {rounds} Runden, {time.time() - t0:.0f}s")
    return {"meta": meta, "calibration": {r: c.to_json() for r, c in calibrators.items()},
            "summary": summary_cal}
