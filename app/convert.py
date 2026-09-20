"""
Format wechseln: MP3 320 kbit/s CBR (maximale LAME-Qualitaetsstufe) oder
AIFF (verlustfrei, Bittiefe der Quelle erhalten).

Wie rewrite.py zuerst komplett bauen und pruefen, dann erst das Original
anfassen -- hier zusaetzlich: die neue Datei traegt eine andere Endung,
landet also unter einem eigenen, kollisionsfreien Pfad statt am Platz des
Originals (kein os.replace an gleicher Stelle wie bei rewrite.py).

Tags/Cover werden -- anders als bei rewrite.py -- nicht als Ganzes vom
Original uebernommen (das setzt denselben Containertyp voraus), sondern ueber
tags.py's formatunabhaengige Feld-API neu geschrieben, mit optionalem
DB-Fallback je Feld. Einzige Ausnahme: WAV->AIFF, wo beide Seiten ID3-
Container sind -- dort wird zusaetzlich das komplette Tag-Objekt der Quelle
uebernommen, damit unbekannte Frames (GEOB/PRIV, Serato-/Rekordbox-
Cuepunkte) erhalten bleiben.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from . import media
from . import probe as probe_mod
from . import tags as tags_mod

_ENCODE_TIMEOUT_S = 600

TARGET_MP3_320 = "mp3_320"
TARGET_AIFF = "aiff"
TARGETS = (TARGET_MP3_320, TARGET_AIFF)


class ConvertError(RuntimeError):
    """Konvertierung fehlgeschlagen -- das Original ist in jedem Fall unangetastet."""


class ConvertSkip(RuntimeError):
    """Keine Fehler, sondern eine bewusste Ablehnung. reason ist 'upscale'
    (Ziel waere eine Qualitaetsverbesserung) oder 'already_target' (Datei
    liegt schon im Zielformat, ein Neukodieren brächte nichts)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def decide(codec_family: str, declared_kbps: int, suffix: str, target: str) -> tuple[str, str]:
    """Reine Entscheidungsfunktion ohne I/O -- Client (app.js) und Server
    nutzen dieselbe Tabelle:

    Ziel AIFF: nur erlaubt, wenn die Quelle schon verlustfrei ist (sonst
    waere AIFF eine Qualitaetsverbesserung, die es real gar nicht gibt --
    "eine MP3 darf nicht zu AIFF konvertiert werden"). Liegt sie bereits als
    AIFF/AIF vor, gibt es nichts zu tun.

    Ziel MP3 320: eine verlustfreie Quelle darf immer (echte Abwaertskodierung).
    Eine bereits verlustbehaftete Quelle nur, wenn ihre deklarierte Bitrate
    schon mindestens 320 erreicht -- sonst waere das Ziel eine
    Qualitaetsverbesserung ("eine 192-kbit-MP3 darf nicht zu 320 kbit
    konvertiert werden"). Liegt sie schon als MP3 mit >= 320 kbit vor, gibt
    es nichts zu gewinnen (Generationsverlust ohne Nutzen).

    Liefert (decision, reason) mit decision in {'allow', 'skip'} und reason
    in {'', 'upscale', 'already_target'}.
    """
    suffix = suffix.lower()
    if target == TARGET_AIFF:
        if codec_family != "lossless":
            return "skip", "upscale"
        if suffix in (".aiff", ".aif"):
            return "skip", "already_target"
        return "allow", ""
    if target == TARGET_MP3_320:
        if codec_family == "lossless":
            return "allow", ""
        if codec_family == "lossy_mp3":
            return ("skip", "already_target") if declared_kbps >= 320 else ("skip", "upscale")
        return ("allow", "") if declared_kbps >= 320 else ("skip", "upscale")
    raise ValueError(f"Unbekanntes Ziel: {target}")


def convert_format(path: str, target: str, cfg: dict,
                    db_fallback: dict | None = None,
                    trash_original: bool = True) -> dict:
    """Konvertiert 'path' nach 'target' (TARGET_MP3_320 | TARGET_AIFF).

    db_fallback: optionale Metadaten aus der DB-Zeile (artist/title/album/
    album_artist/composer/genre/year/bpm/comment), fuer Felder, die die
    Datei selbst nicht traegt. None fuer Einzelpruefungs-Pfade ohne DB-Zeile.

    trash_original: Original nach erfolgreicher Konvertierung in den
    Papierkorb legen (Standard). False laesst das Original unangetastet
    liegen -- die konvertierte Datei entsteht dann als eigenstaendige,
    zusaetzliche Datei daneben, statt das Original zu ersetzen. Der
    Aufrufer (server.py) entscheidet danach, ob die DB-Zeile umgezogen
    (Ersetzen) oder eine neue angelegt wird (Original bleibt).

    Liefert bei Erfolg {"status": "converted", "old_path", "new_path", "row"}
    -- row in derselben Form wie analyzer.analyse_file(), passend fuer
    db.save(). Wirft ConvertSkip (Upscaling/bereits im Zielformat, kein
    Fehler) oder ConvertError (echter Fehler) -- der Aufrufer (server.py)
    faengt beide und baut daraus den Batch-Bericht.
    """
    from . import analyzer

    if target not in TARGETS:
        raise ConvertError(f"Unbekanntes Ziel: {target}")

    src = Path(path)
    if not src.is_file():
        raise ConvertError("Datei existiert nicht mehr.")

    try:
        orig_probe = probe_mod.probe(str(src))
    except Exception as exc:                          # noqa: BLE001
        raise ConvertError(f"Original nicht lesbar: {exc}") from exc
    if not orig_probe.ok:
        raise ConvertError(orig_probe.error or "Original nicht lesbar.")

    decision, reason = decide(orig_probe.codec_family, orig_probe.declared_kbps,
                               src.suffix, target)
    if decision == "skip":
        if reason == "upscale":
            raise ConvertSkip(
                "upscale",
                "Ziel wäre eine Qualitätsverbesserung (Upscaling) — abgelehnt.")
        raise ConvertSkip("already_target", "Datei liegt bereits im Zielformat.")

    orig_duration = orig_probe.duration_s
    target_suffix = ".mp3" if target == TARGET_MP3_320 else ".aiff"
    new_path = _unique_target(src.parent, src.stem, target_suffix)

    tmp = src.with_name(f".{src.stem}.aqc-{uuid.uuid4().hex[:8]}{target_suffix}")
    try:
        if target == TARGET_MP3_320:
            _encode_mp3_320(src, tmp)
        else:
            bit_depth = probe_mod.source_bit_depth(str(src))
            _encode_aiff(src, tmp, bit_depth)

        new_duration = probe_mod.probe(str(tmp)).duration_s
        if new_duration <= 0 or abs(new_duration - orig_duration) > max(1.0, orig_duration * 0.02):
            raise ConvertError(
                f"Neu kodierte Datei wirkt beschädigt (Dauer {new_duration:.1f}s "
                f"statt {orig_duration:.1f}s) — Original wurde nicht angefasst.")

        _transfer_tags(src, tmp, orig_probe, db_fallback)

        # Ab hier ist die neue Datei geprueft und vollstaendig -- erst jetzt
        # wird das Original beruehrt (bzw. bewusst NICHT, siehe trash_original).
        if trash_original:
            media.move_to_trash(str(src))
        os.replace(str(tmp), str(new_path))
    finally:
        _cleanup(tmp)

    st = os.stat(new_path)
    row = analyzer.analyse_file((str(new_path), st.st_size, st.st_mtime, cfg))
    return {"status": "converted", "old_path": str(src), "new_path": str(new_path),
            "row": row, "trashed": trash_original}


def _unique_target(folder: Path, stem: str, suffix: str) -> Path:
    """Zielpfad, der noch frei ist -- wie rename._unique_target(), aber fuer
    einen Endungswechsel statt eines Namenswechsels (die Quelle selbst hat ja
    eine andere Endung und zaehlt nie als eigene Kollision)."""
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _encode_mp3_320(src: Path, dst: Path) -> None:
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-y", "-nostdin",
        "-i", str(src),
        "-map", "0:a:0", "-map_metadata", "-1",
        "-c:a", "libmp3lame", "-b:a", "320k", "-compression_level", "0",
        "-id3v2_version", "0",
        str(dst),
    ]
    _run(cmd, dst)


def _encode_aiff(src: Path, dst: Path, bit_depth: int) -> None:
    # AIFF ist ein Big-Endian-Container -- anders als WAV (Little-Endian).
    # pcm_s16le/pcm_s24le liefert ffmpeg dafuer klaglos eine kaputte Datei
    # (Muxer-Fehler "Could not write header"), es muss die *be-Variante sein.
    codec = "pcm_s24be" if bit_depth >= 24 else "pcm_s16be"
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-y", "-nostdin",
        "-i", str(src),
        "-map", "0:a:0", "-map_metadata", "-1",
        "-c:a", codec,
        str(dst),
    ]
    _run(cmd, dst)


def _run(cmd: list[str], dst: Path) -> None:
    result = subprocess.run(cmd, capture_output=True, timeout=_ENCODE_TIMEOUT_S)
    if result.returncode != 0 or not dst.is_file():
        raise ConvertError(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "ffmpeg konnte die Datei nicht konvertieren.")


_DB_FIELD_MAP = {
    "artist": "artist", "title": "title", "album": "album",
    "album_artist": "album_artist", "composer": "composer", "genre": "genre",
    "comment": "comment",
}


def _transfer_tags(src: Path, dst: Path, orig_probe, db_fallback: dict | None) -> None:
    """Metadaten und Cover von 'src' (bzw. der DB-Zeile als Rueckfallebene)
    auf 'dst' (die neu kodierte, noch namenlose Zieldatei) uebertragen.

    Sonderfall WAV -> AIFF: beide sind ID3-Container mit demselben
    Wrapper-Prinzip (mutagen.wave.WAVE / mutagen.aiff.AIFF) -- hier wird
    zuerst das GESAMTE Tag-Objekt der Quelle uebernommen, damit unbekannte
    Frames (GEOB/PRIV, Serato-/Rekordbox-Cuepunkte) erhalten bleiben. Danach
    ueberschreiben die generischen Feld-Writes unten gezielt die
    normalisierten Felder -- GEOB/PRIV bleiben davon unberuehrt, write_tags()
    setzt/loescht nur benannte Frames.

    Fuer jede andere Quelle/Ziel-Kombination gibt es nichts zu bruecken:
    MP3/AAC/ALAC/FLAC-Container kennen kein GEOB/PRIV (ID3-exklusiv), und ein
    MP3/AAC-Quelle -> AIFF-Ziel ist ohnehin durch die Upscale-Regel blockiert,
    bevor diese Funktion ueberhaupt aufgerufen wird.
    """
    if src.suffix.lower() == ".wav" and dst.suffix.lower() == ".aiff":
        _bridge_wav_to_aiff(src, dst)

    extra = tags_mod.read_extra(str(src))
    fallback = db_fallback or {}

    def pick(key: str, probe_value: str = "") -> str:
        value = probe_value or extra.get(key) or ""
        if not value and key in _DB_FIELD_MAP:
            value = fallback.get(_DB_FIELD_MAP[key]) or ""
        return value

    year = extra.get("year") or 0
    if not year:
        year = fallback.get("year") or 0
    bpm = extra.get("bpm") or 0.0
    if not bpm:
        bpm = fallback.get("bpm") or 0.0
    track_no = extra.get("track_no") or 0
    if not track_no:
        track_no = fallback.get("track_no") or 0
    track_total = extra.get("track_total") or 0
    if not track_total:
        track_total = fallback.get("track_total") or 0

    fields = {
        "artist": pick("artist", orig_probe.artist),
        "title": pick("title", orig_probe.title),
        "album": pick("album", orig_probe.album),
        "album_artist": pick("album_artist"),
        "composer": pick("composer"),
        "genre": pick("genre"),
        "year": year,
        "bpm": bpm,
        "comment": pick("comment"),
        "track_no": track_no,
        "track_total": track_total,
    }
    tags_mod.write_tags(str(dst), fields)

    cover = tags_mod.read_cover(str(src))
    if cover is not None:
        data, mime = cover
        tags_mod.write_cover(str(dst), data, mime)


def _bridge_wav_to_aiff(src: Path, dst: Path) -> None:
    """Uebernimmt das komplette ID3-Tag-Objekt einer WAV-Quelle wortwoertlich
    auf die neu kodierte AIFF-Datei, damit GEOB/PRIV (Serato-/Rekordbox-
    Cuepunkte) erhalten bleiben -- dieselbe "ganzes Objekt umladen"-Idee wie
    rewrite.py, nur ueber die Wrapper-Klassengrenze WAVE -> AIFF hinweg."""
    from mutagen.wave import WAVE
    from mutagen.aiff import AIFF
    try:
        source_tags = WAVE(str(src))
    except Exception:                                  # noqa: BLE001
        return
    if source_tags.tags is None or len(source_tags.tags) == 0:
        return
    audio = AIFF(str(dst))
    if audio.tags is None:
        audio.add_tags()
    for frame in list(source_tags.tags.values()):
        audio.tags.add(frame)
    audio.save(padding=lambda info: 0)


def _cleanup(tmp: Path) -> None:
    try:
        if tmp.is_file():
            tmp.unlink()
    except OSError:
        pass
