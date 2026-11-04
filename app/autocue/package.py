"""
Ein Auto-Cue-Modell als ZIP: schreiben (Training) und in die App laden (Einstellungen).

So kommt ein neues Modell in eine installierte App, ohne eine neue DMG zu bauen.
Geladen wird nach <base_dir>/models/autocue/ (autocue.trained_dir()), wie das
Stem-Modell; es gilt, solange es juenger ist als das mitgelieferte
(autocue.model_dir()).

Die Datei kommt von aussen, deshalb streng: nur die bekannten Dateinamen, keine
Ordner, Groessen gedeckelt, jede .npz als Baumpaket ladbar, Merkmalsversion wie
in dieser App -- ein Modell fuer eine andere Merkmalsrechnung rechnete still falsch.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import time
import zipfile

from app.autocue import MANIFEST, _MODEL_FILES

ALLOWED = set(_MODEL_FILES) | {"chain_outro.npz", MANIFEST}
_MAX_FILE = 64 * 1024 * 1024
_MAX_TOTAL = 160 * 1024 * 1024
KEEP_BACKUPS = 3


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


def install(data: bytes, target: str, backup_root: str) -> dict:
    """Pruefen, alten Stand sichern, ersetzen. Rueckgabe: Manifest des neuen Modells."""
    files, manifest = check(data)
    if os.path.isdir(target) and os.listdir(target):
        dest = os.path.join(backup_root, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(target, dest, dirs_exist_ok=True)
        old = sorted(p for p in os.listdir(backup_root) if os.path.isdir(os.path.join(backup_root, p)))
        for p in old[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(backup_root, p), ignore_errors=True)
    tmp = target + ".neu"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for name, raw in files.items():
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(raw)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.rename(tmp, target)
    return manifest


def remove(target: str, backup_root: str) -> bool:
    """Eigenes Modell entfernen (gesichert); danach gilt wieder das mitgelieferte."""
    if not os.path.isdir(target):
        return False
    dest = os.path.join(backup_root, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(target, dest)
    return True
