"""
Bitrate einer Datei korrigieren: neu kodieren, ersetzen, Tags erhalten.

Bisher hat das Tool nie an Audiodateien geschrieben — das hier ist die
Ausnahme, deshalb besonders vorsichtig: die neue Datei wird komplett gebaut
und geprueft, bevor irgendetwas am Original passiert. Erst dann wandert das
Original in den Papierkorb (nie hart geloescht) und die neue Datei nimmt
exakt seinen Platz ein, damit andere Programme (Rekordbox, Traktor, Finder)
den Track unter demselben Pfad weiterfinden.

Tags — inklusive der GEOB/PRIV-Frames, in denen Serato/Rekordbox Cue-Punkte
und Analyse-Daten ablegen — werden vom Original uebernommen statt neu
geschrieben, damit unbekannte Frames unangetastet bleiben.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from . import analyzer
from . import media
from . import probe as probe_mod
from . import tags as tags_mod

_ENCODE_TIMEOUT_S = 600


class RewriteError(RuntimeError):
    """Neukodierung fehlgeschlagen — das Original ist in jedem Fall unangetastet."""


def rewrite_bitrate(path: str, target_kbps: int, cfg: dict) -> dict:
    """Kodiert 'path' auf target_kbps (CBR) neu und ersetzt die Datei in place.

    Liefert die frische Analyse-Zeile (gleiche Form wie analyzer.analyse_file),
    passend zum Speichern per db.save().
    """
    src = Path(path)
    if not src.is_file():
        raise RewriteError("Datei existiert nicht mehr.")

    try:
        orig_probe = probe_mod.probe(str(src))
    except Exception as exc:                          # noqa: BLE001
        raise RewriteError(f"Original nicht lesbar: {exc}") from exc
    orig_duration = orig_probe.duration_s

    if orig_probe.codec_family == "lossless":
        raise RewriteError(
            "Keine Bitrate-Korrektur für verlustfreie Formate möglich — die "
            "echte Lösung ist eine tatsächlich verlustfreie Quelle, die "
            "sich nicht automatisch beschaffen lässt."
        )
    if not (32 <= int(target_kbps) <= 320):
        raise RewriteError("Ziel-Bitrate muss zwischen 32 und 320 kbps liegen.")

    codec = ("libmp3lame" if orig_probe.codec_family == "lossy_mp3"
             else media.resolved_aac_encoder(cfg))

    original_tags = _load_tags(src)
    _strip_junk_comment(original_tags)

    tmp = src.with_name(f".{src.stem}.aqc-{uuid.uuid4().hex[:8]}{src.suffix}")
    try:
        _encode(src, tmp, int(target_kbps), codec)
        if original_tags is not None:
            if codec == "libmp3lame":
                original_tags.save(str(tmp), v2_version=3)
            else:
                original_tags.save(str(tmp))

        new_duration = probe_mod.probe(str(tmp)).duration_s
        if new_duration <= 0 or abs(new_duration - orig_duration) > max(1.0, orig_duration * 0.02):
            raise RewriteError(
                f"Neu kodierte Datei wirkt beschädigt (Dauer {new_duration:.1f}s "
                f"statt {orig_duration:.1f}s) — Original wurde nicht angefasst.")

        # Ab hier ist die neue Datei geprueft — erst jetzt wird das Original beruehrt.
        media.move_to_trash(str(src))
        os.replace(str(tmp), str(src))
    finally:
        _cleanup(tmp)

    st = os.stat(src)
    return analyzer.analyse_file((str(src), st.st_size, st.st_mtime, cfg))


def _load_tags(src: Path):
    suffix = src.suffix.lower()
    try:
        if suffix in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4
            return MP4(str(src))
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            return ID3(str(src))
        except ID3NoHeaderError:
            return None
    except ImportError:
        return None
    except Exception:                                  # noqa: BLE001
        return None


def _strip_junk_comment(tags_obj) -> None:
    """Entfernt ein iTunSMPB-artiges Muell-Kommentarfeld (siehe
    tags.is_junk_comment()) aus dem geladenen Original, bevor es auf die neu
    kodierte Datei uebernommen wird -- sonst wuerde jede Neukodierung es
    unveraendert weiterschleppen, wie beim manuellen Tags-Speichern auch."""
    if tags_obj is None:
        return
    try:
        from mutagen.id3 import ID3
        if isinstance(tags_obj, ID3):
            frames = tags_obj.getall("COMM")
            if frames and any(tags_mod.is_junk_comment(str(f)) for f in frames):
                tags_obj.delall("COMM")
            return
        from mutagen.mp4 import MP4
        if isinstance(tags_obj, MP4) and tags_obj.tags is not None:
            cmt = tags_obj.tags.get("\xa9cmt")
            if cmt and any(tags_mod.is_junk_comment(str(v)) for v in cmt):
                del tags_obj.tags["\xa9cmt"]
    except Exception:                                  # noqa: BLE001
        pass


def _encode(src: Path, dst: Path, kbps: int, codec: str = "libmp3lame") -> None:
    cmd = [
        media.ffmpeg_path(), "-v", "error", "-y", "-nostdin",
        "-i", str(src),
        "-map", "0:a:0", "-map_metadata", "-1",
        "-c:a", codec, "-b:a", f"{kbps}k",
    ]
    if codec == "libmp3lame":
        cmd += ["-id3v2_version", "0"]
    cmd.append(str(dst))
    result = subprocess.run(cmd, capture_output=True, timeout=_ENCODE_TIMEOUT_S)
    if result.returncode != 0 or not dst.is_file():
        raise RewriteError(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "ffmpeg konnte die Datei nicht neu kodieren.")


def _cleanup(tmp: Path) -> None:
    try:
        if tmp.is_file():
            tmp.unlink()
    except OSError:
        pass
