"""
Die beiden Playlisten des Trainings, beide TrackTab-Playlisten der Instanz:

  Training   aus ihr wird gelernt
  Pruefliste an ihr wird gemessen (Gold-Set) -- nie Lernmaterial

Angegeben per Name in autocue-train.yaml, bei gleichen Namen mit Ordnerpfad
("Ordner/Name"). Nur normale Playlisten: intelligente berechnet nur der Browser.
Zum Testen geht fuer die Pruefliste auch eine Textdatei (ein Pfad je Zeile).
"""
from __future__ import annotations

from app import db as db_mod


class ListError(RuntimeError):
    pass


def _path_of(node: dict, by_id: dict) -> str:
    parts = [node["name"]]
    parent = by_id.get(node.get("parent_id"))
    while parent is not None:
        parts.append(parent["name"])
        parent = by_id.get(parent.get("parent_id"))
    return "/".join(reversed(parts))


def resolve(conn, name: str, what: str) -> tuple[str, list[str]]:
    """(Anzeigename, Pfade) einer Playlist. ListError mit anzeigbarem Text."""
    name = str(name or "").strip()
    if not name:
        raise ListError(f"Keine Playlist fuer „{what}“ angegeben (autocue-train.yaml).")
    nodes = db_mod.playlists_all(conn)
    by_id = {n["id"]: n for n in nodes}
    hits = [n for n in nodes if n["kind"] == "playlist"
            and (n["name"] == name or _path_of(n, by_id) == name)]
    if not hits:
        smart = [n for n in nodes if n["name"] == name and n["kind"] != "playlist"]
        if smart:
            raise ListError(f"„{name}“ ({what}) ist keine normale Playlist ({smart[0]['kind']}).")
        raise ListError(f"Playlist „{name}“ ({what}) gibt es in dieser Instanz nicht.")
    if len(hits) > 1:
        options = ", ".join(sorted(_path_of(n, by_id) for n in hits))
        raise ListError(f"„{name}“ ({what}) ist mehrdeutig -- mit Ordner angeben: {options}")
    paths = db_mod.playlist_items_map(conn).get(hits[0]["id"], [])
    if not paths:
        raise ListError(f"Playlist „{name}“ ({what}) ist leer.")
    return _path_of(hits[0], by_id), paths


def from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]
