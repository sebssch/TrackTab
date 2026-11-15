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
var DJ_METRO_SOUND;       // Einstellung dj_metronome_sound, gesetzt von app.js
var DJ_METRO_NOISE;       // Rauschpuffer je Kontext fuer die Metronom-Klaenge
var DJ_METRO_PREVIEW;     // eigener Kontext fuers Vorhoeren in den Einstellungen
// Verwaltung des Kontexts (siehe djCtx()): Leerlauf-Timer, Zeitpunkt des letzten
// Ersatzes, Uhr-Beobachtung {ctx, time, at}, ob 'devicechange' schon verdrahtet ist.
var DJ_CTX_IDLE;
var DJ_CTX_RENEWED;
var DJ_CTX_WATCH;
var DJ_CTX_DEVICE_HOOK;
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

// Safari verliert die Ausgabe eines AudioContext, wenn ein anderes Programm das
// Ausgabegeraet umstellt (Abtastrate, Geraetewechsel): das Deck laeuft weiter, zu
// hoeren ist nichts mehr, bis die Seite neu laedt. Ein NEUER Kontext bekommt die
// Ausgabe zurueck (<audio> ist nicht betroffen). Deshalb:
// - der Kontext lebt nur, solange das Deck spielt: nach 1,5 s Pause schliesst
//   djCtxIdle() ihn, das naechste Play baut einen frischen (in der Nutzergeste);
// - waehrend des Spielens ersetzt djCtxRenew() ihn an derselben Stelle, wenn sich
//   die Geraete aendern, die Audio-Uhr steht (djCtxWatch()) oder Safari ihn von
//   aussen anhaelt (djCtxStateChanged()).
// Die AudioBuffer der Decks haengen an keinem Kontext (new AudioBuffer()), nur der
// Lautstaerke-Knoten wird je Kontext neu angelegt (startSource()).
function djCtx() {
  if (!DJ_AUDIO_CTX) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    ctx.onstatechange = () => djCtxStateChanged(ctx);
    DJ_AUDIO_CTX = ctx;
    DJ_CTX_WATCH = null;
    if (!DJ_CTX_DEVICE_HOOK && navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
      DJ_CTX_DEVICE_HOOK = true;
      navigator.mediaDevices.addEventListener("devicechange", () => {
        if (djDeckPlaying()) djCtxRenew();
        else djCtxDrop();
      });
    }
  }
  return DJ_AUDIO_CTX;
}
// Spielt das Deck des offenen Editors ueber Web Audio (nicht der <audio>-Rueckfall)?
function djDeckPlaying() {
  return !!(DJ && DJ.audio && !DJ.audio.html && !DJ.audio.paused);
}
function djCtxDrop() {
  const ctx = DJ_AUDIO_CTX;
  clearTimeout(DJ_CTX_IDLE);
  DJ_AUDIO_CTX = null;
  DJ_CTX_WATCH = null;
  if (!ctx) return;
  ctx.onstatechange = null;
  if (ctx.state !== "closed") ctx.close().catch(() => {});
}
// Nach jedem Anhalten: kurz offen lassen (CUE antippen, Pause/Play), dann schliessen.
function djCtxIdle() {
  clearTimeout(DJ_CTX_IDLE);
  DJ_CTX_IDLE = setTimeout(() => { if (!djDeckPlaying()) djCtxDrop(); }, 1500);
}
// Ersetzt den Kontext waehrend der Wiedergabe, das Deck spielt an derselben Stelle weiter.
function djCtxRenew() {
  const now = performance.now();
  // Hoechstens alle 3 s, sonst dreht sich ein Fehler im Kreis.
  if (DJ_CTX_RENEWED && now - DJ_CTX_RENEWED < 3000) return;
  DJ_CTX_RENEWED = now;
  const ed = djDeckPlaying() ? DJ : null;
  const at = ed ? ed.audio.rawTime() : 0;
  if (ed) ed.audio.stopSource();
  djCtxDrop();
  if (!ed) return;
  const ctx = djCtx();
  if (ctx.state !== "running") ctx.resume().catch(() => {});
  ed.audio.startSource(at);
  ed.metroNext = 0;
  // Ohne Nutzergeste darf Safari den neuen Kontext stumm lassen. Dann sichtbar
  // anhalten statt still weiterlaufen -- das naechste Play (eine Geste) startet ihn.
  setTimeout(() => {
    if (DJ_AUDIO_CTX === ctx && ctx.state !== "running" && DJ === ed && djDeckPlaying()) ed.audio.pause();
  }, 400);
}
// Von aussen angehalten ("interrupted" in Safari, "suspended"): erst fortsetzen, sonst ersetzen.
function djCtxStateChanged(ctx) {
  if (ctx !== DJ_AUDIO_CTX || !djDeckPlaying()) return;
  if (ctx.state === "running" || ctx.state === "closed") return;
  ctx.resume().catch(() => {});
  setTimeout(() => {
    if (ctx === DJ_AUDIO_CTX && ctx.state !== "running" && djDeckPlaying()) djCtxRenew();
  }, 300);
}
// Laeuft je Bild (deck.tick()): steht die Audio-Uhr trotz "running" laenger still,
// liefert der Kontext nichts mehr an das Geraet.
function djCtxWatch() {
  const ctx = DJ_AUDIO_CTX;
  if (!ctx || ctx.state !== "running" || !djDeckPlaying()) { DJ_CTX_WATCH = null; return; }
  const now = performance.now(), w = DJ_CTX_WATCH;
  if (!w || w.ctx !== ctx || ctx.currentTime !== w.time) {
    DJ_CTX_WATCH = {ctx, time: ctx.currentTime, at: now};
    return;
  }
  if (now - w.at > 800) djCtxRenew();
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
      // Der Kontext kann seit dem letzten Start ersetzt worden sein (djCtxRenew()/djCtxIdle()).
      if (!deck.gain || deck.gain.context !== ctx) {
        deck.gain = ctx.createGain();
        deck.gain.connect(ctx.destination);
        deck.tick();
      }
      const now = ctx.currentTime, src = ctx.createBufferSource(), fade = ctx.createGain();
      src.buffer = deck.buffer;
      if (deck.range) { src.loop = true; src.loopStart = deck.range.start; src.loopEnd = deck.range.end; }
      fade.gain.setValueAtTime(0, now);
      fade.gain.linearRampToValueAtTime(1, now + FADE);
      src.connect(fade).connect(deck.gain);
      src.onended = () => {
        if (deck.src !== src) return;
        deck.src = null; deck.paused = true; deck.ended = true; deck.offset = deck.buffer.duration;
        djCtxIdle();
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
      clearTimeout(DJ_CTX_IDLE);
      // Ein noch offener, aber nicht laufender Kontext (von aussen angehalten) wird
      // hier in der Nutzergeste ersetzt statt nur fortgesetzt.
      if (DJ_AUDIO_CTX && DJ_AUDIO_CTX.state !== "running") djCtxDrop();
      const ctx = djCtx();
      if (ctx.state !== "running") ctx.resume().catch(() => {});
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
      djCtxIdle();
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
      if (!deck.paused) djCtxWatch();
    },
    close() {
      deck.abort.abort();
      if (deck.html) { deck.html.pause(); deck.html.removeAttribute("src"); return; }
      deck.stopSource();
      if (deck.gain) deck.gain.disconnect();
      deck.buffer = null; deck.paused = true;
      djCtxIdle();
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
      // Ohne Kontext anlegen: der Puffer ueberlebt so jeden Ersatz des Kontexts (siehe djCtx()).
      let buffer;
      try { buffer = new AudioBuffer({numberOfChannels: 2, length: Math.max(1, frames), sampleRate: rate}); }
      catch { buffer = djCtx().createBuffer(2, Math.max(1, frames), rate); }
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
          <button class="dj-mode" data-dj="mode" data-mode="grid" data-text="${esc(t("dj.mode_grid"))}">${esc(t("dj.mode_grid"))}</button>
          <button class="dj-mode" data-dj="mode" data-mode="phrase" data-text="${esc(t("dj.mode_phrase"))}">${esc(t("dj.mode_phrase"))}</button>
          <button class="dj-mode" data-dj="mode" data-mode="cue" data-text="${esc(t("dj.mode_cue"))}">${esc(t("dj.mode_cue"))}</button>
        </span>
        <span class="dj-sep"></span>
        <span class="dj-cuebar dj-group"></span>
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
    // Beatjumps stehen seit dem Zusammenlegen im Reiter Cue/Loop.
    mode: prefs.mode === "jump" ? "cue" : prefs.mode, side: prefs.side,
    segs: [], phrases: [], cues: [],
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
  root.addEventListener("pointerdown", () => {
    ed.pointerDown = true;
    const up = () => { ed.pointerDown = false; window.removeEventListener("pointerup", up, true); window.removeEventListener("pointercancel", up, true); };
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
  }, true);
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
  if (bpmCell) bpmCell.innerHTML = bpmCellHtml(ed.r.bp, ed.r.gridTempo, djGridSourceKey(ed), ed.r.gridSeq);
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
    if (len > best && !djIsRamp(ed, i)) { best = len; bpm = s.bpm; }
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
  ed.locks = {grid: !!(data.locks && data.locks.grid), phrases: !!(data.locks && data.locks.phrases)};
  ed.segs = data.grid.segments || djSegmentsFromBeats(data.grid.beats || []);
  // Das Tempo je Rampen-Schlag rechnet djPayload() beim Speichern jedes Mal neu.
  for (const s of ed.segs) if (s.ramp) s.ramp = {beats: s.ramp.beats, anchors: s.ramp.anchors || []};
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
        segments: ed.segs.map((seg, i) => {
          // Rampe: das Tempo je Schlag rechnet nur der Client genau (aus den ungerundeten
          // Zeiten), Rekordbox bekommt es so (gridstore.beats_with_tempo()).
          if (djIsRamp(ed, i)) return {...seg, ramp: {...seg.ramp, tempos: djRampTempos(ed, i)}};
          const k = seg.t < 0 ? Math.ceil(-seg.t * seg.bpm / 60) : 0;
          return {...seg, t: seg.t + k * 60 / seg.bpm, n: ((seg.n - 1 + k) % 4) + 1};
        }),
        locked: false,
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
  return djCollapseRamps(segs, beats);
}

// Eine gemessene Rampe (beatgrid._segment_transition(), dynamisches Grid aus Rekordbox)
// kommt als Folge kurzer Abschnitte an (je Schlag ein Tempo, wo es kurz gleich bleibt auch
// ein paar Schlaege). Liegen zwischen zwei langen Abschnitten (ab djRampLongBeats()) mindestens
// zwei kurze mit zusammen drei Schlaegen, wird daraus eine Rampe, jeder Schlag ein Anker --
// die Schlaege bleiben genau, wo sie waren, lassen sich aber wie eine Rampe bearbeiten. Ein
// einzelner kurzer Abschnitt (etwa Rekordbox' Rest an einem harten Wechsel) bleibt.
function djRampLongBeats() { return 8; }
function djCollapseRamps(segs, beats) {
  const ends = segs.map((s, i) => (i + 1 < segs.length ? segs[i + 1].t : Infinity));
  const long = i => (ends[i] - segs[i].t) * segs[i].bpm / 60 >= djRampLongBeats();
  const out = [];
  for (let i = 0; i < segs.length; i++) {
    let j = i;
    while (j < segs.length && !long(j)) j++;
    if (j - i >= 2 && i > 0 && j < segs.length) {
      const t0 = segs[i].t, t1 = segs[j].t;
      const times = beats.map(b => b[0] / 1000).filter(t => t >= t0 - 0.0005 && t < t1 - 0.0005);
      if (times.length >= 3) {
        out.push({t: t0, bpm: out[out.length - 1].bpm, n: segs[i].n,
                  ramp: {beats: times.length, anchors: times.slice(1).map((t, k) => [k + 1, t])}});
        i = j - 1;
        continue;
      }
    }
    out.push(segs[i]);
  }
  return out;
}

// Phrasen haengen am Beatgrid: aendert es sich, wandern ihre Grenzen mit. Gerechnet in
// Schlaegen ab dem Beginn des Abschnitts, das bleibt beim Verschieben und Dehnen gleich.
// Liegt eine Grenze auf einem Schlag, rastet sie dort ein. Das Trackende bleibt fest.
function djCarryPhrases(ed) {
  const from = ed.gridRef, to = ed.segs;
  ed.gridRef = to.map(s => ({t: s.t, bpm: s.bpm, ramp: s.ramp ? JSON.stringify(s.ramp) : ""}));
  if (!from || !ed.phrases.length) return;
  // Abschnitt dazu oder weg: nichts mitzurechnen, einrasten reicht (djSnapPhrases()).
  if (from.length !== to.length) { ed.phrasesCarried = true; return; }
  if (from.every((s, i) => s.t === to[i].t && s.bpm === to[i].bpm && s.ramp === ed.gridRef[i].ramp)) return;
  // In einer Rampe sind die Schlaege nicht gleich weit, mitrechnen geht nicht, einrasten schon.
  if (from.some(s => s.ramp) || to.some(s => s.ramp)) { ed.phrasesCarried = true; return; }
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

// ── Rampen ──────────────────────────────────────────────
// Eine Rampe ist ein Tempoabschnitt zwischen zwei festen Tempi, dessen Schlaege nicht im
// gleichen Abstand liegen: seg.ramp = {beats, anchors: [[k, sec], ...]}. Schlag 0 liegt auf
// seg.t, Schlag `beats` auf dem Beginn des naechsten Abschnitts. Anker heften einzelne
// Schlaege fest, dazwischen verteilt djRampTimes() die Schlaege weich. Ein dritter Wert 1
// ([k, sec, 1]) heisst "von Hand gesetzt" (Ziehen, "Schlag hierher", eingefuegter Schlag):
// solche Anker bleiben bei "An Audio anpassen" und geaenderter Schlagzahl fest stehen und
// verschwinden nur von Hand (Anker loesen, Schlag entfernen). Ohne ihn stammt er aus dem
// Einpassen bzw. der Analyse und darf ersetzt werden. seg.bpm bleibt das
// Tempo davor (Server-Pruefung, Anzeige), fuer die Schlaege zaehlt es nicht.
// Eine Rampe braucht einen Abschnitt davor und danach und mindestens einen Schlag, sonst
// gilt sie nicht. "Rampe Start" ohne Ende legt sie offen an (beats 0, djRampOpen()): bis
// "Rampe Ende" laeuft der Abschnitt im festen Tempo weiter.
function djIsRamp(ed, i) {
  const s = ed.segs[i];
  return !!(s && s.ramp && s.ramp.beats >= 1 && i > 0 && i + 1 < ed.segs.length);
}
function djRampEligible(ed, i) { return i > 0 && i + 1 < ed.segs.length; }
function djRampOpen(ed, i) { return !!(i > 0 && ed.segs[i] && ed.segs[i].ramp && !djIsRamp(ed, i)); }

// Tempowechsel-Marken, wie sie in den Wellenformen stehen: anderes Tempo, Rampenbeginn
// oder -ende. Ein nachjustiertes Raster mit gleichem Tempo zaehlt nicht.
function djIsTempoMark(ed, i) {
  if (i <= 0 || i >= ed.segs.length) return false;
  const s = ed.segs[i], p = ed.segs[i - 1];
  // Verglichen wird, was angezeigt wird: 127,996 und 128,00 sind kein Tempowechsel.
  return !!(s.ramp || p.ramp || djFmtBpm(s.bpm) !== djFmtBpm(p.bpm));
}

// Index der Tempowechsel-Marke an der Abspielposition (naechster Schlag bzw. 30 ms), sonst -1.
function djTempoMarkAt(ed) {
  if (!ed.segs.length || !ed.bt.length) return -1;
  const pos = djPos(ed), at = ed.bt[djNearestBeat(ed, pos)];
  return ed.segs.findIndex((s, i) => i > 0 && (Math.abs(s.t - at) < 0.002 || Math.abs(s.t - pos) <= djRampEndSnap()));
}

// Angezeigtes Tempo: im festen Abschnitt sein Tempo, in einer Rampe das des Schlags an der
// Position (Abstand zum naechsten), damit das Tempofeld dort nicht leer bleibt.
function djShownBpm(ed, pos) {
  if (!ed.segs.length) return 0;
  const i = djSegIndexAt(ed, pos);
  if (!djIsRamp(ed, i)) return ed.segs[i].bpm;
  const b = Math.max(0, djLowerBeat(ed, pos + 1e-6) - 1);
  return b + 1 < ed.bt.length ? 60 / (ed.bt[b + 1] - ed.bt[b]) : ed.segs[i].bpm;
}

// Erster Schlag des Grids. Dort beginnt immer ein Tempo, auch wenn der erste Abschnitt erst
// spaeter verankert ist -- das Grid laeuft bis zum Trackanfang zurueck (djRebuildBeats()).
function djGridStart(ed) { return ed.bt.length ? ed.bt[0] : (ed.segs.length ? ed.segs[0].t : 0); }

// Ziele der Tempowechsel-Pfeile: der Gridanfang (Nutzervorgabe: dort beginnt ein Tempo, man
// soll immer dorthin zurueck koennen) und alle Marken, gesetzte wie aus der Analyse erkannte.
function djTempoMarkTimes(ed) {
  if (!ed.segs.length) return [];
  const start = djGridStart(ed);
  const marks = ed.segs.map((s, i) => (djIsTempoMark(ed, i) ? s.t : null)).filter(x => x != null && x > start + 0.01);
  return [start, ...marks];
}

// Zur vorigen (dir -1) bzw. naechsten (dir 1) Tempowechsel-Marke springen.
function djGoTempoMark(ed, dir) {
  const pos = djPos(ed), marks = djTempoMarkTimes(ed);
  const to = dir > 0 ? marks.find(x => x > pos + 0.01) : marks.filter(x => x < pos - 0.01).at(-1);
  if (to == null) { note(t(dir > 0 ? "dj.tempo_mark_none_next" : "dj.tempo_mark_none_prev"), "soft"); return; }
  djSeek(ed, to);
}

// Gueltige Anker in Reihenfolge: innerhalb der Rampe, Zeit und Nummer streng steigend.
// Eigene Anker werden zuerst geprueft, damit ein eingepasster sie nie verdraengt.
function djRampAnchors(seg, next) {
  const n = seg.ramp.beats, out = [];
  const valid = (k, sec, lo, hi) => k > lo[0] && k < hi[0] && sec > lo[1] + 0.02 * (k - lo[0]) && sec < hi[1] - 0.02 * (hi[0] - k);
  const sorted = [...(seg.ramp.anchors || [])].sort((a, b) => a[0] - b[0]);
  const keep = [];
  for (const pass of [true, false]) {
    for (const a of sorted) {
      if (djAnchorManual(a) !== pass) continue;
      const [k, sec] = a;
      const lo = [[0, seg.t], ...keep].filter(([ak]) => ak < k).at(-1);
      const hi = [...keep, [n, next.t]].find(([ak]) => ak > k);
      if (!keep.some(([ak]) => ak === k) && valid(k, sec, lo, hi)) {
        keep.push(pass ? [k, sec, 1] : [k, sec]);
        keep.sort((x, y) => x[0] - y[0]);
      }
    }
  }
  return keep;
}
function djAnchorManual(a) { return !!(a && a[2]); }
function djManualAnchors(seg) { return (seg.ramp.anchors || []).filter(djAnchorManual); }

// Zeiten aller Schlaege 0..beats. Stueckweise kubische Hermite-Kurve ueber der Schlagnummer
// durch Anfang, Anker und Ende: an den Raendern mit dem Abstand der Nachbar-Tempi (das
// Tempo geht stetig ueber), an den Ankern mit dem Mittel der beiden Nachbarstrecken.
// Begrenzt nach Fritsch-Carlson, damit kein Schlag hinter seinen Vorgaenger rutscht.
function djRampTimes(ed, i) {
  const seg = ed.segs[i], prev = ed.segs[i - 1], next = ed.segs[i + 1], n = seg.ramp.beats;
  const pins = [[0, seg.t], ...djRampAnchors(seg, next), [n, next.t]];
  const m = pins.length, d = [], slope = new Array(m);
  for (let j = 0; j + 1 < m; j++) d.push((pins[j + 1][1] - pins[j][1]) / (pins[j + 1][0] - pins[j][0]));
  slope[0] = 60 / prev.bpm;
  slope[m - 1] = 60 / next.bpm;
  for (let j = 1; j + 1 < m; j++) slope[j] = (d[j - 1] + d[j]) / 2;
  for (let j = 0; j + 1 < m; j++) {
    const a = slope[j] / d[j], b = slope[j + 1] / d[j], s = a * a + b * b;
    if (s > 9) { const tau = 3 / Math.sqrt(s); slope[j] = tau * a * d[j]; slope[j + 1] = tau * b * d[j]; }
  }
  const out = new Float64Array(n + 1);
  for (let j = 0; j + 1 < m; j++) {
    const [ka, ta] = pins[j], [kb, tb] = pins[j + 1], h = kb - ka;
    for (let k = ka; k <= kb; k++) {
      const s = (k - ka) / h, s2 = s * s, s3 = s2 * s;
      out[k] = (2 * s3 - 3 * s2 + 1) * ta + (s3 - 2 * s2 + s) * h * slope[j]
             + (-2 * s3 + 3 * s2) * tb + (s3 - s2) * h * slope[j + 1];
    }
  }
  return out;
}

// Tempo je Rampen-Schlag aus dem Abstand zum naechsten Schlag, wie Rekordbox' PQTZ es fuehrt.
function djRampTempos(ed, i) {
  const times = djRampTimes(ed, i), out = [];
  for (let k = 0; k + 1 < times.length; k++) {
    out.push(Math.round(Math.min(400, Math.max(20, 60 / (times[k + 1] - times[k]))) * 1000) / 1000);
  }
  return out;
}

// Vorschlag fuer die Schlagzahl: die Laenge geteilt durch den mittleren Abstand der beiden Tempi.
// Vorschlag: die Zahl, die der Schaetzung am naechsten liegt und mit der die durchgehende
// Zaehlung vom Rampenbeginn (seg.n) am Ende auf Takt 1 trifft (Uebergaenge sind praktisch
// immer ganze Takte). Mit +-1 laesst sie sich danach frei aendern.
function djRampDefaultBeats(ed, i) {
  const span = ed.segs[i + 1].t - ed.segs[i].t;
  const avg = (60 / ed.segs[i - 1].bpm + 60 / ed.segs[i + 1].bpm) / 2;
  const est = span / avg, rest = djRampBeatRest(ed, i), [lo, hi] = djRampBeatLimits(ed, i);
  let best = 0;
  for (let n = rest || 4; n <= Math.max(hi, rest || 4); n += 4) {
    if (n >= lo && (!best || Math.abs(n - est) < Math.abs(best - est))) best = n;
  }
  return best || Math.max(1, Math.round(est));
}
// Rest der Schlagzahl modulo 4, mit dem das Ende auf Takt 1 faellt.
// Schlag im Takt, auf dem das Ende der Rampe i liegt.
function djRampEndBeat(ed, i) { return ((ed.segs[i].n - 1 + ed.segs[i].ramp.beats) % 4) + 1; }
function djRampBeatRest(ed, i) { return ((5 - ed.segs[i].n) % 4 + 4) % 4; }

// Kleinste und groesste Schlagzahl, bei der der mittlere Abstand noch ein Tempo von 20 bis 400 BPM ergibt.
function djRampBeatLimits(ed, i) {
  const span = ed.segs[i + 1].t - ed.segs[i].t;
  return [Math.max(1, Math.ceil(span * 20 / 60)), Math.max(1, Math.floor(span * 400 / 60))];
}

// Nummer des Rampen-Schlags an einem Index von ed.bt (0 = Rampenbeginn), sonst -1.
function djRampBeatIndex(ed, b) {
  return ed.bk && b >= 0 && b < ed.bk.length ? ed.bk[b] : -1;
}

function djRampSeg(ed) {
  const i = ed.segs.length ? djSegIndexAt(ed, djPos(ed)) : -1;
  return djIsRamp(ed, i) ? i : -1;
}

// Rampe Start: Tempowechsel am naechsten Schlag (dort stimmt das alte Grid noch) bzw. die
// Marke, die dort schon steht. Die Rampe bleibt offen (beats 0), bis "Rampe Ende" sie
// schliesst -- auch wenn schon eine Marke folgt, die gehoert oft gar nicht dazu.
function djRampStart(ed) {
  if (!ed.segs.length) return;
  const b = djNearestBeat(ed, djPos(ed)), at = ed.bt[b];
  let i = djSegIndexAt(ed, at);
  const exists = Math.abs(ed.segs[i].t - at) < 0.002;
  if (exists && i === 0) { note(t("dj.ramp_start_first"), "soft"); return; }
  if (exists && djIsRamp(ed, i)) { note(t("dj.ramp_exists"), "soft"); return; }
  if (!exists && djIsRamp(ed, i)) { note(t("dj.ramp_no_segment"), "soft"); return; }
  djPush(ed);
  if (!exists) { ed.segs.splice(i + 1, 0, {t: at, bpm: ed.segs[i].bpm, n: ed.bn[b]}); i++; }
  ed.segs[i].ramp = {beats: 0, anchors: []};
  djTouched(ed, "grid");
}

// Unterschied in BPM, unter dem zwei Abschnitte als dasselbe Tempo gelten (alte Marken nach dem Rampenende).
function djSameTempo() { return 0.5; }

// Abstand, ab dem "Rampe Ende" eine vorhandene Marke uebernimmt statt eine neue zu setzen.
function djRampEndSnap() { return 0.03; }

// Rampe Ende: erster Schlag im neuen Tempo, genau an der Abspielposition (das alte Grid
// stimmt dort nicht mehr). Steht dort schon eine Marke (Sprung per Marken-Navigation), wird
// sie das Ende. Die Rampe beginnt an der letzten offenen Rampe davor; Marken dazwischen
// fallen weg. Steht die Position in einer fertigen Rampe, endet diese hier frueher. Das neue
// Tempo kommt vom naechsten Abschnitt danach, ohne einen vom Abschnitt an der Position --
// danach im Tempofeld pruefen.
function djRampEnd(ed) {
  if (!ed.segs.length) return;
  const pos = djClampOver(ed, djPos(ed)), snap = djRampEndSnap();
  let start = -1;
  ed.segs.forEach((s, j) => { if (s.t < pos - snap && djRampOpen(ed, j)) start = j; });
  const inside = djSegIndexAt(ed, pos - snap);
  if (djIsRamp(ed, inside) && inside > start) start = inside;
  if (start < 1) { note(t("dj.ramp_end_no_start"), "soft"); return; }
  const marker = ed.segs.findIndex((s, j) => j > start && Math.abs(s.t - pos) <= snap);
  const endAt = marker >= 0 ? ed.segs[marker].t : pos;
  if ((endAt - ed.segs[start].t) * ed.segs[start - 1].bpm / 60 < 1.5) { note(t("dj.ramp_too_short"), "soft"); return; }
  djPush(ed);
  const after = ed.segs.find(s => s.t > endAt + snap);
  const here = ed.segs[djSegIndexAt(ed, endAt)];
  const end = marker >= 0 ? ed.segs[marker]
    : {t: Math.round(pos * 10000) / 10000, bpm: (after || here).bpm, n: 1};
  // Alles zwischen Start und Ende liegt jetzt in der Rampe. Das Ende legt das neue Tempo
  // fest: folgende Abschnitte mit (fast) demselben Tempo sind alte Messungen desselben
  // Tempos, die meist spaeter beginnen -- sie fallen weg, bis ein anderes Tempo kommt.
  const rest = ed.segs.filter(s => s.t > endAt + snap);
  while (rest.length && !rest[0].ramp && Math.abs(rest[0].bpm - end.bpm) < djSameTempo()) rest.shift();
  ed.segs = [...ed.segs.slice(0, start + 1), end, ...rest];
  const seg = ed.segs[start];
  seg.bpm = ed.segs[start - 1].bpm;
  seg.ramp = {beats: 1, anchors: ((seg.ramp && seg.ramp.anchors) || []).filter(([, at]) => at < endAt)};
  seg.ramp.beats = djRampDefaultBeats(ed, start);
  seg.ramp.anchors = seg.ramp.anchors.filter(([k]) => k < seg.ramp.beats);
  djRenumberFrom(ed, start);
  djTouched(ed, "grid");
  djRampFit(ed, start, true);
}

function djDissolveRamp(ed) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  djPush(ed);
  delete ed.segs[i].ramp;
  djRenumberFrom(ed, i);
  djTouched(ed, "grid");
}

// Schlagzahl aendern: der Rampenbeginn bleibt, die Zaehlung danach rueckt mit (wer nach der
// Rampe einen Schlag zu frueh oder zu spaet bei Takt 1 landet, korrigiert das hier).
// Eigene Anker bleiben mit Nummer und Zeit stehen, eingepasste fallen weg und werden neu
// eingepasst. Liegt auf dem wegfallenden Schlag ein eigener Anker, erst diesen loesen.
function djRampCount(ed, delta) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  const ramp = ed.segs[i].ramp, [lo, hi] = djRampBeatLimits(ed, i);
  const beats = ramp.beats + delta;
  if (beats < Math.max(1, lo) || beats > hi) return;
  const manual = djManualAnchors(ed.segs[i]);
  if (manual.some(([k]) => k >= beats)) { note(t("dj.ramp_count_pinned"), "soft"); return; }
  djPush(ed);
  ed.segs[i].ramp = {beats, anchors: manual};
  djRenumberFrom(ed, i);
  djTouched(ed, "grid");
  djRampFit(ed, i, true);
}

// Alle Schlaege der Rampe dort festhalten, wo sie gerade liegen (fehlende Anker als
// eingepasste), damit Einfuegen/Entfernen eines Schlags die uebrigen nicht verschiebt.
function djRampPinAll(ed, i) {
  const seg = ed.segs[i], rt = djRampTimes(ed, i), own = new Map();
  for (const a of djRampAnchors(seg, ed.segs[i + 1])) own.set(a[0], a);
  const out = [];
  for (let k = 1; k < seg.ramp.beats; k++) out.push(own.get(k) || [k, Math.round(rt[k] * 10000) / 10000]);
  return out;
}

// Mindestabstand eines eingefuegten Schlags zu seinen Nachbarn (s).
function djRampInsertGap() { return 0.03; }

// Schlag hier einfuegen: ein neuer, eigener Anker genau an der Abspielposition, zwischen
// den beiden Rampen-Schlaegen um sie herum. Alle anderen Schlaege bleiben, wo sie sind,
// die folgenden zaehlen eine Nummer weiter -- wie bei "+", rueckt die Zaehlung nach der
// Rampe mit (Anzeige "Ende auf ...").
function djRampInsertBeat(ed) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  const seg = ed.segs[i], [, hi] = djRampBeatLimits(ed, i);
  if (seg.ramp.beats + 1 > hi) { note(t("dj.ramp_insert_full"), "soft"); return; }
  const pos = djPos(ed), rt = djRampTimes(ed, i);
  let kb = 0;
  while (kb + 1 < seg.ramp.beats && rt[kb + 1] <= pos) kb++;
  if (pos - rt[kb] < djRampInsertGap() || rt[kb + 1] - pos < djRampInsertGap()) { note(t("dj.ramp_insert_close"), "soft"); return; }
  djPush(ed);
  const anchors = djRampPinAll(ed, i).map(a => (a[0] > kb ? [a[0] + 1, ...a.slice(1)] : a));
  anchors.push([kb + 1, Math.round(pos * 10000) / 10000, 1]);
  seg.ramp = {beats: seg.ramp.beats + 1, anchors: anchors.sort((a, b) => a[0] - b[0])};
  djRenumberFrom(ed, i);
  djTouched(ed, "grid");
}

// Schlag hier entfernen: der Rampen-Schlag naechst der Abspielposition faellt weg, auch
// ein eigener Anker darauf. Die uebrigen bleiben, wo sie sind, die Zaehlung danach rueckt mit.
function djRampRemoveBeat(ed) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  const seg = ed.segs[i], [lo] = djRampBeatLimits(ed, i);
  const k = djRampBeatIndex(ed, djNearestBeat(ed, djPos(ed)));
  if (k <= 0 || k >= seg.ramp.beats) { note(t("dj.ramp_remove_edge"), "soft"); return; }
  if (seg.ramp.beats - 1 < Math.max(1, lo)) { note(t("dj.ramp_remove_min"), "soft"); return; }
  djPush(ed);
  const anchors = djRampPinAll(ed, i).filter(a => a[0] !== k).map(a => (a[0] > k ? [a[0] - 1, ...a.slice(1)] : a));
  seg.ramp = {beats: seg.ramp.beats - 1, anchors};
  djRenumberFrom(ed, i);
  djTouched(ed, "grid");
}

// Schlaege der Rampe an die Anschlaege im Audio legen (/api/dj-ramp-fit, beatgrid.fit_ramp()):
// jeder gefundene Schlag wird ein Anker. auto: nach "Rampe Ende" bzw. geaenderter Schlagzahl
// -- dann gehoert das Einpassen zum selben Rueckgaengig-Schritt und Fehler bleiben still.
async function djRampFit(ed, i = djRampSeg(ed), auto = false) {
  if (!djIsRamp(ed, i) || ed.rampFitBusy) return;
  const seg = ed.segs[i], prev = ed.segs[i - 1], next = ed.segs[i + 1], beats = seg.ramp.beats;
  // Eigene Anker bleiben fest, eingepasst wird nur dazwischen (beatgrid.fit_ramp(fixed)).
  const manual = djRampAnchors(seg, next).filter(djAnchorManual), sig = JSON.stringify(manual);
  const same = () => ed.segs[i] && ed.segs[i].t === seg.t && djIsRamp(ed, i)
    && ed.segs[i].ramp.beats === beats && ed.segs[i + 1].t === next.t
    && JSON.stringify(djRampAnchors(ed.segs[i], ed.segs[i + 1]).filter(djAnchorManual)) === sig;
  ed.rampFitBusy = true;
  djRenderTools(ed);
  try {
    const res = await fetch("/api/dj-ramp-fit", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: ed.path, t0: seg.t, t1: next.t, bpm0: prev.bpm, bpm1: next.bpm, beats,
                            fixed: manual.map(([k, sec]) => [k, sec])})});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
    // Inzwischen geaendert (weiter bearbeitet, Rueckgaengig): das Ergebnis passt nicht mehr.
    if (ed.closed || !same()) return;
    const fixedK = new Set(manual.map(([k]) => k));
    const hits = data.beats.filter(([k, , h]) => h > 0 && !fixedK.has(k));
    const free = data.beats.length - fixedK.size;
    if (!hits.length) { if (!auto) note(t("dj.ramp_fit_none"), "soft"); return; }
    if (!auto) djPush(ed);
    ed.segs[i].ramp = {beats, anchors: [...manual, ...hits.map(([k, sec]) => [k, Math.round(sec * 10000) / 10000])]
      .sort((a, b) => a[0] - b[0])};
    djTouched(ed, "grid");
    if (!auto) note(t(manual.length ? "dj.ramp_fit_done_fixed" : "dj.ramp_fit_done", {n: hits.length, total: free, fixed: manual.length}));
  } catch (err) {
    if (!auto) note(t("dj.ramp_fit_failed", {error: err.message}), true);
  } finally {
    ed.rampFitBusy = false;
    if (!ed.closed) djRenderTools(ed);
  }
}

// Einen eigenen Anker setzen: Schlag k der Rampe liegt bei sec. Anker, die damit die
// Reihenfolge verletzen, fallen weg (beim Ziehen haelt der Aufrufer ihn zwischen den Nachbarn).
function djSetRampAnchor(ed, i, k, sec) {
  const ramp = ed.segs[i].ramp;
  const kept = (ramp.anchors || []).filter(([ak, at]) => ak !== k && (ak < k ? at < sec : at > sec));
  ed.segs[i].ramp = {...ramp, anchors: [...kept, [k, Math.round(sec * 10000) / 10000, 1]].sort((a, b) => a[0] - b[0])};
}

// Den Rampen-Schlag naechst der Abspielposition genau dorthin legen.
function djRampAnchorHere(ed) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  const pos = djPos(ed), k = djRampBeatIndex(ed, djNearestBeat(ed, pos));
  if (k <= 0 || k >= ed.segs[i].ramp.beats) { note(t("dj.ramp_anchor_edge"), "soft"); return; }
  djPush(ed);
  djSetRampAnchor(ed, i, k, pos);
  djTouched(ed, "grid");
}

// Den Anker naechst der Abspielposition loesen.
function djRampDropAnchor(ed) {
  const i = djRampSeg(ed);
  if (i < 0) return;
  const anchors = ed.segs[i].ramp.anchors || [];
  if (!anchors.length) return;
  const pos = djPos(ed);
  const near = anchors.reduce((a, b) => (Math.abs(b[1] - pos) < Math.abs(a[1] - pos) ? b : a));
  djPush(ed);
  ed.segs[i].ramp = {...ed.segs[i].ramp, anchors: anchors.filter(a => a !== near)};
  djTouched(ed, "grid");
}

function djRebuildBeats(ed) {
  // Am Trackanfang gibt es kein Tempo davor: dort ist eine Rampe ein normaler Abschnitt.
  // Am Ende bleibt sie als offene Rampe stehen (djRampOpen()), bis "Rampe Ende" kommt.
  if (ed.segs[0] && ed.segs[0].ramp) delete ed.segs[0].ramp;
  djCarryPhrases(ed);
  const times = [], nums = [], bars = [], rampK = [];
  const dur = ed.dur || 0, near = djGridStartTolerance();
  let bar = 0;
  ed.segs.forEach((seg, i) => {
    if (djIsRamp(ed, i)) {
      const rt = djRampTimes(ed, i);
      for (let k = 0; k < seg.ramp.beats; k++) {
        const num = ((seg.n - 1 + k) % 4) + 1;
        if (num === 1) bar++;
        times.push(rt[k]); nums.push(num); bars.push(bar); rampK.push(k);
      }
      return;
    }
    const gap = 60 / seg.bpm;
    const end = i + 1 < ed.segs.length ? ed.segs[i + 1].t : dur;
    // Vor dem ersten Abschnitt laeuft das Grid bis zum Trackanfang zurueck.
    const first = i === 0 ? -Math.floor((seg.t + near) / gap + 1e-9) : 0;
    // Ein Schlag knapp vor dem naechsten Abschnitt waere ein doppelter (wie beatgrid.beats_of()).
    const last = i + 1 < ed.segs.length ? end - 0.5 * gap : end - 1e-6;
    for (let k = first; ; k++) {
      const sec = seg.t + k * gap;
      if (sec >= last) break;
      if (sec < -near) continue;
      const num = (((seg.n - 1 + k) % 4) + 4) % 4 + 1;
      if (num === 1) bar++;
      times.push(Math.max(0, sec)); nums.push(num); bars.push(bar); rampK.push(-1);
    }
  });
  if (ed.phrasesCarried) djSnapPhrases(ed, times);
  // Tempowechsel in Reihenfolge, fuer den Titel. Kleine Schwankungen fallen heraus, ebenso
  // Abschnitte unter einem Takt: das ist ein nachjustiertes Raster, kein Tempo. Eine Rampe
  // ist der Weg zwischen zwei Tempi, kein eigenes.
  ed.tempoSeq = [];
  const steady = ed.segs.filter((s, i) => !djIsRamp(ed, i));
  ed.segs.forEach((seg, i) => {
    if (djIsRamp(ed, i)) return;
    const len = (i + 1 < ed.segs.length ? ed.segs[i + 1].t : dur) - seg.t;
    if (len * seg.bpm / 60 < 4) return;
    if (!ed.tempoSeq.length || Math.abs(seg.bpm - ed.tempoSeq.at(-1)) >= 1) ed.tempoSeq.push(seg.bpm);
  });
  ed.tempoSpan = steady.length
    ? [Math.min(...steady.map(s => s.bpm)), Math.max(...steady.map(s => s.bpm))] : null;
  ed.bt = Float64Array.from(times);
  ed.bn = Uint8Array.from(nums);
  ed.bb = Int32Array.from(bars);
  ed.bk = Int32Array.from(rampK);
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
// Der Takt zaehlt ueber Tempowechsel und Rampen durchgehend weiter, wie Rekordbox' eigene
// Grids (an 129 Tracks mit Tempowechsel nie ein Taktsprung): ein angebrochener Takt koennte
// beim Sync, der auch die Stelle im Takt angleicht, das andere Deck springen lassen, und
// Auto Cue lehnt Grids mit Taktsprung ab. Liegt die 1 nach einem Wechsel daneben, stimmt
// die 1 davor oder die Schlagzahl der Rampe nicht.
function djSegBeats(ed, j) {
  return djIsRamp(ed, j) ? ed.segs[j].ramp.beats : Math.round((ed.segs[j + 1].t - ed.segs[j].t) * ed.segs[j].bpm / 60);
}
// Nummern der Abschnitte nach i aus der Zaehlung von i.
function djRenumberFrom(ed, i) {
  for (let j = i + 1; j < ed.segs.length; j++) {
    const k = djSegBeats(ed, j - 1);
    ed.segs[j].n = (((ed.segs[j - 1].n - 1 + k) % 4) + 4) % 4 + 1;
  }
}
// Nummern der Abschnitte vor i aus der Zaehlung von i (1.1 gilt fuer das ganze Grid).
function djRenumberBack(ed, i) {
  for (let j = i - 1; j >= 0; j--) {
    const k = djSegBeats(ed, j);
    ed.segs[j].n = (((ed.segs[j + 1].n - 1 - k) % 4) + 4) % 4 + 1;
  }
}

// ── Aenderungen, Rueckgaengig ───────────────────────────

function djState(ed) {
  return {segs: JSON.stringify(ed.segs),
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
  if (snap.segs !== now.segs) {
    ed.segs = JSON.parse(snap.segs); fields.push("grid");
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
// Nur Memory Cues: Pfeile neben "Memory Cue setzen".
function djGoMemory(ed, dir) {
  const stops = ed.cues.filter(c => c.kind === "memory").map(c => c.position_s).sort((x, y) => x - y);
  const pos = djPos(ed);
  const to = dir > 0 ? stops.find(x => x > pos + 0.05) : stops.filter(x => x < pos - 0.05).at(-1);
  if (to == null) { note(t(dir > 0 ? "dj.memory_none_next" : "dj.memory_none_prev"), "soft"); return; }
  djSeek(ed, to);
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
    djMetroVoice(DJ_AUDIO_CTX, at, ed.bn[i] === 1, DJ_METRO_SOUND);
    ed.metroNext = ed.bt[i] + 0.001;
  }
}

// Ein Metronom-Klang zur Zeit "at", ohne Audiodateien aus Oszillatoren und Rauschen gebaut.
// accent = die 1 im Takt (hoeher bzw. heller). Alle Klaenge liegen weit ueber den Mitten
// oder haben einen harten Anschlag: ein leiser Sinus bei 1 kHz (die allererste Fassung)
// ging in einem gemasterten Track unter, obwohl er spielte.
function djMetroNoise(ctx) {
  if (DJ_METRO_NOISE && DJ_METRO_NOISE.ctx === ctx) return DJ_METRO_NOISE.buf;
  const buf = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.25), ctx.sampleRate);
  const d = buf.getChannelData(0);
  // Fester Startwert statt Math.random(): sonst klingt (und pegelt) jeder neue Kontext
  // etwas anders -- gemessen schwankte die Spitze des Klicks zwischen 0,55 und 1,03.
  let x = 0x5eed;
  for (let i = 0; i < d.length; i++) {
    x = (Math.imul(x, 1664525) + 1013904223) >>> 0;
    d[i] = x / 2147483648 - 1;
  }
  DJ_METRO_NOISE = {ctx, buf};
  return buf;
}
// Verstaerkung je Klang, so eingestellt, dass jeder Klang allein knapp unter Vollaussteuerung
// liegt (Spitze ~0,95, offline gemessen) -- lauter ginge nur mit Uebersteuern.
const DJ_METRO_LEVEL = {beep: 2.2, click: 1.43, wood: 1.28, rim: 1.54, cowbell: 1.5, hihat: 1.29};
function djMetroVoice(ctx, at, accent, kind) {
  const out = ctx.createGain();
  out.gain.value = DJ_METRO_LEVEL[kind] || DJ_METRO_LEVEL.beep;
  out.connect(ctx.destination);
  const env = (node, peak, decay) => {
    const g = ctx.createGain();
    g.gain.setValueAtTime(peak, at);
    g.gain.exponentialRampToValueAtTime(0.001, at + decay);
    node.connect(g).connect(out);
    return g;
  };
  const tone = (type, freq, peak, decay, drop) => {
    const o = ctx.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, at);
    if (drop) o.frequency.exponentialRampToValueAtTime(freq * drop, at + decay);
    env(o, peak, decay);
    o.start(at); o.stop(at + decay + 0.01);
  };
  const noise = (filter, freq, q, peak, decay) => {
    const src = ctx.createBufferSource(), f = ctx.createBiquadFilter();
    src.buffer = djMetroNoise(ctx);
    f.type = filter; f.frequency.value = freq; f.Q.value = q;
    src.connect(f);
    env(f, peak, decay);
    src.start(at); src.stop(at + decay + 0.01);
  };
  switch (kind) {
    case "click":    // kurzer, trockener Tick wie in einer DAW
      noise("bandpass", accent ? 6000 : 4200, 1.2, 1.3, 0.025);
      break;
    case "wood":     // Woodblock: kurzer Ton mit fallender Hoehe und hartem Anschlag
      tone("triangle", accent ? 1700 : 1250, 0.7, 0.06, 0.8);
      noise("bandpass", accent ? 2600 : 2000, 4, 0.5, 0.012);
      break;
    case "rim":      // Rimshot/Stick: hoher Ton plus Rauschspitze
      tone("triangle", accent ? 2100 : 1700, 0.35, 0.035);
      noise("highpass", 3000, 0.7, 0.45, accent ? 0.045 : 0.03);
      break;
    case "cowbell":  // zwei verstimmte Rechtecke durch einen Bandpass, wie die 808-Cowbell
      for (const f of accent ? [700, 1040] : [540, 800]) tone("square", f, 0.22, 0.18);
      out.disconnect();
      { const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = accent ? 3000 : 2600; bp.Q.value = 1;
        out.connect(bp).connect(ctx.destination); out.gain.value *= 1.6; }
      break;
    case "hihat":    // geschlossene Hi-Hat, die 1 klingt etwas offener
      noise("highpass", 7000, 0.7, 0.55, accent ? 0.09 : 0.04);
      break;
    default:         // "beep": Rechteck, wie bisher
      tone("square", accent ? 3200 : 2400, 0.45, 0.07);
  }
}

// Vorhoeren in den Einstellungen: ein Takt (1 + drei Schlaege) bei 120 BPM ueber einen
// eigenen Kontext -- DJ_AUDIO_CTX lebt nur, solange das Deck spielt (siehe djCtx()).
function djMetroPreview(kind) {
  try {
    if (!DJ_METRO_PREVIEW || DJ_METRO_PREVIEW.state === "closed") {
      DJ_METRO_PREVIEW = new (window.AudioContext || window.webkitAudioContext)();
    }
    const ctx = DJ_METRO_PREVIEW;
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    const t0 = ctx.currentTime + 0.05;
    for (let k = 0; k < 4; k++) djMetroVoice(ctx, t0 + k * 0.5, k === 0, kind);
    clearTimeout(ctx._closeTimer);
    ctx._closeTimer = setTimeout(() => { if (ctx === DJ_METRO_PREVIEW) { ctx.close().catch(() => {}); DJ_METRO_PREVIEW = null; } }, 3000);
  } catch (err) { /* ohne Web Audio kein Vorhoeren */ }
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
  // BPM-Zelle: immer der Wert aus der Datei, rot bei Abweichung vom Beatgrid (Nutzervorgabe),
  // Tempo und Tempowechsel des Grids im Tooltip.
  const cellKey = `${ed.tempoSpan}|${ed.tempoSeq}`;
  const bpmCell = djRowCell(ed, "bpmCell", ".bpmcell");
  if (cellKey !== ed._bpmCell && bpmCell) {
    ed._bpmCell = cellKey;
    bpmCell.innerHTML = bpmCellHtml(ed.r.bp, ed.tempoSpan, djGridSourceKey(ed), ed.tempoSeq);
  }
  // Rampen haben eigene Werkzeuge: wechselt die Position hinein oder heraus, neu aufbauen.
  const rampKey = seg >= 0 ? `${djIsRamp(ed, seg)}|${ed.segs.some((s, j) => djRampOpen(ed, j) && s.t <= pos + 1e-6)}|${djTempoMarkAt(ed) > 0}|${djTempoMarkTimes(ed).map(x => (x < pos - 0.01 ? "<" : x > pos + 0.01 ? ">" : "=")).join("")}` : "";
  if (ed.mode === "grid" && rampKey !== ed._rampKey) { ed._rampKey = rampKey; djRenderTools(ed); }
  // Nachholen, was waehrend einer Eingabe im Tempofeld liegen blieb -- nicht, solange die Maus
  // auf einem Knopf der Leiste gedrueckt ist (ein Neuaufbau zwischen mousedown und mouseup
  // verschluckte den Klick).
  else if (ed._toolsStale) {
    const host = ed.root.querySelector(".dj-tools");
    if (host && !host.querySelector(":active")) djRenderTools(ed);
  }
  djFollowPhrase(ed, pos);
  // Memory-Pfeile: gesperrt, sobald in der Richtung kein Memory Cue mehr liegt.
  const nav = djMemoryNav(ed), navKey = `${nav.prev}|${nav.next}`;
  if (navKey !== ed._memNav) { ed._memNav = navKey; djSyncCueBar(ed); }
  // Im Tempofeld in einer Rampe das Tempo des Schlags an der Position (aendert sich je Schlag).
  const fieldBpm = seg >= 0 ? Math.round(djShownBpm(ed, pos) * 100) / 100 : 0;
  if (seg !== ed._segShown || fieldBpm !== ed._bpmShown) {
    ed._segShown = seg; ed._bpmShown = fieldBpm;
    const input = ed.root.querySelector(".dj-bpminput");
    if (input && document.activeElement !== input) input.value = seg >= 0 ? djFmtBpm(fieldBpm) : "";
    const part = ed.root.querySelector(".dj-seginfo");
    if (part) part.textContent = seg >= 0 ? djSegInfo(ed, seg) : "";
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

function djSetBpm(ed, bpm) {
  if (!(bpm >= 20 && bpm <= 400)) { note(t("dj.bpm_invalid"), "soft"); djRenderTools(ed); return; }
  if (djRampSeg(ed) >= 0) { note(t("dj.ramp_bpm"), "soft"); djRenderTools(ed); return; }
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
  // Eine Rampe laesst sich nicht teilen, "ab hier" beginnt dort an ihrem Anfang.
  if (Math.abs(ed.segs[i].t - at) < 0.002 || djIsRamp(ed, i)) return i;
  ed.segs.splice(i + 1, 0, {t: at, bpm: ed.segs[i].bpm, n: ed.bn[b]});
  // Das Raster bleibt dabei gleich. Die Phrasen muessen den neuen Abschnitt kennen.
  djRebuildBeats(ed);
  return i + 1;
}
function djShiftGrid(ed, sec) {
  if (!ed.segs.length) return;
  djPush(ed);
  djShiftSegs(ed, ed.segs, sec, djGridFrom(ed));
  djTouched(ed, "grid");
}
// Ein Abschnitt darf hier vor dem Trackanfang beginnen, djRebuildBeats() laesst die
// Schlaege davor weg und djCarryPhrases() rechnet von seinem Beginn aus. Gespeichert
// wird er ab dem ersten Schlag im Track (djPayload()).
function djShiftSegs(ed, base, sec, from) {
  ed.segs = base.map((s, j) => {
    if (j < from) return {...s};
    const moved = {...s, t: s.t + sec};
    if (s.ramp) moved.ramp = {...s.ramp, anchors: (s.ramp.anchors || []).map(a => [a[0], a[1] + sec, ...a.slice(2)])};
    return moved;
  });
}
function djGridToPlayhead(ed) {
  if (!ed.bt.length) return;
  djShiftGrid(ed, djPos(ed) - ed.bt[djNearestBeat(ed, djPos(ed))]);
}
// Beat-Intervalle weiter oder enger, der erste Schlag jedes Abschnitts bleibt stehen.
function djStretchIntervals(ed, delta) {
  if (!ed.segs.length) return;
  djPush(ed);
  const from = djGridFrom(ed);
  for (let j = from; j < ed.segs.length; j++) {
    if (djIsRamp(ed, j)) continue;
    ed.segs[j].bpm = Math.min(400, Math.max(20, Math.round((ed.segs[j].bpm + delta) * 1000) / 1000));
  }
  djRenumberFrom(ed, from);
  djTouched(ed, "grid");
}
// Der Schlag naechst der Position wandert genau auf die Position und wird Takt 1.
// Gilt wie die Knoepfe daneben fuer das ganze Grid oder ab hier.
function djSetDownbeat(ed) {
  if (!ed.segs.length) return;
  djPush(ed);
  const pos = djPos(ed), offset = pos - ed.bt[djNearestBeat(ed, pos)];
  djShiftSegs(ed, ed.segs, offset, djGridFrom(ed));
  djRebuildBeats(ed);
  const beat = ed.bt[djNearestBeat(ed, pos)];
  const i = djSegIndexAt(ed, beat), seg = ed.segs[i];
  const ramp = djRampBeatIndex(ed, djNearestBeat(ed, pos));
  const k = ramp >= 0 ? ramp : Math.round((beat - seg.t) * seg.bpm / 60);
  seg.n = (((-k) % 4) + 4) % 4 + 1;
  // Die Zaehlung bleibt durchgehend: davor und danach rueckt sie mit.
  djRenumberFrom(ed, i);
  djRenumberBack(ed, i);
  djTouched(ed, "grid");
}
// Der Knopf schaltet: steht die Abspielposition auf einer Marke, entfernt er sie wieder.
function djAddSegment(ed) {
  if (!ed.segs.length) return;
  const mark = djTempoMarkAt(ed);
  if (mark > 0) { djPush(ed); djRemoveSegmentAt(ed, mark); djTouched(ed, "grid"); return; }
  const b = djNearestBeat(ed, djPos(ed)), at = ed.bt[b];
  // Vor dem ersten Abschnitt (Grid zum Trackanfang zurueckgerechnet) gibt es kein Tempo davor:
  // ein Abschnitt dort laege vor segs[0] und braeche die Reihenfolge.
  if (b === 0 || at <= ed.segs[0].t + 0.002) { note(t(Math.abs(ed.segs[0].t - at) < 0.002 ? "dj.segment_exists" : "dj.segment_at_start"), "soft"); return; }
  const i = djSegIndexAt(ed, at);
  if (Math.abs(ed.segs[i].t - at) < 0.002) { note(t("dj.segment_exists"), "soft"); return; }
  if (djIsRamp(ed, i)) { note(t("dj.ramp_no_segment"), "soft"); return; }
  djPush(ed);
  // Die Zaehlung laeuft weiter (djRenumberFrom()).
  ed.segs.splice(i + 1, 0, {t: at, bpm: ed.segs[i].bpm, n: ed.bn[b]});
  djTouched(ed, "grid");
}
function djRemoveSegment(ed) {
  if (ed.segs.length < 2) return;
  djPush(ed);
  djRemoveSegmentAt(ed, djSegIndexAt(ed, djPos(ed)));
  djTouched(ed, "grid");
}
function djRemoveSegmentAt(ed, i) {
  // Ohne ihr Ende waere die Rampe vor dem entfernten Abschnitt ploetzlich viel laenger.
  if (i > 0 && ed.segs[i - 1].ramp) delete ed.segs[i - 1].ramp;
  ed.segs.splice(i, 1);
  djRenumberFrom(ed, Math.max(0, i - 1));
}
function djTap(ed) {
  if (ed.audio.paused || djRampSeg(ed) >= 0) return;
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
  ed.audio.duck = ed.metro ? 0.5 : 1;
  ed.audio.tick();
  // Eigener Takt statt der Bildschleife: die steht, sobald die Wellenform aus dem Bild
  // gescrollt ist, und dann bliebe das Metronom stumm.
  clearInterval(ed.metroTimer);
  if (ed.metro) ed.metroTimer = setInterval(() => { if (!ed.audio.paused) { ed.audio.tick(); djMetronome(ed); } }, 40);
  // Klicks gibt es nur beim Spielen, dann steht der Kontext des Decks (djCtx()).
  if (ed.metro && DJ_AUDIO_CTX && DJ_AUDIO_CTX.state === "suspended") DJ_AUDIO_CTX.resume().catch(() => {});
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
  djSyncCueBar(ed);
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
  // Waehrend im Tempofeld getippt wird, nicht neu aufbauen (die Eingabe ginge verloren), aber
  // vormerken: djSyncLabels() holt es nach, sobald das Feld den Fokus abgibt. Vorher blieb die
  // Leiste dann stehen -- etwa "Hier wechseln" nicht als Marke markiert, nachdem man mit
  // Fokus im Tempofeld auf einen Tempowechsel gesprungen war.
  if (host.contains(document.activeElement) && document.activeElement.matches("input, select")) { ed._toolsStale = true; return; }
  ed._toolsStale = false;
  djRenderChrome(ed);
  host.dataset.mode = ed.mode;
  if (ed.mode === "grid") host.innerHTML = djGridTools(ed);
  else if (ed.mode === "phrase") host.innerHTML = djPhraseTools(ed);
  else host.innerHTML = djCueTools(ed);
  ed._segShown = undefined;
  djFitTools(ed);
}

// Die Werkzeugleiste ist immer so hoch wie die hoechste der drei Ansichten (meist Beatgrid
// mit drei Zeilen): ein Wechsel des Reiters oder in eine Rampe laesst den Editor nicht
// springen. Gemessen in einem unsichtbaren Abbild gleicher Breite im Editor (gleiche Styles).
function djFitTools(ed) {
  const host = ed.root && ed.root.querySelector(".dj-tools");
  if (!host || !host.clientWidth) return;
  let probe = ed.toolsProbe;
  if (!probe || !probe.isConnected) {
    probe = ed.toolsProbe = document.createElement("div");
    probe.className = "dj-tools";
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;left:0;top:0;";
    ed.root.appendChild(probe);
  }
  probe.style.width = host.clientWidth + "px";
  let max = 0;
  for (const [mode, html] of [["grid", djGridTools(ed)], ["phrase", djPhraseTools(ed)], ["cue", djCueTools(ed)]]) {
    probe.dataset.mode = mode;
    probe.innerHTML = html;
    max = Math.max(max, probe.offsetHeight);
  }
  probe.innerHTML = "";
  // Nur wachsen lassen, solange die Breite gleich bleibt: andere Zustaende (in einer Rampe,
  // Anpassen laeuft) koennen eine Zeile mehr umbrechen als der gerade gemessene.
  if (!ed.toolsMax || ed.toolsMax.w !== host.clientWidth) ed.toolsMax = {w: host.clientWidth, h: 0};
  ed.toolsMax.h = Math.max(ed.toolsMax.h, max);
  host.style.minHeight = ed.toolsMax.h + "px";
}

function djCueTools(ed) {
  const loop = ed.loop;
  let active = 0;
  const lens = djLoopKeys().slice().reverse().map(([key, n]) => {
    const on = loop && loop.on && ed.bt.length
      && (loop.beats === n || Math.abs((loop.end - loop.start) - n * 60 / (ed.segs[djSegIndexAt(ed, loop.start)] || {bpm: 120}).bpm) < 0.005);
    if (on) active = n;
    return djToolBtn("looplen", djKeyLabel(djLoopLabel(n), key.toUpperCase()), t("dj.loop_len_title", {beats: djLoopLabel(n), key: key.toUpperCase()}), {cls: "kb" + (on ? " on" : ""), attrs: `data-n="${n}"`});
  }).join("");
  // Ueberschrift = Zustand der Schleife: nichts, nur Anfang, aktiv (mit Laenge, wenn sie einer Taste entspricht).
  const cap = !loop ? t("dj.loop_caption") : loop.end == null ? t("dj.loop_caption_in")
    : active ? t("dj.loop_caption_active", {beats: djLoopLabel(active)}) : t("dj.loop_caption_on");
  // Reiter Beatjump/Loop: oben die Beatjumps, darunter die Schleifen, je Wert die Taste darunter.
  // Auto Cue und Memory Cue stehen in der Zeile der Reiter (djCueBarHTML()), in jedem Reiter erreichbar.
  return `<div class="dj-trow">${djJumpTools()}</div>
    <div class="dj-trow">${djToolGroup(esc(cap),
      `${djToolBtn("loopin", esc(t("dj.loop_in")), t("dj.loop_in_title"), {cls: loop ? "on" : ""})}
      ${djToolBtn("loopout", esc(t("dj.loop_out")), t("dj.loop_out_title"), {cls: loop && loop.end != null ? "on" : ""})}
      ${djSegRow(lens)}
      ${djToolBtn("loopexit", djIcon("x"), t("dj.loop_exit"), {attrs: loop ? "" : "disabled"})}`)}</div>`;
}

// Beschriftete Gruppe der Werkzeugleiste: kleine Ueberschrift ueber den Knoepfen.
// `caption` ist bereits HTML (darf Spans enthalten, z. B. die live nachgefuehrte Abschnittsangabe).
function djToolGroup(caption, body, cls = "") {
  return `<span class="dj-tgroup${cls ? " " + cls : ""}"><span class="dj-tcap">${caption}</span><span class="dj-tbtns">${body}</span></span>`;
}
// Knopfreihe ohne Abstand fuer Werte einer Groesse (-10/-1/+1/+10 ms, Loop-Laengen, ...).
function djSegRow(body) { return `<span class="dj-segrow">${body}</span>`; }
// Wert mit zugehoeriger Taste darunter (Beatjump, Loop-Laengen).
function djKeyLabel(label, key) { return `<span class="dj-kl">${esc(label)}<kbd>${esc(key)}</kbd></span>`; }
// Dezimalzahl in der Schreibweise der Oberflaeche (0,1 statt 0.1).
function djFmtDec(x) { return String(x).replace(".", t("dj.decimal_sep")); }
// Zusatz zur Ueberschrift "Tempowechsel": welcher Abschnitt (nur bei mehreren).
function djSegInfo(ed, seg) { return ed.segs.length > 1 ? " · " + t("dj.segment_of", {i: seg + 1, n: ed.segs.length}) : ""; }

// Memory Cue setzen mit Pfeilen zum vorigen/naechsten und Auto Cue: in der Zeile der Reiter.
// Die Pfeile sind gesperrt, wenn in der Richtung kein Memory Cue liegt (ohne Memory Cue beide).
function djCueBarHTML(ed) {
  const nav = djMemoryNav(ed);
  return `${AUTOCUE_AVAILABLE ? djToolBtn("autocue", `${djIcon("square-sparkles")} ${esc(t("dj.autocue"))}`, t("dj.autocue_title"),
      {cls: "wide", attrs: ed.autocueBusy ? "disabled" : ""}) + '<span class="dj-sep"></span>' : ""}
    ${djToolBtn("memorynav", djIcon("chevron-left"), t("dj.memory_prev"), {attrs: `data-dir="-1" ${nav.prev ? "" : "disabled"}`})}
    ${djToolBtn("memory", `${djIcon("bookmark")} ${esc(t("dj.memory_set"))}`, t("dj.memory_title"), {cls: "wide"})}
    ${djToolBtn("memorynav", djIcon("chevron-right"), t("dj.memory_next"), {attrs: `data-dir="1" ${nav.next ? "" : "disabled"}`})}`;
}

// Gibt es vor/hinter der Position einen Memory Cue? Gleiche Grenzen wie djGoMemory().
function djMemoryNav(ed) {
  const pos = djPos(ed);
  const stops = ed.cues.filter(c => c.kind === "memory").map(c => c.position_s);
  return {prev: stops.some(x => x < pos - 0.05), next: stops.some(x => x > pos + 0.05)};
}

function djSyncCueBar(ed) {
  const cuebar = ed.root.querySelector(".dj-cuebar"), cueHtml = djCueBarHTML(ed);
  if (cuebar && cueHtml !== ed._cueBar) { cuebar.innerHTML = cueHtml; ed._cueBar = cueHtml; }
}

// Tippen ergibt nur zum laufenden Track Sinn, in einer Rampe gar nicht.
function djTapBtn(ed, ramp) {
  return djToolBtn("tap", 'TAP <kbd class="dj-kbd">B</kbd>', t("dj.tap_title"), {attrs: ed.audio.paused || ramp ? "disabled" : ""});
}

function djGridTools(ed) {
  if (!ed.segs.length) {
    return `<div class="dj-trow" style="align-items:center"><span class="dj-hint">${esc(t("dj.no_grid"))}</span>
      ${PHRASES_AVAILABLE ? djToolBtn("measure", `${ICONS.analysePhrases} ${esc(t("dj.measure"))}`, t("dj.measure_title"), {cls: "wide"}) : ""}
      ${djToolBtn("newgrid", esc(t("dj.new_grid")), t("dj.new_grid_title"), {cls: "wide"})}
      ${djTapBtn(ed)}</div>`;
  }
  const seg = djSegIndexAt(ed, djPos(ed)), ramp = djIsRamp(ed, seg);
  // In einer Rampe ergibt sich das Tempo aus den Schlaegen: Tempofeld, TAP, Halbieren und
  // Intervalle bleiben sichtbar (die Leiste springt sonst), sind aber gesperrt.
  const tempoOff = ramp ? "disabled" : "";
  const shift = ms => djToolBtn("shift", (ms < 0 ? "−" : "+") + Math.abs(ms), t(ms < 0 ? "dj.shift_left" : "dj.shift_right", {ms: Math.abs(ms)}), {attrs: `data-ms="${ms}"`});
  // d < 0 = Intervalle erweitern = langsamer, deshalb "−".
  const interval = d => djToolBtn("interval", (d < 0 ? "−" : "+") + djFmtDec(Math.abs(d)), t(d < 0 ? "dj.interval_expand" : "dj.interval_shrink", {bpm: Math.abs(d)}), {attrs: `data-d="${d}" ${tempoOff}`});
  const scope = (here, icon, label, title) => djToolBtn("gridscope", `${djIcon(icon)} ${esc(t(label))}`, t(title), {cls: !!djPrefs().gridHere === here ? "on" : "", attrs: `data-here="${here ? 1 : 0}"`});
  const mark = djTempoMarkAt(ed), marks = djTempoMarkTimes(ed), pos = djPos(ed);
  const hasPrev = marks.some(x => x < pos - 0.01), hasNext = marks.some(x => x > pos + 0.01);
  // Eine umbrechende Zeile: Tempo, Grid (Gilt fuer, Ausrichten, Verschieben, Intervall) und als
  // letzte Gruppe Tempowechsel -- so steht sie immer direkt ueber der Rampe (Nutzervorgabe) und
  // die Leiste bleibt bei ueblicher Breite zweizeilig, statt eine eigene Zeile zu belegen.
  const tempo = djToolGroup(esc(t("dj.grp_tempo")),
    `<input class="dj-bpminput" type="number" step="0.01" min="20" max="400" value="${djFmtBpm(djShownBpm(ed, pos))}" title="${esc(t(ramp ? "dj.bpm_input_ramp_title" : "dj.bpm_input_title"))}" ${tempoOff}>
    ${djTapBtn(ed, ramp)}
    ${djToolBtn("half", "×½", t("dj.half_title"), {attrs: tempoOff})}
    ${djToolBtn("double", "×2", t("dj.double_title"), {attrs: tempoOff})}
    ${djToolBtn("metro", djIcon("metronome"), t("dj.metronome_title"), {cls: ed.metro ? "on" : ""})}`);
  const change = djToolGroup(`${esc(t("dj.grp_tempo_change"))}<span class="dj-seginfo">${esc(djSegInfo(ed, seg))}</span>`,
    `${djToolBtn("tempomark", djIcon("chevron-left"), t("dj.tempo_mark_prev"), {attrs: `data-dir="-1" ${hasPrev ? "" : "disabled"}`})}
    ${djToolBtn("addseg", `${djIcon("timer")} ${esc(t("dj.add_segment_short"))}`, t(mark > 0 ? "dj.remove_segment_here" : "dj.add_segment_title"), {cls: "wide" + (mark > 0 ? " on" : "")})}
    ${djToolBtn("tempomark", djIcon("chevron-right"), t("dj.tempo_mark_next"), {attrs: `data-dir="1" ${hasNext ? "" : "disabled"}`})}
    ${djToolBtn("delseg", djIcon("x"), t("dj.remove_segment"), {attrs: ed.segs.length > 1 ? "" : "disabled"})}`);
  return `<div class="dj-trow">${tempo}
      ${djToolGroup(esc(t("dj.grp_scope")), djSegRow(scope(false, "move-horizontal", "dj.scope_whole_short", "dj.scope_whole") + scope(true, "arrow-right-from-line", "dj.scope_here_short", "dj.scope_here")))}
      ${djToolGroup(esc(t("dj.grp_align")),
        `${djToolBtn("downbeat", `${djIcon("flag-triangle-right")} ${esc(t("dj.downbeat_short"))}`, t("dj.downbeat_title"), {cls: "wide"})}
        ${djToolBtn("gridhere", `${djIcon("locate-fixed")} ${esc(t("dj.grid_here_short"))}`, t("dj.grid_to_playhead"), {cls: "wide"})}`)}
      ${djToolGroup(esc(t("dj.grp_shift")), djSegRow([-10, -1, 1, 10].map(shift).join("")))}
      ${djToolGroup(esc(t("dj.grp_interval")), djSegRow([-0.1, -0.01, 0.01, 0.1].map(interval).join("")))}
      ${change}
    </div>
    ${djRampTools(ed, seg)}`;
}

// Dritte Zeile: die Rampe als eigener Kasten. Ausserhalb einer Rampe gestrichelt mit Start/Ende
// und einem Hinweis, bei offener Rampe (Start ohne Ende) umrandet, in einer Rampe hinterlegt
// mit den Rampen-Werkzeugen statt Start/Ende (dort ohne Nutzen, die Zeile passt sonst nicht
// in eine Reihe). Die Hoehe bleibt gleich (djFitTools()), die Leiste springt nicht.
function djRampTools(ed, i) {
  const ramp = djIsRamp(ed, i);
  const open = ed.segs.some((s, j) => djRampOpen(ed, j) && s.t <= djPos(ed) + 1e-6);
  const startEnd = `${djToolBtn("rampstart", esc(t("dj.ramp_start_short")), t("dj.ramp_start_title"))}
    ${djToolBtn("rampend", esc(t("dj.ramp_end_short")), t(open ? "dj.ramp_end_open_title" : "dj.ramp_end_title"), {cls: open ? "on" : ""})}`;
  const icon = djIcon("chart-no-axes-column-increasing");
  if (!ramp) {
    return `<div class="dj-ramp${open ? " open" : ""}"><span class="dj-ramplabel">${icon} ${esc(t("dj.ramp"))}</span>
      ${startEnd}${open ? `<span class="dj-ramphint">${esc(t("dj.ramp_hint_open"))}</span>` : ""}</div>`;
  }
  const [lo, hi] = djRampBeatLimits(ed, i);
  const beats = ed.segs[i].ramp.beats;
  const anchors = djRampAnchors(ed.segs[i], ed.segs[i + 1]).length;
  const own = djRampAnchors(ed.segs[i], ed.segs[i + 1]).filter(djAnchorManual).length;
  const endBeat = djRampEndBeat(ed, i);
  const from = djFmtBpm(ed.segs[i - 1].bpm), to = djFmtBpm(ed.segs[i + 1].bpm);
  const short = bpm => djFmtDec(Math.round(bpm * 100) / 100);
  return `<div class="dj-ramp on">
    <span class="dj-ramplabel" title="${esc(t("dj.ramp_label_title", {from, to}))}">${icon} ${esc(t("dj.ramp_from_to", {from: short(ed.segs[i - 1].bpm), to: short(ed.segs[i + 1].bpm)}))}</span>
    ${djSegRow(`${djToolBtn("rampinsert", djIcon("diamond-plus"), t("dj.ramp_insert_title"), {attrs: beats + 1 > hi ? "disabled" : ""})}
      ${djToolBtn("rampremove", djIcon("diamond-minus"), t("dj.ramp_remove_title"), {attrs: beats - 1 < Math.max(1, lo) ? "disabled" : ""})}`)}
    ${djSegRow(`${djToolBtn("rampcount", djIcon("minus"), t("dj.ramp_fewer"), {attrs: `data-d="-1" ${beats - 1 < Math.max(1, lo) ? "disabled" : ""}`})}
      <span class="dj-segval" title="${esc(t("dj.ramp_beats_title"))}">${esc(t("dj.ramp_beats", {beats}))}</span>
      ${djToolBtn("rampcount", djIcon("plus"), t("dj.ramp_more"), {attrs: `data-d="1" ${beats + 1 > hi ? "disabled" : ""}`})}`)}
    <b class="dj-rampend${endBeat === 1 ? "" : " warn"}" title="${esc(t("dj.ramp_beats_title"))}">${esc(endBeat === 1 ? t("dj.ramp_end_on_one") : t("dj.ramp_end_on", {n: endBeat}))}</b>
    <span class="dj-spacer"></span>
    ${djToolBtn("rampfit", `${djIcon("magnet")} ${esc(t(ed.rampFitBusy ? "dj.ramp_fit_busy" : "dj.ramp_fit"))}`, t("dj.ramp_fit_title"), {cls: "wide", attrs: ed.rampFitBusy ? "disabled" : ""})}
    ${djToolBtn("rampanchor", `${djIcon("map-pin")} ${esc(t("dj.ramp_anchor_short"))}`, t("dj.ramp_anchor_title"), {cls: "wide"})}
    ${djToolBtn("rampunpin", `${djIcon("map-pin-off")} ${esc(t("dj.ramp_unpin_short"))}`, t("dj.ramp_unpin", {n: anchors, own}), {cls: "wide", attrs: anchors ? "" : "disabled"})}
    ${djToolBtn("rampoff", djIcon("trash"), t("dj.ramp_dissolve"), {cls: "danger"})}
  </div>`;
}

function djJumpTools() {
  const keys = Object.fromEntries(djJumpKeys().map(([k, n]) => [n, k.toUpperCase()]));
  const jump = n => djToolBtn("jump", djKeyLabel((n < 0 ? "−" : "+") + Math.abs(n), keys[Math.abs(n)]),
    t("dj.jump_title", {beats: Math.abs(n), key: keys[Math.abs(n)], arrow: n < 0 ? "←" : "→"}), {cls: "kb", attrs: `data-n="${n}"`});
  return djToolGroup(esc(t("dj.beatjump_caption")),
    `${djToolBtn("prevcue", djIcon("chevron-left"), t("dj.prev_cue"))}
    ${djSegRow([-64, -32, -16, -8, -4].map(jump).join(""))}
    <span class="dj-jumpmid">${djIcon("arrow-left-right")}</span>
    ${djSegRow([4, 8, 16, 32, 64].map(jump).join(""))}
    ${djToolBtn("nextcue", djIcon("chevron-right"), t("dj.next_cue"))}`);
}

// Reiter Phrasen: oben die ausgewaehlte Phrase (mit Blaettern und den Aktionen dazu), darunter
// ihre Art, unten die Ziehen-Hilfe.
function djPhraseTools(ed) {
  const n = ed.phrases.length, i = ed.selPhrase, p = ed.phrases[i];
  const step = dir => djToolBtn("phrasestep", djIcon(dir < 0 ? "chevron-left" : "chevron-right"), t(dir < 0 ? "dj.phrase_prev" : "dj.phrase_next"),
    {attrs: `data-dir="${dir}" ${!n || (p && (i + dir < 0 || i + dir >= n)) ? "disabled" : ""}`});
  let head;
  if (p) {
    const beats = djLowerBeat(ed, p.end) - djLowerBeat(ed, p.start);
    const bars = beats ? " · " + t("dj.bars", {bars: djFmtDec(Math.round(beats / 4 * 10) / 10)}) : "";
    head = `<span class="dj-pdot" style="background:var(--phrase-${phraseToken(p.kind, p.num)})"></span><b>${esc(djPhraseLabel(p))}</b><span class="dj-dim">· ${esc(djFmt(p.start))}${esc(bars)}</span>`;
  } else {
    head = `<span class="dj-dim">${esc(t(n ? "dj.phrase_none_selected" : "dj.phrase_none"))}</span>`;
  }
  const kinds = djPhraseKinds().map(k =>
    `<button class="dj-kind${p && p.kind === k ? " on" : ""}" data-dj="kind" data-kind="${k}" style="--c:var(--phrase-${k});color:${theme().phrase[k + "Label"]}">${esc(t("phrase." + k))}</button>`).join("");
  // Feste Breite (halbe Leiste): beim Durchschalten springen die Knoepfe nicht mit dem Namen.
  return `<div class="dj-trow" style="align-items:center">
      <span class="dj-pnav">${step(-1)}<span class="dj-ptitle">${head}</span>${step(1)}</span>
      <span class="dj-spacer"></span>
      ${djToolBtn("cut", `${djIcon("scissors")} ${esc(t("dj.cut"))} <kbd class="dj-kbd">X</kbd>`, t("dj.cut_title"), {cls: "wide"})}
      ${djToolBtn("delphrase", `${djIcon("delete")} ${esc(t("dj.delete_phrase"))}`, t("dj.delete_phrase_title"), {cls: "wide"})}
      ${djToolBtn("clearphrases", `${djIcon("trash")} ${esc(t("dj.clear_phrases"))}`, t("dj.clear_phrases_title"), {cls: "wide danger"})}
    </div>
    <div class="dj-trow">${djToolGroup(esc(t("dj.grp_phrase_kind")), kinds)}</div>
    <div class="dj-trow"><span class="dj-thint">${esc(t("dj.phrase_drag_hint"))}</span></div>`;
}

// Die Auswahl folgt der Abspielposition (Nutzervorgabe): wechselt die Position in eine andere
// Phrase, wird diese ausgewaehlt; vor der ersten die erste, hinter der letzten die letzte. Nur
// beim Wechsel, eine von Hand gewaehlte Phrase bleibt also stehen, bis die Position weiterzieht.
// Nicht waehrend die Maus gedrueckt ist (Ziehen im Phrasenband setzt die Auswahl selbst).
function djFollowPhrase(ed, pos) {
  const n = ed.phrases.length;
  const at = n ? djPhraseAt(ed, pos) : -1;
  const key = n ? `${at}|${at >= 0 ? ed.phrases[at].start : pos < ed.phrases[0].start}` : "";
  if (key === ed._phraseKey || ed.pointerDown) return;
  ed._phraseKey = key;
  const want = !n ? -1 : at >= 0 ? at : pos < ed.phrases[0].start ? 0 : n - 1;
  if (want === ed.selPhrase) return;
  ed.selPhrase = want;
  ed.phrasePicked = false;
  djRenderSide(ed);
  if (ed.mode === "phrase") djRenderTools(ed);
  requestWavePaint();
}

// Phrasen der Reihe nach auswaehlen (und hinspringen). Ohne Auswahl die Phrase an der Abspielposition.
function djStepPhrase(ed, dir) {
  const n = ed.phrases.length;
  if (!n) return;
  const base = ed.selPhrase >= 0 ? ed.selPhrase + dir : Math.max(0, djPhraseAt(ed, djPos(ed)));
  djSelectPhrase(ed, Math.min(n - 1, Math.max(0, base)), true);
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
  // Die Hoehe waechst nur: sonst wird der ganze Editor beim Reiterwechsel kleiner.
  // Gemessen ohne Flex-Wachstum: gestreckt auf eine hoehere linke Spalte (Hinweisbanner,
  // schmaleres Fenster) hielte die Leiste sonst deren Hoehe fest, auch wenn sie wieder schrumpft.
  host.classList.add("fit");
  host.style.height = "";
  host.style.flex = "none";
  if (ed.side === "memory") ed.sideMinH = Math.max(ed.sideMinH || 0, host.offsetHeight);
  else if (!ed.sideMinH) {
    host.innerHTML = djMemoryHTML(ed);
    ed.sideMinH = host.offsetHeight;
    host.innerHTML = html;
  }
  host.style.flex = "";
  if (ed.side !== "memory") host.classList.remove("fit");
  host.style.height = ed.sideMinH + "px";
  djRenderRekordbox(ed);
}

// Knoepfe und Hinweis zum Abgleich mit Rekordbox.
function djRenderRekordbox(ed) {
  const box = ed.root.querySelector(".dj-rb"), banner = ed.root.querySelector(".dj-banner");
  const rb = ed.rb;
  if (!rb || !rb.available) { box.hidden = true; banner.hidden = true; return; }
  ed.r.rb = rb.present ? 1 : 0;
  ed.r.rbDiff = [...new Set([...rb.pending, ...rb.changed, ...(rb.notes || [])])];
  ed.r.rbTagFields = rb.tag_fields && rb.tag_fields.length ? rb.tag_fields : null;
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
    case "rampstart": djRampStart(ed); break;
    case "rampend": djRampEnd(ed); break;
    case "tempomark": djGoTempoMark(ed, Number(d.dir)); break;
    case "memorynav": djGoMemory(ed, Number(d.dir)); break;
    case "rampoff": djDissolveRamp(ed); break;
    case "rampcount": djRampCount(ed, Number(d.d)); break;
    case "rampanchor": djRampAnchorHere(ed); break;
    case "rampfit": djRampFit(ed); break;
    case "rampunpin": djRampDropAnchor(ed); break;
    case "rampinsert": djRampInsertBeat(ed); break;
    case "rampremove": djRampRemoveBeat(ed); break;
    case "delseg": djRemoveSegment(ed); break;
    case "metro": djToggleMetro(ed); break;
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
    case "phrasestep": djStepPhrase(ed, Number(d.dir)); break;
    case "reset": djReset(ed, d.field); break;
  }
}

function djOnChange(ed, ev) {
  const el = ev.target;
  if (el.matches(".dj-bpminput")) {
    // Nach dem Uebernehmen den Fokus abgeben: sonst baut sich die Leiste nicht neu auf
    // (djRenderTools() wartet auf das Feld) und die Pfeiltasten bleiben im Feld.
    el.blur();
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
  // Bewusst gewaehlt (Maus, Liste, Pfeile): Entf/Backspace loescht sie in jeder Ansicht.
  // Die mitlaufende Auswahl (djFollowPhrase()) setzt das zurueck -- sonst loeschte die Taste
  // im Beatgrid die Phrase unter der Abspielposition, die niemand ausgewaehlt hat.
  ed.phrasePicked = i >= 0;
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
  else if (ev.altKey && (key === "ArrowLeft" || key === "ArrowRight")) djGoTempoMark(ed, key === "ArrowLeft" ? -1 : 1);
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
    else if (ed.selPhrase >= 0 && (ed.mode === "phrase" || ed.phrasePicked)) djDeletePhrase(ed);
    else done = false;
  } else done = false;
  if (done) { ev.preventDefault(); ev.stopImmediatePropagation(); }
}, true);

window.addEventListener("resize", () => { if (DJ && !DJ.closed) djFitTools(DJ); });

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
      else if (ed.mode === "grid" && ed.bt.length) {
        const b = djNearestBeat(ed, secAt(x));
        const rk = djRampBeatIndex(ed, b);
        if (Math.abs(((ed.bt[b] - start) / ed.span) * w - x) <= 5 && rk > 0) {
          // Ein Schlag in der Rampe wird beim Ziehen zum Anker, der Rest verteilt sich neu.
          const segIndex = djSegIndexAt(ed, ed.bt[b]);
          Object.assign(drag, {kind: "anchor", seg: segIndex, k: rk, grab: ed.bt[b] - secAt(x)});
        } else if (Math.abs(((ed.bt[b] - start) / ed.span) * w - x) <= 5) {
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
        if (["cue", "edge", "grid", "stretch", "anchor"].includes(drag.kind)) djPush(ed);
        if (drag.kind === "phrase" && drag.base) { djPush(ed); djSelectPhrase(ed, drag.index, false); }
      }
      const dt = (px - x) / w * ed.span;
      if (drag.kind === "scrub") ed.dragPos = djClampOver(ed, drag.pos0 - dt);
      else if (drag.kind === "cue") djDragCue(ed, drag.cue, secAt(px), e);
      else if (drag.kind === "edge") djDragEdge(ed, drag, secAt(px) + drag.grab, e);
      else if (drag.kind === "phrase" && drag.base) djDragPhrase(ed, drag, dt, e);
      else if (drag.kind === "grid") { djShiftSegs(ed, drag.base, dt, djPrefs().gridHere ? drag.seg : 0); djRebuildBeats(ed); }
      else if (drag.kind === "anchor") {
        // Zwischen den Nachbar-Ankern (bzw. Rampenrand) halten, sonst fielen sie weg.
        const seg = ed.segs[drag.seg], next = ed.segs[drag.seg + 1];
        const pins = [[0, seg.t], ...djRampAnchors(seg, next).filter(([k]) => k !== drag.k), [seg.ramp.beats, next.t]];
        const before = pins.filter(([k]) => k < drag.k).at(-1), after = pins.find(([k]) => k > drag.k);
        const lo = before[1] + 0.03 * (drag.k - before[0]), hi = after[1] - 0.03 * (after[0] - drag.k);
        if (hi > lo) {
          djSetRampAnchor(ed, drag.seg, drag.k, Math.min(hi, Math.max(lo, secAt(px) + drag.grab)));
          djRebuildBeats(ed);
        }
      }
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
      else if (drag.kind === "grid" || drag.kind === "stretch" || drag.kind === "anchor") djTouched(ed, "grid");
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
    else if (ed.mode === "grid" && ed.bt.length) {
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
  const marks = ed.segs.map((s, i) => djIsTempoMark(ed, i));
  if (!marks.some(Boolean)) return;
  const th = theme().dj, pad = small ? 3 : 6, height = small ? 12 : 16;
  const free = small ? [-Infinity, -Infinity] : [-Infinity, -Infinity, -Infinity];
  ctx.save();
  ctx.font = `600 ${small ? 9 : 11}px -apple-system, sans-serif`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  // Hat der Track einen Tempowechsel, steht das Anfangstempo am ersten Schlag des Grids (nicht
  // dort, wo der erste Abschnitt verankert ist -- das Grid reicht bis zum Trackanfang zurueck).
  // An jeder Marke das Tempo ab dort; am Rampenbeginn das Tempo davor mit der Richtung, am Ende das neue.
  const badge = (x, label, line) => {
    if (x < -90 || x > w) return;
    if (line) { ctx.fillStyle = th.tempo; ctx.fillRect(x, top, 1.5, bottom - top); }
    const row = free.findIndex(limit => x >= limit);
    if (row < 0) return;
    const bw = ctx.measureText(label).width + pad * 2, y = top + 2 + row * (height + 2);
    ctx.fillStyle = th.tempoBg;
    ctx.beginPath();
    ctx.roundRect(x + 3, y, bw, height, small ? 3 : 4);
    ctx.fill();
    ctx.fillStyle = th.tempoFg;
    ctx.fillText(label, x + 3 + pad, y + height / 2 + 0.5);
    free[row] = x + 3 + bw + 3;
  };
  badge(((djGridStart(ed) - start) / span) * w, djFmtBpm(ed.segs[0].bpm), false);
  ed.segs.forEach((s, i) => {
    if (!marks[i]) return;
    const x = ((s.t - start) / span) * w;
    let label = djFmtBpm(s.bpm);
    if (djIsRamp(ed, i)) label += ed.segs[i + 1].bpm >= s.bpm ? " ↗" : " ↘";
    else if (djRampOpen(ed, i)) label = t("dj.ramp_open_badge", {bpm: djFmtBpm(s.bpm)});
    badge(x, label, true);
  });
  ctx.restore();
}

// Rampen. layer "under": leicht getoenter Bereich unter der Wellenform. layer "over": ein
// schmaler Streifen in der Tempofarbe oben (die Toenung verschwindet unter dichter
// Wellenform), mit anchors zusaetzlich die angehefteten Schlaege als Raute.
function djDrawRamps(ctx, ed, start, span, w, top, bottom, layer, anchors) {
  const th = theme().dj;
  ed.segs.forEach((s, i) => {
    if (!djIsRamp(ed, i)) return;
    const x0 = ((s.t - start) / span) * w, x1 = ((ed.segs[i + 1].t - start) / span) * w;
    if (x1 < 0 || x0 > w) return;
    if (layer === "under") {
      ctx.fillStyle = th.ramp;
      ctx.fillRect(x0, top, x1 - x0, bottom - top);
      return;
    }
    ctx.fillStyle = th.tempo;
    ctx.fillRect(x0, top, x1 - x0, 2);
    if (!anchors) return;
    // Eigene Anker gefuellt, eingepasste nur umrandet (die darf "An Audio anpassen" ersetzen).
    ctx.fillStyle = th.anchor;
    ctx.strokeStyle = th.anchor;
    ctx.lineWidth = 1.5;
    for (const a of djRampAnchors(s, ed.segs[i + 1])) {
      const x = ((a[1] - start) / span) * w;
      if (x < -6 || x > w + 6) continue;
      ctx.beginPath();
      ctx.moveTo(x, top + 0.75); ctx.lineTo(x + 4.5, top + 5); ctx.lineTo(x, top + 9.25); ctx.lineTo(x - 4.5, top + 5);
      ctx.closePath();
      if (djAnchorManual(a)) ctx.fill(); else ctx.stroke();
    }
  });
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
  djDrawRamps(ctx, ed, start, span, w, waveTop, waveTop + waveH, "under");
  djDrawWave(ctx, ed, w, waveTop, waveH, start, span);
  djDrawBeats(ctx, ed, start, span, w, waveTop, waveTop + waveH);
  djDrawRamps(ctx, ed, start, span, w, waveTop, waveTop + waveH, "over", true);
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
  djDrawRamps(ctx, ed, start, span, w, lay.top, lay.top + waveH, "under");
  djDrawRamps(ctx, ed, start, span, w, lay.top, lay.top + waveH, "over", false);
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
