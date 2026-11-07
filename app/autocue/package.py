"""
Ein Auto-Cue-Modell als ZIP: schreiben (Training) und in die App laden (Einstellungen).

So kommt ein Modell von einem Rechner auf einen anderen, ohne eine neue DMG zu
bauen. Ein Import legt einen neuen Eintrag der Modellbibliothek an (library.py);
gelten wird er erst, wenn er ausgewaehlt ist (config["autocue_model"]).

Die Datei kommt von aussen, deshalb streng: nur die bekannten Dateinamen, keine
Ordner, Groessen gedeckelt, jede .npz als Baumpaket ladbar, Merkmalsversion wie
in dieser App -- ein Modell fuer eine andere Merkmalsrechnung rechnete still falsch.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

from app.autocue import _CHAIN_MODEL_FILES, MANIFEST, _MODEL_FILES

ALLOWED = set(_MODEL_FILES) | set(_CHAIN_MODEL_FILES) | {MANIFEST}
_MAX_FILE = 64 * 1024 * 1024
_MAX_TOTAL = 160 * 1024 * 1024


class PackageError(ValueError):
    pass


def write_zip(folder: str, dest: str) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(folder)):
            if name in ALLOWED:
                zf.write(os.path.join(folder, name), arcname=name)
    return dest


def _read(data: bytes) -> dict[str, bytes]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("Keine gültige ZIP-Datei.") from None
    files, total = {}, 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if "/" in name or "\\" in name or name not in ALLOWED:
            raise PackageError(f"Unerwartete Datei im Modell: {name}")
        if info.file_size > _MAX_FILE:
            raise PackageError(f"{name} ist zu groß.")
        total += info.file_size
        if total > _MAX_TOTAL:
            raise PackageError("Das Modell ist zu groß.")
        files[name] = zf.read(info)
    return files


def check(data: bytes) -> tuple[dict[str, bytes], dict]:
    """Inhalt pruefen. Rueckgabe (Dateien, Manifest); PackageError mit anzeigbarem Text."""
    from app.autocue import learned, trees
    files = _read(data)
    missing = [f for f in _MODEL_FILES if f not in files]
    if missing:
        raise PackageError(f"Im Modell fehlt: {', '.join(missing)}")
    if MANIFEST not in files:
        raise PackageError("Im Modell fehlt manifest.json (Stand des Trainings).")
    try:
        manifest = json.loads(files[MANIFEST])
        meta = json.loads(files["features.json"])
        json.loads(files["calibration.json"])
    except ValueError:
        raise PackageError("Beschreibung des Modells ist beschädigt.") from None
    if not str(manifest.get("trained_at") or ""):
        raise PackageError("manifest.json nennt keinen Trainingszeitpunkt.")
    bar, chain = meta.get("bar") or {}, meta.get("chain") or {}
    if int(bar.get("feature_version", -1)) != learned.FEATURE_VERSION or \
            int(chain.get("feature_version", -1)) != learned.CHAIN_FEATURE_VERSION:
        raise PackageError("Das Modell passt nicht zu dieser TrackTab-Version (andere Merkmalsrechnung).")
    for name, raw in files.items():
        if name.endswith(".npz"):
            tmp = io.BytesIO(raw)
            try:
                import numpy as np
                with np.load(tmp) as z:
                    model = {k: z[k] for k in z.files}
                trees.raw_score(model, np.zeros((1, int(model["n_features"]))))
            except Exception:                      # noqa: BLE001
                raise PackageError(f"{name} ist kein gültiges Modell.") from None
    return files, manifest


def install(data: bytes, name: str = "") -> tuple[str, dict]:
    """Pruefen und als neuen Eintrag der Modellbibliothek ablegen (library.py).
    Rueckgabe (id, Manifest). Aktiv wird er erst durch die Auswahl
    (ausser es ist das erste Modell, library.add_files())."""
    from app.autocue import library
    files, manifest = check(data)
    model_id = library.add_files(files, name, "imported")
    return model_id, manifest


def export(model_id: str, dest: str) -> str:
    """ZIP eines Bibliothekseintrags schreiben."""
    from app.autocue import library
    folder = library.folder_of(model_id)
    if folder is None:
        raise PackageError("Dieses Modell gibt es nicht mehr.")
    return write_zip(folder, dest)
