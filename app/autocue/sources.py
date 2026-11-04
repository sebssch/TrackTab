"""
Schritt 2 der Analysekette: Signalquellen bereitstellen.

Bevor Merkmale berechnet werden koennen, muss klar sein, WORAUS. Die Quelle sind
vier Frequenzbaender (Sub, Bass, Mid, High) desselben Spektrogramms: guenstig,
immer verfuegbar, und genau das, worauf die gelernten Ankermodelle trainiert sind.

In TrackTab dekodiert ffmpeg (wie in bpmkey.py/spectral.py, Mono als Mittel der
Kanaele = librosa.to_mono) direkt nach 22.050 Hz; das Original lud ueber
librosa.load (soxr). Das Spektrogramm rechnet dsp.stft_magnitude() wie
librosa.stft.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

from app import media, spectral
from app.autocue import dsp

# Analyse-Abtastrate. 22050 Hz reicht: Alles, was fuer die Struktur zaehlt, liegt
# unter 11 kHz, und die halbe Rate kostet die halbe Rechenzeit.
ANALYSIS_SR = 22050
N_FFT = dsp.N_FFT
HOP_LENGTH = dsp.HOP

# Frequenzbaender in Hz. Die Grenzen folgen dem, was beim Auflegen zaehlt:
# Sub = das, was im Bauch ankommt; Bass = Grundton der Basslinie; Mid = Melodie,
# Synth, Stimme; High = Hats, Riser, Luft.
BANDS = {
    "sub":  (20, 120),
    "bass": (120, 250),
    "mid":  (250, 4000),
    "high": (4000, 11000),
}


class TooLongError(RuntimeError):
    """Laenger als das eingestellte Limit (bpmkey_max_minutes) -- wird nicht gemessen."""


@dataclass
class SourceSignals:
    """
    Die Signale, aus denen die Merkmale entstehen.

    `bands` bildet einen Namen auf die Zeilen des Spektrogramms ab, die zu ihm
    gehoeren (Original: Kopien dieser Zeilen; hier Indexbereiche, das spart bei
    langen Tracks rund 60 MB).
    """
    sample_rate: int
    hop_length: int
    spectrogram: np.ndarray                         # Betragsspektrogramm, (freq, frames), float32
    frequencies: np.ndarray                         # Mittenfrequenz je Zeile
    bands: dict

    @property
    def n_frames(self) -> int:
        return int(self.spectrogram.shape[1])

    def frame_times(self) -> np.ndarray:
        return np.arange(self.n_frames) * self.hop_length / self.sample_rate


def _info(path: str) -> tuple[int, float]:
    """(Kanalzahl, Dauer in s) per mutagen; 0 = unbekannt."""
    try:
        from mutagen import File
        info = File(path).info
        return (int(getattr(info, "channels", 0) or 0),
                float(getattr(info, "length", 0) or 0))
    except Exception:                                  # noqa: BLE001
        return 0, 0.0


def _decode(path: str, max_seconds: float) -> np.ndarray:
    channels, length = _info(path)
    if max_seconds and length > max_seconds:
        raise TooLongError(path)
    downmix = spectral._downmix_filter(channels) if channels > 0 else ["-ac", "1"]
    cmd = [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0"]
    if max_seconds:
        cmd += ["-t", f"{max_seconds + 1:.3f}"]
    cmd += [*downmix, "-ar", str(ANALYSIS_SR), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200] or "Dekodieren fehlgeschlagen")
    y = np.frombuffer(out.stdout, dtype="<f4")
    if max_seconds and y.size > max_seconds * ANALYSIS_SR:
        raise TooLongError(path)
    return y


def sources_from_audio(y: np.ndarray, sr: int = ANALYSIS_SR) -> SourceSignals:
    """Aus fertigem Mono-Audio (auch fuer den Abgleich gegen librosa, siehe tools/)."""
    spec = dsp.stft_magnitude(y, N_FFT, HOP_LENGTH)
    freqs = dsp.fft_frequencies(sr, N_FFT)
    bands = {}
    for name, (low, high) in BANDS.items():
        idx = np.flatnonzero((freqs >= low) & (freqs < high))
        bands[name] = (int(idx[0]), int(idx[-1]) + 1) if idx.size else None
    return SourceSignals(sample_rate=sr, hop_length=HOP_LENGTH, spectrogram=spec,
                         frequencies=freqs, bands=bands)


def load_sources(audio_path: str, max_seconds: float = 0.0) -> SourceSignals:
    """
    Signalquellen fuer einen Track bereitstellen.

    Das Audio wird genau EINMAL dekodiert und EINMAL in ein Spektrogramm
    ueberfuehrt; die Baender sind Ausschnitte daraus.
    """
    return sources_from_audio(_decode(audio_path, max_seconds))
