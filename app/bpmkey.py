"""
BPM- und Tonart-Analyse ueber libsonare (optionale Abhaengigkeit).

Ablauf: ffmpeg dekodiert die Datei nach Mono-Float32 mit 22.050 Hz (dieselbe
explizite pan-Gewichtung wie spectral.py), libsonare misst Tempo und Tonart.
Die Tonart kommt intern immer als Camelot ("8A") heraus; in welcher Schreibweise
sie in die Datei geht, entscheidet allein cfg["key_notation"] (tags.key_file_value()).

Fehlt libsonare (z. B. Intel-Mac: das PyPI-Wheel gibt es nur fuer arm64) oder
laesst sich die Bibliothek nicht laden, meldet available() False und die
Oberflaeche blendet die Funktion aus.

Trennung bewusst: analyse() misst (teuer, keine Seiteneffekte), plan_fields()
entscheidet rein aus Einstellungen + vorhandenen Tags, was geschrieben wird
(billig, ohne Datei- oder Netzzugriff -- gut pruefbar).
"""
from __future__ import annotations

import subprocess
import threading
from functools import lru_cache

import numpy as np

from . import media
from . import spectral
from . import tags

_SAMPLE_RATE = 22050

_MODE_MINOR = 1

# Ob die native Bibliothek parallele Aufrufe aus mehreren Threads vertraegt, ist
# nicht zugesichert -- der Server bedient Anfragen in eigenen Threads. Das
# Messen selbst ist ohnehin CPU-gebunden; nur das Dekodieren laeuft parallel.
_MEASURE_LOCK = threading.Lock()


class TooLongError(RuntimeError):
    """Datei ist laenger als das eingestellte Limit (bpmkey_max_minutes) --
    wird bewusst nicht gemessen (DJ-Mixe: ein Wert fuer den ganzen Mix sagt
    nichts, und die Analyse kostet Zeit und Speicher)."""


@lru_cache(maxsize=1)
def _lib():
    """libsonare-Modul oder None. Import lazy und gecacht -- der Import laedt die
    native Bibliothek, das soll weder den Start noch Worker-Prozesse bremsen."""
    try:
        import libsonare
        return libsonare
    except Exception:                                  # noqa: BLE001
        return None


def available() -> bool:
    return _lib() is not None


def _info(path: str) -> tuple[int, float]:
    """(Kanalzahl, Dauer in s) per mutagen (ms, kein Unterprozess); 0 = unbekannt."""
    try:
        from mutagen import File
        info = File(path).info
        return (int(getattr(info, "channels", 0) or 0),
                float(getattr(info, "length", 0) or 0))
    except Exception:                                  # noqa: BLE001
        return 0, 0.0


def _decode(path: str, channels: int, max_seconds: float) -> np.ndarray:
    """Dekodiert hoechstens max_seconds + 1 s -- die eine Sekunde Zugabe dient
    nur dazu, eine zu lange Datei an den Samples zu erkennen (siehe analyse())."""
    # Unbekannte Kanalzahl: ffmpegs eigenen Downmix nehmen (siehe
    # spectral._downmix_filter zu seinen Macken bei PCM-Containern).
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


def analyse(path: str, max_seconds: float = 600.0) -> dict:
    """Misst Tempo und Tonart. Rueckgabe: {"bpm": int (0 = unbekannt),
    "camelot": str ("" = keine Dur/Moll-Tonart erkannt), "confidence": float}.

    Dateien ueber max_seconds werden nicht gemessen: TooLongError, ohne
    ffmpeg-Lauf, wenn mutagen die Dauer kennt; sonst erkennt der Dekodierlauf
    sie an mehr Samples als erlaubt. Sonst RuntimeError, wenn libsonare fehlt
    oder die Datei sich nicht dekodieren laesst."""
    lib = _lib()
    if lib is None:
        raise RuntimeError("libsonare ist nicht installiert")
    channels, length = _info(path)
    if length > max_seconds:
        raise TooLongError(f"länger als {max_seconds / 60:g} Minuten")
    samples = _decode(path, channels, max_seconds)
    if samples.size > max_seconds * _SAMPLE_RATE:
        raise TooLongError(f"länger als {max_seconds / 60:g} Minuten")
    if samples.size < _SAMPLE_RATE * 5:
        raise RuntimeError("Zu kurz für eine BPM-/Tonart-Analyse")
    with _MEASURE_LOCK:
        audio = lib.Audio.from_buffer(samples, _SAMPLE_RATE)
        bpm = float(audio.detect_bpm() or 0.0)
        key = audio.detect_key()
    mode = int(key.mode)
    camelot = ""
    if mode in (0, _MODE_MINOR):                       # Dur / Moll
        camelot = tags._pc_to_camelot(int(key.root), mode == _MODE_MINOR)
    return {"bpm": int(round(bpm)) if bpm > 0 else 0,
            "camelot": camelot,
            "confidence": float(key.confidence)}


def _mode(value) -> str:
    """Auswahlfeld "In ... schreiben": "yes" | "if_empty" | "no". Nimmt auch
    einen echten Bool; Unbekanntes zaehlt als "no" (nie ungefragt schreiben)."""
    if value is True:
        return "yes"
    text = str(value).strip().lower()
    return text if text in ("yes", "if_empty") else "no"


def plan_fields(cfg: dict, result: dict, existing: dict) -> dict:
    """Entscheidet, was in die Datei geschrieben wird -- im Format von
    tags.write_tags() (nur geaenderte Felder, leer = nichts zu tun).

    `existing` ist tags.read_extra(path). Die Tonart steht in Feld UND Kommentar
    in derselben Schreibweise (cfg["key_notation"]). Junk-Kommentare (technische
    Hex-Felder) zaehlen als leer -- write_tags() verwirft sie ohnehin.
    """
    fields: dict = {}
    camelot = result.get("camelot") or ""
    bpm = int(result.get("bpm") or 0)
    key_txt = tags.key_file_value(camelot, cfg) if camelot else ""

    mode = _mode(cfg.get("bpmkey_write_key", "yes"))
    if mode != "no" and key_txt:
        if not (mode == "if_empty" and existing.get("key")) \
                and str(existing.get("key_raw") or "") != key_txt:
            fields["key"] = key_txt

    mode = _mode(cfg.get("bpmkey_write_bpm", "yes"))
    if mode != "no" and bpm > 0:
        old = int(round(float(existing.get("bpm") or 0)))
        if not (mode == "if_empty" and old > 0) and old != bpm:
            fields["bpm"] = float(bpm)

    content = cfg.get("bpmkey_comment_content", "none")
    if content in ("key", "key_bpm"):
        parts = [key_txt] if key_txt else []
        if content == "key_bpm" and bpm > 0:
            parts.append(str(bpm))
        text = " - ".join(parts)
        if text:
            current = str(existing.get("comment") or "").strip()
            if tags.is_junk_comment(current):
                current = ""
            cmode = cfg.get("bpmkey_comment_existing", "overwrite")
            if not (cmode == "if_empty" and current):
                if cmode == "prepend" and current:
                    # Schon vorangestellt -> nichts doppelt schreiben.
                    new = current if current.startswith(text) else f"{text} - {current}"
                else:
                    new = text
                if new != current:
                    fields["comment"] = new
    return fields
