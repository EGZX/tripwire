"use strict";
/* Tripwire dashboard: views over data/history/screen_*.csv served by dashboard/server.py.
   All untrusted strings (names, flags, lists) go into the DOM through textContent, never innerHTML. */

// ---------------------------------------------------------------- state
const S = {
  runs: [], config: null, data: null, rows: [], hist: {},
  run: null, prev: "",
  view: "overview", sel: null,
  filters: { q: "", status: "all", cls: "all", owned: false, list: "all" },
  sort: { key: "entry_score", dir: -1 }, colset: "decision",
  holdSort: { key: "position_eur", dir: -1 }, entrySort: { key: "position_eur", dir: -1 }, changeSort: { key: "score_change_abs", dir: -1 },
};
const SCORED = ["eligible", "data flag", "price above ceiling"];
const STATUS_ORDER = ["eligible", "data flag", "price above ceiling", "not rankable", "not screened"];
const STATUS_LABEL = { "eligible": "Candidate", "data flag": "Data flag", "price above ceiling": "Above ceiling",
  "not rankable": "Not rankable", "not screened": "Not screened" };
const CRITICAL = ["stale financials", "no market cap", "check one-offs", "thin capital"];  // screener/scoring.py CRITICAL_FLAGS
const CLIP_LO = -100, CLIP_HI = 100;  // the screener floors the entry score at -100 (price >= 2x the ceiling)
const TICKS = [-100, -75, -50, -25, 0, 25, 50, 75, 100];
const MINUS = "−";

// ---------------------------------------------------------------- DOM helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function s(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  setAttrs(el, attrs);
  append(el, kids);
  return el;
}
function setAttrs(el, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
}
function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
const $ = (id) => document.getElementById(id);
function fill(el, ...kids) { el.replaceChildren(); append(el, kids); return el; }  // like replaceChildren, but skips null/false
const icon = (d) => { const e = s("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" }); e.innerHTML = d; return e; }; // static, trusted markup only

// ---------------------------------------------------------------- numbers
function num(v) {
  if (v === "+inf") return Infinity;
  if (v === "-inf") return -Infinity;
  if (v === null || v === undefined || v === "" || typeof v === "boolean") return NaN;
  return Number(v);
}
const ok = (v) => Number.isFinite(num(v));
const sign = (x, str) => (x > 0 ? "+" : x < 0 ? MINUS : "") + str;
function fmtNum(v, d = 0) {
  const x = num(v);
  if (x === Infinity) return "∞";
  if (!Number.isFinite(x)) return "–";
  const out = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (x < 0 && out.replace(/[0.,]/g, "") !== "" ? MINUS : "") + out;
}
const fmtPct = (v, d = 1) => { const x = num(v); return Number.isFinite(x) ? fmtNum(x * 100, d) + "%" : fmtNum(x); };
const fmtSignedPct = (v, d = 1) => { const x = num(v); return Number.isFinite(x) ? sign(x, Math.abs(x * 100).toFixed(d) + "%") : "–"; };
const fmtPP = (v, d = 1) => { const x = num(v); return Number.isFinite(x) ? sign(x, Math.abs(x * 100).toFixed(d) + " pp") : "–"; };
const fmtScore = (v) => { const x = num(v); if (!Number.isFinite(x)) return "–"; if (x <= CLIP_LO) return "≤ " + MINUS + "100"; return sign(Math.round(x), Math.abs(Math.round(x)).toLocaleString("en-US")); };
const fmtTick = (t) => t === 0 ? "0" : sign(t, String(Math.abs(t)));
const fmtPx = (v) => { const x = num(v); return x === Infinity ? "∞ (unbounded)" : fmtNum(x, Math.abs(x) >= 10000 ? 0 : 2); };
const fmtEur = (v) => ok(v) ? "€" + fmtNum(v, 0) : "–";
const fmtEurK = (v) => { const x = num(v); if (!Number.isFinite(x)) return "–"; return Math.abs(x) >= 1e6 ? "€" + (x / 1e6).toFixed(2) + "M" : Math.abs(x) >= 1e4 ? "€" + (x / 1e3).toFixed(1) + "k" : fmtEur(x); };
const fmtRatio = (v, d = 2) => fmtNum(v, d);
function baseCcy() { return (S.config && S.config.valuation && S.config.valuation.base_currency) || "EUR"; }
function fmtDate(d) { return d || "–"; }
const runDay = (run) => String(run || "").slice(0, 10);  // run ids are YYYY-MM-DD or YYYY-MM-DD_N for same-day reruns
function runLabel(run) { const n = String(run || "").split("_")[1]; return n ? runDay(run) + " · run " + (Number(n) + 1) : runDay(run); }
function daysBetween(a, b) { return Math.round((Date.parse(b) - Date.parse(a)) / 86400000); }
const statusKey = (st) => "s-" + String(st || "unknown").replace(/\s+/g, "-");

// distance to the line, in the two directions people ask about
function headroom(r) {
  const p = num(r.price), c = num(r.ceiling_px);
  if (!(p > 0) || !(c > 0)) return NaN;
  return c / p - 1;  // > 0: price may rise this much and still be at/below the ceiling; < 0: price must fall
}

// ---------------------------------------------------------------- tooltip
const tip = { el: null };
function showTip(evt, title, sub, rows) {
  const el = tip.el || (tip.el = $("tooltip"));
  fill(el,
    h("div", { class: "tt-title", text: title }),
    sub ? h("div", { class: "tt-sub", text: sub }) : null,
    (rows || []).map((r) => h("div", { class: "tt-row" },
      h("span", { class: "k", style: r.color ? { borderTopColor: r.color } : { borderTopColor: "transparent" } }),
      h("span", { class: "lbl", text: r.label }),
      h("span", { class: "val", text: r.value }))));
  el.classList.add("show");
  moveTip(evt);
}
function moveTip(evt) {
  const el = tip.el; if (!el) return;
  let x, y;
  if (evt && evt.clientX !== undefined && (evt.clientX || evt.clientY)) { x = evt.clientX; y = evt.clientY; }
  else if (evt && evt.target && evt.target.getBoundingClientRect) { const b = evt.target.getBoundingClientRect(); x = b.right; y = b.top; }
  else return;
  const w = el.offsetWidth, hh = el.offsetHeight;
  let left = x + 14, top = y + 14;
  if (left + w > innerWidth - 8) left = x - w - 14;
  if (top + hh > innerHeight - 8) top = y - hh - 14;
  el.style.left = Math.max(8, left) + "px"; el.style.top = Math.max(8, top) + "px";
}
function hideTip() { if (tip.el) tip.el.classList.remove("show"); }
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// ---------------------------------------------------------------- data
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}
async function init() {
  wireChrome();
  readHash();
  try {
    const [runs, cfg] = await Promise.all([getJSON("api/runs"), getJSON("api/config")]);
    S.runs = runs.runs || []; S.today = runs.today; S.root = runs.root; S.config = cfg;
  } catch (e) { return fatal("Could not reach the dashboard API: " + e.message); }
  if (!S.runs.length) return renderNoRuns();
  const dates = S.runs.map((r) => r.date);
  if (!S.run || !dates.includes(S.run)) S.run = dates[dates.length - 1];
  fillRunSelects();
  await loadRun();
}
async function loadRun() {
  $("main").classList.add("loading");
  try {
    const q = "api/run?date=" + encodeURIComponent(S.run) + (S.prev ? "&prev=" + encodeURIComponent(S.prev) : "");
    S.data = await getJSON(q);
    S.rows = S.data.rows.map((r) => ({ ...r, owned: !!r.owned, candidate: !!r.candidate }));
    S.bySym = new Map(S.rows.filter((r) => r.symbol).map((r) => [r.symbol, r]));
    S.byIsin = new Map(S.rows.filter((r) => r.symbol && r.isin).map((r) => [String(r.isin).toUpperCase(), r]));
    S.hist = {};
  } catch (e) { return fatal("Could not load run " + S.run + ": " + e.message); }
  finally { $("main").classList.remove("loading"); }
  if (S.sel && !S.bySym.has(S.sel)) S.sel = null;
  renderChrome();
  render();
  renderInspector();
}
async function reloadAll() {
  try {
    const [runs, cfg] = await Promise.all([getJSON("api/runs"), getJSON("api/config")]);
    S.runs = runs.runs || []; S.today = runs.today; S.config = cfg;
  } catch (e) { return fatal(e.message); }
  if (!S.runs.length) return renderNoRuns();
  const dates = S.runs.map((r) => r.date);
  if (!dates.includes(S.run)) S.run = dates[dates.length - 1];
  fillRunSelects();
  await loadRun();
}

// ---------------------------------------------------------------- chrome (top bar, rail, status bar)
const VIEWS = [
  { id: "overview", label: "Overview", icon: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/>' },
  { id: "screen", label: "Screen", icon: '<path d="M3 5h14M3 10h14M3 15h14"/><path d="M7 3v14" opacity=".5"/>' },
  { id: "holdings", label: "Holdings", icon: '<path d="M3 16V8l7-4 7 4v8z"/><path d="M8 16v-5h4v5"/>' },
  { id: "lists", label: "Lists", icon: '<path d="M7 5h10M7 10h10M7 15h6"/><circle cx="3.5" cy="5" r=".8"/><circle cx="3.5" cy="10" r=".8"/><circle cx="3.5" cy="15" r=".8"/><path d="M16 13v6M13 16h6"/>' },
  { id: "changes", label: "Changes", icon: '<path d="M4 7h11l-3-3M16 13H5l3 3"/>' },
  { id: "data", label: "Data quality", icon: '<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>' },
  { id: "method", label: "Method", icon: '<path d="M5 3h8l3 3v11H5z"/><path d="M8 9h6M8 12h6M8 15h4"/>' },
];
function wireChrome() {
  const rail = $("rail");
  for (const v of VIEWS) {
    rail.appendChild(h("button", { "data-view": v.id, title: v.label, onclick: () => go(v.id) },
      icon(v.icon), h("span", { text: v.label })));
  }
  $("run-select").addEventListener("change", (e) => { S.run = e.target.value; S.prev = ""; fillRunSelects(); loadRun(); });
  $("prev-select").addEventListener("change", (e) => { S.prev = e.target.value; loadRun(); });
  $("reload-btn").addEventListener("click", reloadAll);
  $("theme-btn").addEventListener("click", () => {
    const t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("screener-theme", t); } catch (e) { /* ignore */ }
    render(); renderInspector();
  });
  addEventListener("hashchange", () => { const v = S.view, sel = S.sel; readHash(); if (v !== S.view) render(); if (sel !== S.sel) { renderInspector(); markSelection(); } });
  addEventListener("keydown", (e) => { if (e.key === "Escape" && S.sel) select(null); });
  addEventListener("scroll", hideTip, true);
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (S.data && !document.activeElement?.matches("input, select")) render(); }, 200); });
}
function readHash() {
  const m = location.hash.replace(/^#/, "").split("?");
  if (VIEWS.some((v) => v.id === m[0])) S.view = m[0];
  const p = new URLSearchParams(m[1] || "");
  S.sel = p.get("s") || null;
  if (p.get("run")) S.run = p.get("run");
}
function writeHash() {
  const p = new URLSearchParams();
  if (S.sel) p.set("s", S.sel);
  const next = "#" + S.view + (p.toString() ? "?" + p : "");
  if (location.hash !== next) history.replaceState(null, "", next);
}
function go(view) { S.view = view; writeHash(); render(); $("main").scrollTop = 0; }
function select(sym) { S.sel = sym; writeHash(); renderInspector(); markSelection(); }
function fillRunSelects() {
  const rs = $("run-select"), ps = $("prev-select");
  const dates = S.runs.map((r) => r.date).reverse();
  rs.replaceChildren(...dates.map((d) => h("option", { value: d, text: runLabel(d), selected: d === S.run })));
  const earlier = dates.slice(dates.indexOf(S.run) + 1);  // dates is newest first, in the server's run order
  ps.replaceChildren(h("option", { value: "", text: earlier.length ? "previous run" : "no earlier run" }),
    ...earlier.slice(1).map((d) => h("option", { value: d, text: runLabel(d), selected: d === S.prev })));
  ps.disabled = earlier.length < 2;
}
function renderChrome() {
  for (const b of $("rail").querySelectorAll("button")) {
    b.toggleAttribute("aria-current", b.dataset.view === S.view);
    if (b.dataset.view === S.view) b.setAttribute("aria-current", "page");
    b.querySelector(".badge-count")?.remove();
  }
  const cands = S.rows.filter((r) => r.candidate).length;
  const btn = $("rail").querySelector('[data-view="overview"]');
  if (cands && btn) btn.appendChild(h("span", { class: "badge-count", text: String(cands), title: cands + " candidates" }));
  const age = S.today ? daysBetween(runDay(S.run), S.today) : 0;
  const fr = $("freshness");
  fr.className = "chip " + (age > 8 ? "warn" : age <= 1 ? "ok" : "");
  fr.textContent = age <= 0 ? "today" : age === 1 ? "1 day old" : age + " days old";
  fr.title = "Run date " + S.run + (age > 8 ? " — older than a weekly cycle; run the screener" : "");
  const d = S.data, c = S.config || {};
  const cfgAfter = c.modified && d.written_at && c.modified > d.written_at;
  fill($("statusbar"),
    h("span", { text: "Run " + runLabel(S.run) + (d.written_at ? " · written " + d.written_at.replace("T", " ") : "") }),
    h("span", { text: d.previous ? "compared with " + runLabel(d.previous) : "no earlier run to compare" }),
    h("span", { text: S.runs.length + " run" + (S.runs.length === 1 ? "" : "s") + " in history" }),
    cfgAfter ? h("span", { class: "chip warn", text: "config.toml changed after this run" }) : null,
    h("span", { class: "grow" }),
    h("span", { title: S.root || "", text: c.editable ? "Config editable" : "Read-only" }));
}
function fatal(msg) {
  $("main").replaceChildren(h("div", { class: "banner err" }, icon('<circle cx="10" cy="10" r="7"/><path d="M10 6v5M10 13.5v.5"/>'), h("div", { text: msg })));
}
function renderNoRuns() {
  $("run-select").replaceChildren(h("option", { text: "no runs" }));
  $("main").replaceChildren(pageHeader("No screener runs yet", ""), h("div", { class: "card" }, h("div", { class: "empty" },
    h("p", { text: "The dashboard reads data/history/screen_YYYY-MM-DD.csv under " + (S.root || "the repository") + "." }),
    h("p", {}, "Run the screener first: ", h("code", { text: ".venv\\Scripts\\python -m screener" }), ", then press Reload."))));
}

// ---------------------------------------------------------------- filtering
function listsOf(r) { return String(r.lists || "").split(/,\s*/).map((x) => x.trim()).filter(Boolean); }
function passes(r, f = S.filters) {
  if (f.owned && !r.owned) return false;
  if (f.cls !== "all") {
    const c = r.class || "";
    if (f.cls === "other" ? ["operating", "cyclical", "financial"].includes(c) : c !== f.cls) return false;
  }
  if (f.status === "scored" && !SCORED.includes(r.status)) return false;
  if (f.status === "near" && !(r.status === "price above ceiling" && num(r.entry_score) >= -25)) return false;
  if (!["all", "scored", "near"].includes(f.status) && r.status !== f.status) return false;
  if (f.list !== "all" && !listsOf(r).some((l) => l === f.list)) return false;
  if (f.q) {
    const q = f.q.toLowerCase();
    if (![r.symbol, r.name, r.industry, r.sector, r.isin].some((x) => String(x || "").toLowerCase().includes(q))) return false;
  }
  return true;
}
const filtered = () => S.rows.filter((r) => passes(r));
function filterBar(count, total) {
  const f = S.filters;
  const set = (k, v) => { f[k] = v; render(); };
  const lists = [...new Set(S.rows.flatMap(listsOf))].sort();
  const seg = (k, opts) => h("div", { class: "segmented", role: "group" },
    opts.map(([v, label]) => h("button", { "aria-pressed": String(f[k] === v), onclick: () => set(k, v), text: label })));
  const search = h("input", { type: "search", placeholder: "Symbol, name, industry, ISIN", value: f.q, "aria-label": "Search" });
  let t;
  search.addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { f.q = e.target.value; render(); restoreFocus(); }, 160); });
  const restoreFocus = () => { const el = $("main").querySelector('input[type="search"]'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } };
  const statusSel = h("select", { "aria-label": "Status", onchange: (e) => set("status", e.target.value) },
    [["all", "All statuses"], ["scored", "Scored (candidate, flag, above)"], ["near", "Within 25 points of the ceiling"],
      ...STATUS_ORDER.map((st) => [st, STATUS_LABEL[st]])].map(([v, l]) => h("option", { value: v, text: l, selected: f.status === v })));
  const listSel = h("select", { "aria-label": "Source list", onchange: (e) => set("list", e.target.value) },
    h("option", { value: "all", text: "All lists" }), lists.map((l) => h("option", { value: l, text: l, selected: f.list === l })));
  const dirty = f.q || f.status !== "all" || f.cls !== "all" || f.owned || f.list !== "all";
  return h("div", { class: "toolbar" },
    h("div", { class: "group" }, search),
    h("div", { class: "group" }, statusSel),
    h("div", { class: "group" }, seg("cls", [["all", "All"], ["operating", "Operating"], ["cyclical", "Cyclical"], ["financial", "Financial"], ["other", "Other"]])),
    h("div", { class: "group" }, listSel,
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.owned, onchange: (e) => set("owned", e.target.checked) }), "Holdings only")),
    dirty ? h("button", { class: "btn ghost", text: "Clear", onclick: () => { S.filters = { q: "", status: "all", cls: "all", owned: false, list: "all" }; render(); } }) : null,
    h("span", { class: "count", text: count === total ? total + " names" : count + " of " + total + " names" }));
}

// ---------------------------------------------------------------- render dispatch
function render() {
  if (!S.data) return;
  renderChrome();
  hideTip();
  const main = $("main");
  const views = { overview: viewOverview, screen: viewScreen, holdings: viewHoldings, lists: viewLists, changes: viewChanges, data: viewData, method: viewMethod };
  fill(main, views[S.view]());
  markSelection();
}
function pageHeader(title, subtitle, ...right) {
  return h("div", { class: "page-header" }, h("h2", { text: title }), subtitle ? h("span", { class: "subtitle", text: subtitle }) : null,
    right.length ? h("span", { style: { marginLeft: "auto", display: "flex", gap: "6px" } }, right) : null);
}
function card(title, hint, body, opts = {}) {
  return h("section", { class: "card" + (opts.cls ? " " + opts.cls : "") },
    h("div", { class: "card-head" }, h("h3", { text: title }),
      opts.foot ? h("span", { class: "info", tabindex: "0", role: "note", title: opts.foot, "aria-label": opts.foot, text: "i" }) : null,
      hint ? h("span", { class: "hint", text: hint }) : null,
      opts.right ? h("div", { class: "right" }, opts.right) : null),
    h("div", { class: "card-body" + (opts.flush ? " flush" : "") }, body));
}
function kpi(label, value, delta, opts = {}) {
  return h("div", { class: "card kpi" + (opts.onclick ? " clickable" : ""), onclick: opts.onclick, title: opts.title,
    tabindex: opts.onclick ? "0" : null, onkeydown: opts.onclick ? (e) => { if (e.key === "Enter") opts.onclick(); } : null },
    h("div", { class: "label" }, opts.status ? h("span", { class: "status " + statusKey(opts.status) }) : null, label),
    h("div", { class: "value" + (opts.hero ? " hero" : ""), text: value }),
    delta ? h("div", { class: "delta " + (opts.deltaCls || ""), text: delta }) : null);
}
function statusEl(st) { return h("span", { class: "status " + statusKey(st), text: STATUS_LABEL[st] || st || "unknown" }); }
function markSelection() {
  for (const tr of document.querySelectorAll("tr[data-sym]")) tr.classList.toggle("selected", tr.dataset.sym === S.sel);
}
function filterTo(status) { S.filters = { q: "", status, cls: "all", owned: false, list: "all" }; go("screen"); }

// ---------------------------------------------------------------- generic sortable table
function table(rows, cols, sortState, opts = {}) {
  const sorted = rows.slice();
  const col = cols.find((c) => c.key === sortState.key) || cols[0];
  const val = col.sort || ((r) => r[col.key]);
  sorted.sort((a, b) => {
    const va = val(a), vb = val(b);
    const na = typeof va === "number" ? (Number.isNaN(va) ? null : va) : (va === null || va === undefined || va === "" ? null : va);
    const nb = typeof vb === "number" ? (Number.isNaN(vb) ? null : vb) : (vb === null || vb === undefined || vb === "" ? null : vb);
    if (na === null && nb === null) return 0;
    if (na === null) return 1;  // missing values always last
    if (nb === null) return -1;
    return (typeof na === "number" && typeof nb === "number" ? na - nb : String(na).localeCompare(String(nb))) * sortState.dir;
  });
  const shown = opts.limit ? sorted.slice(0, opts.limit) : sorted;
  const thead = h("thead", {}, h("tr", {}, cols.map((c) => {
    const active = c.key === sortState.key;
    const fixed = c.nosort || opts.fixed;  // fixed: the sort order is part of the card's meaning
    return h("th", { class: (c.num ? "num " : "") + (fixed ? "" : "sortable"), title: c.title || null, scope: "col",
      "aria-sort": active ? (sortState.dir > 0 ? "ascending" : "descending") : null,
      onclick: fixed ? null : () => { if (active) sortState.dir *= -1; else { sortState.key = c.key; sortState.dir = c.num ? -1 : 1; } render(); } },
    c.num && active ? h("span", { class: "arrow", text: sortState.dir > 0 ? "▲" : "▼" }) : null,
    c.label, !c.num && active ? h("span", { class: "arrow", text: sortState.dir > 0 ? " ▲" : " ▼" }) : null);
  })));
  const tbody = h("tbody", {}, shown.map((r) => h("tr", {
    class: r.symbol ? "tr-click" : null, "data-sym": r.symbol || null, tabindex: r.symbol ? "0" : null,
    onclick: r.symbol ? () => select(r.symbol) : null,
    onkeydown: r.symbol ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(r.symbol); } } : null,
  }, cols.map((c) => {
    const v = c.fmt ? c.fmt(r) : r[c.key];
    return h("td", { class: [c.num ? "num" : "", c.cls || "", c.cellCls ? c.cellCls(r) : ""].join(" ").trim() || null,
      title: c.titleFn ? c.titleFn(r) : null }, v instanceof Node ? v : (v === null || v === undefined || v === "" ? "–" : String(v)));
  }))));
  const t = h("table", { class: "data" + (opts.dense ? " dense" : "") }, thead, tbody);
  if (!rows.length) return h("div", { class: "empty", text: opts.empty || "Nothing matches the current filters." });
  return h("div", { class: "table-wrap" + (opts.short ? " short" : "") }, t);
}
// common columns
const C = {
  symbol: { key: "symbol", label: "Symbol", fmt: (r) => h("span", {}, h("span", { class: "sym", text: r.symbol || "–" }), r.owned ? h("span", { class: "owned-mark", title: "Owned" }) : null) },
  name: { key: "name", label: "Name", cls: "name", titleFn: (r) => r.name || "" },
  status: { key: "status", label: "Status", fmt: (r) => statusEl(r.status), sort: (r) => STATUS_ORDER.indexOf(r.status) },
  cls: { key: "class", label: "Class" },
  score: { key: "entry_score", label: "Entry score", num: true, title: "100 × (1 − price / max entry price)", fmt: (r) => scoreCell(r.entry_score), sort: (r) => num(r.entry_score) },
  price: { key: "price", label: "Price", num: true, fmt: (r) => fmtPx(r.price), sort: (r) => num(r.price) },
  ceiling: { key: "ceiling_px", label: "Max entry", num: true, title: "Max entry price per share, quote currency", fmt: (r) => fmtPx(r.ceiling_px), sort: (r) => num(r.ceiling_px) },
  ccy: { key: "quote_ccy", label: "Ccy" },
  head: { key: "headroom", label: "To ceiling", num: true, title: "Price change that puts the price exactly on the max entry price (ceiling / price − 1)",
    fmt: (r) => fmtSignedPct(headroom(r)), sort: (r) => headroom(r), cellCls: (r) => headroom(r) >= 0 ? "pos-text" : "" },
  adv: { key: "adverse_score", label: "Adverse score", num: true, title: "Same formula against the adverse-case value: the downside check", fmt: (r) => fmtScore(r.adverse_score), sort: (r) => num(r.adverse_score) },
  impRet: { key: "implied_return", label: "Impl. return", num: true, fmt: (r) => fmtPct(r.implied_return), sort: (r) => num(r.implied_return) },
  hurdle: { key: "hurdle", label: "Hurdle", num: true, fmt: (r) => fmtPct(r.hurdle), sort: (r) => num(r.hurdle) },
  spread: { key: "spread", label: "Impl. − hurdle", num: true, title: "Implied return minus the currency hurdle, percentage points",
    fmt: (r) => fmtPP(num(r.implied_return) - num(r.hurdle)), sort: (r) => num(r.implied_return) - num(r.hurdle) },
  pos: { key: "position_eur", label: "Position", num: true, fmt: (r) => ok(r.position_eur) ? fmtEur(r.position_eur) : "", sort: (r) => num(r.position_eur) },
};
const pctCol = (key, label, title) => ({ key, label, num: true, title, fmt: (r) => fmtPct(r[key]), sort: (r) => num(r[key]) });
const ratioCol = (key, label, d = 2, title) => ({ key, label, num: true, title, fmt: (r) => fmtRatio(r[key], d), sort: (r) => num(r[key]) });
const pctlCol = (key, label) => ({ key, label, num: true, fmt: (r) => fmtNum(r[key], 0), sort: (r) => num(r[key]) });

function scoreCell(v) {
  const x = num(v);
  if (!Number.isFinite(x)) return h("span", { class: "dim", text: "–" });
  const W = 90, zero = W * (-CLIP_LO) / (CLIP_HI - CLIP_LO);
  const cx = Math.max(CLIP_LO, Math.min(CLIP_HI, x));
  const px = (cx / (CLIP_HI - CLIP_LO)) * W;
  const bar = x >= 0
    ? h("span", { class: "bar pos", style: { left: zero + "px", width: Math.max(1, px) + "px" } })
    : h("span", { class: "bar neg" + (x <= CLIP_LO ? " clip" : ""), style: { left: zero + px + "px", width: Math.max(1, -px) + "px" } });
  return h("span", { class: "cellbar" }, h("span", { text: fmtScore(x) }),
    h("span", { class: "track", "aria-hidden": "true" }, bar, h("span", { class: "zero", style: { left: zero + "px" } }),
      x <= CLIP_LO ? h("span", { class: "over", text: "‹" }) : null));
}

// ---------------------------------------------------------------- Overview
function viewOverview() {
  const rows = filtered();
  const scored = rows.filter((r) => SCORED.includes(r.status) && ok(r.entry_score));
  const by = (st) => rows.filter((r) => r.status === st).length;
  const cands = rows.filter((r) => r.candidate);
  const prevCands = rows.filter((r) => r.candidate_prev === true || r.candidate_prev === "True").length;
  const hasPrev = !!S.data.previous;
  const med = median(scored.map((r) => num(r.entry_score)));
  const owned = rows.filter((r) => r.owned);
  const ownedScored = owned.filter((r) => SCORED.includes(r.status) && ok(r.entry_score));
  const vScored = sum(ownedScored.map((r) => num(r.position_eur)));
  const vBelow = sum(ownedScored.filter((r) => num(r.entry_score) >= 0).map((r) => num(r.position_eur)));
  const age = S.today ? daysBetween(runDay(S.run), S.today) : 0;

  const out = [pageHeader("Overview")];
  if (age > 8) out.push(h("div", { class: "banner warn" }, icon('<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'),
    h("div", { text: "This run is " + age + " days old. Rerun the screener, then Reload." })));
  out.push(filterBar(rows.length, S.rows.length));
  out.push(h("div", { class: "grid kpis" },
    kpi("Candidates", String(cands.length), hasPrev ? "previous run " + prevCands : null, { hero: true, status: "eligible", onclick: () => filterTo("eligible"),
      deltaCls: hasPrev ? (cands.length > prevCands ? "up" : cands.length < prevCands ? "down" : "") : "", title: "Entry score ≥ 0 and no critical data flag" }),
    kpi("Above ceiling", String(by("price above ceiling")), null, { status: "price above ceiling", onclick: () => filterTo("price above ceiling") }),
    kpi("Data flag", String(by("data flag")), null, { title: "Scored, but a critical data flag blocks candidate status", status: "data flag", onclick: () => filterTo("data flag") }),
    kpi("Not rankable", String(by("not rankable")), null, { title: "An essential input is missing", status: "not rankable", onclick: () => filterTo("not rankable") }),
    kpi("Not screened", String(by("not screened")), null, { title: "Loss-making, holding company or fund", status: "not screened", onclick: () => filterTo("not screened") }),
    kpi("Median entry score", fmtScore(med), null, { title: "Median over candidates, data-flag and above-ceiling names" }),
    kpi("Holdings at/below ceiling", vScored > 0 ? fmtPct(vBelow / vScored, 0) : "–", vScored > 0 ? fmtEurK(vBelow) + " of " + fmtEurK(vScored) : null,
      { onclick: () => go("holdings"), title: "Share of the euro value of scored holdings whose price is at or below the max entry price" }),
  ));
  out.push(card("Distance to the max entry price", "", swarmChart(scored), {
    right: legend([["dot", "var(--pos)", "At/below ceiling"], ["dot", "var(--neg)", "Above ceiling"], ["hollow", "var(--fg-dim)", "Data flag"], ["ring", "", "Owned"]]),
    foot: "Entry score = 100 × (1 − price / max entry price), floored at −100. Click a dot for details." }));
  const near = rows.filter((r) => r.status === "price above ceiling" && num(r.entry_score) >= -25);
  out.push(h("div", { class: "grid halves" },
    card("Candidates", String(cands.length),
      table(cands, [C.symbol, C.name, C.score, C.head, C.spread], { key: "entry_score", dir: -1 }, { fixed: true, dense: true, short: true, empty: "No name is at or below its max entry price with clean data." }),
      { flush: true }),
    card("Closest to the line", "",
      table(near, [C.symbol, C.name, C.score, C.price, C.ceiling, C.ccy, C.head], { key: "entry_score", dir: -1 }, { fixed: true, dense: true, short: true, empty: "No name is within 25 points of its ceiling." }),
      { flush: true, foot: "Above the ceiling by at most 25 points. “To ceiling” is the price move that reaches the max entry price." })));
  const growthPts = scored.filter((r) => ok(r.est_growth) && ok(r.implied_growth));
  const ownedRows = rows.filter((r) => r.owned && SCORED.includes(r.status));
  out.push(h("div", { class: "grid halves" },
    card("Growth the price needs vs estimated growth", "", scatterGrowth(growthPts), {
      right: legend([["dot", "var(--cls-operating)", "Operating"], ["dot", "var(--cls-cyclical)", "Cyclical"], ["dot", "var(--cls-financial)", "Financial"]]),
      foot: "Below the diagonal the price needs less growth than estimated. " + growthPts.length + " of " + scored.length + " names have a solution." }),
    card("Holdings", "",
      table(ownedRows, [C.symbol, C.name, C.score, C.head, C.pos], { key: "entry_score", dir: -1 }, { fixed: true, dense: true, short: true, empty: "No scored holdings in the current filter." }),
      { flush: true, right: h("button", { class: "btn ghost", text: "All holdings →", onclick: () => go("holdings") }) })));
  return out;
}
function legend(items) {
  return h("div", { class: "legend" }, items.map(([kind, color, label]) => h("span", { class: "key" },
    kind === "ring" ? h("span", { class: "ring" }) :
      kind === "hollow" ? h("span", { class: "dot", style: { boxShadow: "inset 0 0 0 2px " + color } }) :
        kind === "line" ? h("span", { class: "line", style: { borderTopColor: color } }) :
          h("span", { class: kind === "sw" ? "sw" : "dot", style: { background: color } }), label)));
}
const sum = (a) => a.filter(Number.isFinite).reduce((x, y) => x + y, 0);
function median(a) { const v = a.filter(Number.isFinite).sort((x, y) => x - y); if (!v.length) return NaN; const m = v.length >> 1; return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2; }

// --- swarm (1-D beeswarm of the entry score)
function swarmChart(rows) {
  if (!rows.length) return h("div", { class: "empty", text: "No scored names in the current filter." });
  const W = cardWidth(1), padL = 104, padR = 16, R = 4.5, gap = 2, plotW = W - padL - padR;
  const xOf = (v) => padL + (Math.max(CLIP_LO, Math.min(CLIP_HI, v)) - CLIP_LO) / (CLIP_HI - CLIP_LO) * plotW;
  const all = rows.map((r) => ({ r, v: num(r.entry_score) }));
  const over = all.filter((p) => p.v <= CLIP_LO).sort((a, b) => b.v - a.v);   // shown as one overflow marker, not as dots
  const pts = all.filter((p) => p.v > CLIP_LO).map((p) => ({ ...p, x: xOf(p.v) })).sort((a, b) => a.x - b.x);
  const placed = []; const D = 2 * R + gap;
  for (const p of pts) {   // nearest free offset from the centre line
    let y = 0;
    for (let k = 0; k < 400; k++) {
      const cand = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (D / 2);
      if (!placed.some((q) => Math.abs(q.x - p.x) < D && (q.x - p.x) ** 2 + (q.y - cand) ** 2 < D * D)) { y = cand; break; }
    }
    p.y = y; placed.push(p);
  }
  const ext = Math.max(D * 2.5, ...placed.map((p) => Math.abs(p.y))) + R + 4;
  const top = 24, mid = top + ext, Hh = mid + ext + 30;
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Entry score of each scored name" });
  svgEl.appendChild(s("rect", { class: "zone-pos", x: xOf(0), y: top - 8, width: xOf(CLIP_HI) - xOf(0), height: Hh - top - 22 }));
  for (const t of TICKS) {
    svgEl.appendChild(s("line", { class: t === 0 ? "zero-line" : "grid-line", x1: xOf(t), x2: xOf(t), y1: top - 8, y2: Hh - 24 }));
    svgEl.appendChild(s("text", { class: "tick-label", x: xOf(t), y: Hh - 10, "text-anchor": "middle", text: fmtTick(t) }));
  }
  svgEl.appendChild(s("text", { class: "label-strong", x: xOf(0) + 6, y: top + 4, text: "Max entry price" }));
  svgEl.appendChild(s("text", { x: xOf(CLIP_HI) - 4, y: top + 4, "text-anchor": "end", text: "cheaper →" }));
  svgEl.appendChild(s("text", { x: xOf(CLIP_LO) + 4, y: top + 4, text: "← dearer" }));
  const surface = cssVar("--bg-card"), ink = cssVar("--fg-main");
  if (over.length) {   // overflow bin left of the axis: names priced above 3x their ceiling
    const g = s("g", { class: "pt", tabindex: "0", role: "button", "aria-label": over.length + " names at the " + CLIP_LO + " floor" });
    g.appendChild(s("rect", { class: "mark", x: 6, y: mid - 18, width: padL - 20, height: 36, rx: 4, fill: "var(--neg-soft)", stroke: "var(--neg)", "stroke-width": 1 }));
    g.appendChild(s("text", { class: "label-strong", x: 6 + (padL - 20) / 2, y: mid - 3, "text-anchor": "middle", text: over.length + " names" }));
    g.appendChild(s("text", { x: 6 + (padL - 20) / 2, y: mid + 12, "text-anchor": "middle", text: "≥ 2× ceiling" }));
    const tipRows = over.slice(0, 10).map((p) => ({ label: p.r.symbol, value: fmtSignedPct(headroom(p.r), 0) + " to ceiling", color: "var(--neg)" }));
    if (over.length > 10) tipRows.push({ label: "and " + (over.length - 10) + " more", value: "" });
    g.addEventListener("pointerenter", (e) => showTip(e, "Price at least 2× the max entry price", "score floored at " + MINUS + "100 · click to list them", tipRows));
    g.addEventListener("pointermove", moveTip); g.addEventListener("pointerleave", hideTip);
    g.addEventListener("click", () => { S.sort = { key: "entry_score", dir: 1 }; filterTo("scored"); });
    svgEl.appendChild(g);
  }
  for (const p of placed) {
    const r = p.r, pos = p.v >= 0, flag = r.status === "data flag";
    const color = pos ? "var(--pos)" : "var(--neg)";
    const g = s("g", { class: "pt", "data-sym": r.symbol });
    g.appendChild(s("circle", { class: "hit", cx: p.x, cy: mid + p.y, r: 9 }));
    g.appendChild(s("circle", { class: "mark", cx: p.x, cy: mid + p.y, r: R, fill: flag ? surface : color, stroke: flag ? color : surface, "stroke-width": 2 }));
    if (r.owned) g.appendChild(s("circle", { cx: p.x, cy: mid + p.y, r: R + 2.2, fill: "none", stroke: ink, "stroke-width": 1.4, "pointer-events": "none" }));
    g.addEventListener("pointerenter", (e) => showTip(e, r.symbol + " · " + (r.name || ""), STATUS_LABEL[r.status] + (r.owned ? " · owned" : ""), [
      { label: "Entry score", value: fmtScore(r.entry_score), color },
      { label: "Price", value: fmtPx(r.price) + " " + (r.quote_ccy || "") },
      { label: "Max entry", value: fmtPx(r.ceiling_px) + " " + (r.quote_ccy || "") },
      { label: "To ceiling", value: fmtSignedPct(headroom(r)) }]));
    g.addEventListener("pointermove", moveTip);
    g.addEventListener("pointerleave", hideTip);
    g.addEventListener("click", () => select(r.symbol));
    svgEl.appendChild(g);
  }
  return svgEl;
}

// --- scatter: estimated vs implied growth
// Charts are drawn at the pixel width of their card (viewBox = pixels), so 11px text stays 11px on any screen.
// share: the card's fraction of the row when cards sit side by side (they stack below 1280px).
function cardWidth(share = 1) {
  const inner = $("main").clientWidth - 24;                       // .main horizontal padding
  const avail = share < 1 && innerWidth > 1280 ? (inner - 8) * share : inner;
  return Math.max(320, Math.floor(avail - 26));                   // card border + body padding
}
// marks at or beyond the score floor: a small left chevron outside the plot says "≤ −100"
function floorChevron(svgEl, x, y) {
  svgEl.appendChild(s("path", { d: `M${x - 7},${y} l5,-4 v8 z`, fill: "var(--fg-dim)", "pointer-events": "none" }));
}

function scatterGrowth(rows) {
  if (!rows.length) return h("div", { class: "empty", text: "No scored name has both an estimated and an implied growth rate." });
  const W = cardWidth(0.5), Hh = Math.round(Math.min(440, Math.max(300, W * 0.62))), pad = { l: 56, r: 16, t: 14, b: 42 };
  // one domain for both axes (the diagonal stays at 45°); robust to outliers, which are pinned to the edge
  const vals = rows.flatMap((r) => [num(r.est_growth), num(r.implied_growth)]).sort((p, q) => p - q);
  const pct = (f) => vals[Math.min(vals.length - 1, Math.max(0, Math.round(f * (vals.length - 1))))];
  const step = niceStep((pct(0.98) - pct(0.02) + 0.1) / 6);
  const a = Math.floor((pct(0.02) - 0.03) / step) * step, b = Math.ceil((pct(0.98) + 0.03) / step) * step;
  const inside = (v) => v >= a && v <= b;
  const X = (v) => pad.l + (Math.max(a, Math.min(b, v)) - a) / (b - a) * (W - pad.l - pad.r);
  const Y = (v) => Hh - pad.b - (Math.max(a, Math.min(b, v)) - a) / (b - a) * (Hh - pad.t - pad.b);
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Estimated growth versus growth implied by the price" });
  svgEl.appendChild(s("path", { class: "zone-pos", d: `M${X(a)},${Y(a)} L${X(b)},${Y(b)} L${X(b)},${Y(a)} Z` }));
  for (let v = a; v <= b + 1e-9; v += step) {
    svgEl.appendChild(s("line", { class: "grid-line", x1: X(v), x2: X(v), y1: pad.t, y2: Hh - pad.b }));
    svgEl.appendChild(s("line", { class: "grid-line", x1: pad.l, x2: W - pad.r, y1: Y(v), y2: Y(v) }));
    svgEl.appendChild(s("text", { class: "tick-label", x: X(v), y: Hh - pad.b + 15, "text-anchor": "middle", text: fmtPct(v, 0) }));
    if (v > a + 1e-9) svgEl.appendChild(s("text", { class: "tick-label", x: pad.l - 6, y: Y(v) + 4, "text-anchor": "end", text: fmtPct(v, 0) }));
  }
  if (a < 0 && b > 0) {
    svgEl.appendChild(s("line", { class: "axis-line", x1: X(0), x2: X(0), y1: pad.t, y2: Hh - pad.b }));
    svgEl.appendChild(s("line", { class: "axis-line", x1: pad.l, x2: W - pad.r, y1: Y(0), y2: Y(0) }));
  }
  svgEl.appendChild(s("line", { class: "ref-line", x1: X(a), y1: Y(a), x2: X(b), y2: Y(b) }));
  // diagonal label follows the line near its lower end, where points are sparse; region label sits in its corner
  const ang = -Math.atan2(Y(a) - Y(b), X(b) - X(a)) * 180 / Math.PI, lx = X(a + (b - a) * 0.08), ly = Y(a + (b - a) * 0.08) - 6;
  svgEl.appendChild(s("text", { x: lx, y: ly, transform: `rotate(${ang} ${lx} ${ly})`, text: "price needs exactly the estimate" }));
  svgEl.appendChild(s("text", { x: X(b) - 8, y: Hh - pad.b - 10, "text-anchor": "end", text: "price needs less growth than estimated" }));
  svgEl.appendChild(s("text", { x: (pad.l + W - pad.r) / 2, y: Hh - 8, "text-anchor": "middle", text: "Estimated starting growth (reasonable case)" }));
  svgEl.appendChild(s("text", { x: 12, y: (pad.t + Hh - pad.b) / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${(pad.t + Hh - pad.b) / 2})`, text: "Growth the price implies" }));
  const surface = cssVar("--bg-card"), ink = cssVar("--fg-main");
  const colorOf = (c) => ({ operating: "var(--cls-operating)", cyclical: "var(--cls-cyclical)", financial: "var(--cls-financial)" })[c] || "var(--neutral-mark)";
  let pinned = 0;
  for (const r of rows) {
    const ex = num(r.est_growth), iy = num(r.implied_growth), x = X(ex), y = Y(iy), out = !(inside(ex) && inside(iy));
    pinned += out ? 1 : 0;
    const g = s("g", { class: "pt", "data-sym": r.symbol });
    g.appendChild(s("circle", { class: "hit", cx: x, cy: y, r: 10 }));
    g.appendChild(s("circle", { class: "mark", cx: x, cy: y, r: 4.5, fill: out ? surface : colorOf(r.class), stroke: out ? colorOf(r.class) : surface, "stroke-width": 2 }));
    if (r.owned) g.appendChild(s("circle", { cx: x, cy: y, r: 7, fill: "none", stroke: ink, "stroke-width": 1.2, "pointer-events": "none" }));
    g.addEventListener("pointerenter", (e) => showTip(e, r.symbol + " · " + (r.name || ""), (r.class || "") + " · " + STATUS_LABEL[r.status] + (out ? " · outside the axis range" : ""), [
      { label: "Implied growth", value: fmtPct(r.implied_growth) },
      { label: "Estimated growth", value: fmtPct(r.est_growth), color: colorOf(r.class) },
      { label: "Return on capital", value: fmtPct(r.return_on_capital) },
      { label: "Entry score", value: fmtScore(r.entry_score) }]));
    g.addEventListener("pointermove", moveTip);
    g.addEventListener("pointerleave", hideTip);
    g.addEventListener("click", () => select(r.symbol));
    svgEl.appendChild(g);
  }
  if (pinned) svgEl.appendChild(s("text", { x: pad.l + 6, y: pad.t + 12, text: pinned + " outside the range, shown hollow at the edge" }));
  return svgEl;
}
function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
}

// ---------------------------------------------------------------- Screen
const COLSETS = {
  decision: () => [C.symbol, C.name, C.status, C.cls, C.score, C.price, C.ceiling, C.ccy, C.head, C.adv, C.impRet, C.hurdle, C.spread,
    pctCol("est_growth", "Est. g"), pctCol("implied_growth", "Impl. g"), pctCol("return_on_capital", "R", "Return on new capital (ROIC or ROE, capped)"), C.pos],
  valuation: () => [C.symbol, C.name, C.price, C.ceiling, { key: "reasonable_px", label: "Reasonable", num: true, fmt: (r) => fmtPx(r.reasonable_px), sort: (r) => num(r.reasonable_px) },
    { key: "adverse_px", label: "Adverse", num: true, fmt: (r) => fmtPx(r.adverse_px), sort: (r) => num(r.adverse_px) }, C.ccy, C.adv, C.hurdle,
    pctCol("floor_rate", "Adverse floor"), pctCol("est_growth", "Est. g"), pctCol("adverse_growth", "Adverse g"), pctCol("terminal_growth", "Terminal g"),
    pctCol("return_on_capital", "R"), pctCol("reinvest_rate", "Reinvest", "Share of earnings reinvested to fund growth (g / R)"),
    pctCol("implied_return_base", "Impl. return, base ccy", "Local implied return converted to the base currency by the rate differential"),
    { key: "caps_hit", label: "Caps hit", cls: "wrap" }],
  fundamentals: () => [C.symbol, C.name, C.cls, pctCol("nopat_yield", "NOPAT/EV"), pctCol("fcf_yield", "FCF−SBC/EV"), pctCol("ebit_yield", "EBIT/EV"),
    pctCol("earnings_yield", "Earn. yield"), pctCol("roic", "ROIC"), pctCol("roic_ex_goodwill", "ROIC ex GW"), pctCol("roe", "ROE"), pctCol("gross_profitability", "GP/assets"),
    ratioCol("cash_conversion", "FCF/NI"), pctCol("rev_cagr_3y", "Rev CAGR 3y"), pctCol("analyst_growth", "Analyst g"),
    ratioCol("net_debt_ebitda", "ND/EBITDA", 1), pctCol("share_cagr", "Shares CAGR"), ratioCol("evs_vs_hist", "EV/S vs 5y", 2, "EV/Sales relative to own 5-year median"),
    { key: "mcap_eur_bn", label: "Mcap €bn", num: true, fmt: (r) => fmtNum(r.mcap_eur_bn, 1), sort: (r) => num(r.mcap_eur_bn) }],
  context: () => [C.symbol, C.name, C.cls, pctlCol("composite", "Peer pctl"), pctlCol("score_value", "Value"), pctlCol("score_quality", "Quality"),
    pctlCol("score_growth", "Growth"), pctlCol("score_balance", "Balance"), { key: "sector", label: "Sector" }, { key: "industry", label: "Industry", cls: "name" }, { key: "lists", label: "Lists" }],
  data: () => [C.symbol, C.name, C.status, { key: "data_as_of", label: "Data as of" }, { key: "currency", label: "Reporting" }, C.ccy,
    { key: "not_rankable", label: "Not rankable: reason" }, { key: "flags", label: "Flags", cls: "wrap" }],
};
function viewScreen() {
  const rows = filtered();
  const cols = COLSETS[S.colset]();
  if (!cols.some((c) => c.key === S.sort.key)) S.sort = { key: cols[2].key, dir: -1 };
  const seg = h("div", { class: "segmented", role: "group", "aria-label": "Column set" },
    [["decision", "Decision"], ["valuation", "Valuation inputs"], ["fundamentals", "Fundamentals"], ["context", "Peer context"], ["data", "Data"]]
      .map(([k, l]) => h("button", { "aria-pressed": String(S.colset === k), text: l, onclick: () => { S.colset = k; render(); } })));
  const exp = h("button", { class: "btn", onclick: () => exportCsv(rows, cols) }, icon('<path d="M10 3v10M6 9l4 4 4-4M4 16h12"/>'), "Export CSV");
  return [pageHeader("Screen", "", seg, exp),
    filterBar(rows.length, S.rows.length),
    h("section", { class: "card" }, h("div", { class: "card-body flush" }, table(rows, cols, S.sort)))];
}
function exportCsv(rows, cols) {
  const keys = cols.map((c) => c.key).filter((k) => !["headroom", "spread"].includes(k));
  const esc = (v) => { const t = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
  const csv = [keys.join(","), ...rows.map((r) => keys.map((k) => esc(r[k])).join(","))].join("\r\n");
  const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "screen_" + S.run + "_" + S.colset + ".csv" });
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

// ---------------------------------------------------------------- Holdings
function viewHoldings() {
  const owned = S.rows.filter((r) => r.owned);
  const total = sum(owned.map((r) => num(r.position_eur)));
  const byStatus = STATUS_ORDER.map((st) => ({ st, v: sum(owned.filter((r) => r.status === st).map((r) => num(r.position_eur))), n: owned.filter((r) => r.status === st).length }))
    .filter((x) => x.n);
  const other = owned.filter((r) => !STATUS_ORDER.includes(r.status));
  if (other.length) byStatus.push({ st: "unknown", v: sum(other.map((r) => num(r.position_eur))), n: other.length });
  const scored = owned.filter((r) => SCORED.includes(r.status) && ok(r.entry_score));
  const vScored = sum(scored.map((r) => num(r.position_eur)));
  const below = scored.filter((r) => num(r.entry_score) >= 0);
  const vBelow = sum(below.map((r) => num(r.position_eur)));
  const wavg = vScored > 0 ? sum(scored.map((r) => num(r.position_eur) * Math.max(CLIP_LO, num(r.entry_score)))) / vScored : NaN;
  const maxPos = Math.max(0, ...owned.map((r) => num(r.position_eur)).filter(Number.isFinite));
  const posCol = { key: "position_eur", label: "Position", num: true, sort: (r) => num(r.position_eur),
    fmt: (r) => h("span", { class: "hbar" }, h("span", { class: "track" }, h("span", { class: "bar" + (SCORED.includes(r.status) ? "" : " muted"), style: { width: (maxPos > 0 ? num(r.position_eur) / maxPos * 100 : 0) + "%" } })), h("span", { text: fmtEur(r.position_eur) })) };
  const wCol = { key: "weight", label: "Weight", num: true, sort: (r) => num(r.position_eur), fmt: (r) => total > 0 && ok(r.position_eur) ? fmtPct(num(r.position_eur) / total, 1) : "–" };
  return [pageHeader("Holdings"),
    h("div", { class: "grid kpis" },
      kpi("Holdings value", fmtEurK(total), owned.length + " positions", { hero: true }),
      kpi("Scored value at/below ceiling", vScored > 0 ? fmtPct(vBelow / vScored, 0) : "–", fmtEurK(vBelow) + " of " + fmtEurK(vScored), { status: "eligible" }),
      kpi("Value-weighted entry score", fmtScore(wavg), null, { title: "Position-weighted mean entry score of scored holdings" }),
      kpi("Not screened", fmtEurK(sum(owned.filter((r) => r.status === "not screened").map((r) => num(r.position_eur)))), null, { title: "Funds, holding companies, loss-making", status: "not screened" })),
    card("Holdings value by status", "", statusBar(byStatus, total), {}),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "Positions" }), null),
      h("div", { class: "card-body flush" }, table(owned, [C.symbol, C.name, C.status, posCol, wCol, C.score, C.price, C.ceiling, C.ccy, C.head, C.spread], S.holdSort,
        { empty: "No holdings in this run. Fill input/holdings.csv and run the screener." }))),
    entriesCard(owned)];
}

// your buy-in against today's max entry price (cost_px / cost_score come from the screener, see metrics.cost_basis)
function entriesCard(owned) {
  const rows = owned.filter((r) => ok(r.cost_px));
  const scored = rows.filter((r) => ok(r.cost_score) && ok(r.entry_score));
  if (!rows.length) return card("Your entries", "", h("div", { class: "empty", text: "No cost basis yet: add cost_eur to input/holdings.csv and rerun the screener." }));
  const vScored = sum(scored.map((r) => num(r.position_eur)));
  const vBelow = sum(scored.filter((r) => num(r.cost_score) >= 0).map((r) => num(r.position_eur)));
  const gain = (r) => ok(r.cost_eur) && num(r.cost_eur) > 0 ? num(r.position_eur) / num(r.cost_eur) - 1 : NaN;
  const cols = [C.symbol, C.name,
    { key: "cost_px", label: "Your avg cost", num: true, title: "Euro cost per share at today's exchange rate", fmt: (r) => fmtPx(r.cost_px), sort: (r) => num(r.cost_px) },
    C.price, C.ceiling, C.ccy,
    { key: "cost_score", label: "Score at your cost", num: true, title: "100 × (1 − your avg cost / today's max entry price)", fmt: (r) => scoreCell(r.cost_score), sort: (r) => num(r.cost_score) },
    C.score,
    { key: "gain", label: "Since purchase", num: true, title: "Position value / cost basis − 1, in euro", fmt: (r) => fmtSignedPct(gain(r)), sort: (r) => gain(r), cellCls: (r) => gain(r) >= 0 ? "pos-text" : "neg-text" },
    C.pos];
  return h("div", { class: "grid", style: { gap: "8px" } },
    card("Your entries vs today", vScored > 0 ? fmtPct(vBelow / vScored, 0) + " of scored value bought at or below today's max entry" : "",
      entryChart(scored.sort((a, b) => num(b.position_eur) - num(a.position_eur))), {
        right: legend([["hollow", "var(--fg-dim)", "At your cost"], ["dot", "var(--pos)", "Today, at/below ceiling"], ["dot", "var(--neg)", "Today, above ceiling"]]),
        foot: "Both scores use today's max entry price. Your cost is converted from euro at today's exchange rate, so currency moves since you bought are included." }),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "Your entries" })),
      h("div", { class: "card-body flush" }, table(rows, cols, S.entrySort))));
}
function entryChart(rows) {
  if (!rows.length) return h("div", { class: "empty", text: "No scored holding has a cost basis." });
  const W = cardWidth(1), rowH = 24, pad = { l: 96, r: 70, t: 8, b: 28 }, inset = 16, Hh = pad.t + rows.length * rowH + pad.b;
  const X = (v) => pad.l + inset + (Math.max(CLIP_LO, Math.min(CLIP_HI, v)) - CLIP_LO) / (CLIP_HI - CLIP_LO) * (W - pad.l - inset - pad.r);
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Entry score at your cost and today" });
  svgEl.appendChild(s("rect", { class: "zone-pos", x: X(0), y: pad.t, width: X(CLIP_HI) - X(0), height: Hh - pad.t - pad.b }));
  for (const v of TICKS) {
    svgEl.appendChild(s("line", { class: v === 0 ? "zero-line" : "grid-line", x1: X(v), x2: X(v), y1: pad.t, y2: Hh - pad.b }));
    svgEl.appendChild(s("text", { class: "tick-label", x: X(v), y: Hh - 9, "text-anchor": "middle", text: v === CLIP_LO ? "≤ −100" : fmtTick(v) }));
  }
  const surface = cssVar("--bg-card");
  rows.forEach((r, i) => {
    const y = pad.t + i * rowH + rowH / 2, a = num(r.cost_score), b = num(r.entry_score);
    const color = b >= 0 ? "var(--pos)" : "var(--neg)";
    const g = s("g", { class: "pt", "data-sym": r.symbol });
    g.appendChild(s("rect", { class: "hit", x: 0, y: y - rowH / 2, width: W, height: rowH }));
    g.appendChild(s("text", { x: pad.l - 10, y: y + 4, "text-anchor": "end", class: "label-strong", text: r.symbol }));
    if (Math.abs(X(a) - X(b)) > 1) g.appendChild(s("line", { x1: X(a), x2: X(b), y1: y, y2: y, stroke: "var(--neutral-mark)", "stroke-width": 2, "stroke-linecap": "round" }));
    if (a <= CLIP_LO || b <= CLIP_LO) floorChevron(g, X(CLIP_LO) - 4, y);
    g.appendChild(s("circle", { class: "mark", cx: X(b), cy: y, r: 4.5, fill: color, stroke: surface, "stroke-width": 2 }));
    // the cost ring is drawn last and unfilled, so it never hides today's dot when the two are close
    g.appendChild(s("circle", { cx: X(a), cy: y, r: 5.5, fill: "none", stroke: "var(--fg-dim)", "stroke-width": 1.6 }));
    g.appendChild(s("text", { class: "tick-label", x: W - pad.r + 10, y: y + 4, text: fmtEurK(r.position_eur) }));
    g.addEventListener("pointerenter", (e) => showTip(e, r.symbol + " · " + (r.name || ""), "", [
      { label: "Score at your cost", value: fmtScore(a) }, { label: "Score today", value: fmtScore(b), color },
      { label: "Your avg cost", value: fmtPx(r.cost_px) + " " + (r.quote_ccy || "") }, { label: "Price", value: fmtPx(r.price) + " " + (r.quote_ccy || "") },
      { label: "Max entry", value: fmtPx(r.ceiling_px) + " " + (r.quote_ccy || "") }]));
    g.addEventListener("pointermove", moveTip); g.addEventListener("pointerleave", hideTip);
    g.addEventListener("click", () => select(r.symbol));
    svgEl.appendChild(g);
  });
  return svgEl;
}
function statusBar(parts, total) {
  if (!(total > 0)) return h("div", { class: "empty", text: "No position values." });
  const colors = { "eligible": "var(--fg-ok)", "data flag": "var(--warning-accent)", "price above ceiling": "var(--neg)", "not rankable": "var(--fg-err)", "not screened": "var(--neutral-mark)", "unknown": "var(--grid-major)" };
  const W = 1000, Hh = 26, gap = 2;
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, preserveAspectRatio: "none", style: { height: "26px" }, role: "img", "aria-label": "Holdings value by status" });
  let x = 0;
  parts.forEach((p, i) => {
    const w = p.v / total * W;
    const rect = s("rect", { class: "mark", x: x + (i ? gap / 2 : 0), y: 0, height: Hh, width: Math.max(0, w - (i ? gap / 2 : 0) - (i < parts.length - 1 ? gap / 2 : 0)), fill: colors[p.st], rx: 3 });
    rect.addEventListener("pointerenter", (e) => showTip(e, STATUS_LABEL[p.st] || p.st, p.n + " positions", [{ label: "Value", value: fmtEur(p.v), color: colors[p.st] }, { label: "Share", value: fmtPct(p.v / total) }]));
    rect.addEventListener("pointermove", moveTip); rect.addEventListener("pointerleave", hideTip);
    svgEl.appendChild(rect); x += w;
  });
  return h("div", {}, svgEl, h("div", { class: "legend", style: { marginTop: "8px" } },
    parts.map((p) => h("span", { class: "key" }, h("span", { class: "sw", style: { background: colors[p.st] } }),
      (STATUS_LABEL[p.st] || p.st) + " · " + fmtEurK(p.v) + " (" + fmtPct(p.v / total, 0) + ", " + p.n + ")"))));
}

// ---------------------------------------------------------------- Changes
function viewChanges() {
  const out = [pageHeader("Changes", S.data.previous ? runLabel(S.run) + " vs " + runLabel(S.data.previous) : runLabel(S.run))];
  out.push(card("Run history", S.runs.length + " runs", runHistory(), {}));
  if (!S.data.previous) {
    out.push(h("div", { class: "card" }, h("div", { class: "empty", text: "Only one run so far. Week-over-week changes appear after the next screener run." })));
    return out;
  }
  const rows = S.rows.filter((r) => r.symbol);
  const ev = (e) => rows.filter((r) => r.event === e);
  const removed = S.data.removed || [];
  out.push(h("div", { class: "grid kpis" },
    kpi("New candidates", String(ev("new candidate").length), null, { status: "eligible" }),
    kpi("Dropped as candidates", String(ev("dropped as candidate").length), null),
    kpi("Newly rankable", String(ev("newly rankable").length), null),
    kpi("No longer rankable", String(ev("no longer rankable").length), null, { status: "not rankable" }),
    kpi("Added / removed", ev("new in universe").length + " / " + removed.length, null)));
  const movers = rows.filter((r) => ok(r.score_change) && Math.abs(num(r.score_change)) >= 1).sort((a, b) => Math.abs(num(b.score_change)) - Math.abs(num(a.score_change)));
  out.push(h("div", { class: "grid two" },
    card("Biggest score moves", movers.length ? "top " + Math.min(15, movers.length) + " of " + movers.length : "", dumbbell(movers.slice(0, 15)), {
      right: legend([["dot", "var(--cls-operating)", "Price moved more"], ["dot", "var(--cls-cyclical)", "Ceiling moved more"], ["hollow", "var(--fg-dim)", "Previous run"]]),
      foot: "Driver: whichever moved more in relative terms, the price or the ceiling." }),
    card("Status events", "", table(rows.filter((r) => r.event), [C.symbol, C.name,
      { key: "event", label: "Event" }, { key: "status_prev", label: "Was", fmt: (r) => r.status_prev ? statusEl(r.status_prev) : "–" }, C.status], { key: "event", dir: 1 },
    { fixed: true, dense: true, short: true, empty: "No status changes." }), { flush: true })));
  const changeCols = [C.symbol, C.name, { key: "event", label: "Event" }, { key: "reason", label: "Driver" },
    { key: "entry_score_prev", label: "Score before", num: true, fmt: (r) => fmtScore(r.entry_score_prev), sort: (r) => num(r.entry_score_prev) },
    { key: "entry_score", label: "Score now", num: true, fmt: (r) => fmtScore(r.entry_score), sort: (r) => num(r.entry_score) },
    { key: "score_change_abs", label: "Δ score", num: true, fmt: (r) => fmtScore(r.score_change), sort: (r) => Math.abs(num(r.score_change)) },
    { key: "price_change", label: "Price Δ", num: true, fmt: (r) => fmtSignedPct(r.price_change), sort: (r) => num(r.price_change) },
    { key: "ceiling_change", label: "Ceiling Δ", num: true, fmt: (r) => fmtSignedPct(r.ceiling_change), sort: (r) => num(r.ceiling_change) }];
  out.push(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", { text: "All changes" }), null),
    h("div", { class: "card-body flush" }, table(rows.filter((r) => (ok(r.score_change) && Math.abs(num(r.score_change)) >= 0.5) || r.event), changeCols, S.changeSort, { empty: "No score moved and no status changed between these runs." }))));
  if (removed.length) out.push(card("Removed since " + S.data.previous, removed.length + " names", h("ul", { class: "flags" },
    removed.map((r) => h("li", { text: r.symbol + " · " + (r.name || "") + (ok(r.entry_score) ? " (score was " + fmtScore(r.entry_score) + ")" : "") })))));
  return out;
}
function runHistory() {
  const runs = S.runs.filter((r) => !r.error);
  if (runs.length < 2) return h("div", { class: "empty", text: "One run so far: history starts with the next weekly run." });
  const short = (run) => { const n = String(run).split("_")[1]; return runDay(run).slice(5) + (n ? " #" + (Number(n) + 1) : ""); };
  const W = innerWidth > 1280 ? Math.floor((cardWidth(1) - 8) / 2) : cardWidth(1), Hh = 170, pad = { l: 48, r: 12, t: 14, b: 28 };
  const mk = (title, vals, fmt, kind, floor0) => {
    const v = vals.filter(Number.isFinite);
    let lo = floor0 ? 0 : Math.min(0, ...v), hi = Math.max(...v, floor0 ? 1 : 0);
    const step = niceStep((hi - lo) / 4 || 1); lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const band = (W - pad.l - pad.r) / runs.length, X = (i) => pad.l + (i + 0.5) * band;   // band centres: nothing clips
    const bw = Math.min(28, band * 0.55);
    const Y = (x) => Hh - pad.b - (x - lo) / (hi - lo || 1) * (Hh - pad.t - pad.b);
    const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": title });
    for (let t = lo; t <= hi + 1e-9; t += step) {
      svgEl.appendChild(s("line", { class: t === 0 ? "axis-line" : "grid-line", x1: pad.l, x2: W - pad.r, y1: Y(t), y2: Y(t) }));
      svgEl.appendChild(s("text", { class: "tick-label", x: pad.l - 8, y: Y(t) + 4, "text-anchor": "end", text: fmt(t) }));
    }
    const every = Math.max(1, Math.ceil(runs.length / Math.max(2, Math.floor((W - pad.l) / 70))));
    runs.forEach((r, i) => { if (i % every === 0 || i === runs.length - 1) svgEl.appendChild(s("text", { class: "tick-label", x: X(i), y: Hh - 9, "text-anchor": "middle", text: short(r.date) })); });
    const tip = (e, r, i, color) => showTip(e, runLabel(r.date), title, [{ label: title, value: fmt(vals[i]), color }]);
    if (kind === "bar") runs.forEach((r, i) => {
      if (!Number.isFinite(vals[i])) return;
      const y = Y(Math.max(0, vals[i])), y0 = Y(0), color = r.date === S.run ? "var(--pos)" : "var(--neutral-mark)";
      const bar = s("path", { class: "mark", fill: color, d: roundTopBar(X(i) - bw / 2, y, bw, y0 - y, 4) });
      bar.addEventListener("pointerenter", (e) => tip(e, r, i, color));
      bar.addEventListener("pointermove", moveTip); bar.addEventListener("pointerleave", hideTip);
      svgEl.appendChild(bar);
    });
    else {
      const d = runs.map((r, i) => Number.isFinite(vals[i]) ? (i ? "L" : "M") + X(i) + "," + Y(vals[i]) : "").join(" ");
      svgEl.appendChild(s("path", { d, fill: "none", stroke: "var(--pos)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      runs.forEach((r, i) => {
        if (!Number.isFinite(vals[i])) return;
        const g = s("g", { class: "pt" });
        g.appendChild(s("circle", { class: "hit", cx: X(i), cy: Y(vals[i]), r: 12 }));
        g.appendChild(s("circle", { class: "mark", cx: X(i), cy: Y(vals[i]), r: 4, fill: "var(--pos)", stroke: cssVar("--bg-card"), "stroke-width": 2 }));
        g.addEventListener("pointerenter", (e) => tip(e, r, i, "var(--pos)"));
        g.addEventListener("pointermove", moveTip); g.addEventListener("pointerleave", hideTip);
        svgEl.appendChild(g);
      });
    }
    return h("div", {}, h("div", { class: "small dim", text: title, style: { marginBottom: "4px" } }), svgEl);
  };
  return h("div", { class: "grid halves" },
    mk("Candidates per run", runs.map((r) => r.candidates), (x) => fmtNum(x, 0), "bar", true),
    mk("Median entry score", runs.map((r) => num(r.median_score)), (x) => x <= CLIP_LO ? "≤ −100" : fmtTick(x), "line", false));
}
function roundTopBar(x, y, w, hgt, r) {
  if (hgt <= 0) return "";
  r = Math.min(r, hgt, w / 2);
  return `M${x},${y + hgt} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + hgt} Z`;
}
function dumbbell(rows) {
  if (!rows.length) return h("div", { class: "empty", text: "No score moved by a point or more between these runs." });
  const W = cardWidth(0.6), rowH = 24, pad = { l: 92, r: 56, t: 8, b: 28 }, inset = 16, Hh = pad.t + rows.length * rowH + pad.b;
  const X = (v) => pad.l + inset + (Math.max(CLIP_LO, Math.min(CLIP_HI, v)) - CLIP_LO) / (CLIP_HI - CLIP_LO) * (W - pad.l - inset - pad.r);
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Entry score before and after" });
  for (const v of TICKS) {
    svgEl.appendChild(s("line", { class: v === 0 ? "zero-line" : "grid-line", x1: X(v), x2: X(v), y1: pad.t, y2: Hh - pad.b }));
    if (v % 50 === 0) svgEl.appendChild(s("text", { class: "tick-label", x: X(v), y: Hh - 9, "text-anchor": "middle", text: v === CLIP_LO ? "≤ −100" : fmtTick(v) }));
  }
  const surface = cssVar("--bg-card");
  rows.forEach((r, i) => {
    const y = pad.t + i * rowH + rowH / 2;
    const a = num(r.entry_score_prev), b = num(r.entry_score);
    const color = r.reason === "price" ? "var(--cls-operating)" : "var(--cls-cyclical)";
    const g = s("g", { class: "pt", "data-sym": r.symbol });
    g.appendChild(s("rect", { class: "hit", x: 0, y: y - rowH / 2, width: W, height: rowH }));
    g.appendChild(s("text", { x: pad.l - 10, y: y + 4, "text-anchor": "end", class: "label-strong", text: r.symbol }));
    g.appendChild(s("line", { x1: X(a), x2: X(b), y1: y, y2: y, stroke: color, "stroke-width": 2, "stroke-linecap": "round" }));
    if (a <= CLIP_LO || b <= CLIP_LO) floorChevron(g, X(CLIP_LO) - 4, y);
    g.appendChild(s("circle", { class: "mark", cx: X(b), cy: y, r: 4.5, fill: color, stroke: surface, "stroke-width": 2 }));
    g.appendChild(s("circle", { cx: X(a), cy: y, r: 5, fill: "none", stroke: color, "stroke-width": 1.6 }));
    g.appendChild(s("text", { class: "tick-label", x: W - pad.r + 10, y: y + 4, text: fmtScore(r.score_change) }));
    g.addEventListener("pointerenter", (e) => showTip(e, r.symbol + " · " + (r.name || ""), "driver: " + (r.reason || "–"), [
      { label: "Score before", value: fmtScore(a) }, { label: "Score now", value: fmtScore(b), color },
      { label: "Price change", value: fmtSignedPct(r.price_change) }, { label: "Ceiling change", value: fmtSignedPct(r.ceiling_change) }]));
    g.addEventListener("pointermove", moveTip); g.addEventListener("pointerleave", hideTip);
    g.addEventListener("click", () => select(r.symbol));
    svgEl.appendChild(g);
  });
  return svgEl;
}

// ---------------------------------------------------------------- Data quality
function splitFlags(r) { return String(r.flags || "").split(/;\s*/).map((x) => x.trim()).filter(Boolean); }
function flagKind(f) { return f.replace(/^fx \S+->\S+$/, "currency conversion (fx)").replace(/^(\w+) > 25%: check one-offs$/, "yield > 25%: check one-offs"); }
function isCritical(f) { return CRITICAL.some((c) => f.includes(c)); }
function viewData() {
  const rows = S.rows;
  const counts = new Map();
  for (const r of rows) for (const f of new Set(splitFlags(r).map(flagKind))) counts.set(f, (counts.get(f) || 0) + 1);
  const flagRows = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const staleDays = (S.config && S.config.valuation && S.config.valuation.stale_after_days) || 270;
  const stale = rows.filter((r) => r.data_as_of && daysBetween(r.data_as_of, runDay(S.run)) > staleDays);
  const blocked = rows.filter((r) => r.status === "not rankable" || ["error", "unresolved"].includes(r.class));
  const critical = rows.filter((r) => splitFlags(r).some(isCritical));
  const rateCcys = new Set(((S.config && S.config.rates) || []).map((x) => x.currency));
  const missingCcy = [...new Set(rows.map((r) => r.currency).filter((c) => c && !rateCcys.has(c)))];
  const out = [pageHeader("Data quality")];
  if (missingCcy.length && S.config && S.config.available) out.push(h("div", { class: "banner warn" }, icon('<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'),
    h("div", { text: "Reporting currencies missing from the rates table in config.toml: " + missingCcy.join(", ") + ". Names reporting in them are not rankable." })));
  const usedCcys = new Set(rows.map((r) => r.currency).filter(Boolean));
  const staleRates = ((S.config && S.config.rates) || []).filter((x) => usedCcys.has(x.currency) && (x.age_days === null || x.age_days === undefined || x.age_days > RATE_WARN_DAYS));
  if (staleRates.length) out.push(h("div", { class: "banner warn" }, icon('<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'),
    h("div", {}, "Rates older than " + RATE_WARN_DAYS + " days (or undated) for currencies in use: " + staleRates.map((x) => x.currency + (x.age_days === null || x.age_days === undefined ? " (no date)" : " (" + x.age_days + " d)")).join(", ") + ". ",
      h("a", { href: "#method", text: "Update them in Method." }))));
  out.push(h("div", { class: "grid kpis" },
    kpi("Not rankable / errors", String(blocked.length), null, { status: "not rankable", onclick: () => filterTo("not rankable") }),
    kpi("Critical flags", String(critical.length), null, { title: "Stale statements, no market cap, yield above 25%, bank thin capital", status: "data flag" }),
    kpi("Stale statements", String(stale.length), null, { title: "Older than " + staleDays + " days at run date" }),
    kpi("Oldest data used", fmtDate(S.data.summary.oldest_data_as_of), null)));
  out.push(h("div", { class: "grid halves" },
    card("Flags by type", "", flagBars(flagRows, rows.length), { foot: "Critical flags (marked) block candidate status. Others are context: e.g. no SBC data flatters FCF yield but not the NOPAT-based ceiling." }),
    card("Not rankable and errors", blocked.length + " names", table(blocked, [C.symbol, C.name, C.cls,
      { key: "not_rankable", label: "Reason", cls: "wrap", fmt: (r) => r.not_rankable || r.flags || "" }], { key: "symbol", dir: 1 }, { fixed: true, dense: true, short: true, empty: "Every name could be ranked or was deliberately not screened." }), { flush: true })));
  out.push(card("Critical flags", critical.length + " names", table(critical, [C.symbol, C.name, C.status, C.score, { key: "data_as_of", label: "Data as of" },
    { key: "flags", label: "Flags", cls: "wrap" }], { key: "entry_score", dir: -1 }, { fixed: true, dense: true, short: true, empty: "No critical flags." }), { flush: true }));
  return out;
}
function flagBars(entries, total) {
  if (!entries.length) return h("div", { class: "empty", text: "No flags." });
  const max = Math.max(...entries.map((e) => e[1]));
  return h("div", { style: { display: "grid", gap: "6px" } }, entries.map(([f, n]) => h("div", { class: "pctbar", style: { gridTemplateColumns: "minmax(0,1fr) 140px 34px" } },
    h("span", { class: "name", title: f, style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, isCritical(f) ? h("span", { class: "chip warn", text: "critical", style: { marginRight: "6px" } }) : null, f),
    h("span", { class: "track" }, h("span", { class: "bar", style: { width: n / max * 100 + "%", background: isCritical(f) ? "var(--warning-accent)" : "var(--neutral-mark)" } })),
    h("span", { class: "num", text: String(n), title: fmtPct(n / total, 0) + " of names" }))));
}

// ---------------------------------------------------------------- Lists (watchlist + holdings editor, Yahoo search, rerun)
const L = { tab: "watchlist", data: null, rows: null, removed: null, review: null, reviewed: null, busy: false, notice: null,
  q: "", results: [], searching: false, searchErr: null, filter: "", job: null, poll: null, seq: 0 };
const LIST_COLS = {
  watchlist: [["yahoo_symbol", "Yahoo symbol", "text", "7.5em"], ["symbol", "Symbol", "text", "6em"], ["name", "Name", "text", "16em"],
    ["isin", "ISIN", "text", "9em"], ["lists", "Lists", "text", "11em"]],
  holdings: [["yahoo_symbol", "Yahoo symbol", "text", "7.5em"], ["isin", "ISIN", "text", "9em"], ["name", "Name", "text", "14em"],
    ["shares", "Shares", "num", "6em"], ["avg_cost", "Avg cost €/share", "num", "7em"], ["value_eur", "Value €", "num", "7em"]],
};
const LIST_FIELDS = { watchlist: ["symbol", "name", "isin", "yahoo_symbol", "lists"],
  holdings: ["isin", "symbol", "name", "yahoo_symbol", "shares", "avg_cost", "value_eur"] };

async function postJSON(url, body) {
  let r;
  try {
    r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": (S.config || {}).csrf || "" }, body: JSON.stringify(body) });
  } catch (err) {  // server stopped or unreachable: report it instead of leaving the editor busy forever
    return { ok: false, errors: ["Could not reach the dashboard server (" + err.message + "). Is it still running?"], changes: [] };
  }
  const out = await r.json().catch(() => ({ ok: false, errors: [r.statusText], changes: [] }));
  if (!r.ok && !out.errors) out.errors = [out.error || r.statusText];
  out.status = r.status;
  return out;
}
const listKey = (kind, r) => String((kind === "watchlist" ? (r.yahoo_symbol || r.symbol || r.isin) : (r.isin || r.yahoo_symbol || r.symbol)) || "").toUpperCase();
const snapshot = (kind, r) => JSON.stringify(LIST_FIELDS[kind].map((f) => r[f] ?? ""));
function toEditRow(kind, r) {
  const row = { ...r, _id: ++L.seq };
  if (kind === "holdings") {
    row.avg_cost = ok(r.cost_eur) && num(r.shares) > 0 ? +(num(r.cost_eur) / num(r.shares)).toFixed(6) : null;
    row._basis = [row.shares, row.avg_cost];  // the stored cost_eur is sent back untouched unless these change
  }
  row._orig = snapshot(kind, row);
  return row;
}
async function loadLists() {
  try { L.data = await getJSON("api/inputs"); } catch (e) { L.data = { error: e.message }; return; }
  L.rows = {}; L.removed = { watchlist: new Set(), holdings: new Set() };
  for (const kind of ["watchlist", "holdings"]) L.rows[kind] = (L.data[kind]?.rows || []).map((r) => toEditRow(kind, r));
  L.review = null; L.reviewed = null;
  if (!L.job) {  // a run may already be going (started from another tab or device)
    try { const j = await getJSON("api/rerun"); if (j.running) { L.job = j; pollRun(); } } catch (e) { /* status is optional */ }
  }
}
function listDirty(kind) {
  if (!L.rows) return 0;
  return L.rows[kind].filter((r) => !r._orig || r._orig !== snapshot(kind, r)).length + L.removed[kind].size;
}
function listPayload(kind) {
  return L.rows[kind].filter((r) => !L.removed[kind].has(r._id)).map((r) => {
    const o = {};
    for (const f of LIST_FIELDS[kind]) if (f !== "avg_cost") o[f] = r[f] ?? "";
    if (kind === "holdings") {
      o.shares = ok(r.shares) ? num(r.shares) : null;
      o.value_eur = ok(r.value_eur) ? num(r.value_eur) : null;
      const untouched = r._basis && r._basis[0] === r.shares && r._basis[1] === r.avg_cost;
      o.cost_eur = untouched ? (ok(r.cost_eur) ? num(r.cost_eur) : null)
        : ok(r.shares) && ok(r.avg_cost) ? +(num(r.shares) * num(r.avg_cost)).toFixed(2) : null;
    }
    return o;
  });
}
function latestFor(kind, r) {
  const resolved = (L.data && L.data.resolved) || {};
  if (!S.bySym) return null;
  const ysym = r.yahoo_symbol || resolved[kind === "watchlist" ? (r.symbol || r.isin) : (r.isin || r.symbol)] || resolved[r.symbol] || "";
  // holdings that merged into a watchlist row by name are found through the ISIN the run records for owned names
  return S.bySym.get(ysym) || (r.isin && S.byIsin.get(String(r.isin).toUpperCase())) || S.bySym.get(r.symbol) || null;
}

function viewLists() {
  if (!L.data) { loadLists().then(() => { if (S.view === "lists") render(); }); return [pageHeader("Lists"), h("div", { class: "empty", text: "Loading…" })]; }
  if (L.data.error) return [pageHeader("Lists"), h("div", { class: "banner err" }, h("div", { text: "Could not load the input lists: " + L.data.error }))];
  const editable = !!L.data.editable, kind = L.tab;
  const tabs = h("div", { class: "segmented", role: "group", "aria-label": "List" }, ["watchlist", "holdings"].map((k) => {
    const n = L.rows[k].length - L.removed[k].size, d = listDirty(k);
    return h("button", { "aria-pressed": String(L.tab === k), onclick: () => { L.tab = k; L.review = null; L.reviewed = null; render(); } },
      (k === "watchlist" ? "Watchlist" : "Holdings") + " · " + n, d ? h("span", { class: "dirty-dot", title: d + " unsaved change" + (d === 1 ? "" : "s") }) : null);
  }));
  const running = L.job && L.job.running;
  const runBtn = h("button", { class: "btn", disabled: !editable || running, onclick: startRun, title: "Run the screener now with the saved lists" },
    icon('<path d="M6 4l10 6-10 6z"/>'), running ? "Running…" : "Run screener now");
  const out = [pageHeader("Lists", "", tabs, runBtn)];
  if (!editable) out.push(h("div", { class: "banner" }, icon('<rect x="4" y="9" width="12" height="8" rx="1.5"/><path d="M7 9V6a3 3 0 0 1 6 0v3"/>'),
    h("div", { text: "View only: the lists can be edited on the machine running the dashboard (http://127.0.0.1:8765)." })));
  if (L.notice) out.push(h("div", { class: "banner " + (L.notice.kind === "ok" ? "ok" : "warn") },
    icon(L.notice.kind === "ok" ? '<path d="M4 10l4 4 8-8"/>' : '<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'), h("div", { text: L.notice.text })));
  if (L.job && (L.job.running || L.job.log?.length)) out.push(jobCard());
  if (editable) out.push(searchCard(kind));
  out.push(listTableCard(kind, editable));
  if (editable) out.push(listSaveBar(kind));
  return out;
}

function searchCard(kind) {
  const input = h("input", { type: "search", value: L.q, placeholder: "Company name, ticker or ISIN", "aria-label": "Search Yahoo Finance", style: { width: "min(420px, 100%)" } });
  let t;
  input.addEventListener("input", (e) => {
    L.q = e.target.value; clearTimeout(t);
    t = setTimeout(() => runSearch(kind), 350);
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(t); runSearch(kind); } });
  return card("Add from Yahoo Finance", "", h("div", {},
    h("div", { class: "search-row" }, input, h("span", { class: "small dim", id: "search-state", text: L.searching ? "Searching…" : "" })),
    h("div", { id: "search-results" }, searchResults(kind))), {});
}
async function runSearch(kind) {
  const q = L.q.trim();
  if (!q) { L.results = []; L.searchErr = null; return paintSearch(kind); }
  L.searching = true; paintSearch(kind);
  try {
    const r = await fetch("api/search?q=" + encodeURIComponent(q), { cache: "no-store" });
    const body = await r.json();
    if (q !== L.q.trim()) return;                    // a newer query is on its way
    L.results = body.results || []; L.searchErr = body.error || null;
  } catch (e) { L.results = []; L.searchErr = "Search failed: " + e.message; }
  L.searching = false; paintSearch(kind);
}
function paintSearch(kind) {
  const box = $("search-results"), st = $("search-state");
  if (st) st.textContent = L.searching ? "Searching…" : "";
  if (box) fill(box, searchResults(kind));
}
function searchResults(kind) {
  if (L.searchErr) return h("div", { class: "banner warn", style: { marginTop: "8px" } }, h("div", { text: L.searchErr }));
  if (!L.q.trim()) return h("div", { class: "small dim", style: { marginTop: "6px" }, text: "Results come straight from Yahoo Finance; pick the listing (exchange) you want screened." });
  if (!L.results.length) return L.searching ? null : h("div", { class: "small dim", style: { marginTop: "6px" }, text: "No listing found." });
  const have = (k, sym) => L.rows[k].some((r) => !L.removed[k].has(r._id) && [r.yahoo_symbol, r.symbol].some((x) => String(x || "").toUpperCase() === sym.toUpperCase()));
  return h("table", { class: "data dense", style: { marginTop: "8px" } },
    h("thead", {}, h("tr", {}, ["Symbol", "Name", "Exchange", "Type", "Sector", ""].map((x) => h("th", { text: x })))),
    h("tbody", {}, L.results.map((x) => h("tr", {},
      h("td", {}, h("span", { class: "sym", text: x.symbol })), h("td", { class: "name", text: x.name, title: x.name }),
      h("td", { text: x.exchange }), h("td", { text: x.type }), h("td", { class: "dim", text: x.sector || "–" }),
      h("td", { class: "actions" },
        have("watchlist", x.symbol) ? h("span", { class: "chip", text: "on watchlist" })
          : h("button", { class: "btn", onclick: () => addFromSearch("watchlist", x) }, icon('<path d="M10 5v10M5 10h10"/>'), "Watchlist"),
        have("holdings", x.symbol) ? h("span", { class: "chip", text: "in holdings" })
          : h("button", { class: "btn", onclick: () => addFromSearch("holdings", x) }, icon('<path d="M10 5v10M5 10h10"/>'), "Holding"))))));
}
function addFromSearch(kind, x) {
  const row = kind === "watchlist"
    ? { symbol: x.symbol, yahoo_symbol: x.symbol, name: x.name, isin: "", lists: "" }
    : { isin: "", symbol: x.symbol, yahoo_symbol: x.symbol, name: x.name, shares: null, avg_cost: null, value_eur: null };
  const r = { ...row, _id: ++L.seq, _orig: null };
  L.rows[kind].unshift(r);
  L.tab = kind; L.review = null; L.reviewed = null; L.notice = null;
  render();
  if (kind === "holdings") setTimeout(() => document.querySelector(`tr[data-row="${r._id}"] input[data-f="shares"]`)?.focus(), 0);
}

function listTableCard(kind, editable) {
  const cols = LIST_COLS[kind];
  const filter = h("input", { type: "search", placeholder: "Filter", value: L.filter, "aria-label": "Filter rows", style: { width: "180px" } });
  filter.addEventListener("input", (e) => { L.filter = e.target.value; const b = $("list-body"); if (b) fill(b, listRows(kind, editable)); });
  const total = L.rows[kind].length - L.removed[kind].size;
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h3", { text: kind === "watchlist" ? "Watchlist" : "Holdings" }),
      h("span", { class: "hint", text: total + (kind === "watchlist" ? " companies" : " positions") + " · " + (L.data[kind]?.path || "") }),
      h("div", { class: "right" }, filter, editable ? h("button", { class: "btn ghost", onclick: () => addBlank(kind) }, icon('<path d="M10 5v10M5 10h10"/>'), "Empty row") : null)),
    h("div", { class: "card-body flush" }, h("div", { class: "table-wrap" }, h("table", { class: "data dense edit" },
      h("thead", {}, h("tr", {}, cols.map(([, label, type, w]) => h("th", { class: type === "num" ? "num" : "", style: { minWidth: w }, text: label })),
        kind === "holdings" ? h("th", { class: "num", text: "Cost €" }) : null, h("th", { text: "Latest run" }), h("th", { text: "" }))),
      h("tbody", { id: "list-body" }, listRows(kind, editable))))),
    kind === "holdings" ? h("div", { class: "card-foot", text: "Value € left empty is computed by each run as shares × today's price. Changing shares clears an imported value." }) : null);
}
function addBlank(kind) {
  const blank = Object.fromEntries(LIST_FIELDS[kind].map((f) => [f, ["shares", "avg_cost", "value_eur"].includes(f) ? null : ""]));
  L.rows[kind].unshift({ ...blank, _id: ++L.seq, _orig: null });
  L.review = null; L.reviewed = null; render();
  setTimeout(() => document.querySelector("#list-body tr input")?.focus(), 0);
}
function listRows(kind, editable) {
  const f = L.filter.trim().toLowerCase();
  const rows = L.rows[kind].filter((r) => !f || LIST_FIELDS[kind].some((k) => String(r[k] ?? "").toLowerCase().includes(f)));
  if (!rows.length) return h("tr", {}, h("td", { colspan: "9", class: "empty", text: f ? "No row matches the filter." : "Empty: add companies with the search above." }));
  return rows.map((r) => {
    const removed = L.removed[kind].has(r._id);
    const tr = h("tr", { "data-row": String(r._id), class: rowState(kind, r, removed) });
    for (const [field, , type] of LIST_COLS[kind]) {
      const val = r[field] ?? "";
      const dp = { shares: 6, avg_cost: 4, value_eur: 2 }[field] ?? 6;  // display only: unedited values are saved exactly
      const inp = h("input", { type: type === "num" ? "number" : "text", value: type === "num" && val !== "" && val !== null ? String(+(+val).toFixed(dp)) : String(val),
        "data-f": field, disabled: !editable || removed, step: "any", min: type === "num" ? "0" : null, "aria-label": field + " " + (listKey(kind, r) || "new row"),
        placeholder: field === "value_eur" ? "auto" : field === "yahoo_symbol" ? (latestFor(kind, r)?.symbol || "") : "" });
      inp.addEventListener("input", (e) => {
        const v = e.target.value;
        r[field] = type === "num" ? (v === "" ? null : Number(v)) : v;
        if (kind === "holdings" && field === "shares" && ok(r.value_eur)) {  // an imported value no longer matches
          r.value_eur = null; const vi = tr.querySelector('input[data-f="value_eur"]'); if (vi) vi.value = "";
        }
        if (kind === "holdings") { const c = tr.querySelector(".cost-cell"); if (c) c.textContent = costText(r); }
        tr.className = rowState(kind, r, false); L.review = null; L.reviewed = null; paintSaveBar(kind);
      });
      tr.appendChild(h("td", { class: type === "num" ? "num" : "" }, inp));
    }
    if (kind === "holdings") tr.appendChild(h("td", { class: "num cost-cell", text: costText(r) }));
    const latest = latestFor(kind, r);
    tr.appendChild(h("td", {}, latest ? h("span", { class: "latest" }, statusEl(latest.status), ok(latest.entry_score) ? h("span", { class: "num", text: fmtScore(latest.entry_score) }) : null)
      : h("span", { class: "dim small", text: !r._orig ? "new" : isSkipped(r) ? "skipped (fund / ETF)" : "not in the latest run" })));
    tr.appendChild(h("td", { class: "actions" }, editable ? h("button", { class: "btn ghost", title: removed ? "Keep" : "Remove", "aria-label": (removed ? "Keep " : "Remove ") + (listKey(kind, r) || "row"),
      onclick: () => { if (!r._orig) L.rows[kind] = L.rows[kind].filter((x) => x._id !== r._id); else if (removed) L.removed[kind].delete(r._id); else L.removed[kind].add(r._id); L.review = null; L.reviewed = null; render(); } },
      icon(removed ? '<path d="M4 10l4 4 8-8"/>' : '<path d="M5 5l10 10M15 5L5 15"/>')) : null));
    return tr;
  });
}
const isSkipped = (r) => { const res = (L.data && L.data.resolved) || {}; return [r.isin, r.symbol, r.yahoo_symbol].some((k) => k && String(res[k] || "").toUpperCase() === "SKIP"); };
const costText = (r) => ok(r.shares) && ok(r.avg_cost) ? fmtEur(num(r.shares) * num(r.avg_cost)) : "–";
const rowState = (kind, r, removed) => removed ? "removed" : !r._orig ? "added" : r._orig !== snapshot(kind, r) ? "changed" : "";

function listSaveBar(kind) {
  return h("div", { id: "list-savebar" }, saveBarBody(kind));
}
function paintSaveBar(kind) { const el = $("list-savebar"); if (el) fill(el, saveBarBody(kind)); }
function saveBarBody(kind) {
  const d = listDirty(kind), rv = L.review, body = [];
  if (rv) {
    if (rv.errors && rv.errors.length) body.push(h("div", { class: "banner err" }, h("div", {}, h("div", { text: "Not saved:" }), h("ul", {}, rv.errors.map((x) => h("li", { text: x }))))));
    if (rv.ok) {
      const parts = [["added", rv.diff.added], ["removed", rv.diff.removed], ["changed", rv.diff.changed]].filter(([, v]) => v.length);
      body.push(parts.length ? h("ul", { class: "diff" }, parts.map(([k, v]) => h("li", {}, h("span", { class: "dim", text: k + ": " }), v.slice(0, 30).join(", ") + (v.length > 30 ? " … +" + (v.length - 30) : ""))))
        : h("div", { class: "dim", text: "No differences to the saved file." }));
    }
  }
  return card("Save " + (kind === "watchlist" ? "watchlist" : "holdings"), d ? d + " unsaved change" + (d === 1 ? "" : "s") : "no unsaved changes", h("div", {}, body,
    h("div", { class: "editbar" },
      h("button", { class: "btn", disabled: L.busy || !d, onclick: () => reviewList(kind) }, "Review changes"),
      h("button", { class: "btn primary", disabled: L.busy || !(rv && rv.ok && Object.values(rv.diff).some((v) => v.length)), onclick: () => saveList(kind) }, "Save"),
      h("button", { class: "btn ghost", disabled: L.busy || !d, onclick: async () => { await loadLists(); L.notice = null; render(); } }, "Discard"),
      L.busy ? h("span", { class: "dim small", text: "working…" }) : null)),
  { foot: "Saving keeps a dated copy of the previous file under data/input-history/. New names are scored by the next run." });
}
async function reviewList(kind) {
  const body = { kind, rows: listPayload(kind), base_sha: L.data[kind].sha };
  L.busy = true; paintSaveBar(kind);
  L.review = await postJSON("api/inputs", { ...body, dry_run: true });
  L.reviewed = JSON.stringify(body); L.busy = false; paintSaveBar(kind);
}
async function saveList(kind) {
  const now = JSON.stringify({ kind, rows: listPayload(kind), base_sha: L.data[kind].sha });
  if (now !== L.reviewed) { await reviewList(kind); L.notice = { kind: "warn", text: "The rows changed after the review, so they were reviewed again. Check the list, then save." }; return render(); }
  L.busy = true; paintSaveBar(kind);
  const res = await postJSON("api/inputs", JSON.parse(L.reviewed));
  L.busy = false;
  if (res.ok && res.written) {
    await loadLists();
    L.notice = { kind: "ok", text: "Saved " + (kind === "watchlist" ? "the watchlist" : "the holdings") + ". Run the screener to score the changes (or wait for the weekly run)." };
  } else {
    L.review = res;
    if (res.conflict) L.notice = { kind: "warn", text: "The file was changed elsewhere since this page loaded it. Discard, then redo the edit." };
  }
  render();
}

// ---------- run the screener from the dashboard
function jobCard() {
  const j = L.job || {};
  const state = j.running ? "running since " + (j.started || "").replace("T", " ") : j.code === 0 ? "finished " + (j.finished || "").replace("T", " ") : "failed (exit " + j.code + ")";
  return h("section", { class: "card", id: "job-card" }, h("div", { class: "card-head" }, h("h3", { text: "Screener run" }),
    h("span", { class: "chip " + (j.running ? "" : j.code === 0 ? "ok" : "err"), text: state })),
    h("div", { class: "card-body" }, h("pre", { class: "log", text: (j.log || []).slice(-12).join("\n") || "Starting…" })));
}
async function startRun() {
  const res = await postJSON("api/rerun", {});
  if (res.status === 409 && res.running) L.notice = { kind: "warn", text: "A run is already in progress." };
  else if (res.errors && !res.running) { L.notice = { kind: "warn", text: res.errors.join("; ") }; return render(); }
  L.job = res; render(); pollRun();
}
function pollRun() {
  clearTimeout(L.poll);
  L.poll = setTimeout(async () => {
    try { L.job = await getJSON("api/rerun"); } catch (e) { return; }
    const card = $("job-card");
    if (card && S.view === "lists") card.replaceWith(jobCard());
    if (L.job.running) return pollRun();
    if (L.job.code === 0) {
      S.run = null; S.prev = "";  // jump to the run that just finished
      L.notice = { kind: "ok", text: "Run finished: the dashboard now shows it." }; await reloadAll(); await loadLists();
    }
    else L.notice = { kind: "warn", text: "The run failed; the log below shows why." };
    if (S.view === "lists") render();
  }, 1500);
}

// ---------------------------------------------------------------- Method (definitions + parameter editor)
const RATE_WARN_DAYS = 90, RATE_ERR_DAYS = 180;
const STRUCTURAL = [  // shown, never edited here: a change is a model change (config edit + golden-file update)
  ["years", "Horizon", (v) => (v ?? "–") + " years"],
  ["min_rate_over_terminal_growth", "Rates at least", (v) => "terminal g + " + fmtPct(v ?? 0.02, 1)],
  ["return_smoothing", "Return on capital", (v) => v ? "lower of current and 3-year average" : "current"],
  ["insurer_smooth_earnings", "Insurer earnings", (v) => v ? "lower of TTM and 3-year average" : "TTM"],
  ["cyclical_adverse_margin", "Cyclical adverse margin", (v) => v === "min" ? "trough margin of the window" : "median margin"],
  ["cyclical_adverse_growth_at_terminal", "Cyclical adverse growth", (v) => v ? "never above terminal growth" : "as other names"],
];

// mirror of dashboard/data.py derived_rates (itself a mirror of screener/metrics.py)
function derivedRates(r, v) {
  const rf = num(r.rf), g = num(r.g), crp = num(r.crp) || 0, erp = num(v.equity_risk_premium);
  if (![rf, g, erp].every(Number.isFinite)) return { hurdle: NaN, floor: NaN, minBinds: false };
  const least = g + (num(v.min_rate_over_terminal_growth) || 0.02);
  let hurdle = Math.max(rf + erp + crp, least);
  const minBinds = hurdle < (num(v.min_hurdle) || 0);
  if (minBinds) hurdle = num(v.min_hurdle);
  const floor = Math.min(Math.max(rf + (num(v.adverse_return_spread) || 0) + crp, least), hurdle);
  return { hurdle, floor, minBinds };
}
const pctIn = (x) => Number.isFinite(num(x)) ? String(Number((num(x) * 100).toFixed(3))) : "";
const pctOut = (str) => str === "" ? NaN : Number(str) / 100;
const ageOf = (asOf) => asOf ? daysBetween(asOf, S.today || new Date().toISOString().slice(0, 10)) : null;
function ageChip(asOf) {
  const a = ageOf(asOf);
  if (a === null) return h("span", { class: "chip err", text: "no date" });
  const cls = a > RATE_ERR_DAYS ? "err" : a > RATE_WARN_DAYS ? "warn" : "ok";
  return h("span", { class: "chip " + cls, text: a <= 0 ? "today" : a + " d" });
}
const universeCcys = () => new Set(S.rows.map((r) => r.currency).filter(Boolean));

function editState() {
  const c = S.config;
  if (!S.edit || S.edit.stamp !== c.modified) {
    const valuation = {};
    for (const k of Object.keys(c.editable_keys || {})) if (c.valuation[k] !== undefined) valuation[k] = c.valuation[k];
    const rates = {};
    for (const r of c.rates) rates[r.currency] = { rf: r.rf, g: r.g, crp: r.crp || 0, as_of: r.as_of || "" };
    S.edit = { stamp: c.modified, valuation, rates, removed: new Set(), added: [], review: null, busy: false, notice: null };
  }
  return S.edit;
}
function editPayload(e) {
  const c = S.config, valuation = {}, rates = {};
  for (const [k, v] of Object.entries(e.valuation)) if (v !== c.valuation[k]) valuation[k] = v;
  const orig = Object.fromEntries(c.rates.map((r) => [r.currency, r]));
  for (const [ccy, r] of Object.entries(e.rates)) {
    if (e.removed.has(ccy)) { if (orig[ccy]) rates[ccy] = null; continue; }
    const o = orig[ccy];
    const entry = { rf: r.rf, g: r.g };
    if (num(r.crp) > 0) entry.crp = r.crp;
    if (r.as_of) entry.as_of = r.as_of;
    if (!o || o.rf !== r.rf || o.g !== r.g || (o.crp || 0) !== (num(r.crp) || 0) || (o.as_of || "") !== (r.as_of || "")) rates[ccy] = entry;
  }
  for (const a of e.added) {
    const ccy = String(a.ccy || "").trim().toUpperCase();
    if (!ccy && !Number.isFinite(num(a.rf)) && !Number.isFinite(num(a.g))) continue;  // untouched blank row
    const entry = { rf: a.rf, g: a.g };
    if (num(a.crp) > 0) entry.crp = a.crp;
    if (a.as_of) entry.as_of = a.as_of;
    rates[ccy || "???"] = entry;
  }
  return { valuation, rates };
}
const postConfig = (body) => postJSON("api/config", body);
async function reviewEdits() {
  const e = editState();
  e.busy = true; e.notice = null; render();
  const body = { ...editPayload(e), base_sha: S.config.sha };
  e.review = await postConfig({ ...body, dry_run: true });
  e.reviewed = JSON.stringify(body);  // Save sends exactly this, never inputs typed after the review
  e.busy = false; render();
}
async function saveEdits() {
  const e = editState();
  if (JSON.stringify({ ...editPayload(e), base_sha: S.config.sha }) !== e.reviewed) {
    await reviewEdits();
    e.notice = { kind: "warn", text: "Inputs changed after the review, so they were reviewed again. Check the list, then save." };
    return render();
  }
  e.busy = true; render();
  const res = await postConfig(JSON.parse(e.reviewed));
  if (res.conflict) {
    e.busy = false; e.review = res;
    e.notice = { kind: "warn", text: "config.toml was changed elsewhere since this page loaded it. Reload the page and redo the edit." };
  } else if (res.ok && res.written) {
    S.config = await getJSON("api/config");
    S.edit = null;
    editState().notice = { kind: "ok", text: "Saved to config.toml (previous version kept as config.toml.bak and under data/config-history/). " +
      "Scores and statuses change only after the screener is rerun." };
  } else {
    e.busy = false; e.review = res;
  }
  render();
}
function discardEdits() { S.edit = null; render(); }

function viewMethod() {
  const c = S.config || {};
  const out = [pageHeader("Method")];
  if (c.modified && S.data.written_at && c.modified > S.data.written_at)
    out.push(h("div", { class: "banner warn" }, icon('<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'),
      h("div", { text: "config.toml was modified (" + c.modified.replace("T", " ") + ") after this run was written (" + S.data.written_at.replace("T", " ") + "). The parameters below are the current file, not necessarily the ones this run used. Per-name hurdles in the tables are the run's own." })));
  const defs = [
    ["Purpose", "Triage: decides which companies deserve a full valuation. It is not a buy list."],
    ["Max entry price", "The reasonable-case equity value per share, in the quote currency."],
    ["Entry score", "100 × (1 − price / max entry price), floored at −100 (price at least twice the ceiling, where the exact gap says nothing). +20: the price is 20% below the ceiling. Negative: above it. A signed price gap, not a return forecast or a probability."],
    ["To ceiling", "ceiling / price − 1: the price move that would put the name exactly on its max entry price. −20% means the price must fall 20%."],
    ["Funded growth", "Earnings grow at g, fading to the currency's terminal growth. The owner receives earnings × (1 − g / R), R = return on new capital (ROIC excluding goodwill, or ROE; lower of current and 3-year average, capped). Growth adds nothing when R equals the discount rate."],
    ["Reasonable case", "g = mean of 3-year revenue CAGR and analyst growth (clipped, capped at R); R held; discounted at the hurdle = currency 10-year yield + equity risk premium + country risk, at least terminal growth + 2 points and at least the minimum hurdle."],
    ["Adverse case", "Lower growth (see Parameters; cyclicals on the trough margin and never above terminal growth); R fades to the hurdle; discounted at the floor = currency 10-year yield + spread + country risk. Shown as the adverse score (same formula against the adverse value), a downside check that does not change the max entry price or the status."],
    ["Status", "Candidate: score ≥ 0 and no critical flag. Data flag: scored but stale statements, no market cap, a yield above 25% or a bank with thin capital. Not rankable: an essential input is missing or the ceiling is at or below zero. Not screened: loss-making, holding company or fund."],
    ["Implied return / growth", "Implied return: discount rate at which the reasonable-case funded path equals EV (market cap for financials); the base-currency version subtracts the 10-year yield differential so currencies compare on one number. Implied growth: starting growth the price needs at the hurdle, limited to growth the business can fund."],
    ["Peer percentiles", "Value / quality / growth / balance ranks within operating+cyclical or financials. Context only: they are not gates and shift when the watchlist changes."],
  ];
  if (!c.available) {
    out.push(h("div", { class: "grid halves" }, card("Definitions", "", h("dl", { class: "prose" }, defs.map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]))),
      card("Parameters", "config.toml not found", h("div", { class: "empty", text: "No config.toml under " + (S.root || "") }))));
    return out;
  }
  const e = editState();
  const editable = !!c.editable;
  if (e.notice) out.push(h("div", { class: "banner " + (e.notice.kind === "ok" ? "ok" : "warn") },
    icon(e.notice.kind === "ok" ? '<path d="M4 10l4 4 8-8"/>' : '<path d="M10 3l7.5 13h-15z"/><path d="M10 8v4M10 14.2v.3"/>'), h("div", { text: e.notice.text })));
  if (!editable) out.push(h("div", { class: "banner" }, icon('<rect x="4" y="9" width="12" height="8" rx="1.5"/><path d="M7 9V6a3 3 0 0 1 6 0v3"/>'),
    h("div", { text: c.read_only ? "Parameters are read-only: the dashboard was started with --read-only." : "Parameters can be edited only from the machine running the dashboard (or start it with --allow-remote-edit)." })));
  out.push(h("div", { class: "grid halves" },
    h("div", { class: "grid", style: { alignContent: "start" } },
      card("Parameters", "config.toml · modified " + (c.modified || "").replace("T", " "), paramForm(e, editable), {}),
      card("Structural", "", h("dl", { class: "kv" }, STRUCTURAL.map(([k, label, f]) => [h("dt", { text: label }), h("dd", { text: f(c.valuation[k]) })])),
        { foot: "Model settings: change them in config.toml, not here." }),
      card("Definitions", "", h("dl", { class: "prose" }, defs.map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])))),
    h("div", { class: "grid", style: { alignContent: "start" } },
      card("Rates by currency", "", ratesEditor(e, editable),
        { flush: true, foot: "Rate age turns amber after " + RATE_WARN_DAYS + " days and red after " + RATE_ERR_DAYS + "." }),
      editable ? editBar(e) : null)));
  return out;
}
function paramForm(e, editable) {
  const spec = S.config.editable_keys || {};
  const ccys = Object.keys(e.rates).filter((k) => !e.removed.has(k)).sort();
  const field = (k) => {
    const sp = spec[k];
    const v = e.valuation[k];
    let input;
    if (!editable) input = h("span", { class: "num", text: sp.type === "pct" ? fmtPct(v, 2) : sp.type === "int" ? String(v) : String(v) });
    else if (sp.type === "pct") input = h("input", { type: "number", step: "0.05", min: sp.min * 100, max: sp.max * 100, value: pctIn(v), "aria-label": sp.label,
      oninput: (ev) => { e.valuation[k] = pctOut(ev.target.value); refreshDerived(e); } });
    else if (sp.type === "int") input = h("input", { type: "number", step: "1", min: sp.min, max: sp.max, value: v ?? "", "aria-label": sp.label,
      oninput: (ev) => { e.valuation[k] = ev.target.value === "" ? NaN : Number(ev.target.value); } });
    else if (sp.type === "choice") input = h("select", { "aria-label": sp.label, onchange: (ev) => { e.valuation[k] = ev.target.value; } },
      sp.choices.map((o) => h("option", { value: o, text: o, selected: v === o })));
    else if (sp.type === "currency") input = h("select", { "aria-label": sp.label, onchange: (ev) => { e.valuation[k] = ev.target.value; } },
      ccys.map((o) => h("option", { value: o, text: o, selected: v === o })));
    return h("div", { class: "field" }, h("label", {}, h("span", { class: "lbl", text: sp.label }), h("span", { class: "unit", text: sp.type === "pct" ? "%" : "" }), input),
      sp.help ? h("div", { class: "help", text: sp.help }) : null);
  };
  return h("div", { class: "form" }, Object.keys(spec).filter((k) => e.valuation[k] !== undefined).map(field));
}
function refreshDerived(e) {
  const v = e.valuation;
  for (const [ccy, r] of Object.entries(e.rates)) setDerivedCells(ccy, r, v);
  e.added.forEach((a, i) => setDerivedCells("new" + i, a, v));
}
function setDerivedCells(id, r, v) {
  const d = derivedRates(r, v);
  const hc = $("rate-h-" + id), fc = $("rate-f-" + id);
  if (hc) hc.textContent = fmtPct(d.hurdle) + (d.minBinds ? " (min)" : "");
  if (fc) fc.textContent = fmtPct(d.floor);
}
function ratesEditor(e, editable) {
  const inUse = universeCcys(), base = e.valuation.base_currency || baseCcy();
  const counts = new Map(); for (const r of S.rows) counts.set(r.currency, (counts.get(r.currency) || 0) + 1);
  const pct = (obj, k, id, label, extra = {}) => editable
    ? h("input", { type: "number", step: "0.05", value: pctIn(obj[k]), "aria-label": label + " " + id, ...extra, oninput: (ev) => { obj[k] = pctOut(ev.target.value); setDerivedCells(id, obj, e.valuation); } })
    : h("span", { class: "num", text: fmtPct(obj[k], 2) });
  const dateIn = (obj, id) => editable
    ? h("input", { type: "date", value: obj.as_of || "", "aria-label": "as of " + id, oninput: (ev) => { obj.as_of = ev.target.value; const chip = $("rate-age-" + id); if (chip) chip.replaceWith(Object.assign(ageChip(obj.as_of), { id: "rate-age-" + id })); } })
    : h("span", { text: obj.as_of || "–" });
  const row = (id, ccyCell, r, removable, onRemove, removed) => {
    const d = derivedRates(r, e.valuation);
    return h("tr", { class: removed ? "removed" : "" },
      h("td", {}, ccyCell), h("td", { class: "num", text: String(counts.get(id) || "") }),
      h("td", { class: "num" }, pct(r, "rf", id, "10y yield")), h("td", { class: "num" }, pct(r, "g", id, "terminal growth")),
      h("td", { class: "num" }, pct(r, "crp", id, "country risk", { min: 0 })),
      h("td", { class: "num", id: "rate-h-" + id, text: fmtPct(d.hurdle) + (d.minBinds ? " (min)" : "") }),
      h("td", { class: "num", id: "rate-f-" + id, text: fmtPct(d.floor) }),
      h("td", {}, dateIn(r, id)), h("td", {}, Object.assign(ageChip(r.as_of), { id: "rate-age-" + id })),
      editable ? h("td", {}, removable ? h("button", { class: "btn ghost", title: removed ? "Keep" : "Remove", "aria-label": (removed ? "Keep " : "Remove ") + id, onclick: onRemove },
        icon(removed ? '<path d="M4 10l4 4 8-8"/>' : '<path d="M5 5l10 10M15 5L5 15"/>')) : h("span", { class: "dim small", text: inUse.has(id) ? "in use" : "base" })) : null);
  };
  const rows = Object.keys(e.rates).sort().map((ccy) => row(ccy, h("span", { class: "mono", text: ccy }), e.rates[ccy], !inUse.has(ccy) && ccy !== base,
    () => { if (e.removed.has(ccy)) e.removed.delete(ccy); else e.removed.add(ccy); render(); }, e.removed.has(ccy)));
  const added = e.added.map((a, i) => row("new" + i,
    h("input", { type: "text", maxlength: 3, placeholder: "CCY", value: a.ccy || "", "aria-label": "new currency code", class: "mono ccy", oninput: (ev) => { a.ccy = ev.target.value.toUpperCase(); } }),
    a, true, () => { e.added.splice(i, 1); render(); }, false));
  const head = ["Ccy", "Names", "10y yield %", "Terminal g %", "Country risk %", "Hurdle", "Adverse floor", "As of", "Age", editable ? "" : null].filter((x) => x !== null);
  return h("div", { class: "table-wrap" }, h("table", { class: "data dense rates" },
    h("thead", {}, h("tr", {}, head.map((t, i) => h("th", { class: i >= 2 && i <= 6 ? "num" : "", text: t })))),
    h("tbody", {}, rows, added,
      editable ? h("tr", {}, h("td", { colspan: String(head.length) }, h("button", { class: "btn ghost", onclick: () => { e.added.push({ ccy: "", rf: NaN, g: NaN, crp: 0, as_of: S.today || "" }); render(); } },
        icon('<path d="M10 5v10M5 10h10"/>'), "Add currency"))) : null)));
}
function editBar(e) {
  const rv = e.review;
  const body = [];
  if (rv) {
    if (rv.errors && rv.errors.length) body.push(h("div", { class: "banner err" }, icon('<circle cx="10" cy="10" r="7"/><path d="M10 6v5M10 13.5v.5"/>'), h("div", {}, h("div", { text: "Not saved:" }), h("ul", {}, rv.errors.map((x) => h("li", { text: x }))))));
    if (rv.changes && rv.changes.length) body.push(h("ul", { class: "diff" }, rv.changes.map((ch) => h("li", {},
      h("span", { class: "mono", text: ch.section + " · " + ch.key }), " ", h("span", { class: "dim", text: ch.kind }), ": ",
      h("span", { text: describeChange(ch) })))));
    if (rv.ok && !(rv.changes || []).length) body.push(h("div", { class: "dim", text: "No differences to the current config.toml." }));
  }
  return card("Save changes", rv ? (rv.changes || []).length + " change" + ((rv.changes || []).length === 1 ? "" : "s") + " reviewed" : "", h("div", {},
    body,
    h("div", { class: "editbar" },
      h("button", { class: "btn", disabled: e.busy, onclick: reviewEdits }, "Review changes"),
      h("button", { class: "btn primary", disabled: !(rv && rv.ok && (rv.changes || []).length) || e.busy, onclick: saveEdits }, "Save to config.toml"),
      h("button", { class: "btn ghost", disabled: e.busy, onclick: discardEdits }, "Discard"),
      e.busy ? h("span", { class: "dim small", text: "working…" }) : null)),
    { foot: "Saving keeps a backup. Scores change after the next screener run." });
}
function describeChange(ch) {
  const f = (v, k) => typeof v === "number" && (k === undefined || !["stale_after_days"].includes(k)) && Math.abs(v) < 5 ? fmtPct(v, 2) : String(v);
  if (ch.section === "valuation") return f(ch.old, ch.key) + " → " + f(ch.new, ch.key);
  const r = (x) => x ? ["rf", "g", "crp", "as_of"].filter((k) => x[k] !== undefined).map((k) => k + " " + (k === "as_of" ? x[k] : fmtPct(x[k], 2))).join(", ") : "–";
  return r(ch.old) + " → " + r(ch.new);
}

// ---------------------------------------------------------------- Inspector
async function renderInspector() {
  const el = $("inspector");
  const r = S.sel && S.bySym ? S.bySym.get(S.sel) : null;
  if (!r) { el.classList.add("closed"); el.replaceChildren(); return; }
  el.classList.remove("closed");
  const ccy = r.quote_ccy || "";
  const x = num(r.entry_score), hr = headroom(r);
  const scored = SCORED.includes(r.status) && Number.isFinite(x);
  const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter((p) => p && p[1] !== null).map(([k, v, sub]) => [h("dt", { text: k }), h("dd", {}, v, sub ? h("span", { class: "sub", text: sub }) : null)]));
  const has = (k) => r[k] !== null && r[k] !== undefined && r[k] !== "";
  const pct = (k, label) => has(k) ? [label, fmtPct(r[k])] : null;
  const ratio = (k, label, d = 2) => has(k) ? [label, fmtRatio(r[k], d)] : null;
  const spread = num(r.implied_return) - num(r.hurdle);
  const flags = splitFlags(r);
  const yahoo = r.symbol ? "https://finance.yahoo.com/quote/" + encodeURIComponent(r.symbol) : null;

  fill(el,
    h("div", { class: "insp-head" },
      h("div", { class: "row1" }, h("span", { class: "sym", text: r.symbol }), r.owned ? h("span", { class: "chip accent", text: "owned" + (ok(r.position_eur) ? " · " + fmtEur(r.position_eur) : "") }) : null,
        h("button", { class: "btn ghost close", title: "Close (Esc)", "aria-label": "Close", onclick: () => select(null) }, icon('<path d="M5 5l10 10M15 5L5 15"/>'))),
      h("div", { class: "name", text: r.name || "" }),
      h("div", { class: "chips" }, statusEl(r.status), r.class ? h("span", { class: "chip", text: r.class + (r.fin_type ? " · " + r.fin_type : "") }) : null,
        r.sector ? h("span", { class: "chip", text: r.sector }) : null)),
    h("div", { class: "insp-section" },
      h("h4", { text: "Entry score" }),
      scored ? h("div", { class: "score-line" }, h("span", { class: "big", text: fmtScore(x) }),
        h("span", { class: "expl", text: hr >= 0 ? "price is " + fmtPct(1 - num(r.price) / num(r.ceiling_px), 0) + " below the max entry price" : "price must fall " + fmtPct(-hr, 0) + " to reach the max entry price" }))
        : h("div", { class: "dim", text: r.not_rankable ? "Not rankable: " + r.not_rankable : r.status === "not screened" ? "Not screened (" + (r.class || "") + "): no ceiling is computed for this class." : "No score." }),
      scored && ok(r.adverse_score) ? h("div", { class: "small dim", text: "Adverse score " + fmtScore(r.adverse_score) }) : null,
      scored && ok(r.cost_score) ? h("div", { class: "small dim", text: "Score at your avg cost (" + fmtPx(r.cost_px) + ") " + fmtScore(r.cost_score) }) : null,
      scored ? ladder(r) : null),
    scored || has("price") ? h("div", { class: "insp-section" }, h("h4", { text: "Valuation (" + (ccy || "quote currency") + " per share)" }), kv([
      ["Price", fmtPx(r.price)], ["Max entry price", fmtPx(r.ceiling_px)], ["Reasonable value", fmtPx(r.reasonable_px)], ["Adverse value", fmtPx(r.adverse_px)],
      has("implied_return") ? ["Implied return", fmtPct(r.implied_return), Number.isFinite(spread) ? fmtPP(spread) + " vs hurdle" : null] : null,
      has("implied_return_base") ? ["Implied return in " + baseCcy() + " terms", fmtPct(r.implied_return_base)] : null,
      pct("hurdle", "Hurdle (reasonable)"), pct("floor_rate", "Adverse floor rate"),
      has("est_growth") ? ["Estimated growth", fmtPct(r.est_growth)] : null,
      has("est_growth") ? ["Implied growth", has("implied_growth") ? fmtPct(r.implied_growth) : "no solution", null] : null,
      pct("adverse_growth", "Adverse growth"), pct("terminal_growth", "Terminal growth"),
      pct("return_on_capital", "Return on new capital (R)"), pct("reinvest_rate", "Reinvestment rate (g / R)")]),
      has("caps_hit") ? h("div", { class: "small dim", style: { marginTop: "6px" }, text: "Caps hit: " + r.caps_hit }) : null) : null,
    h("div", { class: "insp-section", id: "insp-history" }, h("h4", { text: "Price and max entry across runs" }), h("div", { class: "dim small", text: "Loading…" })),
    h("div", { class: "insp-section" }, h("h4", { text: "Fundamentals" }), kv([
      pct("nopat_yield", "NOPAT / EV"), pct("fcf_yield", "FCF − SBC / EV"), pct("ebit_yield", "EBIT / EV"), pct("earnings_yield", "Earnings yield"),
      ratio("book_yield", "Book / market cap"), pct("roic", "ROIC"), pct("roic_ex_goodwill", "ROIC ex goodwill"), pct("roe", "ROE"), pct("gross_profitability", "Gross profit / assets"),
      ratio("cash_conversion", "FCF / net income"), pct("rev_cagr_3y", "Revenue CAGR 3y"), pct("analyst_growth", "Analyst revenue growth"),
      ratio("net_debt_ebitda", "Net debt / EBITDA", 1), pct("share_cagr", "Share count CAGR"), ratio("evs", "EV / Sales", 1), ratio("evs_vs_hist", "EV/Sales vs 5y median"),
      pct("equity_to_assets", "Equity / assets"), has("mcap_eur_bn") ? ["Market cap", "€" + fmtNum(r.mcap_eur_bn, 1) + "bn"] : null,
      has("currency") ? ["Reporting currency", r.currency] : null, has("data_as_of") ? ["Data as of", r.data_as_of] : null])),
    has("composite") ? h("div", { class: "insp-section" }, h("h4", { text: "Peer percentiles" }),
      h("div", { style: { display: "grid", gap: "6px" } }, [["Composite", "composite"], ["Value", "score_value"], ["Quality", "score_quality"], ["Growth", "score_growth"], ["Balance", "score_balance"]]
        .map(([l, k]) => h("div", { class: "pctbar" }, h("span", { text: l }), h("span", { class: "track" }, h("span", { class: "bar", style: { width: (ok(r[k]) ? num(r[k]) : 0) + "%" } })), h("span", { class: "num", text: fmtNum(r[k], 0) }))))) : null,
    h("div", { class: "insp-section" }, h("h4", { text: "Data flags" }),
      flags.length ? h("ul", { class: "flags" }, flags.map((f) => h("li", { class: isCritical(f) ? "critical" : null, text: f + (isCritical(f) ? " (critical)" : "") })))
        : h("div", { class: "dim small", text: "No flags." })),
    h("div", { class: "insp-section" }, h("h4", { text: "Source" }), kv([
      has("lists") ? ["Lists", r.lists] : null, has("isin") ? ["ISIN", r.isin] : null, has("industry") ? ["Industry", r.industry] : null,
      has("country") ? ["Country", r.country] : null]),
    yahoo ? h("div", { style: { marginTop: "8px" } }, h("a", { href: yahoo, target: "_blank", rel: "noopener noreferrer", class: "small", text: "Open on Yahoo Finance ↗" })) : null));

  const sym = r.symbol;
  if (!S.hist[sym]) { try { S.hist[sym] = await getJSON("api/symbol?s=" + encodeURIComponent(sym)); } catch (e) { S.hist[sym] = { points: [] }; } }
  if (S.sel !== sym) return;
  const box = $("insp-history");
  if (box) box.replaceChildren(h("h4", { text: "Price and max entry across runs" }), historyChart(S.hist[sym].points, ccy));
}
function ladder(r) {
  const items = [["Max entry price", num(r.ceiling_px), "var(--pos)"]];
  // runs before 2026-10-10 evening took the lower of the two cases; show the reasonable value only where it differs
  if (ok(r.reasonable_px) && Math.abs(num(r.reasonable_px) / num(r.ceiling_px) - 1) > 1e-9) items.push(["Reasonable value", num(r.reasonable_px), "var(--neutral-mark)"]);
  items.push(["Adverse value", num(r.adverse_px), "var(--neutral-mark)"]);
  const p = num(r.price);
  const finite = [p, ...items.map((i) => i[1])].filter(Number.isFinite);
  const hi = Math.max(...finite) * 1.08;
  const W = 350, pad = { l: 104, r: 12, t: 6 }, rowH = 22, Hh = pad.t + items.length * rowH + 30;
  const X = (v) => pad.l + Math.min(v, hi) / hi * (W - pad.l - pad.r);
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Price compared with the case values" });
  const surface = cssVar("--bg-card");
  items.forEach(([label, v, color], i) => {
    const y = pad.t + i * rowH;
    svgEl.appendChild(s("text", { x: pad.l - 8, y: y + 14, "text-anchor": "end", class: i === 0 ? "label-strong" : null, text: label }));
    if (!Number.isFinite(v) && v !== Infinity) return;
    const w = X(v) - pad.l, inf = v === Infinity;
    svgEl.appendChild(s("path", { d: roundEndBar(pad.l, y + 5, Math.max(1, w), 10, 4), fill: color }));
    const txt = inf ? "∞" : fmtPx(v);
    const inside = !inf && w > 70;
    svgEl.appendChild(s("text", { x: inside ? X(v) - 6 : Math.min(X(v) + 6, W - 2), y: y + 14, "text-anchor": inside ? "end" : "start", style: inside ? { fill: surface } : null, class: "tick-label", text: txt }));
  });
  const px = X(p), yb = pad.t + items.length * rowH;
  svgEl.appendChild(s("line", { x1: px, x2: px, y1: pad.t - 2, y2: yb + 4, stroke: "var(--fg-main)", "stroke-width": 2 }));
  svgEl.appendChild(s("text", { x: Math.max(pad.l, Math.min(px, W - 60)), y: yb + 18, "text-anchor": "middle", class: "label-strong", text: "Price " + fmtPx(p) }));
  svgEl.appendChild(s("line", { class: "axis-line", x1: pad.l, x2: pad.l, y1: pad.t - 2, y2: yb + 2 }));
  return svgEl;
}
function roundEndBar(x, y, w, hgt, r) {
  r = Math.min(r, w, hgt / 2);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + hgt - r} Q${x + w},${y + hgt} ${x + w - r},${y + hgt} H${x} Z`;
}
function historyChart(points, ccy) {
  const pts = points.filter((p) => ok(p.price) || ok(p.ceiling_px));
  if (pts.length < 2) return h("div", { class: "dim small", text: pts.length ? "Only in one run so far; the history fills in week by week." : "No history." });
  const ccys = new Set(pts.map((p) => p.quote_ccy).filter(Boolean));
  if (ccys.size > 1) return h("div", { class: "dim small", text: "Quote currency changed between runs (" + [...ccys].join(", ") + "); values are not comparable." });
  const W = 350, Hh = 150, pad = { l: 48, r: 10, t: 10, b: 22 };
  const vals = pts.flatMap((p) => [num(p.price), num(p.ceiling_px)]).filter(Number.isFinite);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo *= 0.9; hi *= 1.1; }
  const step = niceStep((hi - lo) / 4); lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const X = (i) => pad.l + i / (pts.length - 1) * (W - pad.l - pad.r);
  const Y = (v) => Hh - pad.b - (v - lo) / (hi - lo) * (Hh - pad.t - pad.b);
  const svgEl = s("svg", { class: "chart", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "Price and max entry price per run" });
  for (let t = lo; t <= hi + 1e-9; t += step) {
    svgEl.appendChild(s("line", { class: "grid-line", x1: pad.l, x2: W - pad.r, y1: Y(t), y2: Y(t) }));
    svgEl.appendChild(s("text", { class: "tick-label", x: pad.l - 6, y: Y(t) + 4, "text-anchor": "end", text: fmtNum(t, t >= 100 ? 0 : 2) }));
  }
  pts.forEach((p, i) => { if (i === 0 || i === pts.length - 1) svgEl.appendChild(s("text", { class: "tick-label", x: X(i), y: Hh - 6, "text-anchor": i ? "end" : "start", text: runLabel(p.date) })); });
  const series = [["Price", "price", "var(--fg-main)"], ["Max entry", "ceiling_px", "var(--pos)"]];
  for (const [, k, color] of series) {
    const d = pts.map((p, i) => ok(p[k]) ? (i && ok(pts[i - 1][k]) ? "L" : "M") + X(i) + "," + Y(num(p[k])) : "").join(" ");
    svgEl.appendChild(s("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  const cross = s("line", { class: "axis-line", y1: pad.t, y2: Hh - pad.b, x1: -10, x2: -10 });
  svgEl.appendChild(cross);
  const hit = s("rect", { x: pad.l, y: pad.t, width: W - pad.l - pad.r, height: Hh - pad.t - pad.b, fill: "transparent" });
  hit.addEventListener("pointermove", (e) => {
    const b = svgEl.getBoundingClientRect(), vx = (e.clientX - b.left) / b.width * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round((vx - pad.l) / (W - pad.l - pad.r) * (pts.length - 1))));
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i));
    const p = pts[i];
    showTip(e, runLabel(p.date), (STATUS_LABEL[p.status] || p.status || "") + " · " + (ccy || ""), [
      { label: "Price", value: fmtPx(p.price), color: cssVar("--fg-main") }, { label: "Max entry", value: fmtPx(p.ceiling_px), color: cssVar("--pos") },
      { label: "Entry score", value: fmtScore(p.entry_score) }]);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("x1", -10); cross.setAttribute("x2", -10); hideTip(); });
  svgEl.appendChild(hit);
  return h("div", {}, h("div", { style: { marginBottom: "6px" } }, legend([["line", "var(--fg-main)", "Price"], ["line", "var(--pos)", "Max entry price"]])), svgEl);
}

init();
