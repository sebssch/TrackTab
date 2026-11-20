#!/usr/bin/env python3
"""
Sammelt die Lizenztexte der gebuendelten Drittanbieter-Software nach licenses/
(gehoert ins Repository und in die .dmg, siehe THIRD_PARTY_LICENSES.md).

  - licenses/gnu/       GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0 (aus der Homebrew-FFmpeg-Installation)
  - licenses/python/    Lizenzdateien der im .venv installierten Laufzeit-Pakete
  - licenses/ffmpeg-libs/  Lizenzdateien der Bibliotheken in vendor/libs (aus Homebrew)
  - licenses/native/    weitere native Bibliotheken im Bundle (libomp fuer LightGBM, aus Homebrew)
  - licenses/other/     von Hand gepflegte Texte, die kein Paket mitbringt (SQLCipher,
                        flatbuffers); wird hier nur gelesen, nie geloescht

Aufruf (im Projektordner):  .venv/bin/python collect_licenses.py
Vor jedem Release erneut laufen lassen, wenn sich Abhaengigkeiten geaendert haben.
"""
import importlib.metadata as md
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "licenses"

# Reine Build-/Entwicklungswerkzeuge, nicht Teil der App.
# packaging gehoert NICHT hierher: pyrekordbox importiert es zur Laufzeit
# (pyrekordbox/config.py), es steckt also im Bundle.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
        "altgraph", "macholib"}
LICENSE_NAME = re.compile(r"(licen[cs]e|copying|copyright|notice|authors)", re.I)

# Homebrew-Formeln der Bibliotheken in vendor/libs.
BREW = ["ffmpeg", "x264", "x265", "lame", "mpg123", "opus", "libvpx", "dav1d",
        "svt-av1", "libvmaf", "openssl@4", "xz"]
# Weitere Homebrew-Bibliotheken im Bundle, die nicht zu FFmpeg gehoeren:
# libomp (LightGBM), mpdecimal und zstd (CPython aus Homebrew: _decimal, _zstd).
# sqlite (_sqlite3) ist gemeinfrei und bringt keinen Lizenztext mit.
NATIVE = ["libomp", "mpdecimal", "zstd"]

# Pakete, deren Wheel den Lizenztext nicht im dist-info-Ordner fuehrt.
# Wert = Quelle des Textes: ("dist", <anderes Paket desselben Projekts>) oder
# ("other", <Datei in licenses/other/>, von Hand gepflegt).
FALLBACK = {
    "pyobjc-core": ("dist", "pyobjc-framework-Cocoa"),      # ein Projekt, ein Text
    "flatbuffers": ("other", "flatbuffers-LICENSE.txt"),    # Apache-2.0 verlangt den Text
}
# In ein Paket eingebettete Fremdsoftware ohne eigenen Text im Wheel:
# sqlcipher3-wheels linkt SQLCipher (BSD-3-Clause) und OpenSSL (Apache-2.0) statisch.
EMBEDDED = {
    "sqlcipher3-wheels": ["sqlcipher-LICENSE.md"],
}


def brew_prefix(name):
    try:
        return Path(subprocess.check_output(["brew", "--prefix", name], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def copy_files(files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copyfile(f, dest / Path(f).name)


def dist_license_files(dist):
    """Lizenzdateien eines Pakets: aus dist-info, sonst aus dem Paketordner selbst
    (onnxruntime legt LICENSE und ThirdPartyNotices.txt neben den Code)."""
    files = [dist.locate_file(p) for p in (dist.files or [])
             if LICENSE_NAME.search(Path(str(p)).name)
             and ("dist-info" in str(p)) and Path(dist.locate_file(p)).is_file()]
    if files:
        return files
    tops = {Path(str(p)).parts[0] for p in (dist.files or []) if len(Path(str(p)).parts) > 1}
    top_name = re.compile(r"^(licen[cs]e|copying|notice|third.?party.?notices)", re.I)
    return [dist.locate_file(p) for p in (dist.files or [])
            if len(Path(str(p)).parts) == 2 and Path(str(p)).parts[0] in tops
            and "dist-info" not in str(p) and top_name.search(Path(str(p)).name)
            and Path(str(p)).suffix.lower() in ("", ".txt", ".md", ".rst")
            and Path(dist.locate_file(p)).is_file()]


def main():
    # Nur die erzeugten Ordner neu aufbauen -- licenses/ui/ und licenses/other/ werden von Hand gepflegt.
    for generated in ("gnu", "python", "ffmpeg-libs", "native"):
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
        files = dist_license_files(dist)
        if not files and name in FALLBACK:
            kind, src = FALLBACK[name]
            if kind == "dist":
                files = dist_license_files(md.distribution(src))
            elif (OUT / "other" / src).is_file():
                files = [OUT / "other" / src]
        files = list(files) + [OUT / "other" / n for n in EMBEDDED.get(name, [])
                               if (OUT / "other" / n).is_file()]
        if name in FALLBACK and not files or len(EMBEDDED.get(name, [])) > sum(
                1 for n in EMBEDDED.get(name, []) if (OUT / "other" / n).is_file()):
            print("WARNUNG: Lizenztext fehlt fuer", name)
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

    # CPython selbst steckt im Bundle (PSF-Lizenz verlangt Text und Copyright-Vermerk).
    py_license = Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt"
    if py_license.is_file():
        copy_files([py_license], OUT / "python" / ("CPython-%d.%d.%d" % sys.version_info[:3]))
    else:
        print("WARNUNG: CPython-Lizenztext nicht gefunden:", py_license)

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

    # libomp (OpenMP fuer LightGBM, Auto-Cue-Training) kommt ueber den rpath von
    # lib_lightgbm.dylib aus Homebrew ins Bundle (tracktab.spec), mpdecimal und
    # zstd ueber die Erweiterungsmodule des Homebrew-Pythons.
    for formula in NATIVE:
        prefix = brew_prefix(formula)
        if not prefix:
            print("Homebrew-Formel nicht gefunden:", formula)
            continue
        copy_files([p for p in prefix.iterdir() if p.is_file() and LICENSE_NAME.search(p.name)],
                   OUT / "native" / formula)

    (OUT / "README.md").write_text(
        "# Lizenztexte\n\nLizenztexte der Drittanbieter-Software, die TrackTab nutzt oder "
        "mitliefert. Uebersicht und Quellen: [../THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). "
        "Erzeugt mit `collect_licenses.py`.\n\n"
        "- `gnu/` — GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0\n"
        "- `python/` — Python-Pakete (je Paket ein Ordner)\n"
        "- `ffmpeg-libs/` — FFmpeg und seine Bibliotheken in `vendor/libs`\n"
        "- `native/` — weitere native Bibliotheken (libomp fuer das Auto-Cue-Training, "
        "mpdecimal und zstd fuer Python)\n"
        "- `ui/` — Bestandteile der Oberflaeche (von Hand gepflegt)\n"
        "- `other/` — Texte, die kein Paket mitbringt (von Hand gepflegt): SQLCipher "
        "(in sqlcipher3-wheels eingebettet), flatbuffers\n",
        encoding="utf-8")
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"{n} Dateien nach {OUT}")


if __name__ == "__main__":
    main()
