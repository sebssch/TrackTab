"""
App Nap abschalten und Ruhezustand verhindern (NSProcessInfo-Aktivitaeten).

Die gepackte App hat kein eigenes Fenster (die Oberflaeche liegt im Browser).
macOS haelt sie deshalb fast immer fuer "nicht sichtbar" und schickt sie in App
Nap: Prozessprioritaet 4, nur Effizienzkerne, gebuendelte Timer. An echtem
Material gemessen: eine LightGBM-Runde des Auto-Cue-Trainings 81 ms im Terminal,
1.812 ms in dieser Stufe -- 22-mal langsamer; das Laden der Trainingsdaten 2,8 s
statt rund 80 s. Betroffen ist alles, was die App rechnet (Scan, Stems, Analysen),
auch die Worker-Prozesse, die sie startet.

hold() meldet fuer die ganze Laufzeit eine vom Nutzer ausgeloeste Aktivitaet an
(macapp.run()); keep_awake() verhindert zusaetzlich den Ruhezustand bei Leerlauf,
solange eine lange Arbeit laeuft (Training). Beides ist ohne pyobjc (Terminal,
andere Systeme) wirkungslos -- dort gibt es kein App Nap.
"""
from __future__ import annotations

import contextlib
import threading

_lock = threading.Lock()
_held: list = []                 # Token festhalten, sonst endet die Aktivitaet mit dem GC


def _begin(reason: str, prevent_sleep: bool):
    try:
        import Foundation
    except Exception:                                    # noqa: BLE001 -- kein pyobjc: nichts zu tun
        return None
    opts = (Foundation.NSActivityUserInitiated if prevent_sleep
            else Foundation.NSActivityUserInitiatedAllowingIdleSystemSleep)
    try:
        return Foundation.NSProcessInfo.processInfo().beginActivityWithOptions_reason_(opts, reason)
    except Exception:                                    # noqa: BLE001
        return None


def _end(token) -> None:
    if token is None:
        return
    try:
        import Foundation
        Foundation.NSProcessInfo.processInfo().endActivity_(token)
    except Exception:                                    # noqa: BLE001
        pass


def hold(reason: str) -> None:
    """Fuer die restliche Laufzeit des Prozesses kein App Nap (Ruhezustand bleibt erlaubt)."""
    token = _begin(reason, prevent_sleep=False)
    if token is not None:
        with _lock:
            _held.append(token)


@contextlib.contextmanager
def keep_awake(reason: str):
    """Waehrend des Blocks weder App Nap noch Ruhezustand bei Leerlauf (Bildschirm
    darf ausgehen). Fuer Arbeit, die ohne Zutun des Nutzers lange laeuft."""
    token = _begin(reason, prevent_sleep=True)
    try:
        yield
    finally:
        _end(token)
