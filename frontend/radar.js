"use strict";

// 雨雲レーダー (tap the weather panel): a full-screen loop of the past hour of observed rain into the
// forecast (JMA ナウキャスト, then 降水短時間予報) over a dark base map, centred on
// this tablet's city. The backend picks the frames (/api/radar/frames) and proxies
// every tile, so the page stays same-origin under the CSP. Sits BELOW the
// earthquake overlay (style.css): a quake always covers it.

const radarOverlay = document.getElementById("radar-overlay");
const RADAR_FRAME_MS = 700;            // per frame
const RADAR_HOLD_MS = 2000;            // the last frame stays this long before the loop restarts
const RADAR_IDLE_MS = 5 * 60 * 1000;   // a wall tablet must find its own way back to the dashboard
const RADAR_TILE = 256;                // Web-Mercator XYZ tile size, px
const RADAR_RETRY_MS = 3000;           // one delayed retry for a tile that failed to load
const RADAR_STALE_MS = 20 * 60 * 1000; // newest observation older than this → the list stopped updating upstream
let radarData = null;                  // validated /api/radar/frames response while open
let radarLayers = [];                  // one rain layer per frame, same order as radarData.frames
let radarIdx = 0, radarTimer = null, radarIdleTimer = null, radarResizeTimer = null;
let radarSeq = 0;                      // bumped by every open/close: a late response for an old session is dropped
let radarBuiltFor = "";                // "WxH" the layers were laid out for

const radarTimeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
function radarClock(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso || "").slice(11, 16) : radarTimeFmt.format(d);
}

// Fractional Web-Mercator pixel position of lat/lon at zoom z.
function radarWorldPx(lat, lon, z) {
  const size = RADAR_TILE * Math.pow(2, z);
  const s = Math.sin(Math.max(-85.05, Math.min(85.05, lat)) * Math.PI / 180);
  return { x: (lon + 180) / 360 * size, y: (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * size };
}

// One base layer, then one (hidden) rain layer per frame — all requested now, so
// the loop never waits on the network mid-play — plus the city marker: the tiles
// around the city at RADAR_ZOOM, city at the viewport centre, every viewport pixel
// covered.
function buildRadar() {
  const map = document.getElementById("radar-map");
  map.textContent = "";
  radarLayers = [];
  const W = window.innerWidth, H = window.innerHeight;
  radarBuiltFor = W + "x" + H;
  const z = radarData.zoom, n = Math.pow(2, z);
  const c = radarWorldPx(radarData.lat, radarData.lon, z);
  const left = c.x - W / 2, top = c.y - H / 2;            // world px of the viewport's top-left corner
  const x0 = Math.floor(left / RADAR_TILE), y0 = Math.floor(top / RADAR_TILE);
  const cols = Math.ceil((left + W) / RADAR_TILE) - x0;     // only tiles that intersect the viewport
  const rows = Math.ceil((top + H) / RADAR_TILE) - y0;
  const ox = Math.round(x0 * RADAR_TILE - left), oy = Math.round(y0 * RADAR_TILE - top);
  const layer = (cls, url) => {
    const el = document.createElement("div");
    el.className = "radar-layer " + cls;
    el.style.left = ox + "px";
    el.style.top = oy + "px";
    for (let j = 0; j < rows; j++) {
      if (y0 + j < 0 || y0 + j >= n) continue;            // beyond the poles: no such tile
      for (let i = 0; i < cols; i++) {
        const img = document.createElement("img");
        img.alt = "";
        // A tile that failed (502 from the proxy, or 404: not published yet) stays blank,
        // not a broken-image icon — but marked, so a rain frame with holes never passes
        // for a dry sky. A 502 is never cached, so one delayed retry can still fill it.
        img.onerror = () => {
          img.style.visibility = "hidden";
          img.dataset.failed = "1";
          if (radarLayers[radarIdx] === el) showRadarFrame(radarIdx);   // failed while on screen
          if (img.dataset.retried) return;
          img.dataset.retried = "1";
          setTimeout(() => {
            if (img.isConnected) img.src = img.src;      // closed / rebuilt meanwhile: drop it
          }, RADAR_RETRY_MS);
        };
        img.onload = () => {
          if (!img.dataset.failed) return;
          img.style.visibility = "";
          delete img.dataset.failed;
          if (radarLayers[radarIdx] === el) showRadarFrame(radarIdx);
        };
        img.style.left = i * RADAR_TILE + "px";
        img.style.top = j * RADAR_TILE + "px";
        img.src = url(x0 + i, y0 + j);
        el.appendChild(img);
      }
    }
    map.appendChild(el);
    return el;
  };
  layer("radar-base", (x, y) => `/api/radar/base/${z}/${x}/${y}.png`);
  radarData.frames.forEach((f) => radarLayers.push(layer("radar-rain", (x, y) => `${f.tile}/${z}/${x}/${y}.png`)));
  const marker = document.createElement("div");
  marker.className = "radar-marker";
  marker.style.left = (c.x - left) + "px";
  marker.style.top = (c.y - top) + "px";
  map.appendChild(marker);
}

function showRadarFrame(i) {
  if (radarLayers[radarIdx]) radarLayers[radarIdx].classList.remove("on");
  radarIdx = i;
  radarLayers[i].classList.add("on");
  const f = radarData.frames[i], last = radarLayers.length - 1;
  document.getElementById("radar-time").textContent = radarClock(f.time);
  const kind = document.getElementById("radar-kind");
  const missing = !!radarLayers[i].querySelector("img[data-failed]");
  kind.textContent = missing ? t("noDataError") : t(f.kind === "obs" ? "radarObs" : "radarFcst");
  kind.classList.toggle("fcst", !missing && f.kind !== "obs");
  kind.classList.toggle("err", missing);
  document.getElementById("radar-fill").style.width = (last ? i / last * 100 : 100) + "%";
}

// The one timer chain: every open/close/step clears it before re-arming, so it
// can never run twice.
function scheduleRadarStep() {
  clearTimeout(radarTimer);
  const last = radarLayers.length - 1;
  radarTimer = last > 0 ? setTimeout(radarStep, radarIdx === last ? RADAR_HOLD_MS : RADAR_FRAME_MS) : null;
}
function radarStep() {
  showRadarFrame((radarIdx + 1) % radarLayers.length);
  scheduleRadarStep();
}

function setRadarStatus(key) {
  const el = document.getElementById("radar-status");
  el.textContent = key ? t(key) : "";
  el.classList.toggle("hidden", !key);
  document.getElementById("radar-play").classList.toggle("hidden", !!key);
}

// ✕ sits exactly where 🗾 is, like the quake overlay's (same technique as alignQuakeClose in quake.js).
function alignRadarClose() {
  if (radarOverlay.classList.contains("hidden")) return;
  const tb = document.getElementById("quake-toggle").getBoundingClientRect();
  if (!tb.width) return;                           // not laid out: keep the CSS position
  const btn = document.getElementById("radar-close");
  const r = btn.offsetWidth / 2;
  btn.style.top = Math.max(0, tb.top + tb.height / 2 - r) + "px";
  btn.style.right = Math.max(0, window.innerWidth - (tb.left + tb.width / 2) - r) + "px";
}

// Stop everything and drop every tile <img>: a closed radar holds no timers and no images.
function resetRadar() {
  radarSeq++;
  clearTimeout(radarTimer); clearTimeout(radarIdleTimer); clearTimeout(radarResizeTimer);
  radarTimer = radarIdleTimer = radarResizeTimer = null;
  document.getElementById("radar-map").textContent = "";
  document.getElementById("radar-stale").classList.add("hidden");
  radarLayers = []; radarData = null; radarIdx = 0; radarBuiltFor = "";
}

async function openRadar() {
  resetRadar();
  const seq = radarSeq;
  radarOverlay.classList.remove("hidden");
  alignRadarClose();
  setRadarStatus("noData");
  radarIdleTimer = setTimeout(closeRadar, RADAR_IDLE_MS);
  const d = await getJson("/api/radar/frames?city=" + encodeURIComponent(cityId || ""), null);
  if (seq !== radarSeq) return;                    // closed (or reopened) while the request was out
  // only our own tile proxy ever becomes an <img src>
  const frames = d && Array.isArray(d.frames)
    ? d.frames.filter((f) => f && typeof f.tile === "string" && f.tile.startsWith("/api/radar/tile/")) : [];
  if (!frames.length || !Number.isInteger(d.zoom) || !Number.isFinite(d.lat) || !Number.isFinite(d.lon)) {
    setRadarStatus("noDataError");
    return;
  }
  radarData = { zoom: d.zoom, lat: d.lat, lon: d.lon, frames };
  buildRadar();
  const last = frames.length - 1, now = Math.min(Math.max(Number(d.now) || 0, 0), last);
  document.getElementById("radar-now").style.left = (last ? now / last * 100 : 100) + "%";
  // The backend serves a last-good list for a while; say so once it is clearly old.
  // The overlay hides the dashboard clock, so HH:MM alone cannot tell the viewer.
  const newest = Date.parse(frames[now].time);
  document.getElementById("radar-stale").classList.toggle("hidden",
    !(Number.isFinite(newest) && Date.now() - newest > RADAR_STALE_MS));
  setRadarStatus(null);
  showRadarFrame(0);
  scheduleRadarStep();
}

function closeRadar() {
  resetRadar();
  radarOverlay.classList.add("hidden");
}

// New viewport (rotation, split screen): re-lay the tiles so the city stays centred
// and the screen stays covered. Debounced — a rotation fires several resizes.
window.addEventListener("resize", () => {
  if (radarOverlay.classList.contains("hidden")) return;
  alignRadarClose();
  clearTimeout(radarResizeTimer);
  radarResizeTimer = setTimeout(() => {
    if (!radarData || radarBuiltFor === window.innerWidth + "x" + window.innerHeight) return;
    const i = radarIdx;
    buildRadar();
    showRadarFrame(i);
  }, 200);
});
