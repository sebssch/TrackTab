"""
Lokaler Server fuer die Oberflaeche.

Der Report ist eine statische Datei und kann von sich aus weder speichern
noch Audio ausliefern. Dieser Server macht daraus eine Anwendung: er liefert
die Seite, nimmt Entscheidungen entgegen, streamt Musik zum Gegenhoeren und
prueft per Drag & Drop abgelegte Dateien.

Gebunden wird ausschliesslich auf 127.0.0.1 — nichts davon ist im Netz
erreichbar. Das allein reicht aber nicht: aus dem Browser heraus kann JEDE
geoeffnete Seite Anfragen an 127.0.0.1 schicken (CSRF), und ein auf 127.0.0.1
umgebogener fremder Name waere aus Browsersicht sogar derselbe Origin und
duerfte damit auch die Antworten lesen (DNS-Rebinding). Deshalb prueft
_same_origin() bei jeder Anfrage Host und Origin.

Zum Dateizugriff: Endpunkte, die auf eine Audiodatei zugreifen, nehmen zwar
einen Pfad entgegen, geben ihn aber nur frei, wenn er in der Datenbank steht.
Damit ist der Zugriff auf die analysierte Bibliothek begrenzt; beliebige
Systempfade sind ausgeschlossen. Geschrieben wird an eine Audiodatei nur an
einer einzigen Stelle: beim ausdruecklich angestossenen Neukodieren der
Bitrate — und auch dort wandert das Original erst in den Papierkorb.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import os
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from . import audit_log
from . import backup as backup_mod
from . import config as cfgmod
from . import classify as classify_mod
from . import convert as convert_mod
from . import coverfill
from . import db as db_mod
from . import jobs
from . import lookup as lookup_mod
from . import media
from . import pwa as pwa_mod
from . import rekordbox as rekordbox_mod
from . import report as report_mod
from . import rewrite as rewrite_mod
from . import scanner
from . import settings as settings_mod
from . import stats as stats_mod
from . import taganomaly as taganomaly_mod
from . import tags as tags_mod

_AUDIO_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".wav": "audio/wav", ".flac": "audio/flac",
    ".aif": "audio/aiff", ".aiff": "audio/aiff", ".ogg": "audio/ogg",
}

SCAN = jobs.ScanJob()

# Der laufende Server, damit ihn /api/quit und die App-Huelle (macapp.py) von
# aussen anhalten koennen. serve() setzt ihn, request_stop() haelt ihn an.
_HTTPD: ThreadingHTTPServer | None = None

# Namen, unter denen der eigene Server angesprochen werden darf (siehe
# _Handler._same_origin()). Gebunden ist nur 127.0.0.1; ::1 steht der
# Vollstaendigkeit halber dabei, falls das Binden spaeter erweitert wird.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _split_hostport(raw: str | None, default_port: int) -> tuple[str, int]:
    """Zerlegt eine Autoritaetsangabe ("127.0.0.1:8756", "[::1]:8756",
    "localhost") in Name und Port. Fuer den Host- und den Origin-Abgleich in
    _Handler._same_origin().

    Ohne Portangabe gilt default_port (bei http also 80). Ein ungueltiger
    Wert liefert ("", 0) und faellt damit garantiert durch die anschliessende
    Pruefung -- nie ein Ergebnis, das versehentlich passt.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", 0
    if raw.startswith("["):                 # IPv6 in eckigen Klammern
        end = raw.find("]")
        if end < 0:
            return "", 0
        host, rest = raw[1:end], raw[end + 1:]
        port = rest[1:] if rest.startswith(":") else ""
        if rest and not rest.startswith(":"):
            return "", 0
    elif raw.count(":") == 1:
        host, _, port = raw.partition(":")
    else:                                   # nackte IPv6 oder gar kein Port
        host, port = raw, ""
    if not port:
        return host.lower(), default_port
    if not port.isdigit():
        return "", 0
    return host.lower(), int(port)

# Inhaltsrichtlinie fuer jede Antwort, siehe _Handler._security_headers().
# manifest-src/worker-src explizit noetig fuer /manifest.json bzw. /sw.js
# (PWA-Installierbarkeit, siehe pwa.py) -- ohne eigene Angabe fallen beide auf
# default-src 'none' zurueck und der Browser verweigert Manifest-Fetch bzw.
# Service-Worker-Registrierung stillschweigend.
_CSP = ("default-src 'none'; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

# Erlaubte Cover-Bildtypen. Der MIME-Typ eines eingebetteten Covers ist freier
# Text in der Datei (ID3-APIC, FLAC-Picture) -- ungeprueft ginge er als
# Content-Type-Kopfzeile wieder hinaus. send_header() laesst Zeilenumbrueche
# durch (Antwort-Aufspaltung), und ein "text/html" waere ausgeliefertes HTML
# aus dem Origin der Oberflaeche.
# image/svg+xml gehoert bewusst NICHT dazu: SVG kann Skript enthalten und
# wuerde beim direkten Aufruf der Cover-URL im Origin der Oberflaeche laufen.
_COVER_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp",
                          "image/avif", "image/bmp"})


def _sniff_image_mime(data: bytes) -> str:
    """Bildtyp aus den Kopfbytes, "" wenn es keiner der erlaubten ist.

    Der Content-Type einer Cover-Anfrage ist eine reine Behauptung des
    Aufrufers. Ohne diese Gegenprobe landet beliebiger Inhalt als "Bild" in
    der Musikdatei -- und wird spaeter von Pillow gelesen
    (taganomaly.detect()), einem Bildparser mit regelmaessigen
    Sicherheitsluecken. Der Rueckweg ueber /api/cover ist zwar durch
    _COVER_MIMES und nosniff abgesichert, der Parser dahinter aber nicht.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    # ISO-BMFF: Laenge, "ftyp", dann die Marke. avis ist die Sequenzvariante.
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return ""

MAX_UPLOAD = 400 * 1024 * 1024      # Obergrenze fuer per Drag & Drop gepruefte Dateien
_CHUNK = 256 * 1024

# Ab welcher Groesse eine Antwort gepackt wird und welche Inhaltstypen davon
# profitieren. Der gebackene Report sind rund 10 MB Text, der auf etwa ein
# Fuenftel zusammenfaellt; die Markierungslisten (/api/music-added,
# /api/rekordbox) sind Pfadlisten und packen noch besser. Kleine Antworten
# bleiben ungepackt -- darunter kostet der Kopfzeilen-Aufwand mehr, als die
# Ersparnis bringt.
_GZIP_MIN_BYTES = 32 * 1024
_GZIP_TYPES = ("text/", "application/json", "application/javascript",
               "image/svg+xml", "application/xml", "application/manifest+json")
# Stufe 6 ist die zlib-Vorgabe. Am Report gemessen: Stufe 6 braucht 189 ms und
# liefert 20 %, Stufe 1 ist deutlich schneller bei kaum schlechterer Quote --
# und diese Antworten entstehen waehrend der Nutzer wartet, nicht im Voraus.
_GZIP_LEVEL = 1


def _wants_gzip(header: str | None) -> bool:
    """Akzeptiert die Gegenseite gzip?

    Wertet nur aus, was hier zaehlt: kommt gzip (oder *) vor, und ist es
    nicht mit q=0 ausdruecklich abgelehnt. Eine vollstaendige Rangfolge ueber
    alle Verfahren waere fuer einen Server, der den eigenen Browser bedient,
    Aufwand ohne Ertrag -- ein falsch gelesenes q=0 dagegen waere eine
    Antwort, die der Aufrufer nicht auspacken kann.
    """
    for teil in (header or "").lower().split(","):
        name, _, rest = teil.strip().partition(";")
        if name.strip() not in ("gzip", "*"):
            continue
        for param in rest.split(";"):
            schluessel, _, wert = param.partition("=")
            if schluessel.strip() != "q":
                continue
            try:
                return float(wert.strip()) > 0
            except ValueError:
                return False
        return True
    return False


def _gzip_ok(ctype: str, size: int) -> bool:
    return size >= _GZIP_MIN_BYTES and ctype.split(";")[0].strip().startswith(_GZIP_TYPES)


# Der gebackene Report, einmal gepackt. Er aendert sich nur beim Neubau, waere
# aber sonst bei JEDEM Seitenaufruf neu zu packen -- rund 10 MB, die ausserdem
# erst von der Platte gelesen werden muessten. Schluessel ist der Dateizustand
# (mtime + Groesse), damit auch ein Neubau bemerkt wird, der an diesem Prozess
# vorbeilaeuft (./run.command report in einem zweiten Fenster).
_report_gzip: tuple[tuple[int, int], bytes] | None = None
_report_gzip_lock = threading.Lock()

# Wie lange eine einmal gepruefte Pfad-Freigabe gilt (siehe
# _Handler._authorize_path). Kurz genug, dass eine inzwischen verschwundene
# Datei nicht dauerhaft freigegeben bleibt; lang genug, dass eine Lawine von
# Range-Anfragen fuer denselben Track nicht jedes Mal die Datenbank anfasst.
_AUTH_TTL_S = 3.0


# Wie lange eine ueber die native Dateiauswahl freigegebene Datei ohne jede
# Benutzung freigegeben bleibt (siehe _PickedPaths). Grosszuegig, weil die
# Einzelpruefungs-Zeilen in der Oberflaeche stehen bleiben, bis der Nutzer sie
# raeumt -- wer morgens Dateien ablegt und nachmittags importiert, soll nicht
# vor einem "Unbekannter Pfad" stehen. Jede Benutzung setzt die Frist neu.
_PICKED_IDLE_S = 12 * 3600
_PICKED_LIMIT = 2000


class _PickedPaths:
    """Pfade aus /api/open-file-pick, die _authorize_path zusaetzlich zur
    Datenbank freigibt. Prozesslokal.

    Frueher ein einfaches set() -- das wuchs unbegrenzt und gab jede je
    geoeffnete Datei fuer die gesamte Laufzeit des Prozesses frei. Bei einer
    Desktop-App, die tagelang laeuft, sammelt sich das an: jeder Eintrag ist
    eine Datei, die ueber /api/audio gelesen werden darf.

    Die Frist laeuft ab der letzten Benutzung, nicht ab dem Hinzufuegen
    (__contains__ setzt sie neu) -- eine Zeile, mit der noch gearbeitet wird,
    verfaellt also nie, eine vergessene nach _PICKED_IDLE_S. Beim Erreichen
    des Deckels fliegen die aeltesten raus statt alle: anders als bei
    _AuthCache ist ein Eintrag hier keine nachschlagbare Zwischenspeicherung,
    sondern die einzige Quelle der Freigabe -- ihn zu verlieren macht die
    zugehoerige Zeile unbedienbar.
    """

    def __init__(self, idle_s: float = _PICKED_IDLE_S,
                 limit: int = _PICKED_LIMIT) -> None:
        self._data: dict[str, float] = {}
        self._idle_s = idle_s
        self._limit = limit
        self._lock = threading.Lock()

    def __contains__(self, path: str) -> bool:
        now = time.monotonic()
        with self._lock:
            seen = self._data.get(path)
            if seen is None:
                return False
            if now - seen > self._idle_s:
                del self._data[path]
                return False
            self._data[path] = now        # Frist laeuft ab der letzten Benutzung
            return True

    def add(self, path: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._data[path] = now
            if len(self._data) <= self._limit:
                return
            for p, seen in list(self._data.items()):
                if now - seen > self._idle_s:
                    del self._data[p]
            # Reicht das Aufraeumen nicht, die aeltesten opfern. dict haelt
            # die Einfuegereihenfolge; neu gesetzte Zeitstempel schreiben den
            # Eintrag nicht um, deshalb wird nach Zeit sortiert.
            if len(self._data) > self._limit:
                for p, _ in sorted(self._data.items(), key=lambda kv: kv[1]
                                   )[:len(self._data) - self._limit]:
                    del self._data[p]

    def discard(self, path: str) -> None:
        with self._lock:
            self._data.pop(path, None)


class _AuthCache:
    """Pfad -> (Zeitpunkt, Freigabe ja/nein). Prozesslokal.

    Klein gehalten und ohne LRU: darin landen nur die Pfade, die gerade
    gespielt oder bearbeitet werden. Ist der Deckel erreicht, wird komplett
    geleert -- das kostet hoechstens ein paar Datenbankabfragen mehr.
    """

    def __init__(self, limit: int = 256) -> None:
        self._data: dict[str, tuple[float, bool]] = {}
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, path: str) -> bool | None:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(path)
            if hit is None:
                return None
            if now - hit[0] > _AUTH_TTL_S:
                self._data.pop(path, None)
                return None
            return hit[1]

    def put(self, path: str, result: bool) -> None:
        with self._lock:
            if len(self._data) >= self._limit:
                self._data.clear()
            self._data[path] = (time.monotonic(), result)

    def drop(self, path: str) -> None:
        with self._lock:
            self._data.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_auth_cache = _AuthCache()


class _ReportBuilder:
    """
    Baeckt data/report.html gebuendelt in EINEM Hintergrund-Thread.

    Warum ueberhaupt: der gebackene Report ist eine einzige Datei mit allen
    Zeilen als JSON darin (bei 10.800 Tracks rund 10 MB). Ein Neubau kostet
    an echtem Material rund 600 ms -- gemessen: 105 ms db.fetch, 230 ms
    rows_to_payload, 87 ms JSON-Escaping, der Rest Schreiben von HTML, CSV
    und M3U. Bisher lief genau das synchron im Anfrage-Thread, meist noch
    innerhalb von `self.lock`. Eine Sammelaktion ueber 50 Tracks (der
    Tags-Dialog schickt einen Aufruf JE Datei) hat damit 50 Neubauten
    ausgeloest: rund 30 Sekunden, in denen jeder andere Endpunkt am Lock
    haengt, und rund 500 MB geschriebene Bytes fuer ein Ergebnis, das nur
    beim naechsten Neuladen der Seite ueberhaupt gelesen wird.

    Statt dessen: `mark_dirty()` merkt sich nur, DASS neu gebacken werden
    muss, und weckt den Worker. Der wartet ein kurzes Sammelfenster ab
    (_DEBOUNCE_S), damit eine Serie von Aenderungen zu einem einzigen Bau
    zusammenfaellt.

    Aktualitaet bleibt garantiert, weil `flush()` vor dem Ausliefern der
    Seite laeuft (siehe _Handler._serve_report()): wer den Report wirklich
    liest, bekommt nie einen veralteten Stand -- er wartet hoechstens auf
    den einen Bau, der ohnehin faellig war. Die laufende Oberflaeche
    braucht die Datei nicht, sie zieht Aenderungen ueber die API nach.

    Reihenfolge-Sicherheit ueber zwei Zaehler statt eines Bool: `_dirty_seq`
    zaehlt jede Markierung hoch, `_built_seq` haelt fest, welcher Stand
    fertig gebaut ist. Eine Markierung, die WAEHREND eines laufenden Baus
    hereinkommt, hebt damit `_dirty_seq` ueber `_built_seq` -- der naechste
    Durchlauf holt sie nach, statt sie zu verschlucken.
    """

    _DEBOUNCE_S = 0.35          # Sammelfenster fuer eine Serie von Aenderungen
    _RETRY_S = 3.0              # Backoff nach einem Fehlschlag
    _FLUSH_TIMEOUT_S = 30.0     # Obergrenze fuers Warten beim Ausliefern

    def __init__(self) -> None:
        self._wake = threading.Event()
        self._build_lock = threading.Lock()     # genau EIN Bau gleichzeitig
        self._state_lock = threading.Lock()
        self._dirty_seq = 0
        self._built_seq = 0
        self._cfg: dict | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.last_error: str | None = None

    # ── Anmelden ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="report-builder",
                                        daemon=True)
        self._thread.start()

    def mark_dirty(self, cfg: dict) -> None:
        """Vormerken und den Worker wecken. Kehrt sofort zurueck."""
        with self._state_lock:
            self._dirty_seq += 1
            self._cfg = cfg
        self._wake.set()

    def is_pending(self) -> bool:
        with self._state_lock:
            return self._dirty_seq > self._built_seq

    # ── Ausfuehren ──────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stopping:
            self._wake.wait()
            if self._stopping:
                return
            # Sammelfenster: erst schlafen, dann bauen. Alles, was in dieser
            # Zeit noch hereinkommt, faellt in denselben Bau.
            time.sleep(self._DEBOUNCE_S)
            self._wake.clear()
            try:
                self._build_once()
            except Exception as exc:                   # noqa: BLE001
                # Nicht verschlucken: _built_seq bleibt zurueck, der naechste
                # Durchlauf versucht es erneut. Ohne das Wecken bliebe der
                # Report bis zur naechsten Aenderung veraltet.
                self.last_error = str(exc)
                self._log_error("Report-Neubau fehlgeschlagen", exc)
                time.sleep(self._RETRY_S)
                self._wake.set()

    def _build_once(self, timeout: float = -1) -> bool:
        """
        Baut genau dann, wenn etwas offen ist. Serialisiert ueber
        _build_lock -- Worker und flush() koennen sich nie ueberholen.

        timeout gilt fuer das WARTEN auf einen bereits laufenden Bau (-1 =
        unbegrenzt, so wartet der Worker). Liefert False, wenn in dieser
        Zeit kein Zugriff zu bekommen war.
        """
        if not self._build_lock.acquire(timeout=timeout):
            return False
        try:
            with self._state_lock:
                seq = self._dirty_seq
                cfg = self._cfg
                if seq <= self._built_seq:
                    return True                        # jemand war schneller
            cfg = cfg or cfgmod.load()
            conn = db_mod.connect(cfg)
            try:
                report_mod.build_all(conn, cfg)
            finally:
                conn.close()
            with self._state_lock:
                # Nur bis zum Stand VOR dem Bau hochsetzen: was waehrend des
                # Baus markiert wurde, war im db.fetch() nicht mehr drin und
                # bleibt damit fuer den naechsten Durchlauf offen.
                self._built_seq = max(self._built_seq, seq)
            self.last_error = None
            return True
        finally:
            self._build_lock.release()

    def flush(self, timeout: float | None = None) -> bool:
        """
        Einen offenen Neubau JETZT erledigen. Aufgerufen vor dem Ausliefern
        der Seite und beim Beenden.

        Liefert False, wenn nichts gebaut werden konnte (Fehler oder
        Zeitueberschreitung) -- der Aufrufer liefert dann den vorhandenen
        Stand aus, statt die Seite ganz zu verweigern.
        """
        if not self.is_pending():
            return True
        limit = self._FLUSH_TIMEOUT_S if timeout is None else timeout
        try:
            return self._build_once(timeout=limit)
        except Exception as exc:                       # noqa: BLE001
            self.last_error = str(exc)
            self._log_error("Report-Neubau beim Ausliefern fehlgeschlagen", exc)
            return False

    def stop(self) -> None:
        """Beim Herunterfahren: offenen Stand noch wegschreiben, damit der
        naechste Start nicht mit einem veralteten Report hochkommt."""
        thread, self._thread = self._thread, None
        try:
            self.flush(timeout=self._FLUSH_TIMEOUT_S)
        finally:
            self._stopping = True
            self._wake.set()
            if thread is not None:
                thread.join(timeout=2.0)

    @staticmethod
    def _log_error(label: str, exc: Exception) -> None:
        import sys
        import traceback
        print(f"[report] {label}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


REPORT = _ReportBuilder()

# Gleichzeitige Anfragen fuer dieselbe Huellkurve teilen sich EINEN
# ffmpeg-Lauf. Vorher startete jede gleichzeitig aufgeklappte Zeile ihren
# eigenen Unterprozess, der die Datei komplett nach f32 dekodiert (bei 8 kHz
# mono rund 10 MB je 5-Minuten-Track, vollstaendig im Speicher) -- beim
# Aufklappen mehrerer Zeilen kurz hintereinander also mehrfach dasselbe.
# Der dauerhafte Speicher bleibt der DB-Cache (db.waveform_put); _wave_result
# haelt das Ergebnis nur, bis die Wartenden es abgeholt haben.
_wave_inflight: dict[str, threading.Event] = {}
_wave_result: dict[str, list] = {}
_wave_lock = threading.Lock()


def _waveform_shared(path: str, mtime: float) -> list:
    with _wave_lock:
        waiting = _wave_inflight.get(path)
        owner = waiting is None
        if owner:
            waiting = threading.Event()
            _wave_inflight[path] = waiting

    if not owner:
        waiting.wait(timeout=300)
        with _wave_lock:
            result = _wave_result.get(path)
        if result is not None:
            return result
        raise RuntimeError("Berechnung im Nebenlauf fehlgeschlagen")

    try:
        peaks = media.waveform_peaks(path)
        conn = db_mod.connect(cfgmod.load())
        try:
            db_mod.waveform_put(conn, path, mtime, peaks)
        finally:
            conn.close()
        with _wave_lock:
            _wave_result[path] = peaks
        return peaks
    finally:
        with _wave_lock:
            _wave_inflight.pop(path, None)
        waiting.set()
        # Kurz stehen lassen, damit die Wartenden es noch abholen -- danach
        # ist der DB-Cache zustaendig.
        threading.Timer(5.0, lambda: _wave_result.pop(path, None)).start()


def request_stop(delay: float = 0.0) -> bool:
    """
    Beendet serve_forever() von aussen: Beenden-Knopf, Menue, Cmd+Q.

    Immer in einem eigenen Thread, nie direkt: shutdown() wartet auf das Ende
    genau der Schleife, die den aufrufenden Request bedient -- aus einem
    Handler heraus wuerde es sich selbst blockieren. Das kurze delay gibt der
    Antwort Zeit, den Browser noch zu erreichen.

    Liefert False, wenn gar kein Server laeuft.
    """
    httpd = _HTTPD
    if httpd is None:
        return False

    def _stop() -> None:
        if delay:
            time.sleep(delay)
        httpd.shutdown()

    threading.Thread(target=_stop, daemon=True).start()
    return True


def is_running() -> bool:
    """Ob serve() gerade eine Schleife dreht -- fuer die App-Huelle."""
    return _HTTPD is not None


class _Handler(BaseHTTPRequestHandler):
    report_path: Path
    lock = threading.Lock()
    # Nur fuer Schreibzugriffe auf Rekordbox' master.db. Frueher uebernahm das
    # 'lock' oben mit -- und legte damit die ganze Oberflaeche still, solange
    # pyrekordbox die SQLCipher-Datei sicherte und beschrieb (Sekunden, inkl.
    # rohem copyfile der master.db). Ein Pfad (die Pfadkorrektur nach einem
    # Relink) lief ausserdem schon bisher ganz ohne Lock, zwei Rekordbox-
    # Schreibzugriffe konnten sich also ueberholen.
    #
    # REIHENFOLGE-REGEL: Wer 'lock' haelt, darf '_rekordbox_lock' zusaetzlich
    # nehmen -- NIE umgekehrt. Sonst warten zwei Anfragen ueber Kreuz
    # aufeinander. Wer beide braucht und 'lock' nicht schon haelt, nimmt sie
    # deshalb nacheinander, nicht ineinander (siehe
    # _post_rekordbox_add_playlist).
    _rekordbox_lock = threading.Lock()
    protocol_version = "HTTP/1.1"
    # Pfade aus /api/open-file-pick -- landen bewusst nicht in der DB (siehe
    # dort), duerfen aber trotzdem editiert werden (siehe _authorize_path).
    # Prozesslokal, kein Neustart-Ueberlebender; Freigabe verfaellt nach
    # Untaetigkeit (siehe _PickedPaths).
    _unscanned_paths = _PickedPaths()
    # Negativer Cache fuer _try_relocate(): verhindert, dass mehrere Anfragen
    # fuer denselben (weiterhin) fehlenden Pfad je einen vollen
    # Bibliotheks-Scan ausloesen. Prozesslokal, kein Neustart-Ueberlebender.
    _relocate_negative_cache: dict = {}
    _RELOCATE_RETRY_S = 30.0

    def log_message(self, fmt, *args):        # noqa: A003
        pass

    # ── Antwort-Helfer ────────────────────────────────────────────────────
    def _security_headers(self) -> None:
        """
        Grundhaertung fuer jede Antwort -- auch fuer die von Hand gebauten
        (Audio-Streaming, ZIP-Buendel), deshalb eine eigene Methode statt
        ein paar Zeilen in _send().

        nosniff: der MIME-Typ eines Covers steht als freier Text in der
        Audiodatei (siehe _get_cover()); der Browser soll ihn keinesfalls
        selbst uminterpretieren.
        DENY/frame-ancestors: die Oberflaeche hat Knoepfe, die Dateien in den
        Papierkorb legen -- in eine fremde Seite eingebettet waeren die per
        Clickjacking bedienbar.
        CSP: die Seite laedt nichts aus dem Netz nach (CSS, JS, Icons und
        Uebersetzungen sind eingebettet, siehe report._template()), also darf
        sie es auch nicht duerfen. 'unsafe-inline' bleibt noetig, weil genau
        diese Einbettung inline ist. Ausnahme img-src https: -- die
        Cover-Vorschauen der Online-Suche (iTunes/Deezer) kommen von dort.
        """
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None,
              gzipped: bool = False) -> None:
        """Antwort mit den Standardkopfzeilen.

        'gzipped' heisst: body ist BEREITS gepackt (der Aufrufer hat das
        Ergebnis vorliegen, siehe _serve_report) -- dann wird hier nur noch
        die Kopfzeile gesetzt, nicht erneut gepackt.
        """
        extra = dict(extra or {})
        if gzipped or (_wants_gzip(self.headers.get("Accept-Encoding"))
                       and "Content-Encoding" not in extra
                       and _gzip_ok(ctype, len(body))):
            if not gzipped:
                body = gzip.compress(body, _GZIP_LEVEL)
            extra.setdefault("Content-Encoding", "gzip")
        # Vary immer setzen, auch bei ungepackter Antwort: ob gepackt wird,
        # haengt an der Anfragekopfzeile, und ein Zwischenspeicher darf eine
        # gepackte Antwort nicht an einen Client ohne gzip weiterreichen.
        extra.setdefault("Vary", "Accept-Encoding")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Vorgabe ist 'no-store'; ein eigener Cache-Control-Wert in extra
        # ERSETZT ihn, statt zusaetzlich gesendet zu werden. Zwei Header
        # gleichen Namens fasst der Browser zu einer Liste zusammen, in der
        # 'no-store' immer gewinnt -- das eigene max-age blieb dadurch
        # wirkungslos (betraf bisher schon /api/appicon).
        if not any(k.lower() == "cache-control" for k in extra):
            self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, message: str, code: int = 400) -> None:
        self._json({"ok": False, "error": message}, code)

    def _require_music(self, cfg: dict) -> bool:
        """True wenn Music.app-Integration eingeschaltet ist (cfg["external_music"]
        gesetzt) -- sonst Fehlerantwort und False. Zweite Sicherung hinter der
        Client-Seite (Baum/Knoepfe bleiben dort schon ausgeblendet, siehe
        app.js:MUSIC_NAME) fuer jeden Endpunkt, der sonst still AppleScript an
        Music.app schickt, obwohl der Nutzer die Integration abgeschaltet hat.
        Fuer best-effort-Abgleiche (Papierkorb, Cover, Tag-Umbenennung,
        Konvertieren), die nicht scheitern duerfen, wenn nur der
        Music.app-Teil misslingt, wird cfg["external_music"] direkt an der
        jeweiligen Stelle geprueft statt ueber diese Methode (die antwortet
        mit einem Fehler, was fuer einen Nebeneffekt falsch waere).
        """
        if cfg.get("external_music"):
            return True
        self._fail("Keine Music App in den Einstellungen ausgewählt")
        return False

    def _rekordbox_available(self, cfg: dict) -> bool:
        """True wenn Rekordbox eingestellt ODER automatisch gefunden ist --
        dieselbe Bedingung wie app.js:REKORDBOX_NAME, die dort schon Baum und
        Knoepfe ausblendet. Anders als _require_music() liefert das hier
        keine Fehlerantwort, nur ein bool: die Live-Checks, die diese Methode
        schuetzt (Rekordbox-Praesenz bei "neu analysieren"), sind
        Nebeneffekte eines Erfolgs, kein eigener Endpunkt -- ohne
        installiertes/eingestelltes Rekordbox waeren sie ohnehin nur ein
        Fehlschlag nach Zeitaufwand fuer die (nie vorhandene) master.db,
        kein Fehler, den der Aufrufer melden muesste.
        """
        return bool(cfg.get("external_rekordbox") or media.find_rekordbox())

    # ── Gemeinsame Pruefungen ─────────────────────────────────────────────
    def _query(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _is_own_address(self, host: str, port: int) -> bool:
        """Zeigt Name+Port auf genau diesen Server?

        Der Port gehoert zwingend dazu. Nur den Namen zu pruefen hiesse, jeder
        anderen Seite auf 127.0.0.1 zu vertrauen -- ein Entwicklungsserver auf
        Port 3000, eine Electron-App mit Web-Ansicht, eine beliebige lokal
        ausgelieferte Seite. Alle haetten damit denselben Rang wie die eigene
        Oberflaeche und koennten schreibende Endpunkte bedienen (/api/trash,
        /api/rewrite, /api/settings). Lesen bliebe ihnen zwar verwehrt (es gibt
        keine CORS-Kopfzeilen), Schreiben aber nicht.

        127.0.0.1, localhost und ::1 bleiben alle drei gueltig: die Oberflaeche
        laesst sich unter jedem dieser Namen oeffnen, und welcher davon in der
        Adresszeile steht, entscheidet der Nutzer.
        """
        return bool(host) and host in _LOCAL_HOSTS \
            and port == self.server.server_address[1]

    def _same_origin(self) -> bool:
        """
        Wehrt zwei Angriffe ab, die beide aus dem Browser des Nutzers kommen
        und die die Bindung an 127.0.0.1 nicht verhindert:

        CSRF -- eine beliebige geoeffnete Webseite schickt Schreib-Anfragen an
        den Server. Ohne Content-Type-Pruefung (siehe _body(), es wird einfach
        json.loads() aufgerufen) genuegt dafuer eine "simple request", die der
        Browser ohne Vorabfrage durchlaesst. Angreifbar waere damit alles von
        /api/trash bis /api/settings. Der Abgleich laeuft ueber Name UND Port
        (siehe _is_own_address) -- eine andere Seite auf demselben Rechner ist
        genauso fremd wie eine aus dem Netz.

        DNS-Rebinding -- ein fremder Name, dessen Adresse nach dem ersten
        Laden auf 127.0.0.1 wechselt. Aus Browsersicht ist das derselbe
        Origin, die Antworten waeren also lesbar (Bibliothek, Audiodateien,
        Einstellungen). Dagegen hilft nur der Host-Abgleich: die Anfrage kam
        dann unter einem fremden Namen herein.

        Origin schickt jeder Browser bei POST mit; fehlt er, stammt die
        Anfrage nicht aus einem Browser (curl, eigenes Skript). Lesend bleibt
        das erlaubt, schreibend nicht -- ein Aufruf per curl braucht also
        -H "Origin: http://127.0.0.1:<port>" mit dem echten Port.
        """
        # Host traegt keinen Plan mit sich; ohne Portangabe gilt die
        # http-Vorgabe 80 -- die trifft nur zu, wenn der Server selbst dort
        # laeuft, und wird sonst korrekt abgewiesen.
        if not self._is_own_address(*_split_hostport(self.headers.get("Host"), 80)):
            return False
        origin = self.headers.get("Origin")
        if origin:
            parts = urlparse(origin)
            # Nur http: der eigene Server spricht nichts anderes. Ein
            # https-Origin auf demselben Port waere ein anderer Origin.
            if parts.scheme != "http":
                return False
            return self._is_own_address(*_split_hostport(parts.netloc, 80))
        return self.command == "GET"

    def _known_file(self, path: str):
        """Pfad nur freigeben, wenn er analysiert wurde und noch existiert.

        Ohne self.lock: unter WAL (siehe db.connect) duerfen mehrere Leser
        gleichzeitig ran. Der Lock hat hier nichts geschuetzt -- er hat nur
        alle Endpunkte hintereinander gestellt, auch Audio-Range gegen Cover
        gegen Waveform.
        """
        conn = db_mod.connect(cfgmod.load())
        try:
            row = db_mod.row_for_path(conn, path)
        finally:
            conn.close()
        if row is None:
            return None
        return row if os.path.isfile(path) else None

    def _reject_missing(self, path: str) -> bool:
        """Sagt klar, ob der Pfad unbekannt oder die Datei inzwischen weg ist."""
        conn = db_mod.connect(cfgmod.load())
        try:
            known = db_mod.row_for_path(conn, path) is not None
        finally:
            conn.close()
        if not known:
            self._fail("Unbekannter Pfad", 404)
        else:
            self._fail("Datei existiert nicht mehr", 410)
        return True

    def _authorize_path(self, path: str):
        """Wie _known_file, akzeptiert zusaetzlich Pfade aus einer eigenen
        nativen Dateiauswahl (_post_open_file_pick), die noch nicht gescannt
        sind -- damit Metadaten schon vor dem ersten vollen Scan korrigierbar
        sind. Gibt (DB-Zeile-oder-None, ok) zurueck; row ist None bei einer
        noch ungescannten, aber autorisierten Datei."""
        row = self._known_file(path)
        if row is not None:
            return row, True
        if path in self._unscanned_paths and os.path.isfile(path):
            return None, True
        return None, False

    def _authorize_fast(self, path: str) -> bool:
        """Nur die Ja/Nein-Freigabe, mit kurzem Zwischenspeicher -- fuer die
        reinen Ausliefer-Endpunkte (Audio, Cover, Waveform, Rekordbox-Extras,
        ZIP-Buendel), die die DB-Zeile ohnehin nicht anfassen.

        Anlass: ein Sprung im Track erzeugt eine Range-Anfrage, ein Zug ueber
        den Fortschrittsbalken Dutzende -- alle fuer denselben Pfad, und jede
        ging vorher einzeln durch die Datenbank.

        Bewusst NICHT fuer Endpunkte, die die zurueckgegebene Zeile
        weiterverwenden (_post_rename, _post_rewrite, ...): dort waere eine
        veraltete Zeile ein echter Fehler, keine Unschaerfe. _AUTH_TTL_S ist
        kurz gehalten, und jede Stelle, die selbst schreibt, verschiebt,
        umbenennt oder loescht, verwirft den Eintrag zusaetzlich sofort
        (_invalidate_path()).
        """
        cached = _auth_cache.get(path)
        if cached is not None:
            return cached
        ok = self._authorize_path(path)[1]
        _auth_cache.put(path, ok)
        return ok

    @staticmethod
    def _invalidate_path(*paths: str) -> None:
        """Verwirft gemerkte Freigaben. Pflicht ueberall, wo eine Datei
        geschrieben, verschoben, umbenannt oder geloescht wird."""
        for p in paths:
            if p:
                _auth_cache.drop(p)

    def _try_relocate(self, path: str) -> str | None:
        """Sucht eine Datei, die Music.app nach einer Tag-Aenderung in seinem
        Medienordner umsortiert hat (siehe scanner.find_moved()), an ihrem
        neuen Ort und schreibt die Datenbank darauf um (db.move_path()) --
        wie /api/relink, aber automatisch fuer einen einzelnen Pfad. Nur fuer
        Zeilen, die in der Music.app-Bibliothek stehen -- nur die raeumt
        Music.app eigenstaendig um, ein manuell verwaltetes fehlendes File
        soll keinen vollen Bibliotheks-Scan ausloesen.

        Liefert den neuen Pfad bei einem eindeutigen Treffer, sonst None.
        """
        now = time.monotonic()
        last = self._relocate_negative_cache.get(path)
        if last is not None and now - last < self._RELOCATE_RETRY_S:
            return None

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                row = db_mod.row_for_path(conn, path)
                if row is None or path not in db_mod.music_added_map(conn):
                    return None
                known = {r["path"] for r in conn.execute("SELECT path FROM files")}
                missing_row = {"path": path, "size": row["size"],
                               "duration_s": row["duration_s"],
                               "artist": row["artist"], "title": row["title"]}
            finally:
                conn.close()

        try:
            found = scanner.find_moved(cfg, [missing_row], known)
        except Exception:                     # noqa: BLE001
            self._relocate_negative_cache[path] = now
            return None

        new_path = found["matches"].get(path)
        if not new_path:
            self._relocate_negative_cache[path] = now
            return None

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                moved = db_mod.move_path(conn, path, new_path)
            finally:
                conn.close()
        if not moved:
            self._relocate_negative_cache[path] = now
            return None
        audit_log.log("automatisch verschoben", new_path, f"vorher {path}")
        self._relocate_negative_cache.pop(path, None)
        # Der alte Pfad ist ab hier unbekannt, der neue freigegeben -- dieser
        # Weg laeuft an _rebuild() vorbei (siehe dort), also hier selbst.
        self._invalidate_path(path, new_path)
        return new_path

    def _authorize_or_relocate(self, path: str):
        """Wie _authorize_path(), sucht aber bei einer fehlenden, in der
        Music.app-Bibliothek stehenden Datei zuerst automatisch nach ihrem
        neuen Ort (siehe _try_relocate), bevor endgueltig "fehlt" gemeldet
        wird -- sonst schlaegt das Speichern nach einer Metadaten-Aktualisierung
        fehl, sobald Music.app die Datei zwischenzeitlich umsortiert hat.
        Gibt (DB-Zeile-oder-None, ok, aktueller_pfad) zurueck."""
        row, ok = self._authorize_path(path)
        if not ok:
            new_path = self._try_relocate(path)
            if new_path is not None:
                path = new_path
                row, ok = self._authorize_path(path)
        return row, ok, path

    @staticmethod
    def _mark_sets(conn) -> tuple:
        """Die fuenf Markierungsmengen, die rows_to_payload() braucht.

        Jede davon liest ihre Tabelle vollstaendig -- zusammen an echtem
        Material rund 16.400 Zeilen (vor allem music_added und rekordbox).
        Deshalb gibt es _compact_rows(): fuer eine Liste von Pfaden werden sie
        EINMAL gelesen statt je Pfad neu.
        """
        return (db_mod.ignored_paths(conn), db_mod.corrected_paths(conn),
                db_mod.rekordbox_paths(conn), db_mod.music_added_map(conn),
                db_mod.dup_dismissed_paths(conn))

    def _compact_rows(self, conn, cfg: dict, paths, marks: tuple | None = None) -> list:
        """Mehrere DB-Zeilen im kompakten Report-Format, positionsgleich zu
        'paths' -- None, wo es den Pfad nicht (mehr) gibt.

        Die Markierungsmengen werden einmal fuer den ganzen Aufruf gelesen
        (siehe _mark_sets). Wer sie schon hat, reicht sie als 'marks' durch.

        Achtung: rows_to_payload() wird bewusst je Zeile einzeln aufgerufen,
        nicht einmal fuer alle. Es vergibt das Feld 'i' aus der Position in
        seiner Eingabe -- ein Sammelaufruf wuerde daraus 0,1,2,... machen,
        waehrend der Client hier wie bisher ueberall 0 erwartet und den
        richtigen Index selbst setzt.
        """
        if marks is None:
            marks = self._mark_sets(conn)
        out = []
        for path in paths:
            row = db_mod.row_for_path(conn, path)
            out.append(None if row is None
                       else report_mod.rows_to_payload([row], cfg, *marks)[0])
        return out

    def _compact_row(self, conn, cfg: dict, path: str):
        """Eine DB-Zeile im kompakten Report-Format, wie es der Client fuer
        seine DATA-Zeilen erwartet -- None, wenn es den Pfad nicht (mehr)
        gibt. Erwartet eine offene Verbindung.

        Fuer mehrere Pfade IMMER _compact_rows() nehmen: hier werden die fuenf
        Markierungstabellen je Aufruf komplett gelesen.
        """
        return self._compact_rows(conn, cfg, [path])[0]

    def _body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            # Nicht gelesener Koerper bleibt bei Keep-Alive im Puffer stehen
            # und wuerde als naechste Anfrage gedeutet -- siehe _body_to_file.
            self.close_connection = True
            raise ValueError(f"Ungültige Länge: {length}")
        return self.rfile.read(length)

    def _body_to_file(self, fh, limit: int) -> int:
        """Liest den Anfragekoerper blockweise in eine offene Datei.

        Warum nicht _body(): dort entsteht der gesamte Inhalt als ein einziges
        bytes-Objekt im Arbeitsspeicher, bevor das erste Byte auf die Platte
        geht. Beim Drag & Drop einer Datei sind das bis zu MAX_UPLOAD, also
        400 MB je Anfrage -- und der Server beantwortet mehrere Anfragen
        gleichzeitig (ThreadingHTTPServer), zwei parallele Ablagen grosser
        WAV-Dateien reichen also fuer einen Speicherengpass.

        Bei unzulaessiger Laenge wird der Koerper NICHT gelesen und die
        Verbindung geschlossen: unter HTTP/1.1 mit Keep-Alive laege er sonst
        noch im Puffer und der Server wuerde ihn als naechste Anfrage lesen.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            self.close_connection = True
            raise ValueError(f"Ungültige Länge: {length}")
        rest = length
        while rest > 0:
            block = self.rfile.read(min(_CHUNK, rest))
            if not block:
                self.close_connection = True
                raise ValueError("Verbindung brach beim Lesen ab")
            fh.write(block)
            rest -= len(block)
        return length

    # ── GET ───────────────────────────────────────────────────────────────
    def do_GET(self):                          # noqa: N802
        if not self._same_origin():
            self._send(403, "Anfrage von fremder Herkunft abgelehnt.".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        route = urlparse(self.path).path

        if route in ("/", "/report.html", "/index.html"):
            self._serve_report()
        elif route == "/manifest.json":
            self._get_manifest()
        elif route == "/sw.js":
            self._get_service_worker()
        elif route == "/icon-192.png":
            self._send(200, pwa_mod.ICON_192_PNG, "image/png")
        elif route == "/icon-512.png":
            self._send(200, pwa_mod.ICON_512_PNG, "image/png")
        elif route == "/apple-touch-icon.png":
            self._send(200, pwa_mod.ICON_APPLE_TOUCH_PNG, "image/png")
        elif route == "/api/ping":
            tools_ok, tools_msg = media.available()
            self._json({"ok": True, "version": "2.0",
                        "tools_ok": tools_ok, "tools_message": tools_msg})
        elif route == "/api/check-update":
            self._get_check_update()
        elif route == "/api/ignored":
            self._get_ignored()
        elif route == "/api/corrected":
            self._get_corrected()
        elif route == "/api/dup-dismissed":
            self._get_dup_dismissed()
        elif route == "/api/rekordbox":
            self._get_rekordbox()
        elif route == "/api/rekordbox-status":
            self._get_rekordbox_status()
        elif route == "/api/rekordbox-extras":
            self._get_rekordbox_extras()
        elif route == "/api/music-added":
            self._get_music_added()
        elif route == "/api/merge-dismissed":
            self._get_merge_dismissed()
        elif route == "/api/audio":
            self._get_audio()
        elif route == "/api/download-zip":
            self._get_download_zip()
        elif route == "/api/waveform":
            self._get_waveform()
        elif route == "/api/appicon":
            self._get_appicon()
        elif route == "/api/settings":
            self._json(settings_mod.describe())
        elif route == "/api/scan/status":
            self._json({"ok": True, **SCAN.state})
        elif route == "/api/backups":
            self._get_backups()
        elif route == "/api/stats":
            self._get_stats()
        elif route == "/api/cover":
            self._get_cover()
        elif route == "/api/playlists":
            self._get_playlists()
        elif route == "/api/music-playlists":
            self._get_music_playlists()
        elif route == "/api/music-playlist":
            self._get_music_playlist()
        elif route == "/api/rekordbox-playlists":
            self._get_rekordbox_playlists()
        elif route == "/api/rekordbox-playlist":
            self._get_rekordbox_playlist()
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _serve_report(self) -> None:
        # Der einzige Leser des gebackenen Reports -- und damit die Stelle,
        # an der ein vorgemerkter Neubau faellig wird (siehe _rebuild()).
        # Schlaegt er fehl, wird der vorhandene Stand ausgeliefert: eine
        # leicht veraltete Seite ist besser als gar keine, und der Worker
        # versucht es im Hintergrund weiter.
        global _report_gzip
        REPORT.flush()
        try:
            st = self.report_path.stat()
        except OSError:
            self._send(404, "Report fehlt. Zuerst 'report' ausführen.".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return

        if _wants_gzip(self.headers.get("Accept-Encoding")):
            schluessel = (st.st_mtime_ns, st.st_size)
            with _report_gzip_lock:
                gemerkt = _report_gzip
            if gemerkt is not None and gemerkt[0] == schluessel:
                # Treffer: die Datei wird gar nicht erst gelesen.
                self._send(200, gemerkt[1], "text/html; charset=utf-8", gzipped=True)
                return
            try:
                gepackt = gzip.compress(self.report_path.read_bytes(), _GZIP_LEVEL)
            except OSError:
                self._send(404, "Report fehlt. Zuerst 'report' ausführen.".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            with _report_gzip_lock:
                _report_gzip = (schluessel, gepackt)
            self._send(200, gepackt, "text/html; charset=utf-8", gzipped=True)
            return

        try:
            body = self.report_path.read_bytes()
        except OSError:
            self._send(404, "Report fehlt. Zuerst 'report' ausführen.".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _get_ignored(self) -> None:
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                paths = sorted(db_mod.ignored_paths(conn))
            finally:
                conn.close()
        self._json({"ignored": paths})

    def _get_playlists(self) -> None:
        """Baum-Definitionen und Zuordnungen in einem Zug.

        Gehoert wie /api/ignored und /api/corrected zur
        _rebuild()-Ausnahme: der gebackene data/report.html traegt zwar die
        Definitionen (report.build_html -> META.playlists, damit der Baum schon
        vor dem ersten Fetch steht), aber jede Aenderung daran ueber einen
        vollen Rebuild zu schicken waere bei ueber 10.000 Zeilen und knapp 9 MB
        Report unbrauchbar. Der Client zieht stattdessen live nach.
        """
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                nodes = db_mod.playlists_all(conn)
                items = db_mod.playlist_items_map(conn)
            finally:
                conn.close()
        self._json({"ok": True, "playlists": nodes, "items": items})

    def _get_music_playlists(self) -> None:
        """Playlisten-Baum aus Music.app -- nur lesend.

        Wird bewusst NICHT beim Laden der Seite geholt, sondern erst beim
        Aufklappen des Astes: der AppleScript-Aufruf startet Music.app, wenn
        es nicht laeuft, und das soll nicht als Nebenwirkung eines
        Seitenaufrufs passieren.
        """
        if not self._require_music(cfgmod.load()):
            return
        try:
            nodes = media.music_playlists()
        except Exception as exc:                  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)[:300]})
            return
        self._json({"ok": True, "playlists": nodes})

    def _get_music_playlist(self) -> None:
        """Tracks einer Music.app-Playlist in ihrer Reihenfolge.

        Ergaenzt jeden Track um 'known': ob TrackTab eine Datenbankzeile zu
        diesem Pfad hat. Daran haengt die Markierung im Baum -- ein Track ohne
        lokale Datei (Apple-Music-Cloud) oder einer, den unser Scan nie
        gesehen hat, wird in der Liste als solcher gekennzeichnet, statt still
        zu fehlen.
        """
        # _query() liefert bereits flache Zeichenketten (siehe dort) -- ein
        # zusaetzliches [0] wuerde daraus den ersten Buchstaben machen.
        pid = self._query().get("id", "")
        if not pid:
            self._fail("Keine Playlist angegeben")
            return
        if not self._require_music(cfgmod.load()):
            return
        try:
            tracks = media.music_playlist_tracks(pid)
        except Exception as exc:                  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)[:300]})
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                known = {r["path"] for r in conn.execute("SELECT path FROM files")}
            finally:
                conn.close()
        for tr in tracks:
            # Music.app und unsere Datenbank koennen denselben Pfad in
            # unterschiedlicher Unicode-Normalform fuehren (macOS liefert
            # Umlaute je nach Quelle als NFD oder NFC) -- ohne den Abgleich
            # ueber beide Formen gaelte "Grueoesse.mp3" faelschlich als
            # unbekannt.
            path = tr.get("path") or ""
            tr["known"] = bool(path) and (
                path in known
                or unicodedata.normalize("NFC", path) in known
                or unicodedata.normalize("NFD", path) in known)
        self._json({"ok": True, "tracks": tracks})

    def _get_rekordbox_playlists(self) -> None:
        """Playlisten-Baum aus Rekordbox -- nur lesend.

        Laeuft ueber rekordbox._open_cached(), oeffnet master.db also nur
        dann neu, wenn sie sich geaendert hat. Wie beim Music-Ast erst beim
        Aufklappen geholt, nicht beim Laden der Seite.
        """
        try:
            nodes = rekordbox_mod.playlists()
        except Exception as exc:                  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)[:300]})
            return
        self._json({"ok": True, "playlists": nodes})

    def _get_rekordbox_playlist(self) -> None:
        """Tracks einer Rekordbox-Playlist samt Abgleich gegen unsere Datenbank."""
        # _query() liefert bereits flache Zeichenketten (siehe dort) -- ein
        # zusaetzliches [0] wuerde daraus den ersten Buchstaben machen.
        pid = self._query().get("id", "")
        if not pid:
            self._fail("Keine Playlist angegeben")
            return
        try:
            tracks = rekordbox_mod.playlist_tracks(pid)
        except rekordbox_mod.SmartListUnsupported as exc:
            # Kein Fehlerfall der Anwendung, sondern eine bekannte Grenze der
            # Bibliothek (siehe SmartListUnsupported) -- als leere, aber
            # gueltige Antwort melden, damit der Baum nicht wie kaputt wirkt.
            self._json({"ok": True, "tracks": [], "unsupported": True,
                        "error": str(exc)[:200]})
            return
        except Exception as exc:                  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)[:300]})
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                known = {r["path"] for r in conn.execute("SELECT path FROM files")}
            finally:
                conn.close()
        # macOS-Standardvolumes sind case-insensitiv, aber case-preserving:
        # derselbe Ordner kann in Rekordbox' FolderPath und in unserem
        # eigenen Scan mit unterschiedlicher Gross-/Kleinschreibung stehen
        # (z.B. ein von Hand umbenannter Artist-Ordner), obwohl es dieselbe
        # Datei ist. loose_index bildet Normalisierung+Kleinschreibung auf
        # den tatsaechlichen DB-Pfad ab, damit ein Treffer trotzdem zaehlt.
        loose_index: dict[str, str] = {}
        for p in known:
            for variant in (p, unicodedata.normalize("NFC", p), unicodedata.normalize("NFD", p)):
                loose_index[variant.casefold()] = p
        for tr in tracks:
            path = tr.get("path") or ""
            if not path:
                tr["known"] = False
                continue
            if path in known:
                tr["known"] = True
                continue
            match = (loose_index.get(unicodedata.normalize("NFC", path).casefold())
                      or loose_index.get(unicodedata.normalize("NFD", path).casefold())
                      or loose_index.get(path.casefold()))
            if match:
                # Client indiziert Zeilen exakt ueber den DB-Pfad
                # (ROW_BY_PATH) -- ohne diese Korrektur waere der Track als
                # "known" markiert, aber ueber seinen eigenen (abweichend
                # geschriebenen) Pfad trotzdem nicht auffindbar.
                tr["path"] = match
                tr["known"] = True
            else:
                tr["known"] = False
        self._json({"ok": True, "tracks": tracks})

    def _get_corrected(self) -> None:
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                paths = sorted(db_mod.corrected_paths(conn))
            finally:
                conn.close()
        self._json({"corrected": paths})

    def _get_dup_dismissed(self) -> None:
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                paths = sorted(db_mod.dup_dismissed_paths(conn))
            finally:
                conn.close()
        self._json({"dup_dismissed": paths})

    def _get_rekordbox(self) -> None:
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                paths = sorted(db_mod.rekordbox_paths(conn))
            finally:
                conn.close()
        self._json({"paths": paths})

    def _get_rekordbox_status(self) -> None:
        """
        Leichter Vorab-Check, ob Rekordbox gerade laeuft -- die Oberflaeche
        fragt das VOR einem Schreibversuch ab (Knopf-Klick), damit ein
        laufendes Rekordbox erst gar keinen Schreibzugriff mehr ausloest.
        add_tracks_to_playlist() prueft ohnehin nochmal selbst (die
        eigentliche Absicherung gegen eine beschaedigte Bibliothek), dieser
        Endpunkt ist nur die schnellere, klarere Rueckmeldung davor.
        """
        try:
            running = rekordbox_mod.is_running()
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "running": running})

    def _get_merge_dismissed(self) -> None:
        """Dauerhaft ausgeblendete Zusammenfuehrungs-Vorschlaege aller drei
        Felder (Genre/Interpret/Album) -- Werte- und Gruppenlisten selbst
        kommen aus dem bereits im Client gehaltenen DATA, ein Server-Roundtrip
        dafuer waere unnoetig (siehe app.js groupCounts())."""
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                dismissed = {
                    field: [{"a": a, "b": b} for a, b in
                            sorted(db_mod.merge_dismissed_pairs(conn, field))]
                    for field in ("genre", "artist", "album")
                }
            finally:
                conn.close()
        self._json({"dismissed": dismissed})

    def _get_music_added(self) -> None:
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                added = db_mod.music_added_map(conn)
            finally:
                conn.close()
        self._json({"added": added})

    def _get_rekordbox_extras(self) -> None:
        """
        Cues + farbige Waveform aus Rekordbox fuer einen Track, falls dort
        analysiert. 'Nicht in Rekordbox' ist der erwartete Normalfall und
        deshalb kein Fehlerstatus, sondern {"available": false}.
        """
        path = unquote(self._query().get("path", ""))
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        style = str(cfg.get("rekordbox_waveform_style") or "rgb")
        extras = rekordbox_mod.get_track_extras(path, style)
        if extras is None:
            self._json({"available": False})
            return
        self._json({"available": True, **extras})

    def _get_backups(self) -> None:
        cfg = cfgmod.load()
        backups = backup_mod.list_backups(cfg)
        items = [{"name": p.name, "size": p.stat().st_size} for p in reversed(backups)]
        self._json({"ok": True, "backups": items, "dir": str(backup_mod.backup_dir(cfg))})

    def _get_stats(self) -> None:
        """Jahres-/Monats-Statistik, lazy gebaut (siehe stats.is_stale()).

        Kein self.lock: reiner Lesezugriff auf events/files unter WAL,
        Schreiben geht nur an stats.json (Dateisystem), nicht an die DB.
        """
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            data = (stats_mod.build(cfg, conn) if stats_mod.is_stale(cfg, conn)
                    else stats_mod.read(cfg))
        finally:
            conn.close()
        self._json({"ok": True, **data})

    def _post_stats_rebuild(self) -> None:
        """Expliziter 'Neu berechnen'-Knopf -- erzwingt den Rebuild
        unabhaengig vom Staleness-Vergleich."""
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            data = stats_mod.build(cfg, conn)
        finally:
            conn.close()
        self._json({"ok": True, **data})

    def _get_check_update(self) -> None:
        """Manuell angestossene Update-Pruefung (Link im Einstellungen-Dialog)
        gegen die GitHub-Releases-API -- siehe lookup.check_update()."""
        result = lookup_mod.check_update(__version__)
        if result is None:
            self._json({"ok": True, "reachable": False})
        else:
            self._json({"ok": True, "reachable": True, **result})

    def _get_appicon(self) -> None:
        """Symbol von Finder bzw. dem eingestellten Audio-Editor."""
        which = self._query().get("which", "")
        if which == "finder":
            app_path = media.FINDER_APP
        elif which == "music":
            app_path = media.MUSIC_APP
        elif which == "mik":
            app_path = media.mik_path(cfgmod.load())
        elif which == "rekordbox":
            app_path = media.rekordbox_path(cfgmod.load())
        elif which == "daw":
            app_path = media.daw_path(cfgmod.load())
        elif which == "editor":
            app_path = media.editor_path(cfgmod.load())
        else:
            self._fail("Unbekanntes Symbol", 404)
            return
        if not app_path:
            self._fail("Kein Editor eingestellt", 404)
            return
        try:
            png = media.app_icon_png(app_path)
        except Exception as exc:                     # noqa: BLE001
            self._fail(f"Symbol nicht lesbar: {exc}", 404)
            return
        # Symbole aendern sich praktisch nie — der Browser darf sie behalten
        self._send(200, png, "image/png", {"Cache-Control": "max-age=86400"})

    # ── PWA: Installierbarkeit ───────────────────────────────────────────
    def _get_manifest(self) -> None:
        """Web-App-Manifest, siehe pwa.py. Kein Cache-Control-Override --
        aendert sich genau dann, wenn TrackTab selbst aktualisiert wird, eine
        veraltete Kopie nach einem In-Place-Update waere schlechter als der
        vernachlaessigbare Mehraufwand eines Neu-Fetches."""
        self._send(200, pwa_mod.MANIFEST_JSON, "application/manifest+json; charset=utf-8")

    def _get_service_worker(self) -> None:
        """Service Worker fuer die PWA-Installation, siehe app/webui/sw.js.
        Anders als /manifest.json (in Python generiert) frisch von der Platte
        gelesen -- reine Textdatei, kein Grund fuer eine eigene Konstante."""
        try:
            body = (cfgmod.webui_dir() / "sw.js").read_bytes()
        except OSError:
            self._send(404, "sw.js fehlt.".encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(200, body, "application/javascript; charset=utf-8")

    # ── Audio-Streaming mit Range ─────────────────────────────────────────
    def _get_audio(self) -> None:
        """
        Liefert die Originaldatei aus. Range-Unterstuetzung ist Pflicht:
        ohne sie kann der Browser im Track nicht springen.
        """
        path = unquote(self._query().get("path", ""))
        # Endung zusaetzlich pruefen: die Freigabe haengt an einer Zeile in der
        # Datenbank, und welche Dateien dort landen, bestimmen "extensions"
        # und die gescannten Ordner. Ohne diese zweite Huerde waere jede Datei
        # mit passend eingestellter Endung ueber diesen Endpunkt lesbar
        # (siehe cfgmod.AUDIO_EXTENSIONS).
        if (not path or Path(path).suffix.lower() not in cfgmod.AUDIO_EXTENSIONS
                or not self._authorize_fast(path)):
            self._reject_missing(path)
            return

        # Erst oeffnen, dann messen: die Groesse muss vom selben Deskriptor
        # stammen, aus dem gleich gelesen wird. Sonst kann die Datei zwischen
        # getsize() und dem Lesen kleiner werden (z.B. durch das Neukodieren
        # der Bitrate) und der Server verspricht mehr Bytes als er liefert.
        try:
            fh = open(path, "rb")
        except OSError as exc:
            self._send(404, f"Datei nicht lesbar: {exc.strerror or exc}".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return

        with fh:
            st = os.fstat(fh.fileno())
            size = st.st_size
            ctype = _AUDIO_TYPES.get(Path(path).suffix.lower()) \
                or mimetypes.guess_type(path)[0] or "application/octet-stream"
            # Kennung aus Inode, Groesse und mtime: aendert sich, sobald die
            # Datei angefasst wird (Bitrate korrigieren, Tags schreiben).
            etag = '"%s"' % hashlib.blake2b(
                f"{st.st_ino}:{size}:{st.st_mtime_ns}".encode("utf-8"),
                digest_size=12).hexdigest()

            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            start, end = 0, size - 1
            status = 200
            rng = self.headers.get("Range", "")
            # If-Range: passt die Kennung nicht mehr, will der Browser die
            # ganze Datei -- ein Teilbereich bezoege sich sonst auf eine
            # andere Fassung (z.B. nach einer Bitratenkorrektur).
            if_range = self.headers.get("If-Range")
            if rng.startswith("bytes=") and (if_range is None or if_range == etag):
                spec = rng[6:].split(",")[0].strip()
                first, _, last = spec.partition("-")
                try:
                    if first:
                        start = int(first)
                        end = int(last) if last else size - 1
                    elif last:                   # Suffix-Range: letzte N Bytes
                        start = max(0, size - int(last))
                    if start >= size or start > end:
                        raise ValueError
                    end = min(end, size - 1)
                    status = 206
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self._security_headers()
                    self.end_headers()
                    return

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self._security_headers()
            self.send_header("ETag", etag)
            # Der ETag deckt jede Aenderung an der Datei ab -- der Browser
            # darf sie also behalten und muss nur nachfragen, statt bei jedem
            # Sprung im Track alles erneut zu holen.
            self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            remaining = length
            try:
                # sendfile() laesst den Kernel direkt vom Dateideskriptor in
                # den Socket schaufeln -- kein bytes-Objekt je 256-KB-Haeppchen
                # in Python. wfile puffert die Header, die muessen vorher raus.
                self.wfile.flush()
                offset = start
                while remaining > 0:
                    sent = self.connection.sendfile(fh, offset, remaining)
                    if not sent:
                        break
                    offset += sent
                    remaining -= sent
            except (BrokenPipeError, ConnectionResetError):
                return                           # Browser hat abgebrochen — normal
            except OSError:
                remaining = -1                   # Lesefehler mitten im Stream
            if remaining != 0:
                # Weniger geschrieben als im Content-Length versprochen. Die
                # Verbindung ist damit unbrauchbar: bliebe sie offen, liest der
                # Browser die naechste Antwort als Rest dieser Datei und meldet
                # "no supported sources". Also zumachen.
                self.close_connection = True

    def _get_download_zip(self) -> None:
        """
        Buendelt mehrere Tracks als ein ZIP fuer den DownloadURL-Mechanismus
        beim Ziehen aus dem Browser hinaus (z.B. auf den Finder) -- der kann
        pro Zug nur eine Datei liefern, ein Buendel ist die einzige
        Moeglichkeit, eine Mehrfachauswahl in einem Rutsch zu kopieren. Auch
        Ziel des Export-Knopfs der Mehrfachauswahl-Leiste (optional mit
        Umbenennen nur innerhalb des Archivs, siehe unten).
        """
        q = self._query()
        try:
            paths = json.loads(unquote(q.get("paths", "[]")))
        except (json.JSONDecodeError, TypeError, ValueError):
            paths = []
        if not isinstance(paths, list):
            paths = []

        known = [p for p in paths if isinstance(p, str)
                 and self._authorize_fast(p) and os.path.isfile(p)]
        if not known:
            self._send(404, "Keine gueltigen Dateien".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return

        # Dateiname aus der Query kommt vom Client (t()-generiert) -- gegen
        # Header-Injection und Pfadwechsel absichern, nicht blind uebernehmen.
        name = unquote(q.get("name", "Tracks.zip"))
        name = "".join(c for c in name if c not in ("\r", "\n", '"', "/", "\\"))[:200] or "Tracks.zip"
        if not name.lower().endswith(".zip"):
            name += ".zip"

        # Optionales Umbenennen NUR innerhalb des Archivs -- die Originale in
        # der Bibliothek bleiben unangetastet (kein os.rename, kein
        # db.move_path). Nutzt dieselben reinen Lesefunktionen wie das echte
        # Umbenennen der Einzelpruefungen (app/rename.py), aber nie plan()/
        # rename_file() selbst.
        rename_pattern: str | None = None
        if q.get("rename") == "1":
            from . import rename as rename_mod
            rename_pattern = unquote(q.get("pattern", "")) or cfgmod.load()["rename_pattern"]
            try:
                rename_pattern = rename_mod.validate_pattern(rename_pattern)
            except ValueError as exc:
                self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return

        fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            used_names: set[str] = set()
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:
                for p in known:
                    arcname = os.path.basename(p)
                    if rename_pattern is not None:
                        try:
                            meta = rename_mod.read_meta(p)
                            values = rename_mod.values_for(meta)
                            if rename_mod.has_identity(values, rename_pattern):
                                ext = os.path.splitext(p)[1]
                                arcname = rename_mod.render_name(values, rename_pattern) + ext
                        except Exception:                # noqa: BLE001
                            pass                          # Originalname als Fallback
                    if arcname in used_names:
                        stem, ext = os.path.splitext(arcname)
                        n = 2
                        while f"{stem} ({n}){ext}" in used_names:
                            n += 1
                        arcname = f"{stem} ({n}){ext}"
                    used_names.add(arcname)
                    zf.write(p, arcname)

            size = os.path.getsize(tmp_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self._security_headers()
            self.end_headers()
            with open(tmp_path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return                       # Browser hat abgebrochen — normal
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _get_waveform(self) -> None:
        path = unquote(self._query().get("path", ""))
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return

        mtime = os.path.getmtime(path)
        conn = db_mod.connect(cfgmod.load())
        try:
            peaks = db_mod.waveform_get(conn, path, mtime)
        finally:
            conn.close()

        if peaks is None:
            try:
                peaks = _waveform_shared(path, mtime)
            except Exception as exc:             # noqa: BLE001
                self._fail(f"Waveform fehlgeschlagen: {exc}", 500)
                return
        self._json({"peaks": peaks})

    def _get_cover(self) -> None:
        """Eingebettetes Cover einer Datei, falls vorhanden.

        Die Antwort darf zwischengespeichert werden: aendert sich das Cover
        durch uns selbst, haengt der Client ohnehin ein neues &v=<rev> an die
        URL (siehe bumpCoverRev() in app.js), und der ETag deckt jede
        Aenderung an der Datei ab. Vorher schickte _send() pauschal
        'no-store' -- jede Neuzeichnung der Tabelle und jeder Trackwechsel
        holte damit jedes Bild erneut, und serverseitig hiess das jedes Mal
        die komplette Datei durch mutagen parsen.
        """
        path = unquote(self._query().get("path", ""))
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        try:
            st = os.stat(path)
        except OSError:
            self._fail("Datei existiert nicht mehr", 410)
            return
        etag = '"cov-%s"' % hashlib.blake2b(
            f"{st.st_size}:{st.st_mtime_ns}".encode("utf-8"), digest_size=12).hexdigest()
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        result = tags_mod.read_cover(path)
        if result is None:
            # Fallback: ein Cover, das write_cover() nicht in die Datei
            # einbetten konnte (siehe coverfill.py), liegt stattdessen im
            # DB-Cache.
            conn = db_mod.connect(cfgmod.load())
            try:
                result = db_mod.cached_cover(conn, path)
            finally:
                conn.close()
        if result is None:
            self._fail("Kein eingebettetes Cover", 404)
            return
        data, mime = result
        mime = (mime or "").split(";")[0].strip().lower()
        if mime not in _COVER_MIMES:
            mime = "image/jpeg"          # siehe _COVER_MIMES
        self._send(200, data, mime, {
            "ETag": etag,
            "Cache-Control": "private, max-age=604800",
        })

    # ── POST ──────────────────────────────────────────────────────────────
    def do_POST(self):                         # noqa: N802
        if not self._same_origin():
            self._fail("Anfrage von fremder Herkunft abgelehnt", 403)
            return
        route = urlparse(self.path).path
        if route == "/api/ignore":
            self._post_ignore()
        elif route == "/api/play":
            self._post_play()
        elif route == "/api/correct":
            self._post_correct()
        elif route == "/api/dup-dismiss-bulk":
            self._post_dup_dismiss_bulk()
        elif route == "/api/reveal":
            self._post_reveal()
        elif route == "/api/analyse":
            self._post_analyse()
        elif route == "/api/reanalyse":
            self._post_reanalyse()
        elif route == "/api/prune":
            self._post_prune()
        elif route == "/api/relink":
            self._post_relink()
        elif route == "/api/open-in":
            self._post_open_in()
        elif route == "/api/open-music":
            self._post_open_music()
        elif route == "/api/open-mik":
            self._post_open_mik()
        elif route == "/api/open-daw":
            self._post_open_daw()
        elif route == "/api/orphan-cleanup":
            self._post_orphan_cleanup()
        elif route == "/api/open-file-pick":
            self._post_open_file_pick()
        elif route == "/api/analyse-path":
            self._post_analyse_path()
        elif route == "/api/rename":
            self._post_rename()
        elif route == "/api/add-to-library":
            self._post_add_to_library()
        elif route == "/api/rekordbox-add-playlist":
            self._post_rekordbox_add_playlist()
        elif route == "/api/rekordbox-sync":
            self._post_rekordbox_sync()
        elif route == "/api/rekordbox-scan":
            self._post_rekordbox_scan()
        elif route == "/api/rekordbox-fix-paths":
            self._post_rekordbox_fix_paths()
        elif route == "/api/music-added-sync":
            self._post_music_added_sync()
        elif route == "/api/pick-folder":
            self._post_pick_folder()
        elif route == "/api/pick-daw-template":
            self._post_pick_daw_template()
        elif route == "/api/backup/create":
            self._post_backup_create()
        elif route == "/api/backup/restore-pick":
            self._post_backup_restore_pick()
        elif route == "/api/log-folder":
            self._post_log_folder()
        elif route == "/api/backup-folder":
            self._post_backup_folder()
        elif route == "/api/open-store":
            self._post_open_store()
        elif route == "/api/trash":
            self._post_trash()
        elif route == "/api/convert":
            self._post_convert()
        elif route == "/api/rewrite":
            self._post_rewrite()
        elif route == "/api/rewrite-drop":
            self._post_rewrite_drop()
        elif route == "/api/tags":
            self._post_tags()
        elif route == "/api/fix-tag-issues":
            self._post_fix_tag_issues()
        elif route == "/api/genre-rename":
            self._post_genre_rename()
        elif route == "/api/artist-rename":
            self._post_artist_rename()
        elif route == "/api/album-rename":
            self._post_album_rename()
        elif route == "/api/merge-dismiss":
            self._post_merge_dismiss()
        elif route == "/api/cover":
            self._post_cover()
        elif route == "/api/cover-delete":
            self._post_cover_delete()
        elif route == "/api/lookup":
            self._post_lookup()
        elif route == "/api/lookup-cover":
            self._post_lookup_cover()
        elif route == "/api/settings":
            self._post_settings()
        elif route == "/api/shops":
            self._post_shops()
        elif route == "/api/columns":
            self._post_columns()
        elif route == "/api/column-views":
            self._post_column_views()
        elif route == "/api/column-assign":
            self._post_column_assign()
        elif route == "/api/drop-columns":
            self._post_drop_columns()
        elif route == "/api/playlist":
            self._post_playlist()
        elif route == "/api/playlist-items":
            self._post_playlist_items()
        elif route == "/api/settings/reset":
            self._post_settings_reset()
        elif route == "/api/reclassify":
            self._post_reclassify()
        elif route == "/api/stats/rebuild":
            self._post_stats_rebuild()
        elif route == "/api/scan":
            self._post_scan()
        elif route == "/api/scan/cancel":
            SCAN.cancel()
            self._json({"ok": True})
        elif route == "/api/quit":
            self._post_quit()
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _post_ignore(self) -> None:
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
            flag = bool(payload["ignored"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                if db_mod.row_for_path(conn, path) is None:
                    self._fail("Unbekannter Pfad")
                    return
                db_mod.set_ignored(conn, path, flag)
                count = conn.execute("SELECT COUNT(*) FROM ignored").fetchone()[0]
            finally:
                conn.close()
        self._json({"ok": True, "ignored": flag, "count": count})

    def _post_play(self) -> None:
        """Protokolliert eine qualifizierende Wiedergabe (>= 30s tatsaechliche
        Hoerzeit, siehe listenFinish() in app.js) fuer die spaetere
        Statistik-Ansicht (Issue #15/#16). Rein additiv -- ein unbekannter
        Pfad (z.B. eine noch nicht gescannte Einzelpruefung) oder ein Fehler
        hier darf die Wiedergabe selbst nie stoeren, deshalb immer {"ok": true}
        statt eines Fehlers."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            path = str(payload["path"])
            duration_s = float(payload["duration_s"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if duration_s < 30:
            self._json({"ok": True})
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                row = db_mod.row_for_path(conn, path)
                if row is not None:
                    cap = (row["duration_s"] or duration_s) + 5
                    db_mod.log_event(
                        conn, "play", path,
                        duration_s=min(duration_s, cap),
                        in_music=path in db_mod.music_added_map(conn),
                    )
            finally:
                conn.close()
        self._json({"ok": True})

    def _post_correct(self) -> None:
        """Track als manuell korrigiert markieren oder zurueckholen."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
            flag = bool(payload["corrected"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                if db_mod.row_for_path(conn, path) is None:
                    self._fail("Unbekannter Pfad")
                    return
                db_mod.set_corrected(conn, path, flag)
                count = conn.execute("SELECT COUNT(*) FROM corrected").fetchone()[0]
            finally:
                conn.close()
        self._json({"ok": True, "corrected": flag, "count": count})

    def _post_dup_dismiss_bulk(self) -> None:
        """
        Alle aktuell lebenden Mitgliedspfade EINER Duplikat-Gruppe auf einen
        Schlag als "kein Duplikat" markieren oder zuruecknehmen -- sowohl fuer
        den Knopf im Gruppenkopf der Duplikate-Ansicht als auch fuer den
        automatischen Reset, wenn eine neue Kopie zu einer bereits
        bestaetigten Gruppe dazukommt. r.dg selbst ist ephemer (pro
        Seitenladung neu vergeben), deshalb schickt der Client die volle,
        aktuelle Pfadliste statt einer Gruppen-ID.
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            paths = [str(p) for p in payload["paths"]]
            flag = bool(payload["flag"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Pfade angegeben")
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                for p in paths:
                    if db_mod.row_for_path(conn, p) is None:
                        self._fail("Unbekannter Pfad")
                        return
                db_mod.set_dup_dismissed_bulk(conn, paths, flag)
                count = conn.execute("SELECT COUNT(*) FROM dup_dismissed").fetchone()[0]
            finally:
                conn.close()
        self._json({"ok": True, "flag": flag, "count": count})

    def _post_reveal(self) -> None:
        """
        Zeigt die Datei im Finder. Browser koennen das nicht: ein file://-Link
        auf einen Ordner landet bestenfalls in einer Verzeichnisauflistung.
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return

        if self._known_file(path) is None:
            self._reject_missing(path)
            return
        try:
            subprocess.run(["open", "-R", path], check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            self._fail(f"Finder konnte nicht geöffnet werden: {exc}", 500)
            return
        self._json({"ok": True})

    def _post_open_in(self) -> None:
        """Datei im externen Editor oeffnen (Vorgabe: iZotope RX)."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not self._authorize_path(path)[1]:
            self._reject_missing(path)
            return
        try:
            editor = media.open_in_editor(path, cfgmod.load())
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "editor": Path(editor).stem})

    def _post_open_daw(self) -> None:
        """Datei in der eingestellten DAW oeffnen (siehe media.daw_path)."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not self._authorize_path(path)[1]:
            self._reject_missing(path)
            return
        try:
            daw = media.open_in_daw(path, cfgmod.load())
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "editor": Path(daw).stem})

    def _post_open_mik(self) -> None:
        """
        Bekannte oder ueber _authorize_path autorisierte Datei(en) in Mixed In
        Key oeffnen -- importiert sie dort, MIK analysiert (Key/BPM/Energy)
        und schreibt Tags danach selbst, im Hintergrund. Ein Pfad oder eine
        Liste, wie bei /api/reanalyse. Bewusst _authorize_path statt
        _known_file: der Bulk-Knopf in den Einzelpruefungen ruft dies fuer
        Zeilen auf, die nie in der DB landen (siehe /api/open-file-pick).
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            raw = payload.get("paths")
            paths = [str(p) for p in raw] if raw is not None else [str(payload["path"])]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        for p in paths:
            if not self._authorize_path(p)[1]:
                self._reject_missing(p)
                return
        try:
            media.open_in_mik(paths, cfgmod.load())
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "count": len(paths)})

    def _post_open_file_pick(self) -> None:
        """
        Native Dateiauswahl fuer die einfache Einzelpruefung per "Dateien
        öffnen" -- liefert nur die gewaehlten Pfade zurueck, analysiert wird
        NICHT hier. Der Client ruft dafuer /api/analyse-path einzeln je Datei
        auf (siehe dropPick in app.js) -- nur so laesst sich beim Auswaehlen
        mehrerer Dateien ein "x von y"-Fortschritt anzeigen, statt dass die
        Oberflaeche bis zum letzten Ergebnis reaktionslos wartet.
        Landet bewusst NICHT in der Datenbank, wie bei /api/analyse -- das
        soll die Bibliotheks-Statistik nicht verfaelschen. Der echte Pfad
        macht die Zeilen fuer /api/add-to-library importierbar (r.nativePath,
        siehe app.js).
        """
        try:
            paths = media.choose_audio_files("Tracks öffnen")
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        if paths is None:
            self._json({"ok": True, "cancelled": True})
            return
        if not paths:
            self._fail("Keine Datei ausgewählt")
            return

        for p in paths:
            self._unscanned_paths.add(p)
        self._json({"ok": True, "paths": paths})

    def _post_analyse_path(self) -> None:
        """
        Misst Dateien der Einzelpruefung an ihrem Ort noch einmal -- der
        "neu analysieren"-Knopf dort. Bewusst NICHT /api/reanalyse: das
        verlangt eine DB-Zeile und schreibt das Ergebnis zurueck, waehrend
        eine Einzelpruefung nie in der Datenbank landet (siehe
        _post_open_file_pick). Ergebnis geht nur an den Client.

        Anlass ist vor allem Mixed In Key: es schreibt Key/BPM/Energy erst
        nach seiner eigenen Analyse in die Datei, also lange nachdem
        /api/open-mik geantwortet hat.

        'extra_checks' (optional, bool): schaltet denselben Cover-/Music.app-/
        Rekordbox-Abgleich wie bei /api/reanalyse zu, rein informativ (siehe
        app.js:reanalyseDrops()). Wird NUR vom "neu analysieren"-Knopf einer
        Einzelpruefung gesetzt, NICHT beim erstmaligen Oeffnen/Ablegen einer
        Datei (_post_open_file_pick) -- sonst wuerde jeder erste Import
        zusaetzliche AppleScript-/Rekordbox-Aufrufe kosten, obwohl nur nach
        dem Knopf gefragt war. Schreibt NIE in music_added/rekordbox: diese
        Tabellen sind an eine files-Zeile gebunden, die eine Einzelpruefung
        nie hat -- nur die Datei selbst kann ein Cover bekommen (siehe
        coverfill.fill_cover_for_untracked()). Ist eine Datei weder in
        Music.app noch in Rekordbox bekannt, bleibt das jeweilige Feld in
        der Antwortzeile einfach weg.
        """
        from .analyzer import analyse_file

        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            raw = payload.get("paths")
            paths = [str(p) for p in raw] if raw is not None else [str(payload["path"])]
            extra_checks = bool(payload.get("extra_checks"))
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        for p in paths:
            if not self._authorize_path(p)[1]:
                self._reject_missing(p)
                return

        cfg = cfgmod.load()
        rows = []
        for p in paths:
            try:
                st = os.stat(p)
                rows.append(analyse_file((p, st.st_size, st.st_mtime, cfg)))
            except OSError as exc:
                rows.append({"path": p, "status": "error", "error": str(exc)})

        rekordbox_running = False
        # Ohne ausgewaehlte Music App bzw. ohne eingestelltes/gefundenes
        # Rekordbox bleibt der jeweilige Abgleich hier ganz aus (siehe
        # _require_music()/_rekordbox_available()) -- sonst wuerde jede
        # "neu analysieren"-Anfrage eine nie vorhandene master.db suchen.
        music_enabled = bool(cfg.get("external_music"))
        rekordbox_enabled = self._rekordbox_available(cfg)
        if extra_checks:
            rekordbox_running = rekordbox_enabled and rekordbox_mod.is_running()
            for row in rows:
                if row.get("status") == "error":
                    continue
                path, title = row["path"], row.get("title")
                if music_enabled:
                    try:
                        added_ts = media.music_added_date_for(path, title)
                    except Exception:                      # noqa: BLE001
                        added_ts = None
                    if added_ts:
                        row["music_added_ts"] = added_ts
                if rekordbox_enabled and not rekordbox_running:
                    try:
                        present = rekordbox_mod.content_present(path)
                    except Exception:                  # noqa: BLE001
                        present = False
                    if present:
                        row["rekordbox_present"] = True
                if music_enabled:
                    try:
                        if coverfill.fill_cover_for_untracked(
                                path, title, bool(row.get("has_cover"))):
                            row["has_cover"] = 1
                    except Exception:                      # noqa: BLE001
                        pass

        self._json({"ok": True, "rows": rows, "rekordbox_running": rekordbox_running})

    def _post_rename(self) -> None:
        """
        Benennt Dateien der Einzelpruefung nach dem konfigurierten
        Namensmuster um (cfg["rename_pattern"], siehe app/rename.py) -- der
        Knopf "Dateien automatisch umbenennen" dort.

        Nur fuer Dateien AUSSERHALB der konfigurierten Bibliotheksordner:
        was schon in der Bibliothek liegt, ist dort ueber seinen Pfad
        verlinkt (Music.app, Rekordbox, Playlisten) und wird uebersprungen
        statt umbenannt (siehe rename.is_in_library()).

        Nach dem Umbenennen ist die Datei unter ihrem alten Pfad weg -- jede
        Zuordnung, die daran haengt, wird hier sofort nachgezogen:
        _unscanned_paths (sonst verweigert _authorize_path jeden weiteren
        Zugriff, vom Abspielen bis zum Tag-Speichern) und, falls es doch
        eine Datenbankzeile gibt, db.move_path() samt Report-Neubau. Der
        Client zieht seinerseits ueber die zurueckgegebenen Pfade nach.

        Die Antwort ist positionsgleich zu 'paths', ein Eintrag je Datei mit
        status 'renamed' | 'unchanged' | 'in_library' | 'no_data' | 'error'.
        """
        from . import rename as rename_mod

        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            raw = payload.get("paths")
            paths = [str(p) for p in raw] if raw is not None else [str(payload["path"])]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        for p in paths:
            if not self._authorize_path(p)[1]:
                self._reject_missing(p)
                return

        cfg = cfgmod.load()
        results = []
        moved_db_rows = False
        for old in paths:
            try:
                res = rename_mod.rename_file(old, cfg)
            except rename_mod.RenameError as exc:
                results.append({"path": old, "status": "error", "error": str(exc)})
                continue
            except Exception as exc:                     # noqa: BLE001
                results.append({"path": old, "status": "error", "error": str(exc)})
                continue
            if res["status"] != "rename":
                results.append(res)
                continue

            new = res["new_path"]
            # Autorisierung mitnehmen: die Datei ist dieselbe, nur ihr Pfad
            # ist neu -- ohne das faellt sie fuer jeden folgenden Endpunkt
            # (Audio, Tags, Cover, neu messen) in "Unbekannter Pfad".
            self._unscanned_paths.discard(old)
            self._unscanned_paths.add(new)
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    if db_mod.move_path(conn, old, new):
                        moved_db_rows = True
                finally:
                    conn.close()
            audit_log.log("umbenannt", new, f"vorher {os.path.basename(old)}")
            results.append({"path": old, "status": "renamed", "new_path": new,
                            "name": os.path.basename(new)})

        # Nur wenn wirklich eine Datenbankzeile mitgewandert ist -- der
        # Regelfall (Einzelpruefung ausserhalb der Bibliothek) hat keine.
        if moved_db_rows:
            self._rebuild(cfg)
        self._json({"ok": True, "results": results})

    def _post_add_to_library(self) -> None:
        """
        Importiert Dateien richtig in die Music.app-Bibliothek (nicht nur
        abspielen wie /api/open-music). Bewusst OHNE den ueblichen
        _known_file()-Datenbankabgleich: die Einzelpruefung (Drag & Drop /
        nativer Dateidialog, siehe /api/open-file-pick) landet nie in der DB,
        soll aber trotzdem importieren koennen. Es genuegt hier ein reiner
        Existenzcheck -- an den Dateien selbst wird nichts geschrieben oder
        geloescht, sie werden nur ins eigene Music.app importiert.

        Der Import ist zugleich der Uebergang von der Einzelpruefung in die
        eigentliche Bibliotheksliste: ab hier liegt die Datei dauerhaft im
        Medienordner, also bekommt sie hier auch ihre DB-Zeile (analysiert am
        endgueltigen Pfad) und einen Eintrag in 'music_added'. Ohne das wuerde
        sie erst beim naechsten vollen Scan auftauchen.

        Vorbehalt: liegt der Music.app-Medienordner ausserhalb der
        konfigurierten Bibliothekspfade (cfg["library_paths"]), raeumt der
        naechste Scan mit --prune diese Zeilen wieder ab -- dieselbe
        Voraussetzung, unter der schon die Pfadkorrektur unten (move_path)
        ueberhaupt sinnvoll ist.
        """
        from .analyzer import analyse_file

        cfg = cfgmod.load()
        if not self._require_music(cfg):
            return
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            raw = payload.get("paths")
            paths = [str(p) for p in raw] if raw is not None else [str(payload["path"])]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        for p in paths:
            if not os.path.isfile(p):
                self._fail("Datei existiert nicht (mehr)", 410)
                return
        try:
            locations = media.add_to_music_library(paths)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        for p in paths:
            audit_log.log("import", p, "Music-Bibliothek")

        # Music.app legt die Datei in seinen eigenen Medienordner, wenn dort
        # "Dateien beim Hinzufuegen kopieren" eingeschaltet ist -- der Pfad in
        # unserer Datenbank zeigt danach ins Leere und die Datei taucht beim
        # naechsten Scan als fehlend UND als vermeintlicher Neuzugang auf.
        # 'location' ist der Pfad, den Music.app dem Track wirklich zuweist:
        # weicht er ab, wird die DB-Zeile darauf umgeschrieben. locations ist
        # positionsgleich zu paths (media.add_to_music_library haelt die
        # Reihenfolge und fuellt Unbekanntes mit einem leeren Eintrag auf).
        moved: list[dict] = []
        final: list[str] = []          # je Eingabepfad der Ort, an dem die Datei jetzt liegt
        for old_path, new_path in zip(paths, locations):
            if not new_path or new_path == old_path or not os.path.isfile(new_path):
                final.append(old_path)
                continue
            if old_path in self._unscanned_paths:
                self._unscanned_paths.discard(old_path)
                self._unscanned_paths.add(new_path)
            moved.append({"old": old_path, "new": new_path})
            final.append(new_path)

        # Erster Abschnitt unter dem Lock: nur Datenbankarbeit. Die Messung
        # selbst liegt bewusst DAZWISCHEN und ohne Lock (siehe unten).
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                for entry in moved:
                    entry["_had_row"] = db_mod.move_path(conn, entry["old"], entry["new"])
                    audit_log.log("verschoben", entry["new"], f"vorher {entry['old']}")
                # Zeilen erst NACH allen Verschiebungen bauen: _compact_rows()
                # liest die Markierungstabellen einmal fuer den ganzen Aufruf,
                # und genau in die schreibt move_path(). Verschraenkt muessten
                # sie je Eintrag neu gelesen werden -- rund 16.400 Zeilen je
                # Stueck (siehe _mark_sets).
                if moved:
                    gebaut = self._compact_rows(conn, cfg, [e["new"] for e in moved])
                    for entry, row in zip(moved, gebaut):
                        entry["row"] = row if entry.pop("_had_row") else None
                # Welche Pfade noch keine Zeile haben, entscheidet sich hier;
                # gemessen wird gleich ausserhalb des Locks.
                zu_messen = [p for p in final if db_mod.row_for_path(conn, p) is None]
            finally:
                conn.close()

        # Neuzugaenge aus der Einzelpruefung: am endgueltigen Pfad messen,
        # nicht das Ergebnis vom alten Ort uebernehmen -- Music.app kann die
        # Datei beim Kopieren umschreiben, und der Cache-Schluessel (Pfad,
        # Groesse, mtime) muss zum Ziel passen.
        #
        # OHNE Lock: analyse_file() startet ffprobe und ffmpeg und braucht an
        # echtem Material rund 2 Sekunden JE DATEI. Unter dem Lock stand
        # solange die gesamte Oberflaeche still -- ein Import von 20 Tracks
        # hat sie rund 40 Sekunden blockiert, auch fuer alles Lesende.
        fresh = []
        for path in zu_messen:
            try:
                st = os.stat(path)
            except OSError:
                continue
            fresh.append(analyse_file((path, st.st_size, st.st_mtime, cfg)))

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                if fresh:
                    db_mod.save(conn, fresh)
                # Nur Pfade mit 'files'-Zeile vormerken: fuer eine Datei, die
                # sich nicht messen liess, gibt es in der Liste nichts, woran
                # ein Hinzugefuegt-Datum haengen koennte (wie sync_music_added,
                # das ebenfalls nur bekannte Dateien uebernimmt). Muss VOR dem
                # Bauen der kompakten Zeilen passieren -- die tragen das Datum
                # als Feld 'da', nach dem der Client anschliessend sortiert.
                added = [p for p in final if db_mod.row_for_path(conn, p) is not None]
                db_mod.mark_music_added(conn, added)
                for p in added:
                    db_mod.log_event(conn, "track_added", p, in_music=True)
                # Positionsgleich zu 'paths', Unbekanntes als None -- der
                # Client haengt diese Zeilen an seine Bibliotheksliste an.
                rows = self._compact_rows(conn, cfg, final)
            finally:
                conn.close()
            self._rebuild(cfg)

        self._json({"ok": True, "count": len(paths), "locations": locations,
                    "moved": moved, "final": final, "rows": rows})

    def _post_rekordbox_add_playlist(self) -> None:
        """
        Fuegt Dateien der in den Einstellungen hinterlegten, bereits
        bestehenden Rekordbox-Playlist hinzu. Schreibt direkt in Rekordbox'
        master.db (siehe app/rekordbox.py) -- Rekordbox muss dafuer beendet
        sein.
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            raw = payload.get("paths")
            paths = [str(p) for p in raw] if raw is not None else [str(payload["path"])]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        # Zeilen gleich mitnehmen statt nur auf Vorhandensein zu pruefen --
        # sie liefern Titel/Interpret/Album/Genre/BPM fuer neu in Rekordbox
        # anzulegende Tracks (siehe _content_tag_kwargs() in rekordbox.py).
        metadata: dict = {}
        for p in paths:
            row = self._known_file(p)
            if row is None:
                self._reject_missing(p)
                return
            metadata[p] = row

        cfg = cfgmod.load()
        playlist_name = str(cfg.get("rekordbox_playlist") or "")
        try:
            # Die beiden Locks NACHEINANDER, nicht ineinander (siehe
            # Reihenfolge-Regel bei _rekordbox_lock): der Rekordbox-Schreib-
            # zugriff braucht die Oberflaeche nicht anzuhalten, und zwischen
            # den beiden Abschnitten liegt nichts, was zusammenhaengen muss --
            # mark_rekordbox_present() ist ein reiner Praesenz-Cache.
            with self._rekordbox_lock:
                result = rekordbox_mod.add_tracks_to_playlist(paths, playlist_name, metadata)
            if result["added"]:
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        db_mod.mark_rekordbox_present(conn, result["added"])
                    finally:
                        conn.close()
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 500)
            return
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        for p in result["added"]:
            audit_log.log("rekordbox", p, f"zu „{playlist_name}\"")
        self._json({"ok": True, **result})

    def _post_rekordbox_sync(self) -> None:
        """Voller Abgleich gegen Rekordbox' Sammlung -- ein Fehlschlag darf
        die bestehenden Haken nicht loeschen, der letzte bekannte Stand
        bleibt stehen."""
        try:
            present = rekordbox_mod.collection_paths()
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                matched = db_mod.sync_rekordbox_presence(conn, present)
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
        self._json({"ok": True, "matched": matched, "checked": total})

    def _post_rekordbox_scan(self) -> None:
        """Erster Schritt der Pfad-Korrektur: findet DjmdContent-Zeilen, deren
        Datei fehlt, und sucht sie unter unseren eigenen 'files'-Pfaden
        wieder. Schreibt nichts -- siehe _post_rekordbox_fix_paths() fuer den
        zweiten, vom Nutzer bestaetigten Schritt.

        Eintraege, die sich gar nicht wiederfinden lassen (Datei wirklich
        geloescht), tauchen in der Antwort bewusst nicht auf -- das Entfernen
        eines Tracks aus Rekordbox' eigener Sammlung macht der Nutzer dort
        selbst, nicht hier."""
        try:
            stale = rekordbox_mod.stale_content_entries()
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        if not stale:
            self._json({"ok": True, "moved": [], "ambiguous": [], "checked": 0})
            return

        # Reine Gross-/Kleinschreibungs-Abweichungen (case_fix, siehe
        # rekordbox._case_correct_path()) kennen ihren Zielpfad bereits --
        # keine Fuzzy-Suche noetig, direkt uebernehmen. Nur die restlichen,
        # wirklich fehlenden Dateien gehen in den Abgleich gegen unsere DB.
        moved, ambiguous = [], []
        missing = []
        for e in stale:
            base = {"id": e["id"], "old_path": e["path"], "artist": e["artist"], "title": e["title"]}
            if e.get("case_fix"):
                moved.append({**base, "new_path": e["case_fix"]})
            else:
                missing.append(e)

        if missing:
            with self.lock:
                conn = db_mod.connect(cfgmod.load())
                try:
                    candidates = [dict(r) for r in conn.execute(
                        "SELECT path, size, duration_s, artist, title FROM files")
                        if os.path.isfile(r["path"])]
                finally:
                    conn.close()

            found = scanner.find_moved_among(missing, candidates)
            for e in missing:
                base = {"id": e["id"], "old_path": e["path"], "artist": e["artist"], "title": e["title"]}
                if e["path"] in found["matches"]:
                    moved.append({**base, "new_path": found["matches"][e["path"]]})
                elif e["path"] in found["ambiguous"]:
                    ambiguous.append({**base, "candidates": found["ambiguous"][e["path"]]})
            checked = found["checked"]
        else:
            checked = 0
        self._json({"ok": True, "moved": moved, "ambiguous": ambiguous,
                    "checked": checked})

    def _post_rekordbox_fix_paths(self) -> None:
        """Zweiter Schritt: schreibt die im Popup bestaetigten Pfad-
        Korrekturen nach master.db -- ueber dieselbe
        rekordbox.update_relocated_tracks(), die auch _post_relink() fuer
        verschobene Music.app-Dateien nutzt."""
        try:
            raw = json.loads(self._body(1_000_000).decode("utf-8") or "{}")
            moves = [{"path": str(f["new_path"]), "prior_path": str(f["old_path"])}
                     for f in raw.get("fixes") or []]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not moves:
            self._json({"ok": True, "updated": [], "not_found": [], "errors": {}})
            return

        conn = db_mod.connect(cfgmod.load())
        try:
            metadata = {m["path"]: db_mod.row_for_path(conn, m["path"]) for m in moves}
            metadata = {k: v for k, v in metadata.items() if v is not None}
        finally:
            conn.close()

        try:
            # Lief bisher ohne jeden Lock -- zwei Rekordbox-Schreibzugriffe
            # konnten sich ueberholen (siehe _rekordbox_lock).
            with self._rekordbox_lock:
                result = rekordbox_mod.update_relocated_tracks(moves, metadata)
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 500)
            return
        except Exception as exc:                      # noqa: BLE001
            self._fail(str(exc), 500)
            return
        for p in result["updated"]:
            audit_log.log("rekordbox-pfad-korrigiert", p, "ueber Abgleich-Popup")
        self._json({"ok": True, **result})

    def _post_music_added_sync(self) -> None:
        """Voller Abgleich gegen Music.app -- wie _post_rekordbox_sync darf
        ein Fehlschlag den zuletzt bekannten Stand nicht loeschen."""
        if not self._require_music(cfgmod.load()):
            return
        try:
            dates = media.music_added_dates()
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                matched = db_mod.sync_music_added(conn, dates)
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
        self._json({"ok": True, "matched": matched, "checked": total})

    def _post_pick_folder(self) -> None:
        """Nativer Ordnerauswahl-Dialog (mehrfach) fuer die Bibliotheksordner."""
        try:
            chosen = media.choose_folders("Bibliotheksordner wählen")
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "paths": chosen or []})

    def _post_pick_daw_template(self) -> None:
        """Nativer Dateiauswahl-Dialog fuer die Ableton-Vorlage in den Einstellungen."""
        try:
            chosen = media.choose_single_file("Ableton-Vorlage (.als) wählen")
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "path": chosen})

    def _post_backup_create(self) -> None:
        """
        Manuelles Backup -- Speicherort per nativem 'Speichern unter'-Dialog,
        zaehlt bewusst nicht zur automatischen Rotation in backup/.
        """
        cfg = cfgmod.load()
        default_name = time.strftime("quality-%Y%m%d-%H%M%S.db")
        try:
            dest = media.choose_save_path("Backup speichern unter", default_name,
                                           str(backup_mod.backup_dir(cfg)))
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        if dest is None:
            self._json({"ok": True, "cancelled": True})
            return
        try:
            with self.lock:
                path = backup_mod.create_backup(cfg, dest=dest)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "path": str(path)})

    def _post_backup_restore_pick(self) -> None:
        """
        Native Dateiauswahl, startet automatisch im backup/-Ordner. Der
        bisherige Stand wird vor dem Einspielen selbst weggesichert.
        """
        cfg = cfgmod.load()
        try:
            chosen = media.choose_single_file("Backup zum Wiederherstellen wählen",
                                               str(backup_mod.backup_dir(cfg)))
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        if chosen is None:
            self._json({"ok": True, "cancelled": True})
            return
        try:
            with self.lock:
                safety = backup_mod.restore_backup(chosen, cfg)
                self._rebuild(cfg)
        except (FileNotFoundError, ValueError) as exc:
            self._fail(str(exc))
            return
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "safety": str(safety) if safety else None})

    def _post_backup_folder(self) -> None:
        """Backup-Ordner im Finder oeffnen -- quality.db oder Rekordbox' master.db."""
        try:
            payload = json.loads(self._body(1_000).decode("utf-8"))
            which = str(payload.get("which", ""))
        except (ValueError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        cfg = cfgmod.load()
        if which == "db":
            folder = backup_mod.backup_dir(cfg)
        elif which == "rekordbox":
            folder = cfgmod.resolve("backup") / "rekordbox"
        else:
            self._fail("Unbekannter Backup-Ordner")
            return
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["open", str(folder)], check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            self._fail(f"Finder konnte nicht geöffnet werden: {exc}", 500)
            return
        self._json({"ok": True})

    def _post_log_folder(self) -> None:
        """Ordner des Aenderungsprotokolls im Finder oeffnen (siehe audit_log.py)."""
        folder = audit_log.log_dir(cfgmod.load())
        try:
            subprocess.run(["open", str(folder)], check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            self._fail(f"Finder konnte nicht geöffnet werden: {exc}", 500)
            return
        self._json({"ok": True})

    def _post_open_music(self) -> None:
        """Datei in Music.app oeffnen/abspielen. Fest auf Music.app verdrahtet
        (kein Programmpfad zum Starten wie beim Audio-Editor) -- cfg["external_music"]
        entscheidet nur, ob dieser Weg ueberhaupt erlaubt ist (siehe _require_music())."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if self._known_file(path) is None:
            self._reject_missing(path)
            return
        if not self._require_music(cfgmod.load()):
            return
        try:
            media.open_in_music(path)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "editor": Path(media.MUSIC_APP).stem})

    def _post_open_store(self) -> None:
        """Suche im iTunes Store öffnen — dort lassen sich Titel kaufen.

        Der eigentliche Suchtreffer kommt zwar per Web-API (siehe
        media.itunes_lookup()), das Oeffnen selbst laeuft aber immer ueber
        'open itmss://...' und aktiviert Music.app -- deshalb wie die
        anderen direkten Music.app-Aktionen hinter _require_music()."""
        if not self._require_music(cfgmod.load()):
            return
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            query = str(payload["query"]).strip()
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not query:
            self._fail("Leere Suchanfrage")
            return
        try:
            found = media.open_itunes_store(query)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, **found})

    def _post_trash(self) -> None:
        """
        Verschiebt eine Datei in den Papierkorb und raeumt ihren Eintrag ab.

        Bewusst der Papierkorb und kein echtes Loeschen: die Entscheidung
        bleibt umkehrbar, solange der Papierkorb nicht geleert wird. Die
        Bestaetigung holt die Oberflaeche vorher ein.
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row = self._known_file(path)
        if row is None:
            self._reject_missing(path)
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Ohne ausgewaehlte Music App bleibt der Abgleich unten aus,
                # auch wenn der Pfad noch aus einer aelteren Zeit in
                # 'music_added' steht (siehe _require_music()).
                in_library = bool(cfg.get("external_music")) and \
                    path in db_mod.music_added_map(conn)
            finally:
                conn.close()

        # Muss VOR move_to_trash() laufen: remove_from_music_library() liest
        # 'location' je Kandidat einzeln von der noch vorhandenen Datei --
        # nach dem Papierkorb waere das nicht mehr moeglich, die Music.app-
        # Karteileiche bliebe garantiert stehen (genau das war der Bug).
        # Titel mit Dateiname als Rueckfallebene: Music.app selbst benennt
        # taglose Importe so, siehe remove_from_music_library().
        library_error = None
        cloud_note = False
        if in_library:
            title = row["title"] or Path(path).stem
            try:
                _, cloud_note = media.remove_from_music_library(path, title)
                audit_log.log("music-entfernt", path)
            except Exception as exc:                     # noqa: BLE001
                library_error = str(exc)

        try:
            media.move_to_trash(path)
        except Exception as exc:                     # noqa: BLE001
            self._fail(f"Papierkorb: {exc}", 500)
            return
        audit_log.log("papierkorb", path)

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                conn.execute("DELETE FROM files WHERE path = ?", (path,))
                conn.execute("DELETE FROM ignored WHERE path = ?", (path,))
                conn.execute("DELETE FROM corrected WHERE path = ?", (path,))
                conn.execute("DELETE FROM waveform WHERE path = ?", (path,))
                conn.execute("DELETE FROM music_added WHERE path = ?", (path,))
                conn.commit()
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
            # Ohne Neubacken zeigt report.html die geloeschte Zeile nach dem
            # naechsten Neuladen weiter an, siehe /api/prune.
            self._rebuild(cfg)
        self._json({"ok": True, "total": total, "removed_from_music": in_library,
                     "music_error": library_error, "music_cloud_note": cloud_note})

    def _post_convert(self) -> None:
        """
        Wandelt Dateien in eines von zwei Zielformaten (MP3 320 kbit/s CBR
        oder AIFF, siehe app/convert.py) -- der Knopf "Konverter" bei
        Mehrfachauswahl. Anders als /api/rewrite bleibt der Pfad NICHT
        gleich: die neue Datei traegt eine andere Endung, landet also unter
        einem neuen, kollisionsfreien Pfad.

        Autorisierung einheitlich ueber _authorize_path() (Obermenge von
        _known_file(), deckt sowohl Haupttabellen- als auch
        Einzelpruefungs-Pfade aus _unscanned_paths ab) -- fuer beide
        Aufrufer (Haupttabelle, Einzelpruefung) derselbe Endpunkt.

        'trash_original' (Standard True, Knopf im Konverter-Popup) steuert
        zwei grundverschiedene Ablaeufe:
          - True (ersetzen): wie bisher -- Original in den Papierkorb, die
            DB-Zeile zieht per move_path() auf den neuen Pfad um, war der
            alte Pfad in Rekordbox bekannt, wird dort der Pfad korrigiert.
          - False (behalten): das Original bleibt unangetastet liegen (Datei
            UND DB-Zeile UND Rekordbox-Verknuepfung). Die konvertierte Datei
            bekommt stattdessen eine EIGENE, neue DB-Zeile (echte Ergaenzung,
            kein Umzug). Rekordbox wird in diesem Fall bewusst nicht
            angefasst: ohne einen tatsaechlichen Umzug gibt es dort nichts
            zu korrigieren, und ein automatischer Zusatz-Import waere eine
            eigene, hier nicht angefragte Aktion.

        Music.app ist die einzige Ausnahme von "nur anfassen, was vorher
        schon dort war": jede erfolgreich konvertierte Haupttabellen-Zeile
        wird IMMER in Music.app aufgenommen, unabhaengig davon, ob das
        Original dort bereits gefuehrt wurde -- bei 'ersetzen' wird ein
        vorher dort bekannter Track zusaetzlich entfernt (sonst bliebe eine
        Karteileiche auf die jetzt im Papierkorb liegende Datei zurueck),
        bei 'behalten' ist es ein reiner Zusatz-Import. Einzige Ausnahme
        davon wiederum: die Einzelpruefung (kein DB-Row, siehe unten) ruehrt
        Music.app nie an.

        Ein Track ohne DB-Zeile (Einzelpruefung) bekommt so oder so keine
        Datenbankbehandlung und keine Music.app-/Rekordbox-Anbindung -- nur
        der Papierkorb-Umzug entfaellt bei 'behalten', das Original bleibt
        als eigene, weiterhin gueltige Einzelpruefungs-Zeile bestehen.

        Antwort: {"ok": true, "results": [...]} positionsgleich zu 'paths',
        je Eintrag {path, status: "converted"|"skip"|"error", reason?,
        new_path?, row?, original_kept?, music_relinked?, music_error?,
        rekordbox?}.
        """
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            paths = [str(p) for p in payload["paths"]]
            target = str(payload["target"])
            trash_original = bool(payload.get("trash_original", True))
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        if target not in convert_mod.TARGETS:
            self._fail("Unbekanntes Zielformat")
            return

        cfg = cfgmod.load()
        results: list[dict] = []
        any_db_mutated = False

        for path in paths:
            row, ok = self._authorize_path(path)
            if not ok:
                results.append({"path": path, "status": "error", "error": "Unbekannter Pfad"})
                continue

            db_fallback = dict(row) if row is not None else None
            was_music_added = False
            was_rekordbox = False
            if row is not None:
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        was_music_added = path in db_mod.music_added_map(conn)
                        was_rekordbox = path in db_mod.rekordbox_paths(conn)
                    finally:
                        conn.close()

            try:
                outcome = convert_mod.convert_format(
                    path, target, cfg, db_fallback, trash_original=trash_original)
            except convert_mod.ConvertSkip as exc:
                results.append({"path": path, "status": "skip", "reason": exc.reason,
                                "error": str(exc)})
                continue
            except Exception as exc:                     # noqa: BLE001
                results.append({"path": path, "status": "error", "error": str(exc)})
                continue

            new_path = outcome["new_path"]
            entry: dict = {"path": path, "status": "converted", "new_path": new_path,
                           "original_kept": not trash_original}
            detail = (f"nach {target}, vorher {os.path.basename(path)}" if trash_original
                      else f"nach {target}, Original behalten ({os.path.basename(path)})")

            if row is None:
                # Einzelpruefung: keine DB-Zeile, nur die Autorisierung
                # nachziehen -- wie bei _post_rename() fuer _unscanned_paths.
                # 'row' hier ist die rohe Analyse-Zeile aus convert_format()
                # (Schluessel 'path', nicht 'p') -- der Client braucht sie,
                # um die Drop-Zeile komplett zu aktualisieren (Codec/Bitrate/
                # Format haben sich geaendert, nicht nur der Pfad). Bleibt
                # das Original erhalten, ist auch sein alter Pfad weiterhin
                # eine gueltige Einzelpruefungs-Zeile -- nur der neue kommt
                # zusaetzlich dazu, statt den alten zu ersetzen.
                if trash_original:
                    self._unscanned_paths.discard(path)
                self._unscanned_paths.add(new_path)
                audit_log.log("convert", new_path, detail)
                entry["row"] = outcome["row"]
                results.append(entry)
                continue

            any_db_mutated = True
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    if trash_original:
                        db_mod.move_path(conn, path, new_path)
                    db_mod.save(conn, [outcome["row"]])
                    if trash_original:
                        # Der von move_path() mitgezogene Waveform-/Cover-
                        # Cache ist jetzt falsch (anderes Format/andere
                        # Audiodaten), siehe /api/rewrite fuer dasselbe
                        # Vorgehen. Bei 'behalten' gibt es an new_path noch
                        # gar keinen Cache-Eintrag -- nichts zu loeschen.
                        conn.execute("DELETE FROM waveform WHERE path = ?", (new_path,))
                        conn.execute("DELETE FROM cover_cache WHERE path = ?", (new_path,))
                        conn.commit()
                    db_mod.log_event(conn, "converted", new_path, in_music=was_music_added)
                    entry["row"] = self._compact_row(conn, cfg, new_path)
                finally:
                    conn.close()
            audit_log.log("convert", new_path, detail)

            # Music.app: IMMER, unabhaengig davon, ob das Original dort
            # bekannt war -- die einzige Ausnahme von "nur anfassen, was
            # vorher schon dort war" in diesem Endpunkt (siehe Docstring).
            # 'remove' laeuft nur, wenn dort wirklich etwas zu entfernen ist
            # (sonst ein unnoetiger Leerlauf-Apple-Event); 'add' immer --
            # aber nur, wenn ueberhaupt eine Music App ausgewaehlt ist
            # (siehe _require_music()): ohne Auswahl bleibt Music.app hier
            # komplett unangetastet, auch fuer eine vorher schon dort
            # bekannte Zeile.
            if cfg.get("external_music"):
                title = row["title"] or Path(path).stem
                try:
                    if trash_original and was_music_added:
                        media.remove_from_music_library(path, title)
                    locations = media.add_to_music_library([new_path])
                    final_path = locations[0] if locations and locations[0] else new_path
                    if final_path != new_path and os.path.isfile(final_path):
                        with self.lock:
                            conn = db_mod.connect(cfg)
                            try:
                                db_mod.move_path(conn, new_path, final_path)
                                entry["row"] = self._compact_row(conn, cfg, final_path)
                            finally:
                                conn.close()
                        new_path = final_path
                        entry["new_path"] = new_path
                    with self.lock:
                        conn = db_mod.connect(cfg)
                        try:
                            db_mod.mark_music_added(conn, [new_path])
                        finally:
                            conn.close()
                    entry["music_relinked"] = True
                except Exception as exc:                     # noqa: BLE001
                    entry["music_error"] = str(exc)

            # Rekordbox-Pfadkorrektur nur, wenn tatsaechlich etwas umgezogen
            # ist -- bleibt das Original stehen, gibt es dort nichts zu
            # korrigieren, und ein automatischer Zusatz-Import in Rekordbox
            # ist eine eigene, hier nicht angefragte Aktion.
            if was_rekordbox and trash_original:
                try:
                    conn = db_mod.connect(cfg)
                    try:
                        metadata = {new_path: db_mod.row_for_path(conn, new_path)}
                    finally:
                        conn.close()
                    with self._rekordbox_lock:
                        rb_result = rekordbox_mod.update_relocated_tracks(
                            [{"path": new_path, "prior_path": path}], metadata)
                    entry["rekordbox"] = {"running": False, **rb_result}
                    for p in rb_result["updated"]:
                        audit_log.log("rekordbox-korrigiert", p, "nach Konvertierung")
                except rekordbox_mod.RekordboxRunning:
                    entry["rekordbox"] = {"running": True,
                        "moves": [{"path": new_path, "prior_path": path}]}

            results.append(entry)

        if any_db_mutated:
            self._rebuild(cfg)
        self._json({"ok": True, "results": results})

    def _post_rewrite(self) -> None:
        """
        Kodiert eine Datei auf die angegebene Bitrate neu und ersetzt sie unter
        demselben Namen. Das Original landet vorher im Papierkorb, nie geloescht.
        """
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
            kbps = int(payload["kbps"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if self._known_file(path) is None:
            self._reject_missing(path)
            return

        cfg = cfgmod.load()
        try:
            row = rewrite_mod.rewrite_bitrate(path, kbps, cfg)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        audit_log.log("reencode", path, f"auf {kbps} kbps")

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.save(conn, [row])
                conn.execute("DELETE FROM waveform WHERE path = ?", (path,))
                conn.commit()
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact})

    def _post_rewrite_drop(self) -> None:
        """Bitrate korrigieren fuer eine Einzelpruefung -- eigener Endpunkt
        statt _post_rewrite() zu erweitern: der dortige Client (Haupttabelle)
        erwartet immer das kompakte Report-Format zurueck, das ohne DB-Zeile
        nicht gebaut werden kann. Autorisierung ueber _authorize_or_relocate()
        statt _known_file() -- wie bei _post_tags() reicht ein ueber
        _authorize_path bekannt gemachter, noch nicht gescannter Pfad
        (siehe _post_open_file_pick). rewrite_bitrate() braucht ohnehin keine
        DB-Zeile, es probt die Datei frisch (siehe app/rewrite.py)."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
            kbps = int(payload["kbps"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return

        cfg = cfgmod.load()
        try:
            fresh_row = rewrite_mod.rewrite_bitrate(path, kbps, cfg)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        audit_log.log("reencode", path, f"auf {kbps} kbps")

        if row is not None:
            # Der Pfad hat zufaellig doch eine DB-Zeile (z.B. eine bereits
            # gescannte Bibliotheksdatei, ueber "Dateien oeffnen" erneut
            # geladen) -- die haelt sich sonst sowohl bei der Bitrate als
            # auch beim Cutoff einen veralteten Stand.
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.save(conn, [fresh_row])
                    conn.execute("DELETE FROM waveform WHERE path = ?", (path,))
                    conn.commit()
                finally:
                    conn.close()
                self._rebuild(cfg)

        self._json({"ok": True, "row": fresh_row})

    def _post_tags(self) -> None:
        """Titel/Interpret/Album/Albumkuenstler/Komponist/Genre/Jahr/BPM/
        Kommentar/Tracknummer/-gesamtzahl in die Datei UND die DB schreiben.
        Fuer einen noch nicht
        gescannten, aber ueber _authorize_path autorisierten Pfad (siehe
        _post_open_file_pick) nur in die Datei -- es gibt noch keine
        DB-Zeile."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return

        fields: dict = {}
        for key in ("artist", "title", "album", "album_artist", "composer",
                    "genre", "comment"):
            if key in payload:
                fields[key] = str(payload[key] or "").strip()
        if "bpm" in payload:
            try:
                fields["bpm"] = float(payload["bpm"]) if payload["bpm"] else 0.0
            except (TypeError, ValueError):
                self._fail("Ungültiger BPM-Wert")
                return
        if "year" in payload:
            try:
                fields["year"] = int(payload["year"]) if payload["year"] else 0
            except (TypeError, ValueError):
                self._fail("Ungültiges Jahr")
                return
        for key, label in (("track_no", "Tracknummer"), ("track_total", "Tracknummer (Gesamt)")):
            if key in payload:
                try:
                    fields[key] = int(payload[key]) if payload[key] else 0
                except (TypeError, ValueError):
                    self._fail(f"Ungültige {label}")
                    return
        if not fields:
            self._fail("Keine Felder angegeben")
            return

        try:
            tags_mod.write_tags(path, fields)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        audit_log.log("tags", path, ", ".join(sorted(fields)))

        if row is None:
            # Noch nicht gescannt (siehe _authorize_path) -- es gibt keine
            # DB-Zeile zum Aktualisieren, der Client pflegt seine lokale
            # Kopie (drops-Array) selbst nach.
            self._json({"ok": True, "row": None})
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.update_tags(conn, path, fields)
                db_mod.refresh_stat(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact})

    def _post_fix_tag_issues(self) -> None:
        """Sicher automatisch behebbare Auffaelligkeiten (Steuerzeichen,
        Leerraum, ID3-Versionsanhebung, Genre-Code, doppelte Frames,
        NFC-Normalisierung) beheben. Wie _post_tags(): DB-Zeile optional --
        Einzelpruefungen (Drops) haben keine."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return

        try:
            fixed_codes, written_fields = taganomaly_mod.fix_safe(path)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        if not fixed_codes:
            self._fail("Keine sicher behebbaren Probleme gefunden")
            return
        audit_log.log("auffaelligkeiten-fix", path, ", ".join(fixed_codes))

        cfg = cfgmod.load()
        new_issues = taganomaly_mod.detect(path, (row["codec_family"] if row else "") or "")

        if row is None:
            # Noch nicht gescannt -- keine DB-Zeile, der Client pflegt seine
            # lokale Kopie (drops-Array) selbst mit 'fields' nach (siehe
            # _post_tags()).
            self._json({"ok": True, "row": None, "fixed": fixed_codes,
                        "fields": written_fields, "issues": new_issues})
            return

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                if written_fields:
                    db_mod.update_tags(conn, path, written_fields)
                db_mod.update_tag_issues(conn, path, new_issues)
                db_mod.refresh_stat(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact, "fixed": fixed_codes})

    def _rename_tag_value(self, field: str, old_label: str, paths_fn, new: str,
                           media_setter) -> None:
        """
        Benennt einen Tag-Wert (Genre/Interpret/Album) fuer ALLE betroffenen
        Tracks um: Datei-Tag, DB-Zeile, und -- best-effort -- Music.app
        (falls dort importiert) und Rekordbox (falls dort bekannt). Ein
        bereits vorhandener Zielname fuehrt beide Werte zusammen, das ist
        kein Fehlerfall. 'paths_fn(conn)' ermittelt die betroffenen Pfade
        innerhalb derselben Sperre wie die Schreibvorgaenge (kein Wettlauf
        mit einem gleichzeitigen Scan). Pfade kommen aus der eigenen
        DB-Abfrage (nicht aus dem Request), brauchen deshalb keine
        _authorize_path()-Pruefung -- wie bei _post_relink()/_post_reanalyse().
        Einzelne fehlschlagende Dateien (fehlt/gesperrt) brechen den
        restlichen Vorgang nicht ab.
        """
        cfg = cfgmod.load()
        updated: list[str] = []
        failed: dict[str, str] = {}
        titles: dict[str, str] = {}
        music_map: dict[str, float] = {}
        rb_paths: set[str] = set()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                paths = paths_fn(conn)
                if not paths:
                    self._fail("Kein Track mit diesem Wert gefunden", 404)
                    return
                for path in paths:
                    row = db_mod.row_for_path(conn, path)
                    titles[path] = (row["title"] or "") if row else ""
                    try:
                        tags_mod.write_tags(path, {field: new})
                    except Exception as exc:               # noqa: BLE001
                        failed[path] = str(exc)
                        continue
                    db_mod.update_tags(conn, path, {field: new})
                    db_mod.refresh_stat(conn, path)
                    updated.append(path)
                if updated:
                    audit_log.log(f"{field}-rename", old_label,
                                  f"{new} ({len(updated)} Tracks)")
                # Ohne ausgewaehlte Music App bleibt music_map leer, auch
                # wenn Zeilen aus aelterer Zeit noch in 'music_added' stehen
                # (siehe _require_music()).
                if cfg.get("external_music"):
                    music_map = db_mod.music_added_map(conn)
                rb_paths = db_mod.rekordbox_paths(conn)
            finally:
                conn.close()
            if updated:
                self._rebuild(cfg)

        # Music.app: best-effort wie _post_cover()'s Cover-Abgleich -- schlaegt
        # es fehl, bleiben die bereits geschriebenen Dateien/DB-Zeilen gueltig,
        # nur ein weicher Hinweis statt eines Fehlers.
        music_result = {"attempted": 0, "matched": 0, "error": None}
        to_sync_music = [p for p in updated if p in music_map]
        if to_sync_music:
            music_result["attempted"] = len(to_sync_music)
            try:
                music_result["matched"] = media_setter(
                    [(p, titles.get(p, "")) for p in to_sync_music], new)
            except Exception as exc:                       # noqa: BLE001
                music_result["error"] = str(exc)

        # Rekordbox: dasselbe Muster wie _post_convert()'s Tag-Korrektur nach
        # einer Verschiebung -- update_relocated_tracks() mit gleichem
        # path/prior_path (kein echter Umzug) loest nur den Tag-Abgleich aus.
        rekordbox_result = {"running": False, "updated": [], "not_found": [], "errors": {}}
        to_sync_rb = [p for p in updated if p in rb_paths]
        if to_sync_rb:
            try:
                conn = db_mod.connect(cfg)
                try:
                    metadata = {p: db_mod.row_for_path(conn, p) for p in to_sync_rb}
                finally:
                    conn.close()
                with self._rekordbox_lock:
                    rb_result = rekordbox_mod.update_relocated_tracks(
                        [{"path": p, "prior_path": p} for p in to_sync_rb], metadata)
                rekordbox_result.update(rb_result)
                for p in rb_result["updated"]:
                    audit_log.log(f"rekordbox-{field}", p, new)
            except rekordbox_mod.RekordboxRunning:
                rekordbox_result["running"] = True

        self._json({"ok": True, "updated": len(updated), "failed": failed,
                     "music": music_result, "rekordbox": rekordbox_result})

    def _post_genre_rename(self) -> None:
        """
        Siehe _rename_tag_value(). old/new sind Genre-Werte. Weder 'old' noch
        'new' werden hier getrimmt -- beide koennen ein exakter, bereits in
        der DB stehender Wert sein (alle Zusammenfuehrungs-Vorschlaege in
        app.js schicken beide Seiten unveraendert, auch 'new': ein reiner
        Leerzeichen-Unterschied ist eine der vier Vorschlagsarten, siehe
        findMergeSuggestions() -- ein Trim wuerde alte/neue Seite hier
        faelschlich gleich machen, wenn ausgerechnet die Leerzeichen-Variante
        als Ziel gewaehlt wird). Der Client trimmt stattdessen selbst, wo
        tatsaechlich frei getippt wird (renameGroupValue()).
        """
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            old = str(payload["old"])
            new = str(payload["new"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not old.strip() or not new.strip():
            self._fail("Alter und neuer Name dürfen nicht leer sein")
            return
        if old == new:
            self._fail("Neuer Name entspricht dem alten")
            return
        self._rename_tag_value("genre", old, lambda conn: db_mod.paths_by_genre(conn, old),
                               new, media.set_tracks_genre)

    def _post_artist_rename(self) -> None:
        """Siehe _rename_tag_value() und _post_genre_rename() (kein Trim von
        'old'/'new' -- dieselbe Begruendung)."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            old = str(payload["old"])
            new = str(payload["new"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not old.strip() or not new.strip():
            self._fail("Alter und neuer Name dürfen nicht leer sein")
            return
        if old == new:
            self._fail("Neuer Name entspricht dem alten")
            return
        self._rename_tag_value("artist", old, lambda conn: db_mod.paths_by_artist(conn, old),
                               new, media.set_tracks_artist)

    def _post_album_rename(self) -> None:
        """
        Siehe _rename_tag_value() und _post_genre_rename() (kein Trim von
        'album'/'group_artist'/'new' -- dieselbe Begruendung, alle drei
        koennen exakte, bereits vorhandene Werte sein). Anders als Genre/
        Interpret ist ein Albumname allein nicht eindeutig -- 'group_artist'
        (album_artist, ersatzweise artist) grenzt auf genau die angeklickte
        Gruppe ein, siehe db.paths_by_album()/albumGroupKeyExact() in app.js.
        """
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            album = str(payload["album"])
            group_artist = str(payload.get("group_artist", ""))
            new = str(payload["new"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not album.strip() or not new.strip():
            self._fail("Alter und neuer Name dürfen nicht leer sein")
            return
        if album == new:
            self._fail("Neuer Name entspricht dem alten")
            return
        self._rename_tag_value(
            "album", album,
            lambda conn: db_mod.paths_by_album(conn, album, group_artist),
            new, media.set_tracks_album)

    def _post_merge_dismiss(self) -> None:
        """Blendet einen Zusammenfuehrungs-Vorschlag (Genre/Interpret/Album)
        dauerhaft aus oder wieder ein -- reine Sync-Mark wie
        ignored/corrected/Playlist-Zuordnungen, deshalb keine _rebuild()."""
        try:
            payload = json.loads(self._body(2_000).decode("utf-8"))
            field = str(payload["field"])
            a = str(payload["a"])
            b = str(payload["b"])
            flag = bool(payload.get("flag", True))
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if field not in ("genre", "artist", "album"):
            self._fail("Unbekanntes Feld")
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                db_mod.set_merge_dismissed(conn, field, a, b, flag)
            finally:
                conn.close()
        self._json({"ok": True})

    def _post_cover(self) -> None:
        """Ersetzt das eingebettete Cover. Pfad im Query-String (wie /api/audio),
        Bilddaten roh im Body, MIME-Typ im Content-Type-Header."""
        path = unquote(self._query().get("path", ""))
        row, ok, path = self._authorize_or_relocate(path)
        if not path or not ok:
            self._reject_missing(path)
            return
        mime = (self.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip().lower()
        if mime not in _COVER_MIMES:
            # Was hier hereinkommt, steht spaeter als MIME-Typ in der Datei und
            # geht ueber /api/cover wieder hinaus (siehe _COVER_MIMES).
            self._fail("Nicht unterstütztes Bildformat: " + (mime or "ohne Angabe"))
            return
        try:
            data = self._body(20_000_000)
        except ValueError as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        # Ab hier zaehlen die Kopfbytes, nicht der Content-Type: der ist nur
        # eine Behauptung des Aufrufers. Im Browser kommt er aus File.type,
        # also aus dessen eigener Vermutung -- bei einer Datei ohne erkennbaren
        # Typ schickt der Client "image/jpeg", auch wenn eine PNG drinsteckt.
        # Deshalb nicht auf Gleichheit pruefen (das wuerde gueltige Bilder
        # abweisen), sondern den erkannten Typ uebernehmen. Zurueckgewiesen
        # wird nur, was ueberhaupt kein bekanntes Bild ist.
        mime = _sniff_image_mime(data)
        if not mime:
            self._fail("Die Daten sind kein Bild in einem unterstützten Format "
                       "(JPEG, PNG, GIF, WebP, AVIF oder BMP).")
            return

        try:
            tags_mod.write_cover(path, data, mime)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        audit_log.log("cover", path)

        if row is None:
            self._json({"ok": True, "row": None})
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Ohne ausgewaehlte Music App bleibt der Abgleich unten aus,
                # auch fuer einen Pfad, der noch aus aelterer Zeit in
                # 'music_added' steht (siehe _require_music()).
                in_library = bool(cfg.get("external_music")) and \
                    path in db_mod.music_added_map(conn)
            finally:
                conn.close()

        # Best-effort wie _post_trash()'s Music.app-Abgleich: schlaegt das
        # Setzen in Music.app fehl, bleibt die bereits geschriebene Datei
        # trotzdem gueltig -- nur ein weicher Hinweis statt eines Fehlers.
        music_cover_synced = None
        music_cover_error = None
        if in_library:
            title = row["title"] or Path(path).stem
            try:
                matched = media.set_track_artwork(path, title, data, mime)
                if matched:
                    music_cover_synced = True
                    audit_log.log("music-cover", path)
                else:
                    music_cover_synced = False
                    music_cover_error = "Kein passender Track in der Music.app-Bibliothek gefunden."
            except Exception as exc:                     # noqa: BLE001
                music_cover_synced = False
                music_cover_error = str(exc)

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.set_has_cover(conn, path, True)
                db_mod.refresh_stat(conn, path)
                db_mod.delete_cached_cover(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact,
                     "music_cover_synced": music_cover_synced,
                     "music_cover_error": music_cover_error})

    def _post_cover_delete(self) -> None:
        """Entfernt das eingebettete Cover. Pfad im Query-String (wie /api/cover)."""
        path = unquote(self._query().get("path", ""))
        row, ok, path = self._authorize_or_relocate(path)
        if not path or not ok:
            self._reject_missing(path)
            return

        try:
            tags_mod.delete_cover(path)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        audit_log.log("cover-loeschen", path)

        if row is None:
            self._json({"ok": True, "row": None})
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Ohne ausgewaehlte Music App bleibt der Abgleich unten aus,
                # auch fuer einen Pfad, der noch aus aelterer Zeit in
                # 'music_added' steht (siehe _require_music()).
                in_library = bool(cfg.get("external_music")) and \
                    path in db_mod.music_added_map(conn)
            finally:
                conn.close()

        music_cover_synced = None
        music_cover_error = None
        if in_library:
            title = row["title"] or Path(path).stem
            try:
                matched = media.clear_track_artwork(path, title)
                if matched:
                    music_cover_synced = True
                    audit_log.log("music-cover-loeschen", path)
                else:
                    music_cover_synced = False
                    music_cover_error = "Kein passender Track in der Music.app-Bibliothek gefunden."
            except Exception as exc:                     # noqa: BLE001
                music_cover_synced = False
                music_cover_error = str(exc)

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.set_has_cover(conn, path, False)
                db_mod.refresh_stat(conn, path)
                db_mod.delete_cached_cover(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact,
                     "music_cover_synced": music_cover_synced,
                     "music_cover_error": music_cover_error})

    def _post_lookup(self) -> None:
        """Online-Metadatenvorschläge -- rein manuell angestossen, nichts wird
        gespeichert. Netzwerkfehler duerfen diesen Endpunkt nie zum Absturz
        bringen, lookup_mod faengt alles selbst ab."""
        try:
            payload = json.loads(self._body(20_000).decode("utf-8"))
            artist = str(payload.get("artist") or "").strip()
            title = str(payload.get("title") or "").strip()
            query = str(payload.get("query") or "").strip()
        except (ValueError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not artist and not title and not query:
            self._fail("Interpret, Titel oder Suchbegriff angeben")
            return
        results = lookup_mod.search(artist, title, query)
        self._json({"ok": True, "results": results})

    def _post_lookup_cover(self) -> None:
        """Uebernimmt nur das Cover eines Online-Vorschlags, ohne die
        uebrigen Felder anzufassen -- Pfad und Bild-URL kommen im Body,
        anders als /api/cover (Bilddaten roh im Body) gibt es hier nur die
        URL, das eigentliche Laden passiert serverseitig (lookup_mod)."""
        try:
            payload = json.loads(self._body(4_000).decode("utf-8"))
            path = str(payload.get("path") or "")
            url = str(payload.get("url") or "")
        except (ValueError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not path or not ok:
            self._reject_missing(path)
            return
        if not url:
            self._fail("Keine Cover-URL angegeben")
            return

        fetched = lookup_mod.fetch_cover(url)
        if fetched is None:
            self._fail("Cover konnte nicht geladen werden", 502)
            return
        data, mime = fetched

        try:
            tags_mod.write_cover(path, data, mime)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        audit_log.log("cover", path, "aus Online-Vorschlag")

        if row is None:
            self._json({"ok": True, "row": None})
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Ohne ausgewaehlte Music App bleibt der Abgleich unten aus,
                # auch fuer einen Pfad, der noch aus aelterer Zeit in
                # 'music_added' steht (siehe _require_music()).
                in_library = bool(cfg.get("external_music")) and \
                    path in db_mod.music_added_map(conn)
            finally:
                conn.close()

        music_cover_synced = None
        music_cover_error = None
        if in_library:
            title = row["title"] or Path(path).stem
            try:
                matched = media.set_track_artwork(path, title, data, mime)
                if matched:
                    music_cover_synced = True
                    audit_log.log("music-cover", path)
                else:
                    music_cover_synced = False
                    music_cover_error = "Kein passender Track in der Music.app-Bibliothek gefunden."
            except Exception as exc:                     # noqa: BLE001
                music_cover_synced = False
                music_cover_error = str(exc)

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.set_has_cover(conn, path, True)
                db_mod.refresh_stat(conn, path)
                db_mod.delete_cached_cover(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed)[0]
        self._json({"ok": True, "row": compact,
                     "music_cover_synced": music_cover_synced,
                     "music_cover_error": music_cover_error})

    def _post_reanalyse(self) -> None:
        """
        Misst bereits bekannte Dateien noch einmal, auch wenn sich Groesse und
        Datum seit dem letzten Lauf nicht geaendert haben — der Cache wird
        also bewusst uebergangen. Die Dateien selbst bleiben unangetastet;
        neu geschrieben wird nur das gespeicherte Messergebnis.

        Gleicht ausserdem, je Datei, Cover (unabhaengig von
        cfg["cover_auto_fill"]), Music.app-"date added" und — sofern
        Rekordbox gerade nicht laeuft — die Rekordbox-Praesenz live ab
        (media.music_added_date_for()/rekordbox.content_present(), beide
        schreiben nur den einen betroffenen Pfad, siehe db.set_music_added()/
        set_rekordbox_present()). Laeuft Rekordbox, wird der Rekordbox-Check
        uebersprungen und "rekordbox.running": true zurueckgegeben, damit der
        Client einen Hinweis zeigt statt den Cache stillschweigend veralten
        zu lassen.
        """
        try:
            payload = json.loads(self._body(4_000_000).decode("utf-8"))
            raw = payload.get("paths")
            if raw is None:
                raw = [payload["path"]]
            paths = [str(p) for p in raw]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return
        # Wie ueberall: nur Pfade, die in der Datenbank stehen.
        for p in paths:
            if self._known_file(p) is None:
                self._reject_missing(p)
                return

        cfg = cfgmod.load()
        # OHNE Lock: run_scan() startet je Datei ffprobe und zwei
        # ffmpeg-Durchgaenge -- an echtem Material rund 2 Sekunden JE DATEI.
        # Unter dem Lock stand solange die gesamte Oberflaeche still, auch
        # fuer alles Lesende; eine Neumessung von 20 Tracks hat sie rund 45
        # Sekunden blockiert. Dass dabei parallel in die Datenbank geschrieben
        # wird, ist kein neuer Fall: der grosse Scan ueber /api/scan laeuft
        # ohnehin in einem eigenen Thread ohne diesen Lock, und WAL plus
        # busy_timeout (siehe db.connect) sind genau dafuer eingestellt.
        try:
            # force=True heisst: nicht aus dem Cache antworten, wirklich messen.
            jobs.run_scan(cfg, paths=paths, force=True)
        except Exception as exc:                     # noqa: BLE001
            self._fail(f"Analyse fehlgeschlagen: {exc}", 500)
            return

        # Music.app und Rekordbox ebenfalls ausserhalb des Locks befragen --
        # beides sind Fremdsysteme (AppleScript startet notfalls Music.app,
        # Rekordbox liegt in einer SQLCipher-Datei). Erst die Antworten
        # einsammeln, dann weiter unten in einem kurzen Abschnitt schreiben.
        added_dates: dict[str, float | None] = {}
        rekordbox_present: dict[str, bool] = {}
        rekordbox_running = False
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                vorab = [row for row in (db_mod.row_for_path(conn, p) for p in paths)
                         if row is not None]
            finally:
                conn.close()
        if cfg.get("external_music"):
            for row in vorab:
                try:
                    added_dates[row["path"]] = media.music_added_date_for(
                        row["path"], row["title"])
                except Exception:                      # noqa: BLE001
                    added_dates[row["path"]] = None
        # Rekordbox: Praesenz-Check nur, wenn Rekordbox eingestellt/gefunden
        # ist (siehe _rekordbox_available()) UND nicht gerade laeuft
        # (laufendes Rekordbox riskiert eine beschaedigte master.db, siehe
        # rekordbox.py). Laeuft es, bleibt der Cache unveraendert und die
        # Antwort meldet running=True, damit der Client einen Hinweis-Toast
        # zeigt statt den Cache stillschweigend veraltet zu lassen.
        if self._rekordbox_available(cfg):
            rekordbox_running = rekordbox_mod.is_running()
            if not rekordbox_running:
                for row in vorab:
                    rekordbox_present[row["path"]] = rekordbox_mod.content_present(
                        row["path"])

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Cover: immer neu abgleichen, unabhaengig von
                # cfg["cover_auto_fill"] -- das ist eine bewusste, vom
                # Nutzer ausgeloeste Vollpruefung, kein automatischer
                # Hintergrund-Scan, fuer den die Einstellung gedacht ist.
                try:
                    coverfill.fill_missing_covers(conn, cfg, paths, "Manuelle Analyse")
                    coverfill.refresh_cached_covers(conn, cfg, "Manuelle Analyse",
                                                    only_paths=paths)
                except Exception:                      # noqa: BLE001
                    pass    # Analyse ist schon gelungen, Cover-Abgleich ist best effort

                # Frisch lesen: run_scan() und der Cover-Abgleich oben haben
                # die Zeilen inzwischen geschrieben, 'vorab' ist der Stand
                # davor und taugt nur zum Einsammeln der Pfade.
                rows = [row for row in (db_mod.row_for_path(conn, p) for p in paths)
                        if row is not None]
                # Die oben ausserhalb des Locks eingesammelten Antworten der
                # Fremdsysteme jetzt schreiben -- reine Datenbankarbeit.
                for pfad, added_ts in added_dates.items():
                    db_mod.set_music_added(conn, pfad, added_ts)
                for pfad, present in rekordbox_present.items():
                    db_mod.set_rekordbox_present(conn, pfad, present)

                marks = self._mark_sets(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(rows, cfg, *marks)
        self._json({"ok": True, "rows": compact, "done": len(compact),
                    "rekordbox": {"running": rekordbox_running}})

    def _post_relink(self) -> None:
        """Sucht Dateien, die nicht mehr am gespeicherten Pfad liegen, an ihrem
        neuen Ort und schreibt die Zeile darauf um.

        Anlass: Music.app raeumt seinen Medienordner nach Tags auf. Wer die
        Tags hier aendert und den Track danach in Music.app abspielt, findet
        ihn anschliessend unter Interpret/Album wieder -- neuer Ordner, neuer
        Dateiname. Der Track ist dann in dieser Liste "Datei fehlt", obwohl er
        noch da ist.

        Ohne 'paths' werden alle Zeilen geprueft, deren Datei fehlt; mit
        'paths' nur diese. Verschoben wird nichts, nur die Datenbank
        nachgezogen (db.move_path()) -- wie nach einem Music-Import.

        'rekordbox_retry' (optional): [{"path", "prior_path"}, ...] aus einer
        vorherigen Antwort, deren Rekordbox-Korrektur an einem laufenden
        Rekordbox gescheitert ist (siehe unten) -- der Client haelt diese
        Liste nur im Speicher und schickt sie beim naechsten Klick auf
        "Auto-Relocate" erneut mit, auch wenn dann keine Datei mehr fehlt
        (der DB-Move war ja schon erfolgreich, nur der Rekordbox-Push nicht).
        """
        try:
            raw = json.loads(self._body(1_000_000).decode("utf-8") or "{}")
            wanted = [str(p) for p in raw.get("paths") or []]
            retry_moves = [
                {"path": str(m["path"]), "prior_path": str(m.get("prior_path") or m["path"])}
                for m in raw.get("rekordbox_retry") or []]
        except (ValueError, TypeError, KeyError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                known = {r["path"] for r in conn.execute("SELECT path FROM files")}
                # Vor jeder Verschiebung erfassen: move_path() schreibt diese
                # Cache-Tabelle beim eigentlichen Move bereits auf den neuen
                # Pfad um, danach liesse sich "war der ALTE Pfad schon in
                # Rekordbox bekannt" nicht mehr abfragen.
                rekordbox_before = db_mod.rekordbox_paths(conn)
                sought = wanted or sorted(known)
                missing = []
                for path in sought:
                    if path in known and not os.path.isfile(path):
                        row = db_mod.row_for_path(conn, path)
                        if row is not None:
                            missing.append({
                                "path": path, "size": row["size"],
                                "duration_s": row["duration_s"],
                                "artist": row["artist"], "title": row["title"],
                            })
            finally:
                conn.close()

        # Kein fruehes Zurueckkehren mehr, wenn nichts fehlt: ein
        # 'rekordbox_retry' muss auch dann verarbeitet werden (siehe
        # Docstring oben).
        found = {"matches": {}, "ambiguous": {}, "checked": 0, "truncated": False}
        relinked: list[dict] = []
        if missing:
            try:
                found = scanner.find_moved(cfg, missing, known)
            except Exception as exc:                     # noqa: BLE001
                self._fail(f"Suche fehlgeschlagen: {exc}", 500)
                return

            if found["matches"]:
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        # Erst alle Verschiebungen, dann die Zeilen dazu in
                        # EINEM Aufruf -- move_path() schreibt genau in die
                        # Tabellen, die _compact_rows() liest (siehe dort und
                        # in _post_add_to_library).
                        verschoben = []
                        for old_path, new_path in found["matches"].items():
                            if db_mod.move_path(conn, old_path, new_path):
                                audit_log.log("verschoben", new_path, f"vorher {old_path}")
                                verschoben.append((old_path, new_path))
                        if verschoben:
                            gebaut = self._compact_rows(
                                conn, cfg, [neu for _, neu in verschoben])
                            relinked.extend(
                                {"old": alt_p, "new": neu_p, "row": row}
                                for (alt_p, neu_p), row in zip(verschoben, gebaut))
                    finally:
                        conn.close()
                    if relinked:
                        self._rebuild(cfg)

        matched = set(found["matches"])
        unmatched = [m["path"] for m in missing
                     if m["path"] not in matched and m["path"] not in found["ambiguous"]]

        # Tracks, die schon in Rekordbox bekannt waren (vor dem Move) UND
        # gerade verschoben wurden, plus ein Retry aus einem frueheren
        # Fehlschlag -- fuer beide muss Rekordbox' eigene DjmdContent-Zeile
        # (FolderPath + Tags) nachgezogen werden, siehe rekordbox.py.
        rekordbox_moves = [
            {"path": r["new"], "prior_path": r["old"]}
            for r in relinked if r["old"] in rekordbox_before
        ] + retry_moves

        rekordbox_result = None
        if rekordbox_moves:
            conn = db_mod.connect(cfg)
            try:
                metadata = {m["path"]: db_mod.row_for_path(conn, m["path"])
                            for m in rekordbox_moves}
                metadata = {k: v for k, v in metadata.items() if v is not None}
            finally:
                conn.close()
            try:
                with self._rekordbox_lock:
                    result = rekordbox_mod.update_relocated_tracks(rekordbox_moves, metadata)
                rekordbox_result = {"running": False, **result}
                for p in result["updated"]:
                    audit_log.log("rekordbox-korrigiert", p, "nach Verschiebung")
            except rekordbox_mod.RekordboxRunning:
                rekordbox_result = {"running": True, "moves": rekordbox_moves}

        self._json({"ok": True, "relinked": relinked,
                    "ambiguous": [{"old": k, "candidates": v}
                                  for k, v in found["ambiguous"].items()],
                    "unmatched": unmatched, "checked": found["checked"],
                    "truncated": found["truncated"], "missing": len(missing),
                    "rekordbox": rekordbox_result})

    def _post_prune(self) -> None:
        """
        Entfernt Datenbankeintraege zu geloeschten Dateien. Loescht selbst
        nichts auf der Platte — die Dateien sind bereits weg.
        """
        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                existing = {r["path"] for r in conn.execute("SELECT path FROM files")
                            if os.path.isfile(r["path"])}
                gone = db_mod.prune_missing(conn, existing)
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
            # Ohne das liefert /report.html weiter den alten Stand: die gerade
            # entfernten Zeilen staenden nach "Seite neu laden" wieder da,
            # samt gelber Hinweisleiste -- das Aufraeumen sah dann wirkungslos
            # aus, obwohl die DB laengst stimmte. Jeder andere zeilen-
            # aendernde Endpunkt backt ebenfalls neu.
            if gone:
                self._rebuild(cfg)
        self._json({"ok": True, "removed": len(gone), "paths": gone, "total": total})

    def _post_orphan_cleanup(self) -> None:
        """
        Sucht in den konfigurierten Bibliotheksordnern nach leeren
        Unterordnern (leer zaehlt auch mit nichts als .DS_Store darin) und
        verschiebt sie in den Papierkorb -- nur auf Knopfdruck in den
        Einstellungen, keine automatische Aufraeumaktion nach einem Scan.
        Ruehrt keine DB-Zeilen an: ein leerer Ordner enthaelt per Definition
        keine Audiodatei, also auch keinen erfassten Track.
        """
        cfg = cfgmod.load()
        found: list[str] = []
        for root in cfg.get("library_paths") or []:
            found.extend(media.find_empty_folders(root))
        removed: list[str] = []
        errors: list[dict] = []
        for p in found:
            try:
                media.move_to_trash(p)
                removed.append(p)
            except Exception as exc:                     # noqa: BLE001
                errors.append({"path": p, "error": str(exc)})
        self._json({"ok": True, "found": len(found), "removed": removed, "errors": errors})


    def _rebuild(self, cfg: dict) -> None:
        """
        Report als neu zu backen VORMERKEN — die eingebetteten Daten sind
        sonst veraltet.

        Kehrt sofort zurueck. Gebacken wird gebuendelt im Hintergrund (siehe
        _ReportBuilder) und spaetestens beim naechsten Ausliefern der Seite
        (_serve_report -> REPORT.flush()). Der Aufrufer darf sich also NICHT
        darauf verlassen, dass data/report.html nach dieser Zeile bereits
        neu auf der Platte liegt -- wer den Stand wirklich braucht (Test,
        Herunterfahren, CLI), ruft REPORT.flush().

        Jeder Endpunkt, der Zeilen aendert oder loescht, kommt hier durch
        (siehe CLAUDE.md). Das ist damit die eine Stelle, an der die
        gemerkten Pfad-Freigaben (_authorize_fast) verfallen muessen --
        umbenannt, verschoben, in den Papierkorb, aus der DB entfernt. Das
        bleibt synchron: eine veraltete Freigabe waere ein Sicherheits-
        problem, kein Darstellungsfehler, und darf nicht auf einen
        Hintergrund-Thread warten.
        """
        _auth_cache.clear()
        REPORT.mark_dirty(cfg)

    def _reclassify(self, cfg: dict) -> dict:
        conn = db_mod.connect(cfg)
        try:
            return classify_mod.reclassify_all(conn, cfg)
        finally:
            conn.close()

    def _post_settings(self) -> None:
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            cfg = settings_mod.save(payload)
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return

        with self.lock:
            stats = self._reclassify(cfg)
            self._rebuild(cfg)
        self._json({"ok": True, "values": settings_mod.current(cfg), **stats})

    def _post_shops(self) -> None:
        try:
            payload = json.loads(self._body(200_000).decode("utf-8"))
            shops = settings_mod.save_shops(payload.get("shops") or [])
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, "shops": shops})

    def _post_playlist(self) -> None:
        """Einen Baumknoten anlegen, aendern, verschieben oder loeschen.

        Bewusst OHNE _rebuild(): der Client zieht den Baum live ueber
        /api/playlists nach (siehe _get_playlists()).
        """
        try:
            payload = json.loads(self._body(200_000).decode("utf-8"))
            op = str(payload.get("op") or "")
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if op not in ("create", "update", "move", "delete", "reorder"):
            self._fail("Unbekannte Aktion")
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                node = self._playlist_apply(conn, op, payload)
            except ValueError as exc:
                self._fail(str(exc))
                return
            finally:
                conn.close()
        self._json({"ok": True, **node})

    # Erlaubte Farbtoken. Bewusst KEIN freier Hex-Wert: nur so bleibt der
    # Kontrast in beiden Themes garantiert, ohne dass die Oberflaeche etwas
    # ueber das Theme wissen muesste. Zwei Gruppen, beide als CSS-Variablen
    # in app.css hinterlegt: die 12 Playlist-Farben (--fl1..--fl12, auch fuer
    # als Merkliste markierte Playlisten) und die 5 Statusfarben, die im
    # Tool ohnehin schon fuer die Verdikt-Abzeichen benutzt werden (--ok
    # gruen, --susp orange, --fake rot, --unk grau, --accent blau). Letztere
    # tragen die festen Status-Listen (db._SYSTEM_PLAYLISTS), damit Baum und
    # Abzeichen in der Tabelle dieselbe Farbe zeigen.
    _PLAYLIST_COLORS = ({f"fl{i}" for i in range(1, 13)}
                        | {"ok", "susp", "fake", "unk", "accent"})

    # Nachfolger der frueheren 3 fest benannten Merklisten-Slots -- jetzt
    # eine beliebige normale Playlist, aber weiterhin gedeckelt, damit
    # Schnell-Filter-Tabs und Zeilen-Knoepfe nicht unbegrenzt wachsen.
    _MAX_FAV_SLOTS = 4

    @classmethod
    def _resolve_fav_slot(cls, conn, node_id: str, current_slot, merkliste: bool):
        """Naechsten freien Merklisten-Slot (1..4) vergeben bzw. wieder
        freigeben. Wirft ValueError, wenn bereits alle Slots belegt sind."""
        if not merkliste:
            return None
        if current_slot:
            return current_slot
        used = {r["fav_slot"] for r in conn.execute(
            "SELECT fav_slot FROM playlists WHERE fav_slot IS NOT NULL AND id != ?",
            (node_id,))}
        for slot in range(1, cls._MAX_FAV_SLOTS + 1):
            if slot not in used:
                return slot
        raise ValueError(f"Es sind bereits {cls._MAX_FAV_SLOTS} Merklisten markiert.")

    @staticmethod
    def _clean_playlist_rules(raw):
        """Regelwerk einer Smart Playlist pruefen. Gespeichert wird JSON-Text,
        ausgewertet wird ausschliesslich im Client (die Zeilen liegen dort
        ohnehin vollstaendig vor). Hier wird deshalb nur die Form geprueft --
        kein Feld- oder Operatornamen-Abgleich, der sonst bei jeder neuen
        Regelart an zwei Stellen gepflegt werden muesste."""
        if raw in (None, ""):
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"Regelwerk nicht lesbar: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Regelwerk muss ein Objekt sein.")
        rules = raw.get("rules")
        if not isinstance(rules, list) or len(rules) > 50:
            raise ValueError("Regelwerk braucht bis zu 50 Regeln.")
        text = json.dumps(raw, ensure_ascii=False)
        if len(text) > 20_000:
            raise ValueError("Regelwerk ist zu gross.")
        return text

    @staticmethod
    def _clean_playlist_style(value: dict, key: str):
        """Symbol bzw. Farbe aus einer Anfrage saeubern. Leer heisst
        ausdruecklich 'keine Angabe' (Vorgabe), nicht 'ungueltig'."""
        raw = value.get(key)
        if raw in (None, ""):
            return None
        text = str(raw)
        if key == "color":
            return text if text in _Handler._PLAYLIST_COLORS else None
        # "sym:<lucide-name>" (laengster bekannter Name ca. 30 Zeichen) oder
        # "emoji:<zeichen>" -- 4 Zeichen (Stand der fruehren Einzelzeichen-
        # Icons) haette hier "sym:bot" zu "sym:" verstuemmelt und jedes
        # Lucide-Icon unbemerkt verworfen.
        return text[:48]

    def _playlist_apply(self, conn, op: str, payload: dict) -> dict:
        """Der eigentliche Schreibvorgang -- ausgelagert, damit _post_playlist()
        nur noch Transport und Fehlerbehandlung macht. Wirft ValueError mit
        einem fuer den Nutzer lesbaren Text."""
        if op == "create":
            name = str(payload.get("name") or "").strip()[:80]
            if not name:
                raise ValueError("Die Liste braucht einen Namen.")
            kind = str(payload.get("kind") or "playlist")
            if kind not in db_mod.PLAYLIST_KINDS:
                raise ValueError("Unbekannte Listenart")
            parent = payload.get("parent_id") or None
            if parent and db_mod.playlist_by_id(conn, parent) is None:
                raise ValueError("Der Zielordner existiert nicht mehr.")
            # Undurchsichtige, zufaellige id statt eines aus dem Namen
            # abgeleiteten Slugs (wie bei den Shops): Playlisten werden oft
            # umbenannt, und eine id, die dann nicht mehr zum Namen passt,
            # waere in Datenbank und Protokollen irrefuehrend.
            pid = "pl" + uuid.uuid4().hex[:10]
            fav_slot = None
            if "merkliste" in payload:
                if kind != "playlist":
                    raise ValueError("Nur normale Playlisten lassen sich als Merkliste markieren.")
                fav_slot = self._resolve_fav_slot(conn, pid, None, bool(payload["merkliste"]))
            node = db_mod.save_playlist(conn, {
                "id": pid,
                "parent_id": parent, "kind": kind, "name": name,
                "icon": self._clean_playlist_style(payload, "icon"),
                "color": self._clean_playlist_style(payload, "color"),
                "rules": self._clean_playlist_rules(payload.get("rules")),
                "seq": int(payload.get("seq") or 0),
                "fav_slot": fav_slot,
            })
            return {"node": node}

        if op == "reorder":
            # Geschwister einer Ebene komplett neu durchnummerieren. Einzelne
            # seq-Werte zu setzen waere fehleranfaellig (Luecken, Dubletten);
            # der Client kennt die gewuenschte Reihenfolge ohnehin vollstaendig.
            parent = payload.get("parent_id") or None
            ids = [str(x) for x in (payload.get("ids") or [])]
            for seq, pid in enumerate(ids):
                node = db_mod.playlist_by_id(conn, pid)
                if node is None or (node.get("parent_id") or None) != parent:
                    continue          # zwischenzeitlich verschoben oder weg
                node["seq"] = seq
                db_mod.save_playlist(conn, node)
            return {"reordered": len(ids)}

        node_id = str(payload.get("id") or "")
        current = db_mod.playlist_by_id(conn, node_id)
        if current is None:
            raise ValueError("Diese Liste existiert nicht mehr.")
        # Feste Listen: Name, Symbol, Farbe und Regelwerk gehoeren uns (sie
        # werden bei jedem Start aus db._SYSTEM_PLAYLISTS neu gesetzt), und
        # geloescht werden koennen sie nicht. Ihre POSITION im Baum gehoert
        # dagegen dem Nutzer -- 'move' und 'reorder' sind deshalb ausdruecklich
        # erlaubt (reorder laeuft ohnehin schon oben durch).
        if current.get("system") and op in ("update", "delete"):
            raise ValueError("Feste Listen lassen sich nicht umbenennen oder löschen.")

        if op == "delete":
            return {"deleted": db_mod.delete_playlist(conn, node_id)}

        if op == "move":
            parent = payload.get("parent_id") or None
            if parent and db_mod.playlist_by_id(conn, parent) is None:
                raise ValueError("Der Zielordner existiert nicht mehr.")
            if not db_mod.playlist_can_reparent(conn, node_id, parent):
                raise ValueError("Ein Ordner kann nicht in sich selbst liegen.")
            current["parent_id"] = parent
            current["seq"] = int(payload.get("seq") or 0)
            return {"node": db_mod.save_playlist(conn, current)}

        # op == "update": nur die mitgeschickten Felder anfassen, damit ein
        # reines Umbenennen Farbe/Symbol/Regeln nicht stillschweigend leert.
        for key in ("name", "icon", "color", "rules"):
            if key not in payload:
                continue
            if key in ("icon", "color"):
                current[key] = self._clean_playlist_style(payload, key)
            elif key == "rules":
                current[key] = self._clean_playlist_rules(payload[key])
            else:
                current[key] = payload[key]
        if "merkliste" in payload:
            if current["kind"] != "playlist":
                raise ValueError("Nur normale Playlisten lassen sich als Merkliste markieren.")
            current["fav_slot"] = self._resolve_fav_slot(
                conn, node_id, current.get("fav_slot"), bool(payload["merkliste"]))
        current["name"] = str(current.get("name") or "").strip()[:80]
        if not current["name"]:
            raise ValueError("Die Liste braucht einen Namen.")
        return {"node": db_mod.save_playlist(conn, current)}

    def _post_playlist_items(self) -> None:
        """Tracks einer Playlist zuordnen, entfernen oder komplett neu setzen.

        'set' traegt die ganze Reihenfolge -- das ist der Weg fuer Umsortieren
        und fuer das Zuruecknehmen einer Aenderung. Pfade werden wie ueberall
        gegen die Datenbank geprueft; unbekannte werden gemeldet statt still
        uebergangen, sonst bliebe ein Fehlgriff im Client unsichtbar.
        """
        try:
            payload = json.loads(self._body(5_000_000).decode("utf-8"))
            op = str(payload.get("op") or "")
            node_id = str(payload.get("id") or "")
            paths = [str(x) for x in (payload.get("paths") or [])]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if op not in ("add", "remove", "set"):
            self._fail("Unbekannte Aktion")
            return

        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                node = db_mod.playlist_by_id(conn, node_id)
                if node is None:
                    self._fail("Diese Liste existiert nicht mehr.")
                    return
                if node["kind"] != "playlist":
                    self._fail("Nur reguläre Playlisten nehmen Tracks auf.")
                    return
                # Beim Entfernen nicht gegen 'files' pruefen: ein Eintrag darf
                # auch dann heraus, wenn die Datei inzwischen weg ist.
                unknown: list[str] = []
                if op in ("add", "set"):
                    known = []
                    for path in paths:
                        if db_mod.row_for_path(conn, path) is None:
                            unknown.append(path)
                        else:
                            known.append(path)
                    paths = known
                if op == "add":
                    changed = db_mod.add_playlist_items(conn, node_id, paths)
                elif op == "remove":
                    changed = db_mod.remove_playlist_items(conn, node_id, paths)
                else:
                    changed = db_mod.set_playlist_items(conn, node_id, paths)
                items = db_mod.playlist_items_map(conn).get(node_id, [])
            finally:
                conn.close()
        self._json({"ok": True, "id": node_id, "changed": changed,
                    "items": items, "unknown": unknown})

    def _post_columns(self) -> None:
        """Standard-Spalten einer Ansicht (Bearbeiten/Player) speichern --
        Reihenfolge, ausgeblendete Spalten und Breiten. Sie gelten fuer jede
        Liste ohne eigene Zuordnung (siehe _post_column_assign())."""
        try:
            payload = json.loads(self._body(20_000).decode("utf-8"))
            layout = payload.get("layout") or "edit"
            # Anders als die uebrigen Einstellungen kommen Spalten-Speicherungen
            # unbeaufsichtigt und schnell hintereinander (jedes Loslassen des
            # Ziehgriffs, jedes Haekchen) -- ohne Schloss koennten sich zwei
            # Lese-Aendere-Schreibe-Folgen auf config.local.yaml ueberholen.
            with self.lock:
                saved = settings_mod.save_columns(
                    layout, payload.get("order") or [], payload.get("hidden") or [],
                    payload.get("widths") or {})
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, "layout": layout, **saved})

    def _post_drop_columns(self) -> None:
        """Spalten der Einzelpruefungen speichern -- unabhaengig von
        _post_columns()/den Ansichten der Haupttabelle, siehe
        settings.save_drop_columns()."""
        try:
            payload = json.loads(self._body(20_000).decode("utf-8"))
            with self.lock:
                saved = settings_mod.save_drop_columns(
                    payload.get("order") or [], payload.get("hidden") or [],
                    payload.get("widths") or {})
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, **saved})

    def _post_column_views(self) -> None:
        """Die gespeicherten Spaltenansichten am Stueck ersetzen -- anlegen,
        umbenennen, loeschen und jede Aenderung an einer aktiven Ansicht laufen
        ueber denselben Weg. Der Client haelt die vollstaendige Liste ohnehin
        im Speicher; ein Einzelsatz-Protokoll braeuchte hier nur mehr
        Zustaende, die auseinanderlaufen koennen."""
        try:
            payload = json.loads(self._body(200_000).decode("utf-8"))
            with self.lock:
                saved = settings_mod.save_column_views(
                    payload.get("column_views") or [],
                    # Nicht mitgeschickt = unveraendert lassen (Spalten-Menue),
                    # mitgeschickt = setzen (Einstellungs-Dialog).
                    payload.get("default") if "default" in payload else None,
                    payload.get("music_default") if "music_default" in payload else None)
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, **saved})

    def _post_column_assign(self) -> None:
        """Einer Liste eine gespeicherte Spaltenansicht zuordnen (leere 'id' =
        zurueck auf die Standard-Spalten)."""
        try:
            payload = json.loads(self._body(20_000).decode("utf-8"))
            with self.lock:
                assign = settings_mod.save_column_assign(
                    payload.get("view") or "", payload.get("id") or "")
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, "column_view_assign": assign})

    def _post_settings_reset(self) -> None:
        try:
            payload = json.loads(self._body(100_000).decode("utf-8") or "{}")
            cfg = settings_mod.reset(payload.get("group"))
        except ValueError as exc:
            self._fail(str(exc))
            return
        with self.lock:
            stats = self._reclassify(cfg)
            self._rebuild(cfg)
        self._json({"ok": True, "values": settings_mod.current(cfg),
                    "shops": cfg.get("shops", []), **stats})

    def _post_quit(self) -> None:
        """
        Server sauber beenden -- der einzige Weg dorthin, wenn die App per
        Doppelklick statt aus dem Terminal gestartet wurde (kein Strg+C).

        Ein laufender Scan wird nicht stillschweigend abgeschnitten: dann
        erst nach ausdruecklicher Bestaetigung (force), sonst 409, damit der
        Client nachfragen kann.
        """
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
        except ValueError:
            payload = {}                       # leerer Body ist erlaubt
        force = bool(payload.get("force")) if isinstance(payload, dict) else False

        if SCAN.is_running():
            if not force:
                self._json({"ok": False, "scan_running": True,
                            "error": "Es läuft noch ein Scan."}, 409)
                return
            SCAN.cancel()

        if not request_stop(delay=0.4):
            self._fail("Der Server läuft nicht über serve() und kann sich "
                       "nicht selbst beenden.", 500)
            return
        self._json({"ok": True})

    def _post_reclassify(self) -> None:
        cfg = cfgmod.load()
        with self.lock:
            stats = self._reclassify(cfg)
            self._rebuild(cfg)
        self._json({"ok": True, **stats})

    def _post_scan(self) -> None:
        try:
            payload = json.loads(self._body(100_000).decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        if SCAN.is_running():
            self._fail("Es läuft bereits ein Scan", 409)
            return

        cfg = cfgmod.load()
        roots = payload.get("paths") or None
        if roots and not self._roots_in_library(roots, cfg):
            self._fail("Pfad liegt außerhalb der Bibliotheksordner", 403)
            return
        started = SCAN.start(
            cfg,
            paths=roots,
            force=bool(payload.get("force")),
            limit=int(payload.get("limit") or 0),
            prune=bool(payload.get("prune", True)),
            skip_spectral=bool(payload.get("skip_spectral")),
            skip_loudness=bool(payload.get("skip_loudness")),
            force_cover_fill=bool(payload.get("force_cover_fill")),
            recheck_tag_issues=bool(payload.get("recheck_tags")),
        )
        if not started:
            self._fail("Scan konnte nicht gestartet werden", 409)
            return
        threading.Thread(target=self._after_scan, args=(cfg,), daemon=True).start()
        self._json({"ok": True})

    @staticmethod
    def _roots_in_library(roots, cfg: dict) -> bool:
        """
        Ein angeforderter Scan-Ordner muss innerhalb der eingestellten
        Bibliothek liegen. Der Client schickt hier ohnehin nur Teilmengen
        davon; frei waehlbar waere es ein Weg, beliebige Ordner in die
        Datenbank zu bekommen -- und damit fuer Dateizugriffe freizugeben
        (siehe _authorize_path()).
        """
        allowed = []
        for entry in (cfg.get("library_paths") or []):
            try:
                allowed.append(Path(os.path.expanduser(str(entry))).resolve())
            except OSError:
                continue
        if not allowed:
            return False
        for entry in roots:
            try:
                target = Path(os.path.expanduser(str(entry))).resolve()
            except OSError:
                return False
            if not any(target == base or base in target.parents for base in allowed):
                return False
        return True

    def _after_scan(self, cfg: dict) -> None:
        """Wartet auf das Ende des Scans und backt den Report neu."""
        while SCAN.is_running():
            time.sleep(0.4)
        if not SCAN.state.get("error"):
            # Laeuft ohnehin schon im Hintergrund-Thread: hier wird der
            # vorgemerkte Bau gleich zu Ende gefuehrt, damit ein Fehler im
            # Scan-Status sichtbar wird statt nur auf stderr.
            self._rebuild(cfg)
            if not REPORT.flush():
                SCAN.state["error"] = ("Report konnte nicht erzeugt werden: "
                                       f"{REPORT.last_error}")

    def _post_analyse(self) -> None:
        """
        Prueft eine per Drag & Drop abgelegte Datei. Der Browser liefert nur
        Inhalt und Name, keinen Pfad — deshalb wird der Inhalt zwischenge-
        speichert, gemessen und sofort wieder geloescht. Das Ergebnis landet
        bewusst NICHT in der Datenbank: es soll die Bibliotheks-Statistik
        nicht verfaelschen.
        """
        from .analyzer import analyse_file

        name = unquote(self._query().get("name", "unbekannt"))
        suffix = Path(name).suffix.lower()
        if suffix not in _AUDIO_TYPES:
            self._fail(f"Kein unterstütztes Audioformat: {suffix or 'ohne Endung'}")
            return
        tmp = None
        try:
            # Eigener try-Block nur fuer das Einlesen: analyse_file() kann
            # selbst einen ValueError werfen (mutagen ueber tags.read_extra),
            # der sonst faelschlich als "Datei zu gross" mit 413 herausginge.
            try:
                # Direkt in die Temp-Datei streamen statt erst vollstaendig in
                # den Arbeitsspeicher (siehe _body_to_file). tmp wird VOR dem
                # Lesen gesetzt, damit das finally unten die halb geschriebene
                # Datei auch dann wegraeumt, wenn die Uebertragung abbricht.
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
                    tmp = fh.name
                    self._body_to_file(fh, MAX_UPLOAD)
            except ValueError as exc:
                self._fail(f"Datei zu groß oder leer ({exc})", 413)
                return
            st = os.stat(tmp)
            row = analyse_file((tmp, st.st_size, st.st_mtime, cfgmod.load()))
        except Exception as exc:                 # noqa: BLE001
            self._fail(f"Analyse fehlgeschlagen: {exc}", 500)
            return
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        row["path"] = name                       # Temp-Pfad nie nach aussen geben
        row["dropped"] = True
        self._json({"ok": True, "row": row})


def serve(console, port: int = 8756, open_browser: bool = True) -> int:
    global _HTTPD
    cfg = cfgmod.load()
    report = cfgmod.resolve(cfg["report_path"])
    if not report.exists():
        console.print("[red]Report fehlt.[/red] Zuerst './run.command report' ausführen.")
        return 1

    ok, message = media.available()
    if not ok:
        console.print(f"[yellow]Hinweis:[/yellow] {message}")

    _Handler.report_path = report

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        console.print(f"[red]Port {port} ist belegt[/red] ({exc}). "
                      f"Anderen Port wählen: --port {port + 1}")
        return 1

    # Der Report-Worker laeuft, solange der Server laeuft (siehe
    # _ReportBuilder). Erst NACH dem erfolgreichen Binden starten -- bei
    # belegtem Port kehrt serve() oben zurueck, ohne durch das finally unten
    # zu kommen, das ihn sonst wieder anhalten muesste.
    REPORT.start()

    url = f"http://127.0.0.1:{port}/report.html"
    console.print(f"[bold green]Report läuft:[/bold green] {url}")
    console.print("[dim]Ausgeblendete Tracks werden in der Datenbank gespeichert "
                  "und bleiben beim nächsten Aufruf ausgeblendet.[/dim]")
    console.print("[dim]Beenden mit Strg+C oder über „Beenden“ in der "
                  "Oberfläche.[/dim]")
    if open_browser:
        media.open_url(url, media.browser_path(cfg))

    _HTTPD = httpd
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Server beendet.[/yellow]")
    finally:
        _HTTPD = None
        httpd.server_close()
        # Zuerst: einen noch offenen Report-Neubau wegschreiben. Sonst
        # startet die naechste Sitzung mit einem Stand, dem die letzten
        # Aenderungen dieser Sitzung fehlen -- und das Backup unten wuerde
        # eine DB sichern, die nicht zum Report daneben passt.
        try:
            REPORT.stop()
        except Exception as exc:                       # noqa: BLE001
            console.print(f"[dim]Report-Neubau beim Beenden uebersprungen: {exc}[/dim]")
        # Einziger Backup-Zeitpunkt der gepackten App: sie durchlaeuft nie
        # cli.main() (siehe dort), das das Backup sonst bei jedem CLI-Aufruf
        # prueft. Hier statt (nur) beim Start, weil ein spaeterer Stand im
        # laufenden Betrieb den aktuelleren Stand sichert -- und der Weg gilt
        # gleich fuer Terminal (Strg+C/'./run.command serve') und gepackte App
        # (Cmd+Q/Dock/'/api/quit'), weil beide ueber genau dieses serve()
        # laufen. backup_on_close() ist bewusst NICHT auf eins pro Tag
        # begrenzt (anders als maybe_auto_backup() beim CLI-Start) -- jedes
        # Schliessen soll den aktuellen Stand sichern, auch bei mehreren
        # Sitzungen am selben Tag.
        try:
            backup_mod.backup_on_close(cfg)
        except Exception as exc:                       # noqa: BLE001
            console.print(f"[dim]Automatisches Backup uebersprungen: {exc}[/dim]")
    return 0
