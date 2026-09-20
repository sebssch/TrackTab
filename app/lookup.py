"""
Online-Metadatenvorschlaege -- rein manuell angestossen (Button im
Bearbeiten-Dialog), nie automatisch, nie serverseitig zwischengespeichert.

Shazam selbst hat keine nutzbare Public API fuer Drittanbieter. Beatport
wurde geprueft und verworfen: die Suchseite sitzt hinter einem
Cloudflare-Bot-Check (HTTP 403, 'cf-mitigated: challenge'), die offizielle
API (api.beatport.com/v4) verlangt einen Account/Client-Key (HTTP 401) --
beides ohne Login nicht nutzbar. Stattdessen drei Quellen, alle ohne
API-Key/Login:
  1. iTunes Search API   -- Artist/Titel/Album/Genre/Cover.
  2. Deezer               -- Artist/Titel/Album/Cover, als einzige Quelle
                              teilweise auch BPM (Track-Detail-Endpunkt).
  3. MusicBrainz           -- nur als Rueckfallebene, wenn beide anderen
                              nichts finden (strengeres Rate-Limit).

Jeder Quellen-Aufruf ist einzeln try/except-gekapselt: ein Netzwerkfehler
oder eine geaenderte API dieser Quelle darf weder den Endpunkt zum Absturz
bringen noch die anderen Quellen verhindern -- im Zweifel liefert eine
Quelle einfach eine leere Liste.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT_S = 8
_USER_AGENT = "tracktab/1.0 (lokales Tool, kein Server-Client)"

# Obergrenze fuer ein geladenes Cover -- resp.read() ohne Argument wuerde
# lesen, was auch immer die Gegenseite schickt.
_MAX_COVER_BYTES = 10 * 1024 * 1024
# Was als Cover in die Datei geschrieben werden darf. Dieselbe Liste wie
# server._COVER_MIMES; hier noch einmal, weil lookup.py bewusst nichts aus
# dem Server importiert.
_COVER_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp",
                          "image/avif", "image/bmp"})


def _is_public_host(host: str) -> bool:
    """
    Zeigt der Name auf eine oeffentlich erreichbare Adresse?

    Die Cover-URL kommt beim Ziehen eines Bildes aus dem Browser von einer
    beliebigen, nicht vertrauenswuerdigen Seite. Ohne diese Pruefung wuerde
    der Server als Bruecke ins lokale Netz dienen: Router-Oberflaechen,
    Dienste auf localhost, Adressen im eigenen Subnetz. Das Ergebnis liesse
    sich sogar zurueckholen -- es landet als Cover in der Datei und ist
    danach ueber /api/cover wieder lesbar.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return bool(infos)


def _year_from(raw: str) -> int | None:
    digits = "".join(c for c in str(raw or "")[:4] if c.isdigit())
    return int(digits) if len(digits) == 4 else None


def _get_json(url: str, headers: dict | None = None) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def fetch_cover(url: str) -> tuple[bytes, str] | None:
    """Laedt ein Cover-Bild von einer Lookup-Quelle (iTunes/Deezer-Artwork-URL)
    oder einer per Drag&Drop aus dem Browser gezogenen Bild-URL.
    Fuer die "Nur Cover uebernehmen"-Aktion im Tags-Dialog -- wie _get_json
    darf ein Fehler hier nie den Endpunkt zum Absturz bringen.
    Nur http(s) zulassen: die URL kommt bei einem Browser-Drop von einer
    beliebigen, nicht vertrauenswuerdigen Webseite -- urlopen() wuerde sonst
    auch file:/ftp:/... oeffnen (lokale Dateien lesbar machen). Aus demselben
    Grund muss das Ziel oeffentlich erreichbar sein (_is_public_host), die
    Antwort ein Bild und ihre Groesse begrenzt."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    try:
        if not _is_public_host(parts.hostname or ""):
            return None
    except ValueError:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            # urlopen folgt Umleitungen selbst -- die koennen wieder im
            # lokalen Netz landen, also das tatsaechliche Ziel pruefen.
            final = urllib.parse.urlsplit(resp.geturl())
            if final.scheme not in ("http", "https") \
                    or not _is_public_host(final.hostname or ""):
                return None
            mime = (resp.headers.get_content_type() or "").strip().lower()
            if mime not in _COVER_MIMES:
                return None
            data = resp.read(_MAX_COVER_BYTES + 1)
            if len(data) > _MAX_COVER_BYTES:
                return None
            return data, mime
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def search_itunes(artist: str, title: str, query: str = "") -> list[dict]:
    term = query.strip() if query else " ".join(p for p in (artist, title) if p).strip()
    if not term:
        return []
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": 5})
    data = _get_json(url)
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("results", []) or []:
        cover = item.get("artworkUrl100") or ""
        if cover:
            cover = cover.replace("100x100bb", "600x600bb")
        out.append({
            "source": "iTunes",
            "artist": item.get("artistName") or "",
            "title": item.get("trackName") or "",
            "album": item.get("collectionName") or "",
            "genre": item.get("primaryGenreName") or "",
            "year": _year_from(item.get("releaseDate")),
            "bpm": None,
            "cover": cover,
        })
    return out


def search_deezer(artist: str, title: str, query: str = "") -> list[dict]:
    term = query.strip() if query else " ".join(p for p in (artist, title) if p).strip()
    if not term:
        return []
    url = "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": term, "limit": 5})
    data = _get_json(url)
    if not isinstance(data, dict):
        return []
    out = []
    for item in (data.get("data") or [])[:5]:
        track_id = item.get("id")
        bpm = _deezer_bpm(track_id) if track_id else None
        album = item.get("album") or {}
        out.append({
            "source": "Deezer",
            "artist": (item.get("artist") or {}).get("name") or "",
            "title": item.get("title") or "",
            "album": album.get("title") or "",
            "genre": "",
            "year": None,
            "bpm": bpm,
            "cover": album.get("cover_medium") or album.get("cover") or "",
        })
    return out


def _deezer_bpm(track_id: int) -> float | None:
    data = _get_json(f"https://api.deezer.com/track/{track_id}")
    if not isinstance(data, dict):
        return None
    bpm = data.get("bpm") or 0
    return float(bpm) if bpm else None


def search_musicbrainz(artist: str, title: str, query: str = "") -> list[dict]:
    if query:
        q = query.strip()
    else:
        parts = []
        if artist:
            parts.append(f'artist:"{artist}"')
        if title:
            parts.append(f'recording:"{title}"')
        if not parts:
            return []
        q = " AND ".join(parts)
    if not q:
        return []
    url = "https://musicbrainz.org/ws/2/recording?" + urllib.parse.urlencode(
        {"query": q, "fmt": "json", "limit": 5})
    data = _get_json(url)
    if not isinstance(data, dict):
        return []
    out = []
    for rec in data.get("recordings", []) or []:
        credits = rec.get("artist-credit") or []
        artist_name = credits[0].get("name") if credits else ""
        releases = rec.get("releases") or []
        first_release = releases[0] if releases else {}
        album = first_release.get("title") or ""
        out.append({
            "source": "MusicBrainz",
            "artist": artist_name or "",
            "title": rec.get("title") or "",
            "album": album,
            "genre": "",
            "year": _year_from(first_release.get("date")),
            "bpm": None,
            "cover": "",
        })
    return out


_UPDATE_REPO = "sebssch/TrackTab"


def check_update(current_version: str) -> dict | None:
    """Vergleicht die laufende Version gegen den neuesten GitHub-Release.

    Manuell angestossen ueber den Link im Einstellungen-Dialog, nie
    automatisch. Liefert None bei jedem Fehler (Repo privat/nicht
    erreichbar, kein Release, unerwartete Antwort) -- derselbe stille
    Fallback wie bei den anderen Quellen in dieser Datei, der Aufrufer
    zeigt dafuer nur "nicht erreichbar" statt einer Fehlermeldung."""
    data = _get_json(f"https://api.github.com/repos/{_UPDATE_REPO}/releases/latest")
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name") or "").strip()
    latest = tag[1:] if tag.lower().startswith("v") else tag
    latest_parts = _version_tuple(latest)
    current_parts = _version_tuple(current_version)
    if latest_parts is None or current_parts is None:
        return None
    return {
        "current_version": current_version,
        "latest_version": latest,
        "update_available": latest_parts > current_parts,
        "release_url": data.get("html_url") or "",
    }


def _version_tuple(version: str) -> tuple[int, ...] | None:
    parts = str(version or "").strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def search(artist: str, title: str, query: str = "") -> list[dict]:
    """Fragt iTunes und Deezer immer ab (nur Deezer liefert BPM), MusicBrainz
    nur als Rueckfallebene wenn beide leer bleiben.

    'query' ist der manuelle Suchbegriff aus dem Fallback-Feld im Tags-Dialog
    (erscheint, wenn Interpret/Titel nichts finden) -- ersetzt dann in allen
    drei Quellen den sonst aus Interpret+Titel zusammengesetzten Suchbegriff."""
    results: list[dict] = []
    try:
        results += search_itunes(artist, title, query)
    except Exception:                                  # noqa: BLE001
        pass
    try:
        results += search_deezer(artist, title, query)
    except Exception:                                  # noqa: BLE001
        pass
    if not results:
        try:
            results += search_musicbrainz(artist, title, query)
        except Exception:                              # noqa: BLE001
            pass
    return results
