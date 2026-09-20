"""
Taegliches Aenderungsprotokoll: was an Dateien geschrieben, neu kodiert,
verschoben oder in den Papierkorb gelegt wurde -- fuer Nachvollziehbarkeit
durch einen Menschen (oder eine KI) im Nachhinein, nicht fuer Debugging der
Anwendung selbst (dafuer gibt es keine eigene Log-Datei). Eine Zeile pro
Aktion (JSON Lines: ein JSON-Objekt je Zeile), eine Datei pro Kalendertag
unter logs_path. Wird nicht automatisch geloescht.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config as cfgmod

_NAME_FMT = "%Y-%m-%d.json"
_LOCK = threading.Lock()


def log_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or cfgmod.load()
    d = cfgmod.resolve(cfg.get("logs_path", "logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(action: str, path: str, detail: str = "", cfg: dict | None = None) -> None:
    """Haengt eine JSON-Zeile an die Log-Datei des heutigen Tages an.

    Ein Protokollierungsfehler (z.B. Ordner nicht schreibbar) darf die
    eigentliche, bereits erfolgreich abgeschlossene Aktion nicht nachtraeglich
    scheitern lassen -- deshalb wird ein OSError hier verschluckt.
    """
    cfg = cfg or cfgmod.load()
    entry = {"time": time.strftime("%H:%M:%S"), "action": action, "path": path}
    if detail:
        entry["detail"] = detail
    try:
        with _LOCK:
            with open(log_dir(cfg) / time.strftime(_NAME_FMT), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
