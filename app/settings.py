"""
Einstellungen fuer die Oberflaeche.

Geaenderte Werte landen in config.local.yaml — die dokumentierte config.yaml
mit den Kalibrier-Messwerten bleibt unangetastet und dient weiter als
Rueckfallebene. Ein leeres config.local.yaml bedeutet also: alles auf Vorgabe.

Die Klassengrenzen werden je Codec (MP3, AAC) als einzelne Felder gezeigt
statt als verschachtelte Liste; sie werden beim Speichern wieder zu ihrer
jeweiligen Leiter in cfg["cutoff_classes"] zusammengesetzt.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import ableton as ableton_mod
from . import config as cfgmod
from . import rename as rename_mod

_CLASS_KEYS_MP3 = {"class_320_khz": 320, "class_256_khz": 256,
                    "class_192_khz": 192, "class_128_khz": 128}
_CLASS_KEYS_AAC = {"aac_class_256_khz": 256, "aac_class_192_khz": 192,
                    "aac_class_128_khz": 128}
# Ladder-Name (siehe cfg["cutoff_classes"]) je Key-Set
_LADDER_KEYS = {"mp3": _CLASS_KEYS_MP3, "aac": _CLASS_KEYS_AAC}

GROUPS = [
    {
        "id": "library", "title": "Bibliothek",
        "note": "Nach Änderungen hier ist ein neuer Scan nötig.",
        "fields": [
            {"key": "library_paths", "type": "folderlist", "label": "Ordner",
             "help": "Über den Knopf einen oder mehrere Ordner auswählen. "
                     "~ steht für dein Benutzerverzeichnis."},
            {"key": "extensions", "type": "list", "label": "Dateiendungen", "break": True, "width": "half",
             "help": "Komma-getrennt. Empfehlung: .mp3, .m4a, .flac, .wav, .aiff — "
                     "ALAC/AAC werden im .m4a-Container automatisch anhand des "
                     "Codecs unterschieden. .aac (Rohdaten ohne Container) nur "
                     "bei Bedarf ergänzen, kommt selten vor."},
            {"key": "exclude_dirs", "type": "list", "label": "Ordner überspringen", "width": "half",
             "help": "Ordnernamen, die nicht durchsucht werden."},
            {"key": "min_duration_s", "type": "number", "label": "Mindestdauer", "break": True, "width": "half",
             "unit": "s", "min": 0, "max": 600, "step": 1,
             "help": "Kürzere Dateien werden nicht beurteilt."},
            {"key": "cover_auto_fill", "type": "bool", "width": "half",
             "label": "Cover aus Music.app übernehmen",
             "show_if": {"key": "external_music", "not_empty": True},
             "help": "Trägt beim Bibliothek-Scan automatisch das in Music.app "
                     "hinterlegte Cover nach, wenn eine neu gefundene Datei "
                     "noch keins hat — unabhängig vom Dateiformat, auch für "
                     "WAV/AIFF, wo Music.app das Cover nicht in der Datei "
                     "selbst speichert. Prüft bei jedem Scan außerdem, ob ein "
                     "so übernommenes Cover sich in Music.app inzwischen "
                     "geändert hat oder dort entfernt wurde. Löst NICHT beim "
                     "Import über die Einzelprüfung aus — der Knopf „Manuell "
                     "neu analysieren“ gleicht das Cover dagegen immer ab, "
                     "unabhängig von dieser Einstellung."},
            {"key": "orphan_cleanup", "type": "button", "label": "Verwaiste Ordner",
             "icon": "trash", "break": True,
             "help": "Durchsucht die Ordner oben nach leeren Verzeichnissen — leer "
                     "zählt auch, wenn nur eine .DS_Store-Datei darin liegt — und "
                     "verschiebt sie in den Papierkorb. Fragt vorher noch einmal nach."},
        ],
    },
    {
        "id": "display", "title": "Darstellung",
        "fields": [
            {"key": "ui_language", "type": "select", "label": "Sprache",
             "options": [{"value": "auto", "label": "Automatisch (Browser)"},
                         {"value": "de", "label": "Deutsch"},
                         {"value": "en", "label": "English"}],
             "help": "„Automatisch“ übernimmt die Spracheinstellung des Browsers. "
                     "Beim Speichern laedt die Seite automatisch neu."},
            {"key": "theme", "type": "select", "label": "Farbschema",
             "options": [{"value": "auto", "label": "Automatisch (System)"},
                         {"value": "light", "label": "Hell"},
                         {"value": "dark", "label": "Dunkel"}],
             "help": "„Automatisch“ folgt der Systemeinstellung des Rechners."},
            {"key": "font_size", "type": "select", "label": "Schriftgröße",
             "options": [{"value": "klein", "label": "Klein"},
                         {"value": "mittel", "label": "Mittel"},
                         {"value": "gross", "label": "Groß"}],
             "help": "Wirkt sofort auf die gesamte Oberfläche."},
            {"key": "accent_color", "type": "color", "label": "Designfarbe",
             "options": [
                 {"value": "red", "label": "Rot"},
                 {"value": "orange", "label": "Orange"},
                 {"value": "yellow", "label": "Gelb"},
                 {"value": "green", "label": "Grün"},
                 {"value": "skyblue", "label": "Hellblau"},
                 {"value": "blue", "label": "Blau"},
                 {"value": "indigo", "label": "Indigo"},
                 {"value": "crimson", "label": "Pink"},
                 {"value": "purple", "label": "Lila"},
                 {"value": "brown", "label": "Braun"},
                 {"value": "slate", "label": "Schiefergrau"},
                 {"value": "dustyrose", "label": "Altrosa"},
             ],
             "help": "Akzentfarbe für aktive Knöpfe, den Player-Fortschritt und "
                     "Ähnliches. Wirkt sofort auf die gesamte Oberfläche. "
                     "Dieselbe Palette wie Merklisten und Playlisten."},
            {"key": "pin_filter_bar", "type": "bool", "break": True,
             "label": "Filterleiste anheften",
             "help": "Hält Listen, Status, Suche und die Sammelaktionen beim "
                     "Scrollen am oberen Fensterrand fest. Je nach "
                     "Fensterbreite bricht die Leiste auf mehrere Zeilen um "
                     "und kostet dann einiges an Höhe."},
            {"key": "infinite_scroll", "type": "bool",
             "label": "Endlos-Scrollen",
             "help": "Lädt beim Erreichen des unteren Listenendes automatisch "
                     "50 weitere Treffer nach, statt \"weitere laden\" von "
                     "Hand anklicken zu müssen."},
            {"key": "external_browser", "type": "app", "appList": "browsers",
             "label": "Browser für Web-UI", "break": True, "width": "half",
             "help": "Programm, in dem sich die Oberfläche beim App-Start "
                     "und beim Klick aufs Dock-Symbol öffnet. Leer lassen, "
                     "dann wird der Standardbrowser des Systems verwendet."},
        ],
    },
    {
        "id": "search", "title": "Suche",
        "fields": [
            {"key": "search_typo_tolerance", "type": "number", "label": "Tippfehler-Toleranz",
             "min": 0, "max": 1, "step": 0.05,
             "help": "Wie tolerant die Suche gegenüber Tippfehlern ist. 0 = nur "
                     "exakte Treffer, 1 = sehr tolerant. Wirkt wortweise, auch "
                     "in den /Parameter-Werten."},
            {"key": "search_autocomplete_min_chars", "type": "number",
             "label": "Mindestzeichen für Autovervollständigung",
             "min": 0, "max": 10, "step": 1,
             "help": "Ab wie vielen getippten Zeichen die Werte-Vorschläge "
                     "eines /Parameters erscheinen (z. B. /Genre ho…). Gilt "
                     "nicht für die Vorschlagsliste der Parameternamen selbst "
                     "(/al → /Album) — die greift immer schon ab dem ersten "
                     "Zeichen — und nicht für /Status, dessen kurze feste "
                     "Werteliste sofort vollständig erscheint."},
            {"key": "default_search_filters", "type": "paths", "label": "Standard-Suchfilter",
             "break": True, "allow_empty": True,
             "help": "Ein /Parameter-Ausdruck je Zeile, gleiche Syntax wie das "
                     "Suchfeld (siehe Suchhilfe dort), z. B. „/No /Genre "
                     "Acapella“. Wird IMMER zusätzlich zur eingegebenen Suche "
                     "angewendet (UND-verknüpft) und als eigene, andersfarbige "
                     "Chips unter der Suchleiste angezeigt. Ein Klick auf × "
                     "dort blendet einen Filter nur für die aktuelle Sitzung "
                     "aus — dauerhaft entfernen geht nur hier."},
        ],
    },
    {
        "id": "rename", "title": "Dateinamen",
        "note": "Für „Dateien automatisch umbenennen“ in den Einzelprüfungen. "
                "Dateien, die schon in einem der Bibliotheksordner oben liegen, "
                "werden nie umbenannt.",
        "fields": [
            {"key": "rename_pattern", "type": "text", "label": "Namensmuster",
             "placeholder": rename_mod.DEFAULT_PATTERN,
             "help": "Platzhalter: "
                     + ", ".join("{" + k + "} = " + label
                                 for k, label in rename_mod.PLACEHOLDERS)
                     + ". „_“ trennt die Kategorien, „-“ Wörter innerhalb einer "
                       "Kategorie. Ein Platzhalter ohne Wert lässt seine "
                       "Kategorie ersatzlos entfallen — {bitrate} bleibt bei "
                       "verlustfreien Formaten (FLAC/WAV/AIFF/ALAC) leer. "
                       "Beispiel: daft-punk_get-lucky_116bpm_8a_320kbps_2013.mp3. "
                       "Alles wird klein geschrieben, Umlaute werden "
                       "ausgeschrieben (ä → ae)."},
        ],
    },
    {
        "id": "analysis", "title": "Analyse",
        "note": "Nach Änderungen hier muss neu gemessen werden (Scan erzwingen).",
        "fields": [
            {"key": "max_analysis_s", "type": "number", "label": "Analysierte Länge",
             "unit": "s", "min": 30, "max": 3600, "step": 10,
             "help": "Nur die ersten N Sekunden — kappt lange DJ-Sets."},
            {"key": "cliff_min_db", "type": "number", "label": "Mindesthöhe der Kante",
             "unit": "dB", "min": 5, "max": 40, "step": 0.5,
             "help": "Darunter gilt: kein Tiefpass vorhanden. Gemessen wurden "
                     "5 dB ohne und 55–65 dB mit Encoder-Tiefpass."},
            {"key": "steep_brickwall_db", "type": "number", "label": "Schwelle harte Kante",
             "unit": "dB", "min": 10, "max": 60, "step": 1,
             "help": "Ab hier gilt die Flanke als eindeutiger Encoder-Tiefpass."},
            {"key": "gate_rms_percentile", "type": "number", "label": "Lautheits-Gate",
             "unit": "%", "min": 0, "max": 95, "step": 5,
             "help": "Nur die lautesten Blöcke werden ausgewertet. Höher = strenger."},
        ],
    },
    {
        "id": "classes", "parent": "analysis", "title": "Klassifikation (MP3)",
        "note": "Wirkt sofort — die Neubewertung braucht keine neue Messung.",
        "fields": [
            {"key": "class_320_khz", "type": "number", "label": "ab Klasse 320",
             "unit": "kHz", "min": 15, "max": 22.05, "step": 0.05},
            {"key": "class_256_khz", "type": "number", "label": "ab Klasse 256",
             "unit": "kHz", "min": 14, "max": 22.05, "step": 0.05},
            {"key": "class_192_khz", "type": "number", "label": "ab Klasse 192",
             "unit": "kHz", "min": 12, "max": 22.05, "step": 0.05},
            {"key": "class_128_khz", "type": "number", "label": "ab Klasse 128",
             "unit": "kHz", "min": 8, "max": 22.05, "step": 0.05},
            {"key": "verdict_suspect_steps", "type": "number", "label": "Stufen bis VERDÄCHTIG",
             "break": True, "min": 1, "max": 4, "step": 1},
            {"key": "verdict_fake_steps", "type": "number", "label": "Stufen bis FAKE",
             "min": 1, "max": 4, "step": 1},
        ],
    },
    {
        "id": "classes_aac", "parent": "analysis", "title": "Klassifikation (AAC)",
        "note": "Wirkt sofort. Vorgabewerte sind ungeprüft — mit "
                "./run.command calibrate an echtem Material nachmessen.",
        "fields": [
            {"key": "aac_class_256_khz", "type": "number", "label": "ab Klasse 256",
             "unit": "kHz", "min": 14, "max": 22.05, "step": 0.05},
            {"key": "aac_class_192_khz", "type": "number", "label": "ab Klasse 192",
             "unit": "kHz", "min": 12, "max": 22.05, "step": 0.05},
            {"key": "aac_class_128_khz", "type": "number", "label": "ab Klasse 128",
             "unit": "kHz", "min": 8, "max": 22.05, "step": 0.05},
        ],
    },
    {
        "id": "classes_lossless", "parent": "analysis", "title": "Klassifikation (verlustfrei)",
        "note": "Gilt für ALAC/FLAC/WAV/AIFF. Ohne deklarierte Bitrate zählt "
                "hier nur, ob und wo eine harte Encoder-Kante liegt.",
        "fields": [
            {"key": "lossless_suspect_khz", "type": "number", "label": "Kante ab hier VERDÄCHTIG",
             "unit": "kHz", "min": 10, "max": 22.05, "step": 0.05},
            {"key": "lossless_fake_khz", "type": "number", "label": "Kante ab hier FAKE",
             "unit": "kHz", "min": 10, "max": 22.05, "step": 0.05},
            {"key": "lossless_min_steepness_db", "type": "number", "label": "Mindest-Flankenhöhe",
             "unit": "dB", "min": 3, "max": 40, "step": 0.5,
             "help": "Niedriger als bei MP3, weil 16-Bit-PCM nahezu stille "
                     "Höhen oberhalb des Cutoffs grob quantisiert und die "
                     "Flanke dadurch verflacht."},
        ],
    },
    {
        "id": "loudness", "title": "Lautheit",
        "note": "Reiner Hinweis, kein Verdikt — Lautheit hängt von Genre und "
                "Erscheinungsjahr ab. Nach Änderungen hier den Report neu erzeugen.",
        "fields": [
            {"key": "loudness_ref_low_lufs", "type": "number",
             "label": "Club-Referenz ab (leise)", "unit": "LUFS",
             "min": -30, "max": 0, "step": 0.5,
             "help": "Integrated Loudness darunter gilt als leiser als der "
                     "DJ/Club-Standard — kein Fehler, nur ein Hinweis fürs Gain-Staging."},
            {"key": "loudness_ref_high_lufs", "type": "number",
             "label": "Club-Referenz bis (laut)", "unit": "LUFS",
             "min": -30, "max": 0, "step": 0.5,
             "help": "Integrated Loudness darüber gilt als lauter als der DJ/Club-Standard."},
            {"key": "loudness_clip_dbtp", "type": "number",
             "label": "True-Peak-Warnung ab", "unit": "dBTP",
             "min": -9, "max": 0, "step": 0.5,
             "help": "Ab hier droht Intersample-Clipping — anders als die Referenzwerte "
                     "oben unabhängig von Genre oder Epoche immer ein technischer Fehler."},
        ],
    },
    {
        "id": "tools", "title": "Externe Programme",
        "fields": [
            {"key": "external_editor", "type": "app", "appList": "editors", "width": "half",
             "label": "Audio-Editor", "reload": True,
             "help": "Programm hinter dem Spektrum-Knopf, z. B. iZotope RX oder "
                     "Audacity (Übersicht in der Dokumentation). Ohne Auswahl "
                     "hier bleibt der Knopf komplett ausgeblendet, auch wenn "
                     "ein unterstütztes Programm installiert ist."},
            {"key": "external_mik", "type": "app", "appList": "miks", "label": "Mixed In Key", "width": "half",
             "reload": True,
             "help": "Programm hinter „In Mixed In Key öffnen“. Öffnet Tracks dort "
                     "zum Importieren — MIK analysiert (Key/BPM/Energy) und schreibt "
                     "die Tags danach selbst, im Hintergrund. Ohne Auswahl hier "
                     "bleiben die zugehörigen Knöpfe komplett ausgeblendet, auch "
                     "wenn Mixed In Key installiert ist."},
            {"key": "external_music", "type": "app", "appList": "musics", "label": "Music App", "width": "half",
             "reload": True,
             "help": "Schaltet die Music.app-Integration frei: Playlisten-Baum, "
                     "„In Music öffnen“, Bibliothek-Import aus der Einzelprüfung, "
                     "„Music.app abgleichen“ und Cover-Autofill. Anders als bei "
                     "Editor/Mixed In Key kein automatisches Suchen — Music.app "
                     "ist auf jedem Mac vorhanden, die Auswahl hier ist deshalb "
                     "ein bewusstes Ein-/Ausschalten. Leer = nichts davon wird "
                     "angezeigt oder ausgeführt."},
            {"key": "aac_encoder", "type": "text", "label": "AAC-Encoder", "width": "half",
             "placeholder": "aac",
             "help": "ffmpeg-Encodername für AAC-Neukodierung (calibrate, "
                     "Bitrate-Korrektur). 'aac' ist immer verfügbar. "
                     "'libfdk_aac' klingt besser, ist aber nicht in jedem "
                     "ffmpeg-Build enthalten — bei Nichtverfügbarkeit wird "
                     "automatisch auf 'aac' zurückgefallen."},
            {"key": "external_daw", "type": "app", "appList": "daws", "label": "DAW", "width": "half",
             "reload": True,
             "help": "Programm hinter „In DAW öffnen“ pro Zeile (z. B. Logic, "
                     "Ableton, Cubase). Anders als die anderen Programme hier "
                     "keine automatische Suche — ohne Auswahl bleibt der Knopf "
                     "komplett ausgeblendet statt nur ausgegraut."},
            {"key": "external_daw_template", "type": "text", "label": "Ableton-Vorlage",
             "width": "half", "placeholder": "Pfad zu einer .als-Datei",
             "pick": "/api/pick-daw-template",
             "show_if": {"key": "external_daw", "contains": "ableton"},
             "help": "Nur bei Ableton als DAW: Projekt mit der gewünschten "
                     "FX-Kette auf Spur 1, aber OHNE eigenen Clip im "
                     "Arrangement-Fenster dort. Ableton importiert eine per "
                     "„In DAW öffnen“ übergebene Datei sonst nicht in ein "
                     "offenes Set — mit Vorlage wird stattdessen bei jedem "
                     "Klick ein frisches Projekt mit dem Track auf Spur 1 "
                     "erzeugt. Leer = die mitgelieferte Standard-Vorlage "
                     "wird verwendet."},
        ],
    },
    {
        "id": "rekordbox", "title": "Rekordbox",
        "fields": [
            {"key": "external_rekordbox", "type": "app", "appList": "rekordboxes",
             "label": "Rekordbox", "reload": True,
             "help": "Programm hinter dem Rekordbox-Symbol — die Auswahl zeigt nur "
                     "installierte Rekordbox-Versionen, keine anderen Programme. "
                     "Sind mehrere installiert (z.B. „rekordbox 6“ und "
                     "„rekordbox 7“), legt sie auch fest, welche Version für den "
                     "Bibliothekszugriff (Playlist hinzufügen, Präsenz-Abgleich, "
                     "Cues) verwendet wird. Leer lassen, dann wird die neueste "
                     "installierte Version automatisch verwendet."},
            {"key": "rekordbox_playlist", "type": "text", "rbpick": True, "reload": True,
             "label": "Rekordbox-Playlist",
             "placeholder": "z.B. ##WORK/Neue Tracks",
             "help": "Bereits bestehende Rekordbox-Playlist. Über den "
                     "Rekordbox-Knopf hinzugefügte Tracks landen dort — die "
                     "Playlist wird nicht automatisch angelegt, sie muss vorher "
                     "in Rekordbox existieren. „Playlist auswählen“ lädt den "
                     "aktuellen Playlist-Baum und geht nur bei geschlossenem "
                     "Rekordbox. Leer lassen, dann bleibt der Knopf ausgeblendet."},
            {"key": "rekordbox_waveform_style", "type": "select", "break": True, "width": "half",
             "label": "Waveform-Stil",
             "options": [{"value": "standard", "label": "Standard"},
                         {"value": "rgb", "label": "RGB"},
                         {"value": "3band", "label": "3-Band"}],
             "help": "„Standard“ erzwingt die graue Waveform. RGB/3-Band wirken "
                     "nur, wenn der Track in Rekordbox vorhanden, dort analysiert "
                     "ist und die passende Analysedatei existiert — sonst "
                     "automatisch ebenfalls die graue Standard-Waveform."},
            {"key": "rekordbox_min_cutoff_khz", "type": "number", "unit": "kHz",
             "min": 0, "max": 22, "step": 0.5, "label": "Mindest-Cutoff",
             "help": "Tracks mit gemessenem Cutoff darunter lösen die Warnung aus."},
            {"key": "rekordbox_quality_check", "type": "bool", "width": "half",
             "label": "Qualitätsprüfung beim Import",
             "help": "Prüft beim Hinzufügen zu einer Rekordbox-Playlist den "
                     "gemessenen Cutoff jedes Tracks gegen die Schwelle rechts "
                     "— unabhängig von Verdikt oder manueller "
                     "Korrektur-Markierung. Liegt ein Track darunter (oder ist "
                     "der Cutoff nicht messbar), kommt vor dem Hinzufügen eine "
                     "Warnung mit der Wahl, trotzdem hinzuzufügen."},
        ],
    },
    {
        "id": "performance", "title": "Leistung",
        "fields": [
            {"key": "workers", "type": "number", "label": "Parallele Prozesse",
             "min": 0, "max": 32, "step": 1,
             "help": "0 bedeutet: alle Kerne bis auf einen."},
        ],
    },
]

_FIELDS = {f["key"]: f for g in GROUPS for f in g["fields"]}


def _by_kbps(cfg: dict, ladder: str) -> dict[int, float]:
    return {int(c["kbps"]): float(c["min_khz"]) for c in cfg["cutoff_classes"][ladder]}


def current(cfg: dict | None = None) -> dict:
    """Aktuelle Werte aller bearbeitbaren Felder."""
    cfg = cfg or cfgmod.load()
    values: dict = {}
    by_kbps = {name: _by_kbps(cfg, name) for name in _LADDER_KEYS}
    for key, field in _FIELDS.items():
        for ladder, class_keys in _LADDER_KEYS.items():
            if key in class_keys:
                values[key] = by_kbps[ladder].get(class_keys[key], 0.0)
                break
        else:
            values[key] = cfg.get(key)
    return values


def describe() -> dict:
    from . import media
    ensure_apple_music_column_view()
    defaults = current(_defaults_cfg())
    detected = media.find_audio_editor()
    detected_mik = media.find_mixed_in_key()
    detected_rekordbox = media.find_rekordbox()
    return {"groups": GROUPS, "values": current(), "defaults": defaults,
            "local_file": str(cfgmod.local_config_path()),
            "detected_editor": detected or "",
            "detected_mik": detected_mik or "",
            "detected_rekordbox": detected_rekordbox or "",
            # Kein UI-Feld (kein Setting zum Editieren, nur Anzeige im
            # Backup-Block) -- deshalb hier separat statt ueber _FIELDS/current().
            "backup_keep": int(cfgmod.load().get("backup_keep", 10)),
            "browsers": media.browser_apps(),
            "rekordboxes": media.list_rekordbox_apps(),
            "miks": media.list_mixed_in_key_apps(),
            "daws": media.list_daw_apps(),
            "editors": media.list_audio_editor_apps(),
            "musics": media.list_music_apps(),
            "shops": cfgmod.load().get("shops", []),
            "column_order": cfgmod.load().get("column_order", {"edit": [], "player": []}),
            "hidden_columns": cfgmod.load().get("hidden_columns", {"edit": [], "player": []}),
            "column_widths": cfgmod.load().get("column_widths", {"edit": {}, "player": {}}),
            "column_views": cfgmod.load().get("column_views", []),
            "column_view_assign": cfgmod.load().get("column_view_assign", {}),
            "column_view_default": cfgmod.load().get("column_view_default", ""),
            "column_view_music_default": cfgmod.load().get("column_view_music_default", ""),
            "drop_column_order": cfgmod.load().get("drop_column_order", []),
            "drop_hidden_columns": cfgmod.load().get("drop_hidden_columns", []),
            "drop_column_widths": cfgmod.load().get("drop_column_widths", {})}


def _defaults_cfg() -> dict:
    """Nur Code-Defaults plus config.yaml — ohne die lokale Ebene."""
    cfg = cfgmod.defaults()
    try:
        with open(cfgmod.config_path(), "r", encoding="utf-8") as fh:
            cfg.update({k: v for k, v in (yaml.safe_load(fh) or {}).items()
                        if v is not None})
    except OSError:
        pass
    cfgmod._normalize_cutoff_classes(cfg)
    return cfg


def _clean_number(field: dict, raw):
    value = float(raw)
    lo, hi = field.get("min"), field.get("max")
    if lo is not None and value < float(lo):
        raise ValueError(f"{field['label']}: kleiner als {lo}")
    if hi is not None and value > float(hi):
        raise ValueError(f"{field['label']}: größer als {hi}")
    if float(field.get("step", 1)) >= 1 and field.get("unit") != "kHz":
        return int(round(value))
    return round(value, 3)


def _clean_list(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [x for x in raw.replace("\n", ",").split(",")]
    return [str(x).strip() for x in raw if str(x).strip()]


def validate(incoming: dict) -> dict:
    """Prueft die Eingaben und liefert die zu speichernden Werte."""
    clean: dict = {}
    for key, raw in incoming.items():
        field = _FIELDS.get(key)
        if field is None:
            continue                       # unbekannte Schluessel still verwerfen
        if field["type"] == "number":
            clean[key] = _clean_number(field, raw)
        elif field["type"] == "bool":
            # Der Browser schickt echtes true/false (checkbox.checked); die
            # Strings decken einen Aufruf per curl o.ae. ab.
            clean[key] = raw if isinstance(raw, bool) else \
                str(raw).strip().lower() in ("1", "true", "on", "yes", "ja")
        elif field["type"] == "app":
            clean[key] = _clean_app_path(field, raw)
        elif field["type"] == "text":
            clean[key] = str(raw).strip()
        elif field["type"] in ("select", "color"):
            val = str(raw).strip()
            options = {opt["value"] for opt in field.get("options", [])}
            if options and val not in options:
                raise ValueError(f"{field['label']}: ungültiger Wert")
            clean[key] = val
        elif field["type"] in ("list", "paths", "folderlist"):
            items = _clean_list(raw)
            if not items and not field.get("allow_empty"):
                raise ValueError(f"{field['label']}: darf nicht leer sein")
            if field["type"] == "list" and key == "extensions":
                items = [i if i.startswith(".") else "." + i for i in items]
                unknown = [i for i in items
                           if i.lower() not in cfgmod.AUDIO_EXTENSIONS]
                if unknown:
                    raise ValueError(
                        "Keine bekannte Audio-Endung: " + ", ".join(unknown))
            clean[key] = items
        else:
            clean[key] = raw

    # Klassengrenzen muessen absteigend bleiben, sonst wird die Zuordnung wirr
    _check_descending(clean, ("class_320_khz", "class_256_khz",
                               "class_192_khz", "class_128_khz"))
    _check_descending(clean, ("aac_class_256_khz", "aac_class_192_khz",
                               "aac_class_128_khz"))

    if "rename_pattern" in clean:
        clean["rename_pattern"] = rename_mod.validate_pattern(clean["rename_pattern"])

    if clean.get("external_daw_template"):
        template = Path(clean["external_daw_template"]).expanduser()
        if template.suffix.lower() != ".als" or not template.is_file():
            raise ValueError(
                "Ableton-Vorlage: keine vorhandene .als-Datei.")
        try:
            ableton_mod.validate_template(str(template))
        except ableton_mod.AbletonTemplateError as exc:
            raise ValueError(str(exc)) from exc
        clean["external_daw_template"] = str(template)

    if "loudness_ref_low_lufs" in clean and "loudness_ref_high_lufs" in clean:
        if clean["loudness_ref_low_lufs"] >= clean["loudness_ref_high_lufs"]:
            raise ValueError(
                "Die Club-Referenz „ab (leise)“ muss kleiner sein als „bis (laut)“.")
    return clean


def _clean_app_path(field: dict, raw) -> str:
    """
    Programmpfad aus den Einstellungen pruefen.

    Der Wert landet in `open -a <pfad> <datei>` (media.open_in_app()) und
    startet damit ein Programm mit einer Datei aus der Bibliothek. Ein freier
    Pfad waere ein Weg, aus der Oberflaeche heraus beliebige Programme zu
    starten -- deshalb nur echte, vorhandene Programmbuendel. Leer heisst
    weiterhin "keine Angabe" (dann sucht media.py selbst bzw. der Knopf
    bleibt aus).

    Die Programmauswahl im Dialog kommt aus media.list_apps() und liefert
    ohnehin genau solche Pfade.
    """
    value = str(raw).strip()
    if not value:
        return ""
    expanded = Path(value).expanduser()
    if expanded.suffix.lower() != ".app" or not expanded.is_dir():
        raise ValueError(f"{field['label']}: bitte ein Programm (.app) auswählen.")
    return str(expanded)


def _check_descending(clean: dict, keys: tuple[str, ...]) -> None:
    present = [k for k in keys if k in clean]
    values = [clean[k] for k in present]
    if values != sorted(values, reverse=True):
        raise ValueError(
            f"Die Klassengrenzen müssen von {keys[0]} nach {keys[-1]} abnehmen."
        )


def save(incoming: dict) -> dict:
    """Schreibt die Aenderungen nach config.local.yaml und laedt neu."""
    clean = validate(incoming)

    stored: dict = {}
    path = cfgmod.local_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                stored = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            stored = {}

    all_class_keys = {k for keys in _LADDER_KEYS.values() for k in keys}
    classes = {k: v for k, v in clean.items() if k in all_class_keys}
    plain = {k: v for k, v in clean.items() if k not in all_class_keys}
    stored.update(plain)

    if classes:
        live = cfgmod.load()["cutoff_classes"]
        existing = stored.get("cutoff_classes")
        if isinstance(existing, list):
            existing = {"mp3": existing}       # alte flache Form vor diesem Feature
        stored_ladders = dict(existing or {})
        for ladder, class_keys in _LADDER_KEYS.items():
            touched = {k: v for k, v in classes.items() if k in class_keys}
            if not touched:
                continue
            base = {int(c["kbps"]): float(c["min_khz"]) for c in live[ladder]}
            for key, kbps in class_keys.items():
                if key in touched:
                    base[kbps] = float(touched[key])
            stored_ladders[ladder] = [
                {"min_khz": round(khz, 3), "kbps": kbps}
                for kbps, khz in sorted(base.items(), key=lambda kv: kv[1], reverse=True)
            ]
        stored["cutoff_classes"] = stored_ladders

    header = ("# Vom Einstellungs-Dialog geschrieben.\n"
              "# Überschreibt config.yaml. Datei löschen = zurück auf Vorgabe.\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(stored, fh, allow_unicode=True, sort_keys=True,
                       default_flow_style=False)
    return cfgmod.reload()


def _slugify(name: str, taken: set[str]) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "shop"
    slug = base
    n = 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def validate_shops(items: list) -> list[dict]:
    """Prueft die Shop-Liste aus dem Einstellungs-Dialog."""
    clean: list[dict] = []
    used: set[str] = set()
    for raw in items:
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not name and not url:
            continue                       # leere Zeile (z.B. gerade erst angelegt) still uebergehen
        if not name:
            raise ValueError("Ein Shop braucht einen Namen.")
        if urlsplit(url).scheme not in ("http", "https"):
            # Ohne diese Pruefung waere z.B. "javascript:..." moeglich -- der
            # Client baut daraus einen Link, ein Klick fuehrte den Code im
            # Origin der Oberflaeche aus (siehe shopLinks() in app.js).
            raise ValueError(f"{name}: die URL muss mit http:// oder https:// beginnen.")
        if "{q}" not in url:
            raise ValueError(f"{name}: die URL muss {{q}} als Platzhalter enthalten.")
        slug = str(raw.get("id") or "").strip() or _slugify(name, used)
        used.add(slug)
        clean.append({
            "id": slug, "name": name, "url": url,
            "color": str(raw.get("color") or "#888888").strip(),
            "enabled": bool(raw.get("enabled", True)),
        })
    return clean


def _as_layout_map(value) -> dict:
    """Wandelt einen evtl. noch alten, flachen column_order/hidden_columns-
    Wert (vor dem Layout-Umschalter) in die neue, nach Ansicht getrennte Form
    um -- gleiches Prinzip wie config.py::_normalize_columns()."""
    if isinstance(value, list):
        return {"edit": list(value), "player": []}
    if isinstance(value, dict):
        return {"edit": list(value.get("edit") or []),
                "player": list(value.get("player") or [])}
    return {"edit": [], "player": []}


def _read_local() -> dict:
    """config.local.yaml als Dictionary -- fehlend oder kaputt = leer."""
    path = cfgmod.local_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _write_local(values: dict) -> None:
    """Schreibt die uebergebenen Schluessel nach config.local.yaml (der Rest
    der Datei bleibt stehen) und verwirft den Konfigurations-Cache. Einziger
    Schreibweg fuer die Einstellungen, die nicht ueber GROUPS laufen (Shops,
    Merklisten, Spalten) -- vorher stand dieselbe Lese-Aendere-Schreibe-Folge
    in jeder dieser Funktionen einzeln."""
    stored = _read_local()
    stored.update(values)
    header = ("# Vom Einstellungs-Dialog geschrieben.\n"
              "# Überschreibt config.yaml. Datei löschen = zurück auf Vorgabe.\n")
    with open(cfgmod.local_config_path(), "w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump(stored, fh, allow_unicode=True, sort_keys=True,
                       default_flow_style=False)
    cfgmod.reload()


def _clean_keys(values) -> list[str]:
    """Spalten-Keys entdoppeln und leere verwerfen. Unbekannte Keys werden
    hier NICHT geprueft, das macht app.js beim Laden gegen OPTIONAL_COLUMNS --
    so faellt eine spaeter umbenannte Spalte dort auf und nicht hier."""
    clean: list[str] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, list) else []:
        key = str(raw).strip()
        if key and key not in seen:
            clean.append(key)
            seen.add(key)
    return clean


def _clean_widths(values) -> dict:
    """Spaltenbreiten in Pixeln. Unter 40 laesst sich eine Spalte nicht mehr
    greifen (gleicher Wert wie der Ziehgriff in app.js), nach oben genuegt ein
    grober Deckel gegen unsinnige Werte."""
    clean: dict = {}
    if not isinstance(values, dict):
        return clean
    for key, raw in values.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            width = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        clean[name] = max(40, min(2000, width))
    return clean


def _as_width_map(value) -> dict:
    """Wie _as_layout_map(), nur fuer die Breiten-Dictionaries je Ansicht."""
    if not isinstance(value, dict):
        return {"edit": {}, "player": {}}
    return {"edit": dict(value.get("edit") or {}),
            "player": dict(value.get("player") or {})}


def _as_assign_map(value) -> dict:
    """{view_id: column_view_id} aus config.local.yaml, unbrauchbare Eintraege
    fallen weg."""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if k and v}


def save_columns(layout: str, order: list, hidden: list,
                 widths: dict | None = None) -> dict:
    """Schreibt Reihenfolge, ausgeblendete Spalten und Breiten der
    STANDARD-Spalten einer Ansicht (Bearbeiten/Player, siehe Layout-Umschalter)
    nach config.local.yaml -- server-seitig statt localStorage, damit alles
    auch einen Server-Neustart uebersteht. Die Standard-Spalten gelten fuer
    jede Liste ohne eigene Zuordnung (siehe save_column_assign())."""
    if layout not in ("edit", "player"):
        raise ValueError(f"Unbekannte Ansicht: {layout}")

    clean_order = _clean_keys(order)
    clean_hidden = _clean_keys(hidden)
    clean_widths = _clean_widths(widths)

    stored = _read_local()
    column_order = _as_layout_map(stored.get("column_order"))
    hidden_columns = _as_layout_map(stored.get("hidden_columns"))
    column_widths = _as_width_map(stored.get("column_widths"))
    column_order[layout] = clean_order
    hidden_columns[layout] = clean_hidden
    column_widths[layout] = clean_widths
    _write_local({"column_order": column_order, "hidden_columns": hidden_columns,
                  "column_widths": column_widths})
    return {"order": clean_order, "hidden": clean_hidden, "widths": clean_widths}


def save_drop_columns(order: list, hidden: list, widths: dict | None = None) -> dict:
    """Schreibt Reihenfolge, ausgeblendete Spalten und Breiten der
    Einzelpruefungen-Tabelle -- flach, kein Bearbeiten/Player-Split wie bei
    save_columns(), die Einzelpruefungen sind layoutunabhaengig und haben
    keine eigene Liste, der sich eine Spaltenansicht zuweisen liesse."""
    clean_order = _clean_keys(order)
    clean_hidden = _clean_keys(hidden)
    clean_widths = _clean_widths(widths)
    _write_local({"drop_column_order": clean_order, "drop_hidden_columns": clean_hidden,
                  "drop_column_widths": clean_widths})
    return {"order": clean_order, "hidden": clean_hidden, "widths": clean_widths}


# Mehr braucht niemand von Hand auseinanderzuhalten -- der Deckel steht hier
# nur, damit ein fehlerhafter Client die Datei nicht unbegrenzt aufblaeht.
_MAX_COLUMN_VIEWS = 20


def validate_column_views(items: list) -> list[dict]:
    """Prueft die gespeicherten Spaltenansichten aus dem Spalten-Menue. Wie
    validate_shops() wird die 'id' aus dem Namen gebildet, wenn der Client
    keine mitschickt -- anders als dort darf sie sich beim Umbenennen aber
    NICHT aendern: die Zuordnung Liste -> Ansicht (column_view_assign) haengt
    daran und wuerde sonst verwaisen."""
    clean: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(items if isinstance(items, list) else []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()[:40]
        if not name:
            continue                      # namenlose Ansicht waere nicht waehlbar
        ident = str(raw.get("id", "")).strip()[:40]
        if not ident:
            ident = f"cv{i + 1}"
        while ident in seen:
            ident += "_"
        seen.add(ident)
        clean.append({
            "id": ident, "name": name,
            "order": _clean_keys(raw.get("order")),
            "hidden": _clean_keys(raw.get("hidden")),
            "widths": _clean_widths(raw.get("widths")),
        })
        if len(clean) >= _MAX_COLUMN_VIEWS:
            break
    return clean


_APPLE_MUSIC_VIEW_ID = "cv_apple_music"
_APPLE_MUSIC_COLUMNS = ["n", "cv", "a", "t"]
# Rest der OPTIONAL_COLUMNS-Keys aus app.js (Zeile ~215-240), dort
# massgeblich -- nur zum einmaligen Anlegen der Vorgabe-Ansicht hier
# dupliziert.
_APPLE_MUSIC_HIDDEN_COLUMNS = ["v", "co", "kb", "mk", "cf", "st", "lu", "tp", "du",
                               "rb", "im", "da", "al", "tn", "aa", "cp", "ge", "yr",
                               "bp", "cm"]


def ensure_apple_music_column_view() -> None:
    """Legt einmalig eine Spaltenansicht 'Apple Music' (Datei, Cover,
    Kuenstler, Titel) an und setzt sie als Standard fuer Music.app-Playlisten
    (column_view_music_default) -- diese Fremd-Listen bekommen ueber das
    Sidebar-Menue bewusst keine eigene Zuordnung (siehe app.js), brauchen also
    einen eigenen Vorgabe-Weg statt der allgemeinen Standard-Spaltenansicht.
    Greift nur, wenn column_view_music_default noch nie geschrieben wurde --
    ein bewusst geleerter Wert (Nutzer hat die Einstellung zurueckgesetzt)
    wird nicht erneut ueberschrieben."""
    stored = _read_local()
    if "column_view_music_default" in stored:
        return
    views = list(cfgmod.load().get("column_views", []))
    if not any(v.get("id") == _APPLE_MUSIC_VIEW_ID for v in views):
        views.append({
            "id": _APPLE_MUSIC_VIEW_ID, "name": "Apple Music",
            "order": _APPLE_MUSIC_COLUMNS + _APPLE_MUSIC_HIDDEN_COLUMNS,
            "hidden": list(_APPLE_MUSIC_HIDDEN_COLUMNS), "widths": {},
        })
    clean = validate_column_views(views)
    _write_local({"column_views": clean, "column_view_music_default": _APPLE_MUSIC_VIEW_ID})


def save_column_views(items: list, default_id: str | None = None,
                       music_default_id: str | None = None) -> dict:
    """Schreibt die Spaltenansichten nach config.local.yaml, zusammen mit der
    Standard-Spaltenansicht (welche Ansicht jede Liste ohne eigene Zuordnung
    bekommt). Zuordnungen auf eine inzwischen geloeschte Ansicht fallen dabei
    weg -- sonst zeigte eine Liste dauerhaft auf etwas, das es nicht mehr
    gibt, und laege stumm wieder auf den Standard-Spalten.

    'default_id' None laesst die bisherige Standardansicht stehen (nur die
    Pruefung gegen die geloeschten Ansichten laeuft trotzdem) -- der
    Spalten-Menue-Weg schickt sie nicht mit, der Einstellungs-Dialog schon.
    'music_default_id' ist das Gegenstueck fuer Apple-Music-Playlisten
    (column_view_music_default, siehe ensure_apple_music_column_view()),
    gleiche None-laesst-stehen-Logik."""
    clean = validate_column_views(items)
    known = {v["id"] for v in clean}
    stored = _read_local()
    assign = {view: target for view, target
              in _as_assign_map(stored.get("column_view_assign")).items()
              if target in known}
    if default_id is None:
        default_id = str(stored.get("column_view_default") or "")
    default_id = str(default_id).strip()
    if default_id and default_id not in known:
        default_id = ""
    if music_default_id is None:
        music_default_id = str(stored.get("column_view_music_default") or "")
    music_default_id = str(music_default_id).strip()
    if music_default_id and music_default_id not in known:
        music_default_id = ""
    _write_local({"column_views": clean, "column_view_assign": assign,
                  "column_view_default": default_id,
                  "column_view_music_default": music_default_id})
    return {"column_views": clean, "column_view_assign": assign,
            "column_view_default": default_id,
            "column_view_music_default": music_default_id}


def save_column_assign(view_id: str, column_view_id: str) -> dict:
    """Ordnet EINER Liste eine gespeicherte Spaltenansicht zu (leere
    column_view_id = zurueck auf die Standard-Spalten). Die Liste selbst wird
    nicht geprueft: es gibt Listen ohne Datenbankzeile (die festen Ansichten
    'all'/'ignored'/... und die Merklisten), und fremde Listen (Music.app,
    Rekordbox) kennt der Server ueberhaupt nur waehrend einer Sitzung."""
    view = str(view_id).strip()
    if not view:
        raise ValueError("Keine Liste angegeben")
    target = str(column_view_id or "").strip()
    known = {v.get("id") for v in cfgmod.load().get("column_views", [])}
    if target and target not in known:
        raise ValueError(f"Unbekannte Spaltenansicht: {target}")
    assign = _as_assign_map(_read_local().get("column_view_assign"))
    if target:
        assign[view] = target
    else:
        assign.pop(view, None)
    _write_local({"column_view_assign": assign})
    return assign


def save_shops(items: list) -> list[dict]:
    """Schreibt die Shop-Liste nach config.local.yaml und laedt neu."""
    clean = validate_shops(items)
    _write_local({"shops": clean})
    return clean


def reset(group_id: str | None = None) -> dict:
    """Eine Gruppe (oder alles) auf die Vorgabe zuruecksetzen."""
    path = cfgmod.local_config_path()
    if not path.exists():
        return cfgmod.reload()
    if group_id is None:
        path.unlink()
        return cfgmod.reload()

    group = next((g for g in GROUPS if g["id"] == group_id), None)
    if group is None and group_id != "shops":
        raise ValueError("Unbekannte Gruppe")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        stored = {}
    if group_id == "shops":
        stored.pop("shops", None)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(stored, fh, allow_unicode=True, sort_keys=True)
        return cfgmod.reload()
    stored_ladders = stored.get("cutoff_classes")
    if isinstance(stored_ladders, list):
        stored_ladders = {"mp3": stored_ladders}   # alte flache Form vor diesem Feature
        stored["cutoff_classes"] = stored_ladders
    for field in group["fields"]:
        stored.pop(field["key"], None)
        for ladder, class_keys in _LADDER_KEYS.items():
            if field["key"] in class_keys and isinstance(stored_ladders, dict):
                stored_ladders.pop(ladder, None)
    if isinstance(stored_ladders, dict) and not stored_ladders:
        stored.pop("cutoff_classes", None)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(stored, fh, allow_unicode=True, sort_keys=True)
    return cfgmod.reload()
