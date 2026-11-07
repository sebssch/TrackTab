"""
Modellbibliothek fuer Auto Cue: die eigenen Modelle einer Installation.

Ablage <base_dir>/models/autocue-library/<id>/, je Ordner die Modelldateien
(autocue._MODEL_FILES) plus manifest.json. Die id ist der Zeitstempel des
Eintragens (20261104-153012, bei Gleichstand mit -2, -3 ...); der Anzeigename
steht im Manifest ("name") und laesst sich aendern, ohne den Ordner anzufassen.

Ein mitgeliefertes Modell gibt es nicht mehr (frueher vendor/models/autocue/,
Eintrag "bundled") -- jede Installation trainiert nach ihren eigenen Cues.
Welches Modell gilt, steht in config["autocue_model"] -- explizit gewaehlt, nicht
das juengste, sonst machte jedes weitere Training sich selbst zum geltenden.
Damit die Wahl nie ins Leere zeigt, wenn es Modelle gibt: der erste Eintrag
wird beim Ablegen gewaehlt (add_files()), nach dem Loeschen des geltenden der
juengste verbliebene (delete()), und eine alte Wahl "bundled" ersetzt
migrate() einmalig durch den juengsten eigenen.

Vorher gab es genau ein eigenes Modell in <base_dir>/models/autocue/
(autocue.trained_dir()). migrate() macht daraus einmalig einen
Bibliothekseintrag.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time

from app import config as cfgmod

KEEP_DELETED = 3
_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{1,3})?$")
_LOCK = threading.RLock()
_migrated: set[str] = set()


def library_dir() -> str:
    return str(cfgmod.base_dir() / "models" / "autocue-library")


def valid_id(model_id: str) -> bool:
    return bool(_ID_RE.match(str(model_id or "")))


def folder_of(model_id: str) -> str | None:
    """Ordner eines Eintrags oder None (unbekannt/ungueltig)."""
    if not valid_id(model_id):
        return None
    folder = os.path.join(library_dir(), model_id)
    return folder if os.path.isdir(folder) else None


def _read_manifest(folder: str) -> dict:
    from app import autocue
    return autocue.manifest(folder)


def _write_manifest(folder: str, data: dict) -> None:
    from app.autocue import MANIFEST
    tmp = os.path.join(folder, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, MANIFEST))


def _new_id() -> str:
    base = time.strftime("%Y%m%d-%H%M%S")
    root = library_dir()
    if not os.path.exists(os.path.join(root, base)):
        return base
    for n in range(2, 1000):
        cand = f"{base}-{n}"
        if not os.path.exists(os.path.join(root, cand)):
            return cand
    raise OSError("Keine freie Modell-ID")


def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:60]


def _default_name(manifest: dict, origin: str) -> str:
    stamp = str(manifest.get("trained_at") or "")[:10]
    return {"trained": "Eigenes Training", "imported": "Importiert"}.get(origin, "Modell") + \
        (f" {stamp}" if stamp else "")


# ----------------------------------------------------------------------------
# Eintragen
# ----------------------------------------------------------------------------

def add_files(files: dict[str, bytes], name: str = "", origin: str = "imported") -> str:
    """Gepruefte Dateien (package.check()) als neuen Eintrag ablegen. Rueckgabe: id."""
    from app.autocue import MANIFEST
    with _LOCK:
        root = library_dir()
        os.makedirs(root, exist_ok=True)
        model_id = _new_id()
        tmp = os.path.join(root, f".{model_id}.neu")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for fname, raw in files.items():
            with open(os.path.join(tmp, fname), "wb") as fh:
                fh.write(raw)
        manifest = _read_manifest(tmp) if MANIFEST in files else {}
        manifest["name"] = clean_name(name) or clean_name(manifest.get("name")) or _default_name(manifest, origin)
        manifest["origin"] = origin
        manifest["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write_manifest(tmp, manifest)
        os.rename(tmp, os.path.join(root, model_id))
        if not active_id():
            _select(model_id)           # erstes Modell: gilt sofort, sonst gaebe es keins
        return model_id


def add_folder(src: str, name: str = "", origin: str = "trained") -> str:
    """Einen fertigen Modellordner (Training) als neuen Eintrag kopieren. Rueckgabe: id."""
    from app.autocue.package import ALLOWED
    files = {}
    for fname in os.listdir(src):
        if fname in ALLOWED:
            with open(os.path.join(src, fname), "rb") as fh:
                files[fname] = fh.read()
    return add_files(files, name, origin)


# ----------------------------------------------------------------------------
# Lesen
# ----------------------------------------------------------------------------

def _entry(model_id: str, folder: str, active: str) -> dict:
    m = _read_manifest(folder)
    gold = m.get("gold") if isinstance(m.get("gold"), dict) else None
    if gold:
        gold = {k: v for k, v in gold.items() if k != "paths"}
    origin = str(m.get("origin") or "trained")
    return {
        "id": model_id,
        "name": clean_name(m.get("name")) or _default_name(m, origin),
        "origin": origin,
        "trained_at": str(m.get("trained_at") or ""),
        "added_at": str(m.get("added_at") or ""),
        "n_tracks": m.get("n_tracks"),
        "training_playlist": str(m.get("training_playlist") or ""),
        "gold": gold,
        "label_slots": m.get("label_slots") if isinstance(m.get("label_slots"), dict) else None,
        "memory_roles": m.get("memory_roles") if isinstance(m.get("memory_roles"), list) else None,
        "suggested_slots": m.get("suggested_slots") if isinstance(m.get("suggested_slots"), dict) else None,
        "active": model_id == active,
    }


def library_ids() -> list[str]:
    root = library_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted((n for n in names if _ID_RE.match(n) and os.path.isdir(os.path.join(root, n))), reverse=True)


def entries(cfg: dict | None = None) -> list[dict]:
    """Eigene Modelle, neueste oben. Nur vollstaendige Modelle."""
    from app import autocue
    migrate()
    active = active_id(cfg)
    out = []
    for model_id in library_ids():
        folder = os.path.join(library_dir(), model_id)
        if autocue.complete(folder):
            out.append(_entry(model_id, folder, active))
    return out


def active_id(cfg: dict | None = None) -> str:
    """Die gewaehlte id oder "" (keine Wahl, alte Wahl "bundled", ungueltig)."""
    cfg = cfg if cfg is not None else cfgmod.load()
    value = str(cfg.get("autocue_model") or "")
    return value if valid_id(value) else ""


def newest_id() -> str:
    """id des juengsten vollstaendigen Eintrags oder ""."""
    from app import autocue
    for model_id in library_ids():
        if autocue.complete(os.path.join(library_dir(), model_id)):
            return model_id
    return ""


def _select(model_id: str) -> None:
    from app import settings
    settings._write_local({"autocue_model": model_id})


# ----------------------------------------------------------------------------
# Aendern
# ----------------------------------------------------------------------------

def rename(model_id: str, name: str) -> dict:
    name = clean_name(name)
    if not name:
        raise ValueError("Der Name darf nicht leer sein.")
    with _LOCK:
        folder = folder_of(model_id)
        if folder is None:
            raise ValueError("Dieses Modell gibt es nicht mehr.")
        m = _read_manifest(folder)
        m["name"] = name
        _write_manifest(folder, m)
    return m


def delete(model_id: str, backup_root: str) -> None:
    """Eintrag in die Sicherung verschieben (die letzten KEEP_DELETED bleiben).
    Galt er, gilt danach der juengste verbliebene (ohne weitere: keiner)."""
    with _LOCK:
        folder = folder_of(model_id)
        if folder is None:
            raise ValueError("Dieses Modell gibt es nicht mehr.")
        os.makedirs(backup_root, exist_ok=True)
        dest = os.path.join(backup_root, f"{time.strftime('%Y%m%d-%H%M%S')}-{model_id}")
        shutil.move(folder, dest)
        old = sorted(p for p in os.listdir(backup_root) if os.path.isdir(os.path.join(backup_root, p)))
        for p in old[:-KEEP_DELETED]:
            shutil.rmtree(os.path.join(backup_root, p), ignore_errors=True)
        if active_id() == model_id:
            _select(newest_id())


# ----------------------------------------------------------------------------
# Uebergang vom einzelnen eigenen Modell
# ----------------------------------------------------------------------------

def migrate() -> None:
    """models/autocue/ (frueheres einzelnes eigenes Modell) einmalig in die Bibliothek
    holen, und eine Wahl, die auf kein Modell (mehr) zeigt -- vor allem das entfernte
    mitgelieferte "bundled" --, einmalig durch den juengsten eigenen ersetzen."""
    from app import autocue
    old = autocue.trained_dir()
    if old in _migrated:
        return
    with _LOCK:
        if old in _migrated:
            return
        _migrated.add(old)
        if os.path.isdir(old) and autocue.complete(old):      # unvollstaendig: liegen lassen
            manifest = autocue.manifest(old)
            origin = str(manifest.get("origin") or ("trained" if manifest.get("source") == "tracktab"
                                                    else "imported"))
            add_folder(old, "", origin)
            shutil.rmtree(old, ignore_errors=True)
        chosen = folder_of(active_id())
        if not autocue.complete(chosen):
            newest = newest_id()
            if newest or cfgmod.load().get("autocue_model"):
                _select(newest)
