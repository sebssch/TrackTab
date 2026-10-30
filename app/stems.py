"""
Stem-Trennung (Acapella / Instrumental) mit Demucs v4 (htdemucs), ausgefuehrt
ueber onnxruntime -- lokal, ohne Netz.

Modell (base_dir()/models/demucs/, vom Nutzer in den Einstellungen geladen --
die Gewichte werden bewusst NICHT mit der App ausgeliefert, Lizenz siehe
THIRD_PARTY_LICENSES.md; im Entwicklungsbetrieb gilt ein vorhandenes
vendor/models/demucs/ weiter):
  - htdemucs.onnx + htdemucs.json: der Graph beginnt NACH der STFT und endet
    VOR der iSTFT. Eingaben: `mix` (Wellenform, 2 x segment_samples) und `mag`
    (Spektrogramm im "complex as channels"-Format, 4 x 2048 x 336). Ausgaben:
    `spec` (Spektrogramm je Quelle) und `wav` (Zeitbereichs-Anteil je Quelle).
    STFT, iSTFT und Ueberlappung der Abschnitte rechnet dieses Modul nach
    (Nachbau von demucs.htdemucs._spec/_ispec und demucs.apply.apply_model).

Fehlt onnxruntime oder das Modell, meldet available() False und die
Oberflaeche blendet die Funktion aus.

Trennung wie in bpmkey.py: separate() rechnet (teuer, schreibt nur in den
uebergebenen Ordner), das Uebernehmen von Tags/Cover und Eintragen in
Datenbank/Music/Rekordbox gehoert dem Aufrufer (server.py).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import media

MODEL_NAME = "htdemucs"

# Zusammensetzung der beiden Ergebnisse aus den vier Quellen des Modells
# (Reihenfolge laut JSON: drums, bass, other, vocals).
ACAPELLA = "acapella"
INSTRUMENTAL = "instrumental"
KINDS = (ACAPELLA, INSTRUMENTAL)

_OVERLAP = 0.25                                   # wie demucs.apply (Vorgabe, "genau")
_OVERLAP_FAST = 0.10                               # weniger Abschnitte, ~17 % schneller
_PAD_HOPS = 3                                     # hl//2*3 aus htdemucs._spec

# Die Laufzeit gibt keine Zusage fuer parallele Aufrufe derselben Session aus
# mehreren Server-Threads. Dekodieren laeuft parallel, nur das Rechnen nicht.


class TooLongError(RuntimeError):
    """Datei ist laenger als stems_max_minutes -- bewusst nicht getrennt
    (DJ-Mixe: Rechenzeit im Stundenbereich, kaum sinnvolles Ergebnis)."""


class Cancelled(RuntimeError):
    """Abbruch durch den Nutzer (cancel-Rueckruf lieferte True)."""


# Quelle des ONNX-Exports (STFT/iSTFT ausserhalb des Graphen). Gewichte:
# Meta/Demucs, Nutzung laut Projekt zu wissenschaftlichen Zwecken -- deshalb
# laedt der Nutzer sie selbst (Einstellungen), siehe THIRD_PARTY_LICENSES.md.
MODEL_BASE_URL = "https://huggingface.co/HRSadeghi/demucs-onnx/resolve/main"
MODEL_SIZE = {"htdemucs.json": 279, "htdemucs.onnx": 168683222}   # nur fuer den Fortschritt
MODEL_SHA256 = {
    "htdemucs.json": "d808ba80fc27032bf9e035af8de7efc1c31306a3c156322e1c56d8312f3b4117",
    "htdemucs.onnx": "abaac48bc8fd55fa672268c8f13228b9b00f52f0c4cd10945048982eca4643bc",
}


def _user_model_dir() -> Path:
    return cfgmod.base_dir() / "models" / "demucs"


def model_dir() -> Path:
    """Ablage der geladenen Modelldatei. Im Entwicklungsbetrieb zaehlt ein
    schon vorhandenes vendor/models/demucs/ (Stand vor dem Umbau auf
    Download) weiter, sonst immer der beschreibbare Nutzerordner."""
    user = _user_model_dir()
    if not cfgmod.is_frozen():
        legacy = cfgmod.vendor_dir() / "models" / "demucs"
        if all((legacy / f"{MODEL_NAME}{e}").is_file() for e in (".onnx", ".json")) \
                and not all((user / f"{MODEL_NAME}{e}").is_file() for e in (".onnx", ".json")):
            return legacy
    return user


@lru_cache(maxsize=1)
def _ort():
    """onnxruntime-Modul oder None. Lazy und gecacht -- der Import laedt eine
    grosse native Bibliothek, das soll weder Start noch Worker bremsen."""
    try:
        import onnxruntime
        return onnxruntime
    except Exception:                                  # noqa: BLE001
        return None


def _model_files() -> list[Path]:
    return [model_dir() / f"{MODEL_NAME}{ext}" for ext in (".onnx", ".json")]


def available() -> bool:
    return _ort() is not None and all(p.is_file() for p in _model_files())


def _reset_caches() -> None:
    _meta.cache_clear()
    _drop_sessions()


def model_size() -> int:
    return sum(p.stat().st_size for p in _model_files() if p.is_file())


# ── Modell laden / entfernen ──────────────────────────────────────────────

class DownloadError(RuntimeError):
    pass


_DL_LOCK = threading.Lock()
_DL_STATE: dict = {"running": False, "pct": 0, "error": "", "cancel": False}


def download_state() -> dict:
    with _DL_LOCK:
        return {k: v for k, v in _DL_STATE.items() if k != "cancel"}


def download_cancel() -> None:
    with _DL_LOCK:
        _DL_STATE["cancel"] = True


def download_start() -> bool:
    """Startet das Laden im Hintergrund; False, wenn schon eines laeuft."""
    with _DL_LOCK:
        if _DL_STATE["running"]:
            return False
        _DL_STATE.update(running=True, pct=0, error="", cancel=False)
    threading.Thread(target=_download_worker, daemon=True).start()
    return True


def _download_worker() -> None:
    try:
        _download()
        err = ""
    except Cancelled:
        err = ""
    except Exception as exc:                          # noqa: BLE001
        err = str(exc) or exc.__class__.__name__
    with _DL_LOCK:
        _DL_STATE.update(running=False, error=err, cancel=False)
        if not err:
            _DL_STATE["pct"] = 0
    _reset_caches()


def _cancelled() -> bool:
    with _DL_LOCK:
        return bool(_DL_STATE["cancel"])


def _download() -> None:
    dest = _user_model_dir()
    dest.mkdir(parents=True, exist_ok=True)
    names = list(MODEL_SHA256)
    sizes = MODEL_SIZE
    total = sum(sizes.values()) or 1
    done_before = 0
    for n in names:
        target = dest / n
        if target.is_file() and _sha256(target) == MODEL_SHA256[n]:
            done_before += sizes[n]
            continue
        part = dest / (n + ".part")
        for attempt in range(6):
            have = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(f"{MODEL_BASE_URL}/{n}")
            if have:
                req.add_header("Range", f"bytes={have}-")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    if have and r.status != 206:      # Server ignoriert Range
                        have = 0
                    with open(part, "ab" if have else "wb") as f:
                        while True:
                            if _cancelled():
                                raise Cancelled()
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            have += len(chunk)
                            with _DL_LOCK:
                                _DL_STATE["pct"] = min(99, int(
                                    (done_before + have) * 100 / total))
                break
            except Cancelled:
                raise
            except Exception as exc:                  # noqa: BLE001
                if attempt == 5:
                    raise DownloadError(f"{n}: {exc}") from exc
        if _sha256(part) != MODEL_SHA256[n]:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{n}: Prüfsumme stimmt nicht")
        part.replace(target)
        done_before += sizes[n] or have


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def remove_model() -> None:
    """Loescht die geladene Modelldatei samt CoreML-Cache (nur im Nutzerordner)."""
    _reset_caches()
    d = _user_model_dir()
    for n in MODEL_SHA256:
        (d / n).unlink(missing_ok=True)
        (d / (n + ".part")).unlink(missing_ok=True)
    shutil.rmtree(d / "coreml-cache", ignore_errors=True)


@lru_cache(maxsize=1)
def _meta() -> dict:
    return json.loads((model_dir() / f"{MODEL_NAME}.json").read_text("utf-8"))


def _perf_cores() -> int:
    """Leistungskerne (Apple Silicon: hw.perflevel0); sonst Haelfte der Kerne.
    Effizienzkerne bremsen ONNX Runtime aus, weil sie auf den langsamsten
    Thread eines Operators warten."""
    try:
        out = subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                             capture_output=True, text=True, timeout=5)
        n = int(out.stdout.strip())
        if n > 0:
            return n
    except Exception:                                  # noqa: BLE001
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def _cpu_workers() -> int:
    """Anzahl paralleler CPU-Sitzungen, je 2 Threads. Gemessen (M1 Max, 8
    P-Kerne): 1 x 8 Threads 1,03 s/Abschnitt, 4 x 2 Threads 0,74 s -- mehrere
    kleine Sitzungen lasten die Kerne besser aus als eine grosse."""
    return max(1, min(4, _perf_cores() // 2))


# CoreML (GPU + Neural Engine) statt CPU. Gemessen (M1 Max): 0,27 s statt
# 0,74 s je Abschnitt, groesste relative Abweichung zur CPU 5e-4. Zwei Bedingungen:
#   - Format "MLProgram" (das alte "NeuralNetwork" ist langsamer als die CPU
#     und lieferte in einem Lauf NaN),
#   - feste Eingangsform (batch = 1), sonst scheitert CoreML schon beim Bauen.
# Das erste Uebersetzen dauert rund 60 s, aus dem Cache rund 11 s je Prozess.
# Zwei Sitzungen parallel bringen auf der GPU nichts (0,33 s) -- eine reicht,
# die zweite Arbeitsstelle in separate_array() ueberlappt nur STFT/iSTFT.
_COREML_OPTIONS = {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL",
                   "RequireStaticInputShapes": "1"}
_COREML_WORKERS = 2

_POOL_LOCK = threading.Lock()
_POOL: dict = {"queue": None, "backend": "", "cpu_forced": False}


def _coreml_cache_dir() -> Path:
    """Uebersetztes CoreML-Modell (~600 MB). Je onnxruntime-Version ein eigener
    Ordner -- ein Update der Laufzeit soll keinen fremden Stand weiterverwenden."""
    return _user_model_dir() / "coreml-cache" / str(getattr(_ort(), "__version__", "x"))


def _session_opts(threads: int = 0):
    opts = _ort().SessionOptions()
    opts.log_severity_level = 3                        # nur Fehler
    if threads:
        opts.intra_op_num_threads = threads
    return opts


def _coreml_session():
    """Eine CoreML-Sitzung oder None (nicht verfuegbar / Bauen gescheitert)."""
    ort = _ort()
    if "CoreMLExecutionProvider" not in ort.get_available_providers():
        return None
    cache = _coreml_cache_dir()
    try:
        cache.mkdir(parents=True, exist_ok=True)
        opts = _session_opts()
        opts.add_free_dimension_override_by_name("batch", 1)
        return ort.InferenceSession(
            str(model_dir() / f"{MODEL_NAME}.onnx"), opts,
            providers=[("CoreMLExecutionProvider",
                        dict(_COREML_OPTIONS, ModelCacheDirectory=str(cache))),
                       "CPUExecutionProvider"])
    except Exception:                                  # noqa: BLE001
        return None


def _sessions() -> "queue.Queue":
    """Pool der Sitzungen: eine CoreML-Sitzung, sonst _cpu_workers()
    CPU-Sitzungen. Jede Sitzung ist nur in einem Thread gleichzeitig in
    Benutzung (Entnahme aus der Queue). Gebaut wird unter _POOL_LOCK, damit
    parallele Abschnitte nicht jeder fuer sich ~1 Minute uebersetzen."""
    with _POOL_LOCK:
        if _POOL["queue"] is not None:
            return _POOL["queue"]
        pool: queue.Queue = queue.Queue()
        sess = None if _POOL["cpu_forced"] else _coreml_session()
        if sess is not None:
            pool.put(sess)
            backend = "coreml"
        else:
            for _ in range(_cpu_workers()):
                pool.put(_ort().InferenceSession(
                    str(model_dir() / f"{MODEL_NAME}.onnx"), _session_opts(2),
                    providers=["CPUExecutionProvider"]))
            backend = "cpu"
        _POOL.update(queue=pool, backend=backend)
        return pool


def _drop_sessions(cpu_forced: bool = False) -> None:
    with _POOL_LOCK:
        _POOL.update(queue=None, backend="", cpu_forced=cpu_forced)


def _fall_back_to_cpu() -> None:
    """CoreML hat ein unbrauchbares Ergebnis geliefert: fuer den Rest der
    Prozesslaufzeit auf CPU umstellen."""
    with _POOL_LOCK:
        if _POOL["cpu_forced"]:
            return
        _POOL.update(queue=None, backend="", cpu_forced=True)


def is_prepared() -> bool:
    """True, wenn die Sitzungen schon stehen (kein Warten auf CoreML)."""
    with _POOL_LOCK:
        return _POOL["queue"] is not None


def prepare() -> str:
    """Baut die Sitzungen (beim ersten Mal mit CoreML bis ~1 Minute) und
    liefert das Rechenwerk ("coreml" | "cpu")."""
    _sessions()
    with _POOL_LOCK:
        return _POOL["backend"]


def _workers() -> int:
    return _COREML_WORKERS if prepare() == "coreml" else _cpu_workers()


# --------------------------------------------------------------------------
# Dekodieren / Encodieren
# --------------------------------------------------------------------------

def _info(path: str) -> float:
    """Dauer in s per mutagen (ms, kein Unterprozess); 0 = unbekannt."""
    try:
        from mutagen import File
        return float(getattr(File(path).info, "length", 0) or 0)
    except Exception:                                  # noqa: BLE001
        return 0.0


def _decode(path: str, max_seconds: float) -> np.ndarray:
    """Stereo, 44,1 kHz, float32 -> (2, n). Mono-Quellen verdoppelt ffmpeg
    (-ac 2), Mehrkanal mischt es herunter."""
    sr = int(_meta()["samplerate"])
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0",
        "-t", f"{max_seconds + 1:.3f}",
        "-ac", "2", "-ar", str(sr), "-f", "f32le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=600)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    data = np.frombuffer(out.stdout, dtype="<f4")
    return data.reshape(-1, 2).T.astype(np.float32)


# Verzoegerung, die LAME ohne Xing/Info-Kopf vor das Audio setzt: Encoder
# (576) + Decoder (529) Samples, mit ffmpeg an einem Klick nachgemessen.
_LAME_DELAY = 1105


def _shift(audio: np.ndarray, n: int) -> np.ndarray:
    """n > 0: n Samples Stille voran, n < 0: die ersten -n Samples weg."""
    if n > 0:
        return np.concatenate([np.zeros((audio.shape[0], n), audio.dtype), audio], axis=1)
    return audio[:, -n:] if n < 0 else audio


def write_audio(audio: np.ndarray, dest: str, fmt: str, bitrate_kbps: int = 320,
                lead: int = 0) -> None:
    """(2, n) float32 -> Datei. fmt: 'aiff' (16 Bit PCM, big endian) oder 'mp3'.
    Geschrieben wird in eine .part-Datei und dann umbenannt, damit nie eine
    halbe Datei unter dem Zielnamen liegt.

    Ausgerichtet fuer Rekordbox, damit Beatgrid und Cues des Haupttracks 1:1
    passen (rekordbox.add_stems()): MP3 OHNE Xing/Info-Kopf -- den wertet
    Rekordbox anders aus als ffmpeg (26 oder 53 ms Versatz), ohne ihn spielen
    beide die Datei gleich ab -- und um `_LAME_DELAY` vorgezogen, damit der
    Stem ab Sample 0 so liegt wie das dekodierte Original. `lead` (Samples,
    rekordbox.playback_lead()) setzt zusaetzlich so viel Stille voran, wie
    Rekordbox das ORIGINAL spaeter abspielt als ffmpeg. Gapless-Player hoeren
    dadurch ein paar Millisekunden Stille mehr am Anfang."""
    sr = int(_meta()["samplerate"])
    if fmt == "aiff":
        codec = ["-c:a", "pcm_s16be", "-f", "aiff"]
        audio = _shift(audio, lead)
    elif fmt == "mp3":
        codec = ["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
                 "-write_xing", "0", "-f", "mp3"]
        audio = _shift(audio, lead - _LAME_DELAY)
    else:
        raise ValueError(f"unbekanntes Format: {fmt}")
    part = dest + ".part"
    raw = np.ascontiguousarray(np.clip(audio, -1.0, 1.0).T, dtype="<f4").tobytes()
    cmd = [media.ffmpeg_path(), "-v", "error", "-nostdin", "-y",
           "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "-",
           *codec, part]
    res = subprocess.run(cmd, input=raw, capture_output=True, timeout=600)
    if res.returncode != 0:
        Path(part).unlink(missing_ok=True)
        raise RuntimeError(res.stderr.decode("utf-8", "replace").strip()[:200])
    Path(part).replace(dest)


# --------------------------------------------------------------------------
# STFT / iSTFT -- Nachbau von torch.stft/istft (normalized=True, center=True,
# Hann-Fenster periodisch, Reflect-Padding) in numpy
# --------------------------------------------------------------------------

def _window(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)).astype(np.float32)


def _stft(x: np.ndarray, nfft: int, hop: int) -> np.ndarray:
    """(C, n) -> (C, nfft//2+1, Frames) complex64."""
    pad = nfft // 2
    xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    frames = 1 + (xp.shape[-1] - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(frames)[:, None]
    seg = xp[:, idx] * _window(nfft)                   # (C, Frames, nfft)
    spec = np.fft.rfft(seg, axis=-1) / math.sqrt(nfft)
    return spec.transpose(0, 2, 1).astype(np.complex64)


def _istft(z: np.ndarray, hop: int, length: int) -> np.ndarray:
    """(..., F, Frames) complex -> (..., length) float32."""
    nfft = (z.shape[-2] - 1) * 2
    lead = z.shape[:-2]
    z = z.reshape(-1, z.shape[-2], z.shape[-1])
    frames = z.shape[-1]
    win = _window(nfft)
    seg = np.fft.irfft(z.transpose(0, 2, 1), n=nfft, axis=-1) * math.sqrt(nfft)
    seg = seg * win                                    # (N, Frames, nfft)
    total = nfft + hop * (frames - 1)
    if nfft % hop == 0:
        # Ueberlappungs-Addition ohne Frame-Schleife: jeder Frame besteht aus
        # r = nfft/hop Bloecken, Block k von Frame i landet auf Block i+k.
        r = nfft // hop
        blocks = frames + r - 1
        out_b = np.zeros((z.shape[0], blocks, hop), np.float32)
        env_b = np.zeros((blocks, hop), np.float32)
        seg_b = seg.reshape(seg.shape[0], frames, r, hop)
        w2_b = (win * win).reshape(r, hop)
        for k in range(r):
            out_b[:, k:k + frames] += seg_b[:, :, k]
            env_b[k:k + frames] += w2_b[k]
        out = out_b.reshape(z.shape[0], total)
        env = env_b.reshape(total)
    else:
        out = np.zeros((z.shape[0], total), np.float32)
        env = np.zeros(total, np.float32)
        w2 = win * win
        for i in range(frames):
            s = i * hop
            out[:, s:s + nfft] += seg[:, i]
            env[s:s + nfft] += w2
    out = out / np.maximum(env, 1e-11)
    pad = nfft // 2
    out = out[:, pad:pad + length]
    return out.reshape(*lead, length)


def _spec(mix: np.ndarray, nfft: int, hop: int) -> np.ndarray:
    """htdemucs._spec: Reflect-Padding, STFT, oberstes Bin und Randframes weg."""
    length = mix.shape[-1]
    le = int(math.ceil(length / hop))
    pad = hop // 2 * _PAD_HOPS
    x = np.pad(mix, ((0, 0), (pad, pad + le * hop - length)), mode="reflect")
    z = _stft(x, nfft, hop)[:, :-1, :]
    return z[:, :, 2:2 + le]


def _ispec(z: np.ndarray, hop: int, length: int) -> np.ndarray:
    """htdemucs._ispec: (S, C, F, T) complex -> (S, C, length)."""
    z = np.pad(z, ((0, 0), (0, 0), (0, 1), (2, 2)))
    pad = hop // 2 * _PAD_HOPS
    le = hop * int(math.ceil(length / hop)) + 2 * pad
    x = _istft(z, hop, le)
    return x[..., pad:pad + length]


# --------------------------------------------------------------------------
# Abschnittsweise Trennung
# --------------------------------------------------------------------------

def _run_segment(chunk: np.ndarray) -> np.ndarray:
    """(2, segment_samples) -> (S, 2, segment_samples)."""
    meta = _meta()
    nfft, hop = int(meta["nfft"]), int(meta["hop"])
    n = chunk.shape[-1]
    z = _spec(chunk, nfft, hop)                        # (2, F, T) complex
    mag = np.stack([z.real, z.imag], axis=1)           # (2, 2, F, T)
    mag = mag.reshape(4, z.shape[1], z.shape[2]).astype(np.float32)
    feeds = {"mix": chunk[None].astype(np.float32), "mag": mag[None]}
    for _ in range(2):
        pool = _sessions()
        sess = pool.get()
        try:
            spec, wav = sess.run(["spec", "wav"], feeds)
        finally:
            pool.put(sess)
        # Sicherung gegen ein kaputtes GPU-Ergebnis: einmal auf CPU wiederholen.
        if np.isfinite(spec).all() and np.isfinite(wav).all():
            break
        if prepare() != "coreml":
            raise RuntimeError("Trennmodell lieferte ungültige Werte")
        _fall_back_to_cpu()
    spec, wav = spec[0], wav[0]                        # (S, 4, F, T), (S, 2, n)
    s, _, f, t = spec.shape
    spec = spec.reshape(s, 2, 2, f, t)
    zout = spec[:, :, 0] + 1j * spec[:, :, 1]          # (S, 2, F, T)
    return _ispec(zout, hop, n) + wav


def separate_array(mix: np.ndarray, progress=None, cancel=None,
                   overlap: float = _OVERLAP) -> np.ndarray:
    """(2, n) -> (S, 2, n): alle Quellen des Modells.

    Wie demucs.apply.apply_model (split=True): Abschnitte der Modelllaenge mit
    25 % Ueberlappung, dreieckig gewichtet, am Ende durch die Gewichtssumme
    geteilt. Vorab wird wie in der Demucs-CLI auf Mittelwert 0 / Streuung 1
    normiert und danach zurueckgerechnet.
    """
    seg = int(_meta()["segment_samples"])
    n_src = len(_meta()["sources"])
    length = mix.shape[-1]
    ref = mix.mean(0)
    mean, std = float(ref.mean()), float(ref.std())
    if std < 1e-8:                                     # Stille: nichts zu trennen
        return np.zeros((n_src, 2, length), np.float32)
    norm = ((mix - mean) / std).astype(np.float32)

    stride = int((1.0 - overlap) * seg)
    weight = np.concatenate([np.arange(1, seg // 2 + 1),
                             np.arange(seg - seg // 2, 0, -1)]).astype(np.float32)
    weight /= weight.max()
    out = np.zeros((n_src, 2, length), np.float32)
    wsum = np.zeros(length, np.float32)
    offsets = list(range(0, length, stride))

    def work(off: int):
        if cancel and cancel():
            raise Cancelled()
        chunk = norm[:, off:off + seg]
        valid = chunk.shape[-1]
        if valid < seg:
            chunk = np.pad(chunk, ((0, 0), (0, seg - valid)))
        return _run_segment(chunk)[:, :, :valid]

    # Abschnitte laufen parallel (je eigene Sitzung), aufsummiert wird der
    # Reihe nach im aufrufenden Thread -- Ergebnis identisch zur seriellen Fassung.
    n_work = _workers()
    with ThreadPoolExecutor(max_workers=n_work) as ex:
        futs = [ex.submit(work, off) for off in offsets]
        try:
            for k, (off, fut) in enumerate(zip(offsets, futs)):
                res = fut.result()
                valid = res.shape[-1]
                out[:, :, off:off + valid] += res * weight[:valid]
                wsum[off:off + valid] += weight[:valid]
                if progress:
                    progress((k + 1) / len(offsets))
        except BaseException:
            for f in futs:
                f.cancel()
            raise
    out /= np.maximum(wsum, 1e-8)
    return out * std + mean


def load(path: str, max_seconds: float = 900.0) -> np.ndarray:
    """Dekodiert die Quelle wie fuer separate() (2, n). Wirft TooLongError,
    RuntimeError."""
    dur = _info(path)
    if dur > max_seconds:
        raise TooLongError(f"{dur:.0f} s > {max_seconds:.0f} s")
    mix = _decode(path, max_seconds)
    if mix.shape[-1] / int(_meta()["samplerate"]) > max_seconds:
        raise TooLongError(f"> {max_seconds:.0f} s")
    if mix.shape[-1] == 0:
        raise RuntimeError("keine Audiodaten")
    return mix


def separate(path: str, kinds=KINDS, max_seconds: float = 900.0,
             progress=None, cancel=None, fast: bool = False,
             mix: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Trennt eine Datei. Liefert {'acapella': (2, n), 'instrumental': (2, n)}
    fuer die gewuenschten kinds. `mix`: schon per load() dekodiert. Wirft
    TooLongError, Cancelled, RuntimeError."""
    kinds = [k for k in kinds if k in KINDS]
    if not kinds:
        raise ValueError("kein Stem gewaehlt")
    if mix is None:
        mix = load(path, max_seconds)
    srcs = separate_array(mix, progress, cancel,
                          _OVERLAP_FAST if fast else _OVERLAP)
    names = list(_meta()["sources"])
    result: dict[str, np.ndarray] = {}
    if ACAPELLA in kinds:
        result[ACAPELLA] = srcs[names.index("vocals")]
    if INSTRUMENTAL in kinds:
        keep = [i for i, nm in enumerate(names) if nm != "vocals"]
        result[INSTRUMENTAL] = srcs[keep].sum(axis=0)
    return result
