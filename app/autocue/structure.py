"""
Schritt 5 der Analysekette: den Aufbau des ganzen Tracks bestimmen.

Das ist die zentrale Entscheidung. Von Hand entstehen die Cues nacheinander --
erst der letzte Drop, dann 32 Beats davor, dann der Break nach dem Intro. Das
laesst sich nicht nachbauen, weil jede dieser Entscheidungen von den anderen
abhaengt: Am Ende muss ALLES zusammen auf einem Raster liegen und gleichmaessig
verteilt sein (Regeln 4 und 5). Eine Kette von Einzelentscheidungen erzeugt genau
das nicht -- sie erzeugt drei einzeln plausible Punkte, die zusammen nicht passen.

Deshalb laeuft es umgekehrt: erst die Gliederung des ganzen Tracks, daraus dann
alle Positionen gemeinsam.

Zwei Stufen:
  1. Jeder Takt bekommt einen Wert dafuer, wie sehr er nach einer Grenze aussieht.
  2. Ein Optimierungsverfahren sucht die Abschnittsfolge, die den Track insgesamt
     am besten erklaert -- Grenzstaerke, Passung zum Etikett, plausible Laenge,
     sinnvolle Abfolge und Rastertreue werden GEMEINSAM bewertet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.autocue.evidence import Evidence
from app.autocue.types import BARS_PER_PHRASE, BarFeatures, Lattice

# Abschnittsarten. Bewusst wenige: Was das Verfahren nicht sicher trennen kann,
# soll es auch nicht behaupten.
SECTIONS = ("intro", "build", "drop", "break", "sustain", "outro")

# Erwartete Laenge je Abschnitt in Phrasen (8 Takte). (Mittelwert, Streuung) einer
# Log-Normalverteilung -- Laengen sind nach unten begrenzt und nach oben lang
# auslaufend, das bildet eine Normalverteilung schlecht ab.
DURATION_PRIOR = {
    "intro":   (2.0, 0.9),
    "build":   (1.0, 0.45),   # ein Aufbau ist kurz -- meist genau eine Phrase
    "drop":    (2.0, 0.6),
    "break":   (2.0, 0.7),
    "sustain": (2.5, 1.0),
    "outro":   (2.0, 1.0),
}

# Was ein zusaetzlicher Abschnitt kostet.
#
# Das ist der wichtigste Regler des ganzen Verfahrens. Ohne ihn gewinnt immer die
# feinste Zerlegung: Jeder Abschnitt steuert einen positiven Beitrag bei, also
# lohnt sich Aufspalten beliebig weit. Die erste Fassung erzeugte dadurch fuenf
# Drops je Track (echte Tanztracks haben zwei bis drei) und einen 47-Takte-Aufbau.
SECTION_COST = 3.2

# Erlaubte Abfolgen. Das ist die Grammatik eines Tanztracks:
# Intro -> (Aufbau? -> Drop -> Break?)+ -> Outro
ALLOWED_NEXT = {
    "intro":   ("build", "drop", "break", "sustain", "outro"),
    "build":   ("drop",),
    "drop":    ("break", "build", "sustain", "outro"),
    "break":   ("build", "drop", "sustain", "outro"),
    "sustain": ("build", "drop", "break", "outro"),
    "outro":   (),
}

# Kosten eines Rasterbruchs. Gemessen (M-002) braucht ein Track im Mittel 2,6
# Phasenabschnitte -- Bruechen ist also normal, aber sie muessen sich lohnen.
PHASE_RESET_PENALTY = 1.2
MAX_PHASE_RESETS = 3

# Wie stark Abschnittslaengen belohnt werden, die Vielfache einer Phrase sind.
LATTICE_BONUS = 0.8


@dataclass
class StructureParams:
    """
    Die Stellschrauben des Strukturfits.

    Bis zur ersten Messung gegen von Hand gesetzte Cues waren das geschaetzte
    Werte -- es gab nichts, woran man sie haette pruefen koennen. Mit dem
    Gold-Set lassen sie sich kalibrieren; `tools/eval/calibrate.py` tut das.
    Die Voreinstellungen hier sind das Ergebnis dieser Kalibrierung.
    """
    # Diese Werte stammen aus der Kalibrierung gegen das Gold-Set
    # (tools/eval/calibrate.py, documentation.md M-011). Sie sind NICHT geraten,
    # aber auch nicht das nachweisbare Optimum: Die Zielflaeche ist flach, und
    # eine koordinatenweise Suche landet je nach Gitter an verschiedenen Stellen.
    # Genommen wurde der Satz, der sich am zurueckgehaltenen Teil am besten
    # bewaehrt hat -- nicht der mit dem besten Wert beim Kalibrieren.
    section_cost: float = 3.2
    duration_weight: float = 18.0   # Abschnittslaengen streng an die Erwartung binden
    boundary_weight: float = 10.0   # Grenzen dort, wo die Merkmale es hergeben
    lattice_bonus: float = 0.8
    sharpness: float = 1.6          # wie entschieden zwischen den Etiketten getrennt wird
    evidence_weight: float = 2.2    # Gewicht der Hinweise aus Schritt 4
    duration_prior: dict = field(default_factory=lambda: dict(DURATION_PRIOR))

    def replace(self, **kw) -> "StructureParams":
        from dataclasses import replace as _replace
        return _replace(self, **kw)


DEFAULT_PARAMS = StructureParams()


@dataclass
class Section:
    """Ein Abschnitt des Tracks."""
    label: str
    start_bar: int
    end_bar: int
    score: float = 0.0
    confidence: float = 0.0

    @property
    def n_bars(self) -> int:
        return self.end_bar - self.start_bar

    @property
    def n_phrases(self) -> float:
        return self.n_bars / BARS_PER_PHRASE


@dataclass
class StructureModel:
    """Das Ergebnis des Strukturfits."""
    sections: list[Section]
    boundary_score: np.ndarray          # Grenzwert je Takt (Stufe 1)
    total_score: float = 0.0
    phase_resets: int = 0
    quality: float = 0.0                # 0..1, siehe structure_quality()
    quality_parts: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def label_at(self, bar: int) -> str:
        for s in self.sections:
            if s.start_bar <= bar < s.end_bar:
                return s.label
        return "sustain"

    def sections_of(self, label: str) -> list[Section]:
        return [s for s in self.sections if s.label == label]


# --- Stufe 1: Grenzkandidaten ---

def boundary_scores(features: BarFeatures, evidence: Evidence) -> np.ndarray:
    """
    Wie sehr sieht jeder Takt nach einer Abschnittsgrenze aus?

    Die Gewichte folgen der gemessenen Trennschaerfe (documentation.md, M-004):
    rms_d1 (+0,172), impact_score (+0,161), novelty_4 (+0,154) und low_d1 (+0,117)
    sind die verlaesslichen Grenzanzeiger.
    """
    n = features.n_bars
    score = np.zeros(n)

    weights = {
        "rms_d1": 1.0,
        "impact_score": 0.95,
        "novelty_4": 0.90,
        "impact_relative": 0.85,
        "low_d1": 0.70,
        "novelty_8": 0.45,
    }
    for name, weight in weights.items():
        if features.has(name):
            values = features[name]
            score += weight * np.clip(values, 0, None) / (np.abs(values).max() + 1e-9)

    if score.max() > 0:
        score /= score.max()

    # Evidenz aus Schritt 4 dazu. Sie ist unabhaengig vom Audio-Merkmalsweg,
    # deshalb zaehlt sie kraeftig mit.
    if evidence.n_bars == n:
        score = 0.55 * score + 0.45 * evidence.boundary

    return score


# --- Stufe 2: die Gliederung ---

def _duration_logprob(label: str, n_bars: int, prior: dict | None = None) -> float:
    """Wie plausibel ist diese Laenge fuer diesen Abschnitt?"""
    mean, sigma = (prior or DURATION_PRIOR)[label]
    phrases = max(n_bars / BARS_PER_PHRASE, 0.125)
    return -((math.log(phrases) - math.log(mean)) ** 2) / (2 * sigma ** 2)


def label_loglik(features: BarFeatures, evidence: Evidence,
                 params: "StructureParams" | None = None) -> np.ndarray:
    """
    Log-Wahrscheinlichkeit je Takt und Etikett -- Form (n_bars, len(SECTIONS)).

    Das ist der Kern der Bewertung, und die Normierung darin ist entscheidend.

    Eine erste Fassung gab je Abschnitt einen Passungswert in [0, 1.3] zurueck und
    rechnete `laenge * passung`. Weil dieser Wert nie negativ wird, war ein
    laengerer Abschnitt IMMER besser als ein kuerzerer -- unabhaengig davon, ob er
    passt. Das Ergebnis war ein Track, dessen erste zehn Phrasen als ein einziger
    "Drop" gelesen wurden, Intro eingeschlossen.

    Deshalb wird je Takt ueber alle Etiketten normiert (Softmax). Ein Takt, der
    besser zu einem anderen Etikett passt, liefert dann einen NEGATIVEN Beitrag,
    und ein Abschnitt waechst nur so lange, wie er tatsaechlich passt.
    """
    params = params or DEFAULT_PARAMS
    n = features.n_bars

    def col(name: str, default: float = 0.5) -> np.ndarray:
        return features[name] if features.has(name) else np.full(n, default)

    energy = col("rms")
    low = col("low_rms", 0.5)
    sustain = col("sustain_score")
    riser = col("riser_score", 0.0)
    centroid = col("centroid")
    position = np.linspace(0.0, 1.0, n) if n > 1 else np.zeros(n)

    raw = np.zeros((n, len(SECTIONS)))
    for k, label in enumerate(SECTIONS):
        if label == "drop":
            # Laut, basshaltig UND die Energie haelt. Der letzte Teil ist der
            # wichtigste Einzelfilter gegen Fake Drops.
            v = 1.4 * energy + 1.0 * low + 1.2 * sustain
        elif label == "break":
            v = 1.6 * (1 - energy) + 1.0 * (1 - low)
        elif label == "build":
            v = 1.5 * riser + 0.8 * centroid + 0.6 * (1 - low)
        elif label == "intro":
            v = 1.2 * (1 - energy) + 1.8 * (1 - position) + 0.4 * (1 - low)
        elif label == "outro":
            v = 1.0 * (1 - energy) + 2.0 * position
        else:  # sustain
            v = 1.1 + 0.3 * (1 - np.minimum(1.0, np.abs(energy - 0.55) * 2))
        raw[:, k] = v

    # Hinweise aus Schritt 4 dazu (Rekordbox-Phrasen sagen "hier ist ein Drop").
    if evidence.n_bars == n:
        for k, label in enumerate(SECTIONS):
            hint = {"drop": evidence.drop_hint, "break": evidence.break_hint,
                    "build": evidence.build_hint}.get(label)
            if hint is not None:
                raw[:, k] += params.evidence_weight * hint

    # Softmax je Takt -> echte Log-Wahrscheinlichkeiten, immer negativ.
    raw *= params.sharpness
    raw -= raw.max(axis=1, keepdims=True)
    loglik = raw - np.log(np.exp(raw).sum(axis=1, keepdims=True))
    return loglik


def _lattice_bonus(n_bars: int, weight: float = LATTICE_BONUS) -> float:
    """Belohnt Abschnittslaengen, die Vielfache einer Phrase sind (Regel 5)."""
    if n_bars <= 0:
        return 0.0
    rest = n_bars % BARS_PER_PHRASE
    off = min(rest, BARS_PER_PHRASE - rest)
    return weight * (1.0 - off / (BARS_PER_PHRASE / 2))


def fit_structure(features: BarFeatures, evidence: Evidence,
                  lattice: Lattice,
                  candidates: np.ndarray | None = None,
                  max_sections: int = 14,
                  params: "StructureParams" | None = None) -> StructureModel:
    """
    Die beste Abschnittsfolge suchen.

    Bewertet wird gleichzeitig:
      - wie stark die gewaehlten Grenzen sind
      - wie gut die Merkmale zum Etikett passen
      - wie plausibel die Abschnittslaenge ist
      - ob die Abfolge musikalisch Sinn ergibt
      - ob die Laengen Vielfache einer Phrase sind

    Genau weil das GEMEINSAM geschieht, fallen die schwierigen Faelle ohne
    Sonderregel heraus: Ein Fake Drop verliert am niedrigen sustain_score, ein
    16-Beat-Intro ist durch die Laengenerwartung erlaubt, und ein Track ohne
    erkennbare Gliederung erzeugt einen flachen Score -- also niedrige Konfidenz
    und am Ende keine Hot Cues.
    """
    params = params or DEFAULT_PARAMS
    n = features.n_bars
    if n < BARS_PER_PHRASE:
        return StructureModel(sections=[], boundary_score=np.zeros(n),
                              notes=["Track zu kurz fuer eine Gliederung"])

    bscore = boundary_scores(features, evidence) if candidates is None else candidates

    # Log-Wahrscheinlichkeiten einmal vorberechnen und kumulieren, damit die
    # Summe ueber einen beliebigen Taktbereich eine Subtraktion ist.
    loglik = label_loglik(features, evidence, params)
    cum = np.vstack([np.zeros((1, loglik.shape[1])), np.cumsum(loglik, axis=0)])
    label_index = {name: i for i, name in enumerate(SECTIONS)}

    # Kandidatentakte: die staerksten Grenzen plus alle Rasterpunkte. Ohne die
    # Rasterpunkte koennte eine Gliederung entstehen, die nirgends aufs Raster passt.
    strong = set(np.flatnonzero(bscore > np.percentile(bscore, 80)).tolist())
    strong |= set(range(0, n, BARS_PER_PHRASE))
    strong |= {0, n}
    bars = sorted(b for b in strong if 0 <= b <= n)

    # Dynamische Programmierung ueber (Kandidatenindex, Etikett).
    # best[i][label] = (Score, Vorgaenger)
    neg = -1e18
    best: list[dict[str, tuple[float, tuple | None]]] = [dict() for _ in bars]

    for start_label in ("intro", "build", "drop", "sustain"):
        best[0][start_label] = (0.0 if start_label == "intro" else -1.5, None)

    for i in range(len(bars) - 1):
        if not best[i]:
            continue
        for j in range(i + 1, len(bars)):
            start, end = bars[i], bars[j]
            length = end - start
            if length < 2:
                continue
            if length > n:
                break

            for label, (prev_score, _bp) in list(best[i].items()):
                for nxt in SECTIONS:
                    # Am Trackanfang steht das Etikett des ersten Abschnitts noch
                    # zur Wahl; danach gilt die Grammatik.
                    if i == 0:
                        if nxt != label:
                            continue
                    elif nxt not in ALLOWED_NEXT[label]:
                        continue

                    # Summe der Log-Wahrscheinlichkeiten ueber den Bereich.
                    # Takte, die besser zu einem anderen Etikett passen, ziehen
                    # den Wert nach unten -- ein Abschnitt waechst also nur,
                    # solange er wirklich passt.
                    k = label_index[nxt]
                    fit_sum = float(cum[min(end, n), k] - cum[start, k])
                    step = (
                        fit_sum
                        + params.duration_weight * _duration_logprob(nxt, length,
                                                                    params.duration_prior)
                        + _lattice_bonus(length, params.lattice_bonus)
                        + params.boundary_weight * float(bscore[start] if start < n else 0.0)
                        - params.section_cost
                    )
                    total = prev_score + step
                    have = best[j].get(nxt)
                    if have is None or total > have[0]:
                        best[j][nxt] = (total, (i, label))

    # Beste Kette, die am Trackende landet
    last = len(bars) - 1
    if not best[last]:
        return StructureModel(sections=[], boundary_score=bscore,
                              notes=["keine gueltige Gliederung gefunden"])

    end_label = max(best[last], key=lambda k: best[last][k][0])
    total_score = best[last][end_label][0]

    chain = []
    idx, label = last, end_label
    while True:
        entry = best[idx][label]
        back = entry[1]
        if back is None:
            break
        pi, plabel = back
        chain.append(Section(label=label, start_bar=bars[pi], end_bar=bars[idx]))
        idx, label = pi, plabel
    chain.reverse()

    if len(chain) > max_sections:
        # Zu feingliedrig: die schwaechsten Grenzen wieder zusammenfassen.
        chain = _merge_weakest(chain, bscore, max_sections)

    model = StructureModel(
        sections=chain,
        boundary_score=bscore,
        total_score=float(total_score),
    )
    model.phase_resets = _count_phase_resets(chain)
    model.quality, model.quality_parts = structure_quality(model, bscore)
    if model.phase_resets > MAX_PHASE_RESETS:
        model.notes.append(
            f"{model.phase_resets} Rasterbrueche -- mehr als die {MAX_PHASE_RESETS} "
            f"plausiblen, Gliederung unsicher"
        )
    return model


def structure_quality(model: StructureModel,
                      bscore: np.ndarray) -> tuple[float, dict[str, float]]:
    """
    Wie gut geformt ist diese Gliederung? 0 = beliebig, 1 = mustergueltig.

    Bewusst KEINE Wahrscheinlichkeit. Die naheliegende Groesse waere der Abstand
    zur zweitbesten Gliederung, aber der laesst sich aus einem
    Dynamic-Programming-Lauf nicht ehrlich ablesen: Die verworfenen Teilpfade
    unterscheiden sich meist nur in einer einzigen Grenze und liegen deshalb
    immer knapp darunter -- eine erste Fassung lieferte durchgehend 0,01.

    Stattdessen wird gemessen, was eine brauchbare Gliederung ausmacht:

      Grenzstaerke  Sitzen die gewaehlten Grenzen auf auffaelligen Takten,
                    oder irgendwo?
      Rastertreue   Sind die Abschnittslaengen Vielfache einer Phrase? (Regel 5)
      Rasterbrueche Wenige sind normal (gemessen 2,6), viele sind ein Alarmsignal.
      Bauform       Gibt es ueberhaupt Drops, und sind es plausibel viele?

    Ein monotoner Techno-Track ohne erkennbare Gliederung bekommt hier von selbst
    einen niedrigen Wert -- und damit spaeter keine Hot Cues. Genau so ist es
    gewollt: lieber keine Cue als eine falsche.
    """
    if not model.sections:
        return 0.0, {}

    starts = [s.start_bar for s in model.sections[1:]]
    if starts:
        chosen = float(np.mean([bscore[b] for b in starts if 0 <= b < len(bscore)]))
        typical = float(np.mean(bscore)) + 1e-9
        strength = float(np.clip((chosen / typical - 1.0) / 1.2, 0.0, 1.0))
    else:
        strength = 0.0

    on_lattice = [s for s in model.sections if s.n_bars % BARS_PER_PHRASE == 0]
    lattice_fit = len(on_lattice) / len(model.sections)

    reset_fit = float(np.clip(1.0 - max(0, model.phase_resets - 1) / 4.0, 0.0, 1.0))

    n_drops = len(model.sections_of("drop"))
    # Zwei bis drei Drops sind die Norm; keiner oder sehr viele sind verdaechtig.
    shape_fit = {0: 0.0, 1: 0.6, 2: 1.0, 3: 1.0, 4: 0.7, 5: 0.4}.get(n_drops, 0.2)

    parts = {
        "Grenzstaerke": strength,
        "Rastertreue": lattice_fit,
        "Rasterbrueche": reset_fit,
        "Bauform": shape_fit,
    }
    quality = 0.30 * strength + 0.30 * lattice_fit + 0.15 * reset_fit + 0.25 * shape_fit
    return float(np.clip(quality, 0.0, 1.0)), parts


def _merge_weakest(sections: list[Section], bscore: np.ndarray,
                   target: int) -> list[Section]:
    """Nachbarabschnitte an den schwaechsten Grenzen verschmelzen."""
    out = list(sections)
    while len(out) > target:
        weakest, best_at = None, 1e18
        for i in range(1, len(out)):
            value = float(bscore[out[i].start_bar])
            if value < best_at:
                best_at, weakest = value, i
        if weakest is None:
            break
        merged = Section(label=out[weakest - 1].label,
                         start_bar=out[weakest - 1].start_bar,
                         end_bar=out[weakest].end_bar)
        out[weakest - 1:weakest + 1] = [merged]
    return out


def _count_phase_resets(sections: list[Section]) -> int:
    """
    Wie oft bricht das 32er-Raster?

    Ein Bruch ist kein Fehler -- Regel 5 nennt ihn ausdruecklich (kurze Intros,
    Fake Drops, rasterbrechende Drops). Gemessen (M-002) braucht ein Track im
    Mittel 2,6 Phasenabschnitte. Viel mehr deutet auf eine schlechte Gliederung.
    """
    if not sections:
        return 0
    resets = 0
    phase = sections[0].start_bar % BARS_PER_PHRASE
    for s in sections[1:]:
        p = s.start_bar % BARS_PER_PHRASE
        if p != phase:
            resets += 1
            phase = p
    return resets
