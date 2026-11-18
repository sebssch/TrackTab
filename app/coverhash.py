"""Cover-Fingerabdruck und Gruppierung gleicher Cover.

files.cover_hash ist KEIN Pruefsummen-Hash der Bytes, sondern ein Mini-Bild
des Covers: 8x8 Pixel RGB (192 Bytes, als 384 Hex-Zeichen), siehe
tags.cover_fingerprint(). Grund: Music.app kodiert ein Cover beim Setzen neu
und schreibt ihr eigenes JPEG zurueck in die Datei -- bei jedem Track mit
anderen Bytes. Optisch gleiche Cover haetten dann nie denselben Byte-Hash.

Zwei Cover gelten als gleich, wenn sich ihre Mini-Bilder im Mittel um
hoechstens MAX_MAD Helligkeitsstufen (0-255) je Farbwert unterscheiden --
bei kontrastarmen Bildern weniger (REL_MAD x Streuung, mindestens MIN_MAD).
canonical() bildet jedes Mini-Bild auf die Kennung eines Gruppen-Vertreters
ab; verglichen wird nur mit Vertretern, nicht mit jedem Mitglied (sonst
wuerden Gruppen ueber Ketten kleiner Schritte zusammenwachsen). Der Client
vergleicht nur diese Kennung (r.ch) auf Gleichheit und kennt die Toleranz nicht.

Gemessen an echtem Material (250 Cover, je fuenffach neu kodiert, JPEG
Qualitaet 60-95, teils verkleinert): Neukodierung p99 3,6, schlimmster Fall
6,2; verschiedene Cover ab 9,8; zwei Singles im selben Layout mit anderem
Text 6,6. Ein 64-Bit-dHash (erster Ansatz) trennte schlechter: Neukodierung
bis 11 Bit, aber verschiedene, dunkle Cover schon bei 0-4 Bit.
"""
from __future__ import annotations

import bisect
import hashlib
import threading

import numpy as np

THUMB_SIZE = 8
THUMB_BYTES = THUMB_SIZE * THUMB_SIZE * 3
FINGERPRINT_HEX = THUMB_BYTES * 2          # 384
MAX_MAD = 5.0
# Kontrastarme Cover (fast schwarz/weiss/einfarbig) liegen auch als
# verschiedene Bilder nah beieinander -- dort gilt eine Schwelle relativ zur
# Streuung des Mini-Bilds (Neukodierung gemessen: hoechstens ~0,1 x Streuung).
REL_MAD = 0.15
MIN_MAD = 1.0

_LOCK = threading.Lock()
_canon: dict[str, str] = {}                # Fingerabdruck (hex) -> Kennung
# Vertreter, sortiert nach Mittelwert: |Mittelwert a - Mittelwert b| ist eine
# untere Schranke der mittleren Abweichung -- nur Vertreter in diesem Fenster
# kommen in Frage (exakt, kein Treffer geht verloren).
_rep_means: list[float] = []
_rep_rows: list[np.ndarray] = []
_rep_stds: list[float] = []
_rep_ids: list[str] = []


def is_fingerprint(value: str | None) -> bool:
    """True fuer einen Fingerabdruck im aktuellen Format. Alte Eintraege
    (MD5 der Bytes, 32 Zeichen) muessen neu gelesen werden."""
    if not value or len(value) != FINGERPRINT_HEX:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def canonical(value: str | None) -> str:
    """Kennung (12 Hex-Zeichen) fuer einen Fingerabdruck: die des naechsten
    Vertreters mit mittlerer Abweichung <= MAX_MAD, sonst wird das Cover
    Vertreter einer neuen Gruppe. Leer bleibt leer; ein altes Format wird bis
    zum Nachtragen (naechster Scan) gekuerzt durchgereicht.

    Prozessweit gemerkt, damit eine einzelne, nach einem Cover-Schreiben neu
    gebaute Zeile dieselbe Kennung bekommt wie im gebackenen Report."""
    if not value:
        return ""
    if not is_fingerprint(value):
        return value[:12]
    value = value.lower()
    with _LOCK:
        known = _canon.get(value)
        if known is not None:
            return known
        row = np.frombuffer(bytes.fromhex(value), dtype=np.uint8).astype(np.int16)
        mean = float(row.mean())
        std = float(row.std())
        lo = bisect.bisect_left(_rep_means, mean - MAX_MAD)
        hi = bisect.bisect_right(_rep_means, mean + MAX_MAD)
        result = ""
        if hi > lo:
            mads = np.abs(np.stack(_rep_rows[lo:hi]) - row).mean(axis=1)
            limit = np.clip(REL_MAD * np.minimum(np.array(_rep_stds[lo:hi]), std), MIN_MAD, MAX_MAD)
            ok = np.flatnonzero(mads <= limit)
            if ok.size:
                result = _rep_ids[lo + int(ok[mads[ok].argmin()])]
        if not result:
            result = hashlib.md5(value.encode("ascii")).hexdigest()[:12]
            pos = bisect.bisect_right(_rep_means, mean)
            _rep_means.insert(pos, mean)
            _rep_rows.insert(pos, row)
            _rep_stds.insert(pos, std)
            _rep_ids.insert(pos, result)
        _canon[value] = result
        return result


def register_all(values) -> None:
    """Alle Fingerabdruecke eines Report-Baus in fester Reihenfolge anmelden,
    damit die Gruppierung nicht von der Zeilenreihenfolge abhaengt."""
    for v in sorted({v.lower() for v in values if is_fingerprint(v)}):
        canonical(v)
