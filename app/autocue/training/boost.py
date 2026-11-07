"""
Werkzeug fuers Training: LightGBM, isotonische Kalibrierung und Faltungen.

LightGBM ist die einzige zusaetzliche Abhaengigkeit des Trainings und wird nur
hier importiert -- Auto Cue selbst wertet die Baeume ohne LightGBM aus
(app/autocue/trees.py). Im Bundle ist es enthalten, damit die App trainieren kann
(Einstellungen -> Auto Cue -> Training), geladen wird es aber erst beim Training. scikit-learn brauchte das Original nur fuer zwei
Kleinigkeiten, die hier in numpy stehen:

  Isotonic   sklearn IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip"):
             gleiche Einheiten zusammenfassen, Pool-Adjacent-Violators, Stuetz-
             stellen ohne ueberfluessige Zwischenpunkte. Vorhersage wie zur
             Laufzeit (learned._Isotonic): np.interp mit Clip.
  folds()    Aufteilung nach Tracks, nie nach Takten.
"""
from __future__ import annotations

import os

import numpy as np

from app.autocue import trees


class MissingLightGBM(RuntimeError):
    pass


def lightgbm():
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as exc:          # OSError: libomp nicht ladbar
        raise MissingLightGBM(
            "LightGBM fehlt oder laesst sich nicht laden -- im Entwicklungsordner: "
            ".venv/bin/pip install -r requirements.txt") from exc
    return lgb


_AVAILABLE: bool | None = None


def available() -> bool:
    """Laesst sich LightGBM hier wirklich laden? Echter Import (rund 0,1 s, einmal je
    Prozess): im Bundle haengt das an libomp und scipy, deren Builds ein
    Mindest-macOS haben, das ueber dem der App liegen kann. Dann gibt es eben kein
    Training, der Rest der App laeuft weiter."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            lightgbm()
            _AVAILABLE = True
        except Exception:                          # noqa: BLE001 -- jede Ladeursache heisst: nicht verfuegbar
            _AVAILABLE = False
    return _AVAILABLE


def threads() -> int:
    return max(1, (os.cpu_count() or 4) - 1)


def train(params: dict, X, y, w, rounds: int, early_stopping: int = 0, val=None):
    """Ein Booster; val = (X, y, w) fuer das Abbruchkriterium (nur mit Positiven sinnvoll)."""
    lgb = lightgbm()
    dtrain = lgb.Dataset(X, label=y, weight=w, free_raw_data=True)
    kwargs = {}
    if val is not None and early_stopping and float(np.sum(val[1])) > 0:
        dval = lgb.Dataset(val[0], label=val[1], weight=val[2], reference=dtrain)
        kwargs = {"valid_sets": [dval], "callbacks": [lgb.early_stopping(early_stopping, verbose=False)]}
    return lgb.train({**params, "num_threads": threads()}, dtrain, num_boost_round=rounds, **kwargs)


def best_iteration(booster, default: int) -> int:
    return int(booster.best_iteration or default)


def to_packed(booster) -> dict:
    """Booster -> gepackte Baeume (trees.pack()), wie sie die App laedt."""
    return trees.parse_text(booster.model_to_string())


def save(booster, path: str) -> dict:
    """Als .npz speichern und gegen Booster.predict() pruefen, auch an exakten
    Schwellenwerten (Gleichstand x == Schwelle). Rueckgabe: das gepackte Modell."""
    model = to_packed(booster)
    rng = np.random.default_rng(1)
    n = int(model["n_features"])
    X = rng.normal(0.4, 0.6, size=(800, n)).astype(np.float32)
    X[:, : n // 3] *= 60
    thr, cols = model["thr"][model["thr"] != 0], model["feat"][model["thr"] != 0]
    if len(thr):
        pick = rng.choice(len(thr), size=min(400, len(thr)), replace=False)
        X[np.arange(len(pick)) % len(X), cols[pick]] = thr[pick].astype(np.float32)
    diff = float(np.abs(trees.predict(model, X) - booster.predict(X)).max())
    if diff > 1e-9:
        raise RuntimeError(f"{os.path.basename(path)}: gepackte Baeume weichen von LightGBM ab ({diff:.1e})")
    trees.save(model, path)
    return model


class Isotonic:
    """Monoton steigende Abbildung rohe Wahrscheinlichkeit -> Trefferquote."""

    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.x, self.y = x, y

    @classmethod
    def fit(cls, p, hit) -> "Isotonic":
        p = np.asarray(p, dtype=np.float64)
        hit = np.asarray(hit, dtype=np.float64)
        order = np.lexsort((hit, p))
        p, hit = p[order], hit[order]
        # Gleiche x zusammenfassen (gewichtetes Mittel), wie sklearn _make_unique.
        ux, start = np.unique(p, return_index=True)
        counts = np.diff(np.append(start, len(p))).astype(np.float64)
        uy = np.add.reduceat(hit, start) / counts
        y = _pav(uy, counts)
        y = np.clip(y, 0.0, 1.0)
        x = ux
        if len(y) > 2:
            keep = np.ones(len(y), bool)
            keep[1:-1] = (y[1:-1] != y[:-2]) | (y[1:-1] != y[2:])
            x, y = x[keep], y[keep]
        return cls(x, y)

    def predict(self, values) -> np.ndarray:
        v = np.clip(np.asarray(values, dtype=np.float64), self.x[0], self.x[-1])
        return np.interp(v, self.x, self.y)

    def to_json(self) -> dict:
        return {"x": [float(v) for v in self.x], "y": [float(v) for v in self.y]}


def _pav(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Pool Adjacent Violators, steigend, gewichtet."""
    vals, wts, sizes = [], [], []
    for yi, wi in zip(y, w):
        vals.append(float(yi)); wts.append(float(wi)); sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            wsum = wts[-2] + wts[-1]
            merged = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / wsum
            size = sizes[-2] + sizes[-1]
            vals[-2:], wts[-2:], sizes[-2:] = [merged], [wsum], [size]
    return np.repeat(np.asarray(vals), sizes)


def folds(n: int, k: int, seed: int | None = None) -> list[tuple[np.ndarray, np.ndarray]]:
    """(Training, Test) je Faltung ueber n Tracks. seed: vorher mischen (KFold mit
    shuffle), sonst Reihenfolge wie gegeben, Track i in Faltung i mod k."""
    idx = np.random.default_rng(seed).permutation(n) if seed is not None else np.arange(n)
    if seed is None:
        parts = [idx[i::k] for i in range(k)]
    else:
        sizes = np.full(k, n // k)
        sizes[: n % k] += 1
        bounds = np.cumsum(np.concatenate([[0], sizes]))
        parts = [idx[bounds[i]:bounds[i + 1]] for i in range(k)]
    out = []
    for i in range(k):
        test = np.sort(parts[i])
        rest = np.sort(np.concatenate([parts[j] for j in range(k) if j != i]))
        out.append((rest, test))
    return out
