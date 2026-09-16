"use strict";

// 天気: the today card, the weekly strip and the met.no hourly strip.

// ---- weather ---------------------------------------------------------------
// Rebuilding the card and the weekly strip (six large emoji) blanks and re-lays
// them out for a frame; doing that every 10 minutes for a forecast that has not
// changed is a visible blink, and the reflow of this auto-height row re-fits both
// news columns for nothing. Compare the payload (minus the backend's `updated`
// stamp, which changes on every fetch) and skip the DOM work when it is the same.
let lastWeatherKey = "", lastHourlyKey = "";
function weatherKey(data) { return JSON.stringify({ ...data, updated: undefined }); }

async function loadWeather() {
  const want = cityId;   // discard the response if the user switched city mid-flight
  try {
    const data = await (await fetch("/api/weather?city=" + encodeURIComponent(cityId || ""))).json();
    if (want !== cityId) return;
    if (!(data && data.today)) return;
    const key = weatherKey(data);
    lastWeather = data;
    if (key === lastWeatherKey) return;   // same forecast → leave the DOM alone
    renderWeather(data);
    lastWeatherKey = key;
  } catch (_) { /* keep last */ }
}

// Temperatures from the hourly strip that still belong to TODAY (JST). The strip
// carries only an hour-of-day and runs past midnight, so the wrap is where the
// hour stops increasing.
function hourlyTempsUntilMidnight() {
  const out = [];
  let prev = -1;
  for (const h of Array.isArray(lastHourly) ? lastHourly : []) {
    if (h.hour <= prev) break;              // hour went backwards -> past midnight
    prev = h.hour;
    if (h.temp !== null && h.temp !== undefined) out.push(h.temp);
  }
  return out;
}

function renderWeather(data) {
  if (data.city) document.getElementById("city").textContent = data.city;
  const temp = (v) => (v === null || v === undefined ? "--" : v + "°");   // today card keeps the °
  const tempN = (v) => (v === null || v === undefined ? "--" : String(v)); // weekly: no ° (renders inconsistently across systems)

  const today = data.today || {};
  // Last resort only: JMA drops today's high/low from its forecast around 17:00 JST,
  // and the backend's memo of them is empty if it booted after that. Fall back to
  // the hourly strip — but ONLY its remaining-today points. The strip rolls a full
  // 24h forward, so using all of it would put tomorrow's afternoon peak on today's
  // card (observed: 35° at 19:00 on a day whose real high was 34°).
  let tMax = today.tempMax, tMin = today.tempMin;
  if (tMax == null || tMin == null) {
    const hs = hourlyTempsUntilMidnight();
    if (hs.length) {
      if (tMax == null) tMax = Math.max(...hs);
      if (tMin == null) tMin = Math.min(...hs);
    }
  }
  document.getElementById("today").innerHTML =
    `<div class="t-date"><span id="today-date">${formatDateOnly()}</span>` +
    `<span id="today-wd" class="t-wd">${formatWeekday()}</span></div>` +
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
      `<div class="d-temps"><span class="hi">${tempN(d.tempMax)}</span>` +
      `<span class="d-sep"> / </span><span class="lo">${tempN(d.tempMin)}</span></div>` +
      (d.pop !== null && d.pop !== undefined ? `<div class="d-pop">${d.pop}%</div>` : "");
    weekly.appendChild(el);
  });
}

// today's hourly forecast (met.no)
async function loadHourly() {
  const want = cityId;
  try {
    const list = await (await fetch("/api/weather/hourly?city=" + encodeURIComponent(cityId || ""))).json();
    if (want !== cityId) return;
    if (Array.isArray(list) && list.length) {   // empty = backend cold start during outage; keep last-good
      lastHourly = list;
      const key = JSON.stringify(list);
      if (key === lastHourlyKey) return;        // unchanged strip → no DOM churn
      lastHourlyKey = key;
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
    const mm = Number(h.precip);   // coerce: this is the one upstream value that lands in innerHTML
    item.innerHTML =
      `<div class="hour-time">${label}</div>` +
      `<div class="hour-icon">${h.icon || "❓"}</div>` +
      `<div class="hour-temp">${temp}</div>` +
      `<div class="hour-precip">${mm > 0 ? mm + "mm" : "&nbsp;"}</div>`;
    el.appendChild(item);
  });
}
