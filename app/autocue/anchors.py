"""
Schritt 6 der Analysekette: Anker aus der Gliederung ableiten.

Ein Anker ist eine Stelle im Track, auf die sich eine Cue legen laesst. Welche
Cue -- Hot Cue A, Memory Cue oder gar keine -- entscheidet erst die Konfiguration
in Schritt 8. Hier geht es nur darum, die Stellen zu finden und ehrlich zu sagen,
wie sicher sie sind.

Erkannt und gerechnet stehen bewusst nebeneinander: `build2` ist der Aufbau, den
die Analyse sieht, `pre_drop2` sind schlicht 32 Beats vor dem letzten Drop.
Meistens dasselbe -- dann bestaetigen sie sich. Wenn nicht, ist das eine
Information und kein Widerspruch, der aufzuloesen waere.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.autocue.structure import Section, StructureModel
from app.autocue.types import BARS_PER_PHRASE, BarFeatures, Lattice

# Ein Aufbau dauert eine Phrase: 32 Beats = 8 Takte.
SETUP_BARS = BARS_PER_PHRASE


@dataclass
class Anchor:
    """Eine Stelle im Track, mit Begruendung."""
    name: str
    bar: int
    time_sec: float
    confidence: float
    reason: str
    derived: bool = False       # gerechnet statt erkannt

    def __repr__(self) -> str:
        m, s = divmod(self.time_sec, 60)
        kind = "gerechnet" if self.derived else "erkannt"
        return f"Anchor({self.name} @ {int(m)}:{s:05.2f} Takt {self.bar}, {self.confidence:.2f}, {kind})"


@dataclass
class AnchorSet:
    anchors: dict[str, Anchor] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def get(self, name: str) -> Anchor | None:
        return self.anchors.get(name)

    def add(self, anchor: Anchor | None) -> None:
        if anchor is not None:
            self.anchors[anchor.name] = anchor

    def times(self) -> dict[str, float]:
        return {k: a.time_sec for k, a in self.anchors.items()}


def _first_gate_rise(features: BarFeatures, name: str,
                     start: int = 0, min_bar: int = 0) -> int | None:
    """Erster Takt, ab dem eine Klangschicht dauerhaft anliegt."""
    if not features.has(name):
        return None
    gate = features[name]
    for bar in range(max(start, min_bar), len(gate)):
        if gate[bar] > 0.5:
            return bar
    return None


def _make(name: str, bar: int, lattice: Lattice, confidence: float,
          reason: str, derived: bool = False) -> Anchor | None:
    if bar is None or not (0 <= bar < lattice.n_bars):
        return None
    return Anchor(name=name, bar=int(bar), time_sec=lattice.time_of_bar(bar),
                  confidence=float(np.clip(confidence, 0.0, 1.0)),
                  reason=reason, derived=derived)


def snap_to_impact(features: BarFeatures, start_bar: int, n_bars: int,
                   search_phrases: int = 2) -> tuple[int, str]:
    """
    Den Drop-Anker vom Abschnittsrand auf den tatsaechlichen Einschlag ruecken.

    Warum das noetig ist: Der Strukturfit setzt die Abschnittsgrenze dorthin, wo
    sich die Musik AENDERT -- und das ist bei einem Drop oft schon der Beginn des
    Risers, nicht der Einschlag selbst. Gegen von Hand gesetzte Cues gemessen lag
    der Abschnittsrand deshalb systematisch zu frueh: 61 % der Tracks zu frueh,
    nur 5 % zu spaet, Median eine ganze Phrase (documentation.md, V-013).

    Gesucht wird ausschliesslich auf RASTERPUNKTEN ab dem Abschnittsrand. Damit
    bleibt Regel 4 konstruktiv erfuellt -- der Anker kann das Raster gar nicht
    verlassen. Zwei Phrasen Suchweite genuegen; drei oder vier aendern nichts mehr.

    Returns:
        (Takt, Begruendung)
    """
    if not features.has("impact_score"):
        return start_bar, "Abschnittsrand (kein Einschlagsmerkmal vorhanden)"

    impact = features["impact_score"]
    candidates = [start_bar + k * BARS_PER_PHRASE for k in range(search_phrases + 1)]
    candidates = [b for b in candidates if 0 <= b < n_bars]
    if not candidates:
        return start_bar, "Abschnittsrand"

    def strength(bar: int) -> float:
        return float(impact[bar:bar + 2].max())

    best = max(candidates, key=strength)
    if best == start_bar:
        return best, "Einschlag liegt am Abschnittsrand"
    phrases = (best - start_bar) // BARS_PER_PHRASE
    return best, f"Einschlag {phrases} Phrase{'n' if phrases > 1 else ''} nach dem Abschnittsrand"


def _melody_entry(features: BarFeatures, start: int, end: int) -> int | None:
    """
    Wo setzt in einem Bereich die Melodie oder der Synth ein?

    Regel 3 nennt das als zweite Moeglichkeit fuer Hot Cue A -- neben dem Break
    Down. Gemessen wird im Mittenband, was ungenauer ist, weil sich dort Melodie
    und Percussion ueberlagern.
    """
    name = "melodic_rms"
    if not features.has(name) or end <= start + 2:
        return None
    values = features[name][start:end]
    if values.size < 3:
        return None

    # Nicht den groessten Sprung von Takt zu Takt suchen, sondern den Uebergang
    # von "leise" zu "da" ueber eine Phrase hinweg. Ein Melodieeinsatz baut sich
    # ueber einige Takte auf; ein reiner Nachbarvergleich findet stattdessen
    # irgendeine Schwankung.
    win = min(4, max(1, len(values) // 4))
    best_gain, best_bar = 0.0, None
    for i in range(win, len(values) - win + 1):
        before = float(values[i - win:i].mean())
        after = float(values[i:i + win].mean())
        gain = after - before
        if gain > best_gain:
            best_gain, best_bar = gain, start + i

    # Schwelle bewusst niedrig: Lieber einen Kandidaten liefern, den die
    # Konfidenz spaeter verwirft, als gar keinen. Der Wert liegt nur im
    # Mittenband und wird entsprechend schwaecher gewichtet.
    return best_bar if best_gain > 0.06 else None


def _hot_a_candidate(features: BarFeatures, lattice: Lattice,
                     model: StructureModel, drop1_bar: int,
                     base: float, result: "AnchorSet") -> Anchor | None:
    """
    Den Platz fuer Hot Cue A im Anlauf zu Drop 1 suchen.

    Abgesucht werden ausschliesslich Punkte auf dem 32-Beat-Raster VOR dem Drop
    (also Drop minus 8, 16, 24 ... Takte). Damit ist Regel 4 von vornherein
    erfuellt -- es kann gar kein Kandidat entstehen, der nicht im Raster liegt.

    Jeder Kandidat wird nach drei Gesichtspunkten bewertet, die Regel 3 nennt:
      - nimmt dort der Bass ab? (Break Down)
      - nimmt dort die Lautstaerke ab?
      - setzt dort Melodie oder Synth ein?
    """
    n = features.n_bars
    if drop1_bar < BARS_PER_PHRASE:
        result.notes.append("Drop 1 liegt zu frueh -- kein Platz fuer Hot Cue A davor")
        return None

    energy = features["rms"] if features.has("rms") else np.zeros(n)
    low = features["low_rms"] if features.has("low_rms") else energy
    melody = features["melodic_rms"] if features.has("melodic_rms") else np.zeros(n)

    # Kandidaten: eine bis sechs Phrasen vor dem Drop, und nicht vor Trackbeginn.
    candidates = [drop1_bar - k * BARS_PER_PHRASE for k in range(1, 7)]
    candidates = [b for b in candidates if b >= BARS_PER_PHRASE // 2]
    if not candidates:
        result.notes.append("kein Rasterpunkt zwischen Trackanfang und Drop 1")
        return None

    scored: list[tuple[float, int, str]] = []
    for bar in candidates:
        win = BARS_PER_PHRASE // 2
        before = slice(max(0, bar - win), bar)
        after = slice(bar, min(n, bar + win))
        if before.start >= before.stop or after.start >= after.stop:
            continue

        # Break Down: Bass und Lautstaerke gehen zurueck.
        bass_drop = float(low[before].mean() - low[after].mean())
        level_drop = float(energy[before].mean() - energy[after].mean())
        # Melodieeinsatz: die melodische Schicht kommt dazu.
        melody_rise = float(melody[after].mean() - melody[before].mean())

        breakdown_score = max(0.0, bass_drop) + max(0.0, level_drop)
        melody_score = max(0.0, melody_rise)

        if breakdown_score >= melody_score and breakdown_score > 0.05:
            score = breakdown_score
            reason = f"Bass und Lautstaerke nehmen ab ({BARS_PER_PHRASE and (drop1_bar-bar)//BARS_PER_PHRASE} Phrasen vor Drop 1)"
            conf = base * 0.85
        elif melody_score > 0.05:
            score = melody_score
            reason = f"Melodie/Synth setzt ein ({(drop1_bar-bar)//BARS_PER_PHRASE} Phrasen vor Drop 1)"
            # Die Melodie liegt mit der Percussion im selben Band --
            # die Lesart ist deutlich unsicherer.
            conf = base * 0.55
        else:
            continue
        scored.append((score, bar, reason))
        scored[-1] = (score, bar, reason)

    if not scored:
        result.notes.append(
            "im Anlauf zu Drop 1 ist weder ein Rueckgang von Bass und Lautstaerke "
            "noch ein Melodieeinsatz messbar -- Hot Cue A bleibt aus"
        )
        return None

    scored.sort(reverse=True)
    score, bar, reason = scored[0]
    conf = base * (0.85 if "Bass" in reason else 0.55)
    # Eine Phrase direkt vor dem Drop ist der Aufbau, nicht der Break -- dort
    # gehoert Hot Cue B hin, nicht A.
    if drop1_bar - bar <= BARS_PER_PHRASE:
        conf *= 0.6
        reason += " (dicht am Drop -- koennte der Aufbau sein)"
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03:
        conf *= 0.8
        reason += ", zweite Stelle fast gleichwertig"

    return _make("break1", bar, lattice, conf, reason)


def extract_anchors(model: StructureModel, features: BarFeatures,
                    lattice: Lattice, learned: dict | None = None) -> AnchorSet:
    """
    Aus der Gliederung die Stellen ableiten, auf die Cues gelegt werden koennen.

    Args:
        learned: Ergebnis von app.autocue.learned.predict_anchors() -- je Rolle
            der vom gelernten Modell gewaehlte Takt samt kalibrierter Konfidenz.
            Eintraege fuer `drop2`, `drop1`, `break1`, `break2` und
            `outro_start` haben Vorrang vor dem Strukturfit: Die Modelle haben
            gegen tausende Hand-Cues gelernt, der Fit rechnet mit gesetzten
            Gewichten und bleibt nur Rueckfall. Aufbauten und gerechnete Punkte
            (setup, pre_drop1) richten sich nach den gelernten Drops.
    """
    learned = learned or {}
    result = AnchorSet()
    if not model.sections and not learned:
        result.notes.append("keine Gliederung -- keine Anker")
        return result

    base = model.quality  # Grundvertrauen aus der Gueter der Gliederung
    drops: list[Section] = model.sections_of("drop") if model.sections else []
    breaks: list[Section] = model.sections_of("break") if model.sections else []
    builds: list[Section] = model.sections_of("build") if model.sections else []

    # --- Trackanfang und Einsatz der Drums ---
    result.add(_make("first_beat", 0, lattice, 1.0, "erster Downbeat des Beatgrids"))
    # Die Position "Intro" der Oberflaeche: die erste "1" im Beatgrid. Auftakt-
    # Beats davor zaehlen nicht -- in allen zehn Gold-Set-Tracks mit Auftakt
    # liegt der Hand-Cue auf dieser ersten 1 (documentation.md, V-020).
    result.add(_make("intro_1", 0, lattice, 1.0, "Intro: erste 1 im Beatgrid"))

    kick = _first_gate_rise(features, "drums_on") or _first_gate_rise(features, "low_on")
    if kick is not None:
        result.add(_make("beat_in", kick, lattice, 0.75 if kick > 0 else 1.0,
                         "Kick und Drums setzen ein"))

    # --- Die Drops ---
    learned_drop2 = learned.get("drop2")
    if not drops and learned_drop2 is None:
        result.notes.append("kein Drop erkennbar -- Hot Cues bleiben aus")
        return result

    # Regel 8: Gibt es nur einen Drop, ist er der LETZTE. Hot Cues haben Vorrang,
    # also wird er drop2 und die Rolle drop1 bleibt leer.
    last_drop = drops[-1] if drops else None
    if learned_drop2 is not None:
        drop2_bar = int(learned_drop2.bar)
        drop_conf = float(learned_drop2.confidence)
        why = (f"gelernt: P={learned_drop2.prob:.2f}, Marge {learned_drop2.margin:.2f}")
        if last_drop is not None:
            fit_bar, _ = snap_to_impact(features, last_drop.start_bar, lattice.n_bars)
            diff = lattice.beats_between(fit_bar, drop2_bar)
            why += ", Strukturfit stimmt zu" if diff == 0 else f", Strukturfit laege {diff:+d} Beats"
        result.add(_make("drop2", drop2_bar, lattice, drop_conf, why))
    else:
        drop2_bar, drop2_why = snap_to_impact(features, last_drop.start_bar, lattice.n_bars)
        drop_conf = base
        result.add(_make("drop2", drop2_bar, lattice, base,
                         f"letzter Drop ({last_drop.n_phrases:.1f} Phrasen), {drop2_why}"))

    # Erster Drop: aus der Anker-Kette (gesucht zwischen Takt 4 und Drop 2),
    # sonst aus der Gliederung -- und nur, wenn er wirklich VOR dem letzten
    # Drop liegt.
    drop1_bar = None
    learned_drop1 = learned.get("drop1")
    if learned_drop1 is not None and int(learned_drop1.bar) < drop2_bar:
        drop1_bar = int(learned_drop1.bar)
        result.add(_make("drop1", drop1_bar, lattice, float(learned_drop1.confidence),
                         f"gelernt (Kette): P={learned_drop1.prob:.2f}"))
    elif len(drops) >= 2:
        first_drop = drops[0]
        cand, drop1_why = snap_to_impact(features, first_drop.start_bar, lattice.n_bars)
        if cand + BARS_PER_PHRASE <= drop2_bar:
            drop1_bar = cand
            result.add(_make("drop1", drop1_bar, lattice, base * 0.95,
                             f"erster Drop ({first_drop.n_phrases:.1f} Phrasen), {drop1_why}"))
    if drop1_bar is None:
        result.notes.append(
            "nur ein Drop im Track -- er gilt als letzter Drop (Regel 8), "
            "die Rolle 'drop1' bleibt leer"
        )

    # --- Aufbauten: erkannt und gerechnet nebeneinander ---
    def _build_before(target_bar: int) -> Section | None:
        prior = [b for b in builds if b.end_bar <= target_bar]
        return prior[-1] if prior else None

    anchored = {"drop2": drop2_bar}
    if drop1_bar is not None:
        anchored["drop1"] = drop1_bar

    for drop_name in ("drop2", "drop1"):
        if drop_name not in anchored:
            continue
        suffix = "2" if drop_name == "drop2" else "1"
        # Ab hier zaehlt der EINGERASTETE Drop, nicht der Abschnittsrand --
        # sonst erbt Hot B den Versatz, den das Einrasten gerade behoben hat.
        drop_bar = anchored[drop_name]

        detected = _build_before(drop_bar)
        if detected is not None and drop_bar - detected.start_bar <= 3 * BARS_PER_PHRASE:
            result.add(_make(f"build{suffix}", detected.start_bar, lattice, base * 0.85,
                             f"erkannter Aufbau, {detected.n_phrases:.1f} Phrasen vor dem Drop"))

        # Gerechnet: genau eine Phrase vor dem Drop (Regel 2).
        pre_bar = drop_bar - SETUP_BARS
        if pre_bar >= 0:
            name = "setup" if drop_name == "drop2" else "pre_drop1"
            # Sitzt dort wirklich ein Aufbau? Herrscht schon volle Energie,
            # war die Lesart falsch -- dann sinkt das Vertrauen.
            conf = (drop_conf if drop_name == "drop2" else base) * 0.9
            if features.has("rms"):
                before = float(features["rms"][pre_bar:drop_bar].mean())
                after = float(features["rms"][drop_bar:drop_bar + SETUP_BARS].mean())
                if before >= after - 0.05:
                    conf *= 0.5
                    result.notes.append(
                        f"{name}: 32 Beats vor dem Drop herrscht bereits volle Energie -- "
                        f"dort ist kein Aufbau"
                    )
            result.add(_make(name, pre_bar, lattice, conf,
                             f"32 Beats vor {drop_name}", derived=True))

    # --- Break 1: die Stelle fuer Hot Cue A ---
    #
    # Regel 3 beschreibt keinen Abschnittsrand, sondern einen Punkt IM ANLAUF zu
    # Drop 1 -- "im 32er-Raster zum Drop 1", dort wo entweder der Bass und die
    # Lautstaerke abnehmen oder die Melodie einsetzt.
    #
    # Eine erste Fassung suchte stattdessen den Beginn eines Break-Abschnitts
    # oder das Ende des Intros. Beides scheiterte an der Realitaet: Bei elf von
    # zwoelf Testtracks laeuft das Intro ohne eigenen Break direkt in den Drop,
    # das Ende des Intros IST also der Drop -- es blieb kein Platz fuer einen
    # Kandidaten. Deshalb wird jetzt das Raster selbst abgesucht.
    learned_break1 = learned.get("break1")
    if learned_break1 is not None:
        result.add(_make("break1", int(learned_break1.bar), lattice,
                         float(learned_break1.confidence),
                         f"gelernt: P={learned_break1.prob:.2f}, Marge {learned_break1.margin:.2f}"))
    else:
        result.add(_hot_a_candidate(features, lattice, model,
                                    anchored.get("drop1", drop2_bar), base, result))

    # --- Break 2: der ERSTE Break nach Drop 1 (Festlegung des DJs) ---
    learned_break2 = learned.get("break2")
    if (learned_break2 is not None and drop1_bar is not None
            and drop1_bar < int(learned_break2.bar) < drop2_bar):
        result.add(_make("break2", int(learned_break2.bar), lattice,
                         float(learned_break2.confidence),
                         f"gelernt (Kette): P={learned_break2.prob:.2f}"))
    elif drop1_bar is not None:
        mid = [b for b in breaks if drop1_bar < b.start_bar < drop2_bar]
        if mid:
            b = mid[0]
            result.add(_make("break2", b.start_bar, lattice, base * 0.85,
                             f"erster Break nach Drop 1 ({b.n_phrases:.1f} Phrasen)"))
        else:
            result.notes.append("kein Break zwischen Drop 1 und Drop 2")

    # --- Outro: wo nach Drop 2 die Musik wechselt ---
    # Gemessen am Gold-Set liegt der Hand-Cue in 93 % der Tracks 16 Takte nach
    # Drop 2 (Ende des Drop-2-Chorus), nie in einer leisen Stelle. Die Rekordbox-
    # Phrase dort ist mal Chorus, mal Down, mal Outro -- sie entscheidet nichts.
    learned_outro = learned.get("outro_start")
    if learned_outro is None:
        from app.autocue.learned import outro_by_rule
        rms = features["rms"] if features.has("rms") else None
        learned_outro = outro_by_rule(rms, lattice.n_bars, drop2_bar, drop_conf * 0.8)
    if learned_outro is not None:
        result.add(_make("outro_start", int(learned_outro.bar), lattice,
                         float(learned_outro.confidence),
                         f"{(int(learned_outro.bar) - drop2_bar)} Takte nach Drop 2"))
    else:
        result.notes.append("kein Outro: nach Drop 2 kein lauter Kandidat im Raster")

    return result
