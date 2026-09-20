"""
Datei-Walk ueber die Bibliothek.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import config as cfgmod


def iter_files(cfg: dict, roots: list[str] | None = None):
    """Liefert (pfad, groesse, mtime) fuer alle passenden Audiodateien."""
    exts = {e.lower() for e in cfg["extensions"]}
    excluded = {d.lower() for d in cfg["exclude_dirs"]}
    paths = roots if roots else cfg["library_paths"]

    seen: set[str] = set()
    for root in paths:
        base = Path(os.path.expanduser(str(root)))
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix.lower() in exts:
                st = base.stat()
                yield str(base), st.st_size, st.st_mtime
            continue

        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in excluded and not d.startswith(".")
            ]
            for name in filenames:
                if name.startswith("._") or Path(name).suffix.lower() not in exts:
                    continue
                full = os.path.join(dirpath, name)
                if full in seen:
                    continue
                seen.add(full)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                yield full, st.st_size, st.st_mtime


def count_files(cfg: dict, roots: list[str] | None = None) -> int:
    return sum(1 for _ in iter_files(cfg, roots))


# ── Verschobene Dateien wiederfinden ────────────────────────────────────────
# Music.app raeumt seinen Medienordner nach Tags auf: wer die Tags aendert und
# den Track danach dort abspielt, findet ihn anschliessend unter
# Interpret/Album/Titel wieder -- neuer Ordner UND neuer Dateiname. Der Pfad in
# quality.db zeigt dann ins Leere, obwohl die Datei noch da ist.
#
# Wiedererkannt wird ueber vier Merkmale. Keins davon traegt allein:
#   size      haelt eine Verschiebung aus, aber nicht unseren eigenen
#             Tag-Schreibvorgang (db.update_tags() zieht size/mtime bewusst
#             nicht nach, die Zeile ist danach also veraltet) -- und bei
#             Duplikaten kollidiert sie.
#   basename  haelt den Umzug aus, aber nicht das Umbenennen durch Music.app.
#   tags      genau das, wonach Music.app einsortiert -- aber eine zweite
#             Kopie desselben Tracks traegt dieselben.
#   duration  ueberlebt Tag-Aenderung und Umzug unveraendert, ist aber fuer
#             sich genommen viel zu unspezifisch.
# Deshalb: mindestens ZWEI Merkmale muessen stimmen, und der beste Kandidat
# muss eindeutig besser sein als der zweitbeste. Alles andere wird als
# "nicht gefunden" bzw. "mehrdeutig" gemeldet statt geraten -- ein falsch
# verknuepfter Pfad waere schlimmer als gar keiner.
_DURATION_TOLERANCE_S = 1.0


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _score_candidates(missing: list[dict], candidates: list[dict],
                       require_same_ext: bool = False) -> dict:
    """Scoring-Kern von find_moved()/find_moved_among(): 'candidates' sind
    bereits vollstaendig aufgeloeste Dicts mit path/size/artist/title/
    duration_s -- kein Datei-/Tag-Zugriff mehr noetig. Liefert
    {"matches": {alt: neu}, "ambiguous": {alt: [neu, ...]}}.

    require_same_ext  Kandidaten mit abweichender Dateiendung faellen komplett
                       raus, egal wie hoch ihr Score sonst waere -- fuer den
                       Rekordbox-Pfadabgleich (find_moved_among()): eine
                       Formataenderung (z.B. mp3 -> m4a durch Neu-Encoding)
                       ist keine reine Verschiebung, sondern eine andere
                       Datei. Rekordbox' Analyse (Cues/Wellenform) haengt am
                       konkreten Encoding -- ein falscher Format-Relink wuerde
                       sie stillschweigend auf die falsche Datei zeigen
                       lassen. Der bestehende Music.app-Relink (find_moved())
                       bleibt ohne diese Einschraenkung."""
    matches: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    taken: set[str] = set()

    for row in missing:
        old_path = str(row.get("path") or "")
        if not old_path:
            continue
        want_size = int(row.get("size") or 0)
        want_base = os.path.basename(old_path)
        want_artist, want_title = _norm(row.get("artist")), _norm(row.get("title"))
        want_dur = float(row.get("duration_s") or 0.0)
        want_ext = os.path.splitext(old_path)[1].lower()

        scored: list[tuple[int, str]] = []
        for cand in candidates:
            path = cand["path"]
            if path in taken:
                continue                 # schon einer anderen Zeile zugeordnet
            if require_same_ext and os.path.splitext(path)[1].lower() != want_ext:
                continue                 # anderes Dateiformat -- keine Verschiebung
            score = 0
            if want_size and cand["size"] == want_size:
                score += 1
            if os.path.basename(path) == want_base:
                score += 1
            if want_artist and want_title \
                    and _norm(cand["artist"]) == want_artist \
                    and _norm(cand["title"]) == want_title:
                score += 1
            if want_dur > 0 and cand["duration_s"] > 0 \
                    and abs(cand["duration_s"] - want_dur) <= _DURATION_TOLERANCE_S:
                score += 1
            if score >= 2:
                scored.append((score, path))

        if not scored:
            continue
        scored.sort(key=lambda x: (-x[0], x[1]))
        best_score = scored[0][0]
        best = [path for score, path in scored if score == best_score]
        if len(best) == 1:
            matches[old_path] = best[0]
            taken.add(best[0])
        else:
            ambiguous[old_path] = best[:5]

    return {"matches": matches, "ambiguous": ambiguous}


def find_moved(cfg: dict, missing: list[dict], known_paths: set[str],
               roots: list[str] | None = None, max_candidates: int = 50_000) -> dict:
    """Sucht zu Datenbankzeilen, deren Datei nicht mehr am gespeicherten Pfad
    liegt, dieselbe Datei an ihrem neuen Ort -- Kandidatenpool ist ein
    frischer Festplatten-Scan (siehe find_moved_among() fuer den Fall, dass
    der Kandidatenpool schon feststeht, z.B. aus der eigenen files-Tabelle).

    missing      Zeilen als Dicts mit path/size/duration_s/artist/title
    known_paths  alle Pfade, die die Datenbank kennt -- als Kandidat kommt nur
                 in Frage, was auf der Platte liegt und zu keiner Zeile
                 gehoert. Eine Datei mit eigener Zeile ist bereits verknuepft;
                 sie einer zweiten Zeile zuzuschlagen waere falsch.

    Liefert {"matches": {alt: neu}, "ambiguous": {alt: [neu, ...]},
             "checked": Zahl der geprueften Kandidaten, "truncated": bool}.
    """
    from . import tags as tags_mod

    raw: list[tuple[str, int]] = []
    truncated = False
    for path, size, _mtime in iter_files(cfg, roots):
        if path in known_paths:
            continue
        if len(raw) >= max_candidates:
            truncated = True
            break
        raw.append((path, size))

    # Tags und Dauer kosten je Kandidat einen Dateizugriff -- einmal lesen und
    # fuer alle gesuchten Zeilen wiederverwenden.
    identity: dict[str, dict] = {}

    def ident(path: str) -> dict:
        if path not in identity:
            identity[path] = tags_mod.read_identity(path)
        return identity[path]

    candidates = [{"path": p, "size": s, **ident(p)} for p, s in raw]
    result = _score_candidates(missing, candidates)
    return {**result, "checked": len(candidates), "truncated": truncated}


def find_moved_among(missing: list[dict], candidates: list[dict]) -> dict:
    """Wie find_moved(), aber der Kandidatenpool steht schon fest (z.B. aus
    der eigenen files-Tabelle statt einem frischen iter_files()-Walk) -- fuer
    den Rekordbox-Pfadabgleich (server._post_rekordbox_scan()): dort ist der
    Kandidatenpool bewusst unsere eigene DB, kein Festplatten-Scan.

    candidates  Dicts mit path/size/duration_s/artist/title, bereits
                vollstaendig aufgeloest (kein Datei-/Tag-Zugriff hier).

    Verlangt zusaetzlich dieselbe Dateiendung wie beim urspruenglichen Pfad
    (require_same_ext, siehe _score_candidates()) -- eine Formataenderung
    darf in Rekordbox nicht als Verschiebung durchgehen.
    """
    result = _score_candidates(missing, candidates, require_same_ext=True)
    return {**result, "checked": len(candidates), "truncated": False}
