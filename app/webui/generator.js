// ── Playlist-Generator ────────────────────────────────────────────────────
// Eigene Listenart "generator" im Playlist-Baum (db.PLAYLIST_KINDS). Ein
// Generator fuellt N Slots entlang einer Energiekurve mit Tracks aus der
// Bibliothek -- gesteuert ueber den Liederpool (eine Suchabfrage wie in der
// Suchleiste, mit erweiterter Suche als Felder), Uebergangsregeln (BPM/Tonart) und
// Stimmungsregler. Ausgewaehlt ersetzt er im Hauptbereich die Tabelle
// (syncGeneratorPane(), aufgerufen am Anfang von render() in app.js).
//
// Datenmodell:
//   - Einstellungen (Kurve, Slots, Filter, Gates, Regler) als JSON in
//     playlists.rules -- wie das Regelwerk einer Smart Playlist, gepruefte
//     Form siehe server._clean_generator_config().
//   - Belegung in playlist_items wie bei einer normalen Playlist. In 'rules'
//     stehen bewusst KEINE Pfade, nur je Slot 'filled': Slot i bekommt den
//     k-ten Eintrag, k = Zahl der belegten Slots davor. db.move_path() zieht
//     playlist_items bei Umbenennen/Relocate mit, Pfade in 'rules' wuerden
//     dabei veralten. Export, Mitgliedschaft und Ziehen in andere Listen
//     funktionieren dadurch ohne Sonderfall.
//
// Diese Datei laeuft im selben <script> NACH app.js (report._template()).
// app.js ruft syncGeneratorPane() schon beim allerersten render() auf, also
// bevor die Zeilen hier ausgewertet sind -- deshalb nur Funktionsdeklarationen
// (gehoisted) und 'var' OHNE Startwert fuer den Zustand: ein 'let'/'const'
// laege zu diesem Zeitpunkt noch in der Temporal Dead Zone und wuerde werfen,
// ein 'var x = null' setzte einen schon geoeffneten Generator beim Auswerten
// dieser Zeile wieder zurueck.

// Zustand des geoeffneten Generators (undefined/null = keiner):
//   {id, cfg, tracks: [Zeile|null je Slot], seed, saveTimer}
var GEN;
// Einstellungsblock zugeklappt? Gilt fuer alle Generatoren.
var GEN_SETTINGS_CLOSED;
// Erweiterte Suche des Liederpools aufgeklappt? Gilt fuer alle Generatoren.
var GEN_ADV_OPEN;
// Peaks je Pfad fuer die Waveforms in den Slots (genMountSlotWaves()) --
// die Liste wird bei jedem Abspielen/Umsortieren neu gebaut, ohne Cache
// holte jeder Neuaufbau alle Waveforms erneut vom Server.
var GEN_PEAKS;
// Farbige Rekordbox-Waveform je Pfad (null = dort keine), gleicher Grund
// wie GEN_PEAKS.
var GEN_RBWAVE;
// Slot-Nummer, die gerade gezogen wird (undefined/-1 = keiner).
var GEN_DRAG_FROM;

function genMoods() {
  // Alle Stimmungen der Analyse (genremood.py), Reihenfolge wie dort.
  return MOODS.concat(THEMES);
}

function genDefaultKnobs() {
  return {dark: 0, deep: 0, groovy: 0, melodic: 0, uplifting: 0, aggressive: 0};
}

function genPresetNames() {
  return ["balanced", "warmup", "build", "peak", "wave", "cooldown"];
}

// Verlauf einer Vorlage: x (0..1, Position im Set) -> Energie 1..10.
// Eigene Kurven (GENERATOR_CURVES in app.js, "curve:<id>") werden linear auf
// x abgetastet -- so passt eine mit 16 Slots gespeicherte Kurve auch auf 24.
function genPresetValue(name, x) {
  const curve = genCurveOf(name);
  if (curve && curve.points.length > 1) {
    const f = x * (curve.points.length - 1), i = Math.floor(f);
    const j = Math.min(curve.points.length - 1, i + 1);
    return curve.points[i] + (curve.points[j] - curve.points[i]) * (f - i);
  }
  switch (name) {
    case "warmup":   return 3 + 3 * x;
    case "build":    return 3 + 6 * x;
    case "peak":     return 7 + 1.5 * Math.sin(Math.PI * x);
    case "wave":     return 5.5 - 2.5 * Math.cos(4 * Math.PI * x);
    case "cooldown": return 8 - 5 * x;
    default:         return 4 + 4 * Math.sin(Math.PI * Math.pow(x, 0.8));   // balanced
  }
}

function genCurveOf(preset) {
  const id = String(preset || "").startsWith("curve:") ? String(preset).slice(6) : null;
  return id ? (GENERATOR_CURVES || []).find(c => c.id === id) || null : null;
}

function genClampE(e) {
  return Math.min(10, Math.max(1, Math.round(e * 2) / 2));
}

// Vorlage auf n Slots abtasten. Bestehende Slots behalten Sperre und Belegung.
function genResample(cfg, n) {
  const slots = [];
  for (let i = 0; i < n; i++) {
    const x = n === 1 ? 0.5 : i / (n - 1);
    const old = cfg.slots[i] || {lock: false, filled: false};
    slots.push({...old, e: genClampE(genPresetValue(cfg.preset, x))});
  }
  cfg.slots = slots;
}

// Ausgangszustand eines neuen Generators (createPlaylistNode() in app.js).
// v 2: Ausgeblendete Tracks sind ueber den Schalter der erweiterten Suche
// ausgeschlossen ("/No /Ausgeblendet") statt fest in genPool() -- so laesst
// sich die Vorgabe abwaehlen.
function genDefaultConfig() {
  const cfg = {
    v: 2, preset: "balanced", slots: [],
    // Liederpool: Suchabfrage mit derselben Syntax wie die Suchleiste
    // (/Genre, /BPM, /Add 30d, ...), dazu optional eine Quell-Playlist.
    query: genHiddenDefault(""), filters: {source: null},
    gates: {bpm: "3", half: true, key: "loose"},
    knobs: genDefaultKnobs(), mode: "lean", cohesion: 0.5,
  };
  genResample(cfg, 16);
  return cfg;
}

// Gespeicherte Einstellungen lesen -- fehlende Teile (aeltere oder von Hand
// beschaedigte Daten) mit Vorgaben auffuellen, statt den Generator leer zu zeigen.
function genParseConfig(node) {
  const base = genDefaultConfig();
  let raw = node && node.rules;
  if (typeof raw === "string") {
    try { raw = JSON.parse(raw); } catch (e) { raw = null; }
  }
  if (!raw || typeof raw !== "object") return base;
  const cfg = {...base, ...raw};
  // Aeltere Generatoren hatten eigene Filterfelder (Genre, BPM, Hinzugefuegt)
  // statt der Suchabfrage -- einmalig in eine gleichwertige Abfrage umsetzen.
  cfg.query = typeof raw.query === "string" ? raw.query : genLegacyQuery(raw.filters || {});
  // Vor v 2 schloss genPool() ausgeblendete Tracks fest aus -- einmalig als
  // Ausdruck in die Abfrage uebernehmen, danach entscheidet allein der Schalter.
  if (!(raw.v >= 2)) cfg.query = genHiddenDefault(cfg.query);
  cfg.v = 2;
  cfg.filters = {source: (raw.filters && raw.filters.source) || null};
  cfg.gates = {...base.gates, ...(raw.gates || {})};
  cfg.knobs = (raw.knobs && typeof raw.knobs === "object") ? {...raw.knobs} : base.knobs;
  cfg.slots = Array.isArray(raw.slots) ? raw.slots.map(s => ({
    e: genClampE(Number(s && s.e) || 5), lock: !!(s && s.lock), filled: !!(s && s.filled),
  })) : base.slots;
  cfg.cohesion = Math.min(1, Math.max(0, Number(cfg.cohesion) || 0));
  return cfg;
}

// "/No /Ausgeblendet" anhaengen, sofern die Abfrage den Schalter noch nicht
// setzt (weder "ja" noch "nein").
function genHiddenDefault(query) {
  const def = ADV_BY_KEY.ig;
  const {filters} = parseSearchQuery(String(query || "").toLowerCase());
  return filters.some(f => advMatches(def, f)) ? query : advApplyField(query || "", def, "no");
}

// Frueheres Filterformat {genres, bpm: [lo, hi], added: "7d"} -> Suchabfrage.
// Grenzen wie damals: BPM einschliesslich, ohne BPM-Tag (0) nie im Pool.
function genLegacyQuery(f) {
  const parts = [];
  const quote = v => /\s/.test(v) ? `"${v}"` : v;
  const genres = (Array.isArray(f.genres) ? f.genres : []).map(g => String(g).trim()).filter(Boolean);
  if (genres.length === 1) parts.push(`${advToken(ADV_BY_KEY.ge)} ${quote(genres[0])}`);
  else if (genres.length) parts.push(`${advToken(ADV_BY_KEY.ge)} (${genres.map(quote).join(" OR ")})`);
  const [lo, hi] = Array.isArray(f.bpm) ? f.bpm : [null, null];
  if (lo || hi) parts.push(`${advToken(ADV_BY_KEY.bp)} ${lo || 1}-${hi || 999}`);
  const days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365}[f.added];
  if (days) parts.push(`${advToken(ADV_BY_KEY.da)} ${days}d`);
  return parts.join(" ");
}

// Belegung aus playlist_items auf die Slots verteilen (siehe Kopfkommentar).
function genTracksFromItems(id, cfg) {
  const rows = (PLAYLIST_ITEMS[id] || []).map(p => ROW_BY_PATH.get(p) || null);
  let k = 0;
  return cfg.slots.map(s => {
    if (!s.filled) return null;
    const r = rows[k++];
    return r && !r.removed ? r : null;
  });
}

// ── Einbindung in render() ────────────────────────────────────────────────
// true = ein Generator ist offen und render() soll die Tabelle auslassen.
function syncGeneratorPane() {
  const pane = document.getElementById("generatorPane");
  const view = String(state.view || "");
  const node = view.startsWith("gen:") ? PLAYLISTS.find(n => n.id === view.slice(4)) : null;
  if (!node || !pane) {
    if (GEN) genFlushSave();
    GEN = null;
    delete document.body.dataset.gen;
    if (pane) { pane.hidden = true; pane.innerHTML = ""; }
    return false;
  }
  document.body.dataset.gen = "1";
  pane.hidden = false;
  // Nur beim Wechsel IN einen Generator neu aufbauen: render() laeuft auch
  // aus Anlaessen, die den Generator nicht betreffen (syncMarks() u. a.), und
  // wuerde sonst ein laufendes Ziehen an Kurve oder Regler abbrechen.
  if (!GEN || GEN.id !== node.id) {
    if (GEN) genFlushSave();
    const cfg = genParseConfig(node);
    GEN = {id: node.id, cfg, tracks: genTracksFromItems(node.id, cfg),
           seed: (Math.random() * 2 ** 31) >>> 0, saveTimer: null};
    genWire(pane);
    genRender();
  }
  return true;
}

// ── Speichern ─────────────────────────────────────────────────────────────
// Entprellt: Ziehen an Kurve und Reglern erzeugt viele Aenderungen in Folge.
function genScheduleSave() {
  if (!GEN || !apiMode) return;
  clearTimeout(GEN.saveTimer);
  const g = GEN;
  g.saveTimer = setTimeout(() => { g.saveTimer = null; genSave(g); }, 400);
}

function genFlushSave() {
  if (GEN && GEN.saveTimer) {
    clearTimeout(GEN.saveTimer);
    GEN.saveTimer = null;
    genSave(GEN);
  }
}

async function genSave(g) {
  g.cfg.slots.forEach((s, i) => { s.filled = !!g.tracks[i]; });
  const paths = g.tracks.filter(Boolean).map(r => r.p);
  try {
    // Reihenfolge: erst die Einstellungen (sie tragen die 'filled'-Marken),
    // dann die Belegung -- so passen beide nach einem Abbruch dazwischen
    // hoechstens bis zur naechsten Aenderung nicht zusammen.
    const data = await playlistApi("/api/playlist", {op: "update", id: g.id, rules: g.cfg});
    const node = PLAYLISTS.find(n => n.id === g.id);
    if (node && data.node) node.rules = data.node.rules;
    const res = await playlistApi("/api/playlist-items", {op: "set", id: g.id, paths});
    PLAYLIST_ITEMS[g.id] = res.items || paths;
    PLAYLIST_SETS[g.id] = new Set(PLAYLIST_ITEMS[g.id]);
    renderTree();
  } catch (err) {
    note(t("gen.save_failed", {error: err.message}), true);
  }
}

// ── Pool und Regeln ───────────────────────────────────────────────────────
function genAnalysed(r) {
  return !r.removed && !r.ext && !r.gone && r.eg > 0;
}

// Kandidaten nach Liederpool (Suchabfrage + Quelle) und (bei "Match all")
// den Stimmungen. Die Abfrage einmal parsen, nicht je Zeile -- der Cache von
// parseSearchQuery() haelt nur die zuletzt geparste, und das ist meist die
// der Suchleiste. Die Standard-Suchfilter der Suchleiste gelten hier nicht.
// Ausgeblendete Tracks regelt allein die Abfrage (Schalter "Ausgeblendet" der
// erweiterten Suche); die Vorgabe "nein" setzt genHiddenDefault().
function genPool(cfg) {
  const f = cfg.filters;
  let src = null;
  if (f.source) {
    const node = PLAYLISTS.find(n => n.id === f.source);
    if (node) src = new Set(playlistRows(node.id).map(r => r.p));
  }
  const query = String(cfg.query || "").trim();
  const parsed = query ? parseSearchQuery(query.toLowerCase()) : null;
  const must = [], mustNot = [];
  if (cfg.mode === "all") {
    for (const [m, v] of Object.entries(cfg.knobs)) {
      if (v > 0) must.push(m); else if (v < 0) mustNot.push(m);
    }
  }
  return DATA.filter(r => {
    if (!genAnalysed(r)) return false;
    if (src && !src.has(r.p)) return false;
    if (parsed && !matchesParsedQuery(r, parsed)) return false;
    if (must.length || mustNot.length) {
      const names = moodNames(r);
      if (must.some(m => !names.includes(m))) return false;
      if (mustNot.some(m => names.includes(m))) return false;
    }
    return true;
  });
}

// Perzentil-Rang (0..1) je aktivem Regler im Pool: die Ja/Nein-Koepfe (0..1)
// und die DJ-Stimmungen (kleine Absolutwerte, max. ~0,26) werden so ohne
// feste Skala vergleichbar.
function genRanks(pool, knobs) {
  const out = {};
  for (const [m, v] of Object.entries(knobs)) {
    if (!v) continue;
    const sorted = pool.slice().sort((a, b) => ((a.mm || {})[m] || 0) - ((b.mm || {})[m] || 0));
    const map = new Map();
    const n = Math.max(1, sorted.length - 1);
    sorted.forEach((r, i) => map.set(r, i / n));
    out[m] = map;
  }
  return out;
}

// "8A" -> {n: 8, l: "A"} oder null.
function genCamelot(ky) {
  if (!ky || !KEY_TABLE[ky]) return null;
  const m = /^(\d{1,2})([AB])$/.exec(ky);
  return m ? {n: Number(m[1]), l: m[2]} : null;
}

// Harmonische Nachbarschaft auf dem Camelot-Rad.
//   streng: gleiche Tonart, +-1 gleicher Buchstabe, gleiche Zahl A<->B
//   locker: zusaetzlich +-2 gleicher Buchstabe und +-1 diagonal
// Ohne gueltige Tonart besteht kein Track ein aktives Tonart-Gate.
function genKeyOk(a, b, mode) {
  if (mode === "off") return true;
  const x = genCamelot(a.ky), y = genCamelot(b.ky);
  if (!x || !y) return false;
  const d = Math.min((x.n - y.n + 12) % 12, (y.n - x.n + 12) % 12);
  if (x.l === y.l) return d <= (mode === "strict" ? 1 : 2);
  return d === 0 || (mode === "loose" && d === 1);
}

function genBpmDiff(a, b, half) {
  if (!(a.bp > 0) || !(b.bp > 0)) return Infinity;
  let d = Math.abs(a.bp - b.bp);
  if (half) d = Math.min(d, Math.abs(a.bp * 2 - b.bp), Math.abs(a.bp / 2 - b.bp));
  return d;
}

function genBpmOk(a, b, gates) {
  if (gates.bpm === "any") return true;
  return genBpmDiff(a, b, gates.half) <= (gates.bpm === "0" ? 0.5 : (Number(gates.bpm) || 3));
}

// Verletzte Regeln zwischen zwei aufeinanderfolgenden Tracks -- fuer die
// Hinweise in der Slot-Liste, immer aus der aktuellen Belegung berechnet.
function genConflict(a, b, gates) {
  const out = [];
  if (!genBpmOk(a, b, gates)) {
    const d = Math.round((b.bp || 0) - (a.bp || 0));
    out.push(t("gen.conflict_bpm", {delta: (d > 0 ? "+" : d < 0 ? "−" : "±") + Math.abs(d)}));
  }
  if (!genKeyOk(a, b, gates.key)) out.push(t("gen.conflict_key"));
  return out;
}

// Kosinus-Aehnlichkeit der Genre-Stile ([[Stil, Prozent], ...]); 0,5, wenn
// einer der beiden keine hat (weder Zug noch Strafe).
function genStyleSim(a, b) {
  const ga = a.gs || [], gb = b.gs || [];
  if (!ga.length || !gb.length) return 0.5;
  const mb = new Map(gb);
  let dot = 0, na = 0, nb = 0;
  for (const [s, p] of ga) { na += p * p; if (mb.has(s)) dot += p * mb.get(s); }
  for (const [, p] of gb) nb += p * p;
  return na && nb ? dot / Math.sqrt(na * nb) : 0.5;
}

// Kleine, reproduzierbare Zufallsquelle (mulberry32) -- "Neu wuerfeln" setzt
// nur einen neuen Startwert.
function genRng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

// Kandidaten fuer Slot i, beste zuerst ({r, s}). Prueft die Gates gegen den
// vorigen belegten Slot und -- ist der naechste Slot gesperrt -- auch gegen
// ihn. Findet sich nichts, wird stufenweise gelockert (erst ohne Tonart-,
// dann ohne BPM-Gate); der Hinweis in der Liste zeigt das dann an.
// avoid: Pfade, die beim Neu-Wuerfeln schon im Set standen -- leichter Malus,
// damit "Neu wuerfeln" auch wirklich andere Tracks bringt.
function genCandidates(g, i, pool, ranks, used, avoid) {
  const cfg = g.cfg;
  let prev = null;
  for (let j = i - 1; j >= 0; j--) if (g.tracks[j]) { prev = g.tracks[j]; break; }
  const next = cfg.slots[i + 1] && cfg.slots[i + 1].lock ? g.tracks[i + 1] : null;
  const recent = new Set();
  for (let j = i - 1, seen = 0; j >= 0 && seen < 3; j--) {
    if (g.tracks[j]) { recent.add(String(g.tracks[j].a || "").toLowerCase()); seen++; }
  }
  const target = cfg.slots[i].e;
  const knobs = Object.entries(cfg.knobs).filter(([, v]) => v);
  const lean = cfg.mode !== "all";
  const score = r => {
    let s = Math.abs(r.eg - target) / 9;
    if (lean) for (const [m, v] of knobs) s -= v * (ranks[m].get(r) - 0.5) * 0.6;
    if (prev && cfg.cohesion) s += cfg.cohesion * (1 - genStyleSim(prev, r)) * 0.5;
    if (r.a && recent.has(String(r.a).toLowerCase())) s += 0.3;
    if (avoid && avoid.has(r.p)) s += 0.2;
    return s;
  };
  const steps = [cfg.gates, {...cfg.gates, key: "off"}, {...cfg.gates, key: "off", bpm: "any"}];
  for (const gates of steps) {
    const ok = r => (!prev || (genBpmOk(prev, r, gates) && genKeyOk(prev, r, gates.key)))
                 && (!next || (genBpmOk(r, next, gates) && genKeyOk(r, next, gates.key)));
    const list = [];
    for (const r of pool) if (!used.has(r.p) && ok(r)) list.push({r, s: score(r)});
    if (list.length) return list.sort((a, b) => a.s - b.s);
  }
  return [];
}

// Slots fuellen. which: "empty" (nur leere), "all" (alle ungesperrten neu)
// oder eine Slot-Nummer.
function genFill(which) {
  const g = GEN;
  if (!g) return;
  const pool = genPool(g.cfg);
  if (!pool.length) { note(t("gen.pool_empty"), "soft"); return; }
  const ranks = genRanks(pool, g.cfg.knobs);
  const idx = typeof which === "number" ? [which]
    : g.cfg.slots.map((s, i) => i).filter(i => !g.cfg.slots[i].lock && (which === "all" || !g.tracks[i]));
  const avoid = new Set(idx.map(i => g.tracks[i]).filter(Boolean).map(r => r.p));
  for (const i of idx) g.tracks[i] = null;
  const used = new Set(g.tracks.filter(Boolean).map(r => r.p));
  const rnd = genRng(g.seed++);
  let missing = 0;
  for (const i of idx) {
    const cands = genCandidates(g, i, pool, ranks, used, avoid).slice(0, 5);
    if (!cands.length) { missing++; continue; }
    // Zufaellig gewichtet unter den besten fuenf: naeher am Besten = wahrscheinlicher.
    const w = cands.map(c => Math.exp(-(c.s - cands[0].s) * 12));
    let x = rnd() * w.reduce((a, b) => a + b, 0), pick = cands[0];
    for (let k = 0; k < cands.length; k++) { x -= w[k]; if (x <= 0) { pick = cands[k]; break; } }
    g.tracks[i] = pick.r;
    used.add(pick.r.p);
  }
  if (missing) note(t("gen.pool_exhausted", {count: missing}), "soft");
  genScheduleSave();
  genRender();
}

// ── Darstellung ───────────────────────────────────────────────────────────
function genIcon(name) {
  return SVG(LUCIDE_ICONS[name] || "");
}

// Auswahlfeld fuer die Uebergangsregeln -- Optionen wie bei genSeg().
function genSelect(id, value, options) {
  return `<select id="${esc(id)}" class="geninput gensel">` + options.map(([v, label, title]) =>
    `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</option>`
  ).join("") + `</select>`;
}

function genSeg(group, value, options) {
  return `<span class="genseg">` + options.map(([v, label, title]) =>
    `<button type="button" class="${String(v) === String(value) ? "on" : ""}" ` +
    `data-seg="${esc(group)}" data-val="${esc(v)}"${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</button>`).join("") + `</span>`;
}

function genRender() {
  const pane = document.getElementById("generatorPane");
  if (!pane || !GEN) return;
  const total = DATA.filter(r => !r.removed && !r.ext && !r.gone).length;
  const analysed = DATA.filter(genAnalysed).length;
  if (!analysed) {
    pane.innerHTML = `<div class="genempty">
      <div class="genempty-icon">${genIcon("chart-spline")}</div>
      <p>${esc(t("gen.none_analysed"))}</p>
      <button type="button" class="act" data-act="scan">${esc(t("gen.open_scan"))}</button></div>`;
    return;
  }
  const filled = GEN.tracks.filter(Boolean).length;
  pane.innerHTML = `
    <div class="gentools">
      <button type="button" class="act" data-act="fill" title="${esc(t("gen.fill_title"))}">${genIcon("wand-sparkles")}<span>${esc(t("gen.fill"))}</span></button>
      <button type="button" class="act" data-act="reroll" title="${esc(t("gen.reroll_title"))}">${genIcon("dices")}<span>${esc(t("gen.reroll"))}</span></button>
      <button type="button" class="act" data-act="clear" title="${esc(t("gen.clear_title"))}">${genIcon("eraser")}<span>${esc(t("gen.clear"))}</span></button>
      <span class="genspacer"></span>
      <span class="gencover" title="${esc(t("gen.analysed_title"))}">${esc(t("gen.analysed", {
        count: analysed.toLocaleString("de-DE"), total: total.toLocaleString("de-DE")}))}</span>
      <button type="button" class="act" data-act="m3u" ${filled ? "" : "disabled"}>${ICONS.fileMusic}<span>${esc(t("gen.export_m3u"))}</span></button>
      <button type="button" class="act" data-act="save-playlist" ${filled && apiMode ? "" : "disabled"} title="${esc(t("gen.save_playlist_title"))}">${genIcon("list-plus")}<span>${esc(t("gen.save_playlist"))}</span></button>
    </div>
    ${genSettingsHtml()}
    <div class="genplan">
      ${genPlanHeadHtml()}
      <div class="genarc" id="genArc">${genArcSvg()}</div>
    </div>
    <div class="genslots" id="genSlots">${genSlotsHtml()}</div>
    <button type="button" class="act small genaddslot" data-act="addslot">${genIcon("plus")}<span>${esc(t("gen.add_slot"))}</span></button>`;
  genBindPool();
  genMountSlotWaves();
}

// Kopf des Verlauf-Panels: Vorlage oder eigene Kurve, Speichern/Loeschen,
// Slotzahl und Belegung. Sitzt mit der Energiekurve in EINEM Panel, weil
// beides dieselbe Sache einstellt.
function genPlanHeadHtml() {
  const cfg = GEN.cfg;
  const filled = GEN.tracks.filter(Boolean).length;
  const own = GENERATOR_CURVES || [];
  const curve = genCurveOf(cfg.preset);
  // Gespeicherte Kurve inzwischen geloescht -> als "Eigene Kurve" anzeigen.
  const preset = String(cfg.preset).startsWith("curve:") && !curve ? "custom" : cfg.preset;
  const opt = (v, label) => `<option value="${esc(v)}"${preset === v ? " selected" : ""}>${esc(label)}</option>`;
  return `<div class="genmove">
      <span class="genlbl">${esc(t("gen.curve"))}</span>
      <select id="genPreset" class="geninput">
        <optgroup label="${esc(t("gen.curve_group_presets"))}">
          ${genPresetNames().map(p => opt(p, t("gen.preset_" + p))).join("")}
        </optgroup>
        ${own.length ? `<optgroup label="${esc(t("gen.curve_group_own"))}">
          ${own.map(c => opt("curve:" + c.id, c.name)).join("")}</optgroup>` : ""}
        ${preset === "custom" ? opt("custom", t("gen.preset_custom")) : ""}
      </select>
      <button type="button" class="iconbtn plain" data-act="curve-save" title="${esc(t("gen.curve_save"))}"
              ${apiMode ? "" : "disabled"}>${genIcon("save")}</button>
      ${curve ? `<button type="button" class="iconbtn plain" data-act="curve-del" title="${esc(t("gen.curve_delete", {name: curve.name}))}">${ICONS.trash}</button>` : ""}
      <span class="genlbl">${esc(t("gen.slots"))}</span>
      <span class="genstepper">
        <button type="button" data-act="count-dec" title="${esc(t("gen.slot_less"))}"${cfg.slots.length <= 1 ? " disabled" : ""}>−</button>
        <input type="number" class="gennum" id="genCount" min="1" max="200" value="${cfg.slots.length}">
        <button type="button" data-act="count-inc" title="${esc(t("gen.slot_more"))}"${cfg.slots.length >= 200 ? " disabled" : ""}>+</button>
      </span>
      <span class="genfill"><b>${filled}</b>/${cfg.slots.length}</span>
    </div>`;
}

// ── Eigene Kurven ─────────────────────────────────────────────────────────
// Gespeichert in config.local.yaml (generator_curves, /api/generator-curves),
// gelten fuer alle Generatoren. Gleicher Name ueberschreibt (wie bei den
// Spaltenansichten) -- ein Knopf deckt Anlegen und Aktualisieren ab.
async function genPostCurves(curves) {
  if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return false; }
  try {
    const res = await fetch("/api/generator-curves", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({curves})});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || String(res.status));
    GENERATOR_CURVES = data.generator_curves || [];
    return true;
  } catch (err) {
    note(t("gen.curve_save_failed", {msg: err.message}), true);
    return false;
  }
}

async function genSaveCurve() {
  const cur = genCurveOf(GEN.cfg.preset);
  const props = await askPlaylistProps(t("gen.curve_save"), cur ? {name: cur.name} : null, false);
  if (!props || !props.name || !GEN) return;
  const name = props.name.trim();
  const points = GEN.cfg.slots.map(s => s.e);
  const own = GENERATOR_CURVES || [];
  const same = own.find(c => c.name.toLowerCase() === name.toLowerCase());
  const id = same ? same.id : "c" + Date.now().toString(36);
  const next = same ? own.map(c => c === same ? {...c, points} : c) : [...own, {id, name, points}];
  if (!await genPostCurves(next)) return;
  GEN.cfg.preset = "curve:" + id;
  genScheduleSave(); genRender();
  note(t("gen.curve_saved", {name}));
}

async function genDeleteCurve() {
  const cur = genCurveOf(GEN.cfg.preset);
  if (!cur) return;
  if (!await askConfirmSimple(t("gen.curve_delete", {name: cur.name}), t("gen.curve_delete_note"))) return;
  if (!await genPostCurves((GENERATOR_CURVES || []).filter(c => c.id !== cur.id))) return;
  if (!GEN) return;
  GEN.cfg.preset = "custom";
  genScheduleSave(); genRender();
}

// ── Liederpool (Suchabfrage) ──────────────────────────────────────────────
// Suchfeld und erweiterte Suche werden mit jedem genRender() neu gebaut und
// hier neu verdrahtet (Elemente sind dann frisch, keine doppelten Handler).
function genBindPool() {
  const input = document.getElementById("genQuery");
  if (!input) return;
  attachSearchFilterAutocomplete(input, document.getElementById("genQueryAc"), v => genSetQuery(v, true));
  const adv = document.getElementById("genAdv");
  if (adv) advBindPanel(adv, () => GEN.cfg.query || "", v => genSetQuery(v, false));
  genSyncPool();
}
// fromInput: Text kommt aus dem Suchfeld selbst -- dessen Inhalt dann nicht
// zuruecksetzen, sonst springt die Schreibmarke.
function genSetQuery(v, fromInput) {
  if (!GEN) return;
  GEN.cfg.query = v;
  const input = document.getElementById("genQuery");
  if (input && !fromInput) input.value = v;
  genSyncPool();
  genScheduleSave();
}
// Felder der erweiterten Suche, Zaehler am Knopf und Poolgroesse nachziehen.
function genSyncPool() {
  if (!GEN) return;
  const used = advSyncPanel(document.getElementById("genAdv"), GEN.cfg.query || "");
  const badge = document.querySelector("#generatorPane .genadvbtn .countbadge");
  if (badge) { badge.textContent = used || ""; badge.hidden = !used; }
  const count = document.getElementById("genPoolCount");
  if (count) count.textContent = t("gen.pool_count", {count: genPool(GEN.cfg).length.toLocaleString("de-DE")});
}

function genSettingsHtml() {
  const cfg = GEN.cfg, f = cfg.filters, g = cfg.gates;
  // Quelle: nur eigene Playlisten/Smart Playlists. Die festen Pruef-Listen
  // (Korrekt, Fake, ...) fehlen bewusst -- dasselbe leistet /Status im
  // Liederpool, zwei Wege fuer eine Sache wuerden sich nur ueberlagern.
  const sources = PLAYLISTS.filter(n => (n.kind === "playlist" || n.kind === "smart")
    && !n.system && n.id !== GEN.id).sort((a, b) => cmpText(playlistLabel(a), playlistLabel(b)));
  const knobNames = Object.keys(cfg.knobs);
  const head = `<button type="button" class="genhead" data-act="toggle-settings">
      ${genIcon("sliders-horizontal")}<span>${esc(t("gen.settings"))}</span>
      <span class="twist${GEN_SETTINGS_CLOSED ? "" : " open"}">&#9654;</span></button>`;
  if (GEN_SETTINGS_CLOSED) return `<div class="gensettings closed">${head}</div>`;
  return `<div class="gensettings">${head}
    <div class="genfilters">
      <div class="genfield genpoolfield">
        <span class="genlbl">${esc(t("gen.pool"))}</span>
        <span class="genpair">
          <span class="searchwrap genquerywrap">
            <input type="search" class="geninput genquery" id="genQuery" autocomplete="off" spellcheck="false"
                   value="${esc(cfg.query || "")}" placeholder="${esc(t("gen.pool_ph"))}">
            <ul id="genQueryAc" class="ac-list" style="display:none"></ul>
          </span>
          <button type="button" class="act small genadvbtn${GEN_ADV_OPEN ? " on" : ""}" data-act="adv-toggle"
                  title="${esc(t("advsearch.button_title"))}">${genIcon("sliders-horizontal")}<span>${esc(t("advsearch.button"))}</span>
            <span class="countbadge" hidden></span></button>
        </span>
        <span class="genpoolcount gendim" id="genPoolCount"></span>
      </div>
      <label class="genfield">
        <span class="genlbl">${esc(t("gen.filter_source"))}</span>
        <select id="genSource" class="geninput">
          <option value="">${esc(t("gen.filter_source_all"))}</option>
          ${sources.map(n => `<option value="${esc(n.id)}"${f.source === n.id ? " selected" : ""}>${esc(playlistLabel(n))}</option>`).join("")}
        </select>
      </label>
    </div>
    ${GEN_ADV_OPEN ? `<div class="advsearch genadv" id="genAdv">${advPanelHtml("gen")}</div>` : ""}
    <div class="gengates">
      <div class="gensection">${esc(t("gen.gates"))}</div>
      <div class="genfilters">
        <label class="genfield"><span class="genlbl">${esc(t("gen.gate_bpm"))}</span>
          ${genSelect("genGateBpm", g.bpm, [["0", "±0"], ["3", "±3"], ["5", "±5"], ["7", "±7"], ["any", t("gen.gate_any")]])}</label>
        <div class="genfield" title="${esc(t("gen.gate_half_title"))}"><span class="genlbl">${esc(t("gen.gate_tempo"))}</span>
          <span class="advseg genhalfseg">
            <button type="button" data-act="half-off"${g.half ? "" : ` class="on"`}>${esc(t("gen.gate_off"))}</button>
            <button type="button" data-act="half-on"${g.half ? ` class="on"` : ""}>½ · 2×</button>
          </span></div>
        <label class="genfield" title="${esc(t("gen.gate_" + g.key + "_title"))}"><span class="genlbl">${esc(t("gen.gate_key"))}</span>
          ${genSelect("genGateKey", g.key, [["off", t("gen.gate_off"), t("gen.gate_off_title")],
            ["loose", t("gen.gate_loose"), t("gen.gate_loose_title")],
            ["strict", t("gen.gate_strict"), t("gen.gate_strict_title")]])}</label>
      </div>
    </div>
    <div class="genmood">
      <div class="gensection">${esc(t("gen.nudge"))}</div>
      <div class="genmood-head">
        ${genSeg("mode", cfg.mode, [["lean", t("gen.mode_lean"), t("gen.mode_lean_title")],
          ["all", t("gen.mode_all"), t("gen.mode_all_title")]])}
      </div>
      <div class="genknobs">
        ${knobNames.map(m => genKnobHtml(m, cfg.knobs[m], true)).join("")}
        ${genKnobHtml("cohesion", cfg.cohesion, false)}
      </div>
      <button type="button" class="act small genaddknob" data-act="addknob">${genIcon("plus")}<span>${esc(t("gen.add_knob"))}</span></button>
    </div>
  </div>`;
}

// Drehregler: bipolar (-1..1, Stimmung) oder unipolar (0..1, Kohaesion).
function genKnobHtml(name, value, bipolar) {
  const ang = v => bipolar ? v * 135 : -135 + v * 270;
  const pt = a => {
    const rad = (a - 90) * Math.PI / 180;
    return [28 + 20 * Math.cos(rad), 28 + 20 * Math.sin(rad)];
  };
  const arc = (a0, a1) => {
    const [x0, y0] = pt(a0), [x1, y1] = pt(a1);
    const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
    const sweep = a1 > a0 ? 1 : 0;
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A20 20 0 ${large} ${sweep} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  const a = ang(value), a0 = bipolar ? 0 : -135;
  const [px, py] = pt(a);
  const label = name === "cohesion" ? t("gen.cohesion") : t("mood." + name);
  const strength = Math.abs(value) < 0.05 ? 0 : Math.abs(value) < 0.34 ? 1 : Math.abs(value) < 0.67 ? 2 : 3;
  const state = bipolar
    ? (strength ? t(`gen.knob_${value > 0 ? "prefer" : "avoid"}_${strength}`) : t("gen.knob_hold"))
    : (strength ? t(`gen.pull_${strength}`) : t("gen.pull_0"));
  return `<div class="genknob${strength ? " active" : ""}${value < 0 ? " neg" : ""}" data-knob="${esc(name)}"
      title="${esc(t("gen.knob_title"))}">
    <span class="genknob-name">${esc(label)}${name === "cohesion"
      ? `<span class="genhelp" title="${esc(t("gen.cohesion_title"))}">${genIcon("circle-question-mark")}</span>` : ""}</span>
    <svg viewBox="0 0 56 56" width="56" height="56" aria-hidden="true">
      <path class="genknob-track" d="${arc(-135, 135)}"/>
      ${strength ? `<path class="genknob-val" d="${arc(Math.min(a0, a), Math.max(a0, a))}"/>` : ""}
      <circle class="genknob-cap" cx="28" cy="28" r="14"/>
      <line class="genknob-ptr" x1="28" y1="28" x2="${px.toFixed(2)}" y2="${py.toFixed(2)}"/>
    </svg>
    <span class="genknob-state">${esc(state)}</span>
    ${bipolar ? `<button type="button" class="genknob-del" data-act="knob-del" data-val="${esc(name)}" title="${esc(t("gen.knob_remove"))}">${genIcon("x")}</button>`
      : `<span class="genknob-del" aria-hidden="true"></span>`}
  </div>`;
}

// Energiekurve: Soll je Slot (ziehbar) plus tatsaechliche Energie der Belegung.
// Geometrie der Kurve -- gemeinsam fuer Zeichnen (genArcSvg()) und Eingabe
// (genArcSlotAt()/genArcEnergyAt()), damit Klickpunkt und Darstellung nie
// auseinanderlaufen. Links Platz fuer die Skala E1..E10, unten fuer die
// Slotnummern.
function genArcGeom(W, n) {
  const H = 230, padL = 34, padR = 14, padT = 12, padB = 22;
  const x = i => n === 1 ? (padL + W - padR) / 2 : padL + i * (W - padL - padR) / (n - 1);
  const y = e => padT + (10 - e) * (H - padT - padB) / 9;
  return {W, H, padL, padR, padT, padB, x, y};
}

function genArcSvg() {
  const slots = GEN.cfg.slots, n = slots.length;
  const host = document.getElementById("genArc");
  const geo = genArcGeom(Math.max(320, (host && host.clientWidth) || 900), n);
  const {W, H, padL, padR, padB, x, y} = geo;
  const pts = slots.map((s, i) => `${x(i).toFixed(1)},${y(s.e).toFixed(1)}`);
  const area = n ? `M${x(0).toFixed(1)},${y(1)} L${pts.join(" L")} L${x(n - 1).toFixed(1)},${y(1)} Z` : "";
  // Skala: jede Stufe eine Linie mit Beschriftung -- auf genau diese Stufen
  // rastet ein Klick ein.
  const grid = Array.from({length: 10}, (_, k) => k + 1).map(e =>
    `<line class="genarc-grid${e % 2 ? " odd" : ""}" x1="${padL}" x2="${W - padR}" y1="${y(e)}" y2="${y(e)}"/>` +
    `<text class="genarc-axis" x="${padL - 6}" y="${y(e) + 3}" text-anchor="end">${esc(t("gen.arc_axis", {n: e}))}</text>`).join("");
  // Je Slot eine senkrechte Fuehrung mit Nummer (bei vielen Slots nur jede
  // zweite/fuenfte beschriftet, sonst ueberlappen die Zahlen).
  const every = n > 60 ? 5 : n > 30 ? 2 : 1;
  const cols = slots.map((s, i) =>
    `<line class="genarc-col" x1="${x(i).toFixed(1)}" x2="${x(i).toFixed(1)}" y1="${y(10)}" y2="${y(1)}"/>` +
    ((i % every === 0) ? `<text class="genarc-axis" x="${x(i).toFixed(1)}" y="${H - padB + 15}" text-anchor="middle">${i + 1}</text>` : "")).join("");
  const actual = GEN.tracks.map((r, i) => r
    ? `<circle class="genarc-act" cx="${x(i).toFixed(1)}" cy="${y(r.eg).toFixed(1)}" r="3.5"><title>${esc(r.t || "")} · ${r.eg}</title></circle>` : "").join("");
  return `<div class="genarc-head"><span class="genlbl">${esc(t("gen.arc"))}</span>
      <span class="gendim">${esc(t("gen.arc_hint"))}</span>
      <span class="genspacer"></span>
      <span class="genlegend"><i class="lg-target"></i>${esc(t("gen.legend_target"))}
      <i class="lg-actual"></i>${esc(t("gen.legend_actual"))}</span></div>
    <svg class="genarc-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-w="${W}">
    ${cols}
    ${grid}
    <path class="genarc-area" d="${area}"/>
    <polyline class="genarc-line" points="${pts.join(" ")}"/>
    ${actual}
    ${slots.map((s, i) => `<circle class="genarc-pt${s.lock ? " locked" : ""}" data-pt="${i}" cx="${x(i).toFixed(1)}" cy="${y(s.e).toFixed(1)}" r="6"><title>${esc(t("gen.slot_title", {n: i + 1, energy: String(s.e).replace(".", ",")}))}</title></circle>`).join("")}
  </svg>`;
}

function genSlotsHtml() {
  const cfg = GEN.cfg, tr = GEN.tracks;
  const playing = queueState.current && queueState.current.p;
  const out = [];
  let prev = null;
  cfg.slots.forEach((s, i) => {
    const r = tr[i];
    if (r && prev) {
      const conf = genConflict(prev, r, cfg.gates);
      if (conf.length) out.push(`<div class="genconf">${genIcon("triangle-alert")}<span>${esc(conf.join(" · "))}</span></div>`);
    }
    const nr = String(i + 1).padStart(2, "0");
    const target = `<span class="gentarget" title="${esc(t("gen.target_title"))}">${esc(t("gen.target", {energy: String(s.e).replace(".", ",")}))}</span>`;
    const del = `<button type="button" class="iconbtn plain" data-act="slot-del" data-slot="${i}" title="${esc(t("gen.slot_delete"))}">${ICONS.trash}</button>`;
    const pick = `<button type="button" class="iconbtn plain" data-act="slot-alt" data-slot="${i}" title="${esc(t("gen.slot_alternatives"))}">${genIcon("list")}</button>`;
    const roll = `<button type="button" class="iconbtn plain" data-act="slot-roll" data-slot="${i}" title="${esc(t("gen.slot_roll"))}">${genIcon("dices")}</button>`;
    if (!r) {
      out.push(`<div class="genslot empty" data-slot="${i}">
        <span class="gennr">${nr}</span>
        <span class="genmeta"><span class="gentitle">${esc(t("gen.slot_empty"))}</span></span>
        ${target}<span class="genacts">${roll}${pick}${del}</span></div>`);
      return;
    }
    prev = r;
    const lockBtn = `<button type="button" class="iconbtn plain genlock${s.lock ? " on" : ""}" data-act="slot-lock" data-slot="${i}" title="${esc(t(s.lock ? "gen.slot_unlock" : "gen.slot_lock"))}">${genIcon(s.lock ? "lock" : "lock-open")}</button>`;
    const remove = `<button type="button" class="iconbtn plain" data-act="slot-clear" data-slot="${i}" title="${esc(t("gen.slot_clear"))}">${genIcon("x")}</button>`;
    const sub = [r.a, r.ge].filter(Boolean).map(esc).join(` <span class="gendim">·</span> `);
    out.push(`<div class="genslot${s.lock ? " locked" : ""}${playing === r.p ? " playing" : ""}" data-slot="${i}" draggable="true" title="${esc(t("gen.slot_play"))}">
      <span class="gennr">${nr}</span>
      ${apiMode && r.cv ? `<img class="gencov" src="${coverUrl(r.p)}" alt="" loading="lazy">` : `<span class="gencov"></span>`}
      <span class="genmeta"><span class="gentitle">${esc(r.t || baseName(r.p))}</span><span class="gensub">${sub}</span></span>
      ${apiMode ? `<canvas class="wave genwave" data-wave="${i}" height="34" title="${esc(t("action.seek"))}"></canvas>` : ""}
      ${energyCell(r.eg)}${target}
      <span class="genacts">${roll}${pick}${remove}${del}</span>${lockBtn}
      <span class="genkey">${keyBubble(r.ky, r.kr)}</span>
      <span class="genbpm">${r.bp ? Math.round(r.bp) : "–"}<small>BPM</small></span>
    </div>`);
  });
  return out.join("");
}

// Nur Kurve und Liste neu zeichnen (nach Ziehen/Belegung), Einstellungen
// bleiben stehen -- ein Eingabefeld darin behaelt so seinen Fokus.
function genRenderBody() {
  const arc = document.getElementById("genArc"), list = document.getElementById("genSlots");
  if (arc) arc.innerHTML = genArcSvg();
  if (list) list.innerHTML = genSlotsHtml();
  genMountSlotWaves();
  const fill = document.querySelector("#generatorPane .genfill");
  if (fill) fill.innerHTML = `<b>${GEN.tracks.filter(Boolean).length}</b>/${GEN.cfg.slots.length}`;
}

// ── Bedienung ─────────────────────────────────────────────────────────────
function genSetCount(n, g = GEN) {
  const cfg = g.cfg;
  n = Math.max(1, Math.min(200, Math.round(n) || 1));
  if (cfg.preset === "custom") {
    while (cfg.slots.length < n) {
      const last = cfg.slots[cfg.slots.length - 1];
      cfg.slots.push({e: last ? last.e : 5, lock: false, filled: false});
    }
    cfg.slots.length = n;
  } else {
    genResample(cfg, n);
  }
  while (g.tracks.length < n) g.tracks.push(null);
  g.tracks.length = n;
}

function genMoveSlot(from, to) {
  if (from === to || from < 0 || to < 0) return;
  const [s] = GEN.cfg.slots.splice(from, 1);
  const [r] = GEN.tracks.splice(from, 1);
  GEN.cfg.slots.splice(to, 0, s);
  GEN.tracks.splice(to, 0, r);
  genScheduleSave();
  genRenderBody();
}

function genPlay(i) {
  if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return; }
  const rows = GEN.tracks.filter(Boolean);
  const r = GEN.tracks[i];
  const idx = rows.indexOf(r);
  if (idx === -1) return;
  if (queueState.current) pushQueueHistory(queueState.current);
  queueState.tracks = rows.slice(idx + 1);
  queueState.manual = true;
  loadQueueTrack(r);
  genRenderBody();
}

// ── Waveform je Slot ──────────────────────────────────────────────────────
// Dieselbe Zeichen-Mechanik wie die aufgeklappte Tabellenzeile
// (registerWaveEntry()/paintWaveEntry() in app.js): eine Bildschleife, die
// Ebene wird nur bei neuen Daten/Breite neu gerechnet, abgebaut wird ueber
// den gemeinsamen MutationObserver, sobald die Liste neu gebaut ist. Der
// Slot, der gerade im Player laeuft, zeigt den Fortschritt. Ein Klick in die
// Waveform springt dorthin (startet den Slot, falls er noch nicht laeuft).
function genMountSlotWaves() {
  if (!GEN || !apiMode) return;
  if (!GEN_PEAKS) GEN_PEAKS = new Map();
  if (!GEN_RBWAVE) GEN_RBWAVE = new Map();
  const jobs = [];
  jobs.push = (fn, canvas) => { jobs[jobs.length] = {fn, canvas}; };
  document.querySelectorAll("#genSlots canvas.genwave").forEach(canvas => {
    const i = Number(canvas.dataset.wave);
    const r = GEN.tracks[i];
    if (!r) return;
    const isCurrent = () => !!(queueState.current && queueState.current.p === r.p);
    const entry = registerWaveEntry({
      root: canvas.closest(".genslot"), canvas, r, half: true, peaks: null, cues: null, waveform: null,
      sync() {
        const cur = isCurrent();
        this.currentTime = cur ? queueAudio.currentTime : 0;
        this.duration = cur ? queueAudio.duration : r.du;
      },
    });
    const cached = GEN_PEAKS.get(r.p);
    if (cached) setWaveData(entry, {peaks: cached});
    else jobs.push(() => peaksForLibrary(r.p)
      .then(p => { GEN_PEAKS.set(r.p, p); setWaveData(entry, {peaks: p}); })
      .catch(() => setWaveData(entry, {peaks: []})), canvas);
    // Halbe, einfarbige Huellkurve (entry.half) -- keine Rekordbox-Farben.
    canvas.onclick = ev => {
      ev.stopPropagation();
      const rect = canvas.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
      if (isCurrent()) {
        if (isFinite(queueAudio.duration)) queueAudio.currentTime = frac * queueAudio.duration;
        if (queueAudio.paused) queueAudio.play().catch(err => playFailed(queueAudio, err));
        requestWavePaint();
      } else {
        pendingSeek = isFinite(r.du) ? frac * r.du : null;
        genPlay(i);
      }
    };
  });
  genRunWaveJobs(jobs);
}

// Waveform-Daten erst nachladen, wenn die Cover der Liste da sind, und dann
// nur wenige gleichzeitig: der Browser erlaubt je Host nur ~6 Verbindungen,
// ein Schwung Waveform-Anfragen (serverseitig ffmpeg) hielt sonst Cover,
// Audio und alle anderen Aufrufe auf. Neuaufbau der Liste verwirft den Rest.
var GEN_WAVE_RUN;
function genRunWaveJobs(jobs) {
  const run = GEN_WAVE_RUN = {};
  if (!jobs.length) return;
  const imgs = Array.from(document.querySelectorAll("#genSlots img.gencov"))
    .filter(im => !im.complete);
  const coversDone = Promise.race([
    Promise.all(imgs.map(im => new Promise(res => {
      im.addEventListener("load", res, {once: true});
      im.addEventListener("error", res, {once: true});
    }))),
    new Promise(res => setTimeout(res, 4000)),
  ]);
  coversDone.then(() => new Promise(res => setTimeout(res, 50))).then(() => {
    let next = 0;
    const worker = async () => {
      while (GEN_WAVE_RUN === run && next < jobs.length) {
        const job = jobs[next++];
        if (!job.canvas.isConnected) continue;
        await job.fn();
      }
    };
    worker(); worker();
  });
}

function genOpenAlternatives(i, btn) {
  const g = GEN;
  const pool = genPool(g.cfg);
  const ranks = genRanks(pool, g.cfg.knobs);
  const used = new Set(g.tracks.filter((r, j) => r && j !== i).map(r => r.p));
  const cands = genCandidates(g, i, pool, ranks, used).slice(0, 8);
  if (!cands.length) { note(t("gen.pool_empty"), "soft"); return; }
  openContextMenu(cands.map(({r}) => ({
    label: `${r.a ? r.a + " – " : ""}${r.t || baseName(r.p)} · ${Math.round(r.eg)} · ${keyLabel(r.ky) || "–"} · ${r.bp ? Math.round(r.bp) : "–"}`,
    action: () => { g.tracks[i] = r; genScheduleSave(); genRenderBody(); },
  })), btn);
}

// Momentaufnahme der Belegung als normale Playlist -- unabhaengig vom
// Generator, ein spaeteres Neu-Wuerfeln aendert sie nicht mehr. Gleiches
// Muster wie createTopTracksPlaylist() in app.js; der Generator bleibt offen.
async function genSaveAsPlaylist() {
  const g = GEN;
  const paths = g.tracks.filter(Boolean).map(r => r.p);
  if (!paths.length) { note(t("tree.empty_list"), "soft"); return; }
  const node = PLAYLISTS.find(n => n.id === g.id);
  const props = await askPlaylistProps(t("gen.save_playlist_dialog"), {
    name: t("gen.save_playlist_name", {
      name: node ? playlistLabel(node) : "", date: new Date().toLocaleDateString("de-DE")}),
  }, true, null);
  if (!props) return;
  try {
    const data = await playlistApi("/api/playlist", {
      op: "create", kind: "playlist", name: props.name,
      parent_id: node ? node.parent_id || null : null,
      icon: props.icon, color: props.color,
    });
    await playlistApi("/api/playlist-items", {op: "set", id: data.node.id, paths});
    await reloadPlaylists();
    if (props.columnView) setColumnAssign(`pl:${data.node.id}`, props.columnView);
    renderTree();
    note(t("gen.save_playlist_done", {name: props.name, count: paths.length}));
  } catch (err) {
    note(t("tree.create_failed", {error: err.message}), true);
  }
}

function genAddKnob(btn) {
  const have = new Set(Object.keys(GEN.cfg.knobs));
  const items = genMoods().filter(m => !have.has(m)).map(m => ({
    label: t("mood." + m),
    action: () => { GEN.cfg.knobs[m] = 0; genScheduleSave(); genRender(); },
  }));
  if (!items.length) { note(t("gen.all_knobs"), "soft"); return; }
  openContextMenu(items, btn);
}

function genOnClick(ev) {
  const el = ev.target.closest("[data-act], [data-seg], .genslot");
  if (!el || !GEN) return;
  const cfg = GEN.cfg;
  const slot = el.dataset.slot !== undefined ? Number(el.dataset.slot) : -1;
  if (el.dataset.seg) {
    const v = el.dataset.val;
    if (el.dataset.seg === "mode") cfg.mode = v;
    genScheduleSave(); genRender();
    return;
  }
  switch (el.dataset.act) {
    case "fill": genFill("empty"); return;
    case "reroll": GEN.seed = (Math.random() * 2 ** 31) >>> 0; genFill("all"); return;
    case "clear":
      cfg.slots.forEach((s, j) => { if (!s.lock) GEN.tracks[j] = null; });
      genScheduleSave(); genRender(); return;
    case "scan": document.getElementById("btnScan").click(); return;
    case "m3u": {
      const node = PLAYLISTS.find(n => n.id === GEN.id);
      downloadM3u(GEN.tracks.filter(Boolean), node ? playlistLabel(node) : "generator");
      return;
    }
    case "save-playlist": genSaveAsPlaylist(); return;
    case "addslot": genSetCount(cfg.slots.length + 1); genScheduleSave(); genRender(); return;
    case "count-inc": genSetCount(cfg.slots.length + 1); genScheduleSave(); genRender(); return;
    case "count-dec": genSetCount(cfg.slots.length - 1); genScheduleSave(); genRender(); return;
    case "toggle-settings": GEN_SETTINGS_CLOSED = !GEN_SETTINGS_CLOSED; genRender(); return;
    case "adv-toggle": GEN_ADV_OPEN = !GEN_ADV_OPEN; genRender(); return;
    case "curve-save": genSaveCurve(); return;
    case "half-on": case "half-off":
      cfg.gates.half = el.dataset.act === "half-on";
      el.parentNode.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === el));
      genScheduleSave(); genRenderBody(); return;
    case "curve-del": genDeleteCurve(); return;
    // Menues: Klick nicht bis zum document durchreichen, dessen Handler
    // schliesst #rowMoreMenu sonst sofort wieder. Bewusst nicht ueber
    // [data-more] geloest -- das blendet die Player-Ansicht aus (app.css).
    case "addknob": ev.stopPropagation(); genAddKnob(el); return;
    case "knob-del": delete cfg.knobs[el.dataset.val]; genScheduleSave(); genRender(); return;
    case "slot-lock": cfg.slots[slot].lock = !cfg.slots[slot].lock; genScheduleSave(); genRenderBody(); return;
    case "slot-clear": GEN.tracks[slot] = null; cfg.slots[slot].lock = false; genScheduleSave(); genRenderBody(); return;
    case "slot-roll": GEN.seed++; genFill(slot); return;
    case "slot-alt": ev.stopPropagation(); genOpenAlternatives(slot, el); return;
    case "slot-del":
      cfg.slots.splice(slot, 1); GEN.tracks.splice(slot, 1);
      if (cfg.preset !== "custom") genResample(cfg, cfg.slots.length);
      genScheduleSave(); genRender(); return;
  }
  // Klick auf eine belegte Zeile selbst (nicht auf einen Knopf): abspielen.
  if (el.classList.contains("genslot") && GEN.tracks[slot]) genPlay(slot);
}

function genOnChange(ev) {
  if (!GEN) return;
  const cfg = GEN.cfg, el = ev.target;
  if (el.id === "genSource") { cfg.filters.source = el.value || null; genSyncPool(); }
  else if (el.id === "genGateBpm" || el.id === "genGateKey") {
    cfg.gates[el.id === "genGateBpm" ? "bpm" : "key"] = el.value;
    // Tooltip am Tonart-Feld beschreibt die gewaehlte Stufe.
    if (el.id === "genGateKey") el.closest("label").title = t("gen.gate_" + el.value + "_title");
    genScheduleSave(); genRenderBody(); return;
  }
  else if (el.id === "genPreset") {
    cfg.preset = el.value;
    if (cfg.preset !== "custom") genResample(cfg, cfg.slots.length);
    genScheduleSave(); genRender(); return;
  } else if (el.id === "genCount") {
    genSetCount(Number(el.value));
    genScheduleSave(); genRender(); return;
  } else return;
  genScheduleSave();
}

// Ziehen an Kurvenpunkten (senkrecht = Energie) und Reglern (senkrecht = Wert).
// Zeichnen auf der Skala: ein Klick in die Spalte eines Slots setzt dessen
// Soll-Energie auf die angeklickte Stufe (ganze Zahlen 1..10), Ziehen ueber
// mehrere Slots zeichnet sie der Reihe nach -- auch Slots, ueber die die Maus
// zwischen zwei Ereignissen hinweggesprungen ist (linear dazwischen).
function genArcPos(svg, e) {
  const rect = svg.getBoundingClientRect();
  const geo = genArcGeom(Number(svg.dataset.w), GEN.cfg.slots.length);
  const xx = (e.clientX - rect.left) * geo.W / rect.width;
  const yy = (e.clientY - rect.top) * geo.H / rect.height;
  const n = GEN.cfg.slots.length;
  const step = n > 1 ? (geo.W - geo.padL - geo.padR) / (n - 1) : 1;
  const slot = n > 1 ? Math.round((xx - geo.padL) / step) : 0;
  const energy = 10 - (yy - geo.padT) * 9 / (geo.H - geo.padT - geo.padB);
  return {slot: Math.min(n - 1, Math.max(0, slot)), energy: Math.min(10, Math.max(1, Math.round(energy)))};
}

function genOnPointerDown(ev) {
  if (!GEN) return;
  const svg = ev.target.closest(".genarc-svg");
  const knob = !svg && ev.target.closest("[data-knob]");
  if (knob && ev.target.closest(".genknob-del")) return;
  if (!svg && !knob) return;
  if (ev.button !== undefined && ev.button !== 0) return;
  ev.preventDefault();
  const cfg = GEN.cfg;
  if (svg) {
    if (!cfg.slots.length) return;
    let last = null;
    // Nach jedem Neuzeichnen ist das alte <svg> ersetzt -- Koordinaten
    // deshalb immer am aktuellen messen, nicht am angeklickten.
    const paint = e => {
      const cur = document.querySelector("#genArc .genarc-svg") || svg;
      const {slot, energy} = genArcPos(cur, e);
      const from = last ? last.slot : slot;
      const dir = slot >= from ? 1 : -1;
      for (let i = from; ; i += dir) {
        const span = slot - from;
        cfg.slots[i].e = span ? Math.round(last.energy + (energy - last.energy) * (i - from) / span) : energy;
        if (i === slot) break;
      }
      last = {slot, energy};
      cfg.preset = "custom";
      const arc = document.getElementById("genArc");
      if (arc) arc.innerHTML = genArcSvg();
    };
    paint(ev);
    genDrag(paint, () => { genScheduleSave(); genRender(); });
    return;
  }
  const name = knob.dataset.knob, bipolar = name !== "cohesion";
  const startY = ev.clientY;
  const start = bipolar ? cfg.knobs[name] : cfg.cohesion;
  const move = e => {
    const span = bipolar ? 2 : 1;
    let v = start + (startY - e.clientY) / 150 * span;
    v = Math.round(Math.min(1, Math.max(bipolar ? -1 : 0, v)) * 20) / 20;
    if (bipolar) cfg.knobs[name] = v; else cfg.cohesion = v;
    const host = document.querySelector(`#generatorPane [data-knob="${CSS.escape(name)}"]`);
    if (host) host.outerHTML = genKnobHtml(name, v, bipolar);
  };
  genDrag(move, () => genScheduleSave());
}

function genDrag(move, done) {
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    done();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
}

function genOnDblClick(ev) {
  const knob = ev.target.closest("[data-knob]");
  if (!knob || !GEN) return;
  const name = knob.dataset.knob;
  if (name === "cohesion") GEN.cfg.cohesion = 0.5; else GEN.cfg.knobs[name] = 0;
  knob.outerHTML = genKnobHtml(name, name === "cohesion" ? 0.5 : 0, name !== "cohesion");
  genScheduleSave();
}

// Ziehen von Slots: innerhalb der Liste umsortieren, in den Baum als Tracks
// (DND_TRACKS, dieselbe Ablage wie aus der Tabelle).
function genOnDragStart(ev) {
  const row = ev.target.closest(".genslot[data-slot]");
  if (!row || !GEN) return;
  const i = Number(row.dataset.slot), r = GEN.tracks[i];
  if (!r) { ev.preventDefault(); return; }
  GEN_DRAG_FROM = i;
  ev.dataTransfer.effectAllowed = "copyMove";
  ev.dataTransfer.setData(DND_TRACKS, JSON.stringify([r.p]));
  ev.dataTransfer.setDragImage(buildDragBadge(r, 0), 12, 12);
}
// Fremde Tracks (aus Tabelle/Baum, nicht aus den eigenen Slots) werden
// ueber DND_TRACKS erkannt: der interne Slot-Zug traegt dieselbe Ablage, hat
// aber GEN_DRAG_FROM gesetzt.
function genIsForeignDrag(ev) {
  return GEN && !(GEN_DRAG_FROM >= 0) && [...(ev.dataTransfer.types || [])].includes(DND_TRACKS);
}

// Abgelegte Tracks als neue Slots ans Ende haengen und sperren: von Hand
// gesetzt, also soll "Alle neu wuerfeln" sie nicht ersetzen.
function genAddDropped(paths, g = GEN) {
  const have = new Set(g.tracks.filter(Boolean).map(r => r.p));
  const rows = [];
  for (const p of paths) {
    const r = ROW_BY_PATH.get(p);
    if (!r || r.ext || r.gone || have.has(p)) continue;
    have.add(p);
    rows.push(r);
  }
  const room = 200 - g.cfg.slots.length;
  if (!rows.length) { note(t("gen.drop_none"), "soft"); return 0; }
  if (room <= 0) { note(t("gen.drop_full"), "soft"); return 0; }
  rows.length = Math.min(rows.length, room);
  for (const r of rows) {
    genSetCount(g.cfg.slots.length + 1, g);
    const i = g.cfg.slots.length - 1;
    g.tracks[i] = r;
    g.cfg.slots[i].lock = true;
    g.cfg.slots[i].filled = true;
  }
  if (g === GEN) { genScheduleSave(); genRender(); } else genSave(g);
  note(t("gen.drop_added", {count: rows.length}));
  return rows.length;
}

// Ablage auf einen Generator im Baum -- auch wenn er nicht offen ist: dann
// wird Einstellung und Belegung frisch aus dem Knoten gelesen und gespeichert.
function genDropOnNode(id, paths) {
  if (GEN && GEN.id === id) return genAddDropped(paths);
  const node = PLAYLISTS.find(n => n.id === id);
  if (!node) return 0;
  const cfg = genParseConfig(node);
  return genAddDropped(paths, {id, cfg, tracks: genTracksFromItems(id, cfg), saveTimer: null});
}

function genOnDragOver(ev) {
  if (genIsForeignDrag(ev)) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    document.getElementById("generatorPane").classList.add("gendrop");
    return;
  }
  const row = ev.target.closest(".genslot[data-slot]");
  if (!row || !(GEN_DRAG_FROM >= 0)) return;
  ev.preventDefault();
  document.querySelectorAll("#genSlots .dragover").forEach(x => x.classList.remove("dragover"));
  row.classList.add("dragover");
}
function genOnDrop(ev) {
  document.getElementById("generatorPane").classList.remove("gendrop");
  if (genIsForeignDrag(ev)) {
    ev.preventDefault();
    let paths = [];
    try { paths = JSON.parse(ev.dataTransfer.getData(DND_TRACKS) || "[]"); }
    catch (e) { paths = []; }
    genAddDropped(paths);
    return;
  }
  const row = ev.target.closest(".genslot[data-slot]");
  document.querySelectorAll("#genSlots .dragover").forEach(x => x.classList.remove("dragover"));
  if (!row || !(GEN_DRAG_FROM >= 0)) return;
  ev.preventDefault();
  const from = GEN_DRAG_FROM;
  GEN_DRAG_FROM = -1;
  genMoveSlot(from, Number(row.dataset.slot));
}

// Handler einmal je Element setzen (das Element bleibt bestehen, nur sein
// Inhalt wird neu gebaut) -- Delegation statt Handler je Knopf.
function genWire(pane) {
  if (pane._genWired) return;
  pane._genWired = true;
  pane.addEventListener("click", genOnClick);
  pane.addEventListener("change", genOnChange);
  // Liederpool-Suchfeld: jede Eingabe sofort uebernehmen (Speichern ist
  // ohnehin entprellt). Die Felder der erweiterten Suche haben ihren eigenen
  // Weg (advBindPanel() in genBindPool()).
  pane.addEventListener("input", ev => {
    if (ev.target.id === "genQuery") genSetQuery(ev.target.value, true);
  });
  pane.addEventListener("pointerdown", genOnPointerDown);
  pane.addEventListener("dblclick", genOnDblClick);
  pane.addEventListener("dragstart", genOnDragStart);
  pane.addEventListener("dragover", genOnDragOver);
  pane.addEventListener("drop", genOnDrop);
  pane.addEventListener("dragend", () => { GEN_DRAG_FROM = -1; });
  pane.addEventListener("dragleave", ev => {
    if (!pane.contains(ev.relatedTarget)) pane.classList.remove("gendrop");
  });
  // has_cover ist gesetzt, das Bild aber nicht lesbar (404) -- leere Flaeche
  // statt Symbol fuer ein kaputtes Bild. 'error' steigt nicht auf, deshalb capture.
  pane.addEventListener("error", ev => {
    const img = ev.target;
    if (img.tagName === "IMG" && img.classList.contains("gencov")) {
      const ph = document.createElement("span");
      ph.className = "gencov";
      img.replaceWith(ph);
    }
  }, true);
  // Die Kurve rechnet mit der tatsaechlichen Breite (Kreise sollen rund bleiben).
  new ResizeObserver(() => {
    const arc = document.getElementById("genArc");
    if (GEN && arc) arc.innerHTML = genArcSvg();
  }).observe(pane);
}
