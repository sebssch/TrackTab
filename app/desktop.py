"""
Einstiegspunkt der gepackten App.

Unterschiede zum Betrieb aus dem Quellbaum:
  - Datenbank, Report und Konfiguration liegen unter Application Support,
    weil in ein .app-Bundle nicht geschrieben werden darf.
  - Fehler landen in einem macOS-Dialog statt auf einer Konsole, die es
    im Fenstermodus gar nicht gibt.
  - Beim allerersten Start existiert noch kein Report; er wird angelegt,
    damit die Oberflaeche startet und Bibliothek samt Scan dort eingerichtet
    werden koennen.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser

from . import config as cfgmod
from . import db as db_mod
from . import media
from . import report as report_mod

_FIRST_PORT = 8756
# Eigener Portbereich fuer ein .app-Bundle, das noch nicht nach /Applications
# verschoben wurde (z.B. dist/TrackTab.app aus build_app.sh) -- sonst haelt
# _existing_instance() eine parallel laufende installierte Version faelschlich
# fuer sich selbst und oeffnet nur deren Fenster, statt einen eigenen Server
# zu starten. cfgmod.is_installed_location() entscheidet auch, welchen
# Application-Support-Ordner base_dir() dafuer verwendet (eigene Datenbank).
_BUILD_FIRST_PORT = 8790
_PORT_TRIES = 20


def _default_first_port() -> int:
    return _FIRST_PORT if cfgmod.is_installed_location() else _BUILD_FIRST_PORT


def dialog(text: str, title: str = "TrackTab", stop: bool = True,
           buttons: list[str] | None = None) -> str:
    """
    Meldung im Fenstermodus — print() sieht dort niemand.
    Liefert die gedrueckte Schaltflaeche zurueck (leer bei Abbruch).
    """
    icon = "stop" if stop else "note"
    labels = buttons or ["OK"]
    button_list = "{" + ", ".join(_esc(b) for b in labels) + "}"
    script = (f'display dialog {_esc(text)} buttons {button_list} '
              f'default button {len(labels)} with icon {icon} with title {_esc(title)}')
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, timeout=600)
        answer = out.stdout.decode("utf-8", "replace").strip()
        return answer.split("button returned:")[-1].strip() if answer else ""
    except (OSError, subprocess.SubprocessError):
        print(text, file=sys.stderr)
        return ""


def ensure_tools() -> bool:
    """
    Prueft ffmpeg/ffprobe und bietet die Installation an, wenn sie fehlen.

    Bewusst nichts im Hintergrund: die Installation laeuft sichtbar im
    Terminal, damit nachvollziehbar bleibt, was auf dem Rechner passiert.
    """
    ok, message = media.available()
    if ok:
        return True

    brew = media.brew_path()
    if brew:
        answer = dialog(
            "TrackTab braucht ffmpeg zum Auswerten der Audiodateien.\n\n"
            "Homebrew ist auf diesem Mac vorhanden. Soll ffmpeg jetzt installiert "
            "werden? Das öffnet ein Terminal-Fenster, in dem du den Fortschritt "
            "siehst, und dauert einige Minuten.",
            stop=False, buttons=["Abbrechen", "Im Terminal installieren"])
        if answer.startswith("Im Terminal"):
            _run_in_terminal(f"{brew} install ffmpeg")
            dialog("Sobald die Installation im Terminal durchgelaufen ist, "
                   "starte TrackTab bitte neu.", stop=False)
        return False

    answer = dialog(
        "TrackTab braucht ffmpeg zum Auswerten der Audiodateien.\n\n"
        "Auf diesem Mac ist auch Homebrew nicht installiert. Der einfachste Weg:\n\n"
        "1. Homebrew installieren (brew.sh)\n"
        "2. Im Terminal:  brew install ffmpeg\n"
        "3. TrackTab erneut starten\n\n"
        "Alternativ kannst du ein statisch gelinktes ffmpeg und ffprobe in den "
        "Ordner vendor/ neben der App legen.",
        stop=True, buttons=["Abbrechen", "brew.sh öffnen"])
    if answer.startswith("brew.sh"):
        webbrowser.open("https://brew.sh")
    return False


def _run_in_terminal(command: str) -> None:
    """Befehl sichtbar im Terminal starten statt still im Hintergrund."""
    script = (f'tell application "Terminal" to do script {_esc(command)}\n'
              f'tell application "Terminal" to activate')
    try:
        subprocess.run(["osascript", "-e", script], timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def _esc(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _existing_instance(first: int = _FIRST_PORT) -> int | None:
    """
    Sucht eine bereits laufende Instanz und liefert deren Port.

    Ohne diese Pruefung nimmt _free_port() beim zweiten Doppelklick einfach
    den naechsten freien Port: zwei Server auf derselben Datenbank, und die
    zweite Oberflaeche bekommt Aenderungen der ersten nicht mit. Der Bereich
    wird ganz abgesucht, weil frueher gestartete Instanzen tatsaechlich auf
    einem der hoeheren Ports haengen koennen.

    Auf 127.0.0.1 antwortet ein geschlossener Port sofort mit "refused" --
    die Schleife kostet daher praktisch keine Zeit.
    """
    for port in range(first, first + _PORT_TRIES):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/ping", timeout=1.0) as res:
                data = json.loads(res.read(4096).decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            continue
        if isinstance(data, dict) and data.get("ok"):
            return port
    return None


def _free_port(first: int = _FIRST_PORT) -> int | None:
    """
    Erster Port, auf dem der Server binden kann.

    SO_REUSEADDR ist hier kein Detail, sondern noetig, damit die Probe
    dasselbe sieht wie der Server spaeter: ThreadingHTTPServer setzt die
    Option selbst (allow_reuse_address). Ohne sie scheiterte die Probe an
    den TIME_WAIT-Resten der eben geschlossenen Verbindungen -- nach jedem
    Beenden waere der naechste Start eine Portnummer weiter gewandert, bis
    der Bereich aufgebraucht ist.
    """
    for port in range(first, first + _PORT_TRIES):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


class _Quiet:
    """Ersatz fuer die rich-Konsole, wenn keine Konsole da ist."""

    def print(self, *args, **kwargs):
        try:
            print(*args)
        except Exception:                          # noqa: BLE001
            pass


def main() -> int:
    # Zweitstart zuerst abfangen: laeuft die App schon, ist alles Weitere
    # (ffmpeg-Pruefung, Report anlegen, Port suchen) ueberfluessig -- dann
    # nur die vorhandene Oberflaeche wieder nach vorn holen.
    first_port = _default_first_port()
    running = _existing_instance(first_port)
    if running is not None:
        if os.environ.get("MP3QC_NO_BROWSER", "") != "1":
            url = f"http://127.0.0.1:{running}/report.html"
            from . import macapp
            if macapp.available():
                macapp._focus_or_open(url)
            else:
                media.open_url(url, media.browser_path())
        return 0

    if not ensure_tools():
        return 1

    cfg = cfgmod.load()
    # Beim ersten Start gibt es noch keinen Report; nach einem App-Update ist
    # der vorhandene aelter als die mitgelieferte Oberflaeche. Beides hier
    # abfangen -- 'report' von Hand aufrufen kann man im Bundle nicht.
    if report_mod.is_stale(cfg):
        conn = db_mod.connect(cfg)
        try:
            report_mod.build_all(conn, cfg)
        except Exception as exc:                   # noqa: BLE001
            dialog(f"Der Report konnte nicht erzeugt werden:\n\n{exc}")
            return 1
        finally:
            conn.close()

    port = _free_port(first_port)
    if port is None:
        dialog(f"Kein freier Port zwischen {first_port} und "
               f"{first_port + _PORT_TRIES}. Läuft die App bereits?")
        return 1

    from . import macapp
    from . import server as server_mod
    try:
        # MP3QC_NO_BROWSER=1 startet nur den Server — nützlich zum Testen
        open_browser = os.environ.get("MP3QC_NO_BROWSER", "") != "1"
        # Mit pyobjc laeuft der Server unter einer echten NSApplication:
        # erst dadurch funktionieren Cmd+Q, "Beenden" im Dock-Menue und das
        # Abmelden, ohne den Prozess abzuschiessen (siehe macapp.py).
        if macapp.available():
            return macapp.run(_Quiet(), port=port, open_browser=open_browser)
        return server_mod.serve(_Quiet(), port=port, open_browser=open_browser)
    except Exception as exc:                       # noqa: BLE001
        dialog(f"Unerwarteter Fehler:\n\n{exc}")
        return 1
