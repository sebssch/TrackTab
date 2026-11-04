"""
Schritt 4 der Analysekette: Evidenz einsammeln.

Zwei Quellen sagen etwas darueber, wo ein Abschnitt anfangen koennte. KEINE davon
liefert eine fertige Cue-Position -- beide liefern Hinweise, die auf Takte verteilt
werden:

    Phrasen                   in TrackTab die Phrasen des DJ-Editors (eigene Messung
                              oder aus Rekordbox uebernommen); das Training kannte
                              Rekordbox' PSSI: taktgenau, aber lueckenhaft
    Tempowechsel              seltener, dafuer sehr zuverlaessiger Hinweis

Gemessen (documentation.md, M-002): Rekordbox-Phrasen liegen nur zu 54 % auf dem
32er-Raster und lassen Grenzen aus. Deshalb werden sie als Gewicht behandelt, nie
als Position uebernommen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.autocue.types import Lattice

# --- Rekordbox-Phrasentypen, abhaengig vom mood ---
#
# Rekordbox kennt drei Gliederungsschemata. DIESELBE kind-Zahl bedeutet je nach
# mood etwas anderes. Die Vorgaengerversion wandte die mood-1-Tabelle auf alle
# Tracks an -- 5,5 % der Library (mood 2) wurden dadurch durchgehend falsch
# gedeutet (documentation.md, M-001).
#
# mood 2: Die Reihenfolge aus der Dokumentation (… Chorus, Bridge, Outro) passt
# nicht zu den Daten. Empirisch hat kind 9 die hoechste Energie (0,751) und ist
# mit Abstand am haeufigsten -- genau das Verhalten des Chorus in mood 1. Die
# Tabelle folgt deshalb der Messung, nicht der Dokumentation.
PSSI_KINDS = {
    1: {  # High / Energetic -- 94,5 % der Library
        1: "intro", 2: "build", 3: "break", 4: "drop", 5: "drop", 6: "outro",
    },
    2: {  # Mid
        1: "intro", 2: "verse", 3: "verse", 4: "verse", 5: "verse",
        6: "verse", 7: "verse", 8: "bridge", 9: "drop", 10: "outro",
    },
    3: {  # Low
        1: "intro", 2: "verse", 3: "verse", 4: "verse", 5: "verse",
        6: "verse", 7: "bridge", 8: "bridge", 9: "drop", 10: "outro",
    },
}


@dataclass
class Phrase:
    """Eine Rekordbox-Phrase, mit allem, was der Tag hergibt."""
    beat: int
    kind: int
    role: str                 # intro | build | break | drop | verse | bridge | outro
    bar: int                  # Takt im eigenen Raster
    time_sec: float
    variant: int = 0          # aus k1/k2/k3: bei "build" ist 3 der finale Aufbau
    fill_beat: int | None = None   # Fill-In vor dem naechsten Abschnitt

    # Empirisch bestimmt, nicht aus der Dokumentation uebernommen (V-007):
    # Auf Aufbau-Phrasen mit k2 folgt in 100 % der Faelle ein Drop, auf solche mit
    # k3 nur in 17 %. Die naheliegende Annahme "Up 3 ist der finale Aufbau" ist
    # also falsch -- es ist Up 2.
    FINAL_BUILD_VARIANT = 2

    @property
    def is_final_build(self) -> bool:
        """Der letzte Aufbau vor einem Drop. Gemessen: das ist Variante 2."""
        return self.role == "build" and self.variant == self.FINAL_BUILD_VARIANT


@dataclass
class Evidence:
    """
    Alle Hinweise, auf Takte umgerechnet.

    Jede Kurve hat so viele Eintraege wie der Track Takte hat. Hoehere Werte heissen
    "hier spricht mehr dafuer, dass ein Abschnitt beginnt".
    """
    n_bars: int
    boundary: np.ndarray                      # Gesamtgewicht je Takt
    by_source: dict[str, np.ndarray] = field(default_factory=dict)
    phrases: list[Phrase] = field(default_factory=list)
    drop_hint: np.ndarray | None = None       # wo eine Quelle einen Drop sieht
    break_hint: np.ndarray | None = None
    build_hint: np.ndarray | None = None
    sources_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _blur(curve: np.ndarray, width: int = 1) -> np.ndarray:
    """
    Einen Hinweis auf die Nachbartakte verschmieren.

    Noetig, weil die Quellen unterschiedlich genau sind: Rekordbox-Phrasen sitzen
    exakt, ein Tempowechsel kann um einen Takt abweichen. Ohne Verschmieren wuerde
    ein um einen Takt danebenliegender Hinweis gar nicht zaehlen.
    """
    if width <= 0:
        return curve
    out = curve.copy()
    for offset in range(1, width + 1):
        weight = 1.0 / (offset + 1)
        out[offset:] = np.maximum(out[offset:], curve[:-offset] * weight)
        out[:-offset] = np.maximum(out[:-offset], curve[offset:] * weight)
    return out


# TrackTab-Phrasenarten (phrases.py, rekordbox._phrase_kind()) -> Rolle der
# Kette. Entspricht PSSI_KINDS oben: Up = Aufbau, Down = Break, Chorus = Drop.
TRACKTAB_ROLES = {
    "intro": "intro", "up": "build", "down": "break", "chorus": "drop",
    "outro": "outro", "verse": "verse", "bridge": "bridge",
}

# rekordbox._phrase_kind() nummeriert Up nach k2/k3: k2 gesetzt = "Up 3".
# Genau das ist Variante 2 des Originals (erstes gesetztes Flag k1/k2/k3), der
# gemessen finale Aufbau. TrackTabs eigenes Phrasenmodell (phrases.py) zaehlt
# Up 1..3 nur der Reihe nach (Up 3 = dritter und jeder weitere Aufbau) -- dort
# ist das eine Naeherung.
_FINAL_BUILD_NUM = 3


def read_phrases(phrases, lattice: Lattice) -> list[Phrase]:
    """
    TrackTabs Phrasenliste [{start, end, kind, num}] (Sekunden, eigene
    Zeitachse) in Phrasen der Kette umsetzen. Fill-Markierungen kennt TrackTab
    nicht, sie fehlen hier.
    """
    out: list[Phrase] = []
    for p in phrases or []:
        role = TRACKTAB_ROLES.get(str(p.get("kind") or ""))
        if role is None:
            continue
        time_sec = float(p["start"])
        variant = 0
        if role == "build" and int(p.get("num") or 0) == _FINAL_BUILD_NUM:
            variant = Phrase.FINAL_BUILD_VARIANT
        out.append(Phrase(beat=0, kind=0, role=role, bar=lattice.nearest_bar(time_sec),
                          time_sec=time_sec, variant=variant))
    return out


def collect_evidence(lattice: Lattice, phrases=None,
                     use_phrases: bool = True) -> Evidence:
    """Alle Hinweise einsammeln und auf Takte umrechnen."""
    n = lattice.n_bars
    ev = Evidence(n_bars=n, boundary=np.zeros(n))
    drop = np.zeros(n)
    brk = np.zeros(n)
    build = np.zeros(n)

    # --- Rekordbox-Phrasen: taktgenau, daher kaum verschmieren ---
    if use_phrases and phrases:
        phrases = read_phrases(phrases, lattice)
        if phrases:
            ev.phrases = phrases
            curve = np.zeros(n)
            for p in phrases:
                if not 0 <= p.bar < n:
                    continue
                curve[p.bar] = max(curve[p.bar], 1.0)
                if p.role == "drop":
                    drop[p.bar] = max(drop[p.bar], 1.0)
                elif p.role == "break":
                    brk[p.bar] = max(brk[p.bar], 1.0)
                elif p.role == "build":
                    # Der finale Aufbau zaehlt deutlich mehr: Auf ihn folgt
                    # gemessen IMMER ein Drop, er ist damit der beste Einzelhinweis
                    # auf dessen Lage. Auf die uebrigen Aufbauten folgt nur in
                    # rund einem Drittel der Faelle einer.
                    build[p.bar] = max(build[p.bar], 1.0 if p.is_final_build else 0.4)
                    if p.is_final_build and p.bar + 1 < n:
                        # Der Drop kommt danach -- der Hinweis gehoert nach vorn.
                        drop[min(p.bar + 8, n - 1)] = max(drop[min(p.bar + 8, n - 1)], 0.7)
                # Ein Fill-In kuendigt den naechsten Abschnitt an.
                if p.fill_beat:
                    fb = lattice.nearest_bar(
                        float(lattice.grid.times[
                            int(np.clip(p.fill_beat - 1, 0, len(lattice.grid.times) - 1))])
                    )
                    if 0 <= fb < n:
                        curve[fb] = max(curve[fb], 0.4)
            ev.by_source["phrases"] = _blur(curve, width=1)
            ev.sources_used.append("phrases")
        else:
            ev.notes.append("keine Phrasen fuer diesen Track")

    # --- Tempowechsel: selten, aber sehr verlaesslich ---
    change_beats = getattr(lattice.report, "tempo_change_beats", [])
    if change_beats:
        curve = np.zeros(n)
        times = np.asarray(lattice.grid.times)
        for beat in change_beats:
            idx = int(np.clip(beat, 0, len(times) - 1))
            bar = lattice.nearest_bar(float(times[idx]))
            if 0 <= bar < n:
                curve[bar] = 1.0
        ev.by_source["tempo_change"] = _blur(curve, width=1)
        ev.sources_used.append("tempo_change")
        ev.notes.append(f"{len(change_beats)} Tempowechsel als Grenzhinweis")

    # --- Zusammenfuehren ---
    weights = {"phrases": 0.8, "tempo_change": 0.9}
    for name, curve in ev.by_source.items():
        ev.boundary += weights.get(name, 0.5) * curve
    if ev.boundary.max() > 0:
        ev.boundary /= ev.boundary.max()

    ev.drop_hint = _blur(drop, width=1)
    ev.break_hint = _blur(brk, width=1)
    ev.build_hint = _blur(build, width=1)
    return ev
