"""
Erkennung von Metadaten-Auffaelligkeiten (Tag-Qualitaetsproblemen), getrennt
von der Audioqualitaets-Klassifikation in classify.py. Reines mutagen,
kein ffmpeg -- deutlich billiger als eine Neuanalyse (siehe recheck_all()).

Jeder Fund ist ein Dict {"code", "field", "value", "suggestion"} ohne
deutschen Text -- die Beschriftung kommt clientseitig aus i18n
("tagissue.<code>"). 'field' ist ein Kurzname wie "artist"/"title"/"" (fuer
dateiweite Probleme ohne ein einzelnes Feld). 'suggestion' ist nur bei
MOJIBAKE befuellt (Vorschau eines moeglichen Korrekturwerts, nie automatisch
geschrieben).

Ausloeser war ein realer Fund: ein literales Newline-Zeichen mitten im
Artist-Tag (TPE1 = "...Bebe Rexha\\n") zerlegte eine M3U-EXTINF-Zeile beim
Export in zwei Zeilen und verschob die Pfad-Zuordnung der Folgezeile.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from . import tags as tags_mod

# Dieselbe Endungs-Routing wie tags.py -- dort privat, hier dupliziert statt
# auf die Unterstrich-Namen eines fremden Moduls zuzugreifen.
_ID3_LIKE_SUFFIXES = (".mp3", ".wav", ".aiff", ".aif")
_MP4_SUFFIXES = (".m4a", ".mp4", ".aac")
_FLAC_SUFFIXES = (".flac",)

# ── Codes ─────────────────────────────────────────────────────────────────
ID3_V1_ONLY = "id3_v1_only"
ID3_V22 = "id3_v22"
CONTROL_CHARS = "control_chars"
MOJIBAKE = "mojibake"
UNBALANCED_BRACKETS = "unbalanced_brackets"
LEADING_TRAILING_WS = "leading_trailing_ws"
TAG_EQUALS_FILENAME = "tag_equals_filename"
IMPLAUSIBLE_YEAR = "implausible_year"
MALFORMED_TRACKNO = "malformed_trackno"
MISSING_TITLE = "missing_title"
MISSING_ARTIST = "missing_artist"
MISSING_COVER = "missing_cover"
OVERSIZED_COVER = "oversized_cover"
CORRUPT_COVER = "corrupt_cover"
UNRESOLVED_GENRE_CODE = "unresolved_genre_code"
DUPLICATE_FRAMES = "duplicate_frames"
NFC_NFD_MISMATCH = "nfc_nfd_mismatch"
EXT_CODEC_MISMATCH = "ext_codec_mismatch"

# Sicher automatisch behebbar (siehe fix_safe()) -- ein einziger
# write_tags()-Aufruf mit bereinigten Werten erledigt Text-Normalisierung
# UND hebt nebenbei ID3v1/v2.2 auf v2.3 an (write_tags() erzwingt das bei
# JEDEM Save, siehe tags.py:_id3_tags()) UND dedupliziert Mehrfach-Frames
# (setall() ersetzt IMMER alle vorhandenen Frames eines Typs).
AUTO_FIXABLE = {CONTROL_CHARS, LEADING_TRAILING_WS, ID3_V1_ONLY, ID3_V22,
                UNRESOLVED_GENRE_CODE, DUPLICATE_FRAMES, NFC_NFD_MISMATCH,
                OVERSIZED_COVER}
# Erfordert menschliches Urteilsvermoegen -- nie automatisch schreiben,
# nur anzeigen (ggf. mit 'suggestion') und in den Tags-Dialog verweisen.
MANUAL_ONLY = {MOJIBAKE, UNBALANCED_BRACKETS, TAG_EQUALS_FILENAME,
               IMPLAUSIBLE_YEAR, MALFORMED_TRACKNO, MISSING_TITLE,
               MISSING_ARTIST, MISSING_COVER,
               CORRUPT_COVER, EXT_CODEC_MISMATCH}

# Zielgroesse fuer uebergrosse eingebettete Cover -- laengere Kante,
# Seitenverhaeltnis bleibt erhalten.
_COVER_MAX_SIDE = 1000
_COVER_JPEG_QUALITY = 85

_STRING_FIELDS = ("artist", "title", "album", "album_artist", "composer",
                   "genre", "comment")

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|\n|\r")
# Typische Artefakte, wenn UTF-8 faelschlich als Latin-1/CP1252 interpretiert
# wurde (z.B. "Björk" -> "BjÃ¶rk", "–" -> "â€“"). Bewusst NICHT einfach "viel
# Nicht-ASCII" pruefen -- das würde jedes legitime "ü"/"é" faelschlich melden.
_MOJIBAKE_RE = re.compile(r"Ã[\x80-\xbf]|Â[\x80-\xbf]|â€[\x9c\x9d\x99\x93\x94]")
_GENRE_CODE_RE = re.compile(r"^\((\d+)\)$|^(\d+)$")
_TRACKNO_RE = re.compile(r"^\d+(/\d+)?$")
_MAX_COVER_BYTES = 2 * 1024 * 1024

# Standard-ID3v1-Genreliste (Winamp-erweitert, Index 0-191). Ein TCON-Wert
# wie "(17)" oder blankes "17" ist ein nie aufgeloester Zahlencode aus einer
# alten ID3v1-Quelle -- moderne Tagger schreiben den Klartext.
_ID3V1_GENRES = (
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel",
    "Noise", "Alternative Rock", "Bass", "Soul", "Punk", "Space",
    "Meditative", "Instrumental Pop", "Instrumental Rock", "Ethnic",
    "Gothic", "Darkwave", "Techno-Industrial", "Electronic", "Pop-Folk",
    "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta",
    "Top 40", "Christian Rap", "Pop/Funk", "Jungle", "Native US",
    "Cabaret", "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer",
    "Lo-Fi", "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro",
    "Musical", "Rock & Roll", "Hard Rock", "Folk", "Folk-Rock",
    "National Folk", "Swing", "Fast Fusion", "Bebop", "Latin", "Revival",
    "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock", "Progressive Rock",
    "Psychedelic Rock", "Symphonic Rock", "Slow Rock", "Big Band",
    "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech", "Chanson",
    "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass",
    "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango",
    "Samba", "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul",
    "Freestyle", "Duet", "Punk Rock", "Drum Solo", "A Cappella",
    "Euro-House", "Dance Hall", "Goa", "Drum & Bass", "Club-House",
    "Hardcore", "Terror", "Indie", "BritPop", "Afro-Punk", "Polsk Punk",
    "Beat", "Christian Gangsta Rap", "Heavy Metal", "Black Metal",
    "Crossover", "Contemporary Christian", "Christian Rock", "Merengue",
    "Salsa", "Thrash Metal", "Anime", "JPop", "Synthpop", "Abstract",
    "Art Rock", "Baroque", "Bhangra", "Big Beat", "Breakbeat", "Chillout",
    "Downtempo", "Dub", "EBM", "Eclectic", "Electro", "Electroclash",
    "Emo", "Experimental", "Garage", "Global", "IDM", "Illbient",
    "Industro-Goth", "Jam Band", "Krautrock", "Leftfield", "Lounge",
    "Math Rock", "New Romantic", "Nu-Breakz", "Post-Punk", "Post-Rock",
    "Psytrance", "Shoegaze", "Space Rock", "Trop Rock", "World Music",
    "Neoclassical", "Audiobook", "Audio Theatre", "Neue Deutsche Welle",
    "Podcast", "Indie Rock", "G-Funk", "Dubstep", "Garage Rock",
    "Psybient",
)

# Erwartete Endungen je codec_family (probe.classify_codec_family()) --
# groesstenteils redundant, aber deckt den Fall ab, dass eine Datei mit
# falscher Endung umbenannt/kopiert wurde.
_EXPECTED_SUFFIXES = {
    "lossy_mp3": {".mp3"},
    "lossy_aac": {".m4a", ".mp4", ".aac"},
    "lossless": {".flac", ".wav", ".aiff", ".aif", ".m4a", ".mp4"},
}


def _issue(code: str, field: str = "", value: str | None = None,
           suggestion: str | None = None) -> dict:
    return {"code": code, "field": field, "value": value, "suggestion": suggestion}


def _is_id3v1_only(path: str) -> bool:
    """True, wenn die Datei KEINEN ID3v2-Header hat, aber die letzten 128
    Bytes mit der ID3v1-Signatur "TAG" beginnen. mutagen bietet keine
    oeffentliche ID3v1-Klasse -- die Signatur ist Teil der ID3v1-Spezifikation
    und damit ein stabiler, oeffentlicher Formatdetail, keine mutagen-interne."""
    try:
        with open(path, "rb") as fh:
            fh.seek(-128, 2)
            return fh.read(3) == b"TAG"
    except OSError:
        return False


def _detect_id3(path: str, suffix: str) -> list[dict]:
    from mutagen.id3 import ID3, ID3NoHeaderError

    issues: list[dict] = []
    try:
        id3 = ID3(path)
        has_v2 = True
    except ID3NoHeaderError:
        id3 = None
        has_v2 = False
    except Exception:                                  # noqa: BLE001
        return issues

    if not has_v2:
        if _is_id3v1_only(path):
            issues.append(_issue(ID3_V1_ONLY))
        return issues
    if id3.version[:2] == (2, 2):
        issues.append(_issue(ID3_V22))

    _FRAME_TO_FIELD = {"TIT2": "title", "TPE1": "artist", "TALB": "album",
                        "TPE2": "album_artist", "TCOM": "composer",
                        "TCON": "genre", "COMM": "comment"}
    for frame_id, field in _FRAME_TO_FIELD.items():
        frames = id3.getall(frame_id)
        # COMM ist im ID3-Standard ueber (Sprache, Beschreibung) geschluesselt
        # -- mehrere COMM-Frames (z.B. "" und "ID3v1 Comment") sind normales,
        # gueltiges Verhalten vieler Tagger, kein Fehler. An echtem Material
        # bestaetigt: 6.110 von 10.822 Dateien haetten sonst faelschlich als
        # "doppelter Frame" gegolten. Andere Frames (TIT2/TPE1/...) sind laut
        # Standard NICHT schluessel-mehrfach und duerfen nur einmal vorkommen
        # -- trotzdem nur melden, wenn die Werte tatsaechlich voneinander
        # abweichen (ein Konflikt), nicht bei harmloser, identischer
        # Wiederholung.
        if (frame_id != "COMM" and len(frames) > 1 and
                len({str(f) for f in frames}) > 1):
            issues.append(_issue(DUPLICATE_FRAMES, field, str(frames[0])))
        if frames:
            issues.extend(_check_string(field, str(frames[0])))

    genre_frames = id3.getall("TCON")
    if genre_frames:
        m = _GENRE_CODE_RE.match(str(genre_frames[0]).strip())
        if m:
            idx = int(m.group(1) or m.group(2))
            if 0 <= idx < len(_ID3V1_GENRES):
                issues.append(_issue(UNRESOLVED_GENRE_CODE, "genre",
                                      suggestion=_ID3V1_GENRES[idx]))

    trck_frames = id3.getall("TRCK")
    if trck_frames and not _TRACKNO_RE.match(str(trck_frames[0]).strip()):
        issues.append(_issue(MALFORMED_TRACKNO, "track_no", str(trck_frames[0])))

    title = str(id3.getall("TIT2")[0]) if id3.getall("TIT2") else ""
    artist = str(id3.getall("TPE1")[0]) if id3.getall("TPE1") else ""
    issues.extend(_check_required(path, artist, title))
    issues.extend(_check_bracket_balance(title))

    apic = id3.getall("APIC")
    issues.extend(_check_cover(apic[0].data if apic else None))
    return issues


def _detect_mp4(path: str) -> list[dict]:
    from mutagen.mp4 import MP4

    issues: list[dict] = []
    try:
        audio = MP4(path)
    except Exception:                                  # noqa: BLE001
        return issues
    tags = audio.tags or {}

    _ATOM_TO_FIELD = {"\xa9nam": "title", "\xa9ART": "artist",
                       "\xa9alb": "album", "aART": "album_artist",
                       "\xa9wrt": "composer", "\xa9gen": "genre",
                       "\xa9cmt": "comment"}
    for atom, field in _ATOM_TO_FIELD.items():
        values = tags.get(atom)
        if values:
            issues.extend(_check_string(field, str(values[0])))

    trkn = tags.get("trkn")
    if trkn and trkn[0]:
        no = trkn[0][0] if len(trkn[0]) > 0 else 0
        total = trkn[0][1] if len(trkn[0]) > 1 else 0
        if no < 0 or total < 0:
            issues.append(_issue(MALFORMED_TRACKNO, "track_no", str(trkn[0])))

    title = (tags.get("\xa9nam") or [""])[0]
    artist = (tags.get("\xa9ART") or [""])[0]
    issues.extend(_check_required(path, str(artist), str(title)))
    issues.extend(_check_bracket_balance(str(title)))

    covers = tags.get("covr")
    issues.extend(_check_cover(bytes(covers[0]) if covers else None))
    return issues


def _detect_flac(path: str) -> list[dict]:
    from mutagen.flac import FLAC

    issues: list[dict] = []
    try:
        audio = FLAC(path)
    except Exception:                                  # noqa: BLE001
        return issues

    _VC_TO_FIELD = {"title": "title", "artist": "artist", "album": "album",
                     "albumartist": "album_artist", "composer": "composer",
                     "genre": "genre", "comment": "comment"}
    for vc_key, field in _VC_TO_FIELD.items():
        values = audio.get(vc_key)
        if values:
            # Vorbis-Kommentare erlauben absichtlich Mehrfachwerte je Schluessel
            # (z.B. mehrere ARTIST-Eintraege bei einer Kollaboration) -- nur
            # eine echte Wertabweichung ist ein Konflikt, reine Wiederholung
            # nicht.
            if len(values) > 1 and len({str(v) for v in values}) > 1:
                issues.append(_issue(DUPLICATE_FRAMES, field, str(values[0])))
            issues.extend(_check_string(field, str(values[0])))

    tracknumber = (audio.get("tracknumber") or [""])[0]
    if tracknumber and not _TRACKNO_RE.match(str(tracknumber).strip()):
        issues.append(_issue(MALFORMED_TRACKNO, "track_no", str(tracknumber)))

    title = (audio.get("title") or [""])[0]
    artist = (audio.get("artist") or [""])[0]
    issues.extend(_check_required(path, str(artist), str(title)))
    issues.extend(_check_bracket_balance(str(title)))

    issues.extend(_check_cover(bytes(audio.pictures[0].data) if audio.pictures else None))
    return issues


def _check_string(field: str, value: str) -> list[dict]:
    """Steuerzeichen/Leerraum/Mojibake/NFC fuer EIN Tag-Feld -- gemeinsam
    fuer alle Containerformate, damit sich eine Korrektur nicht je Format
    unterscheidet."""
    issues = []
    if _CONTROL_CHAR_RE.search(value):
        issues.append(_issue(CONTROL_CHARS, field, value))
    elif value != value.strip():
        # Nur melden, wenn KEIN Steuerzeichen vorliegt -- sonst doppelt
        # gegen denselben zugrundeliegenden Rohwert.
        issues.append(_issue(LEADING_TRAILING_WS, field, value))
    if _MOJIBAKE_RE.search(value):
        suggestion = None
        try:
            candidate = value.encode("latin-1").decode("utf-8")
            if candidate != value:
                suggestion = candidate
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        issues.append(_issue(MOJIBAKE, field, value, suggestion))
    if value and unicodedata.normalize("NFC", value) != value:
        issues.append(_issue(NFC_NFD_MISMATCH, field, value))
    return issues


def _check_required(path: str, artist: str, title: str) -> list[dict]:
    issues = []
    if not title.strip():
        issues.append(_issue(MISSING_TITLE))
    if not artist.strip():
        issues.append(_issue(MISSING_ARTIST))
    # NICHT allein "Titel == Dateiname" pruefen -- das ist bei gut getaggten
    # Dateien der Normalfall (der Dateiname wurde nach dem Titel benannt).
    # Erst wenn BEIDE Felder identisch dem rohen Dateinamen entsprechen, ist
    # das ein starkes Zeichen fuer nie editierte, automatisch befuellte Tags.
    stem = Path(path).stem.strip()
    if stem and artist.strip() == stem and title.strip() == stem:
        issues.append(_issue(TAG_EQUALS_FILENAME))
    return issues


def _check_bracket_balance(title: str) -> list[dict]:
    pairs = {")": "(", "]": "["}
    stack: list[str] = []
    for ch in title:
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if not stack or stack[-1] != pairs[ch]:
                return [_issue(UNBALANCED_BRACKETS, "title", title)]
            stack.pop()
    if stack:
        return [_issue(UNBALANCED_BRACKETS, "title", title)]
    return []


def _check_cover(data: bytes | None) -> list[dict]:
    if not data:
        return [_issue(MISSING_COVER)]
    issues = []
    if len(data) > _MAX_COVER_BYTES:
        issues.append(_issue(OVERSIZED_COVER, "cover", str(len(data))))
    try:
        from PIL import Image
        import io
        Image.open(io.BytesIO(data)).verify()
    except ImportError:
        pass                                            # PIL fehlt: Check uebersprungen
    except Exception:                                    # noqa: BLE001
        issues.append(_issue(CORRUPT_COVER))
    return issues


def _resize_cover(data: bytes) -> tuple[bytes, str] | None:
    """Verkleinert ein Cover auf hoechstens _COVER_MAX_SIDE Pixel (laengere
    Kante, Seitenverhaeltnis bleibt erhalten) und komprimiert es neu als
    JPEG -- deckt beide Ursachen von OVERSIZED_COVER ab: zu grosse
    Abmessungen UND ein ineffizientes Ausgangsformat (z.B. unkomprimiertes
    PNG), da thumbnail() bei bereits kleinen Bildern nichts tut, das
    JPEG-Resave aber immer greift. None, wenn die Bilddaten nicht lesbar
    sind -- das waere eigentlich CORRUPT_COVER, kein Fall fuer diesen Fix."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:                                    # noqa: BLE001
        return None
    if img.mode not in ("RGB", "L"):
        # JPEG kennt keine Transparenz -- ein RGBA-Cover (z.B. PNG) bekommt
        # sonst schwarze statt durchsichtiger Flaechen.
        img = img.convert("RGB")
    img.thumbnail((_COVER_MAX_SIDE, _COVER_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=_COVER_JPEG_QUALITY, optimize=True)
    return out.getvalue(), "image/jpeg"


def _check_ext_codec(path: str, codec_family: str) -> list[dict]:
    expected = _EXPECTED_SUFFIXES.get(codec_family)
    if not expected:
        return []
    suffix = Path(path).suffix.lower()
    if suffix and suffix not in expected:
        return [_issue(EXT_CODEC_MISMATCH, "", suffix)]
    return []


def detect(path: str, codec_family: str = "") -> list[dict]:
    """Alle Auffaelligkeiten einer Datei. Best effort wie tags.read_extra():
    liefert [] bei jedem Lesefehler statt den Scan abzubrechen."""
    suffix = Path(path).suffix.lower()
    issues: list[dict] = []
    try:
        if suffix in _ID3_LIKE_SUFFIXES:
            issues = _detect_id3(path, suffix)
        elif suffix in _MP4_SUFFIXES:
            issues = _detect_mp4(path)
        elif suffix in _FLAC_SUFFIXES:
            issues = _detect_flac(path)
    except Exception:                                  # noqa: BLE001
        return []
    issues.extend(_check_ext_codec(path, codec_family))
    return issues


def fix_safe(path: str) -> tuple[list[str], dict]:
    """Behebt alle sicher automatisch behebbaren Probleme (AUTO_FIXABLE) in
    einem einzigen write_tags()-Aufruf. Liefert (behobene Codes, geschriebene
    Feldwerte) -- Letzteres, damit Aufrufer (server.py) sowohl die DB-Spalten
    als auch eine Drop-Zeile ohne DB-Eintrag direkt auf den neuen Stand
    bringen koennen, ohne die Datei erneut zu lesen. Wirft tags.TagError wie
    write_tags() selbst, wenn das Schreiben fehlschlaegt."""
    found = detect(path)
    auto = [i for i in found if i["code"] in AUTO_FIXABLE]
    if not auto:
        return [], {}

    fields: dict = {}
    fixed_codes: set[str] = set()
    for issue in auto:
        code, field = issue["code"], issue["field"]
        if code in (ID3_V1_ONLY, ID3_V22):
            fixed_codes.add(code)
            continue
        if code == UNRESOLVED_GENRE_CODE:
            fields["genre"] = issue["suggestion"] or ""
            fixed_codes.add(code)
            continue
        if field and field in _STRING_FIELDS:
            current = fields.get(field, issue.get("value"))
            if current is None:
                continue
            cleaned = _CONTROL_CHAR_RE.sub("", current).strip()
            cleaned = unicodedata.normalize("NFC", cleaned)
            fields[field] = cleaned
            fixed_codes.add(code)
        elif code == DUPLICATE_FRAMES and field:
            # Feld stand nicht schon oben (kein Steuerzeichen/Whitespace/NFC-
            # Problem) -- trotzdem in 'fields' aufnehmen, damit setall() beim
            # Schreiben greift und die Mehrfach-Frames einsammelt.
            fields.setdefault(field, issue.get("value") or "")
            fixed_codes.add(code)
        elif code == OVERSIZED_COVER:
            # Eigener Schreibweg (write_cover() statt write_tags()) -- laeuft
            # deshalb sofort statt erst am Ende gesammelt mit den Textfeldern.
            cover = tags_mod.read_cover(path)
            if cover is None:
                continue
            resized = _resize_cover(cover[0])
            if resized is None:
                continue                                 # eigentlich CORRUPT_COVER
            tags_mod.write_cover(path, resized[0], resized[1])
            fixed_codes.add(code)

    if not fields and not any(
            c in (ID3_V1_ONLY, ID3_V22, OVERSIZED_COVER) for c in fixed_codes):
        return [], {}

    tags_mod.write_tags(path, fields)
    return sorted(fixed_codes), fields


def recheck_all(conn, cfg: dict) -> dict:
    """Liest bei jeder bekannten Datei die Tags frisch (mutagen, kein
    ffprobe/Spektralanalyse) und aktualisiert nur tag_issues. Deutlich
    billiger als 'scan --force' (mutagen-Open in der Groessenordnung
    ms/Datei statt Sekunden, vgl. tags.read_identity() laut CLAUDE.md
    "~1,3 ms je Datei"). Bewusst sequentiell -- I/O-gebunden, kein
    ffmpeg-Decode, ein ProcessPoolExecutor wuerde hier kaum etwas bringen."""
    rows = list(conn.execute("SELECT path, codec_family FROM files"))
    updates = []
    per_code: dict[str, int] = {}
    with_issues = 0
    for row in rows:
        issues = detect(row["path"], row["codec_family"] or "")
        if issues:
            with_issues += 1
            for i in issues:
                per_code[i["code"]] = per_code.get(i["code"], 0) + 1
        updates.append((json.dumps(issues, ensure_ascii=False), row["path"]))
    conn.executemany("UPDATE files SET tag_issues = ? WHERE path = ?", updates)
    conn.commit()
    return {"total": len(rows), "with_issues": with_issues, "per_code": per_code}
