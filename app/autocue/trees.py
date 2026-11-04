"""
LightGBM-Baeume ohne LightGBM auswerten.

Die Ankermodelle des Autocue-Projekts sind binaere LightGBM-Klassifikatoren
(Textformat .lgb). LightGBM selbst braeuchte unter macOS libomp und waere eine
weitere native Abhaengigkeit im Bundle -- fuer reine Vorhersage reicht eine
Handvoll numpy: je Baum Merkmal, Schwelle und Kinder je Knoten, am Ende die
Summe der Blattwerte durch die Sigmoidfunktion.

Unterstuetzt genau das, was die Modelle benutzen (beim Einlesen geprueft):
numerische Splits, keine Kategorien, keine linearen Baeume. Fehlende Werte gibt
es nicht -- die Merkmalsmatrix laeuft vorher durch nan_to_num (learned.py), und
decision_type ist ueberall 2 (default_left, missing_type None). Damit gilt
LightGBMs NumericalDecision unveraendert: x <= Schwelle -> links.

parse_text() liest ein LightGBM-Textmodell (nur das Training, training/boost.py),
load() die daraus erzeugte .npz (Laufzeit, ein Bruchteil der Ladezeit).
"""
from __future__ import annotations

import numpy as np


# kZeroThreshold aus LightGBM (1e-35f, als double gelesen).
_ZERO_THRESHOLD = float(np.float32(1e-35))


class UnsupportedModel(ValueError):
    pass


def parse_text(text: str) -> dict:
    """LightGBM-Textmodell -> gepackte Arrays (siehe pack())."""
    header, _, rest = text.partition("\nTree=")
    head = dict(line.split("=", 1) for line in header.splitlines() if "=" in line)
    objective = head.get("objective", "")
    if not objective.startswith("binary"):
        raise UnsupportedModel(f"Zielfunktion {objective!r} nicht unterstuetzt")
    sigmoid = 1.0
    for part in objective.split():
        if part.startswith("sigmoid:"):
            sigmoid = float(part.split(":", 1)[1])
    if int(head.get("num_tree_per_iteration", 1)) != 1:
        raise UnsupportedModel("mehrere Baeume je Iteration")
    body = ("Tree=" + rest).split("\nend of trees", 1)[0]
    trees = []
    for block in body.split("\nTree="):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if int(kv.get("num_cat", 0)) != 0:
            raise UnsupportedModel("kategorische Splits")
        if int(kv.get("is_linear", 0)) != 0:
            raise UnsupportedModel("lineare Baeume")
        # Die Blattwerte enthalten die Schrittweite (shrinkage) bereits.
        leaves = np.array(kv["leaf_value"].split(), dtype=np.float64)
        if int(kv["num_leaves"]) <= 1:
            trees.append((np.zeros(0, int), np.zeros(0), np.zeros(0, int), np.zeros(0, int), leaves))
            continue
        dt = np.array(kv["decision_type"].split(), dtype=int)
        if np.any(dt & 1):
            raise UnsupportedModel("kategorische Splits")
        missing = (dt >> 2) & 3
        if np.any(missing != 0):
            raise UnsupportedModel("Splits mit Fehlwert-Behandlung")
        trees.append((np.array(kv["split_feature"].split(), dtype=int),
                      np.array(kv["threshold"].split(), dtype=np.float64),
                      np.array(kv["left_child"].split(), dtype=int),
                      np.array(kv["right_child"].split(), dtype=int),
                      leaves))
    return pack(trees, sigmoid, int(head.get("max_feature_idx", -1)) + 1)


def pack(trees: list, sigmoid: float, n_features: int) -> dict:
    """Alle Baeume in rechteckige Arrays; Kinder < 0 sind Blaetter (~Index)."""
    n = len(trees)
    max_nodes = max(1, max(len(t[0]) for t in trees))
    max_leaves = max(len(t[4]) for t in trees)
    feat = np.zeros((n, max_nodes), np.int32)
    thr = np.zeros((n, max_nodes), np.float64)
    left = np.full((n, max_nodes), -1, np.int32)
    right = np.full((n, max_nodes), -1, np.int32)
    leaf = np.zeros((n, max_leaves), np.float64)
    root = np.zeros(n, np.int32)
    for i, (f, t, lc, rc, lv) in enumerate(trees):
        k = len(f)
        feat[i, :k], thr[i, :k], left[i, :k], right[i, :k] = f, t, lc, rc
        leaf[i, :len(lv)] = lv
        root[i] = 0 if k else -1             # Baum ohne Split: gleich Blatt 0
    return {"feat": feat, "thr": thr, "left": left, "right": right, "leaf": leaf,
            "root": root, "sigmoid": np.float64(sigmoid), "n_features": np.int32(n_features)}


def save(model: dict, path: str) -> None:
    np.savez_compressed(path, **model)


def load(path: str) -> dict:
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def raw_score(model: dict, X: np.ndarray) -> np.ndarray:
    """Summe der Blattwerte je Zeile -- alle Baeume gleichzeitig, Ebene fuer Ebene."""
    X = np.array(X, dtype=np.float64)
    # LightGBM uebernimmt beim Einlesen nur Werte mit |x| > kZeroThreshold, der
    # Rest zaehlt als 0 -- sonst landet x = -1e-35 an einer Schwelle -1e-35 links.
    X[np.abs(X) <= _ZERO_THRESHOLD] = 0.0
    if X.ndim != 2 or X.shape[1] < int(model["n_features"]):
        raise ValueError(f"Merkmalsmatrix passt nicht zum Modell: {X.shape}")
    n_rows = X.shape[0]
    feat, thr, left, right, leaf = model["feat"], model["thr"], model["left"], model["right"], model["leaf"]
    n_trees = feat.shape[0]
    t_idx = np.arange(n_trees)[:, None]
    r_idx = np.arange(n_rows)[None, :]
    node = np.repeat(model["root"][:, None], n_rows, axis=1)
    active = node >= 0
    while active.any():
        cur = np.where(active, node, 0)
        f = feat[t_idx, cur]
        v = X[r_idx, f]
        go_left = v <= thr[t_idx, cur]
        nxt = np.where(go_left, left[t_idx, cur], right[t_idx, cur])
        node = np.where(active, nxt, node)
        active = node >= 0
    return leaf[t_idx, ~node].sum(axis=0)


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    """Wahrscheinlichkeit der Klasse 1, wie Booster.predict() bei 'binary'."""
    return 1.0 / (1.0 + np.exp(-float(model["sigmoid"]) * raw_score(model, X)))
