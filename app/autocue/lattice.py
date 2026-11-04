"""
Schritt 1 der Analysekette: Beatgrid pruefen, Takte und Raster aufspannen.

Das 32-Beat-Raster sind acht Takte auf dem Beatgrid. Takte werden NICHT aus dem
Audio geschaetzt -- sie kommen unveraendert aus dem Grid, das in TrackTab gilt
(im Original: Rekordbox' PQTZ-Tag). Es gibt kein eigenes Beat-Tracking und keine
Downbeat-Korrektur. Das Grid hat recht, oder der Track wird uebersprungen.

Der Grund fuer diese Haerte: Ein Tool, das das Beatgrid heimlich anders auslegt als
Rekordbox, erzeugt Cues, die in Rekordbox falsch sitzen. Und ein falsches Beatgrid
verschiebt nicht eine Cue, sondern alle gleichmaessig -- das sieht sauber aus und
faellt erst beim Auflegen auf.
"""
from __future__ import annotations

import numpy as np

from app.autocue.types import BARS_PER_PHRASE, BEATS_PER_BAR, BeatGrid, BeatgridReport, Lattice

# Schwellen je Strenge. Kalibriert im Autocue-Projekt (tools/check_beatgrids.py) --
# siehe documentation.md, Kapitel 6.
# Fest: Die Pipeline prueft immer streng. Die uebrigen Stufen bleiben fuer die
# Kalibrierung im Autocue-Projekt.
BEATGRID_STRICTNESS = "streng"

STRICTNESS = {
    "streng": {
        "min_beats": 64,
        "max_interval_error": 1.5,   # Abweichung vom LOKALEN Tempo, in Prozent
        "min_coverage": 0.90,        # Anteil der Trackdauer mit Gitter
        "max_cycle_breaks": 0,
        "max_tempo_sections": 4,
    },
    "normal": {
        "min_beats": 32,
        "max_interval_error": 5.0,
        "min_coverage": 0.75,
        "max_cycle_breaks": 2,
        "max_tempo_sections": 12,
    },
    "locker": {
        "min_beats": 32,
        "max_interval_error": 15.0,
        "min_coverage": 0.50,
        "max_cycle_breaks": 8,
        "max_tempo_sections": 64,
    },
}


def tempo_sections(grid, min_change: float = 1.0, min_beats: int = 8) -> list[tuple[int, int, float]]:
    """
    Zerlegt das Beatgrid in Abschnitte konstanten Tempos.

    Ein Tempowechsel ist KEIN Fehler. Mashups und Bootlegs wechseln regelmaessig
    das Tempo, und Rekordbox bildet das mit einem dynamischen Beatgrid korrekt ab
    ("Summerthing! - Davey B 100-130 Transition" traegt es im Titel). Fuer unsere
    Kette ist das ohnehin belanglos, weil in Beat-Indizes gerechnet wird: Ein Takt
    sind vier Beats, eine Phrase acht Takte -- ob ein Beat 480 oder 429 ms dauert,
    aendert daran nichts.

    Im Gegenteil: Die Wechselstelle ist fast immer eine Abschnittsgrenze und damit
    ein wertvoller Hinweis fuer die Strukturerkennung.

    Returns:
        Liste von (start_beat, end_beat, bpm), aufsteigend.
    """
    bpms = np.asarray(getattr(grid, "bpms", []), dtype=float)
    if bpms.size == 0:
        return []

    sections: list[tuple[int, int, float]] = []
    start = 0
    for i in range(1, len(bpms)):
        if abs(bpms[i] - bpms[start]) > min_change:
            if i - start >= min_beats:
                sections.append((start, i, float(np.median(bpms[start:i]))))
                start = i
            else:
                start = i  # zu kurz, um als eigener Abschnitt zu zaehlen
    if len(bpms) - start >= min_beats or not sections:
        sections.append((start, len(bpms), float(np.median(bpms[start:]))))
    return sections


def _bar_cycle_breaks(beat_numbers: np.ndarray) -> int:
    """
    Zaehlt Stellen, an denen die Taktzaehlung springt.

    Erwartet wird 1,2,3,4,1,2,3,4,... Jede andere Fortsetzung ist ein Bruch.
    Ein Taktwechsel mitten im Track (etwa ein eingeschobener 2/4-Takt) erzeugt
    hier genau einen Bruch -- das ist gewollt, weil es das Raster tatsaechlich
    verschiebt.
    """
    if len(beat_numbers) < 2:
        return 0
    expected = np.where(beat_numbers[:-1] >= BEATS_PER_BAR, 1, beat_numbers[:-1] + 1)
    return int(np.count_nonzero(beat_numbers[1:] != expected))


def check_beatgrid(grid, duration_sec: float | None = None,
                   strictness: str = "normal") -> BeatgridReport:
    """
    Plausibilitaetspruefung des Beatgrids.

    Laeuft VOR dem Dekodieren des Audios -- ein Track, dessen Ergebnis ohnehin
    unbrauchbar waere, kostet so keine Analysezeit.

    Args:
        grid:         BeatGrid
        duration_sec: Tracklaenge. Ohne sie entfaellt
                      die Abdeckungspruefung (dann kann nicht festgestellt werden,
                      ob das Gitter vorzeitig endet).
        strictness:   "streng" | "normal" | "locker"

    Returns:
        BeatgridReport -- `ok` sagt, ob weitergerechnet wird; `reasons` nennt im
        Klartext, was nicht stimmt.
    """
    limits = STRICTNESS.get(strictness, STRICTNESS["normal"])
    report = BeatgridReport(ok=True)

    # --- Existenz ---
    if grid is None or getattr(grid, "count", 0) == 0:
        report.ok = False
        report.checks["existenz"] = False
        report.reasons.append("kein Beatgrid vorhanden")
        return report

    times = np.asarray(grid.times, dtype=float)
    beat_numbers = np.asarray(grid.beat_numbers)
    report.n_beats = int(grid.count)
    report.bpm = float(grid.bpm)

    report.checks["existenz"] = report.n_beats >= limits["min_beats"]
    if not report.checks["existenz"]:
        report.ok = False
        report.reasons.append(
            f"nur {report.n_beats} Beats (mindestens {limits['min_beats']} noetig)"
        )
        return report  # alles Weitere waere sinnlos

    # --- Monotonie: Beats muessen zeitlich aufsteigen ---
    report.checks["monoton"] = bool(np.all(np.diff(times) > 0))
    if not report.checks["monoton"]:
        report.ok = False
        report.reasons.append("Beat-Zeitstempel laufen nicht durchgehend aufwaerts")

    # --- Taktzyklus 1,2,3,4,1,... ---
    report.bar_cycle_breaks = _bar_cycle_breaks(beat_numbers)
    report.checks["taktzyklus"] = report.bar_cycle_breaks <= limits["max_cycle_breaks"]
    if not report.checks["taktzyklus"]:
        report.ok = False
        report.reasons.append(
            f"Taktzaehlung springt {report.bar_cycle_breaks}x "
            f"(hoechstens {limits['max_cycle_breaks']} erlaubt)"
        )

    downbeats = np.flatnonzero(beat_numbers == 1)
    report.first_downbeat_idx = int(downbeats[0]) if downbeats.size else 0
    report.checks["downbeat_vorhanden"] = downbeats.size > 0
    if not report.checks["downbeat_vorhanden"]:
        report.ok = False
        report.reasons.append("kein einziger Downbeat markiert")

    # --- Tempoabschnitte: dynamische Gitter sind erlaubt, nur nicht beliebig viele ---
    sections = tempo_sections(grid)
    report.tempo_sections = sections
    bpms_all = np.asarray(grid.bpms, dtype=float)
    finite = bpms_all[np.isfinite(bpms_all) & (bpms_all > 0)]
    report.bpm_spread = float(np.std(finite)) if finite.size else 0.0

    report.checks["tempoabschnitte"] = len(sections) <= limits["max_tempo_sections"]
    if not report.checks["tempoabschnitte"]:
        report.ok = False
        report.reasons.append(
            f"{len(sections)} Tempoabschnitte "
            f"(hoechstens {limits['max_tempo_sections']} plausibel)"
        )

    # --- Beat-Abstaende passen zum LOKALEN Tempo ---
    # Gegen die globale BPM zu pruefen wuerde jedes dynamische Gitter verwerfen.
    # Was wirklich auf ein kaputtes Gitter deutet, ist ein Abstand, der nicht
    # einmal zum Tempo seines eigenen Abschnitts passt.
    errors = []
    for start, end, bpm in sections:
        if bpm <= 0 or end - start < 2:
            continue
        expected = 60.0 / bpm
        iv = np.diff(times[start:end])
        if iv.size:
            errors.append(np.abs(iv - expected) / expected)
    if errors:
        report.interval_error_pct = float(np.median(np.concatenate(errors)) * 100)
    report.checks["abstaende"] = report.interval_error_pct <= limits["max_interval_error"]
    if not report.checks["abstaende"]:
        report.ok = False
        report.reasons.append(
            f"Beat-Abstaende weichen im Median {report.interval_error_pct:.1f} % vom "
            f"Tempo des eigenen Abschnitts ab "
            f"(hoechstens {limits['max_interval_error']} % erlaubt)"
        )

    # --- Abdeckung: reicht das Gitter ueber den ganzen Track? ---
    if duration_sec and duration_sec > 0:
        covered = float(times[-1] - times[0])
        report.coverage_pct = covered / duration_sec * 100
        report.checks["abdeckung"] = (covered / duration_sec) >= limits["min_coverage"]
        if not report.checks["abdeckung"]:
            report.ok = False
            report.reasons.append(
                f"Gitter deckt nur {report.coverage_pct:.0f} % der Tracklaenge ab "
                f"(mindestens {limits['min_coverage']*100:.0f} % noetig)"
            )
    else:
        report.checks["abdeckung"] = True  # ohne Laengenangabe nicht pruefbar

    return report


def build_lattice(grid, duration_sec: float | None = None,
                  strictness: str = "normal",
                  enforce: bool = True) -> Lattice | None:
    """
    Spannt Takte und Raster auf -- nach bestandener Vorpruefung.

    Args:
        enforce: False laesst die Pruefung nur melden statt blockieren. Wird fuer
                 die Kalibrierung der Schwellen gebraucht (Autocue-Projekt).

    Returns:
        Lattice, oder None wenn die Pruefung durchfaellt und `enforce` gilt.
    """
    report = check_beatgrid(grid, duration_sec, strictness)
    if enforce and not report.ok:
        return None
    if grid is None or getattr(grid, "count", 0) == 0:
        return None

    beat_numbers = np.asarray(grid.beat_numbers)
    times = np.asarray(grid.times, dtype=float)

    # Taktanfaenge sind die Downbeats. Faellt die Markierung aus (nur bei
    # "locker" erreichbar), wird ab dem ersten Beat in Vierergruppen gezaehlt.
    bar_starts = np.flatnonzero(beat_numbers == 1)
    if bar_starts.size == 0:
        bar_starts = np.arange(0, len(times), BEATS_PER_BAR)

    return Lattice(
        grid=grid,
        bar_start_beats=bar_starts,
        bar_times=times[bar_starts],
        first_downbeat_idx=int(bar_starts[0]),
        report=report,
    )


def snap_to_lattice(lattice: Lattice, time_sec: float,
                    phase: int = 0, step_bars: int = BARS_PER_PHRASE,
                    max_drift_bars: int | None = None) -> float | None:
    """
    Rastet einen Zeitpunkt auf den naechsten Phrasenanfang ein.

    Args:
        max_drift_bars: Liegt der naechste Rasterpunkt weiter entfernt, wird None
                        zurueckgegeben. So laesst sich unterscheiden zwischen
                        "sitzt knapp daneben, einrasten" und "gehoert gar nicht
                        aufs Raster, lieber verwerfen".
    """
    phrase_bars = lattice.phrase_bars(phase, step_bars)
    if phrase_bars.size == 0:
        return None
    candidate_times = lattice.bar_times[phrase_bars]
    idx = int(np.argmin(np.abs(candidate_times - time_sec)))
    bar = int(phrase_bars[idx])

    if max_drift_bars is not None:
        nearest = lattice.nearest_bar(time_sec)
        if abs(nearest - bar) > max_drift_bars:
            return None

    return float(lattice.bar_times[bar])


# --- Grid aus TrackTab ---

def grid_from_beats(beats) -> BeatGrid | None:
    """
    [[ms, Nummer, BPM], ...] (TrackTabs Grid mit Tempo je Schlag, siehe
    server._dj_beats_with_tempo()) -> BeatGrid. None ohne Schlaege.
    """
    if not beats:
        return None
    arr = np.asarray(beats, dtype=float)
    bpms = arr[:, 2] if arr.shape[1] > 2 else np.full(len(arr), 120.0)
    finite = bpms[np.isfinite(bpms) & (bpms > 0)]
    return BeatGrid(times=arr[:, 0] / 1000.0, beat_numbers=arr[:, 1].astype(int),
                    bpms=bpms, bpm=float(np.median(finite)) if finite.size else 120.0)


def phase_segments(beat_offsets: list[int], tolerance: int = 2,
                   phrase_beats: int = 32) -> tuple[int, list[int]]:
    """
    Zerlegt eine Folge von Beat-Positionen in Abschnitte konstanter Rasterphase.

    Warum das noetig ist: Tracks brechen das 32er-Raster an Drops und Breaks
    bewusst auf -- Regel 5 nennt das ausdruecklich (kurze Intros, Fake Drops,
    rasterbrechende Drops). Innerhalb eines Abschnitts laufen die Positionen dann
    wieder sauber im 32er-Takt, nur mit anderer Phase.

    Wer mit einer FESTEN Phase ueber den ganzen Track misst, zaehlt alles nach dem
    ersten Bruch als Fehler, obwohl die Struktur voellig regelmaessig ist. Dieser
    Messfehler ist hier zweimal passiert (documentation.md, V-004 und V-011) --
    deshalb steht die Funktion jetzt an einer Stelle und wird von allen
    Auswertungen gemeinsam benutzt.

    Returns:
        (Anzahl Abschnitte, Phase je Abschnitt)
    """
    if not beat_offsets:
        return 0, []

    offsets = sorted(beat_offsets)
    phases = [offsets[0] % phrase_beats]
    phase = phases[0]

    for off in offsets[1:]:
        distance = min((off - phase) % phrase_beats, (phase - off) % phrase_beats)
        if distance > tolerance:
            phase = off % phrase_beats
            phases.append(phase)

    return len(phases), phases
