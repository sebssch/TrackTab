"""
Slot-Belegung: welche erkannte Rolle bzw. welche eigene Regel auf welchem Cue landet.

TrackTab speichert die Belegung als Tabelle, wie sie die Einstellungsseite
"Auto Cue" zeigt (config["autocue_slots"]): 8 Hot Cues (A..H) und 10 Memory-
Zeilen, je Zeile

    {"mode": "off" | "role" | "rule",
     "role": "drop2",                         # mode "role"
     "anchor": "drop2", "offset": 8 | "auto", "direction": "before" | "after",
     "color": [r, g, b], "name": "A: Drop 2"}

Die Kette (placement.py) kennt dagegen wie im Autocue-Projekt zwei Teile:
Rollen mit Ziel (structure_cues) und Slot-Regeln (custom_cues). plan() setzt
das eine in das andere um. Die Reihenfolge der Memory-Zeilen ist reine
Anzeige -- nur die Zeilennummer einer Regel wird zur Slot-ID ("memory_3"),
und bei Auto-Regeln auf dieselbe Zone gewinnt die erste.

Farben nur aus rekordbox.cue_palettes(): andere RGB-Werte wuerden beim
Schreiben nach Rekordbox ohnehin auf die naechste Palettenfarbe gerundet.
"""
from __future__ import annotations

HOT_LETTERS = "ABCDEFGH"
N_MEMORY = 10

# Rollen, die eine Zeile direkt belegen kann. Die Auswahl entspricht der
# Einstellungsseite des Autocue-Projekts (settings.html) plus "setup", dessen
# Konfidenz eigens kalibriert ist (min_confidence.setup).
ROLES = ("intro_1", "break1", "drop1", "break2", "setup", "drop2", "outro_start")
# Anker, auf die sich eine Regel beziehen darf (config.CUSTOM_ANCHOR_ROLES des Originals).
ANCHORS = ("intro_1", "first_beat", "beat_in", "break1", "build1", "pre_drop1",
           "drop1", "break2", "build2", "setup", "drop2", "outro_start")
OFFSETS = (0, 4, 8, 16, 32, 64, 128)
DIRECTIONS = ("before", "after")
# Konfidenz-Schwellen je Rolle (config["autocue_conf_<key>"]); alles andere -> default.
CONFIDENCE_KEYS = ("default", "intro_1", "break1", "drop1", "break2", "setup", "drop2", "outro_start")

_HOT_BLUE = [58, 89, 246]          # rekordbox._HOT_CUE_TABLE_COLORS[1]
_MEMORY_BLUE = [0, 102, 255]       # rekordbox._MEMORY_CUE_PALETTE[6]


def _role(role: str, name: str, color: list) -> dict:
    return {"mode": "role", "role": role, "color": list(color), "name": name}


def _rule(anchor: str, offset, direction: str, name: str, color: list) -> dict:
    return {"mode": "rule", "anchor": anchor, "offset": offset, "direction": direction,
            "color": list(color), "name": name}


def off() -> dict:
    return {"mode": "off"}


def defaults() -> dict:
    """Die Belegung aus der config.yaml des Autocue-Projekts (Stand Okt. 2026)."""
    hot = [off() for _ in HOT_LETTERS]
    hot[0] = _role("break1", "A: Break 1", _HOT_BLUE)
    hot[1] = _rule("drop2", 8, "before", "A: Build 8 Takte vor C", _HOT_BLUE)
    hot[2] = _role("drop2", "A: Drop 2", _HOT_BLUE)
    memory = [
        _role("intro_1", "Intro", _MEMORY_BLUE),
        _role("drop1", "Drop 1", _MEMORY_BLUE),
        _rule("intro_1", "auto", "after", "After Intro", _MEMORY_BLUE),
        _rule("drop1", "auto", "after", "After Drop#1", _MEMORY_BLUE),
        _role("break2", "Break #2", _MEMORY_BLUE),
        _rule("drop2", "auto", "after", "After Drop#2", _MEMORY_BLUE),
        _rule("outro_start", "auto", "after", "After Outro", _MEMORY_BLUE),
        _role("outro_start", "Outro", _MEMORY_BLUE),
        _rule("break2", "auto", "before", "Before Break #2", _MEMORY_BLUE),
        off(),
    ]
    return {"hot": hot, "memory": memory}


def _clean_color(value, palette: list) -> list:
    rgb = [int(v) for v in value][:3]
    if len(rgb) != 3 or rgb not in palette:
        raise ValueError(f"Farbe {value!r} ist keine Rekordbox-Farbe")
    return rgb


def _clean_slot(raw, hot: bool, palette: list, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: ungültiger Eintrag")
    mode = str(raw.get("mode") or "off")
    if mode == "off":
        return off()
    name = str(raw.get("name") or "").strip()[:80]
    color = _clean_color(raw.get("color") or [], palette)
    if mode == "role":
        role = str(raw.get("role") or "")
        if role not in ROLES:
            raise ValueError(f"{where}: unbekannte Rolle {role!r}")
        return _role(role, name, color)
    if mode == "rule":
        anchor = str(raw.get("anchor") or "")
        if anchor not in ANCHORS:
            raise ValueError(f"{where}: unbekannter Anker {anchor!r}")
        direction = str(raw.get("direction") or "")
        if direction not in DIRECTIONS:
            raise ValueError(f"{where}: Richtung muss vor oder nach sein")
        offset = raw.get("offset")
        if offset == "auto":
            if hot:
                raise ValueError(f"{where}: „automatisch“ gibt es nur für Memory Cues")
        else:
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                raise ValueError(f"{where}: ungültiger Abstand") from None
            if offset not in OFFSETS:
                raise ValueError(f"{where}: Abstand muss einer von {', '.join(map(str, OFFSETS))} Takten sein")
        return _rule(anchor, offset, direction, name, color)
    raise ValueError(f"{where}: unbekannte Art {mode!r}")


def validate(raw, palettes: dict) -> dict:
    """Pruefen und normieren (settings.validate_autocue_slots()). ValueError mit
    anzeigbarem Text. Eine Rolle darf nur EINE Zeile belegen -- im Original ist
    sie ein einzelner Eintrag in structure_cues mit genau einem Ziel."""
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Slot-Belegung")
    hot_raw, mem_raw = raw.get("hot"), raw.get("memory")
    if not isinstance(hot_raw, list) or len(hot_raw) != len(HOT_LETTERS):
        raise ValueError("Es braucht genau 8 Hot-Cue-Zeilen")
    if not isinstance(mem_raw, list) or len(mem_raw) != N_MEMORY:
        raise ValueError(f"Es braucht genau {N_MEMORY} Memory-Zeilen")
    hot = [_clean_slot(s, True, palettes["hot"], f"Hot Cue {HOT_LETTERS[i]}") for i, s in enumerate(hot_raw)]
    memory = [_clean_slot(s, False, palettes["memory"], f"Memory {i + 1}") for i, s in enumerate(mem_raw)]
    seen: dict[str, str] = {}
    for where, slot in [(f"Hot Cue {HOT_LETTERS[i]}", s) for i, s in enumerate(hot)] + \
                       [(f"Memory {i + 1}", s) for i, s in enumerate(memory)]:
        if slot["mode"] != "role":
            continue
        if slot["role"] in seen:
            raise ValueError(f"Die Rolle ist doppelt belegt: {seen[slot['role']]} und {where}")
        seen[slot["role"]] = where
    return {"hot": hot, "memory": memory}


class SlotPlan:
    """Die Belegung in der Form, die placement.build_cues() braucht."""

    def __init__(self, slots: dict | None):
        slots = slots or {"hot": [off()] * len(HOT_LETTERS), "memory": [off()] * N_MEMORY}
        self.structure: dict[str, tuple[int, str, list]] = {}
        self.custom: list[tuple[str, dict]] = []
        for i, slot in enumerate(slots.get("hot") or []):
            self._add(slot, i + 1, f"hot_{HOT_LETTERS[i].lower()}")
        for i, slot in enumerate(slots.get("memory") or []):
            self._add(slot, 0, f"memory_{i + 1}")

    def _add(self, slot: dict, kind: int, slot_id: str) -> None:
        mode = (slot or {}).get("mode")
        name = (slot.get("name") or "") if slot else ""
        if mode == "role" and slot["role"] not in self.structure:
            self.structure[slot["role"]] = (kind, name or slot["role"], slot.get("color"))
        elif mode == "rule":
            self.custom.append((slot_id, {"anchor": slot["anchor"], "offset_bars": slot["offset"],
                                          "direction": slot["direction"], "label": name or slot_id,
                                          "color": slot.get("color"), "kind": kind}))

    def resolve(self, role: str):
        """Rolle -> (kind, label, color); kind None = nicht gesetzt."""
        hit = self.structure.get(role)
        if hit is None:
            return None, role, None
        return hit

    def custom_color(self, slot_id: str):
        for sid, rule in self.custom:
            if sid == slot_id:
                return rule["color"]
        return None
