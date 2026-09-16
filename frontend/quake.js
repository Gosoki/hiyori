"use strict";

// The earthquake takeover screen: live events over the WebSocket, the 🗾 browse
// list, the per-device 震度 threshold, and the feed-health badge.

// ---- earthquake ------------------------------------------------------------
const overlay = document.getElementById("quake-overlay");
let quakeHideTimer = null, quakeTickTimer = null, quakeIdleTimer = null, currentQuakeKey = null;
const MANUAL_IDLE_MS = 60 * 1000;   // auto-return a manually-opened 🗾 overlay to the dashboard if left untouched
let manualMode = false, shownEvent = null, dismissedBase = null, dismissedRank = -1;
let recentQuakes = [];   // last N 地震情報 for the 🗾 browse list
let recentCount = 5;     // N, from /api/config (EARTHQUAKE_RECENT_COUNT) — kept in step with the backend

// Key by the quake itself, not the bulletin — dismissing one bulletin then also
// dismisses the same quake's follow-up reports (第2報, 詳報…). A 地震情報 is keyed by
// its origin time; an EEW by its eventId, because JMA revises an EEW's origin time
// between serials and a キャンセル報 keyed by time would never find the 第1報 it
// retracts. Mirrors quake_key / _event_base in backend/earthquake.py.
const quakeKey = (ev) => (ev && (ev.kind === "eew" ? (ev.id || ev.originTime) : (ev.originTime || ev.id))) || "";
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

// A tsunami WARNING must not depend on how hard the floor shook here: the classic
// shape is moderate local 震度 with a catastrophic wave (2011: 震度4 in much of
// Tohoku's coast under a 大津波警報). Warning-class forecasts bypass the per-device
// threshold; an advisory (注意報) does not — it still shows in the 🗾 list.
const TSUNAMI_TAKEOVER = new Set(["Warning", "MajorWarning"]);
function mustTakeOver(ev) {
  return TSUNAMI_TAKEOVER.has(ev.tsunami) || quakeScale(ev) >= (minScale || 0);
}
// How "serious" a bulletin is, for the dismiss rule: the raw 震度 (-1 unknown) plus
// a big step for a tsunami warning. A dismissed event stays closed for follow-ups
// of the same or lower rank only — an EEW whose 第2報 escalates 震度3 → 6弱, or a
// 地震情報 that gains a 津波警報, is new information and reopens the screen.
function severityRank(ev) {
  const s = Number(ev && ev.maxScale);
  return (Number.isFinite(s) && s >= 0 ? s : -1) + (TSUNAMI_TAKEOVER.has(ev && ev.tsunami) ? 1000 : 0);
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
    if (manualMode && !overlay.classList.contains("hidden")) {
      // someone is looking at the list right now: a new sub-threshold quake must
      // appear in it, not sit in memory until the 15-minute refresh
      const i = recentQuakes.findIndex((e) => shownEvent && quakeKey(e) === quakeKey(shownEvent));
      renderRecentList();
      document.querySelectorAll("#quake-recent .rq-item").forEach((el, k) =>
        el.classList.toggle("active", k === i));
    }
  }
  if (!mustTakeOver(ev)) return;                   // below this device's 震度 threshold → 🗾 list only
  if (!manualMode && eventBase(ev) === dismissedBase && severityRank(ev) <= dismissedRank) return;  // user closed this one
  const key = ev.kind + ":" + ev.id + ":" + ev.bulletin;
  if (key === currentQuakeKey && !overlay.classList.contains("hidden")) return;  // this very bulletin is already up (replay after a reconnect): keep its countdown
  dismissedBase = null;
  dismissedRank = -1;
  manualMode = false;
  document.getElementById("quake-recent").classList.add("hidden");   // live takeover: no list
  showQuakeLayout(ev);
  currentQuakeKey = key;
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
  if (shownEvent && !manualMode) { dismissedBase = eventBase(shownEvent); dismissedRank = severityRank(shownEvent); }
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

  // tsunami row (earthquake reports only, when info exists). Own keys only: an
  // upstream value like "constructor" would otherwise print a function's source
  const tsuRow = document.getElementById("q-tsunami-row");
  const tsuMap = t("tsunamiMap");
  const tsuLabel = !isEew && Object.prototype.hasOwnProperty.call(tsuMap, ev.tsunami) ? (tsuMap[ev.tsunami] || "") : "";
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
  if (!h) {
    // Backend unreachable. Say so on the badge, but leave every panel's state
    // alone: the "データを取得できません" placeholder set while the backend was
    // still answering must not revert to "取得中…" now that it is gone.
    quakeFeedState = "down";
    setFeedBadge();
    return;
  }
  const q = h.quake || null;
  quakeFeedState = !q ? "down" : q.connected ? "ok" : q.fallbackActive ? "fallback" : "down";
  setFeedBadge();
  markStalePanels(h);
}

// The badge reflects the worst of two links: the backend's P2P feed, and THIS
// tablet's socket to the backend. A tablet whose WebSocket is down gets no push
// at all — for an EEW that is the whole product — even while the backend is fine.
function setFeedBadge() {
  const btn = document.getElementById("quake-toggle");
  if (!btn) return;
  const state = quakeFeedState === "ok" && !wsOpen ? "nolink" : quakeFeedState;
  btn.classList.toggle("feed-degraded", state === "fallback");
  btn.classList.toggle("feed-down", state === "down" || state === "nolink");
  btn.title = t(state === "ok" ? "feedOk" : state === "fallback" ? "feedFallback"
             : state === "nolink" ? "feedNoLink" : "feedDown");
}

// A panel that never received data sits on "データ取得中…" forever. After the
// backend has told us that feed is actually failing, that placeholder is a lie —
// say so instead. We do NOT fabricate a card with "—" in every slot: an empty
// shape that looks like real data is worse than an honest empty state.
//
// A panel that HAS data but whose feed stopped updating hours ago is the other
// lie: it looks exactly like a quiet day. /api/health judges that age (`stale`,
// three missed refreshes); here the panel is dimmed and labelled. The data stays
// — last-good is the whole design — but the viewer can tell it is old.
const STALE_PANELS = {            // feed name in /api/health → the panel it fills
  "weather":    () => document.getElementById("weather"),
  "news.ai":    () => document.getElementById("news-ai").closest(".news-col"),
  "news.japan": () => document.getElementById("news-japan").closest(".news-col"),
  "fx":         () => document.getElementById("fx-body").closest(".info-cell"),
  "holiday":    () => document.getElementById("holiday-cell"),
  "anime":      () => document.getElementById("anime-body").closest(".info-panel"),
};
function markStalePanels(health) {
  const down = new Set((health && health.degraded) || []);
  const loading = document.querySelector("#today .loading");
  if (loading && !lastWeather) {
    loading.textContent = t(down.has("weather") ? "noDataError" : "noData");
    loading.classList.toggle("is-error", down.has("weather"));
  }
  const feeds = (health && health.feeds) || {};
  Object.keys(STALE_PANELS).forEach((name) => {
    const el = STALE_PANELS[name]();
    if (!el) return;
    const stale = !!(feeds[name] && feeds[name].stale);
    el.classList.toggle("is-stale", stale);
    if (stale) el.setAttribute("data-stale", t("stale")); else el.removeAttribute("data-stale");
  });
}

// ---- WebSocket -------------------------------------------------------------
// The server sends {"type":"ping"} every 25 s. A browser cannot notice a half-open
// socket on its own (Wi-Fi blip, AP reboot: readyState stays OPEN, nothing ever
// arrives — including the next EEW), so the watchdog below treats three missed
// pings as dead and reconnects; the server replays any active event on connect.
let ws = null, wsOpen = false, wsLastMsg = 0, wsWasConnected = false;
const WS_STALE_MS = 3 * 25 * 1000;
// Reconnect quickly, but not at a fixed 3 s forever: a weekend backend outage
// would be ~60k attempts per tablet. Doubling up to 20 s keeps the worst-case
// delay small (the 60 s poll and the replay-on-connect cover the gap anyway).
const WS_RETRY_MIN_MS = 3000, WS_RETRY_MAX_MS = 20000;
let wsRetryMs = WS_RETRY_MIN_MS;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${proto}://${location.host}/ws`);
  ws = sock;
  wsLastMsg = Date.now();
  sock.onopen = () => {
    wsLastMsg = Date.now();
    wsOpen = true;
    wsRetryMs = WS_RETRY_MIN_MS;
    setFeedBadge();
    if (wsWasConnected) loadRecentQuakes();   // refresh the browse list after an outage gap
    wsWasConnected = true;
  };
  sock.onmessage = (e) => {
    wsLastMsg = Date.now();
    try {
      const m = JSON.parse(e.data);
      if (m.type === "earthquake") handleQuake(m.event);
      else if (m.type === "alerts") loadNews();   // severe alerts changed → refresh the pinned rows now
    } catch (_) { /* ignore */ }
  };
  sock.onclose = () => {
    if (ws !== sock) return;                       // superseded by the watchdog; it already reconnected
    wsOpen = false;
    setFeedBadge();
    setTimeout(connectWS, wsRetryMs);
    wsRetryMs = Math.min(wsRetryMs * 2, WS_RETRY_MAX_MS);
  };
  sock.onerror = () => { try { sock.close(); } catch (_) {} };
}
function watchWS() {
  if (!ws || ws.readyState !== 1) return;          // connecting/closing: onclose handles it
  if (Date.now() - wsLastMsg < WS_STALE_MS) return;
  const dead = ws;                                 // silent for 3 pings: assume half-open
  ws = null;
  wsOpen = false;
  dead.onclose = null; dead.onmessage = null; dead.onerror = null;
  try { dead.close(); } catch (_) { /* already gone */ }
  connectWS();                                     // don't wait for the close handshake
}
