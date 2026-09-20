"""
Spektralanalyse: misst den tatsaechlichen Frequenz-Cutoff einer Datei.

Ablauf:
  1. ffmpeg dekodiert die Datei nach Mono-Float32 in NATIVER Samplerate
     (kein Resampling — das wuerde eigene Artefakte an der Bandgrenze bauen).
  2. Signal in Bloecke schneiden, leise Bloecke verwerfen. Das ist der
     wichtigste Schritt gegen Fehlalarme: leise Passagen haben von Natur aus
     keine Hoehen und wuerden sonst einen Tiefpass vortaeuschen.
  3. Pro Frequenzbin das Perzentil ueber alle Bloecke (robustes Peak-Hold).
     Ein Encoder-Tiefpass bleibt darin als harte Kante stehen, waehrend
     inhaltlich hoehenarme Stellen herausgemittelt werden.
  4. Die staerkste Kante im Spektrum suchen. Ein Encoder-Tiefpass ist eine
     Brickwall: unterhalb Musik, oberhalb exakte Null. Findet sich keine
     ausreichend steile Kante, hat die Datei gar keinen Tiefpass — dann gilt
     der Inhalt als bis zur Bandgrenze reichend.

Die Kantensuche ist bewusst der einzige Massstab. Ein fester Pegelschwellwert
scheitert an verlustfreiem Material (dort liegt oben echter Inhalt statt
Stille) und an bandbegrenzten Mastern (die weich ausrollen statt abzubrechen).
Die Kante trennt beides sauber: nur ein Encoder schneidet senkrecht ab.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import numpy as np

from . import media

_EPS = 1e-20


@dataclass
class SpectralResult:
    ok: bool = False
    error: str = ""
    cutoff_hz: float = 0.0
    raw_cutoff_hz: float = 0.0       # Position der Kante ohne cliff_min_db-Filter
    steepness_db: float = 0.0        # Hoehe der staerksten Kante
    is_brickwall: bool = False
    nyquist_hz: float = 0.0
    at_nyquist: bool = False         # Inhalt reicht bis an die Bandgrenze
    ref_level_db: float = 0.0
    noise_floor_db: float = 0.0
    threshold_db: float = 0.0
    gated_blocks: int = 0
    total_blocks: int = 0
    spectrum: list = field(default_factory=list)   # (freq_hz, db) fuer Report


def _downmix_filter(channels: int) -> list[str]:
    """Explizite Kanalgewichtung statt '-ac 1'.

    An mindestens einer ffmpeg-Version (9.0.1) haengt der automatische
    '-ac 1'-Downmix am Channel-Layout-Tag des Quellformats: bei MP3 sauber,
    bei PCM-Containern (WAV/AIFF/FLAC) liefert er teils grob verrauschte
    Ergebnisse (bis zu 0,4 Amplitude Abweichung gegenueber einem simplen
    Mittelwert der Kanaele) und maskiert damit jede Tiefpasskante. Ein
    expliziter 'pan'-Filter mit fest ausgeschriebenen Gewichten umgeht die
    Layout-Erkennung komplett und ist unabhaengig vom Container korrekt.
    """
    if channels <= 1:
        return []
    weight = 1.0 / channels
    expr = "+".join(f"{weight:.6f}*c{i}" for i in range(channels))
    return ["-af", f"pan=mono|c0={expr}"]


def decode_mono(path: str, sample_rate: int, max_seconds: float,
                 channels: int = 2) -> np.ndarray:
    """Dekodiert nach Mono-Float32 in nativer Samplerate."""
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-t", f"{max_seconds:.3f}",
        *_downmix_filter(channels),
        "-f", "f32le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    return np.frombuffer(out.stdout, dtype="<f4")


def _smooth(spec_db: np.ndarray, bin_hz: float, width_hz: float) -> np.ndarray:
    n = max(1, int(round(width_hz / max(bin_hz, _EPS))))
    if n <= 1:
        return spec_db
    kernel = np.ones(n, dtype=np.float64) / n
    padded = np.pad(spec_db, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _window_means(spec_db: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Mittelwert der n Bins unterhalb bzw. oberhalb jeder Position."""
    cum = np.concatenate([[0.0], np.cumsum(spec_db)])
    idx = np.arange(spec_db.size)
    lo = (cum[idx] - cum[np.maximum(idx - n, 0)]) / np.maximum(
        idx - np.maximum(idx - n, 0), 1
    )
    hi_end = np.minimum(idx + n, spec_db.size)
    hi = (cum[hi_end] - cum[idx]) / np.maximum(hi_end - idx, 1)
    return lo, hi


def _find_edge(freqs: np.ndarray, smooth_db: np.ndarray, nyquist: float,
               cfg: dict) -> tuple[float, float, float]:
    """
    Sucht die staerkste Abwaertskante im oberen Spektrum.

    Rueckgabe: (cutoff_hz, kantenhoehe_db, halbwert_db).
    Die Kantenhoehe ist der Pegelunterschied zwischen dem Band unter und dem
    Band ueber der Kante — bei einem Encoder-Tiefpass typisch 30-60 dB, bei
    einem natuerlich ausrollenden Master nur wenige dB.
    """
    bin_hz = float(freqs[1] - freqs[0])
    span_bins = max(3, int(round(float(cfg["cliff_span_hz"]) / bin_hz)))

    lo_avg, hi_avg = _window_means(smooth_db, span_bins)
    drop = lo_avg - hi_avg

    # Nur Positionen mit vollstaendigen Fenstern auf beiden Seiten pruefen
    valid = np.zeros(freqs.size, dtype=bool)
    start = max(span_bins, int(np.searchsorted(freqs, float(cfg["search_floor_hz"]))))
    end = freqs.size - span_bins
    if end <= start:
        return float(nyquist), 0.0, 0.0
    valid[start:end] = True

    masked = np.where(valid, drop, -np.inf)
    best = int(np.argmax(masked))
    best_drop = float(masked[best])
    if not np.isfinite(best_drop):
        return float(nyquist), 0.0, 0.0

    # Kantenmitte: hoechste Frequenz, die noch ueber dem Halbwert liegt
    half = (lo_avg[best] + hi_avg[best]) / 2.0
    lo_i = max(0, best - span_bins)
    hi_i = min(freqs.size, best + span_bins)
    window = smooth_db[lo_i:hi_i]
    above = np.flatnonzero(window >= half)
    cutoff_i = lo_i + int(above[-1]) if above.size else best
    return float(freqs[cutoff_i]), best_drop, float(half)


def analyse(path: str, sample_rate: int, duration_s: float, cfg: dict,
            channels: int = 2) -> SpectralResult:
    res = SpectralResult()
    nyquist = sample_rate / 2.0
    res.nyquist_hz = nyquist

    max_s = min(float(cfg["max_analysis_s"]), max(duration_s, 0.0))
    try:
        signal = decode_mono(path, sample_rate, max_s, channels)
    except Exception as exc:                      # noqa: BLE001
        res.error = f"decode: {exc}"
        return res

    if signal.size < sample_rate:
        res.error = "Zu wenig dekodierte Samples"
        return res

    # Anfang und Ende aussparen (Intro-Stille, Fade-Out)
    head = int(signal.size * float(cfg["skip_head_pct"]))
    tail = int(signal.size * float(cfg["skip_tail_pct"]))
    core = signal[head: signal.size - tail] if signal.size - head - tail > sample_rate else signal

    nfft = int(cfg["fft_size"])
    n_blocks = core.size // nfft
    if n_blocks < 2:
        res.error = "Zu kurz für die Blockanalyse"
        return res
    blocks = core[: n_blocks * nfft].reshape(n_blocks, nfft).astype(np.float64)
    res.total_blocks = n_blocks

    # --- Gating: nur die lauteren Bloecke tragen zur Messung bei ---
    rms = np.sqrt(np.mean(blocks * blocks, axis=1) + _EPS)
    rms_db = 20.0 * np.log10(rms + _EPS)
    rel_thr = np.percentile(rms_db, float(cfg["gate_rms_percentile"]))
    keep = (rms_db >= rel_thr) & (rms_db >= float(cfg["gate_abs_dbfs"]))
    if int(keep.sum()) < int(cfg["min_gated_blocks"]):
        keep = rms_db >= np.percentile(rms_db, 90.0)   # Notfall: lauteste 10 %
    if int(keep.sum()) < 2:
        res.error = "Zu wenig laute Blöcke (sehr leise Datei?)"
        return res
    res.gated_blocks = int(keep.sum())

    # --- Spektren der gewaehlten Bloecke ---
    win = np.hanning(nfft)
    win_gain = np.sum(win ** 2)
    spec = np.abs(np.fft.rfft(blocks[keep] * win, axis=1)) ** 2
    spec /= win_gain
    power = np.percentile(spec, float(cfg["block_percentile"]), axis=0)
    spec_db = 10.0 * np.log10(power + _EPS)

    freqs = np.fft.rfftfreq(nfft, d=1.0 / sample_rate)
    bin_hz = freqs[1] - freqs[0]
    smooth_db = _smooth(spec_db, bin_hz, float(cfg["smooth_hz"]))

    # --- Referenzpegel (Mitten) und Rauschboden (oberstes Band) ---
    lo, hi = cfg["ref_band_hz"]
    ref_mask = (freqs >= float(lo)) & (freqs <= float(hi))
    res.ref_level_db = float(np.median(smooth_db[ref_mask])) if ref_mask.any() else 0.0

    noise_mask = freqs >= nyquist * float(cfg["noise_band_rel"])
    res.noise_floor_db = (
        float(np.median(smooth_db[noise_mask])) if noise_mask.any() else -200.0
    )

    # --- Cutoff ueber die staerkste Kante ---
    cutoff, drop, threshold = _find_edge(freqs, smooth_db, nyquist, cfg)
    res.raw_cutoff_hz = cutoff
    res.steepness_db = drop
    res.threshold_db = threshold
    res.is_brickwall = drop >= float(cfg["steep_brickwall_db"])

    if drop < float(cfg["cliff_min_db"]):
        # Keine Kante: die Datei ist oben nicht beschnitten.
        res.cutoff_hz = nyquist
        res.at_nyquist = True
    else:
        res.cutoff_hz = cutoff
        res.at_nyquist = cutoff >= nyquist * 0.985

    res.spectrum = _downsample_spectrum(freqs, smooth_db, int(cfg["spectrum_points"]))
    res.ok = True
    return res


def _downsample_spectrum(freqs: np.ndarray, spec_db: np.ndarray, points: int) -> list:
    """Kompaktes Spektrum fuer die Miniatur-Grafik im HTML-Report."""
    if points <= 0 or freqs.size == 0:
        return []
    edges = np.linspace(0.0, freqs[-1], points + 1)
    idx = np.clip(np.searchsorted(freqs, edges) , 0, freqs.size)
    out = []
    for i in range(points):
        a, b = idx[i], max(idx[i] + 1, idx[i + 1])
        if a >= freqs.size:
            break
        val = float(np.max(spec_db[a:b]))
        out.append([round(float((edges[i] + edges[i + 1]) / 2.0), 1), round(val, 1)])
    return out
