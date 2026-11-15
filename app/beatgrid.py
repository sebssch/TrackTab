"""
Beatgrid aus dem Audio, ohne Rekordbox-Daten und ohne Modell -- Nachbau von
rbl-analysis aus rbxport (github.com/chrisle/rbxport, GPL-2.0-oder-spaeter):
onset.rs, attack.rs, downbeat.rs, tempo.rs, lib.rs. Die Schwellwerte sind
unveraendert uebernommen, die Begruendungen stehen dort im Quelltext.

Original: Copyright der rbxport-Autoren, GPL-2.0-oder-spaeter. Diese Datei ist
eine veraenderte Fassung (2026, Sebastian Gabler: Uebertragung nach Python,
Filter ueber ffmpeg, Modus "auto", Transition-Tracks und Rampen) und wird nach
der "oder spaeter"-Klausel unter GPL-3.0-oder-spaeter weitergegeben (LICENSE,
NOTICE.md, THIRD_PARTY_LICENSES.md).

Drei Modi (Einstellung phrase_grid_mode):
  - "normal": ein festes Tempo ueber den ganzen Track.
  - "flex":   folgt Tempowechseln, Rampen und versetzten Wiedereinstiegen
              (rbxport: "RBXport (Electronic Music)").
  - "auto":   Flex, aber Normal, wenn Flex keinen Tempowechsel findet. Eigene
              Ergaenzung: Flex verbiegt sonst einzelne Tracks mit festem Tempo.

Ablauf: Spektralfluss -> Tempo-Kandidaten (Autokorrelation x Fourier x Prior)
-> Gerade durch die Anschlaege der Kick (900-9000 Hz) -> Takt 1 aus der
Musikstruktur. Liest nur, schreibt weder Datei noch DB.
"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import media

MODES = ("auto", "normal", "flex")

MIN_BPM, MAX_BPM = 70.0, 180.0
_FRAME, _HOP = 1024, 256
_ATT_LOW, _ATT_HIGH, _ATT_STEP, _ATT_REACH = 900.0, 9000.0, 0.001, 0.015
_BAND_EDGES = (40.0, 65.0, 100.0, 160.0, 250.0, 400.0, 630.0, 1000.0, 1600.0,
               2500.0, 4000.0, 6300.0, 10000.0)
_BANDS = len(_BAND_EDGES) - 1
_SCALES = (8, 16, 32, 64)
_FOURIER_WINDOW_SECS = 20.0
_WHOLE_BPM_TOLERANCE, _WHOLE_BPM_SUPPORT, _ON_LINE_SECS = 0.1, 0.85, 0.010
_EPS = 2.220446e-16

_THR, _MIN_WINDOWS = 0.02, 3
_SETTLED_TOL, _RAMP_STEP, _RAMP_REACH, _MAX_WALKED = 0.01, 0.05, 0.1, 1024
_GAIN, _RELEASE = 4.0, 0.020
_KICK_PRESENT, _RUN_STRENGTH, _FILL_RATIO = 0.15, 0.55, 2.0
_BOUNDARY_STRENGTH, _BOUNDARY_BARS = 0.7, 2
_CUT_ON_A_KICK, _LIVE_SOURCE = 0.5, 0.1
_GAP_BEATS, _SUPPORTED_RUN, _PHASE_TOL = 8, 4, 0.1
_HIT_FLOOR, _RESUME_FRACTION = 0.02, 0.4
_RAMP_FADE, _RAMP_MEMORY = 0.25, 4
_RAMP_RATIO, _RAMP_WINDOW = (0.8, 1.3), (0.75, 1.25)
_RAMP_MIN_BEATS, _RAMP_DEPARTURE, _RAMP_WOBBLE = 8, 0.05, 0.01
_KICK_MARGIN, _MIN_STRETCH, _SAME_HIT = 1.5, 64, 0.5


def _rnd(x: float) -> float:
    """Rundung weg von null wie f64::round (Pythons round() rundet zur geraden Zahl)."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


class Segment:
    """Abschnitt eines Tempos: jeder phase + k * period in frm..to ist ein Beat."""
    __slots__ = ("frm", "to", "period", "phase")

    def __init__(self, frm: float, to: float, period: float, phase: float):
        self.frm, self.to, self.period, self.phase = frm, to, period, phase

    def start(self) -> float:
        if self.period <= 0:
            return self.frm
        return self.phase + math.ceil((self.frm - self.phase) / self.period - 1e-6) * self.period

    def beats(self) -> int:
        if self.period <= 0 or self.to <= self.frm:
            return 0
        s = self.start()
        return 0 if s >= self.to else int(math.ceil((self.to - s) / self.period - 1e-9))

    def bpm(self) -> float:
        return 60.0 / self.period if self.period > 0 else 0.0


def beats_of(segs: list, phase: int = 0) -> list:
    """Alle Beats als (Zeit in s, auf ms gerundet; Schlagnummer 1-4; BPM)."""
    out: list = []
    for i, s in enumerate(segs):
        st = s.start()
        count = min(s.beats(), 100000)
        if i + 1 < len(segs):
            last = st + max(count - 1, 0) * s.period
            if count > 0 and segs[i + 1].start() - last < _SAME_HIT * s.period:
                count -= 1
        for k in range(count):
            out.append((_rnd((st + k * s.period) * 1000.0) / 1000.0, (len(out) + phase) % 4 + 1, s.bpm()))
    return out


# ---------------------------------------------------------------- Dekodieren

def _biquad_arg(b0, b1, b2, a0, a1, a2) -> str:
    return (f"biquad=b0={b0 / a0:.17g}:b1={b1 / a0:.17g}:b2={b2 / a0:.17g}:a0=1:"
            f"a1={a1 / a0:.17g}:a2={a2 / a0:.17g}:r=f64")


def _coeffs(kind: str, hz: float, sr: int) -> tuple:
    # alpha wie attack.rs: sin(w) / (2 * sqrt(2)). Das Original rechnet trotz
    # seines Kommentars "Q = 1/sqrt(2)" ueber recip() mit Q = sqrt(2).
    w = 2 * math.pi * hz / sr
    al = math.sin(w) / (2 * math.sqrt(2))
    c = math.cos(w)
    if kind == "hp":
        h = (1 + c) / 2
        return h, -(1 + c), h, 1 + al, -2 * c, 1 - al
    h = (1 - c) / 2
    return h, 1 - c, h, 1 + al, -2 * c, 1 - al


def sample_rate(path: str) -> int:
    out = subprocess.check_output(
        [media.ffprobe_path(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(path)], text=True)
    return int(out.strip().split(",")[0])


def _decode(path: str) -> tuple:
    """(Abtastrate, Mono, Anschlag-Band, Kick-Tiefpass), je Float32 in nativer Rate."""
    sr = sample_rate(path)
    nyq = sr / 2
    hp = _biquad_arg(*_coeffs("hp", min(_ATT_LOW, nyq * .99), sr))
    lp = _biquad_arg(*_coeffs("lp", min(_ATT_HIGH, nyq * .99), sr))
    kq = _biquad_arg(*_coeffs("lp", 200.0, sr))
    fc = ("[0:a:0]aformat=sample_fmts=fltp:channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1,"
          f"asplit=3[m][a][k];[a]{hp},{lp}[b];[k]{kq},{kq}[kk]")
    with tempfile.TemporaryDirectory() as td:
        cmd = [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-filter_complex", fc]
        for lab, name in (("[m]", "m"), ("[b]", "b"), ("[kk]", "k")):
            cmd += ["-map", lab, "-c:a", "pcm_f32le", "-f", "f32le", str(Path(td) / f"{name}.raw")]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        return (sr, np.fromfile(Path(td) / "m.raw", dtype=np.float32),
                np.fromfile(Path(td) / "b.raw", dtype=np.float32),
                np.fromfile(Path(td) / "k.raw", dtype=np.float32))


# ---------------------------------------------------------------- onset.rs

def _frames_iter(x, frame: int, hop: int, batch: int = 4096):
    n = (len(x) - frame) // hop + 1
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(frame) / frame)
    for a in range(0, n, batch):
        idx = np.arange(a, min(n, a + batch))[:, None] * hop + np.arange(frame)
        yield np.fft.rfft(x[idx] * win, axis=1)


def _flux(x, frame: int, hop: int, last_bin: int | None = None):
    prev = np.zeros(frame // 2 + 1)
    parts = []
    for spec in _frames_iter(x, frame, hop):
        mag = np.abs(spec)
        d = np.diff(np.vstack([prev[None], mag]), axis=0)
        rise = np.where(d > 0, d, 0)
        parts.append((rise if last_bin is None else rise[:, :last_bin + 1]).sum(axis=1))
        prev = mag[-1]
    return np.concatenate(parts)


def _normalise(v):
    w = 16
    if len(v) <= w:
        return v
    cs = np.concatenate([[0.0], np.cumsum(v)])
    i = np.arange(len(v))
    lo, hi = np.maximum(i - w, 0), np.minimum(i + w, len(v))
    v = np.maximum(v - (cs[hi] - cs[lo]) / (hi - lo), 0.0)
    return v / v.max() if v.max() > 0 else v


def _onset_envelope(x, sr: int) -> tuple:
    rate, origin = sr / _HOP, _FRAME / 2 / sr
    if len(x) < _FRAME:
        return np.zeros(0), rate, origin
    return _normalise(_flux(x, _FRAME, _HOP)), rate, origin


def _decimation(sr: int, frame: int = 4096, hop: int = 256, high: float = 200.0) -> int:
    d = 1
    while (d < 64 and sr / (d * 2) >= 4.0 * high and frame % (d * 2) == 0
           and hop % (d * 2) == 0 and frame // (d * 2) >= 64):
        d *= 2
    return d


def _kick_envelope(klp, sr: int) -> tuple:
    """onset_envelope_band(Band::LOW) aus der schon tiefpassgefilterten Spur."""
    d = _decimation(sr)
    frame, hop, sr2 = 4096 // d, 256 // d, sr // d
    y = klp[::d]
    rate, origin = sr / 256, 4096 / 2 / sr
    if len(y) < frame:
        return np.zeros(0), rate, origin
    v = _normalise(_flux(y, frame, hop, int(math.ceil(200.0 / (sr2 / frame)))))
    pk = v[1:-1][(v[1:-1] > 0) & (v[1:-1] > v[:-2]) & (v[1:-1] >= v[2:])]
    if len(pk) >= 16:
        s = np.sort(pk)
        strong = s[min(int(len(s) * 0.98), len(s) - 1)]
        if strong > 0:
            v = v / strong
    return v, rate, origin


# ---------------------------------------------------------------- attack.rs

class _AttackMap:
    def __init__(self, band, sr: int):
        self.sr = sr
        self.step = max(1, int(_ATT_STEP * sr))
        n = len(band) // self.step
        sq = band[:n * self.step].astype(np.float64) ** 2
        rms = np.sqrt(sq.reshape(n, self.step).mean(axis=1))
        rest = band[n * self.step:]
        if len(rest):
            rms = np.append(rms, math.sqrt(float((rest.astype(np.float64) ** 2).mean())))
        self.level = rms.tolist()
        self.rise = np.diff(rms, prepend=rms[0]).tolist()

    def within(self, secs: float, reach_secs: float):
        n = len(self.level)
        if n < 3:
            return None
        step_secs = self.step / self.sr
        centre = max(int(_rnd(secs / step_secs)), 0)
        reach = max(int(math.ceil(reach_secs / step_secs)), 1)
        lo = max(max(centre - reach, 0), 1)
        hi = min(centre + reach, n - 1)
        if hi <= lo:
            return None
        rise = self.rise
        best_r, best_i = 0.0, lo
        for i in range(lo, hi + 1):
            if rise[i] > best_r:
                best_r, best_i = rise[i], i
        if best_r <= 0.0:
            return None
        ordinary = sorted(abs(rise[i]) for i in range(lo, hi + 1))
        if best_r < ordinary[len(ordinary) // 2] * 4.0:
            return None
        strong = best_r * 0.5
        ch_r, ch_i = best_r, best_i
        for i in range(lo, hi + 1):
            if rise[i] >= strong and abs(i - centre) < abs(ch_i - centre):
                ch_r, ch_i = rise[i], i
        start = ch_i
        while start > lo and rise[start - 1] > ch_r * 0.1:
            start -= 1
        floor = self.level[start - 1]
        top = max(self.level[start:hi + 1] + [floor])
        return ((start - 1) * self.step + self.step / 2.0) / self.sr, top - floor


# ---------------------------------------------------------------- tempo.rs: Kandidaten

def _interp(table: list, x: float) -> float:
    x = min(max(x, 0.0), len(table) - 1.0)
    i = int(math.floor(x))
    a = table[i]
    b = table[i + 1] if i + 1 < len(table) else a
    return a + (b - a) * (x - i)


def _fourier(centred, rate: float, bpm: float) -> float:
    omega = 2 * math.pi * bpm / 60.0 / rate
    window = min(max(int(_FOURIER_WINDOW_SECS * rate), 64), max(len(centred), 64))
    i = np.arange(window)
    w = (0.5 - 0.5 * np.cos(2 * np.pi * i / window)) * np.exp(1j * omega * i)
    total, count, start = 0.0, 0, 0
    while start + window <= len(centred):
        total += abs(np.dot(centred[start:start + window], w)) / window
        count += 1
        start += window // 2
    return total / count if count else 0.0


def _candidates(v, rate: float) -> list:
    n = len(v)
    centred = v - v.mean()
    var = float((centred ** 2).sum() / n)
    if var <= 0:
        return []
    lag_of = lambda b: rate * 60.0 / b
    min_lag = max(int(math.floor(lag_of(MAX_BPM * 2))), 2)
    max_lag = min(int(math.ceil(lag_of(MIN_BPM / 2))), n // 2)
    if max_lag <= min_lag + 2:
        return []
    acf = [float(np.dot(centred[:n - l], centred[l:])) / ((n - l) * var) for l in range(min_lag, max_lag + 1)]
    at = lambda l: _interp(acf, l - min_lag)
    bpms: list = []
    lo, hi = int(math.floor(lag_of(MAX_BPM))), int(math.ceil(lag_of(MIN_BPM)))
    for lag in range(max(lo, min_lag + 1), min(hi, max_lag - 1)):
        here, a, c = at(lag), at(lag - 1.0), at(lag + 1.0)
        if here <= a or here < c or here <= 0:
            continue
        denom = a - 2 * here + c
        off = 0.5 * (a - c) / denom if abs(denom) > _EPS else 0.0
        bpm = rate * 60.0 / (lag + min(max(off, -0.5), 0.5))
        for r in (1.0, 2.0, 0.5, 1.5, 2.0 / 3.0, 3.0, 1.0 / 3.0, 4.0 / 3.0, 0.75):
            rel = bpm * r
            if MIN_BPM <= rel <= MAX_BPM and not any(abs(b - rel) < rel * 0.01 for b in bpms):
                bpms.append(rel)
    if not bpms:
        return []
    ft = [_fourier(centred, rate, b) for b in bpms]
    ft_peak = max(ft)
    acf_peak = max(at(lag_of(b)) for b in bpms)
    out = []
    for b, f in zip(bpms, ft):
        a = max(at(lag_of(b)) / max(acf_peak, _EPS), 0.0)
        fo = f / ft_peak if ft_peak > 0 else 0.0
        x = math.log(b / 132.0) / 0.5
        out.append({"bpm": b, "acf": a, "fourier": fo, "score": a * math.sqrt(fo) * math.exp(-0.5 * x * x)})
    out.sort(key=lambda c: -c["score"])
    return out


def _related(a: float, b: float) -> bool:
    r = a / b if a > b else b / a
    return any(abs(r - x) < 0.02 * x for x in (1.0, 2.0, 3.0, 4.0, 1.5, 4.0 / 3.0))


# ---------------------------------------------------------------- tempo.rs: Gerade

def _comb_many(v, period: float, phases):
    """comb() fuer mehrere Phasen zugleich -- die Periodensuche ruft das tausendfach."""
    n = len(v)
    phases = np.asarray(phases, dtype=float)
    if period < 2.0 or n == 0:
        return np.zeros(len(phases))
    k = np.arange(int((n - phases.min()) / period) + 2)
    xs = phases[:, None] + k * period
    inside = xs < n
    usable = inside & (xs >= 0)
    x0 = np.where(usable, xs, 0.0)
    i = np.floor(x0).astype(np.int64)
    a = v[i]
    b = np.where(i + 1 < n, v[np.minimum(i + 1, n - 1)], 0.0)
    val = np.where(usable, a + (b - a) * (x0 - i), 0.0)
    cnt = inside.sum(axis=1)
    return np.where(cnt > 0, val.sum(axis=1) / np.maximum(cnt, 1), 0.0)


def _refine_period(v, coarse: float, phases: int = 32) -> float:
    steps = np.arange(phases) / phases
    score = lambda p: float(_comb_many(v, p, p * steps).max())
    best_s, best_p = score(coarse), coarse
    for span, step in ((1.0, 0.02), (0.03, 0.002)):
        centre = best_p
        cand = centre - span
        while cand <= centre + span:
            s = score(cand)
            if s > best_s:
                best_s, best_p = s, cand
            cand += step
    return best_p


def _best_phase(v, period: float, phases: int = 64) -> float:
    ph = period * np.arange(phases) / phases
    sc = _comb_many(v, period, ph)
    i = int(sc.argmax())
    best_ph, b = float(ph[i]), float(sc[i])
    step = period / phases
    a, c = _comb_many(v, period, [best_ph - step, best_ph + step])
    denom = a - 2 * b + c
    off = 0.5 * (a - c) / denom if abs(denom) > _EPS else 0.0
    return best_ph + min(max(off, -1.0), 1.0) * step


def _local_peak(v, around: float, reach: float):
    if int(math.ceil(around + reach)) <= 0:
        return None
    lo = max(int(math.floor(around - reach)), 0)
    hi = min(int(math.ceil(around + reach)), len(v) - 1)
    if lo >= hi:
        return None
    seg = v[lo:hi + 1]
    j = int(seg.argmax())
    pk = float(seg[j])
    if pk <= 0:
        return None
    i = lo + j
    a = float(v[i - 1]) if i > 0 else pk
    c = float(v[i + 1]) if i + 1 < len(v) else 0.0
    denom = a - 2 * pk + c
    off = 0.5 * (a - c) / denom if abs(denom) > _EPS else 0.0
    return i + min(max(off, -0.5), 0.5), pk


class _Reader:
    """Woraus die Anpassung ihre Beats liest: Huellkurve, Anschlaege, Kick-Band."""

    def __init__(self, v, rate: float, origin: float, attacks, kicks):
        self.v, self.rate, self.origin, self.attacks = v, rate, origin, attacks
        self.kv, self.krate, self.korigin = kicks
        self.vl = v.tolist()

    def snap(self, x: float, reach: float):
        secs = self.origin + x / self.rate
        hit = self.attacks.within(secs, min(reach / self.rate, _ATT_REACH))
        return ((hit[0] - self.origin) * self.rate, hit[1]) if hit else None

    def kick_peak(self, around: float, reach: float) -> float:
        lo = max(int(math.floor(around - reach)), 0)
        hi = min(max(int(math.ceil(around + reach)), 0), len(self.kv) - 1)
        if lo > hi or len(self.kv) == 0:
            return 0.0
        return max(float(self.kv[lo:hi + 1].max()), 0.0)

    def evidence(self, x: float, period: float) -> list:
        """[Kick, Anschlag, Fluss] an einem vorhergesagten Beat."""
        reach = max(period * 0.1, 1.0)
        secs = self.origin + x / self.rate
        kick = self.kick_peak((secs - self.korigin) * self.krate, reach)
        at = self.attacks.within(secs, _ATT_REACH)
        lp = _local_peak(self.v, x, reach)
        return [kick, at[1] if at else 0.0, lp[1] if lp else 0.0]


def _line(pts, fixed):
    w, x, y = pts[:, 2], pts[:, 0], pts[:, 1]
    sw = w.sum()
    if sw <= 0:
        return None
    sx, sy = (w * x).sum(), (w * y).sum()
    if fixed is not None:
        return fixed, (sy - fixed * sx) / sw
    denom = sw * (w * x * x).sum() - sx * sx
    if abs(denom) < _EPS:
        return None
    slope = (sw * (w * x * y).sum() - sx * sy) / denom
    return slope, (sy - slope * sx) / sw


def _snap_and_fit(rd: _Reader, frm: int, to: int, cur: tuple, fixed=None):
    phase, period = cur
    if period < 2.0:
        return None
    reach = max(period * 0.2, 1.0)
    first = int(math.ceil((frm - phase) / period))
    last = int(math.floor((to - 1.0 - phase) / period))
    if last < first + 4:
        return None
    sn = []
    for k in range(first, last + 1):
        hit = rd.snap(phase + k * period, reach)
        if hit:
            sn.append((float(k), hit[0], hit[1]))
    if len(sn) < 4:
        return None
    pts = np.asarray(sn)
    h = np.sort(pts[:, 2])
    strong = pts[pts[:, 2] >= h[len(h) // 2] * 0.2]
    if len(strong) >= 4:
        pts = strong
    ln = _line(pts, fixed)
    if ln is None:
        return None
    slope, icpt = ln
    res = np.abs(pts[:, 1] - icpt - slope * pts[:, 0])
    rs = np.sort(res)
    idx = len(rs) * 4 // 5
    cutoff = min(rs[idx] if idx < len(rs) else np.inf, period * 0.1)
    kept = pts[res <= cutoff]
    if len(kept) >= 4:
        ln = _line(kept, fixed)
        if ln is None:
            return None
        slope, icpt = ln
    if abs(slope - period) > period * 0.02:
        return None
    return (icpt, slope), float(kept[:, 2].sum())


def _on_line(rd: _Reader, frm: int, to: int, f: tuple) -> float:
    phase, period = f
    if period < 2.0:
        return 0.0
    reach = max(period * 0.2, 1.0)
    tol = _ON_LINE_SECS * rd.rate
    first = int(math.ceil((frm - phase) / period))
    last = int(math.floor((to - 1.0 - phase) / period))
    total = 0.0
    for k in range(first, last + 1):
        p = phase + k * period
        hit = rd.snap(p, reach)
        if hit:
            total += hit[1] * max(1.0 - abs(hit[0] - p) / tol, 0.0)
    return total


def _whole_bpm(rd: _Reader, frm: int, to: int, f: tuple) -> tuple:
    phase, period = f
    if period <= 0:
        return f
    bpm = rd.rate * 60.0 / period
    whole = float(_rnd(bpm))
    if whole <= 0 or abs(bpm - whole) > _WHOLE_BPM_TOLERANCE:
        return f
    p2 = rd.rate * 60.0 / whole
    start = (phase, p2)
    r = _snap_and_fit(rd, frm, to, start, p2)
    snapped = r[0] if r else start
    if _on_line(rd, frm, to, snapped) < _on_line(rd, frm, to, f) * _WHOLE_BPM_SUPPORT:
        return f
    return snapped


def _fit(rd: _Reader, bpm: float, frm: int, to: int):
    if frm > to or to > len(rd.v) or to - frm < 8:
        return None
    v = rd.v[frm:to]
    period = _refine_period(v, rd.rate * 60.0 / bpm)
    phase = _best_phase(v, period)
    best = None
    for half in (0.0, 0.5):
        f = (phase + half * period + frm, period)
        support = 0.0
        for _ in range(3):
            r = _snap_and_fit(rd, frm, to, f)
            if r is None:
                break
            f, support = r
        if best is None or support > best[1]:
            best = (f, support)
    return _whole_bpm(rd, frm, to, best[0]) if best else None


# ---------------------------------------------------------------- downbeat.rs

def _band_frames(x, sr: int):
    fr, hp = 2048, 512
    if len(x) < fr:
        return np.zeros((0, _BANDS))
    hz = np.arange(fr // 2 + 1) * sr / fr
    mask = np.zeros((_BANDS, fr // 2 + 1))
    for b in range(_BANDS):
        mask[b] = (hz >= _BAND_EDGES[b]) & (hz < _BAND_EDGES[b + 1])
    return np.vstack([np.log((np.abs(s) ** 2) @ mask.T + 1e-6) for s in _frames_iter(x, fr, hp)])


def _beat_profiles(frames, sr: int, secs: list):
    rate = sr / 512
    cs = np.vstack([np.zeros(_BANDS), np.cumsum(frames, axis=0)])
    out = np.zeros((len(secs), _BANDS))
    for i, s in enumerate(secs):
        e = secs[i + 1] if i + 1 < len(secs) else s + (secs[1] - secs[0] if len(secs) > 1 else 0.5)
        a = max(int(math.floor(s * rate)), 0)
        b = min(int(math.ceil(e * rate)), len(frames))
        if a < b:
            out[i] = (cs[b] - cs[a]) / (b - a)
    return out


def _novelty(p, scale: int):
    n = len(p)
    cs = np.vstack([np.zeros(_BANDS), np.cumsum(p, axis=0)])
    nov = np.zeros(n)
    idx = np.arange(scale, n - scale + 1)
    if len(idx):
        before = (cs[idx] - cs[idx - scale]) / scale
        after = (cs[idx + scale] - cs[idx]) / scale
        nov[idx] = np.sqrt(((after - before) ** 2).sum(axis=1))
    return nov


def _grid_phase(frames, sr: int, beats: list) -> tuple:
    """(Grid liegt auf der Zaehlzeit dazwischen?, Zeit von Takt 1)."""
    plain = (False, float(beats[0]) if beats else 0.0)
    if len(beats) < 8 or len(frames) == 0:
        return plain
    halves: list = []
    for a, b in zip(beats[:-1], beats[1:]):
        halves += [a, (a + b) / 2]
    prof = _beat_profiles(frames, sr, halves)
    if len(prof) < 16:
        return plain
    n4 = _novelty(prof, 32) if len(prof) >= 64 else np.zeros(len(prof))
    left, right = np.r_[0.0, n4[:-1]], np.r_[n4[1:], 0.0]
    pk = (n4 > left) & (n4 >= right) & (n4 > 0)
    if not (n4[pk] >= 0.5).any():
        return plain
    scores = np.zeros(8)
    for sc in _SCALES:
        if len(prof) < sc * 2:
            continue
        nov = _novelty(prof, sc)
        l, r = np.r_[0.0, nov[:-1]], np.r_[nov[1:], 0.0]
        nov = np.where((nov > l) & (nov >= r), nov, 0.0)
        tot = nov.sum()
        if tot > 0:
            for i in range(8):
                scores[i] += nov[i::8].sum() / tot
    # max_by nimmt bei Gleichstand das letzte Element
    best = int(len(scores) - 1 - np.argmax(scores[::-1]))
    return best % 2 == 1, float(halves[best])


# ---------------------------------------------------------------- Tempowechsel

def _local_tempo(w, rate: float, bpm: float):
    n = len(w)
    c = w - w.mean()
    var = float((c ** 2).sum() / n)
    if var <= 0:
        return None
    lag_of = lambda b: rate * 60.0 / b
    min_lag = max(int(math.floor(lag_of(MAX_BPM))), 2)
    max_lag = min(int(math.ceil(lag_of(MIN_BPM))), n // 3)
    if max_lag <= min_lag + 2:
        return None
    acf = [float(np.dot(c[:n - l], c[l:])) / ((n - l) * var) for l in range(min_lag, max_lag + 1)]
    home = _interp(acf, lag_of(bpm) - min_lag)
    best, bi = 0.0, 0
    for i in range(1, len(acf) - 1):
        if acf[i] > acf[i - 1] and acf[i] >= acf[i + 1] and acf[i] > best:
            best, bi = acf[i], i
    if best <= 0:
        return None
    l, m, r = acf[bi - 1], acf[bi], acf[bi + 1]
    cur = l - 2 * m + r
    off = 0.5 * (l - r) / cur if abs(cur) > _EPS else 0.0
    ratio = rate * 60.0 / (bi + min_lag + min(max(off, -0.5), 0.5)) / bpm
    while ratio > 1.5 and ratio * bpm / 2 >= MIN_BPM:
        ratio /= 2
    while ratio < 0.75 and ratio * bpm * 2 <= MAX_BPM:
        ratio *= 2
    return 1.0 if home >= best * 0.6 else ratio


def _rhythmic(r: float) -> bool:
    return any(abs(r - x) < 0.01 * x for x in (1.5, 2.0 / 3.0, 4.0 / 3.0, 0.75))


def _run_bounds(labels: list) -> list:
    out, i = [], 0
    while i < len(labels):
        e = i
        while e < len(labels) and abs(labels[e] - labels[i]) < 1e-9:
            e += 1
        out.append((i, e))
        i = e
    return out


def _label_runs(local: list, starts: list, window: int, n: int, bpm: float) -> list:
    clusters: list = []
    for r in local:
        if r is None or abs(r - 1.0) <= _THR or _rhythmic(r):
            continue
        for c in clusters:
            if abs(r - c[0]) < _THR * c[0] * 0.5:
                c[0] = (c[0] * c[1] + r) / (c[1] + 1)
                c[1] += 1
                break
        else:
            clusters.append([r, 1])
    clusters = [c for c in clusters if c[1] >= _MIN_WINDOWS]
    assigned = []
    for r in local:
        if r is None:
            assigned.append(None)
            continue
        opts = [x for x in [1.0] + [c[0] for c in clusters] if abs(r - x) < _THR * 1.5]
        assigned.append(min(opts, key=lambda x: abs(r - x)) if opts else None)
    prev = next((a for a in assigned if a is not None), 1.0)
    labels = []
    for a in assigned:
        prev = a if a is not None else prev
        labels.append(prev)
    for i in range(1, len(labels) - 1):
        if abs(labels[i - 1] - labels[i + 1]) < 1e-9 and abs(labels[i] - labels[i - 1]) > 1e-9:
            labels[i] = labels[i - 1]
    while True:
        runs = _run_bounds(labels)
        if len(runs) < 2:
            break
        hit = None
        for idx, (a, b) in enumerate(runs):
            settled = sum(1 for w in range(a, b) if assigned[w] is not None and abs(assigned[w] - labels[a]) < 1e-9)
            if settled < _MIN_WINDOWS:
                hit = (idx, a, b)
                break
        if hit is None:
            break
        idx, a, b = hit
        lab = labels[b] if idx == 0 else labels[a - 1]
        for w in range(a, b):
            labels[w] = lab
    out = []
    for i, e in _run_bounds(labels):
        settled = [w for w in range(i, e) if assigned[w] is not None and abs(assigned[w] - labels[i]) < 1e-9]
        first = settled[0] if settled else i
        last = settled[-1] if settled else e - 1
        out.append((0 if i == 0 else starts[first], n if e >= len(labels) else min(starts[last] + window, n),
                    bpm * labels[i], starts[last]))
    return out or [(0, n, bpm, n)]


def _peaks_in(vals: list, lo: float, hi: float, floor: float) -> list:
    lo = max(int(math.floor(lo)), 1)
    hi = min(max(int(math.ceil(hi)), 0), max(len(vals) - 2, 0))
    out = []
    for i in range(lo, hi + 1):
        a, b, c = vals[i - 1], vals[i], vals[i + 1]
        if b < floor or b <= a or b < c:
            continue
        d = a - 2 * b + c
        off = 0.5 * (a - c) / d if abs(d) > _EPS else 0.0
        out.append((i + min(max(off, -0.5), 0.5), b))
    return out


def _max_last(items: list, key):
    """max_by: bei Gleichstand gewinnt das letzte Element (Pythons max nimmt das erste)."""
    best = None
    for it in items:
        if best is None or key(it) >= key(best):
            best = it
    return best


class _Transients:
    """Hebt leise Perkussion nur innerhalb eines Tempo-Uebergangs an."""

    def __init__(self, rd: _Reader, frm: int, to: int):
        n = len(rd.vl)
        self.start = min(max(frm - int(math.ceil(rd.rate)), 0), n)
        end = max(min(to + int(math.ceil(rd.rate)), n), self.start)
        decay = math.exp(-1.0 / (rd.rate * _RELEASE))
        prev = rd.vl[self.start - 1] if self.start > 0 else 0.0
        rel = 0.0
        self.vals = []
        for val in rd.vl[self.start:end]:
            att = max(val - prev, 0.0) * _GAIN
            prev = val
            rel = max(att, rel * decay)
            self.vals.append(rel)

    def peak(self, rd: _Reader, x: float, reach: float):
        loc = x - self.start
        best = _max_last(_peaks_in(self.vals, loc - reach, loc + reach, _HIT_FLOOR), lambda p: p[1])
        if best is None:
            return None
        lp = _local_peak(rd.v, best[0] + self.start, 1.0)
        if lp is None:
            return None
        return (lp[0], best[1]) if abs(lp[0] - x) <= reach else None

    def snap(self, rd: _Reader, x: float, period: float, reach: float):
        if rd.evidence(x, period)[0] >= _KICK_PRESENT:
            return rd.snap(x, reach)
        hit = rd.snap(x, reach)
        return hit if hit else self.peak(rd, x, reach)

    def emphasise(self, rd: _Reader, beats: list, period: float) -> None:
        for b in beats:
            if b[1][0] < _KICK_PRESENT:
                p = self.peak(rd, b[0], max(period * 0.1, 1.0))
                b[1][2] = p[1] if p else 0.0


def _walk_beats(rd: _Reader, a: tuple, b: tuple, frm: int, to: int) -> list:
    if a[1] < 2.0 or b[1] < 2.0 or not 0.6 <= b[1] / a[1] <= 1.67:
        return []
    tr = _Transients(rd, frm, to)
    k = math.ceil((frm - a[0]) / a[1])
    hit = tr.snap(rd, a[0] + k * a[1], a[1], max(_RAMP_REACH * a[1], 1.0))
    if not hit:
        return []
    t, period = hit[0], a[1]
    beats, run = [t], 0
    while len(beats) < _MAX_WALKED and t < to:
        nxt = tr.snap(rd, t + period, period, max(_RAMP_REACH * period, 1.0))
        if not nxt:
            break
        np_ = nxt[0] - t
        if np_ <= 0 or abs(np_ - period) > period * _RAMP_STEP:
            break
        period, t = np_, nxt[0]
        beats.append(t)
        sb = b[0] + _rnd((t - b[0]) / b[1]) * b[1]
        if abs(period - b[1]) <= b[1] * _SETTLED_TOL and abs(t - sb) <= rd.rate * 0.002:
            run += 1
            if run >= 4:
                break
        else:
            run = 0
    return list(zip(beats[:-1], beats[1:])) if run >= 4 else []


def _settled_levels(beats: list, sf: float, st: float) -> list:
    settled = [e for at, e in beats if sf <= at < st]
    level = [0.0, 0.0, 0.0]
    for s in range(3):
        if len(settled) >= 4:
            means = [sum(e[s] for e in settled[i:i + 4]) / 4.0 for i in range(len(settled) - 3)]
        else:
            means = [e[s] for e in settled]
        means.sort()
        level[s] = means[len(means) // 2] if means else 0.0
    return level


def _kick_runs(beats: list, ks: int) -> list:
    n = len(beats)
    bar = lambda i: sum(beats[j][1][ks] for j in range(i, i + 4)) / 4.0 if i + 4 <= n else 0.0
    present = [i + 4 <= n and bar(i) >= _KICK_PRESENT for i in range(n)]
    runs, i = [], 0
    while i < n:
        if not present[i]:
            i += 1
            continue
        st = i
        while i < n and present[i]:
            i += 1
        bars = sorted(bar(j) for j in range(st, i))
        runs.append((st, i, bars[len(bars) // 2] if bars else 0.0))
    return runs


def _boundary(rd: _Reader, frm: int, to: int, sf: int, st: int, a: tuple, b: tuple) -> float:
    """Wo das Grid von a nach b wechselt: wo der neue Beat verlaesslich ankommt."""
    n = len(rd.v)
    lo, hi = min(frm, to), min(max(frm, to), n)
    if hi <= lo or b[1] < 2.0 or a[1] < 2.0:
        return float(lo)

    def grid(f, l, h):
        first = int(math.ceil((l - f[0]) / f[1]))
        last = int(math.floor((h - f[0]) / f[1]))
        return [[f[0] + k * f[1], rd.evidence(f[0] + k * f[1], f[1])] for k in range(first, last + 1)]

    bb = grid(b, lo, max(hi, min(st, n)))
    ba = grid(a, lo, hi)

    def mean_sum(beats, f, t):
        r = [sum(e) for at, e in beats if f <= at < t]
        return sum(r) / len(r) if r else 0.0

    level = _settled_levels(bb, float(sf), float(st))
    ks = 0 if level[0] >= _LIVE_SOURCE else (1 if level[1] >= _LIVE_SOURCE else None)
    if ks is not None:
        def mean_over(i, c, s):
            return sum(bb[j][1][s] for j in range(i, i + c)) / c if i + c <= len(bb) else 0.0

        bar = lambda i, s: mean_over(i, 4, s)
        runs = _kick_runs(bb, ks)
        strongest = max([r[2] for r in runs], default=0.0)
        for start, end, lvl in runs:
            if lvl < _RUN_STRENGTH * strongest or end < start + 4 * _BOUNDARY_BARS or bb[start][0] >= hi:
                continue
            for i in range(start, min(start + 4 * _BOUNDARY_BARS, end)):
                if i + 4 * _BOUNDARY_BARS > len(bb) or bb[i][0] >= hi:
                    break
                this, nxt = bar(i, ks), bar(i + 4, ks)
                not_fill = nxt <= _FILL_RATIO * this
                on_beat = mean_over(i, 2, ks) >= 0.5 * (this + nxt) / 2
                with_mix = level[2] < _LIVE_SOURCE or bar(i, 2) >= _BOUNDARY_STRENGTH * level[2]
                cut = bb[i][0]
                span = 4.0 * _BOUNDARY_BARS * b[1]
                better = mean_sum(bb, cut, cut + span) >= mean_sum(ba, cut, cut + span)
                if not (not_fill and on_beat and with_mix and better):
                    continue
                sb = max(max(bb[j][1][ks] for j in range(i, i + 4)), 0.0)
                is_kick = lambda j: bb[j][1][ks] >= _CUT_ON_A_KICK * sb
                has_mix = lambda j: level[2] < _LIVE_SOURCE or bb[j][1][2] >= _BOUNDARY_STRENGTH * level[2]
                has_click = lambda j: level[1] < _LIVE_SOURCE or bb[j][1][1] >= _CUT_ON_A_KICK * level[1]
                rng = range(i, i + 4)
                j = next((j for j in rng if is_kick(j) and has_mix(j) and has_click(j)), None)
                if j is None:
                    j = next((j for j in rng if is_kick(j) and has_mix(j)), None)
                if j is None:
                    j = next((j for j in rng if is_kick(j)), i)
                return bb[j][0]
    tr = _Transients(rd, lo, hi)
    tr.emphasise(rd, ba, a[1])
    tr.emphasise(rd, bb, b[1])
    best = (-math.inf, float(hi))
    for cut, e in bb:
        if not (cut < hi and sum(e) >= _HIT_FLOOR):
            continue
        score = sum(sum(x) for t, x in ba if t < cut) + sum(sum(x) for t, x in bb if cut <= t < hi)
        if score > best[0]:
            best = (score, cut)
    return best[1]


# ---------------------------------------------------------------- Luecken

def _beat_hits(vals: list, f: tuple, frm: int, to: int) -> list:
    if f[1] < 2.0 or to <= frm:
        return []
    reach = max(f[1] * 0.1, 1.0)
    first = int(math.ceil((frm - f[0]) / f[1]))
    last = int(math.floor((to - 1.0 - f[0]) / f[1]))
    out = []
    for k in range(first, last + 1):
        at = f[0] + k * f[1]
        best = _max_last(_peaks_in(vals, at - reach, at + reach, _HIT_FLOOR), lambda p: p[1])
        out.append((at, best[0], best[1]) if best else (at, at, 0.0))
    return out


def _kick_support(rd: _Reader, f: tuple, frm: float, to: float) -> float:
    if f[1] < 2.0 or len(rd.kv) == 0:
        return 0.0
    reach_secs = f[1] * 0.1 / rd.rate
    first = int(math.ceil((frm - f[0]) / f[1]))
    last = int(math.floor((to - f[0]) / f[1]))
    total = 0.0
    for k in range(first, last + 1):
        secs = rd.origin + (f[0] + k * f[1]) / rd.rate
        x = (secs - rd.korigin) * rd.krate
        lo = max(int(math.floor(x - reach_secs * rd.krate)), 0)
        hi = min(max(int(math.ceil(x + reach_secs * rd.krate)), 0), len(rd.kv) - 1)
        if lo <= hi:
            total += max(float(rd.kv[lo:hi + 1].max()), 0.0)
    return total


def _on_the_kicks(rd: _Reader, f: tuple, frm: float, to: float):
    moved = (f[0] + f[1] / 2.0, f[1])
    here, there = _kick_support(rd, f, frm, to), _kick_support(rd, moved, frm, to)
    if there > here * _KICK_MARGIN:
        return moved
    if here > there * _KICK_MARGIN:
        return f
    return None


def _near(g: tuple, x: float) -> float:
    return g[0] + _rnd((x - g[0]) / g[1]) * g[1]


def _judge_gap(rd: _Reader, f: tuple, frm: float, after: float, to: float, probe: float):
    period = f[1]
    same = lambda b: abs(b[1] - period) <= period * _THR

    def own(g, a, b):
        return g if (b - a) / g[1] >= _MIN_STRETCH else (_near(g, (a + b) / 2), period)

    if (to - after) / period < _MIN_STRETCH:
        return None
    bpm_here = rd.rate * 60.0 / period
    g = _fit(rd, bpm_here, int(max(frm, 0)), int(after))
    before = _on_the_kicks(rd, own(g, frm, after) if g and same(g) else f, frm, after)
    if before is None:
        return None
    g = _fit(rd, bpm_here, int(after), int(to))
    if not g or not same(g):
        return None
    rf = _on_the_kicks(rd, own(g, after, to), after, to)
    if rf is None:
        return None
    d = _near(rf, probe) - _near(before, probe)
    return before, rf, d - _rnd(d / period) * period


def _first_gap(sup: list, frm: int):
    ends = lambda i: all(i >= d and sup[i - d] for d in range(_SUPPORTED_RUN))
    starts = lambda i: i + _GAP_BEATS <= len(sup) and all(sup[i + d] for d in range(_GAP_BEATS))
    i = frm
    while i < len(sup):
        if sup[i]:
            i += 1
            continue
        st = i
        while i < len(sup) and not sup[i]:
            i += 1
        if i - st < _GAP_BEATS:
            continue
        last = next((j for j in range(st - 1, frm - 1, -1) if ends(j)), None)
        if last is None:
            continue
        return last, next((j for j in range(i, len(sup)) if starts(j)), None)
    return None


def _walk_ramp(rd: _Reader, start: float, period: float, until: float) -> tuple:
    lp = _local_peak(rd.v, start, 1.0)
    beats, heights = [start], [lp[1] if lp else _HIT_FLOOR]
    t, p, g = start, period, 1.0
    while len(beats) < _MAX_WALKED:
        lo = t + _RAMP_WINDOW[0] * p * min(g, 1.0)
        hi = t + _RAMP_WINDOW[1] * p * max(g, 1.0)
        if lo >= until:
            break
        pred = t + p * g
        recent = heights[-_RAMP_MEMORY:]
        floor = max(sum(recent) / len(recent) * _RAMP_FADE, _HIT_FLOOR)
        reach = (hi - lo) / 2.0
        best = _max_last(_peaks_in(rd.vl, lo, hi, floor), lambda q: q[1] / (1.0 + abs(q[0] - pred) / reach))
        if best is None:
            break
        np_ = best[0] - t
        ratio = np_ / p
        if not _RAMP_RATIO[0] <= ratio <= _RAMP_RATIO[1]:
            break
        if abs(best[0] - until) <= np_ * 0.1 or best[0] >= until:
            break
        beats.append(best[0])
        heights.append(best[1])
        t, p, g = best[0], np_, ratio
    return beats, g


def _one_way(beats: list) -> bool:
    ps = [b - a for a, b in zip(beats[:-1], beats[1:])]
    if not ps:
        return False
    up = ps[-1] > ps[0]
    against = sum(1 for a, b in zip(ps[:-1], ps[1:])
                  if (b < a * (1 - _RAMP_WOBBLE) if up else b > a * (1 + _RAMP_WOBBLE)))
    return against * 4 < len(ps)


def _last_supported_hit(vals: list, f: tuple, frm: float, until: float, floor: float):
    hits = _beat_hits(vals, f, int(max(frm, 0)), int(until + f[1] * 2.0))
    sup = [h[2] >= floor and h[0] < until for h in hits]
    for i in range(len(sup) - 1, -1, -1):
        if all(i >= d and sup[i - d] for d in range(_SUPPORTED_RUN)):
            return hits[i][1]
    return None


def _resume_of(vals: list, b: tuple, frm: float, to: float, floor: float):
    hits = _beat_hits(vals, b, int(frm), int(to))
    sup = [h[2] >= floor for h in hits]
    for i in range(len(sup)):
        if i + _GAP_BEATS <= len(sup) and all(sup[i + d] for d in range(_GAP_BEATS)):
            return hits[i][0]
    return None


def _refit_start(rd: _Reader, f: tuple, frm: float, to: float, hits: list, sup: list):
    leading = 0
    for s in sup:
        if s:
            break
        leading += 1
    if leading < _GAP_BEATS:
        return None
    res = next((i for i in range(len(sup))
                if i + _GAP_BEATS <= len(sup) and all(sup[i + d] for d in range(_GAP_BEATS))), None)
    until = hits[res][0] if res is not None else to
    period = f[1]
    g = _fit(rd, rd.rate * 60.0 / period, int(max(frm, 0)), int(until))
    if not g or abs(g[1] - period) > period * _THR:
        return None
    g = _on_the_kicks(rd, g, frm, until)
    if g is None:
        return None
    d = g[0] - f[0]
    return g if abs(d - _rnd(d / period) * period) > _PHASE_TOL * period else None


def _split_gaps(rd: _Reader, segs: list) -> list:
    """Teilt ein Segment, wo seine Gerade die Treffer verliert und die Musik anderswo zurueckkehrt."""
    vals, rate, origin = rd.vl, rd.rate, rd.origin
    n = len(vals)
    x_of = lambda s: (s - origin) * rate
    to_secs = lambda x: origin + x / rate
    beat = lambda a, b: Segment(to_secs(a), to_secs(b), (b - a) / rate, to_secs(a))
    line = lambda a, b, f: Segment(to_secs(a), to_secs(b), f[1] / rate, to_secs(f[0]))
    out = []
    for seg in segs:
        frm = max(x_of(seg.frm), 0.0)
        to = min(x_of(seg.to), float(n))
        f = (x_of(seg.phase), seg.period * rate)
        searched, refitted = 0, False
        # Das Original laeuft unbegrenzt; jeder Durchgang rueckt vor, die
        # Obergrenze faengt nur einen Rechenfehler ab.
        for _ in range(64):
            if f[1] < 2.0 or to <= frm:
                break
            hits = _beat_hits(vals, f, int(frm), int(to))
            hs = sorted(h[2] for h in hits if h[2] >= _HIT_FLOOR)
            floor = max(hs[len(hs) // 2] * 0.2, _HIT_FLOOR) if hs else _HIT_FLOOR
            sup = [h[2] >= floor for h in hits]
            if not refitted and searched == 0:
                refitted = True
                g = _refit_start(rd, f, frm, to, hits, sup)
                if g is not None:
                    f = g
                    continue
            gap = _first_gap(sup, searched)
            if gap is None:
                break
            last_sup, resumes = gap
            after = hits[last_sup + 1][0] if last_sup + 1 < len(hits) else to
            period = f[1]
            probe = hits[resumes][0] if resumes is not None else (after + to) / 2
            judged = _judge_gap(rd, f, frm, after, to, probe)
            moved = False
            if judged:
                moved = (abs(judged[2]) > _PHASE_TOL * period
                         and _kick_support(rd, judged[1], after, to) > _kick_support(rd, judged[0], after, to) * _KICK_MARGIN)
            before, refit = (judged[0], judged[1]) if judged else (f, None)
            last_hit = _last_supported_hit(vals, before, frm, after, floor)
            if last_hit is None:
                lb = _near(before, hits[last_sup][0])
                lp = _local_peak(rd.v, lb, max(before[1] * 0.1, 1.0))
                last_hit = lp[0] if lp else lb
            f = before
            if not moved:
                if resumes is None:
                    break
                searched = resumes
                continue
            cut = _resume_of(vals, refit, after, to, floor * _RESUME_FRACTION) if refit else None
            if cut is None:
                break
            resumed = refit
            g = _fit(rd, rate * 60.0 / period, int(cut), int(to))
            if g and abs(g[1] - period) <= period * _THR:
                k_ = _on_the_kicks(rd, g, cut, to)
                if k_ is not None:
                    resumed = k_
            cut = _near(resumed, cut)
            walked, growth = _walk_ramp(rd, last_hit, f[1], cut)
            dep = max([abs((b - a) / f[1] - 1.0) for a, b in zip(walked[:-1], walked[1:])], default=0.0)
            if len(walked) >= _RAMP_MIN_BEATS and dep >= _RAMP_DEPARTURE and _one_way(walked):
                if walked[0] - f[1] / 2.0 > frm:
                    out.append(line(frm, walked[0] - f[1] / 2.0, f))
                out.extend(beat(a, b) for a, b in zip(walked[:-1], walked[1:]))
                if len(walked) >= 2 and cut - walked[-1] > 0:
                    lastp = walked[-1] - walked[-2]
                    count = max(int(_rnd((cut - walked[-1]) / (lastp * growth))), 1)
                    step = (cut - walked[-1]) / count
                    out.extend(beat(walked[-1] + i * step, walked[-1] + (i + 1) * step) for i in range(count))
            elif cut - f[1] / 2.0 > frm:
                out.append(line(frm, cut - f[1] / 2.0, f))
            frm, f, searched = cut, resumed, 0
        if to > frm:
            out.append(line(frm, to, f))
    return out


def _segment_flex(rd: _Reader, bpm: float) -> list:
    v, rate, origin = rd.v, rd.rate, rd.origin
    n = len(v)
    window = max(int(16.0 * rate), 64)
    local, starts = [], []
    s = 0
    while s + window <= n:
        starts.append(s)
        local.append(_local_tempo(v[s:s + window], rate, bpm))
        s += window // 2
    fits = []
    for frm, to, run_bpm, change in _label_runs(local, starts, window, n, bpm):
        f = _fit(rd, run_bpm, frm, to)
        if f:
            fits.append((change, frm, to, f))
    if not fits:
        return []
    to_secs = lambda x: origin + x / rate
    from_sample = -origin * rate
    pending = None
    segs = []

    def refit(cur, a, b):
        fi, ti = int(max(a, 0.0)), min(int(b), n)
        if ti <= fi:
            return cur
        return _fit(rd, rate * 60.0 / cur[1], fi, ti) or cur

    for idx, (change, _, _, f) in enumerate(fits):
        cur = pending if pending is not None else f
        if idx + 1 >= len(fits):
            if n > from_sample:
                c = cur if pending is not None else refit(cur, from_sample, float(n))
                segs.append(Segment(to_secs(from_sample), to_secs(float(n)), c[1] / rate, to_secs(c[0])))
            break
        _, fn, tn, nxt = fits[idx + 1]
        walked = _walk_beats(rd, cur, nxt, change, tn)
        if walked:
            cut = walked[0][0]
            nf = (nxt[0] + _rnd((walked[-1][1] - nxt[0]) / nxt[1]) * nxt[1], nxt[1])
        else:
            cut, nf = _boundary(rd, change, tn, fn, tn, cur, nxt), nxt
        if cut > from_sample:
            c = cur if pending is not None else refit(cur, from_sample, cut)
            segs.append(Segment(to_secs(from_sample), to_secs(cut), c[1] / rate, to_secs(c[0])))
        for a, b in walked:
            segs.append(Segment(to_secs(a), to_secs(b), (b - a) / rate, to_secs(a)))
        from_sample = cut if not walked else nf[0]
        pending = nf
    return _split_gaps(rd, segs)


def _segment_normal(rd: _Reader, bpm: float) -> list:
    f = _fit(rd, bpm, 0, len(rd.v))
    if not f:
        return []
    return [Segment(0.0, rd.origin + len(rd.v) / rd.rate, f[1] / rd.rate, rd.origin + f[0] / rd.rate)]


# ---------------------------------------------------------------- lib.rs

def _opening_cut(first: Segment, x, sr: int) -> float:
    nxt = first.start()
    inferred = first.period - nxt
    if inferred <= 0 or inferred > 0.035:
        return 0.0
    lo = int(max(nxt - 0.015, 0.0) * sr)
    hi = min(int((nxt + 0.015) * sr), len(x))
    w = np.abs(x[lo:hi])
    if len(w) == 0:
        return inferred
    peak = float(w.max())
    above = np.nonzero(w > peak * 0.01)[0]
    if len(above) == 0:
        return inferred
    idx = int(above[0])
    if idx < max(sr // 1000, 1) or peak < 1e-5:
        return inferred
    now, prev = float(w[idx]), float(w[idx - 1])
    return first.period - _rnd(lo + idx - now / (now - prev)) / sr


def _anchor_file_start(segs: list, x, sr: int, down: float) -> int | None:
    """Beat 1.1 auf 0:00, wenn der Track genau dort beginnt. Liefert die neue
    Zaehl-Phase (immer 0) oder None, wenn nichts zu tun ist."""
    first = segs[0]
    if first.frm > _ATT_REACH or first.period <= 0:
        return None
    cut = _opening_cut(first, x, sr)
    bar = 4 * first.period
    dp = (down + max(cut, 0.0)) % bar
    if down > first.start() + _ATT_REACH and min(dp, bar - dp) > _ATT_REACH:
        return None
    ph = first.phase % first.period
    clipped = cut > 0.5 / sr
    if (clipped and cut >= 0.020 - 0.5 / sr) or (not clipped and min(ph, first.period - ph) > _ATT_REACH):
        return None
    window = int(math.ceil(sr * _ATT_REACH))
    local = float(np.abs(x[:window]).max()) if window else 0.0
    if local < 1e-5 or float(np.abs(x[:max(sr // 1000, 1)]).max()) < local * 0.1:
        return None
    if clipped:
        nx = first.start()
        segs.insert(0, Segment(0.0, nx, first.period, 0.0))
        first.frm = nx
    else:
        first.frm, first.phase = 0.0, 0.0
    return 0


def has_tempo_change(beats: list, thr: float = 0.02) -> bool:
    return bool(tempo_changes(beats, thr))


def tempo_changes(beats: list, thr: float = 0.02) -> list:
    """Stellen, an denen das Tempo dauerhaft um mehr als thr wechselt:
    [(Sekunde, neues BPM)]. Ein einzelner gelaufener Schlag zaehlt nicht."""
    out: list = []
    if not beats:
        return out
    cur = beats[0][2]
    for i, b in enumerate(beats):
        if abs(b[2] - cur) / cur <= thr:
            continue
        win = [x[2] for x in beats[i:i + 8]]
        if len(win) >= 4 and abs(float(np.median(win)) - cur) / cur > thr:
            later = [x[2] for x in beats[i + 4:i + 16]] or win
            out.append((b[0], float(np.median(later))))
            cur = out[-1][1]
    return out


def _finish(segs: list, frames, x, sr: int) -> list:
    """Takt 1 suchen und alle Beats nummerieren (lib.rs::analyse_with)."""
    half_off, down = _grid_phase(frames, sr, [b[0] for b in beats_of(segs, 0)])
    if half_off:
        for s in segs:
            s.phase += s.period / 2
    if len(segs) > 1 and segs[0].beats() >= 32:
        down = _grid_phase(frames, sr, [b[0] for b in beats_of(segs[:1], 0)])[1]
    plain = beats_of(segs, 0)
    idx = min(range(len(plain)), key=lambda i: abs(plain[i][0] - down)) if plain else 0
    phase = (4 - idx % 4) % 4
    anchored = _anchor_file_start(segs, x, sr, down)
    return beats_of(segs, phase if anchored is None else anchored)


def analyse(path: str, mode: str = "auto", use_title: bool = True) -> dict | None:
    """Beatgrid einer Datei. None, wenn sich kein Tempo finden laesst.
    used = tatsaechlich verwendeter Modus ("normal"/"flex", bei "auto" das Ergebnis;
    "transition" bei zwei Tempi im Dateinamen, siehe _segment_transition()).
    use_title=False schaltet das ab (Vergleichsmessungen)."""
    if mode not in MODES:
        mode = "auto"
    sr, x, band, klp = _decode(path)
    v, rate, origin = _onset_envelope(x, sr)
    if len(v) < 64:
        return None
    cands = _candidates(v, rate)
    if not cands:
        return None
    best = cands[0]
    rival = next((c["acf"] * c["fourier"] for c in cands[1:] if not _related(c["bpm"], best["bpm"])), 0.0)
    own = best["acf"] * best["fourier"]
    confidence = min(max((own - rival) / own, 0.0), 1.0) if own > 0 else 0.0
    rd = _Reader(v, rate, origin, _AttackMap(band, sr), _kick_envelope(klp, sr))
    frames = _band_frames(x, sr)
    used, segs = "normal", None
    hint = title_bpms(path) if use_title and mode in ("flex", "auto") else None
    down = None
    if hint:
        # Transition-Track mit beiden Tempi im Namen: Tempo A, Rampe bzw. harter
        # Wechsel, Tempo B. Klappt das nicht oder trifft Flex klar besser, wie bisher Flex.
        found = _segment_transition(rd, frames, sr, *hint)
        if found:
            flex = _segment_flex(rd, best["bpm"])
            if not flex or _grid_support(rd, found[0]) * _TRANS_KEEP >= _grid_support(rd, flex):
                segs, down = found
                used = "transition"
            else:
                segs, used = flex, "flex"
    if segs is None and mode in ("flex", "auto"):
        segs = _segment_flex(rd, best["bpm"])
        used = "flex"
        if mode == "auto" and segs and not has_tempo_change(beats_of(segs, 0)):
            segs = None
    if segs is None or (mode == "auto" and not segs):
        segs = _segment_normal(rd, best["bpm"])
        used = "normal"
    if not segs:
        return None
    if down is not None:
        # Die Geraden liegen schon auf den Anschlaegen, Takt 1 kennt _segment_transition().
        plain = beats_of(segs, 0)
        idx = min(range(len(plain)), key=lambda i: abs(plain[i][0] - down)) if plain else 0
        beats = beats_of(segs, (4 - idx % 4) % 4)
    else:
        beats = _finish(segs, frames, x, sr)
    if not beats:
        return None
    return {"mode": mode, "used": used, "bpm": segs[0].bpm(), "confidence": confidence,
            "first_beat": beats[0][0],
            "downbeat": next((b[0] for b in beats if b[1] == 1), beats[0][0]),
            "beats": beats, "changes": tempo_changes(beats), "duration": len(x) / sr}


# ---------------------------------------------------------------- Rampe einpassen
# Eigene Ergaenzung, nicht aus rbxport: die Schlaege einer Tempo-Rampe (DJ-Editor,
# dj.js djRampTimes()) an die Anschlaege im Audio legen, und Transition-Tracks
# ("100-128 Transition") mit Rampe messen.

_FIT_REACH = 0.3       # Suchfenster um den erwarteten Schlag, Anteil am Schlagabstand
_FIT_SMOOTH = 20.0     # Gewicht gegen Spruenge im Schlagabstand (relativ zur Vorgabe)
_FIT_FLOOR = 0.05      # schwaechere Spitzen der Huellkurve zaehlen nicht
_LINE_TOL = 0.025      # so nah (s) muss ein Anschlag an einem Schlag der Geraden liegen
_TRANS_HEAD = 24.0     # Anfangstempo aus den ersten Sekunden
_TRANS_TAIL = 0.45     # Endtempo ab diesem Anteil des Tracks
_TRANS_BACK = 8        # Rampenbeginn bis so viele Schlaege vor dem Ende der A-Unterstuetzung
_TRANS_MAX_BEATS = 96  # laengste Rampe
_TRANS_CONTEXT = 32    # Schlaege der Geraden vor/nach der Rampe, die in die Bewertung eingehen
_TRANS_KEEP = 1.05     # Flex gewinnt nur, wenn es mehr als 5 % mehr Anschlaege trifft


def ramp_prior(t0: float, t1: float, beats: int, p0: float, p1: float, pins=None) -> list:
    """Schlagzeiten 0..beats wie djRampTimes(): stueckweise kubische Hermite-Kurve ueber
    der Schlagnummer durch Anfang, feste Anker (pins = [(k, Sekunde)]) und Ende, Steigung
    an den Raendern = Schlagabstand der Tempi davor/danach, an den Ankern das Mittel der
    beiden Nachbarstrecken, begrenzt nach Fritsch-Carlson."""
    pts = [(0, t0)]
    for k, sec in sorted(pins or []):
        if pts[-1][0] < k < beats and pts[-1][1] < sec < t1:
            pts.append((int(k), float(sec)))
    pts.append((beats, t1))
    m = len(pts)
    d = [(pts[j + 1][1] - pts[j][1]) / (pts[j + 1][0] - pts[j][0]) for j in range(m - 1)]
    slope = [0.0] * m
    slope[0], slope[-1] = p0, p1
    for j in range(1, m - 1):
        slope[j] = (d[j - 1] + d[j]) / 2
    for j in range(m - 1):
        a, b = slope[j] / d[j], slope[j + 1] / d[j]
        if a * a + b * b > 9:
            tau = 3 / math.sqrt(a * a + b * b)
            slope[j], slope[j + 1] = tau * a * d[j], tau * b * d[j]
    out = [0.0] * (beats + 1)
    for j in range(m - 1):
        (ka, ta), (kb, tb) = pts[j], pts[j + 1]
        h = kb - ka
        for k in range(ka, kb + 1):
            u = (k - ka) / h
            u2, u3 = u * u, u * u * u
            out[k] = ((2 * u3 - 3 * u2 + 1) * ta + (u3 - 2 * u2 + u) * h * slope[j]
                      + (-2 * u3 + 3 * u2) * tb + (u3 - u2) * h * slope[j + 1])
    return out


def _ramp_core(rd: _Reader, t0: float, t1: float, p0: float, p1: float, beats: int,
               fixed=None) -> tuple:
    """([(k, Sekunde, Staerke)] fuer k = 1..beats-1, Kosten). Je Schlag sind die
    Kandidaten die Spitzen der Onset-Huellkurve im Fenster um die Vorgabe (Zeit vom
    Anschlag-Band wie beim Messen des Grids) plus die Vorgabe selbst (Staerke 0).
    Viterbi minimiert -Staerke + Glaette des Schlagabstands relativ zur Vorgabe.
    fixed = {k: Sekunde}: von Hand gesetzte Anker, dort ist die Zeit der einzige
    Kandidat (Staerke 0) und die Vorgabe laeuft durch sie hindurch."""
    vl, rate, origin = rd.vl, rd.rate, rd.origin
    fixed = fixed or {}
    prior = ramp_prior(t0, t1, beats, p0, p1, list(fixed.items()))
    cands = [[(t0, 0.0)]]
    for k in range(1, beats):
        if k in fixed:
            cands.append([(fixed[k], 0.0)])
            continue
        per = (prior[k + 1] - prior[k - 1]) / 2
        lo, hi = prior[k] - _FIT_REACH * per, prior[k] + _FIT_REACH * per
        found = [(prior[k], 0.0)]
        for xi, h in _peaks_in(vl, (lo - origin) * rate, (hi - origin) * rate, _FIT_FLOOR):
            sec = origin + xi / rate
            hit = rd.attacks.within(sec, _ATT_REACH)
            found.append((hit[0] if hit and lo <= hit[0] <= hi else sec, h))
        cands.append(found)
    cands.append([(t1, 0.0)])
    cost, back = [0.0], [[0]]
    for k in range(1, beats + 1):
        want = prior[k] - prior[k - 1]
        row, ptr = [], []
        for t, h in cands[k]:
            best, arg = math.inf, 0
            for j, (tp, _) in enumerate(cands[k - 1]):
                if t > tp:
                    c = cost[j] + _FIT_SMOOTH * ((t - tp) / want - 1.0) ** 2
                    if c < best:
                        best, arg = c, j
            row.append(best - h)
            ptr.append(arg)
        cost, back = row, back + [ptr]
    total, j, out = cost[0], 0, []
    for k in range(beats, 0, -1):
        j = back[k][j]
        if k > 1:
            t, h = cands[k - 1][j]
            out.append((k - 1, round(t, 4), round(h, 3)))
    return out[::-1], total


def _reader(path: str, decoded: tuple | None = None) -> tuple:
    sr, x, band, klp = decoded or _decode(path)
    v, rate, origin = _onset_envelope(x, sr)
    return _Reader(v, rate, origin, _AttackMap(band, sr), _kick_envelope(klp, sr)), sr, x


def fit_ramp(path: str, t0: float, t1: float, bpm0: float, bpm1: float, beats: int,
             decoded: tuple | None = None, fixed=None) -> list:
    """Schlaege 1..beats-1 einer Rampe an die Anschlaege legen: [(k, Sekunde, Staerke)],
    Staerke 0 = kein Anschlag gefunden (Schlag bleibt auf der Vorgabe) bzw. fester Anker.
    fixed = {k: Sekunde}: eigene Anker des Nutzers, bleiben unveraendert stehen.
    decoded: Ergebnis von _decode() zum Wiederverwenden."""
    if beats < 2 or t1 <= t0:
        return []
    rd = _reader(path, decoded)[0]
    return _ramp_core(rd, t0, t1, 60.0 / bpm0, 60.0 / bpm1, beats, fixed)[0]


# Zwei Tempi im Dateinamen, etwa "Tall Boys 100-128 Transition" oder "(126 To 70 BPM)".
_TITLE_PAIR = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:-|–|—|>|→|\bto\b|\s)\s*(\d{2,3})(?:\.\d+)?(?!\d)", re.I)


def title_bpms(path: str) -> tuple | None:
    """(Tempo davor, Tempo danach) aus dem Dateinamen eines Transition-Tracks, sonst None.
    Nur mit "Transition" oder "BPM" im Namen, beide Werte 60-200 und verschieden."""
    name = Path(path).stem
    if not re.search(r"transition|bpm", name, re.I):
        return None
    for m in _TITLE_PAIR.finditer(name):
        a, b = float(m.group(1)), float(m.group(2))
        # "150-170-150 BPM": drei Tempi, Anfang und Ende gleich -- passt nicht ins Schema.
        if re.match(r"\s*(?:-|–|—|>|→)\s*\d{2,3}", name[m.end():]):
            return None
        if 60 <= a <= 200 and 60 <= b <= 200 and abs(a - b) >= 3:
            return a, b
    return None


def _grid_support(rd: _Reader, segs: list) -> float:
    """Mittlere Hoehe der Onset-Huellkurve (+-20 ms) auf den Schlaegen eines Grids."""
    reach, vals = 0.02 * rd.rate, []
    for t, _, _ in beats_of(segs, 0):
        x = (t - rd.origin) * rd.rate
        lo, hi = max(int(x - reach), 0), min(int(x + reach) + 1, len(rd.vl))
        if hi > lo:
            vals.append(max(rd.vl[lo:hi]))
    return sum(vals) / len(vals) if vals else 0.0


def _line_hits(rd: _Reader, f: tuple) -> list:
    """[(Sekunde, Staerke)] fuer jeden Schlag der Geraden f; Staerke 0, wenn kein
    Anschlag naeher als _LINE_TOL liegt."""
    tol = _LINE_TOL * rd.rate
    return [(rd.origin + at / rd.rate, h if abs(peak - at) <= tol else 0.0)
            for at, peak, h in _beat_hits(rd.vl, f, 0, len(rd.vl))]


def _line_sum(hits: list, lo: float, hi: float) -> float:
    return sum(h for t, h in hits if lo <= t < hi)


def _change_point(ha: list, hb: list) -> float | None:
    """Zeit T, vor der die Gerade A und ab der die Gerade B die meisten Anschlaege traegt."""
    ev = sorted([(t, h) for t, h in ha] + [(t, -h) for t, h in hb])
    best, at, acc = 0.0, None, 0.0
    for t, d in ev:
        if acc > best or at is None:
            best, at = acc, t
        acc += d
    return at


def _support_runs(hits: list, floor: float, min_run: int = 4) -> list:
    """Laeufe von mindestens min_run Schlaegen mit Anschlag: [(erste, letzte Sekunde)]."""
    out, i = [], 0
    while i < len(hits):
        if hits[i][1] < floor:
            i += 1
            continue
        s = i
        while i < len(hits) and hits[i][1] >= floor:
            i += 1
        if i - s >= min_run:
            out.append((hits[s][0], hits[i - 1][0]))
    return out


def _segment_transition(rd: _Reader, frames, sr: int, a_bpm: float, b_bpm: float) -> tuple | None:
    """Transition-Track: Tempo A am Anfang, Tempo B am Ende, dazwischen eine Rampe (je
    Schlag ein Abschnitt) oder ein harter Wechsel. Rampenbeginn, -ende und Schlagzahl
    werden gemeinsam so gewaehlt, dass Gerade A davor, die Rampe und Gerade B danach
    zusammen die meisten Anschlaege treffen. Gezaehlt wird durchgehend ab Takt 1 von A
    (_grid_phase()); die Schlagzahl richtet sich bewusst NICHT nach der Zaehlung: an
    Transition-Tracks lag _grid_phase() oft einen halben Takt daneben, daran gebundene
    Rampen trafen deutlich schlechter. (Abschnitte, Takt 1 von A) oder None, wenn sich
    die beiden Tempi nicht finden lassen."""
    rate, origin, n = rd.rate, rd.origin, len(rd.v)
    fa = _fit(rd, a_bpm, 0, min(n, int(_TRANS_HEAD * rate)))
    fb = _fit(rd, b_bpm, int(n * _TRANS_TAIL), n)
    if not fa or not fb:
        return None
    ha, hb = _line_hits(rd, fa), _line_hits(rd, fb)
    cp = _change_point(ha, hb)
    hs = sorted(h for _, h in ha if h > 0)
    floor = max(hs[len(hs) // 2] * 0.25, _HIT_FLOOR) if hs else _HIT_FLOOR
    ra = [r for r in _support_runs(ha, floor) if cp is not None and r[1] <= cp + 2]
    rb = [r for r in _support_runs(hb, floor) if cp is not None and r[0] >= cp - 2]
    if not ra or not rb:
        return None
    end_a, start_b = max(r[1] for r in ra), min(r[0] for r in rb)
    if start_b < end_a:
        end_a, start_b = start_b, end_a
    # Beide Tempi genau ueber ihren eigenen Bereich nachmessen.
    fa = _fit(rd, 60 * rate / fa[1], 0, int((end_a - origin) * rate) + 1) or fa
    fb = _fit(rd, 60 * rate / fb[1], int((start_b - origin) * rate), n) or fb
    pa, pb = fa[1] / rate, fb[1] / rate
    ha, hb = _line_hits(rd, fa), _line_hits(rd, fb)
    at = lambda f, k: origin + (f[0] + k * f[1]) / rate
    index = lambda f, t: round(((t - origin) * rate - f[0]) / f[1])
    end = origin + n / rate
    a_beats = [at(fa, k) for k in range(index(fa, origin) + 1, index(fa, end_a) + 1)]
    down_a = _grid_phase(frames, sr, a_beats)[1] if len(a_beats) >= 8 else a_beats[0] if a_beats else origin
    ka = index(fa, end_a)
    t0s = [at(fa, ka - j) for j in range(_TRANS_BACK + 1) if at(fa, ka - j) > origin]
    t1s = [at(fb, k) for k in range(index(fb, end_a), index(fb, start_b) + 1)]
    lo, hi = end_a - _TRANS_CONTEXT * pa, start_b + _TRANS_CONTEXT * pb
    best = None
    # Harter Wechsel: A bis vor tc, B ab tc.
    for tc in t1s:
        score = _line_sum(ha, lo, tc - 0.5 * pa) + _line_sum(hb, tc, hi)
        if best is None or score > best[0]:
            best = (score, tc, tc, 0)
    for t0 in t0s:
        before = _line_sum(ha, lo, t0 + 0.5 * pa)
        for t1 in t1s:
            if t1 - t0 < 1.5 * min(pa, pb):
                continue
            est = (t1 - t0) / ((pa + pb) / 2)
            if est > _TRANS_MAX_BEATS:
                continue
            after = _line_sum(hb, t1, hi)
            for beats in {max(2, int(round(est)) + d) for d in (-1, 0, 1)}:
                if not min(pa, pb) * 0.8 <= (t1 - t0) / beats <= max(pa, pb) * 1.25:
                    continue
                score = before + after - _ramp_core(rd, t0, t1, pa, pb, beats)[1]
                if best is None or score > best[0]:
                    best = (score, t0, t1, beats)
    if best is None:
        return None
    _, t0, t1, beats = best
    segs = [Segment(0.0, t0, pa, at(fa, 0))]
    if beats:
        times = [t0] + [t for _, t, _ in _ramp_core(rd, t0, t1, pa, pb, beats)[0]] + [t1]
        segs += [Segment(a, b, b - a, a) for a, b in zip(times[:-1], times[1:])]
    segs.append(Segment(t1, end, pb, at(fb, 0)))
    return segs, down_a
