"""
Stem-Auftrag: trennen (Vorschau), freigeben (endgueltig speichern) oder
verwerfen -- die Zustandsmaschine hinter /api/stems.

Zweistufig mit Absicht: separate() schreibt nur Vorschau-Dateien in einen
Cache-Ordner. Erst finalize() legt die Datei neben das Original, uebertraegt
Tags und Cover und gibt den Pfad zurueck; Datenbank, Music.app und Rekordbox
gehoeren dem Aufrufer (server.py).

Es laeuft immer hoechstens ein Auftrag (die Trennung belegt alle Kerne).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config as cfgmod
from . import probe as probe_mod
from . import rename as rename_mod
from . import stems
from . import tags as tags_mod

_TITLE_KEY = {stems.ACAPELLA: "stems_title_acapella",
              stems.INSTRUMENTAL: "stems_title_instrumental"}
_GENRE_KEY = {stems.ACAPELLA: "stems_genre_acapella",
              stems.INSTRUMENTAL: "stems_genre_instrumental"}
_TITLE_DEFAULT = {stems.ACAPELLA: "(Acapella)", stems.INSTRUMENTAL: "(Instrumental)"}
_GENRE_DEFAULT = {stems.ACAPELLA: "Acapella", stems.INSTRUMENTAL: "Instrumental"}

_MP3_KBPS = 320
_SR = 44100


class JobError(RuntimeError):
    """Fehler mit anzeigbarem Text."""


def preview_root() -> Path:
    return cfgmod.base_dir() / "data" / "stems-preview"


def cleanup_all() -> None:
    """Beim Serverstart: Reste frueherer Laeufe (Vorschau, die niemand
    freigegeben hat) wegraeumen."""
    shutil.rmtree(preview_root(), ignore_errors=True)


def output_format(path: str) -> tuple[str, int]:
    """(Format, kbps): verlustfreie Quelle -> AIFF, verlustbehaftete -> MP3.
    Entschieden wird am Codec, nie an der Endung (ALAC liegt in .m4a)."""
    res = probe_mod.probe(path)
    if res.codec_family == "lossless":
        return "aiff", 0
    return "mp3", _MP3_KBPS


def rekordbox_compatible(path: str) -> bool:
    """Ob ein Stem dieses Haupttracks in Rekordbox angelegt werden darf: nur
    wenn Stem und Haupttrack dasselbe Format haben (MP3 -> MP3, verlustfrei
    -> AIFF, siehe output_format()). Aus AAC & Co. entsteht ein MP3-Stem --
    an echtem Material passte dessen kopiertes Beatgrid nicht (Nutzertest
    m4a -> mp3; WAV -> AIFF dagegen schon)."""
    return probe_mod.probe(path).codec_family in ("lossless", "lossy_mp3")


def _titled(title: str, suffix: str, fallback: str) -> str:
    base = (title or "").strip() or fallback
    suffix = (suffix or "").strip()
    return f"{base} {suffix}".strip()


def build_fields(src: str, kind: str, cfg: dict) -> dict:
    """Tag-Felder der Stem-Datei: alles 1:1 vom Haupttrack, ausser Titel
    (Zusatz angehaengt) und Genre (ersetzt)."""
    ident = tags_mod.read_identity(src)
    extra = tags_mod.read_extra(src)
    fields = {
        "artist": ident.get("artist", ""),
        "title": _titled(ident.get("title", ""),
                         cfg.get(_TITLE_KEY[kind]) or _TITLE_DEFAULT[kind],
                         Path(src).stem),
        "album": ident.get("album", ""),
        "album_artist": extra.get("album_artist", ""),
        "composer": extra.get("composer", ""),
        "genre": cfg.get(_GENRE_KEY[kind]) or _GENRE_DEFAULT[kind],
        "year": extra.get("year", 0),
        "bpm": extra.get("bpm", 0.0),
        # Die Tonart so, wie die Datei sie traegt (1:1), nicht neu formatiert.
        "key": extra.get("key_raw") or tags_mod.read_key(src),
        "comment": extra.get("comment", ""),
        "track_no": extra.get("track_no", 0),
        "track_total": extra.get("track_total", 0),
    }
    mood = tags_mod.read_mood(src)
    if mood:
        fields["mood"] = mood
    return fields


def _tag_and_name(dest: str, src: str, kind: str, cfg: dict) -> tuple[str, str]:
    """Schreibt Tags und Cover des Haupttracks in `dest` (eine Stem-Datei)
    und liefert (Dateiname ohne Endung nach rename_pattern, Endung). Gemeinsam
    fuer Speichern und Herunterladen, damit beide dieselbe Datei ergeben."""
    fields = build_fields(src, kind, cfg)
    ext = Path(dest).suffix.lower()
    meta = rename_mod.read_meta(dest)
    meta.update({"artist": fields["artist"], "title": fields["title"],
                 "album": fields["album"], "album_artist": fields["album_artist"],
                 "composer": fields["composer"], "genre": fields["genre"],
                 "year": fields["year"], "bpm": fields["bpm"],
                 "key": fields["key"], "track_no": fields["track_no"]})
    pattern = cfg.get("rename_pattern") or rename_mod.DEFAULT_PATTERN
    values = rename_mod.values_for(meta)
    stem = rename_mod.render_name(values, pattern)
    if not stem or not rename_mod.has_identity(values, pattern):
        stem = f"{Path(src).stem}-{kind}"
    tags_mod.write_tags(dest, fields)
    cover = tags_mod.read_cover(src)
    if cover:
        tags_mod.write_cover(dest, *cover)
    return stem, ext


def _peaks(audio, buckets: int = 1000) -> list[float]:
    """Huellkurve fuer die Hoerprobe: je Bucket der Spitzenwert (mono), auf 1
    normiert -- gleiches Format wie media.waveform_peaks(), aber direkt aus dem
    Ergebnis berechnet (kein Lesen der Vorschau-Datei, kein Cache-Eintrag in
    der Datenbank fuer einen Pfad, der gleich wieder verschwindet)."""
    import numpy as np
    mono = np.abs(audio).max(axis=0)
    n = (mono.size // buckets) * buckets
    if n < buckets:
        return []
    peaks = mono[:n].reshape(buckets, -1).max(axis=1)
    top = float(peaks.max())
    if top > 0:
        peaks = peaks / top
    return [round(float(v), 4) for v in peaks]


class StemJobs:
    """Der eine laufende oder zuletzt fertige Auftrag."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state: dict = {"status": "idle"}

    # -- Zustand -----------------------------------------------------------
    @property
    def state(self) -> dict:
        with self._lock:
            return dict(self._state, previews={
                k: dict(v) for k, v in self._state.get("previews", {}).items()})

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    # -- Start / Abbruch ----------------------------------------------------
    def start(self, path: str, kinds: list[str], cfg: dict, fast: bool = False) -> dict:
        kinds = [k for k in kinds if k in stems.KINDS]
        if not kinds:
            raise JobError("Kein Stem gewählt")
        with self._lock:
            if self._state.get("status") == "running":
                raise JobError("Es läuft bereits eine Trennung")
            old = self._state
        self._drop_previews(old)
        job_id = uuid.uuid4().hex[:12]
        self._cancel.clear()
        with self._lock:
            self._state = {"status": "running", "id": job_id, "path": path,
                           "kinds": kinds, "progress": 0.0, "previews": {},
                           "error": "", "started": time.time()}
        threading.Thread(target=self._run, args=(job_id, path, kinds, dict(cfg), fast),
                         name="stem-job", daemon=True).start()
        return self.state

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, job_id: str, path: str, kinds: list[str], cfg: dict,
             fast: bool = False) -> None:
        try:
            max_s = float(cfg.get("stems_max_minutes") or 15) * 60.0
            fmt, kbps = output_format(path)
            # Erster Lauf im Prozess: CoreML uebersetzt das Modell (einmalig
            # bis ~1 Minute, danach aus dem Cache ~10 s). Eigene Phase, damit
            # die Oberflaeche nicht 0 % anzeigt, als haenge die Trennung.
            if not stems.is_prepared():
                self._set(phase="prepare")
            self._set(backend=stems.prepare(), phase="split")
            if self._cancel.is_set():
                raise stems.Cancelled()
            mix = stems.load(path, max_s)
            # Liegt der Haupttrack analysiert in Rekordbox, wird der Stem auf
            # dessen Zeitachse geschrieben (siehe stems.write_audio()).
            from . import rekordbox as rekordbox_mod
            lead = rekordbox_mod.playback_lead(path, mix, _SR)
            results = stems.separate(
                path, kinds, max_s,
                progress=lambda f: self._set(progress=round(0.95 * f, 3)),
                cancel=self._cancel.is_set, fast=fast, mix=mix)
            folder = preview_root() / job_id
            folder.mkdir(parents=True, exist_ok=True)
            previews = {}
            ext = ".aiff" if fmt == "aiff" else ".mp3"
            for kind, audio in results.items():
                dest = str(folder / f"{kind}{ext}")
                stems.write_audio(audio, dest, fmt, kbps or _MP3_KBPS, lead)
                previews[kind] = {"path": dest, "fmt": fmt, "peaks": _peaks(audio),
                                  "duration": round(audio.shape[-1] / _SR, 2)}
            self._set(status="ready", progress=1.0, previews=previews)
        except stems.Cancelled:
            self._set(status="cancelled")
        except stems.TooLongError:
            self._set(status="error", error="too_long")
        except Exception as exc:                       # noqa: BLE001
            self._set(status="error", error=str(exc)[:300] or exc.__class__.__name__)

    # -- Aufraeumen ---------------------------------------------------------
    def _drop_previews(self, state: dict, kinds=None) -> list[str]:
        removed = []
        for kind, info in list((state.get("previews") or {}).items()):
            if kinds is not None and kind not in kinds:
                continue
            p = info.get("path")
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
                removed.append(p)
            state["previews"].pop(kind, None)
        jid = state.get("id")
        if jid and not state.get("previews"):
            shutil.rmtree(preview_root() / jid, ignore_errors=True)
        return removed

    def discard(self, job_id: str, kinds=None) -> list[str]:
        """Verwirft Vorschau-Dateien (alle oder nur 'kinds'). Gibt die Pfade
        zurueck, damit der Server ihre Freigabe widerrufen kann."""
        with self._lock:
            if self._state.get("id") != job_id:
                return []
            removed = self._drop_previews(self._state, kinds)
            if not self._state.get("previews") and self._state.get("status") == "ready":
                self._state["status"] = "idle"
        return removed

    # -- Herunterladen ------------------------------------------------------
    def export(self, job_id: str, kind: str, cfg: dict) -> tuple[str, str]:
        """Kopie der Vorschau mit denselben Tags, Cover und demselben Namen
        wie bei finalize(), fuer den Download-Knopf. Die Vorschau bleibt
        (Speichern geht danach weiter). Liefert (Pfad der Kopie, Dateiname);
        die Kopie loescht der Aufrufer nach dem Ausliefern."""
        with self._lock:
            st = self._state
            if st.get("id") != job_id or st.get("status") != "ready":
                raise JobError("Kein fertiger Auftrag")
            info = (st.get("previews") or {}).get(kind)
            src = st.get("path")
        if not info or not os.path.isfile(info["path"]):
            raise JobError("Vorschau existiert nicht mehr")
        if not src or not os.path.isfile(src):
            raise JobError("Originaldatei existiert nicht mehr")
        preview = Path(info["path"])
        copy = preview.with_name(f"download-{uuid.uuid4().hex[:8]}{preview.suffix}")
        shutil.copyfile(preview, copy)
        try:
            stem, ext = _tag_and_name(str(copy), src, kind, cfg)
        except Exception:
            copy.unlink(missing_ok=True)
            raise
        return str(copy), stem + ext

    # -- Freigabe -----------------------------------------------------------
    def finalize(self, job_id: str, kind: str, cfg: dict) -> dict:
        """Legt die Vorschau-Datei endgueltig neben das Original, mit Tags und
        Cover des Haupttracks. Der Name folgt cfg['rename_pattern']; eine
        bestehende Datei wird nie ueberschrieben (Zaehler im Namen).

        Liefert {'path', 'source', 'kind'}. Die Vorschau-Datei wird dabei
        verschoben, nicht kopiert."""
        with self._lock:
            st = self._state
            if st.get("id") != job_id or st.get("status") != "ready":
                raise JobError("Kein fertiger Auftrag")
            info = (st.get("previews") or {}).get(kind)
            src = st.get("path")
        if not info or not os.path.isfile(info["path"]):
            raise JobError("Vorschau existiert nicht mehr")
        if not src or not os.path.isfile(src):
            raise JobError("Originaldatei existiert nicht mehr")

        stem, ext = _tag_and_name(info["path"], src, kind, cfg)
        folder = Path(src).parent
        target = rename_mod._unique_target(folder, stem, ext, Path(info["path"]))
        # Erst getaggt (in der Vorschau-Datei), dann verschoben: so liegt
        # unter dem Zielnamen nie eine Datei ohne Tags.
        shutil.move(info["path"], str(target))
        with self._lock:
            if self._state.get("id") == job_id:
                self._state["previews"].pop(kind, None)
                self._state.setdefault("done", {})[kind] = str(target)
                if not self._state["previews"]:
                    self._state["status"] = "idle"
                    shutil.rmtree(preview_root() / job_id, ignore_errors=True)
        return {"path": str(target), "source": src, "kind": kind}


JOBS = StemJobs()
