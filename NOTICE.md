# TrackTab — Urheberrecht und Lizenz

Copyright (C) 2026 Sebastian Gabler (sgt.works)

TrackTab ist freie Software: Du kannst sie unter den Bedingungen der **GNU General Public License, Version 3, oder (nach deiner Wahl) jeder späteren Version** weitergeben und/oder verändern, wie von der Free Software Foundation veröffentlicht. Den vollständigen Text findest du in [LICENSE](LICENSE).

TrackTab wird in der Hoffnung veröffentlicht, nützlich zu sein, aber **ohne jede Gewährleistung** — auch ohne die implizite Gewährleistung der Marktreife oder der Eignung für einen bestimmten Zweck. Details stehen in der GNU General Public License.

(SPDX: `GPL-3.0-or-later`)

## Geltungsbereich

- Die GPL gilt für den von Sebastian Gabler geschriebenen Quellcode von TrackTab (Verzeichnisse `app/`, `build_assets/` und die Skripte im Projektstamm) und die Dokumentation.
- Software und Modelle Dritter, die TrackTab nutzt, herunterlädt oder in einer fertigen App (`.dmg`) mitliefert, stehen unter ihren **eigenen** Lizenzen: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md); die Lizenztexte liegen in [licenses/](licenses/).
- Die Modelle für Stem-Trennung (Demucs) und für Genre/Stimmung/Energie (Essentia) sind **nicht** Teil dieser Lizenz. Sie stehen laut Quelle unter Creative-Commons-Lizenzen mit dem Zusatz „NonCommercial“ und dürfen nur nicht-kommerziell genutzt werden. Sie liegen nicht in diesem Repository. Das Demucs-Modell wird nicht mit der App ausgeliefert, sondern vom Nutzer in den Einstellungen geladen; die Essentia-Modelle lädt `fetch_models.sh` und können als eigenständige Daten neben dem Programm in der `.dmg` liegen.
- Der Quellcode der in der `.dmg` enthaltenen GPL-/LGPL-Komponenten (FFmpeg, x264, x265, LAME, mpg123, mutagen) liegt am jeweiligen GitHub-Release in `TrackTab-<Version>-third-party-sources.zip` (siehe `package_sources.sh`).
- Namen und Marken Dritter (Rekordbox, Mixed In Key, Apple Music, Music, Serato u. a.) gehören ihren Inhabern. TrackTab ist nicht mit ihnen verbunden und wird von ihnen weder unterstützt noch empfohlen.

## Frühere Versionen

Bis einschließlich Version 1.9.x stand TrackTab unter der MIT-Lizenz. Wer eine solche Version erhalten hat, darf sie weiter unter den MIT-Bedingungen nutzen. Ab der Version, die diese Datei enthält, gilt GPL-3.0-oder-später.
