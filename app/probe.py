"""
Container-Metadaten und Encoder-Fingerabdruecke.

Zwei Quellen:
  1. ffprobe  -> deklarierte Bitrate, Samplerate, Dauer, Kanaele, Tags
  2. Xing/LAME-Header -> Encoder-Version und der vom Encoder EINGETRAGENE
     Lowpass-Wert. Weicht dieser stark vom gemessenen Cutoff ab, ist der
     Track mit hoher Sicherheit aus einer schlechteren Quelle hochkodiert.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import media

# ── Xing/LAME ─────────────────────────────────────────────────────────────
# Aufbau des LAME-Tags ab dem Encoder-String:
#   9 Byte  Encoder-Kurzversion, z.B. "LAME3.100"
#   1 Byte  Tag-Revision (4 Bit) + VBR-Methode (4 Bit)
#   1 Byte  Lowpass in Hz/100   <- der fuer uns interessante Wert
_XING_MAGIC = (b"Xing", b"Info")

# codec_name (ffprobe) -> Familie. Bestimmt, ob classify.py den
# verlustbehafteten Modus (deklariert vs. gemessen) oder den
# verlustfreien Modus (nur Kante ja/nein) anwendet. Bewusst ueber den
# tatsaechlichen Codec und NICHT die Dateiendung entschieden: ALAC und AAC
# teilen sich den .m4a-Container.
_LOSSY_AAC_CODECS = {"aac"}
_LOSSLESS_CODECS = {"alac", "flac"}


def classify_codec_family(codec_name: str) -> str:
    name = (codec_name or "").lower()
    if name == "mp3":
        return "lossy_mp3"
    if name in _LOSSY_AAC_CODECS:
        return "lossy_aac"
    if name in _LOSSLESS_CODECS or name.startswith("pcm_"):
        return "lossless"
    return "lossy_other"


@dataclass
class ProbeResult:
    path: str
    ok: bool = False
    error: str = ""
    duration_s: float = 0.0
    declared_kbps: int = 0
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""
    codec_family: str = ""
    bitrate_mode: str = ""          # CBR | VBR | ABR | ""
    encoder: str = ""               # z.B. "LAME3.100" oder "Lavf60.16.100"
    lame_lowpass_hz: int = 0        # 0 = kein LAME-Tag vorhanden
    artist: str = ""
    title: str = ""
    album: str = ""
    tags: dict = field(default_factory=dict)


def _ffprobe(path: str) -> dict:
    cmd = [
        media.ffprobe_path(), "-v", "error", "-print_format", "json",
        "-show_entries",
        "format=duration,bit_rate,format_name:format_tags:"
        "stream=codec_name,codec_type,sample_rate,channels,bit_rate",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])
    return json.loads(out.stdout or b"{}")


def _id3_offset(fh) -> int:
    """Laenge eines fuehrenden ID3v2-Tags, damit die Suche im Audio startet."""
    fh.seek(0)
    head = fh.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return 0
    size = 0
    for b in head[6:10]:
        size = (size << 7) | (b & 0x7F)   # syncsafe integer
    return 10 + size


def read_lame_header(path: str) -> tuple[str, int]:
    """
    Liefert (encoder_string, lowpass_hz).
    lowpass_hz ist 0, wenn kein LAME-Tag gefunden wurde.
    """
    try:
        with open(path, "rb") as fh:
            start = _id3_offset(fh)
            fh.seek(start)
            buf = fh.read(8192)
            if not buf:
                return "", 0

            pos = -1
            for magic in _XING_MAGIC:
                found = buf.find(magic)
                if found != -1 and (pos == -1 or found < pos):
                    pos = found
            if pos == -1:
                return "", 0

            flags = int.from_bytes(buf[pos + 4:pos + 8], "big")
            off = pos + 8
            if flags & 0x1:
                off += 4          # Frames
            if flags & 0x2:
                off += 4          # Bytes
            if flags & 0x4:
                off += 100        # TOC
            if flags & 0x8:
                off += 4          # Quality

            if off + 11 > len(buf):
                return "", 0

            encoder = buf[off:off + 9].decode("latin-1", "replace").strip("\x00 ")
            if not encoder.startswith("LAME"):
                # Andere Encoder (Lavc/Lavf, Fraunhofer, ...) schreiben hier
                # keinen Lowpass-Wert — Encoder-String trotzdem zurueckgeben.
                return encoder, 0

            lowpass = buf[off + 10] * 100
            if not (1000 <= lowpass <= 24000):
                lowpass = 0
            return encoder, lowpass
    except OSError:
        return "", 0


def source_bit_depth(path: str) -> int:
    """
    Bittiefe der Quelle fuer eine verlustfreie AIFF-Zielkonvertierung, immer
    16 oder 24 (der Konverter kennt nur pcm_s16le/pcm_s24le). Zwei Quellen,
    in dieser Reihenfolge:
      1. bits_per_raw_sample -- ffprobe liefert das direkt fuer FLAC/ALAC und
         echtes PCM in WAV/AIFF, die vertrauenswuerdigste Angabe.
      2. Fallback: aus dem PCM-Codec-Namen abgeleitet (pcm_s16le -> 16,
         pcm_s24le -> 24, alles darueber ebenfalls 24, da AIFF hier keine
         hoehere Stufe kennt).
    Alles > 16 wird auf 24 abgebildet, alles <= 16 auf 16. Unbestimmbar
    (z.B. eine kaputte Datei) faellt konservativ auf 16 zurueck -- der
    Konverter erfindet keine Bittiefe, die sich nicht nachweisen laesst.
    """
    cmd = [
        media.ffprobe_path(), "-v", "error", "-print_format", "json",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,bits_per_raw_sample,bits_per_sample",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30)
        data = json.loads(out.stdout or b"{}")
        stream = (data.get("streams") or [{}])[0]
    except Exception:                                  # noqa: BLE001
        return 16

    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            bits = int(stream.get(key) or 0)
        except (TypeError, ValueError):
            bits = 0
        if bits > 0:
            return 24 if bits > 16 else 16

    m = re.search(r"pcm_[sf](\d+)", str(stream.get("codec_name") or ""))
    if m:
        return 24 if int(m.group(1)) > 16 else 16
    return 16


def _payload_bitrate(path: str, duration_s: float) -> int:
    """
    Bitrate aus der reinen Audionutzlast, also ohne fuehrendes ID3v2-Tag.

    Nur Rueckfalloption fuer den Fall, dass ffprobe zum Stream selbst keine
    Bitrate liefert. Der Wert des Containers taugt dafuer nicht: er rechnet
    das Tag mit.
    """
    if duration_s <= 0:
        return 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            start = _id3_offset(fh)
    except OSError:
        return 0
    payload = max(0, size - start)
    return int(payload * 8 / duration_s) if payload else 0


def _mutagen_info(path: str, res: ProbeResult) -> None:
    """Bitraten-Modus und Encoder-Settings — rein optional.

    Nur fuer MP3 sinnvoll: 'CBR/VBR/ABR' und ein Encoder-String sind
    LAME-Konzepte ohne Entsprechung in AAC/FLAC/WAV/AIFF. Deren Encoder-Tag
    (falls vorhanden) liefert bereits ffprobe ueber die Container-Tags
    ("encoder"/"tsse", siehe probe()).
    """
    if res.codec_family != "lossy_mp3":
        return
    try:
        from mutagen.mp3 import MP3, BitrateMode
    except ImportError:
        return
    try:
        mp3 = MP3(path)
    except Exception:
        return
    info = getattr(mp3, "info", None)
    if info is None:
        return
    mode = getattr(info, "bitrate_mode", None)
    if mode is not None and mode != BitrateMode.UNKNOWN:
        res.bitrate_mode = str(mode).split(".")[-1]
    if not res.encoder:
        res.encoder = (getattr(info, "encoder_info", "") or "").strip()


def probe(path: str) -> ProbeResult:
    res = ProbeResult(path=str(path))
    try:
        data = _ffprobe(path)
    except Exception as exc:                       # noqa: BLE001
        res.error = f"ffprobe: {exc}"
        return res

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or [{}]
    # Ein eingebettetes Cover ist fuer ffprobe ein eigener Stream (mjpeg/png).
    # Ausdruecklich den Audiostream nehmen, sonst landen dessen Kennwerte im
    # Ergebnis.
    audio = (next((s for s in streams if s.get("codec_type") == "audio"), None)
             or next((s for s in streams if s.get("codec_name")), streams[0]))

    try:
        res.duration_s = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        res.duration_s = 0.0

    # Die deklarierte Bitrate MUSS aus dem Audiostream kommen. Der Wert des
    # Containers ist Dateigroesse durch Laufzeit und rechnet damit das
    # ID3-Tag mit: ein 128er-Track mit 500 kB Coverbild erscheint darueber als
    # 264 kbps — und wird prompt als Fake gemeldet, obwohl nur das Bild gross
    # ist. Erst wenn der Stream nichts hergibt, wird selbst gerechnet, dann
    # aber ohne Tag.
    bit_rate = audio.get("bit_rate") or 0
    if not bit_rate:
        bit_rate = _payload_bitrate(path, res.duration_s) or fmt.get("bit_rate") or 0
    try:
        res.declared_kbps = int(round(int(bit_rate) / 1000.0))
    except (TypeError, ValueError):
        res.declared_kbps = 0

    res.codec = audio.get("codec_name", "") or ""
    res.codec_family = classify_codec_family(res.codec)
    try:
        res.sample_rate = int(audio.get("sample_rate") or 0)
    except (TypeError, ValueError):
        res.sample_rate = 0
    try:
        res.channels = int(audio.get("channels") or 0)
    except (TypeError, ValueError):
        res.channels = 0

    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    res.tags = tags
    res.artist = tags.get("artist", "") or tags.get("album_artist", "")
    res.title = tags.get("title", "")
    res.album = tags.get("album", "")

    if res.codec_family == "lossy_mp3":
        res.encoder, res.lame_lowpass_hz = read_lame_header(path)
    if not res.encoder:
        res.encoder = (tags.get("encoder") or tags.get("tsse") or "").strip()
    _mutagen_info(path, res)

    res.ok = res.duration_s > 0 and res.sample_rate > 0
    if not res.ok and not res.error:
        res.error = "Keine verwertbaren Stream-Daten"
    return res
