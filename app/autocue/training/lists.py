"""
Die beiden Playlisten des Trainings, beide TrackTab-Playlisten der Instanz:

  Training   aus ihr wird gelernt
  Pruefliste an ihr wird gemessen (Gold-Set) -- nie Lernmaterial

Im CLI per Name in autocue-train.yaml, bei gleichen Namen mit Ordnerpfad
("Ordner/Name"), nur normale Playlisten: smarte berechnet nur der Browser. Die App
schickt deshalb die Pfade gleich mit (given()), dort gehen auch smarte.
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


def given(name: str, paths: list, what: str) -> tuple[str, list[str]]:
    """Schon aufgeloeste Liste aus der App (normale oder smarte Playlist; den Inhalt
    smarter Listen kennt nur der Browser). ListError, wenn leer."""
    name = str(name or "").strip() or what
    paths = list(dict.fromkeys(p for p in paths or [] if p))
    if not paths:
        raise ListError(f"Playlist „{name}“ ({what}) ist leer.")
    return name, paths


def resolve(conn, name: str, what: str) -> tuple[str, list[str]]:
    """(Anzeigename, Pfade) einer Playlist. ListError mit anzeigbarem Text.
    name: Name, "Ordner/Name" oder "id:<id>" (Auswahl in der App)."""
    name = str(name or "").strip()
    if not name:
        raise ListError(f"Keine Playlist für „{what}“ ausgewählt.")
    nodes = db_mod.playlists_all(conn)
    by_id = {n["id"]: n for n in nodes}
    if name.startswith("id:"):
        hits = [n for n in nodes if n["kind"] == "playlist" and str(n["id"]) == name[3:]]
        if not hits:
            raise ListError(f"Die Playlist für „{what}“ gibt es nicht mehr.")
    else:
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
        raise ListError(f"Playlist „{_path_of(hits[0], by_id)}“ ({what}) ist leer.")
    return _path_of(hits[0], by_id), paths


def from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]
