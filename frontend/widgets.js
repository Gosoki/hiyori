"use strict";

// Bottom-bar widgets: 為替 exchange rate, 祝日 holiday countdown, 新番 anime.

// ---- exchange rate ---------------------------------------------------------
async function loadFx() {
  try {
    const fx = await (await fetch("/api/fx")).json();
    if (fx && fx.rate) renderFx(fx);
  } catch (_) { /* keep last */ }
}
function fxFmt(v) { return v.toFixed(3).replace(/\.?0+$/, ""); }   // 3 decimals, trim trailing zeros
// If the converted amount's integer part is < 1, scale BOTH sides ×10 until it's ≥ 1.
function fxPair(fromLabel, toLabel, rate) {
  let n = 1, val = rate;
  while (val < 1 && n < 1e6) { n *= 10; val = rate * n; }
  return `${n}${fromLabel} = <span class="fx-n">${fxFmt(val)}</span>${toLabel}`;
}
function renderFx(fx) {
  const body = document.getElementById("fx-body");
  if (!body) return;
  if (!fx || !fx.rate) { body.innerHTML = '<span class="slot-dim">—</span>'; return; }
  body.innerHTML =                            // one direction per line
    `<div class="fx-line">${fxPair(fx.baseLabel, fx.quoteLabel, fx.rate)}</div>` +
    `<div class="fx-line">${fxPair(fx.quoteLabel, fx.baseLabel, 1 / fx.rate)}</div>`;
}

// ---- next Japanese holiday countdown (3rd line under the fx rates) ----------
let lastHoliday = null;
async function loadHoliday() {
  try {
    const list = await (await fetch("/api/holiday")).json();
    if (Array.isArray(list) && list.length) { lastHoliday = list; renderHoliday(list); }   // keep last-good on empty
  } catch (_) { /* keep last */ }
}

function renderHoliday(list) {
  // English: hide just the 祝日/Holiday title label; the countdown line still shows.
  const titleEl = document.querySelector("#holiday-cell .slot-title");
  if (titleEl) titleEl.style.display = lang === "en" ? "none" : "";
  const el = document.getElementById("holiday-line");
  if (!el) return;
  const today = jstDateISO();
  const next = (list || []).find((h) => h.date >= today);   // day count is computed here, always current
  if (!next) { el.textContent = ""; return; }
  const days = Math.round((Date.parse(next.date) - Date.parse(today)) / 86400000);
  const nm = `<span class="hl-name" lang="ja">${escapeHtml(next.name)}</span>`;   // bold red, spaces around
  const n = `<span class="hl-n">${days}</span>`;
  if (lang === "ja") el.innerHTML = days === 0 ? `本日は ${nm}` : `${nm} まで ${n} 日`;
  else if (lang === "en") el.innerHTML = days === 0 ? `Today: ${nm}` : `${n} days to ${nm}`;
  else el.innerHTML = days === 0 ? `本日は ${nm}` : `距 ${nm} ${n} 天`;
}

// ---- anime schedule (今夜の放送) -------------------------------------------
let lastAnime = null;
let lastAnimeDay = null;   // JST date of the last successful fetch, to detect a midnight rollover
async function loadAnime() {
  try {
    const list = await (await fetch("/api/anime")).json();
    // non-empty only: an empty list just means the backend hasn't recovered from a
    // Jikan outage yet — don't wipe a list we're already showing
    if (Array.isArray(list) && list.length) {
      const changed = JSON.stringify(list) !== JSON.stringify(lastAnime);
      lastAnime = list;
      // After a JST day-roll, the backend may still serve yesterday's list for a
      // few minutes. Only accept the fetch as "today's" when the content actually
      // changed — otherwise keep lastAnimeDay stale so the 10-min tick retries.
      if (changed || !lastAnimeDay || lastAnimeDay === jstDateISO()) lastAnimeDay = jstDateISO();
      renderAnime(list);
    }
  } catch (_) { /* keep last */ }
}

function renderAnime(list) {
  const body = document.getElementById("anime-body");
  if (!body) return;
  let items = Array.isArray(list) ? list : [];
  // From 18:00 on, drop shows that already aired earlier today (before 18:00);
  // 18:00–23:59 today plus next-day late-night (24:00+) stay.
  if (jstHour() >= 18) items = items.filter((a) => a.time >= "18:00");
  if (!items.length) { body.innerHTML = `<span class="slot-dim">${t("comingSoon")}</span>`; return; }
  const shown = items.slice(0, 9);              // fixed 3-column grid, chronological (backend already sorted)
  const rows = Math.ceil(shown.length / 3);     // ≤3 rows; fewer rows → more lines/cell
  body.style.setProperty("--a-lines", rows <= 1 ? 3 : rows === 2 ? 2 : 1);
  body.innerHTML = shown
    .map((a) => `<div class="a-cell"><span class="a-t">${escapeHtml(a.time)}</span> ${escapeHtml(a.title)}</div>`)
    .join("");
}
