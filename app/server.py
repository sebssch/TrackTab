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
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import __version__
from . import anlzwrite as anlzwrite_mod
from . import audit_log
from . import backup as backup_mod
from . import beatgrid as beatgrid_mod
from . import bpmkey as bpmkey_mod
from . import genremood as genremood_mod
from . import gridstore
from .gridstore import (beats_with_tempo as _dj_beats_with_tempo, dj_hash as _dj_hash,
                        phrases_from_first_beat as _phrases_from_first_beat,
                        segment_tempo as _dj_segment_tempo, snap_phrases as _snap_phrases)
from . import phrases as phrases_mod
from . import playlistsync
from . import autocue as autocue_mod
from .autocue import package as autocue_package
from .autocue import sources as autocue_sources
from . import probe as probe_mod
from . import wavedetail as wavedetail_mod
from . import config as cfgmod
from . import classify as classify_mod
from . import convert as convert_mod
from . import coverfill
from . import db as db_mod
from . import errlog
from . import jobs
from . import lookup as lookup_mod
from . import media
from . import pwa as pwa_mod
from . import rekordbox as rekordbox_mod
from . import report as report_mod
from . import rewrite as rewrite_mod
from . import scanner
from . import stemjob as stemjob_mod
from . import stems as stems_mod
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
GRID_SEED = gridstore.SeedJob()

# Der laufende Server, damit ihn /api/quit und die App-Huelle (macapp.py) von
# aussen anhalten koennen. serve() setzt ihn, request_stop() haelt ihn an.
_HTTPD: ThreadingHTTPServer | None = None

# Namen, unter denen der eigene Server angesprochen werden darf (siehe
# _Handler._same_origin()). Gebunden ist nur 127.0.0.1; ::1 steht der
# Vollstaendigkeit halber dabei, falls das Binden spaeter erweitert wird.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Unter diesen Pfaden liegt die Seite selbst. Nur sie darf von einer fremden
# Seite aus per Link geoeffnet werden (siehe _Handler._fetch_site_ok()).
_PAGE_ROUTES = ("/", "/tracktab.html", "/index.html")


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
    Baeckt data/tracktab.html gebuendelt in EINEM Hintergrund-Thread.

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
                # Nur die HTML-Datei. CSV und M3U entstehen ausschliesslich
                # auf Anforderung (./run.command report), siehe report.build_all().
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
        errlog.log(f"[report] {label}", exc)


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


def _render_spectrogram(path: str) -> bytes:
    """Spektrogramm als PNG (ffmpeg showspectrumpic), bewusst ohne Cache.

    Auf 44,1 kHz gebracht, damit die Frequenzachse (linear) immer
    0..22,05 kHz zeigt und der Client die Cutoff-Linie ohne weiteres
    Wissen ueber die Samplerate einzeichnen kann.
    """
    flt = ("aresample=44100,showspectrumpic=s=1600x512:legend=0:"
           "color=magma:scale=log:fscale=lin")
    proc = subprocess.run(
        [media.ffmpeg_path(), "-v", "error", "-i", path, "-lavfi", flt,
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        capture_output=True, timeout=300)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip()[-300:]
                           or "ffmpeg lieferte kein Bild")
    return proc.stdout


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


def _wave_color_json(data: bytes, style: str) -> dict:
    """Spalten (je vier Bytes) im Format von drawRgbWave()/drawBandWave()."""
    n = len(data) // 4
    col = [data[i * 4:i * 4 + 4] for i in range(n)]
    if style == "rgb":
        return {"style": "rgb", "height": [round(c[3] / 255, 4) for c in col],
                "color": [[c[0], c[1], c[2]] for c in col]}
    return {"style": "3band", "low": [round(c[0] / 255, 4) for c in col],
            "mid": [round(c[1] / 255, 4) for c in col], "high": [round(c[2] / 255, 4) for c in col]}


def _rb_snapshot_sync(snap: dict | None, state: dict, take: list = (), seen: list = ()) -> dict:
    """Festgehaltenen Stand fortschreiben. Leere Felder werden aus Rekordbox gefuellt, was
    schon da ist bleibt. take: Felder, die neu aus Rekordbox gelten sollen (Knopf, eigenes
    Update). seen: Abweichung gesehen, eigener Stand bleibt. Danach stimmt changed wieder."""
    out = snap or {f: None for f in db_mod.DJ_TRACK_FIELDS} | {"seen": {}, "changed": [], "stamp": None}
    for f in db_mod.DJ_TRACK_FIELDS:
        live = state[f]
        digest = _dj_hash(live)
        if f in take or (out[f] is None and live):
            out[f] = live or None
            out["seen"][f] = digest
        elif f in seen:
            out["seen"][f] = digest
    out["changed"] = [f for f in db_mod.DJ_TRACK_FIELDS
                      if out[f] is not None and out["seen"].get(f) != _dj_hash(state[f])]
    return out


def _rb_stamp(rb_stamp: str | None) -> str | None:
    """Stempel eines festgehaltenen Stands: Aenderungsstand in Rekordbox plus Lesart."""
    return None if rb_stamp is None else f"{rb_stamp}#r{rekordbox_mod.READER_VERSION}"


def _rb_snapshot_reread(snap: dict | None, state: dict, rb_stamp: str | None) -> dict | None:
    """Neuen Stand, wenn sich seit dem Festhalten nur die Lesart geaendert hat, nicht
    Rekordbox: dann gilt das Gelesene von heute still, ohne "geaendert". Felder mit einer
    offenen Abweichung (snap["changed"]) bleiben, die entscheidet der Nutzer. None =
    nichts zu tun (gleiche Lesart oder Rekordbox selbst hat sich geaendert)."""
    if not snap or rb_stamp is None or not snap.get("stamp") or not state.get("present"):
        return None
    old = str(snap["stamp"])
    if old == _rb_stamp(rb_stamp) or old.split("#r")[0] != rb_stamp:
        return None
    take = [f for f in db_mod.DJ_TRACK_FIELDS if snap[f] is not None and f not in (snap.get("changed") or [])]
    out = _rb_snapshot_sync(snap, state, take=take)
    out["stamp"] = _rb_stamp(rb_stamp)
    out["reread"] = True                   # nur fuer den Aufrufer, wird nicht gespeichert
    return out


def _dj_rb_hashes(state: dict) -> dict:
    """Pruefsummen dessen, was Rekordbox zu einem Track hat, je Feld auf SEINER
    Zeitachse. Daran haengt die Rueckfrage bei Abweichungen."""
    return {"grid": _dj_hash(state["grid"]), "phrases": _dj_hash(state["phrases"]), "cues": _dj_hash(state["cues"])}


# Pfad -> (Stand von master.db, Pruefsumme der Bearbeitung, Ergebnis). Rekordbox zu
# lesen kostet rund 50 ms je Track und /api/dj-bpms laeuft bei jedem syncMarks().
_dj_rb_compare_cache: dict = {}


def _dj_rb_diff(path: str, edits: dict, base: dict) -> list:
    """Worin weicht die Bearbeitung eines Tracks von Rekordbox ab? Noch nicht dorthin
    geschrieben: diese Felder, ohne Rekordbox zu lesen. Sonst: seither dort geaendert."""
    fields = [f for f in db_mod.DJ_TRACK_FIELDS if edits[f] is not None]
    pending = [f for f in fields if base["pushed"].get(f) != _dj_hash(edits[f])]
    if pending:
        return pending
    stamp, digest = rekordbox_mod.master_db_mtime(), _dj_hash(edits)
    hit = _dj_rb_compare_cache.get(path)
    if hit and hit[0] == stamp and hit[1] == digest:
        return hit[2]
    state = rekordbox_mod.track_state(path)
    if not state["available"]:
        return []
    hashes = _dj_rb_hashes(state)
    result = [f for f in fields if f in base["rb"] and base["rb"][f] != hashes[f]]
    _dj_rb_compare_cache[path] = (stamp, digest, result)
    return result


def _hot_cue_paths(cfg: dict) -> set:
    """Tracks mit mindestens einem Hot Cue, nach derselben Regel wie die DJ-Ansicht
    (_dj_track_payload()): eine Bearbeitung in TrackTab (DJ-Editor, Auto Cue) gilt
    vor allem anderen; ohne sie die Cues aus Rekordbox, zuerst der festgehaltene
    Stand (rb_snapshot), sonst der aktuelle -- ausser beatgrid_source ist
    "tracktab", dann zaehlt nur TrackTabs eigener Stand. Fuer /HotCues der Suche."""
    conn = db_mod.connect(cfg)
    try:
        edited, snap = db_mod.hot_cue_maps(conn)
    finally:
        conn.close()
    out = {p for p, hot in edited.items() if hot}
    if str(cfg.get("beatgrid_source") or "fallback") == "tracktab":
        return out
    live = rekordbox_mod.hot_cue_paths()
    out |= {p for p, hot in snap.items() if hot and p not in edited}
    out |= {p for p in live if p not in edited and p not in snap}
    return out


def _tag_update_needed(fields: list) -> bool:
    """Gibt es Metadaten zu uebertragen? bpm_half allein ist nur ein Hinweis."""
    return any(f != "bpm_half" for f in fields)


def _tag_rows(cfg: dict, path: str | None = None) -> dict:
    """Pfad -> Zeile mit den Metadaten, die nach Rekordbox gehen (rekordbox.TAG_FIELDS)."""
    conn = db_mod.connect(cfg)
    try:
        cols = ", ".join(f'"{f}"' for f in rekordbox_mod.TAG_FIELDS)
        if path is None:
            cur = conn.execute(f"SELECT path, {cols} FROM files")
            grid_edited = {r["path"] for r in conn.execute("SELECT path FROM dj_track WHERE grid IS NOT NULL")}
        else:
            cur = conn.execute(f"SELECT path, {cols} FROM files WHERE path = ?", (path,))
            grid_edited = {r["path"] for r in conn.execute(
                "SELECT path FROM dj_track WHERE grid IS NOT NULL AND path = ?", (path,))}
        rows = {}
        for r in cur:
            row = dict(r)
            # Bearbeitetes Grid: Rekordbox' BPM kommt aus dem Grid, nie aus dem Tag der
            # Datei (der weicht dann oft ab -- die rote BPM in der DJ-Liste).
            if row["path"] in grid_edited:
                row["bpm"] = None
            rows[row["path"]] = row
        return rows
    finally:
        conn.close()


def _dj_tempo_lookup(grid, stored):
    """sec -> BPM an dieser Stelle, fuer BeatLoopSize. 0 = unbekannt."""
    segments = (grid or {}).get("segments") or []
    if segments:
        return lambda sec: _dj_segment_tempo(segments, sec)
    bpm = (stored or {}).get("bpm") or 0
    return lambda _sec: bpm


_PHRASE_RESULT_GRID = ("mode", "used", "bpm", "confidence", "first_beat", "downbeat", "changes", "beats",
                       "grid_origin")
_PHRASE_RESULT_PHRASES = ("phrases", "mood", "phrases_origin")


def _merge_phrase_result(stored: dict | None, result: dict, wanted: set) -> dict:
    """Ein Messgang liefert immer beides, gespeichert wird nur das Gewuenschte. Der Rest
    bleibt, wie er war; ohne alten Stand gibt es nichts zu erhalten."""
    if stored is None:
        return result
    merged = dict(result)
    keep = (_PHRASE_RESULT_GRID if "grid" not in wanted else ()) + (_PHRASE_RESULT_PHRASES if "phrases" not in wanted else ())
    merged.update({k: stored[k] for k in keep})
    return merged


# DJ-Ansicht: der Server prueft nur die Form, gerechnet wird im Client (dj.js).
_DJ_PHRASE_KINDS = {"intro", "up", "down", "verse", "bridge", "chorus", "outro"}
_DJ_HOT_LETTERS = "ABCDEFGH"


def _dj_time(value, lo: float = 0) -> float:
    t = float(value)
    if not lo <= t < 86_400:                # auch NaN faellt hier durch
        raise ValueError(f"Zeit außerhalb des Bereichs: {value}")
    return round(t, 4)


def _clean_dj_ramp(ramp) -> dict:
    """Tempo-Rampe eines Abschnitts (dj.js, djRampTimes()): Schlagzahl, Anker [[k, s]] bzw.
    [[k, s, 1]] (von Hand gesetzt, "An Audio anpassen" laesst ihn stehen) und das Tempo je
    Schlag, das der Client aus den ungerundeten Zeiten rechnet."""
    beats = int(ramp["beats"])               # 0 = offen ("Rampe Start" ohne Ende)
    if not 0 <= beats <= 4096:
        raise ValueError("Ungültige Rampe")
    anchors = []
    for anchor in ramp.get("anchors") or []:
        k, sec = anchor[0], anchor[1]
        if not 0 < int(k) < beats:
            raise ValueError("Ungültiger Rampen-Anker")
        manual = len(anchor) > 2 and bool(anchor[2])
        anchors.append([int(k), _dj_time(sec), 1] if manual else [int(k), _dj_time(sec)])
    tempos = [round(float(b), 3) for b in ramp.get("tempos") or []]
    if len(anchors) > beats or len(tempos) > beats or any(not 20 <= b <= 400 for b in tempos):
        raise ValueError("Ungültige Rampe")
    return {"beats": beats, "anchors": anchors, "tempos": tempos}


def _clean_dj_grid(grid) -> dict | None:
    if grid is None:
        return None
    segments, beats = grid["segments"], grid["beats"]
    if len(segments) > 2000 or len(beats) > 60_000:
        raise ValueError("Beatgrid zu groß")
    clean_segments = []
    for seg in segments:
        bpm, beat = float(seg["bpm"]), int(seg["n"])
        if not 20 <= bpm <= 400 or not 1 <= beat <= 4:
            raise ValueError("Ungültiger Grid-Abschnitt")
        clean = {"t": _dj_time(seg["t"]), "bpm": round(bpm, 3), "n": beat}
        if seg.get("ramp"):
            clean["ramp"] = _clean_dj_ramp(seg["ramp"])
        clean_segments.append(clean)
    return {"segments": clean_segments, "locked": bool(grid.get("locked")),
            "beats": [[int(b[0]), int(b[1])] for b in beats]}


def _clean_dj_phrases(phrases) -> list | None:
    if phrases is None:
        return None
    if len(phrases) > 500:
        raise ValueError("Zu viele Phrasen")
    out = []
    for p in phrases:
        kind, start, end = str(p["kind"]), _dj_time(p["start"]), _dj_time(p["end"])
        if kind not in _DJ_PHRASE_KINDS or end <= start:
            raise ValueError("Ungültige Phrase")
        out.append({"start": start, "end": end, "kind": kind, "num": max(0, min(9, int(p.get("num") or 0)))})
    return out


def _clean_dj_cues(cues) -> list | None:
    if cues is None:
        return None
    if len(cues) > 500:
        raise ValueError("Zu viele Cues")
    out, slots = [], set()
    for c in cues:
        kind = str(c["kind"])
        # Cues duerfen vor dem Track liegen: der erste Schlag kann knapp davor sein.
        pos = _dj_time(c["position_s"], lo=-60)
        loop = c.get("loop_s")
        loop = _dj_time(loop) if loop is not None else None
        if loop is not None and loop <= pos:
            raise ValueError("Ungültige Schleife")
        color = [max(0, min(255, int(v))) for v in c["color"]][:3]
        if len(color) != 3:
            raise ValueError("Ungültige Farbe")
        name = str(c.get("name") or "")[:80]
        if kind == "hot":
            slot = int(c["slot"])
            if not 1 <= slot <= 8 or slot in slots:
                raise ValueError("Ungültiger Hot-Cue-Platz")
            slots.add(slot)
            out.append({"kind": "hot", "slot": slot, "label": _DJ_HOT_LETTERS[slot - 1], "name": name,
                        "position_s": pos, "loop_s": loop, "color": color})
        elif kind == "memory":
            item = {"kind": "memory", "slot": None, "label": name, "name": name,
                    "position_s": pos, "loop_s": loop, "color": color}
            # Nur wenn gesetzt, damit die Pruefsumme normaler Cues gleich bleibt.
            if loop is not None and c.get("active"):
                item["active"] = True
            out.append(item)
        else:
            raise ValueError("Unbekannte Cue-Art")
    return out


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

    def _get_favicon(self) -> None:
        """Favicon eines konfigurierten Shops (?shop=<id>).

        Nur Hosts aus der Shop-Liste -- kein offener Abruf-Proxy. Der Server
        holt das Bild einmal und legt es unter base_dir()/favicons ab.
        """
        qs = parse_qs(urlparse(self.path).query)
        shop_id = (qs.get("shop") or [""])[0]
        host = ""
        for s in cfgmod.load().get("shops", []):
            if s.get("id") == shop_id:
                host = urlparse(str(s.get("url", ""))).hostname or ""
                break
        data = lookup_mod.fetch_favicon(host, cfgmod.resolve("favicons")) if host else None
        if not data:
            self._send(404, b"", "text/plain")
            return
        body, mime = data
        self._send(200, body, mime, {"Cache-Control": "private, max-age=86400"})

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
        if not self._fetch_site_ok():
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

    def _fetch_site_ok(self) -> bool:
        """Weist Anfragen ab, die eine FREMDE Seite ausgeloest hat.

        Luecke, die Host- und Origin-Abgleich offen lassen: ein GET ohne
        Origin ist erlaubt (so ruft der Browser die Seite selbst auf), und
        genau so sieht auch ein <img src="http://127.0.0.1:8756/api/...">
        auf einer beliebigen Webseite aus. Lesen kann die fremde Seite die
        Antwort nicht, ausloesen aber schon: ffmpeg-Laeufe ueber
        /api/spectrogram oder /api/dj-pcm, Temp-Dateien ueber
        /api/download-zip, Netzabrufe ueber /api/check-update.

        Sec-Fetch-Site sagt, woher eine Anfrage stammt; der Browser setzt die
        Kopfzeile selbst, eine Seite kann sie nicht faelschen. "same-origin"
        ist die eigene Oberflaeche, "none" der Nutzer (Adresszeile,
        Lesezeichen, von der App geoeffnet). "cross-site" und "same-site"
        sind fremde Seiten -- "same-site" ist z. B. ein anderer Port auf
        127.0.0.1, also genau der Fall aus _is_own_address().

        Einzige Ausnahme: ein Link auf die Seite selbst (Navigation auf
        _PAGE_ROUTES) -- das laedt nur die Oberflaeche, wie ein Lesezeichen.

        Fehlt die Kopfzeile (aeltere Browser, curl), entscheiden wie bisher
        Host und Origin.
        """
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site not in ("cross-site", "same-site"):
            return True
        return (self.command == "GET"
                and (self.headers.get("Sec-Fetch-Mode") or "").strip().lower() == "navigate"
                and (self.headers.get("Sec-Fetch-Dest") or "").strip().lower() == "document"
                and urlparse(self.path).path in _PAGE_ROUTES)

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
                if row is None or not db_mod.is_music_added(conn, path):
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
        """Die Markierungsmengen (plus Genre-/Stimmungs-Analysen), die
        rows_to_payload() braucht.

        Jede davon liest ihre Tabelle vollstaendig -- zusammen an echtem
        Material rund 16.400 Zeilen (vor allem music_added und rekordbox).
        Deshalb gibt es _compact_rows(): fuer eine Liste von Pfaden werden sie
        EINMAL gelesen statt je Pfad neu.
        """
        return (db_mod.ignored_paths(conn), db_mod.corrected_paths(conn),
                db_mod.rekordbox_paths(conn), db_mod.music_added_map(conn),
                db_mod.dup_dismissed_paths(conn), db_mod.genremood_map(conn))

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

    # ── Auffangnetz ───────────────────────────────────────────────────────
    def send_response(self, code, message=None):
        # Merken, dass die Antwort begonnen hat -- _guarded() darf dann keine
        # zweite hinterherschicken.
        self._responded = True
        super().send_response(code, message)

    def _guarded(self, dispatch) -> None:
        """Faengt alles, was ein Endpunkt nicht selbst behandelt.

        Ohne das beendet eine unerwartete Ausnahme (ein int("abc") auf einem
        Anfragefeld, ein Sonderfall in einer Bibliothek) die Verbindung ohne
        Antwort: der Browser meldet nur "Failed to fetch", und in der per
        Doppelklick gestarteten App sieht den Traceback niemand. Jetzt gibt es
        eine 500-Antwort mit Text und einen Eintrag im Fehlerprotokoll
        (errlog.py).

        Die Verbindung wird danach geschlossen: ob der Anfragekoerper schon
        gelesen wurde, ist an dieser Stelle unbekannt, und ein Rest im Puffer
        wuerde als naechste Anfrage gedeutet.
        """
        self._responded = False
        try:
            dispatch()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True       # Browser hat abgebrochen
        except Exception as exc:               # noqa: BLE001
            errlog.log(f"{self.command} {urlparse(self.path).path}", exc)
            self.close_connection = True
            if not self._responded:
                try:
                    self._fail(f"Interner Fehler: {exc}", 500)
                except Exception:              # noqa: BLE001
                    pass

    def do_GET(self):                          # noqa: N802
        self._guarded(self._dispatch_get)

    def do_POST(self):                         # noqa: N802
        self._guarded(self._dispatch_post)

    # ── GET ───────────────────────────────────────────────────────────────
    def _dispatch_get(self) -> None:
        if not self._same_origin():
            self._send(403, "Anfrage von fremder Herkunft abgelehnt.".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        route = urlparse(self.path).path

        if route in _PAGE_ROUTES:
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
        elif route == "/api/favicon":
            self._get_favicon()
        elif route == "/api/check-update":
            self._get_check_update()
        elif route == "/api/ignored":
            self._get_ignored()
        elif route == "/api/user-tags":
            self._get_user_tags()
        elif route == "/api/corrected":
            self._get_corrected()
        elif route == "/api/dup-dismissed":
            self._get_dup_dismissed()
        elif route == "/api/rekordbox":
            self._get_rekordbox()
        elif route == "/api/rekordbox-status":
            self._get_rekordbox_status()
        elif route == "/api/rekordbox-pending":
            self._get_rekordbox_pending()
        elif route == "/api/rekordbox-extras":
            self._get_rekordbox_extras()
        elif route == "/api/dj-track":
            self._get_dj_track()
        elif route == "/api/dj-bpms":
            self._get_dj_bpms()
        elif route == "/api/dj-pcm":
            self._get_dj_pcm()
        elif route == "/api/dj-wave":
            self._get_dj_wave()
        elif route == "/api/wave-color":
            self._get_wave_color()
        elif route == "/api/wave-phrases":
            self._get_wave_phrases()
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
        elif route == "/api/spectrogram":
            self._get_spectrogram()
        elif route == "/api/appicon":
            self._get_appicon()
        elif route == "/api/settings":
            self._json(settings_mod.describe())
        elif route == "/api/scan/status":
            self._json({"ok": True, **SCAN.state})
        elif route == "/api/grid-seed/status":
            self._json({"ok": True, **GRID_SEED.status()})
        elif route == "/api/stems/status":
            self._get_stems_status()
        elif route == "/api/stems/download":
            self._get_stems_download()
        elif route == "/api/stems/model":
            self._json({"ok": True, **self._stems_model_info()})
        elif route == "/api/autocue/models":
            self._json({"ok": True, "models": settings_mod.autocue_models_info()})
        elif route == "/api/autocue/model-export":
            self._get_autocue_model_export()
        elif route == "/api/autocue/train-status":
            from .autocue.training import job as autocue_job
            try:
                since = int(self._query().get("from", "0"))
            except ValueError:
                since = 0
            self._json({"ok": True, **autocue_job.JOB.state(since)})

        elif route == "/api/backups":
            self._get_backups()
        elif route == "/api/stats":
            self._get_stats()
        elif route == "/api/stats/library":
            self._get_stats_library()
        elif route == "/api/cover":
            self._get_cover()
        elif route == "/api/playlists":
            self._get_playlists()
        elif route == "/api/playlist-links":
            self._get_playlist_links()
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

    # Die reinen Lesepfade unten laufen ohne self.lock: unter WAL (siehe
    # db.connect) duerfen Leser parallel zu einem Schreiber ran. Mit Lock
    # warteten sie hinter jedem langen Schreibvorgang (Neubewertung,
    # Umbenennen eines Tag-Werts ueber viele Dateien). _get_playlists()
    # behaelt ihn: es liest zwei Tabellen, die zueinander passen muessen.
    def _get_ignored(self) -> None:
        conn = db_mod.connect(cfgmod.load())
        try:
            paths = sorted(db_mod.ignored_paths(conn))
        finally:
            conn.close()
        self._json({"ignored": paths})

    def _get_user_tags(self) -> None:
        conn = db_mod.connect(cfgmod.load())
        try:
            tags = db_mod.user_tags_map(conn)
        finally:
            conn.close()
        self._json({"ok": True, "tags": tags})

    _USER_TAG_MAX = 60

    @classmethod
    def _clean_user_tags(cls, raw) -> list[str]:
        """Freie Tag-Namen: getrimmt, Leerraum zusammengefasst, ohne Doppelte
        (Gross/Klein egal), hoechstens _USER_TAG_MAX Zeichen."""
        out, seen = [], set()
        for item in raw or []:
            tag = " ".join(str(item).split())[:cls._USER_TAG_MAX]
            if tag and tag.casefold() not in seen:
                seen.add(tag.casefold())
                out.append(tag)
        return out

    def _post_user_tags(self) -> None:
        """Eigene Tags setzen. {path, tags}: ersetzt die Tags eines Tracks
        (Tags-Dialog). {paths, add, remove}: Sammelaktion, uebrige Tags
        bleiben. Wie Merklisten reine Nutzer-Angabe in der DB, nie in der
        Datei; erlaubt sind Bibliothekspfade und eigene Dateiauswahl
        (_authorize_path). Antwort: alle Tags (wie GET), der Client uebernimmt
        sie komplett."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            single = "path" in payload
            paths = [str(payload["path"])] if single else [str(p) for p in payload["paths"]]
            tags = self._clean_user_tags(payload.get("tags") if single else payload.get("add"))
            remove = [] if single else self._clean_user_tags(payload.get("remove"))
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        for p in paths:
            if not self._authorize_path(p)[1]:
                self._fail("Unbekannter Pfad")
                return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                if single:
                    db_mod.set_user_tags(conn, paths[0], tags)
                else:
                    db_mod.change_user_tags(conn, paths, tags, remove)
                all_tags = db_mod.user_tags_map(conn)
            finally:
                conn.close()
            # Die Tags stecken auch gebacken in META.userTags -- ohne Neubau
            # stuende nach einem Neuladen der alte Stand da.
            self._rebuild(cfgmod.load())
        self._json({"ok": True, "tags": all_tags})

    def _get_playlists(self) -> None:
        """Baum-Definitionen und Zuordnungen in einem Zug.

        Gehoert wie /api/ignored und /api/corrected zur
        _rebuild()-Ausnahme: der gebackene data/tracktab.html traegt zwar die
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
        conn = db_mod.connect(cfgmod.load())
        try:
            meta = playlistsync.file_meta(conn)
        finally:
            conn.close()
        known = set(meta)
        cloud = playlistsync.cloud_matcher(meta)
        for tr in tracks:
            # Eigener Track, den Music nur noch als Cloud-Eintrag fuehrt (z. B. nach
            # einer Aenderung am iPhone): ueber Titel/Interpret/Groesse zuordnen.
            if not tr.get("path"):
                hit = cloud(tr)
                if hit:
                    tr["path"] = hit
                    tr["cloud"] = True
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
        conn = db_mod.connect(cfgmod.load())
        try:
            paths = sorted(db_mod.corrected_paths(conn))
        finally:
            conn.close()
        self._json({"corrected": paths})

    def _get_dup_dismissed(self) -> None:
        conn = db_mod.connect(cfgmod.load())
        try:
            paths = sorted(db_mod.dup_dismissed_paths(conn))
        finally:
            conn.close()
        self._json({"dup_dismissed": paths})

    def _get_rekordbox(self) -> None:
        conn = db_mod.connect(cfgmod.load())
        try:
            paths = sorted(db_mod.rekordbox_paths(conn))
        finally:
            conn.close()
        self._json({"paths": paths})

    def _get_rekordbox_pending(self) -> None:
        """Anzahl der fuer Rekordbox vorgemerkten Tag-Aenderungen (Zaehler am
        Knopf "Rekordbox abgleichen")."""
        conn = db_mod.connect(cfgmod.load())
        try:
            count = (db_mod.rekordbox_pending_count(conn)
                     + db_mod.rekordbox_stem_pending_count(conn))
        finally:
            conn.close()
        self._json({"count": count})

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
            # Der Cloud-Sync-Dienst schreibt ebenfalls in master.db -- fuer
            # die Oberflaeche zaehlt er wie ein geoeffnetes Rekordbox, nur
            # mit eigener Meldung ("agent").
            agent = not running and rekordbox_mod.agent_running()
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "running": running or agent, "agent": agent})

    def _get_merge_dismissed(self) -> None:
        """Dauerhaft ausgeblendete Zusammenfuehrungs-Vorschlaege aller drei
        Felder (Genre/Interpret/Album) -- Werte- und Gruppenlisten selbst
        kommen aus dem bereits im Client gehaltenen DATA, ein Server-Roundtrip
        dafuer waere unnoetig (siehe app.js groupCounts())."""
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
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        extras = rekordbox_mod.get_track_extras(path)
        # In der DJ-Ansicht bearbeitete Cues gelten ueberall, auch fuer Tracks,
        # die Rekordbox gar nicht kennt. cues_own: sie liegen schon auf der
        # eigenen Zeitachse, der Versatz zu Rekordbox faellt weg.
        conn = db_mod.connect(cfg)
        try:
            own_cues = db_mod.get_dj_track(conn, path)["cues"]
            snap = db_mod.get_rb_snapshot(conn, path)
        finally:
            conn.close()
        # Festgehaltener Stand wie im DJ-Editor. Loops zeigen diese Player nicht.
        if snap and snap["cues"] is not None and extras and "error" not in extras:
            extras = {**extras, "cues": [c for c in snap["cues"] if c.get("loop_s") is None]}
        if own_cues is not None and (extras is None or "error" not in extras):
            # Die anderen Player kennen nur die Tracklaenge, nichts davor.
            own_cues = [{**c, "position_s": max(0.0, c["position_s"])} for c in own_cues]
            self._json({"available": True, "cues": own_cues, "cues_own": True})
            return
        if extras is None:
            self._json({"available": False})
            return
        if "error" in extras:
            self._json({"available": False, "retry": True})
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

    def _get_stats_library(self) -> None:
        """Bibliotheks-Kennzahlen (Bestand, Zugaenge, Abdeckung). Kein
        self.lock und kein Cache: reine Leseabfrage unter WAL, siehe
        stats.library()."""
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            data = stats_mod.library(cfg, conn)
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
            # Der Start-Hinweis (app.js, checkUpdateOnStart()) zeigt sich nur,
            # wenn die neueste Version HOEHER ist als die ignorierte.
            ignored = lookup_mod._version_tuple(
                str(cfgmod.load().get("update_ignore_version") or ""))
            latest = lookup_mod._version_tuple(result["latest_version"])
            show = bool(result["update_available"]
                        and (ignored is None or latest > ignored))
            changes = (lookup_mod.fetch_changes(__version__, result["latest_version"])
                       if result["update_available"] else [])
            self._json({"ok": True, "reachable": True, **result,
                        "changes": changes,
                        "show_on_start": show,
                        "repo_url": lookup_mod.REPO_URL})

    def _post_ignore_update(self) -> None:
        """Merkt sich eine Version, fuer die der Start-Hinweis nicht mehr
        erscheinen soll (config.local.yaml, update_ignore_version)."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            version = str(payload["version"]).strip()
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if lookup_mod._version_tuple(version) is None:
            self._fail("Keine gültige Versionsnummer")
            return
        with self.lock:
            settings_mod._write_local({"update_ignore_version": version})
        self._json({"ok": True})

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
        path = self._query().get("path", "")
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
            paths = json.loads(q.get("paths", "[]"))
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
        name = q.get("name", "Tracks.zip")
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
            rename_pattern = q.get("pattern", "") or cfgmod.load()["rename_pattern"]
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
        path = self._query().get("path", "")
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

    def _get_spectrogram(self) -> None:
        """Spektrogramm-PNG; wird je Aufruf frisch gerechnet (kein Cache).

        Rechnet ausserhalb von self.lock: ffmpeg ist externe Arbeit und
        dauert je nach Laenge ein bis mehrere Sekunden.
        """
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        try:
            png = _render_spectrogram(path)
        except Exception as exc:                 # noqa: BLE001
            self._fail(f"Spektrogramm fehlgeschlagen: {exc}", 500)
            return
        self._send(200, png, "image/png")

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
        path = self._query().get("path", "")
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
    def _dispatch_post(self) -> None:
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
        elif route == "/api/rekordbox-import":
            self._post_rekordbox_import()
        elif route == "/api/grid-seed":
            # Nach dem Abgleich: Rekordbox-Grids als eigene uebernehmen (gridstore.SeedJob).
            self._json({"ok": True, "started": GRID_SEED.start(cfgmod.load(), self.lock), **GRID_SEED.status()})
        elif route == "/api/grid-seed/cancel":
            GRID_SEED.cancel()
            self._json({"ok": True})
        elif route == "/api/open-file-pick":
            self._post_open_file_pick()
        elif route == "/api/analyse-path":
            self._post_analyse_path()
        elif route == "/api/bpmkey":
            self._post_bpmkey()
        elif route == "/api/stems":
            self._post_stems()
        elif route == "/api/stems/model-download":
            self._json({"ok": True, "started": stems_mod.download_start(),
                        **self._stems_model_info()})
        elif route == "/api/stems/model-cancel":
            stems_mod.download_cancel()
            self._json({"ok": True})
        elif route == "/api/stems/model-remove":
            if stems_mod.download_state()["running"]:
                self._fail("Download läuft noch", 409)
            else:
                stems_mod.remove_model()
                self._json({"ok": True, **self._stems_model_info()})
        elif route == "/api/stems/cancel":
            stemjob_mod.JOBS.cancel()
            self._json({"ok": True})
        elif route == "/api/stems/discard":
            self._post_stems_discard()
        elif route == "/api/stems/approve":
            self._post_stems_approve()
        elif route == "/api/ignore-update":
            self._post_ignore_update()
        elif route == "/api/genremood":
            self._post_genremood()
        elif route == "/api/phrases":
            self._post_phrases()
        elif route == "/api/autocue":
            self._post_autocue()
        elif route == "/api/dj-track":
            self._post_dj_track()
        elif route == "/api/dj-tempo":
            self._post_dj_tempo()
        elif route == "/api/dj-ramp-fit":
            self._post_dj_ramp_fit()
        elif route == "/api/dj-rekordbox":
            self._post_dj_rekordbox()
        elif route == "/api/user-tags":
            self._post_user_tags()
        elif route == "/api/rename":
            self._post_rename()
        elif route == "/api/rename-library":
            self._post_rename_library()
        elif route == "/api/add-to-library":
            self._post_add_to_library()
        elif route == "/api/rekordbox-add-playlist":
            self._post_rekordbox_add_playlist()
        elif route == "/api/rekordbox-remove-entries":
            self._post_rekordbox_remove_entries()
        elif route == "/api/playlist-links":
            self._post_playlist_links()
        elif route == "/api/playlist-link-sync":
            self._post_playlist_link_sync()
        elif route == "/api/dj-rekordbox-batch":
            self._post_dj_rekordbox_batch()
        elif route == "/api/rekordbox-playlist-edit":
            self._post_rekordbox_playlist_edit()
        elif route == "/api/music-playlist-edit":
            self._post_music_playlist_edit()
        elif route == "/api/music-playlist-add":
            self._post_music_playlist_add()
        elif route == "/api/music-playlist-remove":
            self._post_music_playlist_remove()
        elif route == "/api/rekordbox-reorder":
            self._post_rekordbox_reorder()
        elif route == "/api/rekordbox-remove-collection":
            self._post_rekordbox_remove_collection()
        elif route == "/api/rekordbox-membership":
            self._post_rekordbox_membership()
        elif route == "/api/rekordbox-sync":
            self._post_rekordbox_sync()
        elif route == "/api/rekordbox-pending-apply":
            self._post_rekordbox_pending_apply()
        elif route == "/api/rekordbox-tags-sync":
            self._post_rekordbox_tags_sync()
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
        elif route == "/api/open-browser":
            self._post_open_browser()
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
        elif route == "/api/file-writable":
            self._post_file_writable()
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
        elif route == "/api/cover-copy":
            self._post_cover_copy()
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
        elif route == "/api/autocue-slots":
            self._post_autocue_slots()
        elif route == "/api/autocue/model-import":
            self._post_autocue_model_import()
        elif route == "/api/autocue/model-select":
            self._post_autocue_model_select()
        elif route == "/api/autocue/model-rename":
            self._post_autocue_model_rename()
        elif route == "/api/autocue/model-delete":
            self._post_autocue_model_delete()
        elif route == "/api/autocue/train-check":
            self._post_autocue_train(check_only=True)
        elif route == "/api/autocue/train":
            self._post_autocue_train()
        elif route == "/api/autocue/train-cancel":
            from .autocue.training import job as autocue_job
            autocue_job.JOB.cancel()
            self._json({"ok": True})
        elif route == "/api/columns":
            self._post_columns()
        elif route == "/api/column-views":
            self._post_column_views()
        elif route == "/api/generator-curves":
            self._post_generator_curves()
        elif route == "/api/column-assign":
            self._post_column_assign()
        elif route == "/api/drop-columns":
            self._post_drop_columns()
        elif route == "/api/playlist":
            self._post_playlist()
        elif route == "/api/playlist-items":
            self._post_playlist_items()
        elif route == "/api/playlist-import":
            self._post_playlist_import()
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
                        in_music=db_mod.is_music_added(conn, path),
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

    def _post_open_browser(self) -> None:
        """Link (Shop) im Standardbrowser des Systems oeffnen. Nur http/https, sonst
        liesse sich darueber jedes URL-Schema und damit jedes Programm starten."""
        try:
            url = str(json.loads(self._body(100_000).decode("utf-8"))["url"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            self._fail("Nur http- und https-Links")
            return
        media.open_url(url)
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

    def _post_rename_library(self) -> None:
        """
        Benennt Bibliothekstracks nach cfg["rename_pattern"] um und zieht
        alles nach, was am Pfad haengt: DB-Zeile (db.move_path(), damit auch
        Playlisten, Merklisten, Wellenform ...), den Music.app-Track (zeigt
        per 'set location' auf die neue Datei, Playlisten und Wiedergabe-
        zaehler bleiben) und Rekordbox' DjmdContent-Zeile.

        Reihenfolge je Datei: Music-IDs ermitteln (solange der alte Ort noch
        lesbar ist) -> Datei umbenennen -> Music umbiegen. Lehnt Music.app ab,
        wird die Umbenennung zurueckgenommen, damit kein toter Track
        entsteht. Ist Rekordbox geoeffnet, wird der Pfad nur vorgemerkt
        (rekordbox_pending.prior_path) und spaeter ueber "Rekordbox
        abgleichen" geschrieben -- ein Schreibzugriff auf master.db
        waehrend es laeuft ist riskant.

        Lock-Regel: ffprobe, AppleScript und Rekordbox AUSSERHALB von
        self.lock, nur move_path() darin.

        Antwort positionsgleich zu 'paths'; status 'renamed' | 'unchanged' |
        'no_data' | 'error'.
        """
        from . import rename as rename_mod

        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            paths = [str(p) for p in payload["paths"]]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not paths:
            self._fail("Keine Datei angegeben")
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                known = {p: db_mod.row_for_path(conn, p) for p in paths}
                in_rekordbox = db_mod.rekordbox_paths(conn)
            finally:
                conn.close()
        for p in paths:
            if known[p] is None:
                self._reject_missing(p)
                return

        rb_blocked = False
        if cfg.get("external_rekordbox") and any(p in in_rekordbox for p in paths):
            try:
                rb_blocked = rekordbox_mod.is_running() or rekordbox_mod.agent_running()
            except Exception:                            # noqa: BLE001
                rb_blocked = False

        music_on = bool(cfg.get("external_music"))
        results = []
        renamed: list[tuple[str, str]] = []
        for old in paths:
            row = known[old]
            if not os.path.isfile(old):
                results.append({"path": old, "status": "error", "error": "Datei fehlt"})
                continue
            try:
                new_plan = rename_mod.plan(old, cfg, allow_library=True)
                if new_plan["status"] != "rename":
                    results.append(new_plan)
                    continue
                new = new_plan["new_path"]
                music_ids = []
                if music_on:
                    music_ids = media.music_track_ids(old, row["title"] or Path(old).stem)
                os.rename(old, new)
                try:
                    for pid in music_ids:
                        media.music_set_location(pid, new)
                except Exception:                        # noqa: BLE001
                    os.rename(new, old)                  # kein toter Music-Track
                    raise
            except Exception as exc:                     # noqa: BLE001
                results.append({"path": old, "status": "error", "error": str(exc)})
                continue
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.move_path(conn, old, new)
                finally:
                    conn.close()
            audit_log.log("umbenannt", new, f"vorher {os.path.basename(old)}")
            renamed.append((old, new))
            results.append({"path": old, "status": "renamed", "new_path": new,
                            "name": os.path.basename(new),
                            "music": bool(music_ids)})

        rekordbox_result = None
        rb_moves = [{"path": n, "prior_path": o} for o, n in renamed if o in in_rekordbox]
        if rb_moves:
            conn = db_mod.connect(cfg)
            try:
                metadata = {m["path"]: db_mod.row_for_path(conn, m["path"]) for m in rb_moves}
                metadata = {k: v for k, v in metadata.items() if v is not None}
            finally:
                conn.close()
            queue = rb_blocked
            if not queue:
                try:
                    with self._rekordbox_lock:
                        res = rekordbox_mod.update_relocated_tracks(rb_moves, metadata)
                    rekordbox_result = {"running": False, "queued": 0, **res}
                    for p in res["updated"]:
                        audit_log.log("rekordbox-korrigiert", p, "nach Umbenennen")
                except rekordbox_mod.RekordboxRunning:
                    queue = True        # Rekordbox ging zwischendurch auf
            if queue:
                # Vormerken statt abbrechen: "Rekordbox abgleichen" schreibt
                # die Pfade spaeter gebuendelt (siehe _post_rekordbox_pending_apply()).
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        for m in rb_moves:
                            db_mod.rekordbox_pending_add_rename(conn, m["path"], m["prior_path"])
                        pending = (db_mod.rekordbox_pending_count(conn)
                                   + db_mod.rekordbox_stem_pending_count(conn))
                    finally:
                        conn.close()
                rekordbox_result = {"running": True, "queued": len(rb_moves),
                                    "pending": pending}

        rows = []
        if renamed:
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    rows = self._compact_rows(conn, cfg, [n for _, n in renamed])
                finally:
                    conn.close()
            self._rebuild(cfg)
        new_rows = {n: r for (_, n), r in zip(renamed, rows)}
        for res in results:
            if res["status"] == "renamed":
                res["row"] = new_rows.get(res["new_path"])
        self._json({"ok": True, "results": results, "rekordbox": rekordbox_result})

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

    # ── Stems (Acapella / Instrumental) ────────────────────────────────────

    @staticmethod
    def _stems_model_info() -> dict:
        """Stand des Stem-Modells fuer den Einstellungs-Dialog."""
        return {"available": stems_mod.available(),
                "size": stems_mod.model_size(),
                "download": stems_mod.download_state()}

    def _get_stems_status(self) -> None:
        """Stand des laufenden oder zuletzt fertigen Stem-Auftrags. Fertige
        Vorschau-Dateien werden hier fuer /api/audio freigegeben (der
        Vorschau-Player nutzt denselben Auslieferungsweg wie jeder Track)."""
        state = stemjob_mod.JOBS.state
        for info in (state.get("previews") or {}).values():
            self._unscanned_paths.add(info["path"])
        self._json({"ok": True, **state})

    def _get_stems_download(self) -> None:
        """Eine Hoerprobe als fertige Datei herunterladen (Tags, Cover und
        Name wie beim Speichern, stemjob.export()), ohne sie neben das
        Original zu legen. Die getaggte Kopie wird nach dem Ausliefern
        geloescht, die Hoerprobe bleibt."""
        q = self._query()
        try:
            path, name = stemjob_mod.JOBS.export(
                q.get("job", ""), q.get("kind", ""), cfgmod.load())
        except (stemjob_mod.JobError, tags_mod.TagError, OSError) as exc:
            self._send(404, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            return
        try:
            mime = "audio/aiff" if path.lower().endswith(".aiff") else "audio/mpeg"
            # Name kommt aus Tags (freier Text): ASCII-Ersatz fuer alte
            # Browser plus RFC-5987-Form, ohne Zeichen fuer Header-Injection.
            safe = "".join(c for c in name if c not in ('\r', '\n', '"', "/", "\\"))
            ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{ascii_name}"; '
                             f"filename*=UTF-8''{quote(safe, safe='')}")
            self._security_headers()
            self.end_headers()
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _post_stems(self) -> None:
        """Startet die Trennung (nur Vorschau, es entsteht keine Datei im
        Zielordner). Antwort sofort; Fortschritt ueber /api/stems/status."""
        cfg = cfgmod.load()
        if not stems_mod.available():
            self._fail("Stem-Trennung ist nicht verfügbar (Modell fehlt)", 501)
            return
        payload = self._json_payload()
        if payload is None:
            return
        path = str(payload.get("path") or "")
        kinds = [str(k) for k in (payload.get("kinds") or [])]
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        try:
            state = stemjob_mod.JOBS.start(path, kinds, cfg,
                                           fast=payload.get("quality") == "fast")
        except stemjob_mod.JobError as exc:
            self._fail(str(exc), 409)
            return
        self._json({"ok": True, **state})

    def _post_stems_discard(self) -> None:
        payload = self._json_payload()
        if payload is None:
            return
        kinds = payload.get("kinds")
        removed = stemjob_mod.JOBS.discard(
            str(payload.get("job") or ""),
            [str(k) for k in kinds] if kinds else None)
        for p in removed:
            self._unscanned_paths.discard(p)
        self._json({"ok": True, **stemjob_mod.JOBS.state})

    def _post_stems_approve(self) -> None:
        """
        Gibt EINEN Stem aus der Vorschau frei: Datei neben das Original legen
        (Name nach rename_pattern), Tags und Cover des Haupttracks
        uebernehmen, in die Datenbank aufnehmen, in die Music.app-Bibliothek
        importieren und -- steht der Haupttrack in Rekordbox -- dort samt
        Beatgrid anlegen. Laeuft Rekordbox, wird der Stem vorgemerkt und erst
        ueber "Rekordbox abgleichen" uebernommen.

        Lock-Regel wie _post_add_to_library(): Dateiarbeit, Messung, AppleScript
        und Rekordbox laufen AUSSERHALB von self.lock.
        """
        from .analyzer import analyse_file

        cfg = cfgmod.load()
        payload = self._json_payload()
        if payload is None:
            return
        job_id = str(payload.get("job") or "")
        kind = str(payload.get("kind") or "")
        state = stemjob_mod.JOBS.state
        preview = ((state.get("previews") or {}).get(kind) or {}).get("path", "")
        source = state.get("path") or ""
        try:
            done = stemjob_mod.JOBS.finalize(job_id, kind, cfg)
        except (stemjob_mod.JobError, tags_mod.TagError, OSError) as exc:
            self._fail(str(exc), 409)
            return
        if preview:
            self._unscanned_paths.discard(preview)
        final = done["path"]
        self._unscanned_paths.add(final)
        audit_log.log("stems", final, f"{kind} von {source}")

        # Music.app: best effort -- scheitert nur dieser Teil, bleibt der Stem
        # trotzdem gespeichert (siehe _require_music zu diesem Muster).
        music = None
        if cfg.get("external_music"):
            try:
                loc = media.add_to_music_library([final])
                music = True
                new_path = loc[0] if loc else ""
                if new_path and new_path != final and os.path.isfile(new_path):
                    self._unscanned_paths.discard(final)
                    self._unscanned_paths.add(new_path)
                    final = new_path
                audit_log.log("import", final, "Music-Bibliothek (Stem)")
            except Exception as exc:                  # noqa: BLE001
                music = str(exc)

        # Messen ohne Lock, dann speichern (wie _post_add_to_library).
        fresh = []
        try:
            st = os.stat(final)
            fresh.append(analyse_file((final, st.st_size, st.st_mtime, cfg)))
        except OSError:
            pass
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                if fresh:
                    db_mod.save(conn, fresh)
                has_row = db_mod.row_for_path(conn, final) is not None
                if has_row and music is True:
                    db_mod.mark_music_added(conn, [final])
                if has_row:
                    db_mod.log_event(conn, "track_added", final,
                                     in_music=music is True)
                in_rekordbox = source in db_mod.rekordbox_paths(conn)
                metadata = {final: db_mod.row_for_path(conn, final)}
            finally:
                conn.close()

        # Rekordbox: nur wenn der Haupttrack dort steht, die Einstellung es
        # will und Stem und Haupttrack dasselbe Format haben (sonst passt das
        # kopierte Beatgrid nicht, siehe stemjob.rekordbox_compatible()).
        rb_mode = cfg.get("stems_rekordbox") or "cues"
        rb = {"status": "skipped"}
        if in_rekordbox and has_row and rb_mode != "off":
            try:
                if not stemjob_mod.rekordbox_compatible(source):
                    rb = {"status": "format"}
            except Exception:                         # noqa: BLE001
                rb = {"status": "format"}
        if in_rekordbox and has_row and rb_mode != "off" and rb["status"] != "format":
            busy = False
            try:
                busy = rekordbox_mod.is_running() or rekordbox_mod.agent_running()
            except Exception:                         # noqa: BLE001
                busy = True
            if not busy:
                try:
                    with self._rekordbox_lock:
                        res = rekordbox_mod.add_stems(
                            [{"path": final, "source": source}], metadata,
                            cues=rb_mode == "cues")
                    if res["added"]:
                        rb = {"status": "added", "grid": bool(res["grid"].get(final)),
                              "errors": res["errors"]}
                        with self.lock:
                            conn = db_mod.connect(cfg)
                            try:
                                db_mod.mark_rekordbox_present(conn, [final])
                            finally:
                                conn.close()
                    else:
                        rb = {"status": "error",
                              "error": next(iter(res["errors"].values()), "übersprungen")}
                except rekordbox_mod.RekordboxRunning:
                    busy = True
                except Exception as exc:              # noqa: BLE001
                    rb = {"status": "error", "error": str(exc)}
            if busy:
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        db_mod.rekordbox_stem_pending_add(conn, final, source)
                        pending = (db_mod.rekordbox_pending_count(conn)
                                   + db_mod.rekordbox_stem_pending_count(conn))
                    finally:
                        conn.close()
                rb = {"status": "pending", "pending": pending}

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                rows = self._compact_rows(conn, cfg, [final])
            finally:
                conn.close()
            self._rebuild(cfg)
        self._json({"ok": True, "path": final, "kind": kind, "row": rows[0],
                    "music": music, "rekordbox": rb,
                    "state": stemjob_mod.JOBS.state})

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
            playlist_id = str(payload.get("playlist_id") or "")
            session = str(payload.get("session") or "")
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
        try:
            result = self._rekordbox_push(paths, metadata, playlist_id, session, cfg)
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 500)
            return
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, **result})

    def _rekordbox_push(self, paths: list[str], metadata: dict, playlist_id: str,
                        session: str, cfg: dict) -> dict:
        """Kern von /api/rekordbox-add-playlist, auch fuer verknuepfte Playlisten
        (playlistsync.write_rekordbox()): Tracks in eine bestehende Rekordbox-Playlist,
        neue mit TrackTabs Grid/Phrasen/Cues, My Tags nach Einstellung, danach dieselbe
        Nacharbeit wie nach dem DJ-Knopf. metadata = DB-Zeile je Pfad. Wirft bei
        Fehlern (RekordboxRunning, wenn Rekordbox laeuft)."""
        # Ziel nur noch ueber die ID (Drag & Drop, verknuepfte Playlisten); die
        # fruehere feste Ziel-Playlist aus den Einstellungen gibt es nicht mehr.
        playlist_name = ""
        # My Tags: erkannte Stimmungen (dieselbe Regel wie die Tags-Spalte,
        # genremood.mood_names()) und eigene Tags je Pfad. None = Einstellung
        # aus, die My Tags in Rekordbox bleiben dann unberuehrt.
        mytags: dict | None = None
        if cfg.get("rekordbox_mytags"):
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    moods = db_mod.genremood_map(conn)
                    user_tags = db_mod.user_tags_map(conn)
                finally:
                    conn.close()
            mytags = {p: genremood_mod.mood_names((moods.get(p) or {}).get("m") or {})
                         + user_tags.get(p, []) for p in paths}
        # Tracks, die Rekordbox noch nicht kennt, kommen wie ueber den DJ-Knopf
        # "Zu Rekordbox hinzufuegen" mit TrackTabs Beatgrid, Phrasen und Cues an
        # (ein Weg nach Rekordbox). Vorbereiten misst (ffmpeg), also ohne Lock.
        prepared: dict = {}
        try:
            known = rekordbox_mod.resolve_content_ids(paths)
        except Exception:                            # noqa: BLE001
            known = {p: "" for p in paths}           # im Zweifel nur Tags, wie bisher
        for p in paths:
            if p in known:
                continue
            try:
                prepared[p] = self._dj_rb_prepare(p, "add", list(db_mod.DJ_TRACK_FIELDS), metadata[p], cfg)
            except Exception:                        # noqa: BLE001
                pass                                 # ohne eigene Daten: nur Tags
        # Die beiden Locks NACHEINANDER, nicht ineinander (siehe
        # Reihenfolge-Regel bei _rekordbox_lock): der Rekordbox-Schreib-
        # zugriff braucht die Oberflaeche nicht anzuhalten, und zwischen
        # den beiden Abschnitten liegt nichts, was zusammenhaengen muss --
        # mark_rekordbox_present() ist ein reiner Praesenz-Cache.
        with rekordbox_mod.backup_session(session), self._rekordbox_lock:
            result = rekordbox_mod.add_tracks_to_playlist(
                paths, playlist_name, metadata, playlist_id or None,
                mytags, str(cfg.get("rekordbox_mytag_category") or "").strip() or "TrackTab",
                prepared=prepared)
        if result["added"]:
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.mark_rekordbox_present(conn, result["added"])
                finally:
                    conn.close()
        names = list(db_mod.DJ_TRACK_FIELDS)
        for p, fields in (result.get("written") or {}).items():
            # Wie nach dem DJ-Knopf: Pruefsummen, Sperren, Praesenz.
            try:
                self._dj_rb_after(p, "add", names, {"written": fields}, cfg)
            except Exception as exc:                 # noqa: BLE001
                import sys
                print(f"Rekordbox-Nacharbeit fuer {p}: {exc}", file=sys.stderr)
        if result.get("created"):
            self._rebuild(cfg)
        for p in result["added"]:
            audit_log.log("rekordbox", p, f"zu „{result.get('playlist') or playlist_name}\"")
        return result

    # ── Verknuepfte Playlisten (playlistsync.py) ──────────────────────────────
    _link_sync_locks: dict = {}
    _link_sync_guard = threading.Lock()

    def _get_playlist_links(self) -> None:
        conn = db_mod.connect(cfgmod.load())
        try:
            links = db_mod.playlist_links(conn)
        finally:
            conn.close()
        self._json({"ok": True, "links": links})

    def _post_playlist_links(self) -> None:
        """Verknuepfen/Trennen: {playlist_id, music, rekordbox} mit je einer fremden
        Playlist-ID, "new" (mit dem TrackTab-Namen anlegen) oder null (trennen);
        ein fehlender Schluessel laesst die Seite unveraendert. Nur normale und
        smarte TrackTab-Listen, nur normale fremde Playlisten."""
        payload = self._json_payload()
        if payload is None:
            return
        pid = str(payload.get("playlist_id") or "")
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            node = conn.execute("SELECT id, kind, name FROM playlists WHERE id = ?", (pid,)).fetchone()
        finally:
            conn.close()
        if node is None or node["kind"] not in ("playlist", "smart"):
            self._fail("Nur normale und smarte Playlisten lassen sich verknüpfen")
            return
        changes: dict = {}
        for source in db_mod.LINK_SOURCES:
            if source not in payload:
                continue
            want = payload.get(source)
            if want in (None, ""):
                changes[source] = (None, "")
                continue
            want = str(want)
            try:
                if source == "music":
                    if not cfg.get("external_music"):
                        raise ValueError("Keine Music App in den Einstellungen ausgewählt")
                    if want == "new":
                        ext_id, ext_name = media.music_create_playlist(node["name"], "", False), node["name"]
                    else:
                        hit = next((n for n in media.music_playlists() if n["id"] == want), None)
                        if hit is None or hit["kind"] != "playlist":
                            raise ValueError("Nur normale Music-Playlisten lassen sich verknüpfen")
                        ext_id, ext_name = want, hit["name"]
                else:
                    if want == "new":
                        with self._rekordbox_lock:
                            made = rekordbox_mod.create_playlist(node["name"])
                        ext_id, ext_name = made["id"], made["name"]
                    else:
                        hit = next((n for n in rekordbox_mod.playlists() if n["id"] == want), None)
                        if hit is None or hit["kind"] != "playlist":
                            raise ValueError("Nur normale Rekordbox-Playlisten lassen sich verknüpfen")
                        ext_id, ext_name = want, hit["name"]
            except rekordbox_mod.RekordboxRunning as exc:
                self._json({"ok": False, "error": str(exc), "sticky": True}, 409)
                return
            except Exception as exc:                 # noqa: BLE001
                self._fail(str(exc)[:300], 500)
                return
            changes[source] = (ext_id, ext_name)
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                for source, (ext_id, ext_name) in changes.items():
                    db_mod.set_playlist_link(conn, pid, source, ext_id, ext_name)
                links = db_mod.playlist_links(conn)
            finally:
                conn.close()
        for source, (ext_id, ext_name) in changes.items():
            audit_log.log("playlist-link", "", f"{node['name']} -> {source}: {ext_name or 'getrennt'}")
        self._json({"ok": True, "links": links})

    def _post_playlist_link_sync(self) -> None:
        """Abgleich einer verknuepften Liste (Regel: playlistsync.merge()).
        {playlist_id, paths (nur smarte Listen: Inhalt aus dem Browser), apply,
        allow_guard, skip_import: [pfade], session}. Ohne apply nur der Plan (fuer
        Rueckfragen: guard, needs_import). Music/Rekordbox lesen und schreiben
        ausserhalb von self.lock; Rekordbox, wenn es laeuft, als "pending" auslassen."""
        payload = self._json_payload()
        if payload is None:
            return
        pid = str(payload.get("playlist_id") or "")
        apply = bool(payload.get("apply"))
        with self._link_sync_guard:
            plock = self._link_sync_locks.setdefault(pid, threading.Lock())
        if not plock.acquire(blocking=False):
            self._json({"ok": True, "busy": True})
            return
        try:
            self._link_sync(pid, payload, apply)
        finally:
            plock.release()

    def _link_sync(self, pid: str, payload: dict, apply: bool) -> None:
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            node = conn.execute("SELECT id, kind, name FROM playlists WHERE id = ?", (pid,)).fetchone()
            links = db_mod.playlist_links(conn, pid)
            base = db_mod.link_baselines(conn, pid)
            items = db_mod.playlist_items_map(conn).get(pid, [])
            meta = playlistsync.file_meta(conn)
            music_lib = set(db_mod.music_added_map(conn))
        finally:
            conn.close()
        if node is None or not links:
            self._fail("Keine verknüpfte Playlist")
            return
        known = set(meta)
        kind = "smart" if node["kind"] == "smart" else "playlist"
        if kind == "smart":
            tracktab = [str(p) for p in (payload.get("paths") or []) if str(p) in known]
        else:
            tracktab = list(items)
        resolve = playlistsync.resolver(known)
        reads: dict = {}
        status: dict = {}
        for link in links:
            src, ext_id = link["source"], link["ext_id"]
            try:
                if src == "rekordbox":
                    if rekordbox_mod.is_running() or rekordbox_mod.agent_running():
                        status[src] = "pending"
                        continue
                    reads[src] = playlistsync.read_rekordbox(ext_id, resolve)
                else:
                    if not cfg.get("external_music"):
                        status[src] = "error"
                        continue
                    reads[src] = playlistsync.read_music(ext_id, resolve, playlistsync.cloud_matcher(meta))
                status[src] = "ok"
            except playlistsync.LinkBroken:
                status[src] = "broken"
            except Exception as exc:                 # noqa: BLE001
                status[src] = "error"
                reads.pop(src, None)
                status[f"{src}_error"] = str(exc)[:200]
        first = {link["source"]: link["synced_ts"] is None for link in links}
        plan = playlistsync.merge(kind, tracktab, {s: r["paths"] for s, r in reads.items()}, base, first)
        needs_import = [p for p in plan.get("music", {}).get("add", []) if p not in music_lib] if "music" in reads else []
        summary = {s: {"add": len(plan[s]["add"]), "remove": len(plan[s]["remove"])}
                   for s in ("tracktab", *reads) if s in plan}
        response = {"ok": True, "kind": kind, "summary": summary, "guard": plan["guard"],
                    "needs_import": needs_import, "status": status}
        if not apply or (plan["guard"] and not payload.get("allow_guard")):
            # Stand der fremden Seiten trotzdem festhalten (pending/broken/error).
            self._link_store(pid, links, status, {}, None, cfg)
            self._json({**response, "applied": False})
            return
        skip = {str(p) for p in (payload.get("skip_import") or [])}
        session = str(payload.get("session") or "") or uuid.uuid4().hex
        results: dict = {}
        new_base: dict = {}
        changed_items = False
        if kind != "smart" and plan["items"] != tracktab:
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.set_playlist_items(conn, pid, plan["items"])
                finally:
                    conn.close()
            changed_items = True
        ext = {link["source"]: link["ext_id"] for link in links}
        if "music" in reads:
            try:
                w = playlistsync.write_music(ext["music"], plan["music"], reads["music"], meta, skip)
                results["music"] = {k: v for k, v in w.items() if k != "state"}
                new_base["music"] = w["state"]
            except Exception as exc:                 # noqa: BLE001
                status["music"] = "error"
                status["music_error"] = str(exc)[:200]
        if "rekordbox" in reads:
            def push(paths):
                rows = {p: self._known_file(p) for p in paths}
                rows = {p: r for p, r in rows.items() if r is not None}
                return self._rekordbox_push(list(rows), rows, ext["rekordbox"], session, cfg)
            try:
                with rekordbox_mod.backup_session(session):
                    w = playlistsync.write_rekordbox(ext["rekordbox"], plan["rekordbox"], reads["rekordbox"],
                                                     plan["items"], push, self._rekordbox_lock)
                results["rekordbox"] = {k: v for k, v in w.items() if k != "state"}
                new_base["rekordbox"] = w["state"]
            except rekordbox_mod.RekordboxRunning:
                status["rekordbox"] = "pending"
            except Exception as exc:                 # noqa: BLE001
                status["rekordbox"] = "error"
                status["rekordbox_error"] = str(exc)[:200]
        new_base["tracktab"] = set(plan["items"])
        links_now = self._link_store(pid, links, status, new_base, plan, cfg)
        if changed_items:
            self._rebuild(cfg)
        audit_log.log("playlist-sync", "", f"{node['name']}: " + ", ".join(
            f"{s} +{len(plan[s]['add'])}/-{len(plan[s]['remove'])}" for s in summary))
        self._json({**response, "applied": True, "items": plan["items"], "results": results,
                    "links": links_now})

    def _link_store(self, pid: str, links: list, status: dict, new_base: dict, plan, cfg: dict) -> list:
        """Baselines und Status unter self.lock festhalten. Eine Seite bekommt nur dann
        eine neue Baseline, wenn in sie geschrieben wurde."""
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                for side, paths in new_base.items():
                    db_mod.set_link_baseline(conn, pid, side, paths)
                for link in links:
                    src = link["source"]
                    state = status.get(src, "ok")
                    db_mod.set_link_state(conn, pid, src, "ok" if state == "ok" else state,
                                          status.get(f"{src}_error"), synced=src in new_base)
                return db_mod.playlist_links(conn, None)
            finally:
                conn.close()

    def _rekordbox_write(self, fn) -> dict | None:
        """Gemeinsamer Rahmen der Rekordbox-Bearbeitungen: eigener Lock (nicht
        self.lock, siehe Reihenfolge-Regel bei _rekordbox_lock), laufendes
        Rekordbox/Agent als bleibender Hinweis, alles andere als Fehler.
        Liefert das Ergebnis oder None (Antwort ist dann schon gesendet)."""
        try:
            with self._rekordbox_lock:
                return fn()
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 500)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
        return None

    def _json_payload(self) -> dict | None:
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("kein Objekt")
            return payload
        except (ValueError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return None

    def _post_rekordbox_remove_entries(self) -> None:
        """Einzelne Eintraege (DjmdSongPlaylist.ID) aus einer Rekordbox-
        Playlist entfernen -- der Track bleibt in der Sammlung. Adressiert
        wird ueber Rekordbox' eigene IDs statt ueber Pfade, deshalb ohne
        _known_file(): auch Titel ohne lokale Datei lassen sich entfernen."""
        payload = self._json_payload()
        if payload is None:
            return
        pid = str(payload.get("playlist_id") or "")
        entry_ids = [str(e) for e in (payload.get("entry_ids") or []) if e]
        if not pid or not entry_ids:
            self._fail("Keine Playlist oder keine Einträge angegeben")
            return
        result = self._rekordbox_write(
            lambda: rekordbox_mod.remove_entries_from_playlist(pid, entry_ids))
        if result is None:
            return
        audit_log.log("rekordbox", "", f"{len(result['removed'])} aus „{result['playlist']}\" entfernt")
        self._json({"ok": True, **result})

    def _post_rekordbox_playlist_edit(self) -> None:
        """Playlisten-Baum in Rekordbox bearbeiten: op "create" (name, parent_id,
        folder), "rename" (id, name), "delete" (id, weich wie Rekordbox selbst, siehe
        rekordbox.delete_playlist()). Rekordbox muss geschlossen sein."""
        payload = self._json_payload()
        if payload is None:
            return
        op = str(payload.get("op") or "")
        node_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "").strip()
        if op == "create":
            parent = str(payload.get("parent_id") or "") or None
            folder = bool(payload.get("folder"))
            fn = lambda: rekordbox_mod.create_playlist(name, parent, folder)   # noqa: E731
        elif op == "rename" and node_id:
            fn = lambda: rekordbox_mod.rename_playlist(node_id, name)          # noqa: E731
        elif op == "delete" and node_id:
            fn = lambda: rekordbox_mod.delete_playlist(node_id)                # noqa: E731
        elif op == "move" and node_id:
            parent = str(payload.get("parent_id") or "") or None
            before = str(payload.get("before_id") or "") or None
            fn = lambda: rekordbox_mod.move_playlist(node_id, parent, before)   # noqa: E731
        else:
            self._fail("Ungültige Anfrage")
            return
        if op in ("create", "rename") and not name:
            self._fail("Kein Name angegeben")
            return
        result = self._rekordbox_write(fn)
        if result is None:
            return
        audit_log.log("rekordbox", "", f"Playlist {op}: „{result.get('name') or name}\"")
        self._json({"ok": True, **result})

    def _music_call(self, fn, *args):
        """AppleScript an Music.app ausserhalb von self.lock; Fehler als Antwort.
        Liefert (True, Ergebnis) oder (False, None) -- dann ist die Antwort raus."""
        try:
            return True, fn(*args)
        except Exception as exc:                     # noqa: BLE001
            self._json({"ok": False, "error": str(exc)[:300]}, 500)
            return False, None

    def _post_music_playlist_edit(self) -> None:
        """Playlisten-Baum in Music.app bearbeiten: op "create" (name, parent_id,
        folder), "rename" (id, name), "delete" (id)."""
        payload = self._json_payload()
        if payload is None or not self._require_music(cfgmod.load()):
            return
        op = str(payload.get("op") or "")
        node_id = str(payload.get("id") or "")
        name = str(payload.get("name") or "").strip()
        if op in ("create", "rename") and not name:
            self._fail("Kein Name angegeben")
            return
        if op == "create":
            ok, new_id = self._music_call(media.music_create_playlist, name,
                                          str(payload.get("parent_id") or ""), bool(payload.get("folder")))
            result = {"id": new_id, "name": name}
        elif op == "rename" and node_id:
            ok, _ = self._music_call(media.music_rename_playlist, node_id, name)
            result = {"id": node_id, "name": name}
        elif op == "delete" and node_id:
            ok, _ = self._music_call(media.music_delete_playlist, node_id)
            result = {"id": node_id}
        elif op == "move" and node_id:
            parent = str(payload.get("parent_id") or "")
            ok, _ = self._music_call(media.music_move_playlist, node_id, parent)
            result = {"id": node_id, "parent": parent or None}
        else:
            self._fail("Ungültige Anfrage")
            return
        if not ok:
            return
        audit_log.log("music", "", f"Playlist {op}: „{name or node_id}\"")
        self._json({"ok": True, **result})

    def _post_music_playlist_add(self) -> None:
        """Bibliothekstracks einer Music.app-Playlist hinzufuegen. Nur Pfade mit
        DB-Zeile; was Music.app nicht in seiner Mediathek findet, steht in
        "missing" (der Client importiert solche Tracks vorher)."""
        payload = self._json_payload()
        if payload is None or not self._require_music(cfgmod.load()):
            return
        pid = str(payload.get("playlist_id") or "")
        paths = [str(p) for p in (payload.get("paths") or []) if p]
        if not pid or not paths:
            self._fail("Keine Playlist oder keine Tracks angegeben")
            return
        items = []
        for p in paths:
            row = self._known_file(p)
            if row is None:
                self._reject_missing(p)
                return
            # Interpret/Groesse/Dauer fuer Eintraege, die Music nur noch in der Cloud fuehrt.
            items.append((p, row["title"] or Path(p).stem, row["artist"] or "",
                          int(row["size"] or 0), float(row["duration_s"] or 0)))
        ok, flags = self._music_call(media.music_add_to_playlist, pid, items)
        if not ok:
            return
        added = [p for p, hit in zip(paths, flags) if hit]
        missing = [p for p, hit in zip(paths, flags) if not hit]
        audit_log.log("music", "", f"{len(added)} zu Playlist {pid} hinzugefügt")
        self._json({"ok": True, "added": added, "missing": missing})

    def _post_music_playlist_remove(self) -> None:
        """Tracks (persistent IDs) aus einer Music.app-Playlist entfernen, nicht aus
        der Mediathek."""
        payload = self._json_payload()
        if payload is None or not self._require_music(cfgmod.load()):
            return
        pid = str(payload.get("playlist_id") or "")
        ids = [str(i) for i in (payload.get("track_ids") or []) if i]
        if not pid or not ids:
            self._fail("Keine Playlist oder keine Tracks angegeben")
            return
        ok, removed = self._music_call(media.music_remove_from_playlist, pid, ids)
        if not ok:
            return
        audit_log.log("music", "", f"{removed} aus Playlist {pid} entfernt")
        self._json({"ok": True, "removed": removed})

    def _post_rekordbox_reorder(self) -> None:
        """Neue Gesamtreihenfolge einer Rekordbox-Playlist (Eintrags-IDs)."""
        payload = self._json_payload()
        if payload is None:
            return
        pid = str(payload.get("playlist_id") or "")
        entry_ids = [str(e) for e in (payload.get("entry_ids") or []) if e]
        if not pid or not entry_ids:
            self._fail("Keine Playlist oder keine Einträge angegeben")
            return
        result = self._rekordbox_write(lambda: rekordbox_mod.reorder_playlist(pid, entry_ids))
        if result is None:
            return
        audit_log.log("rekordbox", "", f"„{result['playlist']}\" umsortiert")
        self._json({"ok": True, **result})

    def _content_ids_from_payload(self, payload: dict) -> list[str]:
        """content_ids direkt (aus einer Rekordbox-Playlist) und/oder Pfade
        (Bibliothekszeilen kennen nur den Pfad) -> IDs lebender Tracks."""
        ids = [str(c) for c in (payload.get("content_ids") or []) if c]
        paths = [str(p) for p in (payload.get("paths") or []) if p]
        if paths:
            ids += list(rekordbox_mod.resolve_content_ids(paths).values())
        return list(dict.fromkeys(ids))

    def _post_rekordbox_membership(self) -> None:
        """Wie viele Rekordbox-Playlisten ein Track betrifft -- nur lesend,
        fuer den Warndialog vor dem Entfernen aus der Sammlung."""
        payload = self._json_payload()
        if payload is None:
            return
        try:
            ids = self._content_ids_from_payload(payload)
            counts = rekordbox_mod.collection_membership(ids) if ids else {}
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "found": len(ids), "playlists": counts})

    def _post_rekordbox_remove_collection(self) -> None:
        """Tracks aus der Rekordbox-Sammlung entfernen (wie Rekordbox'
        "Von der Sammlung entfernen"); die Audiodateien bleiben liegen."""
        payload = self._json_payload()
        if payload is None:
            return
        try:
            ids = self._content_ids_from_payload(payload)
        except Exception as exc:                     # noqa: BLE001
            self._fail(str(exc), 500)
            return
        if not ids:
            self._fail("Keiner der Tracks steht in der Rekordbox-Sammlung")
            return
        result = self._rekordbox_write(lambda: rekordbox_mod.remove_from_collection(ids))
        if result is None:
            return
        paths = [r["path"] for r in result["removed"] if r["path"]]
        if paths:
            # Erst NACH dem Rekordbox-Lock (Reihenfolge-Regel). Der Haken und
            # alles andere vom Rekordbox-Stand soll sofort verschwinden.
            with self.lock:
                conn = db_mod.connect(cfgmod.load())
                try:
                    db_mod.forget_rekordbox_tracks(conn, paths)
                finally:
                    conn.close()
            for p in paths:
                wavedetail_mod.forget(p)
        for p in paths:
            audit_log.log("rekordbox", p, "aus Sammlung entfernt")
        self._json({"ok": True, **result, "paths": paths})

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

    def _post_rekordbox_import(self) -> None:
        """Rekordbox' Beatgrid, Phrasen und Cues festhalten, gestueckelt (der Client ruft
        mit wachsendem offset, je Track rund 50 ms). Vorhandenes bleibt: gefuellt wird nur,
        was noch leer ist, Abweichungen werden nur vermerkt (_rb_snapshot_sync()).
        Rekordbox lesen laeuft ausserhalb von self.lock."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            offset, limit = int(payload.get("offset") or 0), int(payload.get("limit") or 100)
        except (ValueError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            paths = sorted(db_mod.rekordbox_paths(conn))
            todo = [(p, db_mod.get_rb_snapshot(conn, p)) for p in paths[offset:offset + limit]]
        finally:
            conn.close()
        imported, changed, updates = 0, 0, []
        for path, snap in todo:
            rb_stamp = rekordbox_mod.track_stamp(path)
            stamp = _rb_stamp(rb_stamp)
            if stamp is None or (snap and snap["stamp"] == stamp):
                changed += bool(snap and snap["changed"])
                continue
            state = rekordbox_mod.track_state(path)
            if not state["available"] or not state["present"]:
                continue
            reread = _rb_snapshot_reread(snap, state, rb_stamp)
            if reread is not None:
                # Nur die Lesart ist neu: still nachziehen, auch eine fruehere Uebernahme.
                changed += bool(reread["changed"])
                updates.append((path, reread))
                continue
            before = {f: snap[f] is not None for f in db_mod.DJ_TRACK_FIELDS} if snap else {}
            out = _rb_snapshot_sync(snap, state)
            out["stamp"] = stamp
            imported += any(out[f] is not None and not before.get(f) for f in db_mod.DJ_TRACK_FIELDS)
            changed += bool(out["changed"])
            updates.append((path, out))
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                for path, out in updates:
                    db_mod.save_rb_snapshot(conn, path, out)
            finally:
                conn.close()
        # Fruehere Uebernahmen dieser Tracks folgen der neuen Lesart -- im Hintergrundlauf
        # nach dem Abgleich (je Track eine Dekodierung), nicht hier.
        GRID_SEED.refresh_later(p for p, out in updates if out.get("reread"))
        self._json({"ok": True, "total": len(paths), "next": offset + len(todo),
                    "imported": imported, "changed": changed})

    def _post_rekordbox_tags_sync(self) -> None:
        """Abgleich-Schalter "Metadaten nach Rekordbox": TrackTabs Metadaten fuer alle
        Tracks der Sammlung schreiben, deren Metadaten abweichen (rekordbox.tag_diff_map(),
        dieselbe Pruefung wie die Spalte Rekordbox). NUR Metadaten -- Beatgrid, Phrasen
        und Cues bleiben dem Knopf "Rekordbox Update" vorbehalten (Nutzervorgabe).
        Halbes/doppeltes Tempo schreibt _write_tags() nie, es bleibt als Hinweis stehen.
        Eine Sicherung fuer den ganzen Lauf (backup_session), _rekordbox_lock je
        Abschnitt, damit andere Rekordbox-Schreibwege zwischendurch drankommen."""
        cfg = cfgmod.load()
        rows = _tag_rows(cfg)
        todo = [p for p, fields in rekordbox_mod.tag_diff_map(rows).items() if _tag_update_needed(fields)]
        done, errors, stopped = [], {}, ""
        session = f"tags-sync-{time.time():.0f}"
        try:
            with rekordbox_mod.backup_session(session):
                for i in range(0, len(todo), 50):
                    items = [{"path": p, "cues": None, "beats": None, "phrases": None,
                              "audio_start_ms": 0, "tags": rows[p]} for p in todo[i:i + 50]]
                    with self._rekordbox_lock:
                        result = rekordbox_mod.update_tracks(items)
                    for p, res in result["results"].items():
                        if res.get("written"):
                            done.append(p)
                            audit_log.log("rekordbox-tags-sync", p, ",".join(res["written"]))
                    errors.update(result["errors"])
                    if result["stopped"]:
                        stopped = result["stopped"]
                        break
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 409)
            return
        except Exception as exc:                      # noqa: BLE001
            self._fail(str(exc), 500)
            return
        self._json({"ok": True, "checked": len(todo), "updated": len(done),
                    "errors": errors, "stopped": stopped})

    def _post_rekordbox_pending_apply(self) -> None:
        """Schreibt alle vorgemerkten Aenderungen in EINEM Durchlauf nach
        master.db: Tag-Aenderungen (siehe _rename_tag_value()) und freigegebene
        Stems (siehe _post_stems_approve()) -- ein Oeffnen, ein Backup, ein
        Commit je Art. Rekordbox laeuft -> sticky-Fehler, die Vormerkungen
        bleiben erhalten. Bearbeitete Pfade (auch 'nicht gefunden') werden
        abgeraeumt, nur Einzelfehler bleiben stehen."""
        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                pending_items = db_mod.rekordbox_pending_list(conn)
                paths = [i["path"] for i in pending_items]
                metadata = {p: db_mod.row_for_path(conn, p) for p in paths}
                stem_items = db_mod.rekordbox_stem_pending_list(conn)
                stem_meta = {i["path"]: db_mod.row_for_path(conn, i["path"])
                             for i in stem_items}
            finally:
                conn.close()
        metadata = {k: v for k, v in metadata.items() if v is not None}
        if not paths and not stem_items:
            self._json({"ok": True, "updated": [], "not_found": [], "errors": {},
                        "pending": 0, "stems_added": []})
            return
        result = {"updated": [], "not_found": [], "errors": {}}
        stem_result = {"added": [], "skipped": [], "errors": {}, "grid": {}}
        try:
            with self._rekordbox_lock:
                if paths:
                    result = rekordbox_mod.update_relocated_tracks(
                        pending_items, metadata)
                if stem_items:
                    # Stems ohne DB-Zeile (Datei inzwischen weg) gibt es nicht
                    # mehr -- sie wandern ohne Eintrag aus der Warteschlange.
                    todo = [i for i in stem_items
                            if stem_meta.get(i["path"]) is not None
                            and os.path.isfile(i["path"])]
                    if todo:
                        stem_result = rekordbox_mod.add_stems(
                            todo, {k: v for k, v in stem_meta.items() if v is not None},
                            cues=(cfg.get("stems_rekordbox") or "cues") == "cues")
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 500)
            return
        except Exception as exc:                      # noqa: BLE001
            self._fail(str(exc), 500)
            return
        done = list(result["updated"]) + list(result["not_found"])
        stems_done = (list(stem_result["added"]) + list(stem_result["skipped"])
                      + [i["path"] for i in stem_items
                         if stem_meta.get(i["path"]) is None
                         or not os.path.isfile(i["path"])])
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.rekordbox_pending_clear(conn, done)
                db_mod.rekordbox_stem_pending_clear(conn, stems_done)
                if stem_result["added"]:
                    db_mod.mark_rekordbox_present(conn, list(stem_result["added"]))
                remaining = (db_mod.rekordbox_pending_count(conn)
                             + db_mod.rekordbox_stem_pending_count(conn))
            finally:
                conn.close()
        for p in result["updated"]:
            audit_log.log("rekordbox-tags", p, "ueber Abgleich (vorgemerkt)")
        for p in stem_result["added"]:
            audit_log.log("rekordbox-stem", p, "ueber Abgleich (vorgemerkt)")
        self._json({"ok": True, **result, "pending": remaining,
                    "stems_added": stem_result["added"],
                    "stems_errors": stem_result["errors"],
                    "stems_grid": stem_result["grid"]})

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
        # Nicht _known_file(): das verlangt eine vorhandene Datei. Liegt sie
        # schon im Papierkorb (Music.app/Rekordbox/Finder waren schneller,
        # oder ein frueherer Aufruf hier scheiterte nach dem Music.app-
        # Entfernen, siehe unten), bliebe die Zeile sonst fuer immer stehen.
        # Freigabe weiterhin nur fuer Pfade mit DB-Zeile; ohne Datei wird
        # unten nichts angefasst ausser der eigenen DB.
        conn = db_mod.connect(cfgmod.load())
        try:
            row = db_mod.row_for_path(conn, path)
        finally:
            conn.close()
        if row is None or (os.path.lexists(path) and not os.path.isfile(path)):
            self._reject_missing(path)
            return

        cfg = cfgmod.load()

        # Muss VOR move_to_trash() laufen: remove_from_music_library() liest
        # 'location' je Kandidat einzeln von der noch vorhandenen Datei --
        # nach dem Papierkorb waere das nicht mehr moeglich, die Music.app-
        # Karteileiche bliebe garantiert stehen (genau das war der Bug).
        # Laeuft bei eingestellter Music App IMMER, nicht nur fuer Pfade in
        # 'music_added': der Track kann auch ausserhalb von TrackTab (oder
        # vor dem ersten Abgleich) in Music.app gelandet sein. Sicher, weil
        # nur ein Treffer mit exakt gleichem Dateipfad geloescht wird.
        # Titel mit Dateiname als Rueckfallebene: Music.app selbst benennt
        # taglose Importe so, siehe remove_from_music_library().
        library_error = None
        cloud_note = False
        removed_from_music = False
        if cfg.get("external_music"):
            title = row["title"] or Path(path).stem
            try:
                count, cloud_note = media.remove_from_music_library(path, title)
                if count > 0:
                    removed_from_music = True
                    audit_log.log("music-entfernt", path)
            except Exception as exc:                     # noqa: BLE001
                library_error = str(exc)

        # Fehlt die Datei schon, gibt es nichts mehr in den Papierkorb zu legen
        # -- 'POSIX file ... as alias' scheitert dann mit -1700. Haeufigster
        # Anlass: Music.app legt eine Datei seines Medienordners beim
        # Entfernen aus der Bibliothek (oben) selbst in den Papierkorb. Die DB-Zeile und die Merkmale
        # raeumen wir trotzdem ab, sonst bliebe die Zeile nicht entfernbar.
        already_gone = not os.path.lexists(path)
        if not already_gone:
            try:
                media.move_to_trash(path)
            except Exception as exc:                     # noqa: BLE001
                # Music.app legt die Datei nach 'delete' zeitversetzt in den
                # Papierkorb -- zwischen lexists() und Finder-Aufruf kann sie
                # verschwinden. Dann ist das Ziel erreicht, kein Fehler.
                if os.path.lexists(path):
                    self._fail(f"Papierkorb: {exc}", 500)
                    return
                already_gone = True
        audit_log.log("papierkorb-durch-music" if (already_gone and removed_from_music)
                      else "datei-fehlt-entfernt" if already_gone else "papierkorb", path)

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Alle Caches und Messungen, nicht nur die Zeile selbst --
                # siehe db.forget_file().
                db_mod.forget_file(conn, path)
                total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
            # Ohne Neubacken zeigt tracktab.html die geloeschte Zeile nach dem
            # naechsten Neuladen weiter an, siehe /api/prune.
            self._rebuild(cfg)
        self._json({"ok": True, "total": total, "removed_from_music": removed_from_music,
                     "music_error": library_error, "music_cloud_note": cloud_note,
                     "already_gone": already_gone})

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
                        was_music_added = db_mod.is_music_added(conn, path)
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
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
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

    def _post_file_writable(self) -> None:
        """Welche Dateien TrackTab nicht beschreiben kann (Finder "Geschuetzt"
        bzw. fehlende Schreibrechte, siehe tags.write_blocker()). Der Tags-
        Dialog sperrt damit seine Felder, bevor ein Speichern scheitert.

        Antwort: {"blocked": {pfad: "locked"|"readonly"}} -- nur blockierte
        Pfade. Unbekannte Pfade fallen still heraus (keine Auskunft ueber
        Dateien ausserhalb der Bibliothek). Nur stat()/access(), ohne Lock.
        """
        try:
            payload = json.loads(self._body(2_000_000).decode("utf-8"))
            paths = [str(p) for p in payload["paths"]][:5000]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        blocked = {}
        for path in paths:
            if not self._authorize_fast(path):
                continue
            reason = tags_mod.write_blocker(path)
            if reason:
                blocked[path] = reason
        self._json({"ok": True, "blocked": blocked})

    def _post_tags(self) -> None:
        """Titel/Interpret/Album/Albumkuenstler/Komponist/Genre/Jahr/BPM/
        Tonart/Kommentar/Tracknummer/-gesamtzahl in die Datei UND die DB schreiben.
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
        if "compilation" in payload:
            fields["compilation"] = 1 if payload["compilation"] else 0
        # Tonart: in die Datei geht sie in der gewaehlten Schreibweise
        # (Einstellung key_notation), in der DB bleibt sie Camelot.
        key_canon = None
        if "key" in payload:
            key_canon = tags_mod.normalize_key(str(payload["key"] or ""))
            fields["key"] = tags_mod.key_file_value(key_canon, cfgmod.load())
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

        # Auffaelligkeiten frisch bilden (nur mutagen, ms) -- sonst bliebe
        # z.B. "Kuenstler fehlt" nach dem Setzen des Tags stehen.
        new_issues = taganomaly_mod.detect(
            path, (row["codec_family"] if row else "") or "")

        if row is None:
            # Noch nicht gescannt (siehe _authorize_path) -- es gibt keine
            # DB-Zeile zum Aktualisieren, der Client pflegt seine lokale
            # Kopie (drops-Array) selbst nach.
            self._json({"ok": True, "row": None, "issues": new_issues})
            return

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.update_tags(
                    conn, path,
                    {**fields, "key": key_canon, "key_raw": fields["key"]}
                    if key_canon is not None else fields)
                db_mod.update_tag_issues(conn, path, new_issues)
                db_mod.refresh_stat(conn, path)
                row = db_mod.row_for_path(conn, path)
                ignored = db_mod.ignored_paths(conn)
                corrected = db_mod.corrected_paths(conn)
                rekordbox = db_mod.rekordbox_paths(conn)
                music_added = db_mod.music_added_map(conn)
                dup_dismissed = db_mod.dup_dismissed_paths(conn)
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
        self._json({"ok": True, "row": compact})

    def _post_bpmkey(self) -> None:
        """BPM + Tonart per libsonare messen und nach den Einstellungen
        (bpmkey_*) in die Datei schreiben -- fuer EINEN Pfad (der Client ruft je
        Datei einmal auf, nur so gibt es "x von y"). Wie _post_tags(): DB-Zeile
        optional, Einzelpruefungen haben keine (dann row=None, der Client baut
        seine Zeile aus 'fields').

        Messen (ffmpeg + libsonare, rund 2-4 s) laeuft AUSSERHALB von self.lock,
        der Lock umschliesst nur das DB-Update (siehe CLAUDE.md, Lock-Regel)."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not bpmkey_mod.available():
            self._json({"ok": True, "status": "unavailable", "row": None})
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return

        cfg = cfgmod.load()
        # BPM haengt am Beatgrid, die Tonart laeuft im selben Messgang mit.
        if self._analysis_locks(path, cfg)["grid"]:
            self._json({"ok": True, "status": "locked", "row": None})
            return
        minutes = int(cfg.get("bpmkey_max_minutes") or 10)
        try:
            result = bpmkey_mod.analyse(path, minutes * 60,
                                        cfg.get("measure_bpm", True),
                                        cfg.get("measure_key", True))
        except bpmkey_mod.TooLongError:
            # Kein Fehler: zu lange Dateien (DJ-Mixe) werden bewusst uebersprungen.
            self._json({"ok": True, "status": "skipped", "reason": "too_long",
                        "minutes": minutes, "row": None})
            return
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Analyse fehlgeschlagen: {exc}", 500)
            return
        # Energie der Genre-/Stimmungs-Analyse fuer den gemeinsamen Kommentar-
        # Eintrag ("8A - 128 - E7") -- kurz unter Lock gelesen, wie jede DB-Arbeit.
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                energy = db_mod.genremood_energy(conn, path)
            finally:
                conn.close()
        fields = bpmkey_mod.plan_fields(cfg, result, tags_mod.read_extra(path), energy)
        base = {"ok": True, "bpm": result["bpm"],
                "key": tags_mod.key_file_value(result["camelot"], cfg),
                "confidence": round(result["confidence"], 2)}
        if not fields:
            self._json({**base, "status": "unchanged", "fields": {}, "row": None})
            return

        try:
            tags_mod.write_tags(path, fields)
        except tags_mod.TagError as exc:
            self._fail(str(exc), 500)
            return
        audit_log.log("bpmkey", path, ", ".join(sorted(fields)))
        new_issues = taganomaly_mod.detect(
            path, (row["codec_family"] if row else "") or "")

        if row is None:
            self._json({**base, "status": "written", "fields": fields,
                        "row": None, "issues": new_issues})
            return

        db_fields = dict(fields)
        if "key" in fields:
            db_fields["key"] = result["camelot"]
            db_fields["key_raw"] = fields["key"]
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.update_tags(conn, path, db_fields)
                db_mod.update_tag_issues(conn, path, new_issues)
                db_mod.refresh_stat(conn, path)
                compact = self._compact_rows(conn, cfg, [path])[0]
            finally:
                conn.close()
            self._rebuild(cfg)
        self._json({**base, "status": "written", "fields": fields, "row": compact})

    def _get_dj_wave(self) -> None:
        """Wellenform des DJ-Editors nach dj_waveform_source und dj_waveform_style,
        je Spalte vier Bytes (wavedetail.STYLED_RATE). X-Wave-Source sagt, woher sie
        wirklich stammt: rekordbox oder tracktab. X-Wave-Lead = Versatz der Rekordbox-Daten zur eigenen Zeitachse."""
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        wanted = str(cfg.get("dj_waveform_source") or "fallback")
        style = str(cfg.get("dj_waveform_style") or "rgb")
        # Standard ist eine Farbe, die Hoehen stecken in den RGB-Spalten.
        columns = "3band" if style == "3band" else "rgb"
        mtime = os.path.getmtime(path)
        data, source, lead = None, "tracktab", 0.0
        try:
            if wanted != "tracktab":
                data = rekordbox_mod.track_wave_detail(path, columns)
                if data:
                    source = "rekordbox"
                    lead = wavedetail_mod.lead(
                        path, mtime, lambda mono, sr: rekordbox_mod.playback_lead(path, mono, sr),
                        rekordbox_mod.lead_stamp(path))
            if not data:
                data = wavedetail_mod.styled(path, mtime, columns)
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Wellenform fehlgeschlagen: {exc}", 500)
            return
        self._send(200, data, "application/octet-stream",
                   {"X-Wave-Rate": str(wavedetail_mod.STYLED_RATE), "X-Wave-Lead": f"{lead:.4f}",
                    "X-Wave-Source": source, "X-Wave-Style": style, "X-Wave-Wanted": wanted})

    def _get_wave_phrases(self) -> None:
        """Phrasen fuer die Phrasen-Leiste der kleinen Wellenformen (Playlist-
        Generator): nur TrackTabs eigener Stand -- Bearbeitung vor eigener Messung
        bzw. Uebernahme (gridstore.own_inputs(), dieselbe Quelle wie Auto Cue).
        Liest nur, uebernimmt nichts aus Rekordbox und misst nichts nach, darf
        also fuer viele Tracks hintereinander laufen. Zeiten in Sekunden auf der
        eigenen Zeitachse, wie die Cues mit cues_own."""
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            own = gridstore.own_inputs(conn, path)
        finally:
            conn.close()
        self._json({"phrases": own["phrases"] or []})

    def _get_wave_color(self) -> None:
        """Farbige Gesamtansicht fuer Bearbeiten und Player nach waveform_style, immer
        aus TrackTabs eigener Messung, nie aus Rekordbox. Standard = grau, dann null.
        Rechnet ausserhalb von self.lock, das Ergebnis bleibt in der DB."""
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        style = str(cfg.get("waveform_style") or "rgb")
        if style not in ("rgb", "3band"):
            self._json({"waveform": None})
            return
        mtime = os.path.getmtime(path)
        conn = db_mod.connect(cfg)
        try:
            data = db_mod.waveform_color_get(conn, path, mtime, style)
        finally:
            conn.close()
        if data is None:
            try:
                data = wavedetail_mod.overview(path, mtime, style)
            except Exception as exc:                   # noqa: BLE001
                self._fail(f"Waveform fehlgeschlagen: {exc}", 500)
                return
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.waveform_color_put(conn, path, mtime, style, data)
                finally:
                    conn.close()
        self._json({"waveform": _wave_color_json(data, style)})

    @staticmethod
    def _wave_lead(path: str, cfg: dict) -> float:
        """Versatz zu Rekordbox in s, siehe wavedetail.lead()."""
        lead_fn, stamp = None, ""
        if str(cfg.get("beatgrid_source") or "fallback") != "tracktab":
            lead_fn = lambda mono, sr: rekordbox_mod.playback_lead(path, mono, sr)
            stamp = rekordbox_mod.lead_stamp(path)
        return wavedetail_mod.lead(path, os.path.getmtime(path), lead_fn, stamp)

    def _dj_track_payload(self, path: str, cfg: dict) -> dict:
        """Was der DJ-Editor je Track braucht, alles auf der eigenen Zeitachse:
        je Beatgrid, Phrasen und Cues die Bearbeitung oder sonst die Quelle nach
        beatgrid_source. Rekordbox-Werte werden hier um den
        gemessenen Versatz korrigiert, damit der Client nur eine Zeitachse kennt
        und das Speichern nichts doppelt verschiebt. Dazu der Abgleich-Stand:
        'changed' = in Rekordbox geaendert, seit zuletzt abgeglichen wurde,
        'pending' = eigene Bearbeitung noch nicht dorthin geschrieben."""
        source = str(cfg.get("beatgrid_source") or "fallback")
        state = rekordbox_mod.track_state(path)
        # Festgehaltener Stand mit alter Lesart, Rekordbox unveraendert: still nachlesen.
        reread = None
        if state.get("available") and state.get("present"):
            conn = db_mod.connect(cfg)
            try:
                snap = db_mod.get_rb_snapshot(conn, path)
            finally:
                conn.close()
            reread = _rb_snapshot_reread(snap, state, rekordbox_mod.track_stamp(path)) if snap else None
            if reread is not None:
                with self.lock:
                    conn = db_mod.connect(cfg)
                    try:
                        db_mod.save_rb_snapshot(conn, path, reread)
                    finally:
                        conn.close()
        # Fehlt der eigene Stand, gilt Rekordbox' Grid ab jetzt als TrackTabs (gridstore);
        # eine fruehere Uebernahme folgt einer neuen Lesart.
        gridstore.seed_from_rekordbox(cfg, path, self.lock, state, refresh=reread is not None)
        conn = db_mod.connect(cfg)
        try:
            edits = db_mod.get_dj_track(conn, path)
            base = db_mod.get_dj_base(conn, path)
            locks = db_mod.dj_locks(conn, path)
            stored = db_mod.get_phrases(conn, path) if source != "rekordbox" else None
            snap = db_mod.get_rb_snapshot(conn, path)
        finally:
            conn.close()
        use_rb = source != "tracktab"

        # Festgehaltener Stand vor dem Live-Stand: Aenderungen in Rekordbox kommen nur per Knopf.
        def rb_value(field):
            if not use_rb:
                return []
            return snap[field] if snap and snap[field] is not None else state[field]

        rb_grid, rb_phrases, rb_cues = rb_value("grid"), rb_value("phrases"), rb_value("cues")

        lead = 0.0
        if rb_grid or rb_phrases or rb_cues:
            try:
                lead = self._wave_lead(path, cfg)
            except Exception:                          # noqa: BLE001
                lead = 0.0

        # Eigener Stand, der aus Rekordbox uebernommen ist, heisst "tracktab_rb": er muss
        # beim Rekordbox-Update nicht zurueck (dj.js schreibt nur Bearbeitetes und "tracktab").
        def own_source(origin):
            return "tracktab_rb" if origin == gridstore.ORIGIN_REKORDBOX else "tracktab"

        beats, grid_source = [], None
        if rb_grid:
            beats = gridstore.beats_to_own(rb_grid, lead)
            grid_source = "rekordbox"
        elif stored and stored["beats"]:
            beats, grid_source = stored["beats"], own_source(gridstore.grid_origin(stored))
        phrases, phrase_source = [], None
        if rb_phrases:
            phrases = gridstore.phrases_to_own(rb_phrases, lead)
            phrase_source = "rekordbox"
        elif stored and stored["phrases"]:
            phrases, phrase_source = stored["phrases"], own_source(gridstore.phrases_origin(stored))
        cues = gridstore.cues_to_own(rb_cues, lead)

        grid_edit = edits["grid"]
        grid_beats = (grid_edit or {}).get("beats") or [] if grid_edit is not None else beats
        phrase_items = _phrases_from_first_beat(edits["phrases"] if edits["phrases"] is not None else phrases, grid_beats)
        hashes = _dj_rb_hashes(state)
        tag_diff = rekordbox_mod.tag_diff_map(_tag_rows(cfg, path)).get(path, []) if state["present"] else []
        return {
            "ok": True,
            "locks": locks,
            "grid": {"beats": grid_beats,
                     "segments": (grid_edit or {}).get("segments"),
                     "locked": bool((grid_edit or {}).get("locked")),
                     "edited": grid_edit is not None, "source": grid_source},
            "phrases": {"items": phrase_items,
                        "edited": edits["phrases"] is not None, "source": phrase_source,
                        "uncertain": bool(phrase_source == "tracktab" and stored and stored["uncertain"])},
            "cues": {"items": edits["cues"] if edits["cues"] is not None else cues,
                     "edited": edits["cues"] is not None, "source": "rekordbox" if cues else None},
            "rekordbox": {
                "available": state["available"], "present": state["present"], "analysed": state["analysed"],
                "changed": [f for f in db_mod.DJ_TRACK_FIELDS
                            if (edits[f] is not None and f in base["rb"] and base["rb"][f] != hashes[f])
                            or (edits[f] is None and use_rb and state["available"] and snap
                                and snap[f] is not None and snap["seen"].get(f) != hashes[f])],
                "pending": [f for f in db_mod.DJ_TRACK_FIELDS
                            if edits[f] is not None and base["pushed"].get(f) != _dj_hash(edits[f])]
                           + (["tags"] if state["present"] and _tag_update_needed(tag_diff) else []),
                "notes": ["bpm_half"] if state["present"] and "bpm_half" in tag_diff else [],
                "tag_fields": [f for f in tag_diff if f != "bpm_half"] if state["present"] else [],
            },
            "palettes": rekordbox_mod.cue_palettes(),
        }

    def _get_dj_track(self) -> None:
        """Beatgrid, Phrasen und Cues eines Tracks fuer den DJ-Editor. Kein
        self.lock: reine Lesezugriffe, die Wellenform rechnet ausserhalb."""
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        self._json(self._dj_track_payload(path, cfgmod.load()))

    def _get_dj_bpms(self) -> None:
        """Fuer die Spalte BPM der DJ-Liste: das Tempo bearbeiteter Beatgrids und die
        gemerkte Tempo-Spanne je Track, dazu fuer die Spalte Rekordbox die Tracks, deren
        Bearbeitung von Rekordbox abweicht. Unbearbeitete zeigen ohnehin Rekordbox' Daten.
        Der Report kennt nichts davon, deshalb zieht syncMarks() es nach."""
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            bpms, tempo = db_mod.dj_edited_bpms(conn), db_mod.grid_tempo_map(conn)
            tempo_seq = db_mod.grid_tempo_seq_map(conn)
            rb_changed = db_mod.rb_snapshot_changed_map(conn)
            edited = db_mod.dj_track_edits(conn)
        finally:
            conn.close()
        rb_diff = {**rb_changed, **{p: d for p, (e, b) in edited.items() if (d := _dj_rb_diff(p, e, b))}}
        # Metadaten (Titel, Interpret, ...): TrackTab gegen Rekordbox, ohne eigene Merkung.
        # bpm_half (halbes/doppeltes Tempo) ist nur ein Hinweis, nichts zu uebertragen.
        # rb_tag_fields: welche Metadaten im Einzelnen (Tooltip der Spalte Rekordbox).
        rb_tag_fields = {}
        for p, fields in rekordbox_mod.tag_diff_map(_tag_rows(cfg)).items():
            extra = (["tags"] if _tag_update_needed(fields) else []) + (["bpm_half"] if "bpm_half" in fields else [])
            rb_diff[p] = [*rb_diff.get(p, []), *extra]
            if _tag_update_needed(fields):
                rb_tag_fields[p] = [f for f in fields if f != "bpm_half"]
        self._json({"bpms": bpms, "tempo": tempo, "tempo_seq": tempo_seq,
                    "rb_diff": rb_diff, "rb_tag_fields": rb_tag_fields, "hot_cues": sorted(_hot_cue_paths(cfg)),
                    "rb_compare": str(cfg.get("beatgrid_source") or "fallback") != "tracktab"})

    def _get_dj_pcm(self) -> None:
        """Der ganze Track als 16-Bit-PCM (Stereo, 44,1 kHz) fuer die Wiedergabe im
        DJ-Editor. Nur so laeuft ein Loop lueckenlos (AudioBufferSourceNode, ein
        <audio> muss springen), und der Ton kommt aus derselben ffmpeg-Dekodierung
        wie Wellenform und Analyse: alles liegt auf einer Zeitachse."""
        path = self._query().get("path", "")
        if not path or not self._authorize_fast(path):
            self._reject_missing(path)
            return
        out = subprocess.run(
            [media.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
             "-af", "aformat=channel_layouts=stereo", "-ar", "44100", "-c:a", "pcm_s16le", "-f", "s16le", "-"],
            capture_output=True, timeout=300)
        if out.returncode != 0 and not out.stdout:
            self._fail(out.stderr.decode("utf-8", "replace").strip()[:200] or "Dekodieren fehlgeschlagen", 500)
            return
        self._send(200, out.stdout, "application/octet-stream", {"X-Pcm-Rate": "44100", "X-Pcm-Channels": "2"})

    def _post_dj_tempo(self) -> None:
        """Tempo-Spanne des Beatgrids merken, siehe Tabelle 'grid_tempo'. Nicht im
        Report, syncMarks() zieht sie ueber /api/dj-bpms nach, also kein _rebuild()."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            path = str(payload["path"])
            lo = float(payload["lo"]) if payload.get("lo") is not None else None
            hi = float(payload["hi"]) if payload.get("hi") is not None else None
            seq = [round(float(v), 2) for v in payload["seq"]][:64] if payload.get("seq") else None
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not self._authorize_fast(path):
            self._reject_missing(path)
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                db_mod.set_grid_tempo(conn, path, lo, hi, seq)
            finally:
                conn.close()
        self._json({"ok": True})

    def _post_dj_ramp_fit(self) -> None:
        """Schlaege einer Tempo-Rampe (DJ-Editor) an die Anschlaege im Audio legen
        (beatgrid.fit_ramp()), eigene Anker (`fixed`) bleiben fest. Schreibt nichts, der Client setzt daraus Anker. Zeiten auf
        TrackTabs Achse wie /api/dj-track. Dekodieren und Suchen ausserhalb von self.lock."""
        try:
            payload = json.loads(self._body(10_000).decode("utf-8"))
            path = str(payload["path"])
            t0, t1 = _dj_time(payload["t0"]), _dj_time(payload["t1"])
            bpm0, bpm1, beats = float(payload["bpm0"]), float(payload["bpm1"]), int(payload["beats"])
            if not (20 <= bpm0 <= 400 and 20 <= bpm1 <= 400 and 2 <= beats <= 4096 and t1 > t0):
                raise ValueError("Werte außerhalb des Bereichs")
            # Eigene Anker des Nutzers: bleiben fest, eingepasst wird nur dazwischen.
            fixed = {}
            for k, sec in payload.get("fixed") or []:
                k, sec = int(k), _dj_time(sec)
                if not (0 < k < beats and t0 < sec < t1):
                    raise ValueError("Ungültiger Anker")
                fixed[k] = sec
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        _row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        try:
            fitted = beatgrid_mod.fit_ramp(path, t0, t1, bpm0, bpm1, beats, fixed=fixed)
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Rampe einpassen fehlgeschlagen: {exc}", 500)
            return
        self._json({"ok": True, "beats": [list(b) for b in fitted]})

    def _post_dj_track(self) -> None:
        """Bearbeitungen aus der DJ-Ansicht speichern. Nur die mitgeschickten
        Felder aendern sich, null setzt eines auf die Quelle zurueck. Liegt nur in
        der Tabelle 'dj_track'; der Report enthaelt die Werte nicht, also kein
        _rebuild(). Beim ersten Bearbeiten eines Felds merkt sich die Zeile den
        Stand von Rekordbox: an ihm erkennt _dj_track_payload() spaetere Aenderungen
        dort. Rekordbox lesen laeuft ausserhalb von self.lock."""
        try:
            payload = json.loads(self._body(2_000_000).decode("utf-8"))
            path = str(payload["path"])
            fields = {}
            if "grid" in payload:
                fields["grid"] = _clean_dj_grid(payload["grid"])
            if "phrases" in payload:
                fields["phrases"] = _clean_dj_phrases(payload["phrases"])
            if "cues" in payload:
                fields["cues"] = _clean_dj_cues(payload["cues"])
            locks = payload.get("locks")
            if locks is not None and not isinstance(locks, dict):
                raise ValueError("locks")
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        _row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        conn = db_mod.connect(cfg)
        try:
            base = db_mod.get_dj_base(conn, path)
        finally:
            conn.close()
        missing = [f for f, v in fields.items() if v is not None and f not in base["rb"]]
        if missing:
            hashes = _dj_rb_hashes(rekordbox_mod.track_state(path))
            base["rb"].update({f: hashes[f] for f in missing})
        for f, v in fields.items():
            if v is None:
                base["rb"].pop(f, None)
                base["pushed"].pop(f, None)
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.save_dj_track(conn, path, fields, base)
                if locks is not None:
                    db_mod.set_dj_locks(conn, path, locks)
            finally:
                conn.close()
        self._json({"ok": True, "path": path})

    def _analysis_locks(self, path: str, cfg: dict) -> dict:
        """In der DJ-Ansicht bearbeitet oder von Hand gesperrt, je Beatgrid und Phrasen:
        die Analyse laesst das Gesperrte aus. Genre und Stimmung nicht, die beruehren
        nichts davon. Kein self.lock, reiner Lesezugriff."""
        conn = db_mod.connect(cfg)
        try:
            return db_mod.dj_locks(conn, path)
        finally:
            conn.close()

    def _post_dj_rekordbox(self) -> None:
        """Abgleich mit Rekordbox fuer EINEN Track, ausgeloest ueber die Knoepfe im
        DJ-Editor. action: "load" (Bearbeitungen verwerfen, es gilt wieder Rekordbox),
        "keep" (Abweichung gesehen, eigene Fassung behalten), "update" (eigene Fassung
        nach Rekordbox schreiben), "add" (Track in die Sammlung aufnehmen).
        Schreiben geht ueber _rekordbox_lock, nie unter self.lock."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path, action = str(payload["path"]), str(payload["action"])
            names = [f for f in (payload.get("fields") or db_mod.DJ_TRACK_FIELDS) if f in (*db_mod.DJ_TRACK_FIELDS, "tags")]
            session = str(payload.get("session") or "")
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        result: dict = {}
        if action == "add" and rekordbox_mod.track_state(path)["present"]:
            # Schon in der Sammlung (Sammelaktion mit gemischter Auswahl): nichts
            # schreiben, auch keine Sicherung der master.db.
            result = {"added": False, "written": [], "skipped": []}
        elif action in ("update", "add"):
            with rekordbox_mod.backup_session(session):
                done = self._dj_write_rekordbox(path, action, names, row, cfg)
            if done is None:
                return                    # Antwort ist schon raus
            result = done
        elif action not in ("load", "keep"):
            self._fail("Unbekannte Aktion")
            return
        self._dj_rb_after(path, action, names, result, cfg)
        if action == "add":
            self._rebuild(cfg)
        if action == "load":
            # Eine fruehere Uebernahme folgt dem neu geladenen Rekordbox-Stand.
            gridstore.seed_from_rekordbox(cfg, path, self.lock, refresh=True)
        audit_log.log(f"rekordbox-dj-{action}", path, ",".join(result.get("written", names)))
        self._json({"ok": True, **result, **self._dj_track_payload(path, cfg)})

    def _dj_rb_after(self, path: str, action: str, names: list, result: dict, cfg: dict) -> None:
        """Nacharbeit nach einem Abgleich (load/keep/update/add): Pruefsummen in rb_base,
        Analyse-Sperren, rb_snapshot, Praesenz-Cache. Gemeinsam fuer DJ-Knoepfe und
        Playlist-Weg. Liest Rekordbox ohne Lock, schreibt unter self.lock."""
        state = rekordbox_mod.track_state(path)
        hashes = _dj_rb_hashes(state)
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                base = db_mod.get_dj_base(conn, path)
                edits = db_mod.get_dj_track(conn, path)
                if action == "load":
                    for f in names:
                        if f == "tags":
                            continue
                        base["rb"].pop(f, None)
                        base["pushed"].pop(f, None)
                    db_mod.save_dj_track(conn, path, {f: None for f in names if f != "tags"}, base)
                elif action == "keep":
                    for f in names:
                        if f in hashes and edits[f] is not None:
                            base["rb"][f] = hashes[f]
                    db_mod.save_dj_track(conn, path, {}, base)
                else:
                    for f in result.get("written", []):
                        if f not in hashes:
                            continue
                        base["rb"][f] = hashes[f]
                        if edits[f] is not None:
                            base["pushed"][f] = _dj_hash(edits[f])
                    db_mod.save_dj_track(conn, path, {}, base)
                    # Was in Rekordbox steht, soll keine Analyse mehr ueberschreiben.
                    if [w for w in result.get("written", []) if w != "tags"] and state["analysed"]:
                        db_mod.set_dj_locks(conn, path, {"grid": True, "phrases": True})
                if state["available"] and state["present"]:
                    snap = db_mod.get_rb_snapshot(conn, path)
                    if snap or action == "load":
                        db_mod.save_rb_snapshot(conn, path, _rb_snapshot_sync(
                            snap, state,
                            take=names if action == "load" else result.get("written", []),
                            seen=names if action == "keep" else []))
                if action in ("update", "add") and state["present"]:
                    db_mod.mark_rekordbox_present(conn, [path])
            finally:
                conn.close()

    def _tag_row(self, path: str):
        """Metadaten-Zeile fuers Schreiben nach Rekordbox, None = nichts zu schreiben."""
        return _tag_rows(cfgmod.load(), path).get(path)

    def _dj_write_rekordbox(self, path: str, action: str, names: list, row, cfg: dict) -> dict | None:
        """Eigene Fassung auf Rekordbox' Zeitachse bringen und schreiben. None = die
        Fehlerantwort ist schon raus."""
        try:
            prep = self._dj_rb_prepare(path, action, names, row, cfg)
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Wellenform fehlgeschlagen: {exc}", 500)
            return None
        try:
            with self._rekordbox_lock:
                if action == "add":
                    return rekordbox_mod.add_track(path, row, prep["cues"], beats=prep["beats"],
                                                   phrases=prep["phrases"], lead=prep["lead"],
                                                   audio_start_ms=prep["audio_start_ms"])
                tag_row = self._tag_row(path) if "tags" in names else None
                return rekordbox_mod.update_track(path, cues=prep["cues"], beats=prep["beats"],
                                                  phrases=prep["phrases"], tags=tag_row,
                                                  audio_start_ms=prep["audio_start_ms"])
        except rekordbox_mod.RekordboxRunning as exc:
            self._json({"ok": False, "error": str(exc), "sticky": True}, 409)
        except Exception as exc:                       # noqa: BLE001
            self._fail(str(exc), 500)
        return None

    def _rb_lead(self, path: str, cfg: dict) -> float:
        """Versatz zu Rekordbox fuers Schreiben. Ein falscher Wert fuehrt hier zu
        falschen Daten in Rekordbox. Frueher wurde deshalb vor jedem Schreiben neu
        gemessen (ffmpeg, rund 0,4 s je Track); jetzt haengt der gemerkte Wert an
        Rekordbox' Wellenform (rekordbox.lead_stamp() in _wave_lead()): hat Rekordbox
        inzwischen analysiert, passt er nicht mehr und es wird neu gemessen. Derselbe
        Zwischenspeicher wie beim Oeffnen im DJ-Editor."""
        return self._wave_lead(path, cfg)

    def _dj_update_fields(self, path: str, names: list, state: dict, cfg: dict) -> list:
        """Welche Felder "Rekordbox Update" fuer einen Track schreibt: bearbeitete und
        TrackTabs eigene Analyse, die angezeigt wird (Quelle "tracktab"). Dieselbe Regel
        wie djRekordboxRun() mit /api/dj-track, nur ohne Wellenform/Versatz."""
        source = str(cfg.get("beatgrid_source") or "fallback")
        use_rb = source != "tracktab"
        conn = db_mod.connect(cfg)
        try:
            edits = db_mod.get_dj_track(conn, path)
            stored = db_mod.get_phrases(conn, path) if source != "rekordbox" else None
            snap = db_mod.get_rb_snapshot(conn, path)
        finally:
            conn.close()

        def rb_value(field):
            if not use_rb:
                return []
            return snap[field] if snap and snap[field] is not None else state.get(field) or []

        own = {
            "grid": (not rb_value("grid") and bool(stored and stored["beats"])
                     and gridstore.grid_origin(stored) != gridstore.ORIGIN_REKORDBOX),
            "phrases": (not rb_value("phrases") and bool(stored and stored["phrases"])
                        and gridstore.phrases_origin(stored) != gridstore.ORIGIN_REKORDBOX),
            "cues": False,
        }
        out = [f for f in names if edits[f] is not None or own.get(f)]
        if _tag_update_needed(rekordbox_mod.tag_diff_map(_tag_rows(cfg, path)).get(path, [])):
            out.append("tags")
        return out

    def _post_dj_rekordbox_batch(self) -> None:
        """"Rekordbox Update" fuer viele Tracks in einem Aufruf: einmal oeffnen, einmal
        sichern (backup_session), je Track eine Transaktion (rekordbox.update_tracks()).
        Der Client schickt Abschnitte von einigen Tracks und dasselbe "session"-Token
        fuer die ganze Aktion. Messen ohne Lock, Schreiben unter _rekordbox_lock."""
        payload = self._json_payload()
        if payload is None:
            return
        paths = [str(p) for p in (payload.get("paths") or []) if p]
        session = str(payload.get("session") or "")
        if not paths or len(paths) > 200:
            self._fail("Keine oder zu viele Tracks angegeben")
            return
        cfg = cfgmod.load()
        names = list(db_mod.DJ_TRACK_FIELDS)
        items, skipped, errors = [], [], {}
        for p in paths:
            row = self._known_file(p)
            if row is None:
                errors[p] = "Datei unbekannt oder nicht mehr vorhanden"
                continue
            state = rekordbox_mod.track_state(p)
            fields = self._dj_update_fields(p, names, state, cfg) if state.get("present") else []
            if not fields:
                skipped.append(p)
                continue
            try:
                prep = self._dj_rb_prepare(p, "update", fields, row, cfg)
            except Exception as exc:                 # noqa: BLE001
                errors[p] = f"Wellenform fehlgeschlagen: {exc}"[:300]
                continue
            items.append({"path": p, **prep, "fields": fields,
                          "tags": self._tag_row(p) if "tags" in fields else None})
        result = {"results": {}, "errors": {}, "stopped": ""}
        if items:
            try:
                with rekordbox_mod.backup_session(session), self._rekordbox_lock:
                    result = rekordbox_mod.update_tracks(items)
            except rekordbox_mod.RekordboxRunning as exc:
                self._json({"ok": False, "error": str(exc), "sticky": True}, 409)
                return
            except Exception as exc:                 # noqa: BLE001
                self._fail(str(exc), 500)
                return
        fields_of = {it["path"]: it["fields"] for it in items}
        for p, res in result["results"].items():
            self._dj_rb_after(p, "update", fields_of.get(p, names), res, cfg)
            audit_log.log("rekordbox-dj-update", p, ",".join(res.get("written", [])))
        done = [p for p, res in result["results"].items() if res.get("written")]
        skipped += [p for p, res in result["results"].items() if not res.get("written")]
        self._json({"ok": True, "done": done, "skipped": skipped,
                    "errors": {**errors, **result["errors"]}, "stopped": result["stopped"]})

    def _dj_rb_prepare(self, path: str, action: str, names: list, row, cfg: dict) -> dict:
        """TrackTabs Fassung (Bearbeitung, sonst eigene Analyse) auf Rekordbox' Zeitachse:
        {cues, beats, phrases, lead (Samples, nur bei "add"), audio_start_ms}. Ohne Lock,
        misst den Versatz (ffmpeg). Gemeinsam fuer DJ-Knoepfe und Playlist-Weg
        (_post_rekordbox_add_playlist()). Wirft bei einem Messfehler."""
        names = [n for n in names if n != "tags"]          # Metadaten brauchen weder Grid noch Versatz
        if action == "update" and not names:
            return {"cues": None, "beats": None, "phrases": None, "lead": None, "audio_start_ms": 0}
        conn = db_mod.connect(cfg)
        try:
            edits = db_mod.get_dj_track(conn, path)
            stored = db_mod.get_phrases(conn, path)
        finally:
            conn.close()
        # Beim Neuanlegen gibt es nichts zu messen, dort gilt die Regel aus dem Dateikopf.
        lead_samples = None
        if action == "add":
            info = row if row is not None else vars(probe_mod.probe(path))
            lead_samples = anlzwrite_mod.lead_samples(path, info["codec_family"] or "")
        if lead_samples is not None:
            lead = lead_samples / (info["sample_rate"] or 44100)
        else:
            lead = self._rb_lead(path, cfg)

        def rb_time(t):
            return round(float(t) + lead, 4)

        # Ohne Bearbeitung zaehlt die eigene Analyse. Was aus Rekordbox stammt und
        # unveraendert ist, muss nicht zurueck.
        grid = edits["grid"]
        phrases = edits["phrases"] if edits["phrases"] is not None else (stored or {}).get("phrases") or None
        beats = None
        if "grid" in names:
            if grid is not None:
                beats = _dj_beats_with_tempo(grid.get("beats") or [], grid.get("segments") or [], lead,
                                             float((stored or {}).get("bpm") or 0))
            elif stored and stored["beats"]:
                beats = _dj_beats_with_tempo(stored["beats"], [], lead, float(stored.get("bpm") or 0))
            beats = beats or None
        phrase_items = None
        if "phrases" in names and phrases:
            own_beats = (grid or {}).get("beats") if grid is not None else (stored or {}).get("beats")
            phrases = _phrases_from_first_beat(phrases, own_beats or [])
            phrase_items = [{**p, "start": rb_time(p["start"]), "end": rb_time(p["end"])} for p in phrases]
        cue_items = None
        if "cues" in names and edits["cues"] is not None:
            bpm_at = _dj_tempo_lookup(grid, stored)
            # Rekordbox kennt keine Cues vor dem Ton (68.566 eigene Cues gelesen, keiner
            # negativ): sie landen dort, wo _grid_from_start() den ersten Schlag hinlegt.
            audio_start = round(lead, 4)
            duration = (row["duration_s"] if row is not None else 0) or 0

            def cue_time(t):
                moved = max(audio_start, rb_time(t))
                return min(moved, round(duration + lead, 4)) if duration else moved

            cue_items = [{**c, "position_s": cue_time(c["position_s"]),
                          "loop_s": rb_time(c["loop_s"]) if c.get("loop_s") is not None else None,
                          "bpm": bpm_at(c["position_s"])} for c in edits["cues"]]
        return {"cues": cue_items, "beats": beats, "phrases": phrase_items,
                "lead": lead_samples, "audio_start_ms": int(round(lead * 1000))}

    def _post_phrases(self) -> None:
        """Beatgrid und Phrasen messen (phrases.py) -- fuer EINEN Pfad, je Datei
        ein Aufruf wie /api/genremood. Geht nur in die Tabelle 'phrases', nie in
        die Datei; der Report enthaelt die Werte nicht, also kein _rebuild().

        Messen (ffmpeg + onnxruntime, 2-4 s) laeuft AUSSERHALB von self.lock."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not phrases_mod.available():
            self._json({"ok": True, "status": "unavailable"})
            return
        _row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        try:
            result = self._measure_phrases(path, cfgmod.load(), str(payload.get("what") or "both"),
                                           bool(payload.get("apply")), payload.get("mode"))
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Analyse fehlgeschlagen: {exc}", 500)
            return
        self._json(result)

    def _measure_phrases(self, path: str, cfg: dict, what: str, apply: bool, mode=None) -> dict:
        """Kern von /api/phrases, auch fuer /api/autocue (Track ohne jedes Grid).
        Antwort wie der Endpunkt; Messfehler gehen als Ausnahme an den Aufrufer."""
        # what: was gemessen werden soll; Gesperrtes faellt heraus.
        locks = self._analysis_locks(path, cfg)
        wanted = {"grid", "phrases"} if what == "both" else {what} & {"grid", "phrases"}
        wanted = {f for f in wanted if not locks[f]}
        if not wanted:
            return {"ok": True, "status": "locked", "path": path}
        minutes = int(cfg.get("bpmkey_max_minutes") or 10)
        mode = str(mode or cfg.get("phrase_grid_mode") or "auto")
        current = None
        # Nur Phrasen gewuenscht und ein Grid vorhanden: auf genau diesem messen, statt ein
        # eigenes zu suchen und die Phrasen danach einrasten zu lassen. Findet
        # beatgrid.analyse() kein Tempo (Uebergaenge, Tempowechsel), gibt es trotzdem
        # Phrasen, sobald das Grid von Hand gesetzt ist. Ebenso, wenn Grid UND Phrasen
        # gewuenscht waren, aber kein Tempo zu finden ist ("no_tempo" in der Antwort).
        no_tempo = False

        def on_grid(grid):
            tempo = gridstore.beats_with_tempo(grid["beats"], grid.get("segments") or [], 0.0)
            return phrases_mod.analyse_on_grid(path, tempo, minutes * 60)

        try:
            result = None
            if "grid" not in wanted:
                current = self._dj_track_payload(path, cfg)
                if current["grid"]["beats"]:
                    result = on_grid(current["grid"])
            if result is None:
                result = phrases_mod.analyse(path, mode, minutes * 60)
            if result is None and "phrases" in wanted:
                current = current or self._dj_track_payload(path, cfg)
                if current["grid"]["beats"]:
                    result = on_grid(current["grid"])
                    wanted, no_tempo = {"phrases"}, result is not None
        except phrases_mod.TooLongError:
            return {"ok": True, "status": "skipped", "reason": "too_long", "minutes": minutes}
        if not result or ("phrases" in wanted and not result["phrases"]):
            return {"ok": True, "status": "none", "path": path}
        # Grid und Phrasen gehoeren zusammen: neue Phrasen rasten auf dem bestehenden Grid
        # ein, ein neues Grid nimmt bestehende Phrasen mit, auch gesperrte. apply (DJ-Ansicht):
        # das Ergebnis gilt sofort als Stand des Tracks, ohne zu sperren.
        fields: dict = {}
        if apply or wanted != {"grid", "phrases"}:
            current = current or self._dj_track_payload(path, cfg)
            if "grid" not in wanted and current["grid"]["beats"]:
                result = {**result, "phrases": _snap_phrases(result["phrases"], current["grid"]["beats"], True)}
            if apply:
                if "grid" in wanted:
                    fields["grid"] = {"segments": None, "locked": False, "beats": result["beats"]}
                if "phrases" in wanted:
                    fields["phrases"] = result["phrases"]
                elif current["phrases"]["items"]:
                    fields["phrases"] = _snap_phrases(current["phrases"]["items"], result["beats"], False)
        hashes = _dj_rb_hashes(rekordbox_mod.track_state(path)) if fields else {}
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                db_mod.save_phrases(conn, path, _merge_phrase_result(db_mod.get_phrases(conn, path), result, wanted))
                if fields:
                    base = db_mod.get_dj_base(conn, path)
                    base["rb"].update({f: hashes[f] for f in fields if f not in base["rb"]})
                    db_mod.save_dj_track(conn, path, fields, base, auto_lock=False)
            finally:
                conn.close()
        return {"ok": True, "status": "done", "path": path, "uncertain": result["uncertain"],
                "no_tempo": no_tempo}

    def _autocue_inputs(self, path: str, cfg: dict) -> dict:
        """Grid und Phrasen fuer Auto Cue: TrackTabs eigener Stand (Bearbeitung, dann
        Tabelle 'phrases'), unabhaengig von beatgrid_source. Fehlt er, wird Rekordbox'
        Stand vorher als eigener uebernommen (gridstore) -- dasselbe sieht das Training.
        Rueckgabe: beats, segments, phrases, bpm, loaded (aus Rekordbox uebernommene Felder)."""
        loaded = gridstore.seed_from_rekordbox(cfg, path, self.lock)
        conn = db_mod.connect(cfg)
        try:
            own = gridstore.own_inputs(conn, path)
        finally:
            conn.close()
        return {"beats": own["beats"], "segments": own["segments"], "phrases": own["phrases"],
                "phrases_edited": own["phrases_origin"] == "edited", "bpm": own["bpm"], "loaded": loaded}

    def _post_autocue(self) -> None:
        """Auto Cue fuer EINEN Pfad (app/autocue/). apply: Ergebnis gleich als Cues des
        Tracks speichern (Sammelaktion); ohne apply nur zurueckgeben, der DJ-Editor
        uebernimmt es mit Rueckgaengig. Ersetzt die Cue-Liste komplett -- aber nur,
        wenn Cues entstanden sind: eine leere Liste loeschte beim naechsten
        Rekordbox-Update dort alle Cues. Fehlt jedes Grid, misst es vorher
        (wie /api/phrases). dj_track steckt nicht im Report, also kein _rebuild().
        Messen und Analyse laufen AUSSERHALB von self.lock."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
            apply = bool(payload.get("apply"))
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not autocue_mod.available():
            self._json({"ok": True, "status": "unavailable"})
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return
        cfg = cfgmod.load()
        minutes = int(cfg.get("bpmkey_max_minutes") or 10)
        measured = False
        try:
            inputs = self._autocue_inputs(path, cfg)
            if not inputs["beats"] and phrases_mod.available():
                done = self._measure_phrases(path, cfg, "both", True)
                if done["status"] == "skipped":
                    self._json({**done, "path": path})
                    return
                measured = done["status"] == "done"
                inputs = self._autocue_inputs(path, cfg)
            if not inputs["beats"]:
                self._json({"ok": True, "status": "nogrid", "path": path, "measured": measured,
                            "loaded": inputs["loaded"]})
                return
            beats = _dj_beats_with_tempo(inputs["beats"], inputs["segments"] or [], 0.0, inputs["bpm"])
            duration = float((row["duration_s"] if row is not None else 0) or 0)
            result = autocue_mod.analyse(path, beats, inputs["phrases"], duration, cfg, minutes * 60,
                                         phrases_edited=inputs["phrases_edited"])
        except autocue_sources.TooLongError:
            self._json({"ok": True, "status": "skipped", "reason": "too_long", "minutes": minutes, "path": path})
            return
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Auto Cue fehlgeschlagen: {exc}", 500)
            return
        out = {"ok": True, "path": path, "measured": measured, "loaded": inputs["loaded"], **result}
        if result["status"] == "done":
            # Herkunftsnachweis fuers Training, auch ohne apply (der Editor uebernimmt es meist).
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    db_mod.save_autocue_log(conn, path, result["cues"])
                finally:
                    conn.close()
        if apply and result["status"] == "done":
            hashes = _dj_rb_hashes(rekordbox_mod.track_state(path))
            with self.lock:
                conn = db_mod.connect(cfg)
                try:
                    base = db_mod.get_dj_base(conn, path)
                    base["rb"].setdefault("cues", hashes["cues"])
                    db_mod.save_dj_track(conn, path, {"cues": _clean_dj_cues(result["cues"])}, base,
                                         auto_lock=False)
                finally:
                    conn.close()
            out["saved"] = True
        self._json(out)

    def _post_genremood(self) -> None:
        """Genre-Vorschlaege, Stimmung und Energie messen (genremood.py) --
        fuer EINEN Pfad (der Client ruft je Datei einmal auf). Ergebnis geht
        immer in die Tabelle 'genremood' (sofern es eine DB-Zeile gibt), in
        die Datei nur nach den Einstellungen genremood_write_*. Einzel-
        pruefungen ohne DB-Zeile bekommen row=None und bauen ihre Zeile aus
        'result'/'fields'.

        Messen (ffmpeg + onnxruntime, mehrere Sekunden) laeuft AUSSERHALB von
        self.lock, wie _post_bpmkey()."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            path = str(payload["path"])
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not genremood_mod.available():
            self._json({"ok": True, "status": "unavailable", "row": None})
            return
        row, ok, path = self._authorize_or_relocate(path)
        if not ok:
            self._reject_missing(path)
            return

        cfg = cfgmod.load()
        # Gemeinsames Laengenlimit beider Analysen.
        minutes = int(cfg.get("bpmkey_max_minutes") or 10)
        try:
            result = genremood_mod.restrict(
                genremood_mod.analyse(path, minutes * 60), cfg)
        except genremood_mod.TooLongError:
            self._json({"ok": True, "status": "skipped", "reason": "too_long",
                        "minutes": minutes, "row": None})
            return
        except Exception as exc:                       # noqa: BLE001
            self._fail(f"Analyse fehlgeschlagen: {exc}", 500)
            return

        fields = genremood_mod.plan_fields(cfg, result, tags_mod.read_extra(path),
                                           tags_mod.read_mood(path))
        new_issues = None
        if fields:
            try:
                tags_mod.write_tags(path, fields)
            except tags_mod.TagError as exc:
                self._fail(str(exc), 500)
                return
            audit_log.log("genremood", path, ", ".join(sorted(fields)))
            new_issues = taganomaly_mod.detect(
                path, (row["codec_family"] if row else "") or "")
        base = {"ok": True, "status": "written" if fields else "unchanged",
                "result": result, "fields": fields}

        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Auch ohne 'files'-Zeile (Einzelpruefung) ablegen: wird die
                # Datei spaeter in die Bibliothek uebernommen, ist das
                # Ergebnis dann schon da, ohne erneut zu messen.
                db_mod.save_genremood(conn, path, result["genres"], result["moods"],
                                      result["energy"], result["model"])
                compact = None
                if row is not None:
                    if fields:
                        # 'mood' ist keine Spalte in 'files' -- update_tags()
                        # nimmt ohnehin nur bekannte Tag-Spalten.
                        db_mod.update_tags(conn, path, fields)
                        db_mod.update_tag_issues(conn, path, new_issues)
                        db_mod.refresh_stat(conn, path)
                    compact = self._compact_rows(conn, cfg, [path])[0]
            finally:
                conn.close()
            if row is not None:
                self._rebuild(cfg)
        if row is None:
            self._json({**base, "row": None,
                        **({"issues": new_issues} if new_issues is not None else {})})
            return
        self._json({**base, "row": compact})

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
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
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
                    [(p, titles.get(p, "")) for p in to_sync_music], new,
                    old_label)
            except Exception as exc:                       # noqa: BLE001
                music_result["error"] = str(exc)

        # Rekordbox: nur vormerken. Der Schreibzugriff (master.db oeffnen +
        # Backup) laeuft gebuendelt ueber "Rekordbox abgleichen", nicht je
        # Zusammenfuehrung.
        to_sync_rb = [p for p in updated if p in rb_paths]
        rekordbox_result = {"pending": 0, "queued": len(to_sync_rb)}
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                if to_sync_rb:
                    db_mod.rekordbox_pending_add(conn, to_sync_rb)
                rekordbox_result["pending"] = db_mod.rekordbox_pending_count(conn)
            finally:
                conn.close()

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
        path = self._query().get("path", "")
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

        self._apply_cover(path, row, data, mime)

    def _post_cover_copy(self) -> None:
        """Uebertraegt das eingebettete Cover einer Datei auf andere ({from, to: [..]}) --
        Quick Fix der Auffaelligkeit album_cover_mismatch. Je Ziel dieselbe Nachbehandlung
        wie /api/cover (Music.app, DB, Hash). Antwort: {ok, results: {pfad: Antwort}}."""
        try:
            payload = json.loads(self._body(1_000_000).decode("utf-8"))
            src = str(payload["from"])
            targets = [str(x) for x in payload["to"]]
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        _, ok, src = self._authorize_or_relocate(src)
        if not ok:
            self._reject_missing(src)
            return
        cover = tags_mod.read_cover(src)
        mime = _sniff_image_mime(cover[0]) if cover else ""
        if not cover or not mime:
            self._fail("Die Quelldatei hat kein lesbares Cover.")
            return
        results: dict = {}
        for path in targets:
            row, ok, path2 = self._authorize_or_relocate(path)
            if not ok:
                results[path] = {"ok": False, "error": "Datei nicht gefunden"}
                continue
            results[path] = self._apply_cover(path2, row, cover[0], mime, respond=False)
        self._json({"ok": True, "results": results})

    def _apply_cover(self, path: str, row, data: bytes, mime: str, respond: bool = True):
        """Schreibt ein Cover in die Datei und zieht DB, Music.app und Report nach.
        respond=False liefert die Antwort als dict zurueck statt sie zu senden."""
        out = self._apply_cover_inner(path, row, data, mime)
        if respond:
            if out.get("ok"):
                self._json(out)
            else:
                self._fail(out.get("error", "Fehler"), 500)
        return out

    def _apply_cover_inner(self, path: str, row, data: bytes, mime: str) -> dict:
        try:
            tags_mod.write_cover(path, data, mime)
        except tags_mod.TagError as exc:
            return {"ok": False, "error": str(exc)}
        audit_log.log("cover", path)

        if row is None:
            return {"ok": True, "row": None}

        cfg = cfgmod.load()
        with self.lock:
            conn = db_mod.connect(cfg)
            try:
                # Ohne ausgewaehlte Music App bleibt der Abgleich unten aus,
                # auch fuer einen Pfad, der noch aus aelterer Zeit in
                # 'music_added' steht (siehe _require_music()).
                in_library = bool(cfg.get("external_music")) and \
                    db_mod.is_music_added(conn, path)
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
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
        return {"ok": True, "row": compact,
                "music_cover_synced": music_cover_synced,
                "music_cover_error": music_cover_error}

    def _post_cover_delete(self) -> None:
        """Entfernt das eingebettete Cover. Pfad im Query-String (wie /api/cover)."""
        path = self._query().get("path", "")
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
                    db_mod.is_music_added(conn, path)
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
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
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
                    db_mod.is_music_added(conn, path)
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
                genremood = db_mod.genremood_map(conn)
            finally:
                conn.close()
            self._rebuild(cfg)

        compact = report_mod.rows_to_payload(
            [row], cfg, ignored, corrected, rekordbox, music_added,
            dup_dismissed, genremood)[0]
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
            # Ohne das liefert /tracktab.html weiter den alten Stand: die gerade
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
        darauf verlassen, dass data/tracktab.html nach dieser Zeile bereits
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

    def _post_autocue_slots(self) -> None:
        """Slot-Belegung von Auto Cue speichern (Einstellungsseite, eigener Knopf)."""
        try:
            payload = json.loads(self._body(100_000).decode("utf-8"))
            slots = settings_mod.save_autocue_slots(payload["slots"])
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, "slots": slots})

    def _autocue_models_reply(self, **extra) -> None:
        self._json({"ok": True, "models": settings_mod.autocue_models_info(), **extra})

    def _post_autocue_model_import(self) -> None:
        """Auto-Cue-Modell als ZIP in die Modellbibliothek laden (autocue/package.py).
        Koerper = die ZIP-Datei, Name optional per ?name=. Gilt erst nach Auswahl
        (ausser es ist das erste Modell)."""
        name = self._query().get("name", "")
        try:
            data = self._body(160 * 1024 * 1024)
            model_id, manifest = autocue_package.install(data, name)
        except ValueError as exc:                      # auch autocue_package.PackageError
            self._fail(str(exc))
            return
        except OSError as exc:
            self._fail(f"Modell konnte nicht gespeichert werden: {exc}", 500)
            return
        audit_log.log("autocue-model-import", model_id, str(manifest.get("trained_at")))
        self._autocue_models_reply(id=model_id)

    def _autocue_model_payload(self) -> dict:
        payload = json.loads(self._body(10_000).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Ungültige Anfrage")
        return payload

    def _post_autocue_model_select(self) -> None:
        """Geltendes Modell waehlen (id der Bibliothek)."""
        try:
            model_id = settings_mod.save_autocue_model(self._autocue_model_payload().get("id"))
        except ValueError as exc:
            self._fail(str(exc))
            return
        self._autocue_models_reply(active=model_id)

    def _post_autocue_model_rename(self) -> None:
        from .autocue import library as autocue_library
        try:
            payload = self._autocue_model_payload()
            autocue_library.rename(str(payload.get("id") or ""), payload.get("name"))
        except ValueError as exc:
            self._fail(str(exc))
            return
        except OSError as exc:
            self._fail(f"Modell konnte nicht umbenannt werden: {exc}", 500)
            return
        self._autocue_models_reply()

    def _post_autocue_model_delete(self) -> None:
        """Eigenes Modell in die Sicherung verschieben; galt es, gilt danach der juengste verbliebene."""
        from .autocue import library as autocue_library
        try:
            model_id = str(self._autocue_model_payload().get("id") or "")
            autocue_library.delete(model_id, str(cfgmod.resolve("backup") / "autocue-models" / "deleted"))
        except ValueError as exc:
            self._fail(str(exc))
            return
        except OSError as exc:
            self._fail(f"Modell konnte nicht gelöscht werden: {exc}", 500)
            return
        audit_log.log("autocue-model-delete", model_id, "")
        self._autocue_models_reply()

    def _post_autocue_train(self, check_only: bool = False) -> None:
        """Training in der App (autocue/training/job.py). check_only: nur zaehlen, wie
        viele Tracks taugen. Koerper {name, training, check (Anzeigenamen),
        training_paths, check_paths}: die Pfade schickt der Client, weil nur er den
        Inhalt smarter Playlisten kennt; genutzt wird davon nur, was als Track mit
        Hand-Cues in der DB steht (dataset.candidates()), fremde Pfade sind wirkungslos.
        Was woraus gelernt wird, kommt aus der gespeicherten Slot-Belegung. Laeuft im
        Hintergrund, ohne self.lock."""
        from .autocue.training import job as autocue_job

        def path_list(raw):
            if raw is None:
                return None
            if not isinstance(raw, list):
                raise ValueError("Ungültige Trackliste")
            return [str(p) for p in raw if isinstance(p, str) and p][:200_000]

        try:
            payload = json.loads(self._body(64 * 1024 * 1024).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Ungültige Anfrage")
            params = {"training": str(payload.get("training") or "")[:300],
                      "check": str(payload.get("check") or "")[:300],
                      "training_paths": path_list(payload.get("training_paths")),
                      "check_paths": path_list(payload.get("check_paths")),
                      "name": str(payload.get("name") or "")}
        except ValueError as exc:
            self._fail(str(exc))
            return
        cfg = cfgmod.load()
        if check_only:
            self._json(autocue_job.TrainJob.check(cfg, params))
            return
        try:
            state = autocue_job.JOB.start(cfg, params)
        except (autocue_job.JobError, ValueError) as exc:
            self._fail(str(exc))
            return
        audit_log.log("autocue-train", params["training"], params["name"])
        self._json({"ok": True, **state})

    def _get_autocue_model_export(self) -> None:
        """Ein Modell als ZIP herunterladen (zum Weitergeben, Import auf einem anderen Rechner)."""
        from .autocue import library as autocue_library
        model_id = self._query().get("id", "")
        entry = next((e for e in autocue_library.entries() if e["id"] == model_id), None)
        if entry is None:
            self._send(404, "Modell nicht gefunden".encode("utf-8"), "text/plain; charset=utf-8")
            return
        label = entry["name"] or "TrackTab"
        stamp = (entry["trained_at"] or "")[:10]
        safe = "".join(c for c in f"Auto-Cue-Modell {label} {stamp}".strip()
                       if c not in ('\r', '\n', '"', "/", "\\", ":"))[:120] + ".zip"
        ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
        fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            autocue_package.export(model_id, tmp_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(os.path.getsize(tmp_path)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{ascii_name}"; '
                             f"filename*=UTF-8''{quote(safe, safe='')}")
            self._security_headers()
            self.end_headers()
            with open(tmp_path, "rb") as fh:
                try:
                    shutil.copyfileobj(fh, self.wfile, _CHUNK)
                except (BrokenPipeError, ConnectionResetError):
                    return
        except (ValueError, OSError) as exc:
            self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

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
        if op not in ("create", "update", "move", "delete", "reorder", "lock"):
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
    def _clean_generator_config(raw):
        """Einstellungen eines Playlist-Generators pruefen (Kurve, Slots,
        Filter, Gates, Regler). Wie bei Smart Playlists wertet ausschliesslich
        der Client aus (app/webui/generator.js) -- hier zaehlt nur die Form.
        Pfade stehen bewusst NICHT darin, die Belegung liegt in
        'playlist_items' (siehe db.PLAYLIST_KINDS)."""
        if raw in (None, ""):
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"Generator-Einstellungen nicht lesbar: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Generator-Einstellungen müssen ein Objekt sein.")
        slots = raw.get("slots")
        if not isinstance(slots, list) or len(slots) > 200:
            raise ValueError("Ein Generator hat bis zu 200 Slots.")
        if not all(isinstance(s, dict) for s in slots):
            raise ValueError("Slots müssen Objekte sein.")
        text = json.dumps(raw, ensure_ascii=False)
        if len(text) > 50_000:
            raise ValueError("Generator-Einstellungen sind zu gross.")
        return text

    @classmethod
    def _clean_rules_for(cls, kind: str, raw):
        """'rules' je Listenart: Generator-Einstellungen oder Regelwerk."""
        if kind == "generator":
            return cls._clean_generator_config(raw)
        return cls._clean_playlist_rules(raw)

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
            # Palette oder eigene Farbe aus dem Farbwaehler.
            if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
                return text.lower()
            return text if text in _Handler._PLAYLIST_COLORS else None
        # "sym:<lucide-name>" (laengster bekannter Name ca. 30 Zeichen) oder
        # "emoji:<zeichen>" -- 4 Zeichen (Stand der fruehren Einzelzeichen-
        # Icons) haette hier "sym:bot" zu "sym:" verstuemmelt und jedes
        # Lucide-Icon unbemerkt verworfen.
        return text[:48]

    @staticmethod
    def _check_parent_unlocked(conn, parent_id) -> None:
        """Ein gesperrter Ordner nimmt nichts auf und gibt nichts her."""
        if not parent_id:
            return
        parent = db_mod.playlist_by_id(conn, parent_id)
        if parent and parent.get("locked"):
            raise ValueError(f"Der Ordner „{parent['name']}“ ist gesperrt.")

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
            self._check_parent_unlocked(conn, parent)
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
                "rules": self._clean_rules_for(kind, payload.get("rules")),
                "seq": int(payload.get("seq") or 0),
                "fav_slot": fav_slot,
            })
            return {"node": node}

        if op == "reorder":
            # Geschwister einer Ebene komplett neu durchnummerieren. Einzelne
            # seq-Werte zu setzen waere fehleranfaellig (Luecken, Dubletten);
            # der Client kennt die gewuenschte Reihenfolge ohnehin vollstaendig.
            parent = payload.get("parent_id") or None
            self._check_parent_unlocked(conn, parent)
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
        # Feste Listen: Symbol, Regelwerk, Name und Farbe der Status-Listen
        # gehoeren uns (bei jedem Start aus db._SYSTEM_PLAYLISTS neu gesetzt);
        # geloescht werden koennen sie nicht. Dem Nutzer gehoeren Position
        # ('move'/'reorder'), Sperre und beim Ordner Name und Farbe.
        if current.get("system"):
            if op == "delete":
                raise ValueError("Feste Listen lassen sich nicht löschen.")
            if op == "update":
                erlaubt = {"color", "name"} if current["kind"] == "folder" else set()
                gesperrt = [k for k in ("name", "icon", "color", "rules", "merkliste")
                            if k in payload and k not in erlaubt]
                if gesperrt:
                    raise ValueError("Symbol, Name und Farbe fester Listen sind gesperrt.")

        if op == "lock":
            current["locked"] = 1 if payload.get("locked") else 0
            return {"node": db_mod.save_playlist(conn, current)}

        # Gesperrt heisst: weder umbenennen noch verschieben noch loeschen
        # (und bei Ordnern: nichts hinein, nichts heraus). Aufheben geht nur
        # ueber op 'lock'.
        if current.get("locked"):
            raise ValueError("Diese Liste ist gesperrt. Zum Ändern zuerst entsperren.")
        self._check_parent_unlocked(conn, current.get("parent_id"))

        if op == "delete":
            for nid in db_mod.playlist_descendants(conn, node_id):
                sub = db_mod.playlist_by_id(conn, nid)
                if sub and sub.get("locked"):
                    raise ValueError(f"„{sub['name']}“ ist gesperrt und liegt in dieser Auswahl.")
            return {"deleted": db_mod.delete_playlist(conn, node_id)}

        if op == "move":
            parent = payload.get("parent_id") or None
            self._check_parent_unlocked(conn, parent)
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
                current[key] = self._clean_rules_for(current["kind"], payload[key])
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

    def _post_playlist_import(self) -> None:
        """M3U/M3U8-Pfade als neue Playlist anlegen. Der Client liest und
        zerlegt die Datei, hier werden die Pfade nur der Datenbank zugeordnet:
        erst exakt, sonst ueber einen EINDEUTIGEN Dateinamen (M3U-Dateien von
        anderen Rechnern haben andere Ordner). Nicht Gefundenes wird gemeldet."""
        try:
            payload = json.loads(self._body(5_000_000).decode("utf-8"))
            name = str(payload.get("name") or "").strip()[:80]
            paths = [str(x) for x in (payload.get("paths") or [])][:50_000]
            parent = payload.get("parent_id") or None
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        if not name:
            self._fail("Die Liste braucht einen Namen.")
            return
        with self.lock:
            conn = db_mod.connect(cfgmod.load())
            try:
                if parent and db_mod.playlist_by_id(conn, parent) is None:
                    self._fail("Der Zielordner existiert nicht mehr.")
                    return
                try:
                    self._check_parent_unlocked(conn, parent)
                except ValueError as exc:
                    self._fail(str(exc))
                    return
                by_name: dict[str, list[str]] = {}
                for r in conn.execute("SELECT path FROM files"):
                    by_name.setdefault(os.path.basename(r["path"]).lower(), []).append(r["path"])
                found: list[str] = []
                missing = 0
                for p in paths:
                    if db_mod.row_for_path(conn, p) is not None:
                        found.append(p)
                        continue
                    cand = by_name.get(os.path.basename(p).lower(), [])
                    if len(cand) == 1:
                        found.append(cand[0])
                    else:
                        missing += 1
                pid = "pl" + uuid.uuid4().hex[:10]
                node = db_mod.save_playlist(conn, {
                    "id": pid, "parent_id": parent, "kind": "playlist", "name": name})
                db_mod.add_playlist_items(conn, pid, found)
            finally:
                conn.close()
        self._json({"ok": True, "node": node, "added": len(found), "missing": missing})

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
                # Ein Generator speichert seine Belegung wie eine normale
                # Playlist (siehe db.PLAYLIST_KINDS).
                if node["kind"] not in ("playlist", "generator"):
                    self._fail("Nur reguläre Playlisten nehmen Tracks auf.")
                    return
                if node.get("locked"):
                    self._fail("Diese Playlist ist gesperrt. Zum Ändern zuerst entsperren.")
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
            if isinstance(payload.get("view"), dict):
                # Einzelne Ansicht (Spalten-Menue): ersetzt nur diese, eine
                # geloeschte kommt dadurch nicht zurueck (save_column_view()).
                with self.lock:
                    saved = settings_mod.save_column_view(payload["view"], bool(payload.get("create")))
                self._json({"ok": True, **saved})
                return
            with self.lock:
                saved = settings_mod.save_column_views(
                    payload.get("column_views") or [],
                    # Nicht mitgeschickt = unveraendert lassen (Spalten-Menue),
                    # mitgeschickt = setzen (Einstellungs-Dialog).
                    payload.get("drop_view") if "drop_view" in payload else None,
                    payload.get("edit_view") if "edit_view" in payload else None,
                    payload.get("player_view") if "player_view" in payload else None)
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, **saved})

    def _post_generator_curves(self) -> None:
        """Eigene Energiekurven des Generators am Stueck ersetzen (anlegen,
        ueberschreiben, loeschen). Unter self.lock wie die Spalten-Endpunkte:
        Lese-Aendere-Schreibe auf config.local.yaml."""
        try:
            payload = json.loads(self._body(200_000).decode("utf-8"))
            with self.lock:
                curves = settings_mod.save_generator_curves(payload.get("curves") or [])
        except ValueError as exc:
            self._fail(str(exc))
            return
        except (KeyError, TypeError) as exc:
            self._fail(f"Ungültige Anfrage: {exc}")
            return
        self._json({"ok": True, "generator_curves": curves})

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
        from .autocue.training import job as autocue_job
        if autocue_job.JOB.is_running():
            if not force:
                self._json({"ok": False, "training_running": True,
                            "error": "Es läuft noch ein Auto-Cue-Training."}, 409)
                return
            autocue_job.JOB.cancel()

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
            recheck_tag_issues=bool(payload.get("recheck_tags")),
            genremood=bool(payload.get("genremood")),
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

        name = self._query().get("name", "unbekannt")
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
    # Vorschau-Dateien eines frueheren Laufs, die niemand freigegeben hat.
    stemjob_mod.cleanup_all()

    url = f"http://127.0.0.1:{port}/tracktab.html"
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
