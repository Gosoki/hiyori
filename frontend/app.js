"use strict";

// ---- language / i18n ------------------------------------------------------
let lang = localStorage.getItem("lang") || null;
let locale = "ja-JP";
let lastWeather = null;
let lastNews = null;
let lastHourly = null;
let cityId = localStorage.getItem("city") || null;
let cities = [];
let aiSrc = localStorage.getItem("aiSrc") || null;
let aiSources = [];

function t(key) {
  return (window.I18N[lang] && window.I18N[lang][key]) || window.I18N.ja[key] || key;
}

function applyI18n() {
  locale = t("_locale");
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  // re-render cached data so labels/locale update immediately
  if (lastWeather) renderWeather(lastWeather);
  if (lastHourly) renderHourly(lastHourly);
  if (lastNews) renderNews(lastNews);
}

function buildLangOptions() {
  const wrap = document.getElementById("lang-options");
  wrap.innerHTML = "";
  Object.keys(window.I18N).forEach((code) => {
    const b = document.createElement("button");
    b.textContent = window.I18N[code]._label;
    if (code === lang) b.classList.add("active");
    b.onclick = () => {
      lang = code;
      localStorage.setItem("lang", code);
      applyI18n();
      buildLangOptions();
    };
    wrap.appendChild(b);
  });
}

function buildCityOptions() {
  const wrap = document.getElementById("city-options");
  wrap.innerHTML = "";
  cities.forEach((c) => {
    const b = document.createElement("button");
    b.textContent = c.name;
    if (c.id === cityId) b.classList.add("active");
    b.onclick = () => {
      if (c.id === cityId) return;
      cityId = c.id;
      localStorage.setItem("city", c.id);
      buildCityOptions();
      loadWeather();      // switch immediately
      loadHourly();
    };
    wrap.appendChild(b);
  });
}

function buildAiOptions() {
  const wrap = document.getElementById("aisrc-options");
  wrap.innerHTML = "";
  aiSources.forEach((s) => {
    const b = document.createElement("button");
    b.textContent = s.name;
    if (s.id === aiSrc) b.classList.add("active");
    b.onclick = () => {
      if (s.id === aiSrc) return;
      aiSrc = s.id;
      localStorage.setItem("aiSrc", s.id);
      buildAiOptions();
      loadNews();       // switch immediately
    };
    wrap.appendChild(b);
  });
}

// ---- clock -----------------------------------------------------------------
function formatDate() {
  return new Intl.DateTimeFormat(locale, {
    month: "long", day: "numeric", weekday: "short",
  }).format(new Date());
}

function startClock() {
  const timeEl = document.getElementById("time");
  const tick = () => {
    timeEl.textContent = new Intl.DateTimeFormat(locale, {
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date());
    const dateEl = document.getElementById("today-date");   // date lives in the today card now
    if (dateEl) dateEl.textContent = formatDate();
  };
  tick();
  setInterval(tick, 5000);
}

// ---- weather ---------------------------------------------------------------
async function loadWeather() {
  try {
    const data = await (await fetch("/api/weather?city=" + encodeURIComponent(cityId || ""))).json();
    if (data && data.today) { lastWeather = data; renderWeather(data); }
  } catch (_) { /* keep last */ }
}

function renderWeather(data) {
  if (data.city) document.getElementById("city").textContent = data.city;
  const temp = (v) => (v === null || v === undefined ? "--" : v + "°");   // today card keeps the °
  const tempN = (v) => (v === null || v === undefined ? "--" : String(v)); // weekly: no ° (renders inconsistently across systems)

  const today = data.today || {};
  // JMA drops today's high/low later in the day — fall back to the met.no hourly.
  let tMax = today.tempMax, tMin = today.tempMin;
  if ((tMax == null || tMin == null) && Array.isArray(lastHourly) && lastHourly.length) {
    const hs = lastHourly.map((h) => h.temp).filter((v) => v !== null && v !== undefined);
    if (hs.length) {
      if (tMax == null) tMax = Math.max(...hs);
      if (tMin == null) tMin = Math.min(...hs);
    }
  }
  document.getElementById("today").innerHTML =
    `<div class="t-date" id="today-date">${formatDate()}</div>` +
    `<div class="today-main">` +
      `<div class="t-icon">${today.icon || "❓"}</div>` +
      `<div class="t-info">` +
        `<div class="t-text" lang="ja">${escapeHtml(today.text || "")}</div>` +
        `<div class="t-temps"><span class="hi">${temp(tMax)}</span> / ` +
        `<span class="lo">${temp(tMin)}</span></div>` +
        (today.pop !== null && today.pop !== undefined
          ? `<div class="t-pop">${t("pop")} ${today.pop}%</div>` : "") +
      `</div>` +
    `</div>`;

  const weekly = document.getElementById("weekly");
  weekly.innerHTML = "";
  (data.weekly || []).slice(0, 7).forEach((d) => {
    const info = weekdayInfo(d.date, d.weekday, d.md);
    const el = document.createElement("div");
    el.className = "day" + (info.dow === 6 ? " is-sat" : info.dow === 0 ? " is-sun" : "");
    el.innerHTML =
      `<div class="d-wd">${info.wd}</div>` +
      `<div class="d-md">${info.md}</div>` +
      `<div class="d-icon">${d.icon || "❓"}</div>` +
      `<div class="d-temps"><span class="hi">${tempN(d.tempMax)}</span> ` +
      `<span class="lo">${tempN(d.tempMin)}</span></div>` +
      (d.pop !== null && d.pop !== undefined ? `<div class="d-pop">${d.pop}%</div>` : "");
    weekly.appendChild(el);
  });
}

// today's hourly forecast (met.no)
async function loadHourly() {
  try {
    const list = await (await fetch("/api/weather/hourly?city=" + encodeURIComponent(cityId || ""))).json();
    if (Array.isArray(list)) {
      lastHourly = list;
      renderHourly(list);
      // if the today card is missing its high/low, re-render now that hourly is in
      const td = lastWeather && lastWeather.today;
      if (td && (td.tempMax === null || td.tempMax === undefined || td.tempMin === null || td.tempMin === undefined)) {
        renderWeather(lastWeather);
      }
    }
  } catch (_) { /* keep last */ }
}

function renderHourly(list) {
  const el = document.getElementById("hourly");
  el.innerHTML = "";
  (list || []).forEach((h, i) => {
    const item = document.createElement("div");
    item.className = "hour-item" + (i === 0 ? " now" : "");
    const label = i === 0 ? t("now") : (h.hour + t("hourUnit"));
    const temp = (h.temp === null || h.temp === undefined) ? "--" : String(h.temp);   // no ° (see weekly)
    item.innerHTML =
      `<div class="hour-time">${label}</div>` +
      `<div class="hour-icon">${h.icon || "❓"}</div>` +
      `<div class="hour-temp">${temp}</div>` +
      `<div class="hour-precip">${h.precip > 0 ? h.precip + "mm" : "&nbsp;"}</div>`;
    el.appendChild(item);
  });
}

function weekdayInfo(dateStr, fallbackWd, fallbackMd) {
  if (!dateStr) return { wd: fallbackWd || "", md: fallbackMd || "", dow: -1 };
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return {
    wd: new Intl.DateTimeFormat(locale, { weekday: "short" }).format(dt),
    md: `${m}/${d}`,
    dow: dt.getDay(),
  };
}

// ---- news ------------------------------------------------------------------
async function loadNews() {
  try {
    const data = await (await fetch("/api/news?ai=" + encodeURIComponent(aiSrc || ""))).json();
    if (data) { lastNews = data; renderNews(data); }
  } catch (_) { /* keep last */ }
}

function renderNews(data) {
  const aiLang = (aiSources.find((s) => s.id === aiSrc) || {}).lang || "zh";
  fillNewsList("news-ai", data.ai || [], aiLang);
  fillNewsList("news-japan", data.japan || [], "ja");   // 主要ニュース is always Japanese
}

function fillNewsList(id, items, contentLang) {
  const ul = document.getElementById(id);
  ul.lang = contentLang || "";       // pick the font by the content's language
  ul.innerHTML = "";
  items.forEach((it) => {
    const li = document.createElement("li");
    if (it.alert) { li.className = "n-alert"; li.lang = "ja"; }   // NERV alerts are Japanese
    const dot = document.createElement("span"); dot.className = "n-dot"; dot.textContent = it.alert ? "⚠️" : "•";
    const title = document.createElement("span"); title.className = "n-title"; title.textContent = it.title;
    li.appendChild(dot); li.appendChild(title);
    if (it.source) {
      const src = document.createElement("span"); src.className = "n-src"; src.textContent = it.source;
      li.appendChild(src);
    }
    ul.appendChild(li);
  });
}

// ---- earthquake ------------------------------------------------------------
const overlay = document.getElementById("quake-overlay");
let quakeHideTimer = null, quakeTickTimer = null, currentQuakeKey = null;
let lastEvent = null, manualMode = false, shownEvent = null, dismissedBase = null;
let recentQuakes = [];   // last N 地震情報 for the 🗾 browse list

const eventBase = (ev) => ev.kind + ":" + ev.id;

const SCALE_CLASS = { 10: "i1", 20: "i2", 30: "i3", 40: "i4", 45: "i5w", 50: "i5s", 55: "i6w", 60: "i6s", 70: "i7" };
function scaleClass(s) { return SCALE_CLASS[s] || "i1"; }

function formatDepth(km) {
  if (km === null || km === undefined || km < 0) return t("unknown");
  if (km === 0) return t("veryShallow");
  return km + "km";
}
function formatMag(m) {
  if (m === null || m === undefined || m < 0) return t("unknown");
  return "M" + m;
}
function formatOrigin(s) {
  if (!s) return "—";
  const d = new Date(s.replace(/\//g, "-").replace(" ", "T"));
  if (isNaN(d.getTime())) return s;
  return new Intl.DateTimeFormat(locale, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(d);
}

// A real earthquake/EEW arrived: take over the screen and auto-hide after 5 min.
function handleQuake(ev) {
  if (!ev || !ev.kind) return;
  if (ev.cancelled) { hideQuake(); return; }
  lastEvent = ev;                                  // always remember the newest
  if (ev.kind === "quake") {                       // keep the browse list fresh
    const key = ev.originTime || ev.id;
    recentQuakes = [ev, ...recentQuakes.filter((e) => (e.originTime || e.id) !== key)].slice(0, 5);
  }
  if (!manualMode && eventBase(ev) === dismissedBase) return;  // user closed this one
  dismissedBase = null;
  manualMode = false;
  document.getElementById("quake-recent").classList.add("hidden");   // live takeover: no list
  showQuakeLayout(ev);
  currentQuakeKey = ev.kind + ":" + ev.id + ":" + ev.revision;
  scheduleHide(ev.expiresAt);
}

function showQuakeLayout(ev) {
  shownEvent = ev || null;
  if (ev) renderQuake(ev); else renderEmptyQuake();
  overlay.classList.remove("hidden");
  overlay.classList.toggle("is-eew", !!ev && ev.kind === "eew");
  document.getElementById("quake-toggle").classList.add("active");
}

// Manual button (🗾): open the earthquake layout on demand, or close it.
function toggleQuakeLayout() {
  if (!overlay.classList.contains("hidden")) { closeQuake(); return; }
  dismissedBase = null;               // user wants to look; allow live events again
  manualMode = true;
  clearTimeout(quakeHideTimer);
  clearInterval(quakeTickTimer);
  const recentEl = document.getElementById("quake-recent");
  if (recentQuakes.length) {
    renderRecentList();
    recentEl.classList.remove("hidden");
    selectRecent(0);                  // show the newest, list lets you pick others
  } else {
    recentEl.classList.add("hidden");
    showQuakeLayout(null);            // nothing recorded yet -> empty state
    document.getElementById("quake-remaining").textContent = t("manualHint");
  }
}

// Build the tappable list of recent quakes (manual browse mode only).
function renderRecentList() {
  const el = document.getElementById("quake-recent");
  el.innerHTML = "";
  const title = document.createElement("div");
  title.className = "rq-title";
  title.textContent = t("recentList");
  el.appendChild(title);
  recentQuakes.forEach((ev, i) => {
    const item = document.createElement("div");
    item.className = "rq-item";
    const b = document.createElement("span");
    b.className = "r-badge " + (ev.maxScale > 0 ? scaleClass(ev.maxScale) : "i1");
    b.textContent = ev.maxIntensity || "—";
    const txt = document.createElement("div");
    txt.className = "rq-text";
    const place = document.createElement("div");
    place.className = "rq-place";
    place.textContent = (ev.hypocenter && ev.hypocenter.name) || "—";
    const time = document.createElement("div");
    time.className = "rq-time";
    time.textContent = formatOrigin(ev.originTime);
    txt.appendChild(place); txt.appendChild(time);
    item.appendChild(b); item.appendChild(txt);
    item.onclick = () => selectRecent(i);
    el.appendChild(item);
  });
}

// Show recent quake i and highlight its list row.
function selectRecent(i) {
  const ev = recentQuakes[i];
  if (!ev) return;
  shownEvent = ev;
  renderQuake(ev);
  overlay.classList.remove("hidden");
  overlay.classList.remove("is-eew");
  document.getElementById("quake-toggle").classList.add("active");
  document.querySelectorAll("#quake-recent .rq-item").forEach((el, k) =>
    el.classList.toggle("active", k === i));
  document.getElementById("quake-remaining").textContent =
    `${t("recentQuake")} · ${formatOrigin(ev.originTime)}`;
}

// Close the earthquake screen (✕ or 🗾). Dismissing a live event keeps it closed
// (so the 30s poll won't immediately reopen it) until a new quake or manual reopen.
function closeQuake() {
  if (shownEvent && !manualMode) dismissedBase = eventBase(shownEvent);
  hideQuake();
}

function renderEmptyQuake() {
  const banner = document.getElementById("quake-banner");
  banner.style.display = "";
  banner.className = "quake-banner i1";
  document.getElementById("quake-title").textContent = t("quakeInfo");
  document.getElementById("quake-sub").textContent = "";
  const badge = document.getElementById("quake-badge");
  badge.className = "intensity-badge i1";
  badge.textContent = "—";
  ["q-epicenter", "q-mag", "q-depth", "q-origin"].forEach((id) => {
    document.getElementById(id).textContent = "—";
  });
  document.getElementById("q-tsunami-row").classList.add("hidden");
  const regions = document.getElementById("quake-regions");
  regions.innerHTML = "";
  const note = document.createElement("div");
  note.className = "quake-note";
  note.textContent = t("noQuake");
  regions.appendChild(note);
  if (window.QuakeMap) window.QuakeMap.paint([], null);
}

function renderQuake(ev) {
  const isEew = ev.kind === "eew";
  const banner = document.getElementById("quake-banner");
  banner.style.display = "";
  banner.className = "quake-banner " + scaleClass(ev.maxScale > 0 ? ev.maxScale : (isEew ? 55 : 20));
  document.getElementById("quake-title").textContent = isEew ? t("eew") : t("quakeInfo");
  document.getElementById("quake-sub").textContent = isEew ? t("eewSub") : (ev.issueLabel || "");

  const badge = document.getElementById("quake-badge");
  badge.className = "intensity-badge " + (ev.maxScale > 0 ? scaleClass(ev.maxScale) : "i1");
  badge.textContent = ev.maxIntensity || "—";

  const hypo = ev.hypocenter || {};
  document.getElementById("q-epicenter").textContent = hypo.name || "—";
  document.getElementById("q-mag").textContent = formatMag(hypo.magnitude);
  document.getElementById("q-depth").textContent = formatDepth(hypo.depth);
  document.getElementById("q-origin").textContent = formatOrigin(ev.originTime);

  // tsunami row (earthquake reports only, when info exists)
  const tsuRow = document.getElementById("q-tsunami-row");
  const tsuLabel = !isEew ? (t("tsunamiMap")[ev.tsunami] || "") : "";
  if (tsuLabel) {
    tsuRow.classList.remove("hidden");
    document.getElementById("q-tsunami").textContent = tsuLabel;
  } else {
    tsuRow.classList.add("hidden");
  }

  // 各地の震度 — group prefectures by intensity level (report style)
  const regions = document.getElementById("quake-regions");
  regions.innerHTML = "";
  const groups = {};
  (ev.regions || []).forEach((r) => {
    if (!r.label) return;
    (groups[r.scale] = groups[r.scale] || { label: r.label, names: [] }).names.push(r.name);
  });
  Object.keys(groups).map(Number).sort((a, b) => b - a).forEach((scale) => {
    const g = groups[scale];
    const row = document.createElement("div");
    row.className = "shindo-group";
    const b = document.createElement("span");
    b.className = "r-badge " + scaleClass(scale);
    b.textContent = g.label;
    const names = document.createElement("span");
    names.className = "shindo-names";
    names.textContent = g.names.join("　");
    row.appendChild(b); row.appendChild(names);
    regions.appendChild(row);
  });

  if (window.QuakeMap) window.QuakeMap.paint(ev.regions, ev.hypocenter);
}

function scheduleHide(expiresAt) {
  clearTimeout(quakeHideTimer);
  clearInterval(quakeTickTimer);
  const remainingEl = document.getElementById("quake-remaining");
  const tick = () => {
    const left = Math.max(0, Math.round(expiresAt - Date.now() / 1000));
    remainingEl.textContent = `${t("remaining")} · ${left}s`;
    if (left <= 0) hideQuake();
  };
  tick();
  quakeTickTimer = setInterval(tick, 1000);
  quakeHideTimer = setTimeout(hideQuake, Math.max(0, expiresAt * 1000 - Date.now()));
}

function hideQuake() {
  clearTimeout(quakeHideTimer);
  clearInterval(quakeTickTimer);
  overlay.classList.add("hidden");
  overlay.classList.remove("is-eew");
  document.getElementById("quake-recent").classList.add("hidden");
  document.getElementById("quake-toggle").classList.remove("active");
  currentQuakeKey = null;
  manualMode = false;
  shownEvent = null;
}

// Seed / refresh the recent-quakes list so 🗾 always has something to browse.
async function loadRecentQuakes() {
  try {
    const list = await (await fetch("/api/earthquake/recent")).json();
    if (Array.isArray(list) && list.length) {
      recentQuakes = list;
      lastEvent = list[0];
    }
  } catch (_) { /* ignore */ }
}

async function pollQuake() {
  try {
    const ev = await (await fetch("/api/earthquake/current")).json();
    if (ev && ev.kind && ev.expiresAt * 1000 > Date.now()) {
      const key = ev.kind + ":" + ev.id + ":" + ev.revision;
      if (key !== currentQuakeKey) handleQuake(ev);
    }
  } catch (_) { /* ignore */ }
}

// ---- WebSocket -------------------------------------------------------------
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "earthquake") handleQuake(m.event);
    } catch (_) { /* ignore */ }
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

// ---- settings --------------------------------------------------------------
const openSettings = () =>
  document.getElementById("settings-overlay").classList.remove("hidden");
document.getElementById("city").onclick = openSettings;
document.getElementById("city").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSettings(); }
};
document.getElementById("settings-close").onclick = () =>
  document.getElementById("settings-overlay").classList.add("hidden");
document.getElementById("quake-toggle").onclick = toggleQuakeLayout;
document.getElementById("quake-close").onclick = closeQuake;

// ---- utils -----------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- init ------------------------------------------------------------------
async function init() {
  let cfg = {};
  try { cfg = await (await fetch("/api/config")).json(); } catch (_) { /* defaults below */ }
  if (!lang) lang = cfg.language || "ja";
  if (!window.I18N[lang]) lang = "ja";
  if (!cityId) cityId = cfg.city || "tokyo";
  if (!aiSrc) aiSrc = cfg.aiSource || "cn";
  try { cities = await (await fetch("/api/cities")).json(); } catch (_) { cities = []; }
  try { aiSources = await (await fetch("/api/ai-sources")).json(); } catch (_) { aiSources = []; }

  applyI18n();
  buildLangOptions();
  buildCityOptions();
  buildAiOptions();
  startClock();
  loadWeather();
  loadHourly();
  loadNews();
  if (window.QuakeMap) window.QuakeMap.init("quake-map");   // preload map in background
  loadRecentQuakes();
  pollQuake();
  connectWS();

  setInterval(loadWeather, 10 * 60 * 1000);
  setInterval(loadHourly, 15 * 60 * 1000);
  setInterval(loadNews, 5 * 60 * 1000);
  setInterval(pollQuake, 30 * 1000);
  setInterval(loadRecentQuakes, 3 * 60 * 1000);
}

init();
