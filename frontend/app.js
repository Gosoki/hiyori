"use strict";

// Shell: settings panel, fullscreen, event wiring, and boot. Loaded LAST — this is
// the only module with top-level code that runs, so every function it calls is
// already defined by the time init() executes.

// ---- settings panel --------------------------------------------------------
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

// Full-screen 震度 threshold (per device): quakes below this stay in the 🗾 list only.
// Three sensible presets — but EARTHQUAKE_MIN_SCALE can be set to any JMA code, and
// a value that isn't offered here would leave the panel with NOTHING highlighted
// while still being in force. Whatever is actually in effect always gets a button.
const THRESHOLDS = [{ scale: 30, label: "3+" }, { scale: 40, label: "4+" }, { scale: 45, label: "5弱+" }];
function thresholdOptions() {
  const opts = THRESHOLDS.slice();
  if (minScale && !opts.some((t) => t.scale === minScale)) {
    opts.push({ scale: minScale, label: (SCALE_LABEL[minScale] || minScale) + "+" });
    opts.sort((a, b) => a.scale - b.scale);
  }
  return opts;
}

function buildThresholdOptions() {
  const wrap = document.getElementById("threshold-options");
  if (!wrap) return;
  wrap.innerHTML = "";
  thresholdOptions().forEach((th) => {
    const b = document.createElement("button");
    b.textContent = th.label;
    if (th.scale === minScale) b.classList.add("active");
    b.onclick = () => {
      minScale = th.scale;
      localStorage.setItem("minScale", th.scale);
      buildThresholdOptions();
    };
    wrap.appendChild(b);
  });
}

// ---- settings --------------------------------------------------------------
const openSettings = () =>
  document.getElementById("settings-overlay").classList.remove("hidden");

// ---- fullscreen ------------------------------------------------------------
function isFullscreen() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}
function updateFullscreenBtn() {
  const btn = document.getElementById("fullscreen-btn");
  if (btn) btn.textContent = t(isFullscreen() ? "exitFullscreen" : "fullscreen");
}
function toggleFullscreen() {
  try {
    if (isFullscreen()) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) { const p = exit.call(document); if (p && p.catch) p.catch(() => {}); }
    } else {
      const el = document.documentElement;
      const req = el.requestFullscreen || el.webkitRequestFullscreen;
      if (req) { const p = req.call(el); if (p && p.catch) p.catch(() => {}); }
    }
  } catch (_) { /* Fullscreen API unavailable (locked-down webview) */ }
}
document.getElementById("fullscreen-btn").onclick = toggleFullscreen;
document.getElementById("time").onclick = toggleFullscreen;   // tap the clock to toggle fullscreen
document.addEventListener("fullscreenchange", updateFullscreenBtn);
document.addEventListener("webkitfullscreenchange", updateFullscreenBtn);
document.getElementById("city").onclick = openSettings;
document.getElementById("city").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSettings(); }
};
document.getElementById("settings-close").onclick = () =>
  document.getElementById("settings-overlay").classList.add("hidden");
document.getElementById("quake-toggle").onclick = toggleQuakeLayout;
document.getElementById("quake-close").onclick = closeQuake;

// ---- init ------------------------------------------------------------------
async function init() {
  const [cfg, cityList, aiList] = await Promise.all([
    getJson("/api/config", {}),
    getJson("/api/cities", []),
    getJson("/api/ai-sources", []),
  ]);
  if (!lang) lang = cfg.language || "ja";
  if (!window.I18N[lang]) lang = "ja";
  if (!cityId) cityId = cfg.city || "tokyo";
  if (!aiSrc) aiSrc = cfg.aiSource || "cn";
  if (!minScale) minScale = cfg.minScale || 30;
  if (cfg.recentCount > 0) recentCount = cfg.recentCount;
  cities = Array.isArray(cityList) ? cityList : [];
  aiSources = Array.isArray(aiList) ? aiList : [];

  applyI18n();
  buildLangOptions();
  buildCityOptions();
  buildAiOptions();
  buildThresholdOptions();
  startClock();
  loadWeather();
  loadHourly();
  loadNews();
  loadFx();
  loadAnime();
  loadHoliday();
  if (window.QuakeMap) window.QuakeMap.init("quake-map");   // preload map in background
  loadRecentQuakes();
  pollQuake();
  pollHealth();
  connectWS();

  setInterval(loadWeather, 10 * 60 * 1000);
  setInterval(loadHourly, 30 * 60 * 1000);   // matches the backend's 30-min hourly cache
  setInterval(loadNews, 5 * 60 * 1000);
  setInterval(loadFx, 60 * 60 * 1000);       // upstream rates only change ~daily
  setInterval(loadHoliday, 60 * 60 * 1000);   // hourly; re-count days across midnight
  setInterval(loadAnime, 60 * 60 * 1000);   // hourly fetch
  setInterval(() => {
    if (!lastAnime || !lastAnime.length ||                            // still empty (Jikan outage) → retry soon
        (lastAnimeDay && jstDateISO() !== lastAnimeDay)) loadAnime(); // JST day rolled over → refetch
    else renderAnime(lastAnime);                                      // else just re-apply the 18:00 cutoff
  }, 10 * 60 * 1000);
  observeNewsLists();   // re-fit whenever a news list's height changes (weather render, orientation…)
  setInterval(pollQuake, 60 * 1000);            // WS delivers instantly; this is only the fallback (90s hold still caught)
  setInterval(pollHealth, 60 * 1000);           // surface a dead quake feed on the 🗾 button
  setInterval(loadRecentQuakes, 15 * 60 * 1000); // WS keeps the list live; 🗾 open + WS-reconnect also refresh it
}

init();
