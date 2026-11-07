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

# Welcher Hot-Cue-Slot (1 = A .. 8 = H) der Hand-Cues welche Rolle bedeutet. Die
# Vorgabe ist die Konvention, mit der Auto Cue entstanden ist. Pflicht sind Drop 2
# und Break 1 als Hot Cue (REQUIRED_ROLES): daraus entstehen die Grundmodelle, und
# Drop 2 ist der Bezug der ganzen Kette. Ein mitgeliefertes Modell, das sie
# ersetzen koennte, gibt es nicht mehr. Die uebrigen Rollen duerfen fehlen -- ohne
# eigenes Modell sucht Auto Cue sie ueber die Strukturanalyse bzw. die Outro-Regel.
# Intro/erster Beat sind keine Lernrollen -- die sind immer die erste 1 im
# Beatgrid (anchors.py) und gehen ueber die Slot-Belegung.
DEFAULT_LABEL_SLOTS = {"break1": 1, "drop2": 3}
LABEL_ROLES = ("drop2", "break1", "drop1", "break2", "outro_start")
REQUIRED_ROLES = ("drop2", "break1")
_LETTERS = "ABCDEFGH"


def clean_label_slots(raw) -> dict[str, int]:
    """{Rolle: Slot 1..8} aus {Rolle: "A".."H" | 1..8 | ""/None}. ValueError mit anzeigbarem Text.
    Drop 2 und Break 1 sind Pflicht (REQUIRED_ROLES)."""
    if raw is None:
        return dict(DEFAULT_LABEL_SLOTS)
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Cue-Zuordnung.")
    out: dict[str, int] = {}
    for role, value in raw.items():
        if role not in LABEL_ROLES:
            raise ValueError(f"Unbekannte Rolle in der Cue-Zuordnung: {role}")
        if value in (None, "", "-", 0):
            continue
        if isinstance(value, str) and value.strip().upper() in _LETTERS and len(value.strip()) == 1:
            slot = _LETTERS.index(value.strip().upper()) + 1
        else:
            try:
                slot = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"Ungültiger Hot Cue für {role}: {value!r}") from None
        if not 1 <= slot <= 8:
            raise ValueError(f"Ungültiger Hot Cue für {role}: {value!r}")
        out[role] = slot
    missing = [r for r in REQUIRED_ROLES if r not in out]
    if missing:
        raise ValueError("Drop 2 und Break 1 brauchen je einen Hot Cue.")
    if len(set(out.values())) != len(out):
        raise ValueError("Ein Hot Cue ist mehreren Rollen zugeordnet.")
    return out


# Rollen der Kette, die aus Memory Cues gelernt werden koennen (position_labels()).
MEMORY_LEARN_ROLES = ("drop1", "break2", "outro_start")


def learn_plan(slots: dict | None, override=None) -> dict:
    """Was ein Training aus welchen Hand-Cues lernt -- einheitlich aus der Slot-Belegung
    (config["autocue_slots"]), wie Auto Cue die Cues auch setzt:

      Hot Cue mit Rolle    Label direkt aus diesem Slot (lernbare Rollen: LABEL_ROLES)
      Memory-Zeile mit Rolle  Label aus den Memory Cues (nur MEMORY_LEARN_ROLES; Memory
                           Cues haben keinen Slot, welcher es ist, sagt position_labels())
      Regeln, Intro, Setup nicht gelernt (gerechnet bzw. erste 1 im Beatgrid)

    override: explizite Zuordnung {Rolle: Slot} (autocue-train.yaml label_slots) statt
    der Belegung; dann lernen die uebrigen Kettenrollen wie frueher aus Memory Cues.
    Rueckgabe {"hot": {Rolle: Slot}, "memory": [Rollen], "error": Text oder ""}.
    """
    if override is not None:
        hot = clean_label_slots(override)
        return {"hot": hot, "memory": [r for r in MEMORY_LEARN_ROLES if r not in hot], "error": ""}
    from app.autocue.slots import defaults
    slots = slots or defaults()
    hot: dict[str, int] = {}
    memory: list[str] = []
    for i, s in enumerate(slots.get("hot") or []):
        if (s or {}).get("mode") == "role" and s.get("role") in LABEL_ROLES:
            hot.setdefault(s["role"], i + 1)
    for s in slots.get("memory") or []:
        if (s or {}).get("mode") == "role" and s.get("role") in MEMORY_LEARN_ROLES and s["role"] not in hot:
            memory.append(s["role"])
    error = ""
    if any(r not in hot for r in REQUIRED_ROLES):
        error = ("Zum Lernen müssen Drop 2 und Break 1 in der Slot-Belegung als Rolle auf "
                 "einem Hot Cue liegen – daraus entstehen die Grundmodelle, alles Weitere "
                 "sucht Auto Cue relativ zu Drop 2.")
    return {"hot": hot, "memory": memory, "error": error}


def count_with_memory(items: list[dict]) -> int:
    """Wie viele Kandidaten (dataset.candidates()) ueberhaupt Memory Cues von Hand haben."""
    n = 0
    for it in items:
        cues = it.get("edit_cues") if it.get("edit_cues") is not None else it.get("snap_cues")
        if any(c.get("kind") != "hot" and not _is_legacy(str(c.get("name") or "")) for c in cues or []):
            n += 1
    return n


def is_default(label_slots: dict | None) -> bool:
    return not label_slots or dict(label_slots) == DEFAULT_LABEL_SLOTS


def letters(label_slots: dict) -> dict[str, str]:
    """{Rolle: "A".."H"} fuer Manifest und Anzeige."""
    return {role: _LETTERS[slot - 1] for role, slot in label_slots.items()}


def required_slots(label_slots: dict | None) -> tuple[int, ...]:
    """Hot Cues, die ein Track haben MUSS: Drop 2 und Break 1 (der Bezug fuer alles
    Weitere). learn_plan() laesst eine Belegung ohne beide gar nicht erst zu."""
    ls = label_slots if label_slots is not None else DEFAULT_LABEL_SLOTS
    return tuple(ls[r] for r in REQUIRED_ROLES if r in ls)


def usable(hot_slots, has_memory: bool, label_slots: dict | None, memory_ok: bool) -> bool:
    """Taugt ein Track zum Lernen? Alle Pflicht-Hot-Cues (required_slots()) und
    mindestens ein gelernter Hot Cue oder -- wenn die Belegung Rollen aus Memory
    Cues lernt -- ein Memory Cue."""
    ls = label_slots if label_slots is not None else DEFAULT_LABEL_SLOTS
    hot_slots = set(hot_slots)
    if not all(s in hot_slots for s in required_slots(ls)):
        return False
    return any(s in hot_slots for s in ls.values()) or (memory_ok and has_memory)


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


def has_anchor_cues(cues: list, label_slots: dict | None = None, memory_ok: bool = False) -> bool:
    """Grobe Vorauswahl ohne Versatz: hat der Track Hand-Cues, aus denen sich lernen
    laesst (usable())?"""
    own = [c for c in cues or [] if not _is_legacy(str(c.get("name") or ""))]
    slots = {int(c["slot"]) for c in own if c.get("kind") == "hot" and c.get("slot")}
    return usable(slots, any(c.get("kind") != "hot" for c in own), label_slots, memory_ok)


def beat_seconds(beats: list) -> float:
    """Schlagdauer aus [[ms, Nummer], ...] (Median), Rueckfall 0,5 s."""
    gaps = sorted(b[0] - a[0] for a, b in zip(beats, beats[1:]) if b[0] > a[0])
    return gaps[len(gaps) // 2] / 1000.0 if gaps else 0.5
