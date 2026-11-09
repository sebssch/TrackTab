// ── DJ-Ansicht: Track-Editor ─────────────────────────────
// Dritte Ansicht neben Bearbeiten und Player (body[data-layout="dj"], siehe
// applyLayout() in app.js). Klappt eine Zeile auf, erscheint statt der normalen
// Wellenform dieser Editor: Gesamtansicht, Zoom-Wellenform, Hot Cues, Memory
// Cues, Loops, Beatgrid und Phrasen.
//
// Datenmodell (Tabelle dj_track, GET/POST /api/dj-track):
//   - je Beatgrid, Phrasen und Cues gilt die Bearbeitung oder sonst die Quelle
//     nach beatgrid_source (Rekordbox, eigene Analyse). Der Server liefert alles
//     auf der eigenen Zeitachse, Rekordbox-Werte schon um den Versatz korrigiert.
//   - Es wird nur gespeichert, was der Nutzer geaendert hat (ed.dirty). Geht nie in
//     die Datei. Nach Rekordbox nur ueber die Knoepfe "Rekordbox laden/Update/
//     Hinzufuegen" (POST /api/dj-rekordbox), siehe rekordbox.update_track().
//   - Beatgrid = Abschnitte {t, bpm, n} (t = Sekunde eines Schlags, n = dessen
//     Nummer im Takt). Die Schlagliste wird daraus gerechnet (djRebuildBeats()) und
//     fuer die anderen Ansichten mitgespeichert, sie kennen nur die Liste.
//
// Eigene Wiedergabe statt queueAudio: der Editor braucht eine stehende Position,
// die vom Player-Balken unabhaengig ist, und einen lueckenlosen Loop. Deshalb Web
// Audio mit dem ganzen Track als PCM (/api/dj-pcm, djCreateDeck()). Der Ton kommt
// damit aus derselben ffmpeg-Dekodierung wie Wellenform und Analyse. Das Deck hat
// dieselbe Oberflaeche wie ein <audio> (play, pause, paused, currentTime, ...), der
// Eintrag in 'players' sorgt dafuer, dass immer nur einer spielt.
//
// Diese Datei laeuft im selben <script> NACH app.js (report._template()), also
// wie generator.js nur Funktionsdeklarationen und 'var' auf oberster Ebene.

// Offener Editor (undefined = keiner). Es ist immer hoechstens einer offen.
var DJ;
// Vorlieben {q, span, mode, side}, je Browser (localStorage).
var DJ_PREFS;
var DJ_AUDIO_CTX;
var DJ_NEXT_ID;
var DJ_BARS_FMT;

function djIcon(name) { return SVG(LUCIDE_ICONS[name] || ""); }

// Hoehen der Spuren in den Canvases (px).
function djZoomLayout() { return {top: 18, lane: 22}; }
function djOverLayout() { return {top: 12, lane: 12}; }

function djPhraseKinds() { return ["intro", "up", "down", "verse", "bridge", "chorus", "outro"]; }
// Wie Rekordbox ohne eigene Farbe: Hot Cues gruen, Memory Cues rot.
function djHotColor() { return [109, 223, 70]; }
function djMemoryColor() { return [255, 0, 0]; }
// Memory Loop: aktiv rot, normal orange.
function djLoopColor(active) { return active ? [255, 0, 0] : [255, 165, 0]; }
// Hot Cue an derselben Stelle wie ein Memory Cue (Rekordbox rundet auf Millisekunden).
function djHotAt(ed, sec) { return ed.cues.find(c => c.kind === "hot" && Math.abs(c.position_s - sec) < 0.01) || null; }
// Loop-Laengen auf der Tastenreihe, lange Schleifen links.
function djLoopKeys() { return [["q", 64], ["w", 32], ["e", 16], ["r", 8], ["t", 4], ["z", 2], ["u", 1], ["i", 1 / 2], ["o", 1 / 4], ["p", 1 / 8]]; }
// Beatjumps: Buchstabe halten und Pfeiltaste. Ohne Buchstabe springt der Pfeil 1 Schlag.
function djJumpKeys() { return [["a", 64], ["s", 32], ["d", 16], ["f", 8], ["g", 4], ["h", 2]]; }
// Das Tempo gilt erst als anders, wenn es wirklich abweicht: 125 und 125.00 sind eins.
function djEditedBpm(bpm, tag) { return bpm && Math.abs(bpm - (tag || 0)) >= 0.005 ? bpm : 0; }
function djLoopLabel(n) { return n >= 1 ? String(n) : "1/" + Math.round(1 / n); }

function djPrefs() {
  if (!DJ_PREFS) {
    DJ_PREFS = {q: true, span: 16, mode: "cue", side: "memory"};
    try {
      Object.assign(DJ_PREFS, JSON.parse(localStorage.getItem("tracktab.dj") || "null") || {});
    } catch (err) { /* ohne Speicher gelten die Vorgaben */ }
  }
  return DJ_PREFS;
}
function djSavePrefs() {
  try { localStorage.setItem("tracktab.dj", JSON.stringify(DJ_PREFS)); } catch (err) { /* nur fuer diese Sitzung */ }
}

// Hoechstzahl Memory Cues je Track. Aus Rekordbox Geladenes bleibt vollstaendig
// sichtbar, nur neue kommen nicht mehr dazu.
function djMaxMemory() { return 10; }

// Ueber Anfang und Ende hinaus: je ein Takt des ersten bzw. letzten Abschnitts, ohne
// Grid zwei Sekunden. Das Grid selbst bleibt im Track.
function djOverPad(ed) {
  const bar = seg => (seg ? 240 / seg.bpm : 2);
  return {pre: bar(ed.segs[0]), post: bar(ed.segs[ed.segs.length - 1])};
}
function djClampOver(ed, sec) {
  const {pre, post} = djOverPad(ed);
  return Math.max(-pre, Math.min((ed.dur || 0) + post, sec));
}
// Zeitfenster der Gesamtansicht.
function djOverRange(ed) {
  const {pre, post} = djOverPad(ed);
  return {start: -pre, span: ed.dur + pre + post};
}
function djPad2(n) { return String(n).padStart(2, "0"); }
function djFmt(sec) {
  const t0 = Math.abs(sec || 0);
  const ms = Math.round(t0 * 1000);
  return `${sec < 0 && ms ? "-" : ""}${djPad2(Math.floor(ms / 60000))}:${djPad2(Math.floor(ms / 1000) % 60)}.${String(ms % 1000).padStart(3, "0")}`;
}
function djFmtTenth(sec) {
  const t0 = Math.abs(sec || 0), d = Math.floor(t0 * 10);
  return `${sec < 0 ? "-" : ""}${djPad2(Math.floor(d / 600))}:${djPad2(Math.floor(d / 10) % 60)}.${d % 10}`;
}
function djFmtBpm(bpm) { return (Number(bpm) || 0).toFixed(2); }
// Hoch ergibt sich wie beim Schreiben aus Up/Down. Dort wird aus Verse Up und aus
// Bridge Down, umgekehrt ebenso: die Zaehlung zeigt schon, was dort ankommt.
function djPhraseIsHigh(ed = DJ) { return !!ed && ed.phrases.some(q => q.kind === "up" || q.kind === "down"); }
function djPhraseCategory(kind, high) {
  return (high ? {verse: "up", bridge: "down"} : {up: "verse", down: "bridge"})[kind] || kind;
}
// Nummer = laufende Zahl innerhalb der Kategorie, in Reihenfolge des Tracks. Nie von
// Hand: rekordbox._number_phrases() rechnet beim Schreiben genauso.
function djRenumberPhrases(ed) {
  const high = djPhraseIsHigh(ed), count = {};
  for (const p of ed.phrases) {
    const cat = djPhraseCategory(p.kind, high);
    p.num = count[cat] = (count[cat] || 0) + 1;
  }
}
function djPhraseLabel(p) { return t("phrase." + p.kind) + (p.num ? " " + p.num : ""); }
function djRgb(c) { return `rgb(${c[0]},${c[1]},${c[2]})`; }
function djNewId() { DJ_NEXT_ID = (DJ_NEXT_ID || 0) + 1; return DJ_NEXT_ID; }

// ── Wiedergabe ──────────────────────────────────────────

function djCtx() {
  if (!DJ_AUDIO_CTX) DJ_AUDIO_CTX = new (window.AudioContext || window.webkitAudioContext)();
  return DJ_AUDIO_CTX;
}

// Web-Audio-Spieler mit der Oberflaeche eines <audio>. Bei sehr langen Dateien
// (Speicher) oder wenn das PCM nicht kommt, faellt er auf ein echtes <audio> zurueck;
// ein Loop muss dort springen.
function djCreateDeck(path, duration) {
  const FADE = 0.004, MAX_SECONDS = 1800;
  const deck = {
    paused: true, ended: false, ready: false, loading: true, wantPlay: false, duck: 1,
    onplay: null, onpause: null, onended: null, onloaded: null,
    buffer: null, html: null, src: null, fade: null, gain: null, offset: 0, startedAt: 0, range: null,
    abort: new AbortController(),
    get duration() {
      if (deck.html) return deck.html.duration;
      return deck.buffer ? deck.buffer.duration : NaN;
    },
    // Roh = was gerade an den Ausgang geht; currentTime = was gerade zu hoeren ist.
    rawTime() {
      if (deck.html) return deck.html.currentTime;
      if (deck.paused || !deck.src) return deck.offset;
      let at = deck.offset + (djCtx().currentTime - deck.startedAt);
      const r = deck.range;
      if (r && at >= r.end) at = r.start + ((at - r.start) % (r.end - r.start));
      return deck.buffer ? Math.min(at, deck.buffer.duration) : at;
    },
    get currentTime() {
      if (deck.html || deck.paused) return deck.rawTime();
      const ctx = djCtx();
      return Math.max(0, deck.rawTime() - (ctx.outputLatency || ctx.baseLatency || 0));
    },
    set currentTime(value) {
      if (deck.html) { deck.html.currentTime = value; return; }
      deck.ended = false;
      deck.offset = value;
      if (!deck.paused && deck.buffer) { deck.stopSource(); deck.startSource(value); }
    },
    startSource(from) {
      const ctx = djCtx();
      if (!deck.gain) { deck.gain = ctx.createGain(); deck.gain.connect(ctx.destination); }
      const now = ctx.currentTime, src = ctx.createBufferSource(), fade = ctx.createGain();
      src.buffer = deck.buffer;
      if (deck.range) { src.loop = true; src.loopStart = deck.range.start; src.loopEnd = deck.range.end; }
      fade.gain.setValueAtTime(0, now);
      fade.gain.linearRampToValueAtTime(1, now + FADE);
      src.connect(fade).connect(deck.gain);
      src.onended = () => {
        if (deck.src !== src) return;
        deck.src = null; deck.paused = true; deck.ended = true; deck.offset = deck.buffer.duration;
        if (deck.onended) deck.onended();
      };
      src.start(now, from);
      deck.src = src; deck.fade = fade; deck.startedAt = now; deck.offset = from;
    },
    stopSource() {
      if (!deck.src) return;
      const now = djCtx().currentTime, src = deck.src;
      deck.src = null;
      src.onended = null;
      deck.fade.gain.cancelScheduledValues(now);
      deck.fade.gain.setValueAtTime(1, now);
      deck.fade.gain.linearRampToValueAtTime(0, now + FADE);
      src.stop(now + FADE + 0.002);
    },
    play() {
      if (deck.html) return deck.html.play();
      if (!deck.buffer) { deck.wantPlay = true; return Promise.resolve(); }
      if (!deck.paused) return Promise.resolve();
      djCtx().resume();
      deck.paused = false; deck.ended = false;
      if (deck.offset >= deck.buffer.duration - 0.01) deck.offset = 0;
      deck.startSource(deck.offset);
      if (deck.onplay) deck.onplay();
      return Promise.resolve();
    },
    pause() {
      deck.wantPlay = false;
      if (deck.html) { deck.html.pause(); return; }
      if (deck.paused) return;
      deck.offset = deck.currentTime;
      deck.paused = true;
      deck.stopSource();
      if (deck.onpause) deck.onpause();
    },
    // {start, end} oder null. Laufend umstellbar: die Quelle spielt einfach weiter,
    // nur die Position wird neu angesetzt.
    setLoop(range) {
      if (deck.html) { deck.range = range; return; }
      const at = deck.rawTime();
      deck.range = range;
      if (!deck.src) return;
      deck.offset = at; deck.startedAt = djCtx().currentTime;
      deck.src.loop = !!range;
      if (range) { deck.src.loopStart = range.start; deck.src.loopEnd = range.end; }
    },
    // Laeuft je Bild: Lautstaerke vom Player-Balken, beim <audio> der Loop-Sprung.
    tick() {
      if (deck.html) {
        const r = deck.range;
        if (r && !deck.html.paused && deck.html.currentTime >= r.end) deck.html.currentTime = r.start;
        deck.html.volume = queueAudio.volume;
        return;
      }
      if (deck.gain) deck.gain.gain.value = queueAudio.muted ? 0 : queueAudio.volume * deck.duck;
    },
    close() {
      deck.abort.abort();
      if (deck.html) { deck.html.pause(); deck.html.removeAttribute("src"); return; }
      deck.stopSource();
      if (deck.gain) deck.gain.disconnect();
      deck.buffer = null; deck.paused = true;
    },
  };

  const useHtml = () => {
    const html = new Audio("/api/audio?path=" + encodeURIComponent(path));
    html.preload = "metadata";
    deck.html = html;
    deck.loading = false;
    Object.defineProperty(deck, "paused", {get: () => html.paused, configurable: true});
    Object.defineProperty(deck, "ended", {get: () => html.ended, configurable: true});
    html.onplay = () => { if (deck.onplay) deck.onplay(); };
    html.onpause = () => { if (deck.onpause) deck.onpause(); };
    html.onended = () => { if (deck.onended) deck.onended(); };
    html.onloadedmetadata = () => { deck.ready = true; if (deck.onloaded) deck.onloaded(); };
    if (deck.offset) html.currentTime = deck.offset;
    if (deck.wantPlay) html.play().catch(err => playFailed(html, err));
  };
  if (duration > MAX_SECONDS) { useHtml(); return deck; }
  (async () => {
    try {
      const res = await fetch("/api/dj-pcm?path=" + encodeURIComponent(path), {signal: deck.abort.signal});
      if (!res.ok) throw new Error(String(res.status));
      const rate = Number(res.headers.get("X-Pcm-Rate")) || 44100;
      const pcm = new Int16Array(await res.arrayBuffer());
      const frames = pcm.length >> 1;
      const buffer = djCtx().createBuffer(2, Math.max(1, frames), rate);
      const left = buffer.getChannelData(0), right = buffer.getChannelData(1);
      for (let i = 0; i < frames; i++) { left[i] = pcm[i * 2] / 32768; right[i] = pcm[i * 2 + 1] / 32768; }
      deck.buffer = buffer; deck.ready = true; deck.loading = false;
      if (deck.onloaded) deck.onloaded();
      if (deck.wantPlay) { deck.wantPlay = false; deck.play(); }
    } catch (err) {
      if (err.name !== "AbortError") useHtml();
    }
  })();
  return deck;
}

// ── Oberflaeche ─────────────────────────────────────────

function djDetailHTML(r) {
  if (r.gone) return `<div class="grid"><div class="detailbody"><div class="path">${t("player.no_file_at_path")}</div></div></div>`;
  if (!apiMode) return `<div class="grid"><div class="detailbody"><div class="path">${t("player.open_via_serve")}</div></div></div>`;
  return `<div class="grid djgrid"><div class="dj">
    <div class="dj-transport">
      <button class="dj-play" data-dj="toggle" data-act="toggle" title="${esc(t("dj.play_title"))}">${ICONS.play}</button>
      <button class="dj-cuebtn" data-dj="cue" title="${esc(t("dj.cue_title"))}">CUE</button>
    </div>
    <div class="dj-main">
      <div class="dj-screen">
        <canvas class="dj-over"></canvas>
        <canvas class="dj-zoom"></canvas>
        <span class="dj-wavesrc" hidden></span>
        <span class="dj-zoombtns">
          <button class="dj-btn" data-dj="zoomin" title="${esc(t("wave.zoom_in"))}">${ICONS.waveZoomIn}</button>
          <button class="dj-btn" data-dj="zoomout" title="${esc(t("wave.zoom_out"))}">${ICONS.waveZoomOut}</button>
        </span>
      </div>
      <div class="dj-banner" hidden></div>
      <div class="dj-pads"></div>
      <div class="dj-toolbar">
        <span class="dj-modes">
          <button class="dj-mode" data-dj="mode" data-mode="cue">${esc(t("dj.mode_cue"))}</button>
          <button class="dj-mode" data-dj="mode" data-mode="grid">${esc(t("dj.mode_grid"))}</button>
          <button class="dj-mode" data-dj="mode" data-mode="phrase">${esc(t("dj.mode_phrase"))}</button>
          <button class="dj-mode" data-dj="mode" data-mode="jump">${esc(t("dj.mode_jump"))}</button>
        </span>
        <span class="dj-spacer"></span>
        <button class="dj-btn dj-lockbtn" data-dj="analysislock" data-field="grid"></button>
        <button class="dj-btn dj-lockbtn" data-dj="analysislock" data-field="phrases"></button>
        <button class="dj-btn" data-dj="undo" title="${esc(t("dj.undo"))}">${djIcon("undo-2")}</button>
        <button class="dj-btn" data-dj="redo" title="${esc(t("dj.redo"))}">${djIcon("redo-2")}</button>
        <button class="dj-btn dj-q" data-dj="q" title="${esc(t("dj.quantize_title"))}">Q</button>
      </div>
      <div class="dj-tools"></div>
    </div>
    <aside class="dj-side">
      <div class="dj-sidetabs">
        <button class="dj-sidetab" data-dj="side" data-side="memory">${esc(t("dj.tab_memory"))}</button>
        <button class="dj-sidetab" data-dj="side" data-side="hot">${esc(t("dj.tab_hot"))}</button>
        <button class="dj-sidetab" data-dj="side" data-side="phrases">${esc(t("dj.tab_phrases"))}</button>
        <button class="dj-sidetab" data-dj="side" data-side="info">${esc(t("dj.tab_info"))}</button>
      </div>
      <div class="dj-sidebody"></div>
      <div class="dj-rb" hidden></div>
      <div class="dj-colorpop" hidden></div>
    </aside>
  </div></div>`;
}

// ── Uebergabe mit dem Player-Balken ─────────────────────
// Oeffnet sich der Editor fuer den Track, den der Player-Balken (queueAudio) gerade
// spielt (Wechsel zu DJ oder Aufklappen in DJ), haelt der Player-Balken an und das
// Deck steht an derselben Stelle bereit -- weiter mit Play. Beim Verlassen der
// DJ-Ansicht umgekehrt (djClose()). Bewusst PAUSE statt nahtloser Uebergabe
// (Nutzervorgabe): queueAudio und Web Audio haben verschiedene Ausgabewege, auch
// mit Latenzausgleich (Deck ~38 ms voraus, queueAudio ~45 ms Anlauf, beide gemessen)
// blieb beim Umschalten ein Stolperer hoerbar. Die Dekodierung selbst ist identisch
// (Kreuzkorrelation /api/audio gegen /api/dj-pcm: 0 Samples Versatz).
function djIsQueueTrack(path) {
  return !!(queueState.current && queueState.current.p === path);
}

function djMount(det, r) {
  const root = det.querySelector(".dj");
  if (!root) return;
  if (DJ) djClose(DJ);
  const audio = djCreateDeck(r.p, r.du || 0);
  const prefs = djPrefs();
  const ed = {
    r, path: r.p, root, det, audio,
    dur: r.du || 0, pos: 0, dragPos: null, span: prefs.span,
    mode: prefs.mode, side: prefs.side,
    segs: [], phrases: [], cues: [], locked: false,
    bt: new Float64Array(0), bn: new Uint8Array(0), bb: new Int32Array(0),
    src: {grid: null, phrases: null, cues: null},
    edited: {grid: false, phrases: false, cues: false},
    uncertain: false, loaded: false, rb: null, palettes: null, cueHold: false, cueLatch: false,
    undo: [], redo: [], dirty: new Set(), saveTimer: 0,
    locks: {grid: false, phrases: false},
    selCue: null, selPhrase: -1, loop: null, tempCue: 0, holdSlot: 0,
    metro: false, metroNext: 0, taps: [], tapPushed: false,
    detail: null, detailRev: 0, closed: false,
  };
  DJ = ed;

  ed.overEntry = registerWaveEntry({
    root: det, canvas: root.querySelector(".dj-over"), ed,
    sync() { djSync(ed); },
    paint: djPaintOver,
  });
  ed.zoomEntry = registerWaveEntry({
    root: det, canvas: root.querySelector(".dj-zoom"), ed, audio,
    sync() {},
    paint: djPaintZoom,
    syncLabels() { djSyncLabels(ed); },
    onDispose() { djClose(ed); },
  });
  players.set(ed.path, ed.zoomEntry);

  audio.onloaded = () => {
    if (isFinite(audio.duration) && Math.abs(audio.duration - ed.dur) > 0.05) {
      ed.dur = audio.duration;
      djRebuildBeats(ed);
    }
    if (!ed.closed) djSyncPlayIcon(ed);
    requestWavePaint();
  };
  audio.onplay = () => { djSyncPlayIcon(ed); scheduleWavePaint(); };
  audio.onpause = () => { djSyncPlayIcon(ed); requestWavePaint(); };
  audio.onended = () => { djSyncPlayIcon(ed); requestWavePaint(); };

  root.addEventListener("click", ev => djOnClick(ed, ev));
  root.addEventListener("change", ev => djOnChange(ed, ev));
  root.addEventListener("keydown", ev => { if (ev.key === "Enter" && ev.target.matches("input")) ev.target.blur(); });
  // Rechtsklick auf ein belegtes Feld: Farbe waehlen.
  root.addEventListener("contextmenu", ev => {
    const pad = ev.target.closest(".dj-pad.on");
    const cue = pad && djHot(ed, Number(pad.dataset.slot));
    if (cue) { ev.preventDefault(); djOpenColors(ed, cue.id, pad); }
  });
  // Der Cue-Knopf wirkt beim Druecken und Loslassen, nicht beim Klick.
  const cueBtn = root.querySelector(".dj-cuebtn");
  cueBtn.addEventListener("pointerdown", ev => { cueBtn.setPointerCapture(ev.pointerId); djCueDown(ed); });
  cueBtn.addEventListener("pointerup", () => djCueUp(ed));
  cueBtn.addEventListener("pointercancel", () => djCueUp(ed));
  // Die Felder ebenso. Das Loslassen haengt am Fenster: djGoCue() baut die Felder
  // neu, das gedrueckte gibt es danach nicht mehr.
  root.querySelector(".dj-pads").addEventListener("pointerdown", ev => {
    const pad = ev.target.closest(".dj-pad");
    if (!pad || ev.button !== 0 || ev.target.closest("i")) return;
    const slot = Number(pad.dataset.slot);
    djHotDown(ed, slot, ev);
    const up = () => {
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      djHotUp(ed, slot);
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  });
  djWireZoom(ed);
  djWireOver(ed);
  djMountRowCells(ed);

  // Track des Player-Balkens: dort anhalten, Deck an dieselbe Stelle (siehe oben).
  if (djIsQueueTrack(r.p) && isFinite(queueAudio.currentTime)) {
    queueAudio.pause();
    ed.pos = queueAudio.currentTime;
    audio.currentTime = queueAudio.currentTime;
    requestWavePaint();
  }
  djRenderAll(ed);
  djSyncPlayIcon(ed);
  // Nacheinander: bei Rekordbox-Tracks rechnet /api/dj-track die Wellenform fuer
  // den Versatz ohnehin mit, parallel liefe die Dekodierung zweimal.
  djLoad(ed).finally(() => { if (!ed.closed) djLoadDetail(ed); });
  if (det.scrollIntoView) det.scrollIntoView({block: "nearest"});
}

// Dauer, Tonart und BPM sind normale Spalten der Liste. Der offene Track fuellt die
// Dauer live: hat er begonnen, gespielte und verbleibende Zeit. Das BPM zeigt der
// Editor nur, wenn es abweicht (djSyncLabels()).
function djMountRowCells(ed) {
  const row = ed.det.previousElementSibling;
  if (!row) return;
  ed.timeCell = row.querySelector(".ducell");
  ed.bpmCell = row.querySelector(".bpmcell");
  ed.rbCell = row.querySelector(".rbcell");
}
// Zelle der Zeile ueber dem Editor. Die beim Oeffnen gemerkte Zelle veraltet,
// sobald render() die Tabelle neu baut: der Editor wandert dann an die neue
// Zeile (expandedByIdx in render()), die gemerkte Zelle haengt aber nicht mehr
// im Dokument -- Status, Dauer und BPM landeten unsichtbar in der alten Zeile
// (z. B. blieb Rekordbox nach "Rekordbox Update" rot, bis man den Track neu
// oeffnete). Deshalb hier bei Bedarf neu suchen; die Anzeige-Caches
// (_tenth/_bpmCell) zuruecksetzen, damit die neue Zelle sofort gefuellt wird.
function djRowCell(ed, key, sel) {
  const cached = ed[key];
  if (cached && cached.isConnected) return cached;
  const row = ed.det && ed.det.previousElementSibling;
  const cell = row && row.classList.contains("row") ? row.querySelector(sel) : null;
  ed[key] = cell;
  if (cell && cached !== undefined) { ed._tenth = null; ed._bpmCell = null; }
  return cell;
}

// Beim Schliessen: die Dauer wie in der Liste, das BPM bleibt, wenn es geaendert wurde.
function djRestoreRowCells(ed) {
  const timeCell = djRowCell(ed, "timeCell", ".ducell");
  if (timeCell) timeCell.textContent = fmtTime(ed.r.du);
  ed.r.djBp = ed.edited.grid ? djEditedBpm(djMainBpm(ed), ed.r.bp) : 0;
  djStoreTempo(ed);
  const bpmCell = djRowCell(ed, "bpmCell", ".bpmcell");
  if (bpmCell) bpmCell.innerHTML = bpmCellHtml(ed.r.bp, ed.r.djBp, ed.r.gridTempo, djGridSourceKey(ed));
}
// Tempo-Spanne merken, damit die Liste auch ohne offenen Editor weiss, ob die Datei
// zum Beatgrid passt. Nur, wenn sich etwas geaendert hat.
function djStoreTempo(ed) {
  if (!ed.loaded || (String(ed.tempoSpan) === String(ed.r.gridTempo) && String(ed.tempoSeq) === String(ed.r.gridSeq))) return;
  ed.r.gridTempo = ed.tempoSpan;
  ed.r.gridSeq = ed.tempoSeq;
  const [lo, hi] = ed.tempoSpan || [null, null];
  fetch("/api/dj-tempo", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: ed.path, lo, hi, seq: ed.tempoSeq})}).catch(() => {});
}
// Tempo des laengsten Abschnitts, dieselbe Regel wie db.dj_edited_bpms().
function djMainBpm(ed) {
  let best = 0, bpm = 0;
  ed.segs.forEach((s, i) => {
    const len = (i + 1 < ed.segs.length ? ed.segs[i + 1].t : ed.dur) - s.t;
    if (len > best) { best = len; bpm = s.bpm; }
  });
  return bpm;
}

function djClose(ed) {
  clearInterval(ed.metroTimer);
  if (ed.closed) return;
  ed.closed = true;
  // Verlaesst man die DJ-Ansicht mit dem Track des Player-Balkens, steht der
  // Player-Balken danach an der Stelle des Editors, angehalten (Gegenstueck zu
  // djMount()). Zuklappen innerhalb von DJ laesst den Player-Balken unberuehrt.
  const handBack = state.layout !== "dj" && djIsQueueTrack(ed.path) && isFinite(ed.audio.currentTime);
  const handBackAt = handBack ? ed.audio.currentTime : 0;

  djReleaseJump(ed);
  djRestoreRowCells(ed);
  djSave(ed, true);
  clearTimeout(ed.saveTimer);
  ed.audio.close();
  // Player-Balken an die Stelle des Editors setzen, angehalten.
  if (handBack && queueAudio.paused) { queueSeek(handBackAt); scheduleWavePaint(); }
  if (players.get(ed.path) === ed.zoomEntry) players.delete(ed.path);
  if (DJ === ed) DJ = undefined;
}

// ── Laden und Speichern ─────────────────────────────────

async function djLoad(ed) {
  try {
    const res = await fetch("/api/dj-track?path=" + encodeURIComponent(ed.path));
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    if (!ed.closed) djAdopt(ed, data);
  } catch (err) {
    if (!ed.closed) note(t("dj.load_failed"), "soft");
  }
}

// Cue in der Form des Servers (dj_track.cues, Rekordbox, /api/autocue) -> Editor.
function djCueFromServer(c) {
  return {
    id: djNewId(), kind: c.kind, slot: c.slot || null, label: c.kind === "hot" ? "ABCDEFGH"[(c.slot || 1) - 1] : "",
    name: c.name != null ? c.name : (c.kind === "memory" ? (c.label || "") : ""),
    // Knapp vor dem Ton gesetzt: der erste Schlag steht jetzt am Anfang, der Cue mit ihm.
    position_s: c.position_s < 0 && c.position_s >= -djGridStartTolerance() ? 0 : c.position_s,
    loop_s: c.loop_s != null ? c.loop_s : null, active: !!c.active,
    color: c.color || djMemoryColor(),
  };
}

function djAdopt(ed, data) {
  ed.src = {grid: data.grid.source, phrases: data.phrases.source, cues: data.cues.source};
  ed.edited = {grid: data.grid.edited, phrases: data.phrases.edited, cues: data.cues.edited};
  ed.uncertain = !!data.phrases.uncertain;
  ed.rb = data.rekordbox || null;
  ed.palettes = data.palettes || ed.palettes;
  ed.locked = !!data.grid.locked;
  ed.locks = {grid: !!(data.locks && data.locks.grid), phrases: !!(data.locks && data.locks.phrases)};
  ed.segs = data.grid.segments || djSegmentsFromBeats(data.grid.beats || []);
  ed.phrases = (data.phrases.items || []).map(p => ({start: p.start, end: p.end, kind: p.kind, num: p.num || 0}));
  djRenumberPhrases(ed);
  ed.cues = (data.cues.items || []).map(djCueFromServer);
  ed.undo = []; ed.redo = [];
  ed.selCue = null; ed.selPhrase = -1;
  ed.dirty.clear();
  ed.loaded = true;
  ed.gridRef = null;
  djRebuildBeats(ed);
  djStoreTempo(ed);
  djRenderAll(ed);
  requestWavePaint();
}

async function djLoadDetail(ed) {
  try {
    const res = await fetch("/api/dj-wave?path=" + encodeURIComponent(ed.path));
    if (!res.ok) throw new Error(String(res.status));
    const head = name => res.headers.get(name);
    const data = new Uint8Array(await res.arrayBuffer());
    if (ed.closed) return;
    ed.detail = {data, rate: Number(head("X-Wave-Rate")) || 150, source: head("X-Wave-Source"),
                 style: head("X-Wave-Style"), lead: Number(head("X-Wave-Lead")) || 0,
                 fallback: head("X-Wave-Wanted") === "fallback"};
    ed.detailRev++;
    djRenderWaveSource(ed);
  } catch (err) {
    if (ed.closed) return;
    ed.overEntry.noAnim = true; ed.zoomEntry.noAnim = true;
    note(t("wave.detail_failed"), "soft");
  }
  requestWavePaint();
}

// Woher die Waveform wirklich stammt, kann vom Eingestellten abweichen.
function djRenderWaveSource(ed) {
  const el = ed.root.querySelector(".dj-wavesrc"), d = ed.detail;
  const style = t("dj.wave_style_" + d.style);
  const text = d.source === "tracktab" && d.fallback ? t("dj.wave_source_fallback", {style})
    : t("dj.wave_source", {source: d.source === "rekordbox" ? "Rekordbox" : "TrackTab", style});
  el.innerHTML = `<img alt="" src="${d.source === "rekordbox" ? appIconSrc("rekordbox") : "/icon-192.png"}">`;
  el.title = `${text}\n${t("dj.wave_source_title")}`;
  el.setAttribute("aria-label", text);
  el.hidden = false;
}

function djQueueSave(ed) {
  clearTimeout(ed.saveTimer);
  ed.saveTimer = setTimeout(() => djSave(ed), 500);
}

function djPayload(ed, fields) {
  const body = {path: ed.path};
  for (const f of fields) {
    if (f === "grid") {
      body.grid = {
        segments: ed.segs.map(seg => {
          const k = seg.t < 0 ? Math.ceil(-seg.t * seg.bpm / 60) : 0;
          return {...seg, t: seg.t + k * 60 / seg.bpm, n: ((seg.n - 1 + k) % 4) + 1};
        }),
        locked: ed.locked,
        beats: Array.from(ed.bt, (sec, i) => [Math.round(sec * 1000), ed.bn[i]]),
      };
    } else if (f === "phrases") {
      body.phrases = ed.phrases;
    } else {
      body.cues = ed.cues.map(c => ({kind: c.kind, slot: c.slot, name: c.name, position_s: c.position_s,
                                      loop_s: c.loop_s, color: c.color, ...(c.active ? {active: true} : {})}));
    }
  }
  return body;
}

async function djSave(ed, leaving) {
  clearTimeout(ed.saveTimer);
  if (!ed.dirty.size) return;
  const fields = [...ed.dirty];
  ed.dirty.clear();
  const body = JSON.stringify(djPayload(ed, fields));
  try {
    // keepalive ueberlebt das Schliessen der Seite, ist aber auf 64 KB begrenzt.
    const res = await fetch("/api/dj-track", {method: "POST", headers: {"Content-Type": "application/json"},
      body, keepalive: !!leaving && body.length < 60000});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
    if (data.path) ed.path = data.path;
  } catch (err) {
    for (const f of fields) ed.dirty.add(f);
    note(t("dj.save_failed", {error: err.message}), true);
  }
}

// ── Beatgrid rechnen ────────────────────────────────────

// Schlagliste [[ms, Nummer]] -> Abschnitte mit gleichem Tempo. Rekordbox rundet
// auf Millisekunden, deshalb mit Toleranz gegen eine laufend verfeinerte Gerade.
function djSegmentsFromBeats(beats) {
  // Ein auf den Audiobeginn gerueckter erster Schlag (rekordbox._grid_from_start())
  // ist kein Tempo: ohne ihn laeuft das Grid von selbst wieder bis zum Anfang zurueck.
  if (beats.length > 2 && beats[1][0] - beats[0][0] < beats[2][0] - beats[1][0] - 6) beats = beats.slice(1);
  if (!beats.length) return [];
  const tol = 6, segs = [];
  const close = (a, b) => {
    const k = b - a, span = beats[b][0] - beats[a][0];
    let bpm = k > 0 && span > 0 ? 60000 * k / span : (segs.length ? segs[segs.length - 1].bpm : 120);
    if (k > 0) {
      const round2 = Math.round(bpm * 100) / 100;
      bpm = Math.abs(k * 60000 / round2 - span) <= 3 ? round2 : Math.round(bpm * 1000) / 1000;
    }
    segs.push({t: beats[a][0] / 1000, bpm: Math.min(400, Math.max(20, bpm)), n: Math.min(4, Math.max(1, beats[a][1] || 1))});
  };
  let a = 0, gap = 0;
  for (let i = 1; i < beats.length; i++) {
    const k = i - a;
    if (k >= 2 && Math.abs(beats[i][0] - (beats[a][0] + k * gap)) > tol) {
      close(a, i - 1);
      a = i - 1;
      gap = beats[i][0] - beats[a][0];
    } else {
      gap = (beats[i][0] - beats[a][0]) / k;
    }
  }
  close(a, beats.length - 1);
  return segs;
}

// Phrasen haengen am Beatgrid: aendert es sich, wandern ihre Grenzen mit. Gerechnet in
// Schlaegen ab dem Beginn des Abschnitts, das bleibt beim Verschieben und Dehnen gleich.
// Liegt eine Grenze auf einem Schlag, rastet sie dort ein. Das Trackende bleibt fest.
function djCarryPhrases(ed) {
  const from = ed.gridRef, to = ed.segs;
  ed.gridRef = to.map(s => ({t: s.t, bpm: s.bpm}));
  if (!from || !ed.phrases.length) return;
  // Abschnitt dazu oder weg: nichts mitzurechnen, einrasten reicht (djSnapPhrases()).
  if (from.length !== to.length) { ed.phrasesCarried = true; return; }
  if (from.every((s, i) => s.t === to[i].t && s.bpm === to[i].bpm)) return;
  const move = sec => {
    if (sec >= ed.dur - 0.01) return sec;
    let i = 0;
    while (i + 1 < from.length && from[i + 1].t <= sec + 1e-6) i++;
    let beats = (sec - from[i].t) * from[i].bpm / 60;
    if (Math.abs(beats - Math.round(beats)) < 0.02) beats = Math.round(beats);
    return Math.max(0, Math.min(ed.dur, to[i].t + beats * 60 / to[i].bpm));
  };
  for (const p of ed.phrases) {
    p.start = move(p.start);
    p.end = Math.max(move(p.end), p.start + 0.01);
  }
  ed.phrasesCarried = true;
}

// Knapp vor dem Ton liegt der erste Schlag noch am Anfang, sonst fehlte er im Grid und
// ein Cue liesse sich nur auf den zweiten setzen. Gleiche Grenze wie
// rekordbox._GRID_START_TOLERANCE_MS, dort landet er ebenso auf dem Audiobeginn.
function djGridStartTolerance() { return 0.08; }

// Nach einer Grid-Aenderung liegen Beginn und Ende jeder Phrase auf einem Schlag. Vor dem
// ersten Schlag beginnt keine Phrase (wie in Rekordbox, davor liegt meist Stille), nach
// dem letzten bleibt die Grenze, wo sie ist (Trackende). So kurz geratene Phrasen fallen
// weg, der Nachbar schliesst an.
function djSnapPhrases(ed, beats) {
  const n = beats.length;
  if (!n) return;
  const nearest = sec => {
    if (sec < beats[0]) return beats[0];
    if (sec > beats[n - 1]) return sec;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (beats[m] < sec) lo = m + 1; else hi = m; }
    return lo && sec - beats[lo - 1] <= beats[lo] - sec ? beats[lo - 1] : beats[lo];
  };
  for (const p of ed.phrases) { p.start = nearest(p.start); p.end = nearest(p.end); }
  ed.phrases = ed.phrases.filter(p => p.end - p.start >= 0.05);
  if (ed.selPhrase >= ed.phrases.length) ed.selPhrase = -1;
}

// Frueheste Stelle fuer eine Phrasengrenze: der erste Schlag, ohne Grid der Trackanfang.
function djPhraseFloor(ed) { return ed.bt && ed.bt.length ? ed.bt[0] : 0; }

function djRebuildBeats(ed) {
  djCarryPhrases(ed);
  const times = [], nums = [], bars = [];
  const dur = ed.dur || 0, near = djGridStartTolerance();
  let bar = 0;
  ed.segs.forEach((seg, i) => {
    const gap = 60 / seg.bpm;
    const end = i + 1 < ed.segs.length ? ed.segs[i + 1].t : dur;
    // Vor dem ersten Abschnitt laeuft das Grid bis zum Trackanfang zurueck.
    const first = i === 0 ? -Math.floor((seg.t + near) / gap + 1e-9) : 0;
    for (let k = first; ; k++) {
      const sec = seg.t + k * gap;
      if (sec >= end - 1e-6) break;
      if (sec < -near) continue;
      const num = (((seg.n - 1 + k) % 4) + 4) % 4 + 1;
      if (num === 1) bar++;
      times.push(Math.max(0, sec)); nums.push(num); bars.push(bar);
    }
  });
  if (ed.phrasesCarried) djSnapPhrases(ed, times);
  // Tempowechsel in Reihenfolge, fuer den Titel. Kleine Schwankungen fallen heraus, ebenso
  // Abschnitte unter einem Takt: das ist ein nachjustiertes Raster, kein Tempo.
  ed.tempoSeq = [];
  ed.segs.forEach((seg, i) => {
    const len = (i + 1 < ed.segs.length ? ed.segs[i + 1].t : dur) - seg.t;
    if (len * seg.bpm / 60 < 4) return;
    if (!ed.tempoSeq.length || Math.abs(seg.bpm - ed.tempoSeq.at(-1)) >= 1) ed.tempoSeq.push(seg.bpm);
  });
  ed.tempoSpan = ed.segs.length
    ? [Math.min(...ed.segs.map(s => s.bpm)), Math.max(...ed.segs.map(s => s.bpm))] : null;
  ed.bt = Float64Array.from(times);
  ed.bn = Uint8Array.from(nums);
  ed.bb = Int32Array.from(bars);
}

// Index des ersten Schlags >= sec.
function djLowerBeat(ed, sec) {
  let lo = 0, hi = ed.bt.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (ed.bt[m] < sec) lo = m + 1; else hi = m; }
  return lo;
}
function djNearestBeat(ed, sec) {
  if (!ed.bt.length) return -1;
  const i = djLowerBeat(ed, sec);
  if (i <= 0) return 0;
  if (i >= ed.bt.length) return ed.bt.length - 1;
  return sec - ed.bt[i - 1] <= ed.bt[i] - sec ? i - 1 : i;
}
function djSnapBeat(ed, sec, ev) {
  if (!djPrefs().q || (ev && ev.altKey) || !ed.bt.length) return sec;
  return ed.bt[djNearestBeat(ed, sec)];
}
function djNearestBar(ed, sec) {
  let best = sec, dist = Infinity;
  const c = djLowerBeat(ed, sec);
  for (let i = Math.max(0, c - 4); i < Math.min(ed.bt.length, c + 4); i++) {
    if (ed.bn[i] === 1 && Math.abs(ed.bt[i] - sec) < dist) { dist = Math.abs(ed.bt[i] - sec); best = ed.bt[i]; }
  }
  return best;
}
// Teilen und Ziehen im Phrasenband rasten immer an den sichtbaren Rasterlinien, auch
// ohne Q: auf jedem Schlag, mit Shift nur auf Takt 1, mit der Wahltaste gar nicht.
function djSnapDrag(ed, sec, ev) {
  if (!ed.bt.length || (ev && ev.altKey)) return sec;
  return ev && ev.shiftKey ? djNearestBar(ed, sec) : ed.bt[djNearestBeat(ed, sec)];
}
function djSegIndexAt(ed, sec) {
  let idx = 0;
  for (let i = 0; i < ed.segs.length; i++) if (ed.segs[i].t <= sec + 1e-6) idx = i;
  return idx;
}
// Nummern der Folgeabschnitte, damit der Takt ueber einen Tempowechsel weiterzaehlt.
function djRenumberFrom(ed, i) {
  for (let j = i + 1; j < ed.segs.length; j++) {
    const prev = ed.segs[j - 1];
    const k = Math.round((ed.segs[j].t - prev.t) * prev.bpm / 60);
    ed.segs[j].n = (((prev.n - 1 + k) % 4) + 4) % 4 + 1;
  }
}

// ── Aenderungen, Rueckgaengig ───────────────────────────

function djState(ed) {
  return {segs: JSON.stringify(ed.segs), locked: ed.locked,
          phrases: JSON.stringify(ed.phrases), cues: JSON.stringify(ed.cues)};
}
function djPush(ed) {
  ed.undo.push(djState(ed));
  if (ed.undo.length > 200) ed.undo.shift();
  ed.redo = [];
}
function djTouched(ed, ...fields) {
  if (fields.includes("grid")) djRebuildBeats(ed);
  if (ed.phrasesCarried) {
    ed.phrasesCarried = false;
    if (!fields.includes("phrases")) fields.push("phrases");
  }
  if (fields.includes("phrases")) djRenumberPhrases(ed);
  for (const f of fields) {
    ed.dirty.add(f); ed.edited[f] = true;
    if (ed.rb && !ed.rb.pending.includes(f)) ed.rb.pending.push(f);
  }
  // Der Server sperrt beim Speichern genauso, siehe db.save_dj_track().
  for (const f of ["grid", "phrases"]) if (fields.includes(f)) ed.locks[f] = true;
  djQueueSave(ed);
  djRenderAll(ed);
  requestWavePaint();
}
function djRestore(ed, snap) {
  const now = djState(ed), fields = [];
  // Der Schnappschuss enthaelt die Phrasen schon passend zum Grid.
  ed.gridRef = null;
  if (snap.segs !== now.segs || snap.locked !== now.locked) {
    ed.segs = JSON.parse(snap.segs); ed.locked = snap.locked; fields.push("grid");
  }
  if (snap.phrases !== now.phrases) { ed.phrases = JSON.parse(snap.phrases); fields.push("phrases"); }
  if (snap.cues !== now.cues) { ed.cues = JSON.parse(snap.cues); fields.push("cues"); }
  if (ed.selPhrase >= ed.phrases.length) ed.selPhrase = -1;
  djTouched(ed, ...fields);
}
function djUndo(ed) {
  if (!ed.undo.length) return;
  ed.redo.push(djState(ed));
  djRestore(ed, ed.undo.pop());
}
function djRedo(ed) {
  if (!ed.redo.length) return;
  ed.undo.push(djState(ed));
  djRestore(ed, ed.redo.pop());
}

// ── Transport ───────────────────────────────────────────

function djPos(ed) { return ed.dragPos != null ? ed.dragPos : ed.pos; }

function djSeek(ed, sec) {
  const to = ed.dur ? djClampOver(ed, sec) : Math.max(0, sec);
  // Wer aus einem laufenden Loop herausspringt, verlaesst ihn.
  if (ed.loop && ed.loop.on && (to < ed.loop.start - 0.002 || to >= ed.loop.end)) {
    ed.loop = null;
    djApplyLoop(ed);
    djRenderTools(ed);
  }
  ed.pos = to;
  // Ausserhalb des Tracks steht nur die Anzeige, der Ton wartet am Rand.
  ed.audio.currentTime = Math.max(0, ed.dur ? Math.min(ed.dur, to) : to);
  ed.metroNext = 0;
  requestWavePaint();
}

function djPlay(ed) {
  if (ed.dur && ed.pos >= ed.dur) return;
  for (const [k, p] of players) if (p !== ed.zoomEntry) p.audio.pause();
  queueAudio.pause();
  // stopPlayerFor() nimmt den Eintrag bei Dateiaenderungen heraus.
  players.set(ed.path, ed.zoomEntry);
  activeKey = ed.path;
  ed.audio.play().catch(err => playFailed(ed.audio, err));
}
function djToggle(ed) {
  // Waehrend der Cue gehalten wird: Play rastet ein, der Track laeuft nach dem
  // Loslassen weiter.
  if (ed.cueHold) { ed.cueLatch = true; return; }
  if (ed.audio.paused) djPlay(ed); else ed.audio.pause();
}

function djSyncPlayIcon(ed) {
  const btn = ed.root.querySelector(".dj-play");
  if (!btn) return;
  btn.innerHTML = ed.audio.paused ? ICONS.play : ICONS.pause;
  btn.classList.toggle("loading", !!ed.audio.loading);
  const tap = ed.root.querySelector('[data-dj="tap"]');
  if (tap) tap.disabled = ed.audio.paused;
}

// Wie am CDJ: spielt der Track, zurueck zum Cue-Punkt und anhalten. Steht er, wird
// die Stelle zum Cue-Punkt und der Track laeuft, solange die Taste gehalten wird.
// Loslassen geht zurueck, ausser Play wurde dazu gedrueckt (djToggle()).
function djCueDown(ed) {
  if (ed.cueHold) return;
  if (!ed.audio.paused) { ed.audio.pause(); djSeek(ed, ed.tempCue); return; }
  const pos = djPos(ed);
  if (Math.abs(pos - ed.tempCue) > 0.003) {
    ed.tempCue = djSnapBeat(ed, pos);
    djSeek(ed, ed.tempCue);
  }
  ed.cueHold = true; ed.cueLatch = false;
  ed.root.querySelector(".dj-cuebtn").classList.add("on");
  djPlay(ed);
}
function djCueUp(ed) {
  if (!ed.cueHold) return;
  ed.cueHold = false;
  ed.root.querySelector(".dj-cuebtn").classList.remove("on");
  if (ed.holdSlot) { ed.holdSlot = 0; djRenderPads(ed); }
  if (ed.cueLatch) return;
  ed.audio.pause();
  djSeek(ed, ed.tempCue);
}

function djJump(ed, beats) {
  if (!ed.bt.length) { djSeek(ed, djPos(ed) + beats * 0.5); return; }
  const pos = djPos(ed), i = djNearestBeat(ed, pos);
  // Lag die Position neben dem Grid, zaehlt der Abstand mit: der Sprung soll das
  // Verhaeltnis zum Schlag behalten.
  const off = pos - ed.bt[i];
  const to = Math.max(0, Math.min(ed.bt.length - 1, i + beats));
  djSeek(ed, ed.bt[to] + off);
}

function djCueStops(ed) {
  return ed.cues.map(c => c.position_s).sort((a, b) => a - b);
}
function djPrevCue(ed) {
  const pos = djPos(ed) - 0.05, stops = djCueStops(ed).filter(x => x < pos);
  djSeek(ed, stops.length ? stops[stops.length - 1] : 0);
}
function djNextCue(ed) {
  const pos = djPos(ed) + 0.05, stop = djCueStops(ed).find(x => x > pos);
  if (stop != null) djSeek(ed, stop);
}

function djSync(ed) {
  const a = ed.audio;
  a.tick();
  // Angehalten ausserhalb des Tracks: die Position gehoert der Anzeige.
  if (a.paused && (ed.pos < 0 || (ed.dur && ed.pos > ed.dur))) return;
  const before = ed.pos;
  ed.pos = a.currentTime;
  if (!a.paused) djEnterActiveLoop(ed, before, ed.pos);
}
// Laeuft der Track ueber den Beginn eines Active Memory Loop, greift er von selbst. Nur beim
// Durchlaufen: ein Sprung ist kein Durchlauf, und wer den Loop verlassen hat, ist schon vorbei.
function djEnterActiveLoop(ed, before, pos) {
  if ((ed.loop && ed.loop.on) || pos <= before || pos - before > 0.5) return;
  const cue = ed.cues.find(c => c.kind === "memory" && c.active && c.loop_s != null && before < c.position_s && c.position_s <= pos);
  if (!cue) return;
  ed.loop = {start: cue.position_s, end: cue.loop_s, on: true};
  djApplyLoop(ed);
  djRenderTools(ed);
}

// Klick ueber die Audio-Uhr planen statt im Bildtakt auszuloesen: ein Bild
// schwankt um 16 ms, das hoert man.
function djMetronome(ed) {
  if (!DJ_AUDIO_CTX || !ed.bt.length) return;
  const pos = ed.audio.rawTime(), horizon = pos + 0.15;
  // Im Loop springt die Position zurueck, dann von vorn planen.
  if (pos < ed.metroNext - 0.3) ed.metroNext = 0;
  const stop = ed.loop && ed.loop.on && ed.loop.end != null ? ed.loop.end : Infinity;
  let i = djLowerBeat(ed, Math.max(pos, ed.metroNext));
  for (; i < ed.bt.length && ed.bt[i] < horizon && ed.bt[i] < stop; i++) {
    const at = DJ_AUDIO_CTX.currentTime + Math.max(0, ed.bt[i] - pos);
    // Rechteck weit ueber den Mitten und laenger ausklingend: ein leiser Sinus bei
    // 1 kHz (wie zuvor) geht in einem gemasterten Track unter, obwohl er spielt.
    const osc = DJ_AUDIO_CTX.createOscillator(), gain = DJ_AUDIO_CTX.createGain();
    osc.type = "square";
    osc.frequency.value = ed.bn[i] === 1 ? 3200 : 2400;
    gain.gain.setValueAtTime(0.45, at);
    gain.gain.exponentialRampToValueAtTime(0.001, at + 0.07);
    osc.connect(gain).connect(DJ_AUDIO_CTX.destination);
    osc.start(at); osc.stop(at + 0.08);
    ed.metroNext = ed.bt[i] + 0.001;
  }
}

function djSyncLabels(ed) {
  const pos = djPos(ed), tenth = Math.floor(pos * 10);
  // Hat der Track begonnen, kommt die zweite Zeit dazu und zaehlt herunter.
  const running = !ed.audio.paused || pos > 0.05;
  const timeCell = djRowCell(ed, "timeCell", ".ducell");
  if ((tenth !== ed._tenth || running !== ed._running) && timeCell) {
    ed._tenth = tenth; ed._running = running;
    timeCell.innerHTML = running
      ? `<b title="${esc(t("dj.elapsed"))}">${djFmtTenth(pos)}</b> <span class="dj-remain" title="${esc(t("dj.remaining"))}">-${djFmtTenth(Math.max(0, ed.dur - pos))}</span>`
      : fmtTime(ed.r.du);
  }
  const seg = ed.segs.length ? djSegIndexAt(ed, pos) : -1;
  const bpm = seg >= 0 ? ed.segs[seg].bpm : 0;
  // Die Liste aendert sich nur, wenn das Beatgrid bearbeitet wurde und wirklich abweicht.
  const shown = ed.edited.grid ? djEditedBpm(bpm, ed.r.bp) : 0;
  const cellKey = `${shown}|${ed.tempoSpan}`;
  const bpmCell = djRowCell(ed, "bpmCell", ".bpmcell");
  if (cellKey !== ed._bpmCell && bpmCell) {
    ed._bpmCell = cellKey;
    bpmCell.innerHTML = bpmCellHtml(ed.r.bp, shown, ed.tempoSpan, djGridSourceKey(ed));
  }
  if (seg !== ed._segShown || bpm !== ed._bpmShown) {
    ed._segShown = seg; ed._bpmShown = bpm;
    const input = ed.root.querySelector(".dj-bpminput");
    if (input && document.activeElement !== input) input.value = seg >= 0 ? djFmtBpm(bpm) : "";
    const part = ed.root.querySelector(".dj-seginfo");
    if (part) part.textContent = ed.segs.length > 1 ? t("dj.segment_of", {i: seg + 1, n: ed.segs.length}) : "";
  }
}

// ── Cues ────────────────────────────────────────────────

function djHot(ed, slot) { return ed.cues.find(c => c.kind === "hot" && c.slot === slot) || null; }
function djCueById(ed, id) { return ed.cues.find(c => c.id === id) || null; }

function djAddHot(ed, slot, ev) {
  djPush(ed);
  const pos = djSnapBeat(ed, djPos(ed), ev);
  ed.cues = ed.cues.filter(c => !(c.kind === "hot" && c.slot === slot));
  const cue = {id: djNewId(), kind: "hot", slot, label: "ABCDEFGH"[slot - 1], name: "", position_s: pos,
               loop_s: ed.loop && ed.loop.on && ed.loop.end != null ? ed.loop.end : null,
               color: djHotColor()};
  if (cue.loop_s != null) cue.position_s = ed.loop.start;
  ed.cues.push(cue);
  djSelectCue(ed, cue.id);
  djTouched(ed, "cues");
}

// Cue und Phrase sind nie gleichzeitig ausgewaehlt: Entf loescht genau das zuletzt
// Gewaehlte (sonst traf es einen eben gesetzten Hot Cue statt der angeklickten Phrase).
function djSelectCue(ed, id) {
  ed.selCue = id;
  ed.selPhrase = -1;
}

function djAddMemory(ed, ev) {
  const hasLoop = ed.loop && ed.loop.on && ed.loop.end != null;
  const pos = hasLoop ? ed.loop.start : djSnapBeat(ed, djPos(ed), ev);
  if (ed.cues.some(c => c.kind === "memory" && Math.abs(c.position_s - pos) < 0.002 && (c.loop_s != null) === !!hasLoop)) return;
  if (ed.cues.filter(c => c.kind === "memory").length >= djMaxMemory()) {
    note(t("dj.memory_limit", {max: djMaxMemory()}), "soft");
    return;
  }
  djPush(ed);
  const cue = {id: djNewId(), kind: "memory", slot: null, label: "", name: "", position_s: pos,
               loop_s: hasLoop ? ed.loop.end : null, active: !!hasLoop, color: hasLoop ? djLoopColor(true) : djMemoryColor()};
  ed.cues.push(cue);
  djSelectCue(ed, cue.id);
  if (ed.side !== "memory") djSetSide(ed, "memory");
  djTouched(ed, "cues");
}

// Klick aufs Loop-Symbol schaltet zwischen aktivem und normalem Memory Loop um.
function djToggleLoopKind(ed, id) {
  const cue = djCueById(ed, id);
  if (!cue || cue.kind !== "memory" || cue.loop_s == null) return;
  djPush(ed);
  cue.active = !cue.active;
  cue.color = djLoopColor(cue.active);
  djTouched(ed, "cues");
}

function djDeleteCue(ed, id) {
  djPush(ed);
  ed.cues = ed.cues.filter(c => c.id !== id);
  if (ed.selCue === id) ed.selCue = null;
  djTouched(ed, "cues");
}

function djGoCue(ed, cue) {
  djSelectCue(ed, cue.id);
  djSeek(ed, cue.position_s);
  // Wie am Player: ein angesprungener Cue wird zum Cue-Punkt.
  ed.tempCue = cue.position_s;
  if (cue.loop_s != null) {
    ed.loop = {start: cue.position_s, end: cue.loop_s, on: true};
    djApplyLoop(ed);
  }
  djRenderPads(ed); djRenderSide(ed); djRenderTools(ed);
}

// Wie die Cue-Taste: steht der Track, spielt er ab dem Hot Cue, solange gehalten
// wird, und geht beim Loslassen dorthin zurueck (djCueUp(), Play rastet ein). Laeuft
// er schon, springt er nur hin und spielt weiter.
function djHotDown(ed, slot, ev) {
  const cue = djHot(ed, slot);
  // Shift loescht wie am Player. Cmd+Zahl geht an den Browser (Tab wechseln).
  if (ev.shiftKey) { if (cue) djDeleteCue(ed, cue.id); return; }
  if (!cue) { djAddHot(ed, slot, ev); return; }
  const hold = ed.cueHold || ed.audio.paused;
  if (hold) {
    if (!ed.cueHold) ed.cueLatch = false;
    ed.cueHold = true;
    ed.holdSlot = slot;
  }
  djGoCue(ed, cue);
  if (hold && ed.audio.paused) djPlay(ed);
}
function djHotUp(ed, slot) {
  if (!ed.closed && ed.holdSlot === slot) djCueUp(ed);
}

// ── Loops ───────────────────────────────────────────────

// Der Loop laeuft im Deck selbst, also ohne Sprung und ohne Luecke.
function djApplyLoop(ed) {
  const l = ed.loop;
  ed.audio.setLoop(l && l.on && l.end != null ? {start: l.start, end: l.end} : null);
}

function djLoopIn(ed, ev) {
  const start = djSnapBeat(ed, djPos(ed), ev);
  ed.loop = {start, end: null, on: false};
  djApplyLoop(ed);
  djRenderTools(ed); requestWavePaint();
}
function djLoopOut(ed, ev) {
  if (!ed.loop) return;
  const end = djSnapBeat(ed, djPos(ed), ev);
  if (end <= ed.loop.start + 0.01) { note(t("dj.loop_invalid"), "soft"); return; }
  ed.loop.end = end; ed.loop.on = true;
  djApplyLoop(ed);
  djRenderTools(ed); requestWavePaint();
}
function djLoopBeats(ed, beats) {
  const pos = djPos(ed);
  const i = ed.bt.length ? djNearestBeat(ed, pos) : -1;
  const start = Math.max(0, i >= 0 ? ed.bt[i] : pos);
  let end;
  if (i >= 0 && beats >= 1 && i + beats < ed.bt.length) end = ed.bt[i + beats];
  else if (i >= 0 && i + 1 < ed.bt.length) end = start + beats * (ed.bt[i + 1] - start);
  else end = start + beats * (ed.segs.length ? 60 / ed.segs[djSegIndexAt(ed, pos)].bpm : 0.5);
  if (ed.dur > start) end = Math.min(end, ed.dur);
  // Gleiche Laenge noch einmal: Schleife aus. Nur ueber die Laenge, die Position ist
  // im laufenden Loop laengst weitergewandert.
  const on = ed.loop && ed.loop.on;
  if (on && (ed.loop.beats === beats || Math.abs(ed.loop.end - ed.loop.start - (end - start)) < 0.01)) {
    ed.loop = null;
  } else {
    ed.loop = {start, end, on: true, beats};
    djFlashLoopHud(ed, djLoopLabel(beats));
  }
  djApplyLoop(ed);
  djRenderTools(ed); requestWavePaint();
}
function djLoopExit(ed) { ed.loop = null; djApplyLoop(ed); djRenderTools(ed); requestWavePaint(); }

// ── Beatgrid bearbeiten ─────────────────────────────────

function djGridGuard(ed) {
  if (ed.locked) { note(t("dj.grid_locked"), "soft"); return false; }
  return true;
}
function djSetBpm(ed, bpm) {
  if (!(bpm >= 20 && bpm <= 400)) { note(t("dj.bpm_invalid"), "soft"); djRenderTools(ed); return; }
  if (!djGridGuard(ed)) return;
  djPush(ed);
  if (!ed.segs.length) ed.segs = [{t: djPos(ed), bpm, n: 1}];
  else {
    const i = djSegIndexAt(ed, djPos(ed));
    ed.segs[i].bpm = Math.round(bpm * 1000) / 1000;
    djRenumberFrom(ed, i);
  }
  djTouched(ed, "grid");
}
function djScaleBpm(ed, factor) {
  if (!ed.segs.length) return;
  djSetBpm(ed, ed.segs[djSegIndexAt(ed, djPos(ed))].bpm * factor);
}
// Wie das GRID-Feld von Rekordbox: nachjustiert wird das Raster, das Tempo folgt
// daraus. Gilt fuer den ganzen Track oder ab der Abspielposition. Liefert den ersten
// betroffenen Abschnitt, "ab hier" beginnt dafuer am naechsten Schlag einen eigenen.
function djGridFrom(ed) {
  if (!djPrefs().gridHere) return 0;
  const b = djNearestBeat(ed, djPos(ed)), at = ed.bt[b], i = djSegIndexAt(ed, at);
  if (Math.abs(ed.segs[i].t - at) < 0.002) return i;
  ed.segs.splice(i + 1, 0, {t: at, bpm: ed.segs[i].bpm, n: ed.bn[b]});
  // Das Raster bleibt dabei gleich. Die Phrasen muessen den neuen Abschnitt kennen.
  djRebuildBeats(ed);
  return i + 1;
}
function djShiftGrid(ed, sec) {
  if (!ed.segs.length || !djGridGuard(ed)) return;
  djPush(ed);
  djShiftSegs(ed, ed.segs, sec, djGridFrom(ed));
  djTouched(ed, "grid");
}
// Ein Abschnitt darf hier vor dem Trackanfang beginnen, djRebuildBeats() laesst die
// Schlaege davor weg und djCarryPhrases() rechnet von seinem Beginn aus. Gespeichert
// wird er ab dem ersten Schlag im Track (djPayload()).
function djShiftSegs(ed, base, sec, from) {
  ed.segs = base.map((s, j) => (j < from ? {...s} : {...s, t: s.t + sec}));
}
function djGridToPlayhead(ed) {
  if (!ed.bt.length || !djGridGuard(ed)) return;
  djShiftGrid(ed, djPos(ed) - ed.bt[djNearestBeat(ed, djPos(ed))]);
}
// Beat-Intervalle weiter oder enger, der erste Schlag jedes Abschnitts bleibt stehen.
function djStretchIntervals(ed, delta) {
  if (!ed.segs.length || !djGridGuard(ed)) return;
  djPush(ed);
  const from = djGridFrom(ed);
  for (let j = from; j < ed.segs.length; j++) {
    ed.segs[j].bpm = Math.min(400, Math.max(20, Math.round((ed.segs[j].bpm + delta) * 1000) / 1000));
  }
  djRenumberFrom(ed, from);
  djTouched(ed, "grid");
}
// Der Schlag naechst der Position wandert genau auf die Position und wird Takt 1.
// Gilt wie die Knoepfe daneben fuer das ganze Grid oder ab hier.
function djSetDownbeat(ed) {
  if (!ed.segs.length || !djGridGuard(ed)) return;
  djPush(ed);
  const pos = djPos(ed), offset = pos - ed.bt[djNearestBeat(ed, pos)];
  djShiftSegs(ed, ed.segs, offset, djGridFrom(ed));
  djRebuildBeats(ed);
  const beat = ed.bt[djNearestBeat(ed, pos)];
  const i = djSegIndexAt(ed, beat), seg = ed.segs[i];
  const k = Math.round((beat - seg.t) * seg.bpm / 60);
  seg.n = (((-k) % 4) + 4) % 4 + 1;
  djRenumberFrom(ed, i);
  djTouched(ed, "grid");
}
function djAddSegment(ed) {
  if (!ed.segs.length || !djGridGuard(ed)) return;
  const b = djNearestBeat(ed, djPos(ed)), at = ed.bt[b];
  const i = djSegIndexAt(ed, at);
  if (Math.abs(ed.segs[i].t - at) < 0.002) { note(t("dj.segment_exists"), "soft"); return; }
  djPush(ed);
  ed.segs.splice(i + 1, 0, {t: at, bpm: ed.segs[i].bpm, n: ed.bn[b]});
  djTouched(ed, "grid");
}
function djRemoveSegment(ed) {
  if (ed.segs.length < 2 || !djGridGuard(ed)) return;
  const i = djSegIndexAt(ed, djPos(ed));
  djPush(ed);
  ed.segs.splice(i, 1);
  djRenumberFrom(ed, Math.max(0, i - 1));
  djTouched(ed, "grid");
}
function djTap(ed) {
  if (ed.audio.paused || !djGridGuard(ed)) return;
  const now = performance.now();
  if (ed.taps.length && now - ed.taps[ed.taps.length - 1] > 2500) { ed.taps = []; ed.tapPushed = false; }
  ed.taps.push(now);
  if (ed.taps.length < 4) return;
  const bpm = Math.round(60000 * (ed.taps.length - 1) / (now - ed.taps[0]) * 10) / 10;
  if (!(bpm >= 20 && bpm <= 400)) return;
  if (!ed.tapPushed) { djPush(ed); ed.tapPushed = true; }
  if (!ed.segs.length) ed.segs = [{t: djPos(ed), bpm, n: 1}];
  else {
    const i = djSegIndexAt(ed, djPos(ed));
    ed.segs[i].bpm = bpm;
    djRenumberFrom(ed, i);
  }
  djTouched(ed, "grid");
}
function djToggleMetro(ed) {
  ed.metro = !ed.metro;
  // Der Track weicht dem Metronom etwas, sonst bleibt es neben einem lauten Master schwach.
  ed.audio.duck = ed.metro ? 0.6 : 1;
  ed.audio.tick();
  // Eigener Takt statt der Bildschleife: die steht, sobald die Wellenform aus dem Bild
  // gescrollt ist, und dann bliebe das Metronom stumm.
  clearInterval(ed.metroTimer);
  if (ed.metro) ed.metroTimer = setInterval(() => { if (!ed.audio.paused) { ed.audio.tick(); djMetronome(ed); } }, 40);
  if (ed.metro && !DJ_AUDIO_CTX) {
    DJ_AUDIO_CTX = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (ed.metro && DJ_AUDIO_CTX.state === "suspended") DJ_AUDIO_CTX.resume();
  ed.metroNext = 0;
  djRenderTools(ed);
}

// ── Phrasen bearbeiten ──────────────────────────────────

function djSortPhrases(ed) { ed.phrases.sort((a, b) => a.start - b.start); }
function djPhraseAt(ed, sec) { return ed.phrases.findIndex(p => sec >= p.start && sec < p.end); }

// Ohne Phrasen gilt der ganze Track (ab dem ersten Schlag) als eine: Teilen legt beide
// Haelften an, statt nur ab der Abspielposition -- sonst bliebe der Anfang ohne Phrase.
function djCutPhrase(ed, ev) {
  const floor = djPhraseFloor(ed);
  const at = Math.max(floor, djSnapDrag(ed, djPos(ed), ev));
  djPush(ed);
  const i = djPhraseAt(ed, at);
  if (!ed.phrases.length) {
    if (ed.dur - floor < 0.05) { ed.undo.pop(); return; }
    if (at - floor >= 0.05) ed.phrases.push({start: floor, end: at, kind: "up", num: 0});
    if (ed.dur - at >= 0.05) ed.phrases.push({start: at, end: ed.dur, kind: "up", num: 0});
  } else if (i < 0) {
    const nextP = ed.phrases.find(p => p.start > at);
    const end = nextP ? nextP.start : ed.dur;
    if (end - at < 0.05) { ed.undo.pop(); return; }
    ed.phrases.push({start: at, end, kind: "up", num: 0});
  } else {
    const p = ed.phrases[i];
    if (at - p.start < 0.05 || p.end - at < 0.05) { ed.undo.pop(); note(t("dj.cut_on_boundary"), "soft"); return; }
    ed.phrases.splice(i + 1, 0, {start: at, end: p.end, kind: p.kind, num: p.num});
    p.end = at;
  }
  djSortPhrases(ed);
  ed.selPhrase = djPhraseAt(ed, at);
  ed.selCue = null;
  djTouched(ed, "phrases");
}
// Erste Phrase von Hand: ganzer Track ab dem ersten Schlag, danach mit Teilen (X) zerlegen.
function djAddPhrase(ed) {
  if (ed.phrases.length) return;
  const floor = djPhraseFloor(ed);
  if (ed.dur - floor < 0.05) return;
  djPush(ed);
  ed.phrases.push({start: floor, end: ed.dur, kind: "up", num: 0});
  ed.selPhrase = 0;
  ed.selCue = null;
  djTouched(ed, "phrases");
}
function djSetPhraseKind(ed, kind) {
  const p = ed.phrases[ed.selPhrase];
  if (!p) { note(t("dj.select_phrase"), "soft"); return; }
  djPush(ed);
  p.kind = kind;
  djTouched(ed, "phrases");
}
// Die Luecke uebernimmt der Vorgaenger, beim ersten Eintrag der Nachfolger.
function djDeletePhrase(ed) {
  const i = ed.selPhrase, p = ed.phrases[i];
  if (!p) { note(t("dj.select_phrase"), "soft"); return; }
  djPush(ed);
  const prev = ed.phrases[i - 1], next = ed.phrases[i + 1];
  if (prev && Math.abs(prev.end - p.start) < 0.01) prev.end = p.end;
  else if (next && Math.abs(next.start - p.end) < 0.01) next.start = p.start;
  ed.phrases.splice(i, 1);
  ed.selPhrase = -1;
  djTouched(ed, "phrases");
}
function djClearPhrases(ed) {
  if (!ed.phrases.length) return;
  djPush(ed);
  ed.phrases = [];
  ed.selPhrase = -1;
  djTouched(ed, "phrases");
}

// ── Oberflaeche zeichnen ────────────────────────────────

function djRenderAll(ed) {
  ed._segShown = undefined;
  djRenderChrome(ed); djRenderPads(ed); djRenderTools(ed); djRenderSide(ed);
}

function djRenderChrome(ed) {
  const prefs = djPrefs();
  ed.root.querySelectorAll(".dj-mode").forEach(b => b.classList.toggle("on", b.dataset.mode === ed.mode));
  ed.root.querySelectorAll(".dj-sidetab").forEach(b => b.classList.toggle("on", b.dataset.side === ed.side));
  ed.root.querySelector(".dj-q").classList.toggle("on", prefs.q);
  ed.root.querySelector('[data-dj="undo"]').disabled = !ed.undo.length;
  ed.root.querySelector('[data-dj="redo"]').disabled = !ed.redo.length;
  ed.root.querySelectorAll('.dj-lockbtn').forEach(lock => {
    const field = lock.dataset.field, on = !!(ed.locks && ed.locks[field]), name = t("dj.field_" + field);
    lock.classList.toggle("on", on);
    lock.innerHTML = `${djIcon(on ? "shield-lock" : "shield")}<span>${esc(name)}</span>`;
    lock.title = t(on ? "dj.analysis_lock_on" : "dj.analysis_lock_off", {field: name});
  });
  djSyncPlayIcon(ed);
}

function djRenderPads(ed) {
  const host = ed.root.querySelector(".dj-pads");
  let html = "";
  for (let slot = 1; slot <= 8; slot++) {
    const cue = djHot(ed, slot), letter = "ABCDEFGH"[slot - 1];
    html += cue
      ? `<button class="dj-pad on${ed.selCue === cue.id ? " sel" : ""}${ed.holdSlot === slot ? " held" : ""}" data-slot="${slot}" style="--c:${djRgb(cue.color)}" title="${esc(t("dj.pad_filled_title", {slot: letter, key: slot}))}">
          <b>${letter}</b><span>${esc(cue.name || djFmt(cue.position_s))}</span>${cue.loop_s != null ? djIcon("repeat") : ""}
          <i data-dj="cuedel" data-id="${cue.id}" title="${esc(t("dj.delete_cue"))}">${djIcon("x")}</i></button>`
      : `<button class="dj-pad" data-slot="${slot}" title="${esc(t("dj.pad_empty_title", {slot: letter, key: slot}))}"><b>${letter}</b></button>`;
  }
  host.innerHTML = html;
}

function djToolBtn(action, content, title, extra = "") {
  return `<button class="dj-btn ${extra.cls || ""}" data-dj="${action}" ${extra.attrs || ""} title="${esc(title)}">${content}</button>`;
}

function djRenderTools(ed) {
  const host = ed.root.querySelector(".dj-tools");
  if (host.contains(document.activeElement) && document.activeElement.matches("input, select")) return;
  djRenderChrome(ed);
  if (ed.mode === "grid") host.innerHTML = djGridTools(ed);
  else if (ed.mode === "phrase") host.innerHTML = djPhraseTools(ed);
  else if (ed.mode === "jump") host.innerHTML = djJumpTools();
  else host.innerHTML = djCueTools(ed);
  ed._segShown = undefined;
}

function djCueTools(ed) {
  const loop = ed.loop;
  const lens = djLoopKeys().slice().reverse().map(([key, n]) => {
    const on = loop && loop.on && ed.bt.length
      && (loop.beats === n || Math.abs((loop.end - loop.start) - n * 60 / (ed.segs[djSegIndexAt(ed, loop.start)] || {bpm: 120}).bpm) < 0.005);
    return djToolBtn("looplen", djLoopLabel(n), t("dj.loop_len_title", {beats: djLoopLabel(n), key: key.toUpperCase()}), {cls: on ? "on" : "", attrs: `data-n="${n}"`});
  }).join("");
  return `${djToolBtn("memory", `${djIcon("bookmark")} ${esc(t("dj.memory_set"))}`, t("dj.memory_title"), {cls: "wide"})}
    ${AUTOCUE_AVAILABLE ? djToolBtn("autocue", `${djIcon("square-sparkles")} ${esc(t("dj.autocue"))}`, t("dj.autocue_title"),
      {cls: "wide", attrs: ed.autocueBusy ? "disabled" : ""}) : ""}
    <span class="dj-sep"></span>
    <span class="dj-toollabel">${djIcon("repeat")} ${esc(t("dj.loop"))}</span>
    ${djToolBtn("loopin", esc(t("dj.loop_in")), t("dj.loop_in_title"), {cls: loop ? "on" : ""})}
    ${djToolBtn("loopout", esc(t("dj.loop_out")), t("dj.loop_out_title"), {cls: loop && loop.end != null ? "on" : ""})}
    ${lens}
    ${loop ? djToolBtn("loopexit", djIcon("x"), t("dj.loop_exit")) : ""}`;
}

// Tippen ergibt nur zum laufenden Track Sinn.
function djTapBtn(ed) {
  return djToolBtn("tap", "TAP", t("dj.tap_title"), {attrs: ed.audio.paused ? "disabled" : ""});
}

function djGridTools(ed) {
  if (!ed.segs.length) {
    return `<span class="dj-hint">${esc(t("dj.no_grid"))}</span>
      ${PHRASES_AVAILABLE ? djToolBtn("measure", `${ICONS.analysePhrases} ${esc(t("dj.measure"))}`, t("dj.measure_title"), {cls: "wide"}) : ""}
      ${djToolBtn("newgrid", esc(t("dj.new_grid")), t("dj.new_grid_title"), {cls: "wide"})}
      ${djTapBtn(ed)}`;
  }
  const shift = (ms, icon) => djToolBtn("shift", djIcon(icon), t(ms < 0 ? "dj.shift_left" : "dj.shift_right", {ms: Math.abs(ms)}), {attrs: `data-ms="${ms}"`});
  const interval = (d, icon) => djToolBtn("interval", djIcon(icon), t(d < 0 ? "dj.interval_expand" : "dj.interval_shrink", {bpm: Math.abs(d)}), {attrs: `data-d="${d}"`});
  const scope = (here, icon, title) => djToolBtn("gridscope", djIcon(icon), t(title), {cls: !!djPrefs().gridHere === here ? "on" : "", attrs: `data-here="${here ? 1 : 0}"`});
  const seg = djSegIndexAt(ed, djPos(ed));
  return `<input class="dj-bpminput" type="number" step="0.01" min="20" max="400" value="${djFmtBpm(ed.segs[seg].bpm)}" title="${esc(t("dj.bpm_input_title"))}" ${ed.locked ? "disabled" : ""}>
    ${djTapBtn(ed)}
    ${djToolBtn("half", "×½", t("dj.half_title"))}
    ${djToolBtn("double", "×2", t("dj.double_title"))}
    <span class="dj-sep"></span>
    ${scope(false, "move-horizontal", "dj.scope_whole")}${scope(true, "arrow-right-from-line", "dj.scope_here")}
    <span class="dj-sep"></span>
    ${shift(-10, "chevrons-left")}${shift(-1, "chevron-left")}${shift(1, "chevron-right")}${shift(10, "chevrons-right")}
    <span class="dj-sep"></span>
    ${interval(0.1, "chevrons-right-left")}${interval(0.01, "fold-horizontal")}${interval(-0.01, "unfold-horizontal")}${interval(-0.1, "chevrons-left-right")}
    <span class="dj-sep"></span>
    ${djToolBtn("gridhere", djIcon("locate-fixed"), t("dj.grid_to_playhead"))}
    ${djToolBtn("downbeat", `${djIcon("flag-triangle-right")} ${esc(t("dj.downbeat"))}`, t("dj.downbeat_title"), {cls: "wide"})}
    ${djToolBtn("addseg", `${djIcon("timer")} ${esc(t("dj.add_segment"))}`, t("dj.add_segment_title"), {cls: "wide"})}
    ${ed.segs.length > 1 ? djToolBtn("delseg", djIcon("x"), t("dj.remove_segment")) : ""}
    <span class="dj-seginfo dj-toollabel">${ed.segs.length > 1 ? esc(t("dj.segment_of", {i: seg + 1, n: ed.segs.length})) : ""}</span>
    <span class="dj-sep"></span>
    ${djToolBtn("metro", djIcon("metronome"), t("dj.metronome_title"), {cls: ed.metro ? "on" : ""})}
    ${djToolBtn("lock", djIcon(ed.locked ? "lock" : "lock-open"), t("dj.lock_title"), {cls: ed.locked ? "on" : ""})}`;
}

function djJumpTools() {
  const keys = Object.fromEntries(djJumpKeys().map(([k, n]) => [n, k.toUpperCase()]));
  const jump = n => djToolBtn("jump", (n < 0 ? "−" : "+") + Math.abs(n),
    t("dj.jump_title", {beats: Math.abs(n), key: keys[Math.abs(n)], arrow: n < 0 ? "←" : "→"}), {attrs: `data-n="${n}"`});
  return `${djToolBtn("prevcue", djIcon("skip-back"), t("dj.prev_cue"))}
    ${[-64, -32, -16, -8, -4].map(jump).join("")}
    <span class="dj-sep"></span>
    ${[4, 8, 16, 32, 64].map(jump).join("")}
    ${djToolBtn("nextcue", djIcon("skip-forward"), t("dj.next_cue"))}`;
}

function djPhraseTools(ed) {
  const p = ed.phrases[ed.selPhrase];
  const kinds = djPhraseKinds().map(k =>
    `<button class="dj-kind${p && p.kind === k ? " on" : ""}" data-dj="kind" data-kind="${k}" style="--c:var(--phrase-${k});color:${theme().phrase[k + "Label"]}">${esc(t("phrase." + k))}</button>`).join("");
  return `${djToolBtn("cut", `${djIcon("scissors")} ${esc(t("dj.cut"))}`, t("dj.cut_title"), {cls: "wide"})}
    <span class="dj-sep"></span>
    ${kinds}
    <span class="dj-sep"></span>
    ${djToolBtn("delphrase", `${djIcon("delete")} ${esc(t("dj.delete_phrase"))}`, t("dj.delete_phrase_title"), {cls: "wide"})}
    ${djToolBtn("clearphrases", `${djIcon("trash")} ${esc(t("dj.clear_phrases"))}`, t("dj.clear_phrases_title"), {cls: "wide"})}`;
}

// Leere Phrasenliste: Hinweis plus Einstieg zum Anlegen und Messen. Gemessen wird auf dem
// vorhandenen Grid (_measure_phrases()), auch wenn sich selbst kein Tempo finden laesst.
function djPhrasesEmptyHTML(ed) {
  const measure = PHRASES_AVAILABLE
    ? `<button class="dj-addrow" data-dj="measure" data-what="phrases"${ed.locks.phrases ? ` disabled title="${esc(t("dj.analysis_locked"))}"` : ""}>${ICONS.analysePhrases} ${esc(t("bulk.dj_phrases"))}</button>`
    : "";
  return `<div class="dj-empty">${esc(t("dj.phrases_empty"))}</div>
    <button class="dj-addrow" data-dj="addphrase" title="${esc(t("dj.add_phrase_title"))}">${djIcon("plus")} ${esc(t("dj.add_phrase"))}</button>
    ${measure}`;
}

function djCueRow(ed, cue, letter) {
  const isMemory = cue.kind === "memory", hot = isMemory ? djHotAt(ed, cue.position_s) : null;
  const loopMark = cue.loop_s == null ? "" : isMemory
    ? `<span class="dj-loopmark ${cue.active ? "active" : "normal"}" data-dj="looptoggle" data-id="${cue.id}" title="${esc(t(cue.active ? "dj.loop_active_title" : "dj.loop_normal_title", {time: djFmt(cue.loop_s)}))}">${djIcon("repeat")}</span>`
    : `<span class="dj-loopmark" title="${esc(t("dj.loop_until", {time: djFmt(cue.loop_s)}))}">${djIcon("repeat")}</span>`;
  const near = ed.nearCues && ed.nearCues.has(String(cue.id));
  return `<div class="dj-row${ed.selCue === cue.id ? " sel" : ""}${near ? " near" : ""}" data-dj="cuejump" data-id="${cue.id}">
    <span class="dj-dot" data-dj="cuecolor" data-id="${cue.id}" title="${esc(t("dj.color_title"))}" style="--c:${djRgb(cue.color)};background:var(--c)">${letter || ""}</span>
    <span class="dj-time">${djFmt(cue.position_s)}</span>
    ${hot ? `<span class="dj-dot dj-samehot" title="${esc(t("dj.memory_same_hot", {slot: hot.label}))}" style="background:${djRgb(hot.color)}">${esc(hot.label)}</span>` : ""}
    ${loopMark}
    <input class="dj-name" data-cuename="${cue.id}" value="${esc(cue.name || "")}" placeholder="${esc(t("dj.name_placeholder"))}">
    <i data-dj="cuedel" data-id="${cue.id}" title="${esc(t("dj.delete_cue"))}">${djIcon("x")}</i>
  </div>`;
}

function djSourceLabel(field, ed) {
  if (ed.edited[field]) return t("dj.source_edited");
  const src = ed.src[field];
  return src ? t("dj.source_" + src) : t("dj.source_none");
}

// Memory-Liste (Hoechstzahl Plaetze); ihre Hoehe bestimmt die der ganzen Seitenleiste.
function djMemoryHTML(ed) {
  let html = "";
    const list = ed.cues.filter(c => c.kind === "memory").sort((a, b) => a.position_s - b.position_s);
    // Immer alle Plaetze: die Zeilen bleiben stehen, nach dem Loeschen rueckt der naechste
    // unter den Zeiger und liesse sich mit demselben Klick gleich mitloeschen.
    html = list.map(c => djCueRow(ed, c)).join("");
    for (let n = list.length + 1; n <= djMaxMemory(); n++) {
      html += `<div class="dj-row off" data-dj="memory"><span class="dj-dot">${n}</span><span class="dj-time">${esc(t("dj.memory_empty_slot"))}</span></div>`;
    }
    html = `<button class="dj-addrow" data-dj="memory"${list.length >= djMaxMemory() ? " disabled" : ""}>${djIcon("bookmark")} ${esc(t("dj.memory_set"))} <small>${list.length}/${djMaxMemory()}</small> <kbd>M</kbd></button>` + html;
  return html;
}

function djRenderSide(ed) {
  const host = ed.root.querySelector(".dj-sidebody");
  if (host.contains(document.activeElement) && document.activeElement.matches("input")) return;
  let html = "";
  if (ed.side === "memory") {
    html = djMemoryHTML(ed);
  } else if (ed.side === "hot") {
    for (let slot = 1; slot <= 8; slot++) {
      const cue = djHot(ed, slot), letter = "ABCDEFGH"[slot - 1];
      html += cue ? djCueRow(ed, cue, letter)
        : `<div class="dj-row off" data-dj="pad" data-slot="${slot}"><span class="dj-dot">${letter}</span><span class="dj-time">${esc(t("dj.hot_empty"))}</span></div>`;
    }
  } else if (ed.side === "phrases") {
    html = ed.phrases.map((p, i) => {
      const beats = djLowerBeat(ed, p.end) - djLowerBeat(ed, p.start);
      return `<div class="dj-row${ed.selPhrase === i ? " sel" : ""}" data-dj="phraserow" data-i="${i}">
        <span class="dj-dot square" style="background:var(--phrase-${phraseToken(p.kind, p.num)})"></span>
        <span class="dj-time">${djFmt(p.start)}</span>
        <span class="dj-phrasename">${esc(djPhraseLabel(p))}</span>
        ${beats ? `<span class="dj-bars">${esc(t("dj.bars", {bars: String(Math.round(beats / 4 * 10) / 10)}))}</span>` : ""}
      </div>`;
    }).join("") || djPhrasesEmptyHTML(ed);
  } else {
    html = djInfoHTML(ed);
  }
  host.innerHTML = html;
  // Die Memory-Liste bestimmt die Hoehe der Leiste, die anderen Reiter scrollen darin.
  host.classList.add("fit");
  host.style.height = "";
  if (ed.side === "memory") ed.sideMinH = host.offsetHeight;
  else {
    if (!ed.sideMinH) {
      host.innerHTML = djMemoryHTML(ed);
      ed.sideMinH = host.offsetHeight;
      host.innerHTML = html;
    }
    host.classList.remove("fit");
    host.style.height = ed.sideMinH + "px";
  }
  djRenderRekordbox(ed);
}

// Knoepfe und Hinweis zum Abgleich mit Rekordbox.
function djRenderRekordbox(ed) {
  const box = ed.root.querySelector(".dj-rb"), banner = ed.root.querySelector(".dj-banner");
  const rb = ed.rb;
  if (!rb || !rb.available) { box.hidden = true; banner.hidden = true; return; }
  ed.r.rb = rb.present ? 1 : 0;
  ed.r.rbDiff = [...new Set([...rb.pending, ...rb.changed, ...(rb.notes || [])])];
  const rbCell = djRowCell(ed, "rbCell", ".rbcell");
  if (rbCell) rbCell.innerHTML = rbSyncMark(ed.r);
  const names = list => list.map(f => t("dj.field_" + f)).join(", ");
  box.hidden = false;
  if (!rb.present) {
    box.innerHTML = `<div class="dj-rbstate">${esc(t("dj.rb_not_in_collection"))}</div>
      <button class="dj-rbbtn" data-dj="rbadd" title="${esc(t("dj.rb_add_title"))}">${djIcon("plus")} ${esc(t("dj.rb_add"))}</button>`;
  } else {
    box.innerHTML = `<div class="dj-rbstate${rb.pending.length ? " warn" : ""}">${esc(rb.pending.length
        ? t("dj.rb_pending", {fields: names(rb.pending)}) : t("dj.rb_in_sync"))}</div>
      <div class="dj-rbbtns">
        <button class="dj-rbbtn" data-dj="rbload" title="${esc(t("dj.rb_load_title"))}">${djIcon("download")} ${esc(t("dj.rb_load"))}</button>
        <button class="dj-rbbtn" data-dj="rbupdate" title="${esc(t("dj.rb_update_title"))}">${djIcon("upload")} ${esc(t("dj.rb_update"))}</button>
      </div>`;
  }
  banner.hidden = !rb.changed.length;
  if (rb.changed.length) {
    banner.innerHTML = `<span>${esc(t("dj.rb_changed", {fields: names(rb.changed)}))}</span>
      <button class="dj-btn wide" data-dj="rbtake">${esc(t("dj.rb_take"))}</button>
      <button class="dj-btn wide" data-dj="rbkeep">${esc(t("dj.rb_keep"))}</button>`;
  }
}

// Eine Abgleich-Aktion ausfuehren. Der Server antwortet mit dem neuen Stand des
// Tracks, Rueckgaengig beginnt danach von vorn.
async function djRekordbox(ed, action, fields) {
  ed.root.querySelector(".dj-rb").classList.add("busy");
  await djSave(ed);
  try {
    const res = await fetch("/api/dj-rekordbox", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ed.path, action, fields})});
    const data = await res.json();
    if (!res.ok || !data.ok) { note(data.error || String(res.status), true, !!data.sticky); return null; }
    if (!ed.closed) djAdopt(ed, data);
    return data;
  } catch (err) {
    note(t("dj.save_failed", {error: err.message}), true);
    return null;
  } finally {
    ed.root.querySelector(".dj-rb").classList.remove("busy");
  }
}

async function djRbLoad(ed) {
  const edited = Object.keys(ed.edited).filter(f => ed.edited[f]);
  if (edited.length) {
    const ok = await askConfirmSimple(t("dj.rb_load_confirm"),
      t("dj.rb_load_note", {fields: edited.map(f => t("dj.field_" + f)).join(", ")}), t("dj.rb_load"));
    if (!ok) return;
  }
  if (await djRekordbox(ed, "load", edited.length ? edited : null)) note(t("dj.rb_loaded"));
}

async function djRbUpdate(ed) {
  // Was unveraendert aus Rekordbox kommt, muss nicht zurueck.
  const fields = ["grid", "phrases", "cues"].filter(f => ed.edited[f] || ed.src[f] === "tracktab");
  // Metadaten (Titel, Interpret, ...): nur, wenn Rekordbox von TrackTab abweicht.
  if (((ed.rb && ed.rb.pending) || []).includes("tags")) fields.push("tags");
  if (!fields.length) { note(t("dj.rb_nothing"), "soft"); return; }
  const changed = (ed.rb && ed.rb.changed) || [];
  const names = fields.map(f => t("dj.field_" + f)).join(", ");
  const warn = changed.length ? " " + t("dj.rb_update_overwrites", {fields: changed.map(f => t("dj.field_" + f)).join(", ")}) : "";
  const ok = await askConfirmSimple(t("dj.rb_update_confirm"), t("dj.rb_update_note", {fields: names}) + warn, t("dj.rb_update"));
  if (!ok) return;
  const data = await djRekordbox(ed, "update", fields);
  if (!data) return;
  if (data.skipped && data.skipped.length) {
    note(t("dj.rb_skipped", {fields: data.skipped.map(f => t("dj.field_" + f)).join(", ")}), "soft", true);
  } else note(t("dj.rb_updated", {fields: names}));
}

async function djRbAdd(ed) {
  const ok = await askConfirmSimple(t("dj.rb_add_confirm"), t("dj.rb_add_note"), t("dj.rb_add"));
  if (!ok) return;
  const data = await djRekordbox(ed, "add", null);
  if (!data) return;
  ed.r.rb = 1;
  if ((data.skipped || []).length) note(t("dj.rb_added_partial", {fields: data.skipped.map(f => t("dj.field_" + f)).join(", ")}), "soft", true);
  else note(t("dj.rb_added"), false, true);
}

// ── Farbwahl ────────────────────────────────────────────

function djCloseColors(ed) {
  const pop = ed.root.querySelector(".dj-colorpop");
  pop.hidden = true;
  pop.innerHTML = "";
}

function djOpenColors(ed, id, anchor) {
  const cue = djCueById(ed, id);
  const colors = cue && ed.palettes && ed.palettes[cue.kind === "hot" ? "hot" : "memory"];
  if (!colors) return;
  const pop = ed.root.querySelector(".dj-colorpop");
  pop.innerHTML = colors.map(c => {
    const on = c.join() === cue.color.join();
    return `<button class="dj-swatch${on ? " on" : ""}" data-dj="setcolor" data-id="${id}" data-rgb="${c.join()}" style="background:${djRgb(c)}"></button>`;
  }).join("");
  pop.classList.toggle("hot", cue.kind === "hot");
  pop.hidden = false;
  const side = ed.root.querySelector(".dj-side").getBoundingClientRect();
  const box = anchor.getBoundingClientRect();
  const inside = box.left >= side.left && box.right <= side.right;
  pop.style.top = (inside ? Math.max(32, box.bottom - side.top + 2) : 32) + "px";
}

function djSetCueColor(ed, id, rgb) {
  const cue = djCueById(ed, id);
  if (!cue) return;
  djPush(ed);
  cue.color = rgb;
  djCloseColors(ed);
  djTouched(ed, "cues");
}

function djInfoHTML(ed) {
  const seg = ed.segs.length ? ed.segs[djSegIndexAt(ed, djPos(ed))] : null;
  const row = (field, label) => `<div class="dj-inforow">
      <span>${esc(label)}</span><b>${esc(djSourceLabel(field, ed))}</b>
      ${ed.edited[field] ? `<button class="dj-link" data-dj="reset" data-field="${field}">${esc(t("dj.reset"))}</button>` : ""}
    </div>`;
  return `<div class="dj-info">
    ${row("grid", t("dj.field_grid"))}
    ${row("phrases", t("dj.field_phrases"))}
    ${row("cues", t("dj.field_cues"))}
    ${ed.uncertain && !ed.edited.phrases ? `<div class="dj-note">${esc(t("dj.uncertain"))}</div>` : ""}
    ${seg ? `<div class="dj-inforow"><span>${esc(t("dj.grid_bpm"))}</span><b>${djFmtBpm(seg.bpm)}</b></div>` : ""}
    ${ed.r.bp ? `<div class="dj-inforow"><span>${esc(t("dj.tag_bpm"))}</span><b>${esc(String(ed.r.bp))}</b></div>` : ""}
    ${["grid", "phrases"].map(f => `<div class="dj-inforow">
      <span>${esc(t("dj.analysis_lock", {field: t("dj.field_" + f)}))}</span><b>${esc(t(ed.locks[f] ? "dj.analysis_lock_active" : "dj.analysis_lock_inactive"))}</b>
      <button class="dj-link" data-dj="analysislock" data-field="${f}">${esc(t(ed.locks[f] ? "dj.analysis_lock_release" : "dj.analysis_lock_set"))}</button>
    </div>`).join("")}
    ${PHRASES_AVAILABLE ? [["grid", ICONS.analyseGrid, "bulk.dj_grid"], ["phrases", ICONS.analysePhrases, "bulk.dj_phrases"], ["both", ICONS.analyseBoth, "dj.measure"]].map(([what, icon, label]) => {
      const off = what === "both" ? ed.locks.grid && ed.locks.phrases : ed.locks[what];
      return `<button class="dj-addrow" data-dj="measure" data-what="${what}"${off ? ` disabled title="${esc(t("dj.analysis_locked"))}"` : ""}>${icon} ${esc(t(label))}</button>`;
    }).join("") : ""}
    ${AUTOCUE_AVAILABLE ? `<button class="dj-addrow" data-dj="autocue"${ed.autocueBusy ? " disabled" : ""} title="${
      esc(t("dj.autocue_title"))}">${djIcon("square-sparkles")} ${esc(t("dj.autocue"))}</button>` : ""}
    <div class="dj-note">${esc(t("dj.storage_note"))}</div>
    <div class="dj-keys">
      ${[["Space", "dj.key_play"], ["C", "dj.key_cue"], ["M", "dj.key_memory"], ["X", "dj.key_cut"], ["1–8", "dj.key_hot"], ["⇧ 1–8", "dj.key_hot_delete"],
         ["Q–P", "dj.key_loops"],
         ["← →", "dj.key_jump"], ["A S D F G H + ← →", "dj.key_jumps"], ["⇧ ← →", "dj.key_jump_bar"],
         ["B", "dj.key_tap"], ["+ −", "dj.key_zoom"], ["⌘Z", "dj.key_undo"], ["⌫", "dj.key_delete"], ["⌥", "dj.key_alt"]]
        .map(([k, label]) => `<div><kbd>${esc(k)}</kbd><span>${esc(t(label))}</span></div>`).join("")}
    </div>
  </div>`;
}

function djSetSide(ed, side) {
  ed.side = side;
  djPrefs().side = side; djSavePrefs();
  djRenderChrome(ed);
  djRenderSide(ed);
}

function djSetMode(ed, mode) {
  ed.mode = mode;
  djPrefs().mode = mode; djSavePrefs();
  if (mode === "phrase") djSetSide(ed, "phrases");
  else if (mode === "cue" && ed.side === "phrases") djSetSide(ed, "memory");
  djRenderTools(ed);
  requestWavePaint();
}

function djZoom(ed, dir) {
  const max = Math.max(4, ed.dur || 600);
  ed.span = Math.min(max, Math.max(0.5, ed.span * (dir > 0 ? 1 / 1.15 : 1.15)));
  djPrefs().span = ed.span; djSavePrefs();
  requestWavePaint();
}

// ── Ereignisse ──────────────────────────────────────────

function djOnClick(ed, ev) {
  if (ev.target.matches("input, select")) return;
  const el = ev.target.closest("[data-dj]");
  if (!ev.target.closest(".dj-colorpop") && !(el && el.dataset.dj === "cuecolor")) djCloseColors(ed);
  if (!el || el.disabled) return;
  const d = el.dataset, id = d.id ? Number(d.id) : null;
  switch (d.dj) {
    case "toggle": djToggle(ed); break;
    case "jump": djJump(ed, Number(d.n)); break;
    case "prevcue": djPrevCue(ed); break;
    case "nextcue": djNextCue(ed); break;
    case "zoomin": djZoom(ed, 1); break;
    case "zoomout": djZoom(ed, -1); break;
    case "mode": djSetMode(ed, d.mode); break;
    case "side": djSetSide(ed, d.side); break;
    case "undo": djUndo(ed); break;
    case "redo": djRedo(ed); break;
    case "q": djPrefs().q = !djPrefs().q; djSavePrefs(); djRenderChrome(ed); break;
    case "pad": djAddHot(ed, Number(d.slot), ev); break;
    case "memory": djAddMemory(ed, ev); break;
    case "loopin": djLoopIn(ed, ev); break;
    case "loopout": djLoopOut(ed, ev); break;
    case "looplen": djLoopBeats(ed, Number(d.n)); break;
    case "loopexit": djLoopExit(ed); break;
    case "cuejump": { const c = djCueById(ed, id); if (c) djGoCue(ed, c); break; }
    case "cuedel": djDeleteCue(ed, id); break;
    case "looptoggle": djToggleLoopKind(ed, id); break;
    case "cuecolor": djOpenColors(ed, id, el); break;
    case "setcolor": djSetCueColor(ed, id, d.rgb.split(",").map(Number)); break;
    case "rbload": djRbLoad(ed); break;
    case "rbupdate": djRbUpdate(ed); break;
    case "rbadd": djRbAdd(ed); break;
    case "rbtake": djRekordbox(ed, "load", ed.rb.changed); break;
    case "rbkeep": djRekordbox(ed, "keep", ed.rb.changed); break;
    case "tap": djTap(ed); break;
    case "half": djScaleBpm(ed, 0.5); break;
    case "double": djScaleBpm(ed, 2); break;
    case "gridscope": djPrefs().gridHere = d.here === "1"; djSavePrefs(); djRenderTools(ed); break;
    case "shift": djShiftGrid(ed, Number(d.ms) / 1000); break;
    case "interval": djStretchIntervals(ed, Number(d.d)); break;
    case "gridhere": djGridToPlayhead(ed); break;
    case "analysislock": djSetAnalysisLock(ed, d.field, !ed.locks[d.field]); break;
    case "downbeat": djSetDownbeat(ed); break;
    case "addseg": djAddSegment(ed); break;
    case "delseg": djRemoveSegment(ed); break;
    case "metro": djToggleMetro(ed); break;
    case "lock": djPush(ed); ed.locked = !ed.locked; djTouched(ed, "grid"); break;
    case "newgrid":
      djPush(ed);
      ed.segs = [{t: djPos(ed), bpm: ed.r.bp >= 20 && ed.r.bp <= 400 ? ed.r.bp : 120, n: 1}];
      djTouched(ed, "grid");
      break;
    case "measure": djMeasure(ed, d.what || "both"); break;
    case "autocue": djAutoCue(ed); break;
    case "cut": djCutPhrase(ed, ev); break;
    case "addphrase": djAddPhrase(ed); break;
    case "kind": djSetPhraseKind(ed, d.kind); break;
    case "delphrase": djDeletePhrase(ed); break;
    case "clearphrases": djClearPhrases(ed); break;
    case "phraserow": djSelectPhrase(ed, Number(d.i), true); break;
    case "reset": djReset(ed, d.field); break;
  }
}

function djOnChange(ed, ev) {
  const el = ev.target;
  if (el.matches(".dj-bpminput")) {
    djSetBpm(ed, parseFloat(String(el.value).replace(",", ".")));
  } else if (el.dataset.cuename) {
    const cue = djCueById(ed, Number(el.dataset.cuename));
    if (cue && cue.name !== el.value) {
      djPush(ed);
      cue.name = el.value.slice(0, 80);
      el.blur();
      djTouched(ed, "cues");
    }
  }
}


function djSelectPhrase(ed, i, jump) {
  ed.selPhrase = i;
  ed.selCue = null;              // siehe djSelectCue()
  if (jump && ed.phrases[i]) djSeek(ed, ed.phrases[i].start);
  djRenderPads(ed); djRenderSide(ed); djRenderTools(ed); requestWavePaint();
}

async function djMeasure(ed, what) {
  const off = what === "both" ? ed.locks.grid && ed.locks.phrases : ed.locks[what];
  if (off) { note(t("dj.analysis_locked"), "soft"); return; }
  await analysePhrases([ed.r], false, what);
}

// Auto Cue (app/autocue/) fuer den offenen Track. Der Server rechnet nur (ohne apply),
// uebernommen wird hier -- so laeuft es wie jede Bearbeitung ueber Rueckgaengig und den
// normalen Speicherweg (djTouched()). Hat der Server dabei Grid/Phrasen aus Rekordbox
// uebernommen oder erst gemessen, steht das schon in dj_track: dann zuerst neu laden.
async function djAutoCue(ed) {
  if (ed.autocueBusy) return;
  ed.autocueBusy = true;
  djRenderTools(ed); djRenderSide(ed);
  const pt = progressToast(t("dj.autocue_running"), {indeterminate: true});
  try {
    await djSave(ed);
    const res = await fetch("/api/autocue", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ed.path})});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
    if (!ed.closed && (data.measured || (data.loaded || []).length)) await djLoad(ed);
    const extra = autocueSourceText(data);
    if (data.status === "done" && !ed.closed) {
      djPush(ed);
      ed.cues = data.cues.map(djCueFromServer);
      ed.selCue = null;
      djTouched(ed, "cues");
      pt.done(`${t("autocue.status_done", {count: data.cues.length})} ${extra}`.trim());
    } else {
      pt.done(`${autocueStatusText(data)} ${extra}`.trim(), true);
    }
  } catch (err) {
    pt.fail(t("error.generic", {error: err.message}));
  } finally {
    ed.autocueBusy = false;
    if (!ed.closed) { djRenderTools(ed); djRenderSide(ed); }
  }
}

// Sammelaktion: Auto Cue fuer die Auswahl, einer nach dem anderen (je Track Dekodieren
// und Messen, ein paar Sekunden). Der Server speichert gleich (apply); Tracks, bei denen
// keine Cue sicher genug war, behalten ihre bisherigen.
async function djBulkAutoCue(rows) {
  const todo = rows.filter(r => !r.gone);
  if (!todo.length) return;
  const label = t("bulk.dj_autocue");
  const ok = await askConfirmSimple(t("dj.autocue_confirm"), t("dj.autocue_confirm_note", {count: todo.length}), label);
  if (!ok) return;
  if (DJ) await djSave(DJ);
  const pt = progressToast(t("dj.bulk_running", {action: label, count: todo.length}));
  let done = 0, skipped = 0, failed = 0, lastError = "";
  for (let i = 0; i < todo.length; i++) {
    const r = todo[i];
    try {
      const res = await fetch("/api/autocue", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({path: r.p, apply: true})});
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
      if (data.status === "done") done++;
      else skipped++;
    } catch (err) {
      failed++; lastError = `${baseName(r.p)}: ${err.message}`;
    } finally {
      pt.update(100 * (i + 1) / todo.length, t("toast.bpmkey_progress", {done: i + 1, total: todo.length}));
    }
  }
  await syncMarks();
  render();
  if (DJ && !DJ.closed && todo.some(r => r.p === DJ.path)) djLoad(DJ);
  const summary = t("autocue.bulk_done", {done, skipped});
  if (failed) pt.fail(`${summary} ${t("dj.bulk_failed", {failed, error: lastError})}`);
  else pt.done(summary, !done);
}

function autocueStatusText(data) {
  switch (data.status) {
    case "none": return t("autocue.status_none");
    case "badgrid": return t("autocue.status_badgrid", {reason: (data.reasons || [])[0] || ""});
    case "nogrid": return t("autocue.status_nogrid");
    case "skipped": return t("autocue.status_skipped", {minutes: data.minutes});
    case "unavailable": return t("settings.requires_autocue");
    default: return String(data.status || "");
  }
}

function autocueSourceText(data) {
  return [(data.loaded || []).length ? t("autocue.loaded") : "", data.measured ? t("autocue.measured") : ""]
    .filter(Boolean).join(" ");
}

// Erst den Stand speichern: das Speichern einer Bearbeitung sperrt sonst gleich wieder.
async function djSetAnalysisLock(ed, field, on) {
  await djSave(ed);
  try {
    const res = await fetch("/api/dj-track", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ed.path, locks: {[field]: on}})});
    if (!res.ok) throw new Error(String(res.status));
  } catch (err) {
    note(t("dj.save_failed", {error: err.message}), true);
    return;
  }
  ed.locks[field] = on;
  djRenderChrome(ed); djRenderSide(ed);
}

// Sammelaktion: Abgleich mit Rekordbox fuer die Auswahl, einer nach dem anderen (jeder
// Schreibvorgang sichert die master.db). Was der Track braucht, sagt sein eigener
// Stand, wie bei djRbLoad()/djRbUpdate(). Laeuft Rekordbox, ist nach dem ersten Schluss.
// "add" fragt nichts vorab ab: Tracks, die schon in der Sammlung stehen, laesst der
// Server aus (added: false), sie zaehlen als uebersprungen.
async function djBulkRekordbox(rows, action) {
  const todo = rows.filter(r => !r.gone), label = t({load: "dj.rb_load", update: "dj.rb_update", add: "dj.rb_add"}[action]);
  if (!todo.length) return;
  const ok = await askConfirmSimple(t({load: "dj.rb_load_confirm", update: "dj.rb_update_confirm", add: "dj.bulk_rb_add_confirm"}[action]),
    t({load: "dj.bulk_rb_load_note", update: "dj.bulk_rb_update_note", add: "dj.bulk_rb_add_note"}[action], {count: todo.length}), label);
  if (!ok) return;
  await djRekordboxRun(todo, action);
}

// Kern der Sammelaktion ohne Rueckfrage, auch fuer das Uebertragen auf eine
// Rekordbox-Playlist (addToRekordboxPlaylist(), angehakte vorhandene Tracks).
// Ein Token je Nutzeraktion: der Server sichert die master.db dann nur einmal
// (rekordbox.backup_session()), auch ueber mehrere Anfragen hinweg.
function rbSessionToken() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// "update" laeuft gebuendelt ueber /api/dj-rekordbox-batch: der Server bestimmt die
// Felder selbst (dieselbe Regel wie unten fuer einzelne Tracks), oeffnet die
// master.db je Abschnitt einmal und schreibt je Track eine Transaktion.
const DJ_RB_BATCH = 10;

async function djRekordboxRun(rows, action, session = rbSessionToken()) {
  const todo = rows.filter(r => !r.gone), label = t({load: "dj.rb_load", update: "dj.rb_update", add: "dj.rb_add"}[action]);
  if (!todo.length) return;
  if (DJ) await djSave(DJ);
  const pt = progressToast(t("dj.bulk_running", {action: label, count: todo.length}));
  let done = 0, skipped = 0, partial = 0, failed = 0, lastError = "", stopped = "";
  if (action === "update") {
    for (let i = 0; i < todo.length && !stopped; i += DJ_RB_BATCH) {
      const chunk = todo.slice(i, i + DJ_RB_BATCH);
      try {
        const res = await fetch("/api/dj-rekordbox-batch", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({paths: chunk.map(r => r.p), session})});
        const data = await res.json();
        if (res.status === 409) { stopped = data.error || String(res.status); break; }
        if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
        done += data.done.length; skipped += data.skipped.length;
        const errs = Object.entries(data.errors || {});
        failed += errs.length;
        if (errs.length) lastError = `${baseName(errs[0][0])}: ${errs[0][1]}`;
        if (data.stopped) stopped = data.stopped;
      } catch (err) {
        failed += chunk.length; lastError = err.message;
      } finally {
        const n = Math.min(i + DJ_RB_BATCH, todo.length);
        pt.update(100 * n / todo.length, t("toast.bpmkey_progress", {done: n, total: todo.length}));
      }
    }
  }
  for (let i = 0; action !== "update" && i < todo.length && !stopped; i++) {
    const r = todo[i];
    try {
      if (action === "add") {
        const res = await fetch("/api/dj-rekordbox", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: r.p, action, session})});
        const data = await res.json();
        if (res.status === 409) { stopped = data.error || String(res.status); break; }
        if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
        r.rb = 1;
        if (!data.added) skipped++;
        else { done++; if ((data.skipped || []).length) partial++; }
        continue;
      }
      const state = await (await fetch(`/api/dj-track?path=${encodeURIComponent(r.p)}`)).json();
      const fields = ["grid", "phrases", "cues"].filter(f => state[f].edited || (action === "load" ? state.rekordbox.changed.includes(f) : state[f].source === "tracktab"));
      if (!state.ok || !state.rekordbox.present || !fields.length) { skipped++; continue; }
      const res = await fetch("/api/dj-rekordbox", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({path: r.p, action, fields})});
      const data = await res.json();
      if (res.status === 409) { stopped = data.error || String(res.status); break; }
      if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
      if (action === "update" && !(data.written || []).length) skipped++;
      else done++;
    } catch (err) {
      failed++; lastError = `${baseName(r.p)}: ${err.message}`;
    } finally {
      pt.update(100 * (i + 1) / todo.length, t("toast.bpmkey_progress", {done: i + 1, total: todo.length}));
    }
  }
  await syncMarks();
  render();
  if (DJ && !DJ.closed && todo.some(r => r.p === DJ.path)) djLoad(DJ);
  const summary = action === "add"
    ? t("dj.bulk_add_done", {done, skipped}) + (partial ? " " + t("dj.bulk_add_partial", {partial}) : "")
    : t("dj.bulk_done", {action: label, done, skipped});
  if (stopped) pt.fail(`${summary} ${stopped}`);
  else if (failed) pt.fail(`${summary} ${t("dj.bulk_failed", {failed, error: lastError})}`);
  else pt.done(summary, !done);
}

async function djReset(ed, field) {
  const ok = await askConfirmSimple(t("dj.reset_title"), t("dj.reset_note"), t("dj.reset"));
  if (!ok || ed.closed) return;
  ed.dirty.delete(field);
  await djSave(ed);
  try {
    const res = await fetch("/api/dj-track", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ed.path, [field]: null})});
    if (!res.ok) throw new Error(String(res.status));
  } catch (err) {
    note(t("dj.save_failed", {error: err.message}), true);
    return;
  }
  if (!ed.closed) djLoad(ed);
}

// Beatjump-Anzeige: erscheint erst, wenn der Buchstabe wirklich gehalten wird, wer ihn
// nur kurz greift, sieht nichts aufblitzen. Ist sie schon da, wechselt sie beim Griff
// zur naechsten Taste sofort.
function djJumpHudDelay() { return 280; }

function djHoldJump(ed, jumpKey) {
  const same = ed.jumpHeld && ed.jumpHeld[0] === jumpKey[0];
  ed.jumpHeld = jumpKey;
  if (same) return;
  const hud = document.getElementById("djJumpHud");
  if (hud && hud.classList.contains("on")) { djShowJumpHud(ed); return; }
  clearTimeout(ed.jumpHudTimer);
  ed.jumpHudTimer = setTimeout(() => djShowJumpHud(ed), djJumpHudDelay());
}

function djShowJumpHud(ed) {
  if (ed.closed || !ed.jumpHeld) return;
  let hud = document.getElementById("djJumpHud");
  if (!hud) {
    hud = document.createElement("div");
    hud.id = "djJumpHud";
    hud.className = "dj-jumphud";
    hud.setAttribute("aria-hidden", "true");
    document.body.appendChild(hud);
  }
  // Ueber der Listenspalte: so bleiben Wellenform und Tracks frei.
  const side = document.querySelector(".sidebar-body").getBoundingClientRect();
  hud.style.left = `${side.left + 8}px`;
  hud.style.width = `${Math.max(side.width - 16, 190)}px`;
  const [held, beats] = ed.jumpHeld;
  hud.innerHTML = `<div class="dj-jumphud-main"><span data-dir="-1">←</span><b>${beats}</b><span data-dir="1">→</span></div>
    <div class="dj-jumphud-unit">${esc(t("dj.jump_hud_beats"))}</div>
    <div class="dj-jumphud-keys">${djJumpKeys().map(([k, n]) =>
      `<span${k === held ? ' class="on"' : ""}><kbd>${k.toUpperCase()}</kbd>${n}</span>`).join("")}</div>`;
  hud.classList.add("on");
}

function djReleaseJump(ed) {
  ed.jumpHeld = null;
  clearTimeout(ed.jumpHudTimer);
  const hud = document.getElementById("djJumpHud");
  if (hud) hud.classList.remove("on");
}

// Loop-Groesse: dieselbe Anzeige wie beim Beatjump, aber ohne Taste halten -- sie blendet nach kurzer Zeit von selbst aus.
function djFlashLoopHud(ed, label) {
  if (ed.closed) return;
  clearTimeout(ed.jumpHudTimer);
  let hud = document.getElementById("djJumpHud");
  if (!hud) {
    hud = document.createElement("div");
    hud.id = "djJumpHud";
    hud.className = "dj-jumphud";
    hud.setAttribute("aria-hidden", "true");
    document.body.appendChild(hud);
  }
  const side = document.querySelector(".sidebar-body").getBoundingClientRect();
  hud.style.left = `${side.left + 8}px`;
  hud.style.width = `${Math.max(side.width - 16, 190)}px`;
  hud.innerHTML = `<div class="dj-jumphud-main"><b>${esc(label)}</b></div>
    <div class="dj-jumphud-unit">${esc(t("dj.loop_hud_beats"))}</div>`;
  hud.classList.add("on");
  clearTimeout(ed.loopHudTimer);
  ed.loopHudTimer = setTimeout(() => { if (!ed.jumpHeld) hud.classList.remove("on"); }, 900);
}

function djPulseJumpHud(dir) {
  const arrow = document.querySelector(`#djJumpHud.on [data-dir="${dir}"]`);
  if (!arrow) return;
  arrow.classList.add("hit");
  setTimeout(() => arrow.classList.remove("hit"), 140);
}

// Tasten gelten, solange ein Editor offen ist und nichts anderes den Fokus hat.
// Capture-Phase: Pfeile, Leertaste und Cmd+Z haetten sonst die Warteschlange
// bzw. die Playlist-Historie.
document.addEventListener("keydown", ev => {
  const ed = DJ;
  if (!ed || ed.closed || state.layout !== "dj") return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  if ([...document.querySelectorAll(".overlay")].some(o => o.style.display === "flex")) return;
  const key = ev.key, lower = key.length === 1 ? key.toLowerCase() : key;
  const mod = ev.metaKey || ev.ctrlKey;
  // Ueber den Tastencode: mit Shift (ueberschreiben) oder der Wahltaste (nicht
  // einrasten) liefert key sonst "!" oder "¡".
  const hotSlot = /^(Digit|Numpad)[1-8]$/.test(ev.code) ? Number(ev.code.slice(-1)) : 0;
  const loopKey = djLoopKeys().find(([k]) => k === lower);
  const jumpKey = djJumpKeys().find(([k]) => k === lower);
  // Solange Cmd gedrueckt ist, meldet macOS kein keyup anderer Tasten: die Anzeige bliebe stehen.
  if (mod && ed.jumpHeld) djReleaseJump(ed);
  let done = true;
  if (mod && lower === "z") { if (ev.shiftKey) djRedo(ed); else djUndo(ed); }
  else if (mod) done = false;
  else if (hotSlot) { if (!ev.repeat) djHotDown(ed, hotSlot, ev); }
  else if (ev.altKey && key.length === 1) done = false;
  else if (key === " ") { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); djToggle(ed); }
  else if (key === "ArrowLeft" || key === "ArrowRight") {
    const dir = key === "ArrowLeft" ? -1 : 1;
    djJump(ed, dir * (ed.jumpHeld ? ed.jumpHeld[1] : ev.shiftKey ? 4 : 1));
    djPulseJumpHud(dir);
  }
  else if (jumpKey) djHoldJump(ed, jumpKey);
  else if (lower === "c") { if (!ev.repeat) djCueDown(ed); }
  else if (lower === "m") djAddMemory(ed, ev);
  else if (lower === "x") { if (!ev.repeat) djCutPhrase(ed, ev); }
  else if (loopKey) { if (!ev.repeat) djLoopBeats(ed, loopKey[1]); }
  else if (lower === "b") djTap(ed);
  else if (key === "+" || key === "=") djZoom(ed, 1);
  else if (key === "-") djZoom(ed, -1);
  else if (key === "Delete" || key === "Backspace") {
    if (ed.selCue != null) djDeleteCue(ed, ed.selCue);
    else if (ed.selPhrase >= 0) djDeletePhrase(ed);
    else done = false;
  } else done = false;
  if (done) { ev.preventDefault(); ev.stopImmediatePropagation(); }
}, true);

document.addEventListener("keyup", ev => {
  if (!DJ) return;
  if (DJ.jumpHeld && DJ.jumpHeld[0] === ev.key.toLowerCase()) djReleaseJump(DJ);
  if (ev.key.toLowerCase() === "c") djCueUp(DJ);
  else if (/^(Digit|Numpad)[1-8]$/.test(ev.code)) djHotUp(DJ, Number(ev.code.slice(-1)));
}, true);
// Geht der Fokus verloren, kommt kein keyup mehr: der Cue darf nicht weiterlaufen.
window.addEventListener("blur", () => { if (DJ) { djCueUp(DJ); djReleaseJump(DJ); } });

window.addEventListener("pagehide", () => { if (DJ) djSave(DJ, true); });

// Rad: Pinch zoomt, seitliches Wischen spult, senkrecht bleibt Seiten-Scrollen.
function djWireZoom(ed) {
  const canvas = ed.zoomEntry.canvas;
  canvas.addEventListener("wheel", ev => {
    if (ev.ctrlKey) {
      ev.preventDefault();
      ed.pinch = (ed.pinch || 0) + ev.deltaY;
      if (Math.abs(ed.pinch) >= 10) { djZoom(ed, ed.pinch < 0 ? 1 : -1); ed.pinch = 0; }
    } else if (Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) {
      ev.preventDefault();
      djSeek(ed, djPos(ed) + ev.deltaX / Math.max(1, ed.zoomEntry.w) * ed.span);
    }
  }, {passive: false});
  // Safari meldet Pinch nicht als wheel, sondern als gesture*.
  let pinchScale = 1;
  canvas.addEventListener("gesturestart", () => { pinchScale = 1; });
  canvas.addEventListener("gesturechange", ev => {
    const ratio = ev.scale / pinchScale;
    if (ratio >= 1.08 || ratio <= 0.92) { djZoom(ed, ratio > 1 ? 1 : -1); pinchScale = ev.scale; }
  });

  canvas.addEventListener("pointerdown", ev => {
    ev.preventDefault();
    djReleaseFocus();
    if (ev.button !== 0) return;
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    const w = rect.width, h = rect.height, lay = djZoomLayout();
    const start = djPos(ed) - ed.span / 2;
    // Spielt der Track, wandert das Fenster waehrend des Ziehens weiter.
    const secAt = px => djPos(ed) - ed.span / 2 + (px / w) * ed.span;
    const drag = {kind: "scrub", x0: x, pos0: djPos(ed), moved: false, wasPlaying: !ed.audio.paused};
    if (y > h - lay.lane) {
      // Am Rand anfassen aendert die Laenge, in der Mitte verschiebt die Phrase.
      const edge = djPhraseEdgeAt(ed, x, start, w, ed.span);
      if (edge) Object.assign(drag, {kind: "edge", base: ed.phrases.map(p => ({...p})), sel: ed.selPhrase, grab: edge.at - secAt(x)}, edge);
      else {
        const i = djPhraseAt(ed, secAt(x));
        drag.kind = "phrase"; drag.at = secAt(x); drag.index = i;
        if (i >= 0) drag.base = {...ed.phrases[i], prev: ed.phrases[i - 1] && {...ed.phrases[i - 1]}, next: ed.phrases[i + 1] && {...ed.phrases[i + 1]}};
      }
    } else {
      const cue = y < lay.top + 6 ? djCueAtX(ed, x, start, ed.span, w) : null;
      if (cue) Object.assign(drag, {kind: "cue", cue});
      else if (ed.mode === "grid" && !ed.locked && ed.bt.length) {
        const b = djNearestBeat(ed, secAt(x));
        if (Math.abs(((ed.bt[b] - start) / ed.span) * w - x) <= 5) {
          const segIndex = djSegIndexAt(ed, ed.bt[b]);
          // Mit Shift dehnen: der erste Schlag bleibt, der angefasste folgt der Maus.
          const beatsIn = Math.round((ed.bt[b] - ed.segs[segIndex].t) * ed.segs[segIndex].bpm / 60);
          Object.assign(drag, {kind: ev.shiftKey && beatsIn >= 1 ? "stretch" : "grid", beatsIn,
                               base: JSON.parse(JSON.stringify(ed.segs)), seg: segIndex});
        }
      }
    }
    canvas.setPointerCapture(ev.pointerId);

    const onMove = e => {
      const px = e.clientX - rect.left;
      if (!drag.moved && Math.abs(px - x) < 3) return;
      if (!drag.moved) {
        drag.moved = true;
        if (drag.kind === "scrub" && drag.wasPlaying) ed.audio.pause();
        if (["cue", "edge", "grid", "stretch"].includes(drag.kind)) djPush(ed);
        if (drag.kind === "phrase" && drag.base) { djPush(ed); djSelectPhrase(ed, drag.index, false); }
      }
      const dt = (px - x) / w * ed.span;
      if (drag.kind === "scrub") ed.dragPos = djClampOver(ed, drag.pos0 - dt);
      else if (drag.kind === "cue") djDragCue(ed, drag.cue, secAt(px), e);
      else if (drag.kind === "edge") djDragEdge(ed, drag, secAt(px) + drag.grab, e);
      else if (drag.kind === "phrase" && drag.base) djDragPhrase(ed, drag, dt, e);
      else if (drag.kind === "grid") { djShiftSegs(ed, drag.base, dt, djPrefs().gridHere ? drag.seg : 0); djRebuildBeats(ed); }
      else if (drag.kind === "stretch") {
        const span = secAt(px) - drag.base[drag.seg].t;
        if (span > 0.05) {
          ed.segs = drag.base.map((seg, j) => j === drag.seg
            ? {...seg, bpm: Math.min(400, Math.max(20, drag.beatsIn * 60 / span))} : {...seg});
          djRebuildBeats(ed);
        }
      }
      requestWavePaint();
    };
    const onUp = e => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      ed.edgeDrag = null;
      if (!drag.moved) {
        if (drag.kind === "cue") djGoCue(ed, drag.cue);
        else if (drag.kind === "phrase") {
          const i = djPhraseAt(ed, drag.at);
          if (i >= 0) { djSelectPhrase(ed, i, false); if (ed.side !== "phrases" && ed.mode === "phrase") djSetSide(ed, "phrases"); }
        } else if (drag.kind !== "edge") djSeek(ed, djSnapBeat(ed, secAt(x), e));
      } else if (drag.kind === "scrub") {
        const to = ed.dragPos;
        ed.dragPos = null;
        djSeek(ed, to);
        if (drag.wasPlaying) djPlay(ed);
      } else if (drag.kind === "cue") djTouched(ed, "cues");
      else if (drag.kind === "edge" || (drag.kind === "phrase" && drag.base)) djTouched(ed, "phrases");
      else if (drag.kind === "grid" || drag.kind === "stretch") djTouched(ed, "grid");
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
  });

  canvas.addEventListener("pointermove", ev => {
    if (ev.buttons) return;
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    const lay = djZoomLayout(), start = djPos(ed) - ed.span / 2;
    let cursor = "grab", hover = null;
    if (y > rect.height - lay.lane) {
      const edge = djPhraseEdgeAt(ed, x, start, rect.width, ed.span);
      if (edge) hover = edge.at;
      cursor = edge ? "col-resize" : "move";
    } else if (y < lay.top + 6 && djCueAtX(ed, x, start, ed.span, rect.width)) cursor = "pointer";
    else if (ed.mode === "grid" && !ed.locked && ed.bt.length) {
      const b = djNearestBeat(ed, start + (x / rect.width) * ed.span);
      if (Math.abs(((ed.bt[b] - start) / ed.span) * rect.width - x) <= 5) cursor = "ew-resize";
    }
    canvas.style.cursor = cursor;
    djSetEdgeHover(ed, hover);
  });
  canvas.addEventListener("pointerleave", () => djSetEdgeHover(ed, null));
}

function djSetEdgeHover(ed, at) {
  if (ed.edgeHover === at) return;
  ed.edgeHover = at;
  requestWavePaint();
}

// preventDefault() auf den Canvases laesst den Fokus stehen. Ein Klick in die
// Wellenform soll aber Suchfeld und Namensfelder verlassen, sonst landen die
// Tastenkuerzel dort.
function djReleaseFocus() {
  const el = document.activeElement;
  if (el && el.matches && el.matches("input, select, textarea")) el.blur();
}

function djWireOver(ed) {
  const canvas = ed.overEntry.canvas;
  canvas.addEventListener("pointerdown", ev => {
    ev.preventDefault();
    djReleaseFocus();
    const rect = canvas.getBoundingClientRect(), lay = djOverLayout();
    const range = djOverRange(ed);
    const secAt = e => djClampOver(ed, range.start + ((e.clientX - rect.left) / rect.width) * range.span);
    const y = ev.clientY - rect.top;
    if (y > rect.height - lay.lane) {
      const edge = djPhraseEdgeAt(ed, ev.clientX - rect.left, range.start, rect.width, range.span);
      if (edge) { djOverEdgeDrag(ed, canvas, ev, edge, secAt); return; }
      const i = djPhraseAt(ed, secAt(ev));
      if (i >= 0) { djSelectPhrase(ed, i, true); return; }
    }
    if (y < lay.top + 2) {
      const cue = djCueAtX(ed, ev.clientX - rect.left, range.start, range.span, rect.width);
      if (cue) { djGoCue(ed, cue); return; }
    }
    const was = !ed.audio.paused;
    if (was) ed.audio.pause();
    ed.dragPos = secAt(ev);
    canvas.setPointerCapture(ev.pointerId);
    requestWavePaint();
    const onMove = e => { ed.dragPos = secAt(e); requestWavePaint(); };
    const onUp = () => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      const to = ed.dragPos;
      ed.dragPos = null;
      djSeek(ed, to);
      if (was) djPlay(ed);
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
  });
  canvas.addEventListener("pointermove", ev => {
    if (ev.buttons) return;
    const rect = canvas.getBoundingClientRect(), y = ev.clientY - rect.top, range = djOverRange(ed);
    const edge = y > rect.height - djOverLayout().lane
      ? djPhraseEdgeAt(ed, ev.clientX - rect.left, range.start, rect.width, range.span) : null;
    canvas.style.cursor = edge ? "col-resize" : "";
    djSetEdgeHover(ed, edge ? edge.at : null);
  });
  canvas.addEventListener("pointerleave", () => djSetEdgeHover(ed, null));
}

// Rand in der Gesamtansicht ziehen. Ohne Bewegung bleibt es ein Klick auf die Phrase.
function djOverEdgeDrag(ed, canvas, ev, edge, secAt) {
  const drag = {...edge, base: ed.phrases.map(p => ({...p})), sel: ed.selPhrase, grab: edge.at - secAt(ev), moved: false};
  const x0 = ev.clientX;
  canvas.setPointerCapture(ev.pointerId);
  const onMove = e => {
    if (!drag.moved && Math.abs(e.clientX - x0) < 3) return;
    if (!drag.moved) { drag.moved = true; djPush(ed); }
    djDragEdge(ed, drag, secAt(e) + drag.grab, e);
    requestWavePaint();
  };
  const onUp = () => {
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerup", onUp);
    canvas.removeEventListener("pointercancel", onUp);
    ed.edgeDrag = null;
    if (drag.moved) { djTouched(ed, "phrases"); return; }
    const i = djPhraseAt(ed, edge.at);
    if (i >= 0) djSelectPhrase(ed, i, true);
  };
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
}

function djCueAtX(ed, x, start, span, w) {
  let best = null, dist = 10;
  for (const c of ed.cues) {
    const d = Math.abs(((c.position_s - start) / span) * w - x);
    if (d < dist) { dist = d; best = c; }
  }
  return best;
}

// Eine Phrasengrenze unter dem Mauszeiger: {left, right} = Indizes der Nachbarn
// (-1 = keiner), side = welche der beiden angefasst ist, touching = kein Abstand,
// at = Zeit der Grenze. Die naechste Grenze gewinnt, in der Gesamtansicht liegen sie dicht.
function djPhraseEdgeAt(ed, x, start, w, span) {
  const touch = (a, b) => !!(a && b && Math.abs(a.end - b.start) < 0.01);
  let best = null, dist = 7;
  ed.phrases.forEach((p, i) => {
    const toStart = Math.abs(((p.start - start) / span) * w - x);
    if (toStart < dist) {
      dist = toStart;
      best = {left: i - 1, right: i, side: "right", touching: touch(ed.phrases[i - 1], p), at: p.start};
    }
    const toEnd = Math.abs(((p.end - start) / span) * w - x);
    if (toEnd < dist) {
      const next = ed.phrases[i + 1];
      dist = toEnd;
      best = {left: i, right: next ? i + 1 : -1, side: "left", touching: touch(p, next), at: p.end};
    }
  });
  return best;
}

function djDragCue(ed, cue, sec, ev) {
  const to = djClampOver(ed, djSnapBeat(ed, sec, ev));
  if (cue.loop_s != null) cue.loop_s = Math.min(ed.dur, cue.loop_s + (to - cue.position_s));
  cue.position_s = to;
}

// Verschieben: die Laenge bleibt, die Nachbarn geben nach, soweit sie koennen.
function djDragPhrase(ed, drag, dt, ev) {
  const b = drag.base, min = 0.2, p = ed.phrases[drag.index];
  const prev = ed.phrases[drag.index - 1], next = ed.phrases[drag.index + 1];
  const touchesPrev = prev && Math.abs(b.prev.end - b.start) < 0.01;
  const touchesNext = next && Math.abs(b.next.start - b.end) < 0.01;
  const len = b.end - b.start;
  const lo = prev ? b.prev.start + min : djPhraseFloor(ed);
  const hi = (next ? b.next.end - min : ed.dur) - len;
  let to = djSnapDrag(ed, b.start + dt, ev);
  to = Math.max(lo, Math.min(hi, to));
  p.start = to; p.end = to + len;
  // Mit Abstand gibt der Nachbar erst nach, wenn die Phrase ihn erreicht.
  if (prev) prev.end = touchesPrev ? to : Math.min(b.prev.end, to);
  if (next) next.start = touchesNext ? to + len : Math.max(b.next.start, to + len);
}

// Die Grenze wandert, die Phrase auf der anderen Seite wird kleiner. Wird sie zu
// klein, verschwindet sie und die naechste ist dran. Immer vom Stand beim Anfassen
// aus gerechnet: wer zurueckzieht, bekommt Verschwundenes wieder.
function djDragEdge(ed, drag, sec, ev) {
  const min = 0.2, eps = 1e-6;
  const left = drag.base.slice(0, drag.left + 1).map(p => ({...p}));
  const right = drag.right < 0 ? [] : drag.base.slice(drag.right).map(p => ({...p}));
  let to = Math.max(djPhraseFloor(ed), Math.min(ed.dur, djSnapDrag(ed, sec, ev)));
  // Am aeussersten Rand gibt es niemanden, der den Platz uebernimmt.
  if (!left.length) to = Math.min(to, right[0].end - min);
  if (!right.length) to = Math.max(to, left[left.length - 1].start + min);
  const moveLeft = drag.touching || drag.side === "left";
  const moveRight = drag.touching || drag.side === "right";
  let goneRight = 0;
  while (right.length && right[0].end < to + min - eps) { right.shift(); goneRight++; }
  while (left.length && left[left.length - 1].start > to - min + eps) left.pop();
  if (right.length) right[0].start = moveRight ? to : Math.max(right[0].start, to);
  if (left.length) {
    const last = left[left.length - 1];
    last.end = moveLeft ? to : Math.min(last.end, to);
  }
  ed.phrases = left.concat(right);
  const grown = left[left.length - 1] || right[0];
  ed.edgeDrag = {at: to, bars: Math.round((djLowerBeat(ed, grown.end) - djLowerBeat(ed, grown.start)) / 4 * 10) / 10};
  // Die Auswahl bleibt auf derselben Phrase, die Indizes verschieben sich.
  const s = drag.sel;
  if (s < 0) ed.selPhrase = -1;
  else if (s <= drag.left) ed.selPhrase = s < left.length ? s : -1;
  else ed.selPhrase = s - drag.right >= goneRight ? left.length + s - drag.right - goneRight : -1;
}

// ── Zeichnen ────────────────────────────────────────────

// Je Pixel vier Werte aus den Spalten (Format wavedetail.STYLED_RATE). Liegen mehrere
// Spalten auf einem Pixel, gilt die hoechste, sonst linear dazwischen: das haelt die
// Kontur spitz und zeigt beim tiefen Zoom keine Bloecke.
function djReadColumns(ed, n, start, pxSec) {
  const {data, rate, style, lead} = ed.detail;
  const cols = data.length / 4, perPx = pxSec * rate, vals = new Float32Array(n * 4);
  // An ganzen Pixeln ausgerichtet: sonst wechselt beim Durchlaufen die Spalte, die
  // ein Pixel zusammenfasst, von Bild zu Bild, und die Kontur flimmert.
  const anchor = Math.round(start / pxSec) * pxSec;
  for (let x = 0; x < n; x++) {
    const at = (anchor + x * pxSec + lead) * rate, o = x * 4;
    if (perPx > 1) {
      const i0 = Math.max(0, Math.floor(at)), i1 = Math.min(cols, Math.floor(at + perPx));
      if (style !== "3band") {
        let top = -1, pick = -1;
        for (let i = i0; i < i1; i++) if (data[i * 4 + 3] > top) { top = data[i * 4 + 3]; pick = i; }
        if (pick >= 0) for (let k = 0; k < 4; k++) vals[o + k] = data[pick * 4 + k];
      } else {
        for (let i = i0; i < i1; i++) for (let k = 0; k < 3; k++) {
          if (data[i * 4 + k] > vals[o + k]) vals[o + k] = data[i * 4 + k];
        }
      }
    } else if (at > -1 && at < cols) {
      const i = Math.floor(at), f = at - i;
      for (let k = 0; k < 4; k++) {
        const a = i >= 0 ? data[i * 4 + k] : 0, b = i + 1 < cols ? data[(i + 1) * 4 + k] : a;
        vals[o + k] = a + (b - a) * f;
      }
    }
  }
  return vals;
}

// Farbe eines Pixels, auf 16 Stufen je Kanal, damit sich gleiche Farben einen Pfad teilen.
function djWaveColor(vals, o, style) {
  let r, g, b;
  if (style === "rgb") {
    r = vals[o]; g = vals[o + 1]; b = vals[o + 2];
    // Helle Flaeche: den weissen Anteil herausnehmen, sonst verschwinden Rosa und Hellblau.
    if (theme().dj.light) {
      const cut = Math.min(r, g, b) * 0.6;
      r = (r - cut) * 0.92 + 6; g = (g - cut) * 0.92 + 6; b = (b - cut) * 0.92 + 6;
    }
  } else if (style === "standard") { [r, g, b] = theme().dj.plain; }
  else {
    const c = theme().dj, l = vals[o] * vals[o], m = vals[o + 1] * vals[o + 1], h = vals[o + 2] * vals[o + 2], sum = l + m + h || 1;
    r = (c.bandLow[0] * l + c.bandMid[0] * m + c.bandHigh[0] * h) / sum;
    g = (c.bandLow[1] * l + c.bandMid[1] * m + c.bandHigh[1] * h) / sum;
    b = (c.bandLow[2] * l + c.bandMid[2] * m + c.bandHigh[2] * h) / sum;
  }
  return (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4);
}
function djFillColored(ctx, paths) {
  for (const [key, path] of paths) {
    ctx.fillStyle = `rgb(${(key >> 8) * 17},${((key >> 4) & 15) * 17},${(key & 15) * 17})`;
    ctx.fill(path);
  }
}

// RGB und Standard: je Pixel ein Trapez statt eines Rechtecks, die Kontur wird eine Linie.
function djFillRgb(ctx, vals, n, mid, amp, below, style) {
  const paths = new Map(), top = i => vals[Math.max(0, i) * 4 + 3];
  let prev = top(0) / 255 * amp;
  for (let x = 0; x < n - 1; x++) {
    // Nur ganz leicht geglaettet, die Spitzen sollen stehen bleiben.
    const cur = Math.max(0.5, (top(x - 1) + 4 * top(x) + top(x + 1)) / 6 / 255 * amp);
    const key = djWaveColor(vals, x * 4, style);
    let path = paths.get(key);
    if (!path) paths.set(key, path = new Path2D());
    path.moveTo(x, mid - prev); path.lineTo(x + 1, mid - cur);
    path.lineTo(x + 1, mid + cur * below); path.lineTo(x, mid + prev * below);
    path.closePath();
    prev = cur;
  }
  djFillColored(ctx, paths);
}

// 3-Band: je Band eine geschlossene Flaeche, Tiefen hinten, Hoehen vorn.
function djFillBands(ctx, vals, n, mid, amp, below) {
  const c = theme().dj;
  [c.bandLow, c.bandMid, c.bandHigh].forEach((color, k) => {
    const y = x => vals[x * 4 + k] / 255 * amp;
    ctx.beginPath();
    ctx.moveTo(0, mid - y(0));
    for (let x = 1; x < n; x++) ctx.lineTo(x, mid - y(x));
    for (let x = n - 1; x >= 0; x--) ctx.lineTo(x, mid + y(x) * below);
    ctx.closePath();
    ctx.fillStyle = `rgb(${color[0]},${color[1]},${color[2]})`;
    ctx.fill();
  });
}

// Tief hineingezoomt zeigt Rekordbox die einzelnen Schwingungen. Das steckt nur im Ton
// selbst, also aus dem PCM des Decks: von Pixel zu Pixel Minimum und Maximum, die
// Spalten liefern nur noch die Farbe. Etwas leiser als voll, sonst springt die Hoehe
// beim Uebergang von der Huellkurve.
function djFillSamples(ctx, ed, buffer, vals, n, start, pxSec, mid, amp) {
  const sr = buffer.sampleRate, left = buffer.getChannelData(0);
  const right = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : left;
  const anchor = Math.round(start / pxSec) * pxSec, style = ed.detail.style, paths = new Map();
  const gain = amp * 0.85, floor = 0.7;
  let lastTop = mid, lastBot = mid;
  for (let x = 0; x < n - 1; x++) {
    // Ein Sample Ueberlappung, damit die Linie von Pixel zu Pixel zusammenhaengt.
    const s0 = Math.max(0, Math.floor((anchor + x * pxSec) * sr) - 1);
    const s1 = Math.min(left.length, Math.ceil((anchor + (x + 1) * pxSec) * sr) + 1);
    let lo = Infinity, hi = -Infinity;
    for (let s = s0; s < s1; s++) {
      const v = (left[s] + right[s]) / 2;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    if (hi < lo) { lo = 0; hi = 0; }
    let top = mid - hi * gain, bot = mid - lo * gain;
    if (bot - top < floor * 2) { const c = (top + bot) / 2; top = c - floor; bot = c + floor; }
    if (x) {
      const key = djWaveColor(vals, x * 4, style);
      let path = paths.get(key);
      if (!path) paths.set(key, path = new Path2D());
      path.moveTo(x, lastTop); path.lineTo(x + 1, top); path.lineTo(x + 1, bot); path.lineTo(x, lastBot);
      path.closePath();
    }
    lastTop = top; lastBot = bot;
  }
  djFillColored(ctx, paths);
}

// half: nur die obere Haelfte, die Grundlinie liegt unten (Gesamtansicht).
function djDrawWave(ctx, ed, w, top, height, start, span, half) {
  const {data, style} = ed.detail;
  if (!data.length) return;
  const mid = half ? top + height : top + height / 2, amp = half ? height : height / 2 - 1, below = half ? 0 : 1;
  const pxSec = span / w, n = Math.ceil(w) + 1, vals = djReadColumns(ed, n, start, pxSec);
  const buffer = !half && ed.audio && ed.audio.buffer;
  // Ausserhalb des Tracks gibt es keinen Ton, auch keine Nulllinie.
  const x0 = (0 - start) / pxSec, x1 = ((ed.dur || span + start) - start) / pxSec;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, top - 1, x1 - x0, height + 2);
  ctx.clip();
  if (buffer && pxSec * buffer.sampleRate <= 160) djFillSamples(ctx, ed, buffer, vals, n, start, pxSec, mid, amp);
  else if (style === "3band") djFillBands(ctx, vals, n, mid, amp, below);
  else djFillRgb(ctx, vals, n, mid, amp, below, style);
  ctx.restore();
}

function djDrawLoop(ctx, ed, start, span, w, h, loop, faint) {
  const th = theme().dj;
  if (loop.end == null) {
    ctx.fillStyle = th.loopLine;
    ctx.fillRect(((loop.start - start) / span) * w, 0, 2, h);
    return;
  }
  const x0 = ((loop.start - start) / span) * w, x1 = ((loop.end - start) / span) * w;
  if (x1 < 0 || x0 > w) return;
  ctx.fillStyle = faint ? th.loopFaint : th.loop;
  ctx.fillRect(x0, 0, x1 - x0, h);
  ctx.fillStyle = th.loopLine;
  ctx.fillRect(x0, 0, 1.5, h);
  ctx.fillRect(x1 - 1.5, 0, 1.5, h);
}

function djDrawPhrases(ctx, ed, start, span, w, y, height, labels) {
  const th = theme().phrase;
  ctx.save();
  ctx.font = "600 11px -apple-system, sans-serif";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.globalAlpha = ed.uncertain && !ed.edited.phrases ? 0.6 : 1;
  ed.phrases.forEach((p, i) => {
    const x0 = Math.max(0, ((p.start - start) / span) * w);
    const x1 = Math.min(w, ((p.end - start) / span) * w);
    if (x1 <= x0) return;
    const token = phraseToken(p.kind, p.num);
    ctx.fillStyle = th[token] || th.outro;
    ctx.fillRect(x0, y, Math.max(1, x1 - x0 - 1), height);
    if (i === ed.selPhrase) {
      ctx.strokeStyle = theme().dj.selected;
      ctx.lineWidth = 2;
      ctx.strokeRect(x0 + 1, y + 1, Math.max(1, x1 - x0 - 3), height - 2);
    }
    if (labels && x1 - x0 > 40) {
      ctx.fillStyle = th[token + "Label"] || "#fff";
      // Bleibt beim Durchlaufen sichtbar, solange die Phrase im Bild ist.
      const label = djPhraseLabel(p).toUpperCase();
      ctx.fillText(label, x0 + 6, y + height / 2 + 0.5, x1 - x0 - 10);
    }
  });
  ctx.restore();
}

// Griff am Phrasenrand unter dem Mauszeiger, beim Ziehen mit Rasterlinie und Laenge.
function djDrawEdgeGrip(ctx, ed, start, span, w, top, laneTop, h, labels) {
  const at = ed.edgeDrag ? ed.edgeDrag.at : ed.edgeHover;
  if (at == null) return;
  const x = Math.round(((at - start) / span) * w), th = theme().dj;
  ctx.save();
  ctx.fillStyle = th.selected;
  ctx.fillRect(x - 2, laneTop, 4, h - laneTop);
  if (ed.edgeDrag) {
    ctx.fillRect(x, top, 1, laneTop - top);
    if (labels) {
      const label = t("dj.bars", {bars: String(ed.edgeDrag.bars)});
      ctx.font = "600 11px -apple-system, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const tw = ctx.measureText(label).width;
      const lx = x + tw + 16 > w ? x - tw - 12 : x + 4;
      ctx.fillStyle = th.labelBg;
      ctx.fillRect(lx, laneTop - 19, tw + 8, 16);
      ctx.fillStyle = th.labelFg;
      ctx.fillText(label, lx + 4, laneTop - 10.5);
    }
  }
  ctx.restore();
}

function djDrawCues(ctx, ed, start, span, w, h, top, big) {
  ctx.save();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const c of ed.cues) {
    const x = ((c.position_s - start) / span) * w;
    if (x < -60 || x > w + 60) continue;
    const col = djRgb(c.color), sel = ed.selCue === c.id;
    if (c.loop_s != null && big) {
      const x1 = ((c.loop_s - start) / span) * w;
      ctx.fillStyle = theme().dj.loopFaint;
      ctx.fillRect(x, top, x1 - x, h - top);
    }
    ctx.strokeStyle = col;
    ctx.lineWidth = sel ? 2.5 : (c.kind === "hot" ? 1.5 : 1);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillStyle = col;
    if (c.kind === "hot") {
      const fh = big ? 16 : 11, fw = big ? 16 : 11;
      ctx.fillRect(x, 0, fw, fh);
      ctx.fillStyle = "#fff";
      ctx.font = `700 ${big ? 11 : 9}px -apple-system, sans-serif`;
      ctx.fillText(c.label || "", x + fw / 2, fh / 2 + 0.5);
      if (big && c.name) {
        ctx.textAlign = "left";
        ctx.fillStyle = theme().dj.text;
        ctx.font = "11px -apple-system, sans-serif";
        ctx.fillText(c.name, x + fw + 4, fh / 2 + 0.5, 140);
        ctx.textAlign = "center";
      }
    } else {
      const s = big ? 7 : 5;
      ctx.beginPath();
      ctx.moveTo(x - s, 0); ctx.lineTo(x + s, 0); ctx.lineTo(x, s * 1.5);
      ctx.closePath(); ctx.fill();
      if (big && c.name) {
        ctx.textAlign = "left";
        ctx.fillStyle = theme().dj.text;
        ctx.font = "11px -apple-system, sans-serif";
        ctx.fillText(c.name, x + s + 3, 7, 140);
        ctx.textAlign = "center";
      }
    }
  }
  ctx.restore();
}

function djDrawBeats(ctx, ed, start, span, w, top, bottom) {
  const n = ed.bt.length;
  if (!n) return;
  const seg = ed.segs[djSegIndexAt(ed, start + span / 2)] || ed.segs[0];
  const pxBeat = (60 / seg.bpm / span) * w;
  const onlyBars = pxBeat < 5, sparse = pxBeat * 4 < 6;
  const every = pxBeat * 4 >= 34 ? 1 : pxBeat * 16 >= 34 ? 4 : pxBeat * 64 >= 34 ? 16 : 64;
  const beats = new Path2D(), bars = new Path2D();
  ctx.font = "10px -apple-system, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "bottom";
  const th = theme().dj, editing = ed.mode === "grid";
  ctx.fillStyle = th.text;
  for (let i = djLowerBeat(ed, start); i < n; i++) {
    const x = ((ed.bt[i] - start) / span) * w;
    if (x > w) break;
    const isBar = ed.bn[i] === 1;
    if (isBar && (ed.bb[i] - 1) % every === 0) ctx.fillText(String(ed.bb[i]), Math.round(x) + 3, bottom - 1);
    if (sparse ? !(isBar && (ed.bb[i] - 1) % 4 === 0) : (onlyBars && !isBar)) continue;
    const px = Math.round(x) + 0.5;
    const path = isBar ? bars : beats;
    path.moveTo(px, top); path.lineTo(px, bottom);
  }
  ctx.lineWidth = 1;
  ctx.strokeStyle = editing ? th.beatEdit : th.beat;
  ctx.stroke(beats);
  ctx.strokeStyle = editing ? th.barEdit : th.bar;
  ctx.stroke(bars);
}

// Tempowechsel: Linie und Marke mit dem neuen Tempo, gezeichnet wie die Stimmungs-
// Tags (.moodtag). Liegen sie zu dicht, stapeln sie sich; ist auch das voll, bleibt
// nur die Linie (Flex-Grids haben sehr viele Abschnitte).
function djDrawTempoBadges(ctx, ed, start, span, w, top, bottom, small) {
  if (ed.segs.length < 2) return;
  const th = theme().dj, pad = small ? 3 : 6, height = small ? 12 : 16;
  const free = small ? [-Infinity, -Infinity] : [-Infinity, -Infinity, -Infinity];
  ctx.save();
  ctx.font = `600 ${small ? 9 : 11}px -apple-system, sans-serif`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ed.segs.forEach((s, i) => {
    // Gleiches Tempo: nur ein nachjustiertes Raster ab hier, kein Tempowechsel.
    if (!i || Math.abs(s.bpm - ed.segs[i - 1].bpm) < 0.005) return;
    const x = ((s.t - start) / span) * w;
    if (x < -90 || x > w) return;
    ctx.fillStyle = th.tempo;
    ctx.fillRect(x, top, 1.5, bottom - top);
    const row = free.findIndex(limit => x >= limit);
    if (row < 0) return;
    const label = djFmtBpm(s.bpm), bw = ctx.measureText(label).width + pad * 2;
    const y = top + 2 + row * (height + 2);
    ctx.fillStyle = th.tempoBg;
    ctx.beginPath();
    ctx.roundRect(x + 3, y, bw, height, small ? 3 : 4);
    ctx.fill();
    ctx.fillStyle = th.tempoFg;
    ctx.fillText(label, x + 3 + pad, y + height / 2 + 0.5);
    free[row] = x + 3 + bw + 3;
  });
  ctx.restore();
}

// Cue "am Abspielkopf": von einem Schlag davor bis einen Takt danach. Steht der Kopf
// zwischen zwei Cues, gilt der naehere; Cues an derselben Stelle (Hot + Memory) zusammen.
function djNearCueWindow(ed, pos) {
  const bpm = ed.segs.length ? ed.segs[djSegIndexAt(ed, pos)].bpm : 0;
  const beat = bpm > 0 ? 60 / bpm : 0.5;
  return {before: beat, after: 4 * beat};
}
function djNearCueIds(ed, pos) {
  const win = djNearCueWindow(ed, pos);
  let best = null, bestDist = Infinity;
  for (const c of ed.cues) {
    const d = pos - c.position_s;
    if (d < -win.before || d > win.after) continue;
    if (Math.abs(d) < bestDist) { best = c; bestDist = Math.abs(d); }
  }
  if (!best) return [];
  return ed.cues.filter(c => Math.abs(c.position_s - best.position_s) < 0.01).map(c => String(c.id));
}
// Laeuft je Bild mit, fasst das DOM aber nur bei einem Wechsel an (Seitenleiste wird
// sonst per innerHTML neu gebaut und verloere den Fokus eines Namensfelds).
function djUpdateNearCue(ed, pos) {
  const ids = djNearCueIds(ed, pos), key = ids.join(",");
  if (key === ed.nearKey) return;
  ed.nearKey = key;
  ed.nearCues = new Set(ids);
  const host = ed.root && ed.root.querySelector(".dj-sidebody");
  if (!host) return;
  for (const row of host.querySelectorAll(".dj-row[data-id]")) {
    row.classList.toggle("near", ed.nearCues.has(row.dataset.id));
  }
}

function djBarsToNextCue(ed, pos) {
  let best = null;
  for (const c of ed.cues) {
    if (c.position_s > pos + 0.05 && (!best || c.position_s < best.position_s)) best = c;
  }
  if (!best || !ed.segs.length) return null;
  const bpm = ed.segs[djSegIndexAt(ed, pos)].bpm;
  return {bars: ((best.position_s - pos) * bpm) / 240, cue: best};
}

function djPaintZoom(ctx, w, h, entry) {
  const ed = entry.ed, lay = djZoomLayout(), pos = djPos(ed), span = ed.span;
  const start = pos - span / 2, waveTop = lay.top, waveH = h - lay.top - lay.lane - 2;
  const th = theme().dj;
  ctx.fillStyle = th.screen;
  ctx.fillRect(0, 0, w, h);
  if (!ed.detail) { drawLoadingWave(ctx, w, h, entry); return; }
  entry.loadingAnim = false;
  if (ed.loop) djDrawLoop(ctx, ed, start, span, w, h - lay.lane, ed.loop);
  djDrawWave(ctx, ed, w, waveTop, waveH, start, span);
  djDrawBeats(ctx, ed, start, span, w, waveTop, waveTop + waveH);
  djDrawPhrases(ctx, ed, start, span, w, h - lay.lane, lay.lane, true);
  djDrawEdgeGrip(ctx, ed, start, span, w, waveTop, h - lay.lane, h, true);
  djDrawCues(ctx, ed, start, span, w, h - lay.lane, lay.top, true);
  djDrawTempoBadges(ctx, ed, start, span, w, waveTop, waveTop + waveH, false);
  djUpdateNearCue(ed, pos);
  // Abspielkopf
  const mid = Math.round(w / 2);
  ctx.fillStyle = th.head;
  ctx.fillRect(mid - 1, 0, 2, h);
  ctx.beginPath(); ctx.moveTo(mid - 6, 0); ctx.lineTo(mid + 6, 0); ctx.lineTo(mid, 8); ctx.closePath(); ctx.fill();
  const next = djBarsToNextCue(ed, pos);
  if (next) {
    if (!DJ_BARS_FMT) DJ_BARS_FMT = new Intl.NumberFormat(LANG === "de" ? "de-DE" : "en-US", {minimumFractionDigits: 1, maximumFractionDigits: 1});
    const label = t("dj.bars_to", {bars: DJ_BARS_FMT.format(next.bars), cue: next.cue.label || "M"});
    ctx.font = "600 12px -apple-system, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = th.labelBg;
    ctx.fillRect(mid - 14 - tw, lay.top, tw + 8, 17);
    ctx.fillStyle = th.labelFg;
    ctx.fillText(label, mid - 10, lay.top + 3);
  }
}

// Die Wellenform der Gesamtansicht aendert sich nur mit Groesse oder Daten.
function djOverLayer(ed, entry, w, h, dpr, height, top) {
  const key = `${w}x${h}@${dpr}#${ed.detailRev}#${THEME_REV}#${ed.dur}#${djOverPad(ed).pre}#${djOverPad(ed).post}`;
  if (entry.layerCanvas && entry.layerKey === key) return entry.layerCanvas;
  const canvas = entry.layerCanvas || (entry.layerCanvas = document.createElement("canvas"));
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const range = djOverRange(ed);
  djDrawWave(ctx, ed, w, top, height, range.start, range.span, true);
  entry.layerKey = key;
  return canvas;
}

function djPaintOver(ctx, w, h, entry, dpr) {
  const ed = entry.ed, lay = djOverLayout(), pos = djPos(ed), dur = ed.dur;
  const th = theme().dj;
  ctx.fillStyle = th.screen;
  ctx.fillRect(0, 0, w, h);
  if (!ed.detail || !(dur > 0)) { drawLoadingWave(ctx, w, h, entry); return; }
  entry.loadingAnim = false;
  const waveH = h - lay.top - lay.lane - 2;
  ctx.drawImage(djOverLayer(ed, entry, w, h, dpr, waveH, lay.top), 0, 0, w, h);
  const {start, span} = djOverRange(ed), xAt = sec => ((sec - start) / span) * w;
  const x = xAt(pos), x0 = xAt(0);
  // Gespielter Teil abgedunkelt.
  ctx.fillStyle = th.shade;
  ctx.fillRect(x0, lay.top, Math.max(0, Math.min(x, xAt(dur)) - x0), waveH);
  if (ed.loop) djDrawLoop(ctx, ed, start, span, w, h - lay.lane, ed.loop, true);
  djDrawPhrases(ctx, ed, start, span, w, h - lay.lane, lay.lane, false);
  djDrawEdgeGrip(ctx, ed, start, span, w, lay.top, h - lay.lane, h, false);
  djDrawCues(ctx, ed, start, span, w, h - lay.lane, lay.top, false);
  djDrawTempoBadges(ctx, ed, start, span, w, lay.top, lay.top + waveH, true);
  // Temporaerer Cue-Punkt und der Ausschnitt der Zoom-Ansicht.
  if (ed.tempCue) {
    const cx = xAt(ed.tempCue);
    ctx.fillStyle = "#ff9a1f";
    ctx.beginPath();
    ctx.moveTo(cx - 5, h - lay.lane); ctx.lineTo(cx + 5, h - lay.lane); ctx.lineTo(cx, h - lay.lane - 7);
    ctx.closePath(); ctx.fill();
  }
  const zw = (ed.span / span) * w;
  ctx.strokeStyle = th.window;
  ctx.lineWidth = 1;
  ctx.strokeRect(Math.round(x - zw / 2) + 0.5, lay.top - 0.5, Math.max(2, Math.round(zw)), waveH + 1);
  ctx.fillStyle = th.head;
  ctx.fillRect(Math.round(x) - 1, 0, 2, h);
}

// Woher das angezeigte Beatgrid stammt, fuer den Hinweis an der roten BPM.
function djGridSourceKey(ed) {
  return ed.src && ed.src.grid === "rekordbox" ? "rekordbox" : "tracktab";
}
