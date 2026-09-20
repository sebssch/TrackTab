"""
Verbindet Probe, Spektralanalyse und Klassifikation zu einem Ergebnis-Dict.
Laeuft im Worker-Prozess, muss deshalb auf Modulebene importierbar bleiben.
"""
from __future__ import annotations

import hashlib

from . import classify as classify_mod
from . import loudness as loudness_mod
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


def analyse_file(job: tuple) -> dict:
    path, size, mtime, cfg = job

    row = {
        "path": path, "size": size, "mtime": mtime,
        "status": "error", "error": "", "duration_s": 0.0,
        "declared_kbps": 0, "bitrate_mode": "", "sample_rate": 0, "channels": 0,
        "codec": "", "codec_family": "",
        "encoder": "", "lame_lowpass_hz": 0,
        "artist": "", "title": "", "album": "",
        "genre": "", "bpm": 0.0, "has_cover": 0,
        "album_artist": "", "composer": "", "year": 0, "comment": "",
        "track_no": 0, "track_total": 0,
        "cutoff_hz": 0.0, "raw_cutoff_hz": 0.0, "steepness_db": 0.0,
        "is_brickwall": 0, "at_nyquist": 0,
        "gated_blocks": 0, "total_blocks": 0,
        "measured_kbps": 0, "declared_class": 0, "class_steps": 0,
        "verdict": classify_mod.VERDICT_UNKNOWN, "confidence": 0.0,
        "reasons": [], "spectrum": [], "tag_issues": [],
        "integrated_lufs": 0.0, "true_peak_dbtp": 0.0, "lra_lu": 0.0,
        "file_hash": "",
    }

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

    if pr.duration_s < float(cfg["min_duration_s"]):
        row["status"] = "ok"
        row["error"] = ""
        row["verdict"] = classify_mod.VERDICT_UNKNOWN
        row["reasons"] = [f"Zu kurz für eine Beurteilung ({pr.duration_s:.0f} s)"]
        return row

    if cfg.get("_skip_spectral"):
        # Cutoff Scan abgewaehlt -- nur Metadaten, kein ffmpeg-Dekodierdurchgang.
        # verdict bleibt beim Default VERDICT_UNKNOWN aus der row-Vorlage oben.
        row["status"] = "ok"
        row["error"] = ""
        row["reasons"] = ["Cutoff-Scan übersprungen"]
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

    return row
