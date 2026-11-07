"""
Auto-Cue-Training in der App (Einstellungen -> Auto Cue -> Training).

Derselbe Ablauf wie `./run.command autocue-train` (run.train_core()), aber mit
den Daten dieser Instanz und im Hintergrund: ein Lauf zur Zeit, Fortschritt je
Phase, Abbrechen ueber ein Event. Das Ergebnis landet immer als neuer Eintrag in
der Modellbibliothek (library.py) -- gelten wird es erst, wenn es gewaehlt ist
(die Oberflaeche bietet das mit dem Ergebnis an). So geht ein fertiges Training
nicht verloren, wenn das Fenster zu ist oder die App neu startet.

Ablage je Lauf: <base_dir>/autocue-training/runs/<zeit>/ (train.log; das Modell
selbst wird nach dem Eintragen dort geloescht). Datensatz und Versatz-Cache
liegen wie beim CLI in <base_dir>/autocue-training/ und werden von Lauf zu Lauf
weiterverwendet -- nur Geaendertes wird neu gerechnet.

Lock-Regel des Servers: nichts hiervon laeuft unter self.lock. Die Vorbereitung
uebernimmt fehlende Grids aus Rekordbox in die Tabelle 'phrases' (wie in der
DJ-Ansicht); die steht nicht im Report, ein _rebuild() ist nicht noetig.
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from app import config as cfgmod

# Export-Worker in der App gedeckelt: der Rechner soll bedienbar bleiben
# (Spektrogramm ~60 MB je 10 Minuten Audio und Worker).
MAX_WORKERS = 4
LOG_LINES = 400


class JobError(RuntimeError):
    pass


def workers() -> int:
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 4) - 2))


class TrainJob:
    """Der eine laufende oder zuletzt fertige Trainingslauf."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state: dict = {"status": "idle"}
        self._log: list[str] = []

    # -- Zustand -----------------------------------------------------------
    def state(self, log_from: int = 0) -> dict:
        with self._lock:
            st = dict(self._state)
            st["log_total"] = len(self._log)
            st["log"] = self._log[max(0, log_from):][-LOG_LINES:]
            return st

    def is_running(self) -> bool:
        with self._lock:
            return self._state.get("status") == "running"

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    # -- Vorab-Pruefung ----------------------------------------------------
    @staticmethod
    def check(cfg: dict, params: dict) -> dict:
        """Nur sammeln (Datenbank, schnell): was aus der Slot-Belegung gelernt wird, wie
        viele Tracks taugen, Warnungen. {"ok": False, "error"} statt Ausnahme -- die
        Oberflaeche zeigt das live. "learn" steht immer drin, auch ohne Playlisten."""
        from app.autocue.training import labels as labels_mod
        from app.autocue.training.run import TrainError, collect
        plan = labels_mod.learn_plan(cfg.get("autocue_slots"))
        learn = {"hot": labels_mod.letters(plan["hot"]), "memory": plan["memory"]}
        if plan["error"]:
            return {"ok": False, "error": plan["error"], "learn": learn, "blocked": True}
        if params.get("training_paths") is None or params.get("check_paths") is None:
            if not params.get("training") or not params.get("check"):
                return {"ok": False, "error": "", "learn": learn}
        lines: list[str] = []
        try:
            train_items, gold_items, _paths, check_name, train_name = collect(
                cfg, params.get("training", ""), params.get("check", ""), None, lines.append,
                label_slots=plan["hot"], memory_ok=bool(plan["memory"]),
                training_paths=params.get("training_paths"), check_paths_given=params.get("check_paths"))
        except (TrainError, ValueError) as exc:
            return {"ok": False, "error": str(exc), "learn": learn}
        warnings = [ln.strip() for ln in lines if "Achtung" in ln]
        n_mem = labels_mod.count_with_memory(train_items)
        if plan["memory"] and n_mem < 10:
            warnings.append(f"Nur {n_mem} Trainingstracks haben Memory Cues – Drop 1, Break 2 und Outro "
                            "aus Memory Cues werden deshalb nicht gelernt, Auto Cue findet sie dann "
                            "über die Strukturanalyse.")
        return {"ok": True, "train": len(train_items), "check": len(gold_items), "memory_tracks": n_mem,
                "training_name": train_name, "check_name": check_name, "learn": learn,
                "warnings": warnings}

    # -- Start / Abbruch ----------------------------------------------------
    def start(self, cfg: dict, params: dict) -> dict:
        from app.autocue.training import boost
        from app.autocue.training import labels as labels_mod
        try:
            boost.lightgbm()
        except boost.MissingLightGBM as exc:
            raise JobError(str(exc)) from None
        params = dict(params)
        # Gelernt wird immer nach der gespeicherten Slot-Belegung -- dieselbe Quelle,
        # nach der Auto Cue die Cues setzt. Eine feste Zuordnung gibt es nur im CLI.
        params.pop("label_slots", None)
        params["slots"] = cfg.get("autocue_slots")
        plan = labels_mod.learn_plan(params["slots"])
        if plan["error"]:
            raise JobError(plan["error"])
        from app.autocue import library
        params["name"] = library.clean_name(params.get("name"))
        with self._lock:
            if self._state.get("status") == "running":
                raise JobError("Es läuft bereits ein Training.")
            self._cancel.clear()
            self._log = []
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._state = {"status": "running", "id": stamp, "phase": "collect", "done": 0, "total": 0,
                           "started": time.time(), "phase_started": time.time(), "eta": None,
                           "name": params["name"], "result": None, "error": ""}
        threading.Thread(target=self._run, args=(stamp, dict(cfg), params),
                         name="autocue-train", daemon=True).start()
        return self.state()

    def cancel(self) -> None:
        self._cancel.set()

    # -- Lauf ---------------------------------------------------------------
    def _logline(self, fh, text: str = "") -> None:
        for line in str(text).split("\n"):
            with self._lock:
                self._log.append(line)
            if fh:
                fh.write(line + "\n")
                fh.flush()

    def _progress(self, phase: str, done: int, total: int) -> None:
        now = time.time()
        with self._lock:
            if self._state.get("phase") != phase:
                self._state.update(phase=phase, phase_started=now)
            started = self._state.get("phase_started") or now
            eta = ((now - started) / done * (total - done)) if done and total and done < total else None
            self._state.update(done=int(done), total=int(total), eta=round(eta) if eta is not None else None)

    def _run(self, stamp: str, cfg: dict, params: dict) -> None:
        from app.autocue import library
        from app.autocue.training.base import Cancelled
        from app.autocue.training.run import TrainError, train_core
        work = cfgmod.base_dir() / "autocue-training"
        run_dir = work / "runs" / stamp
        staging = run_dir / "model"
        fh = None
        try:
            staging.mkdir(parents=True, exist_ok=True)
            fh = open(run_dir / "train.log", "a", encoding="utf-8")
            log = lambda text="": self._logline(fh, text)  # noqa: E731
            log(f"Auto-Cue-Training {stamp} in der App")
            # Ein Training laeuft lange ohne Zutun -- der Mac soll dabei nicht schlafen gehen.
            from app import activity
            with activity.keep_awake("Auto-Cue-Training"):
                out = train_core(cfg, {**params, "workers": workers(), "source": "tracktab",
                                       "instance": str(cfgmod.base_dir())},
                                 staging, work, log, self._progress, self._cancel.is_set)
            model_id = library.add_folder(str(staging), params.get("name") or "", "trained")
            shutil.rmtree(staging, ignore_errors=True)
            log(f"\nIn die Modellbibliothek eingetragen: {model_id}")
            res = out["results"]
            self._set(status="done", phase="package", done=1, total=1, eta=None, finished=time.time(),
                      result={"id": model_id, "new_hits": out["new_hits"], "old_hits": out["old_hits"],
                              "new": res.get("neu"), "active": res.get("aktiv"),
                              "n_tracks": out["manifest"].get("n_tracks"),
                              "gold_n": (out["manifest"].get("gold") or {}).get("n")})
        except Cancelled:
            shutil.rmtree(staging, ignore_errors=True)
            if fh:
                self._logline(fh, "\nAbgebrochen.")
            self._set(status="cancelled", eta=None)
        except (TrainError, JobError, ValueError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            if fh:
                self._logline(fh, f"\nAbbruch: {exc}")
            self._set(status="error", error=str(exc)[:500], eta=None)
        except Exception as exc:                       # noqa: BLE001 -- nie einen Thread still sterben lassen
            shutil.rmtree(staging, ignore_errors=True)
            if fh:
                self._logline(fh, f"\nFehler: {type(exc).__name__}: {exc}")
            self._set(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}", eta=None)
        finally:
            if fh:
                fh.close()


JOB = TrainJob()

