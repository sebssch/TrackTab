"""
Wellenform aus TrackTabs eigener Messung: je 150 Spalten/s mit vier Bytes im Stil von
Rekordbox' Analysedaten, RGB als (r, g, b, Hoehe), 3-Band als (tief, mitte, hoch, 0).
Fuer den DJ-Editor direkt, fuer Bearbeiten und Player als Gesamtansicht (overview()).

Die Rechnung kostet rund eine Sekunde. Deshalb ein kleiner Zwischenspeicher im Prozess,
die Gesamtansicht liegt zusaetzlich in der DB (Tabelle 'waveform_color').
"""
from __future__ import annotations

import subprocess
import threading
from collections import OrderedDict

import numpy as np

from . import media

_SR = 22050
_CACHE_MAX = 8

# Die Werte sind an Rekordbox' PWV5/PWV7 von 5 Tracks angeglichen. So sieht ein Track
# ohne Rekordbox-Analyse aus wie einer mit, ein Wechsel der Quelle aendert kaum etwas.
STYLED_RATE = 150
OVERVIEW_COLUMNS = 1200
_STYLED_HOP = _SR // STYLED_RATE
_STYLED_FRAME = 512
_STYLED_LOW_HZ, _STYLED_HIGH_HZ = 150.0, 2500.0
_HEIGHT_FIT = (22.47, -1.41, 0.2)       # Spitze, RMS, Versatz -> 0..31
_RGB_FIT = ((5.07, -1.14, -1.81, 2.21), (-0.02, 4.08, -0.44, 0.74), (-0.13, 2.37, 4.12, 1.96))
_BAND_FIT = ((12.008, 0.4), (7.146, 0.5), (2.592, 0.8))

_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()


def _decode(path: str) -> np.ndarray:
    out = subprocess.run(
        [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
         "-af", "aformat=sample_fmts=fltp:channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1",
         "-ar", str(_SR), "-f", "f32le", "-"],
        capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    return np.frombuffer(out.stdout, dtype="<f4")


def _styled(x: np.ndarray) -> dict:
    """{"rgb": Bytes, "3band": Bytes}, Format siehe STYLED_RATE."""
    n = len(x) // _STYLED_HOP
    if n < 1:
        return {"rgb": b"", "3band": b""}
    frames = x[:n * _STYLED_HOP].reshape(n, _STYLED_HOP)
    peak = np.abs(frames).max(axis=1)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    hz = np.fft.rfftfreq(_STYLED_FRAME, 1.0 / _SR)
    low_b, high_b = np.searchsorted(hz, _STYLED_LOW_HZ), np.searchsorted(hz, _STYLED_HIGH_HZ)
    win = np.hanning(_STYLED_FRAME)
    padded = np.concatenate([np.zeros(_STYLED_FRAME // 2, dtype=x.dtype), x, np.zeros(_STYLED_FRAME, dtype=x.dtype)])
    energy = np.zeros((n, 3))
    for a in range(0, n, 4096):
        rows = np.arange(a, min(n, a + 4096))
        idx = rows[:, None] * _STYLED_HOP + np.arange(_STYLED_FRAME)
        power = np.abs(np.fft.rfft(padded[idx] * win, axis=1)) ** 2
        energy[rows, 0] = np.sqrt(power[:, :low_b].sum(axis=1))
        energy[rows, 1] = np.sqrt(power[:, low_b:high_b].sum(axis=1))
        energy[rows, 2] = np.sqrt(power[:, high_b:].sum(axis=1))
    height = np.clip(_HEIGHT_FIT[0] * peak + _HEIGHT_FIT[1] * rms + _HEIGHT_FIT[2], 0, 31)
    share = energy / np.maximum(energy.max(axis=1, keepdims=True), 1e-6)
    x_rgb = np.c_[share, np.ones(n)]
    rgb = np.empty((n, 4), dtype=np.uint8)
    for k, fit in enumerate(_RGB_FIT):
        rgb[:, k] = (np.clip(x_rgb @ np.array(fit), 0, 7) * 255 / 7).astype(np.uint8)
    rgb[:, 3] = (height * 255 / 31).astype(np.uint8)
    band = np.zeros((n, 4), dtype=np.uint8)
    for k, (scale, gamma) in enumerate(_BAND_FIT):
        level = scale * np.power(np.maximum(energy[:, k], 1e-6), gamma)
        band[:, k] = (np.clip(level, 0, 126) * 255 / 126).astype(np.uint8)
    return {"rgb": rgb.tobytes(), "3band": band.tobytes()}


def styled(path: str, mtime: float, style: str, lead_fn=None) -> bytes:
    """Alle Spalten, style = "rgb" oder "3band"."""
    return _entry(path, mtime, lead_fn)[1][style]


def overview(path: str, mtime: float, style: str) -> bytes:
    """Gesamtansicht fuer Bearbeiten und Player, hoechstens OVERVIEW_COLUMNS Spalten:
    je Abschnitt der Mittelwert. Das Maximum waere bei gemasterten Tracks fast ueberall
    volle Hoehe, die Abschnitte des Tracks waeren nicht mehr zu erkennen."""
    raw = np.frombuffer(styled(path, mtime, style), dtype=np.uint8).reshape(-1, 4)
    n = len(raw)
    if n <= OVERVIEW_COLUMNS:
        return raw.tobytes()
    edges = np.linspace(0, n, OVERVIEW_COLUMNS + 1).astype(int)
    out = np.empty((OVERVIEW_COLUMNS, 4), dtype=np.uint8)
    for i in range(OVERVIEW_COLUMNS):
        out[i] = raw[edges[i]:edges[i + 1]].mean(axis=0)
    return out.tobytes()


def lead(path: str, mtime: float, lead_fn) -> float:
    """Versatz zu Rekordbox in s. lead_fn(mono, sr) -> Samples misst ihn, solange die
    Dekodierung ohnehin im Speicher liegt."""
    return _entry(path, mtime, lead_fn)[0]


def _entry(path: str, mtime: float, lead_fn) -> tuple:
    key = (path, mtime, lead_fn is not None)
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    x = _decode(path)
    offset = float(lead_fn(x, _SR)) / _SR if lead_fn is not None else 0.0
    result = (offset, _styled(x))
    with _lock:
        _cache[key] = result
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def forget(path: str) -> None:
    """Wirft die Eintraege eines Pfads aus dem Zwischenspeicher."""
    with _lock:
        for key in [k for k in _cache if k[0] == path]:
            del _cache[key]
