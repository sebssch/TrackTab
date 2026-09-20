"""
Automatisches Umbenennen nach Metadaten.

Gedacht fuer die Einzelpruefung ("Dateien oeffnen"): frisch gekaufte Tracks
kommen je nach Shop mit voellig unterschiedlichen Dateinamen, die
Metadaten in der Datei sind dagegen einheitlich. Aus ihnen wird nach einem
frei konfigurierbaren Muster (cfg["rename_pattern"]) ein neuer Dateiname
gebaut.

Namenskonvention (siehe _slug()/_join()):
  Unterstrich  trennt die Kategorien   -> interpret_titel_bpm_key_bitrate_jahr
  Bindestrich  trennt Woerter/Werte innerhalb einer Kategorie -> daft-punk
Alles klein, ohne Umlaute und Sonderzeichen -- Dateinamen, die auf jedem
USB-Stick, in jedem DJ-Programm und in jeder Shell unfallfrei durchgehen.

Ein Platzhalter ohne Wert laesst seine Kategorie ersatzlos entfallen, statt
eine Luecke ("__") oder ein leeres Feld zu hinterlassen. Das deckt zugleich
die Regel fuer die Qualitaetsangabe ab: verlustfreie Formate (FLAC/WAV/
AIFF/ALAC) haben keine aussagekraeftige Bitrate, dort bleibt {bitrate} leer
und faellt damit automatisch weg.

Umbenannt wird nur ausserhalb der konfigurierten Bibliotheksordner (siehe
is_in_library()) -- was schon in der Bibliothek liegt, ist dort verlinkt
(Music.app, Rekordbox, Playlisten) und darf nicht unter den Fuessen
weggezogen werden.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from . import config as cfgmod

# Was in einem Muster stehen darf. Die Beschreibungen sind UI-Text (Hilfe im
# Einstellungs-Dialog), keine Kommentare -- deshalb hier mit Umlauten.
# Reihenfolge = Reihenfolge in der Hilfe und in der Dokumentation.
PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("artist", "Interpret"),
    ("title", "Titel"),
    ("album", "Album"),
    ("albumartist", "Albuminterpret"),
    ("composer", "Komponist"),
    ("genre", "Genre"),
    ("bpm", "Tempo, z. B. 116bpm"),
    ("key", "Tonart, z. B. 8a — schreibt Mixed In Key"),
    ("bitrate", "Qualität, z. B. 320kbps — bei verlustfreien Formaten leer"),
    ("year", "Erscheinungsjahr, z. B. 2013"),
    ("samplerate", "Abtastrate, z. B. 44.1khz"),
    ("track", "Tracknummer, zweistellig"),
)
PLACEHOLDER_KEYS = tuple(key for key, _ in PLACEHOLDERS)

DEFAULT_PATTERN = "{artist}_{title}_{bpm}_{key}_{bitrate}_{year}"

# Laengengrenze fuer den Namensteil ohne Endung. APFS/HFS+ erlauben 255
# Byte; mit Reserve fuer den Kollisions-Suffix ("-2") und mehrbytige Reste
# bleibt es hier deutlich darunter.
_MAX_STEM = 200

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Umlaute und verwandte Buchstaben zuerst ausschreiben -- die
# Unicode-Zerlegung unten wuerde aus "ue" sonst ein blosses "u" machen und
# aus "ß" gar nichts.
_TRANSLITERATE = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "æ": "ae", "ø": "oe", "å": "aa", "đ": "d", "ð": "d", "þ": "th", "ł": "l",
}


class RenameError(RuntimeError):
    """Umbenennen nicht moeglich -- mit einem Text fuer die Oberflaeche."""


# ── Bausteine ─────────────────────────────────────────────────────────────

def _slug(text) -> str:
    """Freitext zu einem Namensteil: klein, nur a-z/0-9, Bindestrich als
    Wortgrenze."""
    value = str(text or "").strip().lower()
    for src, dst in _TRANSLITERATE.items():
        value = value.replace(src, dst)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = "".join(c if (c.isascii() and c.isalnum()) else "-" for c in value)
    return re.sub(r"-+", "-", value).strip("-")


def _khz(sample_rate: int) -> str:
    """44100 -> '44.1khz', 48000 -> '48khz'."""
    if not sample_rate:
        return ""
    return f"{sample_rate / 1000:g}khz".replace(",", ".")


def values_for(meta: dict) -> dict[str, str]:
    """Die fertig formatierten Werte aller Platzhalter zu einem Metadaten-
    Satz (siehe read_meta()). Ein fehlender Wert ist ein leerer String -- die
    Kategorie faellt dann in build_name() weg."""
    try:
        bpm = float(meta.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    try:
        year = int(meta.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        track_no = int(meta.get("track_no") or 0)
    except (TypeError, ValueError):
        track_no = 0
    try:
        kbps = int(meta.get("declared_kbps") or 0)
    except (TypeError, ValueError):
        kbps = 0
    lossless = str(meta.get("codec_family") or "") == "lossless"

    return {
        "artist": _slug(meta.get("artist")),
        "title": _slug(meta.get("title")),
        "album": _slug(meta.get("album")),
        "albumartist": _slug(meta.get("album_artist")),
        "composer": _slug(meta.get("composer")),
        "genre": _slug(meta.get("genre")),
        "bpm": f"{int(round(bpm))}bpm" if bpm >= 1 else "",
        "key": _slug(meta.get("key")),
        # Verlustfrei hat keine aussagekraeftige "Bitrate" (sie haengt nur an
        # Bittiefe/Abtastrate, nicht an der Kompressionsstufe) -- die
        # Kategorie bleibt dort bewusst leer, siehe Modulkopf.
        "bitrate": "" if lossless else (f"{kbps}kbps" if kbps > 0 else ""),
        "year": str(year) if year > 0 else "",
        "samplerate": _khz(int(meta.get("sample_rate") or 0)),
        "track": f"{track_no:02d}" if track_no > 0 else "",
    }


def validate_pattern(pattern: str) -> str:
    """Prueft ein Muster aus dem Einstellungs-Dialog. Wirft ValueError mit
    einem Text, den der Dialog direkt anzeigen kann."""
    text = str(pattern or "").strip()
    if not text:
        raise ValueError("Namensmuster: darf nicht leer sein.")
    unknown = [name for name in _PLACEHOLDER_RE.findall(text)
               if name.lower() not in PLACEHOLDER_KEYS]
    if unknown:
        raise ValueError(
            f"Namensmuster: unbekannte Platzhalter {', '.join('{' + u + '}' for u in unknown)}. "
            f"Erlaubt sind {', '.join('{' + k + '}' for k in PLACEHOLDER_KEYS)}.")
    if not _PLACEHOLDER_RE.search(text):
        raise ValueError("Namensmuster: mindestens ein Platzhalter, "
                         "z.B. {artist}_{title}.")
    if any(c in text for c in "/\\:"):
        raise ValueError("Namensmuster: /, \\ und : sind in Dateinamen nicht erlaubt.")
    return text


# Platzhalter, die eine Datei ueberhaupt identifizieren. Steht keiner davon
# im Muster oder liefert keiner einen Wert, wird nicht umbenannt: ein Name
# aus lauter technischen Werten ("128kbps.mp3") sagt weniger aus als der
# vorhandene Dateiname und laesst sich nicht wieder zurueckholen.
_IDENTITY_KEYS = ("artist", "title", "album", "albumartist", "composer")


def has_identity(values: dict[str, str], pattern: str) -> bool:
    used = {name.lower() for name in _PLACEHOLDER_RE.findall(str(pattern))}
    wanted = used & set(_IDENTITY_KEYS)
    if not wanted:
        return True                    # Muster fragt gar nicht danach
    return any(values.get(k) for k in wanted)


def build_name(meta: dict, pattern: str) -> str:
    """Der Namensteil ohne Endung -- leer, wenn kein einziger Platzhalter
    einen Wert hatte (dann gibt es nichts zu benennen)."""
    return render_name(values_for(meta), pattern)


def render_name(values: dict[str, str], pattern: str) -> str:
    parts = []
    for segment in str(pattern).split("_"):
        filled = _PLACEHOLDER_RE.sub(
            lambda m: values.get(m.group(1).lower(), ""), segment)
        # Der Rest ist Freitext des Musters und wird derselben Konvention
        # unterworfen wie die Werte; ein leer gebliebener Platzhalter darf
        # dabei keinen einzelnen Bindestrich zuruecklassen.
        filled = re.sub(r"-+", "-", filled).strip("-")
        if filled:
            parts.append(filled)
    return "_".join(parts)[:_MAX_STEM].strip("-_")


# ── Metadaten und Bibliotheks-Abgrenzung ──────────────────────────────────

def read_meta(path: str) -> dict:
    """Alles, was die Platzhalter brauchen, direkt aus der Datei.

    Bewusst nicht aus einer Datenbankzeile: Einzelpruefungen haben keine
    (siehe server._post_analyse_path). Interpret/Titel/Album und die
    technischen Werte kommen aus derselben ffprobe-Quelle wie beim Scan,
    damit die Bitrate im Dateinamen mit der in der Tabelle uebereinstimmt.
    """
    from . import probe as probe_mod
    from . import tags as tags_mod

    res = probe_mod.probe(path)
    extra = tags_mod.read_extra(path)
    return {
        "artist": res.artist, "title": res.title, "album": res.album,
        "album_artist": extra.get("album_artist", ""),
        "composer": extra.get("composer", ""),
        "genre": extra.get("genre", ""),
        "year": extra.get("year", 0),
        "bpm": extra.get("bpm", 0.0),
        "track_no": extra.get("track_no", 0),
        "key": tags_mod.read_key(path),
        "declared_kbps": res.declared_kbps,
        "codec_family": res.codec_family,
        "sample_rate": res.sample_rate,
    }


def is_in_library(path: str, cfg: dict) -> bool:
    """Liegt die Datei in einem der konfigurierten Bibliotheksordner?

    Solche Dateien werden nicht umbenannt: sie sind anderswo unter ihrem
    Pfad verlinkt (Music.app, Rekordbox, Playlisten, eigene DB-Zeile), und
    ein neuer Name wuerde diese Verweise reissen.
    """
    try:
        target = Path(path).expanduser().resolve()
    except OSError:
        return False
    for root in cfg.get("library_paths") or []:
        try:
            base = cfgmod.resolve(root).resolve()
        except OSError:
            continue
        if target == base or base in target.parents:
            return True
    return False


def _unique_target(folder: Path, stem: str, suffix: str, source: Path) -> Path:
    """Zielpfad, der noch frei ist. Die Quelle selbst zaehlt nicht als
    besetzt -- auf einem Dateisystem, das Gross-/Kleinschreibung ignoriert
    (macOS-Vorgabe), meldet exists() sonst auch die eigene Datei."""
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists() and not _same_file(candidate, source):
        candidate = folder / f"{stem[:_MAX_STEM - 4]}-{n}{suffix}"
        n += 1
    return candidate


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


# ── Hauptweg ──────────────────────────────────────────────────────────────

def plan(path: str, cfg: dict) -> dict:
    """Was mit einer Datei passieren wuerde, ohne sie anzufassen.

    status: 'rename' | 'unchanged' | 'in_library' | 'no_data'
    """
    source = Path(path)
    if is_in_library(path, cfg):
        return {"path": path, "status": "in_library"}
    pattern = cfg.get("rename_pattern") or DEFAULT_PATTERN
    values = values_for(read_meta(path))
    stem = render_name(values, pattern)
    if not stem or not has_identity(values, pattern):
        return {"path": path, "status": "no_data"}
    target = _unique_target(source.parent, stem, source.suffix.lower(), source)
    if target == source:
        return {"path": path, "status": "unchanged"}
    return {"path": path, "status": "rename", "new_path": str(target)}


def rename_file(path: str, cfg: dict) -> dict:
    """Benennt eine Datei nach dem konfigurierten Muster um.

    Verschiebt nie in einen anderen Ordner -- nur der Name aendert sich.
    Liefert dasselbe Ergebnis-Dict wie plan(); bei status 'rename' zeigt
    'new_path' auf die bereits umbenannte Datei. Aufrufer muessen ab da mit
    diesem Pfad weiterarbeiten (Datenbank, Autorisierung, Oberflaeche) --
    unter dem alten ist die Datei nicht mehr auffindbar.
    """
    result = plan(path, cfg)
    if result["status"] != "rename":
        return result
    try:
        os.rename(path, result["new_path"])
    except OSError as exc:
        raise RenameError(f"Umbenennen fehlgeschlagen: {exc}") from exc
    return result
