"use strict";

// 主要ニュース + AI・テック columns, and the fitting logic that hides a headline
// which would otherwise be clipped mid-line.

// ---- news ------------------------------------------------------------------
let lastNewsText = "";   // raw response of the last render — skip DOM churn when unchanged
async function loadNews() {
  const want = aiSrc;   // discard the response if the user switched source mid-flight
  try {
    const txt = await (await fetch("/api/news?ai=" + encodeURIComponent(aiSrc || ""))).text();
    if (want !== aiSrc) return;
    if (!txt || txt === lastNewsText) return;   // headlines unchanged → no re-render
    const data = JSON.parse(txt);
    if (!data) return;
    lastNews = data;
    renderNews(data);
    lastNewsText = txt;   // only after a clean render, or a render that threw would
                          // make every later poll short-circuit on "unchanged"
  } catch (_) { /* keep last */ }
}

function renderNews(data) {
  // per-column empty guard: a backend cold-started during an outage returns [] —
  // keep whatever the tablet is already showing instead of blanking the column
  const aiLang = (aiSources.find((s) => s.id === aiSrc) || {}).lang || "zh";
  if ((data.ai || []).length) fillNewsList("news-ai", data.ai, aiLang);
  if ((data.japan || []).length) fillNewsList("news-japan", data.japan, "ja");   // 主要ニュース is always Japanese
  renderAlertBanner((data.japan || []).filter((it) => it && it.alert));
}

// The user picked another AI source: the old source's headlines — in the old
// source's font — must not sit there looking current until the next poll lands
// (or forever, if the new source is still cold and returns []). Show the loading
// placeholder in the new language, and forget the "unchanged" fingerprint so the
// next response is rendered even if it happens to be byte-identical.
function resetAiColumn(contentLang) {
  const ul = document.getElementById("news-ai");
  if (!ul) return;
  ul.lang = contentLang || "";
  ul.innerHTML = "";
  const li = document.createElement("li");
  li.className = "loading";
  li.textContent = t("noData");
  ul.appendChild(li);
  lastNewsText = "";
}

// ---- severe-alert banner ---------------------------------------------------
// NERV items the backend flagged `banner` (津波警報 / 特別警報 / Jアラート…): the red
// line in the news column is invisible from across a room, so the newest one is
// also spread across the top of the dashboard. It clears by age — a warning stands
// for hours, not days — or when the backend stops pinning it. No ✕ on purpose: a
// dismissable multi-hour warning would just be re-dismissed by whoever walks past.
const BANNER_MAX_AGE_MS = 3 * 60 * 60 * 1000;
let lastBannerItems = [];
function renderAlertBanner(items) {
  lastBannerItems = Array.isArray(items) ? items : [];
  const el = document.getElementById("alert-banner");
  if (!el) return;
  const now = Date.now();
  const live = lastBannerItems.filter((it) =>
    it && it.banner && it.title && (!it.ts || now - Number(it.ts) * 1000 < BANNER_MAX_AGE_MS));
  const top = live[0] || null;
  el.textContent = top ? "⚠️ " + top.title : "";
  el.classList.toggle("hidden", !top);
  const dash = document.getElementById("dashboard");
  if (dash) dash.classList.toggle("has-alert", !!top);
}

function fillNewsList(id, items, contentLang) {
  const ul = document.getElementById(id);
  ul.lang = contentLang || "";       // pick the font by the content's language
  ul.innerHTML = "";
  items.forEach((it) => {
    const li = document.createElement("li");
    if (it.alert) { li.className = "n-alert"; li.lang = "ja"; }   // NERV alerts are Japanese
    const dot = document.createElement("span"); dot.className = "n-dot"; dot.textContent = it.alert ? "⚠️" : "";   // normal = CSS-drawn circle (font-independent size)
    const title = document.createElement("span"); title.className = "n-title"; title.textContent = it.title;
    li.appendChild(dot); li.appendChild(title);
    if (it.source) {
      const src = document.createElement("span"); src.className = "n-src"; src.textContent = it.source;
      li.appendChild(src);
    }
    ul.appendChild(li);
  });
  clampListToFit(ul);
}

// Hide any trailing headline that can't fully fit (drop the partially-clipped last line).
function clampListToFit(ul) {
  if (!ul || ul.getBoundingClientRect().height < 4) return;
  const kids = Array.from(ul.children);
  kids.forEach((li) => { li.style.display = ""; });     // show all first
  const bottom = ul.getBoundingClientRect().bottom;     // ul height is fixed (flex:1), stable
  let clipped = false;
  kids.forEach((li) => {
    if (clipped) { li.style.display = "none"; return; }
    if (li.getBoundingClientRect().bottom > bottom + 0.5) { li.style.display = "none"; clipped = true; }
  });
}

// The news row is a 1fr grid track, so its height shifts as the weather panel
// (auto height) renders/updates. Re-clamp whenever a list's box actually changes.
let newsObserver;
function observeNewsLists() {
  if (newsObserver || !window.ResizeObserver) return;
  newsObserver = new ResizeObserver((entries) => {
    for (const e of entries) clampListToFit(e.target);
  });
  ["news-ai", "news-japan"].forEach((id) => {
    const ul = document.getElementById(id);
    if (ul) newsObserver.observe(ul);
  });
}
