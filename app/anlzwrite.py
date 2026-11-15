"""
Erzeugt Rekordbox' Analysedateien (ANLZ0000.DAT/.EXT/.2EX) fuer einen Track, den Rekordbox
noch nicht kennt. Bytes, Rekordbox' Datenbank fasst rekordbox.add_track() an.

Die Wellenformen sind ein Nachbau von rbxport (github.com/chrisle/rbxport, GPL-2.0-oder-
spaeter, crates/rbl-analysis/src/waveform.rs und crates/rbl-anlz/src/encode.rs), Konstanten
unveraendert. Die Filter laufen als FFT-Faltung statt je Sample in Python, das Ergebnis
weicht nur um die abgeschnittene Impulsantwort ab. Empirische Naeherung, nicht Rekordbox'
eigene Rechnung: PWV4-PWV7 ersetzt Rekordbox beim ersten Laden selbst, Grid, PSSI, PWV3,
PWAV und PWV2 bleiben byteweise stehen (an AIF, FLAC, M4A, WAV und MP3 geprueft).

Original: Copyright der rbxport-Autoren, GPL-2.0-oder-spaeter. Diese Datei ist eine
veraenderte Fassung (2026, Sebastian Gabler: Uebertragung nach Python, FFT-Faltung) und
wird nach der "oder spaeter"-Klausel unter GPL-3.0-oder-spaeter weitergegeben (LICENSE,
NOTICE.md, THIRD_PARTY_LICENSES.md).
"""
from __future__ import annotations

import struct
import subprocess

import numpy as np

from . import media

_OVERVIEW = 1200
_PREVIEW = 400
_TINY = 100
_IR_LEN = 8192

_MP3_KBPS = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
             2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_AAC_PRIMING = 2112
_MP3_FRAME = 1152
_MP3_BASE = 528


# ── Versatz zu Rekordbox ────────────────────────────────────────────────────

def _mp3_head(path: str) -> tuple[bytes, bytes] | None:
    """(erster Frame, Kopf des zweiten) nach einem ID3v2-Block. None = nicht lesbar."""
    with open(path, "rb") as f:
        raw = f.read(1 << 20)
        pos = 0
        if raw[:3] == b"ID3" and len(raw) >= 10:
            size = 0
            for byte in raw[6:10]:
                size = (size << 7) | (byte & 0x7F)
            pos = 10 + size
            if pos + 8192 > len(raw):
                f.seek(pos)
                raw = f.read(1 << 18)
                pos = 0
    while pos + 4 < len(raw) and not (raw[pos] == 0xFF and (raw[pos + 1] & 0xE0) == 0xE0):
        pos += 1
    if pos + 4 >= len(raw):
        return None
    h = raw[pos:pos + 4]
    version, br_idx, sr_idx = (h[1] >> 3) & 3, h[2] >> 4, (h[2] >> 2) & 3
    if version == 1 or br_idx in (0, 15) or sr_idx == 3:
        return None
    kbps = _MP3_KBPS[3 if version == 3 else 2][br_idx]
    rate = _MP3_RATES[version][sr_idx]
    length = (144 if version == 3 else 72) * kbps * 1000 // rate + ((h[2] >> 1) & 1)
    return raw[pos:pos + length], raw[pos + length:pos + length + 4]


def mp3_lead_samples(path: str) -> int | None:
    """Um wie viele Samples Rekordbox eine MP3 spaeter spielt, als ffmpeg sie dekodiert.
    Ohne Xing/Info-Kopf 0, sonst 528 + Delay aus dem Kopf, plus einen Frame, wenn Info-Frame
    und erster Audio-Frame denselben Kanalmodus haben. Die Kennung im Kopf (LAME, Lavc)
    entscheidet das nicht. Getroffen: 1653 von 1722 Dateien einer echten Sammlung, 12 von 12
    frisch analysierten; die Fehltreffer sehen nach Analysen aelterer Dateifassungen aus.
    Nur bei 44,1 kHz gemessen. None = seltener Kopf (mp3p, leer ...), den die Regel nicht
    abdeckt, dann muss Rekordbox selbst analysieren."""
    head = _mp3_head(path)
    if head is None:
        return None
    frame, second = head
    at = next((i for i in (frame.find(m, 0, 64) for m in (b"Xing", b"Info")) if i >= 0), -1)
    if at < 0:
        return 0
    flags = struct.unpack(">I", frame[at + 4:at + 8])[0]
    tag = at + 8 + sum(n for bit, n in ((1, 4), (2, 4), (4, 100), (8, 4)) if flags & bit)
    if not frame[tag:tag + 9].startswith((b"LAME", b"Lav")) or len(frame) < tag + 24 or len(second) < 4:
        return None
    delay = (frame[tag + 21] << 4) | (frame[tag + 22] >> 4)
    same_mode = (frame[3] >> 6) == (second[3] >> 6)
    return _MP3_BASE + delay + (_MP3_FRAME if same_mode else 0)


def lead_samples(path: str, codec_family: str) -> int | None:
    """Versatz zu Rekordbox in Samples der Datei. Verlustfrei 0 (AIF, FLAC, WAV geprueft),
    AAC der Encoder-Vorlauf, den ffmpeg abschneidet (56 von 56). None = unbekannt."""
    if codec_family == "lossless":
        return 0
    if codec_family == "lossy_aac":
        return _AAC_PRIMING
    if codec_family == "lossy_mp3":
        return mp3_lead_samples(path)
    return None


# ── Messung ─────────────────────────────────────────────────────────────────

def _decode(path: str) -> tuple[np.ndarray, int, int]:
    """Mono in nativer Samplerate als (Samples, Rate, Bittiefe)."""
    info = subprocess.run(
        [media.ffprobe_path(), "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,bits_per_sample,bits_per_raw_sample", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60).stdout.strip().split(",")
    rate = int(info[0])
    depth = max([int(v) for v in info[1:] if v.strip().isdigit()] + [0]) or 16
    out = subprocess.run(
        [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
         "-af", "aformat=sample_fmts=fltp:channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1",
         "-f", "f32le", "-"], capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    return np.frombuffer(out.stdout, dtype="<f4").astype(np.float64), rate, depth


def _biquad(rate: int, cutoff: float, highpass: bool) -> np.ndarray:
    """Impulsantwort eines Butterworth-Abschnitts zweiter Ordnung."""
    w = 2.0 * np.pi * min(cutoff, rate * 0.45) / rate
    c, alpha = np.cos(w), np.sin(w) / np.sqrt(2)
    a0 = 1.0 + alpha
    b = ((1 + c) / 2, -(1 + c), (1 + c) / 2) if highpass else ((1 - c) / 2, 1 - c, (1 - c) / 2)
    b = [v / a0 for v in b]
    a = (-2 * c / a0, (1 - alpha) / a0)
    z1 = z2 = 0.0
    out = np.zeros(_IR_LEN)
    for i in range(_IR_LEN):
        x = 1.0 if i == 0 else 0.0
        y = b[0] * x + z1
        z1 = b[1] * x - a[0] * y + z2
        z2 = b[2] * x - a[1] * y
        out[i] = y
    return out


def _one_pole(rate: int, cutoff: float) -> np.ndarray:
    a = min(max(np.exp(-2 * np.pi * cutoff / rate), 0.0), 0.9999)
    return (1 - a) * a ** np.arange(_IR_LEN)


def _measure(x: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """(Spalten [tief, mitte, hoch, Spitze] 0..255 je 1/150 s, Gesamtansicht 0..127)."""
    per = max(int(rate / 150.0), 1)
    size = 1 << int(np.ceil(np.log2(len(x) + 2 * _IR_LEN)))
    spectrum = np.fft.rfft(x, size)

    def filtered(*impulses):
        y = spectrum
        for h in impulses:
            y = y * np.fft.rfft(h, size)
        return np.fft.irfft(y, size)[:len(x)]

    low = filtered(_one_pole(rate, 200.0))
    below_2k = filtered(_one_pole(rate, 2000.0))
    starts = np.arange(0, len(x), per)
    cols = np.zeros((len(starts), 4), dtype=np.int64)
    for k, sig in enumerate((low, below_2k - low, x - below_2k, x)):
        cols[:, k] = (np.clip(np.maximum.reduceat(np.abs(sig), starts), 0, 1) * 255).astype(np.int64)

    mid = filtered(_biquad(rate, 200.0, True), _biquad(rate, 2000.0, False))
    high = filtered(_biquad(rate, 2000.0, True))
    bucket = np.minimum((np.arange(1, len(x) + 1) * _OVERVIEW - 1) // len(x), _OVERVIEW - 1)
    counts = np.maximum(np.bincount(bucket, minlength=_OVERVIEW), 1).astype(np.float64)
    raw = np.stack([np.bincount(bucket, s, minlength=_OVERVIEW) / counts
                    for s in (low * low, np.abs(mid), np.abs(high))], axis=1)
    raw[:, 0] = raw[:, 0] ** 0.45
    energy = np.add.reduceat(x * x, starts)
    level = np.sqrt(energy / np.diff(np.append(starts, len(x)))).sum()
    target = level / len(starts) * 92.0
    means = raw.mean(axis=0)
    smooth = (raw + np.vstack([raw[0], raw[:-1]])) / 2
    overview = np.where(means[None, :] < 1e-6, 0,
                        np.clip(np.rint(smooth * target / np.maximum(means, 1e-12)), 0, 127))
    return cols, overview.astype(np.int64)


# ── Kodierung ───────────────────────────────────────────────────────────────

def _resample(cols: np.ndarray, n: int) -> np.ndarray:
    """n Spalten: das Lauteste je Abschnitt."""
    starts = np.arange(n) * len(cols) // n
    ends = np.minimum(np.maximum((np.arange(n) + 1) * len(cols) // n, starts + 1), len(cols))
    return np.stack([cols[a:b].max(axis=0) for a, b in zip(starts, ends)])


def _band_height(c: np.ndarray, ceiling: int) -> np.ndarray:
    loud = np.max(c[:, :3] * 44 // 255, axis=1)
    return np.minimum(loud * ceiling // 44, ceiling)


def _detail_height(c: np.ndarray) -> np.ndarray:
    full = 255 * 255
    return np.minimum((31 * c[:, 3] * c[:, 3] + full // 2) // full, 31)


def _whiteness(c: np.ndarray) -> np.ndarray:
    total = c[:, :3].sum(axis=1)
    return np.minimum(np.where(total > 0, (c[:, 2] * 7 + total // 2) // np.maximum(total, 1), 0), 7)


def _pwv5(c: np.ndarray) -> bytes:
    low, mid, high = 2 * c[:, 0], 2 * c[:, 1], 3 * c[:, 2]
    strongest = np.maximum(np.maximum(low, mid), high)
    safe = np.maximum(strongest, 1)

    def share(value, full):
        return np.where(strongest > 0, (2 * full * value + strongest) // (2 * safe), 0)

    word = (share(low, 7) << 13) | (share(mid, 5) << 10) | (share(high, 7) << 7) | (_detail_height(c) << 2)
    return word.astype(">u2").tobytes()


def _pwv4(c: np.ndarray) -> bytes:
    r = _resample(c, _OVERVIEW)
    out = np.zeros((_OVERVIEW, 6), dtype=np.uint8)
    out[:, 0], out[:, 3] = r[:, 3] >> 1, r[:, 0] >> 1
    out[:, 4], out[:, 5] = (r[:, 1] >> 1) * 4 // 5, (r[:, 2] >> 1) * 13 // 20
    return out.tobytes()


def _pwav(c: np.ndarray) -> bytes:
    r = _resample(c, _PREVIEW)
    # Keine Spalte 0: Player halten das fuer eine unfertige Vorschau.
    return ((_whiteness(r) << 5) | np.maximum(_band_height(r, 25), 2)).astype(np.uint8).tobytes()


def _pwv2(c: np.ndarray) -> bytes:
    return np.clip(_band_height(_resample(c, _TINY), 15), 1, 15).astype(np.uint8).tobytes()


# ── Dateien ─────────────────────────────────────────────────────────────────
# PMAI-Rahmen und Kopffelder wie in Rekordbox' eigenen Dateien; die Kopflaenge ist je Tag
# verschieden (PWV6 20 statt 24), Fehler dort werfen die ganze Datei raus.

_HEADER_EXTRA = bytes((0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0))


def _section(tag: bytes, header: bytes, payload: bytes) -> bytes:
    return tag + struct.pack(">II", 12 + len(header), 12 + len(header) + len(payload)) + header + payload


def _file(sections: list[bytes]) -> bytes:
    body = b"".join(sections)
    head = 12 + len(_HEADER_EXTRA)
    return b"PMAI" + struct.pack(">II", head, head + len(body)) + _HEADER_EXTRA + body


def _ppth(name: str) -> bytes:
    text = ("?/" + name).encode("utf-16-be") + b"\0\0"
    return _section(b"PPTH", struct.pack(">I", len(text)), text)


def _preview(tag: bytes, data: bytes) -> bytes:
    return _section(tag, struct.pack(">II", len(data), 0x00010000), data)


def _scroll(tag: bytes, stride: int, data: bytes) -> bytes:
    header = struct.pack(">II", stride, len(data) // stride)
    if tag != b"PWV6":
        header += struct.pack(">I", {b"PWV5": 0x00960305, b"PWV4": 0}.get(tag, 0x00960000))
    return _section(tag, header, data)


def _cue_lists(extended: bool) -> list[bytes]:
    """Leere Listen: Rekordbox fuehrt Cues in der Datenbank."""
    out = [_section(b"PCOB", struct.pack(">IHHI", k, 0, 0, 0xFFFFFFFF), b"") for k in (1, 0)]
    if extended:
        out += [_section(b"PCO2", struct.pack(">IHH", k, 0, 0), b"") for k in (1, 0)]
    return out


def build(path: str, name: str, beats: list[list], lead: int) -> dict:
    """Analysedateien ohne PSSI und ohne Datei-Index (setzt rekordbox.py).
    beats: [[ms, Nummer, BPM], ...] schon auf Rekordbox' Achse, lead: Versatz in Samples.
    Liefert {"files": {Dateiname: Bytes}, "rate", "depth", "seconds"}."""
    x, rate, depth = _decode(path)
    if not len(x):
        raise RuntimeError("Keine Audiodaten")
    if lead:
        x = np.concatenate([np.zeros(lead), x])
    cols, overview = _measure(x, rate)
    grid = b"".join(struct.pack(">HHI", int(n), int(round(bpm * 100)), int(ms)) for ms, n, bpm in beats)
    pwv3 = ((_whiteness(cols) << 5) | _detail_height(cols)).astype(np.uint8).tobytes()
    dat = _file([_ppth(name),
                 _section(b"PVBR", struct.pack(">I", 0), b"\0" * 1604),
                 _section(b"PQTZ", struct.pack(">III", 0, 0x00080000, len(beats)), grid),
                 _preview(b"PWAV", _pwav(cols)), _preview(b"PWV2", _pwv2(cols))] + _cue_lists(False))
    ext = _file([_ppth(name), _scroll(b"PWV3", 1, pwv3)] + _cue_lists(True)
                + [_scroll(b"PWV5", 2, _pwv5(cols)), _scroll(b"PWV4", 6, _pwv4(cols))])
    two = _file([_ppth(name), _scroll(b"PWV7", 3, (cols[:, :3] >> 1).astype(np.uint8).tobytes()),
                 _scroll(b"PWV6", 3, overview.astype(np.uint8).tobytes())])
    return {"files": {"ANLZ0000.DAT": dat, "ANLZ0000.EXT": ext, "ANLZ0000.2EX": two},
            "rate": rate, "depth": depth, "seconds": len(x) / rate}
