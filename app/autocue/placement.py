"""
Schritt 8 der Analysekette: aus Ankern fertige Cue-Punkte machen.

Hier werden die Regeln 4 bis 8 umgesetzt:

    4  Alle Cues liegen auf dem Raster
    5  Gleichmaessige Abstaende, abschnittsweise gezaehlt
    6  Memory Cues auf Drop 1 und Break 2
    7  Die uebrigen Memory Cues verteilen und, wenn noetig, sparen
    8  Kurzer Track: der einzige Drop ist der letzte, Hot Cues haben Vorrang

Die Zuordnung Rolle -> Cue-Slot passiert ganz am Ende und kommt aus der
Konfiguration (slots.py, Einstellungsseite "Auto Cue"). Bis dahin kennt das
Verfahren nur Rollen, keine Buchstaben -- dadurch laesst sich die Belegung frei
aendern, ohne dass die Erkennung etwas davon merkt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.autocue.anchors import AnchorSet
from app.autocue.slots import SlotPlan
from app.autocue.types import BARS_PER_PHRASE, BEATS_PER_BAR, BarFeatures, CuePoint, Lattice

# Die Zonen, in denen Fuellpunkte verteilt werden (Regel 7.1 bis 7.3).
# intro_1 ist die Position "Intro" (erste 1 im Beatgrid) und damit ein
# Kernanker -- als Fuellpunkt im Intro bleiben intro_2 bis intro_4.
ZONE_ROLES = {
    "intro":    ["intro_2", "intro_3", "intro_4"],
    "strophe1": ["strophe_1", "strophe_2", "strophe_3", "strophe_4"],
    "strophe2": ["strophe_1", "strophe_2", "strophe_3", "strophe_4"],
    "outro":    ["outro_1", "outro_2", "outro_3"],
}

# Kernrollen aus Regel 6 -- sie werden nie gestrichen.
CORE_MEMORY_ROLES = ("drop1", "break2")


@dataclass
class ZonePlan:
    """Wie eine Zone gerastert wurde -- samt Begruendung fuer den Bericht."""
    name: str
    start_bar: int
    end_bar: int
    step_bars: int
    direction: str              # "vorwaerts" | "rueckwaerts"
    slots: list[int] = field(default_factory=list)
    hit_subboundaries: int = 0
    remainder_bars: int = 0
    reason: str = ""

    @property
    def n_bars(self) -> int:
        return max(0, self.end_bar - self.start_bar)


@dataclass
class PlacementResult:
    cues: list = field(default_factory=list)          # list[CuePoint]
    zone_plans: list[ZonePlan] = field(default_factory=list)
    escalation_step: int = 0
    escalation_log: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (Rolle, Grund)
    notes: list[str] = field(default_factory=list)


# --- Rasterwahl je Zone ---

def _sub_boundaries(features: BarFeatures, start: int, end: int) -> list[int]:
    """
    Musikalische Zwischengrenzen in einer Zone.

    Das sind die Stellen, an denen eine Klangschicht dazukommt oder wegfaellt:
    Drums setzen ein, der Sub-Bass kommt, eine Acapella beginnt, Gesang oder
    Melodie kommen dazu. Sie entscheiden, wo das Raster verankert wird.
    """
    bounds: set[int] = set()
    for gate in ("low_on", "melodic_on", "drums_on", "high_on"):
        if not features.has(gate):
            continue
        values = features[gate]
        changes = np.flatnonzero(np.abs(np.diff(values)) > 0.5) + 1
        bounds.update(int(b) for b in changes if start < b < end)

    # Starke Einschlaege zaehlen ebenfalls als Zwischengrenze.
    if features.has("impact_score"):
        impact = features["impact_score"][start:end]
        if impact.size:
            threshold = float(np.percentile(impact, 90))
            strong = np.flatnonzero(impact >= max(threshold, 0.5)) + start
            bounds.update(int(b) for b in strong)

    return sorted(bounds)


def plan_zone(name: str, start_bar: int, end_bar: int,
              features: BarFeatures, step_bars: int = BARS_PER_PHRASE,
              max_slots: int = 4) -> ZonePlan:
    """
    Das Raster einer Zone festlegen -- an der Musik ausgerichtet.

    Nicht blind vorwaerts oder rueckwaerts gezaehlt. Stattdessen werden alle
    sinnvollen Raster durchprobiert (Richtung x Phasenlage) und danach bewertet,
    wie viele musikalische Zwischengrenzen sie treffen und wie gross der ungerade
    Rest bleibt.

    Damit fallen beide in Regel 7 genannten Faelle richtig aus: Beginnt ein Intro
    mit vier Takten Drums oder einer Acapella, ist das eine Zwischengrenze -- das
    Raster verankert dort, und der Versatz wandert vom Trackanfang weg. Kommt
    Hot Cue A acht Takte nach einem Sub-Bass-Einsatz, ist der Sub-Bass die
    Zwischengrenze, das Raster muss sie treffen.
    """
    plan = ZonePlan(name=name, start_bar=start_bar, end_bar=end_bar,
                    step_bars=step_bars, direction="vorwaerts")
    n_bars = end_bar - start_bar
    if n_bars < step_bars:
        plan.reason = "Zone kuerzer als ein Rasterschritt"
        return plan

    subs = set(_sub_boundaries(features, start_bar, end_bar))
    best = None

    for direction in ("vorwaerts", "rueckwaerts"):
        if direction == "vorwaerts":
            slots = list(range(start_bar, end_bar, step_bars))
            remainder = (end_bar - start_bar) % step_bars
        else:
            slots = list(range(end_bar - step_bars, start_bar - 1, -step_bars))[::-1]
            remainder = (end_bar - start_bar) % step_bars
        slots = [b for b in slots if start_bar <= b < end_bar]
        if not slots:
            continue

        hits = sum(1 for b in slots if any(abs(b - s) <= 1 for s in subs))
        # Ein Raster, das musikalische Grenzen trifft, ist besser als eines, das
        # nur rechnerisch aufgeht. Der Rest zaehlt nur als Nebenbedingung.
        score = hits * 2.0 - remainder * 0.1
        if best is None or score > best[0]:
            best = (score, direction, slots, remainder, hits)

    if best is None:
        plan.reason = "kein gueltiges Raster in dieser Zone"
        return plan

    _score, direction, slots, remainder, hits = best
    plan.direction = direction
    plan.slots = slots[:max_slots] if len(slots) > max_slots else slots
    plan.hit_subboundaries = hits
    plan.remainder_bars = remainder
    plan.reason = (
        f"{direction}, Schritt {step_bars*BEATS_PER_BAR} Beats, "
        f"{hits} musikalische Grenzen getroffen, Rest {remainder*BEATS_PER_BAR} Beats"
    )
    return plan


# --- Die Eskalationsleiter (Regel 7.4 bis 7.7) ---

ESCALATION = [
    "alle Zonen im 32er-Schritt",
    "letzten Outro-Punkt gestrichen",
    "Punkt auf Beat 1 gestrichen (Intro ab 128 Beats)",
    "laengste Zone auf 64er-Schritt geweitet",
    "alle Nicht-Kern-Zonen auf 64er-Schritt",
    "Outro-Punkte ganz gestrichen",
]


def _track_end(lattice: Lattice, music_end: int | None) -> tuple[int, bool]:
    """(Ende fuer die Zonen, ob es das erkannte Musikende ist). Ohne erkanntes
    Musikende bleibt am Trackende eine Phrase Abstand (Regel 7.4)."""
    if music_end is not None and 0 < music_end < lattice.n_bars:
        return int(music_end), True
    return lattice.n_bars, False


def _zone_bounds(anchors: AnchorSet, lattice: Lattice,
                 music_end: int | None = None) -> dict[str, tuple[int, int]]:
    """Die vier Zonen aus den Ankern ableiten."""
    def bar_of(name: str) -> int | None:
        a = anchors.get(name)
        return a.bar if a else None

    first = bar_of("first_beat") or 0
    break1 = bar_of("break1")
    drop1 = bar_of("drop1")
    break2 = bar_of("break2")
    drop2 = bar_of("drop2")
    outro = bar_of("outro_start")
    end, known = _track_end(lattice, music_end)

    zones: dict[str, tuple[int, int]] = {}
    if break1 is not None and break1 > first:
        zones["intro"] = (first, break1)
    if drop1 is not None:
        stop = break2 if break2 is not None else drop2
        if stop is not None and stop > drop1:
            zones["strophe1"] = (drop1, stop)
    if drop2 is not None:
        stop = outro if outro is not None else end
        if stop > drop2:
            zones["strophe2"] = (drop2, stop)
    if outro is not None and end > outro:
        # Am Trackende braucht es keinen Punkt mehr (Regel 7.4): bis zum erkannten
        # Musikende (tail.py), sonst eine Phrase Sicherheitsabstand.
        zones["outro"] = (outro, end if known else max(outro + 1, end - BARS_PER_PHRASE))
    return zones


def _plan_all_zones(zones: dict[str, tuple[int, int]], features: BarFeatures,
                    step_overrides: dict[str, int],
                    max_slots: dict[str, int]) -> list[ZonePlan]:
    plans = []
    for name, (a, b) in zones.items():
        plans.append(plan_zone(name, a, b, features,
                               step_bars=step_overrides.get(name, BARS_PER_PHRASE),
                               max_slots=max_slots.get(name, 4)))
    return plans


def build_cues(anchors: AnchorSet, features: BarFeatures, lattice: Lattice,
               max_memory_cues: int = 10,
               min_confidence: float | dict[str, float] = 0.4,
               slots: SlotPlan | None = None, music_end: int | None = None) -> PlacementResult:
    """
    Die fertige Cue-Liste erzeugen.

    Args:
        slots: Belegung (slots.SlotPlan). Ohne sie entstehen keine Cues.
        music_end: Takt, ab dem nur noch der Ausklang laeuft (tail.music_end());
            ab dort entsteht kein Cue, auch kein Hot Cue.
    """
    if slots is None:
        slots = SlotPlan(None)
    role_resolver = slots.resolve

    result = PlacementResult()

    def threshold_for(role: str) -> float:
        if isinstance(min_confidence, dict):
            return float(min_confidence.get(role, min_confidence.get("default", 0.4)))
        return float(min_confidence)

    # --- Kernrollen aus den Ankern ---
    core_order = ["first_beat", "intro_1", "beat_in", "break1", "build1",
                  "pre_drop1", "drop1", "break2", "build2", "setup", "drop2", "outro_start"]

    placed_memory: list[tuple[int, str, float]] = []   # (Takt, Rolle, Prioritaet)
    cues: list[CuePoint] = []

    for role in core_order:
        anchor = anchors.get(role)
        if anchor is None:
            continue
        kind, label, color = role_resolver(role)
        if kind is None:
            continue

        # Regel: lieber keine Cue als eine falsche. Die Schwelle gilt je Rolle,
        # damit Hot C strenger oder lockerer sein kann als Hot A.
        limit = threshold_for(role)
        if anchor.confidence < limit:
            result.skipped.append(
                (role, f"Konfidenz {anchor.confidence:.2f} unter Schwelle {limit:.2f} "
                       f"({anchor.reason})")
            )
            continue

        cues.append(CuePoint(
            time_sec=anchor.time_sec, kind=kind, name=label,
            comment=label, color=color,
            priority=1 if role in ("first_beat", "intro_1") else 2,
            role=role,
        ))
        if kind == 0:
            placed_memory.append((anchor.bar, role, 2))

    # --- Eigene Regeln: "N Takte vor/nach Anker X" je Hot-Cue-/Memory-Slot ---
    # Rein additiv und nachgelagert: anchors ist zu diesem Zeitpunkt bereits
    # das fertige AnchorSet (alle Kernanker erkannt), eine eigene Regel liest
    # daraus nur -- die Erkennungsreihenfolge bleibt unberuehrt.
    auto_rules: list[tuple[str, dict]] = []
    for slot_id, rule in slots.custom:
        if rule["offset_bars"] == "auto":
            # Erst sammeln: Auto-Slots teilen sich die Plaetze, die nach
            # allen festen Cues noch frei sind.
            auto_rules.append((slot_id, rule))
            continue

        ref_anchor = anchors.get(rule["anchor"])
        if ref_anchor is None:
            result.skipped.append(
                (slot_id, f"Anker '{rule['anchor']}' auf diesem Track nicht erkannt")
            )
            continue

        limit = threshold_for(slot_id)
        if ref_anchor.confidence < limit:
            result.skipped.append(
                (slot_id, f"Konfidenz {ref_anchor.confidence:.2f} des Ankers "
                          f"'{rule['anchor']}' unter Schwelle {limit:.2f}")
            )
            continue

        offset = rule["offset_bars"]
        target_bar = ref_anchor.bar - offset if rule["direction"] == "before" else ref_anchor.bar + offset
        if not (0 <= target_bar < lattice.n_bars):
            result.skipped.append(
                (slot_id, f"{offset} Takte {rule['direction']} '{rule['anchor']}' "
                          f"liegt ausserhalb des Tracks")
            )
            continue

        kind = rule["kind"]
        cues.append(CuePoint(
            time_sec=lattice.time_of_bar(target_bar), kind=kind,
            name=rule["label"], comment=rule["label"], color=rule["color"],
            priority=2, role=slot_id,
        ))
        if kind == 0:
            placed_memory.append((target_bar, slot_id, 2))

    # --- Auto-Slots: freie Memory-Plaetze auf die Zonen zwischen den Ankern ---
    if auto_rules and AUTO_ENABLED:
        auto_cues = _place_auto_cues(auto_rules, anchors, lattice, placed_memory,
                                     max_memory_cues, threshold_for, result, music_end)
        for bar, slot_id, label in auto_cues:
            cues.append(CuePoint(
                time_sec=lattice.time_of_bar(bar), kind=0, name=label,
                comment=label, color=slots.custom_color(slot_id), priority=3, role=slot_id,
            ))
            placed_memory.append((bar, slot_id, 3))

    # --- Fuellpunkte mit Eskalationsleiter ---
    zones = _zone_bounds(anchors, lattice, music_end)
    if zones:
        budget = max_memory_cues - len(placed_memory)
        fillers, step_used, log = _fill_zones(
            zones, features, budget, role_resolver, threshold_for
        )
        result.escalation_step = step_used
        result.escalation_log.extend(log)
        for bar, role in fillers:
            kind, label, color = role_resolver(role)
            if kind is None:
                continue
            cues.append(CuePoint(
                time_sec=lattice.time_of_bar(bar), kind=kind, name=label,
                comment=label, color=color, priority=4, role=role,
            ))
    else:
        result.notes.append("keine Zonen ableitbar -- es entstehen nur Kern-Cues")

    end, known = _track_end(lattice, music_end)
    if known:
        limit_t = lattice.time_of_bar(end) - 0.05
        for cue in [c for c in cues if c.time_sec >= limit_t]:
            result.skipped.append((cue.role, "liegt im Ausklang am Trackende"))
        cues = [c for c in cues if c.time_sec < limit_t]
    result.cues = _finalise(cues, lattice, max_memory_cues, result)
    return result


def _fill_zones(zones, features, budget: int, role_resolver, threshold_for):
    """
    Fuellpunkte verteilen und, wenn das Budget nicht reicht, Stufe fuer Stufe
    sparen (Regel 7.4 bis 7.7).

    Die Leiter ist bewusst starr und nicht "intelligent". Dadurch ist sie
    vorhersagbar, und im Bericht steht bei jedem Track, welche Stufe gegriffen
    hat -- man sieht also sofort, warum ein Track weniger Memory Cues hat als
    erwartet.
    """
    log: list[str] = []

    for step in range(len(ESCALATION)):
        step_overrides: dict[str, int] = {}
        # Grosszuegig angesetzt: Was tatsaechlich gesetzt wird, begrenzt das
        # Memory-Budget ueber die Eskalationsleiter -- nicht eine feste Obergrenze
        # je Zone. Sonst entstehen Luecken mitten im Track, obwohl Plaetze frei sind.
        max_slots = {"intro": 8, "strophe1": 8, "strophe2": 8, "outro": 4}
        drop_first_intro = False
        active = dict(zones)

        if step >= 1:
            max_slots["outro"] = 2   # Regel 7.4: im Outro zuerst sparen
        if step >= 2:
            drop_first_intro = True
        if step >= 3:
            longest = max(active, key=lambda z: active[z][1] - active[z][0], default=None)
            if longest:
                step_overrides[longest] = 2 * BARS_PER_PHRASE
        if step >= 4:
            for name in active:
                if name != "strophe1":
                    step_overrides[name] = 2 * BARS_PER_PHRASE
        if step >= 5:
            active.pop("outro", None)

        plans = _plan_all_zones(active, features, step_overrides, max_slots)
        fillers: list[tuple[int, str]] = []
        for plan in plans:
            # Nur Rollen verwenden, die in der Konfiguration auch ein Ziel haben.
            # Steht eine auf "off" oder fehlt sie ganz, wuerde der zugehoerige
            # Rasterpunkt sonst still verfallen -- und im Track entstuende eine
            # Luecke, obwohl Budget frei ist. Genau das ist passiert: 82 Sekunden
            # ohne Memory Cue bei sieben von neun genutzten Plaetzen.
            roles = [r for r in ZONE_ROLES.get(plan.name, [])
                     if role_resolver(r)[0] is not None]
            if not roles:
                continue

            slots = plan.slots
            # Regel 7.6: Bei einem Intro ab 128 Beats ist der Punkt auf Beat 1
            # entbehrlich, weil 32, 64 und 96 ohnehin gesetzt werden.
            if (drop_first_intro and plan.name == "intro"
                    and plan.n_bars >= 4 * BARS_PER_PHRASE and slots):
                slots = slots[1:]

            for i, bar in enumerate(slots):
                # Gibt es mehr Rasterpunkte als benannte Rollen, wird die letzte
                # wiederholt. Alle Punkte einer Zone tragen ohnehin dieselbe
                # Beschriftung und Farbe -- die Nummer ist nur ein Name.
                fillers.append((bar, roles[min(i, len(roles) - 1)]))

        usable = [f for f in fillers if role_resolver(f[1])[0] == 0]
        if len(usable) <= budget:
            log.append(f"Stufe {step}: {ESCALATION[step]} -- {len(usable)} Punkte, "
                       f"Budget {budget}")
            for plan in plans:
                log.append(f"   Zone {plan.name}: {plan.reason}")
            return fillers, step, log

        log.append(f"Stufe {step}: {ESCALATION[step]} -- {len(usable)} Punkte, "
                   f"Budget {budget} reicht nicht")

    return [], len(ESCALATION) - 1, log


# --- Auto-Slots (custom_cues.<memory_n>.offset_bars: "auto") ---
#
# Ein Auto-Slot steht fuer die Zone zwischen seinem Anker und dem naechsten Anker
# in Richtung direction. In jeder Zone werden ab dem Zonenstart die Beats gezaehlt
# und ein 32er-Raster gelegt; die freien Memory-Plaetze werden nach Gewicht auf die
# Zonen verteilt, und wo sie nicht fuer jeden Rasterpunkt reichen, weitet sich das
# Raster auf 64 bzw. 128 Beats.
#
# Kalibriert am Gold-Set (tools/eval/memory_auto.py --analyse, 150 Tracks):
# Hand-Cues liegen zu 94-100 % im 32er-Raster vorwaerts ab dem Zonenstart. Der
# Anteil belegter 32er-Rasterpunkte ist das Gewicht -- Intro 75 %, Break 1 bis
# Drop 1 62 %, Drop-Zonen 56 %, Break 2 bis Drop 2 39 %, Outro 27 %.

AUTO_ENABLED = True     # nur fuer Vergleichslaeufe (tools/eval/memory_auto.py)

AUTO_ZONE_WEIGHTS = {
    "intro": 0.75, "build1": 0.62, "strophe1": 0.56,
    "build2": 0.39, "strophe2": 0.56, "outro": 0.27,
}

# (Anker, Richtung) -> Zone
AUTO_ZONE_OF = {
    ("first_beat", "after"): "intro", ("intro_1", "after"): "intro",
    ("break1", "before"): "intro",
    ("break1", "after"): "build1", ("drop1", "before"): "build1",
    ("drop1", "after"): "strophe1", ("break2", "before"): "strophe1",
    ("break2", "after"): "build2", ("drop2", "before"): "build2",
    ("drop2", "after"): "strophe2", ("outro_start", "before"): "strophe2",
    ("outro_start", "after"): "outro",
}

AUTO_STEPS_BARS = (BARS_PER_PHRASE, 2 * BARS_PER_PHRASE, 4 * BARS_PER_PHRASE)  # 32/64/128 Beats

# Im Outro setzt der DJ bei wenigen Plaetzen die ersten 32er-Punkte nach dem
# Outro-Start (Gold-Set: 70 x erster, 47 x Mitte, nie der letzte) -- dort wird
# nicht geweitet, sondern von vorn aufgefuellt.
AUTO_FRONT_ZONES = ("outro",)


def _auto_zone_bounds(anchors: AnchorSet, lattice: Lattice,
                      music_end: int | None = None) -> dict[str, tuple[int, int, list[str]]]:
    """Alle sechs Zonen zwischen den Ankern: Name -> (Start, Ende, Grenzanker)."""
    def bar_of(name: str) -> int | None:
        a = anchors.get(name)
        return a.bar if a else None

    first = bar_of("intro_1") or bar_of("first_beat") or 0
    break1, drop1 = bar_of("break1"), bar_of("drop1")
    break2, drop2, outro = bar_of("break2"), bar_of("drop2"), bar_of("outro_start")
    end, known = _track_end(lattice, music_end)

    zones: dict[str, tuple[int, int, list[str]]] = {}

    def add(name, start, stop, names):
        if start is not None and stop is not None and stop > start:
            zones[name] = (start, stop, names)

    add("intro", first, break1, ["break1"])
    add("build1", break1, drop1, ["break1", "drop1"])
    if break2 is not None:
        add("strophe1", drop1, break2, ["drop1", "break2"])
    else:
        add("strophe1", drop1, drop2, ["drop1", "drop2"])
    add("build2", break2, drop2, ["break2", "drop2"])
    if outro is not None:
        add("strophe2", drop2, outro, ["drop2", "outro_start"])
    else:
        add("strophe2", drop2, end, ["drop2"])
    if outro is not None:
        # Am Trackende braucht es keinen Punkt mehr: bis zum Musikende, sonst eine Phrase Abstand.
        add("outro", outro, end if known else max(outro + 1, end - BARS_PER_PHRASE), ["outro_start"])
    return zones


def _auto_points(start: int, stop: int, k: int, occupied: set[int],
                 front: bool = False) -> tuple[list[int], int]:
    """
    Rasterpunkte einer Zone fuer k Plaetze: das feinste Raster (32, 64, 128 Beats
    ab dem Zonenstart), das mit k Plaetzen auskommt. Der Zonenstart selbst und
    schon belegte Takte zaehlen nicht. front=True: im 32er-Raster bleiben und
    die ersten k Punkte nehmen.
    """
    if k <= 0:
        return [], AUTO_STEPS_BARS[0]
    if front:
        step = AUTO_STEPS_BARS[0]
        return [b for b in range(start + step, stop, step) if b not in occupied][:k], step
    for step in AUTO_STEPS_BARS:
        pts = [b for b in range(start + step, stop, step) if b not in occupied]
        if len(pts) <= k:
            return pts, step
    # Selbst das 128er-Raster hat zu viele Punkte: gleichmaessig ausduennen.
    idx = sorted({int((i + 0.5) * len(pts) / k) for i in range(k)})
    return [pts[i] for i in idx], step


def _allocate_auto(demand: dict[str, int], budget: int,
                   weights: dict[str, float]) -> dict[str, int]:
    """
    Plaetze nach Gewicht verteilen (Hoechstzahlverfahren): der naechste Platz
    geht an die Zone mit dem hoechsten Gewicht / (bisherige Plaetze + 1), solange
    sie noch Rasterpunkte frei hat. Bei Gleichstand gewinnt die fruehere Zone.
    """
    alloc = {z: 0 for z in demand}
    order = list(demand)
    for _ in range(max(0, budget)):
        open_zones = [z for z in order if alloc[z] < demand[z]]
        if not open_zones:
            break
        best = max(open_zones, key=lambda z: (weights.get(z, 0.0) / (alloc[z] + 1), -order.index(z)))
        alloc[best] += 1
    return alloc


def _place_auto_cues(auto_rules, anchors: AnchorSet, lattice: Lattice,
                     placed_memory, max_memory_cues: int, threshold_for,
                     result: PlacementResult, music_end: int | None = None) -> list[tuple[int, str, str]]:
    """Auto-Slots aufloesen. Gibt (Takt, Slot-ID, Beschriftung) je Punkt zurueck."""
    bounds = _auto_zone_bounds(anchors, lattice, music_end)

    # Zone -> erster Slot, der sie anfordert (liefert Beschriftung und Farbe).
    # Zeigen zwei Slots auf dieselbe Zone, zaehlt sie nur einmal.
    zone_slot: dict[str, tuple[str, str]] = {}
    for slot_id, rule in auto_rules:
        zone = AUTO_ZONE_OF.get((rule["anchor"], rule["direction"]))
        if zone is None:
            result.skipped.append(
                (slot_id, f"auto {rule['direction']} '{rule['anchor']}' hat keine Zone"))
            continue
        if zone not in bounds:
            result.skipped.append(
                (slot_id, f"auto: Zone {zone} auf diesem Track nicht ableitbar"))
            continue
        limit = threshold_for(slot_id)
        weak = [n for n in bounds[zone][2]
                if anchors.get(n) is not None and anchors.get(n).confidence < limit]
        if weak:
            result.skipped.append(
                (slot_id, f"auto: Konfidenz von '{weak[0]}' unter Schwelle {limit:.2f}"))
            continue
        zone_slot.setdefault(zone, (slot_id, rule["label"]))

    # Zonen in Track-Reihenfolge -- bei gleichem Gewicht gewinnt die fruehere.
    zones = sorted(zone_slot, key=lambda z: bounds[z][0])
    occupied = {bar for bar, _role, _prio in placed_memory}
    budget = max_memory_cues - len(placed_memory)
    if not zones:
        return []
    if budget <= 0:
        for z in zones:
            result.skipped.append((zone_slot[z][0], "auto: kein Memory-Platz mehr frei"))
        return []

    demand = {z: len(_auto_points(bounds[z][0], bounds[z][1], 10 ** 6, occupied)[0])
              for z in zones}
    # Weitet eine Zone ihr Raster, braucht sie weniger Plaetze, als ihr zustehen --
    # der Rest geht zurueck in den Pool. Wiederholen, bis die Verteilung steht.
    for _ in range(len(zones) * 4):
        alloc = _allocate_auto(demand, budget, AUTO_ZONE_WEIGHTS)
        plans = {z: _auto_points(bounds[z][0], bounds[z][1], alloc[z], occupied,
                                 front=z in AUTO_FRONT_ZONES) for z in zones}
        shrunk = {z: len(plans[z][0]) for z in zones if len(plans[z][0]) < alloc[z]}
        if not shrunk:
            break
        demand.update(shrunk)

    out: list[tuple[int, str, str]] = []
    for z in zones:
        pts, step = plans[z]
        slot_id, label = zone_slot[z]
        if not pts:
            why = ("kein Memory-Platz mehr frei, Zonen mit hoeherem Gewicht gingen vor"
                   if demand[z] else "ohne freien Rasterpunkt")
            result.skipped.append((slot_id, f"auto: Zone {z} {why}"))
            continue
        result.escalation_log.append(
            f"Auto {z}: {len(pts)} Punkt(e), {step * BEATS_PER_BAR}er-Raster ab Zonenstart "
            f"({bounds[z][1] - bounds[z][0]} Takte)")
        out.extend((b, slot_id, label) for b in pts)
    return out


def _finalise(cues, lattice: Lattice, max_memory_cues: int,
              result: PlacementResult):
    """Sortieren, doppelte entfernen, Memory-Budget durchsetzen."""
    cues.sort(key=lambda c: (c.time_sec, -c.kind))

    # Doppelte NUR innerhalb derselben Art entfernen. Hot Cue und Memory Cue
    # duerfen auf derselben Zeit liegen -- das ist bei Rekordbox zulaessig und
    # gewollt, wenn zwei Rollen zusammenfallen (etwa erkannter Aufbau und
    # gerechnete 32 Beats vor dem Drop).
    kept = []
    for cue in cues:
        same_class = [
            c for c in kept
            if (c.kind == 0) == (cue.kind == 0) and abs(c.time_sec - cue.time_sec) < 0.2
        ]
        if same_class:
            continue
        kept.append(cue)

    memory = [c for c in kept if c.kind == 0]
    if len(memory) > max_memory_cues:
        memory.sort(key=lambda c: (c.priority, c.time_sec))
        dropped = memory[max_memory_cues:]
        for cue in dropped:
            result.skipped.append((cue.role, "Memory-Budget erschoepft"))
        keep = set(id(c) for c in memory[:max_memory_cues])
        kept = [c for c in kept if c.kind != 0 or id(c) in keep]

    kept.sort(key=lambda c: c.time_sec)
    return kept
