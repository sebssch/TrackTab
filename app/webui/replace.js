// ── Suchen & Ersetzen + Ersetzungsregeln ─────────────────────────────────
// Laeuft wie generator.js/dj.js im selben <script> NACH app.js. app.js ruft
// ruleIssuesOf()/issueLabel() schon beim ersten render() auf, also bevor
// diese Datei ausgewertet ist: auf oberster Ebene deshalb nur
// Funktionsdeklarationen und var OHNE Startwert (siehe CLAUDE.md, generator.js).
//
// Die Treffer-Logik (parseReplaceTerms()/replaceInValue()) steht NUR hier --
// der Dialog, die Auffaelligkeiten "rule:<id>" (je Regel eine eigene
// Kategorie), die Vorschlaege im Tags-Dialog und die Einzelpruefungen nutzen
// dieselben zwei Funktionen. Der Server kennt die Syntax nicht;
// /api/tag-replace prueft nur, ob der alte Wert noch dem DB-Stand entspricht.

var REPLACE_RULES;        // gespeicherte Regeln (/api/settings -> replace_rules)
var RULE_COMPILED;        // {sig, rules: [{rule, terms}]} der aktiven Regeln
var RULE_EVAL_CACHE;      // WeakMap Zeile -> {sig, ev}
var RP;                   // offener Dialog
var RP_LAST;              // zuletzt gewaehlte Felder/Optionen (nur diese Sitzung)
var TAG_SYNC_QUEUE;       // Pfade fuer /api/tag-replace-finish (Quick Fix)
var tagSyncTimer;
var rrDraft;              // Entwurf im Einstellungs-Block

// Felder: Schluessel wie /api/tags bzw. tags.write_tags(), Zeilenschluessel
// wie TAGS_BULK_FIELD_MAP, Eingabefeld/Vorschlagsknopf des Tags-Dialogs.
function replaceFields() {
  return [
    {key: "title", rk: "t", input: "tagsTitle", sug: "tagsSuggestTitle"},
    {key: "artist", rk: "a", input: "tagsArtist", sug: "tagsSuggestArtist"},
    {key: "album", rk: "al", input: "tagsAlbum", sug: "tagsSuggestAlbum"},
    {key: "album_artist", rk: "aa", input: "tagsAlbumArtist", sug: "tagsSuggestAlbumArtist"},
    {key: "composer", rk: "cp", input: "tagsComposer", sug: "tagsSuggestComposer"},
    {key: "genre", rk: "ge", input: "tagsGenre", sug: "tagsSuggestGenre"},
    {key: "comment", rk: "cm", input: "tagsComment", sug: "tagsSuggestComment"},
  ];
}
// Eigene Tags (nur DB, nie in der Datei) sind ein zusaetzliches Feld hinter den Textfeldern.
var REPLACE_TAGS_KEY = "user_tags";
function replaceTextKeys() { return replaceFields().map(f => f.key); }
function replaceFieldKeys() { return [...replaceTextKeys(), REPLACE_TAGS_KEY]; }
function replaceFieldLabel(key) { return key === REPLACE_TAGS_KEY ? t("tags.user_tags") : t("field." + key); }
function replaceFieldsLabel(keys) {
  const all = replaceFieldKeys();
  if (all.every(k => keys.includes(k))) return t("replace.field_all");
  return all.filter(k => keys.includes(k)).map(replaceFieldLabel).join(", ");
}

// Optionen einer Suche bzw. Regel. Eine weitere Option kommt hier dazu (und in
// settings.validate_replace_rules() sowie ruleOpts()/replaceFold()); Dialog und
// Einstellungen zeichnen die Leiste aus dieser Liste. Alle sind "beachten"-
// Schalter und stehen standardmaessig aus.
function replaceOptionDefs() {
  return [
    {key: "case", short: "Aa", label: t("replace.opt_case"), tip: t("replace.opt_case_tip")},
    {key: "accents", short: "é", label: t("replace.opt_accents"), tip: t("replace.opt_accents_tip")},
  ];
}

// ── Treffer-Logik ─────────────────────────────────────────────────────────
// Suchtext -> [{needle, mode}]. Komma trennt Begriffe (ausserhalb von
// Anfuehrungszeichen). Ohne Zeichen = ganzes Wort, „…“/"…" = genaue Phrase
// (ebenfalls ganzes Wort, darf Komma und Leerzeichen am Rand enthalten),
// *x* = Teilstueck, x* = Wortanfang, *x = Wortende.
function parseReplaceTerms(text) {
  const out = [];
  const OPEN = "„\"“", CLOSE = "“”\"";
  let cur = "", quoted = false, inQuote = false;
  const push = () => {
    let s = quoted ? cur : cur.trim();
    if (s) {
      let mode = "word";
      if (!quoted) {
        const lead = s.startsWith("*"), trail = s.endsWith("*") && s.length > 1;
        s = s.replace(/^\*+/, "").replace(/\*+$/, "");
        mode = lead && trail ? "part" : lead ? "suffix" : trail ? "prefix" : "word";
      }
      if (s) out.push({needle: s, mode});
    }
    cur = ""; quoted = false;
  };
  for (const ch of String(text || "")) {
    if (inQuote) {
      if (CLOSE.includes(ch)) inQuote = false; else cur += ch;
    } else if (OPEN.includes(ch) && !cur.trim()) {
      inQuote = true; quoted = true; cur = "";
    } else if (ch === ",") {
      push();
    } else if (!quoted) {
      cur += ch;
    }
  }
  push();
  return out;
}

// Gefaltete Kopie (klein, ohne Akzente -- je nach Schalter) mit Indexkarte
// zurueck ins Original: map[k] = Startindex des Originalzeichens, aus dem das
// k-te gefaltete Zeichen stammt; map[Laenge] = Laenge des Originals.
// Zeichenweise gefaltet, weil NFD/toLowerCase die Laenge aendern koennen.
function replaceFold(s, opts) {
  let out = "";
  const map = [];
  let i = 0;
  for (const ch of s) {
    let f = opts.caseSensitive ? ch : ch.toLowerCase();
    if (!opts.accentSensitive) f = foldDiacritics(f);
    for (let k = 0; k < f.length; k++) map.push(i);
    out += f;
    i += ch.length;
  }
  map.push(s.length);
  return {s: out, map};
}
function replaceFoldNeedle(s, opts) {
  let f = opts.caseSensitive ? s : s.toLowerCase();
  return opts.accentSensitive ? f : foldDiacritics(f);
}
function isWordChar(ch) { return !!ch && /[\p{L}\p{N}]/u.test(ch); }

// value -> {value, hits: [[s, e]] im Original, out: [[s, e]] im Ergebnis}.
// Treffer ueberlappen sich nicht; bei gleichem Start gewinnt der laengste
// Begriff ("ft." vor "ft"). Endet der Ersatz auf ein Satzzeichen, das direkt
// hinter dem Treffer schon steht, wird es mitgenommen ("ft" -> "feat." in
// "ft. X" ergibt "feat. X", nicht "feat.. X"). Ein leerer Ersatz entfernt den
// Treffer samt EINEM angrenzenden Leerzeichen, wenn sonst zwei aufeinander
// stuenden ("Song (Original Mix) X" -> "Song X").
function replaceInValue(value, terms, repl, opts) {
  value = String(value || "");
  if (!value || !terms.length) return {value, hits: [], out: []};
  opts = opts || {};
  const hay = replaceFold(value, opts);
  const found = [];
  for (const term of terms) {
    const needle = replaceFoldNeedle(term.needle, opts);
    if (!needle) continue;
    const startWord = isWordChar(term.needle[0]), endWord = isWordChar(term.needle[term.needle.length - 1]);
    let from = 0;
    while (true) {
      const k = hay.s.indexOf(needle, from);
      if (k < 0) break;
      from = k + 1;
      const s = hay.map[k], e = hay.map[k + needle.length];
      const checkStart = term.mode === "word" || term.mode === "prefix";
      const checkEnd = term.mode === "word" || term.mode === "suffix";
      if (checkStart && startWord && isWordChar(value[s - 1])) continue;
      if (checkEnd && endWord && isWordChar(value[e])) continue;
      found.push([s, e]);
    }
  }
  if (!found.length) return {value, hits: [], out: []};
  found.sort((a, b) => a[0] - b[0] || (b[1] - b[0]) - (a[1] - a[0]));
  const hits = [];
  let last = -1;
  for (const h of found) if (h[0] >= last) { hits.push(h); last = h[1]; }
  const tail = repl.slice(-1);
  let res = "", pos = 0;
  const out = [];
  for (const h of hits) {
    let e = h[1];
    if (tail && !isWordChar(tail) && tail.trim() && value[e] === tail) e++;
    res += value.slice(pos, h[0]);
    if (!repl && /\s/.test(value[e] || "") && (!res || /\s$/.test(res))) e++;
    out.push([res.length, res.length + repl.length]);
    res += repl;
    pos = e;
    h[1] = e;
  }
  res += value.slice(pos);
  return {value: res, hits, out};
}

// ── Gespeicherte Regeln ──────────────────────────────────────────────────
// Alte Regeln hatten ein einzelnes 'field' ("all" = alle); jetzt eine Liste.
function normalizeRule(r) {
  let fields = Array.isArray(r.fields) ? r.fields : null;
  if (!fields) fields = !r.field || r.field === "all" ? replaceTextKeys() : [r.field];
  return {id: r.id || "", on: r.on !== false, terms: r.terms || "", fields,
          replace: r.replace || "", case: !!r.case, accents: !!r.accents};
}
function replaceRules() { return Array.isArray(REPLACE_RULES) ? REPLACE_RULES : []; }
function ruleOpts(rule) { return {caseSensitive: !!rule.case, accentSensitive: !!rule.accents}; }
function compiledRules() {
  const active = replaceRules().filter(r => r.on && r.terms && r.fields.length);
  const sig = JSON.stringify(active);
  if (!RULE_COMPILED || RULE_COMPILED.sig !== sig) {
    RULE_COMPILED = {sig, rules: active.map(rule => ({rule, terms: parseReplaceTerms(rule.terms)}))};
  }
  return RULE_COMPILED;
}
function ruleLabel(rule) {
  return `${rule.terms} → ${rule.replace || t("replace.remove_label")} (${replaceFieldsLabel(rule.fields)})`;
}

// Auffaelligkeit je Regel: Code "rule:<id>", eigene Kategorie in der Liste
// "Auffaelligkeiten" (Bubble-Reihe, Sortierung, Ignorieren). Quick Fix ist
// immer moeglich.
function isRuleCode(code) { return typeof code === "string" && code.startsWith("rule:"); }
function isAutoFixCode(code) { return AUTO_FIXABLE_TAG_ISSUE_CODES.has(code) || isRuleCode(code); }
function issueLabel(code) {
  if (!isRuleCode(code)) return t("tagissue." + code);
  const rule = replaceRules().find(r => r.id === code.slice(5));
  return rule ? t("tagissue.rule", {rule: `${rule.terms} → ${rule.replace || t("replace.remove_label")}`})
              : t("tagissue.rule_unknown");
}

// Alle aktiven Regeln nacheinander auf EINEN Wert (so schreibt es auch der
// Quick Fix): -> {value, rules: [wirksame Regeln]}. Ein Ergebnis ohne Inhalt
// zaehlt nicht (das Feld bliebe leer). 'allow' (Set von Regel-IDs) grenzt auf
// diese Regeln ein (Quick Fix einer einzelnen Auffaelligkeit, quickFixPlan()).
function applyRulesToValue(value, field, row, allow) {
  let cur = String(value || "").trim();
  const orig = cur;
  const used = [];
  for (const {rule, terms} of compiledRules().rules) {
    if (!rule.fields.includes(field)) continue;
    if (allow && !allow.has(rule.id)) continue;
    if (row && issueIgnored(row, "rule:" + rule.id)) continue;       // am Track ignorierte Regel
    const res = replaceInValue(cur, terms, rule.replace || "", ruleOpts(rule));
    const nv = res.value.trim();
    if (res.hits.length && nv && nv !== cur) { cur = nv; used.push(rule); }
  }
  const changed = cur !== orig;
  return {value: changed ? cur : String(value || ""), rules: changed ? used : []};
}
// Je Zeile: {fields: {feld: neu} nach ALLEN Regeln | null, tags: [{old, new}] der eigenen
// Tags nach ALLEN Regeln | null, issues: [je Regel eine]}. Eine Meldung traegt die Aenderungen
// NUR dieser Regel: fields (Textfelder) und tagChanges ([{orig, new}], orig = Tag in der DB).
function ruleEval(r) {
  const none = {fields: null, tags: null, issues: []};
  if (!r || r.gone || r.ext || !r.p) return none;
  const sig = compiledRules().sig;
  if (!RULE_EVAL_CACHE) RULE_EVAL_CACHE = new WeakMap();
  const hit = RULE_EVAL_CACHE.get(r);
  if (hit && hit.sig === sig) return hit.ev;
  let running = null, runTags = null;
  const issues = [];
  for (const {rule, terms} of compiledRules().rules) {
    let own = null, ownTags = null;
    for (const f of replaceFields()) {
      if (!rule.fields.includes(f.key)) continue;
      const cur = running && f.key in running ? running[f.key] : (r[f.rk] || "").trim();
      if (!cur) continue;
      const res = replaceInValue(cur, terms, rule.replace || "", ruleOpts(rule));
      const nv = res.value.trim();
      if (!res.hits.length || !nv || nv === cur) continue;
      (running = running || {})[f.key] = nv;
      (own = own || {})[f.key] = nv;
    }
    if (rule.fields.includes(REPLACE_TAGS_KEY)) {
      const base = runTags || (USER_TAGS.get(r.p) || []).map(tag => ({orig: tag, cur: tag}));
      for (const e of base) {
        const res = replaceInValue(e.cur, terms, rule.replace || "", ruleOpts(rule));
        const nv = res.value.trim().replace(/\s+/g, " ");
        if (!res.hits.length || !nv || nv === e.cur) continue;
        e.cur = nv;
        (ownTags = ownTags || []).push({orig: e.orig, new: nv});
      }
      if (ownTags) runTags = base;
    }
    if (own || ownTags) {
      issues.push({code: "rule:" + rule.id, field: own ? Object.keys(own)[0] : REPLACE_TAGS_KEY,
                   fields: own || {}, tagChanges: ownTags || []});
    }
  }
  const tags = runTags ? runTags.filter(e => e.cur !== e.orig).map(e => ({old: e.orig, new: e.cur})) : null;
  const ev = running || (tags && tags.length) ? {fields: running, tags: tags && tags.length ? tags : null, issues} : none;
  RULE_EVAL_CACHE.set(r, {sig, ev});
  return ev;
}
function ruleFieldsFor(r) { return ruleEval(r).fields; }
function ruleIssuesOf(r) { return ruleEval(r).issues; }
// Felder fuer den Quick Fix: nur Regeln, deren Meldung an diesem Track nicht ignoriert ist.
function ruleFixFields(r) {
  let out = null;
  for (const i of ruleEval(r).issues) {
    if (issueIgnored(r, i.code)) continue;
    if (Object.keys(i.fields).length) out = Object.assign(out || {}, i.fields);
  }
  return out;
}
// Eigene-Tags-Aenderungen derselben Regeln: [{old, new}] (je urspruenglichem Tag der letzte Stand).
function ruleFixTags(r) {
  const byOrig = new Map();
  for (const i of ruleEval(r).issues) {
    if (issueIgnored(r, i.code)) continue;
    for (const c of i.tagChanges) byOrig.set(c.orig, c.new);
  }
  return byOrig.size ? [...byOrig].map(([old, nv]) => ({old, new: nv})) : null;
}
function ruleFixCodes(r) { return ruleEval(r).issues.filter(i => !issueIgnored(r, i.code)).map(i => i.code); }
// Tag-Aenderungen eines Tracks schreiben (nur Datenbank, nichts geht an Datei, Music.app oder Rekordbox).
async function applyRuleTags(r, changes) {
  await saveUserTags({paths: [r.p], add: changes.map(c => c.new), remove: changes.map(c => c.old)});
}
function resetRuleIssueCache() { RULE_EVAL_CACHE = new WeakMap(); }

// Vorschlag des Tags-Dialogs: Regeln auf den Server-Vorschlag bzw. den
// aktuellen Feldinhalt. -> {value, rules} oder null.
function tagsRuleSuggestion(field, base, row) {
  if (!compiledRules().rules.length || !base) return null;
  const res = applyRulesToValue(base, field, row);
  return res.rules.length ? res : null;
}

function adoptReplaceRules(rules) {
  REPLACE_RULES = Array.isArray(rules) ? rules.map(normalizeRule) : [];
  RULE_COMPILED = null;
  resetRuleIssueCache();
}
async function postReplaceRules(rules) {
  const res = await fetch("/api/replace-rules", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({rules})});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  adoptReplaceRules(data.replace_rules);
  // Geloeschte bzw. in Suchbegriff/Ersatz geaenderte Regeln: der Server hat deren
  // Ignorierungen entfernt (settings.stale_rule_codes()), hier dasselbe nachziehen.
  const cleared = new Set(Array.isArray(data.ignored_cleared) ? data.ignored_cleared : []);
  if (cleared.size) {
    for (const [p, set] of [...IGN_ISSUES]) {
      for (const c of cleared) set.delete(c);
      if (!set.size) IGN_ISSUES.delete(p);
    }
    albumOpenCache = null;
    if (state.tagIssueFilter === ISSUE_FILTER_IGNORED && !DATA.some(r => !r.removed && issueFilterHit(r, ISSUE_FILTER_IGNORED)))
      state.tagIssueFilter = null;
  }
  if (state.tagIssueFilter && isRuleCode(state.tagIssueFilter)) state.tagIssueFilter = null;
  if (typeof updateCards === "function") updateCards();
  render();
  if (typeof updateDropHeadButtons === "function") updateDropHeadButtons();
  return REPLACE_RULES;
}

// ── Chips (Felder, Optionen) -- Dialog und Einstellungen ─────────────────
function rpChipHtml(attrs, label, pressed, tip, short) {
  return `<button type="button" class="rpchip${pressed ? " on" : ""}" ${attrs} aria-pressed="${pressed}"` +
    `${tip ? ` title="${esc(tip)}"` : ""}>${short ? `<span class="rpchipk">${esc(short)}</span>` : ""}${esc(label)}</button>`;
}
// kind: Attributpraefix (rpf/rpo im Dialog, rrf/rro in den Einstellungen).
function fieldChipsHtml(kind, selected) {
  const keys = replaceFieldKeys();
  const all = keys.every(k => selected.includes(k));
  return rpChipHtml(`data-${kind}-all="1"`, t("replace.field_all"), all, t("replace.field_all_tip")) +
    `<span class="rpchipsep"></span>` +
    keys.map(k => rpChipHtml(`data-${kind}="${k}"`, replaceFieldLabel(k), selected.includes(k))).join("");
}
function optionChipsHtml(kind, values) {
  return replaceOptionDefs().map(o => rpChipHtml(`data-${kind}="${o.key}"`, o.label, !!values[o.key], o.tip, o.short)).join("");
}
// Auswahl nach einem Klick auf einen Feld-Chip: -> neue Liste (Reihenfolge der Felder).
function toggleFieldSelection(selected, el, kind) {
  const keys = replaceFieldKeys();
  if (el.hasAttribute(`data-${kind}-all`)) return keys.every(k => selected.includes(k)) ? [] : keys.slice();
  const key = el.getAttribute(`data-${kind}`);
  const set = new Set(selected);
  if (set.has(key)) set.delete(key); else set.add(key);
  return keys.filter(k => set.has(k));
}

// ── Abgleich nach dem Schreiben (Music.app + Rekordbox) ──────────────────
// Quick Fix einer Regel schreibt ueber /api/tags; Music.app laeuft dort ueber
// noteMusicTagQueued(), Rekordbox holt dieser gebuendelte Aufruf nach.
function noteTagSync(path) {
  if (!apiMode) return;
  if (!TAG_SYNC_QUEUE) TAG_SYNC_QUEUE = new Set();
  TAG_SYNC_QUEUE.add(path);
  clearTimeout(tagSyncTimer);
  tagSyncTimer = setTimeout(() => {
    const paths = [...TAG_SYNC_QUEUE];
    TAG_SYNC_QUEUE.clear();
    runTagSync(paths, true);
  }, 1500);
}
async function runTagSync(paths, quiet) {
  if (!paths.length) return null;
  try {
    const res = await fetch("/api/tag-replace-finish", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths, session: `tag-replace-${Date.now()}`})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    const m = data.music;
    if (m) {
      if (applyMovedPaths(m.relinked)) { clearUndoStacks(); updateCards(); render(); }
      if (m.error) note(t("toast.music_tags_failed", {error: m.error}), "soft");
      else if (m.missing && m.missing.length) note(t("toast.music_tags_missing", {count: m.missing.length}), "soft");
    }
    const rb = data.rekordbox;
    if (rb) {
      if (rb.error) note(t("replace.rb_failed", {error: rb.error}), "soft");
      else if (rb.queued) noteRekordboxQueued({running: true, queued: rb.queued, pending: rb.pending}, quiet);
    }
    return data;
  } catch (err) {
    note(t("replace.sync_failed", {error: err.message}), "soft");
    return null;
  }
}

// ── Dialog ───────────────────────────────────────────────────────────────
function openReplaceDialog() {
  if (!apiMode) return;
  const rows = exportRows().filter(r => !r.ext && !r.removed && r.p);
  RP = {mode: "search", rows, isDrop: false, groups: [], unchecked: new Set(), blocked: {}, skippedIgnored: 0,
        fields: RP_LAST ? RP_LAST.fields.slice() : replaceFieldKeys(),
        options: RP_LAST ? {...RP_LAST.options} : {case: false, accents: false}};
  showReplaceDialog();
  fetchWriteBlockers(rows.filter(r => !r.gone).map(r => r.p)).then(b => {
    if (!RP || RP.rows !== rows) return;
    RP.blocked = b || {};
    computeReplace();
  });
}
// Einzelpruefungen: alle aktiven Regeln auf die Auswahl, danach optional umbenennen.
function openReplaceRulesForDrops(rows) {
  RP = {mode: "rules", rows, isDrop: true, groups: [], unchecked: new Set(), blocked: {}, skippedIgnored: 0,
        fields: replaceFieldKeys(), options: {case: false, accents: false}};
  showReplaceDialog();
  fetchWriteBlockers(rows.map(r => r.p)).then(b => {
    if (!RP || RP.rows !== rows) return;
    RP.blocked = b || {};
    computeReplace();
  });
}

function renderReplaceChips() {
  document.getElementById("replaceFieldChips").innerHTML = fieldChipsHtml("rpf", RP.fields);
  document.getElementById("replaceOptionChips").innerHTML = optionChipsHtml("rpo", RP.options);
}
function renderReplaceScope() {
  const rules = RP.mode === "rules";
  const n = RP.rows.length.toLocaleString("de-DE");
  let text = RP.mode === "fix"
    ? (RP.codes && RP.codes.size === 1 ? t("fixprev.scope_code", {count: n, issue: issueLabel([...RP.codes][0])})
                                       : t("fixprev.scope", {count: n}))
    : rules ? t("replace.scope_drops", {count: n})
    : t(state.selected.size ? "replace.scope_selection" : "replace.scope_list", {count: n});
  if (RP.skippedRows) text += " · " + t("fixprev.scope_skipped", {count: RP.skippedRows.toLocaleString("de-DE")});
  if (RP.skippedIgnored) text += " · " + t("replace.scope_ignored", {count: RP.skippedIgnored.toLocaleString("de-DE")});
  document.getElementById("replaceScope").textContent = text;
}
function showReplaceDialog() {
  const ov = document.getElementById("replaceOverlay");
  const rules = RP.mode === "rules", fix = RP.mode === "fix";
  document.getElementById("replaceTitleIcon").innerHTML = fix ? ICONS.sparkles : ICONS.replaceAll;
  document.getElementById("replaceTitle").textContent = t(fix ? "fixprev.title" : rules ? "replace.title_rules" : "replace.title");
  document.getElementById("replaceInputs").hidden = RP.mode !== "search";
  document.getElementById("replaceRuleTools").hidden = RP.mode !== "search";
  document.getElementById("replaceRenameRow").hidden = !rules;
  // Syntaxhilfe: "?" hinter dem Label "Suchen", der Text kommt als Tooltip (wie die ? in den Einstellungen).
  const help = document.getElementById("replaceSyntaxHelp");
  help.innerHTML = ICONS.helpQ;
  help.title = t("replace.syntax");
  help.setAttribute("aria-label", t("replace.syntax_toggle"));
  document.querySelector("#replaceSaveRule .btnicon").innerHTML = SVG(LUCIDE_ICONS["bookmark-plus"]);
  renderReplaceChips();
  renderRuleLoadSelect();
  renderReplaceScope();
  for (const id of ["replaceFind", "replaceWith"]) document.getElementById(id).oninput = scheduleReplaceCompute;
  document.getElementById("replaceFieldChips").onclick = e => {
    const el = e.target.closest(".rpchip");
    if (!el) return;
    RP.fields = toggleFieldSelection(RP.fields, el, "rpf");
    rememberReplaceChoice();
    renderReplaceChips(); computeReplace();
  };
  document.getElementById("replaceOptionChips").onclick = e => {
    const el = e.target.closest(".rpchip");
    if (!el) return;
    const key = el.dataset.rpo;
    RP.options[key] = !RP.options[key];
    rememberReplaceChoice();
    renderReplaceChips(); computeReplace();
  };
  document.getElementById("replaceAll").onclick = () => { RP.unchecked.clear(); renderReplaceList(); };
  document.getElementById("replaceNone").onclick = () => {
    for (const g of RP.groups) for (const it of g.items) if (it.status === "ok") RP.unchecked.add(it.key);
    renderReplaceList();
  };
  document.getElementById("replaceList").onclick = onReplaceListClick;
  document.getElementById("replaceSaveRule").onclick = saveRuleFromDialog;
  document.getElementById("replaceLoadRule").onchange = e => {
    const rule = replaceRules().find(r => r.id === e.target.value);
    e.target.value = "";
    if (!rule) return;
    document.getElementById("replaceFind").value = rule.terms;
    document.getElementById("replaceWith").value = rule.replace || "";
    RP.fields = rule.fields.slice();
    RP.options = {case: !!rule.case, accents: !!rule.accents};
    rememberReplaceChoice();
    renderReplaceChips(); computeReplace();
  };
  document.getElementById("replaceCancel").onclick = closeReplaceDialog;
  document.getElementById("replaceGo").onclick = runReplace;
  ov.style.display = "flex";
  document.onkeydown = replaceKeys;
  computeReplace();
  if (RP.mode === "search") setTimeout(() => document.getElementById("replaceFind").focus(), 30);
}
function rememberReplaceChoice() {
  if (RP && RP.mode === "search") RP_LAST = {fields: RP.fields.slice(), options: {...RP.options}};
}
function replaceKeys(e) { if (e.key === "Escape") closeReplaceDialog(); }
function closeReplaceDialog() {
  document.getElementById("replaceOverlay").style.display = "none";
  document.getElementById("replaceList").innerHTML = "";
  document.onkeydown = null;
  RP = null;
}
function renderRuleLoadSelect() {
  const sel = document.getElementById("replaceLoadRule");
  const rules = replaceRules();
  sel.innerHTML = `<option value="">${esc(t("replace.load_rule"))}</option>` +
    rules.map(r => `<option value="${esc(r.id)}">${esc(ruleLabel(r))}</option>`).join("");
  sel.disabled = !rules.length;
}

function scheduleReplaceCompute() {
  clearTimeout(RP && RP.timer);
  if (RP) RP.timer = setTimeout(computeReplace, 150);
}
function dialogSearchSpec() {
  return {
    terms: parseReplaceTerms(document.getElementById("replaceFind").value),
    text: document.getElementById("replaceFind").value.trim(),
    repl: document.getElementById("replaceWith").value,
    fields: RP.fields,
    opts: {caseSensitive: !!RP.options.case, accentSensitive: !!RP.options.accents},
  };
}
// Entspricht die Suche im Dialog einer gespeicherten Regel (Suchen, Ersetzen, Felder, Optionen),
// gilt deren Ignorieren: Tracks, an denen die Regel unter Auffaelligkeiten ignoriert ist, bleiben aussen vor.
function matchingSavedRule(spec) {
  if (!spec.text) return null;
  return replaceRules().find(r => r.terms.trim() === spec.text && (r.replace || "") === spec.repl &&
    r.fields.join() === replaceFieldKeys().filter(k => spec.fields.includes(k)).join() &&
    !!r.case === !!RP.options.case && !!r.accents === !!RP.options.accents) || null;
}
// Je Track eine Gruppe mit einem Eintrag je geaendertem Feld (bei eigenen Tags je geaendertem Tag):
// {key, row, status, items: [{key, field, old, new, hits, out, status}]}.
// status: ok | gone | locked (Gruppe) bzw. ok | empty (Eintrag: Feld waere leer).
function computeReplace() {
  if (!RP) return;
  if (RP.mode === "fix") { computeQuickFix(); return; }
  const groups = [];
  const fields = replaceFields();
  const spec = RP.mode === "search" ? dialogSearchSpec() : null;
  const saved = spec ? matchingSavedRule(spec) : null;
  let skipped = 0;
  RP.rows.forEach((r, idx) => {
    if (saved && issueIgnored(r, "rule:" + saved.id)) { skipped++; return; }
    const items = [];
    for (const f of fields) {
      if (spec && !spec.fields.includes(f.key)) continue;
      const old = r[f.rk] || "";
      if (!old) continue;
      let nv, hits = null, out = null;
      if (spec) {
        if (!spec.terms.length) continue;
        const res = replaceInValue(old, spec.terms, spec.repl, spec.opts);
        if (!res.hits.length) continue;
        nv = res.value.trim();
        if (nv === old) continue;
        hits = res.hits; out = res.value === nv ? res.out : null;
      } else {
        const res = applyRulesToValue(old, f.key, r);
        if (!res.rules.length) continue;
        nv = res.value;
      }
      items.push({key: `${idx}|${f.key}`, field: f.key, old, new: nv, hits, out,
                  status: nv ? "ok" : "empty"});
    }
    // Eigene Tags: je geaendertem Tag ein Eintrag (nur Suche; Einzelpruefungen haben keine).
    if (spec && spec.terms.length && spec.fields.includes(REPLACE_TAGS_KEY)) {
      (USER_TAGS.get(r.p) || []).forEach((tag, n) => {
        const res = replaceInValue(tag, spec.terms, spec.repl, spec.opts);
        if (!res.hits.length) return;
        const nv = res.value.trim().replace(/\s+/g, " ");
        if (nv === tag) return;
        items.push({key: `${idx}|${REPLACE_TAGS_KEY}|${n}`, field: REPLACE_TAGS_KEY, old: tag, new: nv,
                    hits: res.hits, out: res.value === nv ? res.out : null, status: nv ? "ok" : "empty"});
      });
    }
    if (!items.length) return;
    const status = r.gone ? "gone" : RP.blocked[r.p] ? "locked" : "ok";
    groups.push({key: String(idx), row: r, status, items});
  });
  RP.groups = groups;
  RP.skippedIgnored = skipped;
  renderReplaceScope();
  renderReplaceList();
}
// Markiert [s, e)-Bereiche in 'text' (HTML-escaped). Ein leerer Bereich
// (Treffer wurde entfernt) zeigt eine Schnittmarke.
function markRanges(text, ranges, cls) {
  if (!ranges || !ranges.length) return esc(text);
  let html = "", pos = 0;
  for (const [s, e] of ranges) {
    html += esc(text.slice(pos, s));
    html += s === e ? `<mark class="${cls} rpcut" title="${esc(t("replace.removed_tip"))}">∅</mark>`
                    : `<mark class="${cls}">${esc(text.slice(s, e))}</mark>`;
    pos = e;
  }
  return html + esc(text.slice(pos));
}
// Ohne genaue Trefferstellen (Regel-Modus): gemeinsamen Anfang/Ende abziehen.
function diffRanges(a, b) {
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let q = 0;
  while (q < a.length - p && q < b.length - p && a[a.length - 1 - q] === b[b.length - 1 - q]) q++;
  return [[[p, a.length - q]], [[p, b.length - q]]];
}
function rpTrackTitle(r) {
  const title = r.t || baseName(r.p);
  return `<b>${esc(title)}</b>${r.a && r.t ? `<span class="rpartist"> – ${esc(r.a)}</span>` : ""}`;
}
// Haken wie in der Duplikate-Liste: Kreis mit Haken (gewaehlt), leerer Kreis, Kreis mit Strich (gemischt).
function rpCheckHtml(attr, state) {
  return `<button type="button" class="rpcheck${state === "on" ? " on" : state === "mixed" ? " mixed" : ""}" ${attr}` +
    ` aria-pressed="${state === "mixed" ? "mixed" : state === "on"}">${
      state === "on" ? ICONS.dupKeep : state === "mixed" ? ICONS.checkMixed : ICONS.dupKeepOff}</button>`;
}
function rpSetCheck(btn, state) {
  btn.classList.toggle("on", state === "on");
  btn.classList.toggle("mixed", state === "mixed");
  btn.setAttribute("aria-pressed", state === "mixed" ? "mixed" : String(state === "on"));
  btn.innerHTML = state === "on" ? ICONS.dupKeep : state === "mixed" ? ICONS.checkMixed : ICONS.dupKeepOff;
}
// Gerendert werden alle Tracks (Pruefliste: nichts darf unsichtbar mit uebernommen werden); .rpgroup nutzt content-visibility.
function renderReplaceList() {
  const list = document.getElementById("replaceList");
  const shown = RP.groups;
  list.innerHTML = shown.length ? shown.map(g => {
    const ok = g.status === "ok";
    const head = ok ? rpCheckHtml(`data-rpgroup="${g.key}"`, "on")
      : `<span class="rpicon">${g.status === "locked" ? ICONS.lock : ICONS.fileMissing}</span>`;
    const state = ok ? "" : `<span class="rpstate">${esc(t(g.status === "gone" ? "replace.state_gone" : "replace.state_locked"))}</span>`;
    const changes = g.items.map(it => {
      const [dOld, dNew] = it.plain ? [null, null] : it.hits ? [it.hits, it.out] : diffRanges(it.old, it.new);
      const after = it.status === "empty" ? `<span class="rpstate">${esc(t("replace.state_empty"))}</span>`
        : it.plain ? `<span class="rpnote">${esc(it.new)}</span>`
        : markRanges(it.new, dNew || diffRanges(it.old, it.new)[1], "rpnew");
      const live = ok && it.status === "ok";
      const tip = it.codes ? ` title="${esc(it.codes.map(issueLabel).join(" · "))}"` : "";
      return `<div class="rpchange${live ? "" : " off"}" ${live ? `data-rprow="${esc(it.key)}"` : ""}>
        ${live ? rpCheckHtml(`data-rpkey="${esc(it.key)}"`, RP.unchecked.has(it.key) ? "off" : "on") : `<span></span>`}
        <span class="rpfield"${tip}>${esc(it.label || replaceFieldLabel(it.field))}</span>
        <span class="rpold">${it.plain ? `<span class="rpnote">${esc(it.old)}</span>` : markRanges(it.old, dOld, "rpold")}</span>
        <span class="rparrow">→</span>
        <span class="rpnewcell">${after}</span>
      </div>`;
    }).join("");
    return `<div class="rpgroup${ok ? "" : " off"}">
      <div class="rphead" ${ok ? `data-rphead="${g.key}"` : ""}>${head}<span class="rptitle" title="${esc(g.row.p)}">${rpTrackTitle(g.row)}</span>${state}
        <span class="rpbadge">${esc(t("replace.n_changes", {count: g.items.length}))}</span></div>
      <div class="rpchanges">${changes}</div>
    </div>`;
  }).join("")
    : `<div class="rpempty">${esc(t(RP.mode === "fix" ? (RP.plans ? "fixprev.empty" : "fixprev.loading")
        : RP.mode === "rules" ? "replace.empty_rules"
        : !RP.fields.length ? "replace.empty_fields"
        : dialogSearchSpec().terms.length ? "replace.empty" : "replace.empty_start"))}</div>`;
  syncReplaceGroupBoxes();
  renderReplaceSummary();
}
// Kopf-Haken je Track: an, aus oder gemischt, nach den Haken der Aenderungen.
function syncReplaceGroupBoxes() {
  for (const g of RP.groups) {
    const box = document.querySelector(`#replaceList [data-rpgroup="${g.key}"]`);
    if (!box) continue;
    const live = g.items.filter(it => it.status === "ok");
    const on = live.filter(it => !RP.unchecked.has(it.key)).length;
    rpSetCheck(box, on === 0 ? "off" : on < live.length ? "mixed" : "on");
  }
}
// Klick auf Kopf bzw. Zeile (nicht nur auf den Kreis) schaltet um.
function onReplaceListClick(e) {
  const head = e.target.closest("[data-rphead]");
  if (head) {
    const g = RP.groups.find(x => x.key === head.dataset.rphead);
    const live = g.items.filter(it => it.status === "ok");
    const allOn = live.every(it => !RP.unchecked.has(it.key));
    for (const it of live) { if (allOn) RP.unchecked.add(it.key); else RP.unchecked.delete(it.key); }
    document.querySelectorAll(`#replaceList [data-rpkey^="${g.key}|"]`).forEach(b => rpSetCheck(b, allOn ? "off" : "on"));
  } else {
    const row = e.target.closest("[data-rprow]");
    if (!row) return;
    const key = row.dataset.rprow;
    const nowOn = RP.unchecked.has(key);
    if (nowOn) RP.unchecked.delete(key); else RP.unchecked.add(key);
    rpSetCheck(row.querySelector("[data-rpkey]"), nowOn ? "on" : "off");
  }
  syncReplaceGroupBoxes();
  renderReplaceSummary();
}
function replaceItems() {
  const out = [];
  for (const g of RP.groups) if (g.status === "ok") for (const it of g.items) if (it.status === "ok") out.push({...it, row: g.row});
  return out;
}
function selectedReplaceItems() {
  return RP ? replaceItems().filter(it => !RP.unchecked.has(it.key)) : [];
}
function renderReplaceSummary() {
  const sel = selectedReplaceItems();
  const tracks = new Set(sel.map(it => it.row)).size;
  const all = replaceItems();
  document.getElementById("replaceCount").textContent = t("replace.count", {
    changes: all.length.toLocaleString("de-DE"),
    tracks: new Set(all.map(it => it.row)).size.toLocaleString("de-DE"),
  });
  const go = document.getElementById("replaceGo");
  go.disabled = !sel.length;
  go.textContent = t(RP.mode === "fix" ? "fixprev.go" : RP.mode === "rules" ? "replace.go_rules" : "replace.go",
    {count: tracks.toLocaleString("de-DE")});
}

async function saveRuleFromDialog() {
  const spec = dialogSearchSpec();
  const terms = document.getElementById("replaceFind").value.trim();
  if (!terms) { note(t("replace.rule_needs_terms"), "soft"); return; }
  if (!RP.fields.length) { note(t("replace.empty_fields"), "soft"); return; }
  const rule = {id: "", on: true, terms, fields: replaceFieldKeys().filter(k => RP.fields.includes(k)), replace: spec.repl,
                case: !!RP.options.case, accents: !!RP.options.accents};
  const same = matchingSavedRule(spec);
  if (same) { note(t("replace.rule_exists"), "soft"); return; }
  try {
    await postReplaceRules([...replaceRules(), rule]);
    renderRuleLoadSelect();
    note(t("replace.rule_saved"));
  } catch (err) {
    note(t("error.generic", {error: err.message}), true);
  }
}

// Gruppiert die gewaehlten Eintraege je Zeile: [{row, fields: {feld: {old, new}}, tags: [{old, new}]}].
function replaceGroups(items) {
  const byRow = new Map();
  for (const it of items) {
    if (!byRow.has(it.row)) byRow.set(it.row, {row: it.row, fields: {}, tags: []});
    const g = byRow.get(it.row);
    if (it.field === REPLACE_TAGS_KEY) g.tags.push({old: it.old, new: it.new});
    else g.fields[it.field] = {old: it.old, new: it.new};
  }
  return [...byRow.values()];
}

async function runReplace() {
  if (RP && RP.mode === "fix") { await runQuickFixPreview(); return; }
  const items = selectedReplaceItems();
  if (!items.length) return;
  const groups = replaceGroups(items);
  const fileGroups = groups.filter(g => Object.keys(g.fields).length);
  const isDrop = RP.isDrop;
  const rename = isDrop && document.getElementById("replaceRename").checked;
  // Rueckfrage (Nutzervorgabe: kein Rueckgaengig, lieber vorher fragen).
  let rbLine = "";
  if (!isDrop && REKORDBOX_NAME && fileGroups.some(g => g.row.rb)) {
    let running = false;
    try {
      const st = await (await fetch("/api/rekordbox-status")).json();
      running = !!(st.running || st.agent);
    } catch { /* Status unbekannt: Server entscheidet beim Schreiben */ }
    rbLine = t(running ? "replace.confirm_rb_queue" : "replace.confirm_rb_write");
  }
  const musicLine = !isDrop && MUSIC_NAME && fileGroups.some(g => g.row.im) ? t("replace.confirm_music") : "";
  const tagsLine = groups.some(g => g.tags.length) ? t("replace.confirm_tags") : "";
  const fields = [...new Set(items.map(it => replaceFieldLabel(it.field)))].join(", ");
  const noteText = [t(isDrop ? "replace.confirm_note_drops" : "replace.confirm_note",
    {count: groups.length.toLocaleString("de-DE"), fields}), tagsLine, musicLine, rbLine,
    rename ? t("replace.confirm_rename") : "", t("replace.confirm_final")].filter(Boolean).join(" ");
  const yes = await askConfirmSimple(t("replace.confirm_title"), noteText,
    t(isDrop ? "replace.go_rules" : "replace.go", {count: groups.length.toLocaleString("de-DE")}));
  if (!RP) return;
  document.onkeydown = replaceKeys;
  if (!yes) return;
  const goBtn = document.getElementById("replaceGo");
  goBtn.disabled = true;
  const rows = RP.rows;
  closeReplaceDialog();
  if (isDrop) await runReplaceDrops(groups, rows, rename);
  else await runReplaceLibrary(groups);
}

async function runReplaceLibrary(groups) {
  const prog = progressToast(t("replace.progress", {done: 0, total: groups.length}));
  const written = [];
  let failed = 0, skipped = 0, firstError = "", tagCount = 0;
  const CHUNK = 25;
  for (let i = 0; i < groups.length; i += CHUNK) {
    const part = groups.slice(i, i + CHUNK).filter(g => Object.keys(g.fields).length);
    part.forEach(g => stopPlayerFor(g.row.p));
    if (part.length) try {
      const res = await fetch("/api/tag-replace", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({items: part.map(g => ({path: g.row.p, fields: g.fields}))})});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      const byPath = new Map(part.map(g => [g.row.p, g.row]));
      for (const u of data.updated) {
        const r = byPath.get(u.path);
        if (r && u.row) { applyRowUpdate(r.i, u.row); dropScopeCache(DATA[r.i]); }
        written.push(u.path);
      }
      failed += Object.keys(data.failed || {}).length;
      skipped += Object.values(data.skipped || {}).filter(s => s === "changed").length;
      if (!firstError) firstError = Object.values(data.failed || {})[0] || "";
    } catch (err) {
      failed += part.length;
      if (!firstError) firstError = err.message;
    }
    prog.update(Math.round(100 * Math.min(groups.length, i + CHUNK) / groups.length),
      t("replace.progress", {done: Math.min(groups.length, i + CHUNK), total: groups.length}));
  }
  // Eigene Tags: nur Datenbank, je gleichem Paar alt -> neu ein Aufruf fuer alle Tracks.
  const pairs = new Map();
  for (const g of groups) for (const c of g.tags) {
    const k = c.old + "\u0000" + c.new;
    if (!pairs.has(k)) pairs.set(k, {...c, paths: []});
    pairs.get(k).paths.push(g.row.p);
  }
  for (const p of pairs.values()) {
    try { await saveUserTags({paths: p.paths, add: [p.new], remove: [p.old]}); tagCount += p.paths.length; }
    catch (err) { failed += p.paths.length; if (!firstError) firstError = err.message; }
  }
  invalidateFieldValueCache();
  updateCards(); render();
  const parts = [];
  if (written.length || !tagCount) parts.push(t("replace.done", {count: written.length}));
  if (tagCount) parts.push(t("replace.done_tags", {count: tagCount}));
  if (skipped) parts.push(t("replace.done_changed", {count: skipped}));
  if (failed) parts.push(t("replace.done_failed", {count: failed, error: firstError}));
  prog.done(parts.join(" "), failed ? "soft" : false);
  if (written.length) await runTagSync(written, false);
}

async function runReplaceDrops(groups, rows, rename) {
  let done = 0, failed = 0, firstError = "";
  const queue = groups.filter(g => Object.keys(g.fields).length);
  const worker = async () => {
    while (queue.length) {
      const g = queue.shift();
      const fields = Object.fromEntries(Object.entries(g.fields).map(([k, v]) => [k, v.new]));
      try {
        stopPlayerFor(g.row.p);
        const res = await fetch("/api/tags", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: g.row.p, ...fields})});
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || t("error.unknown"));
        Object.assign(g.row, fieldsToDropRow(fields));
        dropScopeCache(g.row);
        done++;
      } catch (err) {
        failed++;
        if (!firstError) firstError = err.message;
      }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  renderDrops();
  let msg = t("replace.done", {count: done});
  if (failed) msg += " " + t("replace.done_failed", {count: failed, error: firstError});
  note(msg, failed ? "soft" : false);
  if (rename) await renameDrops(rows.filter(r => !r.gone), document.getElementById("dropRules"));
}

// ── Quick-Fix-Vorschau ────────────────────────────────────────────────────
// Derselbe Dialog wie Suchen & Ersetzen (Modus "fix"): je Track jede Aenderung
// vorher/nachher, einzeln abwaehlbar. Gerechnet wird mit quickFixPlan()
// (app.js), der Datei-Teil kommt als Trockenlauf aus /api/fix-tag-issues-preview
// (taganomaly.plan_safe()) -- dieselbe Rechnung wie beim Schreiben. Die
// Vorschau ersetzt die fruehere Rueckfrage, geschrieben wird erst mit dem Knopf.
// opts: codes (Set, nur diese Auffaelligkeiten), isDrop, skipped (Zeilen ohne
// Quick Fix, nur Anzeige), onDone (nach dem Schreiben).
function openQuickFixPreview(rows, opts = {}) {
  if (!apiMode) return;
  RP = {mode: "fix", rows, isDrop: !!opts.isDrop, codes: opts.codes || null, onDone: opts.onDone || null,
        groups: [], unchecked: new Set(), blocked: {}, skippedIgnored: 0, skippedRows: opts.skipped || 0,
        plans: null, fields: [], options: {}};
  const token = RP;
  showReplaceDialog();
  Promise.all([fetchWriteBlockers(rows.filter(r => !r.gone).map(r => r.p)), fetchQuickFixPlans(rows, token)])
    .then(([blocked, plans]) => {
      if (RP !== token) return;
      RP.blocked = blocked || {};
      RP.plans = plans;
      computeReplace();
    });
}
// Trockenlauf des Datei-Teils, je Track mit dessen erlaubten Codes, in Abschnitten
// (der Server liest je Datei die Tags, bei Covern auch das Bild).
async function fetchQuickFixPlans(rows, token) {
  const items = [];
  for (const r of rows) {
    if (r.gone) continue;
    const codes = quickFixServerCodes(r, quickFixAllowed(r, token.codes), new Set());
    if (codes.length) items.push({path: r.p, codes});
  }
  const plans = {};
  const CHUNK = 100;
  for (let i = 0; i < items.length; i += CHUNK) {
    if (RP !== token) break;
    try {
      const res = await fetch("/api/fix-tag-issues-preview", {method: "POST",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify({items: items.slice(i, i + CHUNK)})});
      const data = await res.json();
      if (data.ok) Object.assign(plans, data.plans);
    } catch { /* ohne Vorschau des Datei-Teils: der Track zeigt nur, was der Client selbst rechnet */ }
    if (RP === token && items.length > CHUNK) {
      document.getElementById("replaceCount").textContent = t("fixprev.loading_n",
        {done: Math.min(items.length, i + CHUNK).toLocaleString("de-DE"), total: items.length.toLocaleString("de-DE")});
    }
  }
  return plans;
}
// Text fuer Eintraege ohne Vorher/Nachher-Wert (ID3-Version, Cover, doppelte Frames).
function quickFixNote(field, codes) {
  const labels = [...codes].map(issueLabel).join(" · ");
  if (field === "id3") return {old: labels, new: t("fixprev.do_id3"), label: t("fixprev.field_id3")};
  if (field === "cover") return {old: labels, new: t([...codes].some(c => ALBUM_COVER_CODES.has(c))
    ? "fixprev.do_cover_album" : "fixprev.do_cover_resize")};
  return {old: labels, new: t("fixprev.do_frames")};
}
function computeQuickFix() {
  const groups = [];
  if (RP.plans) RP.rows.forEach((r, idx) => {
    const plan = quickFixPlan(r, RP.isDrop, {codes: RP.codes}, RP.plans[r.p] || null);
    const items = [];
    for (const [field, c] of plan.changes) {
      const n = c.old === null || c.old === c.new ? quickFixNote(field, c.codes)
        // Feld wird geleert (z. B. ein Titel nur aus Leerzeichen): ausdruecklich anzeigen.
        : !c.new ? {old: c.old, new: t("fixprev.empty_value")} : null;
      items.push({key: `${idx}|${field}`, field, plain: !!n, codes: [...c.codes], status: "ok",
                  old: n ? n.old : c.old, new: n ? n.new : c.new, label: n && n.label});
    }
    (plan.tags || []).forEach((tg, n) => items.push({key: `${idx}|${REPLACE_TAGS_KEY}|${n}`, field: REPLACE_TAGS_KEY,
      old: tg.old, new: tg.new, codes: tg.codes, status: "ok"}));
    if (!items.length) return;
    const status = r.gone ? "gone" : RP.blocked[r.p] ? "locked" : "ok";
    groups.push({key: String(idx), row: r, status, items});
  });
  RP.groups = groups;
  renderReplaceScope();
  renderReplaceList();
}
async function runQuickFixPreview() {
  const sel = new Set(selectedReplaceItems().map(it => it.key));
  const jobs = [];
  for (const g of RP.groups) {
    if (g.status !== "ok") continue;
    const skip = new Set();
    let any = false;
    for (const it of g.items) {
      if (it.status === "ok" && sel.has(it.key)) { any = true; continue; }
      skip.add(it.field === REPLACE_TAGS_KEY ? REPLACE_TAGS_KEY + ":" + it.old : it.field);
    }
    if (any) jobs.push({row: g.row, skip});
  }
  if (!jobs.length) return;
  const {isDrop, codes, onDone} = RP;
  closeReplaceDialog();
  const prog = progressToast(t("fixprev.progress", {done: 0, total: jobs.length}));
  let done = 0, failed = 0, finished = 0, firstError = "";
  const queue = jobs.slice();
  const worker = async () => {
    while (queue.length) {
      const j = queue.shift();
      try { await fixTagIssuesOne(j.row, isDrop, {codes, skip: j.skip}); done++; }
      catch (err) { failed++; if (!firstError) firstError = `${baseName(j.row.p)}: ${err.message}`; }
      finished++;
      prog.update(Math.round(100 * finished / jobs.length), t("fixprev.progress", {done: finished, total: jobs.length}));
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  if (onDone) onDone();
  if (isDrop) renderDrops(); else { updateCards(); render(); }
  let msg = t("fixprev.done", {count: done});
  if (failed) msg += " " + t("replace.done_failed", {count: failed, error: firstError});
  prog.done(msg, failed ? "soft" : false);
}

// ── Einstellungen: Block "Ersetzungsregeln" ──────────────────────────────
// Je Regel eine Karte: Kopfzeile (aktiv, Suchen → Ersetzen durch, Loeschen),
// darunter die Felder und die Optionen als Chips (wie im Dialog).
function renderReplaceRulesBlock() {
  rrDraft = (schema.replace_rules || replaceRules()).map(normalizeRule);
  return `<div class="setgroup" data-sg="replacerules">
    <div class="sethead"><div class="sethead-top"><b>${esc(t("settings.replace_rules_title"))}</b></div></div>
    <div class="setbody">
      <div class="sethint">${esc(t("settings.replace_rules_note"))}</div>
      <div class="setpad">
        <div id="rrRows"></div>
        <div class="setactions">
          <button class="act" id="btnAddRule"><span class="btnicon">${SVG(LUCIDE_ICONS["plus"])}</span> ${esc(t("settings.rr_add"))}</button>
          <button class="act" id="btnSaveRules"><span class="btnicon">${ICONS.save}</span> ${esc(t("settings.rr_save"))}</button>
        </div>
        <div id="rrStatus"></div>
      </div>
    </div>
  </div>`;
}
function rrRowHtml(r, i) {
  return `<div class="rrcard${r.on ? "" : " off"}" data-rr="${i}">
    <div class="rrline">
      <input type="checkbox" data-rf="on"${r.on ? " checked" : ""} title="${esc(t("settings.rr_on"))}">
      <input type="text" data-rf="terms" value="${esc(r.terms || "")}" placeholder="${esc(t("replace.find_placeholder"))}">
      <span class="rrarrow">→</span>
      <input type="text" data-rf="replace" value="${esc(r.replace || "")}" placeholder="${esc(t("replace.with_placeholder"))}">
      <button class="iconbtn del" data-rf="remove" title="${esc(t("settings.rr_remove"))}">${ICONS.trash}</button>
    </div>
    <div class="rrgroup"><span class="rrlabel">${esc(t("replace.fields_label"))}</span>
      <div class="rpchips" data-rr-fields>${fieldChipsHtml("rrf", r.fields)}</div></div>
    <div class="rrgroup"><span class="rrlabel">${esc(t("replace.options_label"))}</span>
      <div class="rpchips" data-rr-options>${optionChipsHtml("rro", r)}</div></div>
  </div>`;
}
function redrawReplaceRules() {
  const host = document.getElementById("rrRows");
  if (!host) return;
  host.innerHTML = rrDraft.length ? rrDraft.map(rrRowHtml).join("")
    : `<div class="rpempty">${esc(t("settings.rr_none"))}</div>`;
  host.querySelectorAll(".rrcard").forEach(card => {
    const i = +card.dataset.rr;
    card.querySelectorAll("[data-rf]").forEach(el => {
      const f = el.dataset.rf;
      if (f === "remove") el.onclick = () => { rrDraft.splice(i, 1); redrawReplaceRules(); };
      else if (el.type === "checkbox") el.onchange = () => { rrDraft[i][f] = el.checked; card.classList.toggle("off", !el.checked); };
      else el.oninput = () => { rrDraft[i][f] = el.value; };
    });
    card.querySelector("[data-rr-fields]").onclick = e => {
      const el = e.target.closest(".rpchip");
      if (!el) return;
      rrDraft[i].fields = toggleFieldSelection(rrDraft[i].fields, el, "rrf");
      card.querySelector("[data-rr-fields]").innerHTML = fieldChipsHtml("rrf", rrDraft[i].fields);
    };
    card.querySelector("[data-rr-options]").onclick = e => {
      const el = e.target.closest(".rpchip");
      if (!el) return;
      rrDraft[i][el.dataset.rro] = !rrDraft[i][el.dataset.rro];
      card.querySelector("[data-rr-options]").innerHTML = optionChipsHtml("rro", rrDraft[i]);
    };
  });
}
function wireReplaceRulesBlock() {
  const add = document.getElementById("btnAddRule");
  if (!add) return;
  add.onclick = () => {
    rrDraft.push({id: "", on: true, terms: "", fields: replaceFieldKeys(), replace: "", case: false, accents: false});
    redrawReplaceRules();
    const inputs = document.querySelectorAll('#rrRows [data-rf="terms"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
  };
  document.getElementById("btnSaveRules").onclick = saveReplaceRulesFromSettings;
  redrawReplaceRules();
}
function replaceRulesDirty() {
  if (!rrDraft || !document.getElementById("rrRows")) return false;
  const norm = list => JSON.stringify(list.filter(r => String(r.terms || "").trim())
    .map(r => [r.on !== false, String(r.terms).trim(), r.fields.join(), r.replace || "", !!r.case, !!r.accents]));
  return norm(rrDraft) !== norm(replaceRules());
}
async function saveReplaceRulesFromSettings() {
  const st = document.getElementById("rrStatus");
  st.textContent = t("settings.rr_saving"); st.className = "";
  try {
    const rules = await postReplaceRules(rrDraft);
    schema.replace_rules = rules;
    rrDraft = rules.map(r => ({...r}));
    redrawReplaceRules();
    st.textContent = t("settings.rr_saved");
    return true;
  } catch (err) {
    st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
    return false;
  }
}
