"use strict";

// Shared state, translation lookup and the JST/date helpers every other module
// needs. Loaded first: the panels below all read `lang` / `locale` / `cityId`
// from here, and call t() and escapeHtml().

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
let minScale = Number(localStorage.getItem("minScale")) || null;   // 震度 code; below → no full-screen

// ---- JMA 震度 scale ---------------------------------------------------------
// Mirrors SCALE in backend/earthquake.py. Lives here rather than in quake.js
// because map.js needs the same mapping — two copies of this literal is exactly
// the kind of thing that drifts the day a new code appears.
// 46 = 震度5弱以上と推定 (P2P sends it when the exact intensity isn't determined yet)
const SCALE_LABEL = { 10: "1", 20: "2", 30: "3", 40: "4", 45: "5弱", 46: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7" };
const SCALE_CLASS = { 10: "i1", 20: "i2", 30: "i3", 40: "i4", 45: "i5w", 46: "i5w", 50: "i5s", 55: "i6w", 60: "i6s", 70: "i7" };

function t(key) {
  return (window.I18N[lang] && window.I18N[lang][key]) || window.I18N.ja[key] || key;
}

function applyI18n() {
  locale = t("_locale");
  clockFmt = null;   // locale changed → rebuild the clock/date formatters
  document.documentElement.lang = lang;
  document.title = t("docTitle");   // browser tab / bookmark title, in the current language
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  // re-render cached data so labels/locale update immediately
  if (lastWeather) renderWeather(lastWeather);
  if (lastHourly) renderHourly(lastHourly);
  if (lastNews) renderNews(lastNews);
  if (lastHoliday) renderHoliday(lastHoliday);
  renderAnime(lastAnime || []);   // re-render so the 準備中 placeholder switches language too
  updateFullscreenBtn();
  buildNightOption();
}

// ---- clock -----------------------------------------------------------------
// Pin the clock/date/weekday to JST (Asia/Tokyo) like the rest of the app
// (jstHour/jstDateISO); otherwise a tablet whose OS timezone is not JST would
// show a day that disagrees with the JST anime cutoff and holiday countdown.
// Formatters are built once per locale (rebuilt by applyI18n via clockFmt=null),
// and DOM writes are skipped while the displayed strings haven't changed.
let clockFmt = null;
function formatDateOnly() {
  if (!clockFmt) buildClockFormatters();
  return clockFmt.date.format(new Date());
}
function formatWeekday() {
  if (!clockFmt) buildClockFormatters();
  return clockFmt.wd.format(new Date());
}
function buildClockFormatters() {
  clockFmt = {
    time: new Intl.DateTimeFormat(locale, { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hour12: false }),
    date: new Intl.DateTimeFormat(locale, { timeZone: "Asia/Tokyo", month: "long", day: "numeric" }),
    wd: new Intl.DateTimeFormat(locale, { timeZone: "Asia/Tokyo", weekday: "short" }),
  };
}

// Hooks the clock runs for other panels: once per minute change, and once per JST
// day change (holiday countdown, anime cutoff — things that are correct only if
// they are recomputed when the calendar turns, not when their own poll happens to
// fire). Registered by app.js; core.js stays free of panel knowledge.
const minuteHooks = [], dayHooks = [];

function startClock() {
  const timeEl = document.getElementById("time");
  let shown = "", shownDay = jstDateISO();
  const tick = () => {
    if (!clockFmt) buildClockFormatters();
    const now = new Date();
    const s = clockFmt.time.format(now);
    if (s === shown) return;            // minute unchanged → no DOM work
    shown = s;
    timeEl.textContent = s;
    const dateEl = document.getElementById("today-date");   // date + weekday live in the today card
    if (dateEl) dateEl.textContent = clockFmt.date.format(now);
    const wdEl = document.getElementById("today-wd");
    if (wdEl) wdEl.textContent = clockFmt.wd.format(now);
    minuteHooks.forEach((fn) => { try { fn(now); } catch (_) { /* a panel's problem, not the clock's */ } });
    const day = jstDateISO();
    if (day !== shownDay) {
      shownDay = day;
      dayHooks.forEach((fn) => { try { fn(day); } catch (_) { /* ditto */ } });
    }
  };
  tick();
  // Fire just past each minute boundary instead of polling every 5 s: the display
  // then never lags the real minute by up to a poll period. A coarse interval backs
  // it up in case the browser ever coalesces a long timeout (the tick is idempotent).
  const alignedTick = () => {
    tick();
    setTimeout(alignedTick, 60000 - (Date.now() % 60000) + 50);
  };
  setTimeout(alignedTick, 60000 - (Date.now() % 60000) + 50);
  setInterval(tick, 15000);
}

// ---- cold-start retry ------------------------------------------------------
// The regular pollers run every 5–60 min. If the tablet boots before the backend
// has its first data — a power cut brings both back at once, and the backend
// needs a few seconds per upstream — a panel would sit on its placeholder until
// its next poll: up to an hour for FX / holiday / anime. Until every panel has
// painted once, re-ask the empty ones on a short, backing-off schedule.
// Backend-side caching and its failure cooldown keep this from reaching upstreams
// more than once per ~30 s, and it stops by itself once everything has data.
const COLD_RETRY_MS = 15 * 1000, COLD_RETRY_MAX_MS = 120 * 1000;
function startColdStartRetry(loaders, firstMs = COLD_RETRY_MS) {
  let wait = firstMs;
  const again = () => {
    const pending = loaders.filter((l) => !l.has());
    if (!pending.length) return;                     // everything painted — done
    pending.forEach((l) => { try { l.load(); } catch (_) { /* loaders never throw */ } });
    wait = Math.min(wait * 1.5, COLD_RETRY_MAX_MS);
    setTimeout(again, wait);
  };
  setTimeout(again, wait);
}

// ---- shared helpers --------------------------------------------------------
function jstDateISO() {   // today's date in JST as "YYYY-MM-DD"
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function jstHour() {
  return Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Tokyo", hour: "2-digit", hour12: false,
  }).format(new Date()));
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

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Boot metadata, fetched together — these three are independent, so awaiting them
// in sequence just spent two round trips before the first pixel of real data.
// Bounded: a server that accepts the connection and then never answers would
// otherwise park init() behind Chrome's multi-minute request timeout — with the
// clock reading --:-- and no poller installed until it gave up.
const FETCH_TIMEOUT_MS = 8000;
function fetchOpts() {
  return (typeof AbortSignal !== "undefined" && AbortSignal.timeout)
    ? { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) } : {};
}
async function getJson(url, fallback) {
  try {
    const data = await (await fetch(url, fetchOpts())).json();
    // `null` is valid JSON and does NOT throw — but it would take init() down on
    // the first property read, and a thrown init() means a permanently blank
    // dashboard. Every caller wants the fallback's shape, never null.
    return data == null ? fallback : data;
  } catch (_) {
    return fallback;
  }
}
