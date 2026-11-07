"""
Gelerntes Anker-Modell: welcher Takt ist der Drop-Einschlag, welcher der Break?

Warum es das gibt: `structure.label_loglik()` bewertet jeden Takt mit
handgeschriebenen Gewichten ("laut + basshaltig + haelt an = Drop"). Gegen 145
von Hand gecuete Tracks gemessen stimmt die Gliederung bei einem Drittel gut,
bei zwei Dritteln nicht (documentation.md, M-010). "Drop" und "Break" sind aber
keine akustischen Konstanten, sondern die Cue-Konvention des DJs -- und davon
gibt es rund 4.300 Beispiele in der eigenen Library. Dieses Modul lernt daraus.

Zwei LightGBM-Klassifikatoren (hier ohne LightGBM ausgewertet, siehe trees.py),
je einer fuer `drop2` (Hot C) und `break1`
(Hot A), bekommen je Takt die Merkmale aus Schritt 3, die Evidenz aus Schritt 4
und ein Kontextfenster aus Nachbartakten. Sie liefern je Takt eine
Wahrscheinlichkeit; der Takt mit der hoechsten gewinnt. Die Konfidenz ist
diese Wahrscheinlichkeit, isotonisch kalibriert gegen die tatsaechliche
Trefferquote -- sie sagt also wirklich "so oft sitzt das".

Das Merkmalsfenster wird HIER gebaut und von Training (app/autocue/training/)
und Laufzeit (anchors.py) gemeinsam benutzt. Zwei Fassungen desselben Codes
waren in diesem Projekt schon einmal die Ursache eines stillen Fehlers.

Dahinter kommt die Anker-Kette (unten): Drop 1, Break 1, Break 2 und
Outro werden relativ zum gefundenen Drop 2 gesucht, jede Stufe in ihrem
Fenster (training/chain.py, Autocue-Doku V-020). Zur Laufzeit
fasst `predict_anchors()` beides zusammen.

Ohne Modelldateien (autocue.model_dir()) liefert `predict_anchors()` ein leeres Dict,
und anchors.py rechnet mit dem Strukturfit als Rueckfall.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import numpy as np

from app.autocue import trees
from app.autocue.types import BARS_PER_PHRASE, BarFeatures, Lattice

log = logging.getLogger("autocue.learned")

FEATURE_VERSION = 1
ROLES = ("drop2", "break1")
HOT_KIND_FOR_ROLE = {"drop2": 3, "break1": 1}

# Gesetzt von autocue.analyse() (autocue.model_dir() bzw. der Ordner einer Bewertung).
MODEL_DIR = ""

# Nachbartakte, deren Merkmale jeder Takt mitbekommt. Negativ = davor.
CONTEXT_LAGS = (-16, -8, -4, -2, -1, 1, 2, 4, 8, 16)
# Fenster fuer "danach minus davor" -- der Einschlag ist ein Sprung.
WINDOWS = (4, 8, 16)
WINDOW_COLS = ("rms", "low_rms", "melodic_rms", "percussive_rms", "centroid",
               "onset", "sub_rms", "bass_rms", "mid_rms", "high_rms", "flatness")
EVIDENCE_COLS = ("ev_phrases", "ev_tempo_change", "ev_drop_hint",
                 "ev_break_hint", "ev_build_hint")

# Hot A muss mindestens 128 Beats vor Hot B liegen, Hot B 32 Beats vor Hot C.
MIN_A_TO_C_BARS = (128 + 32) // 4


# ----------------------------------------------------------------------------
# Merkmalsfenster
# ----------------------------------------------------------------------------

def evidence_curves(evidence, n_bars: int) -> dict[str, np.ndarray]:
    """
    Die Evidenzkurven, die das Modell sehen darf.

    Training und Laufzeit muessen dasselbe sehen; deshalb stammen die Kurven
    ausschliesslich aus Phrasen und Tempowechseln.
    """
    def arr(x) -> np.ndarray:
        return np.asarray(x, dtype=np.float32) if x is not None else np.zeros(n_bars, np.float32)

    by_source = getattr(evidence, "by_source", {}) if evidence is not None else {}
    return {
        "ev_phrases": arr(by_source.get("phrases")),
        "ev_tempo_change": arr(by_source.get("tempo_change")),
        "ev_drop_hint": arr(getattr(evidence, "drop_hint", None)),
        "ev_break_hint": arr(getattr(evidence, "break_hint", None)),
        "ev_build_hint": arr(getattr(evidence, "build_hint", None)),
    }


def build_design_matrix(matrix: np.ndarray, names: list[str],
                        curves: dict[str, np.ndarray],
                        bar_start_beats: np.ndarray, bpm: float
                        ) -> tuple[np.ndarray, list[str]]:
    """
    Aus der Merkmalstabelle eines Tracks die Eingabe fuers Modell bauen.

    Args:
        matrix: (n_bars, n_features) aus extract_bar_features()
        names: Spaltennamen dazu
        curves: Evidenzkurven, siehe evidence_curves()
        bar_start_beats: Beat-Index jedes Taktanfangs (fuer die Rasterphase)
        bpm: Tempo

    Returns:
        (X, feature_names) mit X.shape == (n_bars, len(feature_names))
    """
    n = int(matrix.shape[0])
    if n == 0:
        return np.zeros((0, 0), np.float32), []

    cols: dict[str, np.ndarray] = {nm: matrix[:, i].astype(np.float32) for i, nm in enumerate(names)}
    for nm in EVIDENCE_COLS:
        c = curves.get(nm)
        cols[nm] = (np.asarray(c, np.float32) if c is not None and len(c) == n
                    else np.zeros(n, np.float32))

    base_names = sorted(cols)
    base = np.column_stack([cols[k] for k in base_names]).astype(np.float32)

    out_blocks: list[np.ndarray] = [base]
    out_names: list[str] = list(base_names)

    # --- Kontext: Merkmale der Nachbartakte, am Rand mit dem Randwert gefuellt ---
    for lag in CONTEXT_LAGS:
        if lag < 0:
            shifted = np.concatenate([np.repeat(base[:1], -lag, axis=0), base[:lag]], axis=0) \
                if n > -lag else np.repeat(base[:1], n, axis=0)
        else:
            shifted = np.concatenate([base[lag:], np.repeat(base[-1:], lag, axis=0)], axis=0) \
                if n > lag else np.repeat(base[-1:], n, axis=0)
        out_blocks.append(shifted)
        out_names.extend(f"{nm}@{lag:+d}" for nm in base_names)

    # --- Sprung: Mittel der naechsten w Takte minus Mittel der vorigen w ---
    csum = {nm: np.concatenate([[0.0], np.cumsum(cols[nm], dtype=np.float64)])
            for nm in WINDOW_COLS if nm in cols}
    idx = np.arange(n)
    for w in WINDOWS:
        for nm, cs in csum.items():
            lo = np.clip(idx - w, 0, n)
            hi = np.clip(idx + w, 0, n)
            before = (cs[idx] - cs[lo]) / np.maximum(idx - lo, 1)
            after = (cs[hi] - cs[idx]) / np.maximum(hi - idx, 1)
            out_blocks.append((after - before).astype(np.float32)[:, None])
            out_names.append(f"{nm}_jump{w}")

    # --- Lage im Track und im Raster ---
    beats = np.asarray(bar_start_beats, dtype=np.float64)
    beats = beats - beats[0] if len(beats) else beats
    phase8 = (idx % 8).astype(np.float32)
    phase16 = (idx % 16).astype(np.float32)
    phase32 = (idx % 32).astype(np.float32)
    extra = np.column_stack([
        (idx / max(n - 1, 1)).astype(np.float32),
        ((n - 1 - idx) / BARS_PER_PHRASE).astype(np.float32),
        (idx / BARS_PER_PHRASE).astype(np.float32),
        phase8, phase16, phase32,
        np.full(n, n / BARS_PER_PHRASE, np.float32),
        np.full(n, float(bpm), np.float32),
        (beats / 4.0 - idx).astype(np.float32),   # 0, ausser bei Taktwechseln/Tempoabschnitten
    ])
    out_blocks.append(extra)
    out_names.extend(["pos_rel", "phrases_to_end", "phrases_from_start",
                      "phase8", "phase16", "phase32", "n_phrases", "bpm", "beat_offset"])

    X = np.concatenate(out_blocks, axis=1).astype(np.float32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X, out_names


def design_matrix_for(features: BarFeatures, evidence, lattice: Lattice
                      ) -> tuple[np.ndarray, list[str]]:
    """Laufzeit-Variante: direkt aus den Pipeline-Objekten."""
    return build_design_matrix(
        features.matrix, list(features.names),
        evidence_curves(evidence, features.n_bars),
        lattice.bar_start_beats, lattice.bpm,
    )


# ----------------------------------------------------------------------------
# Modelle
# ----------------------------------------------------------------------------

class _Isotonic:
    """sklearn IsotonicRegression(out_of_bounds="clip").predict() aus Stuetzstellen."""

    def __init__(self, x, y):
        self.x = np.asarray(x, dtype=np.float64)
        self.y = np.asarray(y, dtype=np.float64)

    def predict(self, values):
        v = np.clip(np.asarray(values, dtype=np.float64), self.x[0], self.x[-1])
        return np.interp(v, self.x, self.y)


def _calibrators(model_dir: str, stage: str) -> dict:
    path = os.path.join(model_dir, "calibration.json")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        raw = json.load(fh).get(stage, {})
    return {role: _Isotonic(c["x"], c["y"]) for role, c in raw.items()}


def _meta(model_dir: str) -> dict | None:
    path = os.path.join(model_dir, "features.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


@dataclass
class LearnedModels:
    boosters: dict            # role -> gepackte Baeume (app.autocue.trees)
    calibrators: dict         # role -> _Isotonic | None
    feature_names: list[str]
    meta: dict

    def available(self, role: str) -> bool:
        return role in self.boosters


_MODELS: LearnedModels | None = None
_MODELS_KEY = None


def _dir_stamp(model_dir: str):
    """(Ordner, mtime von features.json): aendert sich nach jedem Training, dann wird neu geladen."""
    try:
        return model_dir, os.path.getmtime(os.path.join(model_dir, "features.json"))
    except OSError:
        return model_dir, None


def model_paths(model_dir: str = "") -> dict[str, str]:
    model_dir = model_dir or MODEL_DIR
    return {
        "features": os.path.join(model_dir, "features.json"),
        "calibration": os.path.join(model_dir, "calibration.json"),
        **{role: os.path.join(model_dir, f"{role}.npz") for role in ROLES},
    }


def load_models(model_dir: str = "", force: bool = False) -> LearnedModels | None:
    """Modelle laden, je Ordnerstand einmal. None, wenn keine da sind -- das ist kein Fehler."""
    global _MODELS, _MODELS_KEY
    model_dir = model_dir or MODEL_DIR
    if not model_dir:
        return None                         # kein Modell gewaehlt (autocue.model_dir())
    key = _dir_stamp(model_dir)
    if key == _MODELS_KEY and not force:
        return _MODELS
    _MODELS_KEY, _MODELS = key, None
    _MODELS = _build_models(model_dir)
    return _MODELS


def _build_models(model_dir: str) -> LearnedModels | None:
    paths = model_paths(model_dir)
    try:
        meta = _meta(model_dir)
        if meta is None:
            log.info("Kein gelerntes Anker-Modell in %s -- Anker kommen aus dem Strukturfit", model_dir)
            return None
        bar = meta["bar"]
        if int(bar.get("feature_version", -1)) != FEATURE_VERSION:
            log.warning("Anker-Modell passt nicht zur Merkmalsversion (%s != %s) -- wird ignoriert",
                        bar.get("feature_version"), FEATURE_VERSION)
            return None
        boosters = {role: trees.load(paths[role]) for role in ROLES if os.path.exists(paths[role])}
        if not boosters:
            return None
        models = LearnedModels(boosters=boosters, calibrators=_calibrators(model_dir, "bar"),
                               feature_names=list(bar["feature_names"]), meta=bar)
        log.info("Gelerntes Anker-Modell geladen: %s", ", ".join(sorted(boosters)))
        return models
    except Exception as exc:
        log.warning("Anker-Modell nicht ladbar (%s) -- Anker kommen aus dem Strukturfit", exc)
        return None


def predict_curves_from_matrix(X: np.ndarray, feature_names: list[str],
                               models: LearnedModels) -> dict[str, np.ndarray]:
    """Je Rolle eine Wahrscheinlichkeitskurve ueber die Takte."""
    if feature_names != models.feature_names:
        # Spalten beim Namen zuordnen, nie ueber die Position.
        pos = {nm: i for i, nm in enumerate(feature_names)}
        missing = [nm for nm in models.feature_names if nm not in pos]
        if missing:
            raise ValueError(f"Merkmale fehlen fuers Anker-Modell: {missing[:5]}")
        X = X[:, [pos[nm] for nm in models.feature_names]]
    curves = {}
    for role, booster in models.boosters.items():
        curves[role] = np.asarray(trees.predict(booster, X), dtype=np.float64)
    return curves


def predict_role_curves(features: BarFeatures, evidence, lattice: Lattice
                        ) -> dict[str, np.ndarray]:
    """Laufzeit: leeres Dict, wenn kein Modell vorliegt."""
    models = load_models()
    if models is None or features.n_bars == 0:
        return {}
    X, names = design_matrix_for(features, evidence, lattice)
    return predict_curves_from_matrix(X, names, models)


# ----------------------------------------------------------------------------
# Decodierung
# ----------------------------------------------------------------------------

@dataclass
class Decoded:
    role: str
    bar: int
    prob: float          # rohe Modellwahrscheinlichkeit des gewaehlten Takts
    margin: float        # Abstand zur zweitbesten Stelle (ausserhalb +-1 Takt)
    confidence: float    # kalibriert, wenn Kalibrierung vorliegt, sonst prob


# Unter dieser Wahrscheinlichkeit gibt es keinen Anker: Das Modell sieht dann
# nirgends im erlaubten Bereich die gesuchte Stelle (z.B. break1, wenn der Drop
# so frueh liegt, dass davor kein Platz fuer Hot A + Hot B ist).
MIN_PROB = 0.02


def _top_and_margin(curve: np.ndarray, allowed: np.ndarray) -> tuple[int, float, float] | None:
    if not allowed.any():
        return None
    masked = np.where(allowed, curve, -np.inf)
    best = int(np.argmax(masked))
    p = float(curve[best])
    if p < MIN_PROB:
        return None
    rival = masked.copy()
    rival[max(0, best - 1):best + 2] = -np.inf
    second = float(rival.max()) if np.isfinite(rival).any() else 0.0
    return best, p, p - max(second, 0.0)


def decode(curves: dict[str, np.ndarray], n_bars: int,
           calibrators: dict | None = None,
           min_a_to_c_bars: int = MIN_A_TO_C_BARS) -> dict[str, Decoded]:
    """
    Aus den Kurven je Rolle den Takt waehlen.

    drop2: der wahrscheinlichste Takt. break1: der wahrscheinlichste Takt, der
    weit genug VOR drop2 liegt (Hot A - Hot B - Hot C brauchen 128 + 32 Beats).
    """
    out: dict[str, Decoded] = {}
    calibrators = calibrators or {}

    def finish(role: str, hit: tuple[int, float, float] | None) -> None:
        if hit is None:
            return
        bar, p, margin = hit
        conf = p
        cal = calibrators.get(role)
        if cal is not None:
            try:
                conf = float(np.clip(cal.predict([p])[0], 0.0, 1.0))
            except Exception:
                conf = p
        out[role] = Decoded(role=role, bar=bar, prob=p, margin=margin, confidence=conf)

    if "drop2" in curves and len(curves["drop2"]) == n_bars:
        finish("drop2", _top_and_margin(curves["drop2"], np.ones(n_bars, bool)))

    if "break1" in curves and len(curves["break1"]) == n_bars:
        allowed = np.ones(n_bars, bool)
        if "drop2" in out:
            allowed[max(0, out["drop2"].bar - min_a_to_c_bars + 1):] = False
        finish("break1", _top_and_margin(curves["break1"], allowed))

    return out


def decode_with_models(curves: dict[str, np.ndarray], n_bars: int) -> dict[str, Decoded]:
    models = load_models()
    gap = int(models.meta.get("min_a_to_c_bars", MIN_A_TO_C_BARS)) if models else MIN_A_TO_C_BARS
    return decode(curves, n_bars, models.calibrators if models else None, gap)


# ----------------------------------------------------------------------------
# Anker-Kette: Drop 1, Break 1, Break 2, Outro relativ zu Drop 2
# ----------------------------------------------------------------------------
#
# Die beiden Modelle oben suchen ihren Takt im ganzen Track. Fuer die uebrigen
# Positionen ist das zu frei: Drop 1 sieht aus wie Drop 2, Break 2 wie Break 1.
# Deshalb wird in fester Reihenfolge gesucht -- Drop 2, dann Drop 1 davor, dann
# Break 1 vor Drop 1, Break 2 dazwischen, Outro danach --, jede Stufe nur in
# ihrem Fenster und mit dem Abstand zu den schon gefundenen Ankern als
# zusaetzlichem Merkmal. Gemessen am Gold-Set (documentation.md, V-020):
# Drop 1 43 -> 93 %, Break 2 53 -> 84..92 %, Outro 17 -> 92 %.
#
# Die Wahrheit fuer Drop 1, Break 2 und Outro sind die Memory Cues des DJs:
# Sie liegen laut seiner Festlegung immer auf einem Memory Cue.
# position_labels() bestimmt, welcher es ist -- gemeinsam fuer Training
# (tools/train_anchor_chain.py) und Messung (tools/eval/eval_golden.py).

CHAIN_FEATURE_VERSION = 1
CHAIN_ROLES = ("drop1", "break1", "break2", "outro_start")
# Modelle des Autocue-Projekts kamen ohne Outro-Modell (Modus "rule": outro_by_rule());
# das eigene Training (app/autocue/training/chain.py) schreibt es immer mit.
CHAIN_FILES = {
    "drop1": "chain_drop1.npz",
    "break1": "chain_break1.npz",
    "break2": "chain_break2.npz",
    # Nur genutzt, wenn das Training outro_mode "model" festlegt (sonst Regel).
    "outro_start": "chain_outro.npz",
}

# Outro: Kandidaten im 8-Takt-Raster nach Drop 2. Der Standard ist das Ende des
# 16-taktigen Drop-2-Chorus -- dort liegt der Hand-Cue in 93 % der Gold-Set-
# und 80 % der Bibliothekstracks. Die Rekordbox-Phrase an dieser Stelle ist mal
# Chorus, mal Down, mal Outro und taugt deshalb nicht als Kriterium.
OUTRO_CANDIDATE_BARS = (8, 16, 24, 32)
OUTRO_DEFAULT_BARS = 16
# Eigenes Training: das Outro darf ueberall nach Drop 2 liegen (Extended Mixes:
# +32, +64 ...), begrenzt nur durch den Ausklang. Die Regel nimmt den Abstand,
# den die eigenen Outro-Cues typischerweise haben (chain.learned_outro()).
OUTRO_FULL_RANGE = tuple(range(8, 129, 8))
# Ein Outro braucht mindestens so viele Takte bis zum Ausklang (tail.music_end());
# ist der Rest nach Drop 2 kuerzer, gibt es ein kurzes oder gar keins.
OUTRO_MIN_BARS = 4
# Das Outro ist nie leise: nach dem Hand-Cue liegt der Pegel im Mittel bei 102 %
# des Drop-2-Pegels, bei den leisesten 10 % noch bei 76 %. Kandidaten darunter
# sind Ausklingen oder Stille.
OUTRO_MIN_LEVEL = 0.5

# Drop 1 fruehestens ab Takt 4 und mindestens 4 Takte vor Drop 2.
DROP1_MIN_BAR = 4
DROP1_GAP_BARS = 4

# Werte, die das Training festlegt; die Messung darf sie fuer Vergleichslaeufe
# ueberschreiben (tools/eval/eval_golden.py --break1-mode / --outro-mode).
CHAIN_OVERRIDES: dict = {}


def position_labels(drop_hint, break_hint, build_hint, mem_bars, a_bar: int | None,
                    c_bar: int | None, end_bar: int | None = None,
                    outro_target: int = OUTRO_DEFAULT_BARS) -> dict[str, int | None]:
    """
    Welcher Memory Cue ist Drop 1, Break 2, Outro? Takt-Indizes oder None.

    Drop 1   der erste Chorus-Phrasenstart nach Hot A -- aber nur, wenn dort ein
             Memory Cue sitzt (sonst ist der Track mehrdeutig und zaehlt nicht).
    Break 2  der erste Down/Up-Phrasenstart nach Drop 1, ebenfalls nur mit Cue.
    Outro    der Memory Cue nach Drop 2, der +outro_target Takten (Vorgabe 16) am
             naechsten liegt, ab +4 Takten bis zum Ausklang (end_bar; ohne: +40).
             Braucht keine Phrasen.

    Die Phrasenkurven sind die aus evidence.collect_evidence() (drop_hint = 1 an
    jedem Chorus-Start, break_hint = 1 an Down, build_hint >= 0.4 an Up).
    """
    out: dict[str, int | None] = {"drop1": None, "break2": None, "outro_start": None}
    if c_bar is None:
        return out
    mem = {int(b) for b in mem_bars}

    limit = end_bar - OUTRO_MIN_BARS if end_bar is not None and end_bar > 0 else c_bar + 40
    after = sorted(m for m in mem if c_bar + 4 <= m <= limit)
    if after:
        out["outro_start"] = min(after, key=lambda m: abs(m - (c_bar + outro_target)))

    if a_bar is None or drop_hint is None or len(drop_hint) == 0:
        return out
    drop_hint = np.asarray(drop_hint)
    chorus = [int(b) for b in np.flatnonzero(drop_hint >= 0.99) if a_bar < b < c_bar - DROP1_GAP_BARS]
    if not chorus or chorus[0] not in mem:
        return out
    d1 = chorus[0]
    out["drop1"] = d1

    if break_hint is None or build_hint is None:
        return out
    turn = (np.asarray(break_hint) >= 0.99) | (np.asarray(build_hint) >= 0.39)
    cands = [int(b) for b in np.flatnonzero(turn) if d1 < b < c_bar]
    if cands and cands[0] in mem:
        out["break2"] = cands[0]
    return out


def relative_columns(n_bars: int, drop2_bar: int, drop1_bar: int | None = None
                     ) -> tuple[np.ndarray, list[str]]:
    """Abstand jedes Takts zu Drop 2 (und Drop 1): in Phrasen, Rasterphase, relativ."""
    idx = np.arange(n_bars, dtype=np.float64)
    cols = [(drop2_bar - idx) / BARS_PER_PHRASE,
            np.mod(drop2_bar - idx, BARS_PER_PHRASE),
            idx / max(drop2_bar, 1)]
    names = ["rel_c_phrases", "rel_c_phase8", "rel_c_pos"]
    if drop1_bar is not None:
        cols += [(drop1_bar - idx) / BARS_PER_PHRASE,
                 np.mod(drop1_bar - idx, BARS_PER_PHRASE),
                 idx / max(drop1_bar, 1)]
        names += ["rel_d_phrases", "rel_d_phase8", "rel_d_pos"]
    return np.column_stack(cols).astype(np.float32), names


def level_after(rms: np.ndarray, drop2_bar: int, width: int = 4) -> np.ndarray:
    """Pegel der naechsten `width` Takte relativ zu den ersten 8 Takten von Drop 2."""
    rms = np.asarray(rms, dtype=np.float64)
    n = len(rms)
    ref = float(rms[drop2_bar:min(n, drop2_bar + BARS_PER_PHRASE)].mean()) if drop2_bar < n else 0.0
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    idx = np.arange(n)
    hi = np.minimum(idx + width, n)
    mean = (csum[hi] - csum[idx]) / np.maximum(hi - idx, 1)
    return (mean / ref if ref > 1e-9 else np.ones(n)).astype(np.float32)


def chain_matrix(role: str, X: np.ndarray, base_names: list[str], drop2_bar: int,
                 drop1_bar: int | None = None, rms: np.ndarray | None = None
                 ) -> tuple[np.ndarray, list[str]]:
    """Eingabe eines Kettenmodells: Grundmatrix + Abstaende (+ Pegel beim Outro)."""
    n = X.shape[0]
    use_d1 = role in ("break1", "break2")
    rel, rel_names = relative_columns(n, drop2_bar, drop1_bar if use_d1 else None)
    blocks, names = [X, rel], list(base_names) + rel_names
    if role == "outro_start":
        lvl = level_after(rms if rms is not None else np.ones(n), drop2_bar)
        blocks.append(lvl[:, None])
        names.append("level_vs_drop2")
    return np.concatenate(blocks, axis=1).astype(np.float32), names


def chain_window(role: str, n_bars: int, drop2_bar: int, drop1_bar: int | None = None,
                 outro_bars: tuple = OUTRO_CANDIDATE_BARS, music_end: int | None = None) -> list[int]:
    """Die Takte, unter denen ein Kettenmodell waehlen darf. outro_bars: Kandidaten
    nach Drop 2 (Vorgabe; ein eigenes Training legt sie fest). music_end: Takt, ab
    dem nur noch der Ausklang laeuft -- ein Outro braucht davor OUTRO_MIN_BARS."""
    if role == "drop1":
        return list(range(DROP1_MIN_BAR, max(DROP1_MIN_BAR, drop2_bar - DROP1_GAP_BARS)))
    if role == "break1":
        return list(range(0, max(0, (drop1_bar if drop1_bar is not None else 0) - 1)))
    if role == "break2":
        if drop1_bar is None:
            return []
        return list(range(drop1_bar + 1, drop2_bar))
    if role == "outro_start":
        limit = music_end if music_end is not None and music_end > 0 else n_bars
        return [drop2_bar + int(k) for k in outro_bars
                if drop2_bar + int(k) < n_bars - 1 and drop2_bar + int(k) + OUTRO_MIN_BARS <= limit]
    raise ValueError(role)


@dataclass
class ChainModels:
    boosters: dict            # Rolle -> gepackte Baeume
    calibrators: dict         # Rolle -> _Isotonic
    feature_names: dict       # Rolle -> Spaltennamen
    meta: dict

    def setting(self, key: str, default=None):
        return CHAIN_OVERRIDES.get(key, self.meta.get(key, default))


_CHAIN: ChainModels | None = None
_CHAIN_KEY = None


def load_chain_models(model_dir: str = "", force: bool = False) -> ChainModels | None:
    """Kettenmodelle laden, je Ordnerstand einmal. None, wenn keine da sind -- dann Rueckfall."""
    global _CHAIN, _CHAIN_KEY
    model_dir = model_dir or MODEL_DIR
    if not model_dir:
        return None
    key = _dir_stamp(model_dir)
    if key == _CHAIN_KEY and not force:
        return _CHAIN
    _CHAIN_KEY, _CHAIN = key, None
    try:
        meta = (_meta(model_dir) or {}).get("chain")
        if not meta:
            log.info("Keine Anker-Kette in %s -- Drop 1, Break 2, Outro aus dem Rueckfall", model_dir)
            return None
        if int(meta.get("feature_version", -1)) != CHAIN_FEATURE_VERSION:
            log.warning("Anker-Kette passt nicht zur Merkmalsversion -- wird ignoriert")
            return None
        boosters = {}
        for role, fname in CHAIN_FILES.items():
            path = os.path.join(model_dir, fname)
            if os.path.exists(path) and role in meta.get("feature_names", {}):
                boosters[role] = trees.load(path)
        _CHAIN = ChainModels(boosters=boosters, calibrators=_calibrators(model_dir, "chain"),
                             feature_names=meta.get("feature_names", {}), meta=meta)
        log.info("Anker-Kette geladen: %s", ", ".join(sorted(boosters)) or "nur Regeln")
        return _CHAIN
    except Exception as exc:
        log.warning("Anker-Kette nicht ladbar (%s) -- Rueckfall", exc)
        return None


def _predict_chain(chain: ChainModels, role: str, X: np.ndarray, names: list[str],
                   drop2_bar: int, drop1_bar: int | None, rms) -> np.ndarray:
    Xc, cnames = chain_matrix(role, X, names, drop2_bar, drop1_bar, rms)
    want = chain.feature_names[role]
    if cnames != want:
        pos = {nm: i for i, nm in enumerate(cnames)}
        missing = [nm for nm in want if nm not in pos]
        if missing:
            raise ValueError(f"Merkmale fehlen fuers Kettenmodell {role}: {missing[:5]}")
        Xc = Xc[:, [pos[nm] for nm in want]]
    return np.asarray(trees.predict(chain.boosters[role], Xc), dtype=np.float64)


def _calibrated(chain: ChainModels | None, role: str, p: float) -> float:
    cal = chain.calibrators.get(role) if chain else None
    if cal is None:
        return float(p)
    try:
        return float(np.clip(cal.predict([p])[0], 0.0, 1.0))
    except Exception:
        return float(p)


def _pick(chain: ChainModels | None, role: str, curve: np.ndarray, window: list[int],
          scale: float = 1.0) -> Decoded | None:
    """Besten Takt im Fenster waehlen; Konfidenz kalibriert und mit `scale` verrechnet."""
    if not window:
        return None
    allowed = np.zeros(len(curve), bool)
    allowed[window] = True
    hit = _top_and_margin(curve, allowed)
    if hit is None:
        return None
    bar, p, margin = hit
    return Decoded(role=role, bar=bar, prob=p, margin=margin,
                   confidence=_calibrated(chain, role, p) * scale)


def outro_by_rule(rms, n_bars: int, drop2_bar: int, confidence: float,
                  outro_bars: tuple = OUTRO_CANDIDATE_BARS,
                  default_bars: int = OUTRO_DEFAULT_BARS, music_end: int | None = None) -> Decoded | None:
    """
    Outro ohne Modell: Ende des 16-taktigen Drop-2-Chorus.

    Faellt der Pegel dort unter OUTRO_MIN_LEVEL des Drop-2-Pegels (Ausklingen,
    Stille), wird der naechste lautere Kandidat im 8-Takt-Raster genommen --
    zuerst frueher, dann spaeter.
    """
    window = chain_window("outro_start", n_bars, drop2_bar, outro_bars=outro_bars, music_end=music_end)
    if not window:
        return None
    level = level_after(rms, drop2_bar) if rms is not None else np.ones(n_bars, np.float32)
    loud = [b for b in window if level[b] >= OUTRO_MIN_LEVEL]
    if not loud:
        return None
    default = drop2_bar + int(default_bars)
    bar = min(loud, key=lambda b: (abs(b - default), b))
    return Decoded(role="outro_start", bar=int(bar), prob=1.0, margin=0.0,
                   confidence=float(confidence))


def decode_chain(X: np.ndarray, names: list[str], rms, n_bars: int, drop2: Decoded,
                 break1_curve: np.ndarray | None = None,
                 chain: ChainModels | None = None, music_end: int | None = None) -> dict[str, Decoded]:
    """
    Drop 1, Break 1, Break 2 und Outro in fester Reihenfolge nach Drop 2.

    Jede Konfidenz wird mit der von Drop 2 multipliziert: Liegt Drop 2 falsch,
    liegen alle Fenster falsch.
    """
    chain = chain if chain is not None else load_chain_models()
    out: dict[str, Decoded] = {}
    c = int(drop2.bar)
    scale = float(drop2.confidence)

    if chain is not None and "drop1" in chain.boosters:
        curve = _predict_chain(chain, "drop1", X, names, c, None, rms)
        d1 = _pick(chain, "drop1", curve, chain_window("drop1", n_bars, c), scale)
        if d1 is not None:
            out["drop1"] = d1

    d1_bar = out["drop1"].bar if "drop1" in out else None
    if chain is not None and d1_bar is not None and "break1" in chain.boosters:
        window = chain_window("break1", n_bars, c, d1_bar)
        curve = _predict_chain(chain, "break1", X, names, c, d1_bar, rms)
        if (window and chain.setting("break1_mode", "ensemble") == "ensemble"
                and break1_curve is not None and len(break1_curve) == n_bars):
            # Beide Modelle muessen zustimmen: Der Takt kommt aus dem Produkt
            # beider Kurven, die Konfidenz aus der kalibrierten Kettenkurve.
            joint = np.log(np.clip(curve, 1e-6, 1.0)) + np.log(np.clip(break1_curve, 1e-6, 1.0))
            bar = int(window[int(np.argmax(joint[window]))])
            p = float(curve[bar])
            if p >= MIN_PROB:
                out["break1"] = Decoded(role="break1", bar=bar, prob=p, margin=0.0,
                                        confidence=_calibrated(chain, "break1", p) * scale)
        else:
            b1 = _pick(chain, "break1", curve, window, scale)
            if b1 is not None:
                out["break1"] = b1

    if chain is not None and d1_bar is not None and "break2" in chain.boosters:
        curve = _predict_chain(chain, "break2", X, names, c, d1_bar, rms)
        b2 = _pick(chain, "break2", curve, chain_window("break2", n_bars, c, d1_bar), scale)
        if b2 is not None:
            out["break2"] = b2

    outro = None
    outro_bars = tuple(chain.setting("outro_candidates", OUTRO_CANDIDATE_BARS)) if chain else OUTRO_CANDIDATE_BARS
    outro_default = int(chain.setting("outro_default", OUTRO_DEFAULT_BARS)) if chain else OUTRO_DEFAULT_BARS
    if (chain is not None and "outro_start" in chain.boosters
            and chain.setting("outro_mode", "rule") == "model"):
        curve = _predict_chain(chain, "outro_start", X, names, c, None, rms)
        level = level_after(rms, c) if rms is not None else np.ones(n_bars, np.float32)
        window = [b for b in chain_window("outro_start", n_bars, c, outro_bars=outro_bars, music_end=music_end)
                  if level[b] >= OUTRO_MIN_LEVEL]
        outro = _pick(chain, "outro_start", curve, window, scale)
    if outro is None:
        rule_rate = float(chain.meta.get("outro_rule_hit", 0.8)) if chain else 0.8
        outro = outro_by_rule(rms, n_bars, c, scale * rule_rate, outro_bars, outro_default, music_end)
    if outro is not None:
        out["outro_start"] = outro
    return out


def predict_anchors(features: BarFeatures, evidence, lattice: Lattice,
                    music_end: int | None = None) -> dict[str, Decoded]:
    """
    Laufzeit: alle gelernten Anker eines Tracks. Leeres Dict ohne Grundmodelle.

    Die Merkmalsmatrix wird einmal gebaut und von allen Modellen geteilt.
    """
    models = load_models()
    n = features.n_bars
    if models is None or n == 0:
        return {}
    X, names = design_matrix_for(features, evidence, lattice)
    curves = predict_curves_from_matrix(X, names, models)
    dec = decode(curves, n, models.calibrators,
                 int(models.meta.get("min_a_to_c_bars", MIN_A_TO_C_BARS)))
    if "drop2" in dec:
        rms = features["rms"] if features.has("rms") else None
        dec.update(decode_chain(X, names, rms, n, dec["drop2"], curves.get("break1"), music_end=music_end))
    return dec
