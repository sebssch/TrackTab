"""
Ausgabe: HTML-Report, CSV und M3U-Playlist.

Der HTML-Report ist eine einzelne Datei ohne externe Abhaengigkeiten. Die
Tabelle wird im Browser aus einem eingebetteten Datenarray aufgebaut, damit
auch zehntausend Zeilen fluessig sortier- und filterbar bleiben.
"""
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

from . import __version__
from . import classify as classify_mod
from . import config as cfgmod
from . import genremood as genremood_mod
from . import tags as tags_mod

_FLAGGED = (classify_mod.VERDICT_FAKE, classify_mod.VERDICT_SUSPECT)


def _json_for_script(obj) -> str:
    """
    JSON fuer die Einbettung in ein <script>-Element (siehe _template()).

    json.dumps() escapet '<' nicht: ein '</script>' in einem Tag (Interpret,
    Titel, Album, Kommentar, Dateipfad, Listenname) beendet damit den
    Skriptblock, und der Rest der Zeichenkette landet als HTML im Dokument --
    eine untergeschobene Audiodatei koennte so beliebiges Skript im Origin
    der Oberflaeche ausfuehren, der saemtliche API-Endpunkte offenstehen.
    U+2028/U+2029 sind ausserdem JS-Zeilenumbrueche.

    Die \\uXXXX-Schreibweise ist gueltiges JSON und liefert beim Parsen
    exakt dieselben Zeichen zurueck -- die Daten aendern sich nicht.
    """
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def rows_to_payload(rows, cfg: dict, ignored: set[str] | None = None,
                     corrected: set[str] | None = None,
                     rekordbox: set[str] | None = None,
                     music_added: dict[str, float] | None = None,
                     dup_dismissed: set[str] | None = None,
                     genremood: dict[str, dict] | None = None) -> list:
    """
    Kompakte Struktur fuer das eingebettete JSON.

    Dateiname, Ordner und file://-URLs werden im Browser aus dem Pfad
    abgeleitet statt mitgeschickt — bei zehntausend Zeilen halbiert das die
    Groesse der Seite. Spektrum und Begruendung tragen nur auffaellige
    Dateien; bei OK ergibt sich der Text aus dem Messwert. Merkliste-
    Mitgliedschaft steht NICHT hier -- die kommt clientseitig aus
    META.playlists/META.playlistItems (siehe app.js::merkPlaylists()).
    """
    ignored = ignored or set()
    corrected = corrected or set()
    rekordbox = rekordbox or set()
    music_added = music_added or {}
    dup_dismissed = dup_dismissed or set()
    genremood = genremood or {}
    # Genre-Vorschlaege unter dieser Schwelle (Prozent) bleiben weg -- bei 400
    # Stilen haette sonst fast jede Zeile einen langen Schwanz an 1-%-Treffern.
    min_p = max(0.0, float(cfg.get("genremood_min_percent", 5) or 0)) / 100.0
    out = []
    for idx, r in enumerate(rows):
        flagged = r["verdict"] != classify_mod.VERDICT_OK
        reasons = []
        spectrum = []
        try:
            tag_issues = json.loads(r["tag_issues"] or "[]")
        except (TypeError, ValueError):
            tag_issues = []
        if flagged:
            try:
                reasons = json.loads(r["reasons"] or "[]")
            except (TypeError, ValueError):
                reasons = []
            try:
                spectrum = [p[1] for p in json.loads(r["spectrum"] or "[]")]
            except (TypeError, ValueError):
                spectrum = []
        gm = genremood.get(r["path"]) or {}
        out.append({
            "i": idx,
            "p": r["path"],
            "a": r["artist"] or "",
            "t": r["title"] or "",
            "al": r["album"] or "",
            "aa": r["album_artist"] or "",
            "cp": r["composer"] or "",
            "ge": r["genre"] or "",
            "yr": int(r["year"] or 0),
            "bp": round(r["bpm"] or 0.0, 1),
            "ky": r["key"] or "",
            "kr": r["key_raw"] or "",
            "cm": r["comment"] or "",
            "tn": int(r["track_no"] or 0),
            "tt": int(r["track_total"] or 0),
            "cv": int(r["has_cover"] or 0),
            "v": r["verdict"] or classify_mod.VERDICT_UNKNOWN,
            "fam": r["codec_family"] or "lossy_mp3",
            "cd": r["codec"] or "",
            "kb": r["declared_kbps"] or 0,
            "mk": r["measured_kbps"] or 0,
            "co": round((r["cutoff_hz"] or 0) / 1000.0, 2),
            "st": round(r["steepness_db"] or 0.0, 1),
            "bw": int(r["is_brickwall"] or 0),
            "cf": round(r["confidence"] or 0.0, 2),
            "du": int(r["duration_s"] or 0),
            "sr": r["sample_rate"] or 44100,
            "mo": r["bitrate_mode"] or "",
            "en": r["encoder"] or "",
            "lp": round((r["lame_lowpass_hz"] or 0) / 1000.0, 1),
            "lu": round(r["integrated_lufs"] or 0.0, 1),
            "tp": round(r["true_peak_dbtp"] or 0.0, 1),
            "lra": round(r["lra_lu"] or 0.0, 1),
            "sz": int(r["size"] or 0),
            "hs": r["file_hash"] or "",
            "rs": reasons,
            "sp": spectrum,
            "ti": tag_issues,
            "ig": 1 if r["path"] in ignored else 0,
            "mc": 1 if r["path"] in corrected else 0,
            "dd": 1 if r["path"] in dup_dismissed else 0,
            "rb": 1 if r["path"] in rekordbox else 0,
            "im": 1 if r["path"] in music_added else 0,
            "da": int(music_added.get(r["path"]) or 0),
            # Genre-Vorschlaege [[Stil, Prozent], ...] (hoechstens 5),
            # Energie 1..10 (0 = nicht analysiert), Stimmungen {Name: 0..1}.
            "gs": [[g, round(p * 100)] for g, p in (gm.get("g") or [])[:5]
                   if p >= min_p],
            "eg": round(gm.get("e") or 0.0, 1),
            "mm": {k: round(v, 2) for k, v in (gm.get("m") or {}).items()},
            # Geloeschte Dateien bleiben in der Datenbank, bis aufgeraeumt wird.
            # Die Oberflaeche soll sie als solche zeigen statt still zu scheitern.
            "gone": 0 if os.path.isfile(r["path"]) else 1,
        })
    return out


def build_csv(rows, cfg: dict, hidden: set[str] | None = None) -> Path:
    path = cfgmod.resolve(cfg["csv_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([
            "Verdikt", "Konfidenz", "Codec", "Codec-Familie",
            "Deklariert kbps", "Modus", "Klasse laut Spektrum",
            "Cutoff kHz", "Flanke dB", "Brickwall", "LAME-Lowpass kHz", "Encoder",
            "Lautheit LUFS", "True Peak dBTP", "LRA LU",
            "Dauer s", "Größe MB", "Artist", "Titel", "Album", "Pfad", "Begründung",
        ])
        for r in (r for r in rows if r["path"] not in (hidden or set())):
            try:
                reasons = "; ".join(json.loads(r["reasons"] or "[]"))
            except (TypeError, ValueError):
                reasons = ""
            w.writerow([
                r["verdict"], f"{r['confidence'] or 0:.2f}",
                r["codec"] or "", r["codec_family"] or "lossy_mp3",
                r["declared_kbps"],
                r["bitrate_mode"] or "", r["measured_kbps"],
                f"{(r['cutoff_hz'] or 0)/1000:.2f}".replace(".", ","),
                f"{r['steepness_db'] or 0:.1f}".replace(".", ","),
                "ja" if r["is_brickwall"] else "nein",
                f"{(r['lame_lowpass_hz'] or 0)/1000:.1f}".replace(".", ",") if r["lame_lowpass_hz"] else "",
                r["encoder"] or "",
                f"{r['integrated_lufs']:.1f}".replace(".", ",") if r["integrated_lufs"] else "",
                f"{r['true_peak_dbtp']:.1f}".replace(".", ",") if r["integrated_lufs"] else "",
                f"{r['lra_lu']:.1f}".replace(".", ",") if r["integrated_lufs"] else "",
                int(r["duration_s"] or 0),
                f"{(r['size'] or 0)/1048576:.1f}".replace(".", ","),
                r["artist"] or "", r["title"] or "", r["album"] or "",
                r["path"], reasons,
            ])
    return path


def build_m3u(rows, cfg: dict, hidden: set[str] | None = None) -> Path:
    path = cfgmod.resolve(cfg["m3u_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    hidden = hidden or set()
    flagged = [r for r in rows
               if r["verdict"] in _FLAGGED and r["path"] not in hidden]
    flagged.sort(key=lambda r: (r["cutoff_hz"] or 0))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        fh.write("# Auffällige Dateien aus TrackTab — "
                 "zum Gegenhoeren, nicht zum blinden Loeschen.\n")
        for r in flagged:
            label = " — ".join(x for x in (r["artist"], r["title"]) if x) or Path(r["path"]).stem
            fh.write(f"#EXTINF:{int(r['duration_s'] or 0)},{label}\n{r['path']}\n")
    return path


def build_html(rows, cfg: dict, ignored: set[str] | None = None,
               corrected: set[str] | None = None,
               rekordbox: set[str] | None = None,
               music_added: dict[str, float] | None = None,
               playlists: list[dict] | None = None,
               playlist_items: dict[str, list[str]] | None = None,
               dup_dismissed: set[str] | None = None,
               genremood: dict[str, dict] | None = None,
               user_tags: dict[str, list[str]] | None = None) -> Path:
    path = cfgmod.resolve(cfg["report_path"])
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = rows_to_payload(rows, cfg, ignored, corrected, rekordbox,
                               music_added, dup_dismissed, genremood)

    # Kennzahlen und Histogramm entstehen im Browser, damit sie sich sofort
    # mitbewegen, wenn Tracks ausgeblendet werden.
    by_kbps = {int(c["kbps"]): float(c["min_khz"]) for c in cfg["cutoff_classes"]["mp3"]}
    meta = {
        "generated": time.strftime("%d.%m.%Y %H:%M"),
        "total": len(payload),
        "thr320": by_kbps.get(320, 19.85),
        "thr192": by_kbps.get(192, 17.8),
        "loudRefLow": float(cfg["loudness_ref_low_lufs"]),
        "loudRefHigh": float(cfg["loudness_ref_high_lufs"]),
        "loudClip": float(cfg["loudness_clip_dbtp"]),
        # Gebacken, damit der Seitenbaum -- und mit ihm die Schnell-Filter-Tabs
        # der als Merkliste markierten Playlisten, siehe app.js::
        # rebuildPlaylistViews() -- schon vor dem ersten
        # /api/playlists-Fetch steht, sonst laesst sich ein zuletzt
        # geoeffneter Knoten beim Neuladen nicht wiederherstellen. Die
        # Aktualisierung danach laeuft live ueber den Endpunkt, nicht ueber
        # einen Rebuild -- siehe server._get_playlists().
        "playlists": playlists or [],
        # Eigene Tags (Pfad -> [Tag]) -- wie playlistItems gebacken, damit die
        # Tag-Liste beim Laden sofort steht; danach live ueber /api/user-tags.
        "userTags": user_tags or {},
        "playlistItems": playlist_items or {},
        # Tonart: Camelot -> [Camelot, Open Key, Notennamen] und
        # Schreibweise -> Camelot (Suche/Eingabe), siehe tags.KEY_TABLE.
        # Eine einzige Quelle fuer Server und Oberflaeche.
        "keyTable": {k: list(v) for k, v in tags_mod.KEY_TABLE.items()},
        "keyAliases": tags_mod.key_aliases(),
        "keyNotation": cfg.get("key_notation", "camelot"),
        # Stilnamen der Genre-Analyse fuer die Genre-Auswahl im Tags-Dialog
        # (hinter den Bibliotheks-Genres), leer ohne Modelle.
        "genreStyles": genremood_mod.style_names(),
    }

    html = (
        _template()
        .replace("/*__META__*/null", _json_for_script(meta))
        .replace("/*__DATA__*/null", _json_for_script(payload))
    )
    path.write_text(html, encoding="utf-8")
    return path


def build_all(conn, cfg: dict) -> dict:
    from . import db as db_mod
    rows = db_mod.fetch(conn)
    ignored = db_mod.ignored_paths(conn)
    corrected = db_mod.corrected_paths(conn)
    rekordbox = db_mod.rekordbox_paths(conn)
    music_added = db_mod.music_added_map(conn)
    playlists = db_mod.playlists_all(conn)
    playlist_items = db_mod.playlist_items_map(conn)
    dup_dismissed = db_mod.dup_dismissed_paths(conn)
    genremood = db_mod.genremood_map(conn)
    user_tags = db_mod.user_tags_map(conn)
    # Ausgeblendete und manuell korrigierte Faelle sind abgehakt und tauchen
    # deshalb in CSV und Playlist nicht mehr auf. Merken (eine als Merkliste
    # markierte Playlist) ist bewusst unabhaengig davon und haelt Tracks
    # NICHT aus dem Export fern -- anders als das fruehere 'solved'.
    # dup_dismissed ist aus demselben Grund aussen vor: eine bestaetigte
    # Nicht-Duplikat-Zeile ist kein abgehakter Befund, sie soll in CSV/M3U
    # normal weiter auftauchen.
    hidden = ignored | corrected
    return {
        "HTML": build_html(rows, cfg, ignored, corrected, rekordbox,
                           music_added, playlists, playlist_items, dup_dismissed,
                           genremood, user_tags),
        "CSV": build_csv(rows, cfg, hidden),
        "M3U": build_m3u(rows, cfg, hidden),
    }


# Aus diesen Dateien backt _template() den Report zusammen.
_UI_FILES = ("index.html", "app.css", "app.js", "generator.js", "i18n/de.js", "i18n/en.js",
             "lucide-icons.js")


def is_stale(cfg: dict) -> bool:
    """
    Ob der gebackene Report aelter ist als die Oberflaeche, aus der er kommt.

    Vor allem fuer die gepackte App wichtig: dort gibt es kein 'report' zum
    Nachschieben. Ohne diese Pruefung zeigt eine frisch gebaute App weiter
    die Oberflaeche vom letzten Rebuild -- neue Knoepfe fehlen dann einfach,
    ohne dass irgendwo ein Fehler auftaucht.
    """
    report = cfgmod.resolve(cfg["report_path"])
    try:
        baked = report.stat().st_mtime
        with open(report, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return True                        # gar keiner da -> auf jeden Fall bauen
    # Andere Version (auch eine reine Versionsaenderung ohne neue
    # Oberflaechen-Dateien): die Nummer im Footer ist eingebacken und waere
    # sonst veraltet. Reports ohne Marke stammen aus einer Version vor 1.9.0.
    if _version_marker() not in head:
        return True
    ui = cfgmod.webui_dir()
    for name in _UI_FILES:
        try:
            if (ui / name).stat().st_mtime > baked:
                return True
        except OSError:
            continue
    return False


def _template() -> str:
    """
    Baut die Seite aus den Quelldateien in app/webui zusammen.

    CSS und JS werden eingebettet statt verlinkt: der Report bleibt damit eine
    einzige Datei, die sich auch ohne laufenden Server oeffnen und weitergeben
    laesst.
    """
    ui = cfgmod.webui_dir()
    html = (ui / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!--__CSS__-->", (ui / "app.css").read_text(encoding="utf-8"))
    # i18n und das Lucide-Icon-Lexikon VOR app.js einbetten -- app.js liest
    # I18N_DE/I18N_EN und LUCIDE_ICONS beim Laden aus, muessen also schon
    # definiert sein.
    i18n_de = (ui / "i18n" / "de.js").read_text(encoding="utf-8")
    i18n_en = (ui / "i18n" / "en.js").read_text(encoding="utf-8")
    lucide_js = (ui / "lucide-icons.js").read_text(encoding="utf-8")
    app_js = (ui / "app.js").read_text(encoding="utf-8")
    # Der Playlist-Generator steht in einer eigenen Datei, laeuft aber im
    # selben <script> NACH app.js: er nutzt dessen Helfer (t(), keyBubble(),
    # playlistApi(), ...), und app.js ruft seine Funktionen erst bei Bedarf
    # auf (Funktionsdeklarationen sind im ganzen Skript bekannt).
    gen_js = (ui / "generator.js").read_text(encoding="utf-8")
    html = html.replace("<!--__JS__-->", i18n_de + "\n" + i18n_en + "\n" + lucide_js + "\n"
                        + app_js + "\n" + gen_js)
    html = html.replace("<!--__VERSION__-->", f"v{__version__}")
    # Versionsmarke direkt hinter <head> -- is_stale() liest nur den Anfang
    # der Datei und erkennt daran einen Report aus einer anderen Version.
    html = html.replace("<head>", f"<head>\n{_version_marker()}", 1)
    return html


def _version_marker() -> str:
    return f'<meta name="tracktab-version" content="{__version__}">'
