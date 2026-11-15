# Installation

*Deutsche Fassung: [installation.de.md](installation.de.md)*

This guide describes how to set up TrackTab — in three independent variants. You only need one of them; which one suits you depends on whether you want to work with the terminal at all. A general overview of how to use TrackTab after setup is in the [manual](manual.md).

---

## 1. Installation via .dmg file (macOS)

The easiest variant: a ready-packaged app, no terminal needed. The requirement is a Mac with an Apple Silicon processor (M1 or newer) — this variant does not run on an Intel Mac; use section 2 or 3 there.

1. Double-click the `.dmg` file — a window opens automatically with the app and a shortcut “Applications”.
2. Drag `TrackTab.app` onto the “Applications” shortcut.
3. Open `TrackTab.app` in the Applications folder — **once**, right-click it (or two fingers on the trackpad) and choose *Open*, then confirm *Open* again in the dialog. If macOS still shows a security warning, the app must be allowed manually once under **System Settings → Privacy & Security** (button *Open Anyway* next to the notice about TrackTab).

ffmpeg/ffprobe are already included in this variant — unlike section 2, nothing additional has to be installed.

The BPM/key analysis is included in the app (libsonare), as is the genre/mood analysis including models (onnxruntime, Essentia models; requires macOS 14), while the stem separation model (Demucs, ~170 MB) is not included for licensing reasons: it is loaded once in the settings under “Stems”; until then the stem functions are hidden.

---

## 2. Your own app build (compiling from source)

This variant creates a double-clickable app with which you can then use TrackTab entirely without the terminal.

### Build requirements

- macOS 13 or newer
- Python 3.12 or newer
- `ffmpeg`/`ffprobe` in the PATH (via Homebrew: `brew install ffmpeg`)

### Procedure

1. Download or clone the repository.
2. In the terminal, change to the project folder and run the build command:

   ```bash
   ./build_app.sh
   ```

3. This creates `dist/TrackTab.app` — standalone, without Python, a virtual environment or Homebrew having to be installed on the target computer. In addition, the build creates a shortcut `TrackTab` in `/Applications`.
4. Start the app by double-click. It automatically finds a free port, starts its own server in the background and opens the report in the default browser.

The bundle is not signed. On someone else’s Mac, a right-click on the app → *Open* helps at the very first start, instead of simply double-clicking it.

An own build uses a data folder of its own (`TrackTab-Build` instead of `TrackTab` under `~/Library/Application Support/`) and its own port range — even when started via the shortcut in `/Applications`. It therefore runs independently of and at the same time as a version installed via .dmg (see above), with its own, initially empty library.

**Quitting:** via the **Quit** button in the interface, with `Cmd+Q` or by right-clicking the Dock icon → *Quit*. Merely closing the browser window does **not** quit the app — it keeps running in the background (recognisable by the Dock icon) and a scan is not interrupted by it.

After a new version of the source code, simply run `./build_app.sh` again — the app recognises a changed interface automatically and updates itself at the next start.

---

## 3. Running & operating from the terminal

### Requirements

- macOS 13 or newer
- Python 3.12 or newer
- `ffmpeg`/`ffprobe` in the PATH
- Optional: `libsonare` for the BPM/key analysis (comes along automatically via `requirements.txt`; the PyPI wheel only exists for Apple Silicon — on an Intel Mac TrackTab runs without this function)
- Optional: `onnxruntime` and the Essentia models for genre, mood and energy. `onnxruntime` comes along via `requirements.txt` (Apple Silicon only, macOS 14+); the models (~21 MB) are loaded once by `./fetch_models.sh` into `vendor/models/essentia/`; `./build_app.sh` does this itself. Without both, TrackTab runs without this function.
- Optional: the Demucs model for stem separation (acapella/instrumental, ~170 MB, uses the same `onnxruntime`). It is loaded in the settings under “Stems” (stored in `models/demucs/` in the data folder, not in the bundle); an existing `vendor/models/demucs/` continues to be used in development mode. Without the model the stem functions are hidden.
- Auto Cue brings no model with it: every installation trains its own from its own hand-set cues (in the app or with `./run.command autocue-train`, see “Retraining Auto Cue”) or imports one as a ZIP. TrackTab no longer reads a formerly used `vendor/models/autocue/`; if it is still there, it should go, otherwise `build_app.sh` needlessly packs it into the app.
- Optional: the phrase model `vendor/models/phrases/phrases.onnx` for “Beat grid & phrases” (uses the same `onnxruntime`). It comes from GlideMix (GPL) and is not in the repository (`vendor/` is excluded); `./fetch_models.sh` fetches it once from the GlideMix source archive (download about 70 MB, the model itself 1.2 MB), `./build_app.sh` does this itself. If the file is there, it is packed into the app when building. Without the model the function is hidden.
- `osascript` (already included in macOS; used for Trash, Finder integration, external programs and dialogs)

### Setup

1. Download or clone the repository.
2. In the terminal, change to the project folder and start:

   ```bash
   ./run.command
   ```

   On first start, an isolated Python environment of its own (`.venv`) is created automatically and all required Python packages are installed.
3. If `ffmpeg` is missing, `run.command` aborts with a notice. Install it afterwards via Homebrew:

   ```bash
   brew install ffmpeg
   ```

   Then start `./run.command` again.
4. Without further arguments the complete run now happens: the library is analysed, the report is generated and opened in the browser.

### Commands

All commands run via `./run.command <command>`:

| Command | Purpose |
|---|---|
| *(none)* | Complete run: analysis, report, browser |
| `scan` | Analyse the library (incremental — only new/changed files are measured) |
| `scan --limit 200` | Test run over only 200 files |
| `scan --force` | Ignore the cache, re-measure all files |
| `scan --prune` | Remove database entries for files that have since been deleted |
| `scan --covers` | Cover sync with Music.app only, for all known files without a cover, no re-scan of the audio files |
| `scan "/path/to/folder"` | Check only a specific folder |
| `check "/path/file.mp3"` | Check a single file in detail directly in the terminal |
| `report` | Regenerate report files (HTML, CSV, M3U) from the current database state |
| `serve` | Open the web interface |
| `serve --port 9000` | Open the web interface on a different port if 8756 is taken |
| `serve --no-open` | Start the web interface without automatically opening the browser |
| `reclassify` | Re-derive verdicts from changed settings without measuring the files again |
| `stats` | Show a summary of the verdicts |
| `calibrate` | Regression check: creates test files from your own library and checks whether the detection recognises them correctly |
| `backup create` | Create a backup of the analysis data now |
| `backup list` | Show existing automatic backups |
| `backup restore <file>` | Restore analysis data from a backup (the current state is backed up first) |
| `autocue-train` | Retrain Auto Cue (see below) |
| `autocue-eval` | Evaluate Auto Cue models on the review list without training |

Quitting the web interface: `Ctrl+C` in the terminal, or the **Quit** button at the top right of the interface.

### Retraining Auto Cue

In the app this is done under Settings → Auto Cue → **Training** (see [manual](manual.md#auto-cue)). In the development folder, the same procedure exists as a command that can work with the data of any TrackTab instance, including that of the installed app. LightGBM is in `requirements.txt` and needs the OpenMP library from Homebrew:

```bash
brew install libomp
```

Then copy the template and adapt it (your own file is not checked in):

```bash
cp autocue-train.example.yaml autocue-train.yaml
```

It contains the data folder of the TrackTab instance whose tracks and hand-set cues are learned (`installed` for the installed app, `dev` for the development folder or a path), the playlist **Training** (learned from; the more tracks, the better) and the playlist **Review list** (measured against, never learned from). Both are normal TrackTab playlists of this instance and must not share any track. What is learned comes, as in the app, from the slot assignment of the instance; `label_slots` sets a fixed hot-cue assignment for experimenting, `name` the model’s name. Drop 2 and Break 1 must lie as a role on a hot cue. After a confirmation, the new model goes into this instance’s model list and is selected there (the former `targets` entry with the target `vendor` no longer applies, there is no bundled model any more). Then:

```bash
./run.command autocue-train
```

Learning is from the hand-set hot cues (according to the slot assignment) and the memory cues (from the DJ view or from Rekordbox after “Sync Rekordbox” → Load), on TrackTab’s beat grid and phrases. If an own beat grid is missing, the run takes it over from Rekordbox into the instance’s database. Cues in the fade-out at the end of the track do not count.

The first run over about 4,000 tracks takes about an hour; log, model and ZIP of each run are in the development folder under `autocue-training/runs/<time>/`, dataset and intermediate states in `autocue-training/` in the instance’s data folder; both are excluded from git. After that the export only recalculates what changed. At the end, the new and the active model are shown side by side on the review list (for the instance’s first model only the new one); in addition `autocue-modell-<time>.zip` is created, which can be imported in any TrackTab app under Settings → Auto Cue → **Import model …**. It is adopted after a confirmation. `./run.command autocue-train --probe` checks with just a few tracks (from 3) whether everything runs through, and never installs. `./run.command autocue-eval` only evaluates, without training. More switches are shown by `./run.command autocue-train --help`.

For the meaning of the analysis results and an explanation of the web interface, see the [manual](manual.md).
