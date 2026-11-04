"""
Schritt 7 der Analysekette: jeden Hot Cue unabhaengig pruefen (Regel 10).

Der Punkt dieser Stufe ist ihre UNABHAENGIGKEIT. Die Anker stammen aus dem
Strukturfit; wuerde die Pruefung denselben Fit noch einmal befragen, bekaeme man
dieselbe Antwort und haette nichts gewonnen. Deshalb arbeitet hier alles
ausschliesslich auf der Merkmalstabelle aus Schritt 3 -- dieselben Rohdaten,
aber ein anderer Weg zur Aussage.

Drei Ergebnisse je Cue:

    bestaetigt   Die Merkmale stuetzen den Anker.
    unsicher     Die Merkmale sagen weder das eine noch das andere.
    widerlegt    Die Merkmale sprechen dagegen.

Ein Widerspruch loescht den Anker nicht -- er senkt die Konfidenz. Ob daraus ein
Auslassen wird, entscheidet die Schwelle in der Konfiguration. So bleibt die
Entscheidung an einer Stelle statt an zweien.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.autocue.anchors import AnchorSet
from app.autocue.types import BARS_PER_PHRASE, BarFeatures, Lattice

# Wie weit nach dem Drop geprueft wird, ob die Energie oben bleibt.
SUSTAIN_BARS = 16

# Wie deutlich der Bass beim Drop zulegen muss, in Anteilen der Trackspanne.
MIN_BASS_JUMP = 0.12

# Faktoren auf die Konfidenz. Die Pruefung kann nur DAEMPFEN, nie anheben.
#
# Eine erste Fassung hob bestaetigte Anker um 15 % an. Das klingt harmlos, weicht
# aber still die kalibrierten Schwellen auf: Die Werte in config.yaml stammen aus
# einer Messung OHNE diese Stufe, und wenn danach fast jeder Anker 15 % zulegt,
# bedeutet "0.7" nicht mehr das, was gemessen wurde. Gemessen lag die mittlere
# Anhebung bei +0,07 bis +0,10 -- also genau in der Groessenordnung, in der die
# Schwellen unterscheiden.
#
# Einseitig ist ausserdem das ehrlichere Modell: Eine bestandene Pruefung sagt
# nicht "besser als gedacht", sondern nur "kein Grund zum Zweifel".
FACTOR = {"bestaetigt": 1.0, "unsicher": 0.85, "widerlegt": 0.5}

# Was eine NICHT bestandene Einzelpruefung kostet.
#
# Eine erste Fassung liess die Pruefungen per Mehrheit abstimmen. Das warf
# Information weg: Faellt bei vier Pruefungen eine durch, bleibt die Mehrheit
# dafuer -- und das Urteil lautete "bestaetigt", obwohl gemessen genau diese eine
# Pruefung trennt. Bei break1 kam deshalb bei 100 von 100 Ankern "bestaetigt"
# heraus, obwohl jede Einzelpruefung fuer sich 27 bis 29 Prozentpunkte
# Unterschied macht.
#
# Die Gewichte folgen der gemessenen Trennschaerfe (documentation.md, M-014):
# der Abstand in der Trefferquote zwischen bestanden und durchgefallen.
CHECK_PENALTY = {
    "Energie haelt":             0.55,   # gemessen 71 % gegen 0 %
    "leiser als der Drop":       0.45,   # 69 % gegen 13 %
    "Aufbau messbar":            0.40,   # 65 % gegen 20 %
    "Bass springt an":           0.38,   # 81 % gegen 39 %
    "ist der letzte":            0.30,   # 72 % gegen 40 %
    "Wahl eindeutig":            0.28,   # 56 % gegen 27 %
    "Abschnittswechsel messbar": 0.26,   # 49 % gegen 22 %
}
DEFAULT_PENALTY = 0.25


@dataclass
class Verdict:
    """Das Ergebnis der unabhaengigen Pruefung eines Cues."""
    role: str
    outcome: str
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def factor(self) -> float:
        """
        Der Faktor auf die Konfidenz -- gestuft, nicht dreiwertig.

        Jede nicht bestandene Pruefung kostet einen eigenen Anteil. Zwei
        durchgefallene Pruefungen wiegen damit schwerer als eine, und eine
        gemessen scharfe Pruefung schwerer als eine schwache. Nach oben wird nie
        korrigiert: Eine bestandene Pruefung heisst nicht "besser als gedacht",
        sondern nur "kein Grund zum Zweifel".
        """
        factor = 1.0
        for name, passed in self.checks.items():
            if passed is False:
                factor *= (1.0 - CHECK_PENALTY.get(name, DEFAULT_PENALTY))
        return float(max(0.0, min(1.0, factor)))

    def __repr__(self) -> str:
        return f"Verdict({self.role}: {self.outcome}, {len(self.checks)} Pruefungen)"


def _window(values: np.ndarray, start: int, length: int) -> np.ndarray:
    a = max(0, start)
    b = min(len(values), start + length)
    return values[a:b] if b > a else np.array([])


def _mean(values: np.ndarray, start: int, length: int, default: float = 0.0) -> float:
    seg = _window(values, start, length)
    return float(seg.mean()) if seg.size else default


def _col(features: BarFeatures, name: str, fallback: str = "rms") -> np.ndarray:
    if features.has(name):
        return features[name]
    if features.has(fallback):
        return features[fallback]
    return np.zeros(features.n_bars)


def _decide(checks: dict[str, bool]) -> str:
    """
    Ein Etikett fuer den Bericht.

    Die Konfidenz wird NICHT hierueber gesteuert -- das macht `Verdict.factor`
    gestuft ueber die einzelnen Pruefungen. Dieses Etikett dient nur dazu, im
    Log auf einen Blick zu sehen, wie es um einen Cue steht.
    """
    done = [v for v in checks.values() if v is not None]
    if not done:
        return "unsicher"
    failed = [name for name, v in checks.items() if v is False]
    if not failed:
        return "bestaetigt"
    # Wieviel Vertrauen bleibt uebrig? Darueber entscheidet sich das Etikett.
    remaining = 1.0
    for name in failed:
        remaining *= (1.0 - CHECK_PENALTY.get(name, DEFAULT_PENALTY))
    if remaining >= 0.70:
        return "unsicher"
    return "widerlegt"


def validate_drop2(anchors: AnchorSet, features: BarFeatures) -> Verdict | None:
    """
    Der letzte Drop. Drei Fragen, alle allein aus den Merkmalen:

      1. Springt die Bassenergie an dieser Stelle deutlich an?
      2. Bleibt sie danach mindestens 16 Takte oben? (Fake-Drop-Filter)
      3. Ist das wirklich das LETZTE solche Ereignis im Track?
    """
    anchor = anchors.get("drop2")
    if anchor is None:
        return None

    bar = anchor.bar
    low = _col(features, "low_rms")
    energy = _col(features, "rms")
    n = features.n_bars
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    before = _mean(low, bar - BARS_PER_PHRASE, BARS_PER_PHRASE)
    after = _mean(low, bar, BARS_PER_PHRASE)
    jump = after - before
    checks["Bass springt an"] = jump >= MIN_BASS_JUMP
    if not checks["Bass springt an"]:
        reasons.append(f"Bass legt nur um {jump:+.2f} zu (mindestens {MIN_BASS_JUMP:.2f} erwartet)")

    held = _mean(energy, bar, SUSTAIN_BARS)
    peak = _mean(energy, bar, BARS_PER_PHRASE // 2)
    checks["Energie haelt"] = held >= peak * 0.80
    if not checks["Energie haelt"]:
        reasons.append(
            f"Energie faellt nach {SUSTAIN_BARS} Takten auf {held/max(peak,1e-9)*100:.0f} % "
            f"des Anfangswerts -- das spricht fuer einen Fill, nicht fuer einen Drop"
        )

    # Gibt es SPAETER noch einen staerkeren Bassanstieg? Dann sass der Anker falsch.
    later_jump = 0.0
    later_bar = None
    for b in range(bar + BARS_PER_PHRASE, n - BARS_PER_PHRASE // 2, BARS_PER_PHRASE):
        j = _mean(low, b, BARS_PER_PHRASE) - _mean(low, b - BARS_PER_PHRASE, BARS_PER_PHRASE)
        if j > later_jump:
            later_jump, later_bar = j, b
    checks["ist der letzte"] = later_jump <= max(jump, MIN_BASS_JUMP) * 0.9
    if not checks["ist der letzte"]:
        reasons.append(
            f"spaeter bei Takt {later_bar} ein staerkerer Bassanstieg ({later_jump:+.2f} "
            f"gegen {jump:+.2f}) -- der letzte Drop koennte dort liegen"
        )

    return Verdict("drop2", _decide(checks), checks, reasons)


def validate_setup(anchors: AnchorSet, features: BarFeatures,
                   lattice: Lattice) -> Verdict | None:
    """
    Der Aufbau zum letzten Drop. Zwei Fragen:

      1. Liegt der Punkt wirklich genau eine Phrase vor dem Drop und auf einem Takt?
      2. Ist dort ueberhaupt ein Aufbau messbar?

    Die zweite ist die wichtige. Herrscht 32 Beats vor dem Drop bereits volle
    Energie, dann ist das kein Aufbau, sondern schon der vorige Drop -- und die
    Lesart war falsch.
    """
    anchor = anchors.get("setup")
    drop = anchors.get("drop2")
    if anchor is None or drop is None:
        return None

    checks: dict[str, bool] = {}
    reasons: list[str] = []

    distance = drop.bar - anchor.bar
    checks["eine Phrase vor dem Drop"] = distance == BARS_PER_PHRASE
    if not checks["eine Phrase vor dem Drop"]:
        reasons.append(f"liegt {distance * 4} Beats vor dem Drop statt 32")

    energy = _col(features, "rms")
    low = _col(features, "low_rms")
    centroid = _col(features, "centroid")

    in_setup = _mean(energy, anchor.bar, BARS_PER_PHRASE)
    in_drop = _mean(energy, drop.bar, BARS_PER_PHRASE)
    # Ein Aufbau ist leiser als der Drop danach. Ist er das nicht, stimmt etwas nicht.
    checks["leiser als der Drop"] = in_setup < in_drop - 0.03
    if not checks["leiser als der Drop"]:
        reasons.append(
            f"dort herrscht bereits volle Energie ({in_setup:.2f} gegen {in_drop:.2f} im Drop) "
            f"-- das ist kein Aufbau"
        )

    # Ein Aufbau zeigt sich als steigender Klangschwerpunkt ODER zurueckgenommenem Bass.
    rise = _mean(centroid, anchor.bar + BARS_PER_PHRASE // 2, BARS_PER_PHRASE // 2) \
        - _mean(centroid, anchor.bar, BARS_PER_PHRASE // 2)
    bass_pulled = _mean(low, anchor.bar, BARS_PER_PHRASE) < _mean(low, drop.bar, BARS_PER_PHRASE) - 0.05
    checks["Aufbau messbar"] = rise > 0.02 or bass_pulled
    if not checks["Aufbau messbar"]:
        reasons.append("weder steigender Klangschwerpunkt noch zurueckgenommener Bass")

    return Verdict("setup", _decide(checks), checks, reasons)


def validate_break1(anchors: AnchorSet, features: BarFeatures,
                    lattice: Lattice) -> Verdict | None:
    """
    Die Stelle fuer Hot Cue A. Drei Fragen:

      1. Liegt sie vor dem letzten Drop?
      2. Auf einem Rasterpunkt zu Drop 1?
      3. Ist dort ein Energietal ODER ein Melodieeinsatz? (Regel 3 nennt beides)
    """
    anchor = anchors.get("break1")
    if anchor is None:
        return None

    drop1 = anchors.get("drop1") or anchors.get("drop2")
    drop2 = anchors.get("drop2")
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    if drop2 is not None:
        checks["vor dem letzten Drop"] = anchor.bar < drop2.bar
        if not checks["vor dem letzten Drop"]:
            reasons.append("liegt nicht vor dem letzten Drop")

    if drop1 is not None:
        offset = (drop1.bar - anchor.bar) % BARS_PER_PHRASE
        checks["auf dem Raster zu Drop 1"] = offset == 0
        if not checks["auf dem Raster zu Drop 1"]:
            reasons.append(f"liegt {offset * 4} Beats neben dem 32er-Raster zu Drop 1")

    # Bewusst NICHT geprueft wird "liegt dort ein Energietal oder ein
    # Melodieeinsatz" -- genau danach wurde der Anker gesucht. Diese Frage noch
    # einmal zu stellen ergibt zwangslaeufig dieselbe Antwort und ist keine
    # zweite Meinung. (Die erste Fassung tat das und kam deshalb bei 100 von 100
    # Ankern zu "bestaetigt" -- eine Pruefung, die nie widerspricht, prueft nicht.)
    #
    # Unabhaengig sind zwei andere Fragen: Ist an dieser Stelle ueberhaupt ein
    # Abschnittswechsel messbar, und war die Wahl eindeutig?
    half = BARS_PER_PHRASE // 2
    novelty = _col(features, "novelty_4", "flux")
    local = _window(novelty, anchor.bar - BARS_PER_PHRASE, 2 * BARS_PER_PHRASE)
    if local.size:
        here = float(novelty[anchor.bar]) if anchor.bar < len(novelty) else 0.0
        checks["Abschnittswechsel messbar"] = here >= float(np.median(local))
        if not checks["Abschnittswechsel messbar"]:
            reasons.append(
                f"an dieser Stelle aendert sich weniger als in der Umgebung "
                f"({here:.2f} gegen Median {float(np.median(local)):.2f})"
            )

    # War die Wahl eindeutig? Die uebrigen Rasterpunkte vor Drop 1 werden mit
    # demselben Mass verglichen -- liegt ein anderer fast gleichauf, war die
    # Entscheidung willkuerlich.
    if drop1 is not None and features.has("rms"):
        energy = _col(features, "rms")
        low = _col(features, "low_rms")
        melodic = _col(features, "melodic_rms")

        def strength(bar: int) -> float:
            trough = max(0.0, _mean(energy, bar - half, half) - _mean(energy, bar, half))
            bass = max(0.0, _mean(low, bar - half, half) - _mean(low, bar, half))
            entry = max(0.0, _mean(melodic, bar, half) - _mean(melodic, bar - half, half))
            return max(trough + bass, entry)

        others = [strength(drop1.bar - k * BARS_PER_PHRASE) for k in range(1, 7)
                  if drop1.bar - k * BARS_PER_PHRASE >= half
                  and drop1.bar - k * BARS_PER_PHRASE != anchor.bar]
        if others:
            mine = strength(anchor.bar)
            runner_up = max(others)
            checks["Wahl eindeutig"] = mine >= runner_up * 1.25
            if not checks["Wahl eindeutig"]:
                reasons.append(
                    f"ein anderer Rasterpunkt ist fast gleich stark "
                    f"({runner_up:.2f} gegen {mine:.2f}) -- die Wahl war knapp"
                )

    return Verdict("break1", _decide(checks), checks, reasons)


def validate_anchors(anchors: AnchorSet, features: BarFeatures, lattice: Lattice) -> dict[str, Verdict]:
    """
    Alle Hot-Cue-Anker unabhaengig pruefen und die Konfidenz anpassen.

    Die Anker werden in place geaendert: `confidence` wird mit dem Faktor des
    Urteils multipliziert, und die Begruendung wandert in `reason`, damit im
    Bericht steht, warum eine Cue ausblieb.
    """
    verdicts: dict[str, Verdict] = {}

    for verdict in (
        validate_drop2(anchors, features),
        validate_setup(anchors, features, lattice),
        validate_break1(anchors, features, lattice),
    ):
        if verdict is None:
            continue
        verdicts[verdict.role] = verdict
        anchor = anchors.get(verdict.role)
        if anchor is None:
            continue
        anchor.confidence = float(np.clip(anchor.confidence * verdict.factor, 0.0, 1.0))
        if verdict.outcome != "unsicher":
            note = "; ".join(verdict.reasons) if verdict.reasons else "alle Pruefungen bestanden"
            anchor.reason = f"{anchor.reason} | Pruefung {verdict.outcome}: {note}"

    return verdicts
