"""
Phrasen (Intro, Up/Verse, Down/Bridge, Chorus, Outro) aus dem Audio, ohne
Rekordbox: Beatgrid aus beatgrid.py, Bandlautstaerke je Schlag und ein kleines
Modell in vendor/models/phrases/phrases.onnx (onnxruntime). Liest nur, schreibt
weder in die Datei noch nach Rekordbox.

Das Modell bekommt je Takt 23 Werte: Lautstaerke dreier Baender (< 150 Hz,
150-2000 Hz, > 2 kHz) auf vier Schlaegen (12), deren Mittel (3), die Rolle aus
dem Lautheits-Detektor detect_blocks() (5), Position im Track (2), Tempo (1).
Es liefert je Takt Rolle und Grenze sowie eine Stimmung fuer den ganzen Track,
die die Namen waehlt (hoch: Up/Down, sonst Verse/Bridge).

Trainiert ist es auf ein festes Raster. Bei Tracks mit Tempowechsel rechnet
dieses Modul je Schlag und Takt aus der echten Beat-Liste -- das Ergebnis ist
deutlich schwaecher und gilt deshalb als unsicher ("uncertain").
"""
from __future__ import annotations

import math
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import beatgrid
from . import config as cfgmod
from . import media

# Stand der gespeicherten Analyse (Spalte phrases.version).
ANALYSIS_VERSION = "phrases-2"

_ROLES = ("intro", "up", "down", "chorus", "outro")
_HIGH = ("intro", "up", "down", "chorus", "outro")
_OTHER = ("intro", "verse", "bridge", "chorus", "outro")
_BLOCK = 0.01
_BUCKETS = 1920
_Q1, _Q2 = 0.5411961001461969, 1.3065629648763764
# Darunter ist das Starttempo selbst fraglich (an Acapellas gemessen).
_MIN_CONFIDENCE = 0.5

_session_lock = threading.Lock()
_session_cache = None


class TooLongError(RuntimeError):
    """Datei ist laenger als bpmkey_max_minutes -- bewusst nicht gemessen."""


def model_path() -> Path:
    return cfgmod.vendor_dir() / "models" / "phrases" / "phrases.onnx"


@lru_cache(maxsize=1)
def _ort():
    try:
        import onnxruntime
        return onnxruntime
    except Exception:                                  # noqa: BLE001
        return None


def available() -> bool:
    return _ort() is not None and model_path().is_file()


def _session():
    global _session_cache
    with _session_lock:
        if _session_cache is None:
            _session_cache = _ort().InferenceSession(str(model_path()), providers=["CPUExecutionProvider"])
        return _session_cache


# ---------------------------------------------------------------- Dekodieren

def _biquad(kind: str, sr: int, hz: float, q: float) -> str:
    w = 2 * math.pi * hz / sr
    c, al = math.cos(w), math.sin(w) / (2 * q)
    a0 = 1 + al
    if kind == "lp":
        h = ((1 - c) / 2) / a0
        b1 = (1 - c) / a0
    else:
        h = ((1 + c) / 2) / a0
        b1 = -(1 + c) / a0
    return f"biquad=b0={h:.17g}:b1={b1:.17g}:b2={h:.17g}:a0=1:a1={-2 * c / a0:.17g}:a2={(1 - al) / a0:.17g}:r=f64"


def _butter4(kind: str, sr: int, hz: float) -> str:
    return f"{_biquad(kind, sr, hz, _Q1)},{_biquad(kind, sr, hz, _Q2)}"


def _decode_bands(path: str) -> tuple:
    sr = beatgrid.sample_rate(path)
    fc = ("[0:a:0]aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=2[st][m0];"
          "[m0]pan=mono|c0=0.5*c0+0.5*c1,asplit=4[a][b][c][d];"
          f"[a]{_butter4('lp', sr, 150)}[sub];[b]{_butter4('hp', sr, 150)},{_butter4('lp', sr, 2000)}[mid];"
          f"[c]{_butter4('hp', sr, 2000)}[high];[d]{_butter4('lp', sr, 600)}[low]")
    names = ("st", "sub", "mid", "high", "low")
    with tempfile.TemporaryDirectory() as td:
        cmd = [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-filter_complex", fc]
        for n in names:
            cmd += ["-map", f"[{n}]", "-c:a", "pcm_f32le", "-f", "f32le", str(Path(td) / f"{n}.raw")]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return sr, {n: np.fromfile(Path(td) / f"{n}.raw", dtype=np.float32) for n in names}


def _block_means(x, frames: int):
    n = len(x) // frames
    return (x[:n * frames].astype(np.float64) ** 2).reshape(n, frames).mean(axis=1)


def _energy_per_beat(data: dict, sr: int, times: list):
    """Je Schlag drei Werte in dB, bezogen auf das 95. Perzentil des Bandes."""
    frames = max(1, int(_BLOCK * sr))
    blocks = np.stack([_block_means(data[k], frames) for k in ("sub", "mid", "high")], axis=1)
    n = len(blocks)
    track = n * _BLOCK
    out = []
    for i in range(len(times) - 1):
        t, e = times[i], times[i + 1]
        if e > track + 1e-9:
            break
        a = int(t / _BLOCK)
        z = max(a + 1, min(n, int(e / _BLOCK)))
        out.append(10.0 * np.log10(blocks[a:z].mean(axis=0) + 1e-10))
    res = np.asarray(out, dtype=np.float32).reshape(-1, 3)
    if len(res):
        p = min(len(res) - 1, int(0.95 * (len(res) - 1) + 0.5))
        res = res - np.sort(res, axis=0)[p]
    return res


def _overview(data: dict) -> tuple:
    """Lautheit und Bass (< 600 Hz) als je 1920 Werte 0..255."""
    st = data["st"]
    frames = len(st) // 2
    fpb = max(1, frames // _BUCKETS)
    idx = np.minimum(_BUCKETS - 1, np.arange(frames) // fpb)
    sq = (st[:frames * 2].astype(np.float64) ** 2).reshape(frames, 2).mean(axis=1)
    cnt = np.maximum(np.bincount(idx, minlength=_BUCKETS), 1)
    all_ = np.floor(np.minimum(1.0, np.sqrt(np.bincount(idx, sq, _BUCKETS) / cnt)) * 255.0)
    lowsq = data["low"][:frames].astype(np.float64) ** 2
    low = np.floor(np.minimum(255.0, 255.0 * np.minimum(1.0, np.sqrt(np.bincount(idx, lowsq, _BUCKETS) / cnt)) + 0.5))
    return all_, low


# ---------------------------------------------------------------- Lautheits-Detektor

def _mean_over(v, spc: float, a0: float, b0: float) -> float:
    a = min(max(int(a0 / spc), 0), len(v) - 1)
    b = min(max(int(math.ceil(b0 / spc)), a + 1), len(v))
    return float(v[a:b].mean())


def _pct(vals: list, q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def detect_blocks(all_, low, duration: float, bars: list) -> list:
    """Grobe Abschnitte aus Lautheit und Bass auf 8-Takt-Bloecken. Eingabe des
    Modells, nicht das Ergebnis: Grenzen trifft es deutlich seltener."""
    K = 8
    spc = duration / len(all_)
    ends = bars[1:] + [min(duration, bars[-1] + (bars[-1] - bars[-2] if len(bars) > 1 else 2.0))]
    be, bb = [], []
    for a, e in zip(bars, ends):
        if a + (e - a) * 0.5 >= duration:
            break
        be.append(_mean_over(all_, spc, a, min(duration, e)))
        bb.append(_mean_over(low, spc, a, min(duration, e)))
    nb = len(be)
    if nb < 3 * K:
        return []
    es, bs = max(1.0, _pct(be, .95)), max(1.0, _pct(bb, .95))

    def blocks_from(off: int) -> list:
        out: list = []
        first = off - K
        while first < nb:
            a, b = max(0, first), min(nb, first + K)
            first += K
            if b - a <= 0:
                continue
            bl = {"start": bars[a], "end": min(duration, ends[b - 1]),
                  "energy": sum(x / es for x in be[a:b]) / (b - a),
                  "bass": sum(x / bs for x in bb[a:b]) / (b - a), "bars": float(b - a)}
            if b - a <= 2 and out:
                out[-1]["end"] = bl["end"]
                out[-1]["bars"] += bl["bars"]
                continue
            out.append(bl)
        return out

    blocks: list = []
    best = -1.0
    for off in range(K):
        c = blocks_from(off)
        s = sum(abs(c[i]["energy"] - c[i - 1]["energy"]) + abs(c[i]["bass"] - c[i - 1]["bass"]) for i in range(1, len(c)))
        if s > best:
            best, blocks = s, c
    if len(blocks) < 3:
        return []
    blocks[0]["start"] = 0.0
    blocks[-1]["end"] = duration
    return _name_blocks(blocks)


def _name_blocks(blocks: list) -> list:
    dist = lambda x, y: abs(x["energy"] - y["energy"]) + 1.5 * abs(x["bass"] - y["bass"])

    def merge(i, f):
        tot = i["bars"] + f["bars"]
        i["energy"] = (i["energy"] * i["bars"] + f["energy"] * f["bars"]) / tot
        i["bass"] = (i["bass"] * i["bars"] + f["bass"] * f["bars"]) / tot
        i["start"], i["end"] = min(i["start"], f["start"]), max(i["end"], f["end"])
        i["bars"] = tot

    while len(blocks) >= 2:
        bi, bd = 0, 1e9
        for i in range(1, len(blocks)):
            d = dist(blocks[i - 1], blocks[i])
            if d < bd:
                bd, bi = d, i
        if bd >= 0.12:
            break
        merge(blocks[bi - 1], blocks[bi])
        del blocks[bi]
    changed = True
    while changed and len(blocks) > 1:
        changed = False
        for i in range(len(blocks)):
            if blocks[i]["bars"] >= 15.5:
                continue
            other = 1 if i == 0 else i - 1
            if 0 < i and i + 1 < len(blocks) and dist(blocks[i], blocks[i + 1]) < dist(blocks[i], blocks[i - 1]):
                other = i + 1
            merge(blocks[min(i, other)], blocks[max(i, other)])
            del blocks[max(i, other)]
            changed = True
            break
    if len(blocks) < 2:
        return []
    top_e, top_b = max(b["energy"] for b in blocks), max(b["bass"] for b in blocks)
    if top_b <= 0.05:
        return []
    first, last = -1, -1
    for i, b in enumerate(blocks):
        on = b["bass"] >= 0.55 * top_b
        b["kind"] = "break" if not on else ("drop" if b["energy"] >= 0.8 * top_e else "mid")
        if b["kind"] == "drop":
            first = i if first < 0 else first
            last = i
    if first < 0:
        return []
    named: list = []

    def split(b, n, f, r):
        if n <= 0 or n >= b["bars"] - 0.5:
            w = dict(b)
            w["role"] = r if n <= 0 else f
            named.append(w)
            return
        bar = (b["end"] - b["start"]) / b["bars"]
        x, y = dict(b), dict(b)
        x["end"], x["bars"], x["role"] = b["start"] + n * bar, n, f
        y["start"], y["bars"], y["role"] = x["end"], b["bars"] - n, r
        named.extend([x, y])

    for i, b in enumerate(blocks):
        if b["kind"] == "drop" and i == 0 and b["bars"] > 16:
            split(b, 8.0, "intro", "chorus")
        elif b["kind"] == "drop":
            d = dict(b)
            d["role"] = "chorus"
            named.append(d)
        elif i < first:
            split(b, min(b["bars"], 8.0 if i + 1 == first else 16.0) if i == 0 else 0.0, "intro", "up")
        elif i > last:
            split(b, max(0.0, b["bars"] - 8.0), "down", "outro")
        else:
            split(b, 16.0 if b["bars"] >= 32 else (8.0 if b["bars"] >= 16 else 0.0), "down", "up")
    if named and named[-1]["role"] == "chorus" and named[-1]["bars"] > 16:
        l = named.pop()
        split(l, l["bars"] - 8.0, "chorus", "outro")
    out: list = []
    for b in named:
        if out and out[-1]["role"] == b["role"]:
            out[-1]["end"] = b["end"]
            continue
        out.append({"start": b["start"], "end": b["end"], "role": b["role"]})
    return out


# ---------------------------------------------------------------- Modell

def _model_phrases(data: dict, sr: int, ov: tuple, duration: float, beats: list) -> tuple:
    """(Phrasen, Stimmungs-Index 0=hoch/1=mittel/2=niedrig). Beats: (Zeit, Nummer, BPM)."""
    beats = [b for b in beats if b[0] >= 0]
    if len(beats) < 64:
        return [], None
    times = [b[0] for b in beats] + [beats[-1][0] + 60.0 / max(beats[-1][2], 1.0)]
    energy = _energy_per_beat(data, sr, times)
    bar_idx = [i for i, b in enumerate(beats) if b[1] == 1]
    if not bar_idx:
        return [], None
    built = detect_blocks(ov[0], ov[1], duration, [beats[i][0] for i in bar_idx])
    starts = [i for i in bar_idx if i + 3 < len(energy)]
    n = len(starts)
    if n < 16:
        return [], None
    rows, bar_starts = [], []
    for j, s in enumerate(starts):
        e = energy[s:s + 4]
        today = [0.0] * 5
        mid = times[s + 2]
        for p in built:
            if p["start"] <= mid < p["end"]:
                today[_ROLES.index(p["role"])] = 1.0
                break
        pos = j / max(1, n - 1)
        rows.append(list(e.reshape(-1)) + list(e.mean(axis=0)) + today
                    + [pos, 1 - pos, (beats[s][2] - 98.0) / 98.0])
        bar_starts.append(beats[s][0])
    role, boundary, mood = _session().run(["role", "boundary", "mood"], {"bars": np.asarray(rows, dtype=np.float32)[None]})
    role, boundary, mood = role[0], boundary[0], mood[0]
    cuts = [0]
    for i in range(1, n):
        p = 1 / (1 + math.exp(-float(boundary[i])))
        if p > 0.5 and boundary[i] >= boundary[i - 1] and (i + 1 >= n or boundary[i] >= boundary[i + 1]):
            cuts.append(i)
    cuts.append(n)
    mi = int(np.argmax(mood))
    names = _HIGH if mi == 0 else _OTHER
    high = mi == 0
    counts = [0] * 5
    out = []
    for c in range(len(cuts) - 1):
        l = role[cuts[c]:cuts[c + 1]]
        pr = np.exp(l - l.max(axis=1, keepdims=True))
        pr /= pr.sum(axis=1, keepdims=True)
        r = int(np.argmax(pr.sum(axis=0)))
        counts[r] += 1
        # Nummern wie Rekordbox: hoch Intro 1, Up 1-3, Chorus 1-2, Outro 1;
        # sonst nur Verse 1-2.
        top = (3 if r == 1 else 2 if r == 3 else 0 if r == 2 else 1) if high else (2 if r == 1 else 0)
        # Die erste Phrase beginnt am ersten Schlag, nicht in der Stille davor.
        out.append({"start": float(beats[0][0]) if c == 0 else float(bar_starts[cuts[c]]),
                    "end": float(bar_starts[cuts[c + 1]]) if cuts[c + 1] < n else float(duration),
                    "kind": names[r], "num": min(counts[r], top) if top else 0})
    return out, mi


def _check_length(path: str, max_seconds: float) -> None:
    if not max_seconds:
        return
    try:
        from mutagen import File
        if float(File(path).info.length or 0) > max_seconds:
            raise TooLongError(path)
    except TooLongError:
        raise
    except Exception:                                  # noqa: BLE001
        pass


def _phrases_for(path: str, beats: list) -> tuple:
    """(Phrasen, Stimmung) zu einer fertigen Beat-Liste (Zeit, Nummer, BPM)."""
    sr, data = _decode_bands(path)
    duration = len(data["st"]) / 2 / sr
    return _model_phrases(data, sr, _overview(data), duration, beats)


def analyse_on_grid(path: str, beats_tempo: list, max_seconds: float = 0.0) -> dict | None:
    """Nur Phrasen, auf einem vorhandenen Beatgrid (Bearbeitung, Uebernahme aus Rekordbox
    oder fruehere Messung) statt einem neu gemessenen. beats_tempo: [[ms, Nummer, BPM]]
    wie gridstore.beats_with_tempo() (mit lead=0, also auf eigener Zeitachse). Fuer Tracks, bei denen
    beatgrid.analyse() kein Tempo findet (Tempowechsel, Uebergaenge), deren Grid aber
    von Hand gesetzt ist -- und damit die Phrasen genau auf dem Grid liegen, das gilt.

    Antwort im Format von analyse(), aber ohne eigenes Grid ("beats" leer): gespeichert
    wird es nur als Phrasen-Ergebnis, das Grid bleibt, wo es ist. None ohne Ergebnis."""
    _check_length(path, max_seconds)
    beats = [(b[0] / 1000.0, int(b[1]), float(b[2])) for b in beats_tempo]
    if len(beats) < 2:
        return None
    phrases, mood = _phrases_for(path, beats)
    tempos = sorted(b[2] for b in beats)
    bpm = tempos[len(tempos) // 2]
    # Das Modell kennt nur ein festes Raster (siehe Modulkopf): wechselt das Tempo
    # spuerbar, ist das Ergebnis unsicher.
    varies = tempos[int(0.95 * (len(tempos) - 1))] - tempos[int(0.05 * (len(tempos) - 1))] > bpm * 0.01
    downbeat = next((b[0] for b in beats if b[1] == 1), beats[0][0])
    return {"mode": "grid", "used": "grid", "bpm": round(float(bpm), 3), "confidence": None,
            "first_beat": float(beats[0][0]), "downbeat": float(downbeat), "changes": [],
            "phrases": phrases, "mood": mood, "uncertain": bool(varies), "beats": [],
            "version": ANALYSIS_VERSION}


def analyse(path: str, mode: str = "auto", max_seconds: float = 0.0) -> dict | None:
    """Beatgrid + Phrasen einer Datei. None, wenn sich kein Tempo finden laesst."""
    _check_length(path, max_seconds)
    grid = beatgrid.analyse(path, mode)
    if not grid:
        return None
    phrases, mood = _phrases_for(path, grid["beats"])
    # float()/bool(): numpy-Werte lassen sich nicht als JSON senden.
    confidence = float(grid["confidence"])
    changes = [[round(float(t), 3), round(float(b), 2)] for t, b in grid["changes"]]
    return {"mode": grid["mode"], "used": grid["used"], "bpm": round(float(grid["bpm"]), 3),
            "confidence": round(confidence, 3), "first_beat": float(grid["first_beat"]),
            "downbeat": float(grid["downbeat"]), "changes": changes, "phrases": phrases, "mood": mood,
            "uncertain": bool(changes or confidence < _MIN_CONFIDENCE),
            "beats": [[int(round(b[0] * 1000)), int(b[1])] for b in grid["beats"]],
            "version": ANALYSIS_VERSION}
