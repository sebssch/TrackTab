"""
Einordnung: gemessener Cutoff -> Bitrate-Klasse -> Verdikt.

Grundgedanke: Ein Transcode von 128 auf 320 kbps kann die beim ersten
Encoding verworfenen Hoehen nicht zurueckholen. Der Tiefpass des
Original-Encoders ueberlebt jede spaetere Umkodierung. Liegt die gemessene
Klasse deutlich unter der deklarierten Bitrate, wurde hochgerechnet.

Wichtig: Es gibt echte 320er mit niedrigem Cutoff (bandbegrenzte Master,
Vinyl-Rips, alte Aufnahmen). Das Ergebnis ist eine Pruefliste, kein Urteil.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VERDICT_OK = "OK"
VERDICT_SUSPECT = "VERDAECHTIG"
VERDICT_FAKE = "FAKE"
VERDICT_UNKNOWN = "UNKLAR"

VERDICT_ORDER = {VERDICT_FAKE: 0, VERDICT_SUSPECT: 1, VERDICT_UNKNOWN: 2, VERDICT_OK: 3}


@dataclass
class Verdict:
    verdict: str = VERDICT_UNKNOWN
    measured_kbps: int = 0        # Bitrate-Klasse laut Spektrum
    declared_class: int = 0       # Deklarierte Bitrate, auf Klasse gerundet
    class_steps: int = 0          # Wie viele Klassen unter der Deklaration
    confidence: float = 0.0
    reasons: list = field(default_factory=list)


_FAMILY_LADDER = {
    "lossy_mp3": "mp3",
    "lossy_aac": "aac",
    "lossy_other": "mp3",   # unbekannter Codec: MP3-Leiter als konservativer Fallback
}


def _class_ladder(cfg: dict, ladder: str = "mp3") -> list[int]:
    return [int(c["kbps"]) for c in cfg["cutoff_classes"][ladder]]  # absteigend: 320,256,...


def measured_class(cutoff_hz: float, cfg: dict, ladder: str = "mp3") -> int:
    khz = cutoff_hz / 1000.0
    classes = cfg["cutoff_classes"][ladder]
    for entry in classes:                                     # absteigend sortiert
        if khz >= float(entry["min_khz"]):
            return int(entry["kbps"])
    return int(classes[-1]["kbps"])


def declared_class(declared_kbps: int, cfg: dict, ladder: str = "mp3") -> int:
    """Deklarierte Bitrate auf die naechstniedrigere Klasse abbilden.

    Bewusst nach unten gerundet: eine VBR-Datei mit 271 kbps wird als
    256er-Klasse gewertet, damit legitime VBR-Encodings nicht auffallen.
    """
    steps = _class_ladder(cfg, ladder)
    for kbps in steps:
        if declared_kbps >= kbps:
            return kbps
    return steps[-1]


def classify(probe_res, spec_res, cfg: dict) -> Verdict:
    v = Verdict()

    if not spec_res.ok:
        v.verdict = VERDICT_UNKNOWN
        v.reasons.append(spec_res.error or "Keine Spektraldaten")
        return v

    codec_family = getattr(probe_res, "codec_family", "lossy_mp3") or "lossy_mp3"
    if codec_family == "lossless":
        return _classify_lossless(probe_res, spec_res, cfg)

    ladder_name = _FAMILY_LADDER.get(codec_family, "mp3")
    ladder = _class_ladder(cfg, ladder_name)
    v.measured_kbps = measured_class(spec_res.cutoff_hz, cfg, ladder_name)
    v.declared_class = declared_class(probe_res.declared_kbps, cfg, ladder_name)
    v.class_steps = ladder.index(v.measured_kbps) - ladder.index(v.declared_class)

    cutoff_khz = spec_res.cutoff_hz / 1000.0
    if v.class_steps >= int(cfg["verdict_fake_steps"]):
        v.verdict = VERDICT_FAKE
        v.reasons.append(
            f"Cutoff {cutoff_khz:.1f} kHz entspricht ~{v.measured_kbps} kbps, "
            f"deklariert sind {probe_res.declared_kbps} kbps"
        )
    elif v.class_steps >= int(cfg["verdict_suspect_steps"]):
        v.verdict = VERDICT_SUSPECT
        v.reasons.append(
            f"Cutoff {cutoff_khz:.1f} kHz liegt eine Klasse unter den "
            f"deklarierten {probe_res.declared_kbps} kbps"
        )
    else:
        v.verdict = VERDICT_OK
        v.reasons.append(f"Cutoff {cutoff_khz:.1f} kHz passt zur Deklaration")

    # --- LAME-Gegenprobe ---------------------------------------------------
    # Der Encoder traegt seinen eigenen Lowpass in den LAME-Header ein. Liegt
    # eine harte Kante deutlich darunter, stammt sie aus einem frueheren
    # Encoding — der staerkste Einzelhinweis auf einen Transcode.
    tol_hz = float(cfg["lame_lowpass_tolerance_khz"]) * 1000.0
    lame_mismatch = False
    if probe_res.lame_lowpass_hz > 0:
        gap = probe_res.lame_lowpass_hz - spec_res.cutoff_hz
        if gap > tol_hz:
            lame_mismatch = True
            v.reasons.append(
                f"LAME-Header sagt Lowpass {probe_res.lame_lowpass_hz/1000:.1f} kHz, "
                f"gemessen sind {cutoff_khz:.1f} kHz"
            )
            if v.verdict == VERDICT_OK and spec_res.is_brickwall:
                v.verdict = VERDICT_SUSPECT

    if spec_res.is_brickwall:
        v.reasons.append(f"Harte Kante ({spec_res.steepness_db:.0f} dB Abfall)")
    elif v.verdict in (VERDICT_FAKE, VERDICT_SUSPECT):
        v.reasons.append(
            f"Weicher Übergang ({spec_res.steepness_db:.0f} dB) — kann auch ein "
            "bandbegrenztes Master sein"
        )

    enc = (probe_res.encoder or "").lower()
    if enc.startswith(("lavc", "lavf")):
        v.reasons.append(f"Mit ffmpeg umkodiert ({probe_res.encoder})")

    v.confidence = _confidence(spec_res, v, lame_mismatch, cfg)
    return v


def _classify_lossless(probe_res, spec_res, cfg: dict) -> Verdict:
    """Modus B: verlustfreie Formate (ALAC/FLAC/WAV/AIFF) haben keine
    deklarierte Bitrate, mit der man vergleichen koennte. Der einzige
    Hinweis auf eine Lossy-Quelle ist eine harte Encoder-Kante im Spektrum —
    ihr Vorhandensein zaehlt, nicht ein fester kHz-Wert (ein echtes
    bandbegrenztes Master rollt weich aus statt abzuschneiden)."""
    v = Verdict()
    # spec_res.cutoff_hz greift auf Nyquist zurueck, sobald die Kante unter
    # cliff_min_db (MP3-kalibriert) liegt -- die tatsaechliche Position geht
    # dabei verloren. raw_cutoff_hz umgeht das und liefert die echte
    # Kantenposition unabhaengig von diesem Schwellwert.
    raw_cutoff_khz = getattr(spec_res, "raw_cutoff_hz", spec_res.cutoff_hz) / 1000.0

    # Eigene, niedrigere Schwelle statt spec_res.is_brickwall (die ist auf
    # MP3 kalibriert): 16-Bit-PCM quantisiert nahezu stille Bloecke oberhalb
    # des Cutoffs grob, was die gemessene Flanke gegenueber einer direkt
    # dekodierten MP3 deutlich verflacht.
    has_edge = spec_res.steepness_db >= float(cfg["lossless_min_steepness_db"])
    if not has_edge:
        v.verdict = VERDICT_OK
        v.reasons.append(
            f"Keine harte Kante gefunden (Cutoff {spec_res.cutoff_hz/1000:.1f} kHz) — "
            "spricht für echtes verlustfreies Material"
        )
        v.confidence = _confidence(spec_res, v, False, cfg, has_edge)
        return v

    cutoff_khz = raw_cutoff_khz
    # Nur zur Einordnung im Text/UI: an welche verlustbehaftete Quelle
    # erinnert diese Kante am ehesten? Kein Vergleichswert, keine
    # Verdikt-Grundlage — die MP3-Leiter dient hier rein als Referenzskala.
    approx_kbps = measured_class(raw_cutoff_khz * 1000.0, cfg, "mp3")
    v.measured_kbps = approx_kbps

    if cutoff_khz < float(cfg["lossless_fake_khz"]):
        v.verdict = VERDICT_FAKE
        v.reasons.append(
            f"Harte Kante bei {cutoff_khz:.1f} kHz in verlustfreiem Container — "
            f"erinnert an eine ~{approx_kbps} kbps Quelle"
        )
    elif cutoff_khz < float(cfg["lossless_suspect_khz"]):
        v.verdict = VERDICT_SUSPECT
        v.reasons.append(
            f"Harte Kante bei {cutoff_khz:.1f} kHz in verlustfreiem Container — "
            f"erinnert an eine ~{approx_kbps} kbps Quelle"
        )
    else:
        v.verdict = VERDICT_OK
        v.reasons.append(
            f"Kante bei {cutoff_khz:.1f} kHz liegt nah an der Bandgrenze"
        )

    v.reasons.append(f"Harte Kante ({spec_res.steepness_db:.0f} dB Abfall)")
    v.confidence = _confidence(spec_res, v, False, cfg, has_edge)
    return v


def _confidence(spec_res, v: Verdict, lame_mismatch: bool, cfg: dict,
                 has_edge: bool | None = None) -> float:
    """Wie belastbar ist das Urteil? 0.0 = Rateschaetzung, 1.0 = eindeutig.

    has_edge: welcher Schwellwert "harte Kante" bedeutet. Default
    spec_res.is_brickwall (MP3-Schwelle); der Lossless-Zweig uebergibt
    seine eigene, niedrigere Schwelle, sonst wuerde jedes Lossless-Verdikt
    unfair abgewertet (MP3-Brickwalls sind deutlich steiler).
    """
    if has_edge is None:
        has_edge = spec_res.is_brickwall
    score = 0.45

    # Datenbasis
    if spec_res.gated_blocks >= 60:
        score += 0.20
    elif spec_res.gated_blocks >= 20:
        score += 0.12
    elif spec_res.gated_blocks < int(cfg["min_gated_blocks"]):
        score -= 0.20

    # Eine harte Kante ist ein Encoder-Artefakt, kein Musikinhalt
    if has_edge:
        score += 0.25
    elif v.verdict in (VERDICT_FAKE, VERDICT_SUSPECT):
        score -= 0.15

    if lame_mismatch:
        score += 0.15

    # Je weiter die Klassen auseinanderliegen, desto eindeutiger
    if v.class_steps >= 2:
        score += 0.10

    # Inhalt bis an die Bandgrenze: gar kein Tiefpass messbar
    if spec_res.at_nyquist:
        score += 0.05

    return round(min(1.0, max(0.05, score)), 2)


# ── Neubewertung ohne neue Messung ────────────────────────────────────────
# Aendern sich nur die Klassengrenzen, muss keine Datei erneut dekodiert
# werden: Cutoff und Flankenhoehe stehen bereits in der Datenbank. Statt 22
# Minuten dauert das Sekunden.

def from_row(row, cfg: dict):
    """Eine gespeicherte Zeile in die Objekte uebersetzen, die classify() erwartet."""
    from types import SimpleNamespace

    steepness = float(row["steepness_db"] or 0.0)
    probe = SimpleNamespace(
        declared_kbps=int(row["declared_kbps"] or 0),
        lame_lowpass_hz=int(row["lame_lowpass_hz"] or 0),
        encoder=row["encoder"] or "",
        # Vor diesem Feature gescannte Zeilen haben kein codec_family -> als
        # MP3 behandeln, das war bislang das einzig unterstuetzte Format.
        codec_family=row["codec_family"] or "lossy_mp3",
    )
    cutoff_hz = float(row["cutoff_hz"] or 0.0)
    spec = SimpleNamespace(
        ok=(row["status"] == "ok" and (row["cutoff_hz"] or 0) > 0),
        error=row["error"] or "",
        cutoff_hz=cutoff_hz,
        # Vor diesem Feature gescannte Zeilen haben kein raw_cutoff_hz ->
        # cutoff_hz ist dort der naechstbeste Ersatz.
        raw_cutoff_hz=float(row["raw_cutoff_hz"] or 0.0) or cutoff_hz,
        steepness_db=steepness,
        # Schwelle neu anwenden — sie kann sich geaendert haben
        is_brickwall=steepness >= float(cfg["steep_brickwall_db"]),
        at_nyquist=bool(row["at_nyquist"]),
        gated_blocks=int(row["gated_blocks"] or 0),
    )
    return probe, spec


def reclassify_all(conn, cfg: dict) -> dict:
    """Alle gespeicherten Messwerte neu einordnen. Ruehrt keine Datei an."""
    import json as _json

    rows = list(conn.execute("SELECT * FROM files"))
    updates = []
    changed = 0
    for row in rows:
        probe, spec = from_row(row, cfg)
        v = classify(probe, spec, cfg)
        if v.verdict != row["verdict"] or v.measured_kbps != row["measured_kbps"]:
            changed += 1
        updates.append((
            v.measured_kbps, v.declared_class, v.class_steps, v.verdict,
            v.confidence, _json.dumps(v.reasons, ensure_ascii=False),
            int(spec.is_brickwall), row["path"],
        ))

    conn.executemany(
        "UPDATE files SET measured_kbps=?, declared_class=?, class_steps=?, "
        "verdict=?, confidence=?, reasons=?, is_brickwall=? WHERE path=?",
        updates,
    )
    conn.commit()

    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT verdict, COUNT(*) FROM files GROUP BY verdict")}
    return {"total": len(rows), "changed": changed, "counts": counts}
