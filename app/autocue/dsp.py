"""
Nachbau der librosa-Funktionen, auf denen die Auto-Cue-Modelle trainiert sind.

Die Ankermodelle (learned.py) kennen die Merkmale genau so, wie librosa 0.11 sie
im Autocue-Projekt berechnet hat. Jede Abweichung -- anderes Fenster, andere
Mel-Normierung, Chroma ohne Stimmungsschaetzung -- verschiebt die Vorhersagen
still. Deshalb steht hier keine "aehnliche" Rechnung, sondern dieselbe, Schritt
fuer Schritt aus dem librosa-Quelltext abgelesen (Aufrufe wie in features.py des
Originals: stft, spectral_centroid/rolloff/flatness, power_to_db, onset_strength,
melspectrogram, chroma_stft inkl. estimate_tuning/piptrack).

Gegen librosa pruefen, nie gegen eine eigene Vorstellung davon:
tools/autocue_parity.py (laeuft im .venv des Autocue-Projekts).

librosa steht unter der ISC-Lizenz (siehe THIRD_PARTY_LICENSES.md).

Gerechnet wird in Bloecken ueber die Frames, damit ein 10-Minuten-Track nicht
mehrere hundert MB Zwischenspeicher in float64 belegt; das Spektrogramm selbst
liegt wie bei librosa als float32 vor.
"""
from __future__ import annotations

import numpy as np

N_FFT = 2048
HOP = 512
# Frames je Rechenblock -- nur Speicher, aendert kein Ergebnis.
_BLOCK = 2048


def _hann(n: int) -> np.ndarray:
    """scipy.signal.get_window("hann", n, fftbins=True): periodisch, float64."""
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)


def stft_magnitude(y: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """
    np.abs(librosa.stft(y, n_fft, hop)) fuer float32-Eingabe.

    center=True mit pad_mode="constant" heisst: n_fft/2 Nullen an beiden Enden.
    librosa multipliziert Fenster (float64) und Frames (float32) in float64,
    transformiert und legt das Ergebnis als complex64 ab -- der Betrag ist also
    float32. Genau so hier.
    """
    y = np.asarray(y, dtype=np.float32)
    pad = n_fft // 2
    yp = np.concatenate([np.zeros(pad, np.float32), y, np.zeros(pad, np.float32)])
    n_frames = 1 + (len(yp) - n_fft) // hop if len(yp) >= n_fft else 0
    out = np.empty((n_fft // 2 + 1, n_frames), dtype=np.float32)
    if n_frames == 0:
        return out
    frames = np.lib.stride_tricks.as_strided(
        yp, shape=(n_frames, n_fft), strides=(yp.strides[0] * hop, yp.strides[0]), writeable=False)
    win = _hann(n_fft)
    for a in range(0, n_frames, _BLOCK):
        b = min(a + _BLOCK, n_frames)
        spec = np.fft.rfft(frames[a:b] * win, axis=1).astype(np.complex64)
        out[:, a:b] = np.abs(spec).T
    return out


def fft_frequencies(sr: float, n_fft: int = N_FFT) -> np.ndarray:
    return np.fft.rfftfreq(n=n_fft, d=1.0 / sr)


def _blocks(n: int):
    for a in range(0, n, _BLOCK):
        yield a, min(a + _BLOCK, n)


# ----------------------------------------------------------------------------
# Spektrale Form
# ----------------------------------------------------------------------------

def spectral_centroid(S: np.ndarray, freq: np.ndarray) -> np.ndarray:
    """sum(freq * S / sum(S)); Frames ohne Energie ergeben 0 (normalize, fill=None)."""
    out = np.zeros(S.shape[1])
    tiny = np.finfo(S.dtype).tiny
    for a, b in _blocks(S.shape[1]):
        blk = S[:, a:b]
        length = blk.astype(np.float64).sum(axis=0)
        length[length < tiny] = 1.0
        out[a:b] = (freq[:, None] * (blk / length).astype(np.float32)).sum(axis=0)
    return out


def spectral_rolloff(S: np.ndarray, freq: np.ndarray, roll_percent: float = 0.85) -> np.ndarray:
    """Kleinste Frequenz, bis zu der roll_percent der Energie liegen (cumsum in float32)."""
    out = np.zeros(S.shape[1])
    for a, b in _blocks(S.shape[1]):
        total = np.cumsum(S[:, a:b], axis=0)
        threshold = roll_percent * total[-1]
        idx = np.argmax(total >= threshold[None, :], axis=0)
        out[a:b] = freq[idx]
    return out


def spectral_flatness(S: np.ndarray, amin: float = 1e-10, power: float = 2.0) -> np.ndarray:
    """Geometrisches durch arithmetisches Mittel von max(amin, S**power)."""
    out = np.zeros(S.shape[1])
    for a, b in _blocks(S.shape[1]):
        st = np.maximum(amin, S[:, a:b] ** power)
        gmean = np.exp(np.mean(np.log(st), axis=0))
        amean = np.mean(st, axis=0)
        out[a:b] = gmean / amean
    return out


def power_to_db(S: np.ndarray, amin: float = 1e-10, top_db: float = 80.0) -> np.ndarray:
    """10*log10(max(amin, S)) mit ref=1, nach unten bei (Maximum - top_db) gekappt."""
    log_spec = 10.0 * np.log10(np.maximum(amin, S))
    return np.maximum(log_spec, log_spec.max() - top_db)


def onset_strength_db_power(S: np.ndarray) -> np.ndarray:
    """
    librosa.onset.onset_strength(S=librosa.power_to_db(S ** 2)) -- der Aufruf des
    Originals. Positiver Zuwachs je Frequenzbin von Frame zu Frame, gemittelt;
    vorn um lag + n_fft // (2 * hop) = 3 Frames verschoben, auf die Framezahl
    gekuerzt. Das dB-Spektrum entsteht blockweise; die top_db-Grenze haengt am
    globalen Maximum, das sich ohne die volle Matrix bestimmen laesst.
    """
    n = S.shape[1]
    if n < 2:
        return np.zeros(n)
    floor = 10.0 * np.log10(max(1e-10, float(np.max(S)) ** 2)) - 80.0
    diff_mean = np.zeros(n - 1)
    prev = None
    for a, b in _blocks(n):
        db = np.maximum(10.0 * np.log10(np.maximum(1e-10, S[:, a:b] ** 2)), floor)
        if prev is not None:
            db = np.concatenate([prev, db], axis=1)
            start = a - 1
        else:
            start = a
        d = np.maximum(0.0, db[:, 1:] - db[:, :-1]).mean(axis=0)
        diff_mean[start:start + d.size] = d
        prev = db[:, -1:]
    pad = 1 + N_FFT // (2 * HOP)
    return np.concatenate([np.zeros(pad), diff_mean])[:n]


def db_flux(S: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """features.py des Originals: Mittel des positiven Zuwachses von 20*log10(S), vorn 0."""
    n = S.shape[1]
    out = np.zeros(n)
    prev = None
    for a, b in _blocks(n):
        db = 20.0 * np.log10(np.maximum(S[:, a:b], eps))
        if prev is not None:
            db = np.concatenate([prev, db], axis=1)
            start = a
        else:
            start = a + 1
        d = np.maximum(0, db[:, 1:] - db[:, :-1]).mean(axis=0)
        out[start:start + d.size] = d
        prev = db[:, -1:]
    return out


# ----------------------------------------------------------------------------
# Mel
# ----------------------------------------------------------------------------

def _hz_to_mel(f: np.ndarray) -> np.ndarray:
    f = np.asarray(f, dtype=np.float64)
    f_sp = 200.0 / 3
    mels = f / f_sp
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = f >= min_log_hz
    mels = np.where(log_t, min_log_mel + np.log(np.maximum(f, 1e-12) / min_log_hz) / logstep, mels)
    return mels


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float64)
    f_sp = 200.0 / 3
    freqs = f_sp * m
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), freqs)


def mel_filter(sr: float, n_fft: int = N_FFT, n_mels: int = 128) -> np.ndarray:
    """librosa.filters.mel: Slaney-Skala, Slaney-Normierung, float32."""
    fmax = float(sr) / 2
    weights = np.zeros((n_mels, 1 + n_fft // 2), dtype=np.float32)
    fftfreqs = fft_frequencies(sr, n_fft)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fftfreqs)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels])
    weights *= enorm[:, np.newaxis]
    return weights


def mel_power(S: np.ndarray, sr: float, n_mels: int) -> np.ndarray:
    """librosa.feature.melspectrogram(S=S ** 2, n_mels): Filterbank mal Leistung."""
    basis = mel_filter(sr, N_FFT, n_mels)
    out = np.empty((n_mels, S.shape[1]), dtype=np.float32)
    for a, b in _blocks(S.shape[1]):
        out[:, a:b] = basis @ (S[:, a:b] ** 2)
    return out


# ----------------------------------------------------------------------------
# Chroma mit Stimmungsschaetzung
# ----------------------------------------------------------------------------

def _hz_to_octs(f: np.ndarray, tuning: float = 0.0, bins_per_octave: int = 12) -> np.ndarray:
    # dtype bleibt (librosa: float32-Tonhoehen aus piptrack -> float32-Oktaven).
    a440 = 440.0 * 2.0 ** (tuning / bins_per_octave)
    return np.log2(np.asanyarray(f) / (float(a440) / 16))


def estimate_tuning(S: np.ndarray, sr: float, n_fft: int = N_FFT,
                    resolution: float = 0.01, bins_per_octave: int = 12) -> float:
    """
    librosa.estimate_tuning(S=S) ueber piptrack (fmin 150, fmax 4000, threshold 0.1).

    piptrack sucht je Frame lokale Maxima ueber der Frequenz (nur Bins mit
    150 <= f < 4000 Hz und ueber 10 % des Frame-Maximums), verfeinert sie
    parabolisch und gewichtet mit der Amplitude. Gerechnet wird nur im
    Frequenzband plus je einem Nachbarbin -- dasselbe Ergebnis wie ueber die
    volle Matrix, weil jede Stelle nur ihre direkten Nachbarn braucht.
    """
    freqs = fft_frequencies(sr, n_fft)
    fmax = min(4000.0, float(sr) / 2)
    band = np.flatnonzero((freqs >= 150.0) & (freqs < fmax))
    if band.size == 0:
        return 0.0
    k0, k1 = int(band[0]), int(band[-1]) + 1          # [k0, k1)
    lo, hi = k0 - 1, min(k1 + 1, S.shape[0])
    pitches, mags = [], []
    for a, b in _blocks(S.shape[1]):
        blk = S[:, a:b]
        ref = np.float32(0.1) * blk.max(axis=0)
        sub = blk[lo:hi]                                   # Zeilen k0-1 .. k1
        masked = sub * (sub > ref[None, :])
        mid = masked[1:-1]
        # numba-Stencil: (x[0] > x[-1]) & (x[0] >= x[1]); Rand ausserhalb des Bands
        # trifft hier nie (Bin 0 und der letzte liegen ausserhalb von 150..4000 Hz).
        is_max = (mid > masked[:-2]) & (mid >= masked[2:])
        s_prev, s_mid, s_next = sub[:-2], sub[1:-1], sub[2:]
        a_ = s_next + s_prev - 2 * s_mid
        b_ = (s_next - s_prev) / 2
        with np.errstate(divide="ignore", invalid="ignore"):
            shift = np.where(np.abs(b_) >= np.abs(a_), np.float32(0), -b_ / a_).astype(np.float32)
        avg = b_                                            # np.gradient im Inneren
        dskew = np.float32(0.5) * avg * shift
        rows, cols = np.nonzero(is_max)
        k = rows + k0
        pitches.append(((k + shift[rows, cols]) * float(sr) / n_fft).astype(np.float32))
        mags.append((s_mid[rows, cols] + dskew[rows, cols]).astype(np.float32))
    pitch = np.concatenate(pitches) if pitches else np.zeros(0, np.float32)
    mag = np.concatenate(mags) if mags else np.zeros(0, np.float32)
    mask = pitch > 0
    if not mask.any():
        return 0.0
    threshold = np.median(mag[mask])
    sel = pitch[(mag >= threshold) & mask]
    sel = sel[sel > 0]
    if sel.size == 0:
        return 0.0
    residual = np.mod(bins_per_octave * _hz_to_octs(sel), 1.0)
    residual[residual >= 0.5] -= 1.0
    bins = np.linspace(-0.5, 0.5, int(np.ceil(1.0 / resolution)) + 1)
    counts, edges = np.histogram(residual, bins)
    return float(edges[np.argmax(counts)])


def chroma_filter(sr: float, n_fft: int = N_FFT, n_chroma: int = 12, tuning: float = 0.0,
                  ctroct: float = 5.0, octwidth: float = 2.0) -> np.ndarray:
    """librosa.filters.chroma (norm 2, base_c), float32."""
    frequencies = np.linspace(0, sr, n_fft, endpoint=False)[1:]
    frqbins = n_chroma * _hz_to_octs(frequencies, tuning=tuning, bins_per_octave=n_chroma)
    frqbins = np.concatenate(([frqbins[0] - 1.5 * n_chroma], frqbins))
    binwidthbins = np.concatenate((np.maximum(frqbins[1:] - frqbins[:-1], 1.0), [1]))
    D = np.subtract.outer(frqbins, np.arange(0, n_chroma, dtype="d")).T
    n_chroma2 = np.round(float(n_chroma) / 2)
    D = np.remainder(D + n_chroma2 + 10 * n_chroma, n_chroma) - n_chroma2
    wts = np.exp(-0.5 * (2 * D / np.tile(binwidthbins, (n_chroma, 1))) ** 2)
    length = np.sqrt(np.sum(np.abs(wts) ** 2, axis=0, keepdims=True))
    length[length < np.finfo(wts.dtype).tiny] = 1.0
    wts = wts / length
    wts *= np.tile(np.exp(-0.5 * ((frqbins / n_chroma - ctroct) / octwidth) ** 2), (n_chroma, 1))
    wts = np.roll(wts, -3 * (n_chroma // 12), axis=0)
    return np.ascontiguousarray(wts[:, :int(1 + n_fft / 2)], dtype=np.float32)


def chroma_stft(S: np.ndarray, sr: float) -> np.ndarray:
    """
    librosa.feature.chroma_stft(S=S): mit uebergebenem S nimmt librosa das
    Betragsspektrum, wie es ist (power=2 greift nur ohne S). Je Frame auf das
    Maximum normiert.
    """
    tuning = estimate_tuning(S, sr)
    fb = chroma_filter(sr, N_FFT, tuning=tuning)
    raw = np.empty((fb.shape[0], S.shape[1]), dtype=np.float32)
    for a, b in _blocks(S.shape[1]):
        raw[:, a:b] = fb @ S[:, a:b]
    length = np.abs(raw).astype(float).max(axis=0, keepdims=True)
    length[length < np.finfo(np.float32).tiny] = 1.0
    return (raw / length).astype(np.float32)
