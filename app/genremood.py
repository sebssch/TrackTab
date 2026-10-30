"""
Genre-Vorschlaege, Stimmung und Energie ueber die Essentia-Modelle der MTG
(Universitat Pompeu Fabra), ausgefuehrt mit onnxruntime -- lokal, ohne Netz.

Modelle (vendor/models/essentia/, geladen von fetch_models.sh, Lizenz
CC BY-NC-SA 4.0):
  - discogs-effnet-bsdynamic-1.onnx: Mel-Patches -> 400 Discogs-Stile
    ("Electronic---Electro House", Sigmoid je Stil) UND ein 1280er-Embedding
  - mood_{aggressive,happy,party,relaxed,sad}, danceability: kleine Koepfe auf
    dem Embedding, je Softmax ueber zwei Klassen (Reihenfolge laut JSON)

Ablauf: ffmpeg dekodiert nach Mono 16 kHz (gleiche pan-Gewichtung wie
spectral.py), _melspec() bildet die Eingabe nach, die Essentia beim Training
benutzt hat (TensorflowInputMusiCNN), _patches() schneidet sie in Stuecke zu
128 Frames, Vorhersagen werden ueber alle Stuecke gemittelt.

Fehlt onnxruntime (PyPI-Wheels nur arm64, macOS 14+) oder fehlen die Modelle,
meldet available() False und die Oberflaeche blendet die Funktion aus.

Trennung wie in bpmkey.py: analyse() misst (teuer, keine Seiteneffekte),
plan_fields() entscheidet rein aus Einstellungen + vorhandenen Tags, was in
die Datei geht (billig, gut pruefbar).
"""
from __future__ import annotations

import json
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import media
from . import spectral
from . import tags

MODEL_NAME = "discogs-effnet-bsdynamic-1"
# Stand der gespeicherten Analyse (Spalte genremood.model). Kommt ein Modell
# dazu, aendert sich dieser Wert -- der Bibliotheks-Scan misst dann alle
# Zeilen mit aelterem Stand neu (db.paths_needing_genremood()).
ANALYSIS_VERSION = MODEL_NAME + "+moodtheme-1"

_SAMPLE_RATE = 16000
_FRAME = 512
_HOP = 256
_BANDS = 96
_PATCH = 128
_PATCH_HOP = 62
# Mehr Stuecke je Lauf bringen kaum Tempo, kosten aber Speicher
# (64 x 128 x 96 Float32 = 3 MB Eingabe, Zwischenwerte ein Vielfaches).
_BATCH = 64

# Interner Name -> (Dateiname des Kopfes, Klassenname fuer "trifft zu").
_HEADS = {
    "aggressive": ("mood_aggressive-discogs-effnet-1", "aggressive"),
    "happy": ("mood_happy-discogs-effnet-1", "happy"),
    "party": ("mood_party-discogs-effnet-1", "party"),
    "relaxed": ("mood_relaxed-discogs-effnet-1", "relaxed"),
    "sad": ("mood_sad-discogs-effnet-1", "sad"),
    "danceability": ("danceability-discogs-effnet-1", "danceable"),
}
MOODS = ("aggressive", "happy", "party", "relaxed", "sad")

# DJ-Stimmungen aus dem MTG-Jamendo-Kopf (56 Stimmungen/Themen, Sigmoid je
# Klasse). Bewusst nur eine Auswahl: Film-/Werbe-Themen (corporate, trailer,
# documentary ...) sagen beim Auflegen nichts, happy/sad/party/relaxing decken
# die genaueren Ja/Nein-Koepfe oben schon ab.
_THEME_HEAD = "mtg_jamendo_moodtheme-discogs-effnet-1"
THEMES = ("energetic", "groovy", "dark", "deep", "uplifting", "melancholic",
          "upbeat", "epic", "melodic", "summer", "romantic")
# Der Kopf liefert kleine Absolutwerte (an 14 Testtracks: Median 0,01, Maximum
# 0,26) -- eine feste 0,5-Grenze wie bei MOODS griffe nie. Gezeigt werden
# deshalb je Track die THEME_TOP staerksten Themen, sofern sie THEME_FLOOR
# erreichen.
THEME_TOP = 2
THEME_FLOOR = 0.05

# Ab dieser Wahrscheinlichkeit gilt eine Stimmung als zutreffend (Anzeige als
# Badge, Mood-Tag in der Datei). Die Koepfe sind binaer trainiert, 0,5 ist
# ihre natuerliche Grenze.
MOOD_THRESHOLD = 0.5

# So viele Stile landen in der DB (die Anzeige nimmt davon hoechstens 5
# oberhalb von genremood_min_percent).
_KEEP_GENRES = 10

# Energie 1..10 = 1 + 9 * gewichtete Summe. Lautheit traegt am meisten (ein
# leiser Ambient-Track soll nie "10" werden), die Koepfe unterscheiden
# zwischen gleich lauten Tracks. Der RMS-Bereich -30..-8 dBFS deckt von
# ruhigem Material bis zu laut gemasterter Clubmusik alles ab.
_ENERGY_WEIGHTS = {"loudness": 0.4, "aggressive": 0.25, "party": 0.2,
                   "danceability": 0.15}
_RMS_DB_LOW, _RMS_DB_HIGH = -30.0, -8.0

# Die Laufzeit gibt keine Zusage fuer parallele Aufrufe derselben Session aus
# mehreren Server-Threads. Dekodieren laeuft parallel, nur das Rechnen nicht.
_INFER_LOCK = threading.Lock()


class TooLongError(RuntimeError):
    """Datei ist laenger als bpmkey_max_minutes (gemeinsames Limit beider
    Analysen) -- bewusst nicht gemessen
    (DJ-Mixe: ein Genre fuer den ganzen Mix sagt nichts)."""


def model_dir() -> Path:
    return cfgmod.vendor_dir() / "models" / "essentia"


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
    names = [MODEL_NAME, _THEME_HEAD] + [f for f, _ in _HEADS.values()]
    return [model_dir() / f"{n}{ext}" for n in names for ext in (".onnx", ".json")]


def available() -> bool:
    return _ort() is not None and all(p.is_file() for p in _model_files())


@lru_cache(maxsize=1)
def _labels() -> tuple[str, ...]:
    meta = json.loads((model_dir() / f"{MODEL_NAME}.json").read_text("utf-8"))
    return tuple(meta["classes"])


@lru_cache(maxsize=None)
def _session(name: str):
    ort = _ort()
    opts = ort.SessionOptions()
    opts.log_severity_level = 3                        # nur Fehler
    return ort.InferenceSession(str(model_dir() / f"{name}.onnx"), opts,
                                providers=["CPUExecutionProvider"])


@lru_cache(maxsize=1)
def _theme_indices() -> tuple[int, ...]:
    meta = json.loads((model_dir() / f"{_THEME_HEAD}.json").read_text("utf-8"))
    classes = list(meta["classes"])
    return tuple(classes.index(t) for t in THEMES)


def theme_hits(moods: dict) -> list[str]:
    """Die zutreffenden DJ-Stimmungen eines Ergebnisses, staerkste zuerst."""
    ranked = sorted(((moods.get(t, 0.0), t) for t in THEMES), reverse=True)
    return [t for p, t in ranked[:THEME_TOP] if p >= THEME_FLOOR]


@lru_cache(maxsize=None)
def _positive_index(name: str, positive: str) -> int:
    """Spalte der Softmax-Ausgabe, die "trifft zu" bedeutet -- die Reihenfolge
    ist je Kopf verschieden (party: ["non_party", "party"], happy: ["happy",
    "non_happy"]), deshalb aus dem JSON statt fest verdrahtet."""
    meta = json.loads((model_dir() / f"{name}.json").read_text("utf-8"))
    return list(meta["classes"]).index(positive)


# ── Mel-Frontend ─────────────────────────────────────────────────────────────
# Nachbau von Essentias TensorflowInputMusiCNN mit den Voreinstellungen der
# beteiligten Algorithmen (aus dem Essentia-Quelltext abgelesen):
#   FrameCutter  512/256, startFromZero=false -> erster Frame beginnt bei -256,
#                Raender mit Nullen aufgefuellt, letzter Frame, sobald seine
#                Mitte das Signalende erreicht
#   Windowing    Hann, symmetrisch (Nenner N-1), nicht normiert
#   Spectrum     Betrag der FFT (513 Bins)
#   MelBands     96 Baender 0..8000 Hz, Slaney-Mel-Abstaende, Dreiecke linear
#                in Hz, "unit_tri" (Flaeche 1 ueber die theoretische
#                Dreiecksbreite), type="power" -> Betrag QUADRIERT
#   danach       log10(1 + 10000 * x)


def _hz_to_mel_slaney(f):
    f = np.asarray(f, dtype=np.float64)
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    return np.where(f < min_log_hz, f / f_sp,
                    min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep)


def _mel_to_hz_slaney(m):
    m = np.asarray(m, dtype=np.float64)
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    return np.where(m < min_log_mel, m * f_sp,
                    min_log_hz * np.exp(logstep * (m - min_log_mel)))


@lru_cache(maxsize=1)
def _filterbank() -> np.ndarray:
    """(513, 96)-Matrix: Leistungsspektrum @ Matrix = Mel-Baender."""
    n_bins = _FRAME // 2 + 1
    mels = np.linspace(_hz_to_mel_slaney(0.0), _hz_to_mel_slaney(_SAMPLE_RATE / 2),
                       _BANDS + 2)
    # Essentia addiert das Inkrement schrittweise auf; linspace weicht davon
    # nur in der letzten Stelle ab.
    edges = _mel_to_hz_slaney(mels)
    scale = (_SAMPLE_RATE / 2.0) / (n_bins - 1)
    fb = np.zeros((n_bins, _BANDS), dtype=np.float64)
    for i in range(_BANDS):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        step1, step2 = mid - lo, hi - mid
        j0 = int(np.ceil(lo / scale))
        j1 = int(np.floor(hi / scale))
        for j in range(j0, j1 + 1):
            f = j * scale
            fb[j, i] = (f - lo) / step1 if f < mid else (hi - f) / step2
        fb[:, i] /= (step1 + step2) / 2.0
    return fb.astype(np.float32)


def _melspec(samples: np.ndarray) -> np.ndarray:
    """Mono-Signal (16 kHz) -> (Frames, 96) log-Mel wie Essentia."""
    n = samples.size
    half = (_FRAME + 1) // 2
    n_frames = int(np.ceil(n / _HOP)) + 1 if n else 0
    padded = np.zeros(n_frames * _HOP + _FRAME, dtype=np.float32)
    padded[half:half + n] = samples
    idx = np.arange(_FRAME)[None, :] + _HOP * np.arange(n_frames)[:, None]
    window = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(_FRAME) / (_FRAME - 1)))
    frames = padded[idx] * window.astype(np.float32)
    power = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32) ** 2
    return np.log10(1.0 + 10000.0 * (power @ _filterbank()))


def _patches(mel: np.ndarray) -> np.ndarray:
    """(Frames, 96) -> (Stuecke, 128, 96); unvollstaendiger Rest entfaellt
    (lastPatchMode="discard" wie in Essentia)."""
    if mel.shape[0] < _PATCH:
        return np.zeros((0, _PATCH, _BANDS), dtype=np.float32)
    starts = range(0, mel.shape[0] - _PATCH + 1, _PATCH_HOP)
    return np.stack([mel[s:s + _PATCH] for s in starts]).astype(np.float32)


# ── Analyse ──────────────────────────────────────────────────────────────────

def _info(path: str) -> tuple[int, float]:
    """(Kanalzahl, Dauer in s) per mutagen; 0 = unbekannt."""
    try:
        from mutagen import File
        info = File(path).info
        return (int(getattr(info, "channels", 0) or 0),
                float(getattr(info, "length", 0) or 0))
    except Exception:                                  # noqa: BLE001
        return 0, 0.0


def _decode(path: str, channels: int, max_seconds: float) -> np.ndarray:
    downmix = spectral._downmix_filter(channels) if channels > 0 else ["-ac", "1"]
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0",
        "-t", f"{max_seconds + 1:.3f}",
        *downmix, "-ar", str(_SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    return np.frombuffer(out.stdout, dtype="<f4")


def _io_names(session, width: int) -> tuple[str, str]:
    """(Eingangsname, Name des Ausgangs mit letzter Dimension 'width') --
    die Knotennamen der ONNX-Fassungen sind nicht dokumentiert."""
    inp = session.get_inputs()[0].name
    for out in session.get_outputs():
        if out.shape and out.shape[-1] == width:
            return inp, out.name
    raise RuntimeError(f"Modell ohne Ausgang der Breite {width}")


def _infer(patches: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Stuecke -> (Stil-Aktivierungen (n, 400), Embeddings (n, 1280))."""
    sess = _session(MODEL_NAME)
    inp, out_genre = _io_names(sess, len(_labels()))
    _, out_emb = _io_names(sess, 1280)
    genres, embs = [], []
    for s in range(0, len(patches), _BATCH):
        g, e = sess.run([out_genre, out_emb], {inp: patches[s:s + _BATCH]})
        genres.append(g)
        embs.append(e)
    return np.concatenate(genres), np.concatenate(embs)


def _energy(samples: np.ndarray, moods: dict) -> float:
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    db = 20.0 * np.log10(max(rms, 1e-6))
    loud = min(1.0, max(0.0, (db - _RMS_DB_LOW) / (_RMS_DB_HIGH - _RMS_DB_LOW)))
    parts = {"loudness": loud, **moods}
    score = sum(w * float(parts.get(k, 0.0)) for k, w in _ENERGY_WEIGHTS.items())
    return round(1.0 + 9.0 * min(1.0, max(0.0, score)), 1)


def analyse(path: str, max_seconds: float = 900.0) -> dict:
    """Rueckgabe: {"genres": [[Stil, p], ...] absteigend (hoechstens
    _KEEP_GENRES), "moods": {aggressive, happy, party, relaxed, sad,
    danceability, <THEMES>: 0..1}, "energy": 1..10, "model": ANALYSIS_VERSION}.

    TooLongError ueber max_seconds (ohne ffmpeg-Lauf, wenn mutagen die Dauer
    kennt), sonst RuntimeError, wenn Laufzeit/Modelle fehlen oder die Datei
    sich nicht dekodieren laesst."""
    if not available():
        raise RuntimeError("onnxruntime oder die Modelle fehlen")
    channels, length = _info(path)
    if length > max_seconds:
        raise TooLongError(f"länger als {max_seconds / 60:g} Minuten")
    samples = _decode(path, channels, max_seconds)
    if samples.size > max_seconds * _SAMPLE_RATE:
        raise TooLongError(f"länger als {max_seconds / 60:g} Minuten")
    patches = _patches(_melspec(samples))
    if len(patches) == 0:
        raise RuntimeError("Zu kurz für eine Genre-/Stimmungs-Analyse")

    with _INFER_LOCK:
        activations, embeddings = _infer(patches)
        moods = {}
        for key, (name, positive) in _HEADS.items():
            sess = _session(name)
            inp, out = _io_names(sess, 2)
            probs = sess.run([out], {inp: embeddings.astype(np.float32)})[0]
            moods[key] = round(float(np.mean(probs[:, _positive_index(name, positive)])), 3)
        sess = _session(_THEME_HEAD)
        inp, out = _io_names(sess, 56)
        themes = sess.run([out], {inp: embeddings.astype(np.float32)})[0].mean(axis=0)
        for name, idx in zip(THEMES, _theme_indices()):
            moods[name] = round(float(themes[idx]), 3)

    mean = activations.mean(axis=0)
    order = np.argsort(mean)[::-1][:_KEEP_GENRES]
    labels = _labels()
    genres = [[labels[i], round(float(mean[i]), 4)] for i in order]
    return {"genres": genres, "moods": moods,
            "energy": _energy(samples, moods), "model": ANALYSIS_VERSION}


# ── Was in die Datei geht ────────────────────────────────────────────────────

def style_names() -> list[str]:
    """Alle Stilnamen des Modells ohne Oberkategorie, ohne Doppelte,
    alphabetisch -- fuer die Genre-Auswahl im Tags-Dialog. Braucht nur die
    JSON-Datei, nicht onnxruntime; ohne Modelle eine leere Liste."""
    try:
        labels = _labels()
    except (OSError, ValueError, KeyError):
        return []
    return sorted({style_name(lb) for lb in labels}, key=str.casefold)


def style_name(label: str) -> str:
    """"Electronic---Electro House" -> "Electro House" (nur der Stil)."""
    return label.split("---")[-1].strip()


def mood_names(moods: dict) -> list[str]:
    """Zutreffende Stimmungen als Namen: erst die Ja/Nein-Stimmungen,
    staerkste zuerst, dann die DJ-Stimmungen (["Party", "Happy", "Energetic",
    "Melodic"]) -- dieselbe Regel wie moodNames() in app.js. Englisch wie die
    Klassennamen der Modelle -- ein Datei-Tag (oder Rekordbox-My-Tag) ist
    keine Oberflaechen-Beschriftung und soll in jeder Software gleich aussehen."""
    hits = sorted(((p, k) for k, p in moods.items()
                   if k in MOODS and p >= MOOD_THRESHOLD), reverse=True)
    return [k.capitalize() for k in [k for _, k in hits] + theme_hits(moods)]


def mood_text(moods: dict) -> str:
    """Zutreffende Stimmungen als Tag-Text ("Party, Happy, Energetic")."""
    return ", ".join(mood_names(moods))


def restrict(result: dict, cfg: dict) -> dict:
    """Beschraenkt ein Messergebnis auf die eingeschalteten Teile
    (measure_genre / measure_mood). Gemessen wird immer in einem Modelllauf;
    hier wird nur entschieden, was uebernommen wird."""
    out = dict(result)
    if not cfg.get("measure_genre", True):
        out["genres"] = []
    if not cfg.get("measure_mood", True):
        out["moods"] = {}
        out["energy"] = 0
    return out


def plan_fields(cfg: dict, result: dict, existing: dict, existing_mood: str) -> dict:
    """Entscheidet, was in die Datei geschrieben wird -- im Format von
    tags.write_tags() (nur geaenderte Felder, leer = nichts zu tun).

    `existing` ist tags.read_extra(path), `existing_mood` tags.read_mood(path).
    Die Energie landet im gemeinsamen Kommentar-Eintrag der Analysen
    (tags.plan_comment(), Einstellung bpmkey_comment_content) -- Tonart und
    BPM dafuer aus der Datei, damit der Eintrag gleich aussieht wie nach der
    BPM-/Tonart-Analyse.
    """
    fields: dict = {}
    camelot = str(existing.get("key") or "")
    key_txt = tags.key_file_value(camelot, cfg) if camelot in tags.KEY_TABLE else ""
    bpm = int(round(float(existing.get("bpm") or 0)))
    comment = tags.plan_comment(cfg, str(existing.get("comment") or ""), key_txt,
                                bpm, float(result.get("energy") or 0), {"energy"})
    if comment is not None:
        fields["comment"] = comment
    if cfg.get("genremood_write_mood", "no") == "tag":
        text = mood_text(result.get("moods") or {})
        if text and text != (existing_mood or "").strip():
            fields["mood"] = text
    return fields
