"use strict";

// The earthquake takeover screen: live events over the WebSocket, the 🗾 browse
// list, the per-device 震度 threshold, and the feed-health badge.

// ---- earthquake ------------------------------------------------------------
const overlay = document.getElementById("quake-overlay");
let quakeHideTimer = null, quakeTickTimer = null, quakeIdleTimer = null, currentQuakeKey = null;
const MANUAL_IDLE_MS = 60 * 1000;   // auto-return a manually-opened 🗾 overlay to the dashboard if left untouched
let manualMode = false, shownEvent = null, dismissedBase = null;
let recentQuakes = [];   // last N 地震情報 for the 🗾 browse list
let recentCount = 5;     // N, from /api/config (EARTHQUAKE_RECENT_COUNT) — kept in step with the backend

// Key by originTime (the quake itself), not the bulletin id — dismissing one
// bulletin then also dismisses the same quake's follow-up reports (第2報, 詳報…).
// Mirrors _quake_key / _event_base in backend/earthquake.py.
const quakeKey = (ev) => (ev && (ev.originTime || ev.id)) || "";
const eventBase = (ev) => ev.kind + ":" + quakeKey(ev);

function scaleClass(s) { return SCALE_CLASS[s] || "i1"; }   // SCALE_CLASS: core.js
function quakeScale(ev) {
  const s = Number(ev && ev.maxScale);
  if (Number.isFinite(s) && s >= 0) return s;
  // unknown intensity: an early EEW fails safe (show it — every second counts);
  // but a 551 with -1 (震源に関する情報 carries no intensity) must NOT bypass the
  // per-device threshold — it stays in the 🗾 list only.
  return ev && ev.kind === "eew" ? 999 : -1;
}

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
  if (ev.cancelled) {
    // a cancel only hides the event it refers to — not an unrelated takeover
    // that happens to be on screen, and never a manual 🗾 browse session
    if (!manualMode && shownEvent && eventBase(shownEvent) === eventBase(ev)) hideQuake();
    return;
  }
  if (ev.kind === "quake") {                       // keep the browse list fresh
    const key = quakeKey(ev);
    recentQuakes = [ev, ...recentQuakes.filter((e) => quakeKey(e) !== key)].slice(0, recentCount);
  }
  if (quakeScale(ev) < (minScale || 0)) return;    // below this device's 震度 threshold → 🗾 list only
  if (!manualMode && eventBase(ev) === dismissedBase) return;  // user closed this one
  dismissedBase = null;
  manualMode = false;
  document.getElementById("quake-recent").classList.add("hidden");   // live takeover: no list
  showQuakeLayout(ev);
  currentQuakeKey = ev.kind + ":" + ev.id + ":" + ev.bulletin;
  // prefer the duration (clock-skew-proof local deadline) over the server epoch
  scheduleHide(ev.holdFor ? Date.now() / 1000 + ev.holdFor : ev.expiresAt);
}

function showQuakeLayout(ev) {
  shownEvent = ev || null;
  if (ev) renderQuake(ev); else renderEmptyQuake();
  overlay.classList.remove("hidden");
  overlay.classList.toggle("is-eew", !!ev && ev.kind === "eew");
  document.getElementById("quake-toggle").classList.add("active");
}

// Idle timer for a manually-opened overlay: return to the dashboard if untouched,
// so a stray tap on the wall tablet doesn't hide the info screen indefinitely.
function armManualIdle() {
  clearTimeout(quakeIdleTimer);
  quakeIdleTimer = setTimeout(closeQuake, MANUAL_IDLE_MS);
}

// Manual button (🗾): open the earthquake layout on demand, or close it.
function toggleQuakeLayout() {
  if (!overlay.classList.contains("hidden")) { closeQuake(); return; }
  dismissedBase = null;               // user wants to look; allow live events again
  manualMode = true;
  clearTimeout(quakeHideTimer);
  clearInterval(quakeTickTimer);
  loadRecentQuakes();                 // on-demand refresh so the browse list is current when opened
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
  armManualIdle();
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
  armManualIdle();   // each interaction resets the auto-close countdown
}

// Close the earthquake screen (✕ or 🗾). Dismissing a live event keeps it closed
// (so the 30s poll won't immediately reopen it) until a new quake or manual reopen.
function closeQuake() {
  if (shownEvent && !manualMode) dismissedBase = eventBase(shownEvent);
  hideQuake();
}

function renderEmptyQuake() {
  const banner = document.getElementById("quake-banner");
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
  clearTimeout(quakeIdleTimer);   // a live event supersedes any manual browse auto-close
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
  clearTimeout(quakeIdleTimer);
  overlay.classList.add("hidden");
  overlay.classList.remove("is-eew");
  document.getElementById("quake-recent").classList.add("hidden");
  document.getElementById("quake-toggle").classList.remove("active");
  currentQuakeKey = null;
  manualMode = false;
  shownEvent = null;
}

// Seed / refresh the recent-quakes list so 🗾 always has something to browse.
// While the 🗾 browser is open the refresh has to repaint, otherwise the
// on-demand fetch that toggleQuakeLayout() fires is fetched and then thrown away.
async function loadRecentQuakes() {
  try {
    const list = await (await fetch("/api/earthquake/recent")).json();
    if (!Array.isArray(list) || !list.length) return;
    const wasShowing = shownEvent && quakeKey(shownEvent);   // keep the user's pick selected
    recentQuakes = list;
    if (!manualMode || overlay.classList.contains("hidden")) return;
    const i = recentQuakes.findIndex((e) => quakeKey(e) === wasShowing);
    renderRecentList();
    document.getElementById("quake-recent").classList.remove("hidden");
    selectRecent(i >= 0 ? i : 0);
  } catch (_) { /* ignore */ }
}

async function pollQuake() {
  try {
    const ev = await (await fetch("/api/earthquake/current")).json();
    // the server's active() already filters expired events by ITS clock — comparing
    // its epoch against the tablet clock here would mis-drop alerts on a skewed tablet
    if (ev && ev.kind) {
      const key = ev.kind + ":" + ev.id + ":" + ev.bulletin;
      if (key !== currentQuakeKey) handleQuake(ev);
    }
  } catch (_) { /* ignore */ }
}

// ---- feed health -----------------------------------------------------------
// Every backend failure path degrades to last-good data, so a dead upstream looks
// exactly like a quiet one from here. Poll /api/health and mark the 🗾 button when
// the live quake feed is down — the one outage worth noticing from across a room.
let quakeFeedState = "ok";   // "ok" | "fallback" (JMA polling) | "down" (no source)
async function pollHealth() {
  const h = await getJson("/api/health", null);
  const q = (h && h.quake) || null;
  quakeFeedState = !q ? "down" : q.connected ? "ok" : q.fallbackActive ? "fallback" : "down";
  const btn = document.getElementById("quake-toggle");
  if (!btn) return;
  btn.classList.toggle("feed-degraded", quakeFeedState === "fallback");
  btn.classList.toggle("feed-down", quakeFeedState === "down");
  btn.title = t(quakeFeedState === "ok" ? "feedOk"
             : quakeFeedState === "fallback" ? "feedFallback" : "feedDown");
  markStalePanels(h);
}

// A panel that never received data sits on "データ取得中…" forever. After the
// backend has told us that feed is actually failing, that placeholder is a lie —
// say so instead. We do NOT fabricate a card with "—" in every slot: an empty
// shape that looks like real data is worse than an honest empty state.
function markStalePanels(health) {
  const down = new Set((health && health.degraded) || []);
  const loading = document.querySelector("#today .loading");
  if (loading && !lastWeather) {
    loading.textContent = t(down.has("weather") ? "noDataError" : "noData");
    loading.classList.toggle("is-error", down.has("weather"));
  }
}

// ---- WebSocket -------------------------------------------------------------
let wsWasConnected = false;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    if (wsWasConnected) loadRecentQuakes();   // refresh the browse list after an outage gap
    wsWasConnected = true;
  };
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "earthquake") handleQuake(m.event);
    } catch (_) { /* ignore */ }
  };
  ws.onclose = () => setTimeout(connectWS, 3000);
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}
