"""
Tags lesen und schreiben: Titel, Interpret, Album, Albumkuenstler, Komponist,
Genre, Jahr, BPM, Tonart, Kommentar, Cover, Tracknummer/-gesamtzahl.

mutagen ist bereits Pflicht-Dependency (siehe rewrite.py, das beim
Neukodieren denselben Weg fuer den Tag-Erhalt nutzt). Fuer MP3/WAV/AIFF wird
das ID3-Objekt bearbeitet: es laedt beim Oeffnen ALLE Frames, auch unbekannte
wie GEOB/PRIV (Serato/Rekordbox-Cuepunkte und Analyse-Daten) -- gezieltes
Setzen einzelner Frames per setall()/delall() laesst den Rest unangetastet.
Fuer MP4/AAC/ALAC das MP4-Objekt (eigene Atom-Namen), fuer FLAC die
Vorbis-Kommentare. Anders als beim Neukodieren gibt es hier keine
Formatbeschraenkung auf verlustbehaftet -- ein Tag-Edit ruehrt den
Audio-Stream nicht an, betrifft also auch verlustfreie Formate.
"""
from __future__ import annotations

import re
from pathlib import Path

# Manche Konverter uebernehmen ein iTunSMPB-Gapless-Feld (eigentlich ein
# MP4-Atom fuer Encoder-Delay/Padding/Samplezahl, z.B. bei einer
# AAC/M4A->MP3-Wandlung VOR dieser App) unveraendert in ein Kommentarfeld --
# eine Reihe von Hex-Gruppen statt eines von Menschen geschriebenen Texts.
# Erkennung rein an der Form (mindestens zwei durch Leerzeichen getrennte
# Hex-Gruppen, je mindestens 6 Ziffern), nicht am genauen Wert -- deckt damit
# auch verwandte Varianten (z.B. iTunNORM) ab.
_JUNK_COMMENT_RE = re.compile(r"^[0-9A-Fa-f]{6,}(?:\s+[0-9A-Fa-f]{6,})+$")


def is_junk_comment(text: str) -> bool:
    """True, wenn 'text' wie ein technisches Hex-Feld statt wie ein
    Kommentar aussieht (siehe _JUNK_COMMENT_RE)."""
    return bool(_JUNK_COMMENT_RE.match((text or "").strip()))


# ── Tonart ───────────────────────────────────────────────────────────────────
# Intern (DB, Report) steht die Tonart immer in Camelot-Schreibweise ("8A").
# Ob Camelot, Open Key oder Notennamen angezeigt/geschrieben wird, entscheidet
# allein die Einstellung "key_notation" (format_key()). Eingelesen wird jede
# der drei Schreibweisen (normalize_key()).
# Notennamen in der Schreibweise von Mixed In Key (b statt #, ausser 2B/11A/12A).
_KEY_NOTES = {
    "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm",
    "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "C#m",
    "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb",
    "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
}
KEY_NOTATIONS = ("camelot", "openkey", "notes")
_NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _camelot_to_open(camelot: str) -> str:
    """8A -> 1m, 8B -> 1d (Open Key beginnt bei C-Dur/A-Moll mit 1)."""
    num = int(camelot[:-1])
    return f"{(num - 8) % 12 + 1}{'m' if camelot.endswith('A') else 'd'}"


KEY_CAMELOT = tuple(f"{n}{m}" for m in "AB" for n in range(1, 13))
# camelot -> (camelot, openkey, notes); Reihenfolge des Tupels = KEY_NOTATIONS
KEY_TABLE = {c: (c, _camelot_to_open(c), _KEY_NOTES[c]) for c in KEY_CAMELOT}


def _pc_to_camelot(pc: int, minor: bool) -> str:
    """Grundton (Halbtonklasse 0-11) -> Camelot. Dur: C=8B, je Quinte +1;
    Moll: Parallel-Dur (3 Halbtoene hoeher) uebernehmen."""
    major_pc = (pc + 3) % 12 if minor else pc
    num = (8 + 7 * major_pc - 1) % 12 + 1
    return f"{num}{'A' if minor else 'B'}"


def normalize_key(raw: str) -> str:
    """Tonart in beliebiger Schreibweise (Camelot "8a"/"08A", Open Key
    "1m"/"2d", Notennamen "Am"/"A minor"/"F#"/"Gb major") -> Camelot ("8A").
    Nicht Erkennbares bleibt unveraendert (nur getrimmt) -- nicht raten."""
    text = str(raw or "").strip()
    if not text:
        return ""
    m = re.fullmatch(r"0?(\d{1,2})\s*([ABab])", text)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(1))}{m.group(2).upper()}"
    m = re.fullmatch(r"0?(\d{1,2})\s*([dDmM])", text)
    if m and 1 <= int(m.group(1)) <= 12:
        num = int(m.group(1))
        # Open Key 1 = Camelot 8
        return f"{(num + 6) % 12 + 1}{'A' if m.group(2).lower() == 'm' else 'B'}"
    m = re.fullmatch(r"([A-Ga-g])\s*([#♯b♭]?)\s*(.*)", text)
    if m:
        pc = _NOTE_PC[m.group(1).upper()]
        acc = m.group(2)
        if acc in ("#", "♯"):
            pc += 1
        elif acc in ("b", "♭"):
            pc -= 1
        mode = m.group(3).strip().lower()
        if mode in ("", "maj", "major", "dur"):
            return _pc_to_camelot(pc % 12, False)
        if mode in ("m", "min", "minor", "moll"):
            return _pc_to_camelot(pc % 12, True)
    return text


def format_key(camelot: str, notation: str = "camelot") -> str:
    """Camelot-Wert (siehe normalize_key) in die gewaehlte Schreibweise
    umsetzen. Unbekannte Werte und unbekannte Schreibweisen bleiben stehen."""
    row = KEY_TABLE.get(normalize_key(camelot))
    if row is None or notation not in KEY_NOTATIONS:
        return str(camelot or "").strip()
    return row[KEY_NOTATIONS.index(notation)]


def key_aliases() -> dict[str, str]:
    """Kleingeschriebene Schreibweise -> Camelot, fuer den Client (Suche und
    Eingabefeld): alle drei Schreibweisen plus gaengige Varianten."""
    out: dict[str, str] = {}
    for camelot, names in KEY_TABLE.items():
        for name in names:
            out[name.lower()] = camelot
    for letter, base in _NOTE_PC.items():
        for acc, delta in (("", 0), ("#", 1), ("b", -1)):
            pc = (base + delta) % 12
            for suffix, minor in (("", False), ("m", True), (" minor", True),
                                  (" major", False), ("min", True), ("maj", False)):
                out.setdefault(f"{letter}{acc}{suffix}".lower(),
                               _pc_to_camelot(pc, minor))
    return out


_MP3_SUFFIXES = (".mp3",)
_WAVE_SUFFIXES = (".wav",)
_AIFF_SUFFIXES = (".aiff", ".aif")
_ID3_LIKE_SUFFIXES = _MP3_SUFFIXES + _WAVE_SUFFIXES + _AIFF_SUFFIXES
_MP4_SUFFIXES = (".m4a", ".mp4", ".aac")
_FLAC_SUFFIXES = (".flac",)


class TagError(RuntimeError):
    """Tags oder Cover konnten nicht gelesen/geschrieben werden."""


def _parse_year(raw: str) -> int:
    """Erste 4-stellige Jahreszahl aus einem Datumsstring ('2005-06-01',
    '2005' o.ae. je nach Tagger) -- 0, wenn keine gefunden wird."""
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    return int(digits[:4]) if len(digits) >= 4 else 0


def _parse_track_no(raw: str) -> int:
    """Fuehrende Zahl vor einem eventuellen '/Gesamtzahl' (Format '3/12' oder
    '3') -- 0, wenn keine gefunden wird."""
    head = str(raw or "").split("/")[0].strip()
    return int(head) if head.isdigit() else 0


def _parse_track_total(raw: str) -> int:
    """Gesamtzahl nach dem '/' im Format '3/12' -- 0, wenn keine da ist."""
    parts = str(raw or "").split("/")
    tail = parts[1].strip() if len(parts) > 1 else ""
    return int(tail) if tail.isdigit() else 0


def _format_track_no(no: int, total: int) -> str:
    """'3/12' bzw. nur '3', wenn keine Gesamtzahl bekannt ist."""
    return f"{int(no)}/{int(total)}" if total else str(int(no))


# ── ID3-Container (MP3 nativ, WAV/AIFF ueber ihren jeweiligen Wrapper) ──────
def _id3_tags(path: str, suffix: str):
    """Liefert (tags_obj, save_fn). Alle drei Container tragen ID3-Frames,
    nur die Huelle drumherum unterscheidet sich."""
    if suffix in _MP3_SUFFIXES:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        # padding=0 erzwingen: mutagen behaelt sonst das alte Padding bei,
        # wenn die neuen Frames hineinpassen -- die Dateigroesse bleibt dann
        # exakt gleich. Music.app erkennt Aenderungen an bereits importierten
        # Tracks offenbar ueber die Dateigroesse (an echtem Material
        # bestaetigt: Edits ohne Groessenaenderung blieben in Music.app
        # unsichtbar, auch nach erneutem Abspielen -- Rekordbox/Mixed in Key
        # aendern die Groesse bei jedem Schreiben und werden dort erkannt).
        return tags, lambda: tags.save(path, v2_version=3, padding=lambda info: 0)
    if suffix in _WAVE_SUFFIXES:
        from mutagen.wave import WAVE
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        return audio.tags, lambda: audio.save(padding=lambda info: 0)
    if suffix in _AIFF_SUFFIXES:
        from mutagen.aiff import AIFF
        audio = AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        return audio.tags, lambda: audio.save(padding=lambda info: 0)
    raise TagError(f"Kein ID3-Container fuer '{suffix}'.")


def _raw_id3(tags) -> str:
    return str(tags.getall("TKEY")[0]).strip() if tags.getall("TKEY") else ""


def _read_extra_id3(path: str, suffix: str) -> dict:
    tags, _ = _id3_tags(path, suffix)
    genre = ""
    if tags.getall("TCON"):
        genre = str(tags.getall("TCON")[0])
    bpm = 0.0
    if tags.getall("TBPM"):
        try:
            bpm = float(str(tags.getall("TBPM")[0]))
        except ValueError:
            bpm = 0.0
    album_artist = str(tags.getall("TPE2")[0]) if tags.getall("TPE2") else ""
    composer = str(tags.getall("TCOM")[0]) if tags.getall("TCOM") else ""
    # TYER (ID3v2.3, unser eigenes Schreibformat) zuerst, TDRC (v2.4, z.B.
    # von iTunes getaggte Dateien) als Fallback fuer Fremd-Tags.
    year = 0
    if tags.getall("TYER"):
        year = _parse_year(str(tags.getall("TYER")[0]))
    elif tags.getall("TDRC"):
        year = _parse_year(str(tags.getall("TDRC")[0]))
    comment = str(tags.getall("COMM")[0]) if tags.getall("COMM") else ""
    has_cover = 1 if tags.getall("APIC") else 0
    trck = str(tags.getall("TRCK")[0]) if tags.getall("TRCK") else ""
    track_no = _parse_track_no(trck)
    track_total = _parse_track_total(trck)
    return {"genre": genre, "bpm": bpm, "has_cover": has_cover,
            "album_artist": album_artist, "composer": composer,
            "year": year, "comment": comment,
            "track_no": track_no, "track_total": track_total,
            "key": normalize_key(_raw_id3(tags)), "key_raw": _raw_id3(tags)}


def _write_tags_id3(path: str, suffix: str, fields: dict) -> None:
    from mutagen.id3 import TPE1, TIT2, TALB, TPE2, TCOM, TCON, TYER, TBPM, TKEY, COMM, TRCK
    tags, save = _id3_tags(path, suffix)
    if "artist" in fields:
        tags.setall("TPE1", [TPE1(encoding=3, text=[fields["artist"] or ""])])
    if "title" in fields:
        tags.setall("TIT2", [TIT2(encoding=3, text=[fields["title"] or ""])])
    if "album" in fields:
        tags.setall("TALB", [TALB(encoding=3, text=[fields["album"] or ""])])
    if "album_artist" in fields:
        tags.setall("TPE2", [TPE2(encoding=3, text=[fields["album_artist"] or ""])])
    if "composer" in fields:
        tags.setall("TCOM", [TCOM(encoding=3, text=[fields["composer"] or ""])])
    if "genre" in fields:
        tags.setall("TCON", [TCON(encoding=3, text=[fields["genre"] or ""])])
    if "year" in fields:
        year = fields["year"]
        if year:
            tags.setall("TYER", [TYER(encoding=3, text=[str(int(year))])])
        else:
            tags.delall("TYER")
            tags.delall("TDRC")
    if "bpm" in fields:
        bpm = fields["bpm"]
        if bpm:
            tags.setall("TBPM", [TBPM(encoding=3, text=[str(int(round(float(bpm))))])])
        else:
            tags.delall("TBPM")
    if "key" in fields:
        key = (fields["key"] or "").strip()
        if key:
            tags.setall("TKEY", [TKEY(encoding=3, text=[key])])
        else:
            tags.delall("TKEY")
    if "comment" in fields:
        comment = fields["comment"] or ""
        if is_junk_comment(comment):
            comment = ""
        tags.delall("COMM")
        if comment:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[comment]))
    if "track_no" in fields or "track_total" in fields:
        existing = str(tags.getall("TRCK")[0]) if tags.getall("TRCK") else ""
        no = int(fields.get("track_no", _parse_track_no(existing)) or 0)
        total = int(fields.get("track_total", _parse_track_total(existing)) or 0)
        if no or total:
            tags.setall("TRCK", [TRCK(encoding=3, text=[_format_track_no(no, total)])])
        else:
            tags.delall("TRCK")
    save()


def _read_cover_id3(path: str, suffix: str) -> tuple[bytes, str] | None:
    tags, _ = _id3_tags(path, suffix)
    frames = tags.getall("APIC")
    if not frames:
        return None
    frame = frames[0]
    return bytes(frame.data), frame.mime or "image/jpeg"


def _write_cover_id3(path: str, suffix: str, data: bytes, mime: str) -> None:
    from mutagen.id3 import APIC
    tags, save = _id3_tags(path, suffix)
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    save()


def _delete_cover_id3(path: str, suffix: str) -> None:
    tags, save = _id3_tags(path, suffix)
    tags.delall("APIC")
    save()


# ── MP4 (M4A/AAC/ALAC) ──────────────────────────────────────────────────────
def _read_extra_mp4(path: str) -> dict:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    tags = audio.tags or {}
    genre = (tags.get("\xa9gen") or [""])[0]
    bpm = 0.0
    tmpo = tags.get("tmpo")
    if tmpo:
        try:
            bpm = float(tmpo[0])
        except (TypeError, ValueError, IndexError):
            bpm = 0.0
    album_artist = (tags.get("aART") or [""])[0]
    composer = (tags.get("\xa9wrt") or [""])[0]
    year = _parse_year((tags.get("\xa9day") or [""])[0])
    comment = (tags.get("\xa9cmt") or [""])[0]
    has_cover = 1 if tags.get("covr") else 0
    trkn = tags.get("trkn")
    track_no = int(trkn[0][0]) if trkn and trkn[0] and trkn[0][0] else 0
    track_total = int(trkn[0][1]) if trkn and trkn[0] and len(trkn[0]) > 1 and trkn[0][1] else 0
    return {"genre": genre, "bpm": bpm, "has_cover": has_cover,
            "album_artist": album_artist, "composer": composer,
            "year": year, "comment": comment,
            "track_no": track_no, "track_total": track_total,
            "key": normalize_key(_key_from_mp4(tags)),
            "key_raw": _key_from_mp4(tags).strip()}


# Es gibt kein Standard-Atom fuer die Tonart: iTunes/Mixed In Key schreiben ein
# Freiform-Atom, manche Konverter das (nicht offizielle) '\xa9key'.
_MP4_KEY_ATOM = "----:com.apple.iTunes:initialkey"
_MP4_KEY_ATOMS = (_MP4_KEY_ATOM, "----:com.apple.iTunes:KEY", "\xa9key")


def _key_from_mp4(tags) -> str:
    for name in _MP4_KEY_ATOMS:
        value = tags.get(name)
        if value:
            raw = value[0]
            return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return ""


def _write_tags_mp4(path: str, fields: dict) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if "artist" in fields:
        tags["\xa9ART"] = [fields["artist"] or ""]
    if "title" in fields:
        tags["\xa9nam"] = [fields["title"] or ""]
    if "album" in fields:
        tags["\xa9alb"] = [fields["album"] or ""]
    if "album_artist" in fields:
        tags["aART"] = [fields["album_artist"] or ""]
    if "composer" in fields:
        tags["\xa9wrt"] = [fields["composer"] or ""]
    if "genre" in fields:
        tags["\xa9gen"] = [fields["genre"] or ""]
    if "year" in fields:
        year = fields["year"]
        tags["\xa9day"] = [str(int(year))] if year else [""]
    if "bpm" in fields:
        bpm = fields["bpm"]
        tags["tmpo"] = [int(round(float(bpm)))] if bpm else [0]
    if "key" in fields:
        from mutagen.mp4 import MP4FreeForm
        key = (fields["key"] or "").strip()
        if key:
            tags[_MP4_KEY_ATOM] = [MP4FreeForm(key.encode("utf-8"))]
        elif _MP4_KEY_ATOM in tags:
            del tags[_MP4_KEY_ATOM]
    if "comment" in fields:
        comment = fields["comment"] or ""
        if is_junk_comment(comment):
            comment = ""
        tags["\xa9cmt"] = [comment]
    if "track_no" in fields or "track_total" in fields:
        trkn = tags.get("trkn")
        cur_no = trkn[0][0] if trkn and trkn[0] else 0
        cur_total = trkn[0][1] if trkn and trkn[0] and len(trkn[0]) > 1 else 0
        no = int(fields.get("track_no", cur_no) or 0)
        total = int(fields.get("track_total", cur_total) or 0)
        tags["trkn"] = [(no, total)]
    audio.save()


def _read_cover_mp4(path: str) -> tuple[bytes, str] | None:
    from mutagen.mp4 import MP4, MP4Cover
    audio = MP4(path)
    covers = (audio.tags or {}).get("covr")
    if not covers:
        return None
    cover = covers[0]
    mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    return bytes(cover), mime


def _write_cover_mp4(path: str, data: bytes, mime: str) -> None:
    from mutagen.mp4 import MP4, MP4Cover
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
    audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
    audio.save()


def _delete_cover_mp4(path: str) -> None:
    from mutagen.mp4 import MP4
    audio = MP4(path)
    if audio.tags is not None and "covr" in audio.tags:
        del audio.tags["covr"]
        audio.save()


# ── FLAC (Vorbis-Kommentare) ────────────────────────────────────────────────
def _read_extra_flac(path: str) -> dict:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    genre = (audio.get("genre") or [""])[0]
    bpm = 0.0
    if audio.get("bpm"):
        try:
            bpm = float(audio["bpm"][0])
        except (TypeError, ValueError, IndexError):
            bpm = 0.0
    album_artist = (audio.get("albumartist") or [""])[0]
    composer = (audio.get("composer") or [""])[0]
    year = _parse_year((audio.get("date") or [""])[0])
    comment = (audio.get("comment") or [""])[0]
    has_cover = 1 if audio.pictures else 0
    tracknumber = (audio.get("tracknumber") or [""])[0]
    track_no = _parse_track_no(tracknumber)
    track_total = _parse_track_total(tracknumber) or _parse_track_no((audio.get("tracktotal") or [""])[0])
    return {"genre": genre, "bpm": bpm, "has_cover": has_cover,
            "album_artist": album_artist, "composer": composer,
            "year": year, "comment": comment,
            "track_no": track_no, "track_total": track_total,
            "key": normalize_key(_key_from_flac(audio)),
            "key_raw": _key_from_flac(audio).strip()}


def _key_from_flac(audio) -> str:
    for name in ("initialkey", "key"):
        if audio.get(name):
            return str(audio[name][0])
    return ""


def _write_tags_flac(path: str, fields: dict) -> None:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    if "artist" in fields:
        audio["artist"] = fields["artist"] or ""
    if "title" in fields:
        audio["title"] = fields["title"] or ""
    if "album" in fields:
        audio["album"] = fields["album"] or ""
    if "album_artist" in fields:
        audio["albumartist"] = fields["album_artist"] or ""
    if "composer" in fields:
        audio["composer"] = fields["composer"] or ""
    if "genre" in fields:
        audio["genre"] = fields["genre"] or ""
    if "year" in fields:
        year = fields["year"]
        audio["date"] = str(int(year)) if year else ""
    if "bpm" in fields:
        bpm = fields["bpm"]
        audio["bpm"] = str(int(round(float(bpm)))) if bpm else ""
    if "key" in fields:
        key = (fields["key"] or "").strip()
        if key:
            audio["initialkey"] = key
        elif "initialkey" in audio:
            del audio["initialkey"]
    if "comment" in fields:
        comment = fields["comment"] or ""
        if is_junk_comment(comment):
            comment = ""
        audio["comment"] = comment
    if "track_no" in fields or "track_total" in fields:
        cur_tn = (audio.get("tracknumber") or [""])[0]
        cur_tt = (audio.get("tracktotal") or [""])[0]
        no = int(fields.get("track_no", _parse_track_no(cur_tn)) or 0)
        total = int(fields.get("track_total", _parse_track_total(cur_tt) or _parse_track_total(cur_tn)) or 0)
        audio["tracknumber"] = str(no) if no else ""
        if total:
            audio["tracktotal"] = str(total)
        elif "tracktotal" in audio:
            del audio["tracktotal"]
    audio.save()


def _read_cover_flac(path: str) -> tuple[bytes, str] | None:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    if not audio.pictures:
        return None
    pic = audio.pictures[0]
    return bytes(pic.data), pic.mime or "image/jpeg"


def _write_cover_flac(path: str, data: bytes, mime: str) -> None:
    from mutagen.flac import FLAC, Picture
    audio = FLAC(path)
    audio.clear_pictures()
    pic = Picture()
    pic.data = data
    pic.mime = mime
    pic.type = 3
    audio.add_picture(pic)
    audio.save()


def _delete_cover_flac(path: str) -> None:
    from mutagen.flac import FLAC
    audio = FLAC(path)
    audio.clear_pictures()
    audio.save()


# ── Oeffentliche Schnittstelle ───────────────────────────────────────────────
def read_extra(path: str) -> dict:
    """Albumkuenstler, Komponist, Genre, Jahr, BPM, Tonart (als Camelot,
    siehe normalize_key()), Kommentar,
    Cover-Vorhandensein und Tracknummer/-gesamtzahl -- fuer den Scan
    (Interpret/Titel/Album kommen weiterhin aus probe.py/ffprobe).

    Best effort: liefert bei jedem Fehler (kaputte/unbekannte Datei) leere
    Werte statt den Scan abzubrechen, wie probe.py es fuer optionale Details
    bereits handhabt.
    """
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            return _read_extra_id3(path, suffix)
        if suffix in _MP4_SUFFIXES:
            return _read_extra_mp4(path)
        if suffix in _FLAC_SUFFIXES:
            return _read_extra_flac(path)
    except Exception:                                  # noqa: BLE001
        pass
    return {"genre": "", "bpm": 0.0, "has_cover": 0,
            "album_artist": "", "composer": "", "year": 0, "comment": "",
            "track_no": 0, "track_total": 0, "key": "", "key_raw": ""}


def read_key(path: str) -> str:
    """Die Tonart ("initial key") genau so, wie die Datei sie traegt (z.B.
    "8A" oder "Abm") -- leer, wenn die Datei keine hat. Fuer den Platzhalter
    {key} beim automatischen Umbenennen; der Scan nimmt stattdessen den
    normalisierten Wert aus read_extra()["key"].

    Geschrieben wird die Tonart in der Regel von aussen (Mixed In Key, siehe
    media.open_in_mik()) oder ueber den Tags-Dialog (write_tags()).

    Best effort wie read_extra(): bei jedem Fehler ein leerer Wert.
    """
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            tags, _ = _id3_tags(path, suffix)
            return str(tags.getall("TKEY")[0]) if tags.getall("TKEY") else ""
        if suffix in _MP4_SUFFIXES:
            from mutagen.mp4 import MP4
            return _key_from_mp4(MP4(path).tags or {})
        if suffix in _FLAC_SUFFIXES:
            from mutagen.flac import FLAC
            return _key_from_flac(FLAC(path))
    except Exception:                                  # noqa: BLE001
        pass
    return ""


def write_tags(path: str, fields: dict) -> None:
    """Schreibt eine Teilmenge von Titel/Interpret/Album/Albumkuenstler/
    Komponist/Genre/Jahr/BPM/Tonart/Kommentar/Tracknummer/-gesamtzahl in die
    Datei (die Tonart so, wie sie uebergeben wird -- Umsetzen in die gewaehlte
    Schreibweise ist Sache des Aufrufers, siehe format_key()).

    'fields' darf eine Teilmenge der Schluessel sein -- nur die angegebenen
    werden geaendert, alles andere (inkl. GEOB/PRIV bei ID3) bleibt
    unangetastet. Anders als beim Lesen fuer den Scan wird ein Fehler hier
    NICHT verschluckt: ein fehlgeschlagenes Schreiben muss dem Aufrufer
    sichtbar werden.
    """
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            _write_tags_id3(path, suffix, fields)
        elif suffix in _MP4_SUFFIXES:
            _write_tags_mp4(path, fields)
        elif suffix in _FLAC_SUFFIXES:
            _write_tags_flac(path, fields)
        else:
            raise TagError(f"Tag-Bearbeitung fuer '{suffix}' wird nicht unterstuetzt.")
    except TagError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise TagError(f"Tags konnten nicht geschrieben werden: {exc}") from exc


def read_identity(path: str) -> dict:
    """Dauer plus Interpret/Titel/Album -- die Merkmale, an denen sich eine
    verschobene Datei wiedererkennen laesst (siehe scanner.find_moved()).

    Bewusst ueber mutagen statt ueber probe.py/ffprobe: hier wird im Zweifel
    eine ganze Bibliothek durchgesehen, und ein Unterprozess je Datei waere
    dafuer nicht vertretbar. mutagen liest Tags und Dauer in einem Rutsch aus
    demselben geoeffneten Objekt.

    mutagen.File(easy=True) liefert die Tags fuer MP3/MP4/FLAC bereits unter
    den normierten Namen artist/title/album. Bei WAV/AIFF nicht: deren .tags
    haengt am RIFF/IFF-Wrapper und bleibt rohes ID3 -- dafuer der Rueckfall
    auf die Frames.

    Best effort wie read_extra(): bei jedem Fehler leere Werte statt einer
    Ausnahme, eine unlesbare Datei faellt einfach als Kandidat aus.
    """
    import mutagen
    out = {"duration_s": 0.0, "artist": "", "title": "", "album": ""}
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return out
        if getattr(audio, "info", None) is not None:
            out["duration_s"] = float(getattr(audio.info, "length", 0.0) or 0.0)
        for key in ("artist", "title", "album"):
            value = (audio.get(key) or [""])[0] if audio.tags is not None else ""
            out[key] = str(value or "")
        if not out["artist"] and not out["title"]:
            suffix = Path(path).suffix.lower()
            if suffix in _WAVE_SUFFIXES + _AIFF_SUFFIXES:
                frames, _ = _id3_tags(path, suffix)
                for key, frame in (("artist", "TPE1"), ("title", "TIT2"),
                                   ("album", "TALB")):
                    got = frames.getall(frame)
                    if got:
                        out[key] = str(got[0])
    except Exception:                                  # noqa: BLE001
        pass
    return out


def read_cover(path: str) -> tuple[bytes, str] | None:
    """Liefert (Bilddaten, MIME-Typ) des eingebetteten Covers, oder None."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            return _read_cover_id3(path, suffix)
        if suffix in _MP4_SUFFIXES:
            return _read_cover_mp4(path)
        if suffix in _FLAC_SUFFIXES:
            return _read_cover_flac(path)
    except Exception:                                  # noqa: BLE001
        pass
    return None


def write_cover(path: str, data: bytes, mime: str) -> None:
    """Ersetzt das eingebettete Cover vollstaendig durch 'data'."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            _write_cover_id3(path, suffix, data, mime)
        elif suffix in _MP4_SUFFIXES:
            _write_cover_mp4(path, data, mime)
        elif suffix in _FLAC_SUFFIXES:
            _write_cover_flac(path, data, mime)
        else:
            raise TagError(f"Cover-Bearbeitung fuer '{suffix}' wird nicht unterstuetzt.")
    except TagError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise TagError(f"Cover konnte nicht geschrieben werden: {exc}") from exc


def delete_cover(path: str) -> None:
    """Entfernt ein eingebettetes Cover vollstaendig, falls vorhanden."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            _delete_cover_id3(path, suffix)
        elif suffix in _MP4_SUFFIXES:
            _delete_cover_mp4(path)
        elif suffix in _FLAC_SUFFIXES:
            _delete_cover_flac(path)
        else:
            raise TagError(f"Cover-Bearbeitung fuer '{suffix}' wird nicht unterstuetzt.")
    except TagError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise TagError(f"Cover konnte nicht geloescht werden: {exc}") from exc
