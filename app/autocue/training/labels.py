"""
Hand-Cues eines Tracks als Labels fuers Training und als Wahrheit fuer die Bewertung.

Quelle in dieser Reihenfolge, alles auf TrackTabs Zeitachse:
  1. dj_track.cues -- in der DJ-Ansicht bearbeitet (schon eigene Achse)
  2. rb_snapshot.cues -- Rekordbox-Stand vom Abgleich, um den Versatz verschoben

Was nicht von Hand stammt, faellt heraus:
  - Kommentare mit dem Praefix des alten Autocue-Tools ("A:", "v3:", "v26:", "AutoCue"),
    wie dessen is_autocue_comment();
  - Cues, die hoechstens einen Schlag neben dem liegen, was Auto Cue in TrackTab
    zuletzt an dieselbe Stelle gesetzt hat (Tabelle autocue_log). Ohne das lernte das
    Modell nach dem ersten Rekordbox-Update aus seinen eigenen Ergebnissen. Hat der
    Nutzer den Cue verschoben, zaehlt er wieder als Hand-Cue.
"""
from __future__ import annotations

from app import gridstore

LEGACY_PREFIXES = ("A:", "v3:", "v26:", "AutoCue")


def _is_legacy(name: str) -> bool:
    return bool(name) and name.startswith(LEGACY_PREFIXES)


def own_cues(edits_cues, snap_cues, lead: float | None) -> tuple[list, str | None]:
    """(Cues auf eigener Achse, Quelle). Snapshot-Cues brauchen den Versatz."""
    if edits_cues is not None:
        return list(edits_cues), "edited"
    if snap_cues:
        if lead is None:
            return [], None
        return gridstore.cues_to_own(snap_cues, lead), "rekordbox"
    return [], None


def hand_cues(cues: list, auto: list | None, beat_s: float) -> dict:
    """{"hot": {Slot: Sekunde}, "memory": [Sekunden]} ohne Auto-Cue-Eigenes."""
    auto = auto or []
    auto_hot = {int(c["slot"]): float(c["position_s"]) for c in auto if c.get("kind") == "hot" and c.get("slot")}
    auto_mem = [float(c["position_s"]) for c in auto if c.get("kind") != "hot"]
    tol = max(0.05, beat_s)
    hot: dict[int, float] = {}
    memory: list[float] = []
    for c in cues:
        t = c.get("position_s")
        if t is None or t < 0 or _is_legacy(str(c.get("name") or "")):
            continue
        t = float(t)
        if c.get("kind") == "hot" and c.get("slot"):
            slot = int(c["slot"])
            if slot in auto_hot and abs(auto_hot[slot] - t) <= tol:
                continue
            hot.setdefault(slot, t)
        elif c.get("kind") != "hot":
            if any(abs(a - t) <= tol for a in auto_mem):
                continue
            memory.append(t)
    memory.sort()
    return {"hot": hot, "memory": memory}


def has_anchor_cues(cues: list) -> bool:
    """Grobe Vorauswahl ohne Versatz: hat der Track ueberhaupt Hot A und Hot C?"""
    slots = {int(c["slot"]) for c in cues or [] if c.get("kind") == "hot" and c.get("slot")
             and not _is_legacy(str(c.get("name") or ""))}
    return 1 in slots and 3 in slots


def beat_seconds(beats: list) -> float:
    """Schlagdauer aus [[ms, Nummer], ...] (Median), Rueckfall 0,5 s."""
    gaps = sorted(b[0] - a[0] for a, b in zip(beats, beats[1:]) if b[0] > a[0])
    return gaps[len(gaps) // 2] / 1000.0 if gaps else 0.5
