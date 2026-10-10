# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Bauplan fuer 'TrackTab'."""

datas = [
    ("app/webui", "app/webui"),      # Weboberflaeche
    ("config.yaml", "."),            # dokumentierte Vorgabe, wird beim
                                      # ersten Start herauskopiert
    ("resources", "resources"),      # Standard-Ableton-Vorlage, siehe
]                                    # config.default_daw_template_path()

import os
if os.path.isdir("vendor"):
    datas.append(("vendor", "vendor"))   # statisches ffmpeg/ffprobe

# Einzige Quelle der Versionsnummer ist app/__init__.py -- nicht hier
# zusaetzlich pflegen, sonst laufen App-Anzeige und Bundle-Version auseinander.
from app import __version__ as _version

# mutagen laedt beim Schreiben von ID3v2.3-Tags (Cover, Titel, ... in MP3s)
# Text-Codecs wie "utf-16-le" dynamisch per Namensstring nach (codecs.lookup).
# PyInstaller erkennt das nicht automatisch -> ohne diese Zeile fehlt das
# Codec-Modul im Bundle und der Schreibvorgang bricht mit
# "unknown encoding: utf-16-le" ab (im Dev-venv unsichtbar, da dort immer
# alle Codecs verfuegbar sind).
from PyInstaller.utils.hooks import collect_submodules
_encodings = collect_submodules("encodings")

# libsonare (BPM-/Tonart-Analyse, app/bpmkey.py) ist optional und wird lazy
# importiert -- PyInstaller sieht den Import deshalb nicht. Die native
# libsonare.dylib liegt neben den Python-Modulen im Paket und wird von
# libsonare/_ffi.py relativ zu seiner eigenen Datei gesucht; collect_dynamic_libs()
# legt sie im Bundle wieder in denselben Ordner. Fehlt das Paket im Build-venv
# (z. B. Intel-Mac), entsteht ein Bundle ohne die Funktion.
_sonare_binaries, _sonare_hidden = [], []
try:
    import libsonare  # noqa: F401
    from PyInstaller.utils.hooks import collect_dynamic_libs
    _sonare_binaries = collect_dynamic_libs("libsonare")
    _sonare_hidden = collect_submodules("libsonare")
except Exception:
    print("HINWEIS: libsonare nicht installiert -- Bundle ohne BPM-/Tonart-Analyse.")

a = Analysis(
    ["desktop_main.py"],
    pathex=[],
    binaries=_sonare_binaries,
    datas=datas,
    hiddenimports=["app.analyzer", "app.classify", "app.spectral", "app.probe",
                   "app.macapp", "app.bpmkey"] + _encodings + _sonare_hidden,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TrackTab",
    console=False,
    argv_emulation=False,
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TrackTab")

app = BUNDLE(
    coll,
    name="TrackTab.app",
    icon="build_assets/appicon.icns" if os.path.exists("build_assets/appicon.icns") else None,
    # Bundle-ID ab jetzt fest. macOS haengt erteilte Berechtigungen
    # (Festplattenzugriff, Automation fuer Music.app/Finder) an diese ID --
    # wer sie aendert, muss sie alle neu bestaetigen lassen.
    bundle_identifier="com.sgt.tracktab",
    info_plist={
        "CFBundleName": "TrackTab",
        "CFBundleDisplayName": "TrackTab — Music Library Toolkit by sgt.works",
        "CFBundleShortVersionString": _version,
        "CFBundleVersion": _version,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
        # Cocoa-Anwendung: macapp.py haengt eine echte NSApplication samt
        # Menueleiste davor, damit Cmd+Q und "Beenden" im Dock ankommen.
        "NSPrincipalClass": "NSApplication",
        # Eigenes URL-Schema, damit die "Server nicht erreichbar"-Seite der
        # PWA (app/webui/sw.js) die App per Klick starten kann, statt den
        # Umweg ueber Finder/Spotlight zu verlangen. macOS registriert das
        # Schema ueber die Launch-Services-Indizierung dieses Bundles, auch
        # ohne dass die App je gestartet wurde. macapp.py behandelt den
        # resultierenden Apple Event in application_openURLs_.
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "com.sgt.tracktab.start",
                "CFBundleURLSchemes": ["tracktab"],
            },
        ],
    },
)
