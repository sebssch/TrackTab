"""
Eigenes Beatgrid und eigene Phrasen eines Tracks -- und deren erste Fuellung aus Rekordbox.

TrackTab fuehrt je Track einen eigenen Stand (Tabelle 'phrases'). Fehlt er, wird
Rekordbox' Grid samt Phrasen dort initial gespeichert (Nutzervorgabe): so koennen
in TrackTab mehr Tracks ein Grid haben als in Rekordbox, und Auto Cue rechnet --
zur Laufzeit wie im Training -- immer mit TrackTabs Stand.

Herkunft je Feld in phrases.grid_origin/phrases_origin: "tracktab" (eigene Messung,
auch alte Zeilen ohne Eintrag) oder "rekordbox" (uebernommen). Eine Uebernahme
sperrt nichts und zaehlt nicht als Bearbeitung; eine eigene Messung ersetzt sie.
Bearbeitungen aus der DJ-Ansicht (dj_track) gehen allem vor und werden nie
angefasst.

Zeiten liegen hier immer auf der eigenen Zeitachse (ffmpeg). Rekordbox spielt
manche Dateien spaeter ab (rekordbox.playback_lead()), die Uebernahme zieht den
gemessenen Versatz ab -- genau wie server._dj_track_payload() beim Anzeigen.

Wird vom Server (mit dessen Lock) und vom Training (app/autocue/training/, ohne
Server) benutzt, deshalb ohne Abhaengigkeit von server.py.
"""
from __future__ import annotations

import bisect
import contextlib
import hashlib
import json
import os
import threading
import time

from app import db as db_mod
from app import rekordbox as rekordbox_mod
from app import wavedetail as wavedetail_mod

ORIGIN_TRACKTAB = "tracktab"
ORIGIN_REKORDBOX = "rekordbox"
SEED_VERSION = "rekordbox"


def dj_hash(value) -> str:
    return hashlib.md5(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def phrases_from_first_beat(phrases: list, beats: list) -> list:
    """Keine Phrase vor dem ersten Schlag (wie in Rekordbox, davor liegt meist Stille):
    fruehere Grenzen ruecken auf ihn, was dabei zu kurz wird, faellt weg. Gilt auch fuer
    alte Messungen und Bearbeitungen, die noch beim Trackanfang beginnen."""
    if not phrases or not beats:
        return phrases
    first = beats[0][0] / 1000
    if phrases[0]["start"] >= first:
        return phrases
    out = []
    for p in phrases:
        start, end = max(p["start"], first), max(p["end"], first)
        if end - start >= 0.05:
            out.append({**p, "start": round(start, 4), "end": round(end, 4)})
    return out


def snap_phrases(phrases: list, beats: list, bars_only: bool) -> list:
    """Phrasengrenzen auf den naechsten Schlag bzw. Taktanfang. Der Anfang rueckt auf den
    ersten Schlag, das Trackende bleibt, so kurz geratene Phrasen fallen weg und der
    Nachbar schliesst an."""
    times = [b[0] / 1000 for b in beats if not bars_only or b[1] == 1] or [b[0] / 1000 for b in beats]
    if not times or not phrases:
        return phrases

    def nearest(sec: float) -> float:
        i = bisect.bisect_left(times, sec)
        if i == 0:
            return times[0]
        if i == len(times):
            return times[-1]
        return times[i - 1] if sec - times[i - 1] <= times[i] - sec else times[i]

    out: list = []
    start = phrases[0]["start"]
    for i, p in enumerate(phrases):
        last = i == len(phrases) - 1
        end = p["end"] if last else round(nearest(p["end"]), 4)
        if end - start < 0.05:
            if last and out:
                out[-1]["end"] = end
            continue
        out.append({**p, "start": start, "end": end})
        start = end
    return phrases_from_first_beat(out, beats)


def segment_tempo(segments: list, sec: float) -> float:
    tempo = segments[0]["bpm"]
    for seg in segments:
        if seg["t"] <= sec + 1e-6:
            tempo = seg["bpm"]
    return tempo


def beats_with_tempo(beats: list, segments: list, lead: float, bpm: float = 0) -> list:
    """[[ms, Nummer]] auf eigener Achse -> [[ms, Nummer, BPM]] auf Rekordbox' Achse.
    Ohne Abschnitte (reine Analyse) kommt das Tempo aus dem Abstand ueber je 8 Schlaege;
    bei gleichmaessigem Raster ein festes Tempo, die Rundung auf ms schwankte sonst je
    Schlag. bpm: gemessenes Tempo, gilt, wenn es zum Abstand passt."""
    out = []
    n = len(beats)
    if not segments and n > 1:
        gaps = [b[0] - a[0] for a, b in zip(beats, beats[1:])]
        if max(gaps) - min(gaps) <= 5:
            step = (beats[-1][0] - beats[0][0]) / (n - 1)
            fixed = bpm if bpm and step > 0 and abs(60000 / step - bpm) < 0.5 else (round(60000 / step, 2) if step > 0 else 120.0)
            return [[int(round(ms + lead * 1000)), num, fixed] for ms, num in beats]
    for i, (ms, num) in enumerate(beats):
        if segments:
            bpm = segment_tempo(segments, ms / 1000)
        else:
            a, b = max(0, i - 4), min(n - 1, i + 4)
            span = beats[b][0] - beats[a][0]
            bpm = round(60000 * (b - a) / span, 2) if span > 0 else 120.0
        out.append([int(round(ms + lead * 1000)), num, bpm])
    return out


def rekordbox_lead(path: str) -> float:
    """Versatz zu Rekordbox in s (wavedetail.lead() mit rekordbox.playback_lead())."""
    return wavedetail_mod.lead(path, os.path.getmtime(path),
                               lambda mono, sr: rekordbox_mod.playback_lead(path, mono, sr),
                               rekordbox_mod.lead_stamp(path))


def beats_to_own(rb_beats: list, lead: float) -> list:
    return [[max(0, int(round(b[0] - lead * 1000))), int(b[1])] for b in rb_beats]


def time_to_own(t: float, lead: float) -> float:
    return max(0.0, round(float(t) - lead, 4))


def phrases_to_own(rb_phrases: list, lead: float) -> list:
    return [{**p, "start": time_to_own(p["start"], lead), "end": time_to_own(p["end"], lead)}
            for p in rb_phrases]


def cues_to_own(rb_cues: list, lead: float) -> list:
    return [{**c, "position_s": time_to_own(c["position_s"], lead),
             "loop_s": time_to_own(c["loop_s"], lead) if c.get("loop_s") is not None else None}
            for c in rb_cues]


def grid_origin(stored: dict | None) -> str | None:
    if not stored or not stored.get("beats"):
        return None
    return stored.get("grid_origin") or ORIGIN_TRACKTAB


def phrases_origin(stored: dict | None) -> str | None:
    if not stored or not stored.get("phrases"):
        return None
    return stored.get("phrases_origin") or ORIGIN_TRACKTAB


def own_inputs(conn, path: str) -> dict:
    """Was TrackTab selbst zu einem Track hat, je Feld Bearbeitung vor eigener Zeile:
    {beats [[ms, Nummer]], segments, phrases, bpm, grid_origin, phrases_origin}.
    Origin "edited" = Bearbeitung aus der DJ-Ansicht, None = nichts da."""
    edits = db_mod.get_dj_track(conn, path)
    stored = db_mod.get_phrases(conn, path)
    grid = edits["grid"]
    if grid is not None:
        beats, segments, g_origin = grid.get("beats") or [], grid.get("segments"), "edited"
    else:
        beats, segments, g_origin = (stored or {}).get("beats") or [], None, grid_origin(stored)
    if edits["phrases"] is not None:
        phrases, p_origin = edits["phrases"], "edited"
    else:
        phrases, p_origin = (stored or {}).get("phrases") or [], phrases_origin(stored)
    return {"beats": beats, "segments": segments, "bpm": float((stored or {}).get("bpm") or 0),
            "phrases": phrases_from_first_beat(phrases, beats),
            "grid_origin": g_origin if beats else None,
            "phrases_origin": p_origin if phrases else None}


def _bpm_of(beats: list) -> float:
    gaps = sorted(b[0] - a[0] for a, b in zip(beats, beats[1:]) if b[0] > a[0])
    if not gaps:
        return 0.0
    return round(60000.0 / gaps[len(gaps) // 2], 2)


def _seed_row(beats: list, phrases: list) -> dict:
    """Zeile fuer db.save_phrases() aus uebernommenen Rekordbox-Daten."""
    downbeat = next((b[0] for b in beats if b[1] == 1), beats[0][0] if beats else 0)
    return {"mode": ORIGIN_REKORDBOX, "used": ORIGIN_REKORDBOX, "bpm": _bpm_of(beats),
            "confidence": None, "first_beat": beats[0][0] / 1000 if beats else None,
            "downbeat": downbeat / 1000 if beats else None, "changes": [],
            "phrases": phrases, "mood": None, "uncertain": False, "version": SEED_VERSION,
            "beats": beats, "grid_origin": ORIGIN_REKORDBOX,
            "phrases_origin": ORIGIN_REKORDBOX if phrases else None}


def seed_from_rekordbox(cfg: dict, path: str, lock=None, state: dict | None = None,
                        refresh: bool = False) -> list:
    """Rekordbox' Grid und Phrasen als TrackTabs eigenen Stand speichern, wo der fehlt.

    Je Feld: nur wenn weder eine Bearbeitung noch ein eigener Wert da ist. refresh:
    auch eine fruehere Uebernahme (origin "rekordbox") durch den aktuellen Rekordbox-
    Stand ersetzen (Rekordbox-Abgleich), eigene Messungen nie. Quelle ist der
    festgehaltene Stand (rb_snapshot), sonst Rekordbox live. lock: Server-Lock um den
    Schreibvorgang (die Arbeit davor laeuft ohne). Rueckgabe: uebernommene Felder.
    Wirft nie: ist Rekordbox oder die Datei nicht lesbar, wird nichts uebernommen."""
    guard = lock if lock is not None else contextlib.nullcontext()
    conn = db_mod.connect(cfg)
    try:
        edits = db_mod.get_dj_track(conn, path)
        stored = db_mod.get_phrases(conn, path)
        snap = db_mod.get_rb_snapshot(conn, path)
    finally:
        conn.close()

    def wants(field: str, origin) -> bool:
        if edits[field] is not None:
            return False
        return origin is None or (refresh and origin == ORIGIN_REKORDBOX)

    want_grid = wants("grid", grid_origin(stored))
    want_phrases = wants("phrases", phrases_origin(stored))
    if not want_grid and not want_phrases:
        return []

    def rb_value(field: str) -> list:
        if snap and snap[field] is not None and not refresh:
            return snap[field] or []
        nonlocal state
        if state is None:
            state = rekordbox_mod.track_state(path)
        if not state.get("available"):
            return (snap or {}).get(field) or []
        return state[field] or []

    try:
        rb_grid = rb_value("grid") if want_grid else []
        rb_phrases = rb_value("phrases") if want_phrases else []
    except Exception:                              # noqa: BLE001
        return []
    if not rb_grid and not rb_phrases:
        return []
    try:
        lead = rekordbox_lead(path)
    except Exception:                              # noqa: BLE001
        return []                                  # falscher Versatz waere schlimmer als nichts

    stored_beats = (edits["grid"] or {}).get("beats") if edits["grid"] is not None else (stored or {}).get("beats")
    beats = beats_to_own(rb_grid, lead) if rb_grid else (stored_beats or [])
    phrases = phrases_to_own(rb_phrases, lead) if rb_phrases else []
    if phrases and not rb_grid and beats:
        # Nur Phrasen aus Rekordbox, das Grid ist eigenes: auf dessen Takte einrasten.
        phrases = snap_phrases(phrases, beats, True)
    elif phrases:
        phrases = phrases_from_first_beat(phrases, beats)

    with guard:
        conn = db_mod.connect(cfg)
        try:
            # Erneut lesen: eine Messung kann inzwischen fertig geworden sein.
            edits = db_mod.get_dj_track(conn, path)
            stored = db_mod.get_phrases(conn, path)
            take = []
            if rb_grid and wants("grid", grid_origin(stored)):
                take.append("grid")
            if phrases and wants("phrases", phrases_origin(stored)):
                take.append("phrases")
            if not take:
                return []
            row = dict(stored) if stored else _seed_row(beats if "grid" in take else [], [])
            if "grid" in take:
                row.update({k: v for k, v in _seed_row(beats, []).items()
                            if k not in ("phrases", "mood", "phrases_origin")})
            if "phrases" in take:
                row.update({"phrases": phrases, "mood": None, "phrases_origin": ORIGIN_REKORDBOX})
            if not row.get("beats"):
                return []                          # Phrasen ohne jedes Grid nuetzen nichts
            if stored and all(stored.get(k) == row.get(k) for k in ("beats", "phrases")):
                return []
            db_mod.save_phrases(conn, path, row)
            return take
        finally:
            conn.close()


def seed_candidates(conn) -> list[str]:
    """Pfade mit festgehaltenem Rekordbox-Grid und ohne eigenes Grid."""
    rows = conn.execute(
        "SELECT s.path FROM rb_snapshot s "
        "LEFT JOIN phrases p ON p.path = s.path "
        "LEFT JOIN dj_track d ON d.path = s.path "
        "WHERE s.grid IS NOT NULL AND s.grid != '[]' "
        "AND (p.path IS NULL OR p.beats IS NULL OR p.beats = '[]') "
        "AND (d.path IS NULL OR d.grid IS NULL)").fetchall()
    return [r["path"] for r in rows]


class SeedJob:
    """Hintergrundlauf nach dem Rekordbox-Abgleich: alle Tracks mit Rekordbox-Grid und
    ohne eigenes uebernehmen. Je Track rund 0,3-0,5 s (der Versatz braucht eine
    Dekodierung), deshalb nicht im Abgleich selbst."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        # Pfade, deren fruehere Uebernahme neu aus Rekordbox gelesen werden soll (neue
        # Lesart nach dem Abgleich, server._rb_snapshot_reread()).
        self._refresh: set[str] = set()
        self.state = {"running": False, "done": 0, "total": 0, "seeded": 0, "error": None, "finished": None}

    def status(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, cfg: dict, server_lock=None) -> bool:
        with self._lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "done": 0, "total": 0, "seeded": 0, "error": None, "finished": None}
        self._cancel.clear()
        threading.Thread(target=self._run, args=(cfg, server_lock), daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def refresh_later(self, paths) -> None:
        with self._lock:
            self._refresh.update(paths)

    def _run(self, cfg: dict, server_lock) -> None:
        try:
            conn = db_mod.connect(cfg)
            try:
                with self._lock:
                    refresh, self._refresh = self._refresh, set()
                # Nur Uebernahmen nachziehen, eigene Messungen bleiben (seed_from_rekordbox).
                refresh = {p for p in refresh
                           if grid_origin(db_mod.get_phrases(conn, p)) == ORIGIN_REKORDBOX}
                todo = [(p, False) for p in seed_candidates(conn) if os.path.exists(p)]
                todo += [(p, True) for p in sorted(refresh) if os.path.exists(p)]
            finally:
                conn.close()
            with self._lock:
                self.state["total"] = len(todo)
            for path, again in todo:
                if self._cancel.is_set():
                    break
                took = seed_from_rekordbox(cfg, path, server_lock, refresh=again)
                # Wellenformen nicht mit tausenden Tracks fuellen, der Versatz bleibt
                # (spaeteres Rekordbox Update misst dann nicht erneut).
                wavedetail_mod.forget(path, keep_lead=True)
                with self._lock:
                    self.state["done"] += 1
                    self.state["seeded"] += bool(took)
        except Exception as exc:                   # noqa: BLE001
            with self._lock:
                self.state["error"] = str(exc) or exc.__class__.__name__
        finally:
            with self._lock:
                self.state["running"] = False
                self.state["finished"] = time.time()
