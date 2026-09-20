"""
Zugriff auf die externen Medienwerkzeuge und abgeleitete Audiodaten.

Zwei Aufgaben:
  1. ffmpeg/ffprobe finden — im gepackten App-Bundle liegen sie mit, aus dem
     Quellbaum heraus kommen sie aus dem PATH oder von Homebrew.
  2. Huellkurven fuer die Waveform-Darstellung berechnen.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import webbrowser
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import ableton as ableton_mod
from . import config as cfgmod

# Homebrew liegt je nach Architektur woanders; beide Orte pruefen.
_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")


class MediaToolMissing(RuntimeError):
    """ffmpeg oder ffprobe ist nirgends auffindbar."""


@lru_cache(maxsize=4)
def tool_path(name: str) -> str:
    """
    Sucht ein Werkzeug in dieser Reihenfolge:
      1. MP3QC_FFMPEG_DIR (manuell gesetzter Ordner)
      2. mitgeliefert in vendor/ (gepackte App)
      3. PATH
      4. bekannte Homebrew-Verzeichnisse
    """
    override = os.environ.get("MP3QC_FFMPEG_DIR", "").strip()
    if override:
        candidate = Path(override).expanduser() / name
        if candidate.is_file():
            return str(candidate)

    vendored = cfgmod.vendor_dir() / name
    if vendored.is_file():
        return str(vendored)

    found = shutil.which(name)
    if found:
        return found

    for directory in _FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)

    raise MediaToolMissing(
        f"{name} wurde nicht gefunden. Entweder 'brew install ffmpeg' ausführen "
        f"oder eine statisch gelinkte Version nach {cfgmod.vendor_dir()} legen."
    )


def ffmpeg_path() -> str:
    return tool_path("ffmpeg")


def ffprobe_path() -> str:
    return tool_path("ffprobe")


_ENCODER_LINE = re.compile(r"^\s*[VASFXBDTL.]{6}\s+(\S+)\s+\S")


@lru_cache(maxsize=1)
def _encoder_names() -> frozenset[str]:
    try:
        out = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True, timeout=15,
        )
    except (MediaToolMissing, OSError, subprocess.TimeoutExpired):
        return frozenset()
    names = set()
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        m = _ENCODER_LINE.match(line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


def encoder_available(name: str) -> bool:
    return bool(name) and name in _encoder_names()


def resolved_aac_encoder(cfg: dict) -> str:
    """Konfigurierten AAC-Encoder validieren, mit Fallback auf den
    ueberall verfuegbaren nativen 'aac'-Encoder (z.B. wenn 'libfdk_aac'
    konfiguriert, im lokalen ffmpeg-Build aber nicht enthalten ist)."""
    wanted = str(cfg.get("aac_encoder") or "aac").strip() or "aac"
    if wanted == "aac" or encoder_available(wanted):
        return wanted
    return "aac"


def available() -> tuple[bool, str]:
    """Fuer Startpruefungen: (vorhanden, Meldung)."""
    try:
        ffmpeg_path()
        ffprobe_path()
    except MediaToolMissing as exc:
        return False, str(exc)
    return True, ""


def brew_path() -> str | None:
    """Homebrew finden — es steht nicht immer im PATH der App."""
    found = shutil.which("brew")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if Path(candidate).is_file():
            return candidate
    return None


def forget() -> None:
    """Suchergebnis verwerfen — nach einer Installation neu nachsehen."""
    tool_path.cache_clear()


# ── Waveform ──────────────────────────────────────────────────────────────

# 8 kHz Mono reicht fuer eine Huellkurve voellig aus und dekodiert rund
# zehnmal schneller als die volle Analyse-Aufloesung.
_WAVE_RATE = 8000


def waveform_peaks(path: str, buckets: int = 1000) -> list[float]:
    """
    Huellkurve als Liste von Werten zwischen 0 und 1 — ein Wert je Bucket,
    jeweils der Spitzenwert in diesem Zeitabschnitt.
    """
    cmd = [
        ffmpeg_path(), "-v", "error", "-nostdin",
        "-i", str(path),
        "-map", "0:a:0",
        "-ac", "1", "-ar", str(_WAVE_RATE),
        "-f", "f32le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200])

    signal = np.frombuffer(out.stdout, dtype="<f4")
    if signal.size == 0:
        return []

    buckets = max(1, min(int(buckets), signal.size))
    # Auf ein Vielfaches der Bucket-Zahl kuerzen, dann blockweise das Maximum
    usable = (signal.size // buckets) * buckets
    if usable < buckets:
        return [float(np.max(np.abs(signal)))]
    frames = np.abs(signal[:usable]).reshape(buckets, -1)
    peaks = frames.max(axis=1)

    top = float(peaks.max())
    if top > 0:
        peaks = peaks / top          # auf volle Hoehe normalisieren
    return [round(float(v), 4) for v in peaks]


# ── Externer Audio-Editor ─────────────────────────────────────────────────
# Zum Gegenpruefen im Spektrogramm — iZotope RX wird automatisch gefunden,
# laesst sich in den Einstellungen aber auf jedes andere Programm umstellen.

_EDITOR_PATTERNS = (
    "iZotope RX * Audio Editor.app",
    "iZotope RX*.app",
    "*RX * Audio Editor.app",
)
_APP_DIRS = ("/Applications", str(Path.home() / "Applications"))


def _version_key(name: str) -> tuple:
    """Aus 'iZotope RX 11 Audio Editor' die 11 ziehen, fuer 'neueste zuerst'."""
    digits = [int(part) for part in re.findall(r"\d+", name)]
    return (max(digits) if digits else 0, name)


def _bundle_version(app_path: str) -> str:
    """CFBundleShortVersionString eines App-Bundles, sonst leer -- fuer
    Programme, die ihre Version nicht im Dateinamen tragen (Logic Pro,
    Audacity, GarageBand, siehe list_daw_apps()/list_audio_editor_apps())."""
    plist = Path(app_path) / "Contents" / "Info.plist"
    if not plist.is_file():
        return ""
    out = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(plist)],
        capture_output=True, timeout=15)
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", "replace").strip()


def find_audio_editor() -> str | None:
    """Neueste installierte RX-Version, sonst None."""
    found: list[str] = []
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _EDITOR_PATTERNS:
            found.extend(str(p) for p in base.glob(pattern) if p.is_dir())
    if not found:
        return None
    return sorted(set(found), key=lambda p: _version_key(Path(p).stem), reverse=True)[0]


def editor_path(cfg: dict | None = None) -> str | None:
    """Eingestellter Audio-Editor, sonst automatisch gefundener (iZotope RX)."""
    cfg = cfg or cfgmod.load()
    configured = str(cfg.get("external_editor") or "").strip()
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.exists() else configured
    return find_audio_editor()


_AUDACITY_PATTERNS = ("Audacity.app",)
_AUDITION_PATTERNS = ("Adobe Audition*/Adobe Audition*.app", "Adobe Audition*.app")
_GARAGEBAND_PATTERNS = ("GarageBand.app",)


def list_audio_editor_apps() -> list[dict]:
    """Installierte Audio-Editoren (iZotope RX, Audacity, Adobe Audition,
    GarageBand) als [{name, path}] fuer die appList der Einstellungen-Auswahl
    -- anders als list_apps() nur diese vier, gleiches Prinzip wie bei
    list_rekordbox_apps(). Audacity und GarageBand tragen die Version nicht
    im Dateinamen (immer "Audacity.app"/"GarageBand.app"), sie kommt deshalb
    aus der Info.plist (_bundle_version()); RX und Adobe Audition haben sie
    schon im Datei- bzw. Ordnernamen."""
    apps: dict[str, dict] = {}
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _EDITOR_PATTERNS + _AUDITION_PATTERNS:
            for p in base.glob(pattern):
                if p.is_dir():
                    apps[str(p)] = {"name": p.stem, "path": str(p)}
        for pattern in _AUDACITY_PATTERNS + _GARAGEBAND_PATTERNS:
            for p in base.glob(pattern):
                if not p.is_dir():
                    continue
                version = _bundle_version(str(p))
                name = f"{p.stem} {version}".strip() if version else p.stem
                apps[str(p)] = {"name": name, "path": str(p)}
    return sorted(apps.values(), key=lambda a: a["name"].lower())


# ── DAW ───────────────────────────────────────────────────────────────────
# Anders als RX/MIK/Rekordbox gibt es hier keinen einheitlichen Namen zum
# Erraten (Logic, Ableton, Cubase, FL Studio, Bitwig, ...) -- deshalb keine
# automatische Suche wie find_audio_editor()/find_mixed_in_key(). Der Knopf
# in der Oberflaeche bleibt komplett ausgeblendet, solange hier nichts
# eingestellt ist (siehe app.js DAW_NAME).

def daw_path(cfg: dict | None = None) -> str | None:
    """Eingestellte DAW, oder None ohne Eintrag in den Einstellungen."""
    cfg = cfg or cfgmod.load()
    configured = str(cfg.get("external_daw") or "").strip()
    if not configured:
        return None
    expanded = Path(configured).expanduser()
    return str(expanded) if expanded.exists() else configured


_LOGIC_PATTERNS = ("Logic Pro.app", "Logic Pro X.app")
_ABLETON_PATTERNS = ("Ableton Live *.app",)


def list_daw_apps() -> list[dict]:
    """Installierte DAWs (Logic Pro, Ableton Live) als [{name, path}] fuer die
    appList der Einstellungen-Auswahl -- anders als find_audio_editor() gibt
    es hier keinen einheitlichen Namen zum automatischen Vorschlagen (siehe
    Kommentar oben), aber sehr wohl eine feste Liste erkennbarer Programme.
    Logic traegt die Version nicht im Dateinamen (immer "Logic Pro.app") --
    sie kommt deshalb aus der Info.plist (_bundle_version()), Ableton hat sie
    schon im Namen ("Ableton Live 12.app")."""
    apps: dict[str, dict] = {}
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _LOGIC_PATTERNS:
            for p in base.glob(pattern):
                if not p.is_dir():
                    continue
                version = _bundle_version(str(p))
                name = f"{p.stem} {version}".strip() if version else p.stem
                apps[str(p)] = {"name": name, "path": str(p)}
        for pattern in _ABLETON_PATTERNS:
            for p in base.glob(pattern):
                if p.is_dir():
                    apps[str(p)] = {"name": p.stem, "path": str(p)}
    return sorted(apps.values(), key=lambda a: a["name"].lower())


def open_in_daw(path: str, cfg: dict | None = None) -> str:
    """
    Datei in der eingestellten DAW oeffnen.

    Ableton importiert eine per open -a uebergebene Datei nicht in ein
    offenes Set (siehe Kommentar oben) -- ist eine Vorlage konfiguriert,
    wird stattdessen ein frisches Projekt mit der Datei auf Spur 1 erzeugt
    und dieses geoeffnet (siehe app/ableton.py). Ohne eigene Vorlage in den
    Einstellungen greift die mitgelieferte Standard-Vorlage.
    """
    cfg = cfg or cfgmod.load()
    app_path = daw_path(cfg)
    template = str(cfg.get("external_daw_template") or "").strip()
    if not template:
        default_template = cfgmod.default_daw_template_path()
        if default_template.is_file():
            template = str(default_template)
    if app_path and template and ableton_mod.is_ableton(app_path):
        target = ableton_mod.build_temp_project(path, template)
        return open_in_app(target, app_path,
            "Keine DAW eingestellt. In den Einstellungen unter "
            "'Externe Programme' ein Programm auswählen.")
    return open_in_app(path, app_path,
        "Keine DAW eingestellt. In den Einstellungen unter "
        "'Externe Programme' ein Programm auswählen.")


# ── Mixed In Key ──────────────────────────────────────────────────────────
# Mixed In Key hat weder CLI noch AppleScript-Dictionary (von den Entwicklern
# im eigenen Forum abgelehnt) -- die Engine selbst laesst sich also nicht
# ansteuern. Was funktioniert: Dateien wie beim Finder-Drag per 'open -a'
# uebergeben. MIK importiert sie in seine Bibliothek und analysiert sie dort
# von selbst im Hintergrund -- genau der manuelle Schritt, der entfaellt.

_MIK_PATTERNS = ("Mixed In Key*.app",)


def find_mixed_in_key() -> str | None:
    """Neueste installierte Mixed-In-Key-Version, sonst None."""
    found: list[str] = []
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _MIK_PATTERNS:
            found.extend(str(p) for p in base.glob(pattern) if p.is_dir())
    if not found:
        return None
    return sorted(set(found), key=lambda p: _version_key(Path(p).stem), reverse=True)[0]


def list_mixed_in_key_apps() -> list[dict]:
    """Installierte Mixed-In-Key-Versionen als [{name, path}] fuer die
    appList der Einstellungen-Auswahl -- anders als list_apps() nur Mixed In
    Key, gleiches Prinzip wie bei list_rekordbox_apps(). Die Version steckt
    schon im Dateinamen (z.B. "Mixed In Key 11.app")."""
    found: list[str] = []
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _MIK_PATTERNS:
            found.extend(str(p) for p in base.glob(pattern) if p.is_dir())
    apps = [{"name": Path(p).stem, "path": p} for p in sorted(set(found))]
    return sorted(apps, key=lambda a: a["name"].lower())


def mik_path(cfg: dict | None = None) -> str | None:
    """Eingestellte Mixed-In-Key-Version, sonst automatisch gefundene."""
    cfg = cfg or cfgmod.load()
    configured = str(cfg.get("external_mik") or "").strip()
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.exists() else configured
    return find_mixed_in_key()


# ── Rekordbox ─────────────────────────────────────────────────────────────
# Nur fuers Programm-Symbol (appicon) -- die eigentliche Datenbank-Anbindung
# laeuft komplett getrennt ueber app/rekordbox.py/pyrekordbox. Rekordbox
# liegt anders als die meisten Apps nicht direkt in /Applications, sondern
# in einem versionierten Ordner (z.B. "rekordbox 7/rekordbox.app").
_REKORDBOX_PATTERNS = ("rekordbox*/rekordbox.app", "rekordbox*/*.app")


def find_rekordbox() -> str | None:
    """Neueste installierte Rekordbox-Version, sonst None."""
    found: list[str] = []
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _REKORDBOX_PATTERNS:
            found.extend(str(p) for p in base.glob(pattern) if p.is_dir())
    if not found:
        return None
    return sorted(set(found), key=lambda p: _version_key(Path(p).parent.name), reverse=True)[0]


def list_rekordbox_apps() -> list[dict]:
    """Installierte Rekordbox-Versionen als [{name, path}] -- anders als
    list_apps() nur Rekordbox, fuer die appList der Einstellungen-Auswahl
    (siehe browser_apps() fuer dasselbe Prinzip bei Browsern). Die
    Versionsnummer steckt im Ordnernamen ("rekordbox 7"), nicht im
    Bundle-Namen (immer "rekordbox.app") -- der Name kommt deshalb vom
    Elternordner, nicht von Path(p).stem wie bei find_rekordbox()."""
    found: list[str] = []
    for directory in _APP_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for pattern in _REKORDBOX_PATTERNS:
            found.extend(str(p) for p in base.glob(pattern) if p.is_dir())
    apps = []
    for p in sorted(set(found)):
        folder = Path(p).parent.name
        name = (folder[:1].upper() + folder[1:]) if folder else Path(p).stem
        apps.append({"name": name, "path": p})
    return sorted(apps, key=lambda a: _version_key(a["name"]), reverse=True)


def rekordbox_path(cfg: dict | None = None) -> str | None:
    """
    Eingestellte Rekordbox-Version, sonst automatisch gefundene (neueste).
    Stehen mehrere Versionen auf dem System (z.B. "rekordbox 6" und
    "rekordbox 7"), legt dieser Pfad auch fest, welche Version fuer
    master.db herangezogen wird (siehe rekordbox._resolve_master_db_path()).
    """
    cfg = cfg or cfgmod.load()
    configured = str(cfg.get("external_rekordbox") or "").strip()
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.exists() else configured
    return find_rekordbox()


def open_in_mik(paths: list[str], cfg: dict | None = None) -> str:
    """
    Dateien in Mixed In Key oeffnen -- importiert sie dort, MIK analysiert sie
    danach selbst im Hintergrund. Mehrere Pfade in einem Aufruf oeffnen alle
    Tracks in einem einzigen MIK-Fenster, wie ein Mehrfach-Drag aus dem Finder.
    """
    app_path = mik_path(cfg)
    if not app_path:
        raise MediaToolMissing(
            "Mixed In Key wurde nicht gefunden. In den Einstellungen unter "
            "'Externe Programme' den Pfad angeben, falls es nicht in "
            "/Applications liegt.")
    result = subprocess.run(["open", "-a", app_path, *[str(p) for p in paths]],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:200]
            or "Mixed In Key konnte nicht geöffnet werden")
    return app_path


def choose_audio_files(prompt: str = "Tracks wählen") -> list[str] | None:
    """
    Native macOS-Dateiauswahl per AppleScript.

    Der einzige Weg an echte POSIX-Pfade fuer neue (nicht gescannte) Dateien
    zu kommen: ein Browser-Dateidialog oder Drag&Drop auf die Seite gibt aus
    Sicherheitsgruenden nie den echten Pfad preis, nur den Dateinamen (siehe
    server.py:_post_analyse) -- fuer eine Kopie in einem Temp-Ordner wuerde
    Mixed In Key aber Tags an der falschen Datei schreiben.

    Liefert None, wenn der Dialog abgebrochen wurde.
    """
    script = (
        'on run argv\n'
        '  set thePrompt to item 1 of argv\n'
        '  set theFiles to choose file with prompt thePrompt '
        'with multiple selections allowed\n'
        '  set thePaths to {}\n'
        '  repeat with f in theFiles\n'
        '    set end of thePaths to POSIX path of f\n'
        '  end repeat\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  return thePaths as text\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, prompt],
                            capture_output=True, timeout=300)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")
        if "-128" in stderr or "User canceled" in stderr:
            return None
        raise RuntimeError(stderr.strip()[:200] or "Dateiauswahl fehlgeschlagen")
    out = result.stdout.decode("utf-8", "replace").strip()
    return [line for line in out.splitlines() if line]


def choose_folders(prompt: str = "Ordner wählen") -> list[str] | None:
    """
    Native macOS-Ordnerauswahl (mehrfach) per AppleScript -- fuer die
    Bibliotheksordner in den Einstellungen. Liefert None, wenn der Dialog
    abgebrochen wurde.
    """
    script = (
        'on run argv\n'
        '  set thePrompt to item 1 of argv\n'
        '  set theFolders to choose folder with prompt thePrompt '
        'with multiple selections allowed\n'
        '  set thePaths to {}\n'
        '  repeat with f in theFolders\n'
        '    set end of thePaths to POSIX path of f\n'
        '  end repeat\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  return thePaths as text\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, prompt],
                            capture_output=True, timeout=300)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")
        if "-128" in stderr or "User canceled" in stderr:
            return None
        raise RuntimeError(stderr.strip()[:200] or "Ordnerauswahl fehlgeschlagen")
    out = result.stdout.decode("utf-8", "replace").strip()
    return [line.rstrip("/") for line in out.splitlines() if line]


def choose_single_file(prompt: str, default_dir: str | None = None) -> str | None:
    """
    Nativer Dateiauswahl-Dialog fuer genau eine Datei, optional mit
    Startordner (fuer die Backup-Wiederherstellung: startet direkt im
    backup/-Ordner). Liefert None, wenn abgebrochen.
    """
    script = (
        'on run argv\n'
        '  set thePrompt to item 1 of argv\n'
        '  set theDir to item 2 of argv\n'
        '  if theDir is "" then\n'
        '    set theFile to choose file with prompt thePrompt\n'
        '  else\n'
        '    set theFile to choose file with prompt thePrompt default location (POSIX file theDir)\n'
        '  end if\n'
        '  return POSIX path of theFile\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, prompt, default_dir or ""],
                            capture_output=True, timeout=300)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")
        if "-128" in stderr or "User canceled" in stderr:
            return None
        raise RuntimeError(stderr.strip()[:200] or "Dateiauswahl fehlgeschlagen")
    return result.stdout.decode("utf-8", "replace").strip()


def choose_save_path(prompt: str, default_name: str, default_dir: str | None = None) -> str | None:
    """
    Nativer 'Speichern unter'-Dialog -- fuer den manuellen Backup-Export an
    einen beliebigen Ort. Liefert None, wenn abgebrochen.
    """
    script = (
        'on run argv\n'
        '  set thePrompt to item 1 of argv\n'
        '  set theName to item 2 of argv\n'
        '  set theDir to item 3 of argv\n'
        '  if theDir is "" then\n'
        '    set theFile to choose file name with prompt thePrompt default name theName\n'
        '  else\n'
        '    set theFile to choose file name with prompt thePrompt default name theName ' +
        'default location (POSIX file theDir)\n'
        '  end if\n'
        '  return POSIX path of theFile\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, prompt, default_name, default_dir or ""],
                            capture_output=True, timeout=300)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")
        if "-128" in stderr or "User canceled" in stderr:
            return None
        raise RuntimeError(stderr.strip()[:200] or "Speichern-Dialog fehlgeschlagen")
    return result.stdout.decode("utf-8", "replace").strip()


def open_in_app(path: str, app_path: str | None, missing_hint: str) -> str:
    """Datei in einem externen Programm oeffnen. Liefert den benutzten Programmpfad."""
    if not app_path:
        raise MediaToolMissing(missing_hint)
    result = subprocess.run(["open", "-a", app_path, str(path)],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:200]
            or f"{Path(app_path).stem} konnte nicht geöffnet werden")
    return app_path


def open_in_editor(path: str, cfg: dict | None = None) -> str:
    """Datei im eingestellten Audio-Editor oeffnen. Liefert den benutzten Editor."""
    return open_in_app(path, editor_path(cfg),
        "Kein Audio-Editor gefunden. In den Einstellungen unter "
        "'Externe Programme' ein Programm auswählen.")


# ── Programme fuer die Auswahl in den Einstellungen ──────────────────────
# mdfind fragt Spotlight — das findet Programme systemweit, nicht nur in
# /Applications, genau wie der "Öffnen mit"-Dialog des Finders.

def list_apps() -> list[dict]:
    """Alle installierten Programme als [{name, path}], nach Name sortiert."""
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True, timeout=15, text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    apps = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        apps.append({"name": Path(line).stem, "path": line})
    return sorted(apps, key=lambda a: a["name"].lower())


# ── Browser fuer die Web-UI ───────────────────────────────────────────────
# Kein separates Feld dafuer in list_apps() -- eine feste Bundle-ID-Liste
# reicht, Spotlight indiziert LSApplicationCategoryType (Info.plist-Schluessel
# fuer "das ist ein Browser") nicht als abfragbares kMDItem.

_BROWSER_BUNDLE_IDS = (
    "com.apple.Safari", "com.apple.SafariTechnologyPreview",
    "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev",
    "com.google.Chrome.canary", "org.mozilla.firefox",
    "org.mozilla.firefoxdeveloperedition", "org.mozilla.nightly",
    "com.microsoft.edgemac", "com.microsoft.edgemac.Dev",
    "com.microsoft.edgemac.Beta", "com.microsoft.edgemac.canary",
    "com.brave.Browser", "com.brave.Browser.beta", "com.brave.Browser.nightly",
    "company.thebrowser.Browser", "com.operasoftware.Opera",
    "com.operasoftware.OperaGX", "com.vivaldi.Vivaldi",
    "org.chromium.Chromium", "com.duckduckgo.macos.browser",
)


def browser_apps() -> list[dict]:
    """Installierte Browser als [{name, path}], nach Name sortiert."""
    query = " || ".join(f"kMDItemCFBundleIdentifier == '{bid}'"
                         for bid in _BROWSER_BUNDLE_IDS)
    try:
        result = subprocess.run(["mdfind", query], capture_output=True,
                                timeout=15, text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    apps = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        apps.append({"name": Path(line).stem, "path": line})
    return sorted(apps, key=lambda a: a["name"].lower())


def browser_path(cfg: dict | None = None) -> str | None:
    """Eingestellter Browser fuer die Web-UI, sonst None (= Systemstandard)."""
    cfg = cfg or cfgmod.load()
    configured = str(cfg.get("external_browser") or "").strip()
    if not configured:
        return None
    expanded = Path(configured).expanduser()
    return str(expanded) if expanded.exists() else configured


def open_url(url: str, app_path: str | None = None) -> None:
    """Oeffnet eine URL im angegebenen Programm, sonst im Systemstandard."""
    if app_path:
        try:
            result = subprocess.run(["open", "-a", app_path, url],
                                    capture_output=True, timeout=10)
            if result.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    webbrowser.open(url)


# ── Papierkorb ────────────────────────────────────────────────────────────

def move_to_trash(path: str) -> None:
    """
    Verschiebt die Datei in den Papierkorb — bewusst nicht loeschen.
    Ein Fehlgriff bleibt damit umkehrbar.

    Der Pfad geht als Argument an osascript und nicht in den Skripttext,
    damit Anfuehrungszeichen und Umlaute in Dateinamen nichts anrichten.
    """
    script = (
        'on run argv\n'
        '  set p to POSIX file (item 1 of argv) as alias\n'
        '  tell application "Finder" to delete p\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, str(path)],
                            capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", "replace").strip()[:250]
            or "Der Finder konnte die Datei nicht in den Papierkorb legen.")


# ── Verwaiste Ordner ──────────────────────────────────────────────────────
# Music.app/Rekordbox hinterlassen nach dem Verschieben/Loeschen von Tracks
# gern leere Ordner (Album-/Interpret-Verzeichnisse) zurueck, die nur noch
# eine vom Finder angelegte .DS_Store enthalten -- "leer" zaehlt das hier
# also mit. Nie automatisch, nur per Knopf in den Einstellungen.

def _scan_empty(p: Path, hits: list[Path]) -> bool:
    """
    Traegt in `hits` alle aeussersten leeren Ordner unterhalb von `p` ein und
    gibt zurueck, ob `p` selbst (rekursiv, .DS_Store ignoriert) komplett leer
    ist -- der Aufrufer wertet `p` dann selbst als Treffer, statt seine
    (dann bereits eingetragenen) Unterordner einzeln zu zaehlen.

    Symlinks werden nie angefasst -- weder als Treffer noch zum Absteigen --
    ein leerer Ordner an einem symlink-Ziel soll das dortige Original nicht
    beruehren.
    """
    try:
        children = sorted(p.iterdir())
    except OSError:
        return False
    empty = True
    # Eigene, frische Liste: ein Unterordner, der fuer sich leer ist, wird
    # hier nur VORLAEUFIG gesammelt. Stellt sich am Ende heraus, dass p
    # selbst komplett leer ist, faellt die Liste weg -- p wird dann als
    # Ganzes an den Aufrufer gemeldet (return True), statt seine bereits
    # leeren Unterordner einzeln aufzufuehren.
    pending: list[Path] = []
    for c in children:
        if c.is_symlink():
            empty = False
            continue
        if c.name == ".DS_Store":
            continue
        if c.is_dir():
            if _scan_empty(c, pending):
                pending.append(c)          # c selbst leer -- vorlaeufiger Treffer
            else:
                empty = False
        else:
            empty = False
    if empty:
        return True
    hits.extend(pending)
    return False


def find_empty_folders(root: str) -> list[str]:
    """
    Aeusserste Ordner unter `root`, deren gesamter Inhalt (rekursiv) aus
    nichts als .DS_Store-Dateien besteht. `root` selbst wird nie als Treffer
    gemeldet, auch wenn er komplett leer waere -- ein konfigurierter
    Bibliotheksordner soll nicht selbst im Papierkorb landen.
    """
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return []
    hits: list[Path] = []
    for entry in sorted(root_path.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if _scan_empty(entry, hits):
            hits.append(entry)
    return [str(p) for p in hits]


# ── Programm-Symbole ──────────────────────────────────────────────────────
# Fuer die Knopfleiste werden die echten Icons der installierten Programme
# benutzt statt nachgezeichneter Nachahmungen. Sie liegen ohnehin auf dem
# Rechner; macOS zeigt sie im Finder genauso an.

FINDER_APP = "/System/Library/CoreServices/Finder.app"
MUSIC_APP = "/System/Applications/Music.app"
_ICON_CACHE: dict[str, bytes] = {}


def list_music_apps() -> list[dict]:
    """Music.app als [{name, path}] fuer die appList der Einstellungen-
    Auswahl, falls vorhanden -- anders als bei den anderen Programmen hier
    gibt es nur genau eine moegliche Wahl (siehe Kommentar bei
    external_music/open_in_music())."""
    return [{"name": "Music", "path": MUSIC_APP}] if Path(MUSIC_APP).is_dir() else []


def open_in_music(path: str) -> str:
    """
    Datei in Music.app oeffnen und abspielen -- fest verdrahtet wie der
    Finder-Reveal-Knopf, keine Einstellung noetig (Music.app ist immer da).

    'reveal current track' danach ist ein offizieller AppleScript-Befehl aus
    dem Music.app-Dictionary (kein UI-Scripting): markiert und scrollt zum
    Track in der aktuellen Ansicht, damit er nicht nur unsichtbar im
    Hintergrund abspielt.
    """
    # 'open POSIX file thePath' schlaegt bei Music.app mit einem
    # Koerzierungsfehler fehl (-1728) -- 'open' erwartet einen echten
    # Datei-Specifier, keine rohe POSIX-Referenz. Der Umweg ueber 'as alias'
    # behebt das zuverlaessig (an echten Dateien getestet).
    script = (
        'on run argv\n'
        '  set thePath to item 1 of argv\n'
        '  set theAlias to (POSIX file thePath) as alias\n'
        '  tell application "Music"\n'
        '    activate\n'
        '    open theAlias\n'
        '    delay 0.3\n'
        '    reveal current track\n'
        '  end tell\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, str(path)],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:200]
            or "Music.app konnte nicht geöffnet werden")
    return MUSIC_APP


def add_to_music_library(paths: list[str]) -> list[str]:
    """
    Importiert Dateien richtig in die Music.app-Bibliothek (AppleScript
    'add', nicht 'open' -- 'open' spielt nur ab/reveal't wie open_in_music()
    oben, importiert aber nicht dauerhaft). Liefert je Datei den POSIX-Pfad,
    den Music.app dem importierten Track zuweist ('location'-Eigenschaft).

    Steht in Music.app unter Einstellungen -> Dateien 'Dateien beim
    Hinzufuegen zur Mediathek in den Musik-Media-Ordner kopieren' auf AN (der
    Normalfall, und vom Code nicht setzbar), legt Music.app eine Kopie in
    seinem Medienordner an und benennt sie nach den Tags -- der Track der
    Bibliothek ist ab dann diese Kopie, nicht mehr die uebergebene Datei.
    'location' ist genau dieser neue Ort; server._post_add_to_library()
    schreibt die DB-Zeile darauf um. Das Original bleibt liegen (kopiert,
    nicht verschoben).

    Liefert je Datei den 'location'-Pfad, oder einen leeren String, wenn
    Music.app dafuer keinen lokalen Pfad hat ('location' ist 'missing value',
    z.B. bei einem nur ueber Apple-Music-Abgleich verknuepften Track). Der
    Import selbst (der 'add'-Befehl) gelingt in dem Fall trotzdem.
    """
    # ZWEI Koerzierungen muessen ausserhalb von 'tell application "Music"'
    # stehen, beide aus demselben Grund: innerhalb des tell-Blocks wird eine
    # Koerzierung als Apple-Event an Music.app geschickt, und Music.app kann
    # sie nicht ausfuehren.
    #   1. 'POSIX file p as alias' -> sonst Fehler -1700.
    #   2. 'POSIX path of <location>' -> sonst Fehler -1728, an echtem
    #      Material bestaetigt: "POSIX path of location of file track id ...
    #      kann nicht gelesen werden". Das traf JEDEN Track, nicht nur
    #      Cloud-Tracks ohne lokale Datei -- der frueher hier vermutete
    #      Zusammenhang mit Apple-Music-Abgleich war falsch. Weil der Fehler
    #      im 'try' verschwand und als leerer Pfad zurueckkam, blieb die
    #      Pfadkorrektur in _post_add_to_library() wirkungslos und die
    #      DB-Zeile zeigte weiter auf die Quelldatei.
    # Deshalb holt der tell-Block nur noch die 'location' selbst (ein
    # Datei-Objekt) und die Umwandlung in einen POSIX-Pfad passiert draussen.
    #
    # Ausserdem in zwei Durchgaengen: erst alle Dateien hinzufuegen, dann die
    # Orte lesen. Music.app kopiert beim Import in seinen Medienordner, und
    # gelesen werden soll der Ort NACH dem Kopieren.
    script = (
        'on run argv\n'
        '  set theAliases to {}\n'
        '  repeat with p in argv\n'
        '    set end of theAliases to (POSIX file p) as alias\n'
        '  end repeat\n'
        '  set theTracks to {}\n'
        '  tell application "Music"\n'
        '    repeat with a in theAliases\n'
        '      set end of theTracks to (add a)\n'
        '    end repeat\n'
        '  end tell\n'
        '  set theLocations to {}\n'
        '  repeat with t in theTracks\n'
        '    set theLoc to missing value\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set theLoc to location of t\n'
        '      on error\n'
        '        set theLoc to missing value\n'
        '      end try\n'
        '    end tell\n'
        '    if theLoc is missing value then\n'
        '      set end of theLocations to ""\n'
        '    else\n'
        '      try\n'
        '        set end of theLocations to POSIX path of theLoc\n'
        '      on error\n'
        '        set end of theLocations to ""\n'
        '      end try\n'
        '    end if\n'
        '  end repeat\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  return theLocations as text\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, *[str(p) for p in paths]],
                            capture_output=True, timeout=120)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app konnte die Dateien nicht importieren")
    # Leerzeilen NICHT herausfiltern: das Ergebnis wird Position fuer Position
    # den uebergebenen Pfaden zugeordnet (siehe _post_add_to_library, das
    # daraus den neuen Pfad je Datei ableitet). Eine Datei ohne lesbare
    # 'location' muss deshalb als leerer Eintrag an ihrer Stelle stehen
    # bleiben, sonst rutschen alle folgenden Pfade um eins nach vorn und die
    # Datenbank bekaeme den Pfad einer fremden Datei.
    lines = result.stdout.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()                  # abschliessender Zeilenumbruch von osascript
    locations = [line.strip() for line in lines]
    locations += [""] * (len(paths) - len(locations))
    return locations[:len(paths)]


# cloud status-Werte (Music.app-Dictionary, eClS), bei denen der Track auch
# in Apples iCloud-Mediathek/Katalog gefuehrt wird bzw. dort erneut auftauchen
# koennte -- 'delete' ist in AppleScript der einzige Loesch-Befehl (identisch
# mit einer manuellen Loeschung in Music.app, keine gesonderte "nur lokal"-
# Variante), ob die Loeschung dauerhaft aus der iCloud-Mediathek verschwindet,
# entscheidet Apples eigener Abgleich -- das laesst sich von hier aus weder
# erzwingen noch pruefen. remove_from_music_library() gibt deshalb nur einen
# Hinweis zurueck, keine Garantie.
_CLOUD_LIBRARY_STATUSES = {"matched", "uploaded", "purchased", "subscription", "prerelease"}


def remove_from_music_library(path: str, title: str | None = None) -> tuple[int, bool]:
    """
    Entfernt den Track mit dieser 'location' aus der Music.app-Bibliothek --
    Gegenstueck zu add_to_music_library(), aufgerufen wenn eine Datei, die
    in music_added steht, in den Papierkorb wandert (server._post_trash()).
    Ohne das bliebe in Music.app eine Karteileiche zurueck, die auf die
    geloeschte Datei zeigt.

    Matching wie in track_artwork()/add_to_music_library() dokumentiert: ein
    Bulk-/whose-Filter auf 'location' bricht mit Fehler -1728 fuer die
    GESAMTE Abfrage ab, sobald irgendein Track der Bibliothek keine lokale
    'location' hat (Cloud-/Streaming-Tracks, an echter Mediathek bestaetigt)
    -- die urspruengliche Fassung ('every track whose location is theAlias')
    lief deshalb bei JEDEM Aufruf auf diesen Fehler, nicht nur nach dem
    Papierkorb. Deshalb zuerst per Titel vorfiltern ('name' ist immer
    lesbar, ein Filter darauf unproblematisch), dann je Kandidat 'location'
    einzeln mit 'try' lesen und AUSSERHALB von 'tell application "Music"' zu
    einem POSIX-Pfad koerzieren (sonst -1728 "POSIX path of location ...
    kann nicht gelesen werden" fuer jeden Track, nicht koerzierbar als
    Apple-Event) -- erst der exakte Pfadvergleich entscheidet, welcher der
    (moeglicherweise mehreren gleichnamigen) Titel-Treffer gemeint ist.

    Ohne Titel (leerer/unbekannter Tag) muesste die GESAMTE Bibliothek
    einzeln durchsucht werden -- fuer einen einzelnen Papierkorb-Klick zu
    langsam, remove_from_music_library() gibt dann (0, False) zurueck statt
    zu haengen. server._post_trash() reicht dafuer row["title"] (mit dem
    Dateinamen als Rueckfallebene, Music.apps eigener Vorgabe fuer taglose
    Importe) durch.

    Liefert (Anzahl geloeschter Tracks, cloud_flag). cloud_flag ist True,
    wenn mindestens einer der geloeschten Tracks laut 'cloud status' in
    Apples iCloud-Mediathek/Katalog gefuehrt wurde (siehe
    _CLOUD_LIBRARY_STATUSES) -- server._post_trash() gibt das als Hinweis an
    die Oberflaeche weiter, NICHT als Fehler.
    """
    if not title:
        return 0, False
    script = (
        'on run argv\n'
        '  set thePath to item 1 of argv\n'
        '  set theTitle to item 2 of argv\n'
        '  set candidates to {}\n'
        '  tell application "Music"\n'
        '    try\n'
        '      set candidates to (every track of library playlist 1 whose name is theTitle)\n'
        '    end try\n'
        '  end tell\n'
        '  set theMatches to {}\n'
        '  set theStatuses to {}\n'
        '  repeat with c in candidates\n'
        '    set theLoc to missing value\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set theLoc to location of c\n'
        '      end try\n'
        '    end tell\n'
        '    if theLoc is not missing value then\n'
        '      try\n'
        '        set locPath to POSIX path of theLoc\n'
        '        if locPath is thePath then\n'
        '          set end of theMatches to c\n'
        '          set theStatus to "unknown"\n'
        '          tell application "Music"\n'
        '            try\n'
        '              set theStatus to (cloud status of c) as string\n'
        '            end try\n'
        '          end tell\n'
        '          set end of theStatuses to theStatus\n'
        '        end if\n'
        '      end try\n'
        '    end if\n'
        '  end repeat\n'
        '  set n to count of theMatches\n'
        '  if n > 0 then\n'
        '    tell application "Music"\n'
        '      repeat with m in theMatches\n'
        '        try\n'
        '          delete m\n'
        '        end try\n'
        '      end repeat\n'
        '    end tell\n'
        '  end if\n'
        '  set AppleScript\'s text item delimiters to ","\n'
        '  set statusLine to theStatuses as text\n'
        '  set AppleScript\'s text item delimiters to ""\n'
        '  return (n as text) & "|" & statusLine\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, str(path), str(title)],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:250]
            or "Music.app konnte den Track nicht entfernen")
    out = result.stdout.decode("utf-8", "replace").strip()
    count_part, _, status_part = out.partition("|")
    try:
        count = int(count_part)
    except ValueError:
        count = 0
    statuses = {s.strip().lower() for s in status_part.split(",") if s.strip()}
    cloud_flag = bool(statuses & _CLOUD_LIBRARY_STATUSES)
    return count, cloud_flag


def music_added_date_for(path: str, title: str | None) -> float | None:
    """
    Live-Check von 'date added' fuer EINEN Track -- Gegenstueck zu
    music_added_dates() (liest die GESAMTE Bibliothek, 30 Minuten Timeout,
    fuer einen einzelnen Klick zu langsam). Aufgerufen vom manuellen
    "neu analysieren"-Knopf, siehe server._post_reanalyse()/_post_analyse_path().

    Folgt demselben Titel-Vorfilter + Pfad-Abgleich wie
    remove_from_music_library() (siehe dort fuer die ausfuehrliche
    Begruendung: ein Bulk-/whose-Filter auf 'location' bricht mit Fehler
    -1728 fuer die GESAMTE Abfrage ab, sobald irgendein Bibliothekstrack
    keine lokale Datei hat). Die Epoch-Konstruktion (year/month/day/time of
    epoch) MUSS wie in music_added_dates() ausserhalb jedes
    'tell application "Music"' passieren, sonst Fehler -1731 (dort
    ausfuehrlich dokumentiert).

    Liefert None, wenn 'title' leer ist oder kein Treffer am exakten Pfad
    gefunden wurde -- der Aufrufer behandelt das als 'nicht in Music.app'.
    """
    if not title:
        return None
    script = (
        'on run argv\n'
        '  set thePath to item 1 of argv\n'
        '  set theTitle to item 2 of argv\n'
        '  set candidates to {}\n'
        '  tell application "Music"\n'
        '    try\n'
        '      set candidates to (every track of library playlist 1 whose name is theTitle)\n'
        '    end try\n'
        '  end tell\n'
        '  set epoch to (current date)\n'
        '  set year of epoch to 1970\n'
        '  set month of epoch to 1\n'
        '  set day of epoch to 1\n'
        '  set time of epoch to 0\n'
        '  set resultSecs to ""\n'
        '  repeat with c in candidates\n'
        '    set theLoc to missing value\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set theLoc to location of c\n'
        '      end try\n'
        '    end tell\n'
        '    if theLoc is not missing value then\n'
        '      try\n'
        '        set locPath to POSIX path of theLoc\n'
        '        if locPath is thePath then\n'
        '          tell application "Music"\n'
        '            set theAdded to date added of c\n'
        '            set secs to theAdded - epoch\n'
        '          end tell\n'
        '          set resultSecs to (secs as text)\n'
        '        end if\n'
        '      end try\n'
        '    end if\n'
        '  end repeat\n'
        '  return resultSecs\n'
        'end run'
    )
    try:
        result = subprocess.run(["osascript", "-e", script, str(path), str(title)],
                                capture_output=True, timeout=30)
    except Exception:                                      # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.decode("utf-8", "replace").strip()
    if not out:
        return None
    try:
        return float(out.replace(",", "."))
    except ValueError:
        return None


def music_added_dates() -> dict[str, float]:
    """
    Fragt 'date added' aus Music.app fuer die ganze Bibliothek ab -- eigene
    Sync-Aktion, unabhaengig vom Rekordbox-Abgleich (siehe db.sync_music_added).

    'date added' kommt als Bulk-Abfrage ueber 'of every track' (ein Apple
    Event fuer die ganze Liste). 'location' dagegen MUSS Track fuer Track
    gelesen werden: sowohl der Bulk-Getter als auch ein 'whose location is
    not missing value'-Filter brechen mit Fehler -1728 fuer die GESAMTE Liste
    ab, sobald auch nur ein Track keine lokale Datei hat (z.B. nur per
    Apple-Music-Abgleich in der Cloud, an echter iCloud-Musikmediathek
    bestaetigt) -- offenbar wirft das Lesen der Eigenschaft dort schon
    waehrend der Filterauswertung. Der Einzelzugriff mit 'try' pro Track ist
    deshalb der einzige zuverlaessige Weg, kostet dafuer ein Apple Event pro
    Track (bei zehntausenden Tracks entsprechend spuerbar langsamer). Die
    Schleife MUSS dabei innerhalb eines eigenen 'tell application "Music"'
    laufen -- ausserhalb geht der Bezug von 'item i of trackList' auf ein
    Music.app-Objekt verloren, 'location of (...)' scheitert dann fuer JEDEN
    Track mit Deskriptor-Fehler -10001 (an echtem Material bestaetigt). Die
    Epoch-Konstruktion wiederum MUSS ausserhalb jedes 'tell application
    "Music"' passieren -- 'set year/month/day/time of epoch to ...' innerhalb
    des Blocks wird als Apple-Event an Music.app geschickt (wie die
    'as alias'-Koerzierung in add_to_music_library() oben, nur umgekehrt
    riskiert), und Music.app kennt diese Eigenschaften eines gewoehnlichen
    AppleScript-Datumsobjekts nicht -- Fehler -1731 (ebenfalls bestaetigt).
    Deshalb zwei getrennte 'tell'-Bloecke um die epoch-Konstruktion herum.

    Datumsdifferenz statt AppleScript-Datumstext: 'date added as string' ist
    lokalisiert und liesse sich in Python nicht zuverlaessig zurueckparsen.
    Stattdessen wird pro Track die Differenz zu einer lokal konstruierten
    Epoch (1.1.1970) in Sekunden berechnet -- ein Unix-Timestamp. osascript
    gibt diese Zahl trotzdem lokalisiert zurueck (Komma als Dezimaltrenner,
    ab einer gewissen Groesse in Exponentialschreibweise, z.B.
    "1,566064242E+9") -- das Komma wird deshalb vor dem float()-Parsing in
    Python durch einen Punkt ersetzt, den Rest übernimmt float() von selbst.

    Liefert {POSIX-Pfad: Unix-Timestamp}. Tracks ohne lokale 'location'
    (z.B. nur per Apple-Music-Abgleich verknuepft, kein lokales File) fehlen
    im Ergebnis.
    """
    script = (
        'tell application "Music"\n'
        '  set dateList to date added of every track of library playlist 1\n'
        '  set trackList to every track of library playlist 1\n'
        'end tell\n'
        'set epoch to (current date)\n'
        'set year of epoch to 1970\n'
        'set month of epoch to 1\n'
        'set day of epoch to 1\n'
        'set time of epoch to 0\n'
        'set outList to {}\n'
        'tell application "Music"\n'
        '  repeat with i from 1 to (count of trackList)\n'
        '    try\n'
        '      set loc to location of (item i of trackList)\n'
        '      if loc is not missing value then\n'
        '        set p to POSIX path of loc\n'
        '        set secs to (item i of dateList) - epoch\n'
        '        set end of outList to (p & tab & (secs as text))\n'
        '      end if\n'
        '    end try\n'
        '  end repeat\n'
        'end tell\n'
        'set AppleScript\'s text item delimiters to linefeed\n'
        'return outList as text\n'
    )
    result = subprocess.run(["osascript", "-e", script],
                            capture_output=True, timeout=1800)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app-Bibliothek konnte nicht gelesen werden")
    out = result.stdout.decode("utf-8", "replace").strip()
    mapping: dict[str, float] = {}
    for line in out.splitlines():
        path, _, secs = line.partition("\t")
        if not path or not secs:
            continue
        try:
            mapping[path] = float(secs.replace(",", "."))
        except ValueError:
            continue
    return mapping


def music_playlists() -> list[dict]:
    """
    Liest den Playlisten-Baum aus Music.app (nur lesend, nichts wird
    geaendert). Liefert eine flache Liste von Knoten:
    {id, parent, kind, count, name} mit kind aus playlist/smart/folder.

    Drei Eigenheiten des Music.app-Dictionarys, an echtem Material bestaetigt:

    1. 'every user playlist' enthaelt die ORDNER NICHT, obwohl 'folder
       playlist' laut Dictionary von 'user playlist' erbt. Sie muessen ueber
       'every folder playlist' getrennt geholt und ueber die persistent ID
       mit dem 'parent' der Playlisten verknuepft werden.
    2. 'class of p is folder playlist' bricht mit Fehler -1731 ab ("Unbekannter
       Objekttyp") -- deshalb die getrennte Abfrage statt einer Fallunter-
       scheidung in einer Schleife.
    3. 'special kind' unterscheidet die eingebaute Mediathek ("Music") von
       eigenen Listen ("none"). Die Mediathek selbst bleibt aussen vor -- sie
       entspricht dem eigenen Knoten "Alle" und wuerde beim Anklicken die
       ganze Bibliothek nachladen.

    Die persistent ID ist ein stabiler Hex-Text und aendert sich laut
    Dictionary nie -- der richtige Schluessel fuer die Baumstruktur, anders
    als 'id' (eine je Sitzung vergebene Zahl) oder der Name (mehrfach
    vergeben, aenderbar).
    """
    script = (
        'tell application "Music"\n'
        '  set out to {}\n'
        '  repeat with p in user playlists\n'
        '    if (special kind of p as text) is "none" then\n'
        '      set par to ""\n'
        '      try\n'
        '        set par to persistent ID of (get parent of p)\n'
        '      end try\n'
        '      set kindTxt to "playlist"\n'
        '      if smart of p then set kindTxt to "smart"\n'
        '      set end of out to ((persistent ID of p) & tab & par & tab & kindTxt '
        '& tab & (count of tracks of p) & tab & (name of p))\n'
        '    end if\n'
        '  end repeat\n'
        '  repeat with f in folder playlists\n'
        '    set par2 to ""\n'
        '    try\n'
        '      set par2 to persistent ID of (get parent of f)\n'
        '    end try\n'
        '    set end of out to ((persistent ID of f) & tab & par2 & tab & "folder" '
        '& tab & "0" & tab & (name of f))\n'
        '  end repeat\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  return out as text\n'
        'end tell\n'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=120)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app-Playlisten konnten nicht gelesen werden")
    nodes: list[dict] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        pid, parent, kind, count, name = parts[0], parts[1], parts[2], parts[3], "\t".join(parts[4:])
        if not pid:
            continue
        try:
            n = int(count)
        except ValueError:
            n = 0
        nodes.append({"id": pid, "parent": parent or None, "kind": kind,
                      "count": n, "name": name})
    return nodes


def music_playlist_tracks(persistent_id: str) -> list[dict]:
    """
    Tracks einer Music.app-Playlist in ihrer Reihenfolge. Liefert je Track
    {id, name, artist, path} -- 'path' ist leer, wenn es keine lokale Datei
    gibt (Apple-Music-/Cloud-Track).

    Warum drei parallele Listen statt einer Schleife: 'location of every
    track' bricht mit Fehler -1728 fuer die GANZE Liste ab, sobald ein Track
    keine lokale Datei hat (an echtem Material bestaetigt: 116 Tracks, davon
    10 aus der Cloud). 'location of every file track' funktioniert, liefert
    aber nur die Datei-Tracks -- die Liste ist damit NICHT positionsgleich zu
    'every track'. Deshalb wird zusaetzlich die persistent ID beider Mengen
    geholt und in Python ueber sie verbunden: das erhaelt die Reihenfolge UND
    markiert die Tracks ohne lokale Datei, ohne ein Apple Event je Track
    (eine 728er-Playlist braucht so 1,6 s statt Minuten).

    Die Umwandlung des 'location'-Dateiobjekts in einen POSIX-Pfad passiert
    im selben tell-Block -- 'POSIX path of' auf einem Datei-Objekt ist eine
    reine AppleScript-Operation und laeuft dort ohne den -1728, der beim
    Lesen der Eigenschaft eines Cloud-Tracks entsteht.
    """
    script = (
        'on run argv\n'
        '  set pid to item 1 of argv\n'
        '  tell application "Music"\n'
        '    set pl to (first user playlist whose persistent ID is pid)\n'
        '    set allIDs to (get persistent ID of every track of pl)\n'
        '    set allNames to (get name of every track of pl)\n'
        '    set allArtists to (get artist of every track of pl)\n'
        '    set fileIDs to (get persistent ID of every file track of pl)\n'
        '    set fileLocs to (get location of every file track of pl)\n'
        '    set out to {}\n'
        '    repeat with i from 1 to (count of allIDs)\n'
        '      set end of out to ((item i of allIDs) & tab & (item i of allNames) '
        '& tab & (item i of allArtists))\n'
        '    end repeat\n'
        '    set out2 to {}\n'
        '    repeat with i from 1 to (count of fileIDs)\n'
        '      try\n'
        '        set end of out2 to ((item i of fileIDs) & tab & (POSIX path of (item i of fileLocs)))\n'
        '      end try\n'
        '    end repeat\n'
        '    set AppleScript\'s text item delimiters to linefeed\n'
        '    return (out as text) & linefeed & "---" & linefeed & (out2 as text)\n'
        '  end tell\n'
        'end run\n'
    )
    result = subprocess.run(["osascript", "-e", script, persistent_id],
                            capture_output=True, timeout=300)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app-Playlist konnte nicht gelesen werden")
    text = result.stdout.decode("utf-8", "replace")
    head, _, tail = text.partition("\n---\n")
    paths: dict[str, str] = {}
    for line in tail.splitlines():
        tid, _, path = line.partition("\t")
        if tid and path:
            paths[tid] = path
    tracks: list[dict] = []
    for line in head.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        tid = parts[0]
        tracks.append({
            "id": tid,
            "name": parts[1] if len(parts) > 1 else "",
            "artist": parts[2] if len(parts) > 2 else "",
            "path": paths.get(tid, ""),
        })
    return tracks


def track_artwork(items: list[tuple[str, str]]) -> dict[str, tuple[bytes, str] | None]:
    """
    Liest das in Music.app hinterlegte Artwork fuer eine Liste von
    (Pfad, Titel)-Paaren -- fuer Tracks (v.a. WAV/AIFF), bei denen Music.app
    das Cover nicht in der Datei selbst speichert, sondern nur intern
    zuordnet. Liefert je Pfad (Bilddaten, Mime) oder None (kein passender
    Track gefunden oder kein Artwork vorhanden).

    Matching in zwei Schritten, beide aus denselben Gruenden wie in
    add_to_music_library()/music_added_dates() dokumentiert:
      1. Vorfilter ueber den Titel ('whose name is theTitle') -- ein
         Bulk-Filter auf 'location' waere hier NICHT sicher: er bricht mit
         Fehler -1728 fuer die GESAMTE Abfrage ab, sobald irgendein Track der
         Bibliothek keine lokale 'location' hat (an echtem Material
         bestaetigt, siehe add_to_music_library()). 'name' ist dagegen immer
         lesbar, ein Filter darauf also unproblematisch.
      2. Je Namens-Treffer 'location' einzeln mit 'try' lesen (derselbe
         Grund) UND ausserhalb von 'tell application "Music"' zu einem
         POSIX-Pfad koerzieren (sonst Fehler -1728 'POSIX path of location
         ... kann nicht gelesen werden' fuer JEDEN Track, nicht koerzierbar
         als Apple-Event) -- erst dieser exakte Pfadvergleich entscheidet,
         welcher der (moeglicherweise mehreren) Namens-Treffer gemeint ist.
         Kein Titel-Treffer oder keine passende 'location' -> Pfad wird
         uebersprungen, es wird nicht geraten.

    Bilddaten lassen sich nicht sauber ueber osascripts Text-Stdout
    zurueckgeben -- werden deshalb je Kandidat in eine eigene Datei in einem
    temporaeren Ordner geschrieben, das Manifest (Tab-getrennt: Index,
    Status, ggf. Format) kommt ueber Stdout zurueck.
    """
    if not items:
        return {}
    script = (
        'on run argv\n'
        '  set outDir to item 1 of argv\n'
        '  set n to ((count of argv) - 1) / 2\n'
        '  set results to {}\n'
        '  repeat with i from 1 to n\n'
        '    set thePath to item (2 + (i - 1) * 2) of argv\n'
        '    set theTitle to item (3 + (i - 1) * 2) of argv\n'
        '    set foundTrack to missing value\n'
        '    set candidates to {}\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set candidates to (every track of library playlist 1 whose name is theTitle)\n'
        '      end try\n'
        '    end tell\n'
        '    repeat with c in candidates\n'
        '      if foundTrack is missing value then\n'
        '        set theLoc to missing value\n'
        '        tell application "Music"\n'
        '          try\n'
        '            set theLoc to location of c\n'
        '          end try\n'
        '        end tell\n'
        '        if theLoc is not missing value then\n'
        '          try\n'
        '            set locPath to POSIX path of theLoc\n'
        '            if locPath is thePath then set foundTrack to c\n'
        '          end try\n'
        '        end if\n'
        '      end if\n'
        '    end repeat\n'
        '    if foundTrack is missing value then\n'
        '      set end of results to ((i as text) & tab & "NOTFOUND")\n'
        '    else\n'
        '      set artCount to 0\n'
        '      tell application "Music"\n'
        '        try\n'
        '          set artCount to count of artworks of foundTrack\n'
        '        end try\n'
        '      end tell\n'
        '      if artCount is 0 then\n'
        '        set end of results to ((i as text) & tab & "NOARTWORK")\n'
        '      else\n'
        '        set gotData to false\n'
        '        set theFormat to ""\n'
        '        tell application "Music"\n'
        '          try\n'
        '            set theArt to artwork 1 of foundTrack\n'
        '            set theFormat to (format of theArt) as text\n'
        '            set theData to data of theArt\n'
        '            set gotData to true\n'
        '          end try\n'
        '        end tell\n'
        '        if gotData then\n'
        '          set outPath to outDir & "/" & i & ".bin"\n'
        '          set theFile to (open for access POSIX file outPath with write permission)\n'
        '          set eof theFile to 0\n'
        '          write theData to theFile\n'
        '          close access theFile\n'
        '          set end of results to ((i as text) & tab & "OK" & tab & theFormat)\n'
        '        else\n'
        '          set end of results to ((i as text) & tab & "NOARTWORK")\n'
        '        end if\n'
        '      end if\n'
        '    end if\n'
        '  end repeat\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  return results as text\n'
        'end run'
    )
    import tempfile
    out: dict[str, tuple[bytes, str] | None] = {}
    with tempfile.TemporaryDirectory() as out_dir:
        argv = [out_dir]
        for path, title in items:
            argv += [str(path), str(title)]
        result = subprocess.run(["osascript", "-e", script, *argv],
                                capture_output=True, timeout=30 + 5 * len(items))
        if result.returncode != 0:
            raise MediaToolMissing(
                result.stderr.decode("utf-8", "replace").strip()[:300]
                or "Music.app-Artwork konnte nicht gelesen werden")
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("\t")
            if not parts or not parts[0]:
                continue
            try:
                idx = int(parts[0]) - 1
            except ValueError:
                continue
            if idx < 0 or idx >= len(items):
                continue
            path = items[idx][0]
            if len(parts) < 2 or parts[1] != "OK":
                out[path] = None
                continue
            fmt = parts[2] if len(parts) > 2 else ""
            mime = "image/png" if "PNG" in fmt.upper() else "image/jpeg"
            bin_path = Path(out_dir) / f"{idx + 1}.bin"
            out[path] = (bin_path.read_bytes(), mime) if bin_path.is_file() else None
    return out


def set_track_artwork(path: str, title: str, data: bytes, mime: str) -> int:
    """
    Setzt das Artwork eines bereits in Music.app importierten Tracks direkt --
    Gegenstueck zu track_artwork() (liest), aufgerufen wenn ein Cover ueber
    /api/cover bzw. /api/lookup-cover geschrieben wird und die Datei laut
    music_added bereits in der Bibliothek steht (server.py). Bewusst KEIN
    Entfernen+Neu-Hinzufuegen (add_to_music_library()/remove_from_music_library()):
    das wuerde Playlisten-Zugehoerigkeit, Bewertung und Play-Count unkontrolliert
    verlieren, nichts im Code sichert das ab. Stattdessen die seit den
    iTunes-AppleScript-Tagen uebliche Technik: die Artwork-Eigenschaft eines
    bestehenden Track-Objekts direkt setzen, ohne ihn anzufassen.

    Matching wie remove_from_music_library()/track_artwork(): Titel-Vorfilter
    ('name' ist immer lesbar), dann 'location' je Kandidat einzeln mit 'try'
    lesen und AUSSERHALB von 'tell application "Music"' zum POSIX-Pfad
    koerzieren (dieselben Gruende wie dort -- eine Koerzierung als Apple-Event
    an Music.app scheitert mit -1700/-1728). Erst der exakte Pfadvergleich
    entscheidet, welche der (moeglicherweise mehreren) Namens-Treffer gemeint
    sind -- alle davon werden geaendert, es gibt kein "mehrdeutig", nur "0
    getroffen" oder "1+ getroffen". Ohne Titel sofort 0 statt Voll-Scan.

    Bilddaten koennen aus jedem Format kommen, das der Cover-Dialog akzeptiert
    (accept="image/*" im UI, keine weitere Pruefung) -- AppleScripts
    'read ... as picture'-Koerzierung kennt aber nur wenige klassische
    Bildklassen zuverlaessig, WebP/HEIC/GIF/TIFF u.ae. sind fraglich. Deshalb
    werden die Bilddaten IMMER zuerst per sips (in macOS eingebaut) nach JPEG
    konvertiert, danach gibt es nur noch einen deterministischen
    Koerzierungspfad ('as JPEG picture'). Betrifft nur diese transiente
    Kopie, nicht die in der Datei eingebetteten Original-Bytes.

    Liefert die Anzahl getroffener Tracks (0 = Titel nicht mehr passend oder
    Track nicht in der Bibliothek -- kein Fehler, server.py macht daraus
    einen weichen Hinweis statt eines Fehlers).
    """
    if not title:
        return 0
    import mimetypes
    import tempfile
    ext = mimetypes.guess_extension(mime, strict=False) or ".img"
    if ext == ".jpe":
        ext = ".jpg"
    with tempfile.TemporaryDirectory() as tmp_dir:
        src_path = Path(tmp_dir) / f"src{ext}"
        src_path.write_bytes(data)
        img_path = Path(tmp_dir) / "cover.jpg"
        sips = shutil.which("sips") or "/usr/bin/sips"
        conv = subprocess.run(
            [sips, "-s", "format", "jpeg", str(src_path), "--out", str(img_path)],
            capture_output=True, timeout=30)
        if conv.returncode != 0 or not img_path.is_file():
            raise MediaToolMissing(
                conv.stderr.decode("utf-8", "replace").strip()[:250]
                or "Cover konnte nicht fuer Music.app konvertiert werden")
        script = (
            'on run argv\n'
            '  set thePath to item 1 of argv\n'
            '  set theTitle to item 2 of argv\n'
            '  set imgPath to item 3 of argv\n'
            '  set theData to (read (POSIX file imgPath) as JPEG picture)\n'
            '  set candidates to {}\n'
            '  tell application "Music"\n'
            '    try\n'
            '      set candidates to (every track of library playlist 1 whose name is theTitle)\n'
            '    end try\n'
            '  end tell\n'
            '  set theMatches to {}\n'
            '  repeat with c in candidates\n'
            '    set theLoc to missing value\n'
            '    tell application "Music"\n'
            '      try\n'
            '        set theLoc to location of c\n'
            '      end try\n'
            '    end tell\n'
            '    if theLoc is not missing value then\n'
            '      try\n'
            '        set locPath to POSIX path of theLoc\n'
            '        if locPath is thePath then set end of theMatches to c\n'
            '      end try\n'
            '    end if\n'
            '  end repeat\n'
            '  set n to count of theMatches\n'
            '  if n > 0 then\n'
            '    tell application "Music"\n'
            '      repeat with m in theMatches\n'
            '        try\n'
            '          set data of artwork 1 of m to theData\n'
            '        on error\n'
            '          try\n'
            '            make new artwork at end of artworks of m with properties {data:theData}\n'
            '          end try\n'
            '        end try\n'
            '      end repeat\n'
            '    end tell\n'
            '  end if\n'
            '  return (n as text)\n'
            'end run'
        )
        result = subprocess.run(
            ["osascript", "-e", script, str(path), str(title), str(img_path)],
            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app-Artwork konnte nicht gesetzt werden")
    try:
        return int(result.stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return 0


def _set_tracks_field(items: list[tuple[str, str]], value: str, prop: str) -> int:
    """
    Setzt eine Music.app-Track-Eigenschaft (genre/album/artist) bereits
    importierter Tracks direkt -- gemeinsame Grundlage fuer
    set_tracks_genre()/set_tracks_album()/set_tracks_artist(), aufgerufen von
    server._rename_tag_value() fuer alle Pfade, die laut music_added bereits
    in der Bibliothek stehen. Gleiches Titel-dann-Pfad-Matching wie
    set_track_artwork()/clear_track_artwork(), aber fuer BELIEBIG VIELE
    Tracks in einem einzigen osascript-Aufruf statt eines Prozesses je Track
    (ein Umbenennen kann hunderte Tracks treffen) -- Pfade und Titel werden
    dafuer analog zum Rueckgabemuster von add_to_music_library() mit
    Zeilenumbruch verbunden uebergeben statt als einzelne argv-Eintraege.

    Tracks ohne Titel werden uebersprungen (kein Vorfilter moeglich, wie bei
    set_track_artwork() -- "ohne Titel sofort 0 statt Voll-Scan").

    Liefert die Gesamtzahl getroffener Tracks ueber alle Paare.
    """
    items = [(p, t) for p, t in items if t]
    if not items:
        return 0
    paths_text = "\n".join(p for p, _ in items)
    titles_text = "\n".join(t for _, t in items)
    script = (
        'on run argv\n'
        '  set theValue to item 1 of argv\n'
        '  set thePathsText to item 2 of argv\n'
        '  set theTitlesText to item 3 of argv\n'
        '  set AppleScript\'s text item delimiters to linefeed\n'
        '  set thePaths to text items of thePathsText\n'
        '  set theTitles to text items of theTitlesText\n'
        '  set AppleScript\'s text item delimiters to ""\n'
        '  set matchedCount to 0\n'
        '  repeat with i from 1 to (count of thePaths)\n'
        '    set thePath to item i of thePaths\n'
        '    set theTitle to item i of theTitles\n'
        '    set candidates to {}\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set candidates to (every track of library playlist 1 whose name is theTitle)\n'
        '      end try\n'
        '    end tell\n'
        '    repeat with c in candidates\n'
        '      set theLoc to missing value\n'
        '      tell application "Music"\n'
        '        try\n'
        '          set theLoc to location of c\n'
        '        end try\n'
        '      end tell\n'
        '      if theLoc is not missing value then\n'
        '        try\n'
        '          set locPath to POSIX path of theLoc\n'
        '          if locPath is thePath then\n'
        '            tell application "Music"\n'
        '              try\n'
        f'                set {prop} of c to theValue\n'
        '                set matchedCount to matchedCount + 1\n'
        '              end try\n'
        '            end tell\n'
        '          end if\n'
        '        end try\n'
        '      end if\n'
        '    end repeat\n'
        '  end repeat\n'
        '  return (matchedCount as text)\n'
        'end run'
    )
    result = subprocess.run(
        ["osascript", "-e", script, str(value), paths_text, titles_text],
        capture_output=True, timeout=120)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or f"Music.app-{prop.capitalize()} konnte nicht gesetzt werden")
    try:
        return int(result.stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return 0


def set_tracks_genre(items: list[tuple[str, str]], genre: str) -> int:
    return _set_tracks_field(items, genre, "genre")


def set_tracks_album(items: list[tuple[str, str]], album: str) -> int:
    return _set_tracks_field(items, album, "album")


def set_tracks_artist(items: list[tuple[str, str]], artist: str) -> int:
    return _set_tracks_field(items, artist, "artist")


def clear_track_artwork(path: str, title: str) -> int:
    """
    Loescht das Artwork eines bereits in Music.app importierten Tracks direkt
    -- Gegenstueck zu set_track_artwork(), aufgerufen von
    server._post_cover_delete() wenn die Datei laut music_added bereits in
    der Bibliothek steht. Gleiches Titel-dann-Pfad-Matching, aber ohne
    Bildkonvertierung/-koerzierung: reines 'delete every artwork of m', in
    'try' gekapselt (kein Fehler, falls ohnehin schon leer).

    Liefert die Anzahl getroffener Tracks (0 = kein passender Track
    gefunden -- kein Fehler, siehe set_track_artwork()).
    """
    if not title:
        return 0
    script = (
        'on run argv\n'
        '  set thePath to item 1 of argv\n'
        '  set theTitle to item 2 of argv\n'
        '  set candidates to {}\n'
        '  tell application "Music"\n'
        '    try\n'
        '      set candidates to (every track of library playlist 1 whose name is theTitle)\n'
        '    end try\n'
        '  end tell\n'
        '  set theMatches to {}\n'
        '  repeat with c in candidates\n'
        '    set theLoc to missing value\n'
        '    tell application "Music"\n'
        '      try\n'
        '        set theLoc to location of c\n'
        '      end try\n'
        '    end tell\n'
        '    if theLoc is not missing value then\n'
        '      try\n'
        '        set locPath to POSIX path of theLoc\n'
        '        if locPath is thePath then set end of theMatches to c\n'
        '      end try\n'
        '    end if\n'
        '  end repeat\n'
        '  set n to count of theMatches\n'
        '  if n > 0 then\n'
        '    tell application "Music"\n'
        '      repeat with m in theMatches\n'
        '        try\n'
        '          delete every artwork of m\n'
        '        end try\n'
        '      end repeat\n'
        '    end tell\n'
        '  end if\n'
        '  return (n as text)\n'
        'end run'
    )
    result = subprocess.run(["osascript", "-e", script, str(path), str(title)],
                            capture_output=True, timeout=30)
    if result.returncode != 0:
        raise MediaToolMissing(
            result.stderr.decode("utf-8", "replace").strip()[:300]
            or "Music.app-Artwork konnte nicht geloescht werden")
    try:
        return int(result.stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return 0


def _icns_of(app_path: str) -> Path | None:
    """Icon-Datei eines App-Bundles ueber dessen Info.plist finden."""
    resources = Path(app_path) / "Contents" / "Resources"
    plist = Path(app_path) / "Contents" / "Info.plist"
    name = ""
    if plist.is_file():
        out = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIconFile", str(plist)],
            capture_output=True, timeout=15)
        name = out.stdout.decode("utf-8", "replace").strip()
    for candidate in ([resources / name, resources / f"{name}.icns"] if name else []):
        if candidate.is_file():
            return candidate
    icons = sorted(resources.glob("*.icns")) if resources.is_dir() else []
    return icons[0] if icons else None


def app_icon_png(app_path: str, size: int = 64) -> bytes:
    """Icon eines Programms als PNG. Ergebnis wird zwischengespeichert."""
    key = f"{app_path}:{size}"
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    icns = _icns_of(app_path)
    if icns is None:
        raise FileNotFoundError(f"Kein Symbol in {app_path}")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        out_path = fh.name
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "png", "-Z", str(size), str(icns),
             "--out", out_path],
            capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError("sips konnte das Symbol nicht umwandeln")
        data = Path(out_path).read_bytes()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    _ICON_CACHE[key] = data
    return data


# ── iTunes Store ──────────────────────────────────────────────────────────
# Zum Kaufen, nicht zum Streamen: itmss: oeffnet den Store in der Music-App.
# Ueber den Server aufgerufen statt als Link, damit der Browser nicht bei
# jedem Klick nachfragt, ob er das Schema oeffnen darf.

def itunes_lookup(query: str, country: str = "DE", limit: int = 8) -> dict | None:
    """
    Den Titel ueber Apples oeffentliche Such-API nachschlagen.

    Der Umweg lohnt sich: ein Such-Deeplink laesst die Music-App selbst suchen
    und landet unzuverlaessig, waehrend die direkte Adresse eines Titels
    zielsicher aufgeht. Nebenbei kommt der Kaufpreis mit.

    Gesendet wird nur der Suchtext (Artist und Titel), sonst nichts.
    """
    import json as _json
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({
        "term": query, "media": "music", "entity": "song",
        "country": country, "limit": limit,
    })
    req = urllib.request.Request(
        "https://itunes.apple.com/search?" + params,
        headers={"User-Agent": "MP3-Quality-Check/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:                                # noqa: BLE001
        return None

    results = data.get("results") or []
    if not results:
        return None
    # Kaufbare Treffer zuerst — Album-only-Titel haben keinen Einzelpreis
    results.sort(key=lambda r: (r.get("trackPrice") is None,))
    return results[0]


def itunes_search_url(query: str) -> str:
    """Rueckfallebene: die Music-App selbst suchen lassen."""
    from urllib.parse import quote
    return ("itmss://itunes.apple.com/search?term=" + quote(query)
            + "&media=music&entity=song")


def open_itunes_store(query: str) -> dict:
    """
    Oeffnet den Titel im iTunes Store. Liefert zurueck, was gefunden wurde,
    damit die Oberflaeche Titel und Preis anzeigen kann.
    """
    hit = itunes_lookup(query)
    if hit and hit.get("trackViewUrl"):
        # Ohne app=itunes landet der Aufruf auf der Apple-Music-Seite des
        # Titels, von wo aus erst ein Knopf in den Store fuehrt. Der
        # Parameter ueberspringt diesen Zwischenschritt.
        target = hit["trackViewUrl"]
        if "app=itunes" not in target:
            target += ("&" if "?" in target else "?") + "app=itunes"
        url = target.replace("https://", "itmss://", 1)
        found = {
            "artist": hit.get("artistName", ""),
            "track": hit.get("trackName", ""),
            "price": hit.get("trackPrice"),
            "currency": hit.get("currency", ""),
            "web_url": hit.get("trackViewUrl", ""),
        }
    else:
        url = itunes_search_url(query)
        found = {"artist": "", "track": "", "price": None,
                 "currency": "", "web_url": ""}

    result = subprocess.run(["open", url], capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", "replace").strip()[:200]
            or "Der iTunes Store konnte nicht geöffnet werden")
    # 'open' mit einem Schema startet die App nur im Hintergrund
    subprocess.run(["osascript", "-e", 'tell application "Music" to activate'],
                   capture_output=True, timeout=20)
    found["url"] = url
    found["matched"] = bool(hit)
    return found
