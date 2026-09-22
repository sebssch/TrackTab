"""
macOS-Huelle fuer die gepackte App.

Ohne sie ist der Prozess aus Sicht des Systems eine Anwendung ohne
Event-Loop: das Dock-Symbol erscheint, aber Cmd+Q, "Beenden" im Dock-Menue
und Abmelden/Neustart laufen ins Leere -- der Quit-Apple-Event wird nie
beantwortet, macOS bietet nach kurzer Wartezeit nur noch "Sofort beenden"
an. Der Server stirbt dann per SIGKILL, im Zweifel mitten in einem
Schreibvorgang an der Datenbank.

Deshalb laeuft hier eine echte NSApplication auf dem Hauptthread und der
Server in einem Hintergrund-Thread. applicationShouldTerminate_ haelt ihn
ueber server.request_stop() geordnet an, statt den Prozess abzuschiessen.

pyobjc ist bewusst optional: fehlt es, faellt desktop.py auf das nackte
server.serve() zurueck -- dann eben ohne Cmd+Q.
"""
from __future__ import annotations

import subprocess
import threading
import webbrowser
from pathlib import Path

from . import media
from . import server as server_mod

_QUIT_TIMEOUT = 20.0        # Sekunden Wartezeit auf den Server beim Beenden

# Bundle-IDs von Chromium-Ablegern: teilen sich dasselbe Scripting-Dictionary
# ("tabs of window", "active tab index") wie Google Chrome selbst.
_CHROMIUM_BUNDLE_IDS = {
    "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev",
    "com.google.Chrome.canary", "com.brave.Browser", "com.microsoft.edgemac",
    "com.vivaldi.Vivaldi", "org.chromium.Chromium",
}

_SAFARI_FOCUS_SCRIPT = '''
tell application "Safari"
    set targetURL to {url}
    set foundTab to missing value
    set foundWin to missing value
    repeat with w in windows
        repeat with t in tabs of w
            if (URL of t as string) starts with targetURL then
                set foundTab to t
                set foundWin to w
                exit repeat
            end if
        end repeat
        if foundTab is not missing value then exit repeat
    end repeat
    if foundTab is missing value then
        open location targetURL
    else
        set current tab of foundWin to foundTab
        set index of foundWin to 1
    end if
    activate
end tell
'''

_CHROMIUM_FOCUS_SCRIPT = '''
tell application {app}
    set targetURL to {url}
    set foundWin to missing value
    set foundIdx to 0
    repeat with w in windows
        set idx to 0
        repeat with t in tabs of w
            set idx to idx + 1
            if (URL of t as string) starts with targetURL then
                set foundWin to w
                set foundIdx to idx
                exit repeat
            end if
        end repeat
        if foundWin is not missing value then exit repeat
    end repeat
    if foundWin is missing value then
        open location targetURL
    else
        set active tab index of foundWin to foundIdx
        set index of foundWin to 1
    end if
    activate
end tell
'''


def _as_literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _default_browser() -> tuple[str, str] | None:
    """(Bundle-ID, App-Name) des Standardbrowsers, oder None wenn nicht
    ermittelbar (z.B. kein Handler fuer http:// registriert)."""
    import AppKit
    import Foundation
    ws = AppKit.NSWorkspace.sharedWorkspace()
    probe = Foundation.NSURL.URLWithString_("http://example.com")
    app_url = ws.URLForApplicationToOpenURL_(probe)
    if app_url is None:
        return None
    bundle = Foundation.NSBundle.bundleWithURL_(app_url)
    bundle_id = bundle.bundleIdentifier() if bundle else None
    if not bundle_id:
        return None
    name = app_url.path().rstrip("/").split("/")[-1]
    if name.endswith(".app"):
        name = name[:-4]
    return bundle_id, name


def _bundle_id_for_path(app_path: str) -> tuple[str, str] | None:
    """(Bundle-ID, App-Name) fuer einen konkreten .app-Pfad, oder None."""
    import Foundation
    url = Foundation.NSURL.fileURLWithPath_(app_path)
    bundle = Foundation.NSBundle.bundleWithURL_(url)
    bundle_id = bundle.bundleIdentifier() if bundle else None
    if not bundle_id:
        return None
    name = Path(app_path).stem
    return bundle_id, name


def _focus_or_open(url: str) -> None:
    """
    Bringt einen bereits offenen Tab mit dieser Adresse nach vorn statt
    jedes Mal einen neuen zu oeffnen -- ein Klick aufs Dock-Symbol soll sich
    wie bei einer App mit eigenem Fenster verhalten, obwohl die Oberflaeche
    im Browser liegt.

    Nur fuer Safari und Chromium-Ableger per AppleScript moeglich (deren
    Scripting-Dictionary kennt "tabs of window"); bei anderen Browsern
    (z.B. Firefox) bleibt es beim schlichten neuen Tab. Ist in den
    Einstellungen ein Browser hinterlegt, gilt dieser statt des
    Systemstandards -- fuer diesen wird ebenfalls per Bundle-ID geprueft,
    ob sich ein Tab fokussieren laesst.
    """
    configured = media.browser_path()
    info = None
    if configured:
        try:
            info = _bundle_id_for_path(configured)
        except Exception:                                 # noqa: BLE001
            info = None
        if info is None:
            media.open_url(url, configured)
            return
    else:
        try:
            info = _default_browser()
        except Exception:                                 # noqa: BLE001
            pass
        if info is None:
            webbrowser.open(url)
            return
    bundle_id, app_name = info
    if bundle_id == "com.apple.Safari":
        script = _SAFARI_FOCUS_SCRIPT.format(url=_as_literal(url))
    elif bundle_id in _CHROMIUM_BUNDLE_IDS:
        script = _CHROMIUM_FOCUS_SCRIPT.format(
            app=_as_literal(app_name), url=_as_literal(url))
    else:
        media.open_url(url, configured) if configured else webbrowser.open(url)
        return
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, timeout=10)
        if result.returncode != 0:
            media.open_url(url, configured) if configured else webbrowser.open(url)
    except (OSError, subprocess.SubprocessError):
        media.open_url(url, configured) if configured else webbrowser.open(url)


def available() -> bool:
    """Ob pyobjc vorhanden ist. Import statt Paketabfrage: nur ein
    tatsaechlich ladbares AppKit hilft uns hier weiter."""
    try:
        import AppKit                                    # noqa: F401
        from PyObjCTools import AppHelper                # noqa: F401
    except Exception:                                    # noqa: BLE001
        return False
    return True


def run(console, port: int, open_browser: bool = True) -> int:
    """
    Server im Hintergrund-Thread, NSApplication auf dem Hauptthread.

    Rueckgabe ist der Rueckgabewert von server.serve() -- die Huelle selbst
    kennt kein eigenes Scheitern ausser einem fehlenden pyobjc, und das
    faengt available() ab.
    """
    import AppKit
    from PyObjCTools import AppHelper

    url = f"http://127.0.0.1:{port}/report.html"
    # Ein Dict statt Variablen: die Delegate-Methoden sind Closures und
    # koennten aeussere Namen sonst nur lesen, nicht setzen.
    state: dict = {"thread": None, "code": 0, "delegate": None}

    def _worker() -> None:
        try:
            state["code"] = server_mod.serve(console, port=port,
                                             open_browser=open_browser)
        except Exception as exc:                         # noqa: BLE001
            state["code"] = 1
            console.print(f"Serverfehler: {exc}")
        # Server ist aus -- entweder ueber den Beenden-Knopf der Oberflaeche
        # oder weil er gar nicht erst hochkam. So oder so hat die App nichts
        # mehr zu tun. terminate_ gehoert auf den Hauptthread.
        AppHelper.callAfter(AppKit.NSApp().terminate_, None)

    def _confirm_scan_quit() -> bool:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Es läuft noch ein Scan")
        alert.setInformativeText_(
            "Beim Beenden bricht der Scan ab. Bereits gemessene Dateien "
            "bleiben in der Datenbank, der Rest wird beim nächsten Scan "
            "nachgeholt.")
        alert.addButtonWithTitle_("Trotzdem beenden")
        alert.addButtonWithTitle_("Abbrechen")
        AppKit.NSApp().activateIgnoringOtherApps_(True)
        return alert.runModal() == AppKit.NSAlertFirstButtonReturn

    class AQCAppDelegate(AppKit.NSObject):
        def applicationDidFinishLaunching_(self, _note):
            # Erst wenn die App steht, sonst haengt ein Startfehler des
            # Servers vor einer noch gar nicht laufenden Event-Loop.
            thread = threading.Thread(target=_worker, daemon=True)
            state["thread"] = thread
            thread.start()

        def applicationShouldTerminate_(self, _sender):
            thread = state["thread"]
            if thread is None or not thread.is_alive():
                return AppKit.NSTerminateNow
            if server_mod.SCAN.is_running():
                if not _confirm_scan_quit():
                    return AppKit.NSTerminateCancel
                server_mod.SCAN.cancel()
            server_mod.request_stop()
            # NSTerminateLater waere die feine Art, verlangt aber einen
            # zweiten Rueckweg ueber replyToApplicationShouldTerminate_.
            # Der Server ist nach dem shutdown() in Sekundenbruchteilen
            # unten; das kurze Blockieren hier ist die einfachere Loesung.
            thread.join(_QUIT_TIMEOUT)
            return AppKit.NSTerminateNow

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
            # Klick aufs Dock-Symbol. Die Oberflaeche liegt im Browser, nicht
            # in einem eigenen Fenster -- einen vorhandenen Tab nach vorn
            # holen statt jedes Mal einen neuen zu oeffnen.
            _focus_or_open(url)
            return True

        def application_openURLs_(self, _app, _urls):
            # Eigenes "tracktab://"-Schema (siehe tracktab.spec), ausgeloest
            # vom Start-Knopf auf der Offline-Seite des Service Workers
            # (app/webui/sw.js), wenn der Server nicht erreichbar war. War
            # die App noch nicht gestartet, hat macOS applicationDidFinishLaunching_
            # bereits angestossen -- der Server kommt gleich hoch und oeffnet
            # per open_browser selbst einen Tab. Lief die App schon (Server
            # aus unbekanntem Grund haengengeblieben), bleibt sonst nur die
            # unsichtbare Aktivierung ohne Tab -- deshalb hier zusaetzlich
            # denselben Weg wie beim Dock-Klick nehmen.
            _focus_or_open(url)

        def openReport_(self, _sender):
            _focus_or_open(url)

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    delegate = AQCAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    # Referenz festhalten: setDelegate_ haelt das Objekt nicht am Leben
    # (schwache Referenz). Ohne das raeumt der Python-GC den Delegate ab und
    # der erste Cmd+Q trifft ins Leere.
    state["delegate"] = delegate

    _build_menu(AppKit, app)
    AppHelper.runEventLoop(installInterrupt=True)
    return int(state["code"])


def _build_menu(AppKit, app) -> None:                    # noqa: N803
    """
    Menueleiste. Ohne sie gibt es kein Cmd+Q -- der Kurzbefehl haengt am
    Menueeintrag, nicht an der Anwendung.
    """
    bar = AppKit.NSMenu.alloc().init()
    app_item = AppKit.NSMenuItem.alloc().init()
    bar.addItem_(app_item)
    app.setMainMenu_(bar)

    menu = AppKit.NSMenu.alloc().init()
    menu.addItemWithTitle_action_keyEquivalent_(
        "Report öffnen", "openReport:", "r")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    menu.addItemWithTitle_action_keyEquivalent_(
        "TrackTab ausblenden", "hide:", "h")
    menu.addItemWithTitle_action_keyEquivalent_(
        "Andere ausblenden", "hideOtherApplications:", "")
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    menu.addItemWithTitle_action_keyEquivalent_(
        "TrackTab beenden", "terminate:", "q")
    app_item.setSubmenu_(menu)
