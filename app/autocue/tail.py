"""
Musikende: ab welchem Takt nur noch der Ausklang laeuft (Hall/Delay-Rest).

Dort setzt Auto Cue nichts mehr (placement.build_cues(music_end=...)), und das
Training zaehlt Hand-Cues dort nicht als Lernbeispiel (training/dataset.py) --
beide rufen dieselbe Funktion, damit Lernen und Setzen dieselbe Grenze kennen.

Ein Outro ist nicht automatisch Ausklang: eine laute Outro-Phrase (Outro 1/2) ist
echtes Outro, dort darf ein Memory Cue sitzen. Entscheidend ist der Pegel:

  Pegel       Takt-RMS (features "rms") relativ zum 90. Perzentil des Tracks,
              ueber 2 Takte geglaettet. Leise = unter TAIL_LEVEL.
  Phrasen     die zusammenhaengend leisen Phrasen am Trackende (Mittel unter
              TAIL_LEVEL); der Ausklang beginnt am Anfang der ersten davon.

  von Hand bearbeitete Phrasen  verpflichtend: nur die Phrasengrenze zaehlt,
                                 ohne leise Phrase am Ende gibt es keinen Ausklang
  gemessene Phrasen             die Phrasengrenze, wenn der Pegel die Phrase als
                                 leise bestaetigt, sonst der Takt aus dem Pegel
  keine Phrasen                 der Takt aus dem Pegel: ab ihm bleibt es bis zum
                                 Ende leise
"""
from __future__ import annotations

import numpy as np

TAIL_LEVEL = 0.35
_SMOOTH_BARS = 2


def bar_levels(rms) -> np.ndarray:
    """Pegel je Takt relativ zum lauten Teil des Tracks (90. Perzentil)."""
    rms = np.asarray(rms, dtype=np.float64)
    if rms.size == 0:
        return rms
    ref = float(np.percentile(rms, 90))
    if ref <= 1e-9:
        return np.zeros_like(rms)
    level = rms / ref
    kernel = np.ones(_SMOOTH_BARS) / _SMOOTH_BARS
    return np.convolve(level, kernel, mode="same")


def _level_end(level: np.ndarray) -> int | None:
    """Erster Takt, ab dem es bis zum Ende leise bleibt; None = kein Ausklang."""
    n = len(level)
    if n == 0 or level[-1] >= TAIL_LEVEL:
        return None
    loud = np.nonzero(level >= TAIL_LEVEL)[0]
    if loud.size == 0:
        return None                     # alles leise: kein Ausklang, sondern ein leiser Track
    end = int(loud[-1]) + 1
    return end if end < n else None


def _phrase_end(level: np.ndarray, phrases: list, bar_times: np.ndarray) -> int | None:
    """Anfang der zusammenhaengend leisen Phrasen am Trackende (Takt)."""
    n = len(level)
    if not phrases or n == 0:
        return None
    bars = []
    for p in sorted(phrases, key=lambda p: float(p["start"])):
        a = int(np.clip(np.searchsorted(bar_times, float(p["start"]) - 1e-3), 0, n))
        b = int(np.clip(np.searchsorted(bar_times, float(p["end"]) - 1e-3), 0, n))
        if b > a:
            bars.append((a, b))
    start = None
    for a, b in reversed(bars):
        if float(level[a:b].mean()) < TAIL_LEVEL:
            start = a
        else:
            break
    if start is None or start <= 0:
        return None                     # ganzer Track leise oder nichts Leises am Ende
    return start


def music_end(rms, bar_times, phrases: list | None, phrases_edited: bool) -> tuple[int | None, str]:
    """(Takt des Musikendes oder None, Begruendung)."""
    level = bar_levels(rms)
    times = np.asarray(bar_times, dtype=np.float64)
    if phrases and phrases_edited:
        bar = _phrase_end(level, phrases, times)
        return bar, ("leise Phrase am Ende (bearbeitet)" if bar is not None
                     else "keine leise Phrase am Ende (bearbeitet)")
    if phrases:
        bar = _phrase_end(level, phrases, times)
        if bar is not None:
            return bar, "leise Phrase am Ende"
    bar = _level_end(level)
    return bar, ("Pegel bleibt ab hier leise" if bar is not None else "kein Ausklang")
