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

Modelle an zwei Orten (model_dir()): gebuendelt in vendor/models/autocue/ und aus
einem eigenen Training in <base_dir>/models/autocue/ (app/autocue/training/,
`./run.command autocue-train`). Es gilt der juengere nach manifest.json -- eine
neuere App verdraengt so ein altes eigenes Training und umgekehrt. Ohne Modelle
meldet available() False und die Oberflaeche blendet Auto Cue aus -- der
Strukturfit allein waere deutlich schlechter (Autocue-Doku M-010), deshalb kein
Rueckfall.
"""
from __future__ import annotations

import json
import os
import threading

from app import config as cfgmod

# Die Kette rechnet mit grossen Arrays (Spektrogramm ~60 MB je 10 Minuten);
# parallele Laeufe aus mehreren Server-Threads wuerden nur Speicher kosten.
_LOCK = threading.Lock()

_MODEL_FILES = ("features.json", "calibration.json", "drop2.npz", "break1.npz",
                "chain_drop1.npz", "chain_break1.npz", "chain_break2.npz")


MANIFEST = "manifest.json"


def bundled_dir() -> str:
    return str(cfgmod.vendor_dir() / "models" / "autocue")


def trained_dir() -> str:
    """Ziel eines eigenen Trainings dieser Instanz (beschreibbar, auch im Bundle)."""
    return str(cfgmod.base_dir() / "models" / "autocue")


def complete(folder: str) -> bool:
    return all(os.path.exists(os.path.join(folder, f)) for f in _MODEL_FILES)


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


def model_dir() -> str:
    """Der juengere vollstaendige Ordner von eigenem Training und Bundle."""
    own, bundled = trained_dir(), bundled_dir()
    if not complete(own):
        return bundled
    if not complete(bundled):
        return own
    stamp = lambda folder: str(manifest(folder).get("trained_at") or "")  # noqa: E731
    return own if stamp(own) >= stamp(bundled) else bundled


def model_info() -> dict:
    """Fuer die Einstellungsseite: welches Modell gilt und woher es kommt."""
    folder = model_dir()
    if not complete(folder):
        return {}
    info = manifest(folder)
    return {**info, "origin": "trained" if folder == trained_dir() else "bundled"}


def available() -> bool:
    return complete(model_dir())


def min_confidence(cfg: dict) -> dict:
    from app.autocue.slots import CONFIDENCE_KEYS
    return {k: float(cfg.get(f"autocue_conf_{k}", 0.25)) for k in CONFIDENCE_KEYS}


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
        learned.MODEL_DIR = models or model_dir()
        sources = load_sources(path, max_seconds)
        features = extract_bar_features(lattice, sources)
        del sources
        use_phrases = bool(cfg.get("autocue_use_phrases", True))
        evidence = collect_evidence(lattice, phrases, use_phrases=use_phrases)
        end_bar, end_reason = (music_end(features["rms"], lattice.bar_times,
                                         phrases if (use_phrases or phrases_edited) else None, phrases_edited)
                               if features.has("rms") else (None, "kein Pegel"))
        structure = fit_structure(features, evidence, lattice)
        learned_dec = learned.predict_anchors(features, evidence, lattice)
        anchors = extract_anchors(structure, features, lattice, learned=learned_dec)
        validate_anchors(anchors, features, lattice)
        placement = build_cues(anchors, features, lattice,
                               max_memory_cues=max(0, min(10, int(cfg.get("autocue_max_memory", 10)))),
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
