# Changelog

*Deutsche Fassung: [changelog.de.md](changelog.de.md)*

All notable changes to TrackTab are recorded here.
Format loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) (Added/Changed/Fixed).

## [2.1.0] - 2026-11-15

### Added
- Find & replace: A new icon next to the search help replaces parts of tag values (title, artist, album, album artist, composer, genre, comment or all) in the tracks of the current list or search, e.g. “nervo” → “NERVO”. Several terms separated by commas, no marks = whole word, `*x*` = part of a word, "…" = exact phrase; case and accents are ignored by default. Fields are picked as chips (any number, or all; including own tags, which exist only in TrackTab), options (“Match case”, “Match accents”) sit next to them as switches, and an empty “Replace with” removes the match. A preview grouped by track shows every change before/after to tick or untick, and TrackTab asks before writing. Changes go into the file and the library, Music.app is updated and Rekordbox too (queued while it's running). Every change is logged.
- Replace rules: A search can be saved as a rule and managed under Settings → File names → Replace rules. Every active rule appears under Issues as its own category on its own line (with a quick fix); rules ignored on a track no longer apply to it in Find & replace either, offer suggestions in the tags dialog (for all text fields, also while typing) and can be applied in the single checks via “Apply rules”, optionally followed by automatic renaming. Changing a rule's find or replace text, or deleting the rule, drops the issues ignored on tracks for that rule; switching it on/off, fields and options keep them.
- Moving a file to the Trash also removes the track from the Rekordbox collection when Rekordbox is set up (single, bulk and in the duplicates list). The confirmation says so beforehand. If Rekordbox is open, the removal is queued and carried out via “Sync Rekordbox”.
- Tags dialog, multi-edit: a trash icon at the label clears the field for all selected tracks (also with mixed values). The field is locked until saving and can be released again with the reset arrow. Does not apply to the compilation flag.
- Tags dialog: new button “Copy cover to clipboard” next to “Paste from clipboard”. This lets you paste the cover of one track (even an unsaved one) into another track. If the image came from “Copy”, the original image data is transferred on save (no re-encoding by TrackTab).
- Merge suggestions (genre/album/artist): the difference between the two values is marked character by character (e.g. the differing letter in “Rhytm” vs “Rythm”, the apostrophe in “Who's” vs “Whos”), also in the choice dialog.
- Issues: the filters “Cover differs from the rest of the album” and “Cover missing, other tracks of the album have one” also show the album's tracks without a report, so you can see which cover the others carry. Each filter only shows albums with its own finding: “Cover missing” only albums with at least one track without a cover, “Cover differs” only albums with a differing cover, and there only the tracks that have a cover.
- Tags dialog: Cmd + left/right arrow pages to the previous or next track.
- Issues “Cover differs” / “Cover missing”: the eye ignores only this track, the album stays in the list until every track is fixed or ignored. A second button (album icon) ignores the whole album, i.e. all tracks that currently have this report (only this one: ignoring “Cover missing” leaves “Cover differs” of the same album in place); also on tracks without a report of their own (“Cover fits”). If a track with a report is added later, the album shows up again. Under “Ignored” ignoring can be undone per track or for the whole album. Other issues are not affected.
- Tag changes are passed on to Music.app: saving in the tags dialog (multi-edit included) and quick fixes also set title, artist, album, album artist, composer, genre, year, BPM (whole numbers only), comment, track number and the compilation flag in the Music.app library. All tracks of one save go to Music.app in a single call. If Music.app moves the files into a new folder while doing so, TrackTab follows the paths (in Rekordbox as well). A notice reports tracks that could not be found.
- Quick Fix preview: Quick Fix for several tracks (✨ on an issue bubble, “Fix issues” in the bulk bar) first shows every change per track with before and after, as in Find & replace. Single changes or whole tracks can be deselected. The preview replaces the previous confirmation.
- “Sync Music.app” has two switches for cleaning up the Music.app library: “Remove entries without a file” (shown with an exclamation mark in Music.app) and “Remove duplicate entries” (several entries for the same file; one is kept, the file stays, entries in playlists the other is missing from are locked). A preview lists everything found before anything is removed.

### Changed
- Quick Fix on a bubble now fixes only that one issue, no longer all safely fixable issues of the affected tracks.
- The issue “Featuring not in the chosen notation” and its settings (Title clean-up: featuring notation) are removed. The notation is now set by a replace rule of your own, e.g. “ft, ft., feat, featuring” → “feat.” in the artist.
- Cover issues (“Cover differs from the rest of the album”, “Same cover on different albums”, sorting by cover) compare the image content instead of the image data. Equal covers now count as equal even when Music.app has re-encoded them on setting (this happened on every cover save for tracks in the Music library). The app re-reads the stored values once in the background on its next start (about 35 s for 10,000 covers); no scan needed.
- “Group by album” combines compilations and multi-disc albums: all artists of a compilation and all discs (“[Disc 1]”, “[Disc 2]”) appear under one header, sorted by disc and track number. Before, every artist of a compilation got a group of its own. The same grouping applies to the filters “Cover differs from the rest of the album” and “Cover missing”, which now show the whole album. Renaming from the group header is only offered for groups with exactly one album name and artist.
- Merge suggestions for artists recognise other separators and orders: “Axwell and Ingrosso”, “Axwell & Ingrosso”, “Ingrosso x Axwell” and “Axwell, Ingrosso” count as equal (separators &, and, und, x, comma). “feat.”, “ft.” and “featuring” are equal among themselves, but not equal to a collaboration.
- Importing single checks into the Music library is much faster: the copy Music.app creates in its media folder is no longer measured a second time from scratch, TrackTab reuses the single check's measurement (same audio content). Measured: 4 tracks about 6 s → 0.2 s. Whatever still has to be measured (e.g. after restarting the app) now runs in parallel instead of one after the other.

### Fixed
- Tag sync with Music.app reported “not found in Music.app — the old tags remain there” although Music.app had taken the new tags, whenever album or artist changed: when its location is queried, Music.app re-reads the changed file and moves it to a new folder right away, while TrackTab was looking at the old path. The row then stayed on the old path (“File missing”). A moved track is now recognised by its file ID and the path is updated. The same applies when removing via the Trash.
- Trash left entries without a file in Music.app when the title had been changed in TrackTab and Music.app had not picked it up: Music.app was only searched by the current title. It is now also searched by file name and by the title last sent to Music.app, and by path as a last resort; if the entry still cannot be found, a notice appears.
- Quick Fix also corrected issues that were ignored on that track (e.g. a trailing space in a title).
- Rekordbox path correction was lost when Rekordbox was open: after Auto-Relocate, a format change and re-sorting by Music.app the pending correction only lived in the browser and was gone after a reload. It is now queued as with renaming and written via “Sync Rekordbox”.
- BPM/key analysis, genre/mood analysis and the quick fix for issues now pass their changes (BPM, comment, cleaned fields) on to Music.app as well. Before, Music.app kept the old state.
- Tag changes for Music.app were lost when TrackTab was quit shortly after saving or Music.app did not respond. They are now sent on quit, and a failed run is repeated up to two times.
- Merging artist or album: if Music.app re-sorts the files in the process, TrackTab follows the new locations right away (in Rekordbox too). Before, the tracks showed as “file missing” until Auto-Relocate ran.
- Start and reload: the list only appeared once the comparison with Rekordbox had finished. After every app start and every change in Rekordbox it re-read the analysis files of all tracks edited in TrackTab (about 8 seconds for roughly 100 tracks). The list now shows immediately, the Rekordbox values (differences, hot cues, tempo of edited grids) follow. The comparison only reads tracks that changed in Rekordbox and keeps its result across a restart.
- “Cover differs from the rest of the album” stayed even though all tracks carried the same cover. When Music.app had moved a file and rewritten it, TrackTab only took over the new path and modification time, not the new cover and tags, and a scan did not re-read them either. Both are now read fresh from the file when relinking. Tracks already affected: select them and use “Re-analyse track”.
- Large imports into the Music library could abort after 2 minutes: the tracks were already in Music.app but not in TrackTab's library. The time limit now grows with the number of files.


## [2.0.0] - 2026-11-01

### Added
- Issues: new issue “Same cover on different albums”. Reports cover images that occur on at least two different albums (almost always embedded wrongly); several tracks of the same album with the same cover do not count. Album as in Music.app: album title + album artist (otherwise artist); compilations with the Compilation flag set count by album title only. Only the deviating albums are reported, not the album with the most tracks for this cover. Two new issues “Cover differs from the rest of the album” and “Cover missing, other tracks of the album have one”: the image carried by most tracks of an album counts as correct and can be copied to the affected tracks via Quick Fix (with a tie, e.g. 1:1, no Quick Fix, you choose the image in the tags dialog). In the tags dialog, also in multi-edit, the compilation flag can be set. Merge suggestions (genre/album/artist): toggle “A–Z | Suggestions | Hidden” in the list header (like Player/DJ/Edit), the suggestions appear in place of the A–Z list; one card per suggestion with the two results as buttons (“Merge into …”, without an intermediate dialog) and Hide. A click on a card first shows the affected tracks (preview, “End preview” on the card), merging only happens with an active preview; hidden suggestions can be shown again. New column “Last modified” (modification time of the file, date and time, sortable) in the column views and the column menu; it is hidden at first in existing views. The Issues column cycles through the occurring issues when you click the heading (most frequent first, display “2/11”): the tracks with the chosen issue come first, after the last one it resets. The Cover column sorts by image, and in the Issues list with the filter “Duplicate cover” equal covers sit next to each other. For this, TrackTab stores a hash of the image data per file (on scan, after every cover write and backfilled once for the existing library).
- DJ view: tabs Beat grid, Phrases, Beatjump/Loop (beat jumps with a label at the top, loops below, instead of separate tabs Beatjumps and Cue/Loop); setting a memory cue with arrows and Auto Cue are in the row of tabs and reachable in every tab. The lock for locking the beat grid is dropped (the analysis lock protects against re-measuring); arrows next to “Set memory cue” jump to the previous or next memory cue.
- DJ view, beat grid: tempo ramps for transitions with rising or falling tempo (e.g. transition edits 100 → 128 BPM). **Ramp start** (last beat at the old tempo) and **Ramp end** (first beat at the new tempo) each set a tempo change, between them the beats go smoothly from the old to the new tempo; number of beats via − / +, individual beats pinned by dragging in the zoom or “Beat here”, the rest redistribute. Beat grid tools in three rows (tempo and tempo change; grid; ramps), in a ramp the tempo field stays with the tempo at the position. Arrows next to “Tempo change” or ⌥← / ⌥→ jump from marker to marker, “Tempo change” on a marker removes it again. The bar count runs through tempo changes and ramps as in Rekordbox (no bar jump that would disturb sync or block Auto Cue); 1.1 recounts the whole grid in both directions. Several ramps per track are possible. The expanded DJ editor keeps its height when switching tabs and into/out of a ramp (that of the tallest view). The ramp suggests its beat count so that the end lands on the 1, ± changes by one beat, next to it it says “End on 1 ✓” or “End on 3”. Waveforms show the initial tempo at tempo changes and the respective tempo at ramp start and end. After “Ramp end”, TrackTab puts the beats of the ramp on the onsets in the audio (“Fit to audio”, also by hand), and old markers with the same tempo after it are dropped. Own anchors (dragged, “Beat here”, inserted; filled diamond) stay fixed during fitting and with − / + and only disappear by hand, fitted ones (outlined diamond) are replaced. “Insert beat” / “Remove beat” insert a single beat at the playback position or take one out without moving the others.
- Beat grid analysis: transition tracks with two tempos in the file name (“100-128 Transition”, “126-80bpm”) are measured in Flex/Auto as initial tempo, ramp or hard change and end tempo; the DJ editor shows the transition as a ramp with pinned beats. On 52 tracks of the library equally good or better than the previous Flex measurement; where Flex clearly fits better, it stays with Flex. Measured tracks only change after re-analysis. Rekordbox update writes every ramp beat with its own tempo. Not yet checked in Rekordbox itself.
- Duplicates redesigned: every group is a card with the differences in the header (“Differences: album, genre …” or “Exactly the same file”), deviating cells are marked yellow, the columns follow the column view. New tabs “Open” and “Not a duplicate” (confirmed groups collapsed, “Check again”). For each copy, “Keep” can be switched on and off (button on the right in the “Open” column, several possible, default = best copy by actual quality – fake/suspicious counts with measured bitrate – and then metadata, missing files never); all buttons are fixed at the right edge, “Rest to Trash” moves all others to the Trash after a confirmation.
- Duplicates: the duration tolerance rises from 1 to 3 seconds (the same song on a single and a compilation is now recognised), artist and title are compared loosely as in openDJ: “feat.” in the title or in the artist field, square instead of round brackets, “- Remix” instead of “(Remix)”, an appended “(Original Mix)”, punctuation, accents and apostrophes as well as “&”/“and”/“x”/“vs” and the order of several artists do not count (on a library of 10,968 tracks 115 instead of 13 groups). The best copy is at the top. Sorting: lossless, higher bitrate, more complete tags, in Rekordbox/Music.app, larger file.
- Issues: new title clean-up. Web addresses and promo leftovers (“www…”, “[Promo]”, “Downloaded from …”), video words (“(Official Video)”, “[HD]”, “(Free Download)”) as well as underscores and double spaces are Quick Fixes. Track number or artist in the title, remix and featuring notation (as well as, off by default, upper/lower case and typographic quotation marks) are only reported, the cleaned value is offered as a suggestion. Each rule can be switched under Settings → File names → Title clean-up; the word lists (promo words, domain endings, terms to delete, remix keywords) can be extended, remix and featuring notation, upper/lower case and quotation marks have a selectable target form, double underscores become “ - ” (adjustable). The tags dialog offers the cleaned value as a suggestion under title, artist and album; the title notation no longer turns “don't” into “Don'T”.
- Tags dialog: changes stay queued per track when paging (Previous/Next) and are there again when paging back. “Save (N)” counts the tracks with changes and writes them all at once, “Cancel” discards all. Changed fields are tinted; an arrow at the label resets individual fields (also cover and own tags) to the original value, e.g. after applying an online suggestion.
- Tags dialog: write-protected files (“Locked” in Finder or without write permission) are detected beforehand. Fields, cover, online search and renaming are then locked, own tags stay editable; in multi mode protected files are skipped. Write errors report the reason instead of “Operation not permitted”.
- Edit: “Remove from playlist” is an icon in the row next to “Add to playlist” instead of in the “…” menu.
- DJ view: if a track is playing in the player bar, switching to DJ expands it; playback stops and the editor stands at the same position. When switching back, the player bar stands at the editor’s position.
- Column selection as an icon at the far right of the search bar (locked in DJ) instead of a bar of its own above the table.
- Search bar: all buttons as tall as the search field; the active view button (Player/DJ/Edit) is no longer bold, the buttons no longer jump when switching.
- Shops: YouTube as a new default shop. All shops show their favicon; TrackTab fetches it once from the site and stores it locally (only hosts from the shop list, only public addresses, no SVG). If it does not load, the previous icon or letter stays. If you have already saved the shop list once, add YouTube by hand.
- New column “Track – Artist” (title and artist in one cell, below it format and bitrate with “Copy path” and “Show in Finder”) as in the DJ view, now selectable in every view via the column selection; the default in the Player.
- Default column views as in Apple Music: “Default – Edit” and “Default – Player” apply to all lists without a column view of their own in the respective view. A list’s own column view applies in Edit and Player. The former global “Default column view” is dropped; its columns are taken over once into “Default – Edit”, the view itself stays saved.
- Player row: no “…” menu any more; in an open playlist (TrackTab, Rekordbox, Music.app) “Remove from playlist” is a button next to “To playlist”.
- The row play button turns into a pause icon while playing; in the Player a click on the running track pauses it instead of restarting it.
- Sidebar tree reworked: TrackTab, Music App and Rekordbox are section headers that can be shown and hidden with a click. Every list carries a coloured square (light grey without colour), folders show open/closed. Creating new items and M3U import are in the “…” menu of TrackTab (the buttons in the header are dropped), the menus are divided into labelled groups. An expanded Music/Rekordbox branch loads its lists by itself, also at start.
- Fixed views (All, Genre, Album, Duplicates …) and the Review lists folder behave like own lists: movable, also into folders, colour changeable; the Review lists folder can also be renamed and takes own playlists. Status lists keep name, icon and status colour.
- New fixed view “Recently added” (all tracks, by “Added” descending).
- Right-click on tracks and lists in the tree opens the same menu as “…”.
- Column width: adapt to the widest entry by double-clicking the heading edge.
- Loading placeholders matching the view (Edit, Player, DJ) for table and tree; reloading and switching the playlist show placeholders instead of an empty area.
- Statistics, Sources and Open file are reachable in all three views; “Open file” and “Scan library” switch to Edit for this.
- Own tooltips in the style of Tippy.js (title bold, explanation below); Settings: short help texts as a ? icon at the label.
- Settings as side navigation with search instead of accordions.
- Searching and sorting in large tables considerably faster (precomputed sort keys, cached search scope per row, measured on 10,822 rows: value suggestions of the search 161 → 8 ms, sorting by file 54 → 32 ms).
- Table: a list first shows 20 instead of 150 tracks, “load more” and infinite scrolling fetch 20 at a time. The action column (“Open”) is only built for rows in and near the window, the rest while scrolling; all row actions run via one shared click handler instead of about 35 per build, and icons exist once in the document instead of being written out in every row. Measured on 10,940 tracks: switching to list “All” 143 → 39 ms, “Album” 230 → 60 ms, a keystroke in the search about 110 → 22 ms; with 150 loaded rows a rebuild costs 59 instead of 110 ms. The tolerant word search runs about four times faster with identical hits (20 test queries together 3.3 → 0.7 s).
- Album, Artist and Genre lists: the bar with all values is no longer rebuilt on every click but only after a change to the data; with long lists the browser only draws the visible letter sections. The merge suggestions are calculated once instead of on every click and considerably faster (measured on 7,043 albums: 6.0 → 0.1 s, on 5,567 artists 3.9 → 0.15 s, same suggestions).
- Waveform: drawing from a shared frame loop, bars in a cached layer, cue markers keep their colour during playback.
- Server: gzip for large responses (report 9.8 → 2.5 MB), release cache for pure delivery endpoints, report rebuild bundled in the background.
- New issue “Key notation differs from the setting” with Quick Fix (aligns the notation in the file). The hint under Settings → Key field has a “Show” link that opens the affected files in the Issues list.
- DJ view, bulk bar: “BPM & key” and “Genre & mood” analyses for selected tracks (the same analyses as in Edit).
- Smart playlists: new rule “Playlist is in / is not in …”, e.g. “all House tracks that are not yet in Friday set”. All own lists can be chosen, also favorite lists, other smart playlists and folders.
- DJ view: columns “#” (order in the playlist, own playlists only) and “Added”, both sortable. Selected tracks can be removed from the open playlist via the bulk bar (with undo, the files stay).
- Auto Cue (taken over from the project rekordbox-autocue-tool): sets hot cues and memory cues in the DJ view for the open track (Cue/Loop and Info, with undo) or for selected tracks (bulk bar). The basis is TrackTab’s beat grid and phrases; if they are missing, those from Rekordbox are taken over, otherwise they are measured first. The cues replace the previous ones and live in TrackTab, they go to Rekordbox with Rekordbox update. Own settings page “Auto Cue” with phrase switch, memory budget, confidence per spot and slot assignment (Hot A–H, Memory 1–10: role, rule or automatic, name, Rekordbox colour). Runs without librosa and LightGBM (own re-implementation, checked against the original: 99 % identical cues with the same grid). Not yet checked in Rekordbox itself.
- Train Auto Cue yourself, directly in the app: Settings → Auto Cue → “Training” learns from the own hand-set cues of a playlist and measures against a second one (review list), with progress per step, remaining time and Cancel; the status is also shown under the title when the settings are closed. What is learned comes from the slot assignment – the same one by which Auto Cue sets: hot cues with a role are learned from the own hot cues, Drop 1, Break 2 and Outro on memory rows from the own memory cues, rules are calculated. Only the last drop (Drop 2) and Break 1 as a hot cue are required; whatever is not cued otherwise, the model does not learn, Auto Cue then looks for it via the structure analysis (outro by rule). Without memory cues training no longer aborts. Distances such as “Break 1 at least 40 bars before Drop 2” are learned from the data by such a training. The same in the development folder via `./run.command autocue-train` (`autocue-train.yaml`, new `label_slots` and `name`). LightGBM is now included in the app for this (only loaded for training); where it cannot be loaded (older macOS), the app offers no training. Cues set by Auto Cue never flow back into training as hand-set cues. A training in the app has so far only run through on a copy of the library and with few tracks.
- Auto Cue training: the playlist selection is rebuilt every time the settings are opened (just-created ones are in it right away) and also contains smart playlists.
- Settings → Auto Cue: slot assignment split into two blocks (hot cues, memory cues), each with its own Save and “Restore default”.
- Auto Cue: confidence per spot as five levels (Very safe, Safe, Normal, Risky, Very risky) instead of a number; saved values are taken over. “Memory cues at most” is dropped, it is fixed at 10 as in Rekordbox.
- Auto Cue: phrases edited by hand count as a fixed indicator – drops, breaks and outro snap to their boundaries if these lie cleanly on a bar 1. The switch “Phrases as a hint” now only concerns automatically measured phrases.
- Auto Cue: the outro is searched up to shortly before the fade-out instead of at most 32 bars after the last drop; if the rest is short, it becomes short or is dropped. An own training learns the usual distance from the own outro cues.
- Manage Auto Cue models: Settings → Auto Cue → “Models” lists any number of own ones (name, date, tracks, result on the review list, learned cue convention). There is no bundled model: every installation trains its own from the own cues or imports one as a ZIP; until then Auto Cue is locked, slot assignment and training stay usable. The selected one applies, the first one is selected automatically; rename, export as ZIP, import ZIP, delete. A former choice “bundled model” is replaced by the newest own one. “Adopt the model’s slot assignment” adjusts the slot assignment to a model’s convention. A formerly loaded own model is taken over into the list once and stays selected if it applied.
- Auto Cue no longer sets cues in the fade-out at the end of the track (reverb/delay remnant, recognised by level, with phrases edited by hand at their boundary); training does not count hand-set cues there.
- Take over beat grid from Rekordbox: if TrackTab’s own beat grid is missing, the one from Rekordbox including phrases is saved as its own (when opened in the DJ editor, before Auto Cue and after “Sync Rekordbox” in the background). An own measurement replaces it later.
- Fixed: after the Rekordbox sync, about three quarters of the tracks reported “Phrases changed in Rekordbox” when opened, although nothing had changed there. The cause was a changed reading of the Up numbers in TrackTab; the stored state is now silently brought to the current reading.
- DJ view, bulk bar: “Add to Rekordbox” for several tracks (already existing ones are skipped) and “Beat grid + phrases” in one pass.
- Settings: Beat grid & phrases are now under DJ view.
- “Add to Rekordbox” in the DJ view writes beat grid, phrases, cues, waveforms and cover directly, Rekordbox does not have to analyse the track first. Checked in Rekordbox and via USB export (AIF, FLAC, M4A, WAV, MP3); Flex tempo not separately yet. With rare MP3 headers it stays with tags and cues.
- DJ view: fixed phrase colours per kind and number, for Rekordbox phrases and the own analysis.
- DJ view: zoom and overall view extend one bar beyond the start and end. The beat grid stays in the track; a beat just before the sound sits at the start, so a cue can be set on the first beat.
- DJ view: bulk actions for selected tracks: Load Rekordbox, Rekordbox update, beat grid analysis and phrase analysis (skipping locked ones).
- “Rekordbox update” locks the track for analysis, in TrackTab (beat grid and phrases) and in Rekordbox. Not yet checked in Rekordbox itself.
- The Rekordbox status (logo with outline) is also in the Rekordbox column of Edit and Player. If there is a deviation, it says below what deviates (beat grid, phrases, cues).
- New issue: tempo change in the beat grid is not in the title, with autofix (e.g. “Title (130 → 150 → 110 BPM)”, all changes in track order). Applies to tracks that were once open in the DJ editor. The usual notations from DJ edits are also recognised (“(128 - 80 BPM)”, “100-128 Transition”, “(DJ Rukus 100 128 Transition)”, “126 To 70 BPM”): if the change is in the title like this, TrackTab reports it as not uniform, and the autofix replaces the note including the word “Transition” with the uniform format instead of appending a second one. In the tags dialog, a button above the title rewrites it likewise, a second click takes it back.
- DJ editor: a phrase clicked with the mouse can again be deleted in every view (also beat grid) with Del/⌫; the highlight that follows playback is not deleted by the key.
- Search: switch `/TempoChange` (in the advanced search under “Mix”, also in the track pool of the playlist generator and as a smart playlist rule; there new also “Cover” and “Hot cues” like `/Cover` and `/HotCues`) shows tracks with a tempo change: in the beat grid or “Transition” or a tempo sequence like “100-128” in the title or file name.
- Playlist generator: for tracks with a tempo change, the BPM rule checks the tempo at the respective end (transition 100 → 128 fits behind a 100 and before a 128) instead of the BPM tag; the slot list shows “100→128”.
- DJ editor: metronome sound selectable (Beep, Click, Woodblock, Rimshot, Cowbell, Hi-hat) under Settings → DJ view, with a preview when switching; all sounds considerably louder and equally loud, the track recedes a little more with the metronome on.
- DJ editor, Info: beat grid analysis, phrase analysis and both together as buttons of their own.
- Phrase analysis measures on the existing beat grid (edit, takeover from Rekordbox or own measurement) instead of searching for one of its own. This gives phrases also for tracks without an unambiguous tempo (transitions, tempo changes) as soon as the grid is set by hand; “Beat grid + phrases” then falls back to this too and reports it. Checked at an even tempo on three tracks: the same phrases as before.
- DJ editor, phrases without existing phrases: “Split” creates both halves (before, only from the playback position, the start stayed empty), the empty list offers “Create phrase” and “Phrase analysis”.
- Phrases snap to the beat grid: on an analysis against a locked grid, on a grid analysis also locked phrases, and after changes by hand. In the DJ view an analysis counts immediately as the track’s state.
- DJ view, memory cues: ten fixed places, after deleting the next one moves up. A hot cue at the same spot is shown. An active loop creates an Active Memory Loop (red), a click on the icon switches to the normal one (orange). If the track runs over an Active Memory Loop, it engages by itself. The cue at the playback position (one beat before to one bar after) is marked in the list with a darker ring around the colour dot. Both variants go to Rekordbox as Rekordbox itself stores them (read from one example each).
- Sync Rekordbox loads beat grid, phrases and cues into TrackTab as far as nothing is there yet. Later changes in Rekordbox no longer overwrite anything automatically: they are displayed and only taken over via “Load Rekordbox”.
- Music column: logo like Rekordbox, coloured in Music.app, otherwise grey.
- Rekordbox column: sorting in four steps (in Rekordbox, green, red, not in Rekordbox, each first).
- Lists: own colour via the plus at the last position of the colour selection.
- Rekordbox backup complete: it now also contains what Rekordbox has not yet taken over into the main file (the side file `master.db-wal`). Before, everything since the last transfer into the main file was missing. A bulk action backs up once instead of per track.
- “Rekordbox update” for many tracks considerably faster: one call per ten tracks instead of two per track, the offset to Rekordbox is remembered instead of re-measured every time. Measured on 20 tracks (copy of the library): before about 2 s per track, now 0.7 s on the first and 0.2 s on every further run.
- One way to Rekordbox: dragging onto a Rekordbox playlist (and the button for the target playlist) brings beat grid, phrases, cues and cover from TrackTab along for new tracks. If tracks are already in Rekordbox, you choose in a list which ones get TrackTab’s version; the deviating ones are preticked. The same list exists as “Compare with TrackTab …” per Rekordbox playlist. “Add to Rekordbox” in the DJ view stays (collection only) and only appears in the bar if a selected track is missing there. Checked on a copy of the library, not in Rekordbox itself.
- Linked playlists: a TrackTab playlist or smart playlist can be linked to one playlist each in Music.app and Rekordbox (or created there anew); the targets are chosen in the program’s playlist tree. Adding and removing then applies everywhere, TrackTab determines the order. Sync on open, after changes, at start and via button; Rekordbox is caught up if it was running. Smart playlists only fill the linked lists outwards. Confirmation for missing Music tracks and for many removals. Rekordbox checked on a copy, Music.app with a test playlist on the real library (the import itself not, only the confirmation and the omission).
- Move playlists and folders in Rekordbox and Music.app by drag & drop in the tree (into folders, before/after, in Rekordbox also back to the top level). While a change is running the tree stays expanded, the branch is greyed out and locked, the affected playlist pulses. Rekordbox checked on a copy, Music.app with test folders.
- Create, rename and delete playlists in Rekordbox and Music.app directly in TrackTab (also folders), add tracks by drag & drop and remove via the bar, in Edit as in the DJ view. Rekordbox deletes like Rekordbox itself (the entries stay marked as deleted). Smart playlists remain view-only. Rekordbox checked on a copy, Music.app on the real library with a test playlist (create in folder, rename, add, remove, delete). The Music.app interface (dragging, menu, import confirmation) has not yet been clicked through in the browser.
- “DJ” view next to Edit and Player: shows TrackTab’s own lists and those from Music.app and Rekordbox (titles not present in TrackTab there marked as in Edit, without editor) in a fixed list (cover, track and artist, duration, key, BPM, energy, tags). Expanding opens an editor with overall view and zoom waveform, eight hot cues, up to ten memory cues, colour choice, loops up to 64 beats without a gap (Web Audio), beat grid and phrase editing by dragging, Cue key as on the player, quantise, metronome, undo and keyboard shortcuts. The starting point is the data from Rekordbox or the own analysis; changes live in the TrackTab database, never in the file.
- DJ view waveform: own settings for the source (Rekordbox with TrackTab as fallback or TrackTab only) and the colour (Standard, RGB, 3-band), the actual source is shown by an icon in the zoom. The background is adjustable (dark, light, UI colour), the contour finer, and with deep zoom the individual oscillations of the sound appear.
- Edit and Player now always render the coloured waveform (RGB, 3-band) themselves, also for tracks Rekordbox does not know. The colour fades in smoothly in the shape of the grey waveform. The “Waveform colour” setting is under Appearance, beat grid, phrases and zoom no longer exist there.
- Loops with 1/2, 1/4 and 1/8 beat and the keys I, O, P. Metronome made audible.
- Phrases hang on the beat grid: shifting or stretching takes them along.
- DJ view: key X splits the phrase at the nearest beat grid marker (with Shift at the nearest bar 1, via button with Option key exactly at the playback position), also without Q. If you hold a beat jump key (A, S, D, F, G, H) for a moment, the jump width appears slightly translucent over the list column, waveform and tracks stay free.
- DJ view: open files as in Edit, via folder icon or drag & drop. The single check is available there with all functions.
- Beat jumps in a tab of their own, by keyboard hold a letter and press an arrow key (A 64, S 32, D 16, F 8, G 4, H 2). TAP only works with a running track. The BPM column of the DJ list only changes with a really deviating edited tempo and turns red if the file’s tempo does not match the beat grid. New Rekordbox column: logo coloured or grey depending on the collection, outlined green or red depending on whether TrackTab and Rekordbox match.
- Analysis lock in the DJ view, per beat grid and phrases: an edit locks the respective field for analysis, the beat grid lock also for BPM and key. The locks can be released and set by hand. It only applies in TrackTab, Rekordbox’s own analysis lock is not yet read or written.
- Sync with Rekordbox in the DJ view: “Load Rekordbox”, “Rekordbox update” (writes cues, loops, beat grid and phrases to Rekordbox, with backup) and “Add to Rekordbox”. If an edited part changes in Rekordbox in the meantime, TrackTab asks. The result has so far only been checked on a copy of the library, not in Rekordbox itself.
- Beat grid & phrases (test function): TrackTab detects tempo, bar and sections of a track itself, optionally with a fixed tempo (Normal), with tempo changes (Flex) or automatically (Auto). Beat grid and sections appear in the waveform and can be shown and hidden separately; “Rekordbox, otherwise TrackTab”, “Rekordbox only” or “TrackTab only” can be chosen as the source. Nothing is written to the file or to Rekordbox.
- Waveform zoom in the Player: up to 64×, the width stays, the track runs through under the central playback position. The position relative to the audible sound has not yet been checked in real playback.
- Reloading during playback: Cmd+R, Cmd+Shift+R and F5 as well as the link “Reload page” after a scan or a backup restore ask first while a track or a stem listening preview is running; “Cancel” lets playback continue undisturbed. With the browser’s reload button, when closing the tab and with Cmd+R over an open dialog, the browser’s notice appears instead (this path has not yet been checked in a real browser). If nothing is running, it never asks. The automatic reload after saving settings and on quitting does not ask.
- Reloading by dragging over the upper window edge is switched off so that it does not end playback in Safari without asking. Not yet checked in Safari.

- Bulk bars in Edit, Player, DJ and the single checks are arranged in labelled groups (Queue, Playlists, Favorites, Rekordbox, Analysis, Edit, Review, Open, Export, Delete), the ⋮ menu of a row in the same groups with headings. The DJ bar shows icons instead of text buttons as in Edit.
- Own tooltips instead of the browser’s hints: appear faster, immediately when gliding over a bar, for many actions with a short explanation in a second line.
- Edit: Load Rekordbox, Rekordbox update and Add to Rekordbox now also here (bulk bar and ⋮ menu), “Remove from Rekordbox” is in the Rekordbox group. Beat grid, phrases and both as three actions of their own. New: “Play next” for several tracks.
- DJ view: ⋮ menu at the row end (playlists, Rekordbox, analysis, edit tags, file, delete), bulk bar additionally with “Add to playlist” and “Remove from Rekordbox”; the queue is dropped there.
- Player: ⋮ menu and bulk bar can remove tracks from the open playlist.
- Removing from a Music playlist also works via the ⋮ menu of a row, with its own icon. Tracks that are only in Rekordbox now also have a ⋮ menu in Edit (remove from the playlist or from Rekordbox).
- “Remove from Rekordbox collection …” is now called “Remove from Rekordbox …”.
- Bulk bars: group headings at the same height, icons larger and with more spacing. New icons: beat grid (columns), phrases (rows), beat grid & phrases (grid), correct bitrate, convert format and re-analyse track.
- Tracks can be dragged onto the Queue button and are appended at the end.
- Title bar: the issue counter next to the title is dropped (there is the Issues column for that); the favorite list badges have slightly rounded corners like the mood badges instead of pill shape.
- Scan dialog: “Loudness scan” and “Cutoff scan” are off by default.
- The converter now also takes key and mood into the new file (so far both were lost). The key stays in the original’s notation; if it is missing there, it comes from TrackTab’s database.
- Converter: new target **AAC 256 kbit/s** (`.m4a`, Apple’s encoder as in Music.app) next to MP3 320 and AIFF. Allowed from lossless files and from lossy ones from 256 kbit/s; tags and cover are taken over.
- AAC re-encoding (correct bitrate, converter, calibrate) automatically uses Apple’s encoder as in Music.app, via `afconvert` from a lossless floating-point intermediate (ffmpeg’s own binding to Apple’s encoder only accepts 16 bit and audibly overdrives loud sources), with fallback to ffmpeg’s `aac`. The new file hits the target bitrate exactly. The “AAC encoder” setting is dropped.
- AAC detection calibrated on Apple’s encoder (`cutoff_classes.aac`, before unchecked placeholders): calibrate now recognises AAC second encodings in 18 of 18 cases (before 1 or 7 of 18). Old placeholder values never changed in the own settings are replaced automatically by the new ones.
- Scan catches up skipped measurements: tracks that were scanned without cutoff or without loudness (verdict “unknown”, no LUFS value) are measured by the next scan with the switch on, even if the file has not changed. Failed loudness measurements are retried. Before, such tracks stayed without a value until a full scan.
- Selected tracks stay selected when switching between Edit, Player and DJ (before, the selection was discarded).
- DJ view: bulk bar with the same actions as Edit (tags, convert, bitrate, rename, review, Mixed In Key, ZIP, delete), without queue, with Auto Cue.
- Removed: fixed Rekordbox target playlist (setting “Rekordbox playlist” and the Rekordbox button per row and in the bulk bar). Tracks go into a Rekordbox playlist by drag & drop, into the collection via “Add to Rekordbox”. The Rekordbox history in the statistics now depends on the selection of Rekordbox instead of the target playlist.
- Playlist generator: the waveform per slot is coloured according to the waveform setting (RGB or 3-band), shows hot cues and memory cues (own edit before Rekordbox) and below it a bar with TrackTab’s phrases. Slightly taller than before (40 instead of 34 px).
- Calmer settings: short hints on a field are behind a ? at the name and appear as a tooltip (also via click and keyboard); the search still finds them. Long explanations, examples, warnings and prerequisites stay visible. “Restore default” is a plain button “Reset” without a frame.
- Issues: individual issues can be ignored per track (and restored); ignored ones show greyed out in their own bubble and no longer count.
- Settings → General: keyboard layout (Automatic, QWERTZ, QWERTY, AZERTY) for the DJ view shortcuts. Shortcuts are bound to key position rather than character (loops and jumps sit in the same row on every layout); only the labels change.
- The update notice shows the changes in the interface language (German from `changelog.de.md`, otherwise English).
- Bilingual documentation: `readme`, `manual`, `installation`, `quickstart` and `changelog` exist in English (default) and German (`*.de.md`), linked to each other. `handbuch.md` is now `manual.md`, `technische_funktionen.md` is now `technical_functions.de.md`.

### Fixed
- Hidden tracks: when scrolling horizontally, other cells showed through (double icons, light stripe), and in Duplicates “Keep” slid over the checkbox. The cells now stay opaque, the content is shown via dimmed colours instead of transparency. Checkbox, “Keep” and “Open” stay fully visible and fixed, the checkbox always lies on top.
- DJ view: after another program had played music, the DJ editor in Safari kept running silently until TrackTab was restarted. The editor’s audio output is now built fresh at every play after a pause and renewed by itself during playback if the output device changes or the output hangs. If it ever stays silent: stop and play again.
- DJ view: in Genre, Album and Artist the Track column was missing, the rows became very tall. The cause was the group header spanning over 100 columns and taking the whole width from the Track column.
- Music.app: own tracks that Music only keeps as a cloud entry without a local file (this happens e.g. when you add them to a playlist on the iPhone) no longer count as unknown in Music playlists and can be entered into Music playlists again, also via linked playlists. Matching is via title, artist and file size (otherwise duration), only on an unambiguous hit. Checked only read-only on the real library, entering such a track into a playlist not.
- The app almost always ran throttled without a window of its own (macOS App Nap: efficiency cores only, lowest priority). Scan, stems, analyses and above all Auto Cue training thereby run many times faster, training on real material about nine times. During a training the Mac also no longer goes to sleep by itself.
- Phrases begin at the first beat at the earliest, no longer in the silence before it; applies to new measurements, existing analyses and writing to Rekordbox.
- Settings: the dot in the navigation only shows unsaved changes instead of every deviation from the default.
- Shop links open in the system’s default browser, not in the interface’s browser.
- Touchpad zoom no longer enlarges the page; the gesture only zooms the waveform in the DJ editor, also in Safari.
- DJ editor: the sidebar keeps the height of the ten memory cues, the other tabs scroll within it.
- The waveform colour for Edit and Player in the app always jumped back to the old value after saving if the once copied-out `config.yaml` still carried the earlier setting name.
- When removing from the Rekordbox collection, the cover folder and, in TrackTab, the stored Rekordbox state stayed behind; Player and DJ view then kept showing its cues and grid.
- Phrases that TrackTab wrote into a track without existing phrases were not displayed by Rekordbox (missing bit in `ContentLink`).
- Up 2 and Up 3 were assigned wrongly when reading and writing the Rekordbox phrases.
- `.aif` files could not be added to Rekordbox.
- The beat grid got slightly fluctuating tempos per beat when written to Rekordbox.
- Hover contrast: active buttons like Q turned white on light on hover and were barely readable. Text no longer switches to the accent colour on hover, text on the accent colour is light or dark depending on the theme, labels on coloured areas choose the more readable colour.
- Program icons (Music, Finder, Rekordbox, Mixed In Key) are delivered without a transparent margin and therefore look equally large.
- Hot cue colours: only the 16 colours checked in Rekordbox, with the colour values measured there, also in the colour picker.
- Memory cue colours: the assignment to Rekordbox was off by one, red appeared orange there. All colours are now confirmed in Rekordbox, also “no colour”. Cues already transferred change at the next update.
- Player: in the Issues column and its dialog, no path leads to editing metadata any more.
- DJ view, Rekordbox update: the beat grid now begins at the audio start instead of the first later beat, the first phrase and a cue at the start thus lie at the same spot (not yet checked in Rekordbox itself).
- DJ view: phrases are numbered automatically per category and handed over to Rekordbox like that, as far as Rekordbox can display the number; the choice by hand is dropped.
- Rekordbox hot cues from D on were displayed shifted by one letter (D as E, E as F), H was missing: Rekordbox counts them as 1, 2, 3, 5, 6, 7, 8, 9.

### Changed
- Row buttons “Play next” and “Add to queue” coloured neutrally, only Play keeps the accent colour.
- Icons: DJ view “turntable”, list “Album” “disc-3” (like “group by album”).
- Converter, MP3 320: encodes via the bundled `lame` (`-V0 -b 320 -B 320`, every frame 320 kbit/s, header tag `Info`) instead of ffmpeg CBR. This keeps the frequency range above 20 kHz that LAME otherwise cuts off with CBR 320. Checked in Rekordbox and on an XDJ-RX3. Without `lame` fallback to ffmpeg. Correct bitrate and stems remain unchanged.
- Order of the views: Player, DJ, Edit.
- List header: “Undo” and “Redo” (↩ ↪) now only appear for normal playlists. In fixed views (Album, Artist, Genre …), smart lists and lists from Music.app or Rekordbox they were always locked and are now hidden.
- Licences and bundling: LightGBM (MIT) is now in the bundle for Auto Cue training (before only in the development folder, `requirements-train.txt` is dropped), together with SciPy (only `scipy.sparse`, BSD-3-Clause) and narwhals (MIT); the OpenMP runtime libomp (Apache-2.0 with LLVM exception) comes from Homebrew into the bundle. `THIRD_PARTY_LICENSES.md`, `collect_licenses.py` (new folder `licenses/native/`) and `NOTICE.md` are updated. Auto Cue itself still does not need LightGBM. Where it cannot be loaded (older macOS), only training is missing.
- Licences updated: the licence texts of onnxruntime (with `ThirdPartyNotices.txt`), flatbuffers, PyObjC-Core, CPython and the SQLCipher embedded in `sqlcipher3-wheels` are now in `licenses/` (`collect_licenses.py` takes them along, hand-maintained texts in `licenses/other/`). `THIRD_PARTY_LICENSES.md` and `NOTICE.md` name bidict (MPL-2.0), SQLCipher/OpenSSL, the code taken over from rbxport with attribution and the phrase detection: model and calculation come from GlideMix (GPL); `fetch_models.sh` fetches the model from its source archive, `package_sources.sh` puts the archive with the release. The “Licences & third parties” dialog additionally lists LAME, rbxport, GlideMix, librosa, SciPy, LightGBM, SQLCipher, OpenSSL, bidict and CPython as well as the note “without warranty”.
- The Rekordbox inventory (presence of tracks, statistics) is only read if Rekordbox is selected in the settings, no longer dependent on a linked Rekordbox playlist (the setting is dropped).
- The sync menu (Scan library, sync Rekordbox and Music.app) is also available in the DJ view.
- ZIP export starts the download via a download link instead of a navigation of the page, so that it triggers no browser notice during playback (not yet checked with a real download).

## [1.10.0] - 2026-10-30

### Added
- Stem separation: ⋮ menu → “Generate stems …” separates a track locally (Demucs v4 via onnxruntime on the graphics chip/Neural Engine through CoreML, a 4-minute track in about 15 s; without CoreML on the CPU about 40 s) into acapella and/or instrumental. Progress in percent; the dialog can be minimised and then does not block the interface (progress notice at the bottom, on completion a clickable notice that opens the listening preview). The listening preview shows a waveform per stem with Play/Pause, Stop and time display; a click or drag in the waveform previews from that spot. Only “Save” (floppy icon; next to it “Delete” discards the preview) creates the file: file next to the original (name by name pattern, AIFF for a lossless, MP3 320 kbps for a lossy source), tags and cover from the main track, title with suffix and genre “Acapella”/“Instrumental” (Settings → Stems), entry in TrackTab and Music.
- Stem listening preview: button “Download” downloads the stem with tags, cover and name as when saving as a file, without putting it next to the original or entering it in TrackTab/Music/Rekordbox; the preview stays (`GET /api/stems/download`).
- Settings → Rekordbox → “Transfer stems to Rekordbox”: Yes, with cues (default) / Yes, without cues / No (`stems_rekordbox`). Without a loaded stem model the setting is greyed out and locked, a notice links to Settings → Stems (new field attribute `requires` in `settings.GROUPS`). Locked likewise: the other stem settings without a model, the BPM/key settings without libsonare and the genre/mood settings without Essentia models (length limit and comment field only when both are missing), each with a note on the reason. Without a selected Rekordbox (Settings → Rekordbox → Rekordbox) all Rekordbox settings are locked; the selection releases them immediately, even before saving. The help text there so far said that without a selection the newest version is used — but the interface switches the integration off without a selection. Locked likewise as soon as the related switch is off: write BPM/key field (measure BPM or key), genre suggestions from % and mood tag (measure genre or mood), the auto runs of the single checks, the comment field (options individually by key/tempo/energy), the My Tag column and the minimum cutoff. In the Rekordbox tab the minimum cutoff is now under “Quality check on import”, and the button “Open Rekordbox backup folder” has moved there from Backup & log. “Parallel processes” is now called “Files analysed in parallel”, is in the Library section (instead of a group of its own “Performance” under Backup & log) and explains what a smaller value is good for. Only taken over if main track and stem have the same format (MP3 → MP3, lossless → AIFF); from AAC an MP3 stem is made whose beat grid would not fit, it is not transferred to Rekordbox.
- Stems in Rekordbox: new track with BPM, key, beat grid, waveform, cover and all hot cues/memory cues/loops of the main track (copied ANLZ files in the folder scheme of the content UUID, `contentFile` entries, `DjmdCue` rows), in the same playlists. The first version put the analysis in a wrongly named folder without `contentFile` entries: Rekordbox discarded it at start and re-analysed, the stem had no preview until then and never cues. The second removed the file index `PVBR` from the DAT file, whereupon Rekordbox discarded the beat grid; in addition the main track’s sample rate instead of 44.1 kHz was in Rekordbox. MP3 stems are now written without an info header and on Rekordbox’s time axis of the original (measured offset: 0 ms for AIFF/WAV, 26–53 ms for MP3 with LAME header, 47 ms for AAC) so that grid and cues sit exactly. If Rekordbox is running, the stem is queued (table `rekordbox_stem_pending`) and taken over via “Sync Rekordbox” → “Transfer queued items”. Taking over beat grid and cues has not yet been checked in Rekordbox itself.
- Stem separation uses CoreML (GPU + Neural Engine) instead of the CPU only: about 2.9× faster with an inaudible deviation. The first time the model is translated for about a minute (cache `models/demucs/coreml-cache/`, ~635 MB, deleted with “Remove model”), after that about 10 s per app start; the dialog shows “Preparing separation model …” in the meantime. If CoreML fails, the CPU calculates as before.
- The Demucs model (~170 MB) is not shipped with the app (licence of the weights): Settings → Stems → “Load model” (with licence notice and confirmation) puts it under `models/demucs/` in the data folder. Without the model all stem functions are hidden. `fetch_models.sh` no longer loads it.
- Statistics: under “Most fully listened” there is the button “Create playlist from fully listened” (up to 50 titles, default name “Fully listened · {year}”); the ranking now delivers up to 50 instead of 10 entries for this (10 are still displayed).
- Stem dialog: selection “Quality” (Fast: about 15 % shorter processing time, rarely slight artefacts at the section transitions; Precise: full overlap), the last choice is remembered. A running separation can be cancelled. Files above “Skip longer files” (Settings → Stems, `stems_max_minutes`, default 15 minutes) are not separated.
- Rename library tracks: row menu “Rename file” and a button in the bulk bar rename files by the name pattern (`POST /api/rename-library`). Database, playlists and favorite lists go along. The Music.app track is re-pointed to the new file (playlists and play counts stay); if Music.app refuses, the renaming is taken back. In Rekordbox the path is corrected; if Rekordbox is running, it is queued and written via “Sync Rekordbox” (new column `rekordbox_pending.prior_path`, migration automatic). Re-pointing in Music.app (`set location`) has not yet been checked on real material.
- Settings → Analysis: partial analyses can be switched off individually: “Measure BPM”, “Measure key”, “Genre suggestions”, “Mood & energy” (`measure_bpm`, `measure_key`, `measure_genre`, `measure_mood`). This applies to buttons, scan and single checks. BPM and key are then not measured at all; genre and mood share one model run, the switched-off result is merely not taken over. If both parts of an analysis are off, their buttons disappear too.
- Statistics: new ranking “Most fully listened”. It counts plays from 90 % of the track length, with a tie the listening time in multiples of the track length, so that a 60-minute mix heard once does not beat a track heard five times. Tracks without a complete play or without a known length are missing (`top_tracks_relative`).
- Footer: link “Licences & third parties” opens a dialog with the programs and models used, their licences and the note on the non-commercial models.
- CLI `check`: the “Slope” line additionally names the distance to the band above the edge, the new line “Sections” the edge per 20-s section.

### Changed
- Licence: TrackTab is now under GPL-3.0-or-later instead of MIT, because the `.dmg` contains GPL software (FFmpeg build with x264/x265, LAME, mpg123, mutagen). Versions up to and including 1.9.x remain usable under MIT. New: `NOTICE.md`, `THIRD_PARTY_LICENSES.md` and licence texts in `licenses/` (also in the app bundle), `collect_licenses.py` collects the licence texts, `package_sources.sh` packs the source archives for the GitHub release.
- Settings dialog rebuilt: side navigation on the left (General, Analysis, Connections, Data, Advanced) instead of accordions, plus a search across labels and help texts. A dot in the navigation marks pages with changed values. Switches, selections and numbers appear as a row with the control on the right. The storage location is only a tooltip in the footer field.
- Settings: new page “Music.app” with the Music app selection (before External programs) and “Take covers from Music.app” (before Library). “Orphaned folders” sits directly next to “Add folder”. The help on the name pattern is divided into placeholders, rules and example.
- Statistics: the actions are grouped by area (Analysis, Files, Tags & cover, Music.app, Rekordbox, Other), each area with a total. Newly named are, among others, scan analysis, BPM/key, genre/mood/energy, stems and Trash by Music.app; unknown actions appear with their raw name.
- An open dialog locks the scrolling of the page behind it.
- Dependencies updated: FFmpeg 9.0.2 (brings OpenSSL 4 instead of 3), libsonare 1.10.0, onnxruntime 1.31.1, PyObjC 13.0.
- Cutoff analysis: an edge now only counts if the spectrum above it stays quiet all the way to the top (plateau check, `plateau_min_ratio`). In addition the edge is measured per 20-s section (`edge_segments`): if it is in almost every section, confidence rises; if it jumps between bitrate classes, the explanation says “probably assembled from several sources”; if it sits continuously at ~20 kHz in a lossless file, it says “probably exported from a ~320 kbps source”. New DB columns `plateau_db`/`edge_segments` (migration automatic); the values only arise with `scan --force`. Inspired by a comparison with whatsmybitrate.

### Fixed
- Cutoff analysis: 320s with a gap in the spectrum (at 19.2–20.2 kHz, full content above up to 22 kHz) counted as FAKE or SUSPICIOUS. In a sample of 538 files this affected 14 tracks of one album; all other verdicts stayed unchanged.
- “Move to Trash” failed with “File no longer exists” if the file was already missing (e.g. because Music.app had put it into the Trash itself when removing it from the library) — the row could not be removed afterwards. Now the entry is cleaned up anyway. If the file only disappears during the process (Music.app puts it into the Trash with a delay), this no longer counts as an error either.
- Statistics “Most played titles”: the ranking was sorted by total listening time, so a single track listened to for long (one play) could stand before often-heard titles. Now the number of plays (from 30 s of actual listening time) counts first, with a tie the listening time. The statistics are only re-sorted after “Recalculate”.

## [1.9.5] - 2026-10-26

### Added
- Playlist builder: tracks can be dragged from the table onto a builder in the tree (even if it is not open) or onto the window of an open builder. They are appended as a new slot at the end and locked immediately so that “Re-roll all” does not replace them. Tracks already contained, missing files and entries from Music.app/Rekordbox are skipped; the list is limited to 200 slots.

### Changed
- The baked report is now called `data/tracktab.html` (instead of `report.html`), the CSV `data/tracktab.csv`. The interface opens at `/tracktab.html`; `/report.html` stays valid as an alias. Old `report.*` files in `data/` are no longer written and can be deleted. Own values for `report_path`/`csv_path` remain unchanged.

### Fixed
- The coloured Rekordbox waveform occasionally stayed black or grey while the hot cues appeared. Causes: (1) Rekordbox queries ran in parallel over a single, non-thread-safe database connection (now one after another, `_query_lock` in `rekordbox.py`); (2) a read error was silently treated as “not in Rekordbox” (now the server reports `retry`, the client tries up to three times); (3) colour data consisting only of zeros produced a black waveform. Unusable Rekordbox waveforms now fall back to the grey standard waveform, the cues are kept.

## [1.9.4] - 2026-10-25

### Added
- Bulk bar: new button “Undo correction” next to “Mark as correct” — removes the mark for all selected. The icon (Lucide `circle-dashed-check`) is also in the row menu at “Undo correction”.

### Changed
- List menu (sidebar): “Column view …” is in a group of its own with a separator line in front.
- “Remove from Rekordbox collection” has the Lucide icon “delete” in the row menu and the bulk bar.
- Row menu: after “Remove from playlist” there is a separator line.
- Favorite buttons in the bulk bar have the same opacity as in the row: dimmed as long as no row of the selection is in the list; fully opaque for all (with dot) or some (without dot).

### Fixed
- Move to Trash: if the file is already missing on disk, this failed with “cannot be converted to type alias (-1700)” and the entry stayed. Most common cause: Music.app puts the file into the Trash itself when removing it from its library. Now only the entry is removed; the notice reads “is in the Trash” if it was Music.app, otherwise “was already gone”.

## [1.9.3] - 2026-10-25

### Added
- Update notice at start: TrackTab queries the newest GitHub version once in the background and, for a newer one, shows an overlay with a link to the repository. The checkbox “Ignore this version” suppresses the notice (`update_ignore_version` in `config.local.yaml`) until there is an even higher version. The notice (and the manual check) lists the changes of all newer versions from the repository’s `changelog.md`. Without a network nothing appears; the manual check in the settings always shows.
- Spectrogram: in front of the cutoff value there is an icon that opens an overlay with the track’s spectrogram (main table and single checks). It is calculated fresh with `ffmpeg` on every click (about 1 s for a track of about 8 minutes) and not cached. Frequency and time axes are labelled, a dashed cutoff line with bitrate can be shown and hidden with a checkbox. New endpoint `/api/spectrogram`.
- Logo and title “TrackTab” in the header open the start view: list “All”, search and bubble filters reset, scrolled to the top — without a page reload, playback and single checks are kept.

### Changed
- Scan options rearranged: Database, Full, Loudness, Cutoff, Genre/mood/energy/BPM/key, Issues, Metadata.
- Scan options: “Metadata” removed. The cover sync with Music.app runs on every scan according to the setting “Take covers from Music.app”; it can only be forced via `scan --covers`.
- Scan step “Genre, mood, energy, BPM & key” (before only genre, mood, energy): additionally fills in missing BPM and key (empty fields only, never overwriting). Files are only written according to the settings (BPM & key, comment, mood); before, this step never wrote to files. New counters `bpmkey_done`, `bpmkey_failed`, `tags_written` in the scan result, also in the CLI (`scan --genremood`).
- Row menu (⋮) rearranged: BPM & key, Genre & mood, Re-analyse track — line — Convert format, Correct bitrate / Align key, Mark as correct — line — Hide — line — Remove from Rekordbox collection, Delete. New in the menu: “Convert format”.
- Row menu: “Analyse BPM/key” is now “Analyse BPM & key”, “Correct” is now “Mark as correct”; for hidden tracks “Show” displays the normal instead of the crossed-out eye.
- Bulk bar regrouped (separated by lines): Queue/Playlist/Rekordbox/Favorite lists — Edit and analyses — Convert format, Correct bitrate, Mark as correct (now without colour) — Hide/Show — Mixed In Key — Download — Rekordbox remove/Delete. Empty groups drop out together with their line.
- Playlist icons (sidebar, list header, selection dialogs) are rounded squares (4 px) instead of circles. The favorite buttons are 16 × 16 px, their membership dot 5 × 5 px.
- Waveform: while the envelope loads, instead of the text “Calculating envelope …” a slow animation of grey half bars runs across the whole width (20 s at most, after that the bars stand still).
- Album group rows: font size 14 px (before 12.5 px).

### Fixed
- Player bar: the envelope of the track chosen at start is now also loaded when server mode only becomes known after the first drawing. Before, it stayed empty and the loading animation ran endlessly; without a track or after a load error it now stops.
- Header: in Player mode the statistics button on the left no longer has a separator line (it came from the hidden “Open file”).

## [1.9.2] - 2026-10-24

### Added
- Player bar: above the playback line there is a half, single-colour waveform (36 px high, played part in accent colour, without Rekordbox colours) that merges seamlessly with the line. Clicking and dragging on it scrubs as on the line.
- Playlist generator: the BPM deviation between two tracks can also be set to ±5 and ±7 (before ±0, ±3, any).

### Changed
- Player bar: `.pbarcenter` up to 90 % width, title area 260 px, volume with 30 px spacing, progress line 2 px high.
- Playlist generator: the slot waveforms also only show the upper half as a single-colour envelope; the Rekordbox colour waveform is no longer loaded there.

### Fixed
- Playlist generator: loading the waveforms no longer blocks the interface. First tracks and covers appear, after that the envelopes are loaded at most two at a time and fill in gradually.

## [1.9.1] - 2026-10-23

### Added
- Playlist generator: the advanced search of the track pool now has the switch “Hidden” (any / yes / no). The default is “no” — hidden tracks stay out as before, but can be added. Existing generators are switched to this default once when opened.
- Header: new combined bar next to “Quit” with the icons Open file, Statistics, Sources and Settings. “Sources” (database icon) opens a menu with Scan library, Sync Rekordbox and Sync Music.app; a dot on the icon shows need for action (currently: changes queued for Rekordbox), in the menu the affected entry is marked.
- Settings → Column views: “Column view of the single check”. The single check can take over order, visibility and widths of a saved view (default: still own, independent columns).

### Fixed
- “Issues” list: the bubbles in the list header again stand next to each other with spacing instead of sticking together.
- Playlist generator: the waveform of a slot again shows the coloured RGB display instead of the grey envelope for tracks from Rekordbox (without cue markers, they do not fit in the slot row).

### Changed
- The toolbar under the title is dropped: scan and syncs are in the Sources menu, the statistics in the combined bar. “Auto-relocate” and “Remove from list” stay in place and only appear for missing files.
- The single check is hidden at first and appears as soon as a file is added via “Open file” or by drag & drop; “Clear list” hides it again.
- The running status of scan and syncs is now under “TrackTab”, with a wider bar, highlighted phase and percentage.
- Grey waveform smoothed a little less (strength 0.6 instead of 0.7), transients thus stay sharper.

## [1.9.0] - 2026-10-23

### Added
- Genre, mood and energy: TrackTab detects style, mood and energy (1–10) locally with the Essentia models of the MTG (onnxruntime, models via `fetch_models.sh`). Call in the row menu, in the bulk bars and as an option in the scan dialog; genre suggestions in the genre field of the tags dialog, energy and mood on request in the comment or in the mood field of the file. New columns Energy and Tags, new list “Tags” with own tags.
- Playlist generator (new list type): builds a set along an energy curve with transition rules (BPM, half/double tempo, key strict/loose), mood dials and cohesion; locked slots stay in place on re-roll.
- Generator: energy curve with scale E1–E10 — a click sets a slot’s target energy, dragging draws across several slots. Own curves can be saved under a name and chosen in every generator (they adapt to the slot count). Every filled slot shows the waveform of its track.
- Generator: the track pool is a search query in the same syntax as the search bar (e.g. `/Genre House /BPM 120-128 /Add 30d`), plus optionally a playlist of its own as the source. Older generators are switched automatically when opened.
- Advanced search: via “Advanced” next to the search help, all search parameters are available as input fields (with value suggestions, yes/no fields as segment selection). Fields and search text stay in sync and can be mixed; the same panel is in the track pool of the generator.
- Search: `/Add 30d` finds tracks added in the last 30 days.
- Smart playlists: rule “Energy” as level 1–10 (is, is not, at least, at most) and rule “Tags” with all existing moods and own tags.
- “Tags” list: own tags can be renamed or merged in all tracks via an Edit button.
- Rekordbox: when transferring into a Rekordbox playlist, moods and own tags are set as “My Tag” on request (setting; the chosen My Tag column must exist in Rekordbox).
- Settings → Comment field: new option “Add missing parts” — existing values stay, only missing parts are added by the chosen scheme (`8A - 128` → `8A - 128 - E7`).

### Changed
- Track lists (library and single checks): every row and the header row are rounded cards of their own with spacing. Title semi-bold, BPM large with a label, “In Music” and “Rekordbox” as small app icons instead of ticks, larger selection checkboxes.
- Expanded track: without a spectrum graphic; the waveform uses the full width and is drawn as a slightly smoothed envelope. The action column runs beside the expanded area to the end of the card.
- Badges for tags, key and genre are slightly rounded rectangles; moods fill their badge entirely in their colour.
- Icons: smart playlist “book-a”, playlist generator “book-headphones”, “Analyse BPM/key” clef, “Analyse genre & mood” confetti, “Align key notation” “replace”. In the list header a line separates the list types from the folder.
- “M3U8 export” is now called “Playlist export”, in the list menu “Export playlist”.
- “Re-analyse track” is in the ⋯ menu of the row. In the bulk bars BPM/key, genre & mood and re-analyse are a separate, delimited group.
- Smart playlists: the field selection in the rule editor is sorted alphabetically.
- Generator: transitions and mood as sections of their own (label above the fields, tempo as selection “Off | ½ · 2×”), slot count with −/+, source without the fixed review lists, track energy in the accent colour, explanation of cohesion via question mark.
- Dependencies updated: pyrekordbox 0.5.0 (module is now `pyrekordbox.masterdb`, code switched accordingly), libsonare 1.9.1, Pillow 13.0, numpy 2.5.4, SQLAlchemy 2.1.6, python-dateutil 3.0, pyinstaller-hooks-contrib 2026.9. Checked: BPM/key measurement, cover check and Rekordbox read and write paths deliver the same results as before, `calibrate` (MP3 and lossless 100 %) and a test build run through.

## [1.8.0] - 2026-10-10

### Added
- BPM and key analysis: TrackTab measures tempo and key itself (libsonare, local, without network) and writes them into the file tags. Call via “Analyse BPM/key” in the row menu and in the bulk bar of the table as well as in the bulk bar of the single checks; newly added files of the single checks are analysed automatically on request. New endpoint `/api/bpmkey`, new module `app/bpmkey.py`.
- Settings: new group “BPM & key analysis” (sections Key field, BPM field and Comment field: key and BPM “Yes” / “Yes, only if empty” / “No”, in the comment with existing text “only if empty”, “overwrite” or “prepend”). The key follows the “Key notation” setting. Files above an adjustable length (default 10 minutes) are not analysed.
- Settings: the “Key notation” is now in the “Key field” section of the group “BPM & key analysis”; the group of its own “Key” is dropped.
- libsonare is an optional dependency (`requirements.txt`; PyPI wheel only for macOS Apple Silicon). If it is missing, the interface hides the function. The built app grows by about 10 MB as a result.

### Changed
- “Write key to file” is now called “Align key notation” and is only offered for files whose key notation differs from the setting (dashed outline). It now also exists in the single checks.
- Bulk bars (table and single checks): two separate icons without a menu: “Analyse BPM/key” (waveform) is always there, “Align key notation” (clef) only if at least one track in the selection differs.
- Settings: the group is called “BPM & key”. In the “Key field” section the help text explains that the notation applies to both the analysis and the aligning, and a hint line says how many library files differ.
- Internal: `tags.key_file_value()` is the only place that connects key and `key_notation` to the file notation (tags dialog, aligning, analysis).

## [1.7.4] - 2026-10-10

### Changed
- List header of Genre, Album and Artist: the number at the top right now names the number of genres, albums or artists instead of the total number of tracks.

## [1.7.3] - 2026-10-10

### Added
- Statistics: new “Library” tab with a snapshot of the holdings: track count, playing time, storage size, average length, formats, tag completeness (cover, genre, BPM, key, year, album artist), vintages, BPM distribution, keys (in the Camelot colours), additions per month and year according to Music.app, coverage (Rekordbox, playlists, favorite lists, hidden, confirmed as correct) as well as maintenance hints (tag issues, duplicate groups). New endpoint `/api/stats/library`.
- `./run.command stats` outputs the same holdings figures in the terminal.

### Changed
- Statistics: all charts (also actions and playing time per month) are stacked at full width; the axis labels are normal text and no longer vertically squashed.
- Statistics: years outside 1900 to next year count as “missing” and no longer stretch the vintage axis.

## [1.7.2] - 2026-10-10

### Changed
- Genre, Album and Artist list: the values in the list header appear as a letter index with A–Z bar (jump to the letter), sections per letter with a separator line and left-aligned bubbles; the count is a badge, easily readable also with a selected entry.

## [1.7.1] - 2026-10-04

### Fixed
- Search: umlauts and accents are ignored when comparing (“Kolsch” finds “Kölsch” and vice versa, likewise é/e, ñ/n, č/c). Applies to the normal search, field filters, exact comparisons, the fuzzy search and the suggestions in the search field. Not covered: ß, ø, æ, ł as well as “oe”/“ae” for ö/ä.

## [1.7.0] - 2026-10-04

### Added
- Key: new column “Key” with coloured bubbles in the colours of the Camelot wheel. The key is read from the file tag when scanning (ID3 `TKEY`, MP4 freeform atom, Vorbis `INITIALKEY`) and backfilled for existing libraries at the next scan without `--force`.
- Key: new settings section “Key” with the notation Camelot, Open Key or note names. It applies to the table, the tags dialog, written file tags and the transfer to Rekordbox; all three notations are always read.
- Key: if the value in the file differs from the display in TrackTab, the bubble has a dashed outline; the hint text names the file value.
- Key: editable in the tags dialog (individually and for several tracks) with suggestions for all 24 keys in the chosen notation.
- Key: “Write key to file” in the ⋮ row menu and in the bulk bar writes the key kept in TrackTab into the file tag (tracks without a key and with an already identical value are skipped).
- Key: search with `/Key` or `/Tonart` in any notation (a plain number matches minor and major), sortable by position on the wheel, and as a rule field in smart playlists.
- Rekordbox: when transferring new tracks, the key is set along (existing Rekordbox entries remain untouched).
- New icon `clef-treble` in the vendored Lucide set.

### Changed
- BPM column: whole values appear without “.0” (`130` instead of `130.0`), real decimals stay (`127.5`).
- Tags dialog: BPM starts a new row, the key sits behind it.
- Settings → Appearance: accent colour across the full width, then the two switches half each, then the browser for the web UI.
- Smart playlists: “Limit to” in the rule dialog is now a switch (when “off” the related fields are locked); number field and selection fields visually match the other form fields. As selection criteria “most recently added” and “random” remain (lowest cutoff and artist are dropped; lists already saved like that treat it like “most recently added”).

## [1.6.1] - 2026-10-03

### Fixed
- Queue: several versions of the same song (e.g. “Replay”, “Replay (Remix)”, “Replay - … Edit” by the same artist) no longer land directly one after another. The title comparison now ignores bracket and “ - ” additions, and the automatically filled queue only takes one version (also without shuffle). Further versions only move up if otherwise too few titles are left. Titles queued by hand stay untouched.

## [1.6.0] - 2026-10-03

### Added
- Search: exact comparison with `=` behind a text field (`/Album =Pop` only finds the album “Pop”, not “King of Pop”; also `="Pop Hits"` and groups like `(=Pop OR =Rock)`).
- Search: find empty fields with `=` alone or `=""` (e.g. `/Album =`, `/Genre =""`, `/Year =""`).
- Search: new switches `/Cover`, `/Music` and `/Rekordbox` (negatable with `/No`) for tracks with/without cover or in Music.app/Rekordbox.
- Search: autocomplete shows behind each suggestion whether it is an artist, title or album.

### Changed
- Search: the free text now also searches the album (before only artist, title and path); album names also appear in the suggestions.

## [1.5.2] - 2026-10-03

### Changed
- Statistics: the renamings are now called “View Artist/Album/Genre: … renamed” (instead of “Artist”/“Album”/“Genre renamed”).

### Fixed
- Statistics: “Issue fix” and “Rekordbox tags transferred” showed the raw key instead of a display name.

## [1.5.1] - 2026-10-03

### Fixed
- Music.app: the playlist tree now follows the order of the sidebar in Music.app (folders and lists mixed, before first all lists, then all folders).

## [1.5.0] - 2026-10-03

### Added
- Rekordbox: tag changes from merging (genre/artist/album) are no longer written to Rekordbox immediately but queued. The button “Sync Rekordbox” shows the number as a counter; the new option “Transfer queued tags” writes them all in one run (one backup of `master.db` instead of one per merge).

### Changed
- Merging: the Music.app sync now only queries all tracks with the old value once, instead of searching the whole library for the title per track (considerably faster with large libraries; title matching stays as a fallback).

## [1.4.4] - 2026-10-03

### Changed
- Clean-up: for albums, the merge suggestions and the target dialog show the artist in square brackets behind the album name (as in the bubbles), so that it is recognisable which album is meant.
- Clean-up: the box with the merge suggestions grows up to 500 px (before 240 px), after that it scrolls.

## [1.4.3] - 2026-10-02

### Fixed
- Edit tags: if a file has no artist tag, the “Artist” field showed the album artist as a substitute, and saving reported “No changes”. Now an info box points to the substitute value, and saving writes it as the artist tag into the file.
- Edit tags: the issues (e.g. “Artist missing”) are re-checked after saving and disappear immediately, not only after another scan.

## [1.4.2] - 2026-10-02

### Fixed
- Issues: the note “Interpret fehlt” is now called “Künstler fehlt” (artist missing) and thereby matches the field’s name in the rest of the interface.

## [1.4.1] - 2026-10-02

### Fixed
- Rekordbox: folders and playlists now appear in the sidebar tree in the same order as in Rekordbox (before in the order of their creation).

## [1.4.0] - 2026-10-02

### Added
- Tracks can be dragged by drag & drop onto normal Rekordbox playlists in the tree (Rekordbox must be closed; check when dragging, when dropping and server-side). `/api/rekordbox-add-playlist` optionally takes `playlist_id` for this.
- Editing Rekordbox playlists: “Remove from Rekordbox playlist” and “Remove from Rekordbox collection” (like Rekordbox’s “Remove from Collection”, including backup of the analysis folders), change the order by moving. As in Rekordbox itself, deletion is only a mark (soft delete).
- New column “#” shows the position in Rekordbox, Music App and own playlists; column sorting leaves this order unchanged.

### Fixed
- Rekordbox: tracks and playlist entries removed in Rekordbox (only marked as deleted there) still counted as present in TrackTab — in the presence sync, with cues/waveform and when adding (a formerly removed entry was skipped as “already present”, a new entry could land on an invisible track).
- Rekordbox: adding left gaps in the playlist’s track numbering.
- Write access to Rekordbox now also checks the background service “rekordboxAgent”.

## [1.3.1] - 2026-10-01

### Fixed
- Rekordbox smart playlists with a time rule (e.g. “added in the last 6 months”) can now be displayed. `pyrekordbox` evaluates such rules incorrectly; TrackTab now converts them itself into a fixed cut-off date.
- When moving to the Trash, the track is now always also removed from Music.app as soon as a Music app is set — no longer only if TrackTab had imported it there itself. Only a hit with exactly the same file path is removed; the feedback “removed from Music.app” only appears if something was actually removed.

## [1.3.0] - 2026-09-22

### Added
- The installed PWA can now start TrackTab directly if the local server is not running: the notice page shows the TrackTab icon and a **Start** button that opens the app via a URL scheme of its own (`tracktab://`) and reloads the page automatically as soon as the server is ready. Only works with the built app, not when running from the terminal.

### Changed
- After quitting (**Quit** button), TrackTab now shows the same screen as for an unreachable server instead of a separate notice — the page reloads automatically as soon as the server has actually stopped.

## [1.2.0] - 2026-09-22

### Added
- An own app build (`dist/TrackTab.app`, also via the shortcut in `/Applications` that `build_app.sh` creates) now uses a data folder of its own (`TrackTab-Build`) and its own port range instead of sharing database, settings and port with a version installed via .dmg — both variants can now run at the same time without influencing each other.

## [1.1.0] - 2026-09-22

### Added
- TrackTab can now be installed as an app of its own (Progressive Web App): via the install icon in the address bar (Chrome/Edge) or “Add to Dock” (Safari), the interface then opens in a window of its own without browser tabs/address bar instead of in a normal tab. The local server still has to be running for this.

## [1.0.3] - 2026-09-20

### Fixed
- The scan completion message (“Done — … — Reload page”) no longer stays after reloading: the server keeps the result of the last scan until the next scan, the freshly loaded page immediately drew it again. A tab now remembers which completion message it has already shown, a new scan shows a new one again.

## [1.0.2] - 2026-09-20

### Fixed
- The track counter of a favorite list or playlist in the sidebar tree now counts along immediately when tracks are added or dropped via bulk action, single favoriting, drag & drop or “Remove from list” — before, only after a reload. If the server call fails, the counter jumps back to the old value.

## [1.0.1] - 2026-09-20

### Changed
- Quick Fix “Cover unusually large”: oversized embedded covers are now shrunk to about 1000 px (longer edge) instead of 500 px and recompressed as JPEG. Detection (> 2 MB) remains unchanged.

## [1.0.0] - 2026-09-20

First release.

### Added
- Spectral cutoff analysis for MP3, AAC/M4A, ALAC, FLAC, WAV, AIFF — recognises upscaled/transcoded files by the low-pass edge of the first encoding.
- Verdict logic in two modes: bitrate class via cutoff for lossy formats (MP3, AAC), pure edge detection for lossless formats (ALAC/FLAC/WAV/AIFF).
- Local web interface: searchable review list, waveform with hot cues, spectrum preview, media player, bulk actions, Edit and Player layouts with freely configurable columns.
- Named column views (order, visibility, width), assignable per list, with a selectable default view.
- Playlists, smart lists and favorite lists in a shared tree; favorite lists are marked playlists and take over their colour and icon (up to four). Fixed, read-only status lists for OK, Suspicious, Fake, Unclear and Manually corrected.
- Playlist trees from Music.app and Rekordbox are displayed read-only in the same tree (folders and smart playlists included).
- Metadata editor (title, artist, album, album artist, composer, genre, year, BPM, cover, comment) including online suggestions (iTunes/Deezer/MusicBrainz) and multi-edit.
- Genre, album and artist clean-up: suggestions for spellings to merge, orphaned folders, issues in the tags.
- Bitrate correction by re-encoding (MP3/AAC) with takeover of all tags including Serato/Rekordbox frames.
- Format converter (MP3 320 kbit/s CBR or AIFF) for selected tracks, including protection against upscaling, automatic Music.app import and Rekordbox path correction.
- Automatic renaming by a freely configurable pattern (default `{artist}_{title}_{bpm}_{key}_{bitrate}_{year}`), exclusively for files outside the library folders.
- Duplicate detection via file hash or artist + title + duration.
- Single-file check without a library scan (“single check”/drops) via drag & drop or file selection, with its own, independent column configuration; “Correct bitrate” and tag editing also work there without a database row.
- Music.app import (including path correction when Music.app moves the file into its media folder) and Rekordbox playlist sync with auto-relocate for moved files.
- Queue with “Recently played”; if the list has been played through and “Repeat list” is not active, 25 new titles from the current filter view are appended automatically.
- Statistics dashboard: year/month evaluation from the change log and actual playback time (actions per month, total listening time, most-played titles/artists/genres); “Create playlist from Top 50” creates a new playlist from the most-played titles of a year.
- Change log as JSON Lines without auto-deletion, drag export as ZIP, playlist export, CSV and M3U8 export of the conspicuous tracks.
- Scan options with toggles for cutoff/loudness/metadata; `scan --covers` for a pure cover sync with Music.app without a full re-scan.
- Setting “Browser for the web UI”: determines in which browser the interface opens at app start and via the Dock icon.
- Interface entirely via language files (German, English set up), light and dark design with selectable accent colour.
- `calibrate` regression check: tests the cutoff detection on self-generated transcodes (MP3, AAC, lossless re-wrap).
- macOS app bundle via PyInstaller with its own app icon, single-instance protection, Cmd+Q/Dock quit; `package_dmg.sh` packs it with standalone ffmpeg/ffprobe into a single `.dmg` — installation on someone else’s Mac entirely without the terminal.
- README, manual, installation guide and technical reference including screenshots of all functions and icon reference of the button bar.

### Performance
- Report rebuild decoupled from the request path: baking happens bundled in a background thread (0.35 s collection window) and at the latest when the page is next delivered. A rebuild costs about 600 ms on real material (10,822 rows → 10 MB HTML + CSV + M3U); a bulk action over 50 tracks thus triggers 2 instead of 50 rebuilds.
- The server delivers page and data compressed (gzip): page load 9.8 → 2.5 MB, mark lists 1,430 → 310 KB. The compressed report is kept instead of being regenerated on every request.
- Measurements run outside the global lock: “Re-analyse track” and the import from the single check no longer halt the interface (about 2 s per file, so about 40 s standstill with 20 tracks). The same applies to queries to Music.app and Rekordbox.
- Value suggestions of the search: one `Intl.Collator` built once instead of a new one per comparison — **161 → 8 ms** per keystroke (5,606 album values, measured in Chrome).
- Table sorting via precomputed sort keys: sorting by “File” **54 → 32 ms** at 10,822 rows, order cross-checked on all 17 columns in both directions.
- The search scope per row is built once and kept: **34 → 21 ms** per keystroke, the one-time build (58 ms) runs in idle time after the first drawing.
- Waveform: bars are calculated once into a layer of their own and only copied per frame; the playback position runs from a single `requestAnimationFrame` loop instead of `ontimeupdate`; no `getComputedStyle()` and no layout query in the drawing path; one shared `MutationObserver` for all expanded rows.
- `db.connect()` sets up schema, column migration and fixed lists once per database file and process instead of on every HTTP request; database in WAL mode, the pure read paths thereby no longer need a global lock.
- `/api/audio` delivers via `sendfile()` and supports `ETag`/`If-None-Match`/`If-Range`; `/api/cover` may be cached; `/api/waveform` merges simultaneous requests for the same path into one ffmpeg run.
- Mark tables are read once per call instead of per output row (with 50 imported tracks before over 800,000 rows read).
- Drag & drop writes in blocks to the temporary file instead of loading the entire content into memory.

### Security
- Origin check of the local server against host name **and** the actually bound port — another page on `127.0.0.1` thus does not count as the own interface (protection against CSRF and DNS rebinding).
- File access checked against the database and additionally against a fixed list of known audio extensions (`config.AUDIO_EXTENSIONS`). Approvals from the native file selection expire after 12 hours without use and are capped.
- When setting a cover, the header bytes decide the written image type, not the `Content-Type` sent along. Delivered cover types come from a fixed list (no SVG).
- Data embedded in the report is escaped for the `<script>` context — a `</script>` in a tag would otherwise have ended the script block.
- Cover download from the net only from publicly reachable addresses (no localhost/private subnet), with a size limit and image format check. Program paths from the settings must point to an existing program bundle, shop addresses to `http`/`https`.
- Rekordbox write access all runs through a shared lock of its own, with `pgrep` check and raw backup of `master.db` before every access.

### Known limitations
- AAC bitrate classification is experimental: neither ffmpeg’s native `aac` encoder nor Apple’s `aac_at` applies a bitrate-correlated low-pass like LAME does for MP3 — calibration (28.08.2026) gave only a 39 % hit rate (`aac`) or ~6 % (`aac_at`), compared with 100 % for MP3 and lossless re-wrap. Treat AAC verdicts with caution accordingly.
- 224 and 256 kbit/s use the same LAME low-pass for MP3 and are not spectrally distinguishable.
