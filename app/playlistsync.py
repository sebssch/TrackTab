"""
Verknuepfte Playlisten: eine TrackTab-Liste haelt mit je einer Playlist in
Music.app und Rekordbox denselben Inhalt.

Regel (merge()): je Seite ein gemerkter letzter Stand (Baseline, Tabelle
playlist_link_items). Aenderung einer Seite = jetzt gegen ihre Baseline, nur
ueber Pfade, die TrackTab kennt. Neuer Stand S = (B_tracktab | alle neu) minus
alle entfernt; Hinzufuegen gewinnt bei Widerspruch. Erste Runde einer Seite
vereinigt nur. Smarte TrackTab-Listen gelten nur nach aussen (S = Regel-Inhalt).
Reihenfolge: TrackTab, Neues von aussen hinten an; Music.app sortiert nicht um.

Eine Seite, in die gerade nicht geschrieben werden kann (Rekordbox laeuft),
faellt aus der Runde ganz heraus: ihre Aenderungen werden nicht uebernommen und
ihre Baseline bleibt. Sonst wuerde ein dort entfernter Track bei jedem Lauf
erneut entfernt, auch nachdem man ihn in TrackTab wieder hinzugefuegt hat.

Diese Datei kennt keine HTTP-Details und keine Locks; server.py ruft sie auf.
"""

from __future__ import annotations

import unicodedata
from contextlib import nullcontext
from pathlib import Path

from . import media
from . import rekordbox as rekordbox_mod

# Schutz vor Massenloeschung: mehr als max(GUARD_MIN, GUARD_SHARE * Groesse)
# Entfernungen auf einer Seite oder eine ploetzlich leere Seite = Rueckfrage.
GUARD_MIN = 5
GUARD_SHARE = 0.25


def resolver(known: set[str]):
    """Fremder Pfad -> DB-Pfad oder None. macOS-Volumes sind case-insensitiv und
    die Unicode-Form kann abweichen (wie in server._get_rekordbox_playlist())."""
    loose: dict[str, str] = {}
    for p in known:
        for variant in (p, unicodedata.normalize("NFC", p), unicodedata.normalize("NFD", p)):
            loose.setdefault(variant.casefold(), p)

    def resolve(path: str) -> str | None:
        if not path:
            return None
        if path in known:
            return path
        return (loose.get(unicodedata.normalize("NFC", path).casefold())
                or loose.get(unicodedata.normalize("NFD", path).casefold())
                or loose.get(path.casefold()))

    return resolve


def _ordered_unique(paths) -> list[str]:
    seen: set[str] = set()
    out = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def merge(kind: str, tracktab: list[str], sides: dict[str, list[str]],
          base: dict[str, set[str]], first: dict[str, bool]) -> dict:
    """
    Reine Abgleich-Regel. kind: "playlist" oder "smart". tracktab: aktueller Inhalt
    der TrackTab-Liste (Reihenfolge). sides: {quelle: [bekannte Pfade in deren
    Reihenfolge]} nur fuer Seiten, die in dieser Runde mitmachen. base: {seite:
    Menge}, first: {seite: True} fuer Seiten ohne frueheren Abgleich.

    Liefert {"items": S (Reihenfolge), "tracktab": {add, remove}, "<quelle>": {add,
    remove}, "guard": [{side, remove, of}]}.
    """
    t_list = _ordered_unique(tracktab)
    t_set = set(t_list)
    side_sets = {s: set(_ordered_unique(v)) for s, v in sides.items()}
    if kind == "smart":
        result_set = set(t_set)
    else:
        b_t = set(base.get("tracktab") or set())
        adds = t_set - b_t
        rms = b_t - t_set
        for s, x in side_sets.items():
            b_x = set(base.get(s) or set())
            if first.get(s):
                adds |= x
            else:
                adds |= x - b_x
                rms |= b_x - x
        result_set = (b_t | adds) - (rms - adds)
    # Reihenfolge: TrackTab, dann Neues von aussen (Rekordbox vor Music, je in deren Folge).
    order = [p for p in t_list if p in result_set]
    have = set(order)
    for s in ("rekordbox", "music"):
        for p in _ordered_unique(sides.get(s) or []):
            if p in result_set and p not in have:
                order.append(p)
                have.add(p)
    for p in sorted(result_set - have):          # nur denkbar bei leerer Reihenfolge-Quelle
        order.append(p)
    out: dict = {"items": order, "guard": []}
    out["tracktab"] = {"add": [p for p in order if p not in t_set],
                       "remove": [p for p in t_list if p not in result_set]}
    for s, x in side_sets.items():
        out[s] = {"add": [p for p in order if p not in x],
                  "remove": [p for p in _ordered_unique(sides[s]) if p not in result_set]}
    # Schutz: ploetzlich leere Seite (frueher nicht leer) oder viele Entfernungen.
    if kind != "smart":
        for s, x in side_sets.items():
            if not x and not first.get(s) and base.get(s):
                out["guard"].append({"side": s, "remove": len(base[s]), "of": len(base[s]), "empty": True})
    targets = {"tracktab": t_list} if kind != "smart" else {}
    targets.update({s: list(side_sets[s]) for s in side_sets})
    for s, current in targets.items():
        n_rm = len(out[s]["remove"])
        if n_rm and n_rm > max(GUARD_MIN, GUARD_SHARE * len(current)):
            out["guard"].append({"side": s, "remove": n_rm, "of": len(current)})
    return out


def _norm(text) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip().casefold()


def file_meta(conn) -> dict[str, tuple]:
    """{pfad: (titel, interpret, groesse, dauer)} aller DB-Zeilen -- Grundlage fuer
    cloud_matcher() und die Cloud-Zuordnung in media.music_add_to_playlist()."""
    return {r["path"]: (r["title"] or Path(r["path"]).stem, r["artist"] or "",
                        int(r["size"] or 0), float(r["duration_s"] or 0))
            for r in conn.execute("SELECT path, title, artist, size, duration_s FROM files")}


def cloud_matcher(meta: dict[str, tuple]):
    """
    Music-Track ohne Speicherort -> DB-Pfad oder None. Music.app fuehrt einen
    eigenen Track manchmal nur noch als Cloud-Eintrag, obwohl die Datei weiter im
    Medienordner liegt (an echtem Material: nach dem Hinzufuegen zu einer Playlist
    am iPhone, Klasse 'shared track', 'location' leer). Zuordnung ueber gleichen
    Titel UND Interpreten, dann gleiche Groesse (Music meldet sie auch fuer
    Cloud-Eintraege, aufs Byte gleich), sonst Dauer auf 1 s (nach einem eigenen
    Tag-Schreiben stimmt die Groesse nicht mehr). Nur ein eindeutiger Treffer
    zaehlt, sonst bleibt der Track unbekannt -- nie raten.
    """
    by_name: dict[tuple, list] = {}
    for path, (title, artist, size, dur) in meta.items():
        by_name.setdefault((_norm(title), _norm(artist)), []).append((path, size, dur))

    def match(track: dict) -> str | None:
        cands = by_name.get((_norm(track.get("name")), _norm(track.get("artist"))))
        if not cands:
            return None
        size = int(track.get("size") or 0)
        hits = [p for p, s, _ in cands if size and s == size]
        if not hits:
            du = float(track.get("duration") or 0)
            hits = [p for p, _, d in cands if du and d and abs(d - du) <= 1.0]
        return hits[0] if len(hits) == 1 else None

    return match


# ── Seiten lesen ───────────────────────────────────────────────────────────
class LinkBroken(Exception):
    """Die verknuepfte fremde Playlist gibt es nicht mehr."""


def read_music(ext_id: str, resolve, cloud=None) -> dict:
    """{"paths": [DB-Pfade, Reihenfolge], "ids": {pfad: [track_ids]}, "unknown": n}.
    cloud: cloud_matcher() fuer Eintraege ohne Speicherort."""
    try:
        tracks = media.music_playlist_tracks(ext_id)
    except Exception as exc:                          # noqa: BLE001
        text = str(exc)
        if "-1728" in text or "-1719" in text:
            raise LinkBroken(text) from exc
        raise
    paths, ids, unknown = [], {}, 0
    for tr in tracks:
        p = resolve(tr.get("path") or "")
        if not p and not tr.get("path") and cloud:
            p = cloud(tr)
        if not p:
            unknown += 1
            continue
        paths.append(p)
        ids.setdefault(p, []).append(tr.get("id"))
    return {"paths": paths, "ids": ids, "unknown": unknown}


def read_rekordbox(ext_id: str, resolve) -> dict:
    """{"paths", "entries": {pfad: [entry_ids]}, "order": [(entry_id, pfad|None)], "unknown"}."""
    try:
        tracks = rekordbox_mod.playlist_tracks(ext_id)
    except rekordbox_mod.PlaylistNotFound as exc:
        raise LinkBroken(str(exc)) from exc
    paths, entries, order, unknown = [], {}, [], 0
    for tr in tracks:
        p = resolve(tr.get("path") or "")
        order.append((tr.get("entry_id"), p))
        if not p:
            unknown += 1
            continue
        paths.append(p)
        entries.setdefault(p, []).append(tr.get("entry_id"))
    return {"paths": paths, "entries": entries, "order": order, "unknown": unknown, "resolve": resolve}


# ── Seiten schreiben ───────────────────────────────────────────────────────
def write_music(ext_id: str, plan_side: dict, read: dict, meta: dict[str, tuple],
                skip: set[str]) -> dict:
    """Entfernen und Hinzufuegen in Music.app. meta: file_meta(). skip: Pfade, die
    nicht in der Mediathek sind und nicht importiert werden sollen. Liefert
    {"added", "removed", "missing", "state": neuer Stand der Seite}."""
    removed = 0
    to_remove = plan_side["remove"]
    if to_remove:
        track_ids = [i for p in to_remove for i in read["ids"].get(p, []) if i]
        if track_ids:
            removed = media.music_remove_from_playlist(ext_id, track_ids)
    want = [p for p in plan_side["add"] if p not in skip]
    added, missing = [], [p for p in plan_side["add"] if p in skip]
    if want:
        flags = media.music_add_to_playlist(ext_id, [(p, *(meta.get(p) or ("",))) for p in want])
        for p, ok in zip(want, flags):
            (added if ok else missing).append(p)
    state = (set(read["paths"]) - set(to_remove)) | set(added)
    return {"added": added, "removed": removed, "missing": missing, "state": state}


def write_rekordbox(ext_id: str, plan_side: dict, read: dict, order: list[str], push,
                    lock=None) -> dict:
    """Entfernen, Hinzufuegen (push(paths) = server._rekordbox_push(), bringt bei neuen
    Tracks TrackTabs Daten mit), dann Reihenfolge nach TrackTab. Eintraege, die
    TrackTab nicht kennt, behalten ihre relative Folge und stehen dahinter.
    lock: Schreib-Lock fuer Entfernen/Umsortieren (push nimmt ihn selbst).
    Liefert {"added", "removed", "reordered", "state"}."""
    lock = lock if lock is not None else nullcontext()
    removed = 0
    to_remove = plan_side["remove"]
    if to_remove:
        entry_ids = [e for p in to_remove for e in read["entries"].get(p, []) if e]
        if entry_ids:
            with lock:
                removed = len(rekordbox_mod.remove_entries_from_playlist(ext_id, entry_ids)["removed"])
    added: list[str] = []
    if plan_side["add"]:
        res = push(plan_side["add"])
        added = list(res.get("added") or [])
    # Reihenfolge: frisch lesen (neue Eintrags-IDs), Soll bilden, nur bei Abweichung schreiben.
    reordered = False
    tracks = rekordbox_mod.playlist_tracks(ext_id)
    resolve = read.get("resolve")
    current = [(t.get("entry_id"), resolve(t.get("path") or "") if resolve else t.get("path")) for t in tracks]
    by_path: dict[str, list] = {}
    for eid, p in current:
        if p:
            by_path.setdefault(p, []).append(eid)
    wanted = [eid for p in order for eid in by_path.get(p, [])]
    used = set(wanted)
    wanted += [eid for eid, _ in current if eid not in used]
    if [eid for eid, _ in current] != wanted:
        with lock:
            rekordbox_mod.reorder_playlist(ext_id, wanted)
        reordered = True
    state = {p for _, p in current if p}
    return {"added": added, "removed": removed, "reordered": reordered, "state": state}
