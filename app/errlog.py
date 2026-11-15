"""
Fehlerprotokoll der Anwendung: unerwartete Ausnahmen aus dem Server und dem
Report-Bau, mit Zeitpunkt und Traceback.

Anlass: die per Doppelklick gestartete App hat kein Terminal, stderr landet
dort nirgends. Eine Ausnahme in einem Endpunkt war damit unsichtbar -- der
Browser sah nur eine abgebrochene Verbindung.

Liegt als 'errors.log' im selben Ordner wie das Aenderungsprotokoll
(audit_log.py; in den Einstellungen ueber "Protokoll-Ordner" zu oeffnen) und
wird bei _MAX_BYTES einmal nach 'errors.log.1' weggeschoben, waechst also
nie unbegrenzt. Schreibt zusaetzlich nach stderr (Terminal-Betrieb).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

from . import audit_log

_NAME = "errors.log"
_MAX_BYTES = 1024 * 1024
_LOCK = threading.Lock()


def log(label: str, exc: BaseException | None = None) -> None:
    """Haengt einen Eintrag an. Darf selbst nie scheitern -- ein Fehler beim
    Protokollieren soll den eigentlichen Fehler nicht ueberdecken."""
    text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {label}"
    if exc is not None:
        text += f": {exc}\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    text += "\n"
    try:
        print(text, file=sys.stderr, end="")
    except Exception:                                  # noqa: BLE001
        pass
    try:
        path = audit_log.log_dir() / _NAME
        with _LOCK:
            try:
                if path.stat().st_size > _MAX_BYTES:
                    os.replace(path, path.with_name(_NAME + ".1"))
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)
    except Exception:                                  # noqa: BLE001
        pass
