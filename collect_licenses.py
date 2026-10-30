#!/usr/bin/env python3
"""
Sammelt die Lizenztexte der gebuendelten Drittanbieter-Software nach licenses/
(gehoert ins Repository und in die .dmg, siehe THIRD_PARTY_LICENSES.md).

  - licenses/gnu/       GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0 (aus der Homebrew-FFmpeg-Installation)
  - licenses/python/    Lizenzdateien der im .venv installierten Laufzeit-Pakete
  - licenses/ffmpeg-libs/  Lizenzdateien der Bibliotheken in vendor/libs (aus Homebrew)

Aufruf (im Projektordner):  .venv/bin/python collect_licenses.py
Vor jedem Release erneut laufen lassen, wenn sich Abhaengigkeiten geaendert haben.
"""
import importlib.metadata as md
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "licenses"

# Reine Build-/Entwicklungswerkzeuge, nicht Teil der App.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
        "altgraph", "macholib", "packaging"}
LICENSE_NAME = re.compile(r"(licen[cs]e|copying|notice|authors)", re.I)

# Homebrew-Formeln der Bibliotheken in vendor/libs.
BREW = ["ffmpeg", "x264", "x265", "lame", "mpg123", "opus", "libvpx", "dav1d",
        "svt-av1", "libvmaf", "openssl@4", "xz"]


def brew_prefix(name):
    try:
        return Path(subprocess.check_output(["brew", "--prefix", name], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def copy_files(files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copyfile(f, dest / Path(f).name)


def main():
    # Nur die erzeugten Ordner neu aufbauen -- licenses/ui/ wird von Hand gepflegt.
    for generated in ("gnu", "python", "ffmpeg-libs"):
        shutil.rmtree(OUT / generated, ignore_errors=True)

    ff = brew_prefix("ffmpeg")
    if ff:
        copy_files([ff / n for n in ("COPYING.GPLv2", "COPYING.GPLv3",
                                     "COPYING.LGPLv2.1", "COPYING.LGPLv3")
                    if (ff / n).exists()], OUT / "gnu")

    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in SKIP:
            continue
        files = [dist.locate_file(p) for p in (dist.files or [])
                 if LICENSE_NAME.search(Path(str(p)).name)
                 and ("dist-info" in str(p)) and Path(dist.locate_file(p)).is_file()]
        if files:
            copy_files(files, OUT / "python" / f"{name}-{dist.version}")
        else:
            # Kein Lizenztext im Paket: wenigstens die Angabe aus den Metadaten.
            lic = (dist.metadata.get("License-Expression") or dist.metadata.get("License") or "").strip()
            d = OUT / "python" / f"{name}-{dist.version}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "LICENSE-INFO.txt").write_text(
                f"{name} {dist.version}\nLizenz laut Paket-Metadaten: {lic or 'unbekannt'}\n",
                encoding="utf-8")

    for formula in BREW:
        prefix = brew_prefix(formula)
        if not prefix:
            print("Homebrew-Formel nicht gefunden:", formula)
            continue
        files = [p for p in prefix.iterdir()
                 if p.is_file() and LICENSE_NAME.search(p.name)
                 and not p.name.startswith("COPYING.") or p.name in ("COPYING",)]
        if formula == "ffmpeg":
            files = [prefix / "LICENSE.md"]
        copy_files([f for f in files if f.exists()], OUT / "ffmpeg-libs" / formula)

    (OUT / "README.md").write_text(
        "# Lizenztexte\n\nLizenztexte der Drittanbieter-Software, die TrackTab nutzt oder "
        "mitliefert. Uebersicht und Quellen: [../THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). "
        "Erzeugt mit `collect_licenses.py`.\n\n"
        "- `gnu/` — GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0\n"
        "- `python/` — Python-Pakete (je Paket ein Ordner)\n"
        "- `ffmpeg-libs/` — FFmpeg und seine Bibliotheken in `vendor/libs`\n"
        "- `ui/` — Bestandteile der Oberflaeche (von Hand gepflegt)\n",
        encoding="utf-8")
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"{n} Dateien nach {OUT}")


if __name__ == "__main__":
    main()
