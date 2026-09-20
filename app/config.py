"""
Konfigurations-Loader und Pfad-Aufloesung.

Die Konfiguration hat drei Ebenen, jede ueberschreibt die vorherige:
  1. Defaults im Code
  2. config.yaml       — dokumentierte Vorgabe samt Kalibrier-Messwerten
  3. config.local.yaml — was ueber die Einstellungen im Browser geaendert wird

Dadurch bleibt config.yaml mit allen Kommentaren unangetastet, auch wenn im
UI an den Parametern gedreht wird.

Pfade haengen davon ab, wie das Tool laeuft: aus dem Quellbaum heraus liegt
alles im Projektordner, als gepackte App darf nicht ins Bundle geschrieben
werden — dann landen Daten und Konfiguration unter Application Support.
"""
from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
APP_NAME = "TrackTab"
_OLD_APP_NAMES = ("TrackLab", "Audio Quality Check", "MP3 Quality Check")

# Dateiendungen, die "extensions" annehmen darf (settings.validate()) und die
# der Server ueberhaupt ausliefert (server._get_audio()). Bewusst eine feste
# Liste an einer einzigen Stelle: die Autorisierung von Dateizugriffen haengt
# daran, dass eine Datei in der Datenbank steht -- und welche Dateien dorthin
# gelangen, entscheidet allein diese Liste zusammen mit library_paths. Waere
# sie frei waehlbar, liesse sich ueber "extensions" plus einem Scan jede
# beliebige Datei des Benutzerkontos einlesbar machen.
AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".wav", ".aif", ".aiff",
    ".ogg", ".oga", ".opus", ".wma", ".alac", ".aifc",
})


# ── Umgebung ──────────────────────────────────────────────────────────────

def is_frozen() -> bool:
    """Laeuft der Code aus einem PyInstaller-Bundle?"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Nur-lesbare Ressourcen: Weboberflaeche, Standard-Config, vendor/."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return PROJECT_ROOT


def base_dir() -> Path:
    """Beschreibbarer Ort fuer Datenbank, Reports und lokale Konfiguration."""
    if is_frozen():
        target = Path.home() / "Library" / "Application Support" / APP_NAME
        if not target.exists():
            # Umbenennung: die Daten unter dem alten Namen weiterverwenden statt
            # mit leerer Datenbank neu anzufangen.
            for old_name in _OLD_APP_NAMES:
                old = Path.home() / "Library" / "Application Support" / old_name
                if old.is_dir():
                    old.rename(target)
                    break
        target.mkdir(parents=True, exist_ok=True)
        return target
    return PROJECT_ROOT


def vendor_dir() -> Path:
    return bundle_dir() / "vendor"


def webui_dir() -> Path:
    return bundle_dir() / "app" / "webui"


def default_daw_template_path() -> Path:
    """Mitgelieferte Ableton-Vorlage (siehe app/ableton.py), nur lesend."""
    return bundle_dir() / "resources" / "ableton_template.als"


def config_path() -> Path:
    """Die dokumentierte config.yaml — im App-Betrieb einmalig herauskopiert."""
    target = base_dir() / "config.yaml"
    if is_frozen() and not target.exists():
        source = bundle_dir() / "config.yaml"
        if source.exists():
            shutil.copyfile(source, target)
    return target


def local_config_path() -> Path:
    """Vom Einstellungs-Dialog geschriebene Ebene."""
    return base_dir() / "config.local.yaml"


def resolve(path_value: str) -> Path:
    """Relative Pfade beziehen sich auf base_dir(), ~ wird expandiert."""
    p = Path(os.path.expanduser(str(path_value)))
    return p if p.is_absolute() else base_dir() / p


# ── Defaults ──────────────────────────────────────────────────────────────

def defaults() -> dict:
    return {
        "version": "1.0",
        "library_paths": ["~/Music/Music"],
        "extensions": [".mp3", ".aif", ".aiff"],
        "exclude_dirs": [".git"],
        "min_duration_s": 30.0,

        # Beim Bibliothek-Scan automatisch das in Music.app hinterlegte
        # Cover uebernehmen, wenn eine neue Datei noch keins hat (und bei
        # jedem Scan pruefen, ob ein nur im DB-Cache gehaltenes Cover sich
        # in Music.app geaendert hat) -- siehe app/coverfill.py. Loest NICHT
        # beim Import ueber die Einzelpruefung aus.
        "cover_auto_fill": True,

        # Browser fuer die Web-UI (Programmpfad, siehe settings.py
        # external_browser). Leer = Systemstandard-Browser.
        "external_browser": "/Applications/Safari.app",

        # Muster fuer "Dateien automatisch umbenennen" in der Einzelpruefung
        # (siehe app/rename.py). Unterstrich trennt die Kategorien,
        # Bindestrich Woerter innerhalb einer Kategorie; ein Platzhalter ohne
        # Wert laesst seine Kategorie ersatzlos entfallen -- bei
        # verlustfreien Formaten also z.B. {bitrate}. Umbenannt werden nur
        # Dateien AUSSERHALB der library_paths oben.
        "rename_pattern": "{artist}_{title}_{bpm}_{key}_{bitrate}_{year}",

        "max_analysis_s": 420,
        "skip_head_pct": 0.03,
        "skip_tail_pct": 0.03,

        "fft_size": 8192,
        "block_percentile": 95,
        "gate_rms_percentile": 55,
        "gate_abs_dbfs": -45.0,
        "min_gated_blocks": 8,
        "ref_band_hz": [1000, 6000],
        "noise_band_rel": 0.97,
        "smooth_hz": 200.0,
        "search_floor_hz": 9000.0,
        "cliff_span_hz": 1000.0,
        "cliff_min_db": 15.0,
        "steep_brickwall_db": 25.0,

        "cutoff_classes": {
            "mp3": [
                {"min_khz": 19.85, "kbps": 320},
                {"min_khz": 19.20, "kbps": 256},
                {"min_khz": 17.80, "kbps": 192},
                {"min_khz": 15.50, "kbps": 128},
                {"min_khz": 0.0, "kbps": 64},
            ],
            # Platzhalter -- ungeprueft. AAC hat eine andere Lowpass-zu-Bitrate
            # Kurve als LAME/MP3 und braucht eigene Kalibrierung: ./run.command
            # calibrate, sobald AAC-Referenztracks in der DB vorhanden sind.
            "aac": [
                {"min_khz": 19.50, "kbps": 256},
                {"min_khz": 18.50, "kbps": 192},
                {"min_khz": 17.00, "kbps": 128},
                {"min_khz": 0.0, "kbps": 96},
            ],
        },
        "verdict_suspect_steps": 1,
        "verdict_fake_steps": 2,
        "lame_lowpass_tolerance_khz": 1.0,

        # --- Verlustfreie Formate (ALAC/FLAC/WAV/AIFF) ---
        # Keine deklarierte Bitrate vorhanden -> Verdikt allein ueber die
        # Kantenposition, nicht ueber einen Bitratenvergleich.
        "lossless_suspect_khz": 19.0,
        "lossless_fake_khz": 17.5,
        # Eigene (niedrigere) Flankenhoehe statt steep_brickwall_db: 16-Bit-
        # PCM quantisiert nahezu stille Bloecke oberhalb des Cutoffs grob,
        # was den messbaren Rauschboden dort anhebt und die Flanke gegenueber
        # einer direkt dekodierten MP3 verflacht (an echtem Material
        # gemessen: ~13-16 dB statt >50 dB). Mit ./run.command calibrate
        # nachjustieren.
        "lossless_min_steepness_db": 10.0,
        "aac_encoder": "aac",   # nativer ffmpeg-Encoder, ueberall verfuegbar

        # --- Lautheit (EBU R128 / ITU-R BS.1770 ueber ffmpeg 'loudnorm') ---
        # Reiner Hinweis, kein Verdikt: anders als der Encoder-Tiefpass hat
        # Lautheit keine "richtige" Zielgroesse, sie haengt von Genre und
        # Erscheinungsjahr ab. loudness_ref_low/high_lufs spannen ein
        # DJ/Club-Referenzband auf (typisch fuer heutige Club-Masters), keine
        # Qualitaetsgrenze. loudness_clip_dbtp ist die einzige Ausnahme:
        # Intersample-Clipping ist immer ein technischer Fehler.
        "loudness_ref_low_lufs": -9.0,
        "loudness_ref_high_lufs": -6.0,
        "loudness_clip_dbtp": -1.0,

        "db_path": "data/quality.db",
        "report_path": "data/report.html",
        "csv_path": "data/report.csv",
        "m3u_path": "data/verdaechtig.m3u8",
        # Jahres-/Monats-Statistik aus audit_log.py + der events-Tabelle
        # (siehe app/stats.py), lazy neu gebaut wenn eine der Quellen sich
        # geaendert hat. Rein abgeleitet, jederzeit loeschbar.
        "stats_path": "data/stats.json",
        "spectrum_points": 160,

        # Automatisches Backup: hoechstens eins pro Kalendertag (siehe
        # backup.py), die aeltesten werden ueber backup_keep hinaus geloescht.
        "backup_path": "backup",
        "backup_keep": 10,

        # Taegliches Aenderungsprotokoll (siehe audit_log.py) -- eine Datei
        # pro Kalendertag (JSON Lines), wird nicht automatisch geloescht.
        "logs_path": "logs",

        "workers": 0,

        # Leer = iZotope RX wird automatisch gesucht
        "external_editor": "",
        # Leer = Mixed In Key wird automatisch gesucht (/Applications)
        "external_mik": "",
        # Kein Auto-detect wie bei Editor/MIK (Music.app ist auf jedem Mac
        # vorhanden, es gaebe also immer einen Treffer) -- die Auswahl hier
        # ist deshalb ein bewusster Opt-in, kein Pfad zum Starten des
        # Programms (AppleScript spricht intern immer "Music" an, siehe
        # media.MUSIC_APP). Leer = die gesamte Music.app-Integration bleibt
        # aus: Baum, "In Music"-Knopf, Bibliothek-Import, "Music.app
        # abgleichen", Cover-Autofill (app/coverfill.py).
        "external_music": "",
        # Leer = neueste installierte Rekordbox-Version wird automatisch
        # gesucht -- bei mehreren installierten Versionen (z.B. "rekordbox 6"
        # und "rekordbox 7") legt dieser Pfad auch fest, welche fuer den
        # Bibliothekszugriff (master.db) verwendet wird
        "external_rekordbox": "",
        # Name einer bereits bestehenden Rekordbox-Playlist -- wird NICHT
        # angelegt, nur befuellt. Leer = Rekordbox-Knoepfe bleiben deaktiviert.
        "rekordbox_playlist": "",
        # Prueft beim Hinzufuegen zu einer Rekordbox-Playlist den bereits
        # beim Scan gemessenen Cutoff jedes Tracks gegen rekordbox_min_cutoff_khz
        # -- unabhaengig von Verdikt oder manueller "corrected"-Markierung.
        "rekordbox_quality_check": True,
        # Schwelle in kHz; Tracks darunter (oder ohne verwertbaren Messwert)
        # loesen vor dem Hinzufuegen eine Warnung aus.
        "rekordbox_min_cutoff_khz": 19.0,
        # Welcher Rekordbox-Analysedaten-Stil fuer die Player-Waveform genutzt
        # wird, wenn der Track dort analysiert ist ("rgb" oder "3band") --
        # sonst automatisch die graue Standard-Waveform.
        "rekordbox_waveform_style": "rgb",
        # DAW fuer "In DAW oeffnen" (Logic, Ableton, ...) -- anders als
        # Editor/MIK/Rekordbox keine automatische Suche (kein einheitlicher
        # Name zum Erraten). Leer = der Knopf bleibt komplett ausgeblendet
        # statt wie bei den anderen Programmen ausgegraut zu erscheinen.
        "external_daw": "",
        # Ableton-Vorlagenprojekt (.als) fuer "In DAW oeffnen" -- siehe
        # app/ableton.py. Nur relevant, wenn external_daw auf Ableton zeigt;
        # leer = Ableton bekommt wie jede andere DAW nur die Audiodatei
        # per open -a (importiert dann NICHT in ein offenes Set).
        "external_daw_template": "",

        "shops": [
            {"id": "beatport", "name": "Beatport",
             "url": "https://www.beatport.com/search?q={q}",
             "color": "#01FF95", "enabled": True},
            {"id": "soundcloud", "name": "SoundCloud",
             "url": "https://soundcloud.com/search?q={q}",
             "color": "#FF5500", "enabled": True},
            {"id": "djcity", "name": "DJ City",
             "url": "https://www.djcity.com/search?q={q}",
             "color": "#E4002B", "enabled": True},
            {"id": "zipdj", "name": "Zip DJ",
             "url": "https://www.zipdj.com/app/search?q={q}",
             "color": "#00A651", "enabled": True},
            # id "itunes" ist reserviert: shopLinks() (app.js) erkennt diesen
            # Eintrag an der id und ersetzt den einfachen Link bei
            # ausgewaehlter Music App (external_music) durch die smarte
            # Preis-/Fund-Suche samt itmss:-Deep-Link (siehe media.py:
            # open_itunes_store()). Die URL hier ist die Web-Fallback-Seite
            # ohne Server/Music App -- music.apple.com, nicht die rohe
            # itunes.apple.com/search-JSON-API.
            {"id": "itunes", "name": "iTunes Store",
             "url": "https://music.apple.com/search?term={q}",
             "color": "#fc3c44", "enabled": True},
        ],

        # Reihenfolge der ausblendbaren Tabellenspalten (Web-UI, per Drag
        # sortierbar), je Ansicht (Bearbeiten/Player, siehe Layout-Umschalter)
        # getrennt. Leer = Vorgabe-Reihenfolge aus OPTIONAL_COLUMNS in app.js.
        "column_order": {"edit": [], "player": []},

        # Ausgeblendete Tabellenspalten (Web-UI, Spalten-Menue), ebenfalls je
        # Ansicht getrennt. Server-seitig statt localStorage, damit die Auswahl
        # auch einen Neustart uebersteht -- localStorage haengt am Port/Origin,
        # der beim Bundle nach einem Neustart wechseln kann (siehe _free_port()
        # in desktop.py).
        "hidden_columns": {"edit": [], "player": []},

        # Spaltenbreiten (Web-UI, Ziehgriff am Spaltenkopf), je Ansicht
        # getrennt wie column_order/hidden_columns. Lag frueher nur in
        # localStorage -- seit es gespeicherte Spaltenansichten gibt (siehe
        # column_views), gehoeren auch die Breiten der Standard-Spalten
        # dorthin, wo sie einen Neustart und einen Portwechsel ueberleben.
        "column_widths": {"edit": {}, "player": {}},

        # Gespeicherte Spaltenansichten ("Templates"): benannte Zusammen-
        # stellungen aus Reihenfolge, Sichtbarkeit und Breite, im
        # Spalten-Menue der Web-UI angelegt. Je Eintrag
        # {id, name, order: [], hidden: [], widths: {}}. Bewusst NICHT je
        # Ansicht (Bearbeiten/Player) getrennt -- eine Spaltenansicht ist nur
        # ein Satz Spalten und laesst sich in beiden Ansichten zuweisen.
        "column_views": [],

        # Die Spaltenansicht, die jede Liste ohne eigene Zuordnung bekommt
        # ("Standardliste", im Einstellungs-Dialog waehlbar). Leer = die im
        # Spalten-Menue eingestellten Standard-Spalten der aktiven Ansicht
        # (column_order/hidden_columns/column_widths).
        "column_view_default": "",

        # Fallback speziell fuer Apple-Music-Playlisten (view_id "mu:<id>"):
        # diese Fremd-Listen bekommen ueber das Sidebar-Menue bewusst keine
        # eigene Zuordnung (siehe app.js), werden also vor column_view_default
        # zuerst gegen diesen Wert aufgeloest. Leer = wie column_view_default.
        "column_view_music_default": "",

        # Welche Spaltenansicht welche Liste bekommt: {view_id: column_view_id}.
        # 'view_id' ist die Kennung aus VIEWS in app.js ("all", "ignored",
        # "pl:<id>", "sm:<id>", "fl:list1", ...). Nicht eingetragene Listen
        # nehmen die Standard-Spalten (column_order/hidden_columns/
        # column_widths der aktiven Ansicht).
        "column_view_assign": {},

        # Spaltenkonfiguration der Einzelprueflungen (Web-UI, eigener
        # "Spalten"-Knopf in den Einzelprueflungen), UNABHAENGIG von
        # column_order/hidden_columns/column_widths der Haupttabelle -- flach,
        # kein Bearbeiten/Player-Split, da die Einzelprueflungen
        # layoutunabhaengig sind (siehe CLAUDE.md, Abschnitt Report & Web-UI).
        "drop_column_order": [],
        "drop_hidden_columns": [],
        "drop_column_widths": {},

        # Haelt die Filterleiste (Listen, Status, Suche, Sammelaktionen) beim
        # Scrollen am oberen Fensterrand fest. Aus, weil die Leiste je nach
        # Fensterbreite mehrere Zeilen hoch wird und dann viel Platz kostet.
        "pin_filter_bar": False,

        # Laedt beim Erreichen des Listenendes automatisch weitere Treffer
        # nach, statt dass "weitere N laden" von Hand angeklickt werden muss.
        "infinite_scroll": False,

        # Schriftgroesse der gesamten Oberflaeche (klein/mittel/gross) --
        # skaliert per CSS zoom in app.js, siehe applyFontSize().
        "font_size": "mittel",

        # Farbschema: auto (folgt dem System), light, dark. Siehe
        # applyTheme() in app.js -- setzt/entfernt data-theme auf <html>,
        # die eigentlichen Farben stehen als CSS-Variablen in app.css.
        "theme": "auto",

        # Akzentfarbe der Oberflaeche (aktive Knoepfe, Player-Fortschritt,
        # ausgewaehlte Chips, ...). Eine von 8 festen Vorgabefarben statt
        # freiem Farbwaehler -- garantiert Kontrast in Hell/Dunkel ohne
        # Theme-Wissen im Einstellungen-Dialog, gleiches Prinzip wie die
        # Playlist-Farben (fl1..fl12, siehe db.py). "blue" entspricht
        # der bisherigen, fest verdrahteten Farbe -- unveraendertes Aussehen
        # ohne diese Einstellung anzufassen. Siehe applyAccentColor() in
        # app.js -- setzt data-accent auf <html>, die Farben selbst stehen
        # als CSS-Variablen (--dc-*) in app.css.
        "accent_color": "blue",

        # Oberflaechen-Sprache: auto (folgt navigator.language im Browser)
        # oder ein fester Sprachcode. de/en uebersetzt (siehe app/webui/
        # i18n/de.js, en.js) -- resolveLang() in app.js faellt auf "de"
        # zurueck, wenn die Browsersprache nicht vorliegt.
        "ui_language": "auto",

        # Toleranz fuer Tippfehler in der Suche: 0 = aus (nur exakte Treffer),
        # 1 = sehr tolerant. Wirkt wortweise, siehe fuzzyIncludes() in app.js.
        "search_typo_tolerance": 0.8,

        # Mindestanzahl getippter Zeichen, bevor die Werte-Autovervollstaendigung
        # eines /Parameters (z.B. "/Genre ho...") Vorschlaege zeigt. Gilt NICHT
        # fuer die Vorschlagsliste der Parameternamen selbst ("/al" -> "/Album"),
        # die immer ab dem ersten Zeichen greift. Siehe attachSearchFilterAutocomplete()
        # in app.js.
        "search_autocomplete_min_chars": 3,

        # Immer zusaetzlich zur eingegebenen Suche angewendete /Parameter-
        # Ausdruecke (gleiche Syntax wie das Suchfeld), je Eintrag eine
        # Zeile in den Einstellungen. Leer = kein Standardfilter. Siehe
        # DEFAULT_SEARCH_FILTERS/applyDefaultFilters() in app.js.
        "default_search_filters": [],
    }


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _normalize_cutoff_classes(cfg: dict) -> None:
    """cutoff_classes ist eine Leiter je Codec-Familie ({"mp3": [...], ...}).

    Abwaertskompatibel zur alten flachen Liste aus vor dieser Version
    geschriebenen config.yaml/config.local.yaml-Dateien: die wird als
    MP3-Leiter interpretiert. Da jede Konfigurationsebene den Schluessel
    komplett ersetzt (kein Deep-Merge), kann eine alte Datei mit nur der
    MP3-Leiter die AAC-Vorgabe verdecken -- fehlende Leitern werden deshalb
    aus defaults() nachgefuellt. Jede Leiter wird absteigend sortiert,
    damit die Zuordnung in classify.py eindeutig ist.
    """
    classes = cfg["cutoff_classes"]
    if isinstance(classes, list):
        classes = {"mp3": classes}
    for name, ladder in defaults()["cutoff_classes"].items():
        classes.setdefault(name, ladder)
    for name, ladder in classes.items():
        classes[name] = sorted(ladder, key=lambda c: float(c["min_khz"]), reverse=True)
    cfg["cutoff_classes"] = classes


def _normalize_columns(cfg: dict) -> None:
    """column_order/hidden_columns/column_widths sind je Ansicht (Bearbeiten/Player, siehe
    Layout-Umschalter) getrennte Listen. Abwaertskompatibel zur alten flachen
    Liste aus vor dem Layout-Umschalter geschriebenen config.local.yaml-
    Dateien: die wird als Liste der Ansicht "edit" interpretiert, "player"
    startet dann leer -- gleiches Prinzip wie bei _normalize_cutoff_classes().
    """
    for key in ("column_order", "hidden_columns"):
        value = cfg.get(key)
        if isinstance(value, list):
            value = {"edit": value, "player": []}
        elif not isinstance(value, dict):
            value = {}
        value.setdefault("edit", [])
        value.setdefault("player", [])
        cfg[key] = value

    widths = cfg.get("column_widths")
    if not isinstance(widths, dict):
        widths = {}
    for layout in ("edit", "player"):
        if not isinstance(widths.get(layout), dict):
            widths[layout] = {}
    cfg["column_widths"] = widths

    if not isinstance(cfg.get("column_views"), list):
        cfg["column_views"] = []
    if not isinstance(cfg.get("column_view_assign"), dict):
        cfg["column_view_assign"] = {}
    if not isinstance(cfg.get("column_view_default"), str):
        cfg["column_view_default"] = ""
    if not isinstance(cfg.get("column_view_music_default"), str):
        cfg["column_view_music_default"] = ""

    # Einzelpruefungen: flach, kein Ansicht-Split (siehe Kommentar bei den
    # Defaults oben).
    if not isinstance(cfg.get("drop_column_order"), list):
        cfg["drop_column_order"] = []
    if not isinstance(cfg.get("drop_hidden_columns"), list):
        cfg["drop_hidden_columns"] = []
    if not isinstance(cfg.get("drop_column_widths"), dict):
        cfg["drop_column_widths"] = {}


@lru_cache(maxsize=1)
def load() -> dict:
    cfg = defaults()
    for source in (config_path(), local_config_path()):
        cfg.update({k: v for k, v in _read_yaml(source).items() if v is not None})

    _normalize_cutoff_classes(cfg)
    _normalize_columns(cfg)
    return cfg


def reload() -> dict:
    """Nach dem Speichern von Einstellungen den Cache verwerfen."""
    load.cache_clear()
    return load()


def worker_count() -> int:
    n = load()["workers"]
    if n and n > 0:
        return int(n)
    return max(1, (os.cpu_count() or 2) - 1)
