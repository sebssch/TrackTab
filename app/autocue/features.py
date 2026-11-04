"""
Schritt 3 der Analysekette: Merkmale je Takt.

Eine Zeile je Takt, eine Spalte je Merkmal. Alles entsteht aus dem EINEN
Spektrogramm aus Schritt 2 -- keine weitere Transformation, kein HPSS.

Die Spalten sind namentlich ansprechbar (BarFeatures["sub_rms"]). Index-Arithmetik
ueber einen Merkmalsvektor war in der Vorgaengerversion eine wiederkehrende
Fehlerquelle: Wenn Training und Inferenz die Indizes unterschiedlich belegen,
faellt das nirgends auf -- das Modell wird nur still schlechter.
"""
from __future__ import annotations

import numpy as np

from app.autocue import dsp
from app.autocue.sources import BANDS, SourceSignals
from app.autocue.types import BarFeatures, Lattice

EPS = 1e-9

# Ueber wie viele Takte die Umgebungsvergleiche laufen.
CONTEXT_LAGS = (1, 2, 4, 8)

# Ein Drop muss die Energie halten. Ueber so viele Takte wird das geprueft --
# 16 Takte sind zwei Phrasen, kuerzer waere ein Fill.
SUSTAIN_BARS = 16

# Ueber wie viele Takte ein Aufbau laeuft.
RISER_BARS = 8


def _to_db(x: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(x, EPS))


def _bar_frame_slices(lattice: Lattice, sources: SourceSignals) -> list[tuple[int, int]]:
    """Fuer jeden Takt den Bereich an Spektrogramm-Frames bestimmen."""
    frame_rate = sources.sample_rate / sources.hop_length
    n_frames = sources.n_frames
    bounds = list(lattice.bar_times) + [
        lattice.bar_times[-1] + (lattice.bar_times[-1] - lattice.bar_times[-2]
                                 if lattice.n_bars > 1 else 2.0)
    ]
    slices = []
    for i in range(lattice.n_bars):
        a = int(np.clip(round(bounds[i] * frame_rate), 0, max(0, n_frames - 1)))
        b = int(np.clip(round(bounds[i + 1] * frame_rate), 0, n_frames))
        # Das Beatgrid reicht bei manchen Tracks ueber das Audio hinaus (Rekordbox
        # setzt das Gitter bis zur angegebenen Laenge, die Datei ist kuerzer).
        # Solche Takte bekommen den letzten verfuegbaren Frame statt eines
        # leeren Bereichs -- sonst bricht jede Aggregation ab.
        if b <= a:
            b = min(a + 1, n_frames)
        if a >= n_frames:
            a, b = max(0, n_frames - 1), n_frames
        slices.append((a, b))
    return slices


def _aggregate(values: np.ndarray, slices) -> np.ndarray:
    """Einen Frame-Verlauf auf Takte mitteln. Leere Bereiche ergeben 0."""
    out = np.zeros(len(slices))
    for i, (a, b) in enumerate(slices):
        seg = values[a:b]
        if seg.size:
            out[i] = float(seg.mean())
    return out


def _normalise(x: np.ndarray) -> np.ndarray:
    """Auf 0..1 skalieren, robust gegen Ausreisser (5./95. Perzentil)."""
    if x.size == 0:
        return x
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < EPS:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _hysteresis_gate(level: np.ndarray, on: float = 0.45, off: float = 0.30) -> np.ndarray:
    """
    An/Aus-Erkennung einer Klangschicht mit zwei Schwellen.

    Eine einzelne Schwelle flattert: Liegt der Pegel genau darauf, wechselt das
    Ergebnis im Takt hin und her und erzeugt Dutzende Scheingrenzen. Mit zwei
    Schwellen muss ein Wechsel deutlich sein, um zu zaehlen.
    """
    out = np.zeros(len(level))
    state = level[0] > on if len(level) else False
    for i, value in enumerate(level):
        if state and value < off:
            state = False
        elif not state and value > on:
            state = True
        out[i] = 1.0 if state else 0.0
    return out


def extract_bar_features(lattice: Lattice, sources: SourceSignals) -> BarFeatures:
    """
    Merkmalstabelle fuer einen Track berechnen.

    Args:
        lattice: Takte aus dem Beatgrid (Schritt 1)
        sources: Signalquellen (Schritt 2)
    """
    n_bars = lattice.n_bars
    slices = _bar_frame_slices(lattice, sources)
    columns: dict[str, np.ndarray] = {}

    spec = sources.spectrogram
    freqs = sources.frequencies

    # --- Pegel je Frequenzband ---
    for name in BANDS:
        rows = sources.bands.get(name)
        if rows is None:
            rms_frames = np.zeros(spec.shape[1])
        else:
            rms_frames = np.sqrt(np.mean(spec[rows[0]:rows[1]] ** 2, axis=0))
        rms_bars = _aggregate(rms_frames, slices)
        columns[f"{name}_rms"] = _normalise(rms_bars)
        columns[f"{name}_db"] = _to_db(rms_bars + EPS)

        # Klangschicht an oder aus -- Grundlage fuer "Bass kommt zurueck",
        # "Drums setzen ein", "Gesang beginnt".
        columns[f"{name}_on"] = _hysteresis_gate(_normalise(rms_bars))

    # --- Breitband-Pegel und Dynamik ---
    broadband = np.sqrt(np.mean(spec ** 2, axis=0))
    rms_bars = _aggregate(broadband, slices)
    columns["rms"] = _normalise(rms_bars)
    columns["rms_db"] = _to_db(rms_bars + EPS)

    peak_frames = spec.max(axis=0) if spec.size else np.zeros(spec.shape[1])
    peak_bars = np.zeros(n_bars)
    for i, (a, b) in enumerate(slices):
        seg = peak_frames[a:b]
        if seg.size:
            peak_bars[i] = float(seg.max())
    # Crest-Faktor: Spitze gegen Effektivwert. Ein gemasterter Drop ist stark
    # komprimiert (kleiner Crest), ein offener Breakdown nicht.
    columns["crest"] = _normalise(peak_bars / (rms_bars + EPS))

    # --- Klangfarbe ---
    centroid = dsp.spectral_centroid(spec, freqs)
    rolloff = dsp.spectral_rolloff(spec, freqs)
    flatness = dsp.spectral_flatness(spec)
    columns["centroid"] = _normalise(_aggregate(centroid, slices))
    columns["rolloff"] = _normalise(_aggregate(rolloff, slices))
    columns["flatness"] = _normalise(_aggregate(flatness, slices))

    # --- Rhythmus ---
    onset_env = dsp.onset_strength_db_power(spec)
    columns["onset"] = _normalise(_aggregate(onset_env, slices))
    # Onset-Staerke genau am Taktanfang -- ein Drop schlaegt dort ein.
    onset_at_bar = np.zeros(n_bars)
    for i, (a, _b) in enumerate(slices):
        seg = onset_env[a:a + 3]
        if seg.size:
            onset_at_bar[i] = float(seg.max())
    columns["onset_at_bar"] = _normalise(onset_at_bar)
    # Absolut ist der Wert wenig aussagekraeftig -- in Vier-Viertel-Tanzmusik hat
    # jeder Takt einen Downbeat-Onset. Aussagekraeftig ist der Vergleich zur
    # Umgebung: sticht DIESER Taktanfang gegenueber den Nachbarn heraus?
    local = np.zeros(n_bars)
    for i in range(n_bars):
        neigh = np.concatenate([onset_at_bar[max(0, i - 4):i], onset_at_bar[i + 1:i + 5]])
        if neigh.size:
            local[i] = onset_at_bar[i] - float(neigh.mean())
    columns["onset_relative"] = _normalise(local)

    # --- Harmonik ---
    chroma = dsp.chroma_stft(spec, sources.sample_rate)
    chroma_flux = np.concatenate([[0.0], np.sqrt(((np.diff(chroma, axis=1)) ** 2).sum(axis=0))])
    columns["chroma_flux"] = _normalise(_aggregate(chroma_flux, slices))

    # --- Spektraler Fluss INNERHALB eines Takts ---
    # Misst Unruhe, nicht Grenzen. An einer Abschnittsgrenze ist der neue Takt in
    # sich homogen -- der Wechsel passiert an der Kante zwischen zwei Takten.
    # Als Grenzdetektor taugt das nicht (gemessen: schlechter als Zufall), als
    # Merkmal fuer "wie belebt ist dieser Takt" schon.
    flux = dsp.db_flux(spec, EPS)
    columns["flux"] = _normalise(_aggregate(flux, slices))

    # --- Novelty: der eigentliche Grenzdetektor ---
    # Klangprofil je Takt bilden und vergleichen, was VOR und NACH einer Kante
    # liegt. Genau das misst "hier beginnt etwas Neues".
    mel = dsp.mel_power(spec, sources.sample_rate, n_mels=64)
    mel_db = dsp.power_to_db(mel)
    bar_profile = np.zeros((n_bars, mel_db.shape[0]))
    for i, (a, b) in enumerate(slices):
        seg = mel_db[:, a:b]
        if seg.size:
            bar_profile[i] = seg.mean(axis=1)

    def _cosine_novelty(window: int) -> np.ndarray:
        """Abstand zwischen dem Mittel der `window` Takte davor und danach."""
        out = np.zeros(n_bars)
        for i in range(n_bars):
            before = bar_profile[max(0, i - window):i]
            after = bar_profile[i:i + window]
            if len(before) == 0 or len(after) == 0:
                continue
            u, v = before.mean(axis=0), after.mean(axis=0)
            denom = np.linalg.norm(u) * np.linalg.norm(v)
            out[i] = 1.0 - float(u @ v) / denom if denom > EPS else 0.0
        return out

    # Zwei Weiten: 4 Takte findet feine Wechsel, 8 Takte die Phrasengrenzen.
    columns["novelty_4"] = _normalise(_cosine_novelty(4))
    columns["novelty_8"] = _normalise(_cosine_novelty(8))
    columns["novelty"] = _normalise(columns["novelty_4"] + columns["novelty_8"])

    # --- Umgebungsvergleiche ---
    base = dict(columns)
    for key in ("rms", "centroid", "onset"):
        series = base[key]
        for lag in CONTEXT_LAGS:
            before = np.concatenate([np.full(lag, series[0]), series[:-lag]])
            columns[f"{key}_d{lag}"] = series - before

    # Bass getrennt, weil "Bass weg / Bass zurueck" das wichtigste Einzelsignal ist
    bass_key = "bass_rms" if "bass_rms" in base else "rms"
    bass = base[bass_key]
    sub = base.get("sub_rms", bass)
    low = np.maximum(bass, sub)
    for lag in CONTEXT_LAGS:
        before = np.concatenate([np.full(lag, low[0]), low[:-lag]])
        columns[f"low_d{lag}"] = low - before

    # --- Zusammengesetzte Detektoren ---

    # Aufbau: Klangschwerpunkt UND Lautheit steigen ueber mehrere Takte gemeinsam.
    def _slope_before(series: np.ndarray, window: int) -> np.ndarray:
        """
        Steigung ueber die Takte STRIKT VOR i.

        Der Unterschied ist entscheidend: Wer die Takte bis einschliesslich i
        nimmt, misst den Aufbau und ist am LETZTEN Build-Takt hoch -- also einen
        Takt vor dem Drop. Der Wert soll aber AM Drop hoch sein, denn dort wird
        er gebraucht. (Gemessen: die erste Fassung lag auf Zufallsniveau.)
        """
        out = np.zeros(n_bars)
        for i in range(n_bars):
            seg = series[max(0, i - window):i]
            if len(seg) >= 2:
                out[i] = np.polyfit(np.arange(len(seg)), seg, 1)[0]
        return out

    rise_centroid = _slope_before(base["centroid"], RISER_BARS)
    rise_rms = _slope_before(base["rms"], RISER_BARS)
    # Ein Aufbau zeigt sich als steigender Klangschwerpunkt; die Lautheit steigt
    # oft erst spaet mit. Summe statt Produkt, damit ein Riser ohne Pegelanstieg
    # nicht auf null faellt.
    columns["riser_score"] = _normalise(
        np.maximum(0, rise_centroid) + 0.5 * np.maximum(0, rise_rms)
    )

    # Einschlag: breitbandiger Sprung genau auf einem Taktanfang.
    columns["impact_score"] = _normalise(
        np.maximum(0, columns["rms_d1"]) * base["onset_at_bar"]
    )
    columns["impact_relative"] = _normalise(
        np.maximum(0, columns["rms_d1"]) * np.maximum(0, columns["onset_relative"])
    )

    # Haelt die Energie? Der wichtigste Einzelfilter gegen Fake Drops:
    # ein Moment, der laut aufschlaegt und sofort wieder abfaellt, ist kein Drop.
    sustain = np.zeros(n_bars)
    for i in range(n_bars):
        after = base["rms"][i:i + SUSTAIN_BARS]
        sustain[i] = after.mean() if len(after) else 0.0
    columns["sustain_score"] = _normalise(sustain)

    # Bass kehrt zurueck.
    # Entscheidend ist der Vergleich gegen das TIEF davor, nicht gegen den
    # Mittelwert der Vortakte: Gemeint ist "der Bass war weg und ist wieder da",
    # nicht "der Bass ist etwas lauter als eben". (Die Mittelwert-Fassung lag
    # messbar auf Zufallsniveau.)
    bass_return = np.zeros(n_bars)
    for i in range(n_bars):
        before = low[max(0, i - RISER_BARS):i]
        if before.size:
            bass_return[i] = low[i] - float(before.min())
    columns["bass_return"] = _normalise(np.maximum(0, bass_return))

    # Zusaetzlich der klare Schaltfall: Bass war AUS und ist jetzt AN.
    low_gate = _hysteresis_gate(low)
    columns["bass_switch_on"] = np.concatenate([[0.0], np.maximum(0, np.diff(low_gate))])

    # --- Aliasspalten ---
    #
    # Spaetere Schritte fragen nach den Rollen low/melodic/percussive statt nach
    # den Bandnamen sub/bass/mid/high.
    def _first_of(*names: str) -> np.ndarray | None:
        for nm in names:
            if nm in columns:
                return columns[nm]
        return None

    low_alias = _first_of("sub_rms", "bass_rms")
    if low_alias is not None:
        other = columns.get("bass_rms" if "sub_rms" in columns else "sub_rms")
        columns["low_rms"] = np.maximum(low_alias, other) if other is not None else low_alias
    else:
        columns["low_rms"] = columns["rms"]

    melodic = _first_of("other_rms", "mid_rms")
    columns["melodic_rms"] = melodic if melodic is not None else columns["rms"]

    perc = _first_of("drums_rms", "high_rms")
    columns["percussive_rms"] = perc if perc is not None else columns["rms"]

    columns["low_on"] = _hysteresis_gate(columns["low_rms"])
    columns["melodic_on"] = _hysteresis_gate(columns["melodic_rms"])

    # --- Lage im Track ---
    columns["rel_position"] = np.linspace(0.0, 1.0, n_bars) if n_bars > 1 else np.zeros(n_bars)
    columns["bars_from_start"] = np.arange(n_bars, dtype=float)
    columns["bars_to_end"] = np.arange(n_bars - 1, -1, -1, dtype=float)

    names = sorted(columns)
    matrix = np.column_stack([np.asarray(columns[k], dtype=float) for k in names])
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)

    return BarFeatures(
        matrix=matrix,
        names=names,
        sample_rate=sources.sample_rate,
    )
