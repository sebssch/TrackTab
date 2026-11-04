"""
Auto Cue: Nachbau gegen das Original pruefen. Nur lesend (Rekordbox' master.db
wird nur gelesen, Ergebnisse landen im angegebenen Ordner).

Zwei Laeufe, weil beide Projekte ein Paket namens `app` haben:

1. Referenz im .venv des Autocue-Projekts -- Originalkette (librosa, LightGBM,
   Rekordbox-Grid) auf den Tracks aus tools/eval/eval_set.json:

     <autocue>/.venv/bin/python tools/autocue_parity.py reference <autocue> <ordner> [--phrases]

   Dazu je Spektralfunktion librosa gegen app/autocue/dsp.py auf demselben Audio.

2. Vergleich im .venv von TrackTab:

     .venv/bin/python tools/autocue_parity.py compare <autocue> <ordner> [--phrases]

   a) Merkmale je Takt (ffmpeg-Audio, gleiches Grid) gegen die Referenz
   b) Cues der portierten Kette mit Rekordbox-Grid gegen die Cues des Originals
   c) Cues mit TrackTabs EIGENER Grid-/Phrasenmessung gegen die Hand-Cues
      (ground_truth in eval_set.json), Treffer = hoechstens 2 Schlaege daneben
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

import numpy as np

TRACKTAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _eval_set(autocue: str) -> list[dict]:
    with open(os.path.join(autocue, "tools", "eval", "eval_set.json")) as fh:
        return json.load(fh)["tracks"]


# ----------------------------------------------------------------------------
# 1. Referenz (Autocue-.venv)
# ----------------------------------------------------------------------------

def _load_tracktab_dsp():
    spec = importlib.util.spec_from_file_location("tt_dsp", os.path.join(TRACKTAB, "app", "autocue", "dsp.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dsp_check(y: np.ndarray, sr: int, dsp) -> dict:
    import librosa

    out = {}
    S_ref = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    S = dsp.stft_magnitude(y)
    out["stft"] = float(np.abs(S - S_ref).max() / max(S_ref.max(), 1e-9))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    pairs = {
        "centroid": (librosa.feature.spectral_centroid(S=S_ref, sr=sr)[0], dsp.spectral_centroid(S_ref, freqs)),
        "rolloff": (librosa.feature.spectral_rolloff(S=S_ref, sr=sr)[0], dsp.spectral_rolloff(S_ref, freqs)),
        "flatness": (librosa.feature.spectral_flatness(S=S_ref)[0], dsp.spectral_flatness(S_ref)),
        "onset": (librosa.onset.onset_strength(S=librosa.power_to_db(S_ref ** 2), sr=sr),
                  dsp.onset_strength_db_power(S_ref)),
        "mel_db": (librosa.power_to_db(librosa.feature.melspectrogram(S=S_ref ** 2, sr=sr, n_mels=64)),
                   dsp.power_to_db(dsp.mel_power(S_ref, sr, 64))),
        "chroma": (librosa.feature.chroma_stft(S=S_ref, sr=sr), dsp.chroma_stft(S_ref, sr)),
        "flux": (np.concatenate([[0.0], np.maximum(0, np.diff(20 * np.log10(np.maximum(S_ref, 1e-9)), axis=1)).mean(axis=0)]),
                 dsp.db_flux(S_ref, 1e-9)),
    }
    for name, (ref, mine) in pairs.items():
        scale = max(float(np.abs(ref).max()), 1e-9)
        out[name] = float(np.abs(np.asarray(ref, float) - np.asarray(mine, float)).max() / scale)
    out["tuning"] = [float(librosa.estimate_tuning(S=S_ref, sr=sr, bins_per_octave=12)),
                     float(dsp.estimate_tuning(S_ref, sr))]
    return out


def reference(autocue: str, folder: str, use_phrases: bool) -> None:
    sys.path.insert(0, autocue)
    os.chdir(autocue)
    import logging
    logging.disable(logging.WARNING)
    import librosa
    from app.analysis.pipeline import analyse_track
    from app.beatgrid import open_db

    dsp = _load_tracktab_dsp()
    os.makedirs(folder, exist_ok=True)
    db = open_db()
    results = []
    try:
        for i, track in enumerate(_eval_set(autocue)):
            content = db.get_content(ID=track["content_id"])
            if content is None:
                continue
            t0 = time.time()
            res = analyse_track(content, use_phrases=use_phrases)
            entry = {"content_id": track["content_id"], "path": content.FolderPath, "ok": res.ok,
                     "skip": res.skip_reason, "seconds": round(time.time() - t0, 1)}
            if res.ok:
                g = res.lattice.grid
                entry["beats"] = [[float(t), int(n), float(b)] for t, n, b in zip(g.times, g.beat_numbers, g.bpms)]
                entry["duration"] = float(content.Length or 0)
                entry["cues"] = [[round(c.time_sec, 4), int(c.kind), c.name] for c in res.cues]
                entry["anchors"] = {k: [a.bar, round(a.time_sec, 4), round(a.confidence, 4)]
                                    for k, a in res.anchors.anchors.items()}
                np.savez_compressed(os.path.join(folder, f"{track['content_id']}.npz"),
                                    matrix=res.features.matrix, names=np.array(res.features.names))
            if i < 5 and res.ok:
                y, sr = librosa.load(content.FolderPath, sr=22050, mono=True)
                entry["dsp"] = _dsp_check(y, sr, dsp)
            results.append(entry)
            print(f"{i + 1:3d} {entry['seconds']:5.1f}s  {'ok ' if res.ok else 'SKIP'}  {track['title'][:50]}"
                  + (f"  dsp {entry['dsp']}" if "dsp" in entry else ""), flush=True)
    finally:
        db.close()
    with open(os.path.join(folder, "reference.json"), "w") as fh:
        json.dump({"use_phrases": use_phrases, "tracks": results}, fh)


# ----------------------------------------------------------------------------
# 2. Vergleich (TrackTab-.venv)
# ----------------------------------------------------------------------------

def _match(ref: list, mine: list, tol: float = 0.05) -> tuple[int, int, int]:
    """(gleich, nur Original, nur Nachbau): gleiche Art und Zeit +- tol."""
    left = list(mine)
    same = 0
    for t, kind, _name in ref:
        hit = next((m for m in left if m[1] == kind and abs(m[0] - t) <= tol), None)
        if hit is not None:
            left.remove(hit)
            same += 1
    return same, len(ref) - same, len(left)


def _truth_hits(truth: dict, cues: list, beat_s: float) -> dict:
    """Je Hot Cue A/B/C: liegt der gesetzte Cue hoechstens 2 Schlaege neben dem Hand-Cue?"""
    out = {}
    for kind, label in ((1, "A"), (2, "B"), (3, "C")):
        hand = truth.get(str(kind)) or []
        if not hand:
            continue
        mine = [c[0] for c in cues if c[1] == kind]
        if not mine:
            out[label] = "fehlt"
            continue
        diff = abs(mine[0] - hand[0]["time_ms"] / 1000.0) / beat_s
        out[label] = "ok" if diff <= 2.0 else f"{diff:.0f} Schlaege"
    hand_mem = [m["time_ms"] / 1000.0 for m in truth.get("0") or []]
    mine_mem = [c[0] for c in cues if c[1] == 0]
    on = sum(1 for t in mine_mem if any(abs(t - h) / beat_s <= 2.0 for h in hand_mem))
    out["memory"] = f"{on}/{len(mine_mem)} auf Hand-Memory"
    return out


def compare(autocue: str, folder: str, use_phrases: bool, with_own_grid: bool) -> None:
    sys.path.insert(0, TRACKTAB)
    from app import config as cfgmod
    from app import rekordbox as rekordbox_mod
    from app import server
    from app import wavedetail as wavedetail_mod
    import app.autocue as autocue_mod
    from app.autocue import learned
    from app.autocue.features import extract_bar_features
    from app.autocue.lattice import build_lattice, grid_from_beats
    from app.autocue.sources import load_sources

    with open(os.path.join(folder, "reference.json")) as fh:
        ref = json.load(fh)
    truth = {t["content_id"]: t for t in _eval_set(autocue)}
    cfg = dict(cfgmod.load())
    cfg["autocue_use_phrases"] = use_phrases
    cfg.pop("autocue_slots", None)
    learned.MODEL_DIR = autocue_mod.model_dir()

    feat_diff: dict[str, list] = {}
    cue_same = cue_ref_only = cue_mine_only = 0
    tally: dict[str, dict] = {}
    for entry in ref["tracks"]:
        if not entry.get("ok"):
            continue
        path = entry["path"]
        beats = [[t * 1000.0, n, b] for t, n, b in entry["beats"]]
        state = rekordbox_mod.track_state(path)
        phrases = state.get("phrases") or []

        # a) Merkmale
        lattice = build_lattice(grid_from_beats(beats), entry["duration"] or None, "streng", enforce=False)
        feats = extract_bar_features(lattice, load_sources(path))
        with np.load(os.path.join(folder, f"{entry['content_id']}.npz")) as z:
            rm, rn = z["matrix"], [str(n) for n in z["names"]]
        for j, name in enumerate(rn):
            if name in feats.names and rm.shape[0] == feats.n_bars:
                mine = feats[name]
                feat_diff.setdefault(name, []).append(float(np.mean(np.abs(mine - rm[:, j]))))

        # b) Cues mit gleichem Grid
        res = autocue_mod.analyse(path, beats, phrases, entry["duration"], cfg)
        mine = [[c["position_s"], c["slot"] or 0, c["name"]] for c in res["cues"]]
        s, r_only, m_only = _match(entry["cues"], mine)
        cue_same, cue_ref_only, cue_mine_only = cue_same + s, cue_ref_only + r_only, cue_mine_only + m_only
        line = f"{s:2d} gleich {r_only:2d} nur Original {m_only:2d} nur Nachbau"

        # c) eigenes Grid gegen Hand-Cues
        if with_own_grid:
            from app import phrases as phrases_mod
            measured = phrases_mod.analyse(path, cfg.get("phrase_grid_mode") or "auto")
            if measured:
                own_beats = server._dj_beats_with_tempo(measured["beats"], [], 0.0, float(measured.get("bpm") or 0))
                own = autocue_mod.analyse(path, own_beats, measured["phrases"], entry["duration"], cfg)
                lead = wavedetail_mod.lead(path, os.path.getmtime(path),
                                           lambda mono, sr: rekordbox_mod.playback_lead(path, mono, sr))
                own_cues = [[c["position_s"] + lead, c["slot"] or 0, c["name"]] for c in own["cues"]]
                beat_s = 60.0 / float(np.median([b[2] for b in entry["beats"]]))
                orig_hits = _truth_hits(truth[entry["content_id"]]["ground_truth"], entry["cues"], beat_s)
                own_hits = _truth_hits(truth[entry["content_id"]]["ground_truth"], own_cues, beat_s)
                for key, val in own_hits.items():
                    tally.setdefault(key, {"orig": 0, "own": 0, "n": 0})
                    if key != "memory":
                        tally[key]["n"] += 1
                        tally[key]["orig"] += orig_hits.get(key) == "ok"
                        tally[key]["own"] += val == "ok"
                line += f" | eigenes Grid {own['status']}: {own_hits}  Original: {orig_hits}"
        print(f"{entry['content_id']}: {line}", flush=True)

    print("\nMerkmale, mittlere Abweichung je Takt (die 12 groessten):")
    worst = sorted(((float(np.mean(v)), k) for k, v in feat_diff.items()), reverse=True)[:12]
    for v, k in worst:
        print(f"  {k:20s} {v:.4f}")
    total = cue_same + cue_ref_only
    print(f"\nCues gleiches Grid: {cue_same}/{total} gleich ({100 * cue_same / max(total, 1):.1f} %), "
          f"{cue_ref_only} nur Original, {cue_mine_only} nur Nachbau")
    for key, t in tally.items():
        if t["n"]:
            print(f"Hot {key} gegen Hand-Cue: Original {t['orig']}/{t['n']}, TrackTab-Grid {t['own']}/{t['n']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 3 or args[0] not in ("reference", "compare"):
        raise SystemExit(__doc__)
    mode, autocue, out = args
    if mode == "reference":
        reference(os.path.abspath(autocue), os.path.abspath(out), "--phrases" in flags)
    else:
        compare(os.path.abspath(autocue), os.path.abspath(out), "--phrases" in flags, "--own-grid" in flags)
