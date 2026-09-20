"""
Lautheitsmessung: Integrated Loudness, True Peak und Loudness Range nach
ITU-R BS.1770 / EBU R128 (ffmpeg-Filter 'ebur128', Ein-Pass-Analyse).

Anders als der Encoder-Tiefpass (siehe classify.py) hat Lautheit keine
"richtige" Zielgroesse: sie haengt vom Genre und vom Erscheinungsjahr ab
(Loudness War, sich wandelnde Mastering-Konventionen). Dieses Modul liefert
deshalb nur Rohmesswerte plus einen weichen Hinweis relativ zu einem
konfigurierbaren DJ/Club-Referenzband -- kein Verdikt wie OK/VERDAECHTIG/FAKE.
Einzige Ausnahme ist die True-Peak-Warnung: Intersample-Clipping ist immer
ein technischer Fehler, unabhaengig von Stil oder Epoche.

'ebur128' statt 'loudnorm': beide implementieren denselben EBU-R128-Standard
und liefern an echtem Material praktisch identische Integrated-/True-Peak-/
LRA-Werte (gemessen: Abweichung <0.2 LU/dB je Track). 'loudnorm' berechnet
aber zusaetzlich immer eine Normalisierungs-Gain-Kurve, obwohl ohne echte
Normalisierung ('-f null') nie tatsaechlich normalisiert wird -- diese
ungenutzte Zusatzarbeit war an echtem Material mit Abstand der groesste
Zeitfresser der gesamten Analysekette (~6-9s je Track bei 'loudnorm' gegen
~1-1.6s bei 'ebur128', gemessen an MP3s um 200-300s Laenge) -- deutlich mehr
als Dekodieren, Hashing und die Spektralanalyse zusammen.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from math import isfinite

from . import media

# ffmpeg gibt bei Stille/Sonderfaellen auch 'inf'/'nan' statt einer Zahl aus
# (z.B. True Peak bei absoluter Stille) -- float() liest beides.
_NUM = r"-?(?:\d+\.?\d*|inf|nan)"
_INTEGRATED_RE = re.compile(rf"^\s*I:\s*({_NUM})\s*LUFS", re.MULTILINE | re.IGNORECASE)
_LRA_RE = re.compile(rf"^\s*LRA:\s*({_NUM})\s*LU\b", re.MULTILINE | re.IGNORECASE)
_PEAK_RE = re.compile(rf"^\s*Peak:\s*({_NUM})\s*dBFS", re.MULTILINE | re.IGNORECASE)


@dataclass
class LoudnessResult:
    ok: bool = False
    error: str = ""
    integrated_lufs: float = 0.0
    true_peak_dbtp: float = 0.0
    lra_lu: float = 0.0


def analyse(path: str, duration_s: float, cfg: dict) -> LoudnessResult:
    """Ein-Pass-Messung ueber die ersten max_analysis_s Sekunden (wie spectral.py:
    dieselbe Deckelung fuer lange DJ-Sets). 'framelog=quiet' unterdrueckt nur die
    Sekundentakt-Zwischenwerte -- die abschliessende Zusammenfassung (unten
    geparst) bleibt unabhaengig davon erhalten."""
    res = LoudnessResult()
    max_s = min(float(cfg["max_analysis_s"]), max(duration_s, 0.0))
    cmd = [
        media.ffmpeg_path(), "-hide_banner", "-nostats", "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-t", f"{max_s:.3f}",
        "-af", "ebur128=peak=true:framelog=quiet",
        "-f", "null", "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        res.error = f"loudness: {exc}"
        return res

    stderr = out.stderr.decode("utf-8", "replace")
    m_i, m_lra, m_peak = _INTEGRATED_RE.search(stderr), _LRA_RE.search(stderr), _PEAK_RE.search(stderr)
    if not (m_i and m_lra and m_peak):
        res.error = stderr.strip()[:200] or "Kein Messergebnis"
        return res

    try:
        integrated = float(m_i.group(1))
        true_peak = float(m_peak.group(1))
        lra = float(m_lra.group(1))
    except ValueError:
        res.error = "ebur128-Ausgabe konnte nicht gelesen werden"
        return res

    if not (isfinite(integrated) and isfinite(true_peak) and isfinite(lra)):
        res.error = "Kein Pegel messbar (Stille?)"
        return res

    res.integrated_lufs = integrated
    res.true_peak_dbtp = true_peak
    res.lra_lu = lra
    res.ok = True
    return res


def clip_risk(true_peak_dbtp: float, cfg: dict) -> bool:
    """Intersample-Clipping-Warnung -- immer ein technischer Fehler, nie ein Stilmittel."""
    return true_peak_dbtp > float(cfg["loudness_clip_dbtp"])


def club_hint(integrated_lufs: float, cfg: dict) -> str:
    """
    Weicher Vergleich mit einem DJ/Club-Referenzband, kein Verdikt.

    Der Hinweis dient dem Gain-Staging vor dem Mix (wie laut liegt der Track
    im Vergleich zu ueblichem Club-Material), nicht der Qualitaetsbewertung --
    ein leiser gemastertes altes Stueck ist deshalb nicht "falsch".
    """
    low = float(cfg["loudness_ref_low_lufs"])
    high = float(cfg["loudness_ref_high_lufs"])
    if integrated_lufs < low:
        return "leiser als Club-Standard"
    if integrated_lufs > high:
        return "lauter als Club-Standard"
    return "im Club-Bereich"
