"""
Verbindet Probe, Spektralanalyse und Klassifikation zu einem Ergebnis-Dict.
Laeuft im Worker-Prozess, muss deshalb auf Modulebene importierbar bleiben.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict

from . import classify as classify_mod
from . import loudness as loudness_mod
from . import media
from . import probe as probe_mod
from . import spectral as spectral_mod
from . import taganomaly as taganomaly_mod
from . import tags as tags_mod

_HASH_CHUNK = 1024 * 1024  # 1 MiB, damit auch grosse WAV/FLAC keinen Speicher fressen


def _hash_file(path: str) -> str:
    """SHA-256 des kompletten Dateiinhalts -- fuer echte, byteidentische
    Kopien (gleicher Track unter zwei Pfaden). Ein Lesefehler darf die
    restliche Analyse nicht zum Scheitern bringen, leerer String wie bei
    anderen defensiven Faellen in dieser Datei."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_HASH_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


# Grund-Text einer Zeile, deren Cutoff-Messung im Scan abgewaehlt war. Daran
# erkennt jobs.run_scan() die Zeile wieder und misst beim naechsten Scan mit
# Cutoff nach (db.measurement_gaps()) -- Text und Erkennung haengen an dieser
# einen Konstante.
SPECTRAL_SKIPPED_REASON = "Cutoff-Scan übersprungen"


# Einstellungen, von denen das ERGEBNIS der beiden Dekodierdurchgaenge
# (spectral.analyse(), loudness.analyse()) abhaengt. Daraus bildet
# MeasureCache seinen Schluessel -- ein neuer Messparameter in spectral.py
# oder loudness.py gehoert hier eingetragen, sonst uebernimmt der Cache nach
# einer Parameteraenderung eine alte Messung. Die Einordnung (classify)
# laeuft immer frisch mit den aktuellen Einstellungen und steht hier nicht.
MEASURE_CONFIG_KEYS = (
    "_skip_spectral", "_skip_loudness", "block_percentile", "cliff_min_db",
    "cliff_span_hz", "edge_segment_s", "fft_size", "gate_abs_dbfs",
    "gate_rms_percentile", "loudness_clip_dbtp", "loudness_ref_high_lufs",
    "loudness_ref_low_lufs", "max_analysis_s", "min_gated_blocks",
    "noise_band_rel", "plateau_ceiling_hz", "plateau_min_ratio", "ref_band_hz",
    "search_floor_hz", "skip_head_pct", "skip_tail_pct", "smooth_hz",
    "spectrum_points", "steep_brickwall_db",
)


class MeasureCache:
    """Haelt die teuren Messungen (Spektrum + Lautheit, zusammen rund 2-3 s
    je Datei) fuer bereits gemessenen Audioinhalt fest.

    Schluessel = files.audio_hash (Pruefsumme der Audio-Pakete ohne Tags,
    media.audio_fingerprint()) + die Messparameter (MEASURE_CONFIG_KEYS).
    Gleiche Pakete -> gleiche Dekodierung -> gleiche Messung, unabhaengig von
    Pfad, Dateiname und Tags. Anlass ist der Import einer Einzelpruefung in
    die Music-Bibliothek: Music.app legt eine Kopie in seinem Medienordner an
    (neuer Pfad, ggf. andere Tags), die dort bisher noch einmal vollstaendig
    gemessen wurde, obwohl die Einzelpruefung dieselbe Messung gerade erst
    gemacht hatte.

    Nur im Serverprozess benutzen (die Worker eines Scans bekommen keinen
    Cache, siehe analyse_file()). Probe, Tags, Auffaelligkeiten, Pruefsummen
    und die Einordnung werden auch bei einem Treffer frisch gebildet -- die
    kosten zusammen rund 0,1-0,2 s.
    """

    LIMIT = 300            # Eintraege; je Eintrag ein paar KB (Spektrum-Stuetzpunkte)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict = OrderedDict()

    @staticmethod
    def key(audio_hash: str, cfg: dict, pr) -> str | None:
        if not audio_hash:
            return None              # ffmpeg scheiterte: Inhalt unbekannt, nie wiederverwenden
        # Stream-Angaben aus der Probe gehen als Argumente in die Messung ein
        # (Samplerate, Kanaele, Dauer) -- sicherheitshalber mit in den Schluessel.
        params = json.dumps([cfg.get(k) for k in MEASURE_CONFIG_KEYS]
                            + [pr.sample_rate, pr.channels, round(pr.duration_s, 2)],
                            default=str)
        return audio_hash + "|" + hashlib.sha1(params.encode("utf-8")).hexdigest()

    def get(self, key: str | None):
        if key is None:
            return None
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            self._items.move_to_end(key)
        # Kopie: die Listen (Spektrum, Abschnittskanten) wandern in die Zeile.
        return copy.deepcopy(hit)

    def put(self, key: str | None, value) -> None:
        if key is None:
            return
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.LIMIT:
                self._items.popitem(last=False)


def blank_row(path: str, size: int, mtime: float) -> dict:
    """Grundzeile mit status 'error' und allen Spalten auf ihrer Vorgabe.

    Eigene Funktion, damit auch der Scan (jobs.run_scan) eine vollstaendige
    Fehlerzeile bauen kann, wenn ein Worker gar kein Ergebnis liefert --
    eine Zeile mit lauter None-Spalten wuerde Report und Oberflaeche stoeren.
    """
    return {
        "path": path, "size": size, "mtime": mtime,
        "status": "error", "error": "", "duration_s": 0.0,
        "declared_kbps": 0, "bitrate_mode": "", "sample_rate": 0, "channels": 0,
        "codec": "", "codec_family": "",
        "encoder": "", "lame_lowpass_hz": 0,
        "artist": "", "title": "", "album": "",
        "genre": "", "bpm": 0.0, "has_cover": 0,
        "album_artist": "", "composer": "", "year": 0, "comment": "",
        "track_no": 0, "track_total": 0, "key": "", "key_raw": "",
        "cover_hash": "", "compilation": 0,
        "cutoff_hz": 0.0, "raw_cutoff_hz": 0.0, "steepness_db": 0.0,
        "plateau_db": 0.0, "edge_segments": [],
        "is_brickwall": 0, "at_nyquist": 0,
        "gated_blocks": 0, "total_blocks": 0,
        "measured_kbps": 0, "declared_class": 0, "class_steps": 0,
        "verdict": classify_mod.VERDICT_UNKNOWN, "confidence": 0.0,
        "reasons": [], "spectrum": [], "tag_issues": [],
        "integrated_lufs": 0.0, "true_peak_dbtp": 0.0, "lra_lu": 0.0,
        "file_hash": "", "audio_hash": "",
    }


def analyse_file(job: tuple, cache: MeasureCache | None = None) -> dict:
    """Eine Datei vollstaendig messen. 'cache' (optional, nur im
    Serverprozess): bereits gemessenen Audioinhalt nicht erneut dekodieren,
    siehe MeasureCache. Ohne Cache (Scan-Worker) wie bisher."""
    path, size, mtime, cfg = job
    row = blank_row(path, size, mtime)

    try:
        pr = probe_mod.probe(path)
    except Exception as exc:                       # noqa: BLE001
        row["error"] = f"probe: {exc}"
        row["reasons"] = [row["error"]]
        return row

    row.update({
        "duration_s": pr.duration_s,
        "declared_kbps": pr.declared_kbps,
        "bitrate_mode": pr.bitrate_mode,
        "sample_rate": pr.sample_rate,
        "channels": pr.channels,
        "codec": pr.codec,
        "codec_family": pr.codec_family,
        "encoder": pr.encoder,
        "lame_lowpass_hz": pr.lame_lowpass_hz,
        "artist": pr.artist,
        "title": pr.title,
        "album": pr.album,
    })
    row.update(tags_mod.read_extra(path))
    row["tag_issues"] = taganomaly_mod.detect(path, pr.codec_family)

    if not pr.ok:
        row["error"] = pr.error
        row["reasons"] = [pr.error]
        return row

    row["file_hash"] = _hash_file(path)
    # Kennung des Audioinhalts ohne Tags: daran erkennt db.save(), dass unter
    # demselben Pfad eine andere Aufnahme liegt (siehe dort).
    row["audio_hash"] = media.audio_fingerprint(path)

    if pr.duration_s < float(cfg["min_duration_s"]):
        row["status"] = "ok"
        row["error"] = ""
        row["verdict"] = classify_mod.VERDICT_UNKNOWN
        row["reasons"] = [f"Zu kurz für eine Beurteilung ({pr.duration_s:.0f} s)"]
        return row

    cache_key = MeasureCache.key(row["audio_hash"], cfg, pr) if cache is not None else None
    cached = cache.get(cache_key) if cache is not None else None
    sr = lr = None

    if cfg.get("_skip_spectral"):
        # Cutoff Scan abgewaehlt -- nur Metadaten, kein ffmpeg-Dekodierdurchgang.
        # verdict bleibt beim Default VERDICT_UNKNOWN aus der row-Vorlage oben.
        row["status"] = "ok"
        row["error"] = ""
        row["reasons"] = [SPECTRAL_SKIPPED_REASON]
    else:
        if cached is not None:
            sr, lr = cached
        else:
            try:
                sr = spectral_mod.analyse(path, pr.sample_rate, pr.duration_s, cfg,
                                           pr.channels or 2)
            except Exception as exc:                       # noqa: BLE001
                row["error"] = f"spectral: {exc}"
                row["reasons"] = [row["error"]]
                return row

        row.update({
            "cutoff_hz": sr.cutoff_hz,
            "raw_cutoff_hz": sr.raw_cutoff_hz,
            "steepness_db": sr.steepness_db,
            "plateau_db": sr.plateau_db,
            "edge_segments": sr.edge_segments,
            "is_brickwall": int(sr.is_brickwall),
            "at_nyquist": int(sr.at_nyquist),
            "gated_blocks": sr.gated_blocks,
            "total_blocks": sr.total_blocks,
            "spectrum": sr.spectrum,
        })

        verdict = classify_mod.classify(pr, sr, cfg)
        row.update({
            "measured_kbps": verdict.measured_kbps,
            "declared_class": verdict.declared_class,
            "class_steps": verdict.class_steps,
            "verdict": verdict.verdict,
            "confidence": verdict.confidence,
            "reasons": verdict.reasons,
        })

        if sr.ok:
            row["status"] = "ok"
            row["error"] = ""
        else:
            row["error"] = sr.error

    # Eigener Dekodierdurchgang, unabhaengig vom Tiefpass-Verdikt oben --
    # ein Fehlschlag hier darf den Rest der Zeile nicht entwerten.
    if not cfg.get("_skip_loudness"):
        if cached is None:
            try:
                lr = loudness_mod.analyse(path, pr.duration_s, cfg)
            except Exception:                              # noqa: BLE001
                lr = None
        if lr is not None and lr.ok:
            row.update({
                "integrated_lufs": lr.integrated_lufs,
                "true_peak_dbtp": lr.true_peak_dbtp,
                "lra_lu": lr.lra_lu,
            })

    if cached is None and cache is not None and sr is not None:
        cache.put(cache_key, copy.deepcopy((sr, lr)))

    return row


# Cache des Serverprozesses (siehe MeasureCache). Scan-Worker legen beim
# Import zwar ein eigenes leeres Exemplar an, benutzen es aber nie.
MEASURE_CACHE = MeasureCache()
