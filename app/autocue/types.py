"""
Datentypen der Auto-Cue-Kette (portiert aus rekordbox-autocue-tool).

Bewusst schlank gehalten: Jeder Schritt gibt genau einen dieser Typen weiter, und
kein spaeterer Schritt greift auf die Rohdaten eines frueheren zurueck. Das haelt
die Kette ueberpruefbar -- wer einen Fehler sucht, muss nur ein Zwischenergebnis
ansehen, nicht die ganze Kette nachvollziehen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Ein Takt hat vier Beats, eine Phrase acht Takte.
BEATS_PER_BAR = 4
BARS_PER_PHRASE = 8
PHRASE_BEATS = BEATS_PER_BAR * BARS_PER_PHRASE  # 32


@dataclass
class BeatgridReport:
    """
    Ergebnis der Beatgrid-Vorpruefung.

    Das Beatgrid von Rekordbox ist die Bezugsgroesse fuer alles Weitere -- Takte,
    Raster, Cue-Positionen. Stimmt es nicht, sind nicht einzelne Cues falsch,
    sondern alle gleichzeitig und gleichmaessig. Das sieht sauber aus und faellt
    erst beim Auflegen auf. Deshalb wird es geprueft statt vorausgesetzt.
    """
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    # Kennzahlen, auch wenn die Pruefung durchfaellt -- fuer die Kalibrierung
    n_beats: int = 0
    bpm: float = 0.0
    bpm_spread: float = 0.0          # Streuung der BPM ueber die Beats
    interval_error_pct: float = 0.0  # Abweichung der Beat-Abstaende von der BPM
    coverage_pct: float = 0.0        # Anteil der Trackdauer, den das Gitter abdeckt
    first_downbeat_idx: int = 0
    bar_cycle_breaks: int = 0        # Stellen, an denen die Taktzaehlung springt

    # Abschnitte konstanten Tempos als (start_beat, end_beat, bpm).
    # Mehr als einer ist KEIN Fehler -- Mashups wechseln das Tempo, und Rekordbox
    # bildet das korrekt ab. Die Wechselstellen sind spaeter Strukturhinweise.
    tempo_sections: list[tuple[int, int, float]] = field(default_factory=list)

    @property
    def is_dynamic(self) -> bool:
        """Wechselt der Track das Tempo? Erlaubt, aber gut zu wissen."""
        return len(self.tempo_sections) > 1

    @property
    def tempo_change_beats(self) -> list[int]:
        """Beat-Indizes der Tempowechsel -- Kandidaten fuer Abschnittsgrenzen."""
        return [start for start, _end, _bpm in self.tempo_sections[1:]]

    @property
    def summary(self) -> str:
        if self.ok:
            extra = f", {len(self.tempo_sections)} Tempoabschnitte" if self.is_dynamic else ""
            return f"Beatgrid ok ({self.n_beats} Beats, {self.bpm:.2f} BPM{extra})"
        return "Beatgrid pruefen: " + "; ".join(self.reasons)


@dataclass
class Lattice:
    """
    Takte und Phrasenraster eines Tracks, abgeleitet aus dem Beatgrid.

    Es wird nichts geschaetzt und nichts korrigiert: Die Takte sind die Takte von
    Rekordbox. Die einzige offene Groesse ist die Rasterphase -- welcher Takt eine
    Phrase beginnt -- und die ergibt sich spaeter aus den Strukturankern, nicht
    aus dem Audio.
    """
    grid: object                  # BeatGrid (unten)
    bar_start_beats: np.ndarray   # Beat-Index, an dem jeder Takt beginnt
    bar_times: np.ndarray         # Zeitstempel jedes Taktanfangs
    first_downbeat_idx: int       # Beat-Index des ersten Downbeats
    report: BeatgridReport

    @property
    def n_bars(self) -> int:
        return len(self.bar_start_beats)

    @property
    def bpm(self) -> float:
        return float(self.grid.bpm)

    @property
    def beat_duration(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    def bar_at_time(self, t: float) -> int:
        """Index des Takts, in den ein Zeitpunkt faellt."""
        return int(np.clip(np.searchsorted(self.bar_times, t, side="right") - 1,
                           0, self.n_bars - 1))

    def nearest_bar(self, t: float) -> int:
        """Index des naechstgelegenen Taktanfangs."""
        return int(np.argmin(np.abs(self.bar_times - t)))

    def time_of_bar(self, bar: int) -> float:
        return float(self.bar_times[int(np.clip(bar, 0, self.n_bars - 1))])

    def beats_between(self, bar_a: int, bar_b: int) -> int:
        """Abstand zweier Takte in Beats."""
        a = int(np.clip(bar_a, 0, self.n_bars - 1))
        b = int(np.clip(bar_b, 0, self.n_bars - 1))
        return int(self.bar_start_beats[b] - self.bar_start_beats[a])

    def phrase_bars(self, phase: int = 0, step_bars: int = BARS_PER_PHRASE) -> np.ndarray:
        """
        Takte, die bei gegebener Phase einen Phrasenanfang bilden.

        phase = 0 heisst: das Raster beginnt beim ersten Takt. Eine andere Phase
        verschiebt es -- das ist der Fall, den Regel 5 beschreibt, wenn ein Drop
        das Raster aufbricht.
        """
        return np.arange(phase % step_bars, self.n_bars, step_bars)


@dataclass
class Zone:
    """
    Ein Abschnitt zwischen zwei Ankern, in dem ein eigener Rasterlauf gilt.

    Regel 5: Intro bis Break, Drop bis Break 2, Drop 2 bis Outro sind je ein
    eigener Lauf. Innerhalb einer Zone ist die Phase konstant.
    """
    name: str
    start_bar: int
    end_bar: int
    phase: int = 0
    step_bars: int = BARS_PER_PHRASE

    @property
    def n_bars(self) -> int:
        return max(0, self.end_bar - self.start_bar)

    @property
    def n_beats(self) -> int:
        return self.n_bars * BEATS_PER_BAR


@dataclass
class BarFeatures:
    """
    Merkmalstabelle: eine Zeile je Takt, eine Spalte je Merkmal.

    Das Audio wird genau einmal in ein Spektrogramm ueberfuehrt; alles hier
    entsteht daraus. Die Spaltennamen sind mitgefuehrt, damit spaetere Schritte
    Merkmale beim Namen ansprechen koennen statt ueber Indizes -- Index-Arithmetik
    war in der Vorgaengerversion eine wiederkehrende Fehlerquelle.
    """
    matrix: np.ndarray            # (n_bars, n_features)
    names: list[str]
    sample_rate: int

    def __post_init__(self) -> None:
        if self.matrix.shape[1] != len(self.names):
            raise ValueError(
                f"Merkmalstabelle passt nicht zu den Namen: "
                f"{self.matrix.shape[1]} Spalten, {len(self.names)} Namen"
            )

    @property
    def n_bars(self) -> int:
        return int(self.matrix.shape[0])

    def __getitem__(self, name: str) -> np.ndarray:
        """Eine Merkmalsspalte beim Namen holen."""
        try:
            return self.matrix[:, self.names.index(name)]
        except ValueError:
            raise KeyError(
                f"Unbekanntes Merkmal {name!r}. Vorhanden: {', '.join(self.names)}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self.names


@dataclass
class BeatGrid:
    """
    Das Beatgrid eines Tracks, wie es die Kette braucht.

    Im Original kam es aus Rekordbox' PQTZ-Tag; in TrackTab ist es das Grid, das
    im DJ-Editor gilt (siehe autocue.analyse()). Zeiten auf TrackTabs Zeitachse.
    """
    times: np.ndarray         # Sekunden je Beat
    beat_numbers: np.ndarray  # Beat im Takt (1..4)
    bpms: np.ndarray          # Tempo je Beat
    bpm: float                # Median-Tempo

    @property
    def count(self) -> int:
        return int(len(self.times))


@dataclass
class CuePoint:
    """Ein gesetzter Cue, bevor er ins TrackTab-Format umgesetzt wird."""
    time_sec: float
    kind: int                 # 0 = Memory, 1..8 = Hot A..H
    name: str
    comment: str
    color: object = None      # [r, g, b] oder None (keine Farbe)
    priority: int = 99        # Memory-Budget: niedrig = wichtig
    role: str = ""            # Rolle oder Slot-ID ("hot_b", "memory_3")
