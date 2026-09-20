const META = /*__META__*/null;
const DATA = /*__DATA__*/null;

// Verhindert, dass der Browser bei einem Neuladen von sich aus zur
// vorherigen Scrollposition in der Tabelle zurueckspringt -- ein Neuladen
// soll immer oben beginnen.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

// ── Uebersetzungen ────────────────────────────────────────────────────────
// I18N_DE/I18N_EN kommen aus app/webui/i18n/de.js bzw. en.js (von report.py
// vor app.js in denselben <script>-Block eingebettet, siehe _template()).
// Neue Sprache: eigene i18n/xx.js mit denselben Schluesseln anlegen, hier
// unter I18N eintragen -- resolveLang() findet sie dann automatisch.
const I18N = {
  de: typeof I18N_DE !== "undefined" ? I18N_DE : {},
  en: typeof I18N_EN !== "undefined" ? I18N_EN : {},
};

// pref: expliziter Wunsch aus den Einstellungen (Feld "ui_language"), "auto"
// oder unbekannt/leer faellt auf navigator.language zurueck. Ist die
// Browsersprache (noch) nicht uebersetzt, bleibt "de" die Rueckfallebene --
// bisher die einzige vorhandene Sprache.
function resolveLang(pref) {
  if (pref && pref !== "auto" && I18N[pref]) return pref;
  const nav = String(navigator.language || "de").slice(0, 2).toLowerCase();
  return I18N[nav] ? nav : "de";
}

// Vor dem Laden der Einstellungen (asynchron, siehe initStorage()) schon per
// Browsersprache gesetzt, damit die allererste render() nicht auf den
// Server warten muss. Eine explizite Wahl in den Einstellungen ueberschreibt
// LANG/STRINGS, sobald initStorage() sie kennt -- refreshI18nCache() und ein
// zweiter applyStaticI18n()-Lauf dort holen alles nach, was mit der alten
// Sprache schon gebaut war, ganz ohne "Seite neu laden".
let LANG = resolveLang(null);
let STRINGS = I18N[LANG] || {};

// Ersetzt "{key}" im Uebersetzungstext durch vars[key]. Kein Pluralisierungs-
// system -- Aufrufer reichen z.B. ein fertiges "{plural}" ("" oder "s") mit,
// wie es der Rest der Oberflaeche ohnehin per Ternary macht. Fehlt ein
// Schluessel in STRINGS, wird der Schluessel selbst angezeigt statt eines
// leeren Textes -- so faellt eine vergessene Uebersetzung sofort auf.
function t(key, vars) {
  let s = STRINGS[key] || key;
  if (vars) for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
  return s;
}

// Ueberschreibt statischen Text in index.html (Buttons, Labels, Platzhalter,
// Tooltips), der nicht ohnehin von app.js selbst geschrieben wird -- ueber
// data-i18n/-placeholder/-title-Attribute an den jeweiligen Elementen. Erst
// bei DOMContentLoaded, nicht top-level hier oben: Ueberlagerungen wie der
// Tags-/Bestaetigungs-Dialog stehen im HTML NACH diesem <script>-Block und
// existieren waehrend der Skriptausfuehrung selbst noch nicht (siehe
// Kommentar bei openTagsPopup weiter unten). Ein zweiter Aufruf nach dem
// Laden der Einstellungen (siehe initStorage()) holt eine dort explizit
// gewaehlte Sprache nach -- zu dem Zeitpunkt sind auch diese Ueberlagerungen
// laengst im DOM.
function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach(el => { el.innerHTML = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
}
document.addEventListener("DOMContentLoaded", applyStaticI18n);

// #toTopBtn steht wie die obigen Ueberlagerungen im HTML nach diesem
// <script>-Block -- Zuweisung deshalb ebenfalls erst bei DOMContentLoaded.
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("toTopBtn").onclick = () => window.scrollTo({top: 0, behavior: "smooth"});
  document.getElementById("tagsSaveIcon").innerHTML = ICONS.save;
  document.getElementById("tagsCoverDeleteIcon").innerHTML = ICONS.trash;
});

const PAGE = 150;
const INFINITE_SCROLL_CHUNK = 50;
// Slice-Index, ab dem die zuletzt in render() gebauten Zeilen aus einem
// beginLoadMore()-Aufruf stammen -- die bekommen die Klasse "fresh" (Fade-in).
// -1 = kein Nachladen, normaler Render (Sortierung, Ignorieren-Klick, ...).
let freshFrom = -1;
// Die Liste bestimmt, WELCHE Tracks überhaupt in Frage kommen; die
// Verdikt-Chips schränken innerhalb dieser Liste weiter ein.
const VIEWS = [
  {id: "ignored",   label: t("views.ignored"),   test: r => !!r.ig},
  {id: "corrected", label: t("views.corrected"), test: r => !!r.mc},
  {id: "gone",      label: t("views.missing"),   test: r => !!r.gone},
  {id: "all",       label: t("views.all"),       test: () => true},
  {id: "duplicates",label: t("views.duplicates"),
   test: r => !!r.dg && !merkPlaylists().some(n => PLAYLIST_SETS[n.id] && PLAYLIST_SETS[n.id].has(r.p))},
  {id: "tag_issues", label: t("views.tag_issues"), test: r => hasAnyTagIssues(r)},
  // "Aufraeumen"-Ordner: zeigen die ganze Bibliothek, nur nach Genre/Album/
  // Interpret gruppiert statt gefiltert (siehe groupMode in render()).
  {id: "grp:genre",  label: t("views.grp_genre"),  test: () => true},
  {id: "grp:album",  label: t("views.grp_album"),  test: () => true},
  {id: "grp:artist", label: t("views.grp_artist"), test: () => true},
];
// Anzeige-Reihenfolge der Listen-Tabs -- "corrected" bleibt bewusst aussen
// vor: die Ansicht ist ueber den "Manuell korrigiert"-Chip in der
// Status-Zeile erreichbar (der setzt state.view direkt auf "corrected"),
// ein eigener Tab waere redundant. Der View bleibt trotzdem Teil von VIEWS,
// sonst findet currentView() ihn beim Umschalten nicht mehr.
// "duplicates" ist bewusst NICHT mehr Teil davon -- sie zieht in den
// "Aufraeumen"-Ordner (siehe cleanupKids in treeRoots()), bleibt aber Teil
// von VIEWS, sonst findet currentView() sie beim Umschalten nicht mehr.
const VIEW_TABS = ["all", "ignored", "gone"];


// ── Eigene Playlisten und Ordner ──────────────────────────────────────────
// Definitionen und Zuordnungen kommen beim Laden aus dem gebackenen META
// (report.build_html), damit Baum und zuletzt geoeffneter Knoten sofort
// stehen. Jede Aenderung danach zieht der Client live ueber /api/playlists
// nach -- ein voller Report-Neubau je Umsortierung waere bei ueber 10.000
// Zeilen und knapp 9 MB unbrauchbar. Gleiche Ausnahme wie bei
// ignored/favorites/corrected (siehe syncMarks() weiter unten).
let PLAYLISTS = Array.isArray(META.playlists) ? META.playlists : [];
let PLAYLIST_ITEMS = (META.playlistItems && typeof META.playlistItems === "object")
  ? META.playlistItems : {};
// {id: Set(pfade)} -- Mitgliedschaft in O(1) statt indexOf je Zeile.
let PLAYLIST_SETS = {};
// Pfad -> Zeile: fuer die Trackzahl je Playlist und fuer die manuelle
// Reihenfolge in filtered(). DATA selbst liegt nach Cutoff sortiert vor.
let ROW_BY_PATH = new Map();

// Beschriftung der festen Listen, die TrackTab selbst mitbringt
// (db._SYSTEM_PLAYLISTS). Der Name in der Datenbank ist nur ein lesbarer
// Rueckfall -- angezeigt wird die uebersetzte Fassung, sonst haenge die
// Beschriftung an der Sprache, in der sie einmal angelegt wurden.
// Steht bewusst HIER und nicht bei den uebrigen Baum-Funktionen weiter unten:
// rebuildPlaylistViews() laeuft direkt darunter noch auf Modulebene und
// braucht playlistLabel bereits.
const SYSTEM_PLAYLIST_LABELS = {
  sys_verdicts: "tree.sys_verdicts",
  sys_v_ok: "verdict.ok",
  sys_v_suspect: "verdict.suspicious",
  sys_v_fake: "verdict.fake",
  sys_v_unknown: "verdict.unclear",
  sys_v_corrected: "chip.manual_corrected",
};
const playlistLabel = n => SYSTEM_PLAYLIST_LABELS[n.id] ? t(SYSTEM_PLAYLIST_LABELS[n.id]) : n.name;

// Als Merkliste markierte Playlisten (playlists.fav_slot, siehe db.py) --
// Nachfolger der frueheren, fest konfigurierten FAVORITE_LISTS. Sortiert nach
// Slot fuer eine stabile Reihenfolge in Schnell-Filter-Tabs, Zeilen-Icon und
// Sammelleiste. Vorberechnet statt bei jedem Aufruf neu gefiltert/sortiert --
// merkPlaylists() steht im Zeilen-Renderer (COLS.n) und wird dort je
// sichtbarer Zeile gebraucht (siehe cmpText()/sortKeys()-Regel weiter unten:
// kein Schluessel, der Arbeit kostet, im heissen Pfad). rebuildMerkViews()
// haelt den Cache aktuell.
let MERK_PLAYLISTS_CACHE = [];
const merkPlaylists = () => MERK_PLAYLISTS_CACHE;
// Farbtoken einer Merkliste fuer .fltag/.flbtn -- "fl1" als Rueckfall, falls
// noch keine Farbe gewaehlt wurde (askPlaylistProps() erlaubt eine neue
// Playlist ohne Farbe). playlistBadgeHtml() faellt in diesem Fall auf
// "var(--dim)" zurueck, das hat aber keine "-bg"-Variante fuer den Chip-Hintergrund.
const flColorTok = n => n.color || "fl1";

function rebuildPlaylistIndex() {
  PLAYLIST_SETS = {};
  for (const id of Object.keys(PLAYLIST_ITEMS)) {
    PLAYLIST_SETS[id] = new Set(PLAYLIST_ITEMS[id]);
  }
  ROW_BY_PATH = new Map(DATA.map(r => [r.p, r]));
}

// Ein View je regulaerer Playlist ("pl:<id>"), damit currentView() und
// filtered() unveraendert weiterarbeiten. Ordner bekommen keinen View -- ein
// Klick auf sie klappt nur auf und zu. Dasselbe In-Place-Verfahren wie
// rebuildMerkViews(): VIEWS behaelt seine Array-Referenz. Die Knoten
// stehen bewusst NICHT in VIEW_TABS -- der Baum baut sie aus PLAYLISTS auf,
// nicht aus der Tab-Reihenfolge.
function rebuildPlaylistViews() {
  for (let i = VIEWS.length - 1; i >= 0; i--) {
    if (VIEWS[i].id.startsWith("pl:")) VIEWS.splice(i, 1);
  }
  for (let i = VIEWS.length - 1; i >= 0; i--) {
    if (VIEWS[i].id.startsWith("sm:")) VIEWS.splice(i, 1);
  }
  for (const n of PLAYLISTS) {
    const id = n.id;
    if (n.kind === "playlist") {
      VIEWS.push({
        id: `pl:${id}`, label: playlistLabel(n),
        test: r => !!(PLAYLIST_SETS[id] && PLAYLIST_SETS[id].has(r.p)),
      });
    } else if (n.kind === "smart") {
      // Das Ergebnis kommt aus SMART_CACHE, nicht aus einer Auswertung je
      // Zeile -- siehe recomputeSmart() und die Begruendung dort.
      VIEWS.push({
        id: `sm:${id}`, label: playlistLabel(n),
        test: r => { const s = SMART_CACHE.get(id); return !!s && s.has(r.p); },
      });
    }
  }
}

// Haelt MERK_PLAYLISTS_CACHE aktuell -- KEIN eigener Schnell-Filter-Tab mehr
// (fruehere "fl:<id>"-Views): eine als Merkliste markierte Playlist erscheint
// nur noch an ihrer normalen Stelle im Playlist-Baum (eigener "pl:<id>"-View,
// siehe rebuildPlaylistViews()), dort mit eckigem statt rundem Abzeichen
// (renderTree()). Ein zweiter Eintrag oben in der Tab-Reihe waere dieselbe
// Liste doppelt gezeigt.
function rebuildMerkViews() {
  MERK_PLAYLISTS_CACHE = PLAYLISTS
    .filter(n => n.kind === "playlist" && n.fav_slot)
    .sort((a, b) => a.fav_slot - b.fav_slot);
}

rebuildPlaylistIndex();
rebuildPlaylistViews();
rebuildMerkViews();

// Spalten, die sich ausblenden lassen — Checkbox und Öffnen bleiben immer
// da, die sind fuer die Bedienung noetig. Status (v) steht wie diese beiden
// als feste, nicht per Drag verschiebbare Kopfzelle im HTML (siehe renderHead()
// weiter unten), ist aber wie jede andere Spalte hier ausblendbar.
const OPTIONAL_COLUMNS = [
  {key: "v", label: t("col.status"), numeric: false},
  {key: "co", label: t("col.cutoff"), numeric: true},
  {key: "kb", label: t("field.declared"), numeric: true},
  {key: "mk", label: t("col.class"), numeric: true},
  {key: "cf", label: t("field.confidence"), numeric: true},
  {key: "st", label: t("col.steepness"), numeric: true},
  {key: "lu", label: t("col.loudness"), numeric: true},
  {key: "tp", label: t("col.clip"), numeric: true},
  {key: "du", label: t("field.duration"), numeric: true},
  {key: "rb", label: t("col.rekordbox"), numeric: true},
  {key: "im", label: t("col.in_music"), numeric: true},
  {key: "da", label: t("field.added"), numeric: true},
  {key: "cv", label: t("col.cover"), numeric: false},
  {key: "al", label: t("field.album"), numeric: false},
  {key: "tn", label: t("col.track_no"), numeric: true},
  {key: "aa", label: t("field.album_artist"), numeric: false},
  {key: "cp", label: t("field.composer"), numeric: false},
  {key: "ge", label: t("field.genre"), numeric: false},
  {key: "yr", label: t("field.year"), numeric: true},
  {key: "bp", label: t("field.bpm"), numeric: true},
  {key: "cm", label: t("field.comment"), numeric: false},
  {key: "a", label: t("field.artist"), numeric: false},
  {key: "t", label: t("field.title"), numeric: false},
  {key: "n", label: t("field.file"), numeric: false},
  {key: "ti", label: t("col.tag_issues"), numeric: false},
];
// Muss zu den Breiten im <thead> in index.html passen — dient als
// Rueckfallebene, wenn keine eigene Breite gespeichert ist (auch nach
// "Breiten zuruecksetzen"). "n" (Datei) hat wie jede andere Spalte einen
// festen Wert -- den Restplatz nimmt die Fuellzelle (td.filler) auf, sonst
// liesse sich die Spalte nicht anpassen (sie wuerde jede Aenderung sofort
// wieder mit dem freien Platz ausgleichen).
const DEFAULT_COL_WIDTHS = {v:105, co:90, kb:105, mk:75, cf:110, st:75, lu:90, tp:60, du:70, rb:105,
  im:85, da:115, cv:80, al:160, tn:70, aa:150, cp:140, ge:125, yr:60, bp:62, cm:180, a:150, t:200, n:340,
  ti:280};

let state = {
  view: "all",
  q: "", groupAlbums: false, pinned: false,
  // Aktiver Bubble-Filter einer der drei "Aufraeumen"-Boxen (Genre/Album/
  // Interpret) -- {field, entry}, bewusst nicht persistiert (localStorage/
  // config), gilt nur fuer die laufende Sitzung wie state.q.
  valueFilter: null,
  // Vorschau eines Zusammenfuehrungs-Vorschlags (Klick auf den Hinweistext,
  // siehe toggleMergePreview()): {field, a, b} -- schraenkt die Tabelle auf
  // genau die zwei betroffenen Werte ein, damit VOR dem Zusammenfuehren
  // sichtbar ist, welche Tracks betroffen sind. Beide Filter werden bei
  // jedem Listenwechsel zurueckgesetzt (selectView()), sonst bliebe eine
  // unsichtbare Einschraenkung in einer ganz anderen Ansicht haengen.
  mergePreview: null,
  // Aktiver Bubble-Filter der "Auffaelligkeiten"-Liste -- ein einzelner
  // Code (siehe app/taganomaly.py), nicht persistiert wie valueFilter, gilt
  // nur solange die Liste aktiv ist (selectView() setzt ihn zurueck).
  tagIssueFilter: null,
  sort: "co", dir: 1, shown: PAGE,
  layout: "edit",
  searchTolerance: 0.8,
  searchAcMinChars: 3,
  // Seitenleiste: Breite in px und die EINGEKLAPPTEN Baumknoten. Bewusst die
  // geschlossenen statt der offenen merken -- so ist ein spaeter neu
  // hinzukommender Knoten (Music.app, Rekordbox, eigene Ordner) von sich aus
  // aufgeklappt und nicht unsichtbar.
  sidebarW: 260,
  // Music.app und Rekordbox starten zugeklappt: ihr Inhalt wird erst beim
  // Aufklappen geholt, und das soll eine bewusste Handlung sein.
  treeClosed: ["root:music", "root:rekordbox"],
  // Reihenfolge der OBERSTEN Ebene unter TrackTab, als Liste von Knoten-IDs.
  // Sie muss hier liegen und nicht in der Datenbank: auf dieser Ebene stehen
  // die festen Ansichten (Alle, Ausgeblendet, ...) neben den eigenen Listen,
  // und erstere haben keine Datenbankzeile, in der ein 'seq' Platz haette.
  // Innerhalb von Ordnern gibt es dieses Problem nicht -- dort sind alle
  // Knoten Datenbankzeilen und die Reihenfolge liegt in 'playlists.seq'.
  treeOrder: [],
};

// Spalten-Reihenfolge/-Sichtbarkeit/-Breite je Ansicht (Bearbeiten/Player,
// siehe applyLayout() weiter unten) getrennt gespeichert. state.colOrder/
// hiddenCols/colWidths bleiben als Getter/Setter bestehen -- sie loesen
// immer auf die GERADE AKTIVE Ansicht auf, damit jede bestehende Stelle, die
// diese drei Felder liest/schreibt (Spalten-Menue, Drag-Resize, Drag-Reorder,
// render(), ...), unveraendert weiterfunktioniert.
state.columnsByLayout = {
  edit: {order: OPTIONAL_COLUMNS.map(c => c.key), hidden: [], widths: {}},
  player: {order: OPTIONAL_COLUMNS.map(c => c.key), hidden: [], widths: {}},
};

// Eigener, flacher Spalten-Topf fuer die Einzelprueflungen (Drops) --
// bewusst UNABHAENGIG von state.columnsByLayout/activeColumnStore(): Drops
// sind weder eine Ansicht (Bearbeiten/Player) noch eine Liste, der sich eine
// benannte Spaltenansicht zuweisen liesse. dropVisibleCols()/renderDrops()
// lesen direkt aus diesem Objekt, kein Getter/Setter-Umweg noetig (nur ein
// Topf, keine Zuordnung).
state.dropColumns = {order: OPTIONAL_COLUMNS.map(c => c.key), hidden: [], widths: {}};

// ── Gespeicherte Spaltenansichten ("Templates") ──────────────────────────
// Eine Spaltenansicht ist ein benannter Satz aus Reihenfolge, Sichtbarkeit
// und Breite; COLUMN_ASSIGN ordnet sie einer Liste zu ({viewId: id}). Jede
// Liste ohne Eintrag nimmt die STANDARD-Spalten -- das ist genau der schon
// vorher je Ansicht (Bearbeiten/Player) gespeicherte Zustand in
// state.columnsByLayout. Bewusst nur EINE Zuordnung je Liste statt einer je
// Liste und Ansicht: eine Spaltenansicht ist nur ein Satz Spalten, und wer
// sie einer Playlist gibt, meint sie dort auch im Player.
//
// Beides loest activeColumnStore() auf. Weil die drei Getter darunter
// darueber laufen, schreibt jede bestehende Stelle (Spalten-Menue,
// Ziehgriff, Drag am Spaltenkopf, render(), Einzelpruefungen) von sich aus
// in den richtigen Topf -- keine Fallunterscheidung an den Aufrufstellen.
let COLUMN_VIEWS = [];
let COLUMN_ASSIGN = {};
// Die im Einstellungs-Dialog gewaehlte Standard-Spaltenansicht: sie gilt fuer
// jede Liste ohne eigene Zuordnung. Leer = die im Spalten-Menue eingestellten
// Standard-Spalten der aktiven Ansicht (state.columnsByLayout).
let COLUMN_VIEW_DEFAULT = "";
// Gegenstueck zu COLUMN_VIEW_DEFAULT, aber nur fuer Apple-Music-Playlisten
// (view-id "mu:<id>") -- diese Fremd-Listen bekommen ueber das Sidebar-Menue
// bewusst keine eigene Zuordnung (siehe extSourceOfView()-Aufrufstellen),
// brauchen also einen eigenen Vorgabe-Weg VOR der allgemeinen Standardansicht.
let COLUMN_VIEW_MUSIC_DEFAULT = "";

const columnViewById = id => (id && COLUMN_VIEWS.find(v => v.id === id)) || null;
// Nur die Zuordnung dieser einen Liste -- ohne Ruecksicht auf die
// Standardansicht. Das Auswahlfeld im Spalten-Menue und der Listen-Dialog
// zeigen genau das an: "eigene Einstellung" oder eben keine.
const columnViewFor = viewId => columnViewById(COLUMN_ASSIGN[viewId]);
// Was tatsaechlich gilt: eigene Zuordnung, sonst (fuer Music.app-Playlisten)
// die Apple-Music-Standardansicht, sonst die allgemeine Standardansicht.
const activeColumnView = () =>
  columnViewFor(state.view) ||
  (extSourceOfView(state.view) === "music" ? columnViewById(COLUMN_VIEW_MUSIC_DEFAULT) : null) ||
  columnViewById(COLUMN_VIEW_DEFAULT);
const activeColumnStore = () => activeColumnView() || state.columnsByLayout[state.layout];

// Gespeicherte Spalten-Toepfe (Server, localStorage, alte Ansichten) auf den
// heutigen Spaltenbestand bringen: unbekannte Keys raus, seither
// hinzugekommene ans Ende -- sonst verschwaende eine neue Spalte hinter einer
// aelteren Speicherung.
function normalizeColumnStore(store) {
  const known = new Set(OPTIONAL_COLUMNS.map(c => c.key));
  const order = (Array.isArray(store.order) ? store.order : []).filter(k => known.has(k));
  for (const c of OPTIONAL_COLUMNS) if (!order.includes(c.key)) order.push(c.key);
  store.order = order;
  store.hidden = (Array.isArray(store.hidden) ? store.hidden : []).filter(k => known.has(k));
  store.widths = (store.widths && typeof store.widths === "object") ? store.widths : {};
  return store;
}

Object.defineProperty(state, "colOrder", {
  get() { return activeColumnStore().order; },
  set(v) { activeColumnStore().order = v; },
});
Object.defineProperty(state, "hiddenCols", {
  get() { return activeColumnStore().hidden; },
  set(v) { activeColumnStore().hidden = v; },
});
Object.defineProperty(state, "colWidths", {
  get() { return activeColumnStore().widths; },
  set(v) { activeColumnStore().widths = v; },
});

// Mehrfachauswahl — bewusst nicht persistiert, gilt nur für die laufende Sitzung.
state.selected = new Set();

// Per ×-Klick abgeschaltete Standard-Suchfilter (siehe refreshDefaultFilters()
// weiter unten) — wie state.selected bewusst nicht persistiert: ein Neuladen
// stellt sie wieder her, dauerhaft geht es nur über die Einstellungen.
state.disabledDefaults = new Set();

// Die Filtereinstellung überlebt einen Neustart — der Suchtext bewusst nicht,
// der ist immer auf die gerade laufende Suche gemünzt.
const LS_FILTERS = "tracktab.filters";

function saveFilters() {
  try {
    localStorage.setItem(LS_FILTERS, JSON.stringify({
      view: state.view,
      groupAlbums: state.groupAlbums,
      sort: state.sort, dir: state.dir,
      layout: state.layout,
      editFilterSnapshot: state.editFilterSnapshot || null,
      sidebarW: state.sidebarW, treeClosed: state.treeClosed,
      treeOrder: state.treeOrder,
      // Warteschlange selbst bleibt bewusst Session-only (wie state.selected)
      // -- nur die Wiedergabe-Vorlieben ueberleben einen Neustart.
      shuffle: queueState.shuffle, repeatTrack: queueState.repeatTrack, repeatList: queueState.repeatList,
      // Reihenfolge bleibt bewusst aussen vor (serverseitig, siehe
      // saveColumns() weiter unten) -- hier nur Breite + Sichtbarkeit,
      // je Ansicht (Bearbeiten/Player) getrennt.
      columnsByLayout: {
        edit: {widths: state.columnsByLayout.edit.widths, hidden: state.columnsByLayout.edit.hidden},
        player: {widths: state.columnsByLayout.player.widths, hidden: state.columnsByLayout.player.hidden},
      },
      // Gespeicherte Spaltenansichten und ihre Zuordnung liegen wie
      // Reihenfolge und Sichtbarkeit serverseitig (siehe saveColumns()) --
      // hier nur als Rueckfallebene fuer den Betrieb ohne Server und damit
      // die Kopfzeile beim Laden nicht erst mit den Standard-Spalten
      // aufblitzt, bevor /api/settings zurueck ist.
      columnViews: COLUMN_VIEWS,
      columnAssign: COLUMN_ASSIGN,
      columnViewDefault: COLUMN_VIEW_DEFAULT,
      columnViewMusicDefault: COLUMN_VIEW_MUSIC_DEFAULT,
      // Eigene Spaltenkonfiguration der Einzelprueflungen (state.dropColumns)
      // -- serverseitig die Wahrheit (siehe saveDropColumns()), hier nur die
      // Rueckfallebene wie bei columnsByLayout oben.
      dropColumns: {order: state.dropColumns.order, hidden: state.dropColumns.hidden,
                    widths: state.dropColumns.widths},
    }));
  } catch (e) { /* Speicher nicht verfügbar — dann eben nicht */ }
}

// Nach dem Wiederherstellen muessen die Bedienelemente den geladenen Zustand
// auch anzeigen — sonst steht "Konfidenz: alle" im Feld, waehrend 0,75 filtert.
function syncControls() {
  const ga = document.getElementById("groupAlbums");
  if (ga) ga.checked = !!state.groupAlbums;
  applyPinned();
  syncSortHeaders();
}

// ── Filterleiste anheften ─────────────────────────────────────────────
// Haelt Ansichten, Status, Suche und Sammelaktionen beim Scrollen am oberen
// Rand (CSS: body.pinned #filterPanel). Geschaltet wird das ueber die
// Einstellungen (cfg["pin_filter_bar"]), nicht ueber einen eigenen Knopf.
// Die Kopfzeile der Tabelle haengt sich direkt (ohne Abstand) unter die
// Filterleiste, sobald beide kleben, damit sie optisch zu einem Block
// verschmelzen (deshalb auch #stickyHead ohne eigene obere Kante in app.css --
// die untere Kante der Filterleiste uebernimmt die Trennlinie). Ist die
// Filterleiste nicht angeheftet, haengt sich die Kopfzeile stattdessen direkt
// an den Bildrand -- siehe stickyHeadTargetTop()/syncStickyHeadPosition()
// weiter unten. Ihr eigenes CSS sticky top:0 greift dafuer nicht, weil
// .tablewrap durch overflow-x:auto ein eigener Scrollbereich ist, der
// vertikal nie scrollt.
const PIN_TOP_PX = 16;                   // = top:1rem in app.css

// Der Schatten soll nur zu sehen sein, solange die Leiste wirklich klebt --
// steht sie an ihrem normalen Platz, gehoert sie optisch zu den uebrigen
// Bloecken. "Klebt gerade" laesst sich in CSS nicht abfragen, also wird die
// Position beim Scrollen mitgelesen (rAF-gedrosselt, nur eine Messung je
// Bild) und als body.pinstuck gesetzt; den weichen Uebergang macht dann die
// transition auf box-shadow.
function syncPinStuck() {
  const panel = document.getElementById("filterPanel");
  const stuck = !!state.pinned && !!panel
    && panel.getBoundingClientRect().top <= PIN_TOP_PX + 0.5;
  document.body.classList.toggle("pinstuck", stuck);
}

function applyPinned() {
  document.body.classList.toggle("pinned", !!state.pinned);
  syncPinStuck();
  syncStickyHeadPosition();
}

// Fast die gesamte Oberflaeche verwendet feste px-Groessen statt em/rem --
// eine Schriftgroessen-Einstellung skaliert deshalb ueber "zoom" statt ueber
// font-size, sonst muesste jede einzelne Regel im Stylesheet umgeschrieben
// werden. Skaliert damit bewusst Layout und Text gemeinsam (wie der
// Zoom-Befehl des Browsers), nicht nur die Schrift.
const FONT_ZOOM = {klein: 0.88, mittel: 1, gross: 1.16};
function applyFontSize(v) {
  document.body.style.zoom = FONT_ZOOM[v] || 1;
}

// "auto" ueberlaesst die Wahl app.css' @media(prefers-color-scheme) -- dafuer
// muss data-theme fehlen, ein leeres Attribut waere schon ein Treffer fuer
// [data-theme="light"]/[data-theme="dark"] und wuerde "auto" nie erlauben.
function applyTheme(v) {
  if (v === "light" || v === "dark") {
    document.documentElement.setAttribute("data-theme", v);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  // Die Wellenform-Ebenen sind mit den alten Farben gebacken (siehe
  // buildWaveLayer()) und muessen neu gerechnet werden.
  invalidateWaveTheme();
}

// Setzt data-accent auf <html> -- app.css referenziert das (Regeln
// [data-accent="..."] { --accent: var(--dc-...); ... }) und legt darüber
// --accent/--accent-bg auf die gewaehlte der 8 Vorgabefarben um. "blue"
// ist die bisherige, fest verdrahtete Farbe -- kein data-accent noetig,
// die Vorgabewerte von --accent/--accent-bg in :root bleiben dann einfach
// bestehen (kein Sonderfall, aber auch kein unnoetiges Attribut im DOM).
function applyAccentColor(v) {
  if (v && v !== "blue") {
    document.documentElement.setAttribute("data-accent", v);
  } else {
    document.documentElement.removeAttribute("data-accent");
  }
  // --accent faerbt den gespielten Teil der Wellenform (siehe
  // paintWaveEntry()) -- Themen-Cache und Ebenen entwerten.
  invalidateWaveTheme();
}

// ── Ansicht: Bearbeiten / Player ─────────────────────────────────────────
// "Bearbeiten" ist die heutige volle Oberflaeche (Vorgabe). "Player" blendet
// per [data-layout="player"]-Regeln in app.css alles aus, was fuer reines
// Anhoeren nicht gebraucht wird (siehe auch die data-*-Attribute in render()
// und renderBulkBar()).
// Weder Listenauswahl noch Filter werden beim Wechsel angetastet: der
// Seitenbaum steht in beiden Ansichten sichtbar daneben (siehe renderTree()),
// eine erzwungene Ruecksetzung waere dort nicht mehr nachvollziehbar. Die
// frueher noetige Sonderbehandlung des Verdikt-Filters ist entfallen, seit es
// die Verdikt-Schaltflaechen nicht mehr gibt -- ein Status laesst sich jetzt
// ueber die festen Listen im Baum oder ueber /Status in der Suche waehlen,
// beides in Player wie in Bearbeiten sichtbar.

function syncLayoutSwitch() {
  document.querySelectorAll("#layoutSwitch [data-layout]").forEach(b => {
    b.classList.toggle("on", b.dataset.layout === state.layout);
    if (b.dataset.layout === "player") b.disabled = !!scanRunning;
  });
}

function applyLayout(next) {
  if (next === state.layout) return;
  if (next === "player" && scanRunning) {
    note(t("layout.blocked_scan_running"), "soft");
    return;
  }
  // In der Player-Ansicht klappt kein Zeilenklick mehr eine Wellenform auf
  // (siehe render()-Klick-Handler) -- aus der Bearbeiten-Ansicht offen
  // gebliebene Zeilen wuerden sonst dort weiterhin angezeigt: render()s
  // eigene "aufgeklappte Zeile ueberlebt einen Neuaufbau"-Logik (siehe
  // expandedByIdx) haengt eine .detail-Zeile an jede noch vorhandene
  // tr.row[data-i] wieder an, unabhaengig vom Layout. Deshalb hier explizit
  // vor dem naechsten render() schliessen, nicht dort behandeln. Nur die
  // Haupttabelle (#tb) -- die Einzelpruefungen-Tabelle (mountDropPlayer,
  // dieselben Klassennamen) ist layoutunabhaengig und bleibt unangetastet.
  const tbForCollapse = document.getElementById("tb");
  if (tbForCollapse) {
    tbForCollapse.querySelectorAll("tr.detail.expanded").forEach(det => {
      const tr = det.previousElementSibling;
      if (tr && tr.classList.contains("row")) tr.classList.remove("expanded");
      det.remove();
    });
  }
  // Checkbox-Mehrfachauswahl ist bewusst je Layout eigenstaendig -- eine
  // Auswahl aus "Bearbeiten" haette in "Player" keinen erkennbaren Bezug
  // mehr (andere Spalten/Aktionen) und wuerde dort nur verwirren.
  state.selected.clear();
  state.layout = next;
  document.body.setAttribute("data-layout", state.layout);
  state.shown = PAGE;
  syncLayoutSwitch();
  syncControls();
  lastColumnStoreKey = columnStoreKey();
  renderHead();
  renderColsMenu();
  const colsMenu = document.getElementById("colsMenu");
  if (colsMenu) colsMenu.style.display = "none";
  renderTree();
  updateCards();
  render();
  saveFilters();
  renderPlayerBar();
}

let pinScrollQueued = false;
window.addEventListener("scroll", () => {
  if (pinScrollQueued) return;
  pinScrollQueued = true;
  requestAnimationFrame(() => {
    pinScrollQueued = false;
    syncPinStuck();
    syncStickyHeadPosition();
    maybeAutoLoadMore();
    syncToTopButton();
  });
}, {passive: true});

// ── "Nach oben"-Knopf ────────────────────────────────────────────────────
// Erscheint erst, sobald mindestens 5 Zeilen komplett oberhalb des
// Sichtfensters verschwunden sind -- zaehlt vom Tabellenanfang, bricht am
// ersten (noch) sichtbaren Treffer ab statt jede geladene Zeile zu pruefen.
const TOTOP_ROW_THRESHOLD = 5;
function syncToTopButton() {
  const btn = document.getElementById("toTopBtn");
  if (!btn) return;
  const rows = document.querySelectorAll("tr.row");
  let hiddenAbove = 0;
  for (const row of rows) {
    if (row.getBoundingClientRect().bottom < 0) hiddenAbove++;
    else break;
  }
  btn.classList.toggle("show", hiddenAbove >= TOTOP_ROW_THRESHOLD);
}

// ── Hoerzeit-Erfassung fuer die Statistik (Issue #15) ────────────────────
// Verfolgt die tatsaechliche Hoerzeit des laufenden Tracks. Kumuliert ueber
// ontimeupdate-Deltas (nicht Wanduhrzeit) -- ein Sprung > LISTEN_MAX_JUMP_S
// (Suchleiste, Cue-Klick, naechster Track) zaehlt nicht als Hoerzeit. Wird
// NICHT bei jeder Pause verworfen, sondern bei Fortsetzen desselben Tracks
// weitergefuehrt (sonst wuerde Pause/Resume eine zusammenhaengende
// Wiedergabe faelschlich in zwei zu kurze Haeppchen zerlegen) -- erst ein
// Trackwechsel, 'ended' oder Seiten-Unload schliesst die Sitzung ab und
// sendet sie (nur wenn >= LISTEN_THRESHOLD_S erreicht). Ein einziger,
// modul-globaler State reicht: die beiden Player-Systeme (Inline-Zeilenplayer
// und der feste Player-Balken, siehe queueAudio weiter unten) sind je Ansicht
// exklusiv, es spielt also praktisch nie mehr als einer davon gleichzeitig.
const LISTEN_THRESHOLD_S = 30;
const LISTEN_MAX_JUMP_S = 2;
let listenSession = null; // {path, heard, lastTime, sent}

function listenTrackStart(r, audio) {
  if (!r || !r.p) { listenFinish(); return; }
  if (listenSession && listenSession.path === r.p && !listenSession.sent) {
    listenSession.lastTime = audio.currentTime;
    return;
  }
  listenFinish();
  listenSession = {path: r.p, heard: 0, lastTime: audio.currentTime, sent: false};
}

function listenTrackTick(audio) {
  if (!listenSession) return;
  const t = audio.currentTime;
  const delta = t - listenSession.lastTime;
  if (delta > 0 && delta <= LISTEN_MAX_JUMP_S) listenSession.heard += delta;
  listenSession.lastTime = t;
}

function listenFinish() {
  const s = listenSession;
  listenSession = null;
  if (!s || s.sent || s.heard < LISTEN_THRESHOLD_S || !apiMode) return;
  s.sent = true;
  const body = JSON.stringify({path: s.path, duration_s: Math.round(s.heard)});
  if (navigator.sendBeacon) {
    navigator.sendBeacon("/api/play", new Blob([body], {type: "application/json"}));
  } else {
    fetch("/api/play", {method: "POST", headers: {"Content-Type": "application/json"},
      body, keepalive: true}).catch(() => {});
  }
}
window.addEventListener("pagehide", listenFinish);

// ── Endlos-Scrollen (Einstellung "infinite_scroll") ─────────────────────
// Laedt beim Erreichen des Listenendes automatisch INFINITE_SCROLL_CHUNK
// weitere Treffer nach, statt "weitere laden" von Hand anzuklicken. Haengt
// sich in den bereits vorhandenen, rAF-gedrosselten Scroll-Listener oben ein
// statt einen eigenen zu registrieren. autoLoading verhindert, dass ein
// laufendes render() (kann bei sehr grossen Bibliotheken kurz dauern) durch
// denselben Scroll-Frame mehrfach angestossen wird.
let autoLoading = false;
function maybeAutoLoadMore() {
  if (!state.infiniteScroll || autoLoading) return;
  if (state.shown >= (state.totalFiltered || 0)) return;
  const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 500;
  if (!nearBottom) return;
  autoLoading = true;
  const rows = filtered();
  beginLoadMore(rows, state.shown + INFINITE_SCROLL_CHUNK);
  autoLoading = false;
}

// ── Tabellen-Kopfzeile anheften ───────────────────────────────────────
// Echter position:sticky auf <thead> wirkt innerhalb von .tablewrap nie
// (siehe Kommentar oben) -- stattdessen pflegt syncStickyHeadContent() einen
// Nachbau der Kopfzeile in #stickyHead (position:fixed) und
// syncStickyHeadPosition() zeigt/versteckt und plaziert ihn. Beide Haelften
// getrennt, weil sich der Inhalt (Spaltenbreiten, Sortierpfeil) viel
// seltener aendert als die Position (jeder Scroll-Frame).
function stickyHeadTargetTop() {
  if (!state.pinned) return 0;
  const panel = document.getElementById("filterPanel");
  return panel ? panel.getBoundingClientRect().bottom : 0;
}

function syncStickyHeadPosition() {
  const wrap = document.getElementById("tb")?.closest(".tablewrap");
  const overlay = document.getElementById("stickyHead");
  const realThead = wrap && wrap.querySelector("thead");
  if (!wrap || !overlay || !realThead) return;
  const targetTop = stickyHeadTargetTop();
  const stuck = realThead.getBoundingClientRect().top < targetTop - 0.5;
  overlay.classList.toggle("show", stuck);
  // Nur waehrend die Kopfzeile wirklich an der Filterleiste klebt (target =
  // deren Unterkante, siehe stickyHeadTargetTop()) deren untere Ecken eckig
  // machen -- sonst zeigt sich an den Ecken ein kleiner Rundungs-Spalt
  // zwischen zwei eigentlich beruehrenden Kanten (CSS: body.pinned.headconnected).
  document.body.classList.toggle("headconnected", stuck && !!state.pinned);
  if (!stuck) return;
  const rect = wrap.getBoundingClientRect();
  overlay.style.top = Math.round(targetTop) + "px";
  overlay.style.left = Math.round(rect.left) + "px";
  overlay.style.width = Math.round(wrap.clientWidth) + "px";
  const scrollBox = document.getElementById("stickyHeadScroll");
  if (scrollBox && scrollBox.scrollLeft !== wrap.scrollLeft) scrollBox.scrollLeft = wrap.scrollLeft;
}

// Nachbau statt Original: die eingeklebten Randspalten (sel/links) sollen
// ihr eigenes position:sticky behalten (siehe generische th.sel/th.links-
// Regeln in app.css) -- das braucht einen echten, eigenen horizontalen
// Scrollbereich (#stickyHeadScroll), kein transform. id-Attribute (z.B. die
// Checkbox "selAll") werden entfernt, sonst gaebe es sie zweimal im
// Dokument; interaktive Elemente bis auf die Sortierspalten sind bewusst
// inert, das Original bleibt die einzige Bedienoberflaeche.
function syncStickyHeadContent() {
  const wrap = document.getElementById("tb")?.closest(".tablewrap");
  const overlay = document.getElementById("stickyHead");
  const realRow = wrap && wrap.querySelector("thead tr");
  const thead = overlay && overlay.querySelector("thead");
  if (!wrap || !realRow || !thead) return;
  const clone = realRow.cloneNode(true);
  clone.querySelectorAll("[id]").forEach(el => el.removeAttribute("id"));
  clone.querySelectorAll("input").forEach(el => { el.disabled = true; el.tabIndex = -1; });
  clone.querySelectorAll("th[data-k]").forEach(th => {
    const k = th.dataset.k;
    th.onclick = () => {
      const real = realRow.querySelector(`th[data-k="${CSS.escape(k)}"]`);
      if (real) real.click();
    };
  });
  thead.innerHTML = "";
  thead.appendChild(clone);
  const realTable = wrap.querySelector("table");
  overlay.querySelector("table").style.width = realTable.style.width || "";
  syncStickyHeadPosition();
}

// Horizontal zweiseitig verkoppelt -- ob per echter Scrollleiste an der
// echten Tabelle oder (unsichtbar) im Nachbau gescrollt wird, beide sollen
// im Gleichschritt bleiben. Der Wächter verhindert, dass sich beide
// Listener gegenseitig erneut auslösen.
let stickyHeadScrollSyncing = false;
function linkHorizontalScroll(a, b) {
  a.addEventListener("scroll", () => {
    if (stickyHeadScrollSyncing) return;
    stickyHeadScrollSyncing = true;
    b.scrollLeft = a.scrollLeft;
    stickyHeadScrollSyncing = false;
  }, {passive: true});
}
(function initStickyHeadScrollLink() {
  const wrap = document.getElementById("tb")?.closest(".tablewrap");
  const scrollBox = document.getElementById("stickyHeadScroll");
  if (!wrap || !scrollBox) return;
  linkHorizontalScroll(wrap, scrollBox);
  linkHorizontalScroll(scrollBox, wrap);
})();

function loadFilters() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_FILTERS) || "null");
    if (!s) return;
    // Ansicht zuerst restaurieren -- state.colOrder/hiddenCols/colWidths
    // loesen ueber die Getter/Setter auf state.layout auf, alles danach muss
    // also schon den richtigen Wert vorfinden.
    if (s.layout === "edit" || s.layout === "player") state.layout = s.layout;
    if (VIEWS.some(v => v.id === s.view)) state.view = s.view;
    for (const k of ["sort"]) if (s[k] !== undefined) state[k] = s[k];
    if (typeof s.groupAlbums === "boolean") state.groupAlbums = s.groupAlbums;
    if (s.dir === 1 || s.dir === -1) state.dir = s.dir;
    if (typeof s.shuffle === "boolean") queueState.shuffle = s.shuffle;
    if (typeof s.repeatTrack === "boolean") queueState.repeatTrack = s.repeatTrack;
    if (typeof s.repeatList === "boolean") queueState.repeatList = s.repeatList;
    if (s.editFilterSnapshot && typeof s.editFilterSnapshot === "object") {
      state.editFilterSnapshot = s.editFilterSnapshot;
    }
    if (typeof s.sidebarW === "number") state.sidebarW = s.sidebarW;
    if (Array.isArray(s.treeClosed)) state.treeClosed = s.treeClosed.filter(x => typeof x === "string");
    if (Array.isArray(s.treeOrder)) state.treeOrder = s.treeOrder.filter(x => typeof x === "string");
    if (s.columnsByLayout && typeof s.columnsByLayout === "object") {
      for (const layout of ["edit", "player"]) {
        const saved = s.columnsByLayout[layout];
        if (!saved) continue;
        if (saved.widths && typeof saved.widths === "object") {
          state.columnsByLayout[layout].widths = saved.widths;
        }
        if (Array.isArray(saved.hidden)) {
          state.columnsByLayout[layout].hidden =
            saved.hidden.filter(k => OPTIONAL_COLUMNS.some(c => c.key === k));
        }
      }
    } else {
      // Alte, flache Speicherung von vor dem Layout-Umschalter -- als
      // Ansicht "edit" uebernehmen statt sie stumm zu verwerfen.
      if (s.colWidths && typeof s.colWidths === "object") {
        state.columnsByLayout.edit.widths = s.colWidths;
      }
      if (Array.isArray(s.hiddenCols)) {
        state.columnsByLayout.edit.hidden =
          s.hiddenCols.filter(k => OPTIONAL_COLUMNS.some(c => c.key === k));
      }
    }
    if (Array.isArray(s.columnViews)) {
      COLUMN_VIEWS = s.columnViews
        .filter(v => v && v.id && v.name)
        .map(v => normalizeColumnStore({id: String(v.id), name: String(v.name),
                                        order: v.order, hidden: v.hidden, widths: v.widths}));
    }
    if (s.columnAssign && typeof s.columnAssign === "object") {
      COLUMN_ASSIGN = {};
      for (const [view, id] of Object.entries(s.columnAssign)) {
        if (COLUMN_VIEWS.some(v => v.id === id)) COLUMN_ASSIGN[view] = id;
      }
    }
    if (COLUMN_VIEWS.some(v => v.id === s.columnViewDefault)) {
      COLUMN_VIEW_DEFAULT = s.columnViewDefault;
    }
    if (COLUMN_VIEWS.some(v => v.id === s.columnViewMusicDefault)) {
      COLUMN_VIEW_MUSIC_DEFAULT = s.columnViewMusicDefault;
    }
    if (s.dropColumns && typeof s.dropColumns === "object") {
      if (Array.isArray(s.dropColumns.order) && s.dropColumns.order.length) {
        state.dropColumns.order = s.dropColumns.order;
      }
      if (Array.isArray(s.dropColumns.hidden)) {
        state.dropColumns.hidden =
          s.dropColumns.hidden.filter(k => OPTIONAL_COLUMNS.some(c => c.key === k));
      }
      if (s.dropColumns.widths && typeof s.dropColumns.widths === "object") {
        state.dropColumns.widths = s.dropColumns.widths;
      }
      normalizeColumnStore(state.dropColumns);
    }
  } catch (e) { /* kaputter Eintrag wird ignoriert */ }
}

// Ausgeblendete Tracks: liegen im Normalfall in der Datenbank (Server laeuft).
// Wird die Datei direkt per file:// geoeffnet, faellt die Speicherung auf
// localStorage zurueck — dann gilt sie nur fuer diesen Browser.
const LS_KEY = "tracktab.ignored";
const LS_CORRECTED = "tracktab.corrected";
let apiMode = false;

const fmtTime = s => Math.floor(s/60) + ":" + String(s%60).padStart(2,"0");
const fmtDate = ts => ts ? new Date(ts*1000).toLocaleDateString("de-DE") : "";
// Das einfache Anfuehrungszeichen gehoert mit dazu: derzeit steht zwar kein
// Attribut in der Oberflaeche in einfachen Anfuehrungszeichen (alle Vorlagen
// nutzen doppelte), aber ein kuenftiges title='${esc(r.t)}' waere sonst mit
// einem Apostroph im Interpreten sofort aufbrechbar -- und Tag-Texte kommen
// aus fremden Dateien.
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
// Dateiname, Ordner und file://-URL werden aus dem Pfad abgeleitet
const baseName = p => p.slice(p.lastIndexOf("/") + 1);
const fileExt  = p => (/\.([a-z0-9]+)$/i.exec(baseName(p)) || [, ""])[1].toUpperCase();
const dirName  = p => p.slice(0, p.lastIndexOf("/"));
const fileURL  = p => "file://" + p.split("/").map(encodeURIComponent).join("/");
// Der Browser haelt jedes einmal geladene Bild pro Dokument unter seiner URL
// fest ("list of available images" im HTML-Standard) -- Cache-Control:
// no-store aendert daran nichts, weil diese Ebene ueber dem HTTP-Cache
// liegt. Baut render() die Tabelle neu auf, kommt fuer eine unveraenderte
// URL also das alte Bild zurueck: ein frisch geschriebenes Cover blieb in
// der Liste unsichtbar (an echtem Material: das erste Cover erschien, weil
// es vorher gar kein <img> gab, jedes weitere nicht mehr). Jede
// Cover-Aenderung zaehlt darum bumpCoverRev() hoch, was die URL aendert.
const coverRev = new Map();
const bumpCoverRev = p => coverRev.set(p, (coverRev.get(p) || 0) + 1);
const coverUrl = p => `/api/cover?path=${encodeURIComponent(p)}`
  + (coverRev.get(p) ? `&v=${coverRev.get(p)}` : "");

// Chrome/Firefox koennen AIFF im <audio>-Element grundsaetzlich nicht
// dekodieren (unabhaengig vom vom Server gesendeten MIME-Type), Safari
// schon -- echte Format-Erkennung statt fest auf einen Browser zu setzen,
// damit ein spaeterer Browser-Support das automatisch mitzieht.
const CAN_PLAY_AIFF = (() => {
  try {
    const a = document.createElement("audio");
    return !!(a.canPlayType && a.canPlayType("audio/aiff"));
  } catch { return true; }
})();
// ── Symbole ─────────────────────────────────────────────────────────────
// Strichzeichnungen erben ihre Farbe vom Knopf (currentColor), damit sich
// erledigt/löschen farblich absetzen können. Finder und der Audio-Editor
// bekommen ihr echtes Programmsymbol vom Server.
const SVG = (inner, fill) =>
  `<svg viewBox="0 0 24 24" width="15" height="15" fill="${fill || "none"}"
        stroke="currentColor" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

const ICONS = {
  play: SVG('<polygon points="6 3 20 12 6 21 6 3"/>', "currentColor"),
  pause: SVG('<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>', "currentColor"),
  eye: SVG('<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/>' +
           '<circle cx="12" cy="12" r="3"/>'),
  eyeOff: SVG('<path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/>' +
              '<path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/>' +
              '<path d="M14.12 14.12a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'),
  cart: SVG('<circle cx="9" cy="21" r="1.6" fill="currentColor" stroke="none"/>' +
            '<circle cx="19" cy="21" r="1.6" fill="currentColor" stroke="none"/>' +
            '<path d="M1 2h3.5l2.4 12.1a2 2 0 002 1.6h8.6a2 2 0 002-1.6L21.5 6H6"/>'),
  undo: SVG('<polyline points="1 4 1 10 7 10"/>' +
            '<path d="M3.5 15a9 9 0 102.1-9.4L1 10"/>'),
  correct: SVG('<circle cx="12" cy="12" r="10"/><polyline points="7 12.5 10.5 16 17 8.5"/>'),
  // Nur der Haken ohne Kreis -- fuer Flaechen, die selbst schon die Farbe
  // tragen (Merken-Quadrate/-Farbfelder), wo ein zusaetzlicher Kreis nur
  // stoert.
  check: SVG('<polyline points="4 12.5 9.5 18 20 6.5"/>'),
  // Fragezeichen ohne Kreis, gleiche Begruendung wie beim Haken -- fuer die
  // Sammelleiste bei gemischtem Merken-Vorzustand ueber der Auswahl.
  question: SVG('<path d="M9.5 9a2.5 2.5 0 114.7 1.2c-.9.8-1.7 1.4-1.7 2.8"/>' +
                '<line x1="12.5" y1="17" x2="12.5" y2="17.01"/>'),
  trash: SVG(LUCIDE_ICONS["trash"]),
  exportZip: SVG(LUCIDE_ICONS["file-down"]),
  gripVertical: SVG(LUCIDE_ICONS["grip-vertical"]),
  audioLines: SVG(LUCIDE_ICONS["audio-lines"]),
  chevronDown: SVG(LUCIDE_ICONS["chevron-down"]),
  squareX: SVG(LUCIDE_ICONS["square-x"]),
  // Generisch statt App-gebunden — das Programm dahinter ist frei wählbar.
  editor: SVG('<line x1="4" y1="20" x2="4" y2="12"/><line x1="9" y1="20" x2="9" y2="6"/>' +
              '<line x1="14" y1="20" x2="14" y2="15"/><line x1="19" y1="20" x2="19" y2="9"/>'),
  edit: SVG(LUCIDE_ICONS["square-pen"]),
  pencil: SVG(LUCIDE_ICONS["pencil"]),
  sparkles: SVG(LUCIDE_ICONS["sparkles"]),
  pencilRuler: SVG(LUCIDE_ICONS["pencil-ruler"]),
  eye: SVG(LUCIDE_ICONS.eye),
  eyeOff: SVG(LUCIDE_ICONS["eye-off"]),
  merge: SVG(LUCIDE_ICONS.merge),
  // Drei senkrechte Punkte -- Ausloeser fuer das "Weitere Optionen"-Menue je
  // Zeile (Bitrate korrigieren/Ausblenden/Korrigieren/Loeschen, siehe
  // rowMoreMenuItems() weiter unten).
  moreVert: SVG('<circle cx="12" cy="5" r="1.6" fill="currentColor" stroke="none"/>' +
                '<circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>' +
                '<circle cx="12" cy="19" r="1.6" fill="currentColor" stroke="none"/>'),
  // Fuer den Layout-Umschalter (Ansicht "Player").
  player: SVG(LUCIDE_ICONS["music"]),
  // Fixer Player-Balken (Ansicht "Player", siehe queueState weiter unten).
  next: SVG(LUCIDE_ICONS["skip-forward"]),
  prev: SVG(LUCIDE_ICONS["skip-back"]),
  shuffle: SVG('<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>' +
               '<polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>' +
               '<line x1="4" y1="4" x2="9" y2="9"/>'),
  repeatList: SVG(LUCIDE_ICONS["repeat"]),
  repeatTrack: SVG(LUCIDE_ICONS["repeat-1"]),
  // Warteschlange: gestapelte Linien, teils mit Plus/Play-Dreieck fuers
  // Gegenstueck "hinzufuegen" bzw. "als naechstes wiedergeben".
  listVideo: SVG(LUCIDE_ICONS["list-video"]),
  listEnd: SVG(LUCIDE_ICONS["list-end"]),
  listStart: SVG(LUCIDE_ICONS["list-start"]),
  // Playlist: Liste mit Plus bzw. X, Gegenstuecke ("in die Liste" /
  // "aus der Liste").
  listPlus: SVG(LUCIDE_ICONS["list-plus"]),
  listX: SVG(LUCIDE_ICONS["list-x"]),
  volume: SVG(LUCIDE_ICONS["volume"]),
  volumeMid: SVG(LUCIDE_ICONS["volume-1"]),
  volumeHigh: SVG(LUCIDE_ICONS["volume-2"]),
  volumeMute: SVG(LUCIDE_ICONS["volume-x"]),
  fix: SVG(LUCIDE_ICONS["waves-arrow-down"]),
  convert: SVG(LUCIDE_ICONS["refresh-ccw-dot"]),
  reanalyse: SVG(LUCIDE_ICONS["rotate-ccw"]),
  // Eigenes Icon fuer "Neu analysieren" (Track neu messen), getrennt von
  // reanalyse oben -- das bleibt "rotate-ccw" fuer "Neu laden" bei fremden
  // Playlisten-Baeumen (Music.app/Rekordbox), eine andere Handlung, die nur
  // zufaellig denselben Namen im Code hatte.
  pickaxe: SVG(LUCIDE_ICONS["pickaxe"]),
  scan: SVG('<circle cx="10" cy="10" r="7"/><line x1="21" y1="21" x2="15" y2="15"/>'),
  // Kettenglied: Datei am neuen Ort suchen und wieder mit der Zeile verknuepfen
  relink: SVG('<path d="M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7"/>' +
              '<path d="M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7L12.2 19"/>'),
  update: SVG(LUCIDE_ICONS["rotate-cw"]),
  folder: SVG(LUCIDE_ICONS["folder-open"]),
  rename: SVG(LUCIDE_ICONS["file-type"]),
  libraryBig: SVG(LUCIDE_ICONS["library-big"]),
  power: SVG('<path d="M18.4 6.6a9 9 0 11-12.8 0"/><line x1="12" y1="2" x2="12" y2="12"/>'),
  close: SVG('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'),
  save: SVG(LUCIDE_ICONS["save"]),
  rulerDimensionLine: SVG(LUCIDE_ICONS["ruler-dimension-line"]),
  arrowRightLeft: SVG(LUCIDE_ICONS["arrow-right-left"]),
  columns3: SVG(LUCIDE_ICONS["columns-3"]),
  listRestart: SVG(LUCIDE_ICONS["list-restart"]),
  listChecks: SVG(LUCIDE_ICONS["list-checks"]),
  stickyNotes: SVG(LUCIDE_ICONS["sticky-notes"]),
  fileMusic: SVG(LUCIDE_ICONS["file-music"]),
  fileSearchCorner: SVG(LUCIDE_ICONS["file-search-corner"]),
  brushCleaning: SVG(LUCIDE_ICONS["brush-cleaning"]),
  databaseArrowDown: SVG(LUCIDE_ICONS["database-arrow-down"]),
  databaseBackup: SVG(LUCIDE_ICONS["database-backup"]),
  bookDown: SVG(LUCIDE_ICONS["book-down"]),
  lock: SVG(LUCIDE_ICONS["lock"]),
  // Eigene Marken in den Hausfarben der Dienste — bewusst kein Logo-Nachbau.
  beatport: `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#01FF95"/>
      <text x="12" y="17.5" text-anchor="middle" font-size="15" font-weight="800"
            font-family="-apple-system,Helvetica,sans-serif" fill="#04231a">b</text></svg>`,
  soundcloud: `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#FF5500"/>
      <g fill="#fff">
        <rect x="4.5" y="13" width="1.8" height="6" rx=".9"/>
        <rect x="8" y="10" width="1.8" height="9" rx=".9"/>
        <rect x="11.5" y="6.5" width="1.8" height="12.5" rx=".9"/>
        <rect x="15" y="9" width="1.8" height="10" rx=".9"/>
        <rect x="18.5" y="12" width="1.8" height="7" rx=".9"/>
      </g></svg>`,
  itunes: `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#C33FD1"/>
      <path fill="#fff" d="M12 3.2l2.55 5.66 6.15.62-4.63 4.15 1.3 6.05L12 16.7l-5.37 2.98
               1.3-6.05-4.63-4.15 6.15-.62z"/></svg>`,
};

const appIcon = (which, alt) =>
  `<img class="appicon" src="/api/appicon?which=${which}" alt="${alt}" title="${alt}">`;

// /api/appicon findet Mixed In Key/Rekordbox auch ohne manuell eingetragenen
// Pfad automatisch (mik_path()/rekordbox_path() in media.py) -- das echte
// Symbol ist also so gut wie immer da, selbst wenn das zugehoerige Feld in
// den Einstellungen (noch) leer ist. Ganz ohne Installation liefert der
// Endpunkt 404; dieser Fallback ersetzt das kaputte Bild dann durch den
// generischen Platzhalter.
document.addEventListener("error", ev => {
  if (ev.target.classList && ev.target.classList.contains("appicon")) {
    ev.target.replaceWith(document.createRange().createContextualFragment(ICONS.editor));
  }
}, true);

// Ausgegrauter Knopf fuer ein extern angebundenes Programm (Mixed In Key,
// Rekordbox), dessen Einstellungen-Feld (noch) leer ist -- zeigt trotzdem
// das echte Programm-Symbol (ausgegraut per .iconbtn:disabled), damit
// erkennbar bleibt, welches Programm gemeint ist. Bewusst sichtbar statt
// komplett ausgeblendet, der Tooltip erklaert die fehlende Einstellung.
const disabledAppIcon = (which, alt, title, id) =>
  `<button class="iconbtn plain"${id ? ` id="${id}"` : ""} disabled title="${esc(title)}">${
    appIcon(which, alt)}</button>`;
// Fuer Faelle ohne zugehoeriges Programm-Symbol (z.B. der Audio-Editor, frei
// waehlbar statt an eine bestimmte App gebunden).
const disabledToolBtn = title =>
  `<button class="iconbtn plain" disabled title="${esc(title)}">${ICONS.editor}</button>`;
let REKORDBOX_MISSING_HINT = t("hint.rekordbox_missing");

// ── Suche in den Shops ──────────────────────────────────────────────────────
// Aus Tags oder Dateiname eine brauchbare Suchanfrage bauen. DJ-Pool-Zusätze
// wie "(Clean)" oder "(128 Bpm)" stehen nicht im Shop und würden die Suche
// ins Leere laufen lassen; Remix-Angaben bleiben dagegen stehen, weil genau
// die auf Beatport den Unterschied machen.
const BP_JUNK = /\b(clean|dirty|intro|outro|acapella|acapela|instrumental|transition|quick\s*hit|short\s*edit|hype|explicit|\d{2,3}\s*bpm|dj\s*edit|snip|teaser)\b/i;

function searchQuery(r) {
  const strip = s => String(s || "")
    .replace(/[([]([^)\]]*)[)\]]/g, (all, inner) => BP_JUNK.test(inner) ? " " : all)
    .replace(/^\s*\d{1,3}\s*[-._]?\s+/, "")      // führende Titelnummer
    .replace(/\s*[-–]?\s*\b\d{2,3}\s*bpm\b\s*$/i, "")   // BPM auch ohne Klammern
    .replace(/\s*[-–]\s*$/, "")
    .replace(/\s{2,}/g, " ")
    .trim();

  let query = [strip(r.a), strip(r.t)].filter(Boolean).join(" ");
  if (!query) {
    query = strip(baseName(r.p).replace(/\.[a-z0-9]{2,5}$/i, "").replace(/_/g, " "));
  }
  return query;
}

// Shops sind konfigurierbar (Einstellungen → Shops). Diese Liste ist nur der
// Notanker, falls kein Server läuft oder die Einstellungen noch nicht geladen sind.
const DEFAULT_SHOPS = [
  {id: "beatport", name: "Beatport", url: "https://www.beatport.com/search?q={q}",
   color: "#01FF95", enabled: true},
  {id: "soundcloud", name: "SoundCloud", url: "https://soundcloud.com/search?q={q}",
   color: "#FF5500", enabled: true},
  {id: "djcity", name: "DJ City", url: "https://www.djcity.com/search?q={q}",
   color: "#E4002B", enabled: true},
  {id: "zipdj", name: "Zip DJ", url: "https://www.zipdj.com/app/search?q={q}",
   color: "#00A651", enabled: true},
  {id: "itunes", name: "iTunes Store", url: "https://music.apple.com/search?term={q}",
   color: "#fc3c44", enabled: true},
];
let ALL_SHOPS = DEFAULT_SHOPS;

function shopIcon(s) {
  if (s.id === "beatport") return ICONS.beatport;
  if (s.id === "soundcloud") return ICONS.soundcloud;
  if (s.id === "itunes") return ICONS.itunes;
  const letter = (s.name || "?").trim().charAt(0).toUpperCase() || "?";
  return `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="${esc(s.color || "#888")}"/>
      <text x="12" y="17.5" text-anchor="middle" font-size="15" font-weight="800"
            font-family="-apple-system,Helvetica,sans-serif" fill="#111">${esc(letter)}</text></svg>`;
}

// Der iTunes Store ist kein normaler Link: itmss: öffnet die Music-App, und
// der Browser würde das jedes Mal rückfragen. Mit laufendem Server UND
// ausgewählter Music App (MUSIC_NAME) ruft shopLinks() ihn unten direkt auf;
// sonst bleibt der Shop-Eintrag ein ganz normaler https-Link wie jeder
// andere Shop (s.url, siehe DEFAULT_SHOPS/cfg["shops"]).
const itunesURL = q =>
  "itmss://itunes.apple.com/search?term=" + encodeURIComponent(q) +
  "&media=music&entity=song";

function shopLinks(r) {
  const q = searchQuery(r);
  const shops = (apiMode ? ALL_SHOPS : DEFAULT_SHOPS).filter(s => s.enabled && s.url);
  return shops.map(s => {
    // Die iTunes-Store-Zeile ist an ihrer festen id erkennbar (siehe
    // config.py:defaults()) -- bei ausgewählter Music App und laufendem
    // Server bekommt sie statt eines einfachen Links den Preis-/Fund-Lookup
    // samt itmss:-Deep-Link (openStore()), sonst wie jeder andere Shop nur
    // s.url mit {q} ersetzt (music.apple.com/search, browserfähig).
    if (s.id === "itunes" && apiMode && MUSIC_NAME) {
      return `<button class="iconbtn plain" data-store="${esc(q)}"
                 title="${esc(t("shop.itunes_title", {q}))}">${shopIcon(s)}</button>`;
    }
    const url = s.id === "itunes" && !apiMode
      ? itunesURL(q)
      : s.url.split("{q}").join(encodeURIComponent(q));
    return `<a class="iconbtn plain" href="${esc(url)}" target="_blank" rel="noopener"
        title="${esc(t("shop.search_title", {shop: s.name, q}))}">${shopIcon(s)}</a>`;
  }).join("");
}

async function openStore(query, btn) {
  btn.classList.add("busy");
  try {
    const res = await fetch("/api/open-store", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({query: query})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    btn.classList.add("done");
    if (data.matched) {
      const preis = (data.price != null)
        ? ` · ${String(data.price).replace(".", ",")} ${data.currency}`
        : t("shop.itunes_album_only");
      note(t("shop.itunes_found", {artist: data.artist, track: data.track, price: preis}));
    } else {
      note(t("shop.itunes_not_found"), true);
    }
  } catch (err) {
    btn.classList.add("failed");
    note(t("shop.itunes_open_failed", {error: err.message}), true);
  }
  btn.classList.remove("busy");
  setTimeout(() => btn.classList.remove("done", "failed"), 1400);
}

const reasonsOf = r => {
  const measured = r.rs.length ? r.rs
    : [t("reason.cutoff_matches", {khz: String(r.co).replace(".", ",")})];
  // Bei manueller Korrektur bleibt die ursprüngliche Messung sichtbar, aber
  // klar als überstimmt gekennzeichnet — sonst steht eine FAKE-Begründung
  // kommentarlos unter einem „Korrekt"-Badge.
  return r.mc
    ? [t("reason.manual_override_prefix")].concat(measured)
    : measured;
};

// Verlustfreie Formate haben keine "Bitrate", die sich korrigieren liesse —
// r.mk ist dort nur ein informativer Naeherungswert fuer die Anzeige.
const isFixable = r => (r.v === "FAKE" || r.v === "VERDAECHTIG") && r.mk > 0 &&
  !r.mc && r.fam !== "lossless";

// Auffaelligkeiten (Metadaten-Probleme, unabhaengig vom Audio-Verdikt oben)
// -- Codes, die fix_safe() (app/taganomaly.py) in EINEM Schritt sicher ohne
// Nutzerabwaegung beheben kann. Der Rest bleibt manuell (Sprung in den
// Tags-Dialog), siehe openTagIssuesPopup().
const AUTO_FIXABLE_TAG_ISSUE_CODES = new Set([
  "control_chars", "leading_trailing_ws", "id3_v1_only", "id3_v22",
  "unresolved_genre_code", "duplicate_frames", "nfc_nfd_mismatch",
  "oversized_cover",
]);
const hasAnyTagIssues = r => Array.isArray(r.ti) && r.ti.length > 0;
const hasAutoFixableTagIssues = r =>
  Array.isArray(r.ti) && r.ti.some(i => AUTO_FIXABLE_TAG_ISSUE_CODES.has(i.code));
// Feldnamen von /api/fix-tag-issues (wie tags.write_tags()) auf die kompakten
// Zeilen-Kurzschluessel -- nur fuer den Drop-Zweig ohne DB-Zeile gebraucht,
// eine DB-Zeile kommt bereits kompakt vom Server (report.rows_to_payload()).
const _TAG_FIELD_TO_COMPACT = {artist: "a", title: "t", album: "al",
  album_artist: "aa", composer: "cp", genre: "ge", comment: "cm"};

// Manuell korrigiert heißt: die Messung lag daneben, der Track ist korrekt.
// Der gemessene Wert bleibt in r.v erhalten, damit sich die Markierung
// jederzeit zurücknehmen lässt — angezeigt wird aber das Urteil des Nutzers.
const verdictOf = r => r.mc ? "OK" : r.v;

const order = ["OK","VERDAECHTIG","FAKE","UNKLAR"];
let labels = {FAKE:t("verdict.fake"), VERDAECHTIG:t("verdict.suspicious"), OK:t("verdict.ok"), UNKLAR:t("verdict.unclear")};

// Icon je Verdikt -- dieselbe Zuordnung wie bei den Pruefliste-Symbolen in
// _SYSTEM_PLAYLISTS (db.py), hier zusaetzlich im Status-Abzeichen der
// Tabelle (siehe COLUMN_RENDERERS.v weiter unten). Die Faerbung bleibt
// allein Sache von .badge.<VERDIKT> (app.css) -- die SVGs erben currentColor.
const VERDICT_ICONS = {
  OK: SVG(LUCIDE_ICONS["badge-check"]),
  VERDAECHTIG: SVG(LUCIDE_ICONS["badge-alert"]),
  FAKE: SVG(LUCIDE_ICONS["badge-x"]),
  UNKLAR: SVG(LUCIDE_ICONS["badge-question-mark"]),
};

// Ausgeblendete zaehlen nirgends mit — das ist der Sinn der Funktion.
// Ersatzzeilen fremder Playlisten (ext:1, siehe EXT_SOURCES weiter oben)
// gehoeren nirgends dazu: sie beschreiben Tracks, die TrackTab gar nicht
// kennt, und wuerden Kennzahlen, Suche und Duplikaterkennung verfaelschen.
const live    = () => DATA.filter(r => !r.removed && !r.ext);
const counted = () => DATA.filter(r => !r.ig && !r.mc && !r.removed && !r.ext);

// --- Listenauswahl: Seitenbaum ---
// Die Liste entscheidet, welche Tracks ueberhaupt in Frage kommen; die
// Verdikt-Chips schraenken innerhalb dieser Liste weiter ein.
//
// Loest die fruehere Tab-Reihe ueber der Tabelle (#viewsRow) ab. Die Auswahl
// steuert unveraendert state.view und darueber currentView()/filtered() --
// nur die Darstellung ist eine andere. Anders als bei den Tabs ist sie in
// BEIDEN Ansichten (Bearbeiten/Player) vollstaendig sichtbar und gleich
// bedienbar; der frueher im Player reduzierte Satz ("Alle"/"Ausgeblendet"/
// Merklisten) entfaellt deshalb, und applyLayout() setzt state.view nicht
// mehr zwangsweise auf "all". Der Verdikt-Filter dagegen schon -- dessen
// Chips bleiben im Player verborgen und wuerden sonst stumm weiterfiltern.

// Symbol je Systemknoten. Merklisten bekommen statt eines Symbols ein
// abgerundetes Quadrat in ihrer Listenfarbe (.tbadge.tsquare), damit Baum und
// Zeilen-Badge (.flbtn/.fltag) dieselbe Form und Farbe zeigen.
const TREE_ICONS = {
  all: SVG(LUCIDE_ICONS.music), ignored: SVG(LUCIDE_ICONS.ban),
  gone: SVG(LUCIDE_ICONS["triangle-alert"]), duplicates: SVG(LUCIDE_ICONS.copy),
  corrected: SVG(LUCIDE_ICONS.check),
  "grp:genre": SVG(LUCIDE_ICONS.tag), "grp:album": SVG(LUCIDE_ICONS["disc-2"]),
  "grp:artist": SVG(LUCIDE_ICONS.speech),
  tag_issues: SVG(LUCIDE_ICONS["file-exclamation-point"]),
};

// Wurzelknoten des Baums. Vorerst nur die lokale Datenbank -- die Aeste fuer
// Music.app und Rekordbox kommen in eigenen Stufen dazu und tragen dann
// dieselbe Knotenform, damit renderTree() sich nicht aendern muss.
//
// Knotenform (fuer alle Quellen gleich):
//   id     eindeutig im Baum, Schluessel fuer state.treeClosed
//   view   state.view-Wert -- oder null bei Ordnern/Wurzel (nur auf/zu)
//   node   die Datenbankzeile bei eigenen Listen, sonst undefined
//   kids   Unterknoten
const PLAYLIST_ICONS = {playlist: "sym:list-music", folder: "sym:square-library", smart: "sym:settings"};

// Icon-Auswahl fuer Playlisten/Merklisten (Baum-Badge + Auswahl-Dialog) laeuft
// vollstaendig ueber das lokal vendorte Lucide-Lexikon LUCIDE_ICONS (siehe
// lucide-icons.js, vor app.js eingebettet, app/report.py::_template()) --
// jeder gueltige Lucide-Name (https://lucide.dev/icons/) funktioniert, nicht
// nur eine feste Auswahl. QUICK_PICK_ICONS ist nur die Vorauswahl fuer das
// Button-Raster im Dialog, keine eigene Icon-Definition.
const QUICK_PICK_ICONS = [
  "list-music", "star", "heart", "gem", "circle", "triangle", "square",
  "sparkle", "zap", "moon", "sun", "asterisk", "settings", "music",
  "snowflake", "plane", "cloud-rain", "medal", "luggage", "wrench",
  "activity", "volleyball", "dumbbell", "flag", "gift", "tent", "briefcase",
  "car", "headphones", "gamepad-2", "utensils",
];
// Alte, vor der Lucide-Umstellung gespeicherte Icons sind blanke Unicode-
// Zeichen ohne "sym:"/"emoji:"-Praefix -- Ruecklookup auf den passenden
// Lucide-Namen, damit bestehende Playlisten automatisch ihr Aequivalent
// bekommen statt des rohen Zeichens.
const LEGACY_GLYPH_TO_SYMBOL = {
  "☰": "list-music", "★": "star", "♥": "heart", "♦": "gem", "●": "circle",
  "▲": "triangle", "■": "square", "✦": "sparkle", "⚡": "zap", "☾": "moon",
  "☀": "sun", "✱": "asterisk", "⚙": "settings",
};

// Zerlegt den gespeicherten icon-String in {type: "sym"|"emoji", value}.
// null/leer -> null (kein eigenes Icon, Aufrufer nimmt den Kind-Standard).
// "sym" traegt direkt einen Lucide-Namen (Schluessel in LUCIDE_ICONS).
function parseIcon(icon) {
  if (!icon) return null;
  if (icon.startsWith("emoji:")) return {type: "emoji", value: icon.slice(6)};
  if (icon.startsWith("sym:")) return {type: "sym", value: icon.slice(4)};
  if (LEGACY_GLYPH_TO_SYMBOL[icon]) return {type: "sym", value: LEGACY_GLYPH_TO_SYMBOL[icon]};
  return {type: "emoji", value: icon};
}

// Markup fuer den Icon-Inhalt eines Baum-Badges (Kreis-Hintergrund traegt die
// Playlistfarbe, siehe renderTree()). Ein Emoji bringt seine eigene Farbe mit
// und bleibt deshalb unangetastet -- nur Symbole nehmen ueber "currentColor"
// die Vordergrundfarbe des Badges (weiss) an. Ein nicht (mehr) bekannter
// Lucide-Name (z.B. nach einem Umbenennen bei Lucide) liefert leeren Inhalt
// statt eines Fehlers -- der Farbkreis bleibt trotzdem sichtbar.
function iconGlyphHtml(icon) {
  const parsed = parseIcon(icon);
  if (!parsed) return "";
  if (parsed.type === "emoji") return `<span class="tsym temoji">${esc(parsed.value)}</span>`;
  const inner = LUCIDE_ICONS[parsed.value];
  if (!inner) return "";
  return `<span class="tsym tsvg">${SVG(inner)}</span>`;
}

// Icon-Wert einer Playlist/Smart-Playlist mit Standard, falls der Nutzer
// keins gewaehlt hat -- dieselbe Regel an jeder Stelle, die eine Liste ohne
// eigenes Icon zeigt (Baum, Kopfzeile, "Zur Playlist hinzufuegen"-Auswahl).
function playlistIconValue(node) {
  return node.icon || (node.kind === "smart" ? "sym:settings" : "sym:list-music");
}

// Markup fuer einen farbigen Icon-Kreis ausserhalb des Baums (Kopfzeile,
// Listen-Auswahl) -- dieselbe Optik wie im Baum (renderTree()): eckig statt
// rund, wenn die Playlist als Merkliste markiert ist (node.fav_slot), sonst
// rund wie jede andere eigene Liste.
function playlistBadgeHtml(node) {
  const color = node.color ? `var(--${esc(node.color)})` : "var(--dim)";
  const shape = node.fav_slot ? "tsquare" : "tcircle";
  return `<span class="tbadge ${shape}" style="background:${color}">` +
    `${iconGlyphHtml(playlistIconValue(node))}</span>`;
}


// Anzahl der Tracks einer Playlist, die es noch gibt -- eine in den
// Papierkorb gelegte Zeile (r.removed) zaehlt nicht mehr mit, sonst stuende
// im Baum eine andere Zahl als in der Tabelle.
function playlistCount(id) {
  const node = PLAYLISTS.find(n => n.id === id);
  const paths = node && node.kind === "smart" ? (SMART_CACHE.get(id) || []) : (PLAYLIST_ITEMS[id] || []);
  let n = 0;
  for (const path of paths) {
    const r = ROW_BY_PATH.get(path);
    if (r && !r.removed) n++;
  }
  return n;
}

// Kinder eines Ordners (parentId null = oberste Ebene). Die Tiefenbremse
// faengt einen durch fremde Eingriffe zyklisch gewordenen Baum ab -- der
// Server verhindert Zyklen beim Verschieben, eine Endlosschleife im Client
// waere hier aber ein weisses Fenster ohne Fehlermeldung.
function playlistChildren(parentId, depth = 0) {
  if (depth > 12) return [];
  return PLAYLISTS
    .filter(n => (n.parent_id || null) === (parentId || null))
    .map(n => ({
      id: `node:${n.id}`,
      view: n.kind === "playlist" ? `pl:${n.id}`
          : n.kind === "smart" ? `sm:${n.id}` : null,
      label: playlistLabel(n),
      icon: n.kind === "folder" ? PLAYLIST_ICONS.folder : playlistIconValue(n),
      color: n.color || null,
      // Feste Listen ("Pruefliste") lassen sich weder umbenennen noch
      // loeschen noch verschieben (der Server weist das ebenfalls ab, siehe
      // node.system in nodeMenuItems()) -- das Schloss-Symbol dafuer entfaellt
      // aber (Feedback: "Aufraeumen" ist genauso gesperrt und zeigt auch
      // keins).
      locked: false,
      node: n,
      count: n.kind === "folder" ? null : playlistCount(n.id),
      kids: playlistChildren(n.id, depth + 1),
    }));
}

// Baut die Kinder eines fremden Astes (Music.app/Rekordbox) aus der flachen
// Knotenliste der Quelle. Dieselbe Knotenform wie beim eigenen Ast, nur
// schreibgeschuetzt (readonly) -- renderTree() muss deshalb nichts ueber die
// Herkunft wissen.
function extChildren(source, parentId, depth = 0) {
  if (depth > 12) return [];
  const st = EXT_TREES[source];
  const prefix = EXT_SOURCES[source].prefix;
  return st.nodes
    .filter(n => (n.parent || null) === (parentId || null))
    .map(n => ({
      id: `${source}:${n.id}`,
      view: n.kind === "folder" ? null : `${prefix}${n.id}`,
      label: n.name,
      icon: PLAYLIST_ICONS[n.kind] || PLAYLIST_ICONS.playlist,
      // Smart Playlists der Fremdsysteme tragen zusaetzlich ein Schloss --
      // sie werden dort berechnet und liessen sich hier auch spaeter nicht
      // sinnvoll aendern.
      locked: true,
      count: n.kind === "folder" ? null : n.count,
      kids: extChildren(source, n.id, depth + 1),
    }));
}

function extRootNode(source, labelKey) {
  const st = EXT_TREES[source];
  const kids = st.loaded ? extChildren(source, null) : [];
  return {
    id: EXT_SOURCES[source].root, view: null, label: t(labelKey),
    icon: EXT_SOURCES[source].icon, count: null,
    // Solange nichts geladen ist, bekommt der Knoten trotzdem ein Kind-Signal,
    // damit der Aufklapp-Pfeil sichtbar ist und den Ladevorgang ausloest.
    lazy: source, loading: st.loading, error: st.error,
    kids: kids.length ? kids : (st.loaded ? [] : [{
      id: `${source}:__lade__`, view: null, label: st.loading
        ? t("tree.loading") : (st.error ? t("tree.load_failed", {error: st.error})
                                        : t("tree.expand_to_load")),
      icon: "", count: null, kids: [], hint: true,
    }]),
  };
}

// Sortiert die oberste Ebene nach state.treeOrder. Knoten, die dort (noch)
// nicht vorkommen -- eine gerade angelegte Liste, eine neu hinzugekommene
// Merkliste -- behalten ihre Ausgangsreihenfolge und haengen sich hinten an,
// statt an einer zufaelligen Stelle aufzutauchen.
function sortByTreeOrder(nodes) {
  const pos = new Map(state.treeOrder.map((id, i) => [id, i]));
  return nodes
    .map((n, i) => ({n, i, p: pos.has(n.id) ? pos.get(n.id) : Number.MAX_SAFE_INTEGER}))
    .sort((a, b) => (a.p - b.p) || (a.i - b.i))
    .map(x => x.n);
}

function treeRoots() {
  const sys = VIEW_TABS
    .map(id => VIEWS.find(v => v.id === id))
    .filter(Boolean)
    .map(v => ({
      id: `view:${v.id}`, view: v.id, label: v.label,
      icon: TREE_ICONS[v.id] || "&#8801;",
      count: live().filter(v.test).length,
      kids: [], top: true,
    }));
  // "Genre"/"Album"/"Kuenstler": auf derselben Ebene wie "Alle" -- rein
  // clientseitige Knoten wie zuvor im "Aufraeumen"-Ordner, es gibt (noch)
  // keinen Mechanismus fuer einen DB-losen Ordner um VIEWS-Eintraege.
  // groupEntryCount() zeigt die Anzahl UNTERSCHIEDLICHER Werte statt der
  // Trackzahl -- jede der drei Listen zeigt ohnehin die ganze Bibliothek,
  // nur gruppiert statt gefiltert, eine Trackzahl waere dort ohne Aussage.
  const groupNodes = ["grp:genre", "grp:album", "grp:artist"].map(id => {
    const v = VIEWS.find(x => x.id === id);
    return {id: `view:${id}`, view: id, label: v.label, icon: TREE_ICONS[id],
            count: groupEntryCount(GRP_VIEW_FIELD[id]), kids: [], top: true};
  });
  const own = playlistChildren(null).map(n => ({...n, top: true}));
  // "Duplikate" zieht in den "Pruefliste"-Ordner (sys_verdicts) -- wie die
  // drei Gruppen-Listen ein reiner VIEWS-Eintrag ohne eigene DB-Zeile, wird
  // deshalb clientseitig in den DB-gefuehrten Pruefliste-Ordner eingehaengt
  // statt ueber PLAYLISTS/_SYSTEM_PLAYLISTS.
  const dupView = VIEWS.find(v => v.id === "duplicates");
  const dupNode = {id: "view:duplicates", view: "duplicates", label: dupView.label,
                    icon: TREE_ICONS.duplicates, count: live().filter(dupView.test).length,
                    kids: [], top: true};
  // "Auffaelligkeiten" haengt wie "Duplikate" als reiner VIEWS-Eintrag ohne
  // eigene DB-Zeile im "Pruefliste"-Ordner (sys_verdicts).
  const tagIssuesView = VIEWS.find(v => v.id === "tag_issues");
  const tagIssuesNode = {id: "view:tag_issues", view: "tag_issues", label: tagIssuesView.label,
                          icon: TREE_ICONS.tag_issues, count: live().filter(tagIssuesView.test).length,
                          kids: [], top: true};
  const ownWithDuplicates = own.map(n =>
    n.node && n.node.id === "sys_verdicts" ? {...n, kids: [...n.kids, dupNode, tagIssuesNode]} : n);
  // Music.app/Rekordbox nur, wenn dort ueberhaupt ein Programm ausgewaehlt
  // bzw. gefunden ist (MUSIC_NAME/REKORDBOX_NAME) -- sonst ein Ast fuer ein
  // Programm, das der Nutzer nie eingerichtet hat. Untereinander per
  // state.treeOrder verschiebbar (dieselben ids wie ihre root:-Knoten,
  // siehe dropTreeNode()); TrackTab bleibt fix an erster Stelle, taucht in
  // dieser Liste deshalb gar nicht erst auf.
  const extRoots = sortByTreeOrder([
    ...(MUSIC_NAME ? [extRootNode("music", "tree.root_music")] : []),
    ...(REKORDBOX_NAME ? [extRootNode("rekordbox", "tree.root_rekordbox")] : []),
  ]);
  return [
    {id: "root:tracktab", view: null, label: t("tree.root_tracktab"), icon: "📁",
     count: null, kids: sortByTreeOrder([...sys, ...ownWithDuplicates, ...groupNodes])},
    ...extRoots,
  ];
}

// Bewusst als FLACHE Liste gerendert (Einrueckung ueber --depth in app.css),
// nicht als verschachtelte Container: so braucht das Verschieben per Drag &
// Drop keine DOM-Umhaengung zwischen Ebenen.
function renderTree() {
  const host = document.getElementById("tree");
  if (!host) return;
  const out = [];
  const walk = (n, depth) => {
    const hasKids = n.kids && n.kids.length > 0;
    const open = !state.treeClosed.includes(n.id);
    // Eigene Playlisten/Smart Playlists tragen ihre Farbe+Icon als farbiges
    // Abzeichen -- eckig statt rund, wenn die Playlist als Merkliste markiert
    // ist (n.node.fav_slot, siehe playlistBadgeHtml()), sonst rund. Fremde
    // Aeste (Music.app/Rekordbox) und Ordner behalten die schlichte
    // Textdarstellung.
    const isOwnList = n.node && (n.node.kind === "playlist" || n.node.kind === "smart");
    const badge = isOwnList
      ? `<span class="tbadge ${n.node.fav_slot ? "tsquare" : "tcircle"}" style="background:${n.color ? `var(--${esc(n.color)})` : "var(--dim)"}">` +
        // Feste Verdikt-Playlisten (system=1) tragen ihr Icon fest ueber
        // _SYSTEM_PLAYLISTS (db.py) -- nicht editierbar (nodeMenuItems()
        // blendet fuer sie "Bearbeiten" aus), aber genau wie bei eigenen
        // Listen gerendert.
        `${iconGlyphHtml(n.icon)}</span>`
      : `<span class="ticon"${n.color ? ` style="color:var(--${esc(n.color)})"` : ""}>` +
        // n.icon ist hier entweder fertiges Markup (TREE_ICONS/appIcon()) oder
        // ein "sym:"-codierter Lucide-Name (PLAYLIST_ICONS fuer Ordner/fremde
        // Playlisten/Smart Playlists) -- anders als bei eigenen Listen (oben,
        // isOwnList) kein iconGlyphHtml(), weil das einen Farbkreis erzeugen
        // wuerde, den Ordner/fremde Aeste nicht haben sollen.
        `${n.icon && n.icon.startsWith("sym:") ? SVG(LUCIDE_ICONS[n.icon.slice(4)] || "") : n.icon}</span>`;
    out.push(
      `<button class="treenode${depth === 0 ? " root" : ""}` +
      `${n.hint ? " hint" : ""}${n.locked ? " locked" : ""}` +
      `${n.view && state.view === n.view ? " on" : ""}" ` +
      `data-id="${esc(n.id)}"${n.view ? ` data-view="${esc(n.view)}"` : ""}` +
      `${n.node ? ` data-pl="${esc(n.node.id)}"` : ""}` +
      `${n.top ? ' data-top="1"' : ""}` +
      `${n.lazy ? ` data-lazy="${esc(n.lazy)}"` : ""} style="--depth:${depth}">` +
      `<span class="twist${hasKids ? (open ? " open" : "") : " leaf"}"` +
      `${hasKids ? " data-twist=\"1\"" : ""}>&#9654;</span>` +
      badge +
      `<span class="tlabel">${esc(n.label)}</span>` +
      (n.count == null ? "" : `<span class="tcount">${n.count.toLocaleString("de-DE")}</span>`) +
      (n.locked ? `<span class="tlock" title="${esc(t("tree.readonly"))}">${ICONS.lock}</span>` : "") +
      (n.node && apiMode && nodeMenuItems(n.node).length
        ? `<span class="tmenu" data-nodemenu="${esc(n.node.id)}">&#8943;</span>`
        // Musik.app/Rekordbox-Wurzelknoten tragen .lazy (siehe extRootNode())
        // -- eigenes Menue mit "Neu laden", da diese Baeste nach dem ersten
        // Laden sonst nie von selbst aktualisieren (st.loaded bleibt stehen).
        : n.lazy && apiMode
        ? `<span class="tmenu" data-extmenu="${esc(n.lazy)}">&#8943;</span>`
        // Feste View-Eintraege ohne eigene Playlist-Zeile (Alle/Ausgeblendet/
        // Datei fehlt/Duplikate/Merklisten) -- n.top grenzt sie von den
        // Musik.app/Rekordbox-Fremdaesten ab, die ebenfalls .view aber kein
        // .top tragen und bewusst ohne dieses Menue bleiben.
        : n.view && !n.node && n.top && apiMode
        ? `<span class="tmenu" data-viewmenu="${esc(n.view)}">&#8943;</span>` : "") +
      `</button>`);
    if (hasKids && open) for (const kid of n.kids) walk(kid, depth + 1);
  };
  for (const root of treeRoots()) walk(root, 0);
  host.innerHTML = out.join("");
  renderPlaylistHeader();

  host.querySelectorAll(".treenode").forEach(b => b.onclick = ev => {
    if (ev.target.closest("[data-nodemenu], [data-viewmenu]")) return;   // eigener Handler
    // Ein Knoten ohne View (Wurzel, Ordner) kennt nur auf/zu; bei einem mit
    // View oeffnet nur der Pfeil, alles andere waehlt aus.
    if (!b.dataset.view || ev.target.closest("[data-twist]")) {
      toggleTreeNode(b.dataset.id);
      // Ein fremder Ast holt seinen Inhalt erst hier -- beim Zuklappen
      // bewusst nicht erneut (der Zustand bleibt gecacht).
      if (b.dataset.lazy && !state.treeClosed.includes(b.dataset.id)) {
        loadExtTree(b.dataset.lazy);
      }
      return;
    }
    selectView(b.dataset.view);
  });
  host.querySelectorAll("[data-nodemenu]").forEach(el => el.onclick = ev => {
    ev.stopPropagation();
    const node = PLAYLISTS.find(n => n.id === el.dataset.nodemenu);
    if (node) openNodeMenu(node, el);
  });
  host.querySelectorAll("[data-viewmenu]").forEach(el => el.onclick = ev => {
    ev.stopPropagation();
    const view = VIEWS.find(v => v.id === el.dataset.viewmenu);
    if (view) openViewMenu(view, el);
  });
  host.querySelectorAll("[data-extmenu]").forEach(el => el.onclick = ev => {
    ev.stopPropagation();
    openExtMenu(el.dataset.extmenu, el);
  });
  wireTreeDnd(host);
}

// ── Drag & Drop ───────────────────────────────────────────────────────────
// Eigene Datentypen statt "text/plain": initDropzone() weiter unten haengt an
// window und prueft auf dataTransfer.types "Files" -- ein interner Zug traegt
// keine Dateien und loest die Datei-Ueberlagerung deshalb nicht aus. Zwei
// getrennte Typen, damit ein Knoten-Zug nie als Track-Zug missverstanden wird
// (beim dragover ist der INHALT noch nicht lesbar, nur die Typliste).
const DND_TRACKS = "application/x-tracktab-tracks";
const DND_NODE = "application/x-tracktab-node";

// Kompaktes Zug-Abbild fuer setDragImage() statt der ganzen Tabellenzeile
// (die als Standard-Abbild die Ablageziele darunter verdeckt). EIN einziges,
// dauerhaft im DOM haengendes Element -- waere es erst im dragstart-Handler
// per appendChild eingefuegt, brechen manche WebKit-Versionen (Safari) die
// gesamte native Ziehgeste sofort und lautlos ab, sobald der Handler den DOM-
// Baum veraendert (dragover/drop feuern dann nie). Nur der INHALT wird bei
// jedem Zug aktualisiert, das Element selbst bleibt bestehen.
const dragBadgeEl = document.createElement("div");
dragBadgeEl.className = "drag-badge";
document.body.appendChild(dragBadgeEl);

function buildDragBadge(r, extraCount) {
  dragBadgeEl.innerHTML = `${(apiMode && !r.gone && r.cv)
      ? `<img class="drag-badge-cover" src="${coverUrl(r.p)}" alt="">`
      : `<div class="drag-badge-cover"></div>`}
    <span class="drag-badge-title">${esc(r.t || baseName(r.p))}</span>
    ${extraCount > 0 ? `<span class="drag-badge-count">+${extraCount}</span>` : ""}`;
  return dragBadgeEl;
}

function clearTreeDropMarks() {
  document.querySelectorAll(".treenode.dropinto, .treenode.dropbefore, .treenode.dropafter")
    .forEach(el => el.classList.remove("dropinto", "dropbefore", "dropafter"));
}

// Wohin wuerde die Ablage an dieser Stelle fuehren? "into" nur dort, wo der
// Knoten wirklich aufnehmen kann: Tracks in eine Playlist, Knoten in einen
// Ordner (oder auf die Wurzel = oberste Ebene). Sonst entscheidet die Haelfte
// des Knotens ueber davor/dahinter -- das uebliche Baumverhalten.
function treeDropTarget(btn, ev, kind) {
  const node = btn.dataset.pl ? PLAYLISTS.find(n => n.id === btn.dataset.pl) : null;
  if (kind === "tracks") {
    return (node && node.kind === "playlist") ? "into" : null;
  }
  if (btn.dataset.id === "root:tracktab") return "into";
  // Knoten der obersten Ebene ohne Datenbankzeile (Alle, Ausgeblendet, ...)
  // nehmen zwar nichts auf, koennen aber eine Position abgeben -- deshalb
  // zaehlen sie hier als gueltiges Ziel fuer davor/dahinter. Die fremden
  // Wurzeln (data-lazy, siehe extRootNode()) ebenso, damit sich Music.app/
  // Rekordbox untereinander verschieben lassen -- ob der jeweilige Zug dafuer
  // gueltig ist (nur Wurzel auf Wurzel), entscheidet erst dropTreeNode() beim
  // eigentlichen Ablegen, hier ist beim dragover die gezogene id noch nicht
  // lesbar (Browser-Beschraenkung von dataTransfer).
  if (!node && btn.dataset.top !== "1" && !btn.dataset.lazy) return null;
  const rect = btn.getBoundingClientRect();
  const rel = (ev.clientY - rect.top) / rect.height;
  if (node && node.kind === "folder") {
    if (rel < 0.25) return "before";
    if (rel > 0.75) return "after";
    return "into";
  }
  return rel < 0.5 ? "before" : "after";
}

function dragKindOf(ev) {
  const types = ev.dataTransfer.types || [];
  if ([...types].includes(DND_TRACKS)) return "tracks";
  if ([...types].includes(DND_NODE)) return "node";
  return null;
}

// Haengt die Zieh-Handler an die gerade gerenderten Baumknoten. Wird von
// renderTree() nach jedem Neuaufbau aufgerufen -- der Baum ersetzt sein
// innerHTML komplett, Handler ueberleben das nicht.
function wireTreeDnd(host) {
  host.querySelectorAll(".treenode").forEach(btn => {
    const node = btn.dataset.pl ? PLAYLISTS.find(n => n.id === btn.dataset.pl) : null;
    // Ziehbar ist alles unter TrackTab: eigene Listen, die festen
    // Status-Listen (nur Name/Regeln/Loeschen sind dort gesperrt, die Position
    // gehoert dem Nutzer) und die festen Ansichten der obersten Ebene. Dazu
    // die fremden Wurzeln selbst (data-lazy) -- Music.app/Rekordbox lassen
    // sich untereinander verschieben, TrackTab bleibt aussen vor (kein
    // data-lazy, keine feste id in dieser Bedingung) und damit immer erster.
    // Uebergeben wird die BAUM-Kennung (view:.../node:.../root:...), nicht
    // die Datenbank-id -- nur so laesst sich beim Ablegen unterscheiden, was
    // da gezogen wurde.
    if ((node || btn.dataset.top === "1" || btn.dataset.lazy) && apiMode) {
      btn.draggable = true;
      btn.ondragstart = ev => {
        ev.dataTransfer.setData(DND_NODE, btn.dataset.id);
        ev.dataTransfer.effectAllowed = "move";
      };
      btn.ondragend = () => clearTreeDropMarks();
    }
    btn.ondragover = ev => {
      const kind = dragKindOf(ev);
      if (!kind || !apiMode) return;
      const where = treeDropTarget(btn, ev, kind);
      if (!where) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = kind === "tracks" ? "copy" : "move";
      clearTreeDropMarks();
      btn.classList.add(where === "into" ? "dropinto"
                        : where === "before" ? "dropbefore" : "dropafter");
    };
    btn.ondragleave = ev => {
      if (!btn.contains(ev.relatedTarget)) {
        btn.classList.remove("dropinto", "dropbefore", "dropafter");
      }
    };
    btn.ondrop = ev => {
      const kind = dragKindOf(ev);
      if (!kind || !apiMode) return;
      const where = treeDropTarget(btn, ev, kind);
      clearTreeDropMarks();
      if (!where) return;
      ev.preventDefault();
      if (kind === "tracks") {
        let paths = [];
        try { paths = JSON.parse(ev.dataTransfer.getData(DND_TRACKS) || "[]"); }
        catch (e) { paths = []; }
        dropTracksOnPlaylist(btn.dataset.pl, paths);
      } else {
        dropTreeNode(ev.dataTransfer.getData(DND_NODE), btn, where);
      }
    };
  });
}

async function dropTracksOnPlaylist(playlistId, paths) {
  const node = PLAYLISTS.find(n => n.id === playlistId);
  if (!node || !paths.length) return;
  const before = [...(PLAYLIST_ITEMS[playlistId] || [])];
  try {
    const data = await playlistApi("/api/playlist-items",
      {op: "add", id: playlistId, paths: paths});
    pushUndo(playlistId, t("undo.label_add"), before, data.items);
    await reloadPlaylists();
    updateCards(); render();
    if (!data.changed) {
      note(t("tree.drop_all_known", {name: node.name}), "soft");
    } else {
      note(t("tree.drop_added", {
        count: data.changed, name: node.name,
        track_word: data.changed === 1 ? t("tree.track_singular") : t("tree.track_plural"),
      }));
    }
  } catch (err) {
    note(t("tree.drop_failed", {error: err.message}), true);
  }
}

function clearRowDropMarks() {
  document.querySelectorAll("tr.row.dropbefore, tr.row.dropafter")
    .forEach(el => el.classList.remove("dropbefore", "dropafter"));
}

// Tracks innerhalb einer Playlist an eine andere Stelle setzen. Gerechnet
// wird auf der VOLLSTAENDIGEN Reihenfolge aus PLAYLIST_ITEMS, nicht auf den
// gerade sichtbaren Zeilen -- eine Suche oder ein noch nicht nachgeladener
// Rest der Liste wuerde sonst beim Speichern verschwinden.
async function reorderInPlaylist(playlistId, paths, targetPath, where) {
  if (!paths.length || paths.includes(targetPath)) return;
  // Wer zieht, will die eigene Reihenfolge sehen -- eine noch aktive
  // Spaltensortierung wuerde das Ergebnis sofort wieder verdecken.
  if (state.sort) { state.sort = null; syncSortHeaders(); saveFilters(); }
  const order = (PLAYLIST_ITEMS[playlistId] || []).filter(p => !paths.includes(p));
  let at = order.indexOf(targetPath);
  if (at === -1) at = order.length;
  else if (where === "after") at += 1;
  const before = [...(PLAYLIST_ITEMS[playlistId] || [])];
  order.splice(at, 0, ...paths);
  try {
    const data = await playlistApi("/api/playlist-items",
      {op: "set", id: playlistId, paths: order});
    pushUndo(playlistId, t("undo.label_reorder"), before, data.items);
    await reloadPlaylists();
    render();
  } catch (err) {
    note(t("tree.reorder_failed", {error: err.message}), true);
  }
}

// ── Fremde Playlisten: Music.app und Rekordbox ────────────────────────────
// Beide Aeste sind schreibgeschuetzt (Stufe 1 der Integration): angezeigt
// wird, was dort steht, geaendert wird dort nichts. Geladen wird erst beim
// Aufklappen -- der Music-Ast laeuft ueber AppleScript und wuerde Music.app
// starten, der Rekordbox-Ast oeffnet master.db; beides soll nicht als
// Nebenwirkung eines Seitenaufrufs passieren.
//
// Tracks, die TrackTab nicht kennt (Apple-Music-Cloud ohne lokale Datei, oder
// eine Datei ausserhalb der gescannten Ordner), bekommen eine Ersatzzeile in
// DATA mit dem Merkmal ext:1. Ohne sie liesse sich die Reihenfolge der Liste
// nicht zeigen -- render() geht ueber DATA und kennt nur Zeilen mit Index.
// ext-Zeilen sind ueberall sonst ausgeschlossen (live(), counted(),
// filtered()), damit sie weder Kennzahlen noch Suche noch Duplikaterkennung
// verfaelschen.
const EXT_SOURCES = {
  // Echtes Programmsymbol statt Lucide-Icon: die beiden Wurzelknoten stehen
  // fuer ein konkretes fremdes Programm, dessen Wiedererkennungswert hier
  // mehr zaehlt als eine einheitliche Icon-Sprache (appIcon() holt es ueber
  // /api/appicon, siehe Kommentar dort -- faellt bei fehlender Installation
  // automatisch auf ICONS.editor zurueck).
  music: {root: "root:music", prefix: "mu:", icon: appIcon("music", "Music App"),
          tree: "/api/music-playlists", tracks: "/api/music-playlist"},
  rekordbox: {root: "root:rekordbox", prefix: "rb:", icon: appIcon("rekordbox", "Rekordbox"),
              tree: "/api/rekordbox-playlists", tracks: "/api/rekordbox-playlist"},
};

// Zustand je Quelle: geladene Knoten, Ladezustand, letzter Fehler.
const EXT_TREES = {
  music: {nodes: [], loaded: false, loading: false, error: null},
  rekordbox: {nodes: [], loaded: false, loading: false, error: null},
};
// Inhalt je fremder Playlist: {paths: [...], unsupported: bool}. Der Schluessel
// ist "<quelle>:<id>", damit sich die IDs beider Systeme nicht ins Gehege
// kommen.
const EXT_CONTENTS = new Map();

function extSourceOfView(viewId) {
  for (const [key, src] of Object.entries(EXT_SOURCES)) {
    if (String(viewId).startsWith(src.prefix)) return key;
  }
  return null;
}

async function loadExtTree(source) {
  const st = EXT_TREES[source];
  if (st.loading || st.loaded) return;
  st.loading = true;
  st.error = null;
  renderTree();
  try {
    const res = await fetch(EXT_SOURCES[source].tree, {cache: "no-store"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    st.nodes = data.playlists || [];
    st.loaded = true;
    // Ordner der Fremdsysteme starten zugeklappt. An echtem Material bringt
    // Rekordbox 993 Knoten in Tiefe 4 mit -- alles aufgeklappt ergaebe eine
    // ueber 30.000 px hohe Leiste, durch die niemand scrollen will. Nur
    // Knoten ergaenzen, die noch nicht vermerkt sind, damit eine vom Nutzer
    // aufgeklappte Ebene ein Neuladen ueberlebt (state.treeClosed liegt im
    // localStorage).
    const bekannt = new Set(state.treeClosed);
    for (const n of st.nodes) {
      if (n.kind !== "folder") continue;
      const key = `${source}:${n.id}`;
      if (!bekannt.has(key) && !st.everLoaded) state.treeClosed.push(key);
    }
    st.everLoaded = true;
    saveFilters();
  } catch (err) {
    st.error = err.message;
  } finally {
    st.loading = false;
    renderTree();
  }
}

// Legt fuer einen Track ohne Datenbankzeile eine Ersatzzeile an und liefert
// deren Index. Wiederverwendung ueber den Pfad (bzw. bei Cloud-Tracks ueber
// Quelle+Kennung), damit dieselbe Datei in zwei Listen nicht zweimal in DATA
// landet.
const extRowIndex = new Map();
function ensureExtRow(source, track) {
  const key = track.path
    ? `p:${track.path}`
    : `${source}:${track.id || (track.artist + "|" + track.title)}`;
  if (extRowIndex.has(key)) return extRowIndex.get(key);
  const row = {
    i: DATA.length, p: track.path || "", ext: 1, gone: 1,
    a: track.artist || "", t: track.name || track.title || "",
    al: "", aa: "", cp: "", ge: "", cm: "", yr: 0, bp: 0, tn: 0, tt: 0, cv: 0,
    v: "UNKLAR", fam: "", cd: "", kb: 0, mk: 0, co: 0, st: 0, bw: 0, cf: 0,
    du: 0, sr: 0, mo: "", en: "", lp: 0, lu: 0, tp: 0, lra: 0, sz: 0, hs: "",
    ig: 0, f1: 0, f2: 0, f3: 0, mc: 0, rb: 0, im: 0, da: 0,
    extSource: source,
  };
  DATA.push(row);
  extRowIndex.set(key, row.i);
  return row.i;
}

async function loadExtPlaylist(source, playlistId) {
  const key = `${source}:${playlistId}`;
  if (EXT_CONTENTS.has(key)) return EXT_CONTENTS.get(key);
  const res = await fetch(
    `${EXT_SOURCES[source].tracks}?id=${encodeURIComponent(playlistId)}`,
    {cache: "no-store"});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  const paths = [];
  for (const track of (data.tracks || [])) {
    if (track.known && track.path) {
      const row = ROW_BY_PATH.get(track.path);
      if (row) { paths.push(row.p); continue; }
    }
    const idx = ensureExtRow(source, track);
    paths.push(DATA[idx].p || `\u0000ext${idx}`);   // Ersatzschluessel ohne Pfad
    DATA[idx].extKey = paths[paths.length - 1];
  }
  const entry = {paths, set: new Set(paths),
                 unsupported: !!data.unsupported, error: data.error || null};
  EXT_CONTENTS.set(key, entry);
  return entry;
}

// Legt den View einer fremden Playlist an, sobald sie zum ersten Mal geoeffnet
// wird. Bewusst nicht alle im Voraus: Rekordbox bringt an echtem Material
// knapp 1000 Knoten mit, und ein View je Knoten waere Ballast fuer eine
// Liste, die vielleicht nie angeklickt wird.
function ensureExtView(viewId) {
  if (VIEWS.some(v => v.id === viewId)) return;
  const source = extSourceOfView(viewId);
  if (!source) return;
  const id = viewId.slice(EXT_SOURCES[source].prefix.length);
  const key = `${source}:${id}`;
  const node = EXT_TREES[source].nodes.find(n => String(n.id) === id);
  VIEWS.push({
    id: viewId, label: node ? node.name : viewId,
    ext: true, extKey: key,
    test: r => { const e = EXT_CONTENTS.get(key); return !!e && e.set.has(extRowKey(r)); },
  });
}

// Schluessel einer Zeile fuer die Reihenfolge in einer fremden Liste --
// Cloud-Tracks haben keinen Pfad und tragen stattdessen ihren Ersatzschluessel.
const extRowKey = r => r.extKey || r.p;

// Zwei fertig gebaute Collatoren fuer alle Textvergleiche der Tabelle.
// Ein Collator im Vergleicher gebaut zu bekommen ist der teuerste einzelne
// Fehler in dieser Datei -- und er sieht harmlos aus:
//   localeCompare(a, b, "de")            -> V8 merkt sich den Collator fuer
//                                           wiederholte gleiche Aufrufe,
//                                           halbwegs billig
//   localeCompare(a, b, undefined, {..}) -> jedes frische Optionsobjekt
//                                           erzwingt einen NEUEN Collator,
//                                           je Vergleich
// An echtem Material (10.822 Zeilen, in Chrome gemessen): die zweite Form in
// fieldValuesForAutocomplete() kostete 161 ms je Tastendruck, jetzt 8 ms.
// Die Sortierreihenfolge aendert sich nicht -- Intl.Collator("de") ist genau
// das, was localeCompare(a, b, "de") intern benutzt, nur einmal gebaut.
// Nachmessen bitte im Browser: JavaScriptCore (jsc) kennt die Wiederverwendung
// der ersten Form nicht und uebertreibt den Gewinn um mehr als das Zehnfache.
const COLLATOR = new Intl.Collator("de");
const cmpText = (a, b) => COLLATOR.compare(a, b);
// Eigene Einstellung fuer Vorschlagslisten: Gross/Klein und Akzente egal,
// Zahlen numerisch ("Track 2" vor "Track 10").
const COLLATOR_LOOSE = new Intl.Collator(undefined, {sensitivity: "base", numeric: true});

// Sortierschluessel EINMAL je Zeile berechnen statt in jedem Vergleich neu
// (Schwartzian Transform). Wichtig ueberall dort, wo der Schluessel selbst
// Arbeit kostet -- displayName()+toLowerCase(), albumGroupKey(), extRowKey().
// Gemessen: Sortierung nach "Datei" 54 -> 32 ms bei 10.822 Zeilen.
function sortKeys(rows, keyFn) {
  const m = new Map();
  for (const r of rows) m.set(r, keyFn(r));
  return m;
}

// ── Smart Playlists ───────────────────────────────────────────────────────
// Regelwerk als JSON in playlists.rules:
//   {match: "all"|"any", rules: [{field, op, value}], limit: {...}}
//
// Ausgewertet wird ausschliesslich hier im Client -- DATA liegt vollstaendig
// im Browser, ein Umweg ueber die Datenbank waere ein Rundlauf je Klick ohne
// jeden Gewinn. Die Feldliste ist bewusst dieselbe wie in der Suche
// (SEARCH_FIELD_ALIASES/FIELD_LABELS weiter unten), erweitert um das, was die
// Suche nicht kennt: Verdikt, Format und die Markierungs-Schalter. Zwei
// getrennte Vokabulare wuerden bei jeder neuen Spalte auseinanderlaufen.
//
// Die Neuberechnung laeuft NICHT bei jedem render(): beim Start einmal und
// danach, sobald ein Smart-Knoten angeklickt oder sein Regelwerk geaendert
// wird. Bei ueber 10.000 Zeilen und mehreren Listen waere ein Durchlauf je
// Neuaufbau der Tabelle spuerbar, und der Inhalt aendert sich ohnehin nur,
// wenn sich Messwerte oder Markierungen aendern.

const SMART_TYPES = {TEXT: "text", NUM: "num", ENUM: "enum", BOOL: "bool", DATE: "date"};

// Feldkatalog. 'key' ist das Feld der kompakten Zeile (siehe
// report.rows_to_payload), 'label' kommt wo moeglich aus FIELD_LABELS, damit
// Suche und Regel-Editor dieselben Bezeichnungen zeigen.
function smartFields() {
  return [
    {key: "v",   type: SMART_TYPES.ENUM, label: t("col.status"),
     options: () => ["OK", "VERDAECHTIG", "FAKE", "UNKLAR"].map(v => ({v, l: labels[v]}))},
    {key: "fam", type: SMART_TYPES.ENUM, label: t("smart.field_format"),
     options: () => [
       {v: "mp3", l: "MP3"}, {v: "aac", l: "AAC"},
       {v: "wav", l: "WAV"}, {v: "aiff", l: "AIFF"},
       {v: "flac", l: "FLAC"}, {v: "alac", l: "ALAC"},
       {v: "other", l: t("smart.format_other")}]},
    {key: "a",  type: SMART_TYPES.TEXT, label: FIELD_LABELS.a},
    {key: "t",  type: SMART_TYPES.TEXT, label: FIELD_LABELS.t},
    {key: "al", type: SMART_TYPES.TEXT, label: FIELD_LABELS.al},
    {key: "aa", type: SMART_TYPES.TEXT, label: FIELD_LABELS.aa},
    {key: "cp", type: SMART_TYPES.TEXT, label: FIELD_LABELS.cp},
    {key: "ge", type: SMART_TYPES.TEXT, label: FIELD_LABELS.ge},
    {key: "cm", type: SMART_TYPES.TEXT, label: FIELD_LABELS.cm},
    {key: "p",  type: SMART_TYPES.TEXT, label: FIELD_LABELS.p},
    {key: "yr", type: SMART_TYPES.NUM,  label: FIELD_LABELS.yr},
    {key: "bp", type: SMART_TYPES.NUM,  label: FIELD_LABELS.bp},
    {key: "du", type: SMART_TYPES.NUM,  label: FIELD_LABELS.du, unit: t("smart.unit_seconds")},
    {key: "kb", type: SMART_TYPES.NUM,  label: FIELD_LABELS.kb, unit: "kbps"},
    {key: "mk", type: SMART_TYPES.NUM,  label: t("smart.field_measured"), unit: "kbps"},
    {key: "co", type: SMART_TYPES.NUM,  label: t("col.cutoff"), unit: "kHz"},
    {key: "cf", type: SMART_TYPES.NUM,  label: FIELD_LABELS.cf},
    {key: "lu", type: SMART_TYPES.NUM,  label: t("col.loudness"), unit: "LUFS"},
    {key: "sz", type: SMART_TYPES.NUM,  label: t("smart.field_size"), unit: "MB", scale: 1048576},
    {key: "da", type: SMART_TYPES.DATE, label: FIELD_LABELS.da},
    {key: "mc", type: SMART_TYPES.BOOL, label: t("chip.manual_corrected")},
    {key: "ig", type: SMART_TYPES.BOOL, label: t("views.ignored")},
    {key: "im", type: SMART_TYPES.BOOL, label: t("smart.field_in_music")},
    {key: "rb", type: SMART_TYPES.BOOL, label: t("smart.field_in_rekordbox")},
  ];
}

const SMART_OPS = {
  [SMART_TYPES.TEXT]: ["contains", "not_contains", "is", "is_not", "starts", "ends"],
  [SMART_TYPES.NUM]: ["eq", "ne", "gt", "lt"],
  [SMART_TYPES.ENUM]: ["is", "is_not"],
  [SMART_TYPES.BOOL]: ["is"],
  [SMART_TYPES.DATE]: ["in_last_days", "before_days", "after_days"],
};

function smartFieldByKey(key) {
  return smartFields().find(f => f.key === key) || null;
}

// Eine einzelne Regel gegen eine Zeile pruefen. "enthaelt" nutzt bewusst
// dieselbe unscharfe Suche wie das Suchfeld (fuzzyIncludes, Toleranz aus den
// Einstellungen) -- wer im Suchfeld einen Treffer bekommt, soll ihn in einer
// Regel mit demselben Wort auch bekommen. Fuer den genauen Fall gibt es
// daneben "ist".
// Feineres Format fuer die Regel "fam": r.fam (codec_family) unterscheidet
// verlustfreie Formate NICHT voneinander (WAV/AIFF/FLAC/ALAC sind dort alle
// "lossless") -- fuer eine Regel muss das aber gehen, deshalb zusaetzlich
// per Dateiendung aufgeloest.
function smartFormatOf(r) {
  if (r.fam === "lossy_mp3") return "mp3";
  if (r.fam === "lossy_aac") return "aac";
  if (r.fam !== "lossless") return "other";
  const ext = (String(r.p || "").split(".").pop() || "").toLowerCase();
  if (ext === "wav") return "wav";
  if (ext === "aiff" || ext === "aif") return "aiff";
  if (ext === "flac") return "flac";
  if (ext === "m4a" || ext === "mp4") return "alac";
  return "other";
}

// Nur AIFF betroffen (siehe CAN_PLAY_AIFF) -- alle anderen Formate spielt
// jeder unterstuetzte Browser ab.
const rowUnplayable = r => !CAN_PLAY_AIFF && smartFormatOf(r) === "aiff";

// Vor der Aufteilung des Format-Felds in mp3/aac/wav/aiff/flac/alac/other
// (siehe smartFormatOf) speicherte eine Regel dort noch die rohen
// codec_family-Werte als rule.value -- eine bereits gespeicherte Smart
// Playlist soll nach diesem Update nicht stillschweigend leer laufen.
const SMART_FORMAT_LEGACY = {lossy_mp3: "mp3", lossy_aac: "aac", lossy_other: "other"};

function evalSmartRule(r, rule) {
  const field = smartFieldByKey(rule.field);
  if (!field) return true;                       // unbekanntes Feld nicht filtern
  const raw = r[rule.field];
  if (field.type === SMART_TYPES.BOOL) {
    return (!!raw) === (String(rule.value) === "1");
  }
  if (field.type === SMART_TYPES.ENUM) {
    if (rule.field === "fam" && rule.value === "lossless") {
      const hit = r.fam === "lossless";
      return rule.op === "is_not" ? !hit : hit;
    }
    const value = rule.field === "v" ? verdictOf(r)
                : rule.field === "fam" ? smartFormatOf(r)
                : String(raw ?? "");
    const want = rule.field === "fam" && SMART_FORMAT_LEGACY[rule.value]
      ? SMART_FORMAT_LEGACY[rule.value] : rule.value;
    return rule.op === "is_not" ? value !== want : value === want;
  }
  if (field.type === SMART_TYPES.NUM) {
    const num = Number(raw);
    const want = Number(rule.value) * (field.scale || 1);
    if (!isFinite(num) || !isFinite(want)) return false;
    if (rule.op === "gt") return num > want;
    if (rule.op === "lt") return num < want;
    if (rule.op === "ne") return num !== want;
    return num === want;
  }
  if (field.type === SMART_TYPES.DATE) {
    // 'da' ist ein Unix-Zeitstempel (Music.app "hinzugefuegt"), 0 = unbekannt.
    const ts = Number(raw) || 0;
    if (!ts) return false;
    const days = Number(rule.value);
    if (!isFinite(days)) return false;
    const grenze = Date.now() / 1000 - days * 86400;
    if (rule.op === "before_days") return ts < grenze;
    return ts >= grenze;                          // in_last_days / after_days
  }
  const text = String(raw ?? "").toLowerCase();
  const want = String(rule.value ?? "").toLowerCase();
  if (rule.op === "is") return text === want;
  if (rule.op === "is_not") return text !== want;
  if (rule.op === "starts") return text.startsWith(want);
  if (rule.op === "ends") return text.endsWith(want);
  const hit = fuzzyIncludes(text, want);
  return rule.op === "not_contains" ? !hit : hit;
}

function parseSmartRules(node) {
  if (!node || !node.rules) return null;
  try {
    const spec = typeof node.rules === "string" ? JSON.parse(node.rules) : node.rules;
    if (!spec || !Array.isArray(spec.rules)) return null;
    return spec;
  } catch (e) { return null; }
}

// Ergebnis je Smart Playlist: Menge der passenden Pfade. Wird von den
// VIEWS-Praedikaten und von der Trackzahl im Baum gelesen.
let SMART_CACHE = new Map();

function recomputeSmart(playlistId) {
  const node = PLAYLISTS.find(n => n.id === playlistId);
  const spec = parseSmartRules(node);
  if (!spec) { SMART_CACHE.set(playlistId, new Set()); return; }
  const rules = spec.rules.filter(x => x && x.field);
  const any = spec.match === "any";
  let rows = DATA.filter(r => {
    if (r.removed) return false;
    if (!rules.length) return true;              // ohne Regel: alles
    return any ? rules.some(x => evalSmartRule(r, x))
               : rules.every(x => evalSmartRule(r, x));
  });
  rows = applySmartLimit(rows, spec.limit);
  SMART_CACHE.set(playlistId, new Set(rows.map(r => r.p)));
}

// Begrenzung wie in Apple Music: Anzahl, Spielzeit oder Speichergroesse,
// ausgewaehlt nach einem Kriterium. Die Auswahl wird VOR dem Abschneiden
// sortiert, sonst haenge das Ergebnis an der zufaelligen Reihenfolge in DATA.
function applySmartLimit(rows, limit) {
  if (!limit || !limit.enabled) return rows;
  const value = Number(limit.value);
  if (!isFinite(value) || value <= 0) return rows;
  const sorted = [...rows];
  if (limit.by === "random") {
    for (let i = sorted.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [sorted[i], sorted[j]] = [sorted[j], sorted[i]];
    }
  } else if (limit.by === "added") {
    sorted.sort((a, b) => (b.da || 0) - (a.da || 0));
  } else if (limit.by === "cutoff") {
    sorted.sort((a, b) => (a.co || 0) - (b.co || 0));
  } else if (limit.by === "artist") {
    sorted.sort((a, b) => cmpText(String(a.a || ""), String(b.a || "")));
  }
  if (limit.kind === "count") return sorted.slice(0, Math.floor(value));
  const feld = limit.kind === "minutes" ? "du" : "sz";
  const grenze = limit.kind === "minutes" ? value * 60 : value * 1048576;
  const out = [];
  let summe = 0;
  for (const r of sorted) {
    const add = Number(r[feld]) || 0;
    if (summe + add > grenze && out.length) break;
    out.push(r);
    summe += add;
  }
  return out;
}

function recomputeAllSmart() {
  SMART_CACHE = new Map();
  for (const n of PLAYLISTS) if (n.kind === "smart") recomputeSmart(n.id);
}

// ── Regel-Editor ──────────────────────────────────────────────────────────
// Zeilengenerator mit abhaengigen Auswahlfeldern: die Operatoren und das
// Wertfeld richten sich nach der Art des gewaehlten Feldes (SMART_OPS/
// SMART_TYPES oben). Der Dialog arbeitet auf einer KOPIE des Regelwerks --
// erst "Speichern" schreibt, Abbrechen laesst den Knoten unangetastet.
function smartOpLabel(op) { return t(`smart.op_${op}`); }

function ruleRowHtml(rule, index) {
  const fields = smartFields();
  const field = smartFieldByKey(rule.field) || fields[0];
  const ops = SMART_OPS[field.type] || [];
  const wert = (() => {
    if (field.type === SMART_TYPES.BOOL) {
      return `<select class="ruleval" data-rulevalue="${index}">` +
        `<option value="1"${String(rule.value) === "1" ? " selected" : ""}>${esc(t("smart.yes"))}</option>` +
        `<option value="0"${String(rule.value) === "0" ? " selected" : ""}>${esc(t("smart.no"))}</option></select>`;
    }
    if (field.type === SMART_TYPES.ENUM) {
      return `<select class="ruleval" data-rulevalue="${index}">` +
        field.options().map(o =>
          `<option value="${esc(o.v)}"${o.v === rule.value ? " selected" : ""}>${esc(o.l)}</option>`
        ).join("") + `</select>`;
    }
    const typ = (field.type === SMART_TYPES.NUM || field.type === SMART_TYPES.DATE)
      ? "number" : "text";
    return `<input class="ruleval" type="${typ}" data-rulevalue="${index}" ` +
      `value="${esc(rule.value ?? "")}">`;
  })();
  const einheit = field.type === SMART_TYPES.DATE ? t("smart.unit_days") : (field.unit || "");
  return `<div class="rulerow">
    <select class="rulefield" data-rulefield="${index}">${
      fields.map(f => `<option value="${esc(f.key)}"${f.key === field.key ? " selected" : ""}>${esc(f.label)}</option>`).join("")
    }</select>
    <select class="ruleop" data-ruleop="${index}">${
      ops.map(op => `<option value="${esc(op)}"${op === rule.op ? " selected" : ""}>${esc(smartOpLabel(op))}</option>`).join("")
    }</select>
    <span class="ruleval-wrap">${wert}</span>
    ${einheit ? `<span class="ruleunit">${esc(einheit)}</span>` : ""}
    <button type="button" class="rulebtn" data-ruleadd="${index}" title="${esc(t("smart.add_rule"))}">+</button>
    <button type="button" class="rulebtn" data-ruledel="${index}" title="${esc(t("smart.remove_rule"))}">&minus;</button>
  </div>`;
}

function openRulesDialog(node) {
  const ov = document.getElementById("rulesOverlay");
  const spec = parseSmartRules(node) || {match: "all", rules: [], limit: {}};
  // Kopie, damit Abbrechen wirklich nichts hinterlaesst.
  let rules = (spec.rules || []).map(x => ({...x}));
  if (!rules.length) rules = [{field: "v", op: "is", value: "FAKE"}];
  const limit = {enabled: false, kind: "count", value: 25, by: "added", ...(spec.limit || {})};

  document.getElementById("rulesName").value = node.name || "";
  document.getElementById("rulesMatch").value = spec.match === "any" ? "any" : "all";
  document.getElementById("rulesLimitOn").checked = !!limit.enabled;
  document.getElementById("rulesLimitValue").value = limit.value;
  document.getElementById("rulesLimitKind").value = limit.kind;
  document.getElementById("rulesLimitBy").value = limit.by;

  const currentSpec = () => ({
    match: document.getElementById("rulesMatch").value,
    rules: rules.filter(x => x && x.field),
    limit: {
      enabled: document.getElementById("rulesLimitOn").checked,
      kind: document.getElementById("rulesLimitKind").value,
      value: Number(document.getElementById("rulesLimitValue").value) || 0,
      by: document.getElementById("rulesLimitBy").value,
    },
  });

  // Vorschau: wie viele Zeilen wuerde dieses Regelwerk gerade treffen? Das
  // ist derselbe Weg wie spaeter im Baum (recomputeSmart), nur ohne den
  // Knoten zu speichern -- ohne diese Rueckmeldung baut man Regeln blind.
  const drawPreview = () => {
    const probe = {...node, rules: JSON.stringify(currentSpec())};
    const merk = PLAYLISTS;
    PLAYLISTS = PLAYLISTS.map(n => n.id === node.id ? probe : n);
    recomputeSmart(node.id);
    const n = (SMART_CACHE.get(node.id) || new Set()).size;
    PLAYLISTS = merk;
    document.getElementById("rulesPreview").textContent = t("smart.preview", {
      count: n.toLocaleString("de-DE"),
      track_word: n === 1 ? t("tree.track_singular") : t("tree.track_plural"),
    });
  };

  const draw = () => {
    document.getElementById("rulesRows").innerHTML =
      rules.map((rule, i) => ruleRowHtml(rule, i)).join("");
    const rowsHost = document.getElementById("rulesRows");
    rowsHost.querySelectorAll("[data-rulefield]").forEach(el => el.onchange = () => {
      const i = +el.dataset.rulefield;
      const field = smartFieldByKey(el.value);
      // Feldwechsel setzt Operator und Wert zurueck -- ein Textoperator auf
      // einem Zahlenfeld waere sonst stillschweigend wirkungslos.
      rules[i] = {field: el.value, op: (SMART_OPS[field.type] || ["is"])[0], value: ""};
      if (field.type === SMART_TYPES.ENUM) rules[i].value = field.options()[0].v;
      if (field.type === SMART_TYPES.BOOL) rules[i].value = "1";
      draw();
    });
    rowsHost.querySelectorAll("[data-ruleop]").forEach(el => el.onchange = () => {
      rules[+el.dataset.ruleop].op = el.value; drawPreview();
    });
    rowsHost.querySelectorAll("[data-rulevalue]").forEach(el => {
      const i = +el.dataset.rulevalue;
      const upd = () => { rules[i].value = el.value; drawPreview(); };
      el.onchange = upd;
      el.oninput = upd;
      // Genre wie im Tags-Dialog mit Praefix-Autovorschlaegen aus bereits
      // vergebenen Werten -- verhindert Mehrfachschreibweisen auch hier.
      // draw() baut die Zeilen bei jeder Aenderung komplett neu (innerHTML),
      // el ist also immer ein frisches Element -- kein Bind-Schutz noetig.
      const fld = smartFieldByKey(rules[i].field);
      if (fld && fld.key === "ge" && el.tagName === "INPUT") attachAutocomplete(el, "ge");
    });
    rowsHost.querySelectorAll("[data-ruleadd]").forEach(el => el.onclick = () => {
      rules.splice(+el.dataset.ruleadd + 1, 0, {field: "a", op: "contains", value: ""});
      draw();
    });
    rowsHost.querySelectorAll("[data-ruledel]").forEach(el => el.onclick = () => {
      // Die letzte Zeile bleibt stehen -- ein Regelwerk ohne Zeile traefe
      // alles, was als Ergebnis eines Klicks auf "-" niemand erwartet.
      if (rules.length <= 1) return;
      rules.splice(+el.dataset.ruledel, 1);
      draw();
    });
    drawPreview();
  };
  draw();

  ["rulesMatch", "rulesLimitOn", "rulesLimitValue", "rulesLimitKind", "rulesLimitBy"]
    .forEach(id => { document.getElementById(id).onchange = drawPreview; });

  const close = () => {
    ov.style.display = "none";
    document.onkeydown = null;
    // Die Vorschau hat SMART_CACHE mit einem ungespeicherten Regelwerk
    // gefuellt -- auf den tatsaechlich gespeicherten Stand zuruecksetzen.
    recomputeSmart(node.id);
    updateCards(); render();
  };
  document.getElementById("rulesNo").onclick = close;
  document.getElementById("rulesYes").onclick = async () => {
    const name = document.getElementById("rulesName").value.trim();
    if (!name) { note(t("tree.name_required"), "soft"); return; }
    try {
      await playlistApi("/api/playlist", {
        op: "update", id: node.id, name: name, rules: currentSpec(),
      });
      await reloadPlaylists();
      close();
      note(t("smart.saved", {name: name}));
    } catch (err) {
      note(t("smart.save_failed", {error: err.message}), true);
    }
  };
  document.onkeydown = ev => { if (ev.key === "Escape") close(); };
  ov.style.display = "flex";
}

// ── Rueckgaengig / Wiederherstellen ───────────────────────────────────────
// Entwurfsmuster "Befehl" (Command): jede Aenderung an einer Playlist wird als
// Objekt mit undo()/redo() abgelegt, der Kopfbereich ruft nur noch diese
// beiden Methoden auf und muss nichts ueber die Art der Aenderung wissen.
//
// Jeder Befehl traegt den Zustand VOR und NACH der Aenderung als vollstaendige
// Pfadliste, statt seine Umkehrung aus einem Diff zu rechnen. Der Grund ist
// nicht Bequemlichkeit: eine positionsbasierte Umkehrung ("Track wieder an
// Index 4 einfuegen") wird falsch, sobald die Liste zwischendurch von
// woanders geaendert wurde -- ein zweites Fenster, syncMarks(), ein Import.
// Ein Schnappschuss ist dagegen immer eindeutig, und der Server nimmt mit
// 'set' ohnehin die ganze Liste entgegen. Bei Listen dieser Groesse
// (Hunderte Pfade) kostet das nichts.
//
// Bewusst NICHT persistiert: nach einem Neuladen zeigte ein Schnappschuss
// womoeglich auf Pfade, die es nicht mehr gibt (Scan, Papierkorb,
// Music.app-Umzug). Der Verlauf gilt fuer die laufende Sitzung.
const UNDO_LIMIT = 50;
const undoStacks = new Map();      // playlistId -> {undo: [befehl], redo: [befehl]}

function undoStackFor(playlistId) {
  let st = undoStacks.get(playlistId);
  if (!st) { st = {undo: [], redo: []}; undoStacks.set(playlistId, st); }
  return st;
}

// Schreibt eine Pfadliste, ohne selbst einen Verlaufseintrag zu erzeugen --
// sonst wuerde jedes Rueckgaengig sofort einen neuen Schritt anlegen.
async function applyPlaylistItems(playlistId, paths) {
  await playlistApi("/api/playlist-items", {op: "set", id: playlistId, paths: paths});
  await reloadPlaylists();
  updateCards(); render();
}

function makeItemsCommand(playlistId, label, before, after) {
  return {
    playlistId, label,
    undo: () => applyPlaylistItems(playlistId, before),
    redo: () => applyPlaylistItems(playlistId, after),
  };
}

function pushUndo(playlistId, label, before, after) {
  if (JSON.stringify(before) === JSON.stringify(after)) return;   // nichts passiert
  const st = undoStackFor(playlistId);
  st.undo.push(makeItemsCommand(playlistId, label, before, after));
  if (st.undo.length > UNDO_LIMIT) st.undo.shift();
  // Ein neuer Schritt verwirft den Wiederherstellen-Zweig -- sonst koennte
  // man in einen Zustand springen, den es so nie gab.
  st.redo.length = 0;
  renderPlaylistHeader();
}

// Der Verlauf gilt nur, solange die Pfade stimmen. Ein Scan, ein Aufraeumen
// oder ein Auto-Relocate koennen Zeilen entfernen oder umschreiben -- danach
// zeigte ein Schnappschuss ins Leere, deshalb hier komplett verwerfen.
function clearUndoStacks() {
  undoStacks.clear();
  renderPlaylistHeader();
}

async function runUndo(direction) {
  const node = currentPlaylistNode();
  if (!node) return;
  const st = undoStackFor(node.id);
  const from = direction === "undo" ? st.undo : st.redo;
  const to = direction === "undo" ? st.redo : st.undo;
  const cmd = from.pop();
  if (!cmd) return;
  try {
    await (direction === "undo" ? cmd.undo() : cmd.redo());
    to.push(cmd);
    note(t(direction === "undo" ? "undo.done" : "undo.redone", {label: cmd.label}));
  } catch (err) {
    from.push(cmd);                                  // Schritt bleibt erhalten
    note(t("undo.failed", {error: err.message}), true);
  }
  renderPlaylistHeader();
}

function renderPlaylistHeader() {
  const bar = document.getElementById("playlistHeader");
  if (!bar) return;
  const node = currentListNode();
  if (!node) { bar.style.display = "none"; return; }
  const editable = node.kind === "playlist";
  bar.style.display = "";
  const icon = document.getElementById("plHeadIcon");
  icon.innerHTML = String(node.kind).startsWith("ext_")
    ? EXT_SOURCES[node.kind.slice(4)].icon
    : node.kind === "view"
    ? `<span class="tbadge tcircle" style="background:var(--dim)"><span class="tsym tsvg">${node.icon || ""}</span></span>`
    : playlistBadgeHtml(node);
  document.getElementById("plHeadName").textContent = node.name;
  const n = String(node.kind).startsWith("ext_")
    ? (EXT_CONTENTS.get(`${node.kind.slice(4)}:${node.id.slice(3)}`) || {paths: []}).paths.length
    : node.kind === "view" ? node.count : playlistCount(node.id);
  document.getElementById("plHeadCount").textContent = t("tree.header_count", {
    count: n.toLocaleString("de-DE"),
    track_word: n === 1 ? t("tree.track_singular") : t("tree.track_plural"),
  });
  const st = undoStackFor(node.id);
  const undoBtn = document.getElementById("plUndo");
  const redoBtn = document.getElementById("plRedo");
  undoBtn.disabled = !editable || !st.undo.length || !apiMode;
  redoBtn.disabled = !editable || !st.redo.length || !apiMode;
  // Der naechste Schritt steht im Tooltip -- ohne das ist nicht erkennbar,
  // was ein Klick zuruecknimmt.
  undoBtn.title = st.undo.length
    ? t("undo.undo_what", {label: st.undo[st.undo.length - 1].label}) : t("undo.undo");
  redoBtn.title = st.redo.length
    ? t("undo.redo_what", {label: st.redo[st.redo.length - 1].label}) : t("undo.redo");
}

// Pfad eines Knotens fuer die Anzeige ("Sets / Warm-up") -- im Auswahldialog
// steht die Baumstruktur nicht zur Verfuegung, gleichnamige Listen in
// verschiedenen Ordnern waeren sonst nicht auseinanderzuhalten.
function playlistPathLabel(node) {
  const parts = [];
  let cur = node.parent_id ? PLAYLISTS.find(n => n.id === node.parent_id) : null;
  let guard = 0;
  while (cur && guard++ < 12) {
    parts.unshift(cur.name);
    cur = cur.parent_id ? PLAYLISTS.find(n => n.id === cur.parent_id) : null;
  }
  return parts.join(" / ");
}

// Zielauswahl fuer "Zur Playlist hinzufuegen" -- die Alternative zum Ziehen,
// fuer Tastaturbedienung und fuer Listen, die gerade nicht im Baum sichtbar
// sind (zugeklappter Ordner).
function openPlaylistPicker(paths) {
  const ov = document.getElementById("pickOverlay");
  const host = document.getElementById("pickList");
  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  const lists = PLAYLISTS.filter(n => n.kind === "playlist");
  document.getElementById("pickNote").textContent = t("tree.pick_note", {
    count: paths.length,
    track_word: paths.length === 1 ? t("tree.track_singular") : t("tree.track_plural"),
  });
  host.innerHTML = lists.length
    ? lists.map(n => {
        const path = playlistPathLabel(n);
        return `<button type="button" class="pickitem" data-pick="${esc(n.id)}">` +
          playlistBadgeHtml(n) +
          `<span>${esc(n.name)}</span>` +
          (path ? `<span class="pickpath">${esc(path)}</span>` : "") +
          `<span class="pickcount">${playlistCount(n.id).toLocaleString("de-DE")}</span>` +
          `</button>`;
      }).join("")
    : `<div class="pickempty">${esc(t("tree.pick_empty"))}</div>`;
  host.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => {
    close();
    dropTracksOnPlaylist(b.dataset.pick, paths);
  });
  document.getElementById("pickNo").onclick = close;
  document.getElementById("pickNew").onclick = async () => {
    close();
    // Erst anlegen, dann die Tracks hineinlegen -- createPlaylistNode()
    // waehlt die neue Liste bereits aus, das Ergebnis ist sofort sichtbar.
    const before = new Set(PLAYLISTS.map(n => n.id));
    await createPlaylistNode("playlist");
    const created = PLAYLISTS.find(n => !before.has(n.id));
    if (created) dropTracksOnPlaylist(created.id, paths);
  };
  document.onkeydown = ev => { if (ev.key === "Escape") close(); };
  ov.style.display = "flex";
}

// Tracks aus einer Playlist nehmen -- nie die Datei, nur die Zuordnung.
// Bewusst ohne Rueckfrage: der Schritt ist folgenlos und laesst sich durch
// erneutes Hineinziehen sofort zuruecknehmen (anders als der Papierkorb).
async function removeFromPlaylist(playlistId, paths) {
  const node = PLAYLISTS.find(n => n.id === playlistId);
  if (!node || !paths.length) return;
  const before = [...(PLAYLIST_ITEMS[playlistId] || [])];
  try {
    const data = await playlistApi("/api/playlist-items",
      {op: "remove", id: playlistId, paths: paths});
    pushUndo(playlistId, t("undo.label_remove"), before, data.items);
    await reloadPlaylists();
    state.selected.clear();
    updateCards(); render();
    note(t("tree.removed_from_list", {
      count: data.changed, name: node.name,
      track_word: data.changed === 1 ? t("tree.track_singular") : t("tree.track_plural"),
    }));
  } catch (err) {
    note(t("tree.remove_failed", {error: err.message}), true);
  }
}

// Ablegen eines Baumknotens auf einem anderen. Gezogen wird die BAUM-Kennung
// ("view:all", "node:pl123"), weil auf der obersten Ebene zwei Sorten Knoten
// nebeneinander stehen: die festen Ansichten ohne Datenbankzeile und die
// eigenen Listen mit.
//
// Die Reihenfolge der obersten Ebene liegt deshalb in state.treeOrder
// (localStorage), die Reihenfolge innerhalb eines Ordners dagegen in
// 'playlists.seq' auf dem Server -- dort sind alle Knoten Datenbankzeilen.
async function dropTreeNode(dragId, btn, where) {
  const targetId = btn.dataset.id;
  if (!dragId || !targetId || dragId === targetId) return;

  // Fremde Wurzeln (Music.app/Rekordbox) lassen sich NUR untereinander
  // verschieben -- TrackTab ist hier absichtlich kein gueltiges Ziel (bleibt
  // dadurch immer an erster Stelle, siehe treeRoots()) und nimmt auch keine
  // eigenen Listen/Ansichten auf, die zufaellig ueber einer Wurzel abgelegt
  // werden (treeDropTarget() erlaubt davor/dahinter dort rein optisch, ohne
  // beim dragover schon zu wissen, WAS gezogen wird -- die eigentliche
  // Pruefung gehoert deshalb hierher, an den tatsaechlichen Drop).
  const extRootIds = Object.values(EXT_SOURCES).map(s => s.root);
  if (extRootIds.includes(dragId) || extRootIds.includes(targetId)) {
    if (!extRootIds.includes(dragId) || !extRootIds.includes(targetId) || where === "into") return;
    const roots = treeRoots().slice(1).map(n => n.id).filter(id => id !== dragId);
    const at = roots.indexOf(targetId);
    if (at !== -1) {
      roots.splice(where === "before" ? at : at + 1, 0, dragId);
      state.treeOrder = [...state.treeOrder.filter(id => !extRootIds.includes(id)), ...roots];
      saveFilters();
      updateCards(); render();
    }
    return;
  }

  const dbId = id => id.startsWith("node:") ? id.slice(5) : null;
  const dragDb = dbId(dragId);
  const targetDb = dbId(targetId);
  const target = targetDb ? PLAYLISTS.find(n => n.id === targetDb) : null;
  const zielIstOben = btn.dataset.top === "1" || targetId === "root:tracktab";

  // Eine feste Ansicht hat keine Datenbankzeile -- sie kann nur ihre Position
  // auf der obersten Ebene aendern, nicht in einen Ordner wandern.
  if (!dragDb && (!zielIstOben || where === "into")) return;

  try {
    if (dragDb) {
      const inOrdner = where === "into" && target && target.kind === "folder";
      const parent = inOrdner ? target.id
                   : (where === "into" ? null : (target ? (target.parent_id || null) : null));
      await playlistApi("/api/playlist", {op: "move", id: dragDb, parent_id: parent});
      // Innerhalb eines Ordners zaehlt 'seq': die Ebene wird komplett neu
      // durchnummeriert, sonst blieben Luecken und Dubletten.
      if (parent && where !== "into" && target) {
        const geschwister = PLAYLISTS
          .filter(n => (n.parent_id || null) === parent && n.id !== dragDb)
          .map(n => n.id);
        const at = geschwister.indexOf(target.id);
        geschwister.splice(where === "before" ? at : at + 1, 0, dragDb);
        await playlistApi("/api/playlist",
          {op: "reorder", parent_id: parent, ids: geschwister});
      }
      await reloadPlaylists();
    }

    // Oberste Ebene: Reihenfolge aus der aktuellen Anzeige neu festschreiben.
    // Ueberschreibt state.treeOrder komplett -- die Reihenfolge der fremden
    // Wurzeln (siehe oben) muss deshalb ausdruecklich erhalten bleiben, sie
    // steht im selben Array, gehoert aber zu einer anderen Geschwisterebene.
    if (zielIstOben && where !== "into") {
      const oben = treeRoots()[0].kids.map(n => n.id).filter(id => id !== dragId);
      const at = oben.indexOf(targetId);
      if (at !== -1) {
        oben.splice(where === "before" ? at : at + 1, 0, dragId);
        state.treeOrder = [...oben, ...state.treeOrder.filter(id => extRootIds.includes(id))];
        saveFilters();
      }
    }
    updateCards(); render();
  } catch (err) {
    note(t("tree.move_failed", {error: err.message}), true);
  }
}

function toggleTreeNode(id) {
  const i = state.treeClosed.indexOf(id);
  if (i === -1) state.treeClosed.push(id); else state.treeClosed.splice(i, 1);
  saveFilters(); renderTree();
}

// Auswahl eines Knotens. Beim Wechsel IN eine Playlist wird die
// Spaltensortierung beiseitegelegt und die manuelle Reihenfolge gezeigt
// (filtered() weiter unten) -- sonst waere die selbst gelegte Reihenfolge nie
// zu sehen, weil state.sort mit "co" vorbelegt ist. Beim Verlassen kommt sie
// zurueck; ein Klick auf einen Spaltenkopf innerhalb der Playlist sortiert
// wie ueberall und schaltet die manuelle Reihenfolge voruebergehend ab.
function selectView(id) {
  // Eine fremde Liste wird beim Anklicken geholt (Fokus-Ereignis) -- ihr
  // Inhalt liegt in Music.app bzw. Rekordbox, nicht bei uns.
  if (extSourceOfView(id)) { openExtPlaylist(id); return; }
  // Fokus-Ereignis: eine Smart Playlist wird beim Anklicken neu berechnet,
  // nicht laufend im Hintergrund -- so bleibt render() unbelastet und das
  // Ergebnis ist trotzdem in dem Moment aktuell, in dem man es ansieht.
  if (String(id).startsWith("sm:")) recomputeSmart(id.slice(3));
  const wasPl = String(state.view).startsWith("pl:");
  const isPl = String(id).startsWith("pl:");
  if (isPl && !wasPl) {
    state.sortBeforePlaylist = state.sort;
    state.sort = null;
  } else if (!isPl && wasPl) {
    state.sort = state.sortBeforePlaylist === undefined ? "co" : state.sortBeforePlaylist;
    state.sortBeforePlaylist = undefined;
  }
  state.view = id;
  state.shown = PAGE;
  // Beide gelten nur innerhalb der Liste, in der sie gesetzt wurden --
  // sonst bliebe eine unsichtbare Einschraenkung beim Wechsel in eine ganz
  // andere Ansicht haengen (siehe state.valueFilter/state.mergePreview).
  state.valueFilter = null;
  state.mergePreview = null;
  state.tagIssueFilter = null;
  // Die Genre-Liste gruppiert immer nach Genre (siehe groupMode in
  // render()) -- die Checkbox "nach Album gruppieren" darf dabei nie
  // angehakt stehen bleiben, das waere ein widersprüchliches Signal
  // (gleiches Muster wie bindHeaderSort() bei aktiver Spaltensortierung).
  if (id === "grp:genre" && state.groupAlbums) {
    state.groupAlbums = false;
    const ga = document.getElementById("groupAlbums");
    if (ga) ga.checked = false;
  }
  syncColumnsForView();
  saveFilters(); syncSortHeaders(); updateCards(); render();
  renderPlaylistHeader();
}

// Oeffnet eine Music.app- oder Rekordbox-Playlist: Inhalt holen (einmal je
// Sitzung gecacht), View anlegen, dann wie jede andere Liste anzeigen. Die
// Spaltensortierung wird wie bei eigenen Playlisten beiseitegelegt, damit die
// Reihenfolge der Fremdliste sichtbar ist.
async function openExtPlaylist(viewId) {
  const source = extSourceOfView(viewId);
  const id = viewId.slice(EXT_SOURCES[source].prefix.length);
  try {
    const entry = await loadExtPlaylist(source, id);
    ensureExtView(viewId);
    rebuildPlaylistIndex();          // neue Ersatzzeilen in ROW_BY_PATH
    if (!String(state.view).startsWith("pl:") && !extSourceOfView(state.view)) {
      state.sortBeforePlaylist = state.sort;
    }
    state.sort = null;
    state.view = viewId;
    state.shown = PAGE;
    state.valueFilter = null;
    state.mergePreview = null;
    state.tagIssueFilter = null;
    syncColumnsForView();
    saveFilters(); syncSortHeaders(); updateCards(); render();
    renderPlaylistHeader();
    if (entry.unsupported) note(t("tree.smart_unsupported"), "soft");
  } catch (err) {
    note(t("tree.load_failed", {error: err.message}), true);
  }
}

// ── Eigene Listen anlegen, umbenennen, loeschen ───────────────────────────
// Alle Schreibwege laufen ueber /api/playlist bzw. /api/playlist-items und
// ziehen danach den ganzen Baum ueber /api/playlists nach, statt den lokalen
// Zustand von Hand fortzuschreiben: der Server ist die einzige Wahrheit
// (Zyklusprüfung beim Verschieben, Kaskade beim Loeschen, unbekannte Pfade),
// und ein zweiter, halb gepflegter Zustand im Client waere die sichere
// Quelle fuer Abweichungen.
async function playlistApi(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  // Der Server legt seine Begruendung auch bei 400 in den Rumpf (_fail() in
  // server.py). Erst lesen, dann urteilen -- sonst bekaeme der Nutzer statt
  // „Ein Ordner kann nicht in sich selbst liegen" nur „Server meldet 400".
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (data && data.error) throw new Error(data.error);
  if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
  if (!data || !data.ok) throw new Error(t("error.unknown"));
  return data;
}

async function reloadPlaylists() {
  const res = await fetch("/api/playlists");
  if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
  const data = await res.json();
  PLAYLISTS = data.playlists || [];
  PLAYLIST_ITEMS = data.items || {};
  rebuildPlaylistIndex();
  rebuildPlaylistViews();
  rebuildMerkViews();
  recomputeAllSmart();
  // Steht die gerade gewaehlte Liste nicht mehr im Baum (geloescht, auch als
  // Kind eines geloeschten Ordners), zurueck auf "Alle" -- sonst zeigte
  // currentView() auf einen View, den es nicht mehr gibt, und filtered()
  // faellt stumm auf VIEWS[0] zurueck. Mit der Liste faellt auch ihre
  // Spaltenansicht weg, deshalb die Kopfzeile mitziehen.
  if (!VIEWS.some(v => v.id === state.view)) {
    state.view = "all";
    syncColumnsForView();
  }
}

// Nur Token, keine freien Hex-Werte -- siehe Kommentar bei .stylerow in app.css.
const PLAYLIST_COLOR_CHOICES = ["fl1", "fl2", "fl3", "fl4", "fl5", "fl6",
                                 "fl7", "fl8", "fl9", "fl10", "fl11", "fl12"];

// Muss zu _Handler._MAX_FAV_SLOTS in server.py passen -- nur fuer den
// Hinweistext/die Deaktivierung der Checkbox hier, die eigentliche Grenze
// setzt der Server durch (_resolve_fav_slot()).
const MAX_FAV_SLOTS = 4;

// Dialog fuer Anlegen und Bearbeiten einer Liste: Name, Symbol, Farbe und
// Spaltenansicht. Aufbau wie askTrash()/der M3U-Dialog: versprochenes
// Ergebnis, Escape bricht ab, Enter bestaetigt. Das Overlay steht in
// index.html NACH dem <script>-Block -- deshalb erst hier im Klick-Handler
// nachschlagen, nicht auf Modulebene.
//
// 'columnsView' ist die View-Kennung der Liste ("pl:<id>", "sm:<id>"), deren
// Spaltenansicht hier eingestellt wird -- null beim Anlegen (die Kennung gibt
// es dann noch nicht, der Aufrufer traegt sie hinterher ein) und undefined
// ueberall, wo es keine Tabelle dazu gibt (Ordner, Name einer
// Spaltenansicht). 'withMerkliste' blendet die Checkbox "Als Merkliste
// verwenden" ein (nur beim Anlegen/Bearbeiten einer normalen Playlist, siehe
// createPlaylistNode()/nodeMenuItems()) -- Liefert {name, icon, color,
// columnView, merkliste} oder null bei Abbruch; 'columnView' ist "" fuer die
// Standard-Spalten, 'merkliste' ist undefined, wenn withMerkliste false ist
// (server._playlist_apply() laesst fav_slot dann unangetastet).
function askPlaylistProps(title, current, withStyle = true, columnsView = undefined,
                          withName = true, autocompleteKey = null, titleIconHtml = null,
                          withMerkliste = false) {
  return new Promise(resolve => {
    const ov = document.getElementById("nameOverlay");
    const input = document.getElementById("nameInput");
    const styleRows = document.getElementById("nameStyleRows");
    const colsField = document.getElementById("nameColsField");
    const colsSelect = document.getElementById("nameColsSelect");
    const merkField = document.getElementById("nameMerkField");
    const merkCheckbox = document.getElementById("nameMerkCheckbox");
    const merkHint = document.getElementById("nameMerkHint");
    document.getElementById("nameTitle").textContent = title;
    // Icon der jeweiligen "Aufraeumen"-Liste vor dem Titel (Genre/Album/
    // Kuenstler, siehe renameGroupValue()) -- bei allen anderen Aufrufern
    // (Playlist/Ordner/Spaltenansicht benennen) bleibt der Slot leer.
    const titleIcon = document.getElementById("nameTitleIcon");
    titleIcon.innerHTML = titleIconHtml || "";
    titleIcon.hidden = !titleIconHtml;
    // Ohne Namensfeld (feste Liste: nur die Spaltenansicht ist einstellbar)
    // bleibt der bestehende Name unveraendert stehen.
    document.getElementById("nameField").style.display = withName ? "" : "none";
    input.value = (current && current.name) || "";
    // Vorschlaege aus bereits vorhandenen Werten (Genre/Album/Interpret-
    // Umbenennen) -- null deaktiviert sie fuer alle anderen Aufrufer dieses
    // geteilten Dialogs (Playlist/Ordner/Spaltenansicht benennen).
    attachAutocomplete(input, autocompleteKey, {openOnFocus: false});
    let icon = (current && current.icon) || null;
    let color = (current && current.color) || null;

    const withColumns = columnsView !== undefined;
    colsField.style.display = withColumns ? "" : "none";
    if (withColumns) {
      colsSelect.innerHTML = columnViewOptionsHTML(
        columnsView ? (COLUMN_ASSIGN[columnsView] || "") : "");
    }

    merkField.style.display = withMerkliste ? "" : "none";
    if (withMerkliste) {
      const alreadyMarked = !!(current && current.fav_slot);
      merkCheckbox.checked = alreadyMarked;
      const usedElsewhere = merkPlaylists()
        .filter(n => !current || n.id !== current.id).length;
      const atLimit = usedElsewhere >= MAX_FAV_SLOTS;
      merkCheckbox.disabled = atLimit && !alreadyMarked;
      merkHint.textContent = atLimit && !alreadyMarked
        ? t("tree.merk_limit_reached", {max: MAX_FAV_SLOTS})
        : t("tree.merk_hint_used", {used: usedElsewhere + (merkCheckbox.checked ? 1 : 0), max: MAX_FAV_SLOTS});
      merkCheckbox.onchange = () => {
        merkHint.textContent = t("tree.merk_hint_used",
          {used: usedElsewhere + (merkCheckbox.checked ? 1 : 0), max: MAX_FAV_SLOTS});
      };
    }

    // Ordner tragen kein eigenes Symbol -- ihr Ordnersinnbild ist die Aussage.
    styleRows.style.display = withStyle ? "" : "none";
    // Zwei Modi wie bei Apples Erinnerungen: eine feste Symbol-Bibliothek
    // (einfarbig, nimmt die Badge-Vordergrundfarbe an) oder ein frei
    // gewaehltes Emoji (behaelt seine eigene Farbe -- deshalb faerbt hier
    // die Playlistfarbe nur den Kreis-HINTERGRUND, nie das Emoji selbst).
    let iconMode = (parseIcon(icon) || {}).type === "emoji" ? "emoji" : "sym";
    const emojiInput = document.getElementById("nameEmojiInput");
    const drawPreview = () => {
      document.getElementById("nameIconPreview").innerHTML =
        `<span class="tbadge tcircle" style="background:${color ? `var(--${esc(color)})` : "var(--dim)"}">` +
        `${iconGlyphHtml(icon)}</span>`;
    };
    const symInput = document.getElementById("nameSymInput");
    const symHint = document.getElementById("nameSymHint");
    const setMode = mode => {
      iconMode = mode;
      document.querySelectorAll("#nameIconTabs [data-mode]").forEach(b =>
        b.classList.toggle("on", b.dataset.mode === mode));
      document.getElementById("nameIcons").style.display = mode === "sym" ? "" : "none";
      document.getElementById("nameSymNameRow").style.display = mode === "sym" ? "" : "none";
      symHint.style.display = "none";
      emojiInput.style.display = mode === "emoji" ? "" : "none";
      document.getElementById("nameEmojiHint").style.display = mode === "emoji" ? "" : "none";
    };
    // Schnellauswahl: eine handverlesene Vorauswahl echter Lucide-Namen
    // (QUICK_PICK_ICONS) plus ein freies Namensfeld darunter (siehe
    // nameSymInput) -- jeder gueltige Name aus https://lucide.dev/icons/
    // funktioniert dort, nicht nur die Vorauswahl.
    const drawIcons = () => {
      const parsed = parseIcon(icon);
      document.getElementById("nameIcons").innerHTML =
        `<button type="button" class="styleopt symopt${parsed ? "" : " on"}" ` +
        `data-symkey="" title="${esc(t("tree.icon_none"))}">&ndash;</button>` +
        QUICK_PICK_ICONS.map(name => {
          const active = parsed && parsed.type === "sym" && parsed.value === name;
          return `<button type="button" class="styleopt symopt${active ? " on" : ""}" ` +
            `data-symkey="${esc(name)}" title="${esc(name)}">` +
            `<span class="tsym tsvg">${SVG(LUCIDE_ICONS[name])}</span></button>`;
        }).join("");
      document.querySelectorAll("#nameIcons [data-symkey]").forEach(b => b.onclick = () => {
        icon = b.dataset.symkey ? `sym:${b.dataset.symkey}` : null;
        symInput.value = b.dataset.symkey || "";
        symHint.style.display = "none";
        drawIcons(); drawPreview();
      });
    };
    symInput.value = (parseIcon(icon) || {}).type === "sym" ? parseIcon(icon).value : "";
    symInput.oninput = () => {
      const name = symInput.value.trim();
      if (!name) { icon = null; symHint.style.display = "none"; drawIcons(); drawPreview(); return; }
      if (LUCIDE_ICONS[name]) {
        icon = `sym:${name}`; symHint.style.display = "none";
      } else {
        symHint.style.display = "";
      }
      drawIcons(); drawPreview();
    };
    emojiInput.value = (parseIcon(icon) || {}).type === "emoji" ? parseIcon(icon).value : "";
    emojiInput.oninput = () => {
      const v = emojiInput.value.trim();
      icon = v ? `emoji:${v}` : null;
      drawPreview();
    };
    document.querySelectorAll("#nameIconTabs [data-mode]").forEach(b => b.onclick = () => setMode(b.dataset.mode));
    const drawColors = () => {
      document.getElementById("nameColors").innerHTML =
        [null, ...PLAYLIST_COLOR_CHOICES].map(tok =>
          `<button type="button" class="styleopt swatch${tok ? "" : " none"}` +
          `${tok === color ? " on" : ""}" data-color="${tok || ""}"` +
          `${tok ? ` style="background:var(--${tok})"` : ""}>${tok ? "" : "&ndash;"}</button>`
        ).join("");
      document.querySelectorAll("#nameColors [data-color]").forEach(b => b.onclick = () => {
        color = b.dataset.color || null; drawColors(); drawPreview();
      });
    };
    if (withStyle) { setMode(iconMode); drawIcons(); drawColors(); drawPreview(); }

    ov.style.display = "flex";
    if (withName) { input.focus(); input.select(); }
    else if (withColumns) colsSelect.focus();
    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      resolve(answer);
    };
    const submit = () => {
      const name = withName ? input.value.trim() : ((current && current.name) || "");
      // Ein leeres Namensfeld ist keine gueltige Eingabe -- ohne Namensfeld
      // steht der bestehende Name drin und der Knopf schliesst wie erwartet.
      done(name ? {
        name, icon, color, columnView: withColumns ? colsSelect.value : null,
        merkliste: withMerkliste ? merkCheckbox.checked : undefined,
      } : null);
    };
    document.getElementById("nameNo").onclick = () => done(null);
    document.getElementById("nameYes").onclick = submit;
    input.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); submit(); } };
    document.onkeydown = ev => { if (ev.key === "Escape") done(null); };
  });
}

async function createPlaylistNode(kind) {
  const props = await askPlaylistProps(
    kind === "folder" ? t("tree.dialog_new_folder")
      : kind === "smart" ? t("tree.dialog_new_smart")
      : t("tree.dialog_new_playlist"),
    // Ordner haben keine eigene Tabelle -- weder Symbol/Farbe noch eine
    // Spaltenansicht ergeben dort einen Sinn.
    null, kind !== "folder", kind === "folder" ? undefined : null,
    true, null, null, kind === "playlist");
  if (!props) return;
  const name = props.name;
  // In einen aufgeklappten Ordner hinein anlegen, wenn gerade eine Liste
  // daraus ausgewaehlt ist -- sonst auf oberster Ebene.
  const current = PLAYLISTS.find(n => `pl:${n.id}` === state.view);
  const parent = current ? (current.parent_id || null) : null;
  try {
    const data = await playlistApi("/api/playlist", {
      op: "create", kind: kind, name: name, parent_id: parent,
      icon: props.icon, color: props.color, merkliste: props.merkliste,
    });
    await reloadPlaylists();
    // Zuordnung VOR dem Oeffnen setzen -- selectView() baut die Kopfzeile
    // ueber syncColumnsForView() bereits mit dem richtigen Spalten-Topf auf.
    if (kind !== "folder" && props.columnView) {
      setColumnAssign(nodeViewId({kind, id: data.node.id}), props.columnView);
    }
    if (kind === "playlist") {
      selectView(`pl:${data.node.id}`);
    } else if (kind === "smart") {
      // Eine Smart Playlist ohne Regeln waere leer und ihr Zweck unklar --
      // deshalb direkt in den Regel-Editor, statt sie so stehen zu lassen.
      selectView(`sm:${data.node.id}`);
      const neu = PLAYLISTS.find(n => n.id === data.node.id);
      if (neu) openRulesDialog(neu);
    } else {
      renderTree();
    }
    note(t("tree.created", {name: name}));
  } catch (err) {
    note(t("tree.create_failed", {error: err.message}), true);
  }
}

// Die Tracks einer Playlist als Zeilen, in ihrer manuellen Reihenfolge und
// ohne solche, deren Datei inzwischen im Papierkorb liegt.
function playlistRows(id) {
  const node = PLAYLISTS.find(n => n.id === id);
  if (node && node.kind === "smart") {
    // Berechnetes Ergebnis in der Reihenfolge der Tabelle, nicht in der von
    // DATA -- so entspricht ein Export dem, was in der Liste zu sehen ist.
    const treffer = SMART_CACHE.get(id) || new Set();
    return DATA.filter(r => !r.removed && treffer.has(r.p));
  }
  return (PLAYLIST_ITEMS[id] || [])
    .map(path => ROW_BY_PATH.get(path))
    .filter(r => r && !r.removed);
}

// View-Kennung eines Baumknotens -- Ordner haben keine (kein eigener View,
// ein Klick klappt sie nur auf und zu).
const nodeViewId = node => node.kind === "folder"
  ? null : `${node.kind === "smart" ? "sm" : "pl"}:${node.id}`;

async function setNodeColumnView(node) {
  const viewId = nodeViewId(node);
  if (!viewId) return;
  const props = await askPlaylistProps(t("cols.view_dialog_for", {name: playlistLabel(node)}),
                                       node, false, viewId, false);
  if (!props) return;
  setColumnAssign(viewId, props.columnView);
  if (state.view === viewId) repaintColumns();
}

function nodeMenuItems(node) {
  // Feste Listen (db._SYSTEM_PLAYLISTS) bekommen nur, was nichts an ihnen
  // aendert -- Bearbeiten, Duplizieren und Loeschen weist auch der Server ab.
  // Ihre Position im Baum laesst sich trotzdem per Drag & Drop aendern.
  const locked = !!node.system;
  const spielbar = node.kind === "playlist" || node.kind === "smart";

  // Drei Gruppen, am Ende mit Trennlinien verbunden:
  //   1. was die Liste SELBST betrifft (Name, Regeln, Kopie)
  //   2. was mit ihrem INHALT geschieht (abspielen, exportieren)
  //   3. das Loeschen -- abgesetzt, weil es als einziges nicht umkehrbar ist
  const listeSelbst = [];
  const inhalt = [];
  const entfernen = [];

  if (!locked) {
    listeSelbst.push({
      icon: ICONS.edit, label: t("tree.edit"),
      action: async () => {
        // Spaltenansicht bewusst NICHT hier -- eigener Menuepunkt weiter unten
        // (spielbar-Block), gemeinsam mit den festen Listen, statt an zwei
        // Stellen dieselbe Einstellung aendern zu koennen.
        const props = await askPlaylistProps(t("tree.dialog_edit"), node,
                                             node.kind !== "folder", undefined, true, null, null,
                                             node.kind === "playlist");
        if (!props) return;
        try {
          await playlistApi("/api/playlist", {
            op: "update", id: node.id,
            name: props.name, icon: props.icon, color: props.color,
            merkliste: props.merkliste,
          });
          await reloadPlaylists();
          updateCards();
          render();
        } catch (err) { note(t("tree.rename_failed", {error: err.message}), true); }
      },
    });
    if (node.kind === "smart") {
      listeSelbst.push({
        icon: ICONS.listChecks, label: t("smart.edit_rules"),
        action: () => openRulesDialog(node),
      });
    }
    if (node.kind === "playlist") {
      listeSelbst.push({
        icon: ICONS.stickyNotes, label: t("tree.duplicate"),
        action: async () => {
          try {
            const copy = await playlistApi("/api/playlist", {
              op: "create", kind: "playlist", parent_id: node.parent_id || null,
              name: t("tree.copy_name", {name: node.name}),
            });
            const paths = PLAYLIST_ITEMS[node.id] || [];
            if (paths.length) {
              await playlistApi("/api/playlist-items",
                {op: "set", id: copy.node.id, paths: paths});
            }
            await reloadPlaylists();
            renderTree();
            note(t("tree.created", {name: copy.node.name}));
          } catch (err) { note(t("tree.create_failed", {error: err.message}), true); }
        },
      });
    }
  }

  // Eigener Menuepunkt fuer JEDE spielbare Liste -- normale wie feste
  // (setNodeColumnView() baut sich die viewId selbst ueber nodeViewId() und
  // ist damit fuer beide gleichermassen generisch). Bei festen Listen ist das
  // der einzige Weg (Name/Symbol/Farbe sind dort gesperrt, kein Bearbeiten-
  // Punkt); bei normalen Listen bewusst getrennt vom Bearbeiten-Dialog, damit
  // es pro Einstellung nur eine Stelle gibt.
  if (spielbar) {
    listeSelbst.push({
      icon: ICONS.columns3, label: t("cols.view_menu"),
      action: () => setNodeColumnView(node),
    });
  }

  if (spielbar) {
    inhalt.push({
      icon: ICONS.listEnd, label: t("tree.to_queue"),
      action: () => {
        const rows = playlistRows(node.id);
        if (!rows.length) { note(t("tree.empty_list"), "soft"); return; }
        rows.forEach(r => queueState.tracks.push(r));
        queueState.manual = true;
        renderPlayerBar(); renderQueuePopup();
        note(t("tree.queued", {
          count: rows.length, name: playlistLabel(node),
          track_word: rows.length === 1 ? t("tree.track_singular") : t("tree.track_plural"),
        }));
      },
    });
    inhalt.push({
      icon: ICONS.fileMusic, label: t("tree.export_m3u"),
      action: () => {
        const rows = playlistRows(node.id);
        if (!rows.length) { note(t("tree.empty_list"), "soft"); return; }
        downloadM3u(rows, playlistLabel(node));
      },
    });
  }

  if (!locked) {
    entfernen.push({
      icon: ICONS.trash, cls: "del", label: t("tree.delete"),
      action: async () => {
        // Ordner nehmen ihren Inhalt mit -- das steht in der Rueckfrage, damit
        // es niemanden nachtraeglich ueberrascht.
        const kids = PLAYLISTS.filter(n => (n.parent_id || null) === node.id).length;
        const ok = await askConfirmSimple(
          t("tree.delete_title", {name: node.name}),
          kids ? t("tree.delete_note_folder", {
            count: kids,
            // Kein Pluralsystem in t() -- der Aufrufer liefert das Wort, wie
            // bei toast.entry_singular/-plural weiter unten.
            entry_word: kids === 1 ? t("toast.entry_singular") : t("toast.entry_plural"),
          }) : t("tree.delete_note"));
        if (!ok) return;
        try {
          const gone = await playlistApi("/api/playlist", {op: "delete", id: node.id});
          (gone.deleted || []).forEach(id => undoStacks.delete(id));
          await reloadPlaylists();
          updateCards(); render();
          note(t("tree.deleted", {name: node.name}));
        } catch (err) { note(t("tree.delete_failed", {error: err.message}), true); }
      },
    });
  }

  // Leere Gruppen fallen samt ihrer Trennlinie weg -- bei einer festen Liste
  // bleibt so nur die Inhalts-Gruppe uebrig, ganz ohne Linie.
  const gruppen = [listeSelbst, inhalt, entfernen].filter(g => g.length);
  return gruppen.flatMap((g, i) => i === 0 ? g : [{sep: true}, ...g]);
}

function openNodeMenu(node, el) {
  openContextMenu(nodeMenuItems(node), el);
}

// Dasselbe "..."-Menue wie openNodeMenu(), aber fuer die view-basierten
// Baum-Eintraege ohne eigene Playlist-Zeile (Alle/Ausgeblendet/Datei
// fehlt/Duplikate/Merklisten) -- die tragen nur ein VIEWS-Element, keinen
// PLAYLISTS-Knoten, deshalb kein nodeMenuItems()/askPlaylistProps(node, ...).
async function setViewColumnView(view) {
  const props = await askPlaylistProps(
    t("cols.view_dialog_for", {name: view.label}), null, false, view.id, false);
  if (!props) return;
  setColumnAssign(view.id, props.columnView);
  if (state.view === view.id) repaintColumns();
}
// Zeilen einer festen Liste (Ausgeblendet/Duplikate/Merkliste) in derselben
// Filterlogik wie filtered() fuer den aktiven View, aber unabhaengig davon,
// welcher Tab gerade offen ist -- der Export-Menuepunkt soll ohne Tabwechsel
// funktionieren.
function viewRows(view) {
  return DATA.filter(r => !r.removed && (!r.ext || view.ext) && view.test(r));
}

// Nur fuer Listen exportierbar, bei denen ein Export als Playlist Sinn
// ergibt -- "gone" (Datei fehlt) haette keine spielbaren Pfade, "corrected"
// deckt der Toolbar-Knopf "M3U8 Export" bereits ab.
const SYS_EXPORTABLE_VIEWS = new Set(["all", "ignored", "duplicates", "grp:genre", "grp:album", "grp:artist"]);
function sysMenuItems(view) {
  const items = [{icon: ICONS.columns3, label: t("cols.view_menu"), action: () => setViewColumnView(view)}];
  const exportable = SYS_EXPORTABLE_VIEWS.has(view.id);
  if (exportable) items.push({sep: true});
  // "Zur Warteschlange hinzufuegen" fuer die drei "Aufraeumen"-Listen --
  // anders als der Export ergibt das fuer Ausgeblendet/Duplikate keinen
  // typischen Zweck. Fuer Merklisten uebernimmt das der normale
  // Playlist-Menuepunkt (nodeMenuItems()), sie sind keine reinen Views mehr.
  if (view.id.startsWith("grp:")) {
    items.push({
      icon: ICONS.listEnd, label: t("tree.to_queue"),
      action: () => {
        const rows = viewRows(view);
        if (!rows.length) { note(t("tree.empty_list"), "soft"); return; }
        rows.forEach(r => queueState.tracks.push(r));
        queueState.manual = true;
        renderPlayerBar(); renderQueuePopup();
        note(t("tree.queued", {
          count: rows.length, name: view.label,
          track_word: rows.length === 1 ? t("tree.track_singular") : t("tree.track_plural"),
        }));
      },
    });
  }
  if (exportable) {
    items.push({
      icon: ICONS.fileMusic, label: t("tree.export_m3u"),
      action: () => {
        const rows = viewRows(view);
        if (!rows.length) { note(t("tree.empty_list"), "soft"); return; }
        downloadM3u(rows, view.label);
      },
    });
  }
  return items;
}
function openViewMenu(view, el) {
  openContextMenu(sysMenuItems(view), el);
}

// Musik.app/Rekordbox-Wurzelknoten: laden nach dem ersten Aufklappen nie
// von selbst neu (st.loaded bleibt stehen, siehe loadExtTree()) -- einziger
// Menuepunkt stoesst das gezielt wieder an.
function extMenuItems(source) {
  return [{icon: ICONS.reanalyse, label: t("tree.reload"), action: () => reloadExtTree(source)}];
}
function openExtMenu(source, el) {
  openContextMenu(extMenuItems(source), el);
}
function reloadExtTree(source) {
  const st = EXT_TREES[source];
  st.nodes = []; st.loaded = false; st.error = null;
  for (const key of [...EXT_CONTENTS.keys()]) if (key.startsWith(`${source}:`)) EXT_CONTENTS.delete(key);
  loadExtTree(source);
}

// Schlichte Ja/Nein-Rueckfrage. Bewusst eine EIGENE Ueberlagerung
// (#askOverlay) statt #confirmOverlay: dessen Text und Knopfbeschriftung
// stehen fest im HTML ("In den Papierkorb", data-i18n="confirm.trash_note")
// und werden von askTrash() unveraendert uebernommen -- wer sie hier
// ueberschreibt, veraendert stillschweigend den naechsten Papierkorb-Dialog.
function askConfirmSimple(title, note) {
  return new Promise(resolve => {
    const ov = document.getElementById("askOverlay");
    document.getElementById("askTitle").textContent = title;
    document.getElementById("askNote").textContent = note || "";
    ov.style.display = "flex";
    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      resolve(answer);
    };
    document.getElementById("askNo").onclick = () => done(false);
    document.getElementById("askYes").onclick = () => done(true);
    document.onkeydown = ev => { if (ev.key === "Escape") done(false); };
  });
}

// ── Breite der Seitenleiste ───────────────────────────────────────────────
// Als CSS-Variable am Wurzelelement, damit .sidebar und alles, was sich an
// ihr ausrichtet, ueber eine einzige Stelle laufen.
// Untergrenze so gewaehlt, dass auch ein Knoten auf zweiter Ebene (Einrueckung,
// Symbol, Trackzahl, Schloss, Menue) noch Platz fuer einen erkennbaren Teil des
// Namens laesst -- darunter blieben nur noch die Symbole uebrig.
const SIDEBAR_MIN = 210, SIDEBAR_MAX = 480;

function setSidebarWidth(px) {
  const w = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, Math.round(px)));
  state.sidebarW = w;
  document.documentElement.style.setProperty("--sidebar-w", w + "px");
}

// Ziehgriff. Bewusst mit mousedown + Fenster-Listenern wie setupSeekDrag()
// weiter unten -- gleiche Mechanik im ganzen Projekt.
function initSidebarResize() {
  const grip = document.getElementById("sidebarGrip");
  const bar = document.getElementById("sidebar");
  if (!grip || !bar) return;
  grip.addEventListener("mousedown", ev => {
    ev.preventDefault();
    const startX = ev.clientX;
    const startW = bar.getBoundingClientRect().width;
    document.body.classList.add("sidebar-dragging");
    const onMove = e => setSidebarWidth(startW + (e.clientX - startX));
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("sidebar-dragging");
      saveFilters();
      // Die eingeklebte Kopfzeile ist ein position:fixed-Nachbau und misst
      // die Tabelle nur beim Scrollen nach -- nach dem Ziehen einmal von
      // Hand nachziehen, sonst steht sie bis zum naechsten Scroll daneben.
      syncTableWidths(); syncStickyHeadContent(); syncStickyHeadPosition();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
}

// --- Kennzahlen ---
function updateCards() {
  // Die frueheren Verdikt-Schaltflaechen ueber der Suche sind entfallen: die
  // feste Ordnergruppe "Pruef listen" im Seitenbaum (db._SYSTEM_PLAYLISTS)
  // leistet dasselbe an einer Stelle statt an zweien, und fuer eine
  // Einschraenkung INNERHALB einer Liste gibt es den Suchparameter /Status.
  const mcCount = live().filter(r => r.mc).length;
  renderTree();

  // live() statt DATA: eine gerade per "Aus der Liste entfernen" gestrichene
  // Zeile (removed=1) ist aus der Liste raus und darf die Leiste nicht
  // weiter offen halten.
  const gone = live().filter(r => r.gone).length;
  const gd = document.getElementById("toolbarGoneActions");
  gd.style.display = gone ? "" : "none";
  const goneTitle = gone
    ? `${gone.toLocaleString("de-DE")} ${gone === 1 ? t("gone.file_singular") : t("gone.file_plural")} ${t("gone.suffix")}`
    : "";
  gd.innerHTML = gone
    ? `<button class="act warn-orange" id="relinkBtn" title="${esc(goneTitle)}"><span class="btnicon">${ICONS.fileSearchCorner}</span>` +
      `<span class="label">${esc(t("gone.button_relocate"))}</span></button>` +
      `<button class="act danger" id="pruneBtn" title="${esc(goneTitle)}"><span class="btnicon">${ICONS.brushCleaning}</span>` +
      `<span class="label">${esc(t("gone.button_prune"))}</span></button>`
    : "";
  const pb = document.getElementById("pruneBtn");
  if (pb) pb.onclick = pruneMissing;
  const rb2 = document.getElementById("relinkBtn");
  if (rb2) rb2.onclick = () => relinkMissing(null, rb2);

  const ign = live().filter(r => r.ig).length;
  const merk = merkPlaylists();
  const fav = live().filter(r => merk.some(n => PLAYLIST_SETS[n.id] && PLAYLIST_SETS[n.id].has(r.p))).length;
  document.getElementById("sub").textContent =
    t("summary.analyzed", {count: META.total.toLocaleString("de-DE")}) +
    (ign ? " · " + t("summary.hidden", {count: ign.toLocaleString("de-DE")}) : "") +
    (fav ? " · " + t("summary.favorites", {count: fav.toLocaleString("de-DE")}) : "") +
    (mcCount ? " · " + t("summary.corrected", {count: mcCount.toLocaleString("de-DE")}) : "") +
    " · " + t("summary.as_of", {date: META.generated});
}

// --- Ausblenden: Speicherung ---
async function persistIgnored(path, flag) {
  if (apiMode) {
    const res = await fetch("/api/ignore", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path, ignored: flag})
    });
    if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
    return;
  }
  const set = new Set(JSON.parse(localStorage.getItem(LS_KEY) || "[]"));
  flag ? set.add(path) : set.delete(path);
  localStorage.setItem(LS_KEY, JSON.stringify([...set]));
}

async function persistCorrected(path, flag) {
  if (apiMode) {
    const res = await fetch("/api/correct", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path, corrected: flag})
    });
    if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
    return;
  }
  const set = new Set(JSON.parse(localStorage.getItem(LS_CORRECTED) || "[]"));
  flag ? set.add(path) : set.delete(path);
  localStorage.setItem(LS_CORRECTED, JSON.stringify([...set]));
}

// Merken ist unabhaengig von Ignoriert/Korrigiert (keine gegenseitige
// Exklusivitaet) und ein Track kann gleichzeitig in mehreren der markierten
// Playlisten stehen. Laeuft wie jede andere Playlist-Zuordnung ueber
// /api/playlist-items (siehe playlistApi() weiter unten) statt eines
// eigenen Endpunkts -- Merken IST eine Playlist-Mitgliedschaft.
async function setFavorite(r, playlistId, flag) {
  if (!PLAYLIST_SETS[playlistId]) PLAYLIST_SETS[playlistId] = new Set();
  const before = PLAYLIST_SETS[playlistId].has(r.p);
  flag ? PLAYLIST_SETS[playlistId].add(r.p) : PLAYLIST_SETS[playlistId].delete(r.p);
  updateCards(); render();
  const listName = (merkPlaylists().find(n => n.id === playlistId) || {}).name || playlistId;
  try {
    await playlistApi("/api/playlist-items",
      {op: flag ? "add" : "remove", id: playlistId, paths: [r.p]});
    PLAYLIST_ITEMS[playlistId] = [...PLAYLIST_SETS[playlistId]];
    note(t(flag ? "toast.favorite_added" : "toast.favorite_removed", {list: listName}));
  } catch (err) {
    before ? PLAYLIST_SETS[playlistId].add(r.p) : PLAYLIST_SETS[playlistId].delete(r.p);
    updateCards(); render();
    note(t("toast.save_failed", {error: err.message}), true);
  }
}

async function setIgnored(r, flag) {
  const before = {ig: r.ig, mc: r.mc};
  r.ig = flag ? 1 : 0;
  if (flag) { r.mc = 0; }
  updateCards(); render();
  try {
    await persistIgnored(r.p, flag);
  } catch (err) {
    r.ig = before.ig; r.mc = before.mc;
    updateCards(); render();
    note(t("toast.save_failed", {error: err.message}), true);
  }
}

async function setCorrected(r, flag) {
  const before = {mc: r.mc, ig: r.ig};
  r.mc = flag ? 1 : 0;
  if (flag) { r.ig = 0; }
  updateCards(); render();
  try {
    await persistCorrected(r.p, flag);
  } catch (err) {
    r.mc = before.mc; r.ig = before.ig;
    updateCards(); render();
    note(t("toast.save_failed", {error: err.message}), true);
  }
}

// Knopf "Kein Duplikat" im Gruppenkopf der Duplikate-Ansicht: schaltet ALLE
// aktuell lebenden Mitgliedspfade der Gruppe auf einmal um (r.dg ist
// ephemer -- deshalb frisch aus DATA gelesen statt zwischengespeichert).
// Sind schon alle bestaetigt, nimmt der Klick die Bestaetigung zurueck,
// sonst bestaetigt er die ganze Gruppe.
async function toggleDupGroupDismissed(gid) {
  const members = DATA.filter(r => r.dg === gid && !r.removed);
  if (!members.length) return;
  const flag = !members.every(r => r.dd);
  const before = members.map(r => r.dd);
  members.forEach(r => { r.dd = flag ? 1 : 0; });
  updateCards(); render();
  try {
    const res = await fetch("/api/dup-dismiss-bulk", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths: members.map(r => r.p), flag}),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    note(t(flag ? "toast.dup_dismissed" : "toast.dup_undismissed", {count: members.length}));
  } catch (err) {
    members.forEach((r, i) => { r.dd = before[i]; });
    updateCards(); render();
    note(t("toast.save_failed", {error: err.message}), true);
  }
}

// Frueher gab es zwei getrennte Meldungswege: eine stets sichtbare Zeile
// (#syncnote, note()) fuer Sync-/Aktions-Rueckmeldungen und einen separaten
// Toast fuer Erfolgsmeldungen. Jetzt landet alles im selben Toast -- note()
// ruft nur noch toast() auf. Die Faerbung erkennt vier Bedeutungen an "warn"
// statt an einem eigenen Parameter je Aufrufstelle (~50 Stellen): warn===
// "soft" -> gelb (Server/Programm noetig, Konfiguration fehlt, nichts zu
// tun), warn=true -> rot (echter Fehlschlag), sonst + Text endet auf "…" ->
// blau (laeuft noch/Zwischenstand), sonst gruen (abgeschlossen).
// Frueher wurde die gelbe Faerbung an typischen deutschen Formulierungen im
// Text erkannt (TOAST_SOFT_PATTERN-Regex) -- das haette mit einer zweiten
// Sprache lautlos aufgehoert zu funktionieren, weil die Erkennung am
// deutschen Wortlaut haengt, nicht am Schluessel. Aufrufstellen mit "gelber"
// Bedeutung uebergeben deshalb jetzt explizit "soft" statt true.
function toastLevel(text, warn) {
  if (warn === "soft") return "warn";
  if (warn) return "error";
  return /…\s*$/.test(String(text).trim()) ? "info" : "ok";
}

// sticky=true: verschwindet nicht von selbst, sondern nur ueber den
// Schliessen-Knopf -- fuer Hinweise, die der Nutzer aktiv zur Kenntnis nehmen
// muss (z.B. "Rekordbox ist geoeffnet"), statt in den ueblichen 8s zu
// verpassen. Den Schliessen-Knopf selbst gibt es unabhaengig davon auf JEDEM
// Toast, damit auch nicht-sticky Toasts sich vorzeitig manuell wegklicken
// lassen, statt auf den Timer warten zu muessen.
function toast(text, warn, sticky) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = "toast " + toastLevel(text, warn) + (sticky ? " sticky" : "");
  const textEl = document.createElement("span");
  textEl.className = "toast-text";
  textEl.textContent = text;
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "toast-close";
  closeBtn.setAttribute("aria-label", t("toast.close"));
  closeBtn.textContent = "×";
  const dismiss = () => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 250);
  };
  closeBtn.onclick = dismiss;
  el.append(textEl, closeBtn);
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  if (!sticky) {
    setTimeout(dismiss, 8000);
  }
}

function note(text, warn, sticky) { toast(text, warn, sticky); }

// Fortschritts-Toast fuer mehrstufige Aktionen (mehrere Dateien nacheinander,
// z.B. "Dateien öffnen"/"neu analysieren" in den Einzelprüfungen, oder ein
// einzelner Server-Aufruf ohne Zwischenstand wie Rekordbox-/Music-Abgleich)
// -- bleibt offen und fuellt unten einen gruenen Balken, statt wie toast()
// nach fester Zeit zu verschwinden. update() waehrend des Laufs, danach
// EINMAL done() (bleibt kurz mit vollem Balken stehen) oder fail().
//
// {indeterminate:true}: kein echter Fortschritt bekannt (der Server liefert
// bei Rekordbox-/Music-Abgleich erst am Ende ein Ergebnis, keine
// Zwischenstaende) -- der Balken laeuft dann als wandernder Streifen statt
// eines Fuellstands; update(pct, …) schaltet bei Bedarf auf echten
// Fortschritt um.
//
// Mehrere gleichzeitige Aufrufe sind bewusst unabhaengig voneinander: jeder
// bekommt sein eigenes <div>, eigene Closure-Variablen (closed/el/…), keine
// gemeinsame Instanz -- zwei parallele Aktionen (z.B. Rekordbox- UND
// Music-Abgleich gleichzeitig) zeigen zwei Toasts, die sich nicht
// gegenseitig ueberschreiben oder vorzeitig schliessen. #toasts stapelt sie
// per Flexbox untereinander (siehe app.css).
function progressToast(text, opts) {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast progress info";
  el.innerHTML = `<div class="toast-text"></div><div class="toast-bar"><i></i></div>`;
  const textEl = el.querySelector(".toast-text");
  const barWrap = el.querySelector(".toast-bar");
  const barEl = barWrap.querySelector("i");
  if (opts && opts.indeterminate) barWrap.classList.add("indeterminate");
  textEl.textContent = text;
  if (host) host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));

  let closed = false;
  const close = delayMs => setTimeout(() => {
    if (closed) return;
    closed = true;
    el.classList.remove("show");
    setTimeout(() => el.remove(), 250);
  }, delayMs);

  return {
    update(pct, text2) {
      barWrap.classList.remove("indeterminate");
      barEl.style.width = Math.max(0, Math.min(100, pct)) + "%";
      if (text2 != null) textEl.textContent = text2;
    },
    done(text2, warn) {
      barWrap.classList.remove("indeterminate");
      barEl.style.width = "100%";
      if (text2 != null) {
        textEl.textContent = text2;
        el.className = "toast progress " + toastLevel(text2, warn) + " show";
      }
      close(1200);              // voller Balken soll kurz sichtbar bleiben
    },
    fail(text2) {
      barWrap.classList.remove("indeterminate");
      // Voll statt auf halbem Weg stehenbleibend -- Farbe kommt ueber
      // .toast.error .toast-bar i (kein Gruen bei einem Fehlschlag).
      barEl.style.width = "100%";
      textEl.textContent = text2;
      el.className = "toast progress error show";
      close(3800);              // wie ein normaler Fehler-Toast
    },
  };
}

// Die Markierungen im report.html sind der Stand vom letzten Backen. Alles,
// was danach markiert wurde, fehlt dort — deshalb bei laufendem Server immer
// den Stand aus der Datenbank nachziehen. Sie ist die maßgebliche Quelle.
// Dauerhaft ausgeblendete Zusammenfuehrungs-Vorschlaege (Genre/Interpret/
// Album) -- kanonisch sortierte Paar-Schluessel ("a\u0000b"), siehe
// mergePairKey(). Ueber denselben Live-Nachzug wie ignored/favorites/... statt
// eines eigenen Ladepfads.
let MERGE_DISMISSED = {genre: new Set(), artist: new Set(), album: new Set()};
function mergePairKey(a, b) { return [a, b].sort().join("\u0000"); }

async function syncMarks() {
  try {
    const [ign, cor, ddm, rbx, mad, pls, mgd] = await Promise.all([
      fetch("/api/ignored", {cache: "no-store"}).then(r => r.json()),
      fetch("/api/corrected", {cache: "no-store"}).then(r => r.json()),
      fetch("/api/dup-dismissed", {cache: "no-store"}).then(r => r.json()),
      fetch("/api/rekordbox", {cache: "no-store"}).then(r => r.json()),
      fetch("/api/music-added", {cache: "no-store"}).then(r => r.json()),
      // Der Baum haengt am selben Live-Nachzug: das gebackene META traegt nur
      // den Stand des letzten Report-Neubaus, geaendert wird ohne Rebuild.
      fetch("/api/playlists", {cache: "no-store"}).then(r => r.json()),
      fetch("/api/merge-dismissed", {cache: "no-store"}).then(r => r.json()),
    ]);
    for (const field of ["genre", "artist", "album"]) {
      MERGE_DISMISSED[field] = new Set(
        (mgd.dismissed && mgd.dismissed[field] || []).map(p => mergePairKey(p.a, p.b)));
    }
    PLAYLISTS = pls.playlists || [];
    PLAYLIST_ITEMS = pls.items || {};
    rebuildPlaylistIndex();
    rebuildPlaylistViews();
    rebuildMerkViews();
    recomputeAllSmart();
    if (!VIEWS.some(v => v.id === state.view)) {
      state.view = "all";
      syncColumnsForView();
    }
    const ignored   = new Set(ign.ignored || []);
    const corrected = new Set(cor.corrected || []);
    const dupDismissed = new Set(ddm.dup_dismissed || []);
    const rekordbox = new Set(rbx.paths || []);
    const musicAdded = mad.added || {};
    for (const r of DATA) {
      r.ig = ignored.has(r.p)   ? 1 : 0;    // auch zurücknehmen, nicht nur setzen
      r.mc = corrected.has(r.p) ? 1 : 0;
      r.dd = dupDismissed.has(r.p) ? 1 : 0;
      r.rb = rekordbox.has(r.p) ? 1 : 0;
      r.im = Object.prototype.hasOwnProperty.call(musicAdded, r.p) ? 1 : 0;
      r.da = musicAdded[r.p] || 0;
    }
  } catch (err) {
    note(t("toast.marks_load_failed", {error: err.message}), true);
  }
}

async function initStorage() {
  try {
    const res = await fetch("/api/ping", {cache: "no-store"});
    if (res.ok) {
      apiMode = true;
      const info = await res.json();
      // Ohne Server gibt es nichts zu beenden -- dann bleibt der Knopf weg,
      // statt beim Klick in einen Netzwerkfehler zu laufen.
      document.getElementById("btnQuit").style.display = "";
      // Ohne Server laesst sich keine Liste speichern -- dann bleiben die
      // Knoepfe weg, statt beim Klick in einen Netzwerkfehler zu laufen
      // (gleiche Regel wie bei btnQuit). Der Baum selbst funktioniert auch
      // ohne Server, er zeigt dann nur die festen Knoten.
      document.querySelector(".treehead-actions").style.display = "";
      await syncMarks();
      fetch("/api/settings").then(r => r.json()).then(s => {
        // Vor dem Fetch stand hier schon die Browsersprache (siehe
        // resolveLang()) -- eine explizite Wahl in den Einstellungen
        // ueberschreibt sie jetzt. refreshI18nCache()/applyStaticI18n()
        // holen alles nach, was mit der alten Sprache schon gebaut war
        // (Modul-Konstanten wie labels/FIELD_LABELS, data-i18n-Attribute);
        // renderHead()/render() unten bauen die Tabelle selbst neu.
        LANG = resolveLang(s.values.ui_language);
        STRINGS = I18N[LANG] || {};
        refreshI18nCache();
        applyStaticI18n();
        applyToolbarLabels();
        // apiMode ist erst seit dem Ping oben oberhalb dieser Funktion sicher
        // gesetzt -- ein direkt beim DOMContentLoaded restaurierter
        // Warteschlangen-Stand (loadQueueState()) kann das Cover deshalb beim
        // allerersten Zeichnen noch faelschlich verstecken. Hier einmal
        // erneut zeichnen, sobald apiMode feststeht.
        renderPlayerBar();
        // Bewusst KEIN Rueckfall auf s.detected_editor (gleiche Begruendung
        // wie bei MIK_NAME/REKORDBOX_NAME): der Knopf soll erst erscheinen,
        // wenn ein Editor in den Einstellungen tatsaechlich ausgewaehlt ist.
        EDITOR_NAME = (s.values.external_editor || "").split("/").pop().replace(/\.app$/, "");
        // DAWs haben ohnehin nie einen Auto-detect-Fallback gehabt (kein
        // einheitlicher Name zum Erraten, siehe media.daw_path) --
        // inzwischen gilt dasselbe (keine Detected-Uebernahme) auch fuer
        // Editor/MIK/Rekordbox oben/unten.
        DAW_NAME = (s.values.external_daw || "").split("/").pop().replace(/\.app$/, "");
        // Bewusst KEIN Rueckfall auf s.detected_mik: die MIK-Knoepfe sollen
        // nur erscheinen, wenn das Feld in den Einstellungen tatsaechlich
        // gesetzt ist -- ein automatisch gefundenes, aber nie ausgewaehltes
        // MIK zeigte die Knoepfe sonst trotzdem an, obwohl die Einstellung
        // leer aussieht (Nutzer-Feedback).
        MIK_NAME = (s.values.external_mik || "").split("/").pop().replace(/\.app$/, "");
        // Kein Auto-detect wie bei MIK/Editor: Music.app ist auf jedem Mac
        // vorhanden, die Auswahl hier ist ein bewusstes Ein-/Ausschalten der
        // gesamten Integration (Baum, Knoepfe, Import, Sync, Cover-Autofill),
        // kein Programmpfad zum Starten (siehe config.py:external_music).
        MUSIC_NAME = (s.values.external_music || "").split("/").pop().replace(/\.app$/, "");
        // Bewusst KEIN Rueckfall auf s.detected_rekordbox (gleiche
        // Begruendung wie bei MIK_NAME oben): die gesamte Rekordbox-
        // Integration (Seitenbaum-Ast, Abgleich-Knopf, Zeilen-/Sammel-Icons,
        // Statistik-Abschnitt) soll erst erscheinen, wenn Rekordbox in den
        // Einstellungen tatsaechlich ausgewaehlt ist -- ein automatisch
        // gefundenes, aber nie ausgewaehltes Rekordbox zeigte sie sonst
        // trotzdem an.
        REKORDBOX_NAME = (s.values.external_rekordbox || "").split("/").pop().replace(/\.app$/, "");
        // Ohne ausgewaehltes Rekordbox bzw. ohne ausgewaehlte Music App
        // machen die Abgleich-Knoepfe nichts -- sie bleiben komplett
        // ausgeblendet statt nur ausgegraut, gleiches Prinzip wie beim
        // DAW-Knopf (siehe DAW_NAME).
        document.getElementById("btnRekordboxSync").style.display = REKORDBOX_NAME ? "" : "none";
        document.getElementById("btnMusicAddedSync").style.display = MUSIC_NAME ? "" : "none";
        REKORDBOX_PLAYLIST = s.values.rekordbox_playlist || "";
        RENAME_PATTERN = s.values.rename_pattern || "";
        REKORDBOX_QUALITY_CHECK = s.values.rekordbox_quality_check !== undefined
          ? !!s.values.rekordbox_quality_check : true;
        REKORDBOX_MIN_CUTOFF_KHZ = typeof s.values.rekordbox_min_cutoff_khz === "number"
          ? s.values.rekordbox_min_cutoff_khz : 19;
        DEFAULT_SEARCH_FILTERS = Array.isArray(s.values.default_search_filters)
          ? s.values.default_search_filters : [];
        refreshDefaultFilters();
        state.pinned = !!s.values.pin_filter_bar;
        applyPinned();
        applyFontSize(s.values.font_size);
        applyTheme(s.values.theme);
        applyAccentColor(s.values.accent_color);
        state.infiniteScroll = !!s.values.infinite_scroll;
        state.searchTolerance = typeof s.values.search_typo_tolerance === "number"
          ? s.values.search_typo_tolerance : 0.8;
        state.searchAcMinChars = typeof s.values.search_autocomplete_min_chars === "number"
          ? s.values.search_autocomplete_min_chars : 3;
        if (Array.isArray(s.shops) && s.shops.length) ALL_SHOPS = s.shops;
        // column_order/hidden_columns/column_widths kommen vom Server je
        // Ansicht getrennt (siehe settings.py::describe()) -- beide Ansichten
        // hier befuellen, nicht nur die gerade aktive (state.colOrder &Co.
        // loesen ueber die Getter/Setter ohnehin nur auf die aktive auf).
        {
          const orderByLayout = (s.column_order && typeof s.column_order === "object"
            && !Array.isArray(s.column_order)) ? s.column_order : {};
          const hiddenByLayout = (s.hidden_columns && typeof s.hidden_columns === "object"
            && !Array.isArray(s.hidden_columns)) ? s.hidden_columns : {};
          const widthsByLayout = (s.column_widths && typeof s.column_widths === "object"
            && !Array.isArray(s.column_widths)) ? s.column_widths : {};
          for (const layout of ["edit", "player"]) {
            const std = state.columnsByLayout[layout];
            const savedOrder = orderByLayout[layout];
            if (Array.isArray(savedOrder) && savedOrder.length) {
              // Neue Spalten (nach dem Speichern der Reihenfolge hinzugekommen)
              // haengt normalizeColumnStore() hinten an, statt sie
              // verschwinden zu lassen.
              std.order = savedOrder;
            }
            const savedHidden = hiddenByLayout[layout];
            const savedWidths = widthsByLayout[layout];
            if (Array.isArray(savedHidden) && savedHidden.length) std.hidden = savedHidden;
            if (savedWidths && typeof savedWidths === "object"
                && Object.keys(savedWidths).length) std.widths = savedWidths;
            normalizeColumnStore(std);
            if (!(Array.isArray(savedHidden) && savedHidden.length)
                && layout === state.layout && std.hidden.length) {
              // Bestehende Auswahl aus localStorage (vor dieser Server-Speicherung
              // eingestellt) einmalig serverseitig uebernehmen, statt sie stumm
              // durch das leere Server-Feld zu ersetzen. Nur fuer die gerade
              // aktive Ansicht -- die andere hat keine localStorage-Altlast.
              postStandardColumns();
            }
          }
        }
        // Gespeicherte Spaltenansichten und ihre Zuordnung: der Server ist
        // hier die Wahrheit, die localStorage-Fassung aus loadFilters() war
        // nur die Ueberbrueckung bis hierher.
        if (Array.isArray(s.column_views)) adoptColumnViewsFromServer(s);
        lastColumnStoreKey = columnStoreKey();
        // Eigene Spaltenkonfiguration der Einzelprueflungen -- unabhaengig
        // vom obigen Block, kein Ansicht-Split (siehe state.dropColumns).
        if (Array.isArray(s.drop_column_order) && s.drop_column_order.length) {
          state.dropColumns.order = s.drop_column_order;
        }
        if (Array.isArray(s.drop_hidden_columns) && s.drop_hidden_columns.length) {
          state.dropColumns.hidden = s.drop_hidden_columns;
        }
        if (s.drop_column_widths && typeof s.drop_column_widths === "object"
            && Object.keys(s.drop_column_widths).length) {
          state.dropColumns.widths = s.drop_column_widths;
        }
        normalizeColumnStore(state.dropColumns);
        renderHead();
        renderColsMenu();
        renderTree();
        render();
        updateCards();
        applyDropzoneLabels();
        renderDrops();
        if (document.getElementById("dropColsMenu")?.style.display !== "none") renderDropColsMenu();
      }).catch(() => {});
      if (info.tools_ok === false) {
        note(t("toast.ffmpeg_missing", {msg: info.tools_message || ""}), "soft");
      }
      return;
    }
  } catch (e) { /* kein Server — Datei direkt geoeffnet */ }

  try {
    const stored = new Set(JSON.parse(localStorage.getItem(LS_KEY) || "[]"));
    if (stored.size) for (const r of DATA) if (stored.has(r.p)) r.ig = 1;
    const fixed = new Set(JSON.parse(localStorage.getItem(LS_CORRECTED) || "[]"));
    if (fixed.size) for (const r of DATA) if (fixed.has(r.p)) r.mc = 1;
    note(t("toast.no_server_browser_only"), "soft");
  } catch (e) {
    note(t("toast.no_server_no_storage"), "soft");
  }
}

document.getElementById("q").oninput = e => { state.q = e.target.value.toLowerCase(); state.shown = PAGE; render(); };

let SEARCH_HELP_ENTRIES = [
  [t("search.help.album.token"), t("field.album"), t("search.help.album.example")],
  [t("search.help.artist.token"), t("field.artist"), t("search.help.artist.example")],
  [t("search.help.title_field.token"), t("field.title"), t("search.help.title_field.example")],
  [t("search.help.genre.token"), t("field.genre"), t("search.help.genre.example")],
  [t("search.help.year.token"), t("search.help.year.desc"), t("search.help.year.example")],
  [t("search.help.bpm.token"), t("search.help.bpm.desc"), t("search.help.bpm.example")],
  [t("search.help.composer.token"), t("field.composer"), t("search.help.composer.example")],
  [t("search.help.album_artist.token"), t("field.album_artist"), t("search.help.album_artist.example")],
  [t("search.help.comment.token"), t("search.help.comment.desc"), t("search.help.comment.example")],
  [t("search.help.path.token"), t("search.help.path.desc"), t("search.help.path.example")],
  [t("search.help.duration.token"), t("search.help.duration.desc"), t("search.help.duration.example")],
  [t("search.help.file.token"), t("search.help.file.desc"), t("search.help.file.example")],
  [t("search.help.exclude.token"), t("search.help.exclude.desc"), t("search.help.exclude.example")],
  [t("search.help.declared.token"), t("search.help.declared.desc"), t("search.help.declared.example")],
  [t("search.help.confidence.token"), t("search.help.confidence.desc"), t("search.help.confidence.example")],
  [t("search.help.added.token"), t("search.help.added.desc"), t("search.help.added.example")],
  [t("search.help.status.token"), t("search.help.status.desc"), t("search.help.status.example")],
  [t("search.help.hidden.token"), t("search.help.hidden.desc"), t("search.help.hidden.example")],
];

// ── "/" in der Suche -> Vorschlagsliste der Filterparameter ─────────────
// Nutzt dieselben Eintraege wie die Suchhilfe (SEARCH_HELP_ENTRIES) -- ein
// neuer Filter braucht so nur eine gepflegte Liste statt zweier. Ein Eintrag
// mit mehreren Alias-Schreibweisen ("/Dauer, /Länge") wird zu je einem
// eigenen Vorschlag aufgespalten.
let SEARCH_FILTER_SUGGESTIONS = SEARCH_HELP_ENTRIES.flatMap(([p, desc]) =>
  p.split(",").map(tok => ({token: tok.trim(), desc})));

// Holt fuer ein Feld (Zeilenschluessel wie bei SEARCH_FIELD_ALIASES, z.B.
// "al" fuer Album) alle in DATA tatsaechlich vorkommenden, unterschiedlichen
// Werte -- Grundlage fuer die Wertvorschlaege nach einem ausgeschriebenen
// Filter (siehe attachSearchFilterAutocomplete()). "__ext" ist kein
// Zeilenfeld, sondern die Dateiendung (fileExt(r.p)) fuer "/datei". Keine
// Zwischenspeicherung -- DATA ist bei ein paar tausend Zeilen billig genug
// pro Tastenanschlag neu zu durchlaufen, und ein Cache waere nach einem Scan
// oder einer Tag-Aenderung sofort veraltet.
function fieldValuesForAutocomplete(field) {
  // "/Status" schlaegt die uebersetzten Beschriftungen vor, nicht die
  // internen Schluessel aus r.v -- getippt wird ja ebenfalls die Beschriftung.
  if (field === "v") {
    return [...order.map(k => labels[k]), t("chip.manual_corrected")];
  }
  const set = new Set();
  for (const r of DATA) {
    const v = field === "__ext" ? fileExt(r.p) : r[field];
    if (v !== undefined && v !== null && v !== "") set.add(String(v));
  }
  return [...set].sort(COLLATOR_LOOSE.compare);
}

// Haengt an ein Suchfeld eine Vorschlagsliste an, mit zwei Modi:
// - "filter": an der Schreibmarke steht ein frisch begonnenes "/wort" (kein
//   Leerzeichen mehr dahinter) -- schlaegt Filterparameter vor (wie bisher).
// - "value": direkt davor steht ein bereits vollstaendig ausgeschriebener,
//   erkannter Filter ("/Album ", danach beliebiger Text) -- schlaegt
//   tatsaechlich in der Bibliothek vorkommende Werte fuer dieses Feld vor
//   (Substring-Treffer, Praefix-Treffer zuerst), auch wenn noch nichts
//   getippt wurde. Nur fuer Text-Felder sinnvoll -- numerische/Datums-Filter
//   (Dauer/Deklariert/Konfidenz/Hinzugefuegt) und die Verneinung ("/No")
//   haben keine feste Werteliste und werden uebersprungen.
// Ersetzt beim Uebernehmen jeweils nur den betroffenen Abschnitt (Filtername
// bzw. Wert), nicht das ganze Suchfeld, damit bereits Getipptes erhalten
// bleibt.
// Verzoegerung der Werte-Vorschlaege (nicht der Parameternamen-Vorschlaege,
// die bleiben sofort) -- reine Wahrnehmungs-Optimierung, kein Setting.
const VALUE_AC_DEBOUNCE_MS = 200;
// Felder mit fester, kurzer Werteliste: Vorschlaege sofort und ohne
// Mindestlaenge (siehe open()). Nur "/Status" -- alle anderen Werte kommen
// aus DATA und waeren ohne Schwellwert eine unbrauchbar lange Liste.
const INSTANT_VALUE_FIELDS = new Set(["v"]);

function attachSearchFilterAutocomplete(el, list) {
  let items = [], active = -1, tokStart = -1, tokEnd = -1, debounceTimer = 0;

  const close = () => {
    clearTimeout(debounceTimer);
    list.style.display = "none"; list.innerHTML = ""; items = []; active = -1;
  };

  const currentToken = () => {
    const pos = el.selectionStart;
    const before = el.value.slice(0, pos);
    const m = /(?:^|\s)(\/\S*)$/.exec(before);
    if (!m) return null;
    return {text: m[1], start: pos - m[1].length, end: pos};
  };

  // Text VOR der Schreibmarke mit demselben Tokenizer zerlegen, den auch die
  // Suche benutzt -- so gilt fuer die Vorschlaege dieselbe Elementgrenze
  // (Wort/Phrase/Gruppe) wie fuer die Filterung, statt einer zweiten,
  // leicht abweichenden Regex-Grenzziehung.
  const tokensBeforeCaret = () => {
    const pos = el.selectionStart;
    const toks = tokenizeSearch(el.value.slice(0, pos).toLowerCase());
    return {pos, last: toks[toks.length - 1] || null, prev: toks[toks.length - 2] || null};
  };

  // Steht die Schreibmarke im Wert eines erkannten Filters? Zwei Faelle: der
  // Wert wird gerade getippt ("/Genre ho|") oder er ist noch leer, der
  // Parameter aber schon abgeschlossen ("/Genre |").
  const currentValueToken = () => {
    const {pos, last, prev} = tokensBeforeCaret();
    if (!last) return null;
    let param = null, text = "", start = pos;
    if (last.type === "param" && last.end < pos) param = last;
    else if (last.type === "value" && prev && prev.type === "param") {
      param = prev; text = last.values.map(v => v.text).join(" "); start = last.start;
    }
    if (!param) return null;
    // "/No" und "/Ausgeblendet" haben keine Werteliste; numerische und
    // Datumsfilter ebenso wenig (dort waeren ">", Bereiche usw. gefragt).
    if (NEGATE_ALIASES.has(param.word) || FLAG_ALIASES.has(param.word)) return null;
    const field = EXT_ALIASES.has(param.word) ? "__ext" : SEARCH_FIELD_ALIASES[param.word];
    if (NUMERIC_OP_FIELDS.has(field) || DATE_OP_FIELDS.has(field)) return null;
    return {field, text, start, end: pos};
  };

  // Freies Suchwort an der Schreibmarke -- anders als frueher an jeder Stelle
  // des Suchtextes, nicht nur vor dem ersten "/": seit der Elementgrenze
  // (siehe parseSearchQuery()) ist auch nach einem "/Parameter Wert" wieder
  // freier Text moeglich. Gehoert das Element zu einem Parameter, ist
  // currentValueToken() zustaendig -- ausser bei "/No"/"/Ausgeblendet", nach
  // denen wieder freier Text folgt.
  const currentFreeToken = () => {
    const {pos, last, prev} = tokensBeforeCaret();
    if (!last || last.type !== "value") return null;
    if (prev && prev.type === "param"
        && !NEGATE_ALIASES.has(prev.word) && !FLAG_ALIASES.has(prev.word)) return null;
    const text = last.values.map(v => v.text).join(" ");
    if (!text) return null;
    return {text, start: last.start, end: pos};
  };

  const highlight = () => {
    list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("on", i === active));
  };

  const apply = i => {
    if (i < 0 || i >= items.length) return;
    const insert = items[i].insert;
    el.value = el.value.slice(0, tokStart) + insert + " " + el.value.slice(tokEnd);
    const pos = tokStart + insert.length + 1;
    close();
    el.focus();
    el.setSelectionRange(pos, pos);
    state.q = el.value.toLowerCase(); state.shown = PAGE; render();
  };

  // Ein mehrwortiger Vorschlag muss als Phrase eingesetzt werden -- ohne
  // Anfuehrungszeichen wuerde nach der Elementgrenze nur das erste Wort zum
  // Filterwert, der Rest fiele als freier Suchtext heraus.
  const insertText = v => /[\s"()]/.test(v) ? `"${v.replace(/["\u201c\u201d\u201e]/g, "")}"` : v;

  const renderList = () => {
    active = -1;
    list.innerHTML = items.map((it, i) => `<li data-i="${i}">${it.html}</li>`).join("");
    list.style.display = "";
    list.querySelectorAll("li").forEach(li => {
      li.onmousedown = ev => { ev.preventDefault(); apply(+li.dataset.i); };
    });
  };

  const getAcMinChars = () =>
    typeof state.searchAcMinChars === "number" ? state.searchAcMinChars : 3;

  const showValueSuggestions = val => {
    tokStart = val.start; tokEnd = val.end;
    const q = val.text.trim().toLowerCase();
    const starts = [], contains = [];
    for (const v of fieldValuesForAutocomplete(val.field)) {
      const lv = v.toLowerCase();
      if (!q || lv.startsWith(q)) starts.push(v);
      else if (lv.includes(q)) contains.push(v);
    }
    items = [...starts, ...contains].slice(0, 10).map(v => ({insert: insertText(v), html: esc(v)}));
    if (!items.length) { close(); return; }
    renderList();
  };

  // Freitext-Vorschlaege ohne "/Parameter": Pool aus Interpret + Titel (dem
  // Kern von defaultScope() -- Pfad bleibt bewusst aussen vor, sonst waere
  // die Liste von langen Dateipfaden dominiert statt von brauchbaren
  // Vorschlaegen).
  const showFreeTextSuggestions = free => {
    tokStart = free.start; tokEnd = free.end;
    const q = free.text.toLowerCase();
    const pool = new Set([...fieldValuesForAutocomplete("a"), ...fieldValuesForAutocomplete("t")]);
    const starts = [], contains = [];
    for (const v of pool) {
      const lv = v.toLowerCase();
      if (lv.startsWith(q)) starts.push(v);
      else if (lv.includes(q)) contains.push(v);
    }
    items = [...starts, ...contains].slice(0, 10).map(v => ({insert: insertText(v), html: esc(v)}));
    if (!items.length) { close(); return; }
    renderList();
  };

  const open = () => {
    clearTimeout(debounceTimer);
    const tok = currentToken();
    if (tok) {
      // Parameternamen-Vorschlaege ("/al" -> "/Album") bleiben bewusst sofort
      // und ohne Mindestlaenge -- kein eigenes Setting dafuer.
      tokStart = tok.start; tokEnd = tok.end;
      const q = tok.text.slice(1).toLowerCase();
      items = SEARCH_FILTER_SUGGESTIONS.filter(s => s.token.slice(1).toLowerCase().startsWith(q))
        .map(s => ({insert: s.token, html: `<b>${esc(s.token)}</b> — ${esc(s.desc)}`}));
      if (!items.length) { close(); return; }
      renderList();
      return;
    }
    const val = currentValueToken();
    if (val) {
      // "/Status" hat eine kurze, feste Werteliste (Korrekt, Verdächtig,
      // Fake, Unklar, Manuell korrigiert) -- die erscheint sofort und
      // vollstaendig, sobald der Parameter steht. Schwellwert und Entprellung
      // gelten nur den freien Textfeldern, deren Werte aus DATA kommen.
      if (INSTANT_VALUE_FIELDS.has(val.field)) { showValueSuggestions(val); return; }
      // Werte-Vorschlaege ("/Genre ho…") erst ab dem einstellbaren
      // Schwellwert (state.searchAcMinChars, Vorgabe 3) und entprellt, damit
      // nicht bei jedem Tastenanschlag DATA neu durchsucht wird.
      if (val.text.trim().length < getAcMinChars()) { close(); return; }
      debounceTimer = setTimeout(() => showValueSuggestions(val), VALUE_AC_DEBOUNCE_MS);
      return;
    }
    const free = currentFreeToken();
    if (free) {
      // Gleicher Schwellwert/Debounce wie die Werte-Vorschlaege oben --
      // greift jetzt auch ganz ohne vorangestellten "/Parameter".
      if (free.text.length < getAcMinChars()) { close(); return; }
      debounceTimer = setTimeout(() => showFreeTextSuggestions(free), VALUE_AC_DEBOUNCE_MS);
      return;
    }
    close();
  };

  el.addEventListener("input", open);
  el.addEventListener("click", open);
  el.addEventListener("blur", () => setTimeout(close, 100));
  el.addEventListener("keydown", ev => {
    if (list.style.display === "none") return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); active = Math.min(active + 1, items.length - 1); highlight(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
    else if (ev.key === "Enter" && active >= 0) { ev.preventDefault(); apply(active); }
    else if (ev.key === "Escape") { close(); }
  });
}
attachSearchFilterAutocomplete(document.getElementById("q"), document.getElementById("searchAcList"));

document.getElementById("btnSearchHelp").onclick = () => {
  document.getElementById("searchHelpBody").innerHTML = SEARCH_HELP_ENTRIES.map(([p, desc, ex]) =>
    `<div class="searchhelprow"><b>${esc(p)}</b> — ${esc(desc)}<div class="path">${esc(ex)}</div></div>`).join("");
  const ov = document.getElementById("searchHelpOverlay");
  ov.style.display = "flex";
  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("searchHelpClose").onclick = close;
  ov.onclick = e => { if (e.target === ov) close(); };
  document.onkeydown = e => { if (e.key === "Escape") close(); };
};
document.getElementById("groupAlbums").onchange = e => { state.groupAlbums = e.target.checked; state.shown = PAGE; saveFilters(); render(); };
// Zeigt per Pfeil an, nach welcher Spalte gerade sortiert wird und in
// welcher Richtung — das <thead> wird nie neu gebaut, also hier separat
// von render() gepflegt.
function syncSortHeaders() {
  document.querySelectorAll("th[data-k]").forEach(th => {
    const old = th.querySelector(".sortarrow");
    if (old) old.remove();
    if (th.dataset.k === state.sort) {
      const span = document.createElement("span");
      span.className = "sortarrow";
      span.textContent = state.dir === 1 ? "▲" : "▼";
      th.appendChild(span);
    }
  });
  syncStickyHeadContent();
}

// ── Spaltenbreiten & Ein-/Ausblenden ─────────────────────────────────────
// Das <thead> wird nie neu gebaut (anders als <tbody> in render()) — Breiten
// und Sichtbarkeit werden deshalb einmalig hier eingerichtet und danach nur
// noch bei Bedarf aktualisiert, statt bei jedem render() neu.

function applyColumnLayout() {
  document.querySelectorAll("table thead th[data-k]").forEach(th => {
    const k = th.dataset.k;
    const w = state.colWidths[k] || DEFAULT_COL_WIDTHS[k];
    th.style.width = w ? w + "px" : "";
    th.style.display = state.hiddenCols.includes(k) ? "none" : "";
  });
  syncTableWidths();
  syncStickyHeadContent();
}

// Die Aktionsspalte ("Öffnen") stellt niemand von Hand ein, sie richtet sich
// nach ihrem Inhalt: welche Icons eine Zeile zeigt, haengt von Server-Modus,
// Shops und Zustand ab. In einer Tabelle mit fester Breite muss diese Breite
// gemessen und gesetzt werden, sonst schneidet die Zelle Icons ab. Moeglich
// ist das, weil .actions per CSS width:max-content hat und damit auch in
// einer zu schmalen Zelle seine natuerliche Breite behaelt.
function syncLinksWidth(table) {
  const th = table.querySelector("thead th.links");
  if (!th) return;
  let max = 0;
  table.querySelectorAll("tbody td.links .actions").forEach(d => {
    max = Math.max(max, d.getBoundingClientRect().width);
  });
  if (max) th.style.width = (Math.ceil(max) + 22) + "px";   // + Zellpolster
}

// Ohne feste Breite ignoriert der Browser table-layout:fixed und rechnet
// automatisch -- dann bestimmt der Zellinhalt die Spaltenbreite und keine
// eingestellte Breite haelt. Die Tabellenbreite ist deshalb die Summe der
// sichtbaren Kopfzellen: liegt sie unter der Containerbreite, faengt das
// min-width:100% (CSS) ab und die Fuellzelle nimmt den Rest auf; liegt sie
// darueber, scrollt .tablewrap horizontal.
function syncTableWidths(measureLinks = true) {
  document.querySelectorAll(".tablewrap table").forEach(t => {
    if (measureLinks) syncLinksWidth(t);
    let total = 0;
    t.querySelectorAll("thead th").forEach(th => {
      if (th.style.display === "none") return;
      total += parseFloat(th.style.width) || 0;
    });
    t.style.width = total + "px";
  });
  syncDetailWidth();
}

// Der aufgeklappte Bereich (Spektrum, Gruende, Player mit Waveform) gehoert
// zur Zeile, nicht zu einer Spalte -- er soll beim horizontalen Scrollen
// stehenbleiben statt mit der Tabelle wegzurutschen. Dafuer klebt er wie die
// fixierten Spalten (CSS: tr.detail td > .grid) und wird exakt auf den Platz
// zwischen ihnen begrenzt: von der Trennlinie der Auswahlspalte bis zu der
// vor der Aktionsspalte. Beide Breiten stehen erst zur Laufzeit fest --
// "Öffnen" misst sich nach den sichtbaren Aktionen (syncLinksWidth), und der
// Sichtbereich haengt an der Fensterbreite. Je Tabelle getrennt, weil die
// Einzelpruefungen ihre eigene .tablewrap haben.
function syncDetailWidth() {
  document.querySelectorAll(".tablewrap").forEach(wrap => {
    const sel = wrap.querySelector("thead th.sel");
    const links = wrap.querySelector("thead th.links");
    if (!sel || !links) return;
    const left = sel.getBoundingClientRect().width;
    const free = wrap.clientWidth - left - links.getBoundingClientRect().width;
    wrap.style.setProperty("--detailleft", Math.round(left) + "px");
    wrap.style.setProperty("--detailw", Math.max(240, Math.round(free)) + "px");
  });
}

// Die Fensterbreite bestimmt den Sichtbereich mit -- ohne das behielte ein
// bereits aufgeklappter Track seine alte Breite, bis etwas anderes
// syncTableWidths() ausloest. Die angeheftete Kopfzeile braucht bei jeder
// Breitenaenderung ebenfalls eine neue Position (linke Kante, Breite).
window.addEventListener("resize", syncDetailWidth);
window.addEventListener("resize", syncStickyHeadPosition);

function initColumnResize(ths) {
  const list = ths || document.querySelectorAll("table thead th[data-k]:not([data-reorder])");
  list.forEach(th => {
    const k = th.dataset.k;
    const handle = document.createElement("span");
    handle.className = "colresize";
    handle.onclick = e => e.stopPropagation();   // sonst sortiert ein Klick auf den Griff mit
    handle.onmousedown = e => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const startWidth = th.getBoundingClientRect().width;
      handle.classList.add("active");
      const onMove = e2 => {
        const w = Math.max(40, Math.round(startWidth + (e2.clientX - startX)));
        state.colWidths[k] = w;
        th.style.width = w + "px";
        syncTableWidths(false);   // Aktionsspalte nicht bei jedem Pixel neu messen
      };
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        handle.classList.remove("active");
        // Nach dem Ziehen feuert ein click auf der Kopfzelle (mousedown und
        // mouseup liegen auf verschiedenen Elementen, der Griff kann ihn
        // deshalb nicht selbst abfangen) -- der wuerde die Sortierung
        // umschalten. Einmalig schlucken; der Timer raeumt den Abfang wieder
        // weg, falls gar kein Klick mehr kommt (Maustaste ausserhalb des
        // Fensters losgelassen).
        const swallow = ev => { ev.stopPropagation(); ev.preventDefault(); };
        th.addEventListener("click", swallow, true);
        setTimeout(() => th.removeEventListener("click", swallow, true), 0);
        syncTableWidths();
        syncStickyHeadContent();
        saveColumns();
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    };
    th.appendChild(handle);
  });
}

// Hoechstzahl gespeicherter Spaltenansichten -- derselbe Deckel wie in
// settings.py::_MAX_COLUMN_VIEWS.
const MAX_COLUMN_VIEWS = 20;

// Die Auswahlliste "Spaltenansicht": erster Eintrag ist immer die
// Standardansicht (keine eigene Einstellung fuer diese Liste), danach die
// gespeicherten Ansichten. Wird an drei Stellen gebraucht -- Spalten-Menue,
// Listen-Dialog und der Dialog fuer die festen Listen.
function columnViewOptionsHTML(selectedId) {
  const dflt = columnViewById(COLUMN_VIEW_DEFAULT);
  const standard = dflt
    ? t("cols.view_default_named", {name: dflt.name})
    : t("cols.view_standard");
  return `<option value="">${esc(standard)}</option>` +
    COLUMN_VIEWS.map(v => `<option value="${esc(v.id)}"` +
      `${v.id === selectedId ? " selected" : ""}>${esc(v.name)}</option>`).join("");
}

function renderColsMenu() {
  const menu = document.getElementById("colsMenu");
  const eigene = columnViewFor(state.view);
  // Auswahl alphabetisch sortiert (nach angezeigtem Label, nicht nach Key) --
  // die tatsaechliche Spaltenreihenfolge in der Tabelle bleibt davon
  // unberuehrt, die kommt weiterhin allein aus state.colOrder (Drag am Header).
  const sortedCols = [...OPTIONAL_COLUMNS].sort((a, b) => a.label.localeCompare(b.label, "de"));
  menu.innerHTML = `<div class="colsviewrow">
    <label for="colsViewSel">${esc(t("cols.view_for", {list: currentView().label}))}</label>
    <select id="colsViewSel">${columnViewOptionsHTML(eigene ? eigene.id : "")}</select>
    <span class="sethelp">${esc(t("cols.view_hint"))}</span>
  </div>
  <div class="colsbtnrow">
    <button class="act" id="colsViewSave"><span class="btnicon">${ICONS.save}</span> ${
      esc(t("cols.view_save"))}</button>
    <button class="act" id="colsReset"><span class="btnicon">${ICONS.rulerDimensionLine}</span> ${
      esc(t("cols.reset_widths"))}</button>
    <button class="act" id="colsOrderReset"><span class="btnicon">${ICONS.arrowRightLeft}</span> ${
      esc(t("cols.reset_order"))}</button>
  </div>
  <div class="colsrow">
    ${sortedCols.map(c => `
      <label><input type="checkbox" data-colkey="${c.key}" ${state.hiddenCols.includes(c.key) ? "" : "checked"}>
        ${esc(c.label)}</label>`).join("")}
  </div>`;
  menu.querySelectorAll("[data-colkey]").forEach(cb => cb.onchange = () => {
    const k = cb.dataset.colkey;
    state.hiddenCols = cb.checked
      ? state.hiddenCols.filter(x => x !== k)
      : [...state.hiddenCols, k];
    saveColumns();
    applyColumnLayout();
    render();
  });
  document.getElementById("colsViewSel").onchange = ev => assignColumnView(ev.target.value);
  document.getElementById("colsViewSave").onclick = saveCurrentColumnsAsView;
  document.getElementById("colsReset").onclick = () => {
    state.colWidths = {};
    saveColumns();
    applyColumnLayout();
  };
  document.getElementById("colsOrderReset").onclick = () => {
    state.colOrder = OPTIONAL_COLUMNS.map(c => c.key);
    renderHead();
    render();
    saveColumns();
  };
}

// Eigenes, schlankeres Spalten-Menue fuer die Einzelprueflungen -- keine
// Ansicht-Auswahl/Speichern-Zeile wie bei renderColsMenu(), Drops sind keine
// Liste, der sich eine benannte Spaltenansicht zuweisen liesse. Nur
// Checkboxen + Breiten/Reihenfolge zuruecksetzen, alles auf
// state.dropColumns statt state.colOrder/hiddenCols/colWidths.
function renderDropColsMenu() {
  const menu = document.getElementById("dropColsMenu");
  if (!menu) return;
  const sortedCols = [...OPTIONAL_COLUMNS].sort((a, b) => a.label.localeCompare(b.label, "de"));
  menu.innerHTML = `<div class="colsbtnrow">
    <button class="act" id="dropColsReset"><span class="btnicon">${ICONS.rulerDimensionLine}</span> ${
      esc(t("cols.reset_widths"))}</button>
    <button class="act" id="dropColsOrderReset"><span class="btnicon">${ICONS.arrowRightLeft}</span> ${
      esc(t("cols.reset_order"))}</button>
  </div>
  <div class="colsrow">
    ${sortedCols.map(c => `
      <label><input type="checkbox" data-dropcolkey="${c.key}" ${state.dropColumns.hidden.includes(c.key) ? "" : "checked"}>
        ${esc(c.label)}</label>`).join("")}
  </div>`;
  menu.querySelectorAll("[data-dropcolkey]").forEach(cb => cb.onchange = () => {
    const k = cb.dataset.dropcolkey;
    state.dropColumns.hidden = cb.checked
      ? state.dropColumns.hidden.filter(x => x !== k)
      : [...state.dropColumns.hidden, k];
    saveDropColumns();
    renderDrops();
  });
  document.getElementById("dropColsReset").onclick = () => {
    state.dropColumns.widths = {};
    saveDropColumns();
    renderDrops();
  };
  document.getElementById("dropColsOrderReset").onclick = () => {
    state.dropColumns.order = OPTIONAL_COLUMNS.map(c => c.key);
    saveDropColumns();
    renderDrops();
  };
}

// ── Spalten speichern ────────────────────────────────────────────────────
// Reihenfolge, Sichtbarkeit UND Breite gehen server-seitig nach
// config.local.yaml -- so ueberleben sie auch einen Server-Neustart.
// localStorage haengt am Port/Origin, der beim Bundle nach einem Neustart
// wechseln kann (siehe _free_port() in desktop.py); ohne Server (file://
// geoeffnet) bleibt saveFilters() die einzige Speicherung, deshalb laeuft
// die hier immer mit.
//
// Wohin geschrieben wird, entscheidet allein die aktive Liste: haengt an ihr
// eine gespeicherte Spaltenansicht, aendert jede Anpassung diese Ansicht
// (und damit jede andere Liste, die sie ebenfalls verwendet) -- sonst die
// Standard-Spalten der aktuellen Ansicht (Bearbeiten/Player).
function saveColumns() {
  saveFilters();
  if (activeColumnView()) postColumnViews(); else postStandardColumns();
}

function postStandardColumns() {
  if (!apiMode) return;
  const std = state.columnsByLayout[state.layout];
  fetch("/api/columns", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({layout: state.layout, order: std.order,
                          hidden: std.hidden, widths: std.widths})}).catch(() => {});
}

// Immer die ganze Liste -- Anlegen, Umbenennen, Loeschen und jede Aenderung
// an einer aktiven Ansicht laufen ueber denselben Weg (siehe
// server.py::_post_column_views).
function postColumnViews() {
  if (!apiMode) return Promise.resolve();
  return fetch("/api/column-views", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({column_views: COLUMN_VIEWS})}).catch(() => {});
}

// Eigener Speicherweg fuer state.dropColumns -- kein activeColumnView()/
// Layout-Unterscheidung noetig, es gibt nur diesen einen Topf.
function saveDropColumns() {
  saveFilters();
  if (!apiMode) return;
  fetch("/api/drop-columns", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({order: state.dropColumns.order,
                          hidden: state.dropColumns.hidden, widths: state.dropColumns.widths})}).catch(() => {});
}

// ── Spaltenansichten: zuordnen, anlegen, umbenennen, loeschen ────────────
// Welcher Spalten-Topf gerade gilt. Wechselt der Schluessel, muss die
// Kopfzeile neu gebaut werden -- daran haengt syncColumnsForView() beim
// Listenwechsel.
const columnStoreKey = () => {
  const v = activeColumnView();
  return v ? `cv:${v.id}` : `std:${state.layout}`;
};
let lastColumnStoreKey = null;

// Nach einem Listenwechsel aufrufen, VOR dem render() des Aufrufers: baut
// Kopfzeile und Spalten-Menue nur dann neu, wenn die neue Liste tatsaechlich
// auf einem anderen Spalten-Topf sitzt.
function syncColumnsForView() {
  const key = columnStoreKey();
  if (key === lastColumnStoreKey) return false;
  lastColumnStoreKey = key;
  renderHead();          // ruft applyColumnLayout() und den Sticky-Abgleich mit
  renderColsMenu();
  return true;
}

// Zuordnung Liste -> Spaltenansicht setzen oder (leere id) wieder loesen.
// Bewusst ohne Neuaufbau der Tabelle: der Aufrufer weiss, ob die betroffene
// Liste gerade offen ist (Spalten-Menue: ja; Listen-Dialog: vielleicht).
function setColumnAssign(viewId, id) {
  const target = id && COLUMN_VIEWS.some(v => v.id === id) ? id : "";
  if (target) COLUMN_ASSIGN[viewId] = target;
  else delete COLUMN_ASSIGN[viewId];
  saveFilters();
  if (apiMode) {
    fetch("/api/column-assign", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({view: viewId, id: target})}).catch(() => {});
  }
}

// Kopfzeile, Spalten-Menue und Tabelle auf den aktuellen Spalten-Topf
// bringen -- nach jeder Aenderung an der Zuordnung der OFFENEN Liste.
function repaintColumns() {
  lastColumnStoreKey = columnStoreKey();
  renderHead();
  renderColsMenu();
  render();
}

function assignColumnView(id) {
  setColumnAssign(state.view, id);
  repaintColumns();
}

// Eigene Kennung je Ansicht: sie bleibt beim Umbenennen stehen, weil
// COLUMN_ASSIGN daran haengt (siehe settings.py::validate_column_views).
const newColumnViewId = () =>
  `cv${Date.now().toString(36)}${Math.floor(Math.random() * 1000).toString(36)}`;

// Der einzige Speichern-Weg im Spalten-Menue: die aktuell sichtbaren
// Spalten (Reihenfolge, Sichtbarkeit, Breite) unter einem Namen ablegen.
// Steht der Name schon in der Liste, wird DIESE Ansicht ueberschrieben statt
// einer zweiten gleichen Namens -- so deckt ein einziger Knopf beides ab,
// "neu anlegen" und "aktualisieren". Verwalten (umbenennen, loeschen,
// Standardansicht waehlen) liegt bewusst nicht hier, sondern gesammelt in
// den Einstellungen.
async function saveCurrentColumnsAsView() {
  const aktiv = activeColumnView();
  const props = await askPlaylistProps(t("cols.view_dialog_new"),
                                       aktiv ? {name: aktiv.name} : null, false);
  if (!props) return;
  const vorhanden = COLUMN_VIEWS.find(
    v => v.name.toLowerCase() === props.name.toLowerCase());
  if (!vorhanden && COLUMN_VIEWS.length >= MAX_COLUMN_VIEWS) {
    note(t("cols.view_limit", {max: MAX_COLUMN_VIEWS}), "soft");
    return;
  }
  const spalten = {
    order: [...state.colOrder], hidden: [...state.hiddenCols],
    widths: {...state.colWidths},
  };
  const view = vorhanden
    ? Object.assign(vorhanden, spalten)
    : normalizeColumnStore({id: newColumnViewId(), name: props.name, ...spalten});
  if (!vorhanden) COLUMN_VIEWS.push(view);
  // Erst die Ansicht selbst, dann die Zuordnung: der Server weist eine
  // Zuordnung auf eine ihm unbekannte Ansicht ab, und beide Anfragen laufen
  // in eigenen Threads -- ohne das Abwarten koennten sie sich ueberholen.
  await postColumnViews();
  // Die Ansicht gleich der offenen Liste geben -- ein Speichern ohne
  // sichtbare Folge waere schwer zu deuten, und eine neu angelegte Ansicht,
  // die niemand verwendet, wuerde beim naechsten Spaltenklick still wieder
  // auseinanderlaufen. Gilt sie fuer diese Liste ohnehin schon (weil sie
  // ueberschrieben wurde oder die Standardansicht ist), bleibt die Zuordnung
  // wie sie ist -- eine Liste ohne eigene Einstellung soll durch ein
  // Speichern keine bekommen.
  if (activeColumnView() !== view) {
    assignColumnView(view.id);
  } else {
    saveFilters();
    renderColsMenu();
  }
  note(t("cols.view_saved", {name: view.name}));
}

// Reorderbare Spalten-Kopfzellen — werden bei jeder Umsortierung komplett
// neu gebaut und nach #colsAnchor eingefuegt (die Anker-Zelle selbst bleibt
// unsichtbar liegen). Checkbox/Status/Datei/Öffnen bleiben statisch im HTML.
function renderHead() {
  document.querySelectorAll("table thead th[data-reorder]").forEach(th => th.remove());
  const anchor = document.getElementById("colsAnchor");
  // CELL_RENDERERS statt OPTIONAL_COLUMNS-Mitgliedschaft als Filter -- "v"
  // (Status) ist zwar seit Kurzem auch ein OPTIONAL_COLUMNS-Eintrag (fuer
  // Auswahlmenue & Persistenz der Sichtbarkeit), hat aber bewusst keinen
  // Cell-Renderer: seine Kopfzelle steht statisch im HTML (siehe oben), eine
  // zweite, hier dynamisch eingefuegte wuerde die Spalte doppelt anzeigen.
  const html = state.colOrder
    .filter(k => CELL_RENDERERS[k])
    .map(k => {
      const c = OPTIONAL_COLUMNS.find(c => c.key === k);
      const w = state.colWidths[k] || DEFAULT_COL_WIDTHS[k];
      const draggable = apiMode ? ' draggable="true"' : "";
      return `<th data-k="${k}" data-reorder${c.numeric ? ' class="num"' : ""}${draggable} style="width:${w}px">${esc(c.label)}</th>`;
    }).join("");
  anchor.insertAdjacentHTML("afterend", html);
  const newThs = document.querySelectorAll("table thead th[data-reorder]");
  applyColumnLayout();
  initColumnResize(newThs);
  bindHeaderSort();
  if (apiMode) attachColumnDrag(newThs);
  syncSortHeaders();
}

// Spalten per Drag am Header umsortieren — nur mit laufendem Server, die
// Reihenfolge liegt in config.local.yaml statt localStorage (anders als
// die Breite).
function attachColumnDrag(ths) {
  let dragKey = null;
  ths.forEach(th => {
    th.ondragstart = () => { dragKey = th.dataset.k; };
    th.ondragover = e => { e.preventDefault(); };
    th.ondrop = e => {
      e.preventDefault();
      const targetKey = th.dataset.k;
      if (!dragKey || dragKey === targetKey) return;
      const order = state.colOrder.filter(k => k !== dragKey);
      order.splice(order.indexOf(targetKey), 0, dragKey);
      state.colOrder = order;
      renderHead();
      render();
      saveColumns();
    };
  });
}

// 3-Klick-Zyklus je Spalte: aufsteigend -> absteigend -> zuruecksetzen
// (state.sort = null, Liste faellt auf die Grundreihenfolge zurueck).
function bindHeaderSort() {
  document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    if (state.sort !== k) { state.sort = k; state.dir = 1; }
    else if (state.dir === 1) { state.dir = -1; }
    else { state.sort = null; state.dir = 1; }
    // Spaltensortierung und Album-Gruppierung schliessen sich aus -- sonst
    // waere unklar, welche der beiden Reihenfolgen gerade gilt.
    if (state.groupAlbums) {
      state.groupAlbums = false;
      const ga = document.getElementById("groupAlbums");
      if (ga) ga.checked = false;
    }
    state.shown = PAGE;
    syncSortHeaders(); saveFilters(); render();
  });
}

document.getElementById("btnCols").onclick = () => {
  const menu = document.getElementById("colsMenu");
  const open = menu.style.display === "none";
  // Beim Oeffnen neu aufbauen: der Inhalt haengt an der gerade gewaehlten
  // Liste (Zuordnung, Name in der Ueberschrift) und die kann sich seit dem
  // letzten Aufbau geaendert haben.
  if (open) renderColsMenu();
  menu.style.display = open ? "" : "none";
};

document.getElementById("btnDropCols").onclick = () => {
  const menu = document.getElementById("dropColsMenu");
  const open = menu.style.display === "none";
  if (open) renderDropColsMenu();
  menu.style.display = open ? "" : "none";
};

const currentView = () => VIEWS.find(v => v.id === state.view) || VIEWS[0];

// Feldbezogene Suche: "/genre house" durchsucht nur Genre statt Artist+
// Titel+Pfad. state.q kommt bereits kleingeschrieben an (siehe oninput bei
// #q), das Feld-Kuerzel also auch -- Umlaute werden trotzdem normalisiert,
// falls jemand "/künstler" statt "/kuenstler" tippt.
//
// Suchsyntax (siehe tokenizeSearch()/parseSearchQuery()):
//  - Alles nebeneinander ist UND-verknuepft: "/genre house /bpm 128" ebenso
//    wie zwei freie Suchwoerter.
//  - Ein Parameter nimmt GENAU EIN Element: ein Wort, eine Phrase in
//    Anfuehrungszeichen ("radio edit") oder eine ODER-Gruppe in Klammern
//    ("(house OR dance)"). Alles danach zaehlt wieder als eigenes Element --
//    mehrwortige Werte brauchen deshalb Anfuehrungszeichen.
//  - "/no" ist die einzige Verneinung und wirkt nur auf das unmittelbar
//    folgende Element -- ein Wort, eine Phrase, eine Gruppe oder einen
//    ganzen "/Parameter Wert"-Ausdruck.
//  - "/datei" filtert nur die Dateiendung, "/ausgeblendet" ist ein Schalter
//    ohne Wert (nur ausgeblendete Tracks; mit "/no" nur eingeblendete),
//    "/dauer"/"/deklariert"/"/konfidenz" akzeptieren ">", "<" und Bereiche
//    ("3:00-5:00") ueber parseOperatorRange(), "/add" akzeptiert ">"/"<" vor
//    einem Datum (TT-MM-JJJJ) oder Jahr ueber parseDateRange().
const SEARCH_FIELD_ALIASES = {
  artist: "a", kuenstler: "a",
  titel: "t", title: "t",
  album: "al",
  albumkuenstler: "aa", albumartist: "aa",
  komponist: "cp", composer: "cp",
  genre: "ge",
  kommentar: "cm", comment: "cm",
  pfad: "p", path: "p",
  jahr: "yr", year: "yr",
  bpm: "bp",
  dauer: "du", laenge: "du", length: "du",
  deklariert: "kb", declared: "kb",
  konfidenz: "cf", confidence: "cf",
  add: "da", hinzugefuegt: "da", added: "da",
  status: "v", verdikt: "v", verdict: "v",
};
// Einzige Verneinung der Suche -- bewusst kein zweiter Weg (kein "-wort",
// kein "NOT"), damit die Reichweitenregel ("wirkt auf das naechste Element")
// nur an einer Stelle erklaert werden muss.
const NEGATE_ALIASES = new Set(["no", "exkludiere", "exclude"]);
const EXT_ALIASES = new Set(["datei", "file"]);
// Schalter ohne Wert: "/ausgeblendet" zeigt nur ausgeblendete Tracks
// (r.ig), "/no /ausgeblendet" nur die eingeblendeten. Ein Wert waere hier
// sinnlos -- deshalb schluckt der Parser nach diesem Parameter kein
// Element, das naechste Wort bleibt normaler Suchtext.
const FLAG_ALIASES = new Set(["ausgeblendet", "hidden"]);
// Zahlenfelder: ">", "<" und Bereiche ("120-128") ueber
// parseOperatorRange(). Jahr und BPM stehen hier mit drin, obwohl sie aus
// den Datei-Tags kommen -- als reiner Textvergleich waere "/Jahr >2020"
// eine sinnlose Teilstringsuche. Beide sind in der DB ganzzahlig (0 =
// kein Tag), ein exakter Zahlenvergleich geht also auf.
const NUMERIC_OP_FIELDS = new Set(["du", "kb", "cf", "yr", "bp"]);
// "/Status" braucht eine eigene Filterart: in der Zeile steht der interne
// Schluessel ("OK"/"VERDAECHTIG"/...), getippt wird aber die uebersetzte
// Beschriftung ("Korrekt"/"Verdaechtig"/...). Ein Freitextvergleich auf r.v
// wuerde deshalb nie treffen.
const VERDICT_OP_FIELDS = new Set(["v"]);
const DATE_OP_FIELDS = new Set(["da"]);
const ALIAS_LOOKUP = new Set([...Object.keys(SEARCH_FIELD_ALIASES), ...NEGATE_ALIASES,
  ...EXT_ALIASES, ...FLAG_ALIASES]);
let FIELD_LABELS = {a:t("field.artist"), t:t("field.title"), al:t("field.album"), aa:t("field.album_artist"),
  cp:t("field.composer"), ge:t("field.genre"), cm:t("field.comment"), p:t("field.path"), yr:t("field.year"),
  bp:t("field.bpm"), du:t("field.duration"), kb:t("field.declared"),
  cf:t("field.confidence"), da:t("field.added"), v:t("col.status")};

// Holt labels/FIELD_LABELS/SEARCH_HELP_ENTRIES/... nach, die oben als
// Modul-Konstanten mit t() gebaut wurden, bevor initStorage() die Sprache
// aus den Einstellungen kennt (siehe Kommentar bei LANG weiter oben). Nach
// dem Laden der Einstellungen erneut aufgerufen, damit ein explizit
// gewaehltes ui_language auch bei diesen Texten tatsaechlich greift.
function refreshI18nCache() {
  REKORDBOX_MISSING_HINT = t("hint.rekordbox_missing");
  labels = {FAKE:t("verdict.fake"), VERDAECHTIG:t("verdict.suspicious"), OK:t("verdict.ok"), UNKLAR:t("verdict.unclear")};
  SEARCH_HELP_ENTRIES = [
    [t("search.help.album.token"), t("field.album"), t("search.help.album.example")],
    [t("search.help.artist.token"), t("field.artist"), t("search.help.artist.example")],
    [t("search.help.title_field.token"), t("field.title"), t("search.help.title_field.example")],
    [t("search.help.genre.token"), t("field.genre"), t("search.help.genre.example")],
    [t("search.help.year.token"), t("search.help.year.desc"), t("search.help.year.example")],
    [t("search.help.bpm.token"), t("search.help.bpm.desc"), t("search.help.bpm.example")],
    [t("search.help.composer.token"), t("field.composer"), t("search.help.composer.example")],
    [t("search.help.album_artist.token"), t("field.album_artist"), t("search.help.album_artist.example")],
    [t("search.help.comment.token"), t("search.help.comment.desc"), t("search.help.comment.example")],
    [t("search.help.path.token"), t("search.help.path.desc"), t("search.help.path.example")],
    [t("search.help.duration.token"), t("search.help.duration.desc"), t("search.help.duration.example")],
    [t("search.help.file.token"), t("search.help.file.desc"), t("search.help.file.example")],
    [t("search.help.exclude.token"), t("search.help.exclude.desc"), t("search.help.exclude.example")],
    [t("search.help.declared.token"), t("search.help.declared.desc"), t("search.help.declared.example")],
    [t("search.help.confidence.token"), t("search.help.confidence.desc"), t("search.help.confidence.example")],
    [t("search.help.added.token"), t("search.help.added.desc"), t("search.help.added.example")],
    [t("search.help.status.token"), t("search.help.status.desc"), t("search.help.status.example")],
    [t("search.help.hidden.token"), t("search.help.hidden.desc"), t("search.help.hidden.example")],
  ];
  SEARCH_FILTER_SUGGESTIONS = SEARCH_HELP_ENTRIES.flatMap(([p, desc]) =>
    p.split(",").map(tok => ({token: tok.trim(), desc})));
  FIELD_LABELS = {a:t("field.artist"), t:t("field.title"), al:t("field.album"), aa:t("field.album_artist"),
    cp:t("field.composer"), ge:t("field.genre"), cm:t("field.comment"), p:t("field.path"), yr:t("field.year"),
    bp:t("field.bpm"), du:t("field.duration"), kb:t("field.declared"),
    cf:t("field.confidence"), da:t("field.added"), v:t("col.status")};
  // Gleiche Schluessel wie beim urspruenglichen Aufbau von OPTIONAL_COLUMNS
  // oben (Zeile ~81) -- Objekte werden hier nur umbenannt, nicht ersetzt,
  // sonst wuerden gespeicherte Spaltenreihenfolge/-breiten ihre Referenz
  // verlieren.
  const columnKeyToI18n = {v:"col.status", co:"col.cutoff", kb:"field.declared", mk:"col.class", cf:"field.confidence",
    st:"col.steepness", lu:"col.loudness", tp:"col.clip", du:"field.duration", rb:"col.rekordbox",
    im:"col.in_music", da:"field.added", cv:"col.cover", al:"field.album", tn:"col.track_no",
    aa:"field.album_artist", cp:"field.composer", ge:"field.genre", yr:"field.year", bp:"field.bpm",
    cm:"field.comment", a:"field.artist", t:"field.title", n:"field.file",
    ti:"col.tag_issues"};
  for (const col of OPTIONAL_COLUMNS) col.label = t(columnKeyToI18n[col.key]);
  const viewIdToI18n = {ignored:"views.ignored", corrected:"views.corrected",
    gone:"views.missing", all:"views.all", duplicates:"views.duplicates",
    tag_issues:"views.tag_issues"};
  // "pl:"/"sm:"/Fremd-Ansichten ueberspringen: ihre Label kommen aus der
  // Playlist-Tabelle (Nutzername), nicht aus STRINGS.
  for (const v of VIEWS) if (viewIdToI18n[v.id]) v.label = t(viewIdToI18n[v.id]);
}

// Zerlegt den Suchtext in Elemente. Ein Element ist ein einzelnes Wort, eine
// Phrase in Anfuehrungszeichen ("radio edit"), eine ODER-Gruppe in Klammern
// ("(house OR dance)") oder ein erkannter "/Parameter". Ein "/" zaehlt nur
// als Parameteranfang, wenn ihm Textanfang, Leerzeichen, Klammer oder
// Anfuehrungszeichen vorausgeht -- sonst waere "sgt/mix" ein Parameter mitten
// im Wort. Unbekannte "/woerter" bleiben ganz normaler Suchtext.
// start/end jedes Elements bleiben erhalten: die Chips unter der Suchleiste
// schneiden beim Entfernen genau ihren Abschnitt wieder aus dem Suchtext.
// Typografische Anfuehrungszeichen zaehlen wie gerade -- macOS ersetzt sie
// beim Tippen in manchen Feldern automatisch, und kopierter Text bringt sie
// ohnehin mit. Wer „…“ tippt, meint dasselbe wie "…".
const QUOTE_CHARS = '"\u201c\u201d\u201e\u201f\u00ab\u00bb';
const isQuote = ch => QUOTE_CHARS.includes(ch);
function tokenizeSearch(q) {
  const toks = [];
  let i = 0;
  while (i < q.length) {
    if (/\s/.test(q[i])) { i++; continue; }
    const start = i;
    if (isQuote(q[i])) {
      // Nicht geschlossene Phrase reicht bis zum Textende -- waehrend des
      // Tippens ist genau das der Normalfall.
      let close = i + 1;
      while (close < q.length && !isQuote(q[close])) close++;
      const text = q.slice(i + 1, close).trim();
      i = close >= q.length ? q.length : close + 1;
      if (text) toks.push({type: "value", start, end: i, values: [{text, phrase: true}]});
      continue;
    }
    if (q[i] === "(") {
      const close = q.indexOf(")", i + 1);
      const inner = q.slice(i + 1, close === -1 ? q.length : close);
      i = close === -1 ? q.length : close + 1;
      const values = splitOrGroup(inner);
      if (values.length) toks.push({type: "value", start, end: i, values});
      continue;
    }
    let j = i;
    while (j < q.length && !/\s/.test(q[j]) && !isQuote(q[j]) && q[j] !== "(" && q[j] !== ")") j++;
    const word = q.slice(i, j);
    i = j;
    if (word[0] === "/") {
      const name = word.slice(1).replace(/ü/g, "ue");
      if (ALIAS_LOOKUP.has(name)) { toks.push({type: "param", word: name, start, end: j}); continue; }
    }
    toks.push({type: "value", start, end: j, values: [{text: word, phrase: false}]});
  }
  return toks;
}

// Inhalt einer Klammergruppe -> Liste der ODER-Alternativen. Mehrere Woerter
// ohne "OR" dazwischen gehoeren zu EINER Alternative ("(deep house OR
// trance)") -- innerhalb einer Gruppe ist "OR" der einzige Operator, das
// implizite UND der obersten Ebene gilt hier nicht.
function splitOrGroup(inner) {
  const alts = [];
  const re = new RegExp(`[${QUOTE_CHARS}]([^${QUOTE_CHARS}]*)[${QUOTE_CHARS}]|([^\\s${QUOTE_CHARS}]+)`, "g");
  let m, cur = null;
  while ((m = re.exec(inner))) {
    const phrase = m[1] !== undefined;
    const text = (phrase ? m[1] : m[2]).trim();
    if (!phrase && text.toLowerCase() === "or") { if (cur) alts.push(cur); cur = null; continue; }
    if (!text) continue;
    cur = cur ? {text: (cur.text + " " + text), phrase: false} : {text, phrase};
  }
  if (cur) alts.push(cur);
  return alts;
}

// Setzt die Elemente aus tokenizeSearch() zu freien Suchbegriffen plus einer
// Liste erkannter Filter zusammen (einmal pro Tastenanschlag, kleiner Cache --
// sonst wuerde matchesSearch() pro Zeile neu parsen).
//
// Regeln:
//  - "/Parameter" nimmt genau das naechste Element als Wert. Fehlt es (Ende
//    des Suchtextes oder direkt der naechste Parameter), verfaellt der
//    Parameter stillschweigend -- bewusst kein Fehler, waehrend des Tippens
//    ist dieser Zustand der Normalfall.
//  - "/No" wirkt nur auf das unmittelbar folgende Element: ein Wort, eine
//    Phrase, eine Gruppe oder einen ganzen "/Parameter Wert"-Ausdruck.
//    "/No /No X" kollabiert absichtlich zu einem einzelnen Ausschluss statt
//    zu einer doppelten Verneinung.
//  - Alles Uebrige ist freier Suchtext; mehrere freie Elemente sind
//    UND-verknuepft (jedes muss in Interpret+Titel+Pfad vorkommen).
let _cacheQ = null, _cacheParsed = null;
function parseSearchQuery(q) {
  if (q === _cacheQ) return _cacheParsed;
  _cacheQ = q;
  const toks = tokenizeSearch(q);
  const parsed = {free: [], filters: []};
  for (let i = 0; i < toks.length; i++) {
    const start = toks[i].start;
    let negate = false;
    while (toks[i] && toks[i].type === "param" && NEGATE_ALIASES.has(toks[i].word)) { negate = true; i++; }
    const tok = toks[i];
    if (!tok) break;  // "/No" am Textende: verfaellt
    if (tok.type === "param") {
      if (FLAG_ALIASES.has(tok.word)) {
        parsed.filters.push({kind: "hidden", values: [], raw: "", start, end: tok.end, negate});
        continue;
      }
      const val = toks[i + 1];
      if (!val || val.type !== "value") continue;  // Parameter ohne Wert verfaellt
      i++;
      pushFieldFilter(parsed.filters, tok.word, val.values, start, val.end, negate);
      continue;
    }
    if (negate) parsed.filters.push({kind: "exclude", values: tok.values, raw: valuesLabel(tok.values), start, end: tok.end});
    else parsed.free.push({values: tok.values});
  }
  return (_cacheParsed = parsed);
}
// Anzeigetext eines Filterwerts fuer die Chips unter der Suchleiste --
// mehrere ODER-Alternativen werden mit dem uebersetzten "oder" verbunden.
function valuesLabel(values) { return values.map(v => v.text).join(` ${t("search.chip.or")} `); }
function pushFieldFilter(filters, word, values, start, end, negate) {
  const f = {values, raw: valuesLabel(values), start, end, negate};
  if (EXT_ALIASES.has(word)) { filters.push({...f, kind: "ext"}); return; }
  const field = SEARCH_FIELD_ALIASES[word];
  if (VERDICT_OP_FIELDS.has(field)) { filters.push({...f, kind: "verdict", field}); return; }
  if (DATE_OP_FIELDS.has(field)) { filters.push({...f, kind: "date", field}); return; }
  filters.push({...f, kind: NUMERIC_OP_FIELDS.has(field) ? "numeric" : "field", field});
}

// mm:ss oder "Ns" (Sekunden) -> Sekunden als Zahl, sonst normale Kommazahl.
function parseDurationToken(tok) {
  tok = tok.trim();
  const mmss = /^(\d+):([0-5]?\d)$/.exec(tok);
  if (mmss) return parseInt(mmss[1], 10) * 60 + parseInt(mmss[2], 10);
  const secs = /^(\d+(?:\.\d+)?)s$/i.exec(tok);
  if (secs) return parseFloat(secs[1]);
  const plain = parseFloat(tok);
  return Number.isFinite(plain) ? plain : null;
}
const toPlainNumber = s => { const n = parseFloat(s); return Number.isFinite(n) ? n : null; };

// ">3:30", "<180s", "3:00-5:00" oder eine blanke Zahl (exakter Wert) -- ersetzt
// die alte "ge:"/"lt:"-Konvention der frueheren Deklariert-/Konfidenz-Dropdowns
// und deckt zusaetzlich Bereiche ab.
function parseOperatorRange(value, toNumber) {
  value = value.trim();
  if (value.startsWith(">")) { const n = toNumber(value.slice(1)); return n === null ? null : v => v > n; }
  if (value.startsWith("<")) { const n = toNumber(value.slice(1)); return n === null ? null : v => v < n; }
  const range = /^(.+?)-(.+)$/.exec(value);
  if (range) {
    const lo = toNumber(range[1]), hi = toNumber(range[2]);
    return (lo === null || hi === null) ? null : v => v >= lo && v <= hi;
  }
  const n = toNumber(value);
  return n === null ? null : v => v === n;
}
function matchesNumericFilter(rowValue, rawValue, field) {
  const toNumber = field === "du" ? parseDurationToken : toPlainNumber;
  const test = parseOperatorRange(rawValue, toNumber);
  return test ? test(rowValue) : true;  // unparsbar -> Filter wirkungslos statt Absturz
}

// "TT-MM-JJJJ" (Tag) oder "JJJJ" (Jahr) -> {start, end} als Unix-Sekunden in
// lokaler Zeitzone (wie fmtDate()). Ein Jahr/Tag ist ein Bereich, keine
// Punktzahl -- anders als bei parseOperatorRange() schneiden ">"/"<" deshalb
// VOR bzw. NACH dem gesamten Bereich, nicht an dessen Rand.
function parseDateRange(tok) {
  tok = tok.trim();
  const full = /^(\d{1,2})-(\d{1,2})-(\d{4})$/.exec(tok);
  if (full) {
    const [, d, m, y] = full;
    return {
      start: new Date(+y, +m - 1, +d, 0, 0, 0).getTime() / 1000,
      end: new Date(+y, +m - 1, +d, 23, 59, 59, 999).getTime() / 1000,
    };
  }
  const year = /^(\d{4})$/.exec(tok);
  if (year) {
    const y = +year[1];
    return {
      start: new Date(y, 0, 1, 0, 0, 0).getTime() / 1000,
      end: new Date(y, 11, 31, 23, 59, 59, 999).getTime() / 1000,
    };
  }
  return null;
}
function matchesDateFilter(rowValue, rawValue) {
  if (!rowValue) return false;  // 0 = nie hinzugefuegt, zaehlt bei keinem Datumsfilter als Treffer
  rawValue = rawValue.trim();
  const op = rawValue.startsWith(">") ? ">" : rawValue.startsWith("<") ? "<" : null;
  const range = parseDateRange(op ? rawValue.slice(1) : rawValue);
  if (!range) return true;  // unparsbar -> Filter wirkungslos statt Absturz
  if (op === ">") return rowValue > range.end;
  if (op === "<") return rowValue < range.start;
  return rowValue >= range.start && rowValue <= range.end;
}

// Levenshtein-Distanz mit fruehem Abbruch, sobald eine Zeile der DP-Matrix
// schon weiter als maxDist vom Ziel entfernt ist -- haelt die Kosten pro
// Wortpaar klein, auch bei tausenden Zeilen pro Tastenanschlag.
function levenshteinWithin(a, b, maxDist) {
  if (Math.abs(a.length - b.length) > maxDist) return maxDist + 1;
  let prev = Array.from({length: b.length + 1}, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = cur[0];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (cur[j] < rowMin) rowMin = cur[j];
    }
    if (rowMin > maxDist) return maxDist + 1;
    prev = cur;
  }
  return prev[b.length];
}
function wordsSimilarEnough(w1, w2, tolerance) {
  if (w1 === w2) return true;
  if (w1.length < 3 || w2.length < 3) return false;
  const maxLen = Math.max(w1.length, w2.length);
  const maxDist = Math.floor((1 - tolerance) * maxLen);
  return maxDist > 0 && levenshteinWithin(w1, w2, maxDist) <= maxDist;
}
const getSearchTolerance = () => state.searchTolerance ?? 0.8;
// Apostroph-Varianten (gerade/typografisch/Backtick) fallen komplett weg,
// damit "dont" auch "don't"/"don’t" trifft und umgekehrt -- Tag-Daten und
// Sucheingabe verwenden nie zuverlaessig dieselbe Variante.
const stripApostrophes = s => s.replace(/['’‘`´]/g, "");

// Exakter Teilstring bleibt der schnelle Regelfall; Fuzzy ergaenzt nur bei
// fehlgeschlagenem exaktem Treffer und arbeitet ausschliesslich wortweise
// (nie auf dem ganzen String), damit die Kosten beschraenkt bleiben.
//
// scope (optional) ist der vorbereitete Suchbereich EINER Zeile (siehe
// scopeEntry()): dessen Text ist bereits kleingeschrieben und apostroph-
// bereinigt, und seine Wortliste wird nur einmal gebildet statt bei jedem
// Tastendruck neu. Ohne scope verhaelt sich die Funktion wie bisher --
// Feldfilter ("/album ...") reichen einen frischen String herein.
function fuzzyIncludes(haystack, needle, scope) {
  if (!needle) return true;
  haystack = scope ? scope.text : stripApostrophes(haystack);
  needle = stripApostrophes(needle);
  if (haystack.includes(needle)) return true;
  const tolerance = getSearchTolerance();
  if (!tolerance) return false;
  const needleWords = needle.split(/\s+/).filter(Boolean);
  // Der teure Fall: KEIN wortgetreuer Treffer. Genau der ist beim Tippen der
  // Regelfall (die allermeisten Zeilen passen nicht), und genau dort lief
  // bisher je Zeile ein split() ueber den ganzen Suchbereich plus je
  // Wortpaar eine Levenshtein-Matrix.
  const hayWords = scope
    ? (scope.words || (scope.words = haystack.split(/\s+/).filter(Boolean)))
    : haystack.split(/\s+/).filter(Boolean);
  return needleWords.every(nw => hayWords.some(hw => wordsSimilarEnough(nw, hw, tolerance)));
}

// Suchbereich einer Zeile: Interpret + Titel + Pfad, kleingeschrieben und
// apostroph-bereinigt. Wird bei JEDEM Tastendruck fuer JEDE Zeile gebraucht
// (10.822 mal, teils dreifach) und haengt nur an r.a/r.t/r.p -- deshalb je
// Zeile einmal gebildet und behalten. Im Browser gemessen: 34 -> 21 ms je
// Tastendruck bei 10.822 Zeilen.
//
// WICHTIG: nach jeder Aenderung an Interpret, Titel oder Pfad einer Zeile
// muss dropScopeCache(r) laufen, sonst sucht die Oberflaeche weiter im alten
// Text. Einziger Weg fuer Bibliothekszeilen ist applyRowUpdate() -- dort
// steht der Aufruf.
const _scopeCache = new Map();
function scopeEntry(r) {
  let e = _scopeCache.get(r);
  if (e === undefined) {
    // words bleibt null, bis der Fuzzy-Zweig es wirklich braucht.
    e = {text: stripApostrophes((r.a + " " + r.t + " " + r.p).toLowerCase()), words: null};
    _scopeCache.set(r, e);
  }
  return e;
}
const dropScopeCache = r => { r ? _scopeCache.delete(r) : _scopeCache.clear(); };
// Der erste Tastendruck wuerde den Cache sonst fuer ALLE Zeilen auf einmal
// fuellen und waere damit teurer als frueher (an 10.822 Zeilen gemessen:
// 58 ms statt 34 ms) -- der Gewinn kaeme erst ab dem zweiten. Deshalb einmal
// im Leerlauf nach dem ersten Zeichnen vorbereiten. requestIdleCallback
// laesst dem Browser den Vortritt; wo es fehlt (Safari <17), genuegt ein
// spaeter Timer, das Ergebnis ist dasselbe, nur ohne Leerlauf-Garantie.
function warmScopeCache() {
  const run = () => { for (const r of DATA) scopeEntry(r); };
  if (typeof requestIdleCallback === "function") requestIdleCallback(run, {timeout: 2000});
  else setTimeout(run, 500);
}
const defaultScope = r => scopeEntry(r).text;

// "hit" ist die bisherige Positiv-Bedingung je Filterart; ohne negate (der
// Normalfall) bleibt "!hit -> raus" exakt das alte Verhalten. Mit negate
// kippt der Vergleich auf "hit -> raus" (siehe classifyToken()). Ausnahme:
// bei unparsbarer Zahl/Datum liefert matchesNumericFilter/matchesDateFilter
// bewusst "true" ("wirkungslos", laesst jede Zeile durch) -- kombiniert mit
// negate wuerde das faelschlich ALLE Zeilen ausschliessen. Seltener Fall
// (kaputte Syntax + Verneinung gleichzeitig), bewusst nicht extra
// abgefangen, um keine dritte match/kein-match/wirkungslos-Zustandslogik
// einzufuehren.
// Vergleicht einen getippten Status-Wert mit der Zeile. Verglichen wird
// gegen die uebersetzte Beschriftung UND den internen Schluessel, jeweils als
// Anfangsvergleich -- so genuegt "/Status verd" fuer "Verdaechtig", und ein
// englischsprachiger Nutzer kann "/status susp" tippen. "Manuell korrigiert"
// ist kein Verdikt, sondern die Markierung mc (Tabelle 'corrected'), wird
// hier aber wie ein Status behandelt, weil sie in der Oberflaeche als solcher
// erscheint (auch in den festen Listen im Baum).
function matchesVerdictFilter(r, value) {
  const want = String(value || "").trim().toLowerCase();
  if (!want) return true;
  const korrigiert = String(t("chip.manual_corrected")).toLowerCase();
  if (korrigiert.startsWith(want)) return !!r.mc;
  const key = verdictOf(r);
  return key.toLowerCase().startsWith(want)
      || String(labels[key] || "").toLowerCase().startsWith(want);
}

// Ein einzelner Suchwert: eine Phrase ("radio edit") verlangt den
// wortgetreuen Teilstring, ein einfaches Wort geht wie bisher ueber
// fuzzyIncludes() (Toleranz). Genau das ist der Gewinn der
// Anfuehrungszeichen -- sonst waeren sie reine Schreibweise.
function matchesValue(hay, v, scope) {
  if (!v.text) return true;
  if (!v.phrase) return fuzzyIncludes(hay, v.text, scope);
  return (scope ? scope.text : stripApostrophes(hay)).includes(stripApostrophes(v.text));
}
// Mehrere Werte eines Filters stammen immer aus einer Klammergruppe und sind
// deshalb ODER-verknuepft (das implizite UND wirkt nur zwischen Elementen).
const matchesAnyValue = (hay, values, scope) =>
  !values.length || values.some(v => matchesValue(hay, v, scope));

function applyFilters(r, filters) {
  for (const f of filters) {
    let hit;
    switch (f.kind) {
      // "exclude" traegt seine Verneinung schon in der Filterart, deshalb
      // kein hit/negate-Vergleich wie bei den uebrigen.
      case "exclude": { const sc = scopeEntry(r);
        if (matchesAnyValue(sc.text, f.values, sc)) return false; continue; }
      case "field": hit = matchesAnyValue(String(r[f.field] ?? "").toLowerCase(), f.values); break;
      case "ext": hit = f.values.some(v => fileExt(r.p) === v.text.replace(/^\./, "").toUpperCase()); break;
      case "numeric": hit = f.values.some(v => matchesNumericFilter(r[f.field], v.text, f.field)); break;
      case "date": hit = f.values.some(v => matchesDateFilter(r[f.field], v.text)); break;
      case "verdict": hit = f.values.some(v => matchesVerdictFilter(r, v.text)); break;
      case "hidden": hit = !!r.ig; break;
      // Nur von refreshDefaultFilters() erzeugt (Standard-Suchfilter-Zeile
      // ohne "/Parameter", reiner Freitext) -- der getippte Suchtext selbst
      // laeuft weiter ueber "free" in matchesSearch(), nicht ueber diesen Zweig.
      case "contains": { const sc = scopeEntry(r);
        hit = matchesAnyValue(sc.text, f.values, sc); break; }
      default: continue;
    }
    if (hit === !!f.negate) return false;
  }
  return true;
}
function matchesSearch(r, q) {
  if (!applyFilters(r, ACTIVE_DEFAULT_FILTERS)) return false;
  if (!q) return true;
  const {free, filters} = parseSearchQuery(q);
  // Mehrere freie Elemente sind UND-verknuepft -- jedes muss fuer sich in
  // Interpret+Titel+Pfad vorkommen (Reihenfolge egal).
  const sc = scopeEntry(r);
  for (const el of free) if (!matchesAnyValue(sc.text, el.values, sc)) return false;
  return applyFilters(r, filters);
}

function chipLabel(f) {
  const name = f.kind === "exclude" ? t("search.chip.exclude")
    : f.kind === "ext" ? t("field.file")
    : f.kind === "contains" ? t("search.chip.contains")
    : f.kind === "hidden" ? t("views.ignored")
    : FIELD_LABELS[f.field];
  // "/Ausgeblendet" ist ein Schalter ohne Wert -- ein ": " mit nichts
  // dahinter waere nur Rauschen im Chip.
  const label = f.kind === "hidden" ? name : `${name}: ${f.raw}`;
  return f.negate ? `${t("search.chip.negate_prefix")} ${label}` : label;
}
// Zeigt erkannte Suchparameter als entfernbare Chips unter der Suchleiste --
// zwei Sorten im selben Streifen, optisch per CSS-Klasse "defaultchip"
// unterschieden: die getippten (blau, .searchchip) UND die aus den
// Einstellungen kommenden Standard-Suchfilter (andere Farbe, siehe
// ACTIVE_DEFAULT_FILTERS/refreshDefaultFilters()). Ein getippter Chip merkt
// sich die Start/End-Position seines "/Parameter Wert"-Segments aus
// parseSearchQuery() und schneidet beim Entfernen nur genau das aus dem
// Suchtext heraus. Ein Standardfilter-Chip hat keine Position im Suchtext --
// sein × traegt die Filter-ID stattdessen in state.disabledDefaults ein
// (nur fuer die laufende Sitzung, siehe dort).
function renderSearchChips() {
  const box = document.getElementById("searchChips");
  if (!box) return;
  const {filters} = parseSearchQuery(state.q);
  const queryChips = filters.map((f, i) =>
    `<span class="chip searchchip" data-qchip="${i}">${esc(chipLabel(f))}<span class="rm">×</span></span>`).join("");
  const defaultChips = ACTIVE_DEFAULT_FILTERS.map(f =>
    `<span class="chip searchchip defaultchip" data-defaultchip="${f.defaultId}" title="${esc(t("search.chip.default_title"))}">${esc(chipLabel(f))}<span class="rm">×</span></span>`).join("");
  box.innerHTML = queryChips + defaultChips;
  box.querySelectorAll("[data-qchip]").forEach(el => el.querySelector(".rm").onclick = () => {
    const f = filters[+el.dataset.qchip];
    state.q = (state.q.slice(0, f.start) + state.q.slice(f.end)).replace(/\s+/g, " ").trim();
    const qEl = document.getElementById("q");
    if (qEl) qEl.value = state.q;
    state.shown = PAGE;
    render();
  });
  box.querySelectorAll("[data-defaultchip]").forEach(el => el.querySelector(".rm").onclick = () => {
    state.disabledDefaults.add(+el.dataset.defaultchip);
    refreshDefaultFilters();
    state.shown = PAGE;
    render();
  });
}

// Album-Gruppenschluessel wie in Music.app: Albumkuenstler (Fallback Artist)
// + Album. Kein Album-Tag -> null, wird nie gruppiert (sonst faellt jede
// Datei ohne Album-Tag faelschlich in eine gemeinsame Riesengruppe).
function albumGroupKey(r) {
  const artist = (r.aa || r.a || "").trim().toLowerCase();
  const album = (r.al || "").trim().toLowerCase();
  if (!album) return null;
  return artist + " " + album;
}

// EXAKTE Variante (Gross-/Kleinschreibung zaehlt) fuer die "Aufraeumen"-Liste
// Album -- dort sollen "Bootleg"/"BOOTLEG" zwei Gruppen bleiben, genau wie in
// der Bubble-Leiste/den Zusammenfuehrungs-Vorschlaegen (albumGroupCounts()).
// albumGroupKey() selbst bleibt unveraendert, sie treibt weiterhin die
// bewusst groszuegigere Checkbox "nach Album gruppieren" in anderen
// Ansichten.
function albumGroupKeyExact(r) {
  const artist = r.aa || r.a || "";
  const album = r.al || "";
  if (!album) return null;
  return artist + "\u0000" + album;
}

// Duplikat-Gruppen: einmalig nach dem Laden von DATA berechnet (siehe Aufruf
// vor loadFilters() weiter unten), nicht bei jedem render() -- bei
// zehntausenden Zeilen waere ein Neuclustern pro Tastenanschlag in der Suche
// spuerbar. Schreibt r.dg (Gruppen-ID) oder laesst es undefined (keine
// erkannte Kopie).
//
// Zwei Signale, ueber Union-Find zu Gruppen verschmolzen:
//  - exakter Datei-Hash (hs): sichere Kopie, auch bei anderem Pfad/Tag.
//  - normalisiert Interpret+Titel (wie scanner._norm), innerhalb dessen
//    Dauer +-1s (wie scanner._DURATION_TOLERANCE_S): erkennt
//    Qualitaets-Varianten (z.B. MP3 128 + FLAC), die nie denselben Hash haben.
// Wie albumGroupKey(): fehlender Interpret+Titel gruppiert NIE ueber den
// Tag-Weg (sonst landen alle taglosen Dateien in einer Riesengruppe) --
// solche Zeilen bekommen nur ueber einen Hash-Treffer eine Gruppe.
//
// Bucket-basiert (Hash-Map, Tag-Map) statt paarweisem Vergleich -- O(n) ueber
// die ganze Bibliothek, quadratisch nur innerhalb eines Interpret+Titel-
// Buckets (typischerweise 2-4 Zeilen).
let dupGroupsDirty = false;

function computeDuplicateGroups() {
  DATA.forEach(r => { delete r.dg; });
  const parent = new Map();
  DATA.forEach((r, i) => parent.set(i, i));
  const find = i => {
    while (parent.get(i) !== i) { parent.set(i, parent.get(parent.get(i))); i = parent.get(i); }
    return i;
  };
  const union = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent.set(ra, rb); };

  const byHash = new Map();
  DATA.forEach((r, i) => {
    if (r.removed || !r.hs) return;
    if (!byHash.has(r.hs)) byHash.set(r.hs, []);
    byHash.get(r.hs).push(i);
  });
  for (const idxs of byHash.values())
    for (let k = 1; k < idxs.length; k++) union(idxs[0], idxs[k]);

  const norm = s => (s || "").trim().toLowerCase().replace(/\s+/g, " ");
  const byTag = new Map();
  DATA.forEach((r, i) => {
    if (r.removed) return;
    const artist = norm(r.a), title = norm(r.t);
    if (!artist || !title) return;
    const key = artist + " " + title;
    if (!byTag.has(key)) byTag.set(key, []);
    byTag.get(key).push(i);
  });
  const DUP_DURATION_TOLERANCE_S = 1.0;
  for (const idxs of byTag.values())
    for (let a = 0; a < idxs.length; a++)
      for (let b = a + 1; b < idxs.length; b++)
        if (Math.abs(DATA[idxs[a]].du - DATA[idxs[b]].du) <= DUP_DURATION_TOLERANCE_S)
          union(idxs[a], idxs[b]);

  const rootMembers = new Map();
  DATA.forEach((r, i) => {
    if (r.removed) return;
    const root = find(i);
    if (!rootMembers.has(root)) rootMembers.set(root, []);
    rootMembers.get(root).push(i);
  });
  let nextId = 1;
  for (const members of rootMembers.values()) {
    if (members.length < 2) continue;
    const gid = nextId++;
    for (const i of members) DATA[i].dg = gid;
  }
  resetPartiallyDismissedGroups();
}

// Eine Gruppe, in der mindestens ein, aber nicht alle Mitglieder als "kein
// Duplikat" bestaetigt sind, ist eine vormals vollstaendig bestaetigte
// Gruppe, der gerade eine neue, ungeprueft Kopie beigetreten ist (z.B. nach
// einem erneuten Scan). Ein rein optisches Zurueckfallen auf orange reicht
// hier nicht: sonst wuerde die Gruppe automatisch wieder gruen erscheinen,
// sobald die neue Kopie erneut verschwindet, ohne je bewusst neu geprueft
// worden zu sein. Deshalb wird die Bestaetigung der GANZEN Gruppe aktiv
// zurueckgenommen, lokal sofort und serverseitig per Sammel-Aufruf.
async function resetPartiallyDismissedGroups() {
  const byGroup = new Map();
  for (const r of DATA) {
    if (r.dg == null || r.removed) continue;
    if (!byGroup.has(r.dg)) byGroup.set(r.dg, []);
    byGroup.get(r.dg).push(r);
  }
  const pathsToReset = [];
  for (const members of byGroup.values()) {
    const anyDismissed = members.some(r => r.dd);
    const allDismissed = members.every(r => r.dd);
    if (anyDismissed && !allDismissed) {
      members.forEach(r => { r.dd = 0; });
      pathsToReset.push(...members.map(r => r.p));
    }
  }
  if (!pathsToReset.length) return;
  try {
    await fetch("/api/dup-dismiss-bulk", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths: pathsToReset, flag: false}),
    });
  } catch {
    // Naechster syncMarks()-Lauf gleicht bei einem Fehlschlag ohnehin ab.
  }
}

function filtered() {
  const q = state.q;
  const view = currentView();
  let rows = DATA.filter(r =>
    !r.removed &&
    // Ersatzzeilen tauchen ausschliesslich in der Fremdliste auf, zu der sie
    // gehoeren -- sonst stuenden Cloud-Tracks ploetzlich in "Alle".
    (!r.ext || view.ext) &&
    view.test(r) &&
    matchesSearch(r, q) &&
    // Bubble-Filter einer der drei "Aufraeumen"-Boxen (state.valueFilter) --
    // nur wirksam, solange die passende Liste aktiv ist, siehe mergePreview
    // gleich darunter fuer die Begruendung.
    (!state.valueFilter || GRP_VIEW_FIELD[view.id] !== state.valueFilter.field ||
     rowMatchesValueEntry(r, state.valueFilter.field, state.valueFilter.entry)) &&
    // Zusammenfuehrungs-Vorschau (state.mergePreview) -- nur wirksam,
    // solange die passende "Aufraeumen"-Liste aktiv ist (siehe
    // toggleMergePreview()); in jeder anderen Ansicht wirkungslos, auch wenn
    // sie aus Versehen haengen bliebe.
    (!state.mergePreview || GRP_VIEW_FIELD[view.id] !== state.mergePreview.field ||
     rowMatchesValueEntry(r, state.mergePreview.field, state.mergePreview.a) ||
     rowMatchesValueEntry(r, state.mergePreview.field, state.mergePreview.b)) &&
    // Bubble-Filter der "Auffaelligkeiten"-Liste (state.tagIssueFilter) --
    // wie state.valueFilter nur wirksam, solange diese Liste aktiv ist.
    (!state.tagIssueFilter || view.id !== "tag_issues" ||
     (r.ti || []).some(i => i.code === state.tagIssueFilter))
  );
  if (view.id === "duplicates") {
    // Gruppierung hat hier Vorrang vor „nach Album gruppieren" -- sonst
    // wuerde die Checkbox die Duplikat-Ansicht durcheinanderbringen.
    rows.sort((a, b) => {
      if (a.dg !== b.dg) return (a.dg || 0) - (b.dg || 0);
      // Beste Kopie zuerst: verlustfrei vor verlustbehaftet, dann hoehere
      // deklarierte Bitrate, dann groessere Datei als Tie-Breaker (z.B. VBR).
      const qa = a.fam === "lossless" ? 1 : 0, qb = b.fam === "lossless" ? 1 : 0;
      if (qa !== qb) return qb - qa;
      if ((b.kb || 0) !== (a.kb || 0)) return (b.kb || 0) - (a.kb || 0);
      if ((b.sz || 0) !== (a.sz || 0)) return (b.sz || 0) - (a.sz || 0);
      return cmpText(a.p || "", b.p || "");
    });
  } else if (view.id === "grp:album") {
    // "Aufraeumen"-Liste Album: EXAKTE Gross-/Kleinschreibung als Gruppen-
    // schluessel (anders als die Checkbox "nach Album gruppieren" unten,
    // die bewusst Schreibvarianten zusammenwirft) -- sonst wuerden "Bootleg"
    // und "BOOTLEG" hier zu einer Gruppe verschmelzen, obwohl Bubble-Leiste,
    // Zusammenfuehrungs-Vorschlaege und die Umbenennen-Aktion sie exakt
    // auseinanderhalten. Reihenfolge bleibt wie gehabt (Tracknummer, Titel).
    const fallback = r => "￿" + (r.a||"") + " " + (r.t||"");
    const gk = sortKeys(rows, r => albumGroupKeyExact(r) ?? fallback(r));
    const tk = sortKeys(rows, r => r.t || "");
    rows.sort((a,b) => {
      const c = cmpText(gk.get(a), gk.get(b));
      if (c !== 0) return c;
      if ((a.tn || 0) !== (b.tn || 0)) {
        if (!a.tn) return 1;
        if (!b.tn) return -1;
        return a.tn - b.tn;
      }
      return cmpText(tk.get(a), tk.get(b));
    });
  } else if (state.groupAlbums) {
    // Ungruppierte Zeilen (kein Album-Tag) bekommen einen eindeutigen
    // Fallback-Schluessel mit Sortierpriorisierung ans Gruppenende ("￿"
    // sortiert nach jedem normalen Album-Schluessel), statt an einer
    // zufaelligen Position zu clustern.
    const fallback = r => "￿" + (r.a||"").toLowerCase() + " " + (r.t||"").toLowerCase();
    // albumGroupKey() (drei Feldzugriffe, zwei trim(), zwei toLowerCase())
    // lief bisher in jedem einzelnen Vergleich erneut -- bei 10.822 Zeilen
    // rund 300.000 mal statt 10.822 mal.
    const gk = sortKeys(rows, r => albumGroupKey(r) ?? fallback(r));
    const tk = sortKeys(rows, r => r.t || "");
    rows.sort((a,b) => {
      const c = cmpText(gk.get(a), gk.get(b));
      if (c !== 0) return c;
      // Innerhalb der Gruppe: Track-Nummer fuehrend, 0/fehlend ans
      // Gruppenende statt an den Anfang, danach Titel als letzter Fallback.
      if ((a.tn || 0) !== (b.tn || 0)) {
        if (!a.tn) return 1;
        if (!b.tn) return -1;
        return a.tn - b.tn;
      }
      return cmpText(tk.get(a), tk.get(b));
    });
  } else if (view.id === "grp:genre") {
    // "Aufraeumen"-Liste Genre: Gruppierung nach Genre EXAKT (siehe grp:album
    // oben -- "Bootleg"/"BOOTLEG" bleiben zwei Gruppen, genau wie in der
    // Bubble-Leiste und den Zusammenfuehrungs-Vorschlaegen). Innerhalb der
    // Gruppe nach Interpret dann Titel (Genre hat keine eigene natuerliche
    // Reihenfolge wie eine Tracknummer).
    // Kein trim() im Gruppenschluessel selbst -- ein reiner Leerzeichen-
    // Unterschied ist eine der vier Zusammenfuehrungs-Vorschlagsarten
    // (siehe findMergeSuggestions()) und soll auch hier zwei Gruppen bleiben,
    // genau wie in der Bubble-Leiste (groupCounts() trimmt ebenfalls nicht).
    const fallback = r => "￿" + (r.a||"").toLowerCase() + " " + (r.t||"").toLowerCase();
    const gk = sortKeys(rows, r => r.ge || fallback(r));
    const ak = sortKeys(rows, r => (r.a||"").toLowerCase());
    const tk = sortKeys(rows, r => r.t || "");
    rows.sort((a,b) =>
      cmpText(gk.get(a), gk.get(b)) || cmpText(ak.get(a), ak.get(b)) || cmpText(tk.get(a), tk.get(b)));
  } else if (view.id === "grp:artist") {
    // "Aufraeumen"-Liste Interpret: Gruppierung nach Interpret EXAKT (siehe
    // grp:genre oben), innerhalb der Gruppe nach Album dann Tracknummer dann
    // Titel (Diskografie-Ansicht, analog zur Album-Sortierung).
    // Kein trim() -- siehe grp:genre oben.
    const fallback = r => "￿" + (r.t||"").toLowerCase();
    const gk = sortKeys(rows, r => r.a || fallback(r));
    const alk = sortKeys(rows, r => (r.al||"").toLowerCase());
    const tk = sortKeys(rows, r => r.t || "");
    rows.sort((a,b) => {
      const c = cmpText(gk.get(a), gk.get(b));
      if (c !== 0) return c;
      const c2 = cmpText(alk.get(a), alk.get(b));
      if (c2 !== 0) return c2;
      if ((a.tn || 0) !== (b.tn || 0)) {
        if (!a.tn) return 1;
        if (!b.tn) return -1;
        return a.tn - b.tn;
      }
      return cmpText(tk.get(a), tk.get(b));
    });
  } else if (state.sort) {
    const k = state.sort;
    // "n" (Datei) ist die einzige Spalte ohne gleichnamiges Datenfeld --
    // sortiert wird nach dem angezeigten Text.
    const val = k === "n" ? (r => displayName(r).toLowerCase()) : (r => r[k]);
    // Auch hier den Schluessel je Zeile einmal bilden: bei k === "n" steckt
    // darin displayName() + toLowerCase(), das sonst in jedem Vergleich neu
    // liefe. Ob textlich oder numerisch verglichen wird, entscheidet ein
    // Blick auf den ersten Schluessel statt ein typeof je Vergleich.
    const keys = sortKeys(rows, val);
    const textual = rows.length > 0 && typeof keys.get(rows[0]) === "string";
    const dir = state.dir;
    rows.sort(textual
      ? ((a,b) => cmpText(keys.get(a), keys.get(b)) * dir)
      : ((a,b) => (keys.get(a) - keys.get(b)) * dir));
  } else if (view.ext) {
    // Reihenfolge, wie Music.app bzw. Rekordbox sie fuehren.
    const entry = EXT_CONTENTS.get(view.extKey);
    const order = entry ? entry.paths : [];
    const pos = new Map(order.map((key, i) => [key, i]));
    // extRowKey() lief bisher VIER mal je Vergleich (zweimal has, zweimal
    // get, beide Seiten). Jetzt einmal je Zeile.
    const rank = sortKeys(rows, r => {
      const at = pos.get(extRowKey(r));
      return at === undefined ? order.length : at;
    });
    rows.sort((a, b) => rank.get(a) - rank.get(b));
  } else if (view.id.startsWith("pl:")) {
    // Manuelle Reihenfolge einer eigenen Playlist -- die einzige benutzer-
    // definierte Ordnung im Tool ausser der Warteschlange. Greift nur ohne
    // aktive Spaltensortierung: selectView() legt state.sort beim Wechsel in
    // eine Playlist beiseite, ein Klick auf einen Spaltenkopf holt sie
    // zurueck und hat dann Vorrang (wie in Music.app). Pfade ohne Position
    // (koennen bei einem Wettlauf mit /api/playlists kurz auftreten) wandern
    // ans Ende statt an den Anfang.
    const order = PLAYLIST_ITEMS[view.id.slice(3)] || [];
    const pos = new Map(order.map((path, i) => [path, i]));
    const rank = sortKeys(rows, r => {
      const at = pos.get(r.p);
      return at === undefined ? order.length : at;
    });
    rows.sort((a, b) => rank.get(a) - rank.get(b));
  }
  return rows;
}

// Reiner Hinweis relativ zu einem konfigurierbaren DJ/Club-Referenzband,
// kein Verdikt — Lautheit hängt von Genre und Erscheinungsjahr ab.
function loudnessHint(lu) {
  if (lu < META.loudRefLow) return t("player.loudness_low");
  if (lu > META.loudRefHigh) return t("player.loudness_high");
  return t("player.loudness_ok");
}

function loudnessCell(r) {
  if (!r.lu) return "<span class='path'>–</span>";
  return r.lu.toFixed(1).replace(".",",");
}

function clipCell(r) {
  if (!r.lu) return "<span class='path'>–</span>";
  if (r.tp <= META.loudClip) return "<span class='path'>–</span>";
  return `<span class="cliptag" title="${esc(t("detail.true_peak_clip_title", {tp: String(r.tp.toFixed(1)).replace(".",",")}))}">${esc(t("col.clip"))}</span>`;
}

function spectrumSVG(r) {
  if (!r.sp || !r.sp.length) return `<div class='path'>${esc(t("detail.no_spectrum"))}</div>`;
  const W = 340, H = 110, pad = 18, n = r.sp.length, nyq = r.sr / 2000;
  const max = Math.max(...r.sp);
  const y = v => pad + (Math.min(0, Math.max(-110, v - max)) / -110) * (H - pad*2);
  const pts = r.sp.map((v,i) => `${pad + (i/(n-1))*(W-pad*2)},${y(v).toFixed(1)}`).join(" ");
  const cx = pad + (r.co / nyq) * (W - pad*2);
  const ticks = [16,18,20].map(f => {
    const x = pad + (f/nyq)*(W-pad*2);
    return `<line x1="${x}" y1="${pad}" x2="${x}" y2="${H-pad}" stroke="var(--line)" stroke-dasharray="2,3"/>
            <text x="${x}" y="${H-4}" font-size="9" fill="var(--dim)" text-anchor="middle">${f}k</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:180px;max-width:100%;height:auto">
    ${ticks}
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.2"/>
    <line x1="${cx}" y1="${pad-6}" x2="${cx}" y2="${H-pad}" stroke="var(--fake)" stroke-width="1.5"/>
    <text x="${cx}" y="${pad-9}" font-size="10" fill="var(--fake)" text-anchor="middle">${r.co} kHz</text>
  </svg>`;
}

// Zell-Renderer je reorderbarer Spalte (OPTIONAL_COLUMNS-Keys) — 1:1 aus den
// frueher fest verketteten Zeilen-Templates uebernommen, jetzt datengetrieben
// nach state.colOrder statt fixer Reihenfolge.
const CELL_RENDERERS = {
  co: r => `<td class="num">${r.co.toFixed(2).replace(".",",")}</td>`,
  kb: r => `<td class="num">${r.fam === "lossless"
      ? "<span class='path'>" + esc(t("field.lossless")) + "</span><br><span class='path'>" + fileExt(r.p) + "</span>"
      : r.kb + (r.mo ? " <span class='path'>" + r.mo + "</span>" : "")
        + "<br><span class='path'>" + fileExt(r.p) + "</span>"}</td>`,
  mk: r => `<td class="num">${r.fam === "lossless" && r.mk ? "~" + r.mk : r.mk}</td>`,
  cf: r => `<td class="num confcell"><span class="conf"><i style="width:${Math.round(r.cf*100)}%"></i></span>
      ${r.cf.toFixed(2).replace(".",",")}</td>`,
  st: r => `<td class="num">${r.st.toFixed(0)}${r.bw ? " ▮" : ""}</td>`,
  lu: r => `<td class="num">${loudnessCell(r)}</td>`,
  tp: r => `<td class="num">${clipCell(r)}</td>`,
  du: r => `<td class="num">${fmtTime(r.du)}</td>`,
  rb: r => `<td class="num">${r.rb ? `<span class="rbyes" title="${esc(t("badge.in_rekordbox"))}">✓</span>` : ""}</td>`,
  im: r => `<td class="num">${r.im ? `<span class="rbyes" title="${esc(t("badge.in_music_app"))}">✓</span>` : ""}</td>`,
  da: r => `<td class="num">${esc(fmtDate(r.da))}</td>`,
  // r.gone ausschliessen: die Datei fehlt am gespeicherten Pfad, /api/cover
  // liefert dann 410 -- ohne diese Pruefung haengt ein kaputtes Bild-Icon in
  // der Zelle statt einfach nichts anzuzeigen (has_cover stammt vom letzten
  // erfolgreichen Scan und bleibt bestehen, bis die Datei wiedergefunden wird).
  cv: r => `<td>${apiMode && !r.gone
      ? (() => {
          const unplayable = rowUnplayable(r);
          const cls = unplayable ? " covercell-unplayable" : "";
          const title = unplayable
            ? esc(t("player.aiff_unsupported_browser"))
            : esc(t(r.cv ? "action.cover_enlarge" : "cover.none"));
          return r.cv
            ? `<button class="covercell-btn" data-cover="${r.i}" title="${title}">
                 <img class="covercell${cls}" loading="lazy" src="${coverUrl(r.p)}" alt="Cover"></button>`
            : `<div class="covercell-empty${cls}" title="${title}"></div>`;
        })()
      : ""}</td>`,
  al: r => `<td>${esc(r.al || "")}</td>`,
  tn: r => `<td class="num">${r.tn ? (r.tt ? `${r.tn}/${r.tt}` : r.tn) : ""}</td>`,
  aa: r => `<td>${esc(r.aa || "")}</td>`,
  cp: r => `<td>${esc(r.cp || "")}</td>`,
  ge: r => `<td>${esc(r.ge || "")}</td>`,
  yr: r => `<td class="num">${r.yr || ""}</td>`,
  bp: r => `<td class="num">${r.bp ? r.bp.toFixed(1).replace(".",",") : ""}</td>`,
  cm: r => `<td>${esc(r.cm || "")}</td>`,
  // Eigenstaendige Spalte neben dem Badge in der Namenszelle -- zeigt jede
  // gefundene Auffaelligkeit als Zeile plus zwei Aktionsknoepfe direkt in
  // der Tabelle, ohne erst das Popup (openTagIssuesPopup()) oeffnen zu
  // muessen. "Automatisch beheben" nur, wenn mindestens ein Fund sicher
  // automatisch behebbar ist (AUTO_FIXABLE_TAG_ISSUE_CODES); "Manuell
  // beheben" springt direkt in den bestehenden Tags-Dialog.
  ti: r => {
    const issues = r.ti || [];
    if (!issues.length) return `<td></td>`;
    const lines = issues.map(i => `<div class="tiline">${esc(t("tagissue." + i.code))}${
      i.field ? ` <span class="dim">(${esc(t("field." + i.field) || i.field)})</span>` : ""}</div>`).join("");
    const autoOk = issues.some(i => AUTO_FIXABLE_TAG_ISSUE_CODES.has(i.code));
    return `<td><div class="ticell">${lines}<div class="tiactions">
      <button class="act small" data-tagfixmanual="${esc(r.p)}">${ICONS.pencil} ${esc(t("fixtag.manual_edit_link"))}</button>
      ${autoOk ? `<button class="act small" data-tagfixauto="${esc(r.p)}">${ICONS.sparkles} ${esc(t("fixtag.auto_fix_button"))}</button>` : ""}
    </div></div></td>`;
  },
  a: r => `<td>${esc(r.a || "")}</td>`,
  t: r => `<td>${esc(r.t || "")}</td>`,
  // Zweites Argument: nur die Haupttabelle bindet den Klick-Handler fuer
  // data-copy, in renderDrops bleibt der Pfad deshalb ein schlichter Text.
  // Die Statushinweise schliessen sich je Tabelle gegenseitig aus (gone/mc
  // gibt es nur in DATA, mikPick/libAdded nur in drops).
  n: (r, copyable) => `<td><div class="name">${esc(displayName(r))}${
        r.ext ? ` <span class="extbadge">${esc(t(r.p ? "badge.not_scanned" : "badge.no_local_file"))}</span>`
              : r.gone ? ` <span class="missing">${esc(t("badge.file_deleted"))}</span>` : ""}${
        merkPlaylists().map(n => (PLAYLIST_SETS[n.id] && PLAYLIST_SETS[n.id].has(r.p))
          ? ` <span class="fltag" style="background:var(--${flColorTok(n)}-bg);color:var(--${flColorTok(n)})">` +
            `${iconGlyphHtml(playlistIconValue(n))}${esc(n.name)}</span>`
          : "").join("")}${
        r.mc ? ` <span class="correctedtag">${esc(t("badge.corrected"))}</span>` : ""}${
        r.mikPick ? ` <span class="miktag">${esc(t("badge.mik_pick"))}</span>` : ""}${
        r.libAdded ? ` <span class="miktag">${esc(t("badge.lib_added"))}</span>` : ""}${
        hasAnyTagIssues(r) ? ` <span class="probtag" data-tagissues="${esc(r.p)}" title="${esc(t("views.tag_issues"))}">${
          TREE_ICONS.tag_issues}${r.ti.length}</span>` : ""}</div>
      ${copyable
        ? `<div class="path copyable" data-copy="${esc(r.p)}" title="${esc(t("action.copy_path"))}">${esc(r.p)}</div>`
        : `<div class="path">${esc(r.p)}</div>`}</td>`,
};

// Angezeigter Name einer Zeile -- "Interpret — Titel", ersatzweise der
// Dateiname. Auch die Sortierung der Spalte "Datei" haengt daran (die Zeilen
// haben kein eigenes Feld "n").
const displayName = r => (r.a && r.t) ? (r.a + " — " + r.t) : (r.a || r.t || baseName(r.p));

let lastSelPos = null;      // Shift-Klick-Anker — verfällt bei jeder neuen Zeilenliste

// Erhoeht state.shown und rendert neu -- merkt sich dabei den bisherigen
// Stand als freshFrom, damit render() nur die neu hinzugekommenen Zeilen mit
// der Fade-in-Klasse "fresh" versieht (nicht die schon sichtbaren, sonst
// wuerde z.B. auch ein Sortierklick alte Zeilen erneut einfaden lassen).
function beginLoadMore(rows, targetShown) {
  freshFrom = state.shown;
  state.shown = Math.min(targetShown, rows.length);
  render();
}

// ── "Weitere Optionen"-Menue je Zeile ────────────────────────────────────
// Buendelt Bitrate korrigieren/Ausblenden/Korrigieren/Loeschen (data-more-
// Knopf in render(), ersetzt die vormals einzeln sichtbaren Icons) hinter
// einem gemeinsamen Knopf. EIN wiederverwendetes Menue-Element (#rowMoreMenu
// in index.html) statt je Zeile ein eigenes -- position:fixed + Positionierung
// per getBoundingClientRect() beim Oeffnen umgeht die sticky-/overflow:hidden-
// Regeln der Aktionsspalte (td.links), unter denen ein absolut in der Zelle
// verankertes Menue abgeschnitten wuerde.
// Gibt den Knoten der gerade geoeffneten eigenen Playlist zurueck -- oder
// null, wenn eine Systemliste oder eine Merkliste gewaehlt ist.
function currentPlaylistNode() {
  const id = String(state.view || "");
  if (!id.startsWith("pl:")) return null;
  return PLAYLISTS.find(n => n.id === id.slice(3)) || null;
}

// Der gerade geoeffnete Listenknoten, egal ob regulaer oder smart. Fuer die
// Kopfzeile; Verlauf und "Aus Liste entfernen" haengen dagegen weiter an
// currentPlaylistNode(), weil sich eine berechnete Liste nicht von Hand
// aendern laesst.
function currentListNode() {
  const id = String(state.view || "");
  const source = extSourceOfView(id);
  if (source) {
    // Fremde Liste: der Knoten kommt aus dem geladenen Baum der Quelle, nicht
    // aus PLAYLISTS. Er traegt kein 'kind' unserer Tabelle -- die Kopfzeile
    // schaltet daran den Verlauf ab (editable = kind === "playlist").
    const key = id.slice(EXT_SOURCES[source].prefix.length);
    const n = EXT_TREES[source].nodes.find(x => String(x.id) === key);
    return n ? {id: id, name: n.name, kind: `ext_${source}`, icon: null, color: null} : null;
  }
  if (id.startsWith("pl:") || id.startsWith("sm:")) {
    return PLAYLISTS.find(n => n.id === id.slice(3)) || null;
  }
  // Feste System-Views (Alle/Ausgeblendet/Datei fehlt/Duplikate): dieselbe
  // Kopfzeilen-Box wie eine eigene Playliste, aber ohne editierbares
  // Icon/Farbe -- ein neutrales Symbol aus TREE_ICONS.
  const view = VIEWS.find(v => v.id === id);
  if (!view) return null;
  return {
    id, name: view.label, kind: "view",
    icon: TREE_ICONS[id] || null, color: null,
    count: live().filter(view.test).length,
  };
}

function rowMoreMenuItems(r) {
  const items = [];
  // "Zu Playlist hinzufuegen" sitzt als eigenes Icon im "top"-Aktionsblock
  // neben "Zur Warteschlange hinzufuegen" (siehe render()) -- hier nur noch
  // das Gegenstueck, das ausserhalb der jeweiligen Liste keinen Sinn ergibt.
  const inList = apiMode ? currentPlaylistNode() : null;
  if (inList) {
    items.push({
      icon: ICONS.listX, label: t("tree.remove_from_list", {name: inList.name}),
      action: () => removeFromPlaylist(inList.id, [r.p]),
    });
  }
  if (!r.gone && apiMode && isFixable(r)) {
    items.push({
      icon: ICONS.fix, cls: "", title: t("action.fix_bitrate", {kbps: r.mk}),
      label: t("rowmenu.fix_bitrate"), action: () => openFixPopup(r),
    });
  }
  if (!r.gone) {
    items.push({
      icon: r.ig ? ICONS.eyeOff : ICONS.eye, cls: "ig",
      title: r.ig ? t("action.show_track") : t("action.hide_track"),
      label: r.ig ? t("rowmenu.show_track") : t("rowmenu.hide_track"),
      action: () => setIgnored(r, !r.ig),
    });
    items.push({
      icon: ICONS.correct, cls: "corr",
      title: r.mc ? t("action.undo_correct") : t("action.mark_corrected"),
      label: r.mc ? t("rowmenu.undo_correct") : t("rowmenu.mark_corrected"),
      action: () => setCorrected(r, !r.mc),
    });
  }
  if (!r.gone && apiMode) {
    items.push({
      icon: ICONS.trash, cls: "del", title: t("action.to_trash"),
      label: t("rowmenu.trash"), action: () => trashTrack(r),
    });
  }
  return items;
}

function closeRowMoreMenu() {
  const menu = document.getElementById("rowMoreMenu");
  if (!menu) return;
  menu.classList.remove("show");
  menu.innerHTML = "";
  if (menu._forBtn) menu._forBtn.classList.remove("open");
  menu._forBtn = null;
}

function openRowMoreMenu(r, btn) {
  openContextMenu(rowMoreMenuItems(r), btn);
}

// Dasselbe Menue-Element auch fuer den Seitenbaum (openNodeMenu()) -- die
// Positionierung per getBoundingClientRect() und das Umklappen nach oben
// gelten dort genauso, und zwei parallele Menue-Implementierungen wuerden
// beim Schliessen (Klick daneben, Escape, Scrollen) auseinanderlaufen.
function openContextMenu(items, btn) {
  const menu = document.getElementById("rowMoreMenu");
  if (!menu) return;
  const reopening = menu._forBtn === btn;
  closeRowMoreMenu();
  if (reopening) return;
  // Ein Eintrag {sep: true} wird zur waagerechten Trennlinie. Die Klick-
  // Handler duerfen deshalb NICHT ueber den Kind-Index laufen -- ein Trenner
  // wuerde die Zuordnung um eins verschieben. Stattdessen nur die Knoepfe
  // einsammeln und mit den Eintraegen ohne Trenner paaren.
  menu.innerHTML = items.map(it => it.sep
    ? `<div class="menusep"></div>`
    : `<button type="button" class="${it.cls || ""}" title="${esc(it.title || it.label)}">` +
      `${it.icon || ""}<span>${esc(it.label)}</span></button>`).join("");
  const actions = items.filter(it => !it.sep);
  menu.querySelectorAll("button").forEach((el, idx) => el.onclick = ev => {
    ev.stopPropagation();
    closeRowMoreMenu();
    actions[idx].action();
  });
  menu._forBtn = btn;
  btn.classList.add("open");
  menu.classList.add("show");
  const rect = btn.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = rect.right - mw;
  if (left < 4) left = 4;
  let top = rect.bottom + 4;
  if (top + mh > window.innerHeight - 4) top = Math.max(4, rect.top - mh - 4);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

document.addEventListener("click", ev => {
  if (!ev.target.closest("#rowMoreMenu") && !ev.target.closest("[data-more]")
      && !ev.target.closest("[data-nodemenu]")) closeRowMoreMenu();
});
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeRowMoreMenu(); });
window.addEventListener("scroll", closeRowMoreMenu, {passive: true, capture: true});
window.addEventListener("resize", closeRowMoreMenu);

function render() {
  closeRowMoreMenu();
  lastSelPos = null;
  renderSearchChips();
  renderPlaylistHeaderValueBox();
  renderIssueBubbles();
  if (dupGroupsDirty) { computeDuplicateGroups(); dupGroupsDirty = false; }
  const rows = filtered();
  state.totalFiltered = rows.length;
  // Ergebniszahl direkt an der Suchleiste -- state.totalFiltered ist bereits
  // NACH Liste/Verdikt-Chips/"nur harte Kante"/Suche gezaehlt (siehe
  // filtered()), zeigt also wirklich das Suchergebnis, nicht die
  // Bibliotheksgroesse. "weitere laden" (list.showing_count unten am Ende der
  // Tabelle) zaehlt zusaetzlich die aktuell gerenderte Teilmenge dazu -- hier
  // reicht die reine Trefferzahl.
  const rc = document.getElementById("searchResultCount");
  if (rc) rc.textContent = t("search.result_count", {count: rows.length.toLocaleString("de-DE")});
  // Auswahl auf die aktuell gefilterte Ansicht begrenzen -- nach einem
  // Wechsel von View/Verdikt-Chips/Suche/Schwellwerten sollen zuvor
  // ausgewaehlte, jetzt nicht mehr sichtbare Zeilen nicht "geheim" in
  // state.selected haengen bleiben (sonst wirken Bulk-Aktionen spaeter auf
  // Zeilen aus einer laengst verlassenen Ansicht). "weitere laden" schneidet
  // die Liste erst danach per state.shown, ist also hier nicht betroffen.
  if (state.selected.size) {
    const visible = new Set(rows.map(r => r.i));
    for (const i of state.selected) if (!visible.has(i)) state.selected.delete(i);
  }
  const view = currentView();
  // In der Duplikate-Ansicht ist die Gruppierung immer aktiv (siehe
  // groupMode weiter unten) -- die Checkbox waere dort ohne Wirkung und
  // wird deshalb gesperrt statt einen Zustand vorzutaeuschen, der nicht
  // greift.
  const gaToggle = document.getElementById("groupAlbums");
  if (gaToggle) {
    const forced = view.id === "duplicates" || view.id in GRP_VIEW_FIELD;
    gaToggle.disabled = forced;
    gaToggle.closest(".toggle").classList.toggle("disabled", forced);
  }
  const tb = document.getElementById("tb");
  // Eine aufgeklappte Zeile traegt die Wellenform-Ansicht (siehe
  // mountLibraryPlayer(), traegt bei den Einzelpruefungen zusaetzlich das
  // eigene Audio-Objekt, siehe mountDropPlayer()) -- tb.innerHTML weiter
  // unten wuerde sie sonst zerstoeren, auch wenn render() nur wegen einer
  // ANDEREN Zeile aufgerufen wurde (z.B. Klick auf "Zu Rekordbox hinzufuegen"
  // bei einem Nachbartrack). Die .detail-Knoten werden deshalb vor dem
  // Neuaufbau beiseitegenommen und danach, falls ihre Zeile noch in der
  // aktuellen Ansicht steht, an derselben Stelle wieder eingehaengt -- alles
  // synchron, bevor der jeweilige MutationObserver ueberhaupt zum Zug kommt.
  const expandedByIdx = new Map();
  tb.querySelectorAll("tr.detail.expanded").forEach(det => {
    const tr = det.previousElementSibling;
    if (tr && tr.classList.contains("row")) {
      expandedByIdx.set(+tr.dataset.i, det);
      det.remove();
    }
  });
  const slice = rows.slice(0, state.shown);
  const hid = k => state.hiddenCols.includes(k);
  // Gruppierung: Album-Checkbox ODER die Duplikate-Ansicht (dort immer aktiv,
  // unabhaengig von der Checkbox -- filtered() sortiert dafuer bereits nach
  // r.dg). Beide teilen sich dieselbe Header-/Zaehl-Logik, nur Schluessel-
  // und Label-Funktion unterscheiden sich.
  const groupMode = view.id === "duplicates" ? "dup"
    : (view.id === "grp:album" || state.groupAlbums) ? "album"
    : view.id === "grp:genre" ? "genre"
    : view.id === "grp:artist" ? "artist"
    : null;
  // grp:album/grp:genre/grp:artist gruppieren EXAKT (Gross-/Kleinschreibung
  // UND Leerzeichen zaehlen, kein trim()) -- sie sollen "Bootleg"/"BOOTLEG"
  // bzw. "Bootleg"/"Bootleg " als je zwei Gruppen zeigen, genau wie
  // Bubble-Leiste und Zusammenfuehrungs-Vorschlaege (groupCounts()/
  // albumGroupCounts() trimmen ebenfalls nicht). Die Checkbox "nach Album
  // gruppieren" (state.groupAlbums, in anderen Ansichten) behaelt ihr
  // bisheriges, bewusst grosszuegigeres albumGroupKey() unveraendert bei.
  const groupKeyOf = groupMode === "dup" ? (r => r.dg ?? null)
    : groupMode === "album" ? (view.id === "grp:album" ? albumGroupKeyExact : albumGroupKey)
    : groupMode === "genre" ? (r => r.ge || null)
    : groupMode === "artist" ? (r => r.a || null)
    : null;
  // Gruppen-Gesamtzahl aus der VOLLSTAENDIGEN gefilterten/sortierten Liste,
  // nicht nur dem geladenen Ausschnitt -- der Header zeigt sonst bei
  // paginierten Gruppen eine falsche (zu niedrige) Anzahl.
  // In der Duplikate-Ansicht faellt im selben Durchgang mit ab, ob JEDE Kopie
  // einer Gruppe bestaetigt ist (faerbt den Gruppenkopf). Das stand frueher
  // als rows.filter(...) IM Gruppenkopf, lief also einmal ueber alle Zeilen
  // je gezeichnetem Kopf -- bei vielen kleinen Gruppen quadratisch.
  const groupAllDismissed = groupMode === "dup" ? new Map() : null;
  const groupTotals = groupMode ? (() => {
    const m = new Map();
    for (const gr of rows) {
      const k = groupKeyOf(gr);
      if (k === null) continue;
      m.set(k, (m.get(k) || 0) + 1);
      if (groupAllDismissed) groupAllDismissed.set(k, (groupAllDismissed.get(k) ?? true) && !!gr.dd);
    }
    return m;
  })() : null;
  let lastGroupKey;   // Sentinel undefined -- erste Zeile bekommt so garantiert einen Header
  tb.innerHTML = slice.map((r,i) => {
    let groupHeader = "";
    if (groupMode) {
      const key = groupKeyOf(r);
      if (key !== null && key !== lastGroupKey) {
        if (groupMode === "dup") {
          const allDismissed = groupAllDismissed.get(key) === true;
          groupHeader = `<tr class="albumgroup ${allDismissed ? "dupok" : "dupwarn"}"><td colspan="100">${esc(r.a || "")} — ${esc(r.t || "")}
              <span class="path">(${esc(t("group.copies_count", {count: groupTotals.get(key)}))})</span>
              <button type="button" class="dupdismissbtn" data-dupdismiss="${key}">${
                esc(t(allDismissed ? "group.dup_undismiss_button" : "group.dup_confirm_button"))}</button></td></tr>`;
        } else if (groupMode === "album") {
          groupHeader = `<tr class="albumgroup"><td colspan="100">${esc(r.al || "")} <span class="albumgroup-artist">— ${esc(r.aa || r.a || "")}</span>
              <span class="path">(${esc(t("group.tracks_count", {count: groupTotals.get(key)}))})</span>
              <button type="button" class="genrerename" data-grouprename="album" data-value="${esc(r.al || "")}"
                      data-groupartist="${esc(r.aa || r.a || "")}"
                      title="${esc(fieldEditTitle("album"))}">${ICONS.edit}</button></td></tr>`;
        } else if (groupMode === "genre") {
          const label = r.ge || t("group.no_genre");
          groupHeader = `<tr class="albumgroup"><td colspan="100">${esc(label)}
              <span class="path">(${esc(t("group.tracks_count", {count: groupTotals.get(key)}))})</span>${
                r.ge ? `<button type="button" class="genrerename" data-grouprename="genre" data-value="${esc(r.ge)}"
                      title="${esc(fieldEditTitle("genre"))}">${ICONS.edit}</button>` : ""}</td></tr>`;
        } else if (groupMode === "artist") {
          const label = r.a || t("group.no_artist");
          groupHeader = `<tr class="albumgroup"><td colspan="100">${esc(label)}
              <span class="path">(${esc(t("group.tracks_count", {count: groupTotals.get(key)}))})</span>${
                r.a ? `<button type="button" class="genrerename" data-grouprename="artist" data-value="${esc(r.a)}"
                      title="${esc(fieldEditTitle("artist"))}">${ICONS.edit}</button>` : ""}</td></tr>`;
        }
      }
      lastGroupKey = key;
    }
    const nowPlaying = queueState.current && queueState.current.i === r.i;
    return groupHeader + `
    <tr class="row${r.ig ? " ign" : ""}${r.mc ? " corr" : ""}${r.ext ? " extrow" : r.gone ? " gone" : ""}${state.selected.has(r.i) ? " picked" : ""}${r.i === cursorRowI ? " kbcursor" : ""}${freshFrom >= 0 && i >= freshFrom ? " fresh" : ""}" data-i="${r.i}" draggable="${r.ext ? "false" : "true"}">
      <td class="sel"><input type="checkbox" class="selchk" data-sel="${r.i}" ${state.selected.has(r.i) ? "checked" : ""}>${
        nowPlaying ? `<span class="nowplayingicon" title="${esc(t("player.now_playing_title"))}">${ICONS.audioLines}</span>` : ""}</td>
      ${hid("v") ? "" : `<td><span class="badge ${verdictOf(r)}">${VERDICT_ICONS[verdictOf(r)]}${labels[verdictOf(r)]}</span></td>`}
      ${state.colOrder.filter(k => !hid(k) && CELL_RENDERERS[k]).map(k => CELL_RENDERERS[k](r, true)).join("")}
      <td class="filler"></td>
      <td class="links"><div class="actions">
        ${r.ext ? "" : `<div class="actiongroup top">
          ${(!r.gone || r.ext || !apiMode) ? "" :
            `<button class="iconbtn plain" data-relink="${r.i}"
                     title="${esc(t("action.relink"))}">${ICONS.relink}</button>`}
          ${r.gone ? "" : `<button class="iconbtn" data-play="${r.i}" title="${esc(t("action.listen"))}">${ICONS.play}</button>`}
          ${r.gone ? "" :
            `<button class="iconbtn" data-play-next="${r.i}" title="${esc(t("action.play_next"))}">${ICONS.listStart}</button>`}
          ${r.gone ? "" :
            `<button class="iconbtn" data-queue-add="${r.i}" title="${esc(t("action.queue_add"))}">${ICONS.listEnd}</button>`}
          ${(r.gone || !apiMode) ? "" : `<span class="sep" data-sep="1"></span>`}
          ${(r.gone || !apiMode) ? "" :
            `<button class="iconbtn plain" data-playlistadd="${r.i}"
                     title="${esc(t("tree.add_to_playlist"))}">${ICONS.listPlus}</button>`}
          ${(r.gone || !apiMode || !REKORDBOX_NAME) ? "" : (REKORDBOX_PLAYLIST
            ? `<button class="iconbtn plain" data-rbadd="${r.i}"
                       title="${esc(t("action.add_to_rekordbox", {playlist: REKORDBOX_PLAYLIST}))}">${
                       appIcon("rekordbox", "Rekordbox")}</button>`
            : disabledAppIcon("rekordbox", "Rekordbox", REKORDBOX_MISSING_HINT))}
          ${(r.gone || !apiMode) ? "" : `<span class="sep" data-sep="1"></span>`}
          ${r.gone || apiMode ? "" :
            `<a class="reveal-link" href="${fileURL(dirName(r.p))}" title="${esc(t("action.open_folder"))}">📁</a>`}
          ${r.gone || apiMode ? "" :
            `<a class="iconbtn plain" href="${fileURL(r.p)}" title="${esc(t("action.open_in_music"))}">${ICONS.itunes}</a>`}
          ${(r.gone || !apiMode) ? "" :
            `<button class="iconbtn plain" data-tags="${r.i}" title="${esc(t("action.edit_tags"))}">${ICONS.edit}</button>`}
          ${(r.gone || !apiMode) ? "" :
            `<button class="iconbtn plain" data-rescan="${r.i}"
                     title="${esc(t("action.rescan"))}">${ICONS.pickaxe}</button>`}
          ${rowMoreMenuItems(r).length ? `<span class="sep" data-sep="1"></span>
          <button class="iconbtn plain" data-more="${r.i}" title="${esc(t("action.more_options"))}">${ICONS.moreVert}</button>` : ""}
        </div>`}
        ${(r.gone || !apiMode) ? "" : `<div class="actiongroup" data-group="open">
          <div class="grouplabel">${esc(t("action.group_open"))}</div>
          <div class="groupicons">
            <button class="iconbtn plain" data-reveal="${esc(r.p)}"
                     title="${esc(t("action.reveal_finder"))}">${appIcon("finder", "Finder")}</button>
            ${!MUSIC_NAME ? "" : `<button class="iconbtn plain" data-music="${r.i}"
                     title="${esc(t("action.open_in_music"))}">${appIcon("music", "Music")}</button>`}
            ${!EDITOR_NAME ? "" : `<button class="iconbtn plain" data-rx="${r.i}"
                     title="${esc(t("action.open_in", {name: EDITOR_NAME}))}">${appIcon("editor", EDITOR_NAME)}</button>`}
            ${!DAW_NAME ? "" :
              `<button class="iconbtn plain" data-rdaw="${r.i}"
                       title="${esc(t("action.open_in", {name: DAW_NAME}))}">${appIcon("daw", DAW_NAME)}</button>`}
            ${!MIK_NAME ? "" : `<button class="iconbtn plain" data-mik="${r.i}"
                         title="${esc(t("action.open_in_mik", {name: MIK_NAME}))}">${
                         appIcon("mik", MIK_NAME)}</button>`}
          </div>
        </div>`}
        ${!ALL_SHOPS.some(s => s.enabled && s.url) ? "" : `<div class="actiongroup" data-group="search">
          <div class="grouplabel">${esc(t("action.group_search"))}</div>
          <div class="groupicons">${shopLinks(r)}</div>
        </div>`}
        ${(r.gone || !apiMode || !merkPlaylists().length) ? "" : `<div class="actiongroup" data-group="favorites">
          <div class="grouplabel">${esc(t("action.group_favorites"))}</div>
          <div class="groupicons flgroup">
            ${merkPlaylists().map(n => {
              const on = !!(PLAYLIST_SETS[n.id] && PLAYLIST_SETS[n.id].has(r.p));
              return `<button class="iconbtn flbtn${on ? " on" : ""}" data-fl="${n.id}:${r.i}" style="background:var(--${flColorTok(n)})"
                        title="${esc(n.name)}">${iconGlyphHtml(playlistIconValue(n))}</button>`;
            }).join("")}
          </div>
        </div>`}
      </div></td>
    </tr>`;
  }).join("");
  freshFrom = -1;
  if (expandedByIdx.size) {
    expandedByIdx.forEach((det, idx) => {
      const freshTr = tb.querySelector(`tr.row[data-i="${idx}"]`);
      if (freshTr) { freshTr.classList.add("expanded"); freshTr.after(det); }
      // sonst: die Zeile ist aus der aktuellen Ansicht/den Filtern gefallen --
      // der verwaiste Player bleibt dann unangetastet ausserhalb des DOM.
    });
  }
  syncTableWidths();
  syncToTopButton();

  document.getElementById("more").innerHTML =
    rows.length === 0 ? esc(t("list.no_matches", {view: view.label})) :
    `<div class="morecount">${esc(t("list.showing_count", {shown: slice.length.toLocaleString("de-DE"), total: rows.length.toLocaleString("de-DE")}))}</div>` +
    (slice.length < rows.length
      ? `<a href="#" id="loadmore" class="loadmore-btn">${esc(t("list.load_more", {n: PAGE}))}</a>
         <a href="#" id="loadall" class="loadall-link">${esc(t("list.load_all"))}</a>`
      : "");

  const lm = document.getElementById("loadmore");
  if (lm) lm.onclick = e => { e.preventDefault(); beginLoadMore(rows, state.shown + PAGE); };
  const la = document.getElementById("loadall");
  if (la) la.onclick = e => { e.preventDefault(); beginLoadMore(rows, rows.length); };

  tb.querySelectorAll("tr.row").forEach(tr => tr.ondragstart = ev => {
    const r = DATA[+tr.dataset.i];
    // Titel ohne lokale Datei (Music.app-Playlist ohne Match): draggable
    // steht bereits auf "false" im Markup, diese Zeile ist zusaetzliche
    // Absicherung gegen kuenftige Aenderungen an diesem Attribut.
    if (r.ext) { ev.preventDefault(); return; }
    // Ist die gezogene Zeile Teil der Mehrfachauswahl, wandert die GANZE
    // Auswahl mit -- sonst waere das Auswaehlen mehrerer Zeilen fuer den Baum
    // wertlos. Sonst zaehlt nur die eine Zeile, unabhaengig von der Auswahl.
    const paths = state.selected.has(r.i)
      ? filtered().filter(x => state.selected.has(x.i)).map(x => x.p)
      : [r.p];
    ev.dataTransfer.setData(DND_TRACKS, JSON.stringify(paths));
    // "copy" ALLEIN reicht nicht: das Umsortieren innerhalb einer Playlist
    // (weiter unten) setzt beim Ablegen dropEffect="move" -- ohne "move" in
    // effectAllowed lehnt der Browser (v.a. Safari) die Ablage kommentarlos
    // ab, ondrop feuert dann nie. "copyMove" deckt beide Faelle ab: Kopieren
    // beim Ablegen auf eine Playlist im Baum, Verschieben beim Umsortieren.
    ev.dataTransfer.effectAllowed = "copyMove";
    // Zieht man Zeilen aus dem Browserfenster hinaus (z.B. auf den Finder),
    // soll die eigentliche Audiodatei dorthin kopiert werden -- der von
    // Chrome/Safari unterstuetzte "DownloadURL"-Mechanismus laesst den
    // Browser die Datei beim Ablegen selbst herunterladen. Nur mit Server
    // moeglich (/api/audio gibt es im localStorage-Fallback nicht). Der
    // Mechanismus liefert pro Zug nur EINE Datei -- bei Mehrfachauswahl
    // buendelt sie /api/download-zip serverseitig zu einem ZIP, sonst kaeme
    // beim Ziehen mehrerer Zeilen (Browser-Grenze) immer nur die erste an.
    if (apiMode) {
      const downloadUrl = paths.length === 1
        ? `application/octet-stream:${baseName(paths[0])}:${location.origin}/api/audio?path=${encodeURIComponent(paths[0])}`
        : `application/zip:${t("dragout.zip_name", {count: paths.length})}:${location.origin}/api/download-zip?paths=${encodeURIComponent(JSON.stringify(paths))}&name=${encodeURIComponent(t("dragout.zip_name", {count: paths.length}))}`;
      ev.dataTransfer.setData("DownloadURL", downloadUrl);
    }
    document.body.classList.add("dragging-tracks");
    ev.dataTransfer.setDragImage(buildDragBadge(r, paths.length - 1), 20, 20);
  });
  tb.querySelectorAll("tr.row").forEach(tr => tr.ondragend = () => {
    document.body.classList.remove("dragging-tracks");
    clearTreeDropMarks();
    clearRowDropMarks();
  });

  // Umsortieren innerhalb einer eigenen Playlist -- unabhaengig davon, ob
  // gerade eine Spaltensortierung aktiv ist. Frueher hingen die Handler an
  // "state.sort ist leer"; wer einmal auf einen Spaltenkopf geklickt hatte,
  // konnte danach nichts mehr ziehen und bekam dafuer keine Erklaerung. Jetzt
  // wird beim Ablegen auf die manuelle Reihenfolge zurueckgeschaltet (siehe
  // reorderInPlaylist), sodass das Ergebnis auch sichtbar wird.
  const plNode = currentPlaylistNode();
  if (plNode && apiMode) {
    tb.querySelectorAll("tr.row").forEach(tr => {
      tr.ondragover = ev => {
        if (![...(ev.dataTransfer.types || [])].includes(DND_TRACKS)) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        const rect = tr.getBoundingClientRect();
        clearRowDropMarks();
        tr.classList.add((ev.clientY - rect.top) / rect.height < 0.5
          ? "dropbefore" : "dropafter");
      };
      tr.ondragleave = ev => {
        if (!tr.contains(ev.relatedTarget)) clearRowDropMarks();
      };
      tr.ondrop = ev => {
        if (![...(ev.dataTransfer.types || [])].includes(DND_TRACKS)) return;
        ev.preventDefault();
        const rect = tr.getBoundingClientRect();
        const where = (ev.clientY - rect.top) / rect.height < 0.5 ? "before" : "after";
        clearRowDropMarks();
        let paths = [];
        try { paths = JSON.parse(ev.dataTransfer.getData(DND_TRACKS) || "[]"); }
        catch (e) { paths = []; }
        reorderInPlaylist(plNode.id, paths, DATA[+tr.dataset.i].p, where);
      };
    });
  }

  tb.querySelectorAll("tr.row").forEach(tr => tr.onclick = ev => {
    if (ev.target.closest("a") || ev.target.closest("button") || ev.target.closest("input")) return;
    const r = DATA[+tr.dataset.i];
    // Gleiche visuelle Markierung wie bei Pfeiltasten-Navigation/Klick in der
    // Player-Ansicht (siehe setCursorRow()) -- hier zusaetzlich zu dem
    // startQueueFrom()-Aufruf unten (der sie fuer die Player-Ansicht ohnehin
    // setzt), weil die Bearbeiten-Ansicht bei einem Zeilen-Klick nur die
    // Wellenform aufklappt statt sofort abzuspielen (siehe naechster
    // Kommentar) und den Aufruf dort sonst nie erreichen wuerde.
    setCursorRow(r.i);
    // Player-Ansicht: kein Aufklappen mit Wellenform mehr -- der Klick laedt
    // den Track stattdessen in den fixen Mediaplayer-Balken (queueState
    // weiter unten), inkl. neuer, an dieser Position eingefrorener
    // Warteschlange. Siehe Architekturentscheidung im Plan zu Issue #13.
    if (state.layout === "player") { if (!r.gone) startQueueFrom(r); return; }
    // Ersatzzeile einer fremden Playlist: keine Datei, keine Messwerte --
    // eine Detailzeile mit Wellenform waere leer und der Player liefe ins
    // Nichts.
    if (r.ext) return;
    const next = tr.nextElementSibling;
    if (next && next.classList.contains("detail")) { next.remove(); tr.classList.remove("expanded"); return; }
    const det = document.createElement("tr");
    det.className = "detail expanded";
    // 3 feste Spalten (Checkbox, Fuellzelle, Öffnen) + aktuell sichtbare
    // optionale Spalten (inkl. Status, seit der auch ausblendbar ist) --
    // dynamisch statt fest verdrahtet, sonst faellt die Detailzeile (inkl.
    // Player/Waveform) hinter der echten Tabellenbreite zurueck, sobald sich
    // die Spaltenzahl aendert.
    const visibleCols = 3 + OPTIONAL_COLUMNS.length - state.hiddenCols.length;
    det.innerHTML = `<td colspan="${visibleCols}">${detailHTML(r)}</td>`;
    tr.after(det);
    tr.classList.add("expanded");
    mountLibraryPlayer(det, r);
  });

  tb.querySelectorAll("[data-relink]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    relinkMissing([DATA[+b.dataset.relink].p], b);
  });

  tb.querySelectorAll("[data-play]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    if (state.layout === "player") { startQueueFrom(DATA[+b.dataset.play]); return; }
    const tr = b.closest("tr");
    let det = tr.nextElementSibling;
    if (!det || !det.classList.contains("detail")) { tr.click(); det = tr.nextElementSibling; }
    const pb = det && det.querySelector("[data-act=toggle]");
    if (pb) pb.click();
  });

  tb.querySelectorAll("[data-play-next]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    playNext(DATA[+b.dataset.playNext], b);
  });

  tb.querySelectorAll("[data-queue-add]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    addToQueue(DATA[+b.dataset.queueAdd], b);
  });

  tb.querySelectorAll("[data-reveal]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    await reveal(b.dataset.reveal, b);
  });

  tb.querySelectorAll("[data-copy]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    navigator.clipboard.writeText(b.dataset.copy);
    b.textContent = t("action.path_copied");
    b.classList.add("copied");
    clearTimeout(b._copyTimer);
    b._copyTimer = setTimeout(() => { b.textContent = b.dataset.copy; b.classList.remove("copied"); }, 1100);
  });

  tb.querySelectorAll("[data-tagissues]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = DATA.find(x => x.p === b.dataset.tagissues);
    if (row) openTagIssuesPopup(row, false);
  });
  tb.querySelectorAll("[data-tagfixmanual]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = DATA.find(x => x.p === b.dataset.tagfixmanual);
    if (row) openTagsPopup(row, false);
  });
  tb.querySelectorAll("[data-tagfixauto]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const row = DATA.find(x => x.p === b.dataset.tagfixauto);
    if (!row) return;
    b.disabled = true;
    try {
      const fixed = await fixTagIssuesOne(row, false);
      updateCards(); render();
      note(t("toast.tag_issues_fixed", {name: baseName(row.p), count: fixed.length}));
    } catch (err) {
      note(t("error.failed", {error: err.message}), true);
      b.disabled = false;
    }
  });

  tb.querySelectorAll("[data-fl]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const [listId, idx] = b.dataset.fl.split(":");
    const r = DATA[+idx];
    setFavorite(r, listId, !(PLAYLIST_SETS[listId] && PLAYLIST_SETS[listId].has(r.p)));
  });

  tb.querySelectorAll("[data-dupdismiss]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    toggleDupGroupDismissed(Number(b.dataset.dupdismiss));
  });

  tb.querySelectorAll("[data-grouprename]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    renameGroupValue(b.dataset.grouprename, b.dataset.value, b.dataset.groupartist);
  });

  tb.querySelectorAll("[data-store]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openStore(b.dataset.store, b);
  });

  tb.querySelectorAll("[data-rx]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openInEditor(DATA[+b.dataset.rx], b);
  });

  tb.querySelectorAll("[data-rdaw]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openInDaw(DATA[+b.dataset.rdaw], b);
  });

  tb.querySelectorAll("[data-mik]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openInMik([DATA[+b.dataset.mik]], b);
  });

  tb.querySelectorAll("[data-music]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openInMusic(DATA[+b.dataset.music], b);
  });

  tb.querySelectorAll("[data-rbadd]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    addToRekordboxPlaylist([DATA[+b.dataset.rbadd]], b);
  });

  tb.querySelectorAll("[data-playlistadd]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openPlaylistPicker([DATA[+b.dataset.playlistadd].p]);
  });

  tb.querySelectorAll("[data-cover]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openCoverPreview(DATA[+b.dataset.cover]);
  });

  tb.querySelectorAll("[data-tags]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openTagsPopup(DATA[+b.dataset.tags]);
  });

  tb.querySelectorAll("[data-rescan]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const r = DATA[+b.dataset.rescan];
    const before = r.v;
    b.disabled = true;
    const pt = progressToast(t("toast.reanalysing"), {indeterminate: true});
    try {
      const result = await reanalyse([r]);
      pt.done(verdictOf(r) === before
        ? t("toast.reanalysed_same", {verdict: labels[verdictOf(r)]})
        : t("toast.reanalysed_changed", {verdict: labels[verdictOf(r)], before: labels[before]}));
      if (result.rekordboxRunning) note(t("toast.rekordbox_check_skipped_running"), "soft");
      render();
    } catch (err) {
      b.disabled = false;
      pt.fail(t("toast.analysis_failed", {error: err.message}));
    }
  });

  tb.querySelectorAll("[data-more]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    openRowMoreMenu(DATA[+b.dataset.more], b);
  });

  const selBoxes = [...tb.querySelectorAll("[data-sel]")];
  selBoxes.forEach((c, pos) => c.onclick = ev => {
    ev.stopPropagation();
    if (ev.shiftKey && lastSelPos !== null) {
      const [a, b] = [lastSelPos, pos].sort((x, y) => x - y);
      const checked = c.checked;
      for (let k = a; k <= b; k++) {
        const box = selBoxes[k];
        box.checked = checked;
        checked ? state.selected.add(+box.dataset.sel) : state.selected.delete(+box.dataset.sel);
        box.closest("tr").classList.toggle("picked", checked);
      }
    } else {
      c.checked ? state.selected.add(+c.dataset.sel) : state.selected.delete(+c.dataset.sel);
      c.closest("tr").classList.toggle("picked", c.checked);
    }
    lastSelPos = pos;
    syncSelectAll(slice);
    renderBulkBar();
  });
  syncSelectAll(slice);
  renderBulkBar();
}

// ── Mehrfachauswahl ───────────────────────────────────────────────────
// Der Kopf-Checkbox wählt bewusst nur die aktuell ANGEZEIGTEN Zeilen aus,
// nicht die ganze gefilterte Liste — bei paginierten Listen (>PAGE Treffer)
// waeren sonst unsichtbare Zeilen mit ausgewaehlt, ohne dass das erkennbar ist.
function visibleRows() {
  return filtered().slice(0, state.shown);
}

function syncSelectAll(rows) {
  const el = document.getElementById("selAll");
  if (!el) return;
  const allSelected = rows.length > 0 && rows.every(r => state.selected.has(r.i));
  const someSelected = rows.some(r => state.selected.has(r.i));
  el.checked = allSelected;
  el.indeterminate = !allSelected && someSelected;
}

document.getElementById("selAll").onchange = e => {
  const rows = visibleRows();
  if (e.target.checked) rows.forEach(r => state.selected.add(r.i));
  else rows.forEach(r => state.selected.delete(r.i));
  render();
};

// Vorzustand je Merkliste ueber eine Zeilenauswahl: "all" (Haken), "none"
// (leer) oder "mixed" (Fragezeichen) -- treibt sowohl das Icon als auch die
// Zielrichtung des Klicks in bulkApply(). Von der vollen Sammelleiste UND der
// reduzierten Player-Sammelleiste genutzt, deshalb als eigene Funktion statt
// einer lokalen Closure in renderBulkBar().
function flStateOfSelection(rows, listId) {
  const set = PLAYLIST_SETS[listId];
  const onCount = rows.reduce((n, r) => n + (set && set.has(r.p) ? 1 : 0), 0);
  if (onCount === 0) return "none";
  return onCount === rows.length ? "all" : "mixed";
}

// Player-Ansicht: dieselben Sammelaktionen (Tags/Fix/Rescan/Ignore/Correct/
// Trash), die pro Zeile ausgeblendet sind, sollen nicht ueber die
// Sammelleiste als Hintertuer erreichbar bleiben -- nur "zur Warteschlange
// hinzufuegen" (passend zur Player-Ansicht) und die Merklisten bleiben.
function renderPlayerBulkBar(bar) {
  const rows = [...state.selected].map(i => DATA[i]).filter(Boolean);
  bar.innerHTML = `
    <span>${esc(t("bulk.selected_count", {count: state.selected.size.toLocaleString("de-DE")}))}</span>
    <button class="iconbtn plain" id="bulkQueueAdd" title="${esc(t("bulk.queue_add_title"))}">${ICONS.listEnd}</button>
    ${merkPlaylists().length ? `<span class="sep"></span>
    ${merkPlaylists().map(n => {
        const flState = flStateOfSelection(rows, n.id);
        return `<button class="iconbtn flbtn${flState === "all" ? " on" : ""}" data-bulkfl="${n.id}" style="background:var(--${flColorTok(n)})"
        title="${esc(n.name)}">${iconGlyphHtml(playlistIconValue(n))}</button>`;
      }).join("")}` : ""}
    <button class="act small" id="bulkClear">${esc(t("bulk.clear_selection"))}</button>`;
  document.getElementById("bulkQueueAdd").onclick = () => bulkApply("queue-add-all");
  bar.querySelectorAll("[data-bulkfl]").forEach(b => b.onclick = () => bulkApply(`fl:${b.dataset.bulkfl}`));
  document.getElementById("bulkClear").onclick = () => { state.selected.clear(); render(); };
}

function renderBulkBar() {
  const bar = document.getElementById("bulkbar");
  if (!state.selected.size) { bar.style.display = "none"; bar.innerHTML = ""; return; }
  bar.style.display = "";
  if (state.layout === "player") { renderPlayerBulkBar(bar); return; }
  // Vorzustand je Merkliste ueber die Auswahl: "all" (Haken), "none" (leer,
  // wie bisher) oder "mixed" (Fragezeichen) -- treibt sowohl das Icon als
  // auch die Zielrichtung des Klicks in bulkApply().
  const selRowsForFl = merkPlaylists().length
    ? [...state.selected].map(i => DATA[i]).filter(Boolean) : [];
  const flStateOf = listId => flStateOfSelection(selRowsForFl, listId);
  // "Ausblenden"/"Korrigieren" ergeben ohne lokale Datei keinen Sinn (Music-
  // App-Titel ohne Match, "Datei fehlt") -- die drei Knoepfe verschwinden nur,
  // wenn die GESAMTE Auswahl aus solchen Zeilen besteht; bei gemischter
  // Auswahl bleiben sie sichtbar und bulkApply() filtert gone-Zeilen selbst
  // heraus.
  const selRows = [...state.selected].map(i => DATA[i]).filter(Boolean);
  const anyNonGone = selRows.some(r => !r.gone);
  bar.innerHTML = `
    <span>${esc(t("bulk.selected_count", {count: state.selected.size.toLocaleString("de-DE")}))}</span>
    <button class="iconbtn plain" id="bulkQueueAdd" title="${esc(t("bulk.queue_add_title"))}">${ICONS.listEnd}</button>
    ${apiMode ? `<button class="iconbtn plain" id="bulkListAdd" title="${esc(t("tree.add_to_playlist"))}">${ICONS.listPlus}</button>` : ""}
    ${currentPlaylistNode() && apiMode ? `<button class="iconbtn" id="bulkListRemove" title="${esc(t("tree.remove_from_list", {name: currentPlaylistNode().name}))}">${ICONS.listX}</button>` : ""}
    ${!REKORDBOX_NAME ? "" : (REKORDBOX_PLAYLIST
      ? `<button class="iconbtn plain" id="bulkRbAdd" title="${esc(t("bulk.add_to_rekordbox_title", {playlist: REKORDBOX_PLAYLIST}))}">${appIcon("rekordbox", "Rekordbox")}</button>`
      : disabledAppIcon("rekordbox", "Rekordbox", REKORDBOX_MISSING_HINT, "bulkRbAdd"))}
    <span class="sep"></span>
    ${apiMode ? `<button class="iconbtn plain" id="bulkTags" title="${esc(t("bulk.edit_tags_title"))}">${ICONS.edit}</button>` : ""}
    <button class="iconbtn plain" id="bulkFix" title="${esc(t("bulk.fix_title"))}">${ICONS.fix}</button>
    ${selRows.some(hasAutoFixableTagIssues) ? `<button class="iconbtn plain" id="bulkFixTags"
        title="${esc(t("bulk.action_fix_tag_issues"))}">${TREE_ICONS.tag_issues}</button>` : ""}
    <button class="iconbtn plain" id="bulkConvert" title="${esc(t("bulk.convert_title"))}">${ICONS.convert}</button>
    <span class="sep"></span>
    ${!MIK_NAME ? "" : `<button class="iconbtn plain" id="bulkMik" title="${esc(t("bulk.open_in_mik_title"))}">${appIcon("mik", MIK_NAME)}</button>`}
    <span class="sep"></span>
    <button class="iconbtn plain" id="bulkRescan" title="${esc(t("action.rescan"))}">${ICONS.pickaxe}</button>
    ${anyNonGone ? `<span class="sep"></span>
    <button class="iconbtn ig" id="bulkIgnore" title="${esc(t("bulk.hide_title"))}">${ICONS.eyeOff}</button>
    <button class="iconbtn ig" id="bulkShow" title="${esc(t("bulk.show_title"))}">${ICONS.eye}</button>
    <span class="sep"></span>
    <button class="iconbtn corr" id="bulkCorrect" title="${esc(t("bulk.correct_title"))}">${ICONS.correct}</button>` : ""}
    ${merkPlaylists().length ? `<span class="sep"></span>
    ${merkPlaylists().map(n => {
        const flState = flStateOf(n.id);
        return `<button class="iconbtn flbtn${flState === "all" ? " on" : ""}" data-bulkfl="${n.id}" style="background:var(--${flColorTok(n)})"
        title="${esc(n.name)}">${iconGlyphHtml(playlistIconValue(n))}</button>`;
      }).join("")}` : ""}
    <span class="sep"></span>
    <button class="iconbtn plain" id="bulkExportZip" title="${esc(t("export_zip.title"))}">${ICONS.exportZip}</button>
    <span class="sep"></span>
    <button class="iconbtn del" id="bulkTrash" title="${esc(t("action.to_trash"))}">${ICONS.trash}</button>
    <button class="act small" id="bulkClear">${esc(t("bulk.clear_selection"))}</button>`;
  const bulkListAddBtn = document.getElementById("bulkListAdd");
  if (bulkListAddBtn) bulkListAddBtn.onclick = () => {
    const paths = [...state.selected].map(i => DATA[i]).filter(Boolean).map(x => x.p);
    if (paths.length) openPlaylistPicker(paths);
  };
  const bulkListRemoveBtn = document.getElementById("bulkListRemove");
  if (bulkListRemoveBtn) bulkListRemoveBtn.onclick = () => {
    const node = currentPlaylistNode();
    if (!node) return;
    const paths = [...state.selected].map(i => DATA[i]).filter(Boolean).map(x => x.p);
    removeFromPlaylist(node.id, paths);
  };
  const bulkTagsBtn = document.getElementById("bulkTags");
  if (bulkTagsBtn) bulkTagsBtn.onclick = () => {
    const rows = [...state.selected].map(i => DATA[i]).filter(Boolean);
    if (rows.length === 1) openTagsPopup(rows[0], false);
    else if (rows.length > 1) openTagsPopupBulk(rows);
  };
  document.getElementById("bulkQueueAdd").onclick = () => bulkApply("queue-add-all");
  const bulkIgnoreBtn = document.getElementById("bulkIgnore");
  if (bulkIgnoreBtn) bulkIgnoreBtn.onclick = () => bulkApply("ignore");
  const bulkShowBtn = document.getElementById("bulkShow");
  if (bulkShowBtn) bulkShowBtn.onclick = () => bulkApply("show");
  const bulkCorrectBtn = document.getElementById("bulkCorrect");
  if (bulkCorrectBtn) bulkCorrectBtn.onclick = () => bulkApply("correct");
  bar.querySelectorAll("[data-bulkfl]").forEach(b => b.onclick = () => bulkApply(`fl:${b.dataset.bulkfl}`));
  document.getElementById("bulkFix").onclick = () => bulkApply("fix");
  const bulkFixTagsBtn = document.getElementById("bulkFixTags");
  if (bulkFixTagsBtn) bulkFixTagsBtn.onclick = () => bulkApply("fixtags");
  const bulkConvertBtn = document.getElementById("bulkConvert");
  if (bulkConvertBtn) bulkConvertBtn.onclick = () => bulkApply("convert");
  document.getElementById("bulkRescan").onclick = () => bulkApply("rescan");
  const bulkMikBtn = document.getElementById("bulkMik");
  if (bulkMikBtn) bulkMikBtn.onclick = () => bulkApply("mik");
  const bulkRbAddBtn = document.getElementById("bulkRbAdd");
  if (bulkRbAddBtn) bulkRbAddBtn.onclick = () => bulkApply("rbadd");
  document.getElementById("bulkExportZip").onclick = () =>
    openExportZipPopup([...state.selected].map(i => DATA[i]).filter(Boolean));
  document.getElementById("bulkTrash").onclick = () => bulkApply("trash");
  document.getElementById("bulkClear").onclick = () => { state.selected.clear(); render(); };
}

function askTrashBulk(rows) {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    const totalSize = rows.reduce((s, r) => s + (r.sz || 0), 0);
    document.getElementById("confirmTitle").textContent =
      t("confirm.trash_bulk_title", {count: rows.length});
    document.getElementById("confirmFile").textContent =
      t("confirm.selected_tracks", {count: rows.length});
    document.getElementById("confirmPath").textContent = "";
    document.getElementById("confirmFacts").innerHTML =
      t("confirm.total_size", {size: (totalSize/1048576).toFixed(1).replace(".",",")});
    const inLibrary = rows.filter(r => r.im).length;
    document.getElementById("confirmMusicNote").textContent =
      inLibrary ? t("confirm.trash_music_note_bulk", {count: inLibrary}) : "";
    yesBtn.textContent = inLibrary ? t("action.to_trash_and_library") : t("action.to_trash");
    ov.style.display = "flex";

    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      yesBtn.textContent = t("action.to_trash");
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done(false);
    yesBtn.onclick = () => done(true);
    document.onkeydown = e => { if (e.key === "Escape") done(false); };
  });
}

function askFixBulk(rows) {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    document.getElementById("confirmTitle").textContent =
      t("confirm.fix_bulk_title", {count: rows.length});
    document.getElementById("confirmFile").textContent = t("confirm.selected_tracks", {count: rows.length});
    document.getElementById("confirmPath").textContent = "";
    document.getElementById("confirmFacts").innerHTML =
      `<span>${esc(t("confirm.fix_bulk_note"))}</span>`;
    document.getElementById("confirmMusicNote").textContent = "";
    yesBtn.textContent = t("confirm.encode_now");
    ov.style.display = "flex";
    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      yesBtn.textContent = t("action.to_trash");
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done(false);
    yesBtn.onclick = () => done(true);
    document.onkeydown = e => { if (e.key === "Escape") done(false); };
  });
}

function askFixTagIssuesBulk(rows) {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    document.getElementById("confirmTitle").textContent =
      t("confirm.fix_tag_issues_bulk_title", {count: rows.length});
    document.getElementById("confirmFile").textContent = t("confirm.selected_tracks", {count: rows.length});
    document.getElementById("confirmPath").textContent = "";
    document.getElementById("confirmFacts").innerHTML =
      `<span>${esc(t("confirm.fix_tag_issues_bulk_note"))}</span>`;
    document.getElementById("confirmMusicNote").textContent = "";
    // .confirmnote spricht vom Papierkorb -- ein Tag-Fix schreibt in-place,
    // es gibt kein Original, das dorthin wandert (anders als beim
    // Bitrate-Fix, der denselben Dialog sonst nutzt). Text fuer diesen
    // Dialog leeren und danach wieder herstellen (gleiches Prinzip wie
    // askRekordboxQuality()). ":not(.confirmnote-music)" ist noetig, weil
    // #confirmMusicNote im Markup VOR dieser Notiz steht und ebenfalls die
    // Klasse "confirmnote" traegt.
    const noteEl = ov.querySelector(".confirmnote:not(.confirmnote-music)");
    const noteOriginal = noteEl.textContent;
    noteEl.textContent = "";
    yesBtn.textContent = t("fixtag.auto_fix_button");
    ov.style.display = "flex";
    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      yesBtn.textContent = t("action.to_trash");
      noteEl.textContent = noteOriginal;
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done(false);
    yesBtn.onclick = () => done(true);
    document.onkeydown = e => { if (e.key === "Escape") done(false); };
  });
}

async function bulkApply(kind) {
  let rows = [...state.selected].map(i => DATA[i]).filter(Boolean);
  if (!rows.length) return;

  if (kind === "mik") {
    if (!MIK_NAME) { openSettings(); return; }
    await openInMik(rows, document.getElementById("bulkMik"));
    return;
  }

  if (kind === "rbadd") {
    if (!REKORDBOX_NAME || !REKORDBOX_PLAYLIST) { openSettings(); return; }
    await addToRekordboxPlaylist(rows, document.getElementById("bulkRbAdd"));
    return;
  }

  if (kind === "convert") {
    await openConvertPopup(rows, false);
    return;
  }

  if (kind === "queue-add-all") {
    // Reihenfolge der Auswahl (state.selected ist ein Set in Einfuege-
    // reihenfolge) statt Tabellenreihenfolge -- konsistent mit rows oben.
    rows.filter(r => !r.gone).forEach(r => addToQueue(r));
    state.selected.clear();
    render();
    return;
  }

  let skipped = 0;
  if (kind === "fix") {
    const total = rows.length;
    rows = rows.filter(isFixable);
    skipped = total - rows.length;
    if (!rows.length) {
      note(t("toast.no_fixable"), "soft");
      return;
    }
    if (!await askFixBulk(rows)) return;
  }
  if (kind === "fixtags") {
    const total = rows.length;
    rows = rows.filter(hasAutoFixableTagIssues);
    skipped = total - rows.length;
    if (!rows.length) {
      note(t("toast.no_tag_fixable"), "soft");
      return;
    }
    if (!await askFixTagIssuesBulk(rows)) return;
  }
  if (kind === "trash" && !await askTrashBulk(rows)) return;

  if (kind === "ignore" || kind === "show" || kind === "correct") {
    // Ohne lokale Datei (Music-App-Titel ohne Match, "Datei fehlt") ergeben
    // Ausblenden/Korrigieren keinen Sinn -- bei gemischter Auswahl wirkt die
    // Aktion nur auf die uebrigen Zeilen (siehe renderBulkBar()).
    rows = rows.filter(r => !r.gone);
    if (!rows.length) return;
  }

  if (kind === "rescan") {
    // Eine Anfrage je Block statt je Datei: der Server misst parallel und
    // backt den Report nur einmal pro Block neu. Der Block hält die Wartezeit
    // ueberschaubar und liefert unterwegs eine Rueckmeldung.
    const CHUNK = 25;
    const before = new Map(rows.map(r => [r.p, verdictOf(r)]));
    let done = 0, changed = 0, rekordboxRunning = false, failed = false;
    const pt = progressToast(t("toast.bulk_rescanning", {count: rows.length}));
    for (let i = 0; i < rows.length; i += CHUNK) {
      const chunk = rows.slice(i, i + CHUNK);
      try {
        const result = await reanalyse(chunk);
        done += chunk.length;
        if (result.rekordboxRunning) rekordboxRunning = true;
        chunk.forEach(r => { if (verdictOf(r) !== before.get(r.p)) changed++; });
        pt.update(100 * done / rows.length, t("toast.bulk_rescan_progress", {done, total: rows.length}));
      } catch (err) {
        pt.fail(t("toast.analysis_failed", {error: err.message}));
        failed = true;
        break;
      }
    }
    if (!failed) {
      pt.done(t("toast.bulk_rescan_done", {done, total: rows.length}) +
           (changed ? t("toast.bulk_rescan_changed_suffix", {changed}) : t("toast.bulk_rescan_unchanged_suffix")));
    }
    if (rekordboxRunning) note(t("toast.rekordbox_check_skipped_running"), "soft");
    state.selected.clear();
    updateCards(); render();
    return;
  }

  // Merken-Sammelaktion: Zielrichtung einmal vor der Warteschlange bestimmt,
  // nicht je Zeile -- bei "all" oder "mixed" (irgendeine Zeile hat die Liste
  // schon) schaltet ein Klick fuer ALLE aus, nur bei "none" (keine hat sie)
  // fuer alle an. Deckt sich mit dem Icon in renderBulkBar() (Haken/Fragezeichen/leer).
  const flTarget = kind.startsWith("fl:")
    ? !rows.some(r => PLAYLIST_SETS[kind.slice(3)] && PLAYLIST_SETS[kind.slice(3)].has(r.p))
    : null;

  let musicErrors = 0;
  let cloudNotes = 0;
  const queue = rows.slice();
  const worker = async () => {
    while (queue.length) {
      const r = queue.shift();
      try {
        if (kind === "ignore") {
          r.ig = 1; r.mc = 0;
          await persistIgnored(r.p, true);
        } else if (kind === "show") {
          // Setzt bewusst einheitlich fuer ALLE Ausgewaehlten, nicht je
          // Zeile umgeschaltet -- bei gemischtem Vorzustand (manche schon
          // eingeblendet) sonst uneindeutig, was ein Klick bewirkt.
          r.ig = 0;
          await persistIgnored(r.p, false);
        } else if (kind.startsWith("fl:")) {
          // Zielrichtung einheitlich fuer ALLE Ausgewaehlten (kein Toggle je
          // Zeile) -- sonst uneindeutig, was ein Klick bewirkt. flTarget ist
          // vor der Warteschlange bestimmt (siehe oben).
          const listId = kind.slice(3);
          if (!PLAYLIST_SETS[listId]) PLAYLIST_SETS[listId] = new Set();
          flTarget ? PLAYLIST_SETS[listId].add(r.p) : PLAYLIST_SETS[listId].delete(r.p);
          await playlistApi("/api/playlist-items",
            {op: flTarget ? "add" : "remove", id: listId, paths: [r.p]});
        } else if (kind === "correct") {
          r.mc = 1; r.ig = 0;
          await persistCorrected(r.p, true);
        } else if (kind === "trash") {
          const res = await fetch("/api/trash", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({path: r.p})});
          const data = await res.json();
          if (!data.ok) throw new Error(data.error || t("error.unknown"));
          r.removed = 1;
          dupGroupsDirty = true;
          if (data.music_error) musicErrors++;
          if (data.music_cloud_note) cloudNotes++;
        } else if (kind === "fix") {
          await rewriteOne(r, r.mk);
        } else if (kind === "fixtags") {
          await fixTagIssuesOne(r, false);
        }
      } catch (err) {
        const label = kind === "trash" ? t("bulk.action_trash")
          : kind === "fix" ? t("bulk.action_fix")
          : kind === "fixtags" ? t("bulk.action_fix_tag_issues")
          : t("action.save");
        note(t("toast.bulk_action_failed", {action: label, name: baseName(r.p), error: err.message}), true);
      }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  // PLAYLIST_ITEMS erst hier EINMAL aus dem Set nachziehen, nicht je Zeile im
  // Worker -- die Playlist-eigene Ansicht (playlistRows()) braucht sie, aber
  // ein Neuaufbau des Arrays bei jeder einzelnen Zeile waere bei grosser
  // Auswahl unnoetig teuer.
  if (kind.startsWith("fl:")) PLAYLIST_ITEMS[kind.slice(3)] = [...(PLAYLIST_SETS[kind.slice(3)] || [])];
  // Merken raeumt die Auswahl bewusst nicht ab -- anders als die uebrigen
  // Sammelaktionen ist das Hinzufuegen zu einer Liste kein Abschluss der
  // Bearbeitung: derselbe Satz Tracks soll sich direkt danach noch einer
  // weiteren Merkliste zuordnen lassen, ohne neu auszuwaehlen.
  if (!kind.startsWith("fl:")) state.selected.clear();
  updateCards(); render();
  if (kind === "fix" && skipped) {
    note(t("toast.bulk_fixed", {count: rows.length, skipped}));
  }
  if (kind === "fixtags") {
    note(t("toast.tag_issues_fixed_bulk", {count: rows.length, skipped}));
  }
  if (kind === "trash" && musicErrors) {
    note(t("toast.trash_music_failed", {count: musicErrors}), "soft");
  }
  if (kind === "trash" && cloudNotes) {
    note(t("toast.trash_music_cloud_note_bulk", {count: cloudNotes}), "soft");
  }
}

// ── Bitrate korrigieren ───────────────────────────────────────────────
function stopPlayerFor(path) {
  const entry = players.get(path);
  if (entry) { entry.audio.pause(); players.delete(path); }
  // Der globale Player streamt denselben Pfad -- ohne dieses Zuruecksetzen
  // wuerde ein Resume nach Rescan/Bitrate-Korrektur/Verschieben stumm die
  // alte, inzwischen ungueltige Quelle weiterspielen (queueAudio.src bleibt
  // sonst auf der alten URL stehen).
  if (queueState.current && queueState.current.p === path) {
    queueAudio.pause();
    queueState.current = null;
    renderPlayerBar();
  }
}

function applyRowUpdate(i, row) {
  Object.assign(DATA[i], row, {i});
  invalidateFieldValueCache();
  // Interpret/Titel/Pfad koennen sich hier geaendert haben -- der gemerkte
  // Suchbereich der Zeile ist damit hinfaellig (siehe scopeEntry()).
  dropScopeCache(DATA[i]);
}

// Misst die angegebenen Tracks neu und schreibt das Ergebnis in DATA zurueck.
// Der Server liefert die Zeilen in seiner eigenen Reihenfolge und mit eigenem
// Index — zugeordnet wird deshalb ueber den Pfad, nie ueber die Position.
async function reanalyse(rows) {
  const byPath = new Map(rows.map(r => [r.p, r]));
  rows.forEach(r => stopPlayerFor(r.p));
  const res = await fetch("/api/reanalyse", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({paths: rows.map(r => r.p)})});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  for (const row of data.rows) {
    const target = byPath.get(row.p);
    if (target) applyRowUpdate(target.i, row);
  }
  return {count: data.rows.length, rekordboxRunning: !!(data.rekordbox && data.rekordbox.running)};
}

async function rewriteOne(r, kbps) {
  stopPlayerFor(r.p);
  const res = await fetch("/api/rewrite", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: r.p, kbps: kbps})});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  applyRowUpdate(r.i, data.row);
  return data.row;
}

// Eigener Weg fuer die Einzelpruefungen -- /api/rewrite-drop statt
// /api/rewrite, da dieses hier immer das rohe Analyzer-Format zurueckgibt
// (fromServerRow()-kompatibel), waehrend die Haupttabelle das kompakte
// Report-Format erwartet (siehe server.py::_post_rewrite_drop()).
async function rewriteDrop(r, kbps) {
  stopPlayerFor(r.p);
  const res = await fetch("/api/rewrite-drop", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: r.p, kbps: kbps})});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  dropScopeCache(r);
  Object.assign(r, fromServerRow(data.row, null), {
    _id: r._id, file: r.file, blobUrl: r.blobUrl,
    nativePath: r.nativePath, mikPick: r.mikPick, libAdded: r.libAdded,
  });
  return data.row;
}

function openFixPopup(r, isDrop) {
  const ov = document.getElementById("fixOverlay");
  document.getElementById("fixFile").textContent =
    (r.a || r.t) ? (r.a + (r.t ? " — " + r.t : "")) : baseName(r.p);
  document.getElementById("fixPath").textContent = r.p;
  document.getElementById("fixFacts").innerHTML = `
    <span>deklariert <b>${r.kb} kbps</b></span>
    <span>gemessen <b>${r.mk} kbps</b></span>
    <span>Cutoff <b>${r.co.toFixed(2).replace(".",",")} kHz</b></span>`;
  const kbpsInput = document.getElementById("fixKbps");
  kbpsInput.value = r.mk || 128;
  const status = document.getElementById("fixStatus");
  status.textContent = ""; status.className = "";
  const yes = document.getElementById("fixYes"), no = document.getElementById("fixNo");
  yes.disabled = false; no.disabled = false;
  ov.style.display = "flex";

  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  no.onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  yes.onclick = async () => {
    const kbps = parseInt(kbpsInput.value, 10);
    if (!kbps || kbps < 32 || kbps > 320) {
      status.textContent = t("fix.value_range_hint");
      status.className = "warn";
      return;
    }
    yes.disabled = true; no.disabled = true;
    status.textContent = t("fix.encoding"); status.className = "";
    try {
      await (isDrop ? rewriteDrop(r, kbps) : rewriteOne(r, kbps));
      close();
      if (isDrop) renderDrops(); else { updateCards(); render(); }
      // Die Datei wurde direkt nach dem Kodieren neu gemessen — das Urteil
      // gleich mitzeigen, sonst muss man es sich in der Liste suchen.
      note(t("toast.rewritten", {name: baseName(r.p), kbps, verdict: labels[verdictOf(r)]}));
    } catch (err) {
      status.textContent = t("error.failed", {error: err.message});
      status.className = "warn";
      yes.disabled = false; no.disabled = false;
    }
  };
}

// Schreibt die sicher behebbaren Auffaelligkeiten einer Zeile und zieht das
// Ergebnis lokal nach -- fuer Bibliothekszeilen (DATA, ueber DATA[r.i]) wie
// fuer Einzelpruefungen (Drops, siehe rewriteDrop()) in einer Funktion, weil
// der Server fuer beide dasselbe kompakte Format liefert (report.py und
// fromServerRow() teilen sich dieselben Kurzschluessel a/t/al/...).
async function fixTagIssuesOne(r, isDrop) {
  const res = await fetch("/api/fix-tag-issues", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: r.p})});
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || t("error.unknown"));
  dropScopeCache(r);
  if (!isDrop && data.row) {
    applyRowUpdate(r.i, data.row);
  } else if (data.row) {
    // Drop-Zeile, die zufaellig doch eine DB-Zeile hat (siehe _post_tags()).
    Object.assign(r, data.row, {
      _id: r._id, file: r.file, blobUrl: r.blobUrl,
      nativePath: r.nativePath, mikPick: r.mikPick, libAdded: r.libAdded,
    });
  } else {
    // Echter Drop ohne DB-Zeile -- Felder aus der Serverantwort selbst
    // nachziehen (siehe _post_tags()' "Client pflegt seine lokale Kopie
    // selbst nach").
    const mapped = {};
    for (const [k, v] of Object.entries(data.fields || {})) {
      if (_TAG_FIELD_TO_COMPACT[k]) mapped[_TAG_FIELD_TO_COMPACT[k]] = v;
    }
    Object.assign(r, mapped, {ti: data.issues || []});
  }
  return data.fixed;
}

function openTagIssuesPopup(r, isDrop) {
  const ov = document.getElementById("tagIssuesOverlay");
  document.getElementById("tagIssuesFile").textContent =
    (r.a || r.t) ? (r.a + (r.t ? " — " + r.t : "")) : baseName(r.p);
  document.getElementById("tagIssuesPath").textContent = r.p;
  const list = document.getElementById("tagIssuesList");
  const status = document.getElementById("tagIssuesStatus");
  const closeBtn = document.getElementById("tagIssuesClose");
  const manualBtn = document.getElementById("tagIssuesManual");
  const fixAllBtn = document.getElementById("tagIssuesFixAll");
  document.getElementById("tagIssuesManualIcon").innerHTML = ICONS.pencil;
  document.getElementById("tagIssuesFixAllIcon").innerHTML = ICONS.sparkles;

  const renderList = () => {
    const issues = r.ti || [];
    list.innerHTML = issues.length ? issues.map(i => {
      const auto = AUTO_FIXABLE_TAG_ISSUE_CODES.has(i.code);
      const fieldLabel = i.field ? t("field." + i.field) : "";
      const suggestion = i.suggestion
        ? `<div class="dim">${esc(t("fixtag.mojibake_preview_label"))}: „${esc(i.suggestion)}“</div>` : "";
      return `<div>${auto ? "🔧" : "✋"} ${esc(t("tagissue." + i.code))}` +
        `${fieldLabel ? ` <span class="dim">(${esc(fieldLabel)})</span>` : ""}${suggestion}</div>`;
    }).join("") : `<div>${esc(t("fixtag.no_issues"))}</div>`;
    fixAllBtn.style.display = hasAutoFixableTagIssues(r) ? "" : "none";
    manualBtn.style.display = issues.length ? "" : "none";
  };
  renderList();
  status.textContent = ""; status.className = "";
  ov.style.display = "flex";

  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  closeBtn.onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  manualBtn.onclick = () => { close(); openTagsPopup(r, isDrop); };
  fixAllBtn.onclick = async () => {
    fixAllBtn.disabled = true; manualBtn.disabled = true;
    status.textContent = t("fixtag.encoding"); status.className = "";
    try {
      const fixed = await fixTagIssuesOne(r, isDrop);
      if (isDrop) renderDrops(); else { updateCards(); render(); }
      note(t("toast.tag_issues_fixed", {name: baseName(r.p), count: fixed.length}));
      status.textContent = "";
      renderList();
    } catch (err) {
      status.textContent = t("error.failed", {error: err.message});
      status.className = "warn";
    }
    fixAllBtn.disabled = false; manualBtn.disabled = false;
  };
}

// ── Konverter (MP3 320 kbps CBR / AIFF) ──────────────────────────────────
// Reine Entscheidungsfunktion ohne I/O, spiegelt app/convert.py::decide()
// 1:1 -- Vorfilter, damit offensichtlich unpassende Zeilen (Upscaling,
// bereits im Zielformat) gar nicht erst gesendet werden. Der Server prueft
// dieselbe Tabelle autoritativ noch einmal nach.
function convertDecision(r, target) {
  const suffix = (r.p.split(".").pop() || "").toLowerCase();
  if (target === "aiff") {
    if (r.fam !== "lossless") return "skip_upscale";
    if (suffix === "aiff" || suffix === "aif") return "skip_already_target";
    return "allow";
  }
  // target === "mp3_320"
  if (r.fam === "lossless") return "allow";
  if (r.fam === "lossy_mp3") return r.kb >= 320 ? "skip_already_target" : "skip_upscale";
  return r.kb >= 320 ? "allow" : "skip_upscale";
}

// Popup fuer die Zielformat-Wahl (einmal fuer den ganzen Batch, kein
// Zeilen-Toggle). isDrop steuert, ueber welchen Weg das Ergebnis
// zurueckgeschrieben wird (DB-Zeile vs. Einzelpruefungs-Zeile).
function openConvertPopup(rows, isDrop) {
  const ov = document.getElementById("convertOverlay");
  document.getElementById("convertFile").textContent =
    t("confirm.selected_tracks", {count: rows.length});
  const btnMp3 = document.getElementById("convertTargetMp3");
  const btnAiff = document.getElementById("convertTargetAiff");
  const trashToggle = document.getElementById("convertTrash");
  trashToggle.checked = true;
  const status = document.getElementById("convertStatus");
  status.textContent = ""; status.className = "";
  const yes = document.getElementById("convertYes"), no = document.getElementById("convertNo");
  yes.disabled = false; no.disabled = false;

  let target = "mp3_320";
  const applyTarget = () => {
    btnMp3.classList.toggle("on", target === "mp3_320");
    btnAiff.classList.toggle("on", target === "aiff");
  };
  applyTarget();
  btnMp3.onclick = () => { target = "mp3_320"; applyTarget(); };
  btnAiff.onclick = () => { target = "aiff"; applyTarget(); };
  ov.style.display = "flex";

  return new Promise(resolve => {
    const close = () => { ov.style.display = "none"; document.onkeydown = null; resolve(); };
    no.onclick = close;
    document.onkeydown = e => { if (e.key === "Escape") close(); };
    yes.onclick = async () => {
      yes.disabled = true; no.disabled = true;
      const trashOriginal = trashToggle.checked;
      ov.style.display = "none";
      document.onkeydown = null;
      await runConvertBatch(rows, target, isDrop, trashOriginal);
      resolve();
    };
  });
}

// ZIP-Export der Mehrfachauswahl (Bibliothek) mit optionalem Umbenennen NUR
// innerhalb des Archivs -- die Originaldateien werden dabei nie angefasst
// (kein /api/rename, siehe app/server.py::_get_download_zip()). Startet den
// Download per Navigation statt fetch+Blob, weil /api/download-zip schon
// als Attachment-Stream antwortet.
function openExportZipPopup(rows) {
  if (!rows.length) return;
  const ov = document.getElementById("exportZipOverlay");
  document.getElementById("exportZipFile").textContent =
    t("confirm.selected_tracks", {count: rows.length});
  const renameInput = document.getElementById("exportZipRename");
  const patternField = document.getElementById("exportZipPatternField");
  const patternInput = document.getElementById("exportZipPattern");
  renameInput.checked = true;
  patternInput.value = RENAME_PATTERN;
  const syncPatternField = () => { patternField.style.display = renameInput.checked ? "" : "none"; };
  syncPatternField();
  renameInput.onchange = syncPatternField;
  ov.style.display = "flex";

  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("exportZipNo").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  document.getElementById("exportZipYes").onclick = () => {
    const paths = rows.map(r => r.p);
    const params = new URLSearchParams();
    params.set("paths", JSON.stringify(paths));
    params.set("name", t("dragout.zip_name", {count: paths.length}));
    if (renameInput.checked) {
      params.set("rename", "1");
      params.set("pattern", patternInput.value || RENAME_PATTERN);
    }
    window.location.href = `/api/download-zip?${params.toString()}`;
    close();
  };
}

// Fuehrt die Konvertierung fuer alle vorgefilterten Zeilen aus (chunked --
// eine Datei kostet serverseitig einen echten Encode, ein einzelner
// riesiger Batch liesse die Oberflaeche minutenlang stumm bleiben) und
// zeigt am Ende eine gruppierte Zusammenfassung (konvertiert/uebersprungen/
// fehlgeschlagen/Rekordbox ausstehend).
async function runConvertBatch(rows, target, isDrop, trashOriginal) {
  const CHUNK = 10;
  const pre = rows.map(r => ({row: r, decision: convertDecision(r, target)}));
  const toSend = pre.filter(x => x.decision === "allow").map(x => x.row);
  const results = {converted: [], skip_upscale: [], skip_already_target: [],
                    error: [], rekordbox_pending: []};
  pre.filter(x => x.decision !== "allow").forEach(x => results[x.decision].push(x.row));

  if (toSend.length) {
    const pt = progressToast(t("convert.progress", {done: 0, total: toSend.length}));
    let aborted = false;
    for (let i = 0; i < toSend.length && !aborted; i += CHUNK) {
      const chunk = toSend.slice(i, i + CHUNK);
      let data;
      try {
        const res = await fetch("/api/convert", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({paths: chunk.map(r => r.p), target, trash_original: trashOriginal})});
        data = await res.json();
        if (!data.ok) throw new Error(data.error || t("error.unknown"));
      } catch (err) {
        chunk.forEach(r => results.error.push({row: r, error: err.message}));
        pt.fail(t("error.failed", {error: err.message}));
        aborted = true;
        break;
      }
      for (const item of data.results) {
        const row = chunk.find(r => r.p === item.path);
        if (!row) continue;
        if (item.status === "converted") {
          const kept = !!item.original_kept;
          if (isDrop) {
            // Original bleibt eine eigenstaendige, weiterhin gueltige
            // Einzelpruefungs-Zeile -- die konvertierte Datei kommt als
            // eigene, zusaetzliche Zeile dazu statt sie zu ersetzen.
            if (kept) addDropConvertResult(row, item.row);
            else applyDropConvert(row, item.new_path, item.row);
          } else if (item.row) {
            // Original bleibt unangetastet in DATA stehen -- die
            // konvertierte Datei bekommt eine eigene, neue Zeile
            // (adoptLibraryRows() vergibt dafuer einen frischen Index,
            // gleicher Weg wie nach einem Music.app-Import).
            if (kept) adoptLibraryRows([item.row]);
            else applyRowUpdate(row.i, item.row);
          }
          results.converted.push({row, newName: baseName(item.new_path), kept});
          if (item.rekordbox && item.rekordbox.running) results.rekordbox_pending.push(row);
        } else if (item.status === "skip") {
          results[item.reason === "upscale" ? "skip_upscale" : "skip_already_target"].push(row);
        } else {
          results.error.push({row, error: item.error});
        }
      }
      const done = Math.min(i + CHUNK, toSend.length);
      pt.update(100 * done / toSend.length, t("convert.progress", {done, total: toSend.length}));
    }
    if (!aborted) {
      pt.done(t("convert.done", {count: results.converted.length}),
        results.error.length ? true : (results.converted.length ? false : "soft"));
    }
  }

  if (isDrop) { renderDrops(); }
  else { state.selected.clear(); updateCards(); render(); }
  openConvertResultOverlay(results);
}

// Gruppierte Abschlussmeldung -- es gibt sonst nirgends im Projekt eine
// aggregierte Bericht-Ansicht am Ende einer Sammelaktion (nur einzelne
// Toasts waehrend des Laufs), aber Konverter-Batches koennen mehrere
// unterschiedliche Ablehnungsgruende gleichzeitig enthalten, die einzeln
// sichtbar bleiben sollen statt in einem einzigen Toast zu verschwinden.
function openConvertResultOverlay(results) {
  const ov = document.getElementById("convertResultOverlay");
  const host = document.getElementById("convertResultGroups");
  const groups = [
    ["converted", "convert.group_converted", results.converted],
    ["skip_upscale", "convert.group_skip_upscale", results.skip_upscale],
    ["skip_already_target", "convert.group_skip_already_target", results.skip_already_target],
    ["rekordbox_pending", "convert.group_rekordbox_pending", results.rekordbox_pending],
    ["error", "convert.group_error", results.error],
  ];
  host.innerHTML = groups.filter(([, , items]) => items.length).map(([key, labelKey, items]) => `
    <div class="convertresultgroup">
      <h4>${esc(t(labelKey, {count: items.length}))}</h4>
      <ul>${items.map(x => {
        if (key === "error") {
          return `<li>${esc(baseName(x.row.p))}<span class="err"> — ${esc(x.error)}</span></li>`;
        }
        if (key === "converted") {
          // Original behalten: beide Dateien existieren jetzt, deshalb
          // beide Namen zeigen statt nur den (unveraenderten) Original-
          // Dateinamen -- sonst waere aus der Liste nicht ersichtlich, dass
          // ueberhaupt etwas Neues entstanden ist.
          return x.kept
            ? `<li>${esc(baseName(x.row.p))} → ${esc(x.newName)}</li>`
            : `<li>${esc(baseName(x.row.p))}</li>`;
        }
        return `<li>${esc(baseName(x.p))}</li>`;
      }).join("")}</ul>
    </div>`).join("");
  ov.style.display = "flex";
  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("convertResultClose").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
}

// Zieht eine Einzelpruefungs-Zeile nach erfolgreicher Konvertierung auf den
// neuen Pfad UND aktualisiert alle abgeleiteten Anzeigefelder -- anders als
// applyDropRename() hat sich hier nicht nur der Pfad geaendert, sondern
// Codec/Bitrate/Format komplett (freshRow ist die rohe Analyse-Zeile aus
// /api/convert, gleiche Form wie bei einem Rescan).
function applyDropConvert(r, newPath, freshRow) {
  const oldKey = r.blobUrl || r.p;
  const entry = players.get(oldKey);
  if (entry) { entry.audio.pause(); players.delete(oldKey); }
  if (coverRev.has(r.p)) coverRev.delete(r.p);
  dropScopeCache(r);
  Object.assign(r, fromServerRow(freshRow, null), {
    _id: r._id, file: r.file, blobUrl: r.blobUrl,
    nativePath: r.nativePath, mikPick: r.mikPick, libAdded: r.libAdded,
  });
  r.p = newPath;
}

// Original behalten: die urspruengliche Einzelpruefungs-Zeile bleibt
// unangetastet bestehen (sie zeigt weiterhin auf ihre eigene, echte Datei),
// die konvertierte Datei kommt als eigene, neue Zeile dazu -- gleiches
// Prinzip wie adoptLibraryRows() fuer die Haupttabelle, nur fuer drops.
function addDropConvertResult(r, freshRow) {
  const fresh = fromServerRow(freshRow, null);
  fresh.nativePath = r.nativePath;
  drops.unshift(fresh);
}

// ── Autovorschläge fuer Tag-Felder (Praefix, aus bereits vorhandenen Werten) ─
// Verhindert Mehrfachschreibweisen (z.B. "Katy Perry" vs. "Katty Pery"): beim
// Tippen erscheinen vorhandene Werte, die mit dem Getippten beginnen, statt
// dass unbemerkt eine neue Variante entsteht. DATA ist im Browser ohnehin
// vollstaendig geladen -- kein eigener Server-Endpunkt noetig.
let fieldValueCache = {};
let groupCountsCache = {};
function invalidateFieldValueCache() { fieldValueCache = {}; groupCountsCache = {}; }

// Alle vorkommenden Werte eines Feldes MIT Trackzahl -- Grundlage der
// Genre-/Album-/Interpret-Bubbleleiste und der Zusammenfuehrungs-Vorschlaege
// (siehe VALUE_FIELDS weiter unten). Eigener Cache statt fieldValues(): der
// dort gecachte Wert ist eine reine Werteliste ohne Zaehlung.
function groupCounts(key) {
  if (groupCountsCache[key]) return groupCountsCache[key];
  const m = new Map();
  for (const r of DATA) {
    if (r.removed) continue;
    // Bewusst NICHT trimmen (anders als fieldValues()/Autocomplete): genau
    // ein Leerzeichen-Unterschied ist eine der vier Zusammenfuehrungs-
    // Vorschlagsarten (siehe findMergeSuggestions()) -- getrimmt wuerden
    // "Bootleg " und "Bootleg" hier schon zu einem Eintrag verschmelzen und
    // nie als Vorschlag auftauchen, obwohl die DB-Werte exakt so gespeichert
    // sind (siehe auch albumGroupCounts(), die aus demselben Grund nie
    // trimmt).
    const v = String(r[key] ?? "");
    if (!v) continue;
    m.set(v, (m.get(v) || 0) + 1);
  }
  const list = [...m.entries()].map(([value, count]) => ({value, count}))
    .sort((a, b) => cmpText(a.value, b.value));
  groupCountsCache[key] = list;
  return list;
}

function fieldValues(key) {
  if (fieldValueCache[key]) return fieldValueCache[key];
  const set = new Set();
  for (const r of DATA) {
    const v = String(r[key] ?? "").trim();
    if (v) set.add(v);
  }
  const list = [...set].sort(COLLATOR.compare);
  fieldValueCache[key] = list;
  return list;
}

// input/textarea -> kleines Dropdown mit passenden Praefix-Treffern.
// Pfeiltasten + Enter bedienen es, Klick uebernimmt, Escape/Blur schliesst.
//
// Idempotent UND umschaltbar: ein zweiter Aufruf auf demselben Element
// (el._acConfig existiert bereits) haengt keine weiteren Listener/Listen an,
// sondern aendert nur noch Feld-Schluessel/Optionen -- noetig fuer
// #nameInput, das als geteilter Singleton-Dialog fuer ganz unterschiedliche
// Zwecke wiederverwendet wird (Playlist-Umbenennen ohne Autocomplete, Genre/
// Album/Interpret-Umbenennen mit). 'key' null/undefined deaktiviert die
// Vorschlaege, ohne die Bindung zu entfernen.
//
// 'openOnFocus' (Vorgabe true, Tags-Popup-Verhalten unveraendert): Klick/
// Fokus zeigt sofort alle bekannten Werte. Der Genre/Album/Interpret-
// Umbenennen-Dialog setzt es auf false -- dort steht der ALTE Wert schon im
// Feld, ein voller Vorschlag beim blossen Oeffnen waere nur Rauschen; erst
// getipptes soll etwas anzeigen (siehe askValueRename-Aufruf).
function attachAutocomplete(el, key, {openOnFocus = true} = {}) {
  if (el._acConfig) { el._acConfig.key = key || null; el._acConfig.openOnFocus = openOnFocus; return; }
  const cfg = {key: key || null, openOnFocus};
  el._acConfig = cfg;
  const list = document.createElement("ul");
  list.className = "ac-list";
  list.style.display = "none";
  el.insertAdjacentElement("afterend", list);
  let items = [], active = -1;

  const close = () => { list.style.display = "none"; list.innerHTML = ""; items = []; active = -1; };

  const render = vals => {
    items = vals;
    if (!items.length) { close(); return; }
    active = -1;
    list.innerHTML = items.map((v, i) => `<li data-i="${i}">${esc(v)}</li>`).join("");
    list.style.display = "";
    list.querySelectorAll("li").forEach(li => {
      li.onmousedown = ev => { ev.preventDefault(); pick(items[+li.dataset.i]); };
    });
  };

  // Setzt den Wert UND feuert ein "input"-Event -- ohne das bleibt ein
  // extern per oninput/addEventListener("input", ...) mitgefuehrtes Modell
  // (z.B. rules[i].value im Smart-Playlist-Regel-Editor) auf dem alten Stand,
  // weil eine reine .value-Zuweisung per Skript keine Events ausloest.
  const pick = value => {
    el.value = value;
    el.dispatchEvent(new Event("input", {bubbles: true}));
    close();
  };

  // Getipptes filtert per Praefix wie bisher.
  const openFiltered = () => {
    if (!cfg.key) return;
    const q = el.value.trim().toLowerCase();
    if (!q) { render(fieldValues(cfg.key).slice(0, 200)); return; }
    render(fieldValues(cfg.key).filter(v => v.toLowerCase().startsWith(q) && v.toLowerCase() !== q).slice(0, 8));
  };
  // Klick/Fokus zeigt IMMER alle bereits vergebenen Werte, unabhaengig vom
  // aktuellen Feldinhalt -- sonst wuerde ein schon befuelltes Feld (z.B.
  // Genre "Rock") beim Klick nur noch auf "Rock" beginnende Praefix-Treffer
  // zeigen statt der vollen Liste, aus der man auswaehlen will.
  const openAll = () => { if (cfg.key) render(fieldValues(cfg.key).slice(0, 200)); };

  const highlight = () => {
    list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("on", i === active));
  };

  el.addEventListener("input", openFiltered);
  el.addEventListener("focus", () => { if (cfg.openOnFocus) openAll(); });
  el.addEventListener("click", () => { if (cfg.openOnFocus) openAll(); });
  el.addEventListener("blur", () => setTimeout(close, 100));
  el.addEventListener("keydown", ev => {
    if (list.style.display === "none") return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); active = Math.min(active + 1, items.length - 1); highlight(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); active = Math.max(active - 1, 0); highlight(); }
    else if (ev.key === "Enter" && active >= 0) { ev.preventDefault(); pick(items[active]); }
    else if (ev.key === "Escape") { close(); }
  });
}

const AUTOCOMPLETE_FIELDS = [
  {id: "tagsTitle", key: "t"}, {id: "tagsArtist", key: "a"}, {id: "tagsAlbum", key: "al"},
  {id: "tagsAlbumArtist", key: "aa"}, {id: "tagsComposer", key: "cp"}, {id: "tagsGenre", key: "ge"},
  {id: "tagsComment", key: "cm"},
];

// HTML-Attribute aus dataTransfer("text/html") sind entity-kodiert (z.B.
// "&amp;" statt "&") -- ueber ein Textarea-Element dekodieren ist der
// uebliche Trick dafuer, ohne das Ergebnis ins Dokument einzuhaengen (kein
// Skriptrisiko, .innerHTML wird hier nur geschrieben, nie fremder Code
// ausgefuehrt).
function decodeHtmlEntities(s) {
  const ta = document.createElement("textarea");
  ta.innerHTML = s;
  return ta.value;
}

// Google-Bildersuche verlinkt jede Miniatur ueber eine eigene Zwischenseite
// (<a href="/imgres?imgurl=<echte-bild-url>&...">) statt direkt auf die
// Bild-URL -- ein Drag liefert deshalb oft genau diesen Zwischenseiten-Link
// statt einer ladbaren Bild-URL. imgurl (bzw. der aeltere Parametername
// "url") ist die eigentliche Quelle, die entpackt werden muss.
function unwrapRedirectUrl(rawUrl) {
  try {
    const u = new URL(rawUrl, "https://www.google.com/");
    if (/(^|\.)google\.[a-z.]{2,}$/i.test(u.hostname) && (u.pathname === "/imgres" || u.pathname === "/url")) {
      const inner = u.searchParams.get("imgurl") || u.searchParams.get("url");
      if (inner) return inner;
    }
    return (u.protocol === "https:" || u.protocol === "http:") ? u.href : rawUrl;
  } catch {
    return rawUrl;
  }
}

// Bild-Drop aus dem Browser (z.B. Google-Bildersuche, eine Webseite) liefert
// anders als ein Finder-Drag keine Datei in dataTransfer.files, sondern nur
// die Bild-URL oder (bei noch nicht vollstaendig geladenen Vorschaubildern)
// direkt eine data:-URI -- je nach Quelle als text/uri-list, eingebettet in
// text/html (das <img>, das gezogen wurde, bzw. der umschliessende <a href>
// bei Google-Bildersuche) oder als reiner text/plain-Link. Rueckgabe ist
// {url} (nach Entpacken einer evtl. Google-Zwischenseite), {dataUri} oder
// null. Bei URLs nur http(s) zulassen: die URL landet unveraendert in
// fetch_cover() auf dem Server (urllib.request.urlopen), das auch
// file:/ftp:/... oeffnen wuerde -- bei Finder-Drag ist das kein Risiko
// (eigene Datei wird direkt hochgeladen), bei einem Drop aus einer
// beliebigen, potenziell nicht vertrauenswuerdigen Webseite schon.
function extractDroppedImage(ev) {
  const dt = ev.dataTransfer;
  if (!dt) return null;
  const isHttpUrl = s => /^https?:\/\/\S+$/i.test(s);
  const candidates = [];
  let dataUri = null;

  const html = dt.getData("text/html");
  if (html) {
    // Anchor-Href zuerst: bei der Google-Bildersuche steckt darin (nach dem
    // Entpacken) die Original-URL, das <img> selbst zeigt oft nur auf eine
    // kleine gstatic-Miniatur.
    const am = html.match(/<a[^>]+href=["']([^"']*(?:imgres|\/url)\?[^"']*)["']/i);
    if (am) candidates.push(decodeHtmlEntities(am[1]));
    const im = html.match(/<img[^>]+src=["']([^"']+)["']/i);
    if (im) {
      const src = decodeHtmlEntities(im[1]);
      if (/^data:image\//i.test(src)) dataUri = src;
      else candidates.push(src);
    }
  }
  const uriList = dt.getData("text/uri-list");
  if (uriList) {
    const line = uriList.split("\n").map(s => s.trim()).find(s => s && !s.startsWith("#"));
    if (line) candidates.push(line);
  }
  const plain = dt.getData("text/plain");
  if (plain && plain.trim()) candidates.push(plain.trim());

  for (const raw of candidates) {
    const url = unwrapRedirectUrl(raw);
    if (isHttpUrl(url)) return {url};
  }
  if (dataUri) return {dataUri};
  return null;
}

// Wandelt eine data:-URI (siehe extractDroppedImage) in ein File fuer
// stageCoverFile() um -- derselbe Weg wie eine per Dateiauswahl/Zwischenablage
// uebernommene Datei, kein Server-Roundtrip noetig.
function dataUriToFile(dataUri) {
  const m = /^data:([^;,]*)(;base64)?,(.*)$/s.exec(dataUri);
  if (!m) return null;
  const mime = m[1] || "image/jpeg";
  let bytes;
  if (m[2]) {
    const bin = atob(m[3]);
    bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  } else {
    bytes = new TextEncoder().encode(decodeURIComponent(m[3]));
  }
  return new File([bytes], "cover", {type: mime});
}

// Waehrend saveBtn.onclick laeuft (Einzeltrack wie Mehrfachauswahl) alles
// andere im Dialog sperren -- pointer-events:none deckt auch die nicht
// nativen Klick-Ziele ab (Lookup-Kandidatenzeilen, Cover-Drop-Zone), ohne
// jeden Handler einzeln mit einer Sperr-Pruefung zu versehen.
function setTagsPopupLocked(locked) {
  document.querySelector("#tagsOverlay .tagsbox").classList.toggle("locked", locked);
  if (locked && document.activeElement && document.activeElement.blur) document.activeElement.blur();
}

// ── Tags bearbeiten ───────────────────────────────────────────────────
// isDrop: Zeile stammt aus den Einzelprüfungen (drops-Array, "Dateien
// öffnen"), nicht aus der gescannten Haupttabelle --
// noch keine DB-Zeile, Server schreibt die Tags trotzdem (siehe
// _authorize_path in server.py). Navigation und Ergebnis-Uebernahme laufen
// dann gegen drops/renderDrops statt filtered()/applyRowUpdate.
function openTagsPopup(r, isDrop) {
  // Einmalig pro Element verdrahtet, ueber ein Dataset-Flag abgesichert --
  // openTagsPopup laeuft bei "Vorheriger"/"Nächster" fuer denselben Dialog
  // mehrfach, ohne den Guard haeuften sich Dropdowns und Listener an. Kann
  // erst hier (nicht top-level) passieren: die Tags-Dialog-Markup steht im
  // HTML NACH dem <script>-Block, bei Skriptausfuehrung existieren die
  // Elemente also noch nicht.
  for (const f of AUTOCOMPLETE_FIELDS) {
    const el = document.getElementById(f.id);
    if (el && !el.dataset.acBound) { el.dataset.acBound = "1"; attachAutocomplete(el, f.key); }
  }

  const ov = document.getElementById("tagsOverlay");
  let rows = isDrop ? drops.filter(x => x.nativePath) : filtered();
  // Sind mehrere Tracks markiert und gehoert die geoeffnete Zeile dazu,
  // blaettert Vor/Zurueck durch genau diese Markierung statt durch die
  // komplette (gefilterte) Liste -- unabhaengig von deren Reihenfolge/Filter.
  const markedSet = isDrop ? dropSelected : state.selected;
  const markedKey = isDrop ? "_id" : "i";
  if (markedSet.size > 1 && markedSet.has(r[markedKey])) {
    rows = rows.filter(x => markedSet.has(x[markedKey]));
  }
  const pos = isDrop ? rows.findIndex(x => x._id === r._id) : rows.findIndex(x => x.i === r.i);

  // Sichtbarkeit zuruecksetzen -- openTagsPopupBulk() blendet Prev/Next,
  // Online-Suche und den Cover-Platzhaltertext fuer den Mehrfach-Modus aus.
  document.getElementById("tagsPrev").style.display = "";
  document.getElementById("tagsNext").style.display = "";
  document.querySelector(".tagslookup").style.display = "";
  document.getElementById("tagsCoverEmpty").innerHTML = t("tags.cover_empty");
  for (const [id] of TAGS_BULK_FIELD_MAP) document.getElementById(id).placeholder = "";

  document.getElementById("tagsPath").textContent = r.p;
  document.getElementById("tagsTitle").value = r.t || "";
  document.getElementById("tagsArtist").value = r.a || "";
  document.getElementById("tagsAlbum").value = r.al || "";
  document.getElementById("tagsTrackNo").value = r.tn || "";
  document.getElementById("tagsTrackTotal").value = r.tt || "";
  document.getElementById("tagsAlbumArtist").value = r.aa || "";
  document.getElementById("tagsComposer").value = r.cp || "";
  document.getElementById("tagsGenre").value = r.ge || "";
  document.getElementById("tagsYear").value = r.yr || "";
  document.getElementById("tagsBpm").value = r.bp || "";
  document.getElementById("tagsComment").value = r.cm || "";

  // Schnappschuss der befuellten Werte -- beim Speichern wird nur verschickt,
  // was sich gegenueber diesem Stand geaendert hat (siehe TAGS_BULK_FIELD_MAP
  // weiter unten). Verhindert, dass ein unangetastetes Feld (z.B. Kommentar)
  // einen Wert ueberschreibt, den ein externes Tool (z.B. Mixed In Key)
  // waehrend der offenen Dialogzeit in die Datei geschrieben hat.
  const initial = {};
  for (const [id] of TAGS_BULK_FIELD_MAP) initial[id] = document.getElementById(id).value;

  const img = document.getElementById("tagsCoverImg");
  const empty = document.getElementById("tagsCoverEmpty");
  const zone = document.getElementById("tagsCoverZone");
  if (r.cv) {
    img.src = `/api/cover?path=${encodeURIComponent(r.p)}&_=${Date.now()}`;
    img.style.display = ""; empty.style.display = "none";
  } else {
    img.removeAttribute("src");
    img.style.display = "none"; empty.style.display = "";
  }

  // Cover-Aenderungen (Datei/Zwischenablage/Drag&Drop/Loeschen) werden erst
  // lokal vorgemerkt (Vorschau via Object-URL) und beim Klick auf "Speichern"
  // zusammen mit den Textfeldern committet -- nicht mehr sofort hochgeladen.
  let pendingCover = null;         // File/Blob | {url} | "delete" | null
  let pendingObjectUrl = null;
  let locked = false;              // waehrend saveBtn.onclick laeuft, siehe setTagsPopupLocked()
  const discardPendingCover = () => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = null;
  };
  const stageCoverFile = file => {
    if (!file || !file.type || !file.type.startsWith("image/")) {
      status.textContent = t("tags.not_image_file"); status.className = "warn";
      return;
    }
    if (pendingObjectUrl) URL.revokeObjectURL(pendingObjectUrl);
    pendingObjectUrl = URL.createObjectURL(file);
    pendingCover = file;
    img.src = pendingObjectUrl;
    img.style.display = ""; empty.style.display = "none";
    status.textContent = t("tags.cover_selected");
    status.className = "";
  };
  const stageCoverDelete = () => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = "delete";
    img.removeAttribute("src");
    img.style.display = "none"; empty.style.display = "";
    status.textContent = t("tags.cover_will_delete"); status.className = "";
  };
  // Cover eines Online-Vorschlags: Bytes liegen extern, deshalb kein
  // Object-URL wie bei stageCoverFile -- die Vorschau zeigt direkt die
  // Quell-URL, geladen wird serverseitig erst beim Speichern (ueber
  // /api/lookup-cover, das denselben Weg wie der "Nur Cover"-Knopf nutzt).
  const stageCoverUrl = (url, label = t("tags.cover_online_suggestion")) => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = {url};
    img.src = url;
    img.style.display = ""; empty.style.display = "none";
    status.textContent = t("tags.cover_from_source", {label});
    status.className = "";
  };

  const status = document.getElementById("tagsStatus");
  status.textContent = ""; status.className = "";
  document.getElementById("tagsLookupStatus").textContent = "";
  document.getElementById("tagsLookupResults").innerHTML = "";
  document.getElementById("tagsLookupManual").style.display = "none";
  document.getElementById("tagsLookupQuery").value = "";

  const prevBtn = document.getElementById("tagsPrev");
  const nextBtn = document.getElementById("tagsNext");
  prevBtn.disabled = pos <= 0;
  nextBtn.disabled = pos < 0 || pos >= rows.length - 1;
  prevBtn.onclick = () => { if (pos > 0) { discardPendingCover(); openTagsPopup(rows[pos - 1], isDrop); } };
  nextBtn.onclick = () => { if (pos >= 0 && pos < rows.length - 1) { discardPendingCover(); openTagsPopup(rows[pos + 1], isDrop); } };

  ov.style.display = "flex";
  const close = () => { if (locked) return; discardPendingCover(); ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("tagsCancel").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };

  const coverInput = document.getElementById("tagsCoverInput");
  document.getElementById("tagsCoverBtn").onclick = () => coverInput.click();
  coverInput.onchange = () => {
    const file = coverInput.files[0];
    coverInput.value = "";
    if (file) stageCoverFile(file);
  };

  document.getElementById("tagsCoverPasteBtn").onclick = async () => {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const type = item.types.find(t => t.startsWith("image/"));
        if (type) { stageCoverFile(await item.getType(type)); return; }
      }
      status.textContent = t("tags.no_image_clipboard"); status.className = "warn";
    } catch (err) {
      status.textContent = t("tags.clipboard_unavailable", {error: err.message});
      status.className = "warn";
    }
  };

  document.getElementById("tagsCoverDeleteBtn").onclick = () => {
    if (!r.cv && !pendingCover) {
      status.textContent = t("tags.no_cover"); status.className = "";
      return;
    }
    stageCoverDelete();
  };

  zone.ondragenter = zone.ondragover = ev => { ev.preventDefault(); zone.classList.add("dragover"); };
  zone.ondragleave = () => zone.classList.remove("dragover");
  zone.ondrop = ev => {
    ev.preventDefault();
    zone.classList.remove("dragover");
    const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) { stageCoverFile(file); return; }
    const dropped = extractDroppedImage(ev);
    if (dropped && dropped.dataUri) {
      const f = dataUriToFile(dropped.dataUri);
      if (f) { stageCoverFile(f); return; }
    }
    if (dropped && dropped.url) { stageCoverUrl(dropped.url, t("tags.cover_image_url")); return; }
    status.textContent = t("tags.no_image_recognized"); status.className = "warn";
  };

  const lstatus = document.getElementById("tagsLookupStatus");
  const results = document.getElementById("tagsLookupResults");
  const manualBox = document.getElementById("tagsLookupManual");
  const manualInput = document.getElementById("tagsLookupQuery");

  // Gemeinsam fuer den Erst-Versuch (Interpret+Titel) und den manuellen
  // Fallback (freier Suchbegriff, siehe unten) -- liefert die Erstsuche
  // nichts, blendet sie das Suchfeld ein statt nur "nichts gefunden" zu
  // zeigen, ohne dass der Nutzer selbst noch etwas versuchen koennte.
  const runLookup = async body => {
    lstatus.textContent = t("lookup.searching");
    results.innerHTML = "";
    try {
      const res = await fetch("/api/lookup", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      if (!data.results.length) {
        lstatus.textContent = t("lookup.no_results");
        if (!manualInput.value.trim()) {
          manualInput.value = body.query || [body.artist, body.title].filter(Boolean).join(" ");
        }
        manualBox.style.display = "flex";
      } else {
        lstatus.textContent = t("lookup.results_count", {count: data.results.length});
      }
      results.innerHTML = data.results.map((c, i) => `
        <div class="lookupcand" data-cand="${i}">
          ${c.cover ? `<img src="${esc(c.cover)}" alt="">` : `<div class="lookupcand-nocover"></div>`}
          <div class="lookupcand-info">
            <div><b>${esc(c.artist || "?")}</b> — ${esc(c.title || "?")}</div>
            <div class="path">${esc(c.album || "")}${c.genre ? " · " + esc(c.genre) : ""}${c.year ? " · " + c.year : ""}${c.bpm ? " · " + c.bpm + " BPM" : ""}</div>
            <div class="path">${esc(c.source)}</div>
          </div>
          ${c.cover ? `<button class="act small lookupcand-onlycover" data-onlycover="${i}"
                               title="${esc(t("lookup.cover_only_title"))}">
                         ${esc(t("lookup.cover_only_button"))}</button>` : ""}
        </div>`).join("");
      results.querySelectorAll("[data-cand]").forEach(el => el.onclick = () => {
        const c = data.results[+el.dataset.cand];
        if (c.artist) document.getElementById("tagsArtist").value = c.artist;
        if (c.title) document.getElementById("tagsTitle").value = c.title;
        if (c.album) document.getElementById("tagsAlbum").value = c.album;
        if (c.genre) document.getElementById("tagsGenre").value = c.genre;
        if (c.year) document.getElementById("tagsYear").value = c.year;
        if (c.bpm) document.getElementById("tagsBpm").value = c.bpm;
        if (c.cover) stageCoverUrl(c.cover);
      });
      results.querySelectorAll("[data-onlycover]").forEach(btn => btn.onclick = ev => {
        ev.stopPropagation();
        const c = data.results[+btn.dataset.onlycover];
        stageCoverUrl(c.cover);
      });
    } catch (err) {
      lstatus.textContent = t("toast.search_failed", {error: err.message});
    }
  };

  document.getElementById("tagsLookupBtn").onclick = () => {
    const artist = document.getElementById("tagsArtist").value.trim();
    const title = document.getElementById("tagsTitle").value.trim();
    if (!artist && !title) {
      lstatus.textContent = t("lookup.need_artist_or_title");
      return;
    }
    runLookup({artist, title});
  };
  document.getElementById("tagsLookupManualBtn").onclick = () => {
    const query = manualInput.value.trim();
    if (!query) { lstatus.textContent = t("lookup.need_query"); return; }
    runLookup({query});
  };
  // Blendet dasselbe Suchfeld ein, das runLookup() sonst erst nach einer
  // erfolglosen Online-Suche zeigt -- fuer den Fall, dass der Nutzer gleich
  // selbst formulieren will, statt erst eine Suche scheitern zu lassen.
  document.getElementById("tagsLookupManualToggle").onclick = () => {
    if (!manualInput.value.trim()) {
      const artist = document.getElementById("tagsArtist").value.trim();
      const title = document.getElementById("tagsTitle").value.trim();
      manualInput.value = [artist, title].filter(Boolean).join(" ");
    }
    manualBox.style.display = "flex";
    manualInput.focus();
    manualInput.select();
  };
  manualInput.onkeydown = ev => {
    if (ev.key === "Enter") { ev.preventDefault(); document.getElementById("tagsLookupManualBtn").click(); }
  };

  const saveBtn = document.getElementById("tagsSave");
  saveBtn.onclick = async () => {
    // Nur tatsaechlich geaenderte Felder verschicken (Vergleich gegen den
    // initial-Schnappschuss von oben) -- unangetastete Felder duerfen die
    // Server-/Datei-Seite nicht ueberschreiben, siehe Kommentar bei initial.
    const fields = {};
    for (const [id, key, payloadKey] of TAGS_BULK_FIELD_MAP) {
      const raw = document.getElementById(id).value;
      if (raw === initial[id]) continue;
      if (payloadKey === "year") fields.year = raw ? parseInt(raw, 10) : 0;
      else if (payloadKey === "bpm") fields.bpm = raw ? parseFloat(raw) : 0;
      else if (payloadKey === "track_no" || payloadKey === "track_total") fields[payloadKey] = raw ? parseInt(raw, 10) : 0;
      else fields[payloadKey] = raw;
    }
    if (!Object.keys(fields).length && !pendingCover) {
      status.textContent = t("tags.no_changes");
      status.className = "";
      return;
    }
    locked = true;
    setTagsPopupLocked(true);
    saveBtn.disabled = true;
    saveBtn.classList.add("busy");
    status.textContent = t("tags.saving"); status.className = "busy";
    try {
      if (Object.keys(fields).length) {
        const res = await fetch("/api/tags", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: r.p, ...fields})});
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || t("error.unknown"));
        if (isDrop) {
          Object.assign(r, fieldsToDropRow(fields));
          renderDrops();
        } else {
          applyRowUpdate(r.i, data.row);
          render();
        }
      }

      // Vorgemerkte Cover-Aenderung (Datei/Zwischenablage/Drag&Drop/Loeschen)
      // erst jetzt tatsaechlich schreiben, zusammen mit den Textfeldern.
      if (pendingCover === "delete") {
        const res2 = await fetch(`/api/cover-delete?path=${encodeURIComponent(r.p)}`, {method: "POST"});
        const data2 = await res2.json();
        if (!data2.ok) throw new Error(data2.error || t("error.unknown"));
        bumpCoverRev(r.p);
        if (isDrop) { r.cv = 0; renderDrops(); } else { applyRowUpdate(r.i, data2.row); render(); }
        img.removeAttribute("src");
        img.style.display = "none"; empty.style.display = "";
        discardPendingCover();
        if (data2.music_cover_synced === false) {
          note(t("toast.cover_music_failed_one", {name: baseName(r.p), error: data2.music_cover_error}), "soft");
        }
      } else if (pendingCover && pendingCover.url) {
        // Aus einem Online-Vorschlag ausgewaehlt (stageCoverUrl) -- die Bytes
        // liegen extern, deshalb derselbe Weg wie der "Nur Cover"-Knopf
        // (serverseitiger Download) statt eines rohen Datei-Uploads.
        const res2 = await fetch("/api/lookup-cover", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: r.p, url: pendingCover.url})});
        const data2 = await res2.json();
        if (!data2.ok) throw new Error(data2.error || t("error.unknown"));
        bumpCoverRev(r.p);
        if (isDrop) { r.cv = 1; renderDrops(); } else { applyRowUpdate(r.i, data2.row); render(); }
        discardPendingCover();
        img.src = `/api/cover?path=${encodeURIComponent(r.p)}&_=${Date.now()}`;
        img.style.display = ""; empty.style.display = "none";
        if (data2.music_cover_synced === false) {
          note(t("toast.cover_music_failed_one", {name: baseName(r.p), error: data2.music_cover_error}), "soft");
        }
      } else if (pendingCover) {
        const file = pendingCover;
        const res2 = await fetch(`/api/cover?path=${encodeURIComponent(r.p)}`, {
          method: "POST",
          headers: {"Content-Type": file.type || "image/jpeg"},
          body: file,
        });
        const data2 = await res2.json();
        if (!data2.ok) throw new Error(data2.error || t("error.unknown"));
        bumpCoverRev(r.p);
        if (isDrop) { r.cv = 1; renderDrops(); } else { applyRowUpdate(r.i, data2.row); render(); }
        discardPendingCover();
        img.src = `/api/cover?path=${encodeURIComponent(r.p)}&_=${Date.now()}`;
        img.style.display = ""; empty.style.display = "none";
        if (data2.music_cover_synced === false) {
          note(t("toast.cover_music_failed_one", {name: baseName(r.p), error: data2.music_cover_error}), "soft");
        }
      }

      // Overlay bleibt bewusst offen -- nur ein kurzes Feedback, damit direkt
      // weiter am selben Track editiert werden kann (z.B. Cover danach).
      status.textContent = t("tags.saved"); status.className = "ok";
      saveBtn.classList.add("done");
      note(t("toast.tags_saved", {name: baseName(r.p)}));
    } catch (err) {
      status.textContent = t("error.failed", {error: err.message});
      status.className = "warn";
      saveBtn.classList.add("failed");
    }
    locked = false;
    setTagsPopupLocked(false);
    saveBtn.disabled = false;
    saveBtn.classList.remove("busy");
    setTimeout(() => saveBtn.classList.remove("done", "failed"), 1200);
  };
}

// ── Tags bearbeiten (Mehrfachauswahl) ────────────────────────────────────
// Nutzt dasselbe Overlay-Markup wie openTagsPopup() wieder. Felder, die bei
// allen ausgewaehlten Dateien gleich sind, werden vorbefuellt; abweichende
// Felder bleiben leer mit Platzhaltertext. Beim Speichern wird je Feld
// geprueft, ob sich der Wert gegenueber der Vorbefuellung geaendert hat --
// nur geaenderte Felder werden ueberhaupt verschickt, unangetastete Felder
// lassen den individuellen Wert jeder einzelnen Datei unberuehrt (die
// Server-/DB-Schicht ueberschreibt ohnehin nur Felder, die im Request
// vorkommen, siehe server.py:_post_tags/db.py:update_tags/tags.py:write_tags).
const TAGS_BULK_FIELD_MAP = [
  ["tagsTitle", "t", "title"], ["tagsArtist", "a", "artist"],
  ["tagsAlbum", "al", "album"],
  ["tagsTrackNo", "tn", "track_no"], ["tagsTrackTotal", "tt", "track_total"],
  ["tagsAlbumArtist", "aa", "album_artist"],
  ["tagsComposer", "cp", "composer"], ["tagsGenre", "ge", "genre"],
  ["tagsYear", "yr", "year"], ["tagsBpm", "bp", "bpm"],
  ["tagsComment", "cm", "comment"],
];

// Uebersetzt das an /api/tags geschickte Feld-Objekt zurueck in die kompakten
// Zeilenschluessel -- fuer Einzelprüfungen, deren Zeile der Server mangels
// DB-Eintrag nicht zurueckliefern kann.
function fieldsToDropRow(fields) {
  const out = {};
  for (const [, key, payloadKey] of TAGS_BULK_FIELD_MAP) {
    if (payloadKey in fields) out[key] = fields[payloadKey];
  }
  return out;
}

// isDrop: wie bei openTagsPopup() stammen die Zeilen dann aus den
// Einzelprüfungen (drops) statt aus DATA -- sie haben keinen DATA-Index,
// das Ergebnis wandert deshalb direkt in den Eintrag und nach renderDrops().
function openTagsPopupBulk(rows, isDrop) {
  const ov = document.getElementById("tagsOverlay");
  document.getElementById("tagsPath").textContent = t("tags.selected_count_files", {count: rows.length});

  const initial = {};
  for (const [id, key] of TAGS_BULK_FIELD_MAP) {
    const values = new Set(rows.map(r => String(r[key] ?? "")));
    const common = values.size === 1 ? [...values][0] : "";
    const el = document.getElementById(id);
    el.value = common;
    el.placeholder = values.size > 1 ? t("tags.multiple_values_placeholder") : "";
    initial[id] = common;
  }

  // Prev/Next und Online-Suche zielen auf genau einen Track -- im
  // Mehrfach-Modus ausblenden statt anzupassen.
  document.getElementById("tagsPrev").style.display = "none";
  document.getElementById("tagsNext").style.display = "none";
  document.querySelector(".tagslookup").style.display = "none";

  const img = document.getElementById("tagsCoverImg");
  const empty = document.getElementById("tagsCoverEmpty");
  const zone = document.getElementById("tagsCoverZone");
  img.removeAttribute("src");
  img.style.display = "none"; empty.style.display = "";
  empty.textContent = t("tags.cover_varies");

  let pendingCover = null;        // File/Blob | "delete" | null
  let pendingObjectUrl = null;
  let locked = false;             // waehrend saveBtn.onclick laeuft, siehe setTagsPopupLocked()
  const discardPendingCover = () => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = null;
  };
  const stageCoverFile = file => {
    if (!file || !file.type || !file.type.startsWith("image/")) {
      status.textContent = t("tags.not_image_file"); status.className = "warn";
      return;
    }
    if (pendingObjectUrl) URL.revokeObjectURL(pendingObjectUrl);
    pendingObjectUrl = URL.createObjectURL(file);
    pendingCover = file;
    img.src = pendingObjectUrl;
    img.style.display = ""; empty.style.display = "none";
    status.textContent = t("tags.cover_selected_bulk");
    status.className = "";
  };
  const stageCoverDelete = () => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = "delete";
    img.removeAttribute("src");
    img.style.display = "none"; empty.style.display = "";
    empty.textContent = t("tags.cover_will_delete_bulk");
    status.textContent = ""; status.className = "";
  };
  // Wie bei openTagsPopup(): Bild-URL statt Datei, z.B. per Drag&Drop aus
  // dem Browser -- geladen wird serverseitig erst beim Speichern je Track.
  const stageCoverUrl = (url, label = t("tags.cover_image_url")) => {
    if (pendingObjectUrl) { URL.revokeObjectURL(pendingObjectUrl); pendingObjectUrl = null; }
    pendingCover = {url};
    img.src = url;
    img.style.display = ""; empty.style.display = "none";
    status.textContent = t("tags.cover_from_source_bulk", {label});
    status.className = "";
  };

  const status = document.getElementById("tagsStatus");
  status.textContent = ""; status.className = "";

  ov.style.display = "flex";
  const close = () => { if (locked) return; discardPendingCover(); ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("tagsCancel").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };

  const coverInput = document.getElementById("tagsCoverInput");
  document.getElementById("tagsCoverBtn").onclick = () => coverInput.click();
  coverInput.onchange = () => {
    const file = coverInput.files[0];
    coverInput.value = "";
    if (file) stageCoverFile(file);
  };
  document.getElementById("tagsCoverPasteBtn").onclick = async () => {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const type = item.types.find(t => t.startsWith("image/"));
        if (type) { stageCoverFile(await item.getType(type)); return; }
      }
      status.textContent = t("tags.no_image_clipboard"); status.className = "warn";
    } catch (err) {
      status.textContent = t("tags.clipboard_unavailable", {error: err.message});
      status.className = "warn";
    }
  };
  document.getElementById("tagsCoverDeleteBtn").onclick = () => stageCoverDelete();
  zone.ondragenter = zone.ondragover = ev => { ev.preventDefault(); zone.classList.add("dragover"); };
  zone.ondragleave = () => zone.classList.remove("dragover");
  zone.ondrop = ev => {
    ev.preventDefault();
    zone.classList.remove("dragover");
    const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) { stageCoverFile(file); return; }
    const dropped = extractDroppedImage(ev);
    if (dropped && dropped.dataUri) {
      const f = dataUriToFile(dropped.dataUri);
      if (f) { stageCoverFile(f); return; }
    }
    if (dropped && dropped.url) { stageCoverUrl(dropped.url); return; }
    status.textContent = t("tags.no_image_recognized"); status.className = "warn";
  };

  const saveBtn = document.getElementById("tagsSave");
  saveBtn.onclick = async () => {
    const fields = {};
    for (const [id, key, payloadKey] of TAGS_BULK_FIELD_MAP) {
      const raw = document.getElementById(id).value;
      if (raw === initial[id]) continue;   // unangetastet -> ueberspringen, Original bleibt je Datei erhalten
      if (payloadKey === "year") fields.year = raw ? parseInt(raw, 10) : 0;
      else if (payloadKey === "bpm") fields.bpm = raw ? parseFloat(raw) : 0;
      else if (payloadKey === "track_no" || payloadKey === "track_total") fields[payloadKey] = raw ? parseInt(raw, 10) : 0;
      else fields[payloadKey] = raw;
    }
    if (!Object.keys(fields).length && !pendingCover) {
      status.textContent = t("tags.no_changes"); status.className = "";
      return;
    }

    locked = true;
    setTagsPopupLocked(true);
    saveBtn.disabled = true;
    saveBtn.classList.add("busy");
    status.textContent = t("tags.saving"); status.className = "";

    const queue = rows.slice();
    let okCount = 0, failCount = 0, coverSyncMisses = 0;
    const worker = async () => {
      while (queue.length) {
        const row = queue.shift();
        try {
          if (Object.keys(fields).length) {
            const res = await fetch("/api/tags", {method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({path: row.p, ...fields})});
            const data = await res.json();
            if (!data.ok) throw new Error(data.error || t("error.unknown"));
            // Einzelprüfungen haben keine DB-Zeile -- data.row ist dort
            // null, uebernommen wird deshalb das, was verschickt wurde.
            if (isDrop) Object.assign(row, fieldsToDropRow(fields));
            else applyRowUpdate(row.i, data.row);
          }
          if (pendingCover === "delete") {
            const res2 = await fetch(`/api/cover-delete?path=${encodeURIComponent(row.p)}`, {method: "POST"});
            const data2 = await res2.json();
            if (data2.ok) {
              bumpCoverRev(row.p);
              if (isDrop) row.cv = 0; else applyRowUpdate(row.i, data2.row);
              if (data2.music_cover_synced === false) coverSyncMisses++;
            }
          } else if (pendingCover && pendingCover.url) {
            // Bild-URL (Drag&Drop aus dem Browser) -- wie beim Einzeltrack
            // laedt der Server die Bytes selbst (/api/lookup-cover), kein
            // roher Upload.
            const res2 = await fetch("/api/lookup-cover", {method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({path: row.p, url: pendingCover.url})});
            const data2 = await res2.json();
            if (data2.ok) {
              bumpCoverRev(row.p);
              if (isDrop) row.cv = 1; else applyRowUpdate(row.i, data2.row);
              if (data2.music_cover_synced === false) coverSyncMisses++;
            }
          } else if (pendingCover) {
            const res2 = await fetch(`/api/cover?path=${encodeURIComponent(row.p)}`, {
              method: "POST",
              headers: {"Content-Type": pendingCover.type || "image/jpeg"},
              body: pendingCover,
            });
            const data2 = await res2.json();
            if (data2.ok) {
              bumpCoverRev(row.p);
              if (isDrop) row.cv = 1; else applyRowUpdate(row.i, data2.row);
              if (data2.music_cover_synced === false) coverSyncMisses++;
            }
          }
          okCount++;
        } catch (err) {
          failCount++;
          note(t("toast.bulk_action_failed", {action: t("action.save"), name: baseName(row.p), error: err.message}), true);
        }
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    if (isDrop) renderDrops(); else render();
    if (coverSyncMisses) {
      note(t("toast.cover_music_failed", {count: coverSyncMisses}), "soft");
    }

    status.textContent = t("tags.bulk_saved", {ok: okCount, total: rows.length}) +
      (failCount ? t("tags.bulk_failed_suffix", {count: failCount}) : ".");
    status.className = failCount ? "warn" : "ok";
    saveBtn.classList.add(failCount ? "failed" : "done");
    // Nur die Vormerkung loeschen, nicht die Object-URL freigeben: die
    // Vorschau haengt noch daran und soll das uebernommene Bild
    // weiterzeigen. Aufgeraeumt wird beim Schliessen (close()).
    pendingCover = null;
    locked = false;
    setTagsPopupLocked(false);
    saveBtn.disabled = false;
    saveBtn.classList.remove("busy");
    setTimeout(() => saveBtn.classList.remove("done", "failed"), 1200);
  };
}

// ── Cover-Großansicht ─────────────────────────────────────────────────
function openCoverPreview(r) {
  if (!r.cv) return;
  const ov = document.getElementById("coverOverlay");
  const img = document.getElementById("coverBig");
  img.src = `/api/cover?path=${encodeURIComponent(r.p)}&_=${Date.now()}`;
  ov.style.display = "flex";
  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("coverClose").onclick = close;
  ov.onclick = e => { if (e.target === ov) close(); };
  document.onkeydown = e => { if (e.key === "Escape") close(); };
}

function exportRows() {
  const rows = filtered();
  // Bei aktiver Mehrfachauswahl nur die ausgewaehlten Zeilen exportieren --
  // state.selected ist per render() immer eine Teilmenge der gefilterten
  // Ansicht, die Reihenfolge bleibt dadurch erhalten.
  return state.selected.size ? rows.filter(r => state.selected.has(r.i)) : rows;
}

document.getElementById("csvsel").onclick = () => {
  const rows = exportRows();
  const head = t("csv.header") + "\n";
  const body = rows.map(r => [verdictOf(r), String(r.co).replace(".",","), r.kb, r.mk,
      String(r.cf).replace(".",","), r.lu ? String(r.lu).replace(".",",") : "", r.a, r.t, r.p]
      .map(x => '"' + String(x).replace(/"/g,'""') + '"').join(";")).join("\n");
  const blob = new Blob(["﻿" + head + body], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "mp3-quality-auswahl.csv";
  a.click();
};

function sanitizeM3uFilename(name) {
  const base = String(name || "").trim()
    .replace(/\.m3u8?$/i, "")
    .replace(/[\/\\:*?"<>|]/g, "_")
    .trim();
  return (base || "mp3-quality-auswahl") + ".m3u8";
}

// Erzeugt die M3U8-Datei und stoesst den Download an. Getrennt vom Dialog,
// weil der Seitenbaum dieselbe Ausgabe fuer eine einzelne Playlist braucht --
// dort in deren manueller Reihenfolge statt in der gefilterten Tabellensicht.
function downloadM3u(rows, filename) {
  const lines = ["#EXTM3U"];
  rows.forEach(r => {
    const label = [r.a, r.t].filter(Boolean).join(" — ")
      || r.p.split("/").pop().replace(/\.[^/.]+$/, "");
    lines.push(`#EXTINF:${r.du || 0},${label}`, r.p);
  });
  const blob = new Blob([lines.join("\n") + "\n"], {type: "audio/x-mpegurl;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = sanitizeM3uFilename(filename);
  a.click();
}

document.getElementById("m3usel").onclick = () => {
  const ov = document.getElementById("m3uOverlay");
  const input = document.getElementById("m3uFilename");
  const note = document.getElementById("m3uNote");
  note.textContent = state.selected.size
    ? t("m3u.note_selected", {count: state.selected.size.toLocaleString("de-DE")})
    : t("m3u.note_filtered");
  input.value = "mp3-quality-auswahl";
  ov.style.display = "flex";
  input.focus(); input.select();

  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("m3uNo").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  const doExport = () => { downloadM3u(exportRows(), input.value); close(); };
  document.getElementById("m3uYes").onclick = doExport;
  input.onkeydown = e => { if (e.key === "Enter") doExport(); };
};

let EDITOR_NAME = "";
let DAW_NAME = "";
let MIK_NAME = "";
let MUSIC_NAME = "";
let REKORDBOX_NAME = "";
let REKORDBOX_PLAYLIST = "";
let REKORDBOX_QUALITY_CHECK = true;
let REKORDBOX_MIN_CUTOFF_KHZ = 19;
let RENAME_PATTERN = "";

// Nur im Speicher, kein localStorage: haelt Rekordbox-Korrekturen fest, die
// an einem laufenden Rekordbox gescheitert sind (siehe relinkMissing()), bis
// zum naechsten Klick auf "Auto-Relocate" -- der schickt sie automatisch
// erneut mit, auch wenn dann keine Datei mehr als fehlend gilt.
let pendingRekordboxMoves = [];

// ── Standard-Suchfilter (Einstellungen) ──────────────────────────────────
// DEFAULT_SEARCH_FILTERS sind die rohen Zeilen aus den Einstellungen (ein
// /Parameter-Ausdruck je Zeile). ACTIVE_DEFAULT_FILTERS ist das Ergebnis von
// refreshDefaultFilters() -- dieselbe geparste Filterliste wie bei einer
// getippten Suche (siehe parseSearchQuery()/applyFilters()), abzueglich per
// ×-Klick fuer die laufende Sitzung deaktivierter Eintraege. Wird EINMAL pro
// Einstellungen-Laden/-Speichern neu gebaut, nicht pro Zeile in matchesSearch()
// -- sonst wuerde jede Zeile parseSearchQuery() erneut aufrufen und dessen
// Ein-Slot-Cache (_cacheQ/_cacheParsed) staendig mit dem getippten Suchtext
// ueberschreiben.
let DEFAULT_SEARCH_FILTERS = [];
let ACTIVE_DEFAULT_FILTERS = [];
function refreshDefaultFilters() {
  // parseSearchQuery() erwartet bereits kleingeschriebenen Text (siehe
  // Kommentar dort) -- anders als state.q (schon per oninput lowercased)
  // kommt der Einstellungen-Text roh vom Textarea, deshalb hier explizit.
  const {free, filters} = parseSearchQuery(DEFAULT_SEARCH_FILTERS.join(" ").toLowerCase());
  // Elemente ohne "/Parameter" (reiner Freitext) verlangen wie im Suchfeld
  // selbst einfach, dass Kuenstler+Titel+Pfad sie enthalten -- je Element ein
  // eigener Chip, damit sich einzelne per × abschalten lassen.
  for (const el of free) filters.push({kind: "contains", values: el.values, raw: valuesLabel(el.values)});
  filters.forEach((f, i) => { f.defaultId = i; });
  ACTIVE_DEFAULT_FILTERS = filters.filter(f => !state.disabledDefaults.has(f.defaultId));
}

async function openInApp(url, path, btn, verb) {
  btn.classList.add("busy");
  try {
    const res = await fetch(url, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    btn.classList.add("done");
    note(t("toast.opened_in", {verb, editor: data.editor}));
  } catch (err) {
    btn.classList.add("failed");
    note(t("toast.open_failed", {error: err.message}), true);
  }
  btn.classList.remove("busy");
  setTimeout(() => btn.classList.remove("done", "failed"), 1200);
}

// Kein Fehlender-Hinweis mehr noetig: der Knopf wird nur gerendert, wenn
// EDITOR_NAME/DAW_NAME gesetzt ist (siehe render()/renderDrops()).
const openInEditor = (r, btn) => openInApp("/api/open-in", r.p, btn, "In");
const openInMusic = (r, btn) => openInApp("/api/open-music", r.p, btn, "In");
const openInDaw = (r, btn) => openInApp("/api/open-daw", r.p, btn, "In");

// Mehrere Pfade in einem Aufruf -- MIK importiert sie alle in ein Fenster
// und analysiert sie danach selbst im Hintergrund (Key/BPM/Energy, Tags).
// Gibt zurueck, ob das Oeffnen geklappt hat -- Aufrufer mit einem
// "→ Mixed In Key"-Hinweis (r.mikPick, siehe Einzelprüfungen) werten das aus.
async function openInMik(rows, btn) {
  btn.classList.add("busy");
  let ok = false;
  try {
    const res = await fetch("/api/open-mik", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths: rows.map(r => r.p)})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    btn.classList.add("done");
    ok = true;
    note(t("toast.mik_opened", {count: data.count, plural: data.count === 1 ? "" : "s"}));
  } catch (err) {
    btn.classList.add("failed");
    note(t("toast.open_failed", {error: err.message}), true);
  }
  btn.classList.remove("busy");
  setTimeout(() => btn.classList.remove("done", "failed"), 1200);
  return ok;
}

// Music.app legt beim Import eine Kopie in seinem eigenen Medienordner ab,
// wenn dort "Dateien beim Hinzufuegen kopieren" eingeschaltet ist -- der Pfad
// aendert sich also mitten im Betrieb. Der Server schreibt die DB-Zeile auf
// den neuen Pfad um und meldet die Paare zurueck; hier ziehen die Zeilen im
// Browser nach, damit Anhoeren/Tags/Finder danach nicht auf die weggezogene
// Datei zeigen.
function applyMovedPaths(moved) {
  if (!moved || !moved.length) return 0;
  for (const m of moved) {
    stopPlayerFor(m.old);
    for (const d of drops) if (d.p === m.old) d.p = m.new;
    if (m.row) {
      const idx = DATA.findIndex(r => r.p === m.old);
      if (idx >= 0) applyRowUpdate(idx, m.row);
    }
  }
  return moved.length;
}

// Mehrere Pfade in einem Aufruf -- importiert sie richtig in die Music.app-
// Bibliothek (AppleScript 'add', nicht nur abspielen wie openInMusic oben).
async function addToLibrary(rows, btn) {
  btn.classList.add("busy");
  let result = null;
  try {
    const res = await fetch("/api/add-to-library", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths: rows.map(r => r.p)})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    result = data;
    btn.classList.add("done");
    const movedCount = applyMovedPaths(data.moved);
    if (movedCount) render();
    note(t("toast.import_done", {count: data.count, plural: data.count === 1 ? "" : "s"})
       + (movedCount
          ? t("toast.import_moved_suffix", {count: movedCount, plural: movedCount === 1 ? "" : "e"})
          : ""));
  } catch (err) {
    btn.classList.add("failed");
    note(t("toast.import_failed", {error: err.message}), true);
  }
  btn.classList.remove("busy");
  setTimeout(() => btn.classList.remove("done", "failed"), 1200);
  return result;
}

// Uebernimmt die vom Server nach dem Import gelieferten Bibliothekszeilen in
// DATA. Der Import ist der Punkt, an dem ein Track dauerhaft im Medienordner
// liegt und eine DB-Zeile bekommt (siehe server.py:_post_add_to_library) --
// ab hier gehoert er in die Bibliotheksliste statt in die Einzelprüfungen.
// Liefert die Indizes der uebernommenen Zeilen.
function adoptLibraryRows(compactRows) {
  const idx = [];
  for (const row of compactRows || []) {
    if (!row) continue;                      // Pfad ohne DB-Zeile (Analyse fehlgeschlagen)
    const found = DATA.findIndex(r => r.p === row.p);
    if (found >= 0) {
      applyRowUpdate(found, row);            // war schon in der Bibliothek
      DATA[found].removed = 0;
      idx.push(found);
    } else {
      const at = DATA.length;
      DATA.push({...row, i: at});
      idx.push(at);
    }
  }
  if (idx.length) invalidateFieldValueCache();
  return idx;
}

// Zeigt die gerade importierten Tracks in der Bibliotheksliste: nach
// "Hinzugefügt" absteigend sortiert (die neuen stehen damit oben) und
// ausgewaehlt, damit direkt eine Sammelaktion darauf laufen kann.
// Ansicht und Suche werden dafuer bewusst zurueckgesetzt -- ein frisch
// importierter Track soll unabhaengig von der gerade gewaehlten Liste
// sichtbar sein.
function revealImported(indices) {
  if (!indices.length) return;
  state.view = "all";
  state.q = "";
  state.groupAlbums = false;
  state.sort = "da"; state.dir = -1;
  state.shown = Math.max(PAGE, indices.length);
  // Der Listenwechsel laeuft hier an selectView() vorbei -- den Schluessel
  // des Spalten-Topfes deshalb selbst nachziehen, sonst haelt
  // syncColumnsForView() beim naechsten Wechsel eine falsche Kopfzeile fuer
  // aktuell. Die Reihenfolge zaehlt: state.hiddenCols loest bereits auf die
  // neue Liste auf.
  lastColumnStoreKey = columnStoreKey();
  state.hiddenCols = state.hiddenCols.filter(k => k !== "da");
  const qEl = document.getElementById("q");
  if (qEl) qEl.value = "";
  syncControls();
  saveFilters();
  state.selected = new Set(indices);
  updateCards();
  renderHead();
  render();
  renderTree();
  // Nicht querySelector(".tablewrap"): die Einzelprüfungen haben eine eigene
  // und stehen im DOM davor. Ueber #tb den richtigen Container treffen.
  document.getElementById("tb").closest(".tablewrap")
    .scrollIntoView({behavior: "smooth", block: "start"});
}

// Klick auf Cover/Titel/Interpret im Player: den laufenden Track in der
// Tabelle zeigen. Sucht ueber "/pfad" statt Freitext -- ein echter Dateipfad
// beginnt mit "/" und kann Leerzeichen enthalten, beides wuerde die normale
// Freitextsuche verwirren (siehe scopeEntry()/tokenizeSearch()). Der
// Feld-Filter vergleicht dagegen exakt (Teilstring ohne Fuzzy) gegen r.p und
// liefert deshalb garantiert genau eine Trefferzeile. Ist der Track durch
// einen aktiven Standard-Suchfilter grundsaetzlich ausgeblendet, bleibt er
// bewusst unsichtbar (Sonderfall, wie bei revealImported()).
function revealCurrentQueueTrack() {
  const r = currentQueueTrack();
  if (!r) return;
  const query = `/pfad "${r.p}"`.toLowerCase();
  state.view = "all";
  state.q = query;
  state.shown = PAGE;
  const qEl = document.getElementById("q");
  if (qEl) qEl.value = query;
  syncControls();
  renderHead();
  render();
  document.getElementById("tb").closest(".tablewrap")
    .scrollIntoView({behavior: "smooth", block: "start"});
  setCursorRow(r.i);
}

// Cutoff-Schwelle fuer den Rekordbox-Import -- verwendet den beim Scan
// bereits gemessenen Wert (r.co, kHz), keine neue Analyse. Ohne verwertbaren
// Messwert (r.co <= 0, z.B. zu kurze Datei oder Alt-Zeile) gilt der Track
// als Warnung statt automatisch als bestanden.
function rekordboxQualityProblem(r) {
  return !(r.co > 0) || r.co < REKORDBOX_MIN_CUTOFF_KHZ;
}

function askRekordboxQuality(rows, problems) {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    const extraBtn = document.getElementById("confirmExtra");
    // .confirmnote spricht vom Papierkorb -- hier wird nichts geloescht,
    // sondern zur Rekordbox-Playlist hinzugefuegt. Text fuer diesen Dialog
    // leeren und danach wieder herstellen (gleiches Prinzip wie
    // askOrphanCleanup()). ":not(.confirmnote-music)" ist noetig, weil
    // #confirmMusicNote im Markup VOR dieser Notiz steht und ebenfalls die
    // Klasse "confirmnote" traegt -- ein einfaches ".confirmnote" traefe
    // sonst den falschen (ersten) Absatz.
    const noteEl = ov.querySelector(".confirmnote:not(.confirmnote-music)");
    const noteOriginal = noteEl.textContent;
    noteEl.textContent = "";
    const bulk = rows.length > 1;
    const cutoffText = r => r.co > 0
      ? `${r.co.toFixed(2).replace(".", ",")} kHz` : t("field.cutoff_unknown");
    const trackLabel = r => (r.a || r.t) ? (r.a + (r.t ? " — " + r.t : "")) : baseName(r.p);
    document.getElementById("confirmTitle").textContent = bulk
      ? t("confirm.rekordbox_quality_title_bulk", {count: problems.length})
      : t("confirm.rekordbox_quality_title_single");
    document.getElementById("confirmFile").textContent = bulk
      ? t("confirm.selected_tracks", {count: rows.length})
      : trackLabel(problems[0]);
    document.getElementById("confirmPath").textContent = bulk ? "" : problems[0].p;
    document.getElementById("confirmFacts").innerHTML = bulk
      ? `<div class="rbqlist">${problems.map(r =>
          `<div>${esc(trackLabel(r))} <b>${cutoffText(r)}</b></div>`).join("")}</div>`
      : `<span>${esc(t("col.cutoff"))} <b>${cutoffText(problems[0])}</b></span>`;
    document.getElementById("confirmMusicNote").textContent =
      t("confirm.rekordbox_quality_note", {threshold: REKORDBOX_MIN_CUTOFF_KHZ});
    extraBtn.style.display = bulk ? "" : "none";
    extraBtn.textContent = t("action.rekordbox_skip_problems");
    yesBtn.textContent = t("action.rekordbox_add_anyway");
    ov.style.display = "flex";

    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      extraBtn.style.display = "none";
      document.getElementById("confirmMusicNote").textContent = "";
      yesBtn.textContent = t("action.to_trash");
      noteEl.textContent = noteOriginal;
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done("cancel");
    extraBtn.onclick = () => done("skip");
    yesBtn.onclick = () => done("ignore");
    document.onkeydown = e => { if (e.key === "Escape") done("cancel"); };
  });
}

// Fuegt Tracks der in den Einstellungen hinterlegten, bereits bestehenden
// Rekordbox-Playlist hinzu -- schreibt direkt in Rekordbox' Datenbank,
// Rekordbox muss dafuer beendet sein (Fehlermeldung kommt vom Server).
async function addToRekordboxPlaylist(rows, btn) {
  let targetRows = rows;
  if (REKORDBOX_QUALITY_CHECK) {
    const problems = rows.filter(rekordboxQualityProblem);
    if (problems.length) {
      const answer = await askRekordboxQuality(rows, problems);
      if (answer === "cancel") return;
      if (answer === "skip") {
        targetRows = rows.filter(r => !problems.includes(r));
        if (!targetRows.length) { note(t("toast.rekordbox_all_skipped"), "soft"); return; }
      }
      // "ignore" -> targetRows bleibt rows (inkl. Problemtracks)
    }
  }
  btn.classList.add("busy");
  try {
    // Vorab pruefen, ob Rekordbox gerade laeuft -- erst bei bestaetigt
    // geschlossenem Rekordbox wird ueberhaupt geschrieben. Der Server prueft
    // das vor jedem Schreibzugriff ohnehin nochmal selbst (die eigentliche
    // Absicherung); dieser Aufruf gibt nur die schnellere, klarere
    // Rueckmeldung direkt beim Klick, statt erst nach einem Schreibversuch.
    const status = await fetch("/api/rekordbox-status").then(r => r.json());
    if (status.ok && status.running) {
      const err = new Error(t("toast.rekordbox_open_error"));
      err.sticky = true;
      throw err;
    }
    const res = await fetch("/api/rekordbox-add-playlist", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({paths: targetRows.map(r => r.p)})});
    const data = await res.json();
    if (!data.ok) {
      const err = new Error(data.error || t("error.unknown"));
      if (data.sticky) err.sticky = true;
      throw err;
    }
    targetRows.forEach(r => { if (data.added.includes(r.p)) r.rb = 1; });
    btn.classList.add("done");
    const extra = [];
    if (data.skipped.length) extra.push(t("toast.rekordbox_skipped_suffix", {count: data.skipped.length}));
    const errCount = Object.keys(data.errors).length;
    if (errCount) extra.push(t("toast.rekordbox_errors_suffix", {count: errCount}));
    note(t("toast.rekordbox_added", {count: data.added.length, playlist: esc(REKORDBOX_PLAYLIST)})
       + (extra.length ? `, ${extra.join(", ")}` : "") + ".");
    // Scroll-Position sichern: tb.innerHTML in render() baut die ganze
    // Tabelle neu auf und reisst dabei den Fokus vom geklickten Knopf los —
    // ohne diese Sicherung springt die Seite dabei ganz nach oben.
    const scrollY = window.scrollY;
    render();
    window.scrollTo(0, scrollY);
  } catch (err) {
    btn.classList.add("failed");
    note(t("toast.rekordbox_playlist_failed", {error: err.message}), true, !!err.sticky);
  }
  btn.classList.remove("busy");
  setTimeout(() => btn.classList.remove("done", "failed"), 1200);
}

// ── Löschen mit Rückfrage ───────────────────────────────────────────────
// Es wird nie hart gelöscht, sondern in den Papierkorb verschoben — ein
// Fehlgriff bleibt damit umkehrbar.
function askTrash(r) {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    document.getElementById("confirmTitle").textContent = t("confirm.trash_title");
    document.getElementById("confirmFile").textContent =
      (r.a || r.t) ? (r.a + (r.t ? " — " + r.t : "")) : baseName(r.p);
    document.getElementById("confirmPath").textContent = r.p;
    document.getElementById("confirmFacts").innerHTML = `
      <span class="badge ${verdictOf(r)}">${VERDICT_ICONS[verdictOf(r)]}${labels[verdictOf(r)]}</span>
      <span>Cutoff <b>${r.co.toFixed(2).replace(".",",")} kHz</b></span>
      <span>${r.fam === "lossless" ? esc(t("field.lossless")) : t("confirm.declared_kbps", {kbps: r.kb})}</span>
      <span>${esc(t("field.confidence"))} <b>${r.cf.toFixed(2).replace(".",",")}</b></span>
      <span><b>${(r.sz/1048576).toFixed(1).replace(".",",")} MB</b></span>`;
    document.getElementById("confirmMusicNote").textContent =
      r.im ? t("confirm.trash_music_note") : "";
    yesBtn.textContent = r.im ? t("action.to_trash_and_library") : t("action.to_trash");
    ov.style.display = "flex";

    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      yesBtn.textContent = t("action.to_trash");
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done(false);
    yesBtn.onclick = () => done(true);
    document.onkeydown = e => { if (e.key === "Escape") done(false); };
  });
}

async function trashTrack(r) {
  if (!await askTrash(r)) return;
  try {
    const res = await fetch("/api/trash", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: r.p})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    r.removed = 1;
    dupGroupsDirty = true;
    updateCards(); render();
    note(t("toast.trashed", {name: baseName(r.p)}));
    if (data.music_error) {
      note(t("toast.trash_music_failed_one", {name: baseName(r.p), error: data.music_error}), "soft");
    }
    if (data.music_cloud_note) {
      note(t("toast.trash_music_cloud_note_one", {name: baseName(r.p)}), "soft");
    }
  } catch (err) {
    note(t("toast.trash_failed", {error: err.message}), true);
  }
}

// Music.app raeumt seinen Medienordner nach Tags auf: wer die Tags hier
// aendert und den Track danach in Music.app abspielt, findet ihn hinterher
// unter Interpret/Album wieder -- neuer Ordner UND neuer Dateiname. Die Zeile
// steht dann auf "Datei fehlt", obwohl die Datei noch da ist. Der Server sucht
// sie anhand von Groesse, Dateiname, Tags und Dauer wieder und schreibt die
// Zeile um; hier ziehen nur die Zeilen im Browser nach.
// paths === null: alle fehlenden Dateien auf einmal.
async function relinkMissing(paths, btn) {
  if (!apiMode) { note(t("toast.needs_server"), "soft"); return; }
  // .label statt textContent -- der Knopf hat ein Icon davor (btnicon), ein
  // textContent-Rueckschreiben wuerde das beim Wiederherstellen loeschen.
  // Am reinen Icon-Knopf je Zeile (kein .label) gibt es nichts umzuschalten.
  const labelEl = btn ? btn.querySelector(".label") : null;
  const label = labelEl ? labelEl.textContent : "";
  btn && btn.classList.add("busy");
  if (labelEl) { btn.disabled = true; labelEl.textContent = t("relink.searching"); }
  try {
    const res = await fetch("/api/relink", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...(paths ? {paths} : {}),
                             ...(pendingRekordboxMoves.length
                                 ? {rekordbox_retry: pendingRekordboxMoves} : {})})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    if (applyMovedPaths(data.relinked)) render();
    // Umgeschriebene Pfade machen jeden Schnappschuss im Verlauf ungueltig
    // (siehe Kommentar bei undoStacks).
    if (data.relinked.length) clearUndoStacks();

    const teile = [];
    if (data.relinked.length) teile.push(t("toast.relink_relinked", {count: data.relinked.length}));
    if (data.ambiguous.length) teile.push(t("toast.relink_ambiguous", {count: data.ambiguous.length}));
    if (data.unmatched.length) teile.push(t("toast.relink_unmatched", {count: data.unmatched.length}));
    // Rekordbox-Korrektur (siehe server._post_relink()): laeuft Rekordbox
    // gerade, bleibt die Liste im Speicher fuer den naechsten Klick auf
    // diesen Knopf -- ein eigener, klebender Hinweis statt in der
    // Zusammenfassung unterzugehen, weil er eine Handlung verlangt.
    if (data.rekordbox) {
      if (data.rekordbox.running) {
        pendingRekordboxMoves = data.rekordbox.moves;
        note(t("toast.rekordbox_relocate_open"), true, true);
      } else {
        pendingRekordboxMoves = [];
        if (data.rekordbox.updated.length) {
          teile.push(t("toast.rekordbox_relocate_updated", {count: data.rekordbox.updated.length}));
        }
        const rbErr = Object.keys(data.rekordbox.errors || {}).length;
        if (rbErr) teile.push(t("toast.rekordbox_relocate_errors", {count: rbErr}));
      }
    }
    note(teile.length
      ? t("toast.relink_summary", {parts: teile.join(", "), checked: data.checked.toLocaleString("de-DE")})
      : t("toast.relink_none"),
      !data.relinked.length ? "soft" : false);
    if (data.truncated) {
      note(t("toast.relink_truncated"), "soft");
    }
    btn && btn.classList.add(data.relinked.length ? "done" : "failed");
  } catch (err) {
    btn && btn.classList.add("failed");
    note(t("toast.search_failed", {error: err.message}), true);
  }
  if (btn) {
    btn.classList.remove("busy");
    if (labelEl) { btn.disabled = false; labelEl.textContent = label; }
    setTimeout(() => btn.classList.remove("done", "failed"), 1500);
  }
}

async function pruneMissing() {
  if (!apiMode) { note(t("toast.needs_server"), "soft"); return; }
  const btn = document.getElementById("pruneBtn");
  const labelEl = btn.querySelector(".label");
  btn.disabled = true;
  if (labelEl) labelEl.textContent = t("prune.removing");
  try {
    const res = await fetch("/api/prune", {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    // Die entfernten Zeilen sofort lokal streichen, statt zum Neuladen
    // aufzufordern: live()/counted()/filtered() blenden alles mit removed=1
    // aus, die Leiste verschwindet damit im selben Zug. Der Server hat
    // report.html schon neu gebacken, ein spaeteres Neuladen zeigt dasselbe.
    // Der Server liefert die Pfade; fehlen sie (aelterer Server), bleibt als
    // Rueckfallebene das, was der Client selbst als fehlend kennt.
    const removed = new Set(data.paths || DATA.filter(r => r.gone).map(r => r.p));
    for (const r of DATA) if (removed.has(r.p)) r.removed = 1;
    if (removed.size) dupGroupsDirty = true;
    clearUndoStacks();
    invalidateFieldValueCache();
    updateCards(); render();
    note(t("toast.prune_removed", {
      count: data.removed.toLocaleString("de-DE"),
      entry_word: data.removed === 1 ? t("toast.entry_singular") : t("toast.entry_plural"),
    }));
  } catch (err) {
    btn.disabled = false;
    if (labelEl) labelEl.textContent = t("gone.button_prune");
    note(t("toast.prune_failed", {error: err.message}), true);
  }
}


// ── Einstellungen ───────────────────────────────────────────────────────
let schema = null;

async function openSettings() {
  const ov = document.getElementById("settingsOverlay");
  if (!apiMode) { note(t("settings.needs_server"), "soft"); return; }
  try {
    schema = await (await fetch("/api/settings")).json();
  } catch (err) {
    document.getElementById("settingsBody").innerHTML =
      `<div class="path">${esc(t("settings.load_failed", {error: err.message}))}</div>`;
    ov.style.display = "flex";
    return;
  }
  document.getElementById("settingsFile").textContent =
    t("settings.changes_in", {file: schema.local_file});
  renderSettings();
  ov.style.display = "flex";
  const close = () => { ov.style.display = "none"; document.onkeydown = null; };
  document.getElementById("btnCloseSettings").onclick = close;
  document.onkeydown = e => { if (e.key === "Escape") close(); };
  document.getElementById("btnCheckUpdate").onclick = e => { e.preventDefault(); checkForUpdate(); };
}

// Manuell angestossene Update-Pruefung (Link im Einstellungen-Fuss) gegen
// /api/check-update -- eigenes Overlay statt Toast, weil das Ergebnis
// (insbesondere bei verfuegbarem Update) einen Link braucht.
async function checkForUpdate() {
  const link = document.getElementById("btnCheckUpdate");
  const prevText = link.textContent;
  link.textContent = t("settings.check_update_running");
  let data;
  try {
    data = await (await fetch("/api/check-update")).json();
  } catch (err) {
    data = {ok: false};
  }
  link.textContent = prevText;

  let title, bodyHtml;
  if (!data.ok || !data.reachable) {
    title = t("settings.update_check_failed_title");
    bodyHtml = esc(t("settings.update_check_failed_body"));
  } else if (data.update_available) {
    title = t("settings.update_available_title");
    bodyHtml = esc(t("settings.update_available_body", {version: data.latest_version}));
    if (data.release_url && /^https?:\/\//i.test(data.release_url)) {
      bodyHtml += ` <a href="${esc(data.release_url)}" target="_blank" rel="noopener">` +
        `${esc(t("settings.update_release_link"))}</a>`;
    }
  } else {
    title = t("settings.up_to_date_title");
    bodyHtml = esc(t("settings.up_to_date_body"));
  }
  document.getElementById("updateCheckTitle").textContent = title;
  document.getElementById("updateCheckBody").innerHTML = bodyHtml;

  const ov = document.getElementById("updateCheckOverlay");
  ov.style.display = "flex";
  const close = () => { ov.style.display = "none"; };
  document.getElementById("updateCheckClose").onclick = close;
  ov.onclick = e => { if (e.target === ov) close(); };
}

// Gruppiert Felder anhand des "break"-Flags zu Zeilen -- jede Zeile ist ein
// eigener Flex-Container (.setrow), der die volle Breite unter sich
// aufteilt. Ersetzt das fruehere auto-fit-Raster mit Leer-Spacer
// (.setbreak): der spannte per grid-column:1/-1 immer alle Spalten der
// GESAMTEN Gruppe, wodurch CSS Grid ungenutzte Spalten nicht mehr
// kollabieren liess -- Zeilen mit nur 1-2 Feldern blieben schmal, mit
// Leerraum rechts, statt die volle Breite zu nutzen.
function fieldRows(fields) {
  const rows = [];
  for (const f of fields) {
    if (f.break || !rows.length) rows.push([]);
    rows[rows.length - 1].push(f);
  }
  return rows;
}

// Feldbreite: explizites f.width gilt, sonst sind Ja/Nein-Felder (Toggles)
// standardmaessig halbbreit -- "maximal zweispaltig" statt volle Zeile je
// Toggle. Alles andere bleibt bei der automatischen Flex-Breite (kein
// width-Attribut -> .setfield ohne Breitenklasse).
function fieldWidthClass(f) {
  const width = f.width || (f.type === "bool" ? "half" : null);
  return width ? ` ${width}` : "";
}

// .setbody buendelt Hinweis + Felder (+ ggf. Unter-Accordions) in EINEM
// Container, der bei "collapsed" komplett verschwindet -- Hinweis und
// Unter-Accordions standen frueher direkt in .sethead bzw. lose im
// .setgroup und blieben dadurch auch im eingeklappten Zustand sichtbar.
function groupHTML(g, v, d, open, extraHtml) {
  return `
    <div class="setgroup${open ? "" : " collapsed"}" data-group="${g.id}">
      <div class="sethead">
        <div class="sethead-top">
          <span class="setchevron">${ICONS.chevronDown}</span>
          <b>${esc(g.title)}</b>
          <button class="act small" data-reset="${g.id}"><span class="btnicon">${ICONS.bookDown}</span> ${esc(t("settings.reset_default"))}</button>
        </div>
      </div>
      <div class="setbody">
        ${g.note ? `<div class="sethint">${esc(g.note)}</div>` : ""}
        <div class="setrows">
          ${fieldRows(g.fields).map(row => `<div class="setrow">${
            row.map(f => settingField(f, v[f.key], d[f.key])).join("")}</div>`).join("")}
        </div>
        ${extraHtml || ""}
      </div>
    </div>`;
}

function subGroupHTML(g, v, d) {
  return `
    <div class="setsubgroup collapsed" data-group="${g.id}">
      <div class="sethead">
        <div class="sethead-top">
          <span class="setchevron">${ICONS.chevronDown}</span>
          <b>${esc(g.title)}</b>
          <button class="act small" data-reset="${g.id}"><span class="btnicon">${ICONS.bookDown}</span> ${esc(t("settings.reset_default"))}</button>
        </div>
      </div>
      <div class="setbody">
        ${g.note ? `<div class="sethint">${esc(g.note)}</div>` : ""}
        <div class="setrows">
          ${fieldRows(g.fields).map(row => `<div class="setrow">${
            row.map(f => settingField(f, v[f.key], d[f.key])).join("")}</div>`).join("")}
        </div>
      </div>
    </div>`;
}

// Anzeigereihenfolge im Einstellungs-Dialog -- fest vorgegeben (nicht die
// Reihenfolge von GROUPS in settings.py), weil hier auch die dynamischen
// Bloecke (Spaltenansicht, Shops, Backup, Aenderungsprotokoll) dazwischen
// einsortiert sind, die nicht Teil des generischen Schemas sind. Merklisten
// sind kein eigener Block mehr -- eine Playlist wird ueber ihren eigenen
// Bearbeiten-Dialog markiert (askPlaylistProps()).
const SETTINGS_LAYOUT = [
  "library", "search", "display", "columnviews",
  "rekordbox", "tools", "shops", "rename", "analysis", "loudness",
  "performance", "backup", "logs",
];

function renderSettings() {
  const v = schema.values, d = schema.defaults;
  const top = schema.groups.filter(g => !g.parent);
  const childrenOf = id => schema.groups.filter(g => g.parent === id);

  const groupHtmlById = {};
  top.forEach(g => {
    const kids = childrenOf(g.id);
    // Unter-Accordions (z.B. die drei Klassifikations-Leitern unter
    // "Analyse") landen INNERHALB des Eltern-Accordions (.setbody), damit
    // sie beim Einklappen des Elternteils mit verschwinden.
    const extra = kids.length
      ? `<div class="setsubgroups">${kids.map(k => subGroupHTML(k, v, d)).join("")}</div>` : "";
    groupHtmlById[g.id] = groupHTML(g, v, d, false, extra);
  });
  const dynamicBlocks = {
    columnviews: renderColumnViewsBlock,
    shops: renderShopsBlock, backup: renderBackupBlock, logs: renderLogsBlock,
  };

  document.getElementById("settingsBody").innerHTML = SETTINGS_LAYOUT
    .map(id => groupHtmlById[id] ?? dynamicBlocks[id]()).join("");

  document.querySelectorAll(".sethead-top").forEach(head => head.onclick = e => {
    if (e.target.closest("button")) return;
    head.closest(".setgroup, .setsubgroup").classList.toggle("collapsed");
  });

  document.querySelectorAll("[data-reset]").forEach(b => b.onclick = () =>
    postSettings("/api/settings/reset", {group: b.dataset.reset}));

  const orphanBtn = document.querySelector('[data-settings-action="orphan_cleanup"]');
  if (orphanBtn) orphanBtn.onclick = () => runOrphanCleanup(orphanBtn);

  document.querySelectorAll("[data-app-select]").forEach(sel => sel.onchange = () => {
    const input = document.getElementById("set_" + sel.dataset.appSelect);
    if (sel.value === "__other__") {
      input.style.display = "";
      input.focus();
    } else {
      input.value = sel.value;
      input.style.display = "none";
    }
    applyShowIf();
  });
  document.getElementById("settingsBody").oninput = applyShowIf;
  applyShowIf();

  document.querySelectorAll("[data-color-opt]").forEach(btn => btn.onclick = () => {
    const key = btn.dataset.colorFor;
    document.getElementById("set_" + key).value = btn.dataset.colorOpt;
    btn.parentElement.querySelectorAll("[data-color-opt]").forEach(sw => {
      const on = sw === btn;
      sw.classList.toggle("on", on);
      sw.innerHTML = on ? ICONS.check : "";
    });
  });

  document.querySelectorAll("[data-folder-add]").forEach(btn => btn.onclick = () => addFolders(btn.dataset.folderAdd));
  document.querySelectorAll(".folderlist").forEach(el => wireFolderRows(el.id.slice("folderlist_".length)));
  document.querySelectorAll("[data-file-pick]").forEach(btn => btn.onclick =
    () => pickFile(btn.dataset.filePick, btn.dataset.filePickUrl));
  document.querySelectorAll("[data-rbplaylist-pick]").forEach(btn => btn.onclick =
    () => openRekordboxPlaylistPicker(btn.dataset.rbplaylistPick));

  wireColumnViewsBlock();
  wireShopsBlock();
  wireBackupBlock();
  wireLogsBlock();
}

// ── Aenderungsprotokoll ───────────────────────────────────────────────
// Wie renderBackupBlock() ein eigener Block statt Teil des Schemas -- auch
// hier gibt es nichts zum Editieren, nur einen Knopf, der den Ordner mit den
// taeglichen Log-Dateien im Finder oeffnet (siehe audit_log.py).
function renderLogsBlock() {
  return `<div class="setgroup collapsed">
    <div class="sethead">
      <div class="sethead-top"><span class="setchevron">${ICONS.chevronDown}</span><b>${esc(t("settings.logs_title"))}</b></div>
    </div>
    <div class="setbody">
      <div class="sethint">${esc(t("settings.logs_note"))}</div>
      <div style="margin-top:10px">
        <button class="act" id="btnLogFolder"><span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.logs_open_folder"))}</button>
      </div>
      <div id="logsStatus"></div>
    </div>
  </div>`;
}

function wireLogsBlock() {
  const st = document.getElementById("logsStatus");
  document.getElementById("btnLogFolder").onclick = async () => {
    st.textContent = t("settings.folder_opening"); st.className = "";
    try {
      const res = await fetch("/api/log-folder", {method: "POST"});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      st.textContent = "";
    } catch (err) {
      st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
    }
  };
}

// ── Verwaiste Ordner ──────────────────────────────────────────────────
// Eigene Rueckfrage vor dem Loeschen -- anders als die uebrigen Knoepfe in
// den Einstellungen betrifft das den Datentraeger, nicht nur config.local.yaml.
function askOrphanCleanup() {
  return new Promise(resolve => {
    const ov = document.getElementById("confirmOverlay");
    const yesBtn = document.getElementById("confirmYes");
    // .confirmnote ist auf "eine Datei"/"diese Liste" formuliert -- passt
    // hier nicht (mehrere Ordner, keine Listenzeile) -- fuer diesen Dialog
    // eigenen Text einsetzen und danach wieder den ueblichen herstellen.
    // ":not(.confirmnote-music)" ist noetig, weil #confirmMusicNote im
    // Markup VOR dieser Notiz steht und ebenfalls die Klasse "confirmnote"
    // traegt -- ein einfaches ".confirmnote" traefe sonst den falschen
    // (ersten) Absatz.
    const noteEl = ov.querySelector(".confirmnote:not(.confirmnote-music)");
    const noteOriginal = noteEl.textContent;
    noteEl.textContent = t("settings.orphan_confirm_note");
    document.getElementById("confirmTitle").textContent = t("settings.orphan_confirm_title");
    document.getElementById("confirmFile").textContent = t("settings.orphan_confirm_file");
    document.getElementById("confirmPath").textContent = "";
    document.getElementById("confirmFacts").innerHTML =
      `<span>${esc(t("settings.orphan_confirm_fact"))}</span>`;
    document.getElementById("confirmMusicNote").textContent = "";
    yesBtn.textContent = t("settings.orphan_search_button");
    ov.style.display = "flex";
    const done = answer => {
      ov.style.display = "none";
      document.onkeydown = null;
      yesBtn.textContent = t("action.to_trash");
      noteEl.textContent = noteOriginal;
      resolve(answer);
    };
    document.getElementById("confirmNo").onclick = () => done(false);
    yesBtn.onclick = () => done(true);
    document.onkeydown = e => { if (e.key === "Escape") done(false); };
  });
}

async function runOrphanCleanup(btn) {
  if (!(await askOrphanCleanup())) return;
  const st = document.getElementById("action_orphan_cleanup_status");
  btn.disabled = true;
  if (st) { st.textContent = t("settings.orphan_searching"); st.className = "sethelp"; }
  // indeterminate: der Server liefert erst am Ende ein Ergebnis, keine
  // Zwischenstaende -- wie beim Rekordbox-/Music-Abgleich (siehe progressToast()).
  const pt = progressToast(t("settings.orphan_searching"), {indeterminate: true});
  try {
    const res = await fetch("/api/orphan-cleanup", {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    const msg = data.found === 0
      ? t("settings.orphan_none_found")
      : t("settings.orphan_result", {removed: data.removed.length, found: data.found}) +
        (data.errors.length ? t("settings.orphan_errors_suffix", {count: data.errors.length}) : ".");
    if (st) { st.textContent = msg; st.className = data.errors.length ? "sethelp warn" : "sethelp"; }
    if (data.errors.length && !data.removed.length) pt.fail(msg); else pt.done(msg);
  } catch (err) {
    const msg = t("error.failed", {error: err.message});
    if (st) { st.textContent = msg; st.className = "sethelp warn"; }
    pt.fail(msg);
  }
  btn.disabled = false;
}

// ── Spaltenansichten verwalten ──────────────────────────────────────────
// Wie die Merklisten und Shops ein eigener Block mit eigenem Speichern-Knopf
// statt Teil des generischen Schemas: variable Zeilenzahl, und angelegt
// werden die Ansichten nicht hier, sondern im Spalten-Menue der Tabelle
// ("Ansicht speichern"). Hier steht nur, was danach kommt -- umbenennen,
// loeschen und die Wahl, welche Ansicht als Standard fuer alle Listen ohne
// eigene Einstellung gilt.
let colViewDraft = [];
let colViewDefaultDraft = "";
let colViewMusicDefaultDraft = "";

function renderColumnViewsBlock() {
  colViewDraft = COLUMN_VIEWS.map(v => ({...v}));
  colViewDefaultDraft = COLUMN_VIEW_DEFAULT;
  colViewMusicDefaultDraft = COLUMN_VIEW_MUSIC_DEFAULT;
  return `<div class="setgroup collapsed">
    <div class="sethead">
      <div class="sethead-top"><span class="setchevron">${ICONS.chevronDown}</span><b>${esc(t("settings.column_views_title"))}</b></div>
    </div>
    <div class="setbody">
      <div class="sethint">${esc(t("settings.column_views_note"))}</div>
      <div class="setrows">
        <div class="setrow"><div class="setfield">
          <label for="cvDefault">${esc(t("settings.column_view_default_label"))}</label>
          <select id="cvDefault"></select>
          <div class="sethelp">${esc(t("settings.column_view_default_help"))}</div>
        </div></div>
        <div class="setrow"><div class="setfield">
          <label for="cvMusicDefault">${esc(t("settings.column_view_music_default_label"))}</label>
          <select id="cvMusicDefault"></select>
          <div class="sethelp">${esc(t("settings.column_view_music_default_help"))}</div>
        </div></div>
      </div>
      <div id="cvRows" style="margin-top:10px"></div>
      <div id="cvStatus"></div>
      <button class="act" id="btnSaveCv" style="margin-top:10px"><span class="btnicon">${
        ICONS.save}</span> ${esc(t("settings.column_views_save"))}</button>
    </div>
  </div>`;
}

// Auswahlfeld und Zeilen haengen aneinander (eine geloeschte Ansicht darf
// nicht mehr als Standard waehlbar sein), deshalb werden beide zusammen
// gezeichnet.
function redrawColumnViewsBlock() {
  const sel = document.getElementById("cvDefault");
  if (!sel) return;
  if (!colViewDraft.some(v => v.id === colViewDefaultDraft)) colViewDefaultDraft = "";
  sel.innerHTML = `<option value="">${esc(t("cols.view_standard"))}</option>` +
    colViewDraft.map(v => `<option value="${esc(v.id)}"` +
      `${v.id === colViewDefaultDraft ? " selected" : ""}>${esc(v.name)}</option>`).join("");
  sel.onchange = () => { colViewDefaultDraft = sel.value; };

  const selMusic = document.getElementById("cvMusicDefault");
  if (!colViewDraft.some(v => v.id === colViewMusicDefaultDraft)) colViewMusicDefaultDraft = "";
  selMusic.innerHTML = `<option value="">${esc(t("cols.view_standard"))}</option>` +
    colViewDraft.map(v => `<option value="${esc(v.id)}"` +
      `${v.id === colViewMusicDefaultDraft ? " selected" : ""}>${esc(v.name)}</option>`).join("");
  selMusic.onchange = () => { colViewMusicDefaultDraft = selMusic.value; };

  const host = document.getElementById("cvRows");
  host.innerHTML = colViewDraft.length
    ? colViewDraft.map((v, i) => `<div class="cvrow" data-cv="${i}">
        <input type="text" data-cvf="name" maxlength="40" value="${esc(v.name)}"
               placeholder="${esc(t("settings.column_view_name_placeholder"))}">
        <button class="iconbtn del" data-cvdel="${i}" title="${
          esc(t("cols.view_delete"))}">${ICONS.trash}</button>
      </div>`).join("")
    : `<div class="sethelp">${esc(t("settings.column_views_empty"))}</div>`;
  host.querySelectorAll(".cvrow").forEach(row => {
    const i = +row.dataset.cv;
    row.querySelector("[data-cvf=name]").oninput = ev => { colViewDraft[i].name = ev.target.value; };
    row.querySelector("[data-cvdel]").onclick = () => {
      colViewDraft.splice(i, 1);
      redrawColumnViewsBlock();
    };
  });
}

function wireColumnViewsBlock() {
  redrawColumnViewsBlock();
  document.getElementById("btnSaveCv").onclick = saveColumnViewsFromSettings;
}

async function saveColumnViewsFromSettings() {
  const st = document.getElementById("cvStatus");
  st.textContent = t("settings.column_views_saving"); st.className = "";
  try {
    const res = await fetch("/api/column-views", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({column_views: colViewDraft, default: colViewDefaultDraft,
                            music_default: colViewMusicDefaultDraft})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    adoptColumnViewsFromServer(data);
    redrawColumnViewsBlock();
    st.textContent = t("settings.column_views_saved");
    // Eine geloeschte oder umbenannte Ansicht schlaegt sofort auf die
    // Tabelle durch -- der Server hat verwaiste Zuordnungen mit entfernt.
    repaintColumns();
  } catch (err) {
    st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
  }
}

// Antwort von /api/column-views (oder /api/settings) in die drei Globalen
// uebernehmen -- eine Stelle, damit die Pruefungen (Spalten normalisieren,
// unbekannte Zuordnungen verwerfen) nicht doppelt stehen.
function adoptColumnViewsFromServer(data) {
  COLUMN_VIEWS = (data.column_views || [])
    .filter(v => v && v.id && v.name)
    .map(v => normalizeColumnStore({id: String(v.id), name: String(v.name),
                                    order: v.order, hidden: v.hidden, widths: v.widths}));
  COLUMN_ASSIGN = {};
  const assign = (data.column_view_assign && typeof data.column_view_assign === "object")
    ? data.column_view_assign : {};
  for (const [viewId, id] of Object.entries(assign)) {
    if (COLUMN_VIEWS.some(v => v.id === id)) COLUMN_ASSIGN[viewId] = id;
  }
  COLUMN_VIEW_DEFAULT = COLUMN_VIEWS.some(v => v.id === data.column_view_default)
    ? data.column_view_default : "";
  COLUMN_VIEW_MUSIC_DEFAULT = COLUMN_VIEWS.some(v => v.id === data.column_view_music_default)
    ? data.column_view_music_default : "";
  saveFilters();
}

// ── Shops verwalten ───────────────────────────────────────────────────
// Eigener Speichern-Knopf statt Teil des generischen Schemas: Shops sind
// eine Liste variabler Länge, keine feste Feldmenge wie die übrigen Gruppen.
let shopDraft = [];

function renderShopsBlock() {
  shopDraft = (schema.shops || []).map(s => ({...s}));
  return `<div class="setgroup collapsed">
    <div class="sethead">
      <div class="sethead-top"><span class="setchevron">${ICONS.chevronDown}</span><b>${esc(t("settings.shops_title"))}</b>
        <button class="act small" data-reset="shops"><span class="btnicon">${ICONS.bookDown}</span> ${esc(t("settings.reset_default"))}</button>
      </div>
    </div>
    <div class="setbody">
      <div class="sethint">${esc(t("settings.shops_note"))}</div>
      <div id="shopRows">${shopDraft.map((s, i) => shopRowHTML(s, i)).join("")}</div>
      <button class="act small" id="btnAddShop" style="margin-top:10px">${esc(t("settings.shops_add"))}</button>
      <div id="shopStatus"></div>
      <button class="act" id="btnSaveShops" style="margin-top:10px"><span class="btnicon">${
        ICONS.save}</span> ${esc(t("settings.shops_save"))}</button>
    </div>
  </div>`;
}

function shopRowHTML(s, i) {
  return `<div class="shoprow" data-shop="${i}">
    <input type="checkbox" data-sf="enabled" ${s.enabled ? "checked" : ""} title="${esc(t("settings.shop_active_title"))}">
    <input type="color" data-sf="color" value="${esc(s.color || "#888888")}">
    <input type="text" data-sf="name" value="${esc(s.name || "")}" placeholder="${esc(t("settings.shop_name_placeholder"))}">
    <input type="text" data-sf="url" value="${esc(s.url || "")}" placeholder="https://…?q={q}">
    <button class="iconbtn del" data-sf="remove" title="${esc(t("settings.shop_remove_title"))}">${ICONS.trash}</button>
  </div>`;
}

function redrawShopRows() {
  document.getElementById("shopRows").innerHTML =
    shopDraft.map((s, i) => shopRowHTML(s, i)).join("");
  wireShopRows();
}

function wireShopRows() {
  document.querySelectorAll("#shopRows .shoprow").forEach(row => {
    const i = +row.dataset.shop;
    row.querySelectorAll("[data-sf]").forEach(el => {
      const field = el.dataset.sf;
      if (field === "remove") {
        el.onclick = () => { shopDraft.splice(i, 1); redrawShopRows(); };
      } else if (field === "enabled") {
        el.onchange = () => { shopDraft[i].enabled = el.checked; };
      } else {
        el.oninput = () => { shopDraft[i][field] = el.value; };
      }
    });
  });
}

function wireShopsBlock() {
  document.getElementById("btnAddShop").onclick = () => {
    shopDraft.push({id: "", name: "", url: "", color: "#888888", enabled: true});
    redrawShopRows();
  };
  document.getElementById("btnSaveShops").onclick = saveShops;
  wireShopRows();
}

async function saveShops() {
  const st = document.getElementById("shopStatus");
  st.textContent = t("settings.shops_saving"); st.className = "";
  try {
    const res = await fetch("/api/shops", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({shops: shopDraft})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    ALL_SHOPS = data.shops;
    schema.shops = data.shops;
    shopDraft = ALL_SHOPS.map(s => ({...s}));
    redrawShopRows();
    st.textContent = t("settings.shops_saved");
    render();
  } catch (err) {
    st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
  }
}

// ── Backup ────────────────────────────────────────────────────────────
// Eigener Block statt Teil des generischen Schemas: das sind Aktionen
// (Backup jetzt / wiederherstellen), keine Einstellungswerte zum Editieren.
function renderBackupBlock() {
  const keep = schema.backup_keep || 10;
  return `<div class="setgroup collapsed">
    <div class="sethead">
      <div class="sethead-top"><span class="setchevron">${ICONS.chevronDown}</span><b>${esc(t("settings.backup_title"))}</b></div>
    </div>
    <div class="setbody">
      <div class="sethint">${esc(t("settings.backup_note", {keep}))}</div>
      <div id="backupList" class="path">${esc(t("settings.backup_loading"))}</div>
      <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap">
        <button class="act" id="btnBackupCreate"><span class="btnicon">${ICONS.databaseArrowDown}</span> ${esc(t("settings.backup_create"))}</button>
        <button class="act" id="btnBackupRestore"><span class="btnicon">${ICONS.databaseBackup}</span> ${esc(t("settings.backup_restore"))}</button>
        <button class="act" id="btnBackupFolderDb"><span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.backup_folder_db"))}</button>
        <button class="act" id="btnBackupFolderRb"><span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.backup_folder_rb"))}</button>
      </div>
      <div id="backupStatus"></div>
    </div>
  </div>`;
}

async function loadBackupList() {
  const el = document.getElementById("backupList");
  try {
    const data = await (await fetch("/api/backups")).json();
    if (!data.backups.length) {
      el.textContent = t("settings.backup_none_yet");
      return;
    }
    const newest = data.backups[0];
    el.innerHTML = t("settings.backup_newest", {
      name: esc(newest.name), size: (newest.size/1048576).toFixed(1),
      count: data.backups.length, dir: esc(data.dir),
    });
  } catch (err) {
    el.textContent = t("settings.load_failed", {error: err.message});
  }
}

function wireBackupBlock() {
  loadBackupList();
  const st = document.getElementById("backupStatus");

  document.getElementById("btnBackupCreate").onclick = async () => {
    st.textContent = t("settings.backup_choosing_location"); st.className = "";
    try {
      const res = await fetch("/api/backup/create", {method: "POST"});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      st.textContent = data.cancelled ? t("settings.backup_cancelled") : t("settings.backup_saved_at", {path: data.path});
    } catch (err) {
      st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
    }
  };

  document.getElementById("btnBackupRestore").onclick = async () => {
    if (!confirm(t("settings.backup_restore_confirm"))) return;
    st.textContent = t("settings.backup_choosing"); st.className = "";
    try {
      const res = await fetch("/api/backup/restore-pick", {method: "POST"});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      if (data.cancelled) { st.textContent = t("settings.backup_cancelled"); return; }
      st.innerHTML = t("settings.backup_restored");
      document.getElementById("backupReload").onclick = e => { e.preventDefault(); location.reload(); };
      loadBackupList();
    } catch (err) {
      st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
    }
  };

  const openBackupFolder = which => async () => {
    st.textContent = t("settings.folder_opening"); st.className = "";
    try {
      const res = await fetch("/api/backup-folder", {method: "POST",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify({which})});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      st.textContent = "";
    } catch (err) {
      st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
    }
  };
  document.getElementById("btnBackupFolderDb").onclick = openBackupFolder("db");
  document.getElementById("btnBackupFolderRb").onclick = openBackupFolder("rekordbox");
}

function settingField(f, value, dflt) {
  // Aktion statt Wert -- kein <label>/"geändert"-Vergleich, kein data-key
  // (also auch nicht Teil von collectSettings()/validate()). Der Klick wird
  // separat verdrahtet, siehe renderSettings().
  if (f.type === "button") {
    return `<div class="setfield${fieldWidthClass(f)}">
      <button class="act danger" data-settings-action="${f.key}">${
        f.icon && ICONS[f.icon] ? `<span class="btnicon">${ICONS[f.icon]}</span> ` : ""}${esc(f.label)}</button>
      ${f.help ? `<div class="sethelp">${esc(f.help)}</div>` : ""}
      <div id="action_${f.key}_status" class="sethelp"></div>
    </div>`;
  }
  if (f.type === "folderlist") {
    return `<div class="setfield full">
      <label>${esc(f.label)}</label>
      <div class="folderlist" id="folderlist_${f.key}">${folderListRows(f.key, value || [])}</div>
      <button type="button" class="act small" data-folder-add="${f.key}">
        <span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.add_folder"))}</button>
      <input type="hidden" id="set_${f.key}" data-key="${f.key}" value="${esc((value || []).join("\n"))}">
      ${f.help ? `<div class="sethelp">${esc(f.help)}</div>` : ""}
    </div>`;
  }
  const changed = JSON.stringify(value) !== JSON.stringify(dflt);
  const label = `<label for="set_${f.key}">${esc(f.label)}${
    f.unit ? ` <span class="path">(${f.unit})</span>` : ""}${
    changed ? ` <span class="chg">${esc(t("settings.changed_tag"))}</span>` : ""}</label>`;
  let input;
  if (f.type === "number") {
    input = `<input id="set_${f.key}" type="number" data-key="${f.key}"
             value="${value}" min="${f.min}" max="${f.max}" step="${f.step}">`;
  } else if (f.type === "paths") {
    input = `<textarea id="set_${f.key}" data-key="${f.key}" rows="${
      Math.max(2, (value||[]).length)}">${esc((value||[]).join("\n"))}</textarea>`;
  } else if (f.type === "app") {
    input = appPicker(f, value);
  } else if (f.type === "bool") {
    // Toggle-Schalter statt nacktem Kaestchen (siehe .toggle in app.css) --
    // Beschriftung steht wie bei jedem anderen Feld schon in `label` darueber,
    // hier nur der Schalter selbst.
    input = `<label class="toggle"><input id="set_${f.key}" type="checkbox" data-key="${f.key}"${
      value ? " checked" : ""}><span class="toggle-track"><span class="toggle-thumb"></span></span></label>`;
  } else if (f.type === "text" && f.pick) {
    // Wie appPicker(), aber der Server gibt hier keine Liste vor (z.B. eine
    // .als-Datei irgendwo im Dateisystem) -- nativer Dateidialog statt
    // Dropdown, derselbe Server-Roundtrip wie addFolders()/choose_folders().
    input = `<div class="filepick">
      <input id="set_${f.key}" type="text" data-key="${f.key}"
             placeholder="${esc(f.placeholder || "")}" value="${esc(value || "")}">
      <button type="button" class="act small" data-file-pick="${f.key}" data-file-pick-url="${f.pick}">
        <span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.pick_file"))}</button>
    </div>`;
  } else if (f.type === "text" && f.rbpick) {
    // Wie der Dateidialog oben, aber statt eines Pfads im Dateisystem steht
    // hier eine Rekordbox-Playlist zur Wahl -- eigener Baum-Dialog
    // (openRekordboxPlaylistPicker()) statt eines nativen Dateidialogs.
    input = `<div class="filepick">
      <input id="set_${f.key}" type="text" data-key="${f.key}"
             placeholder="${esc(f.placeholder || "")}" value="${esc(value || "")}">
      <button type="button" class="act small" data-rbplaylist-pick="${f.key}">
        <span class="btnicon">${ICONS.folder}</span> ${esc(t("settings.pick_playlist"))}</button>
    </div>`;
  } else if (f.type === "text") {
    input = `<input id="set_${f.key}" type="text" data-key="${f.key}"
             placeholder="${esc(f.placeholder || "")}" value="${esc(value || "")}">`;
  } else if (f.type === "select") {
    input = `<select id="set_${f.key}" data-key="${f.key}">${
      (f.options || []).map(o => `<option value="${esc(o.value)}"${
        o.value === value ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "color") {
    // Feste Vorgabefarben als Schwatch-Reihe statt Dropdown -- gleiche Optik
    // wie die Merklisten-Farbwahl (.flswatches/.flswatch). Das eigentliche
    // Feld ist das versteckte Eingabefeld darunter (data-key), collectSettings()
    // liest es wie jedes andere Textfeld -- die Knoepfe selbst tragen keinen
    // data-key und werden separat in renderSettings() verdrahtet.
    input = `<div class="colorswatches">${
      (f.options || []).map(o => `<button type="button" class="colorswatch${
        o.value === value ? " on" : ""}" data-color-opt="${o.value}" data-color-for="${f.key}"
        style="background:var(--dc-${o.value})" title="${esc(o.label)}">${
        o.value === value ? ICONS.check : ""}</button>`).join("")}</div>
      <input type="hidden" id="set_${f.key}" data-key="${f.key}" value="${esc(value || "")}">`;
  } else {
    input = `<input id="set_${f.key}" type="text" data-key="${f.key}"
             value="${esc((value||[]).join(", "))}">`;
  }
  const showIf = f.show_if ? ` data-show-if-key="${esc(f.show_if.key)}"` +
    (f.show_if.not_empty ? ` data-show-if-not-empty="1"`
      : ` data-show-if-contains="${esc(f.show_if.contains)}"`) : "";
  return `<div class="setfield${fieldWidthClass(f)}"${showIf}>${label}${input}
          ${f.help ? `<div class="sethelp">${esc(f.help)}</div>` : ""}</div>`;
}

// Blendet Felder mit data-show-if-key/-contains (oder -not-empty) ein/aus,
// je nachdem, ob der aktuelle (Live-)Wert des referenzierten Feldes den
// Teilstring enthaelt bzw. ueberhaupt gesetzt ist -- z.B. "Ableton-Vorlage"
// nur, wenn external_daw auf Ableton zeigt, "Cover aus Music.app
// uebernehmen" nur, wenn external_music ueberhaupt eine App traegt. Reagiert
// per Event-Delegation auf jede Eingabe im Dialog statt einzeln pro Feld
// verdrahtet zu werden.
function applyShowIf() {
  document.querySelectorAll("[data-show-if-key]").forEach(el => {
    const src = document.getElementById("set_" + el.dataset.showIfKey);
    const raw = src ? src.value : "";
    const visible = el.dataset.showIfNotEmpty === "1"
      ? raw.trim() !== ""
      : raw.toLowerCase().includes(el.dataset.showIfContains);
    el.style.display = visible ? "" : "none";
  });
}

function folderListRows(key, items) {
  return items.map((p, i) => `<div class="folderrow"><span title="${esc(p)}">${esc(p)}</span>
    <button type="button" class="act small danger" data-folder-remove="${key}" data-idx="${i}">${ICONS.close}</button></div>`).join("");
}

function currentFolderList(key) {
  const hidden = document.getElementById("set_" + key);
  return hidden.value ? hidden.value.split("\n").filter(Boolean) : [];
}

function setFolderList(key, items) {
  document.getElementById("set_" + key).value = items.join("\n");
  document.getElementById("folderlist_" + key).innerHTML = folderListRows(key, items);
  wireFolderRows(key);
}

function wireFolderRows(key) {
  document.querySelectorAll(`#folderlist_${key} [data-folder-remove]`).forEach(btn => btn.onclick = () => {
    const items = currentFolderList(key);
    items.splice(+btn.dataset.idx, 1);
    setFolderList(key, items);
  });
}

async function addFolders(key) {
  try {
    const res = await fetch("/api/pick-folder", {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    const items = currentFolderList(key);
    for (const p of data.paths || []) if (!items.includes(p)) items.push(p);
    setFolderList(key, items);
  } catch (err) {
    note(t("error.generic", {error: err.message}), true);
  }
}

async function pickFile(key, url) {
  const input = document.getElementById("set_" + key);
  try {
    const res = await fetch(url, {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    if (data.path) {
      input.value = data.path;
      applyShowIf();
    }
  } catch (err) {
    note(t("error.generic", {error: err.message}), true);
  }
}

// Baum-Dialog fuer die Rekordbox-Playlist-Einstellung -- anders als
// pickFile() kein nativer Dateidialog, sondern derselbe Endpunkt wie der
// Rekordbox-Ast im Playlist-Baum (siehe EXT_SOURCES.rekordbox), hier als
// flache, nach Pfad sortierte Liste. Nur bei geschlossenem Rekordbox
// aufrufbar -- ein waehrend des Dialogs laufendes Rekordbox koennte die
// Bibliothek zwischendurch wechseln, siehe /api/rekordbox-status.
async function openRekordboxPlaylistPicker(key) {
  let status;
  try {
    status = await fetch("/api/rekordbox-status").then(r => r.json());
  } catch (err) {
    note(t("error.generic", {error: err.message}), true);
    return;
  }
  if (!status.ok) { note(t("error.generic", {error: t("error.unknown")}), true); return; }
  if (status.running) { note(t("settings.rbpicker_running"), "soft"); return; }

  const ov = document.getElementById("rbPlaylistOverlay");
  const list = document.getElementById("rbPlaylistList");
  const close = () => { ov.style.display = "none"; };
  document.getElementById("rbPlaylistCancel").onclick = close;
  ov.onclick = e => { if (e.target === ov) close(); };
  list.innerHTML = `<div class="sethint">${esc(t("settings.rbpicker_loading"))}</div>`;
  ov.style.display = "flex";

  let data;
  try {
    data = await fetch("/api/rekordbox-playlists").then(r => r.json());
  } catch (err) {
    list.innerHTML = `<div class="sethelp warn">${esc(t("error.generic", {error: err.message}))}</div>`;
    return;
  }
  if (!data.ok) {
    list.innerHTML = `<div class="sethelp warn">${esc(data.error || t("error.unknown"))}</div>`;
    return;
  }
  const nodes = data.playlists || [];
  if (!nodes.some(n => n.kind === "playlist")) {
    list.innerHTML = `<div class="sethint">${esc(t("settings.rbpicker_empty"))}</div>`;
    return;
  }

  // Echter Baum statt einer flachen, alphabetisch sortierten Pfadliste --
  // derselbe Aufbau wie der Rekordbox-Ast im Seitenbaum (extChildren()/
  // renderTree()): FLACH gerendert, Einrueckung ueber --depth (app.css kennt
  // das schon vom Baum), sortiert nach Rekordbox' eigener Reihenfolge (seq)
  // statt alphabetisch. Ordner starten zugeklappt -- an echtem Material
  // knapp 1000 Knoten in Tiefe 4 (siehe rekordbox.py:playlists()). "closed"
  // lebt nur waehrend dieser Dialog-Instanz, kein eigener state-Eintrag.
  const byId = new Map(nodes.map(n => [n.id, n]));
  const pathOf = n => {
    const parts = [n.name];
    for (let p = n.parent ? byId.get(n.parent) : null; p; p = p.parent ? byId.get(p.parent) : null) {
      parts.unshift(p.name);
    }
    return parts.join("/");
  };
  const childrenOf = parentId => nodes
    .filter(n => (n.parent || null) === (parentId || null))
    .sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const closed = new Set(nodes.filter(n => n.kind === "folder").map(n => n.id));

  const renderPicker = () => {
    const out = [];
    const walk = (parentId, depth) => {
      for (const n of childrenOf(parentId)) {
        const hasKids = n.kind === "folder" && childrenOf(n.id).length > 0;
        const open = !closed.has(n.id);
        const icon = PLAYLIST_ICONS[n.kind] || PLAYLIST_ICONS.playlist;
        out.push(`<button type="button" class="treenode rbtreenode${n.kind === "smart" ? " smart" : ""}"
            data-id="${esc(n.id)}" style="--depth:${depth}">
          <span class="twist${hasKids ? (open ? " open" : "") : " leaf"}">&#9654;</span>
          <span class="ticon">${SVG(LUCIDE_ICONS[icon.slice(4)] || "")}</span>
          <span class="tlabel">${esc(n.name)}</span>
          ${n.kind === "smart"
            ? `<span class="tlock" title="${esc(t("tree.smart_unsupported"))}">${ICONS.lock}</span>` : ""}
          ${n.kind === "folder" ? "" : `<span class="tcount">${n.count}</span>`}
        </button>`);
        if (n.kind === "folder" && open) walk(n.id, depth + 1);
      }
    };
    walk(null, 0);
    list.innerHTML = out.join("");
    list.querySelectorAll(".treenode").forEach(btn => btn.onclick = () => {
      const n = byId.get(btn.dataset.id);
      if (n.kind === "folder") {
        if (closed.has(n.id)) closed.delete(n.id); else closed.add(n.id);
        renderPicker();
        return;
      }
      if (n.kind === "smart") return;
      document.getElementById("set_" + key).value = pathOf(n);
      close();
    });
  };
  renderPicker();
}

// Programme kennt der Server ueber mdfind, nicht per Browser-Dateidialog —
// der gibt den echten Pfad einer .app aus Sicherheitsgruenden nie preis.
function appPicker(f, value) {
  const apps = (schema && schema[f.appList || "apps"]) || [];
  const known = apps.some(a => a.path === value);
  const other = value && !known;
  const options = [`<option value="">${esc(f.placeholder || t("settings.no_option_set"))}</option>`]
    .concat(apps.map(a => `<option value="${esc(a.path)}"${a.path === value ? " selected" : ""}>${esc(a.name)}</option>`))
    .concat([`<option value="__other__"${other ? " selected" : ""}>${esc(t("settings.other_path"))}</option>`])
    .join("");
  return `<select data-app-select="${f.key}">${options}</select>
    <input id="set_${f.key}" type="text" data-key="${f.key}" value="${esc(value || "")}"
           placeholder="/Applications/…/Programm.app"
           style="margin-top:6px${other ? "" : ";display:none"}">`;
}

function collectSettings() {
  const out = {};
  document.querySelectorAll("[data-key]").forEach(el => {
    if (el.type === "checkbox") out[el.dataset.key] = el.checked;
    else if (el.type === "number") out[el.dataset.key] = parseFloat(el.value);
    else out[el.dataset.key] = el.value;
  });
  return out;
}

async function postSettings(url, body) {
  const st = document.getElementById("settingsStatus");
  st.textContent = t("settings.shops_saving"); st.className = "";
  try {
    const res = await fetch(url, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    // Ein tatsaechlicher Sprachwechsel laedt die Seite direkt neu, statt wie
    // die uebrigen Einstellungen sofort wirksam zu werden: refreshI18nCache()
    // faengt zwar die meisten Modul-Konstanten (labels, FIELD_LABELS, ...)
    // ab, aber nicht jede Stelle im Code laesst sich so nachziehen -- ein
    // echtes Neuladen ist hier zuverlaessiger als noch mehr Sonderfaelle.
    if (resolveLang(data.values.ui_language) !== LANG) { location.reload(); return; }
    // Ein Neuladen ist nur noetig, wenn sich durch die Aenderung tatsaechlich
    // Verdikte verschoben haben (data.changed > 0) -- die im Client
    // eingebettete DATA kennt sonst weiter die alten Werte. Frueher musste
    // der Nutzer dafuer selbst auf einen Link klicken; das laeuft jetzt
    // automatisch beim Speichern, ohne einen zusaetzlichen Klick.
    if (data.changed > 0) { location.reload(); return; }
    // Ein paar Felder (externe Programme, Rekordbox-Playlist) schalten
    // Knoepfe/Baumaeste ein oder aus, die nur beim Laden der Seite aus
    // schema.values gebaut werden (MIK_NAME/MUSIC_NAME/REKORDBOX_NAME/...,
    // siehe deren Definition weiter unten) -- ein Nachziehen hier muesste
    // jede dieser Stellen doppelt pflegen. Reload ist deshalb auch hier
    // zuverlaessiger, ausgeloest ueber das deklarative "reload"-Flag am
    // Feld (settings.py GROUPS) statt einer hier gepflegten Liste von Keys.
    const reloadKeys = schema.groups.flatMap(g => g.fields
      .filter(f => f.reload).map(f => f.key));
    if (reloadKeys.some(k => JSON.stringify(schema.values[k]) !== JSON.stringify(data.values[k]))) {
      location.reload(); return;
    }
    schema.values = data.values;
    // Nur der Shops-Reset liefert "shops" mit -- alle anderen Gruppen
    // aendern die Liste nicht, ALL_SHOPS/schema.shops sollen dann unberuehrt
    // bleiben statt auf einen fehlenden Wert zurueckzufallen.
    if (data.shops) {
      ALL_SHOPS = data.shops;
      schema.shops = data.shops;
      shopDraft = ALL_SHOPS.map(s => ({...s}));
    }
    // Darstellungs-Einstellungen sofort wirksam machen -- fuer die
    // Filterleiste waere ein Neuladen der Seite unnoetig.
    state.pinned = !!data.values.pin_filter_bar;
    applyPinned();
    applyFontSize(data.values.font_size);
    applyTheme(data.values.theme);
    applyAccentColor(data.values.accent_color);
    state.infiniteScroll = !!data.values.infinite_scroll;
    state.searchTolerance = typeof data.values.search_typo_tolerance === "number"
      ? data.values.search_typo_tolerance : 0.8;
    state.searchAcMinChars = typeof data.values.search_autocomplete_min_chars === "number"
      ? data.values.search_autocomplete_min_chars : 3;
    DEFAULT_SEARCH_FILTERS = Array.isArray(data.values.default_search_filters)
      ? data.values.default_search_filters : [];
    // Eine Aenderung hier macht Sitzungs-Deaktivierungen (state.disabledDefaults)
    // wertlos -- die Filterliste wird komplett neu gebaut und vergibt neue
    // defaultId's, siehe refreshDefaultFilters().
    state.disabledDefaults = new Set();
    refreshDefaultFilters();
    renderSettings();
    render();
    st.textContent = t("settings.saved", {
      changed: data.changed.toLocaleString("de-DE"), total: data.total.toLocaleString("de-DE"),
    });
  } catch (err) {
    st.textContent = t("error.generic", {error: err.message}); st.className = "warn";
  }
}

// ── Scan aus der Oberfläche ─────────────────────────────────────────────
let scanTimer = 0;
// Waehrend eines Scans ist der Wechsel in die Player-Ansicht gesperrt (siehe
// applyLayout()) -- von pollScan() aktuell gehalten, das ohnehin einmal beim
// Laden UND waehrend jedes laufenden Scans aufgerufen wird, spiegelt also
// auch einen von woanders (anderer Tab, Desktop-App) gestarteten Scan wider.
let scanRunning = false;

async function startScan(force, extra = {}) {
  if (!apiMode) { note(t("scan.needs_server"), "soft"); return; }
  try {
    const res = await fetch("/api/scan", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({force: !!force, prune: true, ...extra})});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    pollScan();
  } catch (err) {
    scanLine(esc(t("scan.start_failed", {error: err.message})), true);
  }
}

function scanLine(html, warn) {
  const el = document.getElementById("scanLine");
  el.innerHTML = html;
  el.className = warn ? "warn" : "";
}

async function pollScan() {
  clearTimeout(scanTimer);
  let s;
  try {
    s = await (await fetch("/api/scan/status")).json();
  } catch (err) { scanLine(esc(t("scan.lost_connection")), true); return; }
  scanRunning = !!s.running;
  syncLayoutSwitch();

  if (s.running) {
    const pct = s.total ? Math.round(100 * s.done / s.total) : 0;
    scanLine(`<span class="bar"><i style="width:${pct}%"></i></span>` +
      `${esc(t("scan.status_line", {phase: s.phase, done: s.done.toLocaleString("de-DE"), total: s.total.toLocaleString("de-DE")}))} ` +
      `<button class="act small" id="btnCancelScan">${esc(t("action.cancel"))}</button>`);
    const cb = document.getElementById("btnCancelScan");
    if (cb) cb.onclick = () => fetch("/api/scan/cancel", {method: "POST"});
    scanTimer = setTimeout(pollScan, 500);
    return;
  }

  if (s.error) { scanLine(esc(t("scan.failed", {error: s.error})), true); return; }
  if (s.finished) {
    const f = s.finished;
    scanLine(`${esc(f.cancelled ? t("scan.cancelled_after") : t("scan.finished"))} ` +
      `${esc(t("scan.progress_of", {done: f.done.toLocaleString("de-DE"), total: f.todo.toLocaleString("de-DE")}))}` +
      (f.removed ? esc(t("scan.removed_suffix", {count: f.removed})) : "") +
      (f.covers_filled ? esc(t("scan.covers_filled_suffix", {count: f.covers_filled})) : "") +
      (f.tag_issues_rechecked ? esc(t("scan.tag_issues_rechecked_suffix", {count: f.tag_issues_rechecked})) : "") +
      ` — <a href="#" id="reloadScan">${esc(t("scan.reload_page"))}</a>`);
    const rl = document.getElementById("reloadScan");
    if (rl) rl.onclick = e => { e.preventDefault(); location.reload(); };
    clearUndoStacks();
  }
}

// ── Detailansicht (geteilt von Tabelle und Einzelprüfungen) ──────────────
function detailHTML(r) {
  return `<div class="grid">
    <div class="spectrum">${spectrumSVG(r)}</div>
    <div style="flex:1;min-width:260px">
      <ul class="reasons">${reasonsOf(r).map(x => "<li>"+esc(x)+"</li>").join("")}</ul>
      ${playerHTML(r)}
      ${r.lu ? `<div class="path" style="margin-top:8px">
        Lautheit ${String(r.lu.toFixed(1)).replace(".",",")} LUFS ·
        True Peak ${String(r.tp.toFixed(1)).replace(".",",")} dBTP ·
        LRA ${String(r.lra.toFixed(1)).replace(".",",")} LU —
        ${esc(loudnessHint(r.lu))}${r.tp > META.loudClip ? esc(t("detail.clip_risk")) : ""}
        <span style="opacity:.7"> ${esc(t("detail.hint_note"))}</span>
      </div>` : ""}
      <div class="path" style="margin-top:8px">
        ${r.en ? esc(t("detail.encoder_prefix", {name: r.en})) : ""}
        ${r.lp ? esc(t("detail.lame_lowpass_prefix", {khz: String(r.lp).replace(".",",")})) : ""}
        ${(r.sz/1048576).toFixed(1).replace(".",",")} MB · ${r.sr} Hz
      </div>
    </div></div>`;
}

async function reveal(path, btn) {
  try {
    const res = await fetch("/api/reveal", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path})
    });
    if (!res.ok) throw new Error(String(res.status));
    btn.classList.add("done");
  } catch (err) {
    btn.classList.add("failed");
    note(t("toast.finder_failed"), true);
  }
  setTimeout(() => btn.classList.remove("done", "failed"), 1000);
}

// ── Player mit Waveform ─────────────────────────────────────────────────
// Es läuft immer nur ein Track; ein neuer Start pausiert den vorherigen.
const players = new Map();
let activeKey = null;

// ── Zeichenebene: was sich nicht bewegt, wird nur einmal gerechnet ───────
// Balken und Cue-Marker aendern sich waehrend der Wiedergabe nicht -- nur
// die Abspielposition wandert. Sie landen deshalb in einem eigenen,
// unsichtbaren Canvas je Eintrag (entry.layer, siehe buildWaveLayer) und
// werden pro Bild nur noch kopiert. Pro Bild bleiben damit: clearRect,
// drawImage, ein Rechteck fuer den gespielten Teil und eine Linie --
// konstanter Aufwand, unabhaengig von der Zahl der Rohspalten.
//
// Vorher lief bei JEDEM ontimeupdate-Tick (~4/s je aufgeklappter Zeile) das
// komplette Downsampling ueber 1200 Rohspalten samt Glaettung, HSL-Umrechnung
// und einem rgb()-String je Balken -- die groesste einzelne Quelle von
// Rechenlast und kurzlebigen Objekten in der Oberflaeche.
//
// Alle montierten Wellenformen (Einzelpruefungen wie Bibliothekszeilen)
// haengen in EINER Registry; gezeichnet wird aus einer einzigen
// requestAnimationFrame-Schleife (siehe wavePump weiter unten).
const waveEntries = new Set();

// Thema-Tokens einmal lesen statt pro Balken: getComputedStyle() erzwingt
// eine Stilberechnung, im alten Code zwei- bis dreimal je Zeichenvorgang.
let THEME_REV = 0;
let _theme = null;
function readThemeTokens() {
  const cs = getComputedStyle(document.body);
  const v = name => cs.getPropertyValue(name).trim();
  _theme = {
    accent: v("--accent") || "#3b82f6",
    wave: v("--wave") || "#9aa4b2",
    dim: v("--dim") || "#888",
    bandLow: v("--wave-band-bass"),
    bandMid: v("--wave-band-mid"),
    bandHigh: v("--wave-band-high"),
    cueMemory: v("--cue-memory") || "#8b95a6",
  };
  THEME_REV++;
  return _theme;
}
const theme = () => _theme || readThemeTokens();

// Hell/Dunkel- oder Designfarbenwechsel entwertet jede vorgerechnete Ebene.
// Gerufen aus applyTheme()/applyAccentColor() (Einstellungen) und bei einem
// Wechsel der Systemeinstellung, solange "auto" gilt.
function invalidateWaveTheme() {
  readThemeTokens();
  for (const entry of waveEntries) entry.layer = null;
  requestWavePaint();
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", invalidateWaveTheme);

// Groesse ohne Layout-Read: der ResizeObserver liefert die Breite frei Haus.
// Vorher las drawWave() bei jedem Zeichnen canvas.clientWidth/clientHeight --
// ein erzwungener Reflow je Zeile je Zeitschritt.
const _waveResizeObserver = new ResizeObserver(list => {
  for (const rec of list) {
    const entry = rec.target._waveEntry;
    if (!entry) continue;
    const box = rec.contentRect;
    entry.w = Math.max(1, Math.round(box.width));
    entry.h = Math.max(1, Math.round(box.height));
    entry.layer = null;                 // Breite geaendert -> neu rechnen
  }
  requestWavePaint();
});

// Eine aufgeklappte Zeile, die aus dem Bild gescrollt ist, muss nicht
// mitlaufen -- der alte repaintWaveViews() zeichnete ausnahmslos alle.
const _waveVisibilityObserver = new IntersectionObserver(list => {
  for (const rec of list) {
    const entry = rec.target._waveEntry;
    if (entry) entry.visible = rec.isIntersecting;
  }
}, {rootMargin: "150px"});

// EIN MutationObserver fuer alle Zeilen. Vorher legte jede aufgeklappte
// Zeile einen eigenen an, der auf document.body mit subtree:true lauschte --
// bei n Zeilen also n Rueckrufe mit je einem contains()-Baumlauf pro
// DOM-Aenderung, und ein volles render() der Tabelle loest Tausende aus.
const _waveUnmountObserver = new MutationObserver(() => {
  if (!waveEntries.size) return;
  for (const entry of [...waveEntries]) {
    if (!entry.root.isConnected) disposeWaveEntry(entry);
  }
});
_waveUnmountObserver.observe(document.body, {childList: true, subtree: true});

// Nimmt einen Eintrag in die Registry auf. entry braucht mindestens
// root/canvas sowie sync() (holt currentTime/duration aus der jeweiligen
// Quelle) und optional syncLabels()/onDispose().
function registerWaveEntry(entry) {
  entry.visible = true;
  entry.dataRev = 0;
  entry.layer = null;
  entry.currentTime = 0;
  entry.duration = NaN;
  const box = entry.canvas.getBoundingClientRect();   // einmalig beim Mounten
  entry.w = Math.max(1, Math.round(box.width)) || 300;
  entry.h = Math.max(1, Math.round(box.height)) || 52;
  entry.canvas._waveEntry = entry;
  waveEntries.add(entry);
  _waveResizeObserver.observe(entry.canvas);
  _waveVisibilityObserver.observe(entry.canvas);
  requestWavePaint();
  return entry;
}

function disposeWaveEntry(entry) {
  waveEntries.delete(entry);
  _waveResizeObserver.unobserve(entry.canvas);
  _waveVisibilityObserver.unobserve(entry.canvas);
  entry.canvas._waveEntry = null;
  // Ebene aktiv freigeben: ein 800x80-Canvas haelt bei dpr 2 rund 0,5 MB.
  if (entry.layerCanvas) { entry.layerCanvas.width = 0; entry.layerCanvas.height = 0; }
  entry.layerCanvas = null; entry.layerCtx = null; entry.layer = null; entry.ctx = null;
  entry.peaks = null; entry.waveform = null; entry.cues = null;
  if (entry.onDispose) entry.onDispose();
}

// Neue Daten (Peaks, Cues, Rekordbox-Wellenform, Dauer) entwerten die Ebene.
function setWaveData(entry, patch) {
  if (!waveEntries.has(entry)) return;
  Object.assign(entry, patch);
  entry.dataRev++;
  entry.layer = null;
  requestWavePaint();
}

// ── Eine Bildschleife fuer alles ────────────────────────────────────────
// Vorher wurde aus audio.ontimeupdate heraus gezeichnet: unregelmaessig
// (~4 Hz, browserabhaengig), ausserhalb des Bildtakts und auch dann, wenn
// das Fenster im Hintergrund liegt. Das war die Ursache der sichtbar
// ruckelnden Abspielposition trotz sauber laufendem Ton. Jetzt speist ein
// einziges requestAnimationFrame die Position direkt aus audio.currentTime
// -- damit sitzt sie pro Bild am Ton, und der Browser drosselt die Schleife
// im Hintergrund von selbst. ontimeupdate ist nur noch fuer die
// Hoerzeit-Statistik zustaendig (siehe listenTrackTick()).
let _waveRaf = 0;

function anyAudioPlaying() {
  if (!queueAudio.paused && !queueAudio.ended) return true;
  for (const p of players.values()) if (p.audio && !p.audio.paused && !p.audio.ended) return true;
  return false;
}

function wavePump() {
  _waveRaf = 0;
  for (const entry of waveEntries) {
    entry.sync();
    paintWaveEntry(entry);
    if (entry.syncLabels) entry.syncLabels();
  }
  renderPlayerProgress();
  if (anyAudioPlaying()) scheduleWavePaint();
}

// Bewusst OHNE document.hidden-Pruefung: ein angefordertes Bild wird im
// Hintergrund vom Browser nur zurueckgestellt, nicht verworfen -- es laeuft,
// sobald die Seite wieder sichtbar ist. Eine eigene Sperre hier wuerde
// dagegen genau die Anforderungen verschlucken, die waehrend der
// Unsichtbarkeit anfallen (eintreffende Peaks, ein Trackwechsel), und die
// Wellenform bliebe danach leer. Dass im Hintergrund keine Rechenzeit
// verbraucht wird, erledigt requestAnimationFrame selbst.
function scheduleWavePaint() {
  if (_waveRaf) return;
  _waveRaf = requestAnimationFrame(wavePump);
}

// Einzelbild anfordern (Pause, Sprung, neue Daten, Themenwechsel).
function requestWavePaint() { scheduleWavePaint(); }

// Nach der Rueckkehr aus dem Hintergrund einmal nachziehen: die Schleife hat
// dort geruht, Position und Zeitanzeige sind stehengeblieben.
document.addEventListener("visibilitychange", () => { if (!document.hidden) requestWavePaint(); });

function playerHTML(r) {
  if (r.gone) {
    return `<div class="path" style="margin-top:10px">${t("player.no_file_at_path")}</div>`;
  }
  if (!apiMode && !r.blobUrl) {
    return `<div class="path" style="margin-top:10px">${t("player.open_via_serve")}</div>`;
  }
  return `<div class="player">
    <button class="pbtn" data-act="toggle" title="${esc(t("action.play_space"))}">${ICONS.play}</button>
    <canvas class="wave" height="80" title="${esc(t("action.seek"))}"></canvas>
    <span class="ptime">–:––</span>
  </div>`;
}

// entry = Player-Eintrag aus players/waveViews (siehe mountDropPlayer/
// mountLibraryPlayer) -- traegt neben den grauen peaks optional cues (Hot/
// Memory Cues aus Rekordbox) und optional waveform (farbige Rekordbox-
// Analysedaten, Stil per Einstellung rekordbox_waveform_style server-seitig
// gewaehlt, siehe rekordbox._read_waveform()). Ohne Rekordbox-Bezug oder ohne
// passende Analysedatei bleibt es bei der grauen Standardanzeige.
// Schluessel der statischen Ebene: aendert sich einer der Bestandteile
// (Groesse, Thema, Daten, Dauer -> Cue-Positionen), wird neu gerechnet --
// sonst nie.
function waveLayerKey(entry, dpr) {
  return `${entry.w}x${entry.h}@${dpr}#${THEME_REV}#${entry.dataRev}`;
}

// Baut NUR die Balken in ein eigenes Canvas. Laeuft beim Mounten, bei
// Breitenaenderung, bei neuen Daten und beim Themenwechsel -- nicht waehrend
// der Wiedergabe. Liefert null, solange noch keine Daten da sind.
//
// Die Cue-Marker gehoeren bewusst NICHT hierher, obwohl auch sie stillstehen:
// bei der grauen Standardwellenform faerbt paintWaveEntry() den gespielten
// Teil der Ebene per "source-atop" um, und das erwischt jedes deckende Pixel
// darin -- also auch die Cue-Marker links vom Abspielpunkt samt der weissen
// Buchstaben auf den Hot-Cue-Faehnchen. Sie werden deshalb pro Bild ueber die
// bereits eingefaerbte Ebene gezeichnet. Das kostet nichts: Cues sind eine
// Handvoll Striche, der teure Teil waren die bis zu 1200 Balken.
function buildWaveLayer(entry, dpr) {
  const w = entry.w, h = entry.h;
  const canvas = entry.layerCanvas || (entry.layerCanvas = document.createElement("canvas"));
  const pw = Math.round(w*dpr), ph = Math.round(h*dpr);
  if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
  const ctx = entry.layerCtx || (entry.layerCtx = canvas.getContext("2d"));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const wf = entry.waveform;
  // recolorable = die graue Standardwellenform, deren gespielter Teil beim
  // Kopieren eingefaerbt werden darf. Die bunten Rekordbox-Stile behalten
  // ihre echten Farben und bekommen stattdessen die Positionslinie.
  let recolorable = false;
  if (wf && wf.style === "rgb") {
    drawRgbWave(ctx, w, h, wf);
  } else if (wf && wf.style === "3band") {
    drawBandWave(ctx, w, h, wf);
  } else if (entry.peaks && entry.peaks.length) {
    drawPlainWave(ctx, w, h, entry.peaks);
    recolorable = true;
  } else {
    return null;
  }

  entry.layerRecolorable = recolorable;
  entry.layerKey = waveLayerKey(entry, dpr);
  return canvas;
}

// Zeichnen pro Bild -- konstanter Aufwand, egal wie viele Rohspalten die
// Wellenform hat. entry.currentTime/duration setzt entry.sync() (siehe
// mountDropPlayer/mountLibraryPlayer), damit dieselbe Funktion das eigene
// Audio() der Einzelpruefungen wie den globalen queueAudio bedient.
function paintWaveEntry(entry) {
  if (!entry.visible) return;
  const dpr = window.devicePixelRatio || 1;
  const canvas = entry.canvas, w = entry.w, h = entry.h;
  const pw = Math.round(w*dpr), ph = Math.round(h*dpr);
  if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }

  const ctx = entry.ctx || (entry.ctx = canvas.getContext("2d"));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (!entry.layer || entry.layerKey !== waveLayerKey(entry, dpr)) {
    // Eigener try/catch wie zuvor: ein Fehler beim Rechnen darf Play-Knopf
    // und Zeitanzeige (entry.syncLabels) nicht mit sich reissen.
    try { entry.layer = buildWaveLayer(entry, dpr); }
    catch (err) { console.error(err); entry.layer = null; entry.layerKey = waveLayerKey(entry, dpr); }
  }
  if (!entry.layer) {
    ctx.fillStyle = theme().dim;
    ctx.font = "11px -apple-system, sans-serif";
    ctx.fillText(t("player.envelope_calculating"), 4, h/2);
    return;
  }

  ctx.drawImage(entry.layer, 0, 0, w, h);

  const d = entry.duration;
  const frac = (isFinite(d) && d > 0) ? Math.min(1, Math.max(0, entry.currentTime / d)) : 0;

  if (entry.layerRecolorable && frac > 0) {
    // Gespielten Teil einfaerben, ohne die Balken erneut zu zeichnen:
    // "source-atop" faerbt nur die bereits deckenden Pixel der Ebene.
    // MUSS vor den Cue-Markern laufen -- sonst faerbt es auch die mit ein
    // (siehe buildWaveLayer(), warum die Cues nicht in der Ebene stecken).
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, frac*w, h); ctx.clip();
    ctx.globalCompositeOperation = "source-atop";
    ctx.fillStyle = theme().accent;
    ctx.fillRect(0, 0, frac*w, h);
    ctx.restore();
  }

  // Cue-Marker in ihren echten Rekordbox-Farben, ueber der fertig
  // eingefaerbten Wellenform. Gleiche Reihenfolge wie vor dem Umbau.
  if (isFinite(d) && d > 0 && entry.cues && entry.cues.length) {
    drawCueMarkers(ctx, w, h, entry.cues, d);
  }

  // Zuoberst, NACH den Cue-Markern: die Abspielposition soll Cue-Marker an
  // derselben Stelle ueberdecken. Nur bei den bunten Rekordbox-Stilen -- bei
  // der grauen Wellenform zeigt die Einfaerbung oben die Position an.
  if (!entry.layerRecolorable) drawPlayhead(ctx, w, h, frac);
}

// Einfarbig gebacken -- die Fallunterscheidung nach frac, die frueher den
// ganzen Balkendurchlauf pro Zeitschritt erzwang, uebernimmt jetzt das
// Einfaerben in paintWaveEntry().
function drawPlainWave(ctx, w, h, peaks) {
  const mid = h/2, n = peaks.length, bw = w/n;
  const barW = Math.max(bw - 0.5, 0.6);
  ctx.fillStyle = theme().wave;
  for (let i = 0; i < n; i++) {
    const bh = Math.max(1, peaks[i] * (h - 6));
    ctx.fillRect(i*bw, mid - bh/2, barW, bh);
  }
}

// Duenne Fortschrittslinie statt (wie bei drawPlainWave) die Balkenfarbe hart
// zu tauschen -- ein Farbwechsel wuerde die echten Rekordbox-Farben der
// bunten Stile zunichtemachen. Bewusst schwarz statt --accent (haelt gegen
// jede Balkenfarbe Kontrast) und von paintWaveEntry() als Letztes gezeichnet,
// also auf die bereits kopierte Ebene mit den Cue-Markern -- die
// Abspielposition soll Cue-Marker an derselben Stelle ueberdecken, nicht
// umgekehrt.
function drawPlayhead(ctx, w, h, frac) {
  const x = Math.max(0, Math.min(w - 1, frac * w));
  ctx.strokeStyle = "#000";
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
}

function _rgbToHsl(r, g, b) {
  r/=255; g/=255; b/=255;
  const max = Math.max(r,g,b), min = Math.min(r,g,b);
  let h=0, s=0, l=(max+min)/2;
  if (max !== min) {
    const d = max-min;
    s = l > 0.5 ? d/(2-max-min) : d/(max+min);
    if (max === r) h = (g-b)/d + (g<b?6:0);
    else if (max === g) h = (b-r)/d + 2;
    else h = (r-g)/d + 4;
    h /= 6;
  }
  return [h, s, l];
}
function _hslToRgb(h, s, l) {
  if (s === 0) { const v = Math.round(l*255); return [v,v,v]; }
  const q = l < 0.5 ? l*(1+s) : l+s-l*s, p = 2*l-q;
  const hue2rgb = (p, q, t) => {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1/6) return p + (q-p)*6*t;
    if (t < 1/2) return q;
    if (t < 2/3) return p + (q-p)*(2/3-t)*6;
    return p;
  };
  return [Math.round(hue2rgb(p,q,h+1/3)*255), Math.round(hue2rgb(p,q,h)*255), Math.round(hue2rgb(p,q,h-1/3)*255)];
}
// Kraeftigt matte/dunkle Rohfarben auf ein leuchtendes Mindestmass an
// Saettigung/Helligkeit -- die aus PWV4 dekodierten RGB-Rohwerte sind fuer
// sich genommen oft zu dunkel/matt fuer den leuchtenden Rekordbox-Look
// (Luminanz-Skalierung im Rohformat nicht abschliessend geklaert, an einer
// Vorschau gegen ein echtes Rekordbox-Referenzbild geprueft).
function _vividize(r, g, b) {
  if (r === 0 && g === 0 && b === 0) return [0, 0, 0];
  let [h, s, l] = _rgbToHsl(r, g, b);
  s = Math.max(s, 0.75);
  l = Math.min(Math.max(l, 0.42), 0.62);
  return _hslToRgb(h, s, l);
}

// Downsampling gegen Pixel-Rauschen: RGB/3-Band liefern 1200 Rohspalten,
// die Canvas-Breite im Player liegt aber meist deutlich darunter -- ohne
// dies wuerde `bw = w/n` unter 1px fallen und benachbarte Balken mit
// unterschiedlicher Farbe ueberlappen/uebermalen ("pixelig"). Nie mehr
// Buckets als Rohspalten (kein Hochskalieren erfinden).
// Typisierte Felder statt Array: ein Puffer statt tausender Boxed Numbers.
function _resampleSeries(arr, buckets) {
  const n = arr.length;
  buckets = Math.max(1, Math.min(buckets, n));
  const out = new Float32Array(buckets);
  for (let i = 0; i < buckets; i++) {
    const start = Math.floor(i * n / buckets), end = Math.max(start + 1, Math.floor((i + 1) * n / buckets));
    let sum = 0;
    for (let j = start; j < end; j++) sum += arr[j];
    out[i] = sum / (end - start);
  }
  return out;
}
// Farben flach als [r,g,b,r,g,b,...] statt als Feld von [r,g,b]-Feldern --
// ein Puffer statt eines kleinen Objekts je Bucket.
function _resampleColorSeries(arr, buckets) {
  const n = arr.length;
  buckets = Math.max(1, Math.min(buckets, n));
  const out = new Float32Array(buckets * 3);
  for (let i = 0; i < buckets; i++) {
    const start = Math.floor(i * n / buckets), end = Math.max(start + 1, Math.floor((i + 1) * n / buckets));
    let r = 0, g = 0, b = 0;
    for (let j = start; j < end; j++) { r += arr[j][0]; g += arr[j][1]; b += arr[j][2]; }
    const cnt = end - start;
    out[i*3] = r / cnt; out[i*3+1] = g / cnt; out[i*3+2] = b / cnt;
  }
  return out;
}

// Zusaetzliche leichte Glaettung UEBER die bereits downgesampleten Buckets
// (gleitender Mittelwert, Radius in Buckets) -- an einer Vorschau mit
// einstellbarem Regler gegen eine Flicker-Kennzahl geprueft; Radius 1
// entspricht der dort gewaehlten Staerke (spuerbar ruhiger, ohne Transienten
// wegzumitteln).
const WAVE_SMOOTH_RADIUS = 1;
function _movingAverage(arr, radius) {
  if (radius <= 0) return arr;
  const n = arr.length, out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - radius), hi = Math.min(n - 1, i + radius);
    let sum = 0;
    for (let j = lo; j <= hi; j++) sum += arr[j];
    out[i] = sum / (hi - lo + 1);
  }
  return out;
}
// Arbeitet auf dem flachen [r,g,b,...]-Puffer aus _resampleColorSeries().
function _movingAverageColor(arr, radius) {
  if (radius <= 0) return arr;
  const n = arr.length / 3, out = new Float32Array(arr.length);
  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - radius), hi = Math.min(n - 1, i + radius);
    let r = 0, g = 0, b = 0;
    for (let j = lo; j <= hi; j++) { r += arr[j*3]; g += arr[j*3+1]; b += arr[j*3+2]; }
    const cnt = hi - lo + 1;
    out[i*3] = r / cnt; out[i*3+1] = g / cnt; out[i*3+2] = b / cnt;
  }
  return out;
}

// RGB (.EXT, PWV4): Hoehe aus der glatteren Huellkurve (wf.height, back-
// Spalte in rekordbox._read_waveform()), Farbe aus der echten PWV4-Front-
// farbe, per _vividize() aufgehellt. Reine front-Hoehe wirkte in der
// Vorschau zu klein/jittrig, deshalb der Mix aus back-Hoehe + front-Farbe.
// Vor dem Zeichnen auf die tatsaechliche Canvas-Breite downgesamplet (siehe
// _resampleSeries) plus leichte Nachglaettung (siehe WAVE_SMOOTH_RADIUS).
function drawRgbWave(ctx, w, h, wf) {
  // Nie mehr Buckets als Rohspalten anfordern -- _resampleSeries/
  // _resampleColorSeries kappen intern selbst auf wf.height.length, ohne
  // dieses Min hier liefe die Zeichenschleife unten ueber das Ende der
  // zurueckgegebenen Puffer hinaus (bei Canvas-Breiten >1200px, also ueber
  // der Rohaufloesung). Der try/catch in paintWaveEntry() faengt einen
  // Fehler hier zwar ab, ohne Knopf und Zeitanzeige mitzureissen -- die
  // Wellenform bliebe aber leer.
  const buckets = Math.max(1, Math.min(Math.round(w), wf.height.length));
  const heights = _movingAverage(_resampleSeries(wf.height, buckets), WAVE_SMOOTH_RADIUS);
  const colors = _movingAverageColor(_resampleColorSeries(wf.color, buckets), WAVE_SMOOTH_RADIUS);
  const mid = h/2, bw = w/buckets;
  const barW = bw >= 3 ? Math.max(bw - 0.5, 0.6) : bw + 0.5;
  for (let i = 0; i < buckets; i++) {
    const [r, g, b] = _vividize(colors[i*3], colors[i*3+1], colors[i*3+2]);
    const bh = Math.max(1, heights[i] * (h - 6));
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fillRect(i*bw, mid - bh/2, barW, bh);
  }
}

// 3-Band (.2EX, PWV6): gestapelt (nicht ueberlagert/transparent), von der
// Mittellinie nach aussen, Reihenfolge innen->aussen Tief->Mitten->Hoch
// (Quelle: Deep Symmetry ANLZ-Referenz, von pyrekordbox selbst zitiert).
// Gleiches Downsampling + Nachglaettung wie drawRgbWave.
function drawBandWave(ctx, w, h, wf) {
  // Gleiche Kappung wie in drawRgbWave, gleicher Grund -- ohne sie liefe die
  // Zeichenschleife bei Canvas-Breiten >1200px ueber das Ende der
  // resampleten Arrays hinaus (dort dann NaN-Rechtecke statt einer
  // Exception, aber ebenso eine leere Luecke am rechten Rand).
  const buckets = Math.max(1, Math.min(Math.round(w), wf.low.length));
  const low = _movingAverage(_resampleSeries(wf.low, buckets), WAVE_SMOOTH_RADIUS);
  const midS = _movingAverage(_resampleSeries(wf.mid, buckets), WAVE_SMOOTH_RADIUS);
  const high = _movingAverage(_resampleSeries(wf.high, buckets), WAVE_SMOOTH_RADIUS);
  const mid = h/2, bw = w/buckets;
  const barW = bw >= 3 ? Math.max(bw - 0.5, 0.6) : bw + 0.5;
  const th = theme();
  const avail = (h - 6) / 2;
  // Bandweise statt spaltenweise: drei fillStyle-Wechsel statt 3 x buckets.
  // Der Stapel waechst dabei ueber cursor[] von innen nach aussen weiter,
  // die Reihenfolge Tief -> Mitten -> Hoch bleibt also unveraendert.
  const cursor = new Float32Array(buckets);
  const bands = [[low, th.bandLow], [midS, th.bandMid], [high, th.bandHigh]];
  for (const [series, color] of bands) {
    ctx.fillStyle = color;
    for (let i = 0; i < buckets; i++) {
      const bh = series[i] * avail, x = i*bw, c = cursor[i];
      ctx.fillRect(x, mid - c - bh, barW, bh);
      ctx.fillRect(x, mid + c, barW, bh);
      cursor[i] = c + bh;
    }
  }
}

// Hot Cues als farbige Fähnchen mit Buchstabe (A-H). Memory Cues (inkl.
// Loop-Markierungen, siehe rekordbox.get_track_extras) als duenne, volle
// Linie in ihrer in Rekordbox eingestellten Farbe -- ohne gesetzte Farbe
// neutral in --cue-memory.
function drawCueMarkers(ctx, w, h, cues, duration) {
  ctx.font = "11px -apple-system, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const memoryColor = theme().cueMemory;
  for (const cue of cues) {
    const x = Math.min(w - 1, Math.max(0, (cue.position_s / duration) * w));
    if (cue.kind === "hot") {
      const [r, g, b] = cue.color;
      ctx.strokeStyle = `rgb(${r},${g},${b})`;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.fillRect(x - 9, 0, 18, 14);
      ctx.fillStyle = "#fff";
      ctx.fillText(cue.label, x, 1.5);
    } else {
      const col = cue.color ? `rgb(${cue.color[0]},${cue.color[1]},${cue.color[2]})`
                             : memoryColor;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      // Dreieck an der Spitze, wie bei Hot Cues als Klickziel erkennbar.
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.moveTo(x - 7, 0); ctx.lineTo(x + 7, 0); ctx.lineTo(x, 10);
      ctx.closePath(); ctx.fill();
    }
  }
}

// Trifft ein Klick eine Cue-Markierung (mit etwas Toleranz)? Liefert den
// Cue oder null.
function cueHitAt(entry, canvas, clientX, duration) {
  if (!entry.cues || !entry.cues.length || !isFinite(duration) || duration <= 0) return null;
  const rect = canvas.getBoundingClientRect();
  const clickX = clientX - rect.left, tolerancePx = 9; // an die groesseren Marker (drawCueMarkers) angepasst
  for (const cue of entry.cues) {
    const x = (cue.position_s / duration) * rect.width;
    if (Math.abs(clickX - x) <= tolerancePx) return cue;
  }
  return null;
}

// Ersetzt durch den Themen-Cache (theme()/readThemeTokens() weiter oben) --
// getComputedStyle() erzwingt eine Stilberechnung und hatte im Zeichenpfad
// nichts verloren.

async function peaksForLibrary(path) {
  const res = await fetch("/api/waveform?path=" + encodeURIComponent(path));
  if (!res.ok) throw new Error("Waveform " + res.status);
  return (await res.json()).peaks;
}

// Cues + farbige Waveform aus Rekordbox, falls der Track dort analysiert
// ist. {"available": false}, wenn nicht -- das ist der Normalfall.
async function rekordboxExtrasFor(path) {
  const res = await fetch("/api/rekordbox-extras?path=" + encodeURIComponent(path));
  if (!res.ok) throw new Error("Rekordbox-Extras " + res.status);
  return await res.json();
}

// Für abgelegte Dateien kann der Server nichts berechnen — die Datei liegt
// nur im Browser. Also hier dekodieren.
// Ein geteilter OfflineAudioContext statt eines neuen AudioContext je Datei:
// Browser deckeln die Zahl gleichzeitiger AudioContexts (rund 6) und jeder
// haengt an der Audio-Hardware -- ein Fehler vor dem alten ctx.close() liess
// einen davon dauerhaft stehen. Ein OfflineAudioContext dekodiert genauso,
// belegt aber kein Ausgabegeraet.
let _decodeCtx = null;
const decodeCtx = () => _decodeCtx
  || (_decodeCtx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 1, 44100));

async function peaksFromFile(file, buckets = 800) {
  // decodeAudioData uebernimmt den Puffer (danach detached) -- Datei und
  // dekodiertes PCM liegen so nicht gleichzeitig im Speicher.
  const buf = await decodeCtx().decodeAudioData(await file.arrayBuffer());
  const data = buf.getChannelData(0);
  const step = Math.floor(data.length / buckets) || 1;
  const out = new Float32Array(buckets);
  let top = 0;

  // Bewusst in einem Rutsch: an einem 5½-Minuten-Track gemessen (14,7 Mio.
  // Samples) kostet diese Schleife 29 ms, waehrend decodeAudioData darueber
  // 1130 ms braucht -- und das laeuft ohnehin ausserhalb des Hauptthreads.
  // Eine Stueckelung ueber setTimeout war hier ein Fehlgriff: sie kostete im
  // Hintergrund-Tab mehrere Sekunden (dort auf einen Aufruf je Sekunde
  // gedrosselt) und sparte nicht einmal ein volles Bild ein.
  for (let i = 0; i < buckets; i++) {
    let peak = 0;
    const end = Math.min((i+1)*step, data.length);
    for (let j = i*step; j < end; j++) {
      const v = data[j] < 0 ? -data[j] : data[j];
      if (v > peak) peak = v;
    }
    out[i] = peak;
    if (peak > top) top = peak;
  }

  if (top > 0) for (let k = 0; k < buckets; k++) out[k] /= top;
  return out;
}

// Der Browser meldet jeden Fehler an der Quelle als "The element has no
// supported sources" — ob die Datei fehlt, unlesbar ist oder die Verbindung
// abgerissen ist. Also einmal selbst nachsehen und Klartext melden.
async function playFailed(audio, err) {
  const src = audio.currentSrc || audio.src || "";
  const report = (msg) => note(msg, true);
  const plain = () => report(t("player.playback_failed", {msg: err.message}));
  if (!apiMode || src.indexOf("/api/audio") < 0) { plain(); return; }
  try {
    const res = await fetch(src, {headers: {Range: "bytes=0-65535"}});
    if (!res.ok && res.status !== 206) {
      report(t("player.playback_failed", {msg: (await res.text()).trim() || res.status}));
      return;
    }
    const buf = await res.arrayBuffer();
    if (!buf.byteLength) {
      report(t("player.playback_failed_corrupt"));
      return;
    }
    // Die Quelle liest sich sauber — der erste Versuch lief also in eine
    // abgerissene Verbindung. Neu laden und ein zweites Mal versuchen.
    audio.load();
    audio.play().catch(e => report(t("player.playback_failed", {msg: e.message})));
  } catch (e) {
    plain();
  }
}

// Player fuer die Einzelpruefungen (Drops) -- eigenes, unabhaengiges
// Audio()-Objekt je aufgeklappter Zeile. Bewusst NICHT an die Warteschlange/
// den globalen Player (queueAudio, siehe weiter unten) angebunden: Drops
// haben keine DB-Zeile/Position, siehe CLAUDE.md. Damit nicht gleichzeitig
// ein Drop UND der globale Player laufen, pausiert jeder Play-Start hier
// zusaetzlich queueAudio (Gegenrichtung: queueAudio.onplay pausiert alle
// Drop-Player, siehe dort).
function mountDropPlayer(root, r) {
  const box = root.querySelector(".player");
  if (!box) return;
  const key = r.blobUrl || r.p;
  const canvas = box.querySelector("canvas.wave");
  const btn = box.querySelector("[data-act=toggle]");
  const time = box.querySelector(".ptime");

  const audio = new Audio(r.blobUrl || ("/api/audio?path=" + encodeURIComponent(r.p)));
  audio.preload = "metadata";
  const entry = registerWaveEntry({
    root: root, canvas: canvas, audio: audio,
    peaks: null, cues: null, waveform: null,
    // Holt Position und Dauer aus der eigenen Quelle (siehe wavePump()).
    sync() { this.currentTime = audio.currentTime; this.duration = audio.duration; },
    // Zeitanzeige nur bei echtem Sekundenwechsel anfassen -- ein
    // textContent-Schreiben pro Bild waere 60 Layout-Invalidierungen je
    // Sekunde fuer eine Anzeige, die sich einmal pro Sekunde aendert.
    syncLabels() {
      const sec = Math.floor(audio.currentTime);
      if (sec === this._sec) return;
      this._sec = sec;
      time.textContent = fmtTime(sec) + " / " +
        (isFinite(audio.duration) ? fmtTime(Math.floor(audio.duration)) : fmtTime(r.du));
    },
    // Wird die Zeile zugeklappt, muss die Wiedergabe enden (siehe
    // _waveUnmountObserver).
    onDispose() {
      listenFinish();
      audio.pause();
      players.delete(key);
    },
  });
  players.set(key, entry);

  (r.file ? peaksFromFile(r.file) : peaksForLibrary(r.p))
    .then(p => setWaveData(entry, {peaks: p}))
    .catch(() => setWaveData(entry, {peaks: []}));

  // Per Drag & Drop geprüfte Dateien ohne Bibliothekspfad koennen nicht in
  // Rekordbox nachgeschlagen werden.
  if (!r.file) {
    rekordboxExtrasFor(r.p)
      .then(extras => {
        if (extras && extras.available) {
          setWaveData(entry, {cues: extras.cues || [], waveform: extras.waveform || null});
        }
      })
      .catch(() => {});
  }

  btn.onclick = ev => {
    ev.stopPropagation();
    if (audio.paused) {
      for (const [k, p] of players) if (k !== key) p.audio.pause();
      queueAudio.pause();
      audio.play().catch(err => playFailed(audio, err));
    } else {
      audio.pause();
    }
  };
  // Peaks/Cues koennen eintreffen, bevor die Audio-Metadaten (Dauer) geladen
  // sind -- ohne Dauer laesst sich kein Cue-Marker platzieren, drawWave
  // uebersprang sie dann. Ein Bild anfordern genuegt: die Marker werden
  // ohnehin pro Bild gezeichnet und entry.sync() hat die Dauer dann dabei.
  // Ohne diesen Handler zeichnet bei einem pausierten Player nichts mehr
  // nach, die Cues blieben also unsichtbar (Fahnen faelschlich "nicht
  // platziert").
  audio.onloadedmetadata = () => requestWavePaint();
  audio.onplay  = () => {
    btn.innerHTML = ICONS.pause; activeKey = key;
    listenTrackStart(r, audio); scheduleWavePaint();
  };
  audio.onpause = () => { btn.innerHTML = ICONS.play; requestWavePaint(); };
  audio.onseeked = () => requestWavePaint();
  audio.onended = () => {
    listenFinish();
    btn.innerHTML = ICONS.play;
    requestWavePaint();
    // Naechste Zeile in der aktuellen (gefilterten/sortierten) Ansicht
    // automatisch weiterspielen -- gleiches Muster wie der data-play-Knopf:
    // erst aufklappen (mountDropPlayer legt dabei den Toggle-Knopf neu an)
    // falls noch nicht geschehen, dann dessen Play-Knopf klicken (uebernimmt
    // auch das Pausieren aller anderen Player).
    const nextRow = root.nextElementSibling;
    if (!nextRow || !nextRow.classList.contains("row")) return;
    let det = nextRow.nextElementSibling;
    if (!det || !det.classList.contains("detail")) { nextRow.click(); det = nextRow.nextElementSibling; }
    const pb = det && det.querySelector("[data-act=toggle]");
    if (pb) pb.click();
  };
  // Nur noch Hoerzeit-Statistik -- gezeichnet wird aus wavePump().
  audio.ontimeupdate = () => listenTrackTick(audio);
  canvas.onclick = ev => {
    ev.stopPropagation();
    const hit = cueHitAt(entry, canvas, ev.clientX, audio.duration);
    if (hit) {
      for (const [k, p] of players) if (k !== key) p.audio.pause();
      queueAudio.pause();
      audio.currentTime = hit.position_s;
      audio.play().catch(err => playFailed(audio, err));
      requestWavePaint();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
    if (isFinite(audio.duration)) { audio.currentTime = frac * audio.duration; requestWavePaint(); }
  };
}

// ── Globaler Player-Balken (Issue #13, seit Issue #16 layoutuebergreifend) ─
// Getrennt von players/mountDropPlayer oben (das bleibt den Einzelpruefungen
// vorbehalten): in der Bearbeiten-Ansicht klappt ein Zeilenklick nur die
// Wellenform auf (siehe render()/mountLibraryPlayer() weiter unten), Klick
// auf deren Play-Knopf oder den "Anhoeren"-Knopf laedt den Track aber in
// genau diesen EINEN geteilten Audio-Knoten -- denselben, den auch die
// Player-Ansicht bedient. Die Warteschlange haengt an eigenen Arrays --
// unabhaengig von state.shown/Pagination und davon, was gerade im DOM steht.
const queueAudio = new Audio();
queueAudio.preload = "metadata";
// tracks = Pool der NOCH NICHT gespielten Tracks (Anzeige-/Drag&Drop-
// Reihenfolge; bei Shuffle wird daraus zufaellig statt der Reihe nach
// gezogen). current = gerade geladener Track oder null. history = bereits
// gespielte Tracks, chronologisch (aeltester zuerst) -- eigenes Array statt
// nur "abgeblendet in derselben Liste", speist den Tab "Zuletzt gehoert" im
// Warteschlange-Popup (siehe renderQueuePopup()). Absichtlich unabhaengig
// von einzelnen Warteschlangen-Sitzungen: startQueueFrom() ersetzt nur den
// Pool, die Historie waechst ueber mehrere Klicks hinweg weiter, wie ein
// echter Hoerverlauf.
let queueState = {
  tracks: [],
  current: null,
  history: [],
  manual: false,         // true nach manuellem Hinzufuegen/Umsortieren -> 25er-Deckel entfaellt
  shuffle: false, repeatTrack: false, repeatList: false,
};
// Aus Performance-Gruenden gedeckelt (Absprache mit Nutzer) -- gilt nur fuer
// den automatisch erzeugten Pool, nicht fuer einen manuell bearbeiteten
// (addToQueue()/playNext()/Drag&Drop setzen queueState.manual).
const QUEUE_AUTO_LIMIT = 25;

// Die Historie waechst ueber Sitzungen hinweg weiter (siehe Kommentar an
// queueState) und wird bei JEDEM Trackwechsel komplett nach localStorage
// geschrieben -- ohne Deckel wird daraus nach ein paar hundert Titeln ein
// spuerbarer, synchroner Schreibvorgang mitten im Wechsel. Aeltestes faellt
// vorn heraus; der Tab "Zuletzt gehoert" zeigt ohnehin die juengsten zuerst.
const QUEUE_HISTORY_LIMIT = 200;
function pushQueueHistory(r) {
  queueState.history.push(r);
  const over = queueState.history.length - QUEUE_HISTORY_LIMIT;
  if (over > 0) queueState.history.splice(0, over);
}

const currentQueueTrack = () => queueState.current;

// Neue, automatisch erzeugte Warteschlange ab der angeklickten Zeile --
// ersetzt den Pool (auch einen manuell bearbeiteten) komplett, laesst die
// Historie aber unangetastet (siehe Kommentar oben an queueState). Wird vom
// Zeilen-/Play-Klick in der Player-Ansicht sowie vom Wellenform-Toggle/-Klick
// und dem "Anhoeren"-Knopf in der Bearbeiten-Ansicht aufgerufen (siehe
// render()/mountLibraryPlayer()). Feinere Regeln, WELCHE Aktionen Tracks
// stattdessen gezielt in eine bestehende Warteschlange legen, sind bewusst
// nur mit addToQueue()/playNext() (Knoepfe je Zeile) abgedeckt -- der Rest
// ist laut Absprache eine spaetere Verfeinerung.
function startQueueFrom(r) {
  if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return; }
  const rows = filtered().filter(x => !x.gone);
  const idx = rows.findIndex(x => x.i === r.i);
  if (idx === -1) return;
  if (queueState.current) pushQueueHistory(queueState.current);
  queueState.tracks = rows.slice(idx + 1, idx + QUEUE_AUTO_LIMIT);
  queueState.manual = false;
  loadQueueTrack(r);
  // Gleiche visuelle Markierung wie bei Pfeiltasten-Navigation (Klasse
  // .kbcursor, siehe setCursorRow() weiter unten) -- ein angeklickter/
  // abgespielter Track soll genauso hervorgehoben bleiben wie einer, zu dem
  // man sich mit den Pfeiltasten bewegt hat. Bewusst nur hier (direkter
  // Klick/Play auf eine bestimmte Zeile), nicht in loadQueueTrack() selbst --
  // sonst wuerde auch ein automatisches Vorruecken (Naechster-Knopf, Ende
  // eines Titels) die Tabelle stumm verschieben, obwohl niemand eine Zeile
  // angeklickt hat.
  setCursorRow(r.i);
}

// "Zur Warteschlange hinzufuegen"-Knopf je Zeile -- haengt hinten an, ohne
// eine laufende Wiedergabe zu unterbrechen. Bewusst OHNE Duplikat-Pruefung:
// ein Track laesst sich beliebig oft einreihen (auch wenn er schon im Pool
// steht), und ein Treffer nur in der Historie (bereits gespielt) zaehlt
// dafuer erst recht nicht als "schon drin" -- Pool und Historie sind
// getrennte Arrays, es gibt also nichts, das dedupliziert werden muesste.
// btn optional, fuer die kurze Erfolgsrueckmeldung (gleiches Muster wie
// revealBtn/flashBtn bei anderen Zeilen-Aktionen: Icon kurz auf "erledigt"
// umschalten statt Toast-Spam bei mehreren Klicks hintereinander).
function addToQueue(r, btn) {
  if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return; }
  if (r.gone) return;
  queueState.tracks.push(r);
  queueState.manual = true;
  renderPlayerBar();
  renderQueuePopup();
  flashDoneBtn(btn);
}

// "Als naechstes abspielen"-Knopf je Zeile (neben Anhören) -- reiht VORNE im
// Pool ein statt hinten wie addToQueue(), der Track ist damit garantiert der
// naechste nach dem aktuellen. Mehrfaches Klicken (auch auf denselben
// Track) reiht jedes Mal neu vorne ein, ohne Dedup-Pruefung -- wie bei
// addToQueue() aus Absprache mit dem Nutzer.
function playNext(r, btn) {
  if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return; }
  if (r.gone) return;
  queueState.tracks.unshift(r);
  queueState.manual = true;
  renderPlayerBar();
  renderQueuePopup();
  flashDoneBtn(btn);
}

function flashDoneBtn(btn) {
  if (!btn) return;
  btn.classList.add("done");
  setTimeout(() => btn.classList.remove("done"), 900);
}

// Setzt r als aktuell gespielten Track und startet die Wiedergabe -- reine
// Zuweisung, OHNE die Historie/den Pool selbst anzufassen. Aufrufer
// entscheiden bewusst selbst, wohin der bisherige queueState.current wandert
// (History bei normalem Vorruecken, zurueck in den Pool bei "Vorheriger" --
// siehe advanceFromPool()/queuePrev()), deshalb kein impliziter Seiteneffekt
// hier.
function loadQueueTrack(r) {
  if (!r) return;
  queueState.current = r;
  queueAudio.src = "/api/audio?path=" + encodeURIComponent(r.p);
  queueAudio.play().catch(err => playFailed(queueAudio, err));
  renderPlayerBar();
  renderQueuePopup();
}

// Zwei Tracks gelten als "quasi identisch" (z.B. "Get Busy" und
// "Get Busy (Intro)"), wenn derselbe Interpret UND der Titel textlich zu
// mindestens QUEUE_TITLE_SIMILARITY_THRESHOLD uebereinstimmt. Aehnlichkeit
// per Dice-Koeffizient auf Buchstaben-Bigrammen (bibliotheksfrei, robust
// gegen angehaengte Klammer-Zusaetze wie "(Remix)"/"(Radio Edit)", da ein
// gemeinsamer Titelanfang genug gemeinsame Bigramme liefert).
const QUEUE_TITLE_SIMILARITY_THRESHOLD = 0.55;
function bigrams(s) {
  const out = [];
  for (let i = 0; i < s.length - 1; i++) out.push(s.slice(i, i + 2));
  return out;
}
function titleSimilarity(a, b) {
  const ba = bigrams(a), bb = bigrams(b);
  if (!ba.length || !bb.length) return a === b ? 1 : 0;
  const counts = new Map();
  for (const g of ba) counts.set(g, (counts.get(g) || 0) + 1);
  let matches = 0;
  for (const g of bb) {
    const c = counts.get(g) || 0;
    if (c > 0) { matches++; counts.set(g, c - 1); }
  }
  return (2 * matches) / (ba.length + bb.length);
}
function isNearDuplicateTrack(a, b) {
  const artistA = (a.a || "").trim().toLowerCase();
  const artistB = (b.a || "").trim().toLowerCase();
  if (!artistA || artistA !== artistB) return false;
  const titleA = (a.t || "").trim().toLowerCase();
  const titleB = (b.t || "").trim().toLowerCase();
  return titleA === titleB || titleSimilarity(titleA, titleB) >= QUEUE_TITLE_SIMILARITY_THRESHOLD;
}

// Die letzten QUEUE_ANTI_REPEAT_WINDOW gespielten Tracks (aktueller +
// Rest aus der Historie), gegen die die Zufallswiedergabe beim naechsten
// Ziehen auf Naehe prueft.
const QUEUE_ANTI_REPEAT_WINDOW = 24;
function recentQueueTracks() {
  const recent = [];
  if (queueState.current) recent.push(queueState.current);
  recent.push(...queueState.history.slice(-(QUEUE_ANTI_REPEAT_WINDOW - 1)));
  return recent;
}

// Naechsten Track aus dem Pool ziehen (bei Shuffle zufaellig, sonst vorne)
// und den bisherigen aktuellen Track in die Historie schieben. Im
// Shuffle-Zweig werden Kandidaten gemieden, die zu einem der letzten QUEUE_ANTI_REPEAT_WINDOW
// gespielten Tracks "quasi identisch" sind (siehe isNearDuplicateTrack()) --
// besteht der Pool nur aus solchen Naeherungen, greift der Fallback auf den
// vollen Pool, damit die Wiedergabe nicht stehen bleibt.
function advanceFromPool() {
  const pool = queueState.tracks;
  let idx = 0;
  if (queueState.shuffle) {
    const recent = recentQueueTracks();
    const ok = pool.map((_, i) => i).filter(i => !recent.some(rt => isNearDuplicateTrack(rt, pool[i])));
    const candidates = ok.length ? ok : pool.map((_, i) => i);
    idx = candidates[Math.floor(Math.random() * candidates.length)];
  }
  const next = pool.splice(idx, 1)[0];
  if (queueState.current) pushQueueHistory(queueState.current);
  loadQueueTrack(next);
}

// Ungewichtete Ziehung ohne Zuruecklegen -- gleiches Fisher-Yates-Muster wie
// applySmartLimit()'s "random"-Zweig, hier als eigene Funktion, weil
// extendQueueAutomatically() sie zusaetzlich braucht.
function pickRandom(rows, n) {
  const shuffled = [...rows];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, n);
}

// Haengt QUEUE_AUTO_LIMIT frische, zufaellig gezogene Tracks aus der
// aktuellen Filteransicht an den (gerade leer gewordenen) Pool -- Aufrufer
// ist ausschliesslich queueNext(), wenn die Warteschlange natuerlich zu Ende
// gespielt wurde und NICHT auf Listen-Wiederholung steht (siehe dort).
// Bereits Gehoertes (Historie + aktueller Track) wird nach Moeglichkeit
// gemieden, damit sich das "Radio" nicht sofort wiederholt -- ist der
// Filter dafuer zu klein, gilt lieber Wiederholung als gar kein Nachschub.
// Setzt bewusst manual=false wie startQueueFrom(): ein automatisch gezogener
// Pool ist nicht "manuell kuratiert".
function extendQueueAutomatically() {
  const candidates = filtered().filter(x => !x.gone);
  if (!candidates.length) return false;
  const recent = new Set(queueState.history.map(x => x.i));
  if (queueState.current) recent.add(queueState.current.i);
  const fresh = candidates.filter(x => !recent.has(x.i));
  queueState.tracks = pickRandom(fresh.length ? fresh : candidates, QUEUE_AUTO_LIMIT);
  // Sicherheitsnetz fuer den Fall, dass advanceFromPool() den neuen Pool
  // (z.B. bei ausgeschalteter Zufallswiedergabe) vorne abgreift: Position 0
  // darf zu keinem der letzten QUEUE_ANTI_REPEAT_WINDOW gespielten Tracks "quasi identisch" sein
  // (siehe recentQueueTracks()/isNearDuplicateTrack()) -- Tausch mit dem
  // ersten passenden spaeteren Eintrag.
  const recentTracks = recentQueueTracks();
  if (recentTracks.length && queueState.tracks.length > 1 && recentTracks.some(rt => isNearDuplicateTrack(rt, queueState.tracks[0]))) {
    const swapIdx = queueState.tracks.findIndex((r, i) => i > 0 && !recentTracks.some(rt => isNearDuplicateTrack(rt, r)));
    if (swapIdx > 0) [queueState.tracks[0], queueState.tracks[swapIdx]] = [queueState.tracks[swapIdx], queueState.tracks[0]];
  }
  queueState.manual = false;
  return true;
}

// manual=true: Knopf/Pfeiltaste -- rueckt immer vor, auch bei aktivem
// Titel-Wiederholen (Konvention wie bei den meisten Playern: "Naechster"
// ueberspringt Repeat-Track bewusst). manual=false: natuerliches Ende
// (queueAudio.onended) -- wiederholt bei repeatTrack denselben Track.
function queueNext(manual) {
  if (!manual && queueState.repeatTrack) {
    queueAudio.currentTime = 0;
    queueAudio.play().catch(err => playFailed(queueAudio, err));
    return;
  }
  if (queueState.tracks.length) { advanceFromPool(); return; }
  if (queueState.repeatList && (queueState.history.length || queueState.current)) {
    // Liste von vorn: komplette Historie (+ aktueller Track) wird wieder
    // zum Pool, in derselben Reihenfolge wie urspruenglich gespielt.
    queueState.tracks = queueState.current
      ? [...queueState.history, queueState.current] : [...queueState.history];
    queueState.history = [];
    queueState.current = null;
    advanceFromPool();
    return;
  }
  // Ende der Warteschlange ohne Listen-Wiederholung: automatisch neue
  // Zufallsauswahl aus der aktuellen Filteransicht nachlegen, damit die
  // Wiedergabe wie ein Radiosender weiterlaeuft (siehe extendQueueAutomatically()).
  if (extendQueueAutomatically()) { advanceFromPool(); return; }
  // Kein Nachschub moeglich (Filter liefert nichts/nur "gone"-Zeilen):
  // stehen bleiben, nicht stumm den letzten Track erneut laden.
  renderPlayerBar();
}

function queuePrev() {
  if (!queueState.history.length) return;
  const prev = queueState.history.pop();
  if (queueState.current) queueState.tracks.unshift(queueState.current);
  loadQueueTrack(prev);
}

// Laeuft noch nichts (queueState.current === null), soll ein Klick auf
// "Zufallswiedergabe" nicht nur den Modus umschalten, sondern gleich
// losspielen -- sonst wirkt der Knopf ohne bereits geladenen Track wie ein
// totes Steuerelement. Startpunkt ist ein zufaelliger Track aus der aktuell
// gefilterten Ansicht (dieselbe Quelle wie startQueueFrom()), der Rest der
// Warteschlange fuellt sich von dort aus wie gewohnt per advanceFromPool().
function toggleShuffle() {
  if (!queueState.current) {
    if (!apiMode) { note(t("player.bar.needs_server"), "soft"); return; }
    const rows = filtered().filter(x => !x.gone);
    if (!rows.length) { note(t("player.bar.shuffle_empty"), "soft"); return; }
    queueState.shuffle = true;
    saveFilters();
    startQueueFrom(rows[Math.floor(Math.random() * rows.length)]);
    return;
  }
  queueState.shuffle = !queueState.shuffle;
  saveFilters();
  renderPlayerBar();
}
function toggleRepeatTrack() { queueState.repeatTrack = !queueState.repeatTrack; saveFilters(); renderPlayerBar(); }
function toggleRepeatList()  { queueState.repeatList  = !queueState.repeatList;  saveFilters(); renderPlayerBar(); }

// Klick auf einen POOL-Eintrag im Warteschlange-Tab: spielt ihn sofort,
// die uebrigen Pool-Eintraege behalten ihre relative Reihenfolge (sie waren
// ja nie "dran", bleiben also regulaer "als naechstes").
function playFromPool(idx) {
  const r = queueState.tracks[idx];
  if (!r) return;
  queueState.tracks.splice(idx, 1);
  if (queueState.current) pushQueueHistory(queueState.current);
  loadQueueTrack(r);
}

// Klick auf einen HISTORIE-Eintrag im Tab "Zuletzt gehoert": spielt ihn
// erneut, der bisherige aktuelle Track wandert dafuer zurueck an den Pool-
// Anfang (er war ja noch nicht zu Ende) statt in die Historie.
function playFromHistory(idx) {
  const r = queueState.history[idx];
  if (!r) return;
  queueState.history.splice(idx, 1);
  if (queueState.current) queueState.tracks.unshift(queueState.current);
  loadQueueTrack(r);
}

function removeQueuePoolItem(idx) {
  queueState.tracks.splice(idx, 1);
  queueState.manual = true;
  renderPlayerBar();
  renderQueuePopup();
}

// Drag & Drop im Warteschlange-Popup -- nur der Pool ist umsortierbar
// (die Historie ist chronologisch, der aktuelle Track laeuft schon).
function reorderQueue(fromIdx, toIdx) {
  const [moved] = queueState.tracks.splice(fromIdx, 1);
  queueState.tracks.splice(toIdx, 0, moved);
  queueState.manual = true;
  renderPlayerBar();
  renderQueuePopup();
}

// ── Wellenform einer Bibliothekszeile ⇄ globaler Player ───────────────────
// Zeilenklick in der Bearbeiten-Ansicht klappt nur die Wellenform auf (siehe
// render()), ohne Wiedergabe zu starten. Erst der Toggle-Knopf im Player
// oder ein Wellenform-/Cue-Klick laedt den Track in den globalen queueAudio
// (denselben, den auch die Player-Ansicht nutzt) -- inkl. automatischer
// Warteschlange aus den naechsten QUEUE_AUTO_LIMIT gefilterten Zeilen (siehe
// startQueueFrom()). Es gibt hier bewusst KEIN eigenes Audio()-Objekt: die
// Wellenform ist nur eine Ansicht auf queueAudio, solange dessen Track mit
// dieser Zeile uebereinstimmt (Vergleich ueber r.i), siehe
// repaintWaveViews().
const waveViews = new Map();
// Zielposition (Sekunden) fuer einen Wellenform-/Cue-Klick auf eine noch
// NICHT aktuelle Zeile: startQueueFrom() laedt den Track neu, dessen Dauer
// steht aber erst nach queueAudio.onloadedmetadata fest -- dort wird die
// Position nachgeholt und zurueckgesetzt (siehe queueAudio-Verdrahtung weiter
// unten).
let pendingSeek = null;

function mountLibraryPlayer(root, r) {
  const box = root.querySelector(".player");
  if (!box) return;
  const canvas = box.querySelector("canvas.wave");
  const btn = box.querySelector("[data-act=toggle]");
  const time = box.querySelector(".ptime");

  const isCurrent = () => !!(queueState.current && queueState.current.i === r.i);

  // Nur die Zeile, deren Track gerade queueState.current ist, zeigt
  // laufenden Fortschritt; alle anderen bleiben im unbespielten
  // Ausgangszustand (frac 0, volle Dauer aus der DB-Zeile).
  const entry = registerWaveEntry({
    root: root, canvas: canvas, r: r, btn: btn, timeEl: time,
    peaks: null, cues: null, waveform: null,
    sync() {
      const cur = isCurrent();
      this.currentTime = cur ? queueAudio.currentTime : 0;
      this.duration = cur ? queueAudio.duration : r.du;
    },
    syncLabels() {
      const cur = isCurrent();
      const icon = (cur && !queueAudio.paused) ? ICONS.pause : ICONS.play;
      if (this._icon !== icon) { this._icon = icon; btn.innerHTML = icon; }
      const sec = cur ? Math.floor(this.currentTime) : -1;
      if (sec === this._sec) return;
      this._sec = sec;
      time.textContent = cur
        ? fmtTime(sec) + " / " + (isFinite(this.duration) ? fmtTime(Math.floor(this.duration)) : fmtTime(r.du))
        : "–:–– / " + fmtTime(r.du);
    },
    // Wird die Zeile wieder zugeklappt, bleibt die (globale) Wiedergabe
    // bestehen -- nur die Wellenform-Ansicht selbst verschwindet.
    onDispose() { waveViews.delete(r.i); },
  });
  waveViews.set(r.i, entry);

  (r.file ? peaksFromFile(r.file) : peaksForLibrary(r.p))
    .then(p => setWaveData(entry, {peaks: p}))
    .catch(() => setWaveData(entry, {peaks: []}));

  // Per Drag & Drop geprüfte Dateien ohne Bibliothekspfad koennen nicht in
  // Rekordbox nachgeschlagen werden (kommt bei Bibliothekszeilen praktisch
  // nie vor, r.file bleibt hier defensiv wie bei mountDropPlayer beruecksichtigt).
  if (!r.file) {
    rekordboxExtrasFor(r.p)
      .then(extras => {
        if (extras && extras.available) {
          setWaveData(entry, {cues: extras.cues || [], waveform: extras.waveform || null});
        }
      })
      .catch(() => {});
  }

  btn.onclick = ev => {
    ev.stopPropagation();
    if (isCurrent()) {
      if (queueAudio.paused) queueAudio.play().catch(err => playFailed(queueAudio, err));
      else queueAudio.pause();
    } else {
      startQueueFrom(r);
    }
  };

  canvas.onclick = ev => {
    ev.stopPropagation();
    const current = isCurrent();
    const duration = current ? queueAudio.duration : r.du;
    const hit = cueHitAt(entry, canvas, ev.clientX, duration);
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
    const seekTo = hit ? hit.position_s : (isFinite(duration) ? frac * duration : null);
    if (current) {
      if (seekTo != null) queueAudio.currentTime = seekTo;
      if (queueAudio.paused) queueAudio.play().catch(err => playFailed(queueAudio, err));
      requestWavePaint();
    } else {
      pendingSeek = seekTo;
      startQueueFrom(r);
    }
  };
}

// Zeichnet/aktualisiert alle gerade aufgeklappten Wellenformen neu.
// Fruehere Rolle von _repaintOneWaveView()/repaintWaveViews(): das Zeichnen
// steckt jetzt in paintWaveEntry(), Knopf und Zeitanzeige in entry.syncLabels()
// -- beides von wavePump() aus einer einzigen Bildschleife heraus gerufen.
// Bleibt als benannter Einstieg fuer Aufrufer erhalten, die nur "bitte neu
// zeichnen" sagen wollen.
function repaintWaveViews() { requestWavePaint(); }

// Fortschrittsbalken des Player-Balkens. Wird von wavePump() pro Bild
// gerufen -- deshalb transform statt width (width loest Layout aus,
// transform laeuft im Compositor) und die Textfelder nur bei echtem
// Sekundenwechsel.
let _pbarSec = null;
function renderPlayerProgress() {
  const fill = document.getElementById("pbarProgressFill");
  if (!fill) return;
  const d = queueAudio.duration;
  const frac = (isFinite(d) && d > 0) ? Math.min(1, Math.max(0, queueAudio.currentTime / d)) : 0;
  fill.style.transform = `scaleX(${frac})`;

  const sec = isFinite(queueAudio.currentTime) ? Math.floor(queueAudio.currentTime) : -1;
  if (sec === _pbarSec) return;
  _pbarSec = sec;
  const cur = document.getElementById("pbarTimeCur");
  const total = document.getElementById("pbarTimeTotal");
  if (!cur) return;
  cur.textContent = sec >= 0 ? fmtTime(sec) : "–:––";
  total.textContent = isFinite(d) ? fmtTime(Math.floor(d)) : "–:––";
}

// Ziehen am Fortschrittsbalken -- ersetzt das fruehere reine Klick-zum-
// Springen um echtes Ziehen samt Sprechblase (Klasse .pbar-bubble, siehe
// app.css), die waehrend des Ziehens die Zielzeit an der Mausposition zeigt.
// Ein einfacher Klick (mousedown+mouseup ohne Bewegung) verhaelt sich dabei
// weiter wie zuvor: sofort an diese Stelle springen.
function setupSeekDrag() {
  const bar = document.getElementById("pbarProgress");
  const bubble = document.getElementById("pbarSeekBubble");

  const fracFromEvent = ev => {
    const rect = bar.getBoundingClientRect();
    return Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
  };
  const showAt = frac => {
    bubble.textContent = fmtTime(Math.floor(frac * queueAudio.duration));
    bubble.style.left = (frac * 100) + "%";
    bubble.classList.add("show");
  };

  // Jede Zuweisung an queueAudio.currentTime loest bei einer HTTP-Quelle
  // eine eigene Range-Anfrage aus. Vorher geschah das bei JEDEM mousemove --
  // ein Zug ueber den Balken erzeugte so leicht 50 bis 100 Anfragen, die der
  // Server einzeln autorisieren und bedienen musste. Jetzt folgt die Blase
  // sofort, die Quelle wird hoechstens einmal pro Bild und beim Loslassen
  // endgueltig gesetzt.
  let pendingFrac = null, moveRaf = 0;
  const commit = () => {
    moveRaf = 0;
    if (pendingFrac == null) return;
    const d = queueAudio.duration;
    if (isFinite(d) && d > 0) queueAudio.currentTime = pendingFrac * d;
    pendingFrac = null;
    requestWavePaint();
  };

  bar.addEventListener("mousedown", ev => {
    const d = queueAudio.duration;
    if (!isFinite(d) || d <= 0) return;
    pendingFrac = fracFromEvent(ev);
    showAt(pendingFrac);
    commit();
    const onMove = ev2 => {
      pendingFrac = fracFromEvent(ev2);
      showAt(pendingFrac);
      if (!moveRaf) moveRaf = requestAnimationFrame(commit);
    };
    const onUp = () => {
      if (moveRaf) { cancelAnimationFrame(moveRaf); moveRaf = 0; }
      commit();
      bubble.classList.remove("show");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });
}

function renderPlayerBar() {
  const bar = document.getElementById("playerBar");
  if (!bar) return;
  const r = currentQueueTrack();
  const cover = document.getElementById("pbarCover");
  const coverEmpty = document.getElementById("pbarCoverEmpty");
  const showCover = r && apiMode && !r.gone && r.cv;
  cover.style.display = showCover ? "" : "none";
  coverEmpty.style.display = showCover ? "none" : "";
  if (showCover) cover.src = coverUrl(r.p);
  // #pbarTitle bewusst OHNE data-i18n im HTML (nur der literale deutsche
  // Platzhaltertext als Vor-JS-Fallback) -- applyStaticI18n() ueberschreibt
  // jedes data-i18n-Element blind mit dessen statischer Uebersetzung, auch
  // beim zweiten Aufruf aus initStorage() nach dem Laden der Einstellungen.
  // Mit data-i18n hier wuerde das den laengst geladenen Titel wieder auf
  // "Kein Titel ausgewaehlt" zuruecksetzen, sobald dieser zweite Aufruf nach
  // renderPlayerBar() greift (an echtem Material beobachtet: Warteschlange
  // korrekt restauriert, Balken zeigte trotzdem den Platzhalter).
  const titleBox = document.getElementById("pbarTitle");
  // Laufende Marquee-Animation (siehe setupTitleMarquee() weiter unten)
  // beendet -- sie wurde fuer die Breite des ALTEN Titels berechnet und
  // wuerde bei einem Trackwechsel waehrend der Maus noch ueber dem Titel
  // steht sonst mit falscher Distanz weiterlaufen.
  titleBox.classList.remove("marqueeing");
  document.getElementById("pbarTitleInner").textContent = r ? (r.t || baseName(r.p)) : t("player.bar.no_track");
  document.getElementById("pbarArtist").textContent = r ? (r.a || "") : "";
  document.getElementById("pbarPlay").innerHTML = (r && !queueAudio.paused) ? ICONS.pause : ICONS.play;
  document.getElementById("pbarPlay").disabled = !r;
  document.getElementById("pbarPrev").disabled = !r;
  document.getElementById("pbarNext").disabled = !r;
  document.getElementById("pbarShuffle").classList.toggle("on", queueState.shuffle);
  document.getElementById("pbarRepeatTrack").classList.toggle("on", queueState.repeatTrack);
  document.getElementById("pbarRepeatList").classList.toggle("on", queueState.repeatList);
  document.getElementById("pbarQueueCount").textContent = queueState.tracks.length;
  // Zeitanzeige einmal erzwingen: der Sekunden-Cache in renderPlayerProgress()
  // wuerde einen Trackwechsel innerhalb derselben Sekunde sonst verschlucken
  // (Gesamtdauer bliebe auf dem alten Wert stehen).
  _pbarSec = null;
  renderPlayerProgress();
  requestWavePaint();
  saveQueueState();
  updateNowPlayingIndicator();
}

// Spiegelt queueState.current in der Checkbox-Spalte der Bibliotheksliste
// (siehe render()) -- dort steckt das Icon schon im frisch gebauten HTML,
// hier nur der Nachzug bei einem Trackwechsel OHNE komplettes render()
// (gleiches Prinzip wie setCursorRow() fuer .kbcursor). Bewusst kein
// Play/Pause-Unterschied -- das Icon markiert nur "das ist der geladene
// Track", dafuer reicht ein Wechsel-Check statt bei jedem Pause/Resume neu
// zu zeichnen.
let nowPlayingRowI = null;
function updateNowPlayingIndicator() {
  const cur = queueState.current;
  if (nowPlayingRowI === (cur ? cur.i : null)) return;
  if (nowPlayingRowI !== null) {
    const prevTd = document.querySelector(`tr.row[data-i="${nowPlayingRowI}"] td.sel`);
    if (prevTd) { const ic = prevTd.querySelector(".nowplayingicon"); if (ic) ic.remove(); }
  }
  nowPlayingRowI = cur ? cur.i : null;
  if (!cur) return;
  const td = document.querySelector(`tr.row[data-i="${cur.i}"] td.sel`);
  if (!td) return;
  const icon = document.createElement("span");
  icon.className = "nowplayingicon";
  icon.title = t("player.now_playing_title");
  icon.innerHTML = ICONS.audioLines;
  td.appendChild(icon);
}

// ── Lautstaerke ────────────────────────────────────────────────────────
// Eigener localStorage-Key, unabhaengig von saveFilters()/saveQueueState() --
// gilt geraeteweit fuer den Player, nicht pro Warteschlange.
const LS_VOLUME = "tracktab.volume";

// Regler und Stumm-Taste spiegeln sich gegenseitig: 0 auf dem Regler
// schaltet stumm, die Stumm-Taste zieht den Regler auf 0 (und zurueck).
// Der Regler zeigt deshalb nicht immer queueAudio.volume selbst an, sondern
// den "wirksamen" Wert (0 waehrend stumm) -- renderVolumeUI() ist die
// einzige Stelle, die Regler-Anzeige und Icon berechnet.
function applyVolume(v) {
  queueAudio.volume = Math.min(1, Math.max(0, v));
  queueAudio.muted = queueAudio.volume === 0;
  renderVolumeUI();
  saveVolumeState();
}

function toggleMute() {
  queueAudio.muted = !queueAudio.muted;
  renderVolumeUI();
  saveVolumeState();
}

function renderVolumeUI() {
  const slider = document.getElementById("pbarVolume");
  const btn = document.getElementById("pbarMute");
  if (!slider || !btn) return;
  const effective = queueAudio.muted ? 0 : queueAudio.volume;
  slider.value = Math.round(effective * 100);
  btn.innerHTML = effective === 0 ? ICONS.volumeMute
    : effective < 0.34 ? ICONS.volume
    : effective < 0.67 ? ICONS.volumeMid
    : ICONS.volumeHigh;
  btn.title = queueAudio.muted ? t("player.bar.unmute_title") : t("player.bar.mute_title");
}

// Sprechblase mit dem Prozentwert waehrend am Lautstaerkeregler gezogen wird
// -- dasselbe Prinzip wie setupSeekDrag() beim Fortschrittsbalken, aber ueber
// ein natives <input type=range> statt eines eigenen Divs: die eigentliche
// Wertaenderung uebernimmt weiter dessen "input"-Ereignis (siehe oninput-
// Zuweisung), hier wird nur mitgezogen, was die Blase anzeigt/wo sie sitzt.
function setupVolumeBubble() {
  const slider = document.getElementById("pbarVolume");
  const bubble = document.getElementById("pbarVolBubble");
  const position = () => {
    bubble.textContent = slider.value + "%";
    bubble.style.left = slider.value + "%";
  };
  slider.addEventListener("mousedown", () => { position(); bubble.classList.add("show"); });
  slider.addEventListener("input", () => { if (bubble.classList.contains("show")) position(); });
  window.addEventListener("mouseup", () => bubble.classList.remove("show"));
}

function saveVolumeState() {
  try {
    localStorage.setItem(LS_VOLUME, JSON.stringify({volume: queueAudio.volume, muted: queueAudio.muted}));
  } catch (e) { /* Speicher nicht verfuegbar -- dann eben nicht */ }
}

function loadVolumeState() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_VOLUME) || "null");
    if (!s) return;
    if (typeof s.volume === "number") queueAudio.volume = Math.min(1, Math.max(0, s.volume));
    // 0 gilt immer als stumm (siehe applyVolume()), unabhaengig vom
    // gespeicherten Flag -- deckt einen alten/inkonsistenten Speicherstand ab.
    queueAudio.muted = !!s.muted || queueAudio.volume === 0;
  } catch (e) { /* kaputter Eintrag wird ignoriert */ }
}

// ── Durchlaufender Titel bei Hover (nur #pbarTitle, siehe Plan) ──────────
// Reine JS-Messung statt purer CSS-:hover-Animation, weil die Lauflaenge
// (wie weit/lange animiert werden muss) von der tatsaechlichen Ueberlaenge
// des jeweiligen Titels abhaengt -- ein fixer CSS-Wert waere bei kurzen
// Titeln zu schnell/unnoetig, bei langen zu kurz zum Lesen.
function setupTitleMarquee() {
  const outer = document.getElementById("pbarTitle");
  const inner = document.getElementById("pbarTitleInner");
  if (!outer || !inner) return;
  outer.addEventListener("mouseenter", () => {
    const overflow = inner.scrollWidth - outer.clientWidth;
    if (overflow <= 0) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // ~30px/s, mindestens 3s -- lang genug, um mitzulesen (siehe Plan).
    const duration = Math.max(3, overflow / 30);
    outer.style.setProperty("--marquee-distance", `-${overflow}px`);
    outer.style.setProperty("--marquee-duration", `${duration}s`);
    outer.classList.add("marqueeing");
  });
  outer.addEventListener("mouseleave", () => outer.classList.remove("marqueeing"));
}

// ── Warteschlange ueber ein Neuladen hinweg merken ───────────────────────
// Anders als Shuffle/Repeat (siehe saveFilters()) ueber einen eigenen Key --
// haengt an Pfaden statt Zeilenindizes, weil r.i nur fuer die Lebensdauer
// des aktuell geladenen Reports gilt. saveQueueState() haengt an
// renderPlayerBar() (jede Aenderung an queueState ruft das ohnehin auf),
// nicht an jeder einzelnen Mutation extra.
const LS_QUEUE = "tracktab.queue";

function saveQueueState() {
  try {
    if (!queueState.current && !queueState.tracks.length && !queueState.history.length) {
      localStorage.removeItem(LS_QUEUE);
      return;
    }
    localStorage.setItem(LS_QUEUE, JSON.stringify({
      poolPaths: queueState.tracks.map(r => r.p),
      historyPaths: queueState.history.map(r => r.p),
      currentPath: queueState.current ? queueState.current.p : null,
      manual: queueState.manual,
    }));
  } catch (e) { /* Speicher nicht verfuegbar -- dann eben nicht */ }
}

// Laedt die gemerkte Warteschlange (Pool + Historie + aktueller Track),
// spielt aber NICHT automatisch ab -- ein Neuladen der Seite soll nicht
// ungefragt Ton machen. Tracks, deren Pfad es nicht mehr in DATA gibt
// (Datei entfernt, Bibliothek neu gescannt), fallen dabei einfach raus
// statt die ganze Warteschlange zu verwerfen.
function loadQueueState() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_QUEUE) || "null");
    if (!s) return;
    const byPath = new Map(DATA.map(r => [r.p, r]));
    const pool = (s.poolPaths || []).map(p => byPath.get(p)).filter(Boolean);
    const history = (s.historyPaths || []).map(p => byPath.get(p)).filter(Boolean);
    const current = s.currentPath ? byPath.get(s.currentPath) : null;
    if (!current && !pool.length && !history.length) return;
    queueState.tracks = pool;
    queueState.history = history;
    queueState.manual = !!s.manual;
    if (current) {
      queueState.current = current;
      queueAudio.src = "/api/audio?path=" + encodeURIComponent(current.p);
    }
  } catch (e) { /* kaputter Eintrag wird ignoriert */ }
}

// ── Warteschlange-Popup: zwei Tabs ────────────────────────────────────────
// "queue" (aktueller Track + Pool) und "history" (Tab "Zuletzt gehoert").
// Reine Anzeige-Auswahl, deshalb NICHT Teil von queueState/der Persistenz.
let queuePopupTab = "queue";

function switchQueuePopupTab(tab) {
  queuePopupTab = tab;
  renderQueuePopup();
}

function queueRowHTML(r) {
  // Ziehgriff nur optisch relevant fuer den Pool (Reihenfolge nur dort
  // aenderbar) -- per CSS ([data-qidx] .queueitem-grip) statt eigenem
  // Parameter ausgeblendet, damit dieselbe Funktion fuer laufende Zeile UND
  // Historie unveraendert weiterlaeuft.
  return `<span class="queueitem-grip">${ICONS.gripVertical}</span>${(apiMode && !r.gone && r.cv)
      ? `<img class="queueitem-cover" src="${coverUrl(r.p)}" alt="">`
      : `<div class="queueitem-cover"></div>`}
    <div class="queueitem-meta">
      <div class="queueitem-title">${esc(r.t || baseName(r.p))}</div>
      <div class="queueitem-artist">${esc(r.a || "")}</div>
    </div>`;
}

function renderQueuePopup() {
  const list = document.getElementById("queueList");
  if (!list) return;
  // Ist das Popup zu, gibt es nichts zu zeichnen. Vorher baute jeder
  // Trackwechsel den kompletten Inhalt neu auf -- bis zu 25 Eintraege samt
  // <img>-Cover, unsichtbar, inklusive der zugehoerigen Bildanfragen.
  // toggleQueuePopup() ruft beim Oeffnen ohnehin hier durch.
  const popup = document.getElementById("queuePopup");
  if (popup && popup.style.display === "none") return;
  document.querySelectorAll("[data-qptab]").forEach(b =>
    b.classList.toggle("on", b.dataset.qptab === queuePopupTab));
  if (queuePopupTab === "history") renderQueueHistoryTab(list);
  else renderQueueUpcomingTab(list);
}

function renderQueueUpcomingTab(list) {
  const cur = queueState.current;
  if (!cur && !queueState.tracks.length) {
    list.innerHTML = `<div id="queueEmpty">${esc(t("queue.empty"))}</div>`;
    return;
  }
  const curHTML = cur
    ? `<div class="queueitem current" data-qcurrent="1">${queueRowHTML(cur)}</div>` : "";
  const poolHTML = queueState.tracks.map((r, i) => `
    <div class="queueitem" data-qidx="${i}" draggable="true">${queueRowHTML(r)}
      <button class="iconbtn plain queueitem-remove" data-qremove="${i}" title="${esc(t("queue.remove_title"))}">${ICONS.close}</button>
    </div>`).join("");
  list.innerHTML = curHTML + poolHTML;

  // Klick auf die laufende Zeile: kein Sprungziel (sie laeuft ja schon) --
  // schaltet stattdessen Wiedergabe/Pause, wie ein Klick auf den Play-Knopf
  // im Balken.
  const curEl = list.querySelector("[data-qcurrent]");
  if (curEl) curEl.onclick = () => document.getElementById("pbarPlay").click();

  list.querySelectorAll("[data-qidx]").forEach(el => {
    el.onclick = ev => {
      if (ev.target.closest("[data-qremove], .queueitem-grip")) return;
      playFromPool(+el.dataset.qidx);
    };
  });
  list.querySelectorAll("[data-qremove]").forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();
    removeQueuePoolItem(+btn.dataset.qremove);
  });

  // Umsortieren per Drag & Drop -- dasselbe native HTML5-Muster wie beim
  // Umsortieren der Spalten-Kopfzeile (attachColumnDrag() weiter oben).
  // Nur der Pool ist ziehbar, die laufende Zeile traegt kein data-qidx.
  let dragIdx = null;
  list.querySelectorAll("[data-qidx]").forEach(el => {
    el.ondragstart = () => { dragIdx = +el.dataset.qidx; };
    el.ondragover = e => { e.preventDefault(); el.classList.add("dragover"); };
    el.ondragleave = () => el.classList.remove("dragover");
    el.ondrop = e => {
      e.preventDefault();
      el.classList.remove("dragover");
      const targetIdx = +el.dataset.qidx;
      if (dragIdx === null || dragIdx === targetIdx) return;
      reorderQueue(dragIdx, targetIdx);
    };
  });
}

function renderQueueHistoryTab(list) {
  if (!queueState.history.length) {
    list.innerHTML = `<div id="queueEmpty">${esc(t("queue.history_empty"))}</div>`;
    return;
  }
  // Zuletzt gehoert zuerst -- umgekehrt chronologisch, wie man es von einem
  // Verlauf erwartet. queueState.history selbst bleibt aufsteigend
  // chronologisch (aeltester zuerst), das haelt push()/pop() in
  // advanceFromPool()/queuePrev() einfach. Bewusst KEIN Entfernen-Knopf hier
  // (anders als im Pool-Tab) -- die Historie ist ein reiner Verlauf, einzelne
  // Eintraege loeschen soll nicht gehen, nur "Leeren" auf einmal.
  const indices = queueState.history.map((_, i) => i).reverse();
  list.innerHTML = indices.map(i => { const r = queueState.history[i]; return `
    <div class="queueitem" data-qhidx="${i}">${queueRowHTML(r)}</div>`; }).join("");

  list.querySelectorAll("[data-qhidx]").forEach(el => {
    el.onclick = () => playFromHistory(+el.dataset.qhidx);
  });
}

// removeAttribute("src") allein laesst duration/currentTime an echtem
// Material stehen, bis die Ressourcen-Auswahl des Elements neu anlaeuft --
// ohne load() zeigte der Fortschrittsbalken nach dem Leeren weiter den
// Stand des zuletzt geladenen Tracks (Breite/Zeit blieben haengen).
function resetQueueAudio() {
  queueAudio.pause();
  queueAudio.removeAttribute("src");
  queueAudio.load();
}

function clearQueue() {
  resetQueueAudio();
  queueState.tracks = [];
  queueState.current = null;
  queueState.history = [];
  queueState.manual = false;
  renderPlayerBar();
  renderQueuePopup();
}

function toggleQueuePopup(force) {
  const el = document.getElementById("queuePopup");
  if (!el) return;
  const show = force !== undefined ? force : el.style.display === "none";
  el.style.display = show ? "block" : "none";
  if (show) renderQueuePopup();
}

// ── Einzelprüfungen per Drag & Drop ─────────────────────────────────────
const DROP_EXT = [".mp3",".m4a",".wav",".flac",".aif",".aiff",".ogg",".mp4"];
const drops = [];
// Auswahl per stabiler ID statt Array-Index -- neue Treffer werden vorn
// eingefuegt (unshift), ein Index waere danach fuer aeltere Zeilen falsch.
let dropIdSeq = 0;
const dropSelected = new Set();
let lastDropSelPos = null;   // Shift-Klick-Anker, wie lastSelPos in der Haupttabelle

// Nur Zeilen mit echtem, dauerhaftem Pfad sind ueberhaupt auswaehlbar --
// per Browser-Drop/-Dateiauswahl gepruefte Dateien sind Kopien, die der
// Server nach der Analyse sofort wieder loescht (siehe _post_analyse).
// Fuer sie gibt es serverseitig nichts mehr zu tun: weder Tags schreiben
// noch neu messen noch importieren.
const dropSelectable = () => drops.filter(r => r.nativePath);
const selectedDrops = () => drops.filter(r => dropSelected.has(r._id));

function syncDropSelectAll() {
  const el = document.getElementById("dropSelAll");
  if (!el) return;
  const rows = dropSelectable();
  const all = rows.length > 0 && rows.every(r => dropSelected.has(r._id));
  el.checked = all;
  el.indeterminate = !all && rows.some(r => dropSelected.has(r._id));
}

function fromServerRow(row, file) {
  return {
    _id: ++dropIdSeq,
    p: row.path, a: row.artist || "", t: row.title || "",
    al: row.album || "", aa: row.album_artist || "", cp: row.composer || "",
    ge: row.genre || "", yr: row.year || 0, bp: row.bpm || 0, cm: row.comment || "",
    tn: row.track_no || 0, tt: row.track_total || 0,
    v: row.verdict, fam: row.codec_family || "lossy_mp3", cd: row.codec || "",
    kb: row.declared_kbps || 0, mk: row.measured_kbps || 0,
    co: Math.round((row.cutoff_hz || 0) / 10) / 100,
    st: Math.round((row.steepness_db || 0) * 10) / 10,
    bw: row.is_brickwall ? 1 : 0, cf: row.confidence || 0,
    du: Math.round(row.duration_s || 0), sr: row.sample_rate || 44100,
    mo: row.bitrate_mode || "", en: row.encoder || "",
    lp: row.lame_lowpass_hz ? Math.round(row.lame_lowpass_hz / 100) / 10 : 0,
    lu: row.integrated_lufs || 0, tp: row.true_peak_dbtp || 0, lra: row.lra_lu || 0,
    sz: row.size || (file ? file.size : 0), cv: row.has_cover ? 1 : 0,
    rb: row.rekordbox_present ? 1 : 0,
    im: row.music_added_ts ? 1 : 0, da: row.music_added_ts || 0,
    rs: row.reasons || [], sp: (row.spectrum || []).map(x => x[1]), ig: 0,
    file: file, blobUrl: file ? URL.createObjectURL(file) : null
  };
}

async function analyseFile(file) {
  const res = await fetch("/api/analyse?name=" + encodeURIComponent(file.name), {
    method: "POST", body: file
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || ("HTTP " + res.status));
  return fromServerRow(data.row, file);
}

async function handleDropped(fileList) {
  const files = [...fileList].filter(f =>
    DROP_EXT.some(e => f.name.toLowerCase().endsWith(e)));
  const skipped = fileList.length - files.length;
  if (!files.length) {
    dropStatus(skipped ? t("drop.status_unsupported") : t("drop.status_nothing"), true);
    return;
  }
  if (!apiMode) {
    dropStatus(t("drop.status_needs_server"), true);
    return;
  }

  let done = 0;
  const tick = () => dropStatus(t("drop.status_progress", {done, files: files.length}) +
    (skipped ? t("drop.status_skipped_suffix", {count: skipped}) : ""));
  tick();

  // Höchstens drei gleichzeitig, sonst konkurrieren die ffmpeg-Läufe
  const queue = files.slice();
  const worker = async () => {
    while (queue.length) {
      const file = queue.shift();
      try {
        const r = await analyseFile(file);
        drops.unshift(r);
      } catch (err) {
        drops.unshift({p: file.name, v: "UNKLAR", co: 0, st: 0, bw: 0, cf: 0,
          kb: 0, mk: 0, du: 0, sr: 44100, mo: "", en: "", lp: 0,
          lu: 0, tp: 0, lra: 0, sz: file.size,
          rs: [t("toast.analysis_failed", {error: err.message})], sp: [], ig: 0,
          a: "", t: "", file: null, blobUrl: null});
      }
      done++; tick(); renderDrops();
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  dropStatus(t("drop.status_done", {count: files.length, file_word: files.length === 1 ? t("drop.file_singular") : t("drop.file_plural")}) +
    (skipped ? t("drop.status_skipped_suffix", {count: skipped}) : ""));
}

function dropStatus(text, warn) {
  const el = document.getElementById("dropStatus");
  el.textContent = text;
  el.className = warn ? "warn" : "";
}

// Eigene Spaltenliste der Einzelprueflungen, unabhaengig von der
// Haupttabelle (siehe state.dropColumns) -- nur ohne data-k/data-reorder,
// Drag-Reorder gibt es hier bewusst nicht (Menue-Knopf reicht, siehe
// renderDropColsMenu()).
function dropVisibleCols() {
  return state.dropColumns.order.filter(k => !state.dropColumns.hidden.includes(k) && CELL_RENDERERS[k]);
}

function renderDrops() {
  lastDropSelPos = null;
  // Auswahl auf Zeilen begrenzen, die es noch gibt -- entfernte Eintraege
  // sollen nicht unsichtbar in dropSelected haengen bleiben.
  const alive = new Set(dropSelectable().map(r => r._id));
  for (const id of [...dropSelected]) if (!alive.has(id)) dropSelected.delete(id);

  // Anders als dropRename/dropAddLib (haengen an der Auswahl, siehe
  // updateDropHeadButtons()) haengt der Spalten-Knopf nur daran, ob
  // ueberhaupt etwas geladen ist -- eine Spaltenkonfiguration ohne Zeilen
  // waere nutzlos.
  const colsBtn = document.getElementById("btnDropCols");
  if (colsBtn) colsBtn.disabled = !drops.length;
  if (!drops.length) {
    const menu = document.getElementById("dropColsMenu");
    if (menu) menu.style.display = "none";
  }

  const host = document.getElementById("dropList");
  if (!drops.length) {
    host.innerHTML = `<div class="path">${t("drop.empty_state")}</div>`;
    renderDropBulkBar();
    updateDropHeadButtons();
    return;
  }
  const cols = dropVisibleCols();
  const statusHidden = state.dropColumns.hidden.includes("v");
  const colHead = cols.map(k => {
    const c = OPTIONAL_COLUMNS.find(c => c.key === k);
    const w = state.dropColumns.widths[k] || DEFAULT_COL_WIDTHS[k];
    return `<th${c.numeric ? ' class="num"' : ""} style="width:${w}px">${esc(c.label)}</th>`;
  }).join("");

  const selectable = dropSelectable();
  host.innerHTML = `<div class="tablewrap droptablewrap"><table>
    <thead><tr>
      <th class="sel" style="width:36px">${selectable.length
        ? `<input type="checkbox" id="dropSelAll" title="${esc(t("drop.select_all_title"))}">` : ""}</th>
      ${statusHidden ? "" : `<th style="width:90px">${esc(t("col.status"))}</th>`}
      ${colHead}
      <th class="filler"></th>
      <th class="links" style="width:220px">${esc(t("col.open"))}</th>
    </tr></thead>
    <tbody>${drops.map((r, i) => `
      <tr class="row${dropSelected.has(r._id) ? " picked" : ""}" data-drop="${i}">
        <td class="sel">${r.nativePath ? `<input type="checkbox" class="dropchk" data-dropsel="${r._id}"
              ${dropSelected.has(r._id) ? "checked" : ""}
              title="${esc(t("drop.select_row_title"))}">` : ""}</td>
        ${statusHidden ? "" : `<td><span class="badge ${r.v}">${VERDICT_ICONS[r.v] || ""}${labels[r.v]}</span></td>`}
        ${cols.map(k => CELL_RENDERERS[k](r)).join("")}
        <td class="filler"></td>
        <td class="links"><div class="actions">
          <div class="actiongroup top">
            <button class="iconbtn" data-dropplay="${r._id}" title="${esc(t("action.listen"))}">${ICONS.play}</button>
            ${r.nativePath ? `<button class="iconbtn plain" data-edittags="${r._id}"
                  title="${esc(t("drop.edit_metadata_title"))}">${ICONS.edit}</button>` : ""}
            ${r.nativePath ? `<button class="iconbtn plain" data-droprescan="${r._id}"
                  title="${esc(t("action.rescan"))}">${ICONS.pickaxe}</button>` : ""}
            ${r.nativePath && isFixable(r) ? `<button class="iconbtn plain" data-dropfix="${r._id}"
                  title="${esc(t("action.fix_bitrate", {kbps: r.mk}))}">${ICONS.fix}</button>` : ""}
            <span class="sep"></span>
            <button class="iconbtn del" data-dropremove="${r._id}"
                    title="${esc(t("action.remove_from_list"))}">${ICONS.trash}</button>
          </div>
          ${!r.nativePath ? "" : `<div class="actiongroup" data-group="open">
            <div class="grouplabel">${esc(t("action.group_open"))}</div>
            <div class="groupicons">
              ${!EDITOR_NAME ? "" : `<button class="iconbtn plain" data-droprx="${r._id}"
                       title="${esc(t("action.open_in", {name: EDITOR_NAME}))}">${appIcon("editor", EDITOR_NAME)}</button>`}
              ${!DAW_NAME ? "" : `<button class="iconbtn plain" data-dropdaw="${r._id}"
                       title="${esc(t("action.open_in", {name: DAW_NAME}))}">${appIcon("daw", DAW_NAME)}</button>`}
              ${!MIK_NAME ? "" : `<button class="iconbtn plain" data-dropmik="${r._id}"
                           title="${esc(t("action.open_in_mik", {name: MIK_NAME}))}">${
                           appIcon("mik", MIK_NAME)}</button>`}
            </div>
          </div>`}
          ${!ALL_SHOPS.some(s => s.enabled && s.url) ? "" : `<div class="actiongroup" data-group="search">
            <div class="grouplabel">${esc(t("action.group_search"))}</div>
            <div class="groupicons">${shopLinks(r)}</div>
          </div>`}
        </div></td>
      </tr>`).join("")}</tbody>
  </table></div>`;
  syncTableWidths();
  syncToTopButton();

  const tb = host.querySelector("tbody");

  tb.querySelectorAll("tr.row").forEach(tr => tr.onclick = ev => {
    if (ev.target.closest("a") || ev.target.closest("button") || ev.target.closest("input")) return;
    const next = tr.nextElementSibling;
    if (next && next.classList.contains("detail")) { next.remove(); tr.classList.remove("expanded"); return; }
    const r = drops[+tr.dataset.drop];
    const det = document.createElement("tr");
    det.className = "detail expanded";
    det.innerHTML = `<td colspan="${3 + (statusHidden ? 0 : 1) + cols.length}">${detailHTML(r)}</td>`;
    tr.after(det);
    tr.classList.add("expanded");
    mountDropPlayer(det, r);
  });

  tb.querySelectorAll("[data-store]").forEach(b => b.onclick = ev => {
    ev.stopPropagation(); openStore(b.dataset.store, b);
  });
  // Auswahl wie in der Haupttabelle: Shift erweitert vom zuletzt geklickten
  // Kaestchen aus auf einen ganzen Block.
  const dropBoxes = [...tb.querySelectorAll("[data-dropsel]")];
  const applyBox = (box, on) => {
    on ? dropSelected.add(+box.dataset.dropsel) : dropSelected.delete(+box.dataset.dropsel);
    box.checked = on;
    box.closest("tr").classList.toggle("picked", on);
  };
  dropBoxes.forEach((c, pos) => c.onclick = ev => {
    ev.stopPropagation();
    if (ev.shiftKey && lastDropSelPos !== null) {
      const [a, b] = [lastDropSelPos, pos].sort((x, y) => x - y);
      const on = c.checked;
      for (let k = a; k <= b; k++) applyBox(dropBoxes[k], on);
    } else {
      applyBox(c, c.checked);
    }
    lastDropSelPos = pos;
    syncDropSelectAll();
    renderDropBulkBar();
    updateDropHeadButtons();
  });

  const selAll = document.getElementById("dropSelAll");
  if (selAll) selAll.onchange = ev => {
    ev.stopPropagation();
    const on = ev.target.checked;
    dropSelectable().forEach(r => on ? dropSelected.add(r._id) : dropSelected.delete(r._id));
    lastDropSelPos = null;
    renderDrops();
  };
  tb.querySelectorAll("[data-edittags]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.edittags);
    if (row) openTagsPopup(row, true);
  });
  tb.querySelectorAll("[data-dropplay]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const tr = b.closest("tr");
    let det = tr.nextElementSibling;
    if (!det || !det.classList.contains("detail")) { tr.click(); det = tr.nextElementSibling; }
    const pb = det && det.querySelector("[data-act=toggle]");
    if (pb) pb.click();
  });
  tb.querySelectorAll("[data-droprx]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.droprx);
    if (row) openInEditor(row, b);
  });
  tb.querySelectorAll("[data-dropdaw]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.dropdaw);
    if (row) openInDaw(row, b);
  });
  tb.querySelectorAll("[data-droprescan]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.droprescan);
    if (!row) return;
    const before = row.v;
    b.disabled = true;
    try {
      const result = await reanalyseDrops([row]);
      dropStatus(verdictOf(row) === before
        ? t("toast.reanalysed_same", {verdict: labels[verdictOf(row)]})
        : t("toast.reanalysed_changed", {verdict: labels[verdictOf(row)], before: labels[before]}));
      if (result.rekordboxRunning) note(t("toast.rekordbox_check_skipped_running"), "soft");
      renderDrops();
    } catch (err) {
      b.disabled = false;
      dropStatus(t("toast.analysis_failed", {error: err.message}), true);
    }
  });
  tb.querySelectorAll("[data-dropfix]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.dropfix);
    if (row) openFixPopup(row, true);
  });
  tb.querySelectorAll("[data-tagissues]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x.p === b.dataset.tagissues);
    if (row) openTagIssuesPopup(row, true);
  });
  tb.querySelectorAll("[data-tagfixmanual]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    const row = drops.find(x => x.p === b.dataset.tagfixmanual);
    if (row) openTagsPopup(row, true);
  });
  tb.querySelectorAll("[data-tagfixauto]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const row = drops.find(x => x.p === b.dataset.tagfixauto);
    if (!row) return;
    b.disabled = true;
    try {
      const fixed = await fixTagIssuesOne(row, true);
      renderDrops();
      note(t("toast.tag_issues_fixed", {name: baseName(row.p), count: fixed.length}));
    } catch (err) {
      note(t("error.failed", {error: err.message}), true);
      b.disabled = false;
    }
  });
  tb.querySelectorAll("[data-dropmik]").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const row = drops.find(x => x._id === +b.dataset.dropmik);
    if (!row) return;
    if (await openInMik([row], b)) {
      row.mikPick = true;
      renderDrops();
    }
  });
  tb.querySelectorAll("[data-dropremove]").forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    removeDrop(+b.dataset.dropremove);
  });
  syncDropSelectAll();
  renderDropBulkBar();
  updateDropHeadButtons();
}

// ── Sammelaktionen in den Einzelprüfungen ────────────────────────────────
// Dieselbe Leiste wie unter der Haupttabelle (renderBulkBar), nur mit den
// Aktionen, die ohne DB-Zeile ueberhaupt moeglich sind: Ausblenden/Erledigt/
// Korrigiert sind reine Markierungen in der Datenbank und bleiben aussen vor.
// "Bitrate korrigieren" braucht dagegen KEINE DB-Zeile (siehe
// server.py::_post_rewrite_drop(), rewrite.rewrite_bitrate() probt die
// Datei frisch) -- nur bewusst ohne den Sammel-Modus der Haupttabelle, siehe
// Kommentar bei dropBulkFix unten.
function renderDropBulkBar() {
  const bar = document.getElementById("dropBulkbar");
  if (!bar) return;
  const rows = selectedDrops();
  if (!rows.length) { bar.style.display = "none"; bar.innerHTML = ""; return; }
  bar.style.display = "";
  bar.innerHTML = `
    <span>${esc(t("bulk.selected_count", {count: rows.length.toLocaleString("de-DE")}))}</span>
    <button class="iconbtn plain" id="dropBulkTags"
            title="${esc(t("bulk.edit_tags_title"))}">${ICONS.edit}</button>
    <button class="iconbtn plain" id="dropBulkRescan" title="${esc(t("action.rescan"))}">${ICONS.pickaxe}</button>
    <button class="iconbtn plain" id="dropBulkConvert" title="${esc(t("drop.bulk_convert_title"))}">${ICONS.convert}</button>
    ${(() => {
      // Bewusst nur bei GENAU einem ausgewaehlten Track: anders als bei
      // rescan/convert/mik gibt es hier keinen Sammel-Modus, der jede Zeile
      // auf ihre eigene gemessene Bitrate korrigiert (wie renderBulkBar()
      // es fuer die Haupttabelle tut) -- der Dialog fragt einen einzelnen,
      // frei editierbaren Zielwert ab, der sich nicht auf mehrere Dateien
      // uebertragen laesst.
      const canFix = rows.length === 1 && rows[0].nativePath && isFixable(rows[0]);
      return canFix
        ? `<button class="iconbtn plain" id="dropBulkFix"
                   title="${esc(t("action.fix_bitrate", {kbps: rows[0].mk}))}">${ICONS.fix}</button>`
        : `<button class="iconbtn plain" id="dropBulkFix" disabled
                   title="${esc(t("drop.bulk_fix_disabled_hint"))}">${ICONS.fix}</button>`;
    })()}
    ${!MIK_NAME ? "" : `<button class="iconbtn plain" id="dropBulkMik" title="${esc(t("bulk.open_in_mik_title"))}">${appIcon("mik", MIK_NAME)}</button>`}
    <span class="sep"></span>
    <button class="iconbtn del" id="dropBulkRemove"
            title="${esc(t("drop.bulk_remove_title"))}">${ICONS.trash}</button>
    <button class="act small" id="dropBulkClear">${esc(t("bulk.clear_selection"))}</button>`;

  document.getElementById("dropBulkTags").onclick = () => {
    const sel = selectedDrops();
    if (sel.length === 1) openTagsPopup(sel[0], true);
    else if (sel.length > 1) openTagsPopupBulk(sel, true);
  };
  const dropBulkFixBtn = document.getElementById("dropBulkFix");
  if (dropBulkFixBtn && !dropBulkFixBtn.disabled) dropBulkFixBtn.onclick = () => {
    const sel = selectedDrops();
    if (sel.length === 1) openFixPopup(sel[0], true);
  };
  const dropBulkMikBtn = document.getElementById("dropBulkMik");
  if (dropBulkMikBtn) dropBulkMikBtn.onclick = async () => {
    const sel = selectedDrops();
    if (!sel.length) return;
    if (await openInMik(sel, dropBulkMikBtn)) {
      sel.forEach(r => { r.mikPick = true; });
      renderDrops();
    }
  };
  document.getElementById("dropBulkRescan").onclick = async () => {
    const sel = selectedDrops();
    if (!sel.length) return;
    const btn = document.getElementById("dropBulkRescan");
    const before = new Map(sel.map(r => [r._id, verdictOf(r)]));
    btn.disabled = true;
    const total = sel.length;
    const pt = progressToast(total > 1 ? t("drop.rescan.progress", {done: 0, total}) : t("drop.rescan.single"));
    try {
      const result = await reanalyseDrops(sel, (done, n) => pt.update(100 * done / n,
        n > 1 ? t("drop.rescan.progress", {done, total: n}) : t("drop.rescan.single")));
      const changed = sel.filter(r => verdictOf(r) !== before.get(r._id)).length;
      pt.done(t("drop.rescan.done", {count: sel.length}) +
        (changed ? t("drop.rescan.done_changed_suffix", {changed}) : t("drop.rescan.done_unchanged_suffix")));
      if (result.rekordboxRunning) note(t("toast.rekordbox_check_skipped_running"), "soft");
    } catch (err) {
      pt.fail(t("drop.rescan.failed", {error: err.message}));
    }
    renderDrops();
  };
  const dropBulkConvertBtn = document.getElementById("dropBulkConvert");
  if (dropBulkConvertBtn) dropBulkConvertBtn.onclick = () => openConvertPopup(selectedDrops(), true);
  document.getElementById("dropBulkRemove").onclick = () => {
    selectedDrops().forEach(r => removeDrop(r._id, true));
    renderDrops();
  };
  document.getElementById("dropBulkClear").onclick = () => {
    dropSelected.clear();
    renderDrops();
  };
}

// Ruft /api/analyse-path einmal je Pfad auf statt eines Sammelaufrufs -- nur
// so laesst sich zwischen den Dateien ein "x von y"-Fortschritt anzeigen
// (onProgress(done, total)). Der Server analysiert ohnehin nacheinander
// (kein ProcessPoolExecutor wie beim Scan), die zusaetzlichen Anfragen auf
// localhost kosten dabei keine spuerbare Zeit. Ein Fehlschlag bei einer
// Datei bricht nicht die restlichen ab, sondern liefert eine
// status:"error"-Zeile (wie schon einzeln von /api/analyse-path).
// extraChecks (optional): schickt "extra_checks": true mit -- nur vom
// manuellen "neu analysieren"-Knopf (reanalyseDrops()) gesetzt, NICHT beim
// erstmaligen Oeffnen/Ablegen einer Datei, damit der schnelle Erstimport
// keine zusaetzlichen Music.app-/Rekordbox-Aufrufe kostet.
async function analysePathsSequential(paths, onProgress, extraChecks) {
  const rows = [];
  for (let i = 0; i < paths.length; i++) {
    onProgress(i, paths.length);
    try {
      const res = await fetch("/api/analyse-path", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({paths: [paths[i]],
                               ...(extraChecks ? {extra_checks: true} : {})})});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      rows.push({...data.rows[0], _rekordboxRunning: !!data.rekordbox_running});
    } catch (err) {
      rows.push({path: paths[i], status: "error", error: err.message});
    }
  }
  onProgress(paths.length, paths.length);
  return rows;
}

// Misst Einzelprüfungs-Zeilen an ihrem Ort neu und schreibt das Ergebnis in
// die vorhandenen Eintraege zurueck. Bewusst /api/analyse-path statt
// /api/reanalyse: Einzelprüfungen haben keine DB-Zeile (siehe
// server.py:_post_analyse_path). Zugeordnet wird ueber den Pfad, nie ueber
// die Position -- der Server antwortet in eigener Reihenfolge. Fragt dabei
// zusaetzlich Cover/Music.app/Rekordbox ab (extraChecks=true) -- rein
// informativ, es entsteht keine DB-Zeile.
async function reanalyseDrops(rows, onProgress) {
  const serverRows = await analysePathsSequential(rows.map(r => r.p), onProgress || (() => {}), true);
  const byPath = new Map(rows.map(r => [r.p, r]));
  let ok = 0, rekordboxRunning = false;
  for (const row of serverRows) {
    if (row.status === "error") continue;
    if (row._rekordboxRunning) rekordboxRunning = true;
    const target = byPath.get(row.path);
    if (!target) continue;
    // fromServerRow(row, null): die Object-URL der urspruenglich abgelegten
    // Datei darf nicht neu erzeugt werden (sonst leckt die alte), und
    // Herkunft/Identitaet der Zeile bleiben erhalten.
    Object.assign(target, fromServerRow(row, null), {
      _id: target._id, file: target.file, blobUrl: target.blobUrl,
      nativePath: target.nativePath, mikPick: target.mikPick,
      libAdded: target.libAdded,
    });
    const entry = players.get(target.blobUrl || target.p);
    if (entry) { entry.audio.pause(); players.delete(target.blobUrl || target.p); }
    ok++;
  }
  return {ok, rekordboxRunning};
}

// Benennt ausgewaehlte Einzelprüfungen nach dem in den Einstellungen
// hinterlegten Namensmuster um (/api/rename, siehe app/rename.py).
//
// Ruft den Endpunkt einmal je Pfad auf statt eines Sammelaufrufs -- aus
// demselben Grund wie analysePathsSequential(): nur so laesst sich ein
// "x von y"-Fortschritt anzeigen. Jede Datei kostet serverseitig ein
// ffprobe, bei einer groesseren Auswahl waere ein einzelner Aufruf sonst
// minutenlang stumm.
//
// Der Server meldet je Pfad, was passiert ist -- umbenannt, uebersprungen
// (liegt schon in der Bibliothek), ohne Interpret/Titel, oder
// fehlgeschlagen. Umbenannte Zeilen MUESSEN hier sofort auf den neuen Pfad
// gezogen werden (applyDropRename): jede weitere Aktion (abspielen, Tags,
// Cover, neu messen) laeuft ueber r.p, und unter dem alten Namen gibt es
// die Datei nicht mehr.
async function renameDrops(rows, btn) {
  if (btn) { btn.disabled = true; btn.classList.add("busy"); }
  const total = rows.length;
  const label = done => total > 1
    ? t("drop.rename.progress", {done, total})
    : t("drop.rename.single");
  const pt = progressToast(label(0));

  let renamed = 0, inLibrary = 0, unchanged = 0, noData = 0, failed = 0;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    pt.update(100 * i / total, label(i));
    let item;
    try {
      const res = await fetch("/api/rename", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({paths: [row.p]})});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      item = data.results[0];
    } catch (err) {
      item = {status: "error", error: err.message};
    }
    if (item.status === "renamed") {
      applyDropRename(row, item.new_path);
      renamed++;
    } else if (item.status === "in_library") {
      inLibrary++;
    } else if (item.status === "no_data") {
      noData++;
    } else if (item.status === "error") {
      failed++;
      note(t("drop.rename.item_failed",
             {name: baseName(row.p), error: item.error}), true);
    } else {
      unchanged++;                       // hiess schon genau so
    }
  }
  renderDrops();

  const parts = [];
  if (renamed) parts.push(t("drop.rename.done_renamed", {count: renamed}));
  if (inLibrary) parts.push(t("drop.rename.done_in_library", {count: inLibrary}));
  if (unchanged) parts.push(t("drop.rename.done_unchanged", {count: unchanged}));
  if (noData) parts.push(t("drop.rename.done_no_data", {count: noData}));
  if (failed) parts.push(t("drop.rename.done_failed", {count: failed}));
  // Rot nur bei echten Fehlschlaegen; "nichts zu tun" (alles liegt schon in
  // der Bibliothek oder hiess bereits richtig) ist gelb, nicht rot -- siehe
  // toastLevel().
  pt.done(parts.join(" · ") || t("drop.rename.done_none"),
          failed ? true : (renamed ? false : "soft"));
  if (btn) { btn.disabled = false; btn.classList.remove("busy"); }
}

// Zieht eine Einzelprüfungs-Zeile auf den neuen Pfad um. Nur der Pfad
// aendert sich -- der Dateiinhalt ist derselbe, es wird also nichts neu
// gemessen. Was am alten Pfad haengt, muss trotzdem weg: der laufende
// Player (players ist nach Pfad verschluesselt und wuerde weiter die alte,
// nicht mehr existierende URL laden) und der Cover-Cachebuster.
function applyDropRename(r, newPath) {
  const oldKey = r.blobUrl || r.p;
  const entry = players.get(oldKey);
  if (entry) { entry.audio.pause(); players.delete(oldKey); }
  if (coverRev.has(r.p)) {
    coverRev.set(newPath, coverRev.get(r.p));
    coverRev.delete(r.p);
  }
  dropScopeCache(r);
  r.p = newPath;
}

// Entfernt nur den Eintrag aus der Einzelprüfungs-Liste (drops-Array) --
// ruehrt die eigentliche Datei nicht an, anders als der Papierkorb-Knopf
// in der Haupttabelle.
// defer: beim Entfernen mehrerer Zeilen am Stueck erst am Ende einmal neu
// zeichnen, statt bei jeder einzelnen.
function removeDrop(id, defer) {
  const idx = drops.findIndex(x => x._id === id);
  if (idx < 0) return;
  const [r] = drops.splice(idx, 1);
  if (r.blobUrl) URL.revokeObjectURL(r.blobUrl);
  dropSelected.delete(id);
  const key = r.blobUrl || r.p;
  const entry = players.get(key);
  if (entry) { entry.audio.pause(); players.delete(key); }
  if (!defer) renderDrops();
}

// Auswaehlbar (und damit umbenennbar/importierbar) sind nur Zeilen mit
// echtem, dauerhaftem Pfad -- siehe dropSelectable().
function updateDropHeadButtons() {
  const n = selectedDrops().length;
  const lib = document.getElementById("dropAddLib");
  if (lib) {
    // Ohne ausgewaehlte Music App bleibt der Import-Knopf ganz weg, gleiches
    // Prinzip wie bei btnMusicAddedSync (siehe MUSIC_NAME) -- der Import
    // schreibt sonst trotzdem still in Music.app hinein.
    lib.style.display = MUSIC_NAME ? "" : "none";
    lib.disabled = n === 0;
    lib.innerHTML = `<span class="btnicon">${ICONS.libraryBig}</span>` +
      esc(n ? t("drop.add_lib_button_count", {count: n}) : t("drop.add_lib_button"));
  }
  const ren = document.getElementById("dropRename");
  if (ren) {
    ren.disabled = n === 0;
    ren.innerHTML = `<span class="btnicon">${ICONS.rename}</span>` +
      esc(n ? t("drop.rename_button_count", {count: n}) : t("drop.rename_button"));
  }
}

// Eigene Funktion statt Top-Level-Code in initDropzone(): initDropzone()
// haengt auch Drag&Drop-/Klick-Listener an das Fenster, ein zweiter Aufruf
// nach einer spaeteren Sprachwahl (siehe refreshI18nCache()) wuerde die
// doppelt registrieren.
function applyDropzoneLabels() {
  document.getElementById("dropPick").innerHTML =
    `<span class="btnicon">${ICONS.folder}</span> ${esc(t("drop.pick_button"))}`;
  updateDropHeadButtons();
  document.getElementById("dropClear").innerHTML =
    `<span class="btnicon">${ICONS.trash}</span> ${esc(t("drop.clear_button"))}`;
}

function initDropzone() {
  applyDropzoneLabels();
  const overlay = document.getElementById("dropOverlay");
  let depth = 0;
  // In der Player-Ansicht entfaellt die Einzelpruefung samt Drag&Drop
  // komplett (siehe Issue #14) -- das Panel wird per CSS ausgeblendet, die
  // vier fensterweiten Listener hier muessen aber selbst abschalten, sonst
  // reagiert ein Drag ueber die Seite trotzdem noch (Overlay/Import).
  window.addEventListener("dragenter", e => {
    if (state.layout === "player") return;
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault(); depth++; overlay.style.display = "flex";
  });
  window.addEventListener("dragover", e => {
    if (state.layout === "player") return;
    if ([...e.dataTransfer.types].includes("Files")) e.preventDefault();
  });
  window.addEventListener("dragleave", e => {
    if (state.layout === "player") return;
    if (--depth <= 0) { depth = 0; overlay.style.display = "none"; }
  });
  window.addEventListener("drop", e => {
    if (state.layout === "player") return;
    e.preventDefault(); depth = 0; overlay.style.display = "none";
    if (e.dataTransfer.files.length) handleDropped(e.dataTransfer.files);
  });
  document.getElementById("dropClear").onclick = () => {
    for (const r of drops) if (r.blobUrl) URL.revokeObjectURL(r.blobUrl);
    drops.length = 0;
    dropSelected.clear();
    renderDrops();
  };
  // Ohne Server (Fallback) bleibt nur die Browser-Dateiauswahl -- die
  // verrät nie den echten Pfad (siehe handleDropped/analyseFile), Import in
  // die Music-Bibliothek geht dann nicht (r.nativePath bleibt unset). Läuft der
  // Server, nutzt der Knopf den nativen macOS-Dialog und liefert damit
  // echte, dauerhafte Pfade.
  document.getElementById("dropPick").onclick = async () => {
    if (!apiMode) { document.getElementById("dropInput").click(); return; }
    const btn = document.getElementById("dropPick");
    btn.disabled = true;
    try {
      const res = await fetch("/api/open-file-pick", {method: "POST"});
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || t("error.unknown"));
      if (!data.cancelled && data.paths && data.paths.length) {
        const total = data.paths.length;
        let ok = 0, failed = 0;
        const pt = progressToast(total > 1 ? t("drop.pick.progress", {done: 0, total}) : t("drop.pick.single"));
        const rows = await analysePathsSequential(data.paths, (done) => pt.update(100 * done / total,
          total > 1 ? t("drop.pick.progress", {done, total}) : t("drop.pick.single")));
        for (const row of rows) {
          if (row.status === "error") {
            failed++;
            note(t("drop.pick.item_failed", {name: baseName(row.path), error: row.error}), true);
            continue;
          }
          const r = fromServerRow(row, null);
          r.nativePath = true;
          drops.unshift(r);
          ok++;
          renderDrops();
        }
        pt.done(t("drop.pick.done", {ok, plural: ok === 1 ? "" : "s"}) +
          (failed ? t("drop.pick.done_failed_suffix", {failed}) : ""), !!failed && !ok);
      }
    } catch (err) {
      note(t("drop.pick.failed", {error: err.message}), true);
    }
    btn.disabled = false;
  };
  document.getElementById("dropInput").onchange = e => {
    if (e.target.files.length) handleDropped(e.target.files);
    e.target.value = "";
  };

  document.getElementById("dropRename").onclick = async () => {
    const rows = selectedDrops();
    if (!rows.length) return;
    await renameDrops(rows, document.getElementById("dropRename"));
  };

  document.getElementById("dropAddLib").onclick = async () => {
    const rows = selectedDrops();
    if (!rows.length) return;
    const btn = document.getElementById("dropAddLib");
    const data = await addToLibrary(rows, btn);
    if (!data) { renderDrops(); return; }

    // Ab hier liegen die Tracks dauerhaft im Medienordner und haben eine
    // DB-Zeile -- sie gehoeren damit in die Bibliotheksliste, nicht mehr in
    // die Einzelprüfungen. Der Eintrag wandert also wirklich hinueber statt
    // in beiden Listen zu stehen.
    const indices = adoptLibraryRows(data.rows);
    rows.forEach(r => { r.libAdded = true; removeDrop(r._id, true); });
    renderDrops();
    dropStatus(t("drop.imported", {count: rows.length, plural: rows.length === 1 ? "" : "s"}));
    revealImported(indices);
  };
}

// ── Leertaste: laufenden Track anhalten und weiterspielen ────────────────
// Greift nur, wenn gerade nicht in ein Eingabefeld getippt wird — sonst
// liesse sich in der Suche kein Leerzeichen mehr eingeben.
const isTyping = el => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);

// Nur noch fuer die Einzelpruefungen (Drops) relevant -- Bibliothekszeilen
// laufen inzwischen immer ueber den globalen queueAudio (siehe
// mountLibraryPlayer()/repaintWaveViews() weiter oben), landen also nie in
// players.
function currentDropPlayer() {
  for (const p of players.values()) if (!p.audio.paused) return p;   // was läuft, hat Vorrang
  if (activeKey && players.has(activeKey)) return players.get(activeKey);
  return players.size === 1 ? [...players.values()][0] : null;
}

// ── Cmd/Strg+Z: Rueckgaengig in der geoeffneten Playlist ─────────────────
// Wie die Leertaste nur ausserhalb von Eingabefeldern -- in einem Textfeld
// gehoert Cmd+Z dem Feld selbst. Ausserdem nur, wenn keine Ueberlagerung
// offen ist: dort waere nicht ersichtlich, worauf sich der Schritt bezieht.
document.addEventListener("keydown", ev => {
  if (ev.key !== "z" && ev.key !== "Z") return;
  if (!ev.metaKey && !ev.ctrlKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  if (!currentPlaylistNode() || !apiMode) return;
  if ([...document.querySelectorAll(".overlay")].some(o => o.style.display === "flex")) return;
  ev.preventDefault();
  runUndo(ev.shiftKey ? "redo" : "undo");
});

document.addEventListener("keydown", ev => {
  if (ev.code !== "Space" && ev.key !== " ") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;             // Rückfrage offen

  // Ein laufender Einzelpruefungs-Player hat Vorrang vor dem globalen Player
  // (er wird ja gerade explizit gehoert) -- sonst steuert die Leertaste
  // immer queueAudio, unabhaengig von state.layout (siehe repaintWaveViews()/
  // startQueueFrom() -- der globale Player laeuft in beiden Ansichten).
  const dp = currentDropPlayer();
  if (dp) {
    ev.preventDefault();
    // Ein noch fokussierter Knopf würde die Leertaste sonst ein zweites Mal
    // auswerten und die Wiedergabe sofort wieder umschalten.
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (dp.audio.paused) dp.audio.play().catch(err => playFailed(dp.audio, err));
    else dp.audio.pause();
    return;
  }

  ev.preventDefault();
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  if (!currentQueueTrack()) {
    const first = filtered().find(r => !r.gone);
    if (first) startQueueFrom(first);
    return;
  }
  if (queueAudio.paused) queueAudio.play().catch(err => playFailed(queueAudio, err));
  else queueAudio.pause();
});

// ── f: Suchfeld fokussieren ───────────────────────────────────────────
document.addEventListener("keydown", ev => {
  if (ev.key.toLowerCase() !== "f") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  ev.preventDefault();
  document.getElementById("q").focus();
});

// ── Pfeil links/rechts: vorherigen/naechsten Track in der Warteschlange ──
// Steuert immer den globalen Player (queueAudio/queueState), unabhaengig von
// state.layout -- Bibliothekszeilen haben keinen eigenen "aktiven Player"
// mehr (siehe mountLibraryPlayer()). Pfeil hoch/runter bewegt stattdessen
// einen reinen Navigations-Cursor, siehe moveCursor() weiter unten.
document.addEventListener("keydown", ev => {
  if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  ev.preventDefault();
  if (ev.key === "ArrowRight") queueNext(true); else queuePrev();
});

// ── Pfeil hoch/runter: Tastatur-Cursor durch die Tabelle bewegen ─────────
// Rein visuelle Navigations-Markierung (Klasse .kbcursor), unabhaengig von
// der Checkbox-Mehrfachauswahl (state.selected) und der Wiedergabe -- eine
// bereits vorbereitete Sammelaktions-Auswahl bleibt beim Blättern also
// unangetastet. Haelt r.i statt einer Position fest, damit der Cursor eine
// Sortierung/Filteraenderung uebersteht, solange die Zeile noch existiert.
let cursorRowI = null;

// Startpunkt ohne vorhandenen Cursor: der aktuelle Warteschlangen-Track,
// unabhaengig von state.layout (siehe Pfeil-links/rechts-Handler oben).
function currentActiveRowForCursor() {
  return currentQueueTrack();
}

function setCursorRow(i) {
  if (cursorRowI !== null) {
    const prevTr = document.querySelector(`tr.row[data-i="${cursorRowI}"]`);
    if (prevTr) prevTr.classList.remove("kbcursor");
  }
  cursorRowI = i;
  if (i === null) return;
  const tr = document.querySelector(`tr.row[data-i="${i}"]`);
  if (tr) {
    tr.classList.add("kbcursor");
    // Bewusst kein tr.scrollIntoView(): an echtem Material beobachtet, dass
    // das bei groesserem Sprung (weit entfernte Ausgangs-Scrollposition) auf
    // dieser Tabelle verlaesslich an der falschen Stelle landet -- <tr> ist
    // kein normaler Block, seine Geometrie kommt vom Tabellenlayout.
    // Zielposition deshalb selbst ausrechnen, nur scrollen wenn die Zeile
    // wirklich ausserhalb liegt.
    const rect = tr.getBoundingClientRect();
    if (rect.top < 0) {
      window.scrollTo(0, window.scrollY + rect.top - 12);
    } else if (rect.bottom > window.innerHeight) {
      window.scrollTo(0, window.scrollY + (rect.bottom - window.innerHeight) + 12);
    }
  }
}

function moveCursor(dir) {
  const rows = visibleRows();
  if (!rows.length) return;
  let idx;
  if (cursorRowI !== null) {
    const curIdx = rows.findIndex(r => r.i === cursorRowI);
    idx = curIdx === -1 ? (dir > 0 ? 0 : rows.length - 1) : curIdx + dir;
  } else {
    const activeR = currentActiveRowForCursor();
    const activeIdx = activeR ? rows.findIndex(r => r.i === activeR.i) : -1;
    idx = activeIdx !== -1 ? activeIdx : (dir > 0 ? 0 : rows.length - 1);
  }
  idx = Math.max(0, Math.min(rows.length - 1, idx));
  setCursorRow(rows[idx].i);
}

document.addEventListener("keydown", ev => {
  if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  ev.preventDefault();
  moveCursor(ev.key === "ArrowDown" ? 1 : -1);
});

// ── Eingabetaste: Wiedergabe der Cursor-Zeile starten ────────────────────
document.addEventListener("keydown", ev => {
  if (ev.key !== "Enter") return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  if (cursorRowI === null) return;
  const r = DATA.find(x => x.i === cursorRowI);
  if (!r || r.gone) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  ev.preventDefault();
  if (state.layout === "player") { startQueueFrom(r); return; }
  const btn = document.querySelector(`[data-play="${r.i}"]`);
  if (btn) btn.click();
});

// ── Cmd+Löschen: ausgewählte Tracks in den Papierkorb ────────────────────
// Bezieht sich auf die Checkbox-Auswahl (state.selected), nicht auf die
// gesamte dargestellte Liste. Laeuft ueber bulkApply("trash") -- dieselbe
// Rueckfrage (askTrashBulk) wie beim Klick auf den Papierkorb-Knopf der
// Sammelaktionsleiste.
document.addEventListener("keydown", ev => {
  if (ev.key !== "Backspace" && ev.key !== "Delete") return;
  if (!ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  if (!state.selected.size) return;
  ev.preventDefault();
  bulkApply("trash");
});

// ── Ziffern 1-8: Hot Cue A-H auf dem markierten Track anspringen ─────────
// Nur wirksam, wenn der Ziel-Track ueberhaupt Hot Cues aus Rekordbox
// mitbringt (siehe rekordboxExtrasFor) -- sonst passiert nichts. Fuer
// Bibliothekszeilen ausserdem nur, wenn deren Wellenform gerade aufgeklappt
// ist (nur dann sind die Cues geladen, siehe mountLibraryPlayer()) -- exakt
// dieselbe Einschraenkung wie zuvor bei den Einzelpruefungen.
document.addEventListener("keydown", ev => {
  if (!/^[1-8]$/.test(ev.key)) return;
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (isTyping(ev.target) || isTyping(document.activeElement)) return;
  const dialog = document.getElementById("confirmOverlay");
  if (dialog && dialog.style.display === "flex") return;
  const slot = Number(ev.key);

  const dp = currentDropPlayer();
  if (dp && dp.cues) {
    const cue = dp.cues.find(c => c.kind === "hot" && c.slot === slot);
    if (!cue) return;
    ev.preventDefault();
    for (const pl of players.values()) if (pl !== dp) pl.audio.pause();
    queueAudio.pause();
    dp.audio.currentTime = cue.position_s;
    dp.audio.play().catch(err => playFailed(dp.audio, err));
    return;
  }

  // Ziel ist die markierte Zeile (cursorRowI, dieselbe Markierung wie bei
  // Pfeiltasten-Navigation/Klick, siehe setCursorRow()) -- NICHT zwingend der
  // gerade im Player laufende Track. Ohne Markierung (frisch geladene Seite,
  // noch nie navigiert/geklickt) faellt das auf den aktuell gespielten Track
  // zurueck, wie zuvor.
  const targetR = cursorRowI !== null ? DATA.find(x => x.i === cursorRowI) : queueState.current;
  if (!targetR || targetR.gone) return;
  const view = waveViews.get(targetR.i);
  if (!view || !view.cues) return;
  const cue = view.cues.find(c => c.kind === "hot" && c.slot === slot);
  if (!cue) return;
  ev.preventDefault();
  for (const pl of players.values()) pl.audio.pause();
  if (queueState.current && queueState.current.i === targetR.i) {
    // Markierter Track laeuft bereits im Player -- direkt springen, wie zuvor.
    queueAudio.currentTime = cue.position_s;
    queueAudio.play().catch(err => playFailed(queueAudio, err));
  } else {
    // Markierter Track ist (noch) nicht geladen -- erst starten, die
    // Zielposition zieht queueAudio.onloadedmetadata dann per pendingSeek
    // nach (gleicher Mechanismus wie beim Wellenform-Klick auf eine noch
    // nicht aktuelle Zeile, siehe mountLibraryPlayer()).
    pendingSeek = cue.position_s;
    startQueueFrom(targetR);
  }
});

// Knopfbeschriftungen mit Icon -- als Funktion statt Top-Level-Zuweisung,
// damit refreshI18nCache() sie nach einer expliziten Sprachwahl in den
// Einstellungen (siehe initStorage()) neu setzen kann.
function applyToolbarLabels() {
  document.getElementById("btnCloseSettings").innerHTML =
    `<span class="btnicon-lg">${ICONS.squareX}</span> ${esc(t("action.cancel"))}`;
  document.getElementById("btnSave").innerHTML =
    `<span class="btnicon-lg">${ICONS.save}</span> ${esc(t("action.save"))}`;
  document.getElementById("btnScan").innerHTML =
    `<span class="btnicon">${ICONS.listRestart}</span> ${esc(t("toolbar.scan"))}`;
  document.getElementById("btnRekordboxSync").innerHTML =
    `<span class="btnicon">${ICONS.update}</span> ${esc(t("toolbar.rekordbox_sync"))}`;
  document.getElementById("btnMusicAddedSync").innerHTML =
    `<span class="btnicon">${ICONS.update}</span> ${esc(t("toolbar.music_sync"))}`;
  document.getElementById("btnQuit").innerHTML =
    `<span class="btnicon-lg">${ICONS.power}</span> ${esc(t("quit.button"))}`;
  document.querySelector('#layoutSwitch [data-layout="edit"]').innerHTML =
    `<span class="btnicon">${ICONS.pencilRuler}</span> ${esc(t("layout.edit"))}`;
  document.querySelector('#layoutSwitch [data-layout="player"]').innerHTML =
    `<span class="btnicon">${ICONS.player}</span> ${esc(t("layout.player"))}`;
}
applyToolbarLabels();

// queueAudio selbst ist kein DOM-Element (new Audio()), diese Verdrahtung
// darf deshalb schon hier oben top-level stehen -- nur die Knopf-Wiring
// weiter unten (DOMContentLoaded) muss auf den Balken im HTML warten.
queueAudio.onplay = () => {
  // Gegenrichtung zu mountDropPlayer(): startet der globale Player, darf
  // kein Einzelpruefungs-Player mehr gleichzeitig laufen.
  for (const p of players.values()) p.audio.pause();
  listenTrackStart(queueState.current, queueAudio);
  renderPlayerBar();
  scheduleWavePaint();
};
queueAudio.onpause = () => { renderPlayerBar(); requestWavePaint(); };
queueAudio.onseeked = () => requestWavePaint();
// Nur noch Hoerzeit-Statistik -- gezeichnet wird aus wavePump().
queueAudio.ontimeupdate = () => listenTrackTick(queueAudio);
queueAudio.onloadedmetadata = () => {
  // Wellenform-/Cue-Klick auf eine noch nicht aktuelle Bibliothekszeile
  // (siehe mountLibraryPlayer()): die Zielposition konnte erst jetzt gesetzt
  // werden, da die Dauer vorher nicht feststand.
  if (pendingSeek != null) { queueAudio.currentTime = pendingSeek; pendingSeek = null; }
  // Erst mit der Dauer lassen sich Cue-Marker platzieren. Ein Entwerten der
  // statischen Ebene braucht es dafuer nicht -- die Marker stecken nicht
  // darin, sondern werden pro Bild gezeichnet (siehe paintWaveEntry()), und
  // entry.sync() liefert die Dauer im naechsten Bild mit.
  _pbarSec = null;
  renderPlayerProgress();
  requestWavePaint();
};
queueAudio.onended = () => { listenFinish(); queueNext(false); };

// Media-Tasten (F7/F9 bzw. "Vorheriger/Naechster Titel" auf externen
// Tastaturen): Play/Pause (F8) landet ohne Zutun beim <audio>-Element, weil
// der Browser dafuer selbst einen Standard-Handler anlegt, sobald es spielt
// -- fuer "vorheriger/naechster Titel" gibt es keinen Standard, das muss die
// Seite explizit ueber die Media Session API anmelden.
if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("previoustrack", () => queuePrev());
  navigator.mediaSession.setActionHandler("nexttrack", () => queueNext(true));
}

// #playerBar/#queuePopup stehen wie #toTopBtn im HTML nach diesem
// <script>-Block und existieren erst bei DOMContentLoaded. Icon-Symbole
// aendern sich nicht mit der Sprache (Tooltips kommen ueber data-i18n-title/
// applyStaticI18n()) -- deshalb hier einmalig statt in applyToolbarLabels(),
// das auch VOR DOMContentLoaded laeuft (Erststart, siehe oben) und dort auf
// diese Elemente noch ins Leere zeigen wuerde.
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("pbarShuffle").innerHTML = ICONS.shuffle;
  document.getElementById("pbarPrev").innerHTML = ICONS.prev;
  document.getElementById("pbarPlay").innerHTML = ICONS.play;
  document.getElementById("pbarNext").innerHTML = ICONS.next;
  document.getElementById("pbarRepeatTrack").innerHTML = ICONS.repeatTrack;
  document.getElementById("pbarRepeatList").innerHTML = ICONS.repeatList;
  document.getElementById("pbarQueueIcon").innerHTML = ICONS.listVideo;

  loadVolumeState();
  renderVolumeUI();
  document.getElementById("pbarMute").onclick = toggleMute;
  document.getElementById("pbarVolume").oninput = ev => applyVolume(+ev.target.value / 100);
  setupVolumeBubble();

  setupTitleMarquee();

  document.getElementById("pbarPlay").onclick = () => {
    if (!currentQueueTrack()) return;
    if (queueAudio.paused) queueAudio.play().catch(err => playFailed(queueAudio, err));
    else queueAudio.pause();
  };
  document.getElementById("pbarNext").onclick = () => queueNext(true);
  document.getElementById("pbarPrev").onclick = () => queuePrev();
  document.getElementById("pbarShuffle").onclick = toggleShuffle;
  document.getElementById("pbarRepeatTrack").onclick = toggleRepeatTrack;
  document.getElementById("pbarRepeatList").onclick = toggleRepeatList;
  document.getElementById("pbarQueueBtn").onclick = () => toggleQueuePopup();
  document.getElementById("queuePopupClose").onclick = () => toggleQueuePopup(false);
  document.getElementById("queueClearBtn").onclick = () => clearQueue();
  document.querySelectorAll("[data-qptab]").forEach(b => b.onclick = () => switchQueuePopupTab(b.dataset.qptab));
  document.getElementById("pbarTrack").onclick = () => revealCurrentQueueTrack();
  setupSeekDrag();
  // Gemerkte Warteschlange restaurieren (siehe loadQueueState() weiter oben),
  // dann Erststand zeichnen (Play-Symbol, Shuffle/Repeat-Zustand aus
  // loadFilters(), Warteschlangen-Zaehler) -- der fruehere renderPlayerBar()-
  // Aufruf ganz unten im Skript lief ins Leere, weil der Balken zu dem
  // Zeitpunkt noch nicht existierte.
  loadQueueState();
  renderPlayerBar();
});

document.querySelectorAll("#layoutSwitch [data-layout]").forEach(b => {
  b.onclick = () => applyLayout(b.dataset.layout);
});

document.getElementById("btnSettings").onclick = openSettings;
document.getElementById("btnSave").onclick = () => postSettings("/api/settings", collectSettings());

// ── Statistik ────────────────────────────────────────────────────────────
// Jahres-/Monats-Auswertung aus audit_log.py + der events-Tabelle (siehe
// app/stats.py), lazy vom Server gebaut (/api/stats). Eigenstaendiges
// Vollbild-Overlay wie die Einstellungen (kein Bezug zu VIEWS/der
// Tabellenfilterung) statt eines Sidebar-Eintrags -- die Statistik filtert
// keine Zeilen, sie fasst sie zusammen.
let STATS = null;
let statsYear = null;

function statsFmtDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

// "rekordbox-pfad-korrigiert" -> "stats.action.rekordbox_pfad_korrigiert" --
// die rohen Aktions-Strings kommen 1:1 aus audit_log.py, die Uebersetzung
// passiert ausschliesslich hier client-seitig.
function statsActionKey(action) {
  return "stats.action." + String(action).replace(/[-\s]+/g, "_");
}

function statsBarChartSVG(values, opts) {
  opts = opts || {};
  const w = 600, h = 160, pad = 4, barGap = 4, baseline = 20;
  const n = values.length;
  const barW = (w - pad * 2 - barGap * (n - 1)) / n;
  const max = Math.max(1, ...values);
  const monthNames = [...Array(12)].map((_, i) =>
    new Date(2000, i, 1).toLocaleDateString("de-DE", {month: "short"}));
  const bars = values.map((v, i) => {
    const barH = v > 0 ? Math.max(2, Math.round((v / max) * (h - baseline - 16))) : 0;
    const x = pad + i * (barW + barGap);
    const y = h - baseline - barH;
    const label = opts.fmt ? opts.fmt(v) : Math.round(v).toLocaleString("de-DE");
    return `<g><title>${esc(monthNames[i])}: ${esc(label)}</title>` +
      `<rect x="${x.toFixed(1)}" y="${y}" width="${barW.toFixed(1)}" height="${barH}" rx="2" ` +
      `class="statschart-bar"></rect>` +
      `<text x="${(x + barW / 2).toFixed(1)}" y="${h - 6}" text-anchor="middle" ` +
      `class="statschart-label">${esc(monthNames[i])}</text></g>`;
  }).join("");
  return `<svg class="statschart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}</svg>`;
}

function statsActionsListHTML(actions) {
  const entries = Object.entries(actions).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return `<div class="stats-empty">${esc(t("stats.no_data"))}</div>`;
  return `<table class="stats-actiontable"><tbody>` + entries.map(([action, count]) => `
      <tr><td>${esc(t(statsActionKey(action)))}</td>` +
      `<td class="stats-actioncount">${count.toLocaleString("de-DE")}</td></tr>`
  ).join("") + `</tbody></table>`;
}

function statsTopTracksGrid(tracks) {
  if (!tracks.length) return `<div class="stats-empty">${esc(t("stats.no_data"))}</div>`;
  return `<div class="stats-toptracks">` + tracks.slice(0, 10).map((tr, i) => `
      <div class="stats-trackcard">
        <div class="stats-trackrank">${i + 1}</div>
        ${tr.has_cover
          ? `<img class="stats-trackcover" loading="lazy" src="${coverUrl(tr.path)}" alt="">`
          : `<div class="stats-trackcover stats-trackcover-empty">${ICONS.player}</div>`}
        <div class="stats-trackmeta">
          <div class="stats-tracktitle" title="${esc(tr.title)}">${esc(tr.title)}</div>
          <div class="stats-trackartist" title="${esc(tr.artist)}">${esc(tr.artist || "")}</div>
          <div class="stats-tracktime">${tr.seconds != null ? esc(statsFmtDuration(tr.seconds)) + " · " : ""}${tr.plays}×</div>
        </div>
      </div>`
  ).join("") + `</div>`;
}

function statsRankedList(items, field, opts) {
  opts = opts || {};
  const valueField = opts.valueField || "seconds";
  const fmt = opts.fmt || statsFmtDuration;
  if (!items.length) return `<div class="stats-empty">${esc(t("stats.no_data"))}</div>`;
  const max = Math.max(1, ...items.map(it => it[valueField]));
  return `<div class="stats-rankedlist">` + items.map((it, i) => `
      <div class="stats-rankrow">
        <div class="stats-ranknum">${i + 1}</div>
        <div class="stats-rankbar-wrap">
          <div class="stats-rankname" title="${esc(it[field])}">${esc(it[field])}</div>
          <div class="stats-rankbar"><div class="stats-rankbar-fill" ` +
          `style="width:${Math.round(it[valueField] / max * 100)}%"></div></div>
        </div>
        <div class="stats-rankvalue">${esc(fmt(it[valueField]))}</div>
      </div>`
  ).join("") + `</div>`;
}

// "×" statt Hoerzeit als Wertformat fuer play-count-basierte Ranglisten
// (Rekordbox-History kennt keine Wiedergabedauer je Eintrag).
function statsFmtPlays(n) {
  return `${n}×`;
}

function renderStatsYearSelect() {
  const sel = document.getElementById("statsYearSelect");
  const years = STATS.available_years || [];
  sel.disabled = !years.length;
  if (!years.length) { sel.innerHTML = ""; return; }
  if (!statsYear || !years.includes(statsYear)) statsYear = years[0];
  sel.innerHTML = years.map(y =>
    `<option value="${esc(y)}"${y === statsYear ? " selected" : ""}>${esc(y)}</option>`).join("");
  sel.onchange = () => { statsYear = sel.value; renderStatsBody(); };
}

function renderStatsBody() {
  const body = document.getElementById("statsBody");
  const year = STATS.years[statsYear];
  if (!year) {
    body.innerHTML = `<div class="stats-empty">${esc(t("stats.no_data"))}</div>`;
    return;
  }
  const monthActions = year.months.map(m =>
    Object.values(m.actions).reduce((a, b) => a + b, 0));
  const monthListen = year.months.map(m => m.listen_seconds);
  const actionsTotal = Object.values(year.actions).reduce((a, b) => a + b, 0);
  body.innerHTML = `
    <div class="stats-kpis">
      <div class="stats-kpi"><div class="stats-kpi-value">${actionsTotal.toLocaleString("de-DE")}</div>
        <div class="stats-kpi-label">${esc(t("stats.actions_total"))}</div></div>
      <div class="stats-kpi"><div class="stats-kpi-value">${esc(statsFmtDuration(year.listen_seconds))}</div>
        <div class="stats-kpi-label">${esc(t("stats.listen_time_total"))}</div></div>
    </div>
    <div class="stats-charts">
      <div class="stats-chartbox">
        <h4>${esc(t("stats.chart_actions_title"))}</h4>
        ${statsBarChartSVG(monthActions)}
      </div>
      <div class="stats-chartbox">
        <h4>${esc(t("stats.chart_listen_title"))}</h4>
        ${statsBarChartSVG(monthListen, {fmt: statsFmtDuration})}
      </div>
    </div>
    <div class="stats-section">
      <h4>${esc(t("stats.actions_list_title"))}</h4>
      ${statsActionsListHTML(year.actions)}
    </div>
    <div class="stats-section">
      <div class="stats-sectionhead">
        <h4>${esc(t("stats.top_tracks_title"))}</h4>
        <button class="act small" id="btnStatsPlaylist"${year.top_tracks.length ? "" : " disabled"}>${esc(t("stats.playlist_button"))}</button>
      </div>
      ${statsTopTracksGrid(year.top_tracks)}
    </div>
    <div class="stats-columns">
      <div class="stats-section">
        <h4>${esc(t("stats.top_artists_title"))}</h4>
        ${statsRankedList(year.top_artists, "artist")}
      </div>
      <div class="stats-section">
        <h4>${esc(t("stats.top_genres_title"))}</h4>
        ${statsRankedList(year.top_genres, "genre")}
      </div>
    </div>
    ${REKORDBOX_NAME ? `
    <div class="stats-section">
      <h4>${esc(t("stats.rekordbox_section_title"))}</h4>
      ${statsTopTracksGrid(year.rekordbox_top_tracks || [])}
    </div>
    <div class="stats-columns">
      <div class="stats-section">
        <h4>${esc(t("stats.rekordbox_top_artists_title"))}</h4>
        ${statsRankedList(year.rekordbox_top_artists || [], "artist", {valueField: "plays", fmt: statsFmtPlays})}
      </div>
      <div class="stats-section">
        <h4>${esc(t("stats.rekordbox_top_genres_title"))}</h4>
        ${statsRankedList(year.rekordbox_top_genres || [], "genre", {valueField: "plays", fmt: statsFmtPlays})}
      </div>
    </div>` : ""}`;
  document.getElementById("btnStatsPlaylist").onclick = createTopTracksPlaylist;
}

async function loadStats() {
  const status = document.getElementById("statsStatus");
  status.textContent = "";
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
    STATS = await res.json();
    return true;
  } catch (err) {
    STATS = {available_years: [], years: {}};
    status.textContent = t("stats.load_failed", {error: err.message});
    return false;
  }
}

function closeStats() {
  document.getElementById("statsOverlay").style.display = "none";
  document.onkeydown = null;
}

// /api/stats baut die Statistik bei Bedarf synchron neu (siehe
// stats.is_stale()/server._get_stats()) -- das kann bei vielen Ereignissen
// spuerbar dauern. Ein Fortschritts-Toast waehrend dieses einen Requests
// statt des Popups selbst: das Popup oeffnet erst, wenn die Daten fuer das
// aktuelle Jahr (STATS.available_years[0], siehe renderStatsYearSelect())
// vollstaendig da sind, statt vorher leer aufzuklappen.
async function openStats() {
  if (!apiMode) { note(t("toast.needs_server"), "soft"); return; }
  const pt = progressToast(t("stats.generating"), {indeterminate: true});
  const ok = await loadStats();
  if (!ok) { pt.fail(document.getElementById("statsStatus").textContent); return; }
  pt.done(t("stats.generated"));
  document.getElementById("statsOverlay").style.display = "flex";
  document.getElementById("btnCloseStats").onclick = closeStats;
  document.onkeydown = e => { if (e.key === "Escape") closeStats(); };
  renderStatsYearSelect();
  renderStatsBody();
}

async function rebuildStats() {
  const btn = document.getElementById("btnStatsRebuild");
  const status = document.getElementById("statsStatus");
  btn.disabled = true;
  status.textContent = t("stats.rebuild_running");
  try {
    const res = await fetch("/api/stats/rebuild", {method: "POST"});
    if (!res.ok) throw new Error(t("error.server_status", {status: res.status}));
    STATS = await res.json();
    status.textContent = t("stats.rebuild_done");
    renderStatsYearSelect();
    renderStatsBody();
  } catch (err) {
    status.textContent = t("stats.rebuild_failed", {error: err.message});
  } finally {
    btn.disabled = false;
  }
}

// "Playlist aus Top 50 erstellen" -- braucht keinen neuen Server-Endpunkt,
// nutzt die bestehende Playlist-API (siehe createPlaylistNode() oben fuer
// dasselbe Muster: askPlaylistProps() -> /api/playlist create -> Items
// setzen -> reloadPlaylists() -> Spaltenansicht -> selectView()).
async function createTopTracksPlaylist() {
  const year = STATS.years[statsYear];
  const tracks = (year && year.top_tracks) || [];
  if (!tracks.length) { note(t("stats.no_plays_for_playlist"), true); return; }
  const props = await askPlaylistProps(t("stats.playlist_dialog_title"),
    {name: t("stats.playlist_default_name", {year: statsYear})}, true, null);
  if (!props) return;
  try {
    const data = await playlistApi("/api/playlist", {
      op: "create", kind: "playlist", name: props.name, parent_id: null,
      icon: props.icon, color: props.color,
    });
    await playlistApi("/api/playlist-items", {
      op: "set", id: data.node.id, paths: tracks.map(tr => tr.path),
    });
    await reloadPlaylists();
    if (props.columnView) setColumnAssign(`pl:${data.node.id}`, props.columnView);
    closeStats();
    selectView(`pl:${data.node.id}`);
    note(t("stats.playlist_created", {name: props.name, count: tracks.length}));
  } catch (err) {
    note(t("stats.playlist_create_failed", {error: err.message}), true);
  }
}

document.getElementById("btnStats").onclick = openStats;
document.getElementById("btnStatsRebuild").onclick = rebuildStats;

// ── "Aufraeumen": Genre-/Album-/Interpret-Bubbleleiste, Gruppen-Umbenennen
// und Zusammenfuehrungs-Vorschlaege ──────────────────────────────────────
// Werte+Zaehlung kommen aus dem ohnehin komplett im Client gehaltenen DATA
// (groupCounts(), siehe oben) -- kein Server-Roundtrip dafuer noetig, nur das
// Umbenennen selbst geht ans Backend (Datei-Tag, DB-Zeile, best-effort
// Music.app/Rekordbox, siehe server.py _rename_tag_value()). Zaehlt bewusst
// alle Tracks mit, auch ausgeblendete (ignoriert/korrigiert) -- Ausblenden
// ist ein Workflow-Status, keine Aussage ueber die Tag-Qualitaet.
const VALUE_FIELDS = {
  genre:  {rowKey: "ge", endpoint: "/api/genre-rename"},
  artist: {rowKey: "a",  endpoint: "/api/artist-rename"},
  album:  {rowKey: "al", endpoint: "/api/album-rename"},
};
const FIELD_LABEL_KEY = {genre: "views.grp_genre", album: "views.grp_album", artist: "views.grp_artist"};
function fieldEditTitle(field) {
  return t("genres.edit_title", {field: t(FIELD_LABEL_KEY[field])});
}

// Patcht bereits geladene DATA-Zeilen lokal nach einem erfolgreichen
// Umbenennen -- ohne das blieben Tabelle/Bubbles bis zum naechsten Neuladen
// auf dem alten Wert stehen, obwohl Datei+DB laengst den neuen tragen.
function patchRenamedRows(field, rowKey, oldValue, newValue, groupArtist) {
  for (const r of DATA) {
    if (r[rowKey] !== oldValue) continue;
    if (field === "album" && (r.aa || r.a || "") !== groupArtist) continue;
    r[rowKey] = newValue;
    if (field === "artist") dropScopeCache(r);
  }
  invalidateFieldValueCache();
}

function renameResultToast(data) {
  const parts = [t("genres.toast_updated", {count: data.updated})];
  if (data.music && data.music.attempted) {
    parts.push(t("genres.toast_music", {matched: data.music.matched, attempted: data.music.attempted}));
  }
  if (data.rekordbox && (data.rekordbox.updated || []).length) {
    parts.push(t("genres.toast_rekordbox", {count: data.rekordbox.updated.length}));
  }
  let soft = false;
  if (data.rekordbox && data.rekordbox.running) {
    parts.push(t("genres.toast_rekordbox_running"));
    soft = true;
  }
  const failedCount = Object.keys(data.failed || {}).length;
  if (failedCount) {
    parts.push(t("genres.toast_failed", {count: failedCount}));
    soft = true;
  }
  note(parts.join(" · "), soft ? "soft" : undefined);
}

// Gemeinsamer Kern fuer den interaktiven Umbenennen-Dialog UND die
// Ein-Klick-Zusammenfuehrung aus einem Vorschlag -- beide rufen denselben
// Endpunkt mit demselben Payload-Aufbau auf.
async function performValueRename(field, oldValue, newValue, groupArtist) {
  const {rowKey, endpoint} = VALUE_FIELDS[field];
  const body = field === "album"
    ? {album: oldValue, group_artist: groupArtist || "", new: newValue}
    : {old: oldValue, new: newValue};
  try {
    const res = await fetch(endpoint, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || t("error.server_status", {status: res.status}));
    patchRenamedRows(field, rowKey, oldValue, newValue, groupArtist);
    renameResultToast(data);
    // Der alte Wert existiert danach nicht mehr -- eine noch offene Vorschau
    // auf genau dieses Paar waere sonst eine leere/falsche Einschraenkung.
    if (state.mergePreview && state.mergePreview.field === field) state.mergePreview = null;
    if (state.valueFilter && state.valueFilter.field === field) state.valueFilter = null;
    render();
  } catch (err) {
    note(t("genres.rename_failed", {error: err.message}), true);
  }
}

async function renameGroupValue(field, oldValue, groupArtist) {
  const {rowKey} = VALUE_FIELDS[field];
  const props = await askPlaylistProps(
    t("genres.rename_title", {list: t(FIELD_LABEL_KEY[field]), name: oldValue}),
    {name: oldValue}, false, undefined, true, rowKey, TREE_ICONS["grp:" + field]);
  if (!props || props.name === oldValue) return;
  await performValueRename(field, oldValue, props.name, groupArtist);
}

async function dismissMergeSuggestion(field, a, b, hostId, flag = true) {
  MERGE_DISMISSED[field][flag ? "add" : "delete"](mergePairKey(a, b));
  // Vorschau auf genau dieses (jetzt ausgeblendete) Paar waere sonst eine
  // Einschraenkung ohne sichtbaren Vorschlag dazu -- render() baut in diesem
  // Fall auch die Vorschlagsflaeche neu, ein zusaetzlicher Aufruf waere
  // doppelte Arbeit.
  if (flag && state.mergePreview && state.mergePreview.field === field &&
      mergePairKey(state.mergePreview.a.value, state.mergePreview.b.value) === mergePairKey(a, b)) {
    state.mergePreview = null;
    render();
  } else {
    renderMergeSuggestions(field, hostId);
  }
  try {
    await fetch("/api/merge-dismiss", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({field, a, b, flag}),
    });
  } catch {
    // naechster syncMarks()-Lauf gleicht bei einem Fehlschlag ohnehin ab.
  }
}

// ── Wert-Box im Kopf der geoeffneten Liste (Genre/Album/Interpret) ───────
// Sitzt IM Playlist-Kopf (#playlistHeader/.plhead, siehe index.html) statt
// in einem eigenen Block darunter -- plhead zeigt ohnehin schon Icon/Name/
// Anzahl fuer JEDE Liste, auch "Alle" (siehe currentListNode()), hier kommen
// nur zusaetzlich Bubble-Reihe + Zusammenfuehrungs-Vorschlaege dazu, wenn
// eine der drei "Aufraeumen"-Listen aktiv ist. EIN gemeinsamer Satz Elemente
// reicht, weil immer nur eines der drei Felder gleichzeitig aktiv sein kann.
// Zwei unabhaengige Zustaende je Feld: 'expanded' vergroessert nur die
// Bubble-Reihe (200px Standard-/550px Maximalhoehe ueber
// #plHeadBubblesExpand), 'mergeOpen' blendet die Zusammenfuehrungs-
// Vorschlaege separat ein/aus (#plHeadMergeToggle) -- kein gemeinsamer
// "ganze Box zuklappen"-Schalter mehr (Feedback: der fruehere Knopf links
// neben "Vorschläge" entfaellt, Vorschläge steuert sich nur noch selbst).
const valueBoxState = {
  genre:  {expanded: false, mergeOpen: false},
  album:  {expanded: false, mergeOpen: false},
  artist: {expanded: false, mergeOpen: false},
};
let valueBoxWired = false;

// Werte-Liste eines Feldes MIT Zaehlung -- Album braucht den Gruppen-
// Interpreten zur Unterscheidung gleichnamiger Alben (albumGroupCounts()),
// Genre/Interpret sind ein einzelnes Feld (groupCounts()).
function valueEntriesFor(field) {
  return field === "album" ? albumGroupCounts() : groupCounts(VALUE_FIELDS[field].rowKey);
}
// Album zeigt "Album [Interpret]" in der Bubble, der Interpret in
// abgeschwaechter Farbe (.albumbubbleartist) -- Genre/Interpret sind ohne
// weiteren Kontext bereits eindeutig und bleiben reiner Text. Liefert
// bereits escapetes HTML (anders als die anderen Feldwerte, die der
// Aufrufer selbst escapet), weil Album zwei Werte in einen Span-Tag
// verschachtelt.
function valueBubbleLabelHtml(field, entry) {
  if (field !== "album") return esc(entry.value);
  const artist = entry.groupArtist || t("group.no_artist");
  return `${esc(entry.value)} <span class="albumbubbleartist">[${esc(artist)}]</span>`;
}

function wireValueBoxOnce() {
  if (valueBoxWired) return;
  valueBoxWired = true;
  document.getElementById("plHeadBubblesExpandIcon").innerHTML = SVG(LUCIDE_ICONS["chevron-down"]);
  document.getElementById("plHeadMergeToggleIcon").innerHTML = SVG(LUCIDE_ICONS.merge);
  document.getElementById("plHeadBubblesExpand").onclick = () => {
    const field = GRP_VIEW_FIELD[state.view];
    if (!field) return;
    valueBoxState[field].expanded = !valueBoxState[field].expanded;
    renderPlaylistHeaderValueBox();
  };
  document.getElementById("plHeadMergeToggle").onclick = () => {
    const field = GRP_VIEW_FIELD[state.view];
    if (!field) return;
    valueBoxState[field].mergeOpen = !valueBoxState[field].mergeOpen;
    renderPlaylistHeaderValueBox();
  };
}

function renderPlaylistHeaderValueBox() {
  wireValueBoxOnce();
  const field = GRP_VIEW_FIELD[state.view];
  const expandBtn = document.getElementById("plHeadBubblesExpand");
  const mergeBtn = document.getElementById("plHeadMergeToggle");
  const bubblesRow = document.getElementById("plHeadBubblesRow");
  const mergeHost = document.getElementById("plHeadMergeSuggestions");
  if (!field) {
    expandBtn.hidden = true;
    mergeBtn.hidden = true;
    bubblesRow.hidden = true;
    mergeHost.hidden = true;
    return;
  }
  expandBtn.hidden = false;
  mergeBtn.hidden = false;
  bubblesRow.hidden = false;
  const st = valueBoxState[field];
  expandBtn.classList.toggle("expanded", st.expanded);
  document.getElementById("plHeadBubblesExpandLabel").textContent =
    t(st.expanded ? "genres.bubbles_collapse_label" : "genres.bubbles_expand_label");
  bubblesRow.classList.toggle("expanded", st.expanded);
  mergeBtn.classList.toggle("active", st.mergeOpen);
  mergeHost.hidden = !st.mergeOpen;

  const values = valueEntriesFor(field);
  const vf = state.valueFilter;
  bubblesRow.innerHTML = values.map((v, i) => {
    const active = vf && vf.field === field && vf.entry.value === v.value &&
      (field !== "album" || vf.entry.groupArtist === v.groupArtist);
    return `<span class="genrebubble${active ? " active" : ""}" data-i="${i}">
      <span>${valueBubbleLabelHtml(field, v)}</span>
      <span class="genrecount">(${v.count})</span>
      <button type="button" class="genrerename" data-bubbleedit="${i}"
              title="${esc(fieldEditTitle(field))}">${ICONS.edit}</button>
    </span>`;
  }).join("");
  // 550px bleibt Obergrenze (app.css .genrebox-bubbles.expanded), oeffnet
  // aber nur so weit wie der tatsaechliche Inhalt -- statt immer bis 550px.
  bubblesRow.style.maxHeight = st.expanded ? Math.min(bubblesRow.scrollHeight, 550) + "px" : "";
  bubblesRow.querySelectorAll("[data-i]").forEach(el => el.onclick = ev => {
    if (ev.target.closest("[data-bubbleedit]")) return;
    const entry = values[+el.dataset.i];
    const already = state.valueFilter && state.valueFilter.field === field &&
      state.valueFilter.entry.value === entry.value &&
      (field !== "album" || state.valueFilter.entry.groupArtist === entry.groupArtist);
    state.valueFilter = already ? null : {field, entry};
    state.shown = PAGE;
    render();
  });
  bubblesRow.querySelectorAll("[data-bubbleedit]").forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();
    const entry = values[+btn.dataset.bubbleedit];
    renameGroupValue(field, entry.value, entry.groupArtist || null);
  });

  if (st.mergeOpen) renderMergeSuggestions(field, "plHeadMergeSuggestions");
}

// ── Auffaelligkeiten-Bubbles im Playlist-Kopf ────────────────────────────
// Eigene, einfachere Zeile neben plHeadBubblesRow: Bubbles sind hier CODES
// (app/taganomaly.py) statt Feldwerte, es gibt kein Zusammenfuehren, kein
// Aufklappen -- bei maximal 18 moeglichen Codes wird die Zeile nicht so
// lang wie eine Genre-/Interpretenliste. Jede Zeile zaehlt je Code nur
// einmal (ein Track mit zwei gleichen Codes -- kommt praktisch nicht vor,
// aber zur Sicherheit -- soll nicht doppelt zaehlen).
function renderIssueBubbles() {
  const row = document.getElementById("plHeadIssueBubblesRow");
  if (!row) return;
  if (state.view !== "tag_issues") { row.hidden = true; return; }
  const counts = new Map();
  for (const r of live()) {
    if (!r.ti || !r.ti.length) continue;
    const seen = new Set();
    for (const i of r.ti) {
      if (seen.has(i.code)) continue;
      seen.add(i.code);
      counts.set(i.code, (counts.get(i.code) || 0) + 1);
    }
  }
  const codes = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a));
  if (!codes.length) { row.hidden = true; return; }
  row.hidden = false;
  row.innerHTML = codes.map(code => {
    const active = state.tagIssueFilter === code;
    const auto = AUTO_FIXABLE_TAG_ISSUE_CODES.has(code);
    return `<span class="genrebubble${active ? " active" : ""}" data-code="${esc(code)}">
      <span>${esc(t("tagissue." + code))}</span>
      <span class="genrecount">(${counts.get(code)})</span>
      <button type="button" class="genrerename" data-issueedit="${esc(code)}"
              title="${esc(t(auto ? "fixtag.auto_fix_button" : "fixtag.manual_edit_link"))}">${
              auto ? ICONS.sparkles : ICONS.pencil}</button>
    </span>`;
  }).join("");
  row.querySelectorAll("[data-code]").forEach(el => el.onclick = ev => {
    if (ev.target.closest("[data-issueedit]")) return;
    const code = el.dataset.code;
    state.tagIssueFilter = state.tagIssueFilter === code ? null : code;
    state.shown = PAGE;
    render();
  });
  row.querySelectorAll("[data-issueedit]").forEach(btn => btn.onclick = ev => {
    ev.stopPropagation();
    editAllWithIssueCode(btn.dataset.issueedit);
  });
}

// Bearbeitet ALLE Zeilen mit genau diesem Code auf einmal -- sicher
// automatisch behebbare Codes (AUTO_FIXABLE_TAG_ISSUE_CODES) werden direkt
// per Quick Fix bereinigt (gleiche 3-Worker-Warteschlange wie
// bulkApply("fixtags"), aber auf einer festen Zeilenmenge statt der
// aktuellen Auswahl); alles andere oeffnet den bestehenden
// Sammel-Tags-Dialog fuer genau diese Zeilen, weil es dort kein
// automatisierbares "richtig" gibt.
async function editAllWithIssueCode(code) {
  const rows = live().filter(r => (r.ti || []).some(i => i.code === code));
  if (!rows.length) return;
  if (!AUTO_FIXABLE_TAG_ISSUE_CODES.has(code)) {
    openTagsPopupBulk(rows, false);
    return;
  }
  if (!await askFixTagIssuesBulk(rows)) return;
  let done = 0, failed = 0;
  const queue = rows.slice();
  const worker = async () => {
    while (queue.length) {
      const r = queue.shift();
      try {
        await fixTagIssuesOne(r, false);
        done++;
      } catch (err) {
        failed++;
        note(t("toast.bulk_action_failed", {
          action: t("bulk.action_fix_tag_issues"), name: baseName(r.p), error: err.message,
        }), true);
      }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  updateCards(); render();
  note(t("toast.tag_issues_fixed_bulk", {count: done, skipped: failed}));
}

// ── Zusammenfuehrungs-Vorschlaege (Genre/Interpret/Album) ────────────────
// Vier Stufen absteigender Konfidenz: Leerzeichen, Gross/Klein, Tippfehler
// (Levenshtein), Varianten (Single/Remix/"!"). Nur als Hinweis -- ein Klick
// auf "Zusammenfuehren" ruft denselben Endpunkt wie das manuelle Umbenennen.
function normalizeMergeValue(s) { return s.trim().replace(/\s+/g, " "); }
const MERGE_VARIANT_SUFFIXES = [
  / \(single\)$/i, / \(remix\)$/i, / - single(?: version)?$/i, / - remix$/i,
];
function stripVariantSuffix(s) {
  let out = s;
  for (const re of MERGE_VARIANT_SUFFIXES) out = out.replace(re, "");
  return out.replace(/^!+|!+$/g, "").trim();
}

// 'sameGroup(a,b)' grenzt bei Album auf denselben Interpreten ein -- zwei
// gleichnamige Alben verschiedener Interpreten sollen nie als
// Zusammenfuehrung vorgeschlagen werden.
function findMergeSuggestions(values, dismissedSet, sameGroup = () => true) {
  const suggestions = [];
  const seenPairs = new Set();
  const addSuggestion = (a, b, kind) => {
    if (a.value === b.value || !sameGroup(a, b)) return;
    const key = mergePairKey(a.value, b.value);
    if (seenPairs.has(key) || dismissedSet.has(key)) return;
    seenPairs.add(key);
    suggestions.push({a, b, kind});
  };

  // Leerzeichen/Gross-Klein: Gruppierung ueber denselben normalisierten
  // Schluessel, kein paarweiser Vergleich noetig.
  const byNorm = new Map();
  for (const entry of values) {
    const norm = normalizeMergeValue(entry.value).toLowerCase();
    if (!byNorm.has(norm)) byNorm.set(norm, []);
    byNorm.get(norm).push(entry);
  }
  for (const group of byNorm.values()) {
    for (let i = 1; i < group.length; i++) {
      // Gleicher (fallsensitiv) normalisierter Wert -> der einzige
      // Unterschied lag im Leerraum. Unterschiedlich -> es war die
      // Gross-/Kleinschreibung (beide sind hier bereits als Gruppe
      // faellsensitiv-gleich bekannt, siehe byNorm-Schluessel oben).
      const kind = normalizeMergeValue(group[0].value) === normalizeMergeValue(group[i].value)
        ? "whitespace" : "case";
      addSuggestion(group[0], group[i], kind);
    }
  }

  // Tippfehler: nur innerhalb aehnlicher Laenge vergleichen (Bucket je 2
  // Zeichen) statt global paarweise -- bei tausenden Interpreten sonst O(n^2).
  // levenshteinWithin() bricht selbst ab, sobald die Laengendifferenz
  // groesser als maxDist ist, das Bucketing ist nur ein Vorfilter.
  const byLenBucket = new Map();
  for (const entry of values) {
    const norm = normalizeMergeValue(entry.value).toLowerCase();
    const bucket = Math.floor(norm.length / 2);
    if (!byLenBucket.has(bucket)) byLenBucket.set(bucket, []);
    byLenBucket.get(bucket).push({entry, norm});
  }
  for (const bucket of byLenBucket.keys()) {
    const candidates = [...byLenBucket.get(bucket), ...(byLenBucket.get(bucket - 1) || [])];
    for (let i = 0; i < candidates.length; i++) {
      for (let j = i + 1; j < candidates.length; j++) {
        const {entry: ea, norm: na} = candidates[i], {entry: eb, norm: nb} = candidates[j];
        if (na === nb) continue;   // schon als Leerzeichen/Gross-Klein erfasst
        const maxDist = na.length > 6 ? 2 : 1;
        if (levenshteinWithin(na, nb, maxDist) <= maxDist) addSuggestion(ea, eb, "typo");
      }
    }
  }

  // Varianten (Single/Remix/"!"): niedrigste Konfidenz, nur ein Hinweis.
  const exactByNorm = new Map(values.map(v => [normalizeMergeValue(v.value).toLowerCase(), v]));
  for (const entry of values) {
    const norm = normalizeMergeValue(entry.value);
    const stripped = stripVariantSuffix(norm).toLowerCase();
    if (stripped === norm.toLowerCase()) continue;   // nichts entfernt
    const exact = exactByNorm.get(stripped);
    if (exact) addSuggestion(exact, entry, "variant");
  }

  return suggestions;
}

// Album-"Werte" fuer Vorschlaege sind (Interpret, Album)-Paare, nicht nur der
// Albumname -- sonst wuerden gleichnamige Alben verschiedener Interpreten
// als Zusammenfuehrung vorgeschlagen. 'groupArtist' haengt am Eintrag, damit
// mergeGroupValues() den Rename-Aufruf korrekt scopen kann.
function albumGroupCounts() {
  const m = new Map();
  for (const r of DATA) {
    if (r.removed || !r.al) continue;
    const groupArtist = r.aa || r.a || "";
    const key = groupArtist + "\u0000" + r.al;
    if (!m.has(key)) m.set(key, {value: r.al, groupArtist, count: 0});
    m.get(key).count++;
  }
  return [...m.values()].sort((a, b) => cmpText(a.value, b.value));
}

const GRP_VIEW_FIELD = {"grp:genre": "genre", "grp:album": "album", "grp:artist": "artist"};

// Anzahl unterschiedlicher Werte eines Feldes -- fuer die Baum-Zahl neben
// Genre/Album/Kuenstler (siehe treeRoots()). Eine Trackzahl waere dort ohne
// Aussage: jede der drei Listen zeigt ohnehin die ganze Bibliothek, nur
// gruppiert statt gefiltert.
function groupEntryCount(field) {
  return field === "album" ? albumGroupCounts().length : groupCounts(VALUE_FIELDS[field].rowKey).length;
}

// Ob eine Zeile zu einem Werte-Eintrag (aus groupCounts()/albumGroupCounts())
// gehoert -- bei Album zaehlt der Gruppen-Interpret mit, sonst wuerden
// gleichnamige Alben verschiedener Interpreten mit in die Vorschau rutschen.
function rowMatchesValueEntry(r, field, entry) {
  if (field === "genre") return (r.ge || "") === entry.value;
  if (field === "artist") return (r.a || "") === entry.value;
  return (r.al || "") === entry.value && (r.aa || r.a || "") === entry.groupArtist;
}

// Klick auf den Hinweistext (nicht die Knoepfe) zeigt VOR dem Zusammenfuehren,
// welche Tracks betroffen sind: filtered() schraenkt die Tabelle auf genau
// die zwei Werte ein (siehe state.mergePreview dort), die ohnehin schon
// aktive Gruppierung der Liste (nach Genre/Album/Interpret) zeigt beide
// Gruppen dann nebeneinander. Erneuter Klick auf denselben Vorschlag
// schaltet die Vorschau wieder aus.
function toggleMergePreview(field, a, b) {
  const key = mergePairKey(a.value, b.value);
  const isActive = state.mergePreview && state.mergePreview.field === field &&
    mergePairKey(state.mergePreview.a.value, state.mergePreview.b.value) === key;
  state.mergePreview = isActive ? null : {field, a, b};
  state.shown = PAGE;
  render();
}

// Fragt, WELCHER der beiden Werte erhalten bleiben soll -- vorher entschied
// automatisch die hoehere Trackzahl, das kann bei den Zusammenfuehrungs-
// Vorschlaegen aber falsch liegen (z.B. ist der laenger gebraeuchliche, nicht
// zwingend der haeufigere Schreibweise). Liefert den gewaehlten Eintrag oder
// null bei Abbruch.
// Uebersetzt findMergeSuggestions()' 'kind' in einen fuer den Nutzer
// verstaendlichen Grund, im Zusammenfuehren-Dialog gezeigt (Feedback: "den
// Grund nennen z.B. Unnoetiges Leerzeichen, Gross-/Kleinschreibung").
const MERGE_REASON_KEY = {
  whitespace: "genres.merge_reason_whitespace",
  case: "genres.merge_reason_case",
  typo: "genres.merge_reason_typo",
  variant: "genres.merge_reason_variant",
};

// Macht fuehrende/folgende/doppelte Leerzeichen im Zusammenfuehren-Dialog
// sichtbar (Feedback: "nicht ersichtlich, was die Option ohne Leerzeichen
// ist") -- ein einzelnes Leerzeichen ist im Fliesstext unsichtbar, deshalb
// bekommt genau der Leerraum, den findMergeSuggestions() als Unterschied
// erkannt hat, einen hervorgehobenen Hintergrund statt nur den Text roh
// auszugeben.
function visualizeMergeValue(value) {
  const escaped = esc(value);
  return escaped.replace(/^ +| +$| {2,}/g,
    m => `<span class="ws-marker">${"&nbsp;".repeat(m.length)}</span>`);
}

function askMergeTarget(a, b, kind) {
  return new Promise(resolve => {
    const ov = document.getElementById("mergeChooseOverlay");
    document.getElementById("mergeChooseReason").textContent =
      t("genres.merge_choose_reason", {reason: t(MERGE_REASON_KEY[kind] || "genres.merge_reason_typo")});
    const opts = document.getElementById("mergeChooseOptions");
    opts.innerHTML = [a, b].map((v, i) => `
      <button type="button" class="act mergechoose-opt" data-i="${i}">
        <span>${visualizeMergeValue(v.value)}</span><span class="path">${esc(t("group.tracks_count", {count: v.count}))}</span>
      </button>`).join("");
    ov.style.display = "flex";
    const close = result => { ov.style.display = "none"; document.onkeydown = null; resolve(result); };
    opts.querySelectorAll("[data-i]").forEach(btn => btn.onclick = () => close([a, b][+btn.dataset.i]));
    document.getElementById("mergeChooseCancel").onclick = () => close(null);
    document.onkeydown = e => { if (e.key === "Escape") close(null); };
  });
}

function renderMergeSuggestions(field, hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const {rowKey} = VALUE_FIELDS[field];
  const values = field === "album" ? albumGroupCounts() : groupCounts(rowKey);
  const sameGroup = field === "album" ? (a, b) => a.groupArtist === b.groupArtist : undefined;
  const suggestions = findMergeSuggestions(values, MERGE_DISMISSED[field], sameGroup);
  const heading = `<div class="mergehint-heading">${esc(t("genres.merge_heading", {count: suggestions.length}))}</div>`;
  if (!suggestions.length) {
    host.innerHTML = heading + `<div class="mergehint-empty">${esc(t("genres.merge_none"))}</div>`;
    return;
  }
  const mp = state.mergePreview;
  host.innerHTML = heading + suggestions.map((s, i) => {
    const previewing = mp && mp.field === field &&
      mergePairKey(mp.a.value, mp.b.value) === mergePairKey(s.a.value, s.b.value);
    return `<div class="mergehint${previewing ? " previewing" : ""}" data-i="${i}">
      <span class="mergehint-text" data-mergeaction="preview" title="${esc(t("genres.merge_preview_title"))}">
        <span class="mergehint-eye">${previewing ? ICONS.eyeOff : ICONS.eye}</span>
        ${esc(t("genres.merge_hint", {a: s.a.value, b: s.b.value}))}
      </span>
      <button type="button" class="act small mergebtn" data-mergeaction="merge">
        <span class="btnicon">${ICONS.merge}</span> <span>${esc(t("genres.merge_button"))}</span>
      </button>
      <button type="button" class="mergedismiss" data-mergeaction="dismiss" title="${esc(t("genres.merge_dismiss_title"))}">×</button>
    </div>`;
  }).join("");
  host.querySelectorAll("[data-i]").forEach(row => {
    const s = suggestions[+row.dataset.i];
    row.querySelector('[data-mergeaction="preview"]').onclick = () => toggleMergePreview(field, s.a, s.b);
    row.querySelector('[data-mergeaction="merge"]').onclick = async () => {
      const keep = await askMergeTarget(s.a, s.b, s.kind);
      if (!keep) return;
      const drop = keep === s.a ? s.b : s.a;
      performValueRename(field, drop.value, keep.value, keep.groupArtist);
    };
    row.querySelector('[data-mergeaction="dismiss"]').onclick =
      () => dismissMergeSuggestion(field, s.a.value, s.b.value, hostId);
  });
}

function askScanOptions() {
  return new Promise(resolve => {
    const ov = document.getElementById("scanOptionsOverlay");
    const dbEl = document.getElementById("scanModeDb");
    const fullEl = document.getElementById("scanModeFull");
    const cutoffEl = document.getElementById("scanOptCutoff");
    const loudnessEl = document.getElementById("scanOptLoudness");
    const metaEl = document.getElementById("scanOptMeta");
    const tagIssuesEl = document.getElementById("scanOptTagIssues");
    const startBtn = document.getElementById("scanOptionsStart");

    // "Metadaten Scan" erzwingt nur den Cover-Abgleich mit Music.app
    // (force_cover_fill -> coverfill.fill_missing_covers(), das ohne
    // cfg["external_music"] sofort abbricht) -- ohne konfigurierte Music App
    // ist der Schalter ein reines No-op und bleibt deshalb ausgeblendet,
    // statt wirkungslos anwaehlbar zu sein.
    document.getElementById("scanOptMetaRow").style.display = MUSIC_NAME ? "" : "none";
    if (!MUSIC_NAME) metaEl.checked = false;

    const syncState = () => {
      const active = dbEl.checked || fullEl.checked;
      [cutoffEl, loudnessEl, metaEl, tagIssuesEl].forEach(el => { el.disabled = !active; });
      startBtn.disabled = !active;
    };
    dbEl.onchange = () => { if (dbEl.checked) fullEl.checked = false; syncState(); };
    fullEl.onchange = () => { if (fullEl.checked) dbEl.checked = false; syncState(); };
    syncState();

    ov.style.display = "flex";
    const done = answer => { ov.style.display = "none"; resolve(answer); };
    document.getElementById("scanOptionsCancel").onclick = () => done(null);
    startBtn.onclick = () => {
      if (startBtn.disabled) return;
      done({
        fullMode: fullEl.checked,
        cutoff: cutoffEl.checked,
        loudness: loudnessEl.checked,
        meta: metaEl.checked,
        tagIssues: tagIssuesEl.checked,
      });
    };
  });
}

document.getElementById("btnScan").onclick = async () => {
  const choice = await askScanOptions();
  if (!choice) return;
  startScan(choice.fullMode, {
    skip_spectral: !choice.cutoff,
    skip_loudness: !choice.loudness,
    force_cover_fill: choice.meta,
    recheck_tags: choice.tagIssues,
  });
};
function askRekordboxSyncOptions() {
  return new Promise(resolve => {
    const ov = document.getElementById("rbSyncOverlay");
    const presenceEl = document.getElementById("rbSyncPresence");
    const fixEl = document.getElementById("rbSyncFixPaths");
    ov.style.display = "flex";
    const done = answer => { ov.style.display = "none"; resolve(answer); };
    document.getElementById("rbSyncCancel").onclick = () => done(null);
    document.getElementById("rbSyncStart").onclick = () =>
      done({presence: presenceEl.checked, fixPaths: fixEl.checked});
  });
}

async function runRekordboxPresenceSync() {
  // indeterminate: der Server liefert erst am Ende ein Ergebnis, keine
  // Zwischenstaende -- siehe progressToast().
  const pt = progressToast(t("sync.rekordbox.running"), {indeterminate: true});
  try {
    const res = await fetch("/api/rekordbox-sync", {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    await syncMarks();
    render();
    pt.done(t("sync.rekordbox.done", {matched: data.matched, checked: data.checked}));
  } catch (err) {
    pt.fail(t("sync.rekordbox.failed", {error: err.message}));
  }
}

// Baut aus einem Treffer die Anzeige-Beschriftung -- Interpret/Titel, wenn
// vorhanden, sonst nur der alte Pfad (Rekordbox-Eintraege ohne Tags).
function rekordboxFixLabel(e) {
  return (e.artist || e.title) ? `${e.artist} — ${e.title}` : e.old_path;
}

// Zerlegt zwei Pfade in "/"-Komponenten und liefert den gemeinsamen Kopf-
// und Schwanz-Bereich (identische Segmente vorn/hinten) -- der Rest
// dazwischen ist genau der Teil, der sich unterscheidet. Segment-weise statt
// zeichenweise: alle bisher beobachteten Faelle (Gross-/Kleinschreibung
// eines Ordners, umbenannter Ordner, geänderter Dateiname/Endung) betreffen
// ganze Pfadsegmente, nie einzelne Zeichen mitten in einem unveraenderten
// Namen -- eine Segment-Markierung bleibt dadurch lesbar, ein Zeichen-Diff
// waere bei langen, ohnehin schon fett/kursiv wirkenden Pfaden nur Unruhe.
function diffPathParts(oldPath, newPath) {
  const a = oldPath.split("/"), b = newPath.split("/");
  const n = Math.min(a.length, b.length);
  let head = 0;
  while (head < n && a[head] === b[head]) head++;
  let tailA = a.length - 1, tailB = b.length - 1;
  while (tailA > head && tailB > head && a[tailA] === b[tailB]) { tailA--; tailB--; }
  return {a, b, headEnd: head, tailStartA: tailA + 1, tailStartB: tailB + 1};
}

// Zeichnet einen Pfad mit den abweichenden Segmenten (siehe diffPathParts())
// in <mark>. which entscheidet, ob der alte oder neue Pfad gezeichnet wird.
function renderPathDiff(parts, which) {
  const arr = which === "old" ? parts.a : parts.b;
  const tailStart = which === "old" ? parts.tailStartA : parts.tailStartB;
  return arr.map((seg, i) => {
    const text = esc(seg) + (i < arr.length - 1 ? "/" : "");
    return (i >= parts.headEnd && i < tailStart) ? `<mark class="diffseg">${text}</mark>` : text;
  }).join("");
}

// Kurzbeschreibung, was sich zwischen altem und neuem Pfad konkret geaendert
// hat -- steht ueber der Pfad-Gegenueberstellung, damit auf einen Blick klar
// ist, worum es geht (Gross-/Kleinschreibung, Umbenennung, verschobener
// Ordner, geaenderte Dateiendung), ohne beide Pfade selbst vergleichen zu
// muessen. Deckt sich mit den bislang beobachteten Faellen aus
// rekordbox._case_correct_path()/scanner.find_moved_among() -- ein noch
// unbekannter Mischfall faellt auf die allgemeinste Meldung zurueck.
function describePathChange(oldPath, newPath) {
  if (oldPath.toLowerCase() === newPath.toLowerCase()) return t("rbfix.change_case_full");
  const parts = diffPathParts(oldPath, newPath);
  const changedOld = parts.a.slice(parts.headEnd, parts.tailStartA);
  const changedNew = parts.b.slice(parts.headEnd, parts.tailStartB);
  const isFile = parts.tailStartA === parts.a.length && parts.tailStartB === parts.b.length;
  if (changedOld.length === 1 && changedNew.length === 1) {
    const [o, n] = [changedOld[0], changedNew[0]];
    if (o.toLowerCase() === n.toLowerCase()) {
      return t(isFile ? "rbfix.change_case_file" : "rbfix.change_case_folder", {old: o, new: n});
    }
    if (isFile) {
      const oldExt = o.includes(".") ? o.split(".").pop() : "";
      const newExt = n.includes(".") ? n.split(".").pop() : "";
      if (oldExt !== newExt) return t("rbfix.change_extension", {old: o, new: n});
      return t("rbfix.change_filename", {old: o, new: n});
    }
    return t("rbfix.change_folder", {old: o, new: n});
  }
  if (!changedOld.length || !changedNew.length) return t("rbfix.change_moved");
  return t("rbfix.change_folder", {old: changedOld.join("/"), new: changedNew.join("/")});
}

function askRekordboxFixPreview(data) {
  return new Promise(resolve => {
    const ov = document.getElementById("rbFixOverlay");
    const listEl = document.getElementById("rbFixList");
    document.getElementById("rbFixSummary").textContent = t("rbfix.summary", {
      moved: data.moved.length, ambiguous: data.ambiguous.length});

    listEl.innerHTML = [
      ...data.moved.map(e => {
        const parts = diffPathParts(e.old_path, e.new_path);
        return `
        <div class="rbfixrow" data-kind="moved" data-old="${esc(e.old_path)}" data-new="${esc(e.new_path)}">
          <label><input type="checkbox" checked> ${esc(rekordboxFixLabel(e))}</label>
          <div class="pathchange">${esc(describePathChange(e.old_path, e.new_path))}</div>
          <div class="pathold">${renderPathDiff(parts, "old")}</div>
          <div class="pathnew">${renderPathDiff(parts, "new")}</div>
        </div>`;
      }),
      ...data.ambiguous.map(e => `
        <div class="rbfixrow" data-kind="ambiguous" data-old="${esc(e.old_path)}">
          <label><input type="checkbox"> ${esc(rekordboxFixLabel(e))}</label>
          <div class="path">${esc(e.old_path)}</div>
          <select class="rbfixcand">
            <option value="">${esc(t("rbfix.pick_candidate"))}</option>
            ${e.candidates.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}
          </select>
        </div>`),
    ].join("");

    // Kandidat auswaehlen hakt automatisch die Checkbox der Zeile an.
    listEl.querySelectorAll(".rbfixcand").forEach(sel => {
      sel.onchange = () => {
        sel.closest(".rbfixrow").querySelector("input[type=checkbox]").checked = !!sel.value;
      };
    });

    ov.style.display = "flex";
    const done = fixes => { ov.style.display = "none"; resolve(fixes); };
    document.getElementById("rbFixCancel").onclick = () => done(null);
    document.getElementById("rbFixApply").onclick = () => {
      const fixes = [];
      listEl.querySelectorAll('.rbfixrow[data-kind="moved"], .rbfixrow[data-kind="ambiguous"]').forEach(row => {
        const cb = row.querySelector("input[type=checkbox]");
        if (!cb.checked) return;
        const oldPath = row.dataset.old;
        const newPath = row.dataset.kind === "moved" ? row.dataset.new : row.querySelector(".rbfixcand").value;
        if (newPath) fixes.push({old_path: oldPath, new_path: newPath});
      });
      done(fixes);
    };
  });
}

async function runRekordboxFixPathsFlow() {
  const pt = progressToast(t("rbfix.scanning"), {indeterminate: true});
  let data;
  try {
    const res = await fetch("/api/rekordbox-scan", {method: "POST"});
    data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    pt.done(t("rbfix.scan_done", {moved: data.moved.length, ambiguous: data.ambiguous.length}));
  } catch (err) {
    pt.fail(t("rbfix.scan_failed", {error: err.message}));
    return;
  }
  if (!data.moved.length && !data.ambiguous.length) return;
  const fixes = await askRekordboxFixPreview(data);
  if (!fixes || !fixes.length) return;
  const pt2 = progressToast(t("rbfix.applying"), {indeterminate: true});
  try {
    const res = await fetch("/api/rekordbox-fix-paths", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({fixes})});
    const result = await res.json();
    if (!result.ok) {
      if (result.sticky) { pt2.fail(result.error); return; }
      throw new Error(result.error || t("error.unknown"));
    }
    pt2.done(t("rbfix.apply_done", {updated: result.updated.length}));
  } catch (err) {
    pt2.fail(t("rbfix.apply_failed", {error: err.message}));
  }
}

async function openRekordboxSyncFlow() {
  const choice = await askRekordboxSyncOptions();
  if (!choice || (!choice.presence && !choice.fixPaths)) return;
  const btn = document.getElementById("btnRekordboxSync");
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = t("sync.button_busy");
  try {
    if (choice.presence) await runRekordboxPresenceSync();
    if (choice.fixPaths) await runRekordboxFixPathsFlow();
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

document.getElementById("btnRekordboxSync").onclick = () => openRekordboxSyncFlow();
document.getElementById("btnMusicAddedSync").onclick = async () => {
  const btn = document.getElementById("btnMusicAddedSync");
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = t("sync.button_busy");
  const pt = progressToast(t("sync.music.running"), {indeterminate: true});
  try {
    const res = await fetch("/api/music-added-sync", {method: "POST"});
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    await syncMarks();
    render();
    pt.done(t("sync.music.done", {matched: data.matched, checked: data.checked}));
  } catch (err) {
    pt.fail(t("sync.music.failed", {error: err.message}));
  }
  btn.disabled = false;
  btn.innerHTML = orig;
};

// ── Beenden ─────────────────────────────────────────────────────────────
// Startet die App per Doppelklick statt aus dem Terminal, gibt es kein
// Strg+C. Ohne diesen Knopf laeuft der Server nach dem Schliessen des Tabs
// unsichtbar weiter -- und der naechste Start faende den Port belegt.
// Beschriftung: siehe applyToolbarLabels() oben.

async function quitServer(force) {
  const res = await fetch("/api/quit", {
    method: "POST",
    body: JSON.stringify(force ? {force: true} : {}),
  });
  return res.json();
}

// Die Seite bleibt nach dem Beenden stehen, aber jeder Knopf darauf liefe
// jetzt ins Leere. Der Schleier sagt das und deckt sie zu. window.close()
// waere schoener, greift aber nur bei Tabs, die ein Skript geoeffnet hat --
// diesen hier hat der Server aufgemacht.
function showQuitVeil() {
  clearTimeout(scanTimer);
  for (const a of document.querySelectorAll("audio")) a.pause();
  queueAudio.pause();
  const veil = document.createElement("div");
  veil.id = "quitveil";
  veil.innerHTML = `<div><strong>${esc(t("quit.done_title"))}</strong><br>`
                 + `${esc(t("quit.done_note"))}</div>`;
  document.body.appendChild(veil);
}

document.getElementById("btnQuit").onclick = async () => {
  const btn = document.getElementById("btnQuit");
  btn.disabled = true;
  try {
    let data = await quitServer(false);
    if (!data.ok && data.scan_running) {
      btn.disabled = false;
      const go = confirm(t("quit.confirm_scan_running"));
      if (!go) return;
      btn.disabled = true;
      data = await quitServer(true);
    }
    if (!data.ok) throw new Error(data.error || t("error.unknown"));
    showQuitVeil();
  } catch (err) {
    btn.disabled = false;
    note(t("quit.failed", {error: err.message}), true);
  }
};

computeDuplicateGroups();
// Erstberechnung der Smart Playlists: die zweite der beiden vorgesehenen
// Gelegenheiten neben dem Anklicken eines Knotens (siehe selectView()).
recomputeAllSmart();
loadFilters();
// Nur das Attribut setzen + Umschalter synchronisieren, NICHT applyLayout()
// aufrufen -- das wuerde Verdikt-/Harte-Kante-Filter unnoetig neu erzwingen/
// snapshotten, obwohl loadFilters() sie fuer die geladene Ansicht schon
// korrekt wiederhergestellt hat.
document.body.setAttribute("data-layout", state.layout);
setSidebarWidth(state.sidebarW);
initSidebarResize();
document.getElementById("plUndo").onclick = () => runUndo("undo");
document.getElementById("plRedo").onclick = () => runUndo("redo");
document.getElementById("btnNewPlaylist").onclick = () => createPlaylistNode("playlist");
document.getElementById("btnNewSmart").onclick = () => createPlaylistNode("smart");
document.getElementById("btnNewFolder").onclick = () => createPlaylistNode("folder");
syncLayoutSwitch();
syncControls();
renderHead();
applyColumnLayout();
initColumnResize();
renderColsMenu();
initStorage().then(() => { initDropzone(); pollScan(); updateCards(); render(); renderDrops(); warmScopeCache(); });
