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
