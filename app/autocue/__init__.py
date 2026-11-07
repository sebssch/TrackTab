"""
Auto Cue: Cues aus Beatgrid, Phrasen und Klang setzen.

Portiert aus dem Projekt rekordbox-autocue-tool (Fassung "ohne Interface",
Okt. 2026). Uebernommen ist nur der Algorithmus; Eingabe ist das Grid und die
Phrasen, die in TrackTab gelten, Ausgabe eine Cue-Liste im Format von
dj_track.cues. Nach Rekordbox geht sie nur ueber den vorhandenen Weg
(/api/dj-rekordbox).

Die Kette in acht Schritten, jeder baut nur auf dem vorigen auf:

    1  lattice    Beatgrid pruefen, Takte und 32-Beat-Raster aufspannen  (Tor)
    2  sources    Audio dekodieren, Spektrogramm (dsp.py = librosa-Nachbau)
    3  features   Merkmale je Takt
    4  evidence   Hinweise aus Phrasen und Tempowechseln
    5  structure  Aufbau des ganzen Tracks
    6  anchors    Anker (Drop 2 und relativ dazu Drop 1, Break 1/2, Outro),
                  gelernte Modelle (learned.py, ohne LightGBM: trees.py)
    7  validate   jeden Hot-Cue-Anker unabhaengig pruefen
    8  placement  Cues nach der Slot-Belegung (slots.py) platzieren

Modelle (model_dir()): nur eigene, in der Bibliothek
<base_dir>/models/autocue-library/ (library.py: Training in der App oder per
`./run.command autocue-train`, ZIP-Import). Ein mitgeliefertes Modell gibt es
nicht mehr -- jede Installation trainiert nach ihren eigenen Cues. Es gilt das
in config["autocue_model"] gewaehlte. Ohne Modell meldet available() False und
die Oberflaeche sperrt Auto Cue -- der Strukturfit allein waere deutlich
schlechter (Autocue-Doku M-010), deshalb kein Rueckfall.
"""
from __future__ import annotations

import json
import os
import threading

from app import config as cfgmod

# Die Kette rechnet mit grossen Arrays (Spektrogramm ~60 MB je 10 Minuten);
# parallele Laeufe aus mehreren Server-Threads wuerden nur Speicher kosten.
_LOCK = threading.Lock()

# Pflicht: Grundmodelle. Die Kette ist optional -- ein Training ohne Memory Cues
# (oder ohne Beispiele fuer eine Rolle) laesst ihr Modell weg, Auto Cue sucht die
# Stelle dann ueber die Strukturanalyse (anchors.py) bzw. die Outro-Regel.
_MODEL_FILES = ("features.json", "calibration.json", "drop2.npz", "break1.npz")
_CHAIN_MODEL_FILES = ("chain_drop1.npz", "chain_break1.npz", "chain_break2.npz", "chain_outro.npz")


MANIFEST = "manifest.json"


def trained_dir() -> str:
    """Frueherer Ort des einzigen eigenen Modells. Nur noch Quelle der einmaligen
    Uebernahme in die Bibliothek (library.migrate())."""
    return str(cfgmod.base_dir() / "models" / "autocue")


def complete(folder: str | None) -> bool:
    return bool(folder) and all(os.path.exists(os.path.join(folder, f)) for f in _MODEL_FILES)


def manifest(folder: str) -> dict:
    """Stand eines Modellordners. Ohne manifest.json (umgewandelte Modelle aus dem
    Autocue-Projekt) gilt das Trainingsdatum aus features.json."""
    try:
        with open(os.path.join(folder, MANIFEST)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(folder, "features.json")) as fh:
            meta = json.load(fh)
        return {"trained_at": str((meta.get("chain") or {}).get("trained_at") or ""), "source": "autocue"}
    except (OSError, ValueError):
        return {}


def model_dir(cfg: dict | None = None) -> str:
    """Ordner des geltenden Modells (config["autocue_model"], library.py) oder ""
    ohne gewaehltes, vollstaendiges Modell."""
    from app.autocue import library
    library.migrate()
    chosen = library.folder_of(library.active_id(cfg))
    return chosen if complete(chosen) else ""


def model_info(cfg: dict | None = None) -> dict:
    """Fuer die Einstellungsseite: welches Modell gilt, Name und Herkunft."""
    from app.autocue import library
    folder = model_dir(cfg)
    if not complete(folder):
        return {}
    for entry in library.entries(cfg):
        if os.path.normpath(library.folder_of(entry["id"]) or "") == os.path.normpath(folder):
            return entry
    return {}


def available() -> bool:
    return complete(model_dir())


def min_confidence(cfg: dict) -> dict:
    """Schwelle je Rolle aus der Stufe (autocue_conf_<rolle>, slots.confidence_threshold())."""
    from app.autocue.slots import CONFIDENCE_KEYS, confidence_threshold
    return {k: confidence_threshold(k, cfg.get(f"autocue_conf_{k}", "normal")) for k in CONFIDENCE_KEYS}


# Rekordbox fuehrt hoechstens 10 Memory Cues -- fest, keine Einstellung.
MAX_MEMORY = 10


def _tracktab_cue(cue) -> dict:
    """CuePoint -> Eintrag in dj_track.cues (Form wie server._clean_dj_cues())."""
    pos = round(float(cue.time_sec), 4)
    if cue.kind == 0:
        return {"kind": "memory", "slot": None, "label": cue.name, "name": cue.name,
                "position_s": pos, "loop_s": None,
                "color": list(cue.color) if cue.color else [128, 128, 128]}
    return {"kind": "hot", "slot": int(cue.kind), "label": "ABCDEFGH"[cue.kind - 1], "name": cue.name,
            "position_s": pos, "loop_s": None,
            "color": list(cue.color) if cue.color else [109, 223, 70]}


# Von Hand bearbeitete Phrasen sind ein fester Indikator: die Stellen, die Phrasen-
# anfaenge sind (Drops, Breaks, Outro), rasten auf die naechste Phrasengrenze ein,
# wenn sie hoechstens SNAP_MAX_BARS entfernt liegt -- eine Korrektur, kein Sprung in
# eine andere Phrase. "Korrekt gesetzt" heisst: die Grenze liegt auf einer Takt-1
# (Toleranz ein Viertel Schlag); eine Grenze mitten im Takt zaehlt nicht, sonst zoege
# sie einen richtigen Cue mit (an der Pruefliste gesehen: Grenze 1 s vor Takt 33,
# Hand-Cue auf Takt 32). Beat-Einsatz, Builds und Gerechnetes bleiben unberuehrt.
SNAP_MAX_BARS = 4
SNAP_ROLES = ("drop1", "drop2", "break1", "break2", "outro_start")


def phrase_boundaries(phrases, lattice) -> dict:
    """{"grid": Takte der Phrasenanfaenge auf einer Takt-1, "loose": naechster Takt der
    uebrigen (mitten im Takt gesetzt)}, je sortiert. Leer = nichts einzurasten."""
    tol = 0.25 * float(lattice.beat_duration)
    grid, loose = set(), set()
    for p in phrases or []:
        if p.get("start") is None:
            continue
        t = float(p["start"])
        bar = lattice.nearest_bar(t)
        (grid if abs(t - lattice.time_of_bar(bar)) <= tol else loose).add(bar)
    return {"grid": sorted(grid), "loose": sorted(loose)} if grid else {}


def _nearest(boundaries: dict, bar: int) -> int | None:
    """Naechste saubere Grenze in Reichweite. Liegt eine mitten im Takt gesetzte Grenze
    naeher, ist die Phrase dort nicht verlaesslich -- dann kein Einrasten."""
    grid = boundaries.get("grid") or []
    if not grid:
        return None
    best = min(grid, key=lambda b: (abs(b - bar), b))
    loose = boundaries.get("loose") or []
    if loose and min(abs(b - bar) for b in loose) < abs(best - bar):
        return None
    return best if abs(best - bar) <= SNAP_MAX_BARS else None


def _snap_decoded(dec, boundaries: dict):
    import dataclasses
    if dec.role not in SNAP_ROLES:
        return dec
    hit = _nearest(boundaries, int(dec.bar))
    return dataclasses.replace(dec, bar=hit) if hit is not None else dec


def snap_anchors(anchors, boundaries: dict, lattice) -> None:
    """Erkannte (nicht gerechnete) Anker auf die naechste bearbeitete Phrasengrenze."""
    for name, a in list(anchors.anchors.items()):
        if a.derived or name not in SNAP_ROLES:
            continue
        hit = _nearest(boundaries, a.bar)
        if hit is None:
            anchors.notes.append(f"{name}: keine bearbeitete Phrasengrenze in der Naehe, bleibt bei Takt {a.bar}")
        elif hit != a.bar:
            a.reason += f"; auf Phrasengrenze eingerastet ({hit - a.bar:+d} Takte)"
            a.bar, a.time_sec = int(hit), lattice.time_of_bar(hit)


def analyse(path: str, beats: list, phrases: list, duration: float, cfg: dict,
            max_seconds: float = 0.0, models: str | None = None, phrases_edited: bool = False) -> dict:
    """
    Cues fuer einen Track.

    beats: [[ms, Nummer, BPM], ...] auf TrackTabs Zeitachse (server._dj_beats_with_tempo()).
    phrases: [{start, end, kind, num}] oder leer.

    Rueckgabe {"status", "cues", "anchors", "reasons", "quality"}:
      done     Cues gesetzt
      badgrid  Grid besteht die Pruefung nicht (reasons sagt warum), nichts gesetzt
      none     keine Rolle ueber ihrer Konfidenz-Schwelle
    TooLongError (sources.TooLongError) ueber max_seconds. models: anderer
    Modellordner (Bewertung im Training), sonst model_dir(). phrases_edited: die
    Phrasen sind von Hand bearbeitet -- dann entscheiden sie allein ueber den
    Ausklang am Trackende (tail.py), auch wenn Phrasen sonst nicht als Hinweis gelten.
    """
    from app.autocue import learned
    from app.autocue.anchors import extract_anchors
    from app.autocue.evidence import collect_evidence
    from app.autocue.features import extract_bar_features
    from app.autocue.lattice import BEATGRID_STRICTNESS, build_lattice, check_beatgrid, grid_from_beats
    from app.autocue.placement import build_cues
    from app.autocue.slots import SlotPlan, defaults as slot_defaults
    from app.autocue.sources import load_sources
    from app.autocue.structure import fit_structure
    from app.autocue.tail import music_end
    from app.autocue.validate import validate_anchors

    grid = grid_from_beats(beats)
    lattice = build_lattice(grid, duration or None, BEATGRID_STRICTNESS, enforce=True)
    if lattice is None:
        report = check_beatgrid(grid, duration or None, BEATGRID_STRICTNESS)
        return {"status": "badgrid", "cues": [], "anchors": [], "reasons": report.reasons, "quality": 0.0}

    with _LOCK:
        # MODEL_DIR ist modulweit: nur unter _LOCK setzen, sonst rechnete ein paralleler
        # Lauf mit dem Modell eines anderen.
        learned.MODEL_DIR = models or model_dir(cfg)
        sources = load_sources(path, max_seconds)
        features = extract_bar_features(lattice, sources)
        del sources
        # Automatische Phrasen nur als Hinweis (Schalter), bearbeitete gelten immer.
        use_phrases = bool(cfg.get("autocue_use_phrases", True)) or phrases_edited
        evidence = collect_evidence(lattice, phrases, use_phrases=use_phrases)
        end_bar, end_reason = (music_end(features["rms"], lattice.bar_times,
                                         phrases if (use_phrases or phrases_edited) else None, phrases_edited)
                               if features.has("rms") else (None, "kein Pegel"))
        structure = fit_structure(features, evidence, lattice)
        learned_dec = learned.predict_anchors(features, evidence, lattice, music_end=end_bar)
        boundaries = phrase_boundaries(phrases, lattice) if phrases_edited else []
        if boundaries:
            # Vor extract_anchors(), damit Gerechnetes (Build 8 Takte vor Drop 2 ...)
            # vom eingerasteten Anker ausgeht.
            learned_dec = {k: _snap_decoded(d, boundaries) for k, d in learned_dec.items()}
        anchors = extract_anchors(structure, features, lattice, learned=learned_dec)
        if boundaries:
            snap_anchors(anchors, boundaries, lattice)
        validate_anchors(anchors, features, lattice)
        placement = build_cues(anchors, features, lattice,
                               max_memory_cues=MAX_MEMORY,
                               min_confidence=min_confidence(cfg),
                               slots=SlotPlan(cfg.get("autocue_slots") or slot_defaults()),
                               music_end=end_bar)

    cues = [_tracktab_cue(c) for c in placement.cues]
    anchor_list = [{"role": a.name, "time": round(float(a.time_sec), 3), "bar": int(a.bar),
                    "confidence": round(float(a.confidence), 3), "reason": a.reason}
                   for a in sorted(anchors.anchors.values(), key=lambda a: a.time_sec)]
    tail = ({"time": round(float(lattice.time_of_bar(end_bar)), 3), "bar": int(end_bar), "reason": end_reason}
            if end_bar is not None else None)
    return {"status": "done" if cues else "none", "cues": cues, "anchors": anchor_list, "music_end": tail,
            "reasons": [f"{role}: {why}" for role, why in placement.skipped],
            "quality": round(float(structure.quality), 3)}
