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
from types import SimpleNamespace
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
# Titel-/Namens-Bereinigung (Schalter je Regel: tagclean_*/tagwatch_*, siehe _rules())
WEB_JUNK = "web_junk"
VIDEO_WORDS = "video_words"
UNDERSCORE_SPACES = "underscore_spaces"
TRACKNO_IN_TITLE = "trackno_in_title"
ARTIST_IN_TITLE = "artist_in_title"
REMIX_NOTATION = "remix_notation"
# Frueher "feat_notation" (eingebaute Featuring-Pruefung) -- abgeloest durch
# eigene Ersetzungsregeln (Suchen & Ersetzen, Nutzervorgabe). Alte Meldungen
# in der DB filtert enabled_issues() weg, bis die Datei neu geprueft wird.
_RETIRED_CODES = {"feat_notation"}
CASE_ALL = "case_all"
TYPO_QUOTES = "typo_quotes"

# Sicher automatisch behebbar (siehe fix_safe()) -- ein einziger
# write_tags()-Aufruf mit bereinigten Werten erledigt Text-Normalisierung
# UND hebt nebenbei ID3v1/v2.2 auf v2.3 an (write_tags() erzwingt das bei
# JEDEM Save, siehe tags.py:_id3_tags()) UND dedupliziert Mehrfach-Frames
# (setall() ersetzt IMMER alle vorhandenen Frames eines Typs).
AUTO_FIXABLE = {CONTROL_CHARS, LEADING_TRAILING_WS, ID3_V1_ONLY, ID3_V22,
                UNRESOLVED_GENRE_CODE, DUPLICATE_FRAMES, NFC_NFD_MISMATCH,
                OVERSIZED_COVER, WEB_JUNK, VIDEO_WORDS, UNDERSCORE_SPACES}
# Erfordert menschliches Urteilsvermoegen -- nie automatisch schreiben,
# nur anzeigen (ggf. mit 'suggestion') und in den Tags-Dialog verweisen.
MANUAL_ONLY = {MOJIBAKE, UNBALANCED_BRACKETS, TAG_EQUALS_FILENAME,
               IMPLAUSIBLE_YEAR, MALFORMED_TRACKNO, MISSING_TITLE,
               MISSING_ARTIST, MISSING_COVER,
               CORRUPT_COVER, EXT_CODEC_MISMATCH,
               TRACKNO_IN_TITLE, ARTIST_IN_TITLE, REMIX_NOTATION,
               CASE_ALL, TYPO_QUOTES}

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


# ── Titel-/Namens-Bereinigung ────────────────────────────────────────────
# Vorgabe je Regel (config.yaml/UI ueberschreiben): die drei sicheren laufen
# als Auto-Fix, der Rest meldet nur (MANUAL_ONLY) und gibt in 'suggestion'
# den bereinigten Wert an.
_RULE_DEFAULTS = {
    "tagclean_web": True, "tagclean_video": True, "tagclean_spaces": True,
    "tagwatch_trackno": True, "tagwatch_remix": True,
    "tagwatch_case": False, "tagwatch_quotes": False,
}


def _cfg() -> dict:
    try:
        from . import config as cfgmod
        return cfgmod.load()
    except Exception:                                  # noqa: BLE001
        return {}


# Regel-Schalter -> Auffaelligkeit, die er steuert (siehe _RULE_DEFAULTS).
_RULE_CODES = {
    "tagclean_web": WEB_JUNK, "tagclean_video": VIDEO_WORDS, "tagclean_spaces": UNDERSCORE_SPACES,
    "tagwatch_trackno": TRACKNO_IN_TITLE, "tagwatch_remix": REMIX_NOTATION,
    "tagwatch_case": CASE_ALL, "tagwatch_quotes": TYPO_QUOTES,
}


def enabled_issues(issues: list[dict], cfg: dict) -> list[dict]:
    """Gespeicherte Auffaelligkeiten ohne die Codes, deren Regel in den Einstellungen aus ist.
    In der DB steht der Stand der letzten Pruefung; wer eine Regel danach abschaltet, soll die
    Meldung sofort loswerden, ohne neu zu pruefen (Anzeige, nicht Speicherung)."""
    off = {code for key, code in _RULE_CODES.items()
           if not bool(cfg.get(key, _RULE_DEFAULTS[key]))} | _RETIRED_CODES
    return [i for i in issues if i.get("code") not in off]


def _rules() -> dict:
    cfg = _cfg()
    return {k: bool(cfg.get(k, d)) for k, d in _RULE_DEFAULTS.items()}


# Wortlisten und Ziel-Schreibweisen (config.py/Einstellungen). Fehlt ein Wert
# oder ist er leer/ungueltig, gilt die Vorgabe aus config.defaults().
_CHOICES = {
    "tagclean_underscore_single": ("space", "hyphen"),
    "tagclean_underscore_double": ("dash", "endash", "pipe", "space"),
    "tagwatch_remix_style": ("round", "square", "dash"),
    "tagwatch_case_style": ("title", "sentence", "upper", "lower"),
    "tagwatch_quotes_style": ("straight", "typographic"),
}
_LIST_KEYS = ("tagclean_web_words", "tagclean_web_tlds", "tagclean_video_remove",
              "tagwatch_remix_words")
# Duerfen neben einem Listenwort in der Klammer stehen ("Free Download").
_VIDEO_FILLER = {"free", "music", "out", "now"}
_UNDERSCORE_SINGLE = {"space": " ", "hyphen": "-"}
_UNDERSCORE_DOUBLE = {"dash": " - ", "endash": " \u2013 ", "pipe": " | ", "space": " "}
_RS_CACHE: list = [None, None]


def _alt(words) -> str:
    """Regex-Alternative aus einer Wortliste (laengste zuerst, Leerzeichen
    im Wort passen auf beliebig viel Leerraum)."""
    ws = sorted({str(w).strip().lower() for w in words if str(w).strip()},
                key=len, reverse=True)
    return "|".join(re.escape(w).replace(r"\ ", r"\s*") for w in ws)


def _ruleset() -> SimpleNamespace:
    """Aus den Einstellungen gebaute Regexe und Ziel-Schreibweisen. Wird bei
    unveraenderter Config wiederverwendet (der Aufruf steht je Datei und Feld)."""
    cfg = _cfg()
    try:
        from . import config as cfgmod
        dflt = cfgmod.defaults()
    except Exception:                                  # noqa: BLE001
        dflt = {}
    vals: dict = {}
    for k in _LIST_KEYS:
        v = cfg.get(k)
        vals[k] = tuple(str(x) for x in v) if isinstance(v, (list, tuple)) and v \
            else tuple(dflt.get(k, ()))
    for k, allowed in _CHOICES.items():
        v = str(cfg.get(k, "")).strip()
        vals[k] = v if v in allowed else (dflt.get(k) or allowed[0])
    key = tuple(sorted(vals.items()))
    if _RS_CACHE[0] == key:
        return _RS_CACHE[1]

    web_alt = _alt(vals["tagclean_web_words"])
    promo = (r"\s*[(\[]\s*(?:" + (web_alt + "|" if web_alt else "") +
             r"downloaded\s+from[^)\]]*|download\s+from[^)\]]*|www[^)\]]*)\s*[)\]]")
    tlds = "|".join(re.escape(t.strip().lstrip(".").lower())
                    for t in vals["tagclean_web_tlds"] if t.strip())
    remix_alt = _alt(vals["tagwatch_remix_words"]) or "remix"
    inner = r"([^()\[\]]*?\b(?:" + remix_alt + r"))"
    rs = SimpleNamespace(
        promo_re=re.compile(promo, re.I),
        domain_re=(re.compile(r"(?<![\w@])[\w-]+(?:\.[\w-]+)*\.(?:" + tlds + r")(?![\w.])", re.I)
                   if tlds else None),
        video_remove={w.strip().lower() for w in vals["tagclean_video_remove"]},
        us_single=_UNDERSCORE_SINGLE[vals["tagclean_underscore_single"]],
        us_double=_UNDERSCORE_DOUBLE[vals["tagclean_underscore_double"]],
        remix_style=vals["tagwatch_remix_style"],
        remix_round=re.compile(r"\(" + inner + r"\)", re.I),
        remix_square=re.compile(r"\[" + inner + r"\]", re.I),
        remix_dash=re.compile(
            r"\s+[-\u2013]\s+((?:(?!\s[-\u2013]\s)[^()\[\]])*?\b(?:" + remix_alt + r"))\s*$", re.I),
        remix_trailing=re.compile(r"\s*[(\[]" + inner + r"[)\]]\s*$", re.I),
        case_style=vals["tagwatch_case_style"],
        quotes_style=vals["tagwatch_quotes_style"],
    )
    _RS_CACHE[0], _RS_CACHE[1] = key, rs
    return rs


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)\]>]+", re.I)
_DOWNLOADED_FROM_RE = re.compile(r"\b(?:downloaded|download)\s+(?:from|at|on)\s+\S+", re.I)
_BRACKET_RE = re.compile(r"\s*[(\[]([^()\[\]]{1,40})[)\]]")
_VIDEO_TRAILING_RE = re.compile(
    r"\s*[-\u2013|]\s*(?:official\s+)?(?:music\s+)?(?:video|audio)\s*$"
    r"|\s*[-\u2013|]\s*lyrics?(?:\s+video)?\s*$", re.I)

_SEP_TIDY_RE = re.compile(r"^[\s\-\u2013|,;:_]+|[\s\-\u2013|,;:_]+$")
_EMPTY_BRACKETS_RE = re.compile(r"\s*[(\[]\s*[)\]]")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _tidy(s: str) -> str:
    s = _EMPTY_BRACKETS_RE.sub("", s)
    s = _MULTI_SPACE_RE.sub(" ", s)
    return _SEP_TIDY_RE.sub("", s)


def _strip_web(value: str) -> str:
    rs = _ruleset()
    v = rs.promo_re.sub("", value)
    v = _DOWNLOADED_FROM_RE.sub("", v)
    v = _URL_RE.sub("", v)
    if rs.domain_re:
        v = rs.domain_re.sub("", v)
    return _tidy(v) if v != value else value


def _strip_video(value: str) -> str:
    rs = _ruleset()

    def repl(m: re.Match) -> str:
        words = re.findall(r"[\w]+", m.group(1).lower())
        if words and all(w in rs.video_remove or w in _VIDEO_FILLER for w in words) and (
                any(w in rs.video_remove for w in words) or words == ["out", "now"]):
            return ""
        return m.group(0)
    v = _BRACKET_RE.sub(repl, value)
    v = _VIDEO_TRAILING_RE.sub("", v)
    return _tidy(v) if v != value else value


_EDGE_UNDERSCORE_RE = re.compile(r"^[\s_]+|[\s_]+$")


def _fix_spaces(value: str, field: str) -> str:
    """Unterstriche ersetzen (einzeln und doppelt getrennt einstellbar),
    Mehrfach-Leerzeichen zu einem, Rand trimmen. Nicht im Kommentar."""
    if field == "comment":
        return value
    rs = _ruleset()
    v = _EDGE_UNDERSCORE_RE.sub("", value)
    v = re.sub(r"_{2,}", rs.us_double, v)
    v = v.replace("_", rs.us_single)
    v = _MULTI_SPACE_RE.sub(" ", v)
    v = v.strip()
    return v if v != value else value


_TRACKNO_TITLE_RE = re.compile(
    r"^(?:(?:0\d{1,2}|\d{1,3})\s*[-\u2013._)]\s+|0\d{1,2}\s*[-\u2013._)])(?=\S)")
_TYPO_MAP = str.maketrans({"\u2019": "'", "\u2018": "'", "\u00b4": "'", "`": "'",
                           "\u201c": '"', "\u201d": '"', "\u201e": '"',
                           "\u201a": "'", "\u00ab": '"', "\u00bb": '"'})
_WORD_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")


def _trackno_clean(title: str) -> str:
    return _TRACKNO_TITLE_RE.sub("", title, count=1).strip()


def _artist_in_title_clean(artist: str, title: str) -> str | None:
    a = artist.strip()
    if len(a) < 2:
        return None
    m = re.match(re.escape(a) + r"\s+[-\u2013]\s+(?=\S)", title, re.I)
    return title[m.end():].strip() if m else None


def _remix_clean(title: str) -> str:
    """Remix-Zusatz in die gewaehlte Schreibweise bringen. Erkannt werden alle
    drei Formen ((X Remix), [X Remix], - X Remix). Bindestrich nur am Ende."""
    rs = _ruleset()
    v = title
    if rs.remix_style == "dash":
        return rs.remix_trailing.sub(lambda m: f" - {m.group(1).strip()}", v)
    o, c = ("(", ")") if rs.remix_style == "round" else ("[", "]")
    other = rs.remix_square if rs.remix_style == "round" else rs.remix_round
    v = other.sub(lambda m: f"{o}{m.group(1)}{c}", v)
    return rs.remix_dash.sub(lambda m: f" {o}{m.group(1)}{c}", v)


def _title_case(value: str) -> str:
    return _WORD_RE.sub(lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), value)


def _case_clean(value: str, style: str) -> str:
    if style == "upper":
        return value.upper()
    if style == "lower":
        return value.lower()
    if style == "sentence":
        low = value.lower()
        for i, ch in enumerate(low):
            if ch.isalpha():
                return low[:i] + ch.upper() + low[i + 1:]
        return low
    return _title_case(value)


def _quotes_clean(value: str, style: str) -> str:
    if style != "typographic":
        return value.translate(_TYPO_MAP)
    v = value.translate(str.maketrans({"\u00b4": "\u2019", "`": "\u2019"}))
    out: list[str] = []
    for i, ch in enumerate(v):
        prev = v[i - 1] if i else ""
        nxt = v[i + 1] if i + 1 < len(v) else ""
        opening = (not prev) or prev.isspace() or prev in "([{-\u2013/"
        if ch == '"':
            out.append("\u201c" if opening else "\u201d")
        elif ch == "'":
            if prev.isalnum() and nxt.isalpha():
                out.append("\u2019")                        # Apostroph: don't
            else:
                out.append("\u2018" if opening else "\u2019")
        else:
            out.append(ch)
    return "".join(out)


def _check_cleanup(artist: str, title: str, fields: dict) -> list[dict]:
    """Bereinigungs-Regeln ueber die Textfelder. 'fields' = {Feldname: Wert}
    (nur gesetzte). Jede Regel meldet je Feld einmal, mit dem bereinigten
    Wert als 'suggestion'. Unabhaengig vom Container, daher von allen drei
    Detektoren gleich aufgerufen."""
    rules = _rules()
    rs = _ruleset()
    issues: list[dict] = []
    for field, value in fields.items():
        if not value:
            continue
        # Die Regeln laufen nacheinander auf dem schon bereinigten Wert, so
        # traegt die LETZTE Meldung je Feld den vollstaendig bereinigten Wert
        # (Vorschlag im Tags-Dialog); 'value' bleibt der Ausgangswert.
        cur = value

        def add(code: str, new: str) -> None:
            nonlocal cur
            issues.append(_issue(code, field, value, new))
            cur = new

        if rules["tagclean_web"]:
            c = _strip_web(cur)
            if c != cur:
                add(WEB_JUNK, c)
        if rules["tagclean_video"] and field != "comment":
            c = _strip_video(cur)
            if c != cur:
                add(VIDEO_WORDS, c)
        if rules["tagclean_spaces"]:
            c = _fix_spaces(cur, field)
            if c != cur and c.strip():
                add(UNDERSCORE_SPACES, c)
        if field == "title" and rules["tagwatch_trackno"]:
            c = _trackno_clean(cur)
            if c != cur and c:
                add(TRACKNO_IN_TITLE, c)
            elif artist:
                c2 = _artist_in_title_clean(artist, cur)
                if c2:
                    add(ARTIST_IN_TITLE, c2)
        if field == "title" and rules["tagwatch_remix"]:
            c = _remix_clean(cur)
            if c != cur:
                add(REMIX_NOTATION, c)
        if (rules["tagwatch_case"] and field in ("title", "album")
                and " " in cur.strip()):
            letters = [ch for ch in cur if ch.isalpha()]
            if len(letters) >= 6:
                # Titel-/Satzschreibweise: nur komplett grosse/kleine Werte
                # sind eindeutig falsch. Bei "alles gross/klein" gilt jede
                # Abweichung vom Ziel.
                judged = (cur.isupper() or cur.islower()
                          if rs.case_style in ("title", "sentence") else True)
                c = _case_clean(cur, rs.case_style)
                if judged and c != cur:
                    add(CASE_ALL, c)
        if rules["tagwatch_quotes"] and field in ("title", "artist", "album"):
            c = _quotes_clean(cur, rs.quotes_style)
            if c != cur:
                add(TYPO_QUOTES, c)
    return issues


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
    issues.extend(_check_cleanup(artist, title, {
        fld: str(id3.getall(fid)[0]) for fid, fld in _FRAME_TO_FIELD.items()
        if fld != "genre" and id3.getall(fid)}))

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
    issues.extend(_check_cleanup(str(artist), str(title), {
        fld: str(tags[atom][0]) for atom, fld in _ATOM_TO_FIELD.items()
        if fld != "genre" and tags.get(atom)}))

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
    issues.extend(_check_cleanup(str(artist), str(title), {
        fld: str(audio[vk][0]) for vk, fld in _VC_TO_FIELD.items()
        if fld != "genre" and audio.get(vk)}))

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


def plan_safe(path: str, codes=None, skip_fields=()) -> dict:
    """Was fix_safe() an dieser Datei aendern wuerde, ohne zu schreiben (Vorschau
    des Quick Fix, /api/fix-tag-issues-preview). 'codes' grenzt auf diese
    Auffaelligkeiten ein (None = alle sicher behebbaren), 'skip_fields' laesst
    Felder aus -- Textfelder mit ihrem Namen, dazu "id3" (Versionsanhebung)
    und "cover" (Verkleinern).

    Liefert {"codes": [...], "fields": {feld: neu}, "old": {feld: alt},
    "field_codes": {feld: [codes]}, "other": {"id3"|"cover": [codes]}}.
    Die ID3-Anhebung passiert beim Schreiben eines beliebigen Felds ohnehin
    mit (write_tags() erzwingt v2.3), "id3" auszulassen wirkt also nur, wenn
    sonst nichts geschrieben wird."""
    found = detect(path)
    allow = set(codes) if codes is not None else None
    skip = set(skip_fields or ())
    fields: dict = {}
    old: dict = {}
    field_codes: dict = {}
    other: dict = {}
    fixed: set[str] = set()

    def note(field, code, value):
        old.setdefault(field, value or "")
        lst = field_codes.setdefault(field, [])
        if code not in lst:
            lst.append(code)
        fixed.add(code)

    for issue in found:
        code, field = issue["code"], issue["field"]
        if code not in AUTO_FIXABLE or (allow is not None and code not in allow):
            continue
        if code in (ID3_V1_ONLY, ID3_V22):
            if "id3" in skip:
                continue
            other.setdefault("id3", []).append(code)
            fixed.add(code)
            continue
        if code == OVERSIZED_COVER:
            # Eigener Schreibweg (write_cover()), erst fix_safe() verkleinert.
            if "cover" in skip:
                continue
            other.setdefault("cover", []).append(code)
            fixed.add(code)
            continue
        if not field or field in skip:
            continue
        if code == UNRESOLVED_GENRE_CODE:
            fields["genre"] = issue["suggestion"] or ""
            note("genre", code, issue.get("value"))
            continue
        if field in _STRING_FIELDS:
            current = fields.get(field, issue.get("value"))
            if current is None:
                continue
            cleaned = _CONTROL_CHAR_RE.sub("", current).strip()
            cleaned = unicodedata.normalize("NFC", cleaned)
            if code == WEB_JUNK:
                cleaned = _strip_web(cleaned)
            elif code == VIDEO_WORDS:
                cleaned = _strip_video(cleaned)
            elif code == UNDERSCORE_SPACES:
                cleaned = _fix_spaces(cleaned, field)
            fields[field] = cleaned
            note(field, code, issue.get("value"))
        elif code == DUPLICATE_FRAMES:
            # Feld stand nicht schon oben (kein Steuerzeichen/Whitespace/NFC-
            # Problem) -- trotzdem in 'fields' aufnehmen, damit setall() beim
            # Schreiben greift und die Mehrfach-Frames einsammelt.
            fields.setdefault(field, issue.get("value") or "")
            note(field, code, issue.get("value"))
    return {"codes": sorted(fixed), "fields": fields, "old": old,
            "field_codes": field_codes, "other": other}


def fix_safe(path: str, codes=None, skip_fields=()) -> tuple[list[str], dict]:
    """Behebt die sicher automatisch behebbaren Probleme (AUTO_FIXABLE, ggf.
    eingegrenzt wie plan_safe()) in einem einzigen write_tags()-Aufruf.
    Liefert (behobene Codes, geschriebene Feldwerte) -- Letzteres, damit
    Aufrufer (server.py) sowohl die DB-Spalten als auch eine Drop-Zeile ohne
    DB-Eintrag direkt auf den neuen Stand bringen koennen, ohne die Datei
    erneut zu lesen. Wirft tags.TagError wie write_tags() selbst, wenn das
    Schreiben fehlschlaegt. Die Rechnung steht nur in plan_safe() -- Vorschau
    und Schreiben duerfen nie auseinanderlaufen."""
    plan = plan_safe(path, codes, skip_fields)
    if not plan["codes"]:
        return [], {}
    fixed_codes = set(plan["codes"])
    fields = plan["fields"]
    if "cover" in plan["other"]:
        cover = tags_mod.read_cover(path)
        resized = _resize_cover(cover[0]) if cover is not None else None
        if resized is None:
            fixed_codes.discard(OVERSIZED_COVER)         # eigentlich CORRUPT_COVER
        else:
            tags_mod.write_cover(path, resized[0], resized[1])

    if not fields and not (fixed_codes & {ID3_V1_ONLY, ID3_V22, OVERSIZED_COVER}):
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
