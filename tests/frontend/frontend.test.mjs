// Drives the real frontend inside jsdom against canned API responses.
//
// Offline and deterministic on purpose: the backend contract is covered by the
// Python suite, so what matters here is what the panels DO with a response —
// especially the earthquake takeover rules, where getting it wrong is the one
// failure with consequences.
//
//   node --test "tests/frontend/*.test.mjs"      (from the repo root)
//
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FE = path.join(HERE, "..", "..", "frontend");
const require = createRequire(import.meta.url);

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch {
  console.error("jsdom is missing — run `npm install` in tests/frontend/");
  process.exit(1);
}

// ---------------------------------------------------------------- canned API
const API = {
  "/api/config": { language: "ja", city: "tokyo", aiSource: "cn", minScale: 30, recentCount: 5 },
  "/api/cities": [{ id: "tokyo", name: "東京" }, { id: "osaka", name: "大阪" }],
  "/api/ai-sources": [{ id: "cn", name: "中文", lang: "zh" }, { id: "jp", name: "日本語", lang: "ja" }],
  "/api/weather": {
    city: "東京", updated: "2026-08-05T18:00+09:00",
    today: { code: "200", icon: "☁️", text: "くもり", pop: 20, tempMax: 34, tempMin: 26 },
    weekly: [
      { date: "2026-08-06", md: "8/6", weekday: "木", icon: "⛅", text: "曇後晴", pop: 40, tempMax: 32, tempMin: 24 },
      { date: "2026-08-07", md: "8/7", weekday: "金", icon: "☀️", text: "晴", pop: 10, tempMax: 33, tempMin: 25 },
      { date: "2026-08-08", md: "8/8", weekday: "土", icon: "☀️", text: "晴", pop: 0, tempMax: 34, tempMin: 26 },
      { date: "2026-08-09", md: "8/9", weekday: "日", icon: "🌧️", text: "雨", pop: 70, tempMax: 30, tempMin: 25 },
      { date: "2026-08-10", md: "8/10", weekday: "月", icon: "⛅", text: "曇", pop: 30, tempMax: 31, tempMin: 25 },
      { date: "2026-08-11", md: "8/11", weekday: "火", icon: "☀️", text: "晴", pop: 10, tempMax: 33, tempMin: 26 },
    ],
  },
  "/api/weather/hourly": [
    { hour: 18, temp: 30, icon: "☀️", text: "快晴", precip: 0 },
    { hour: 20, temp: 28, icon: "☁️", text: "曇", precip: 0 },
    { hour: 22, temp: 27, icon: "☁️", text: "曇", precip: 0 },
    { hour: 0, temp: 26, icon: "☁️", text: "曇", precip: 0.2 },
  ],
  "/api/news": {
    ai: [{ title: "AI headline", link: "a", source: "量子位" }],
    japan: [{ title: "主要ニュース", link: "b", source: "産経" }],
  },
  "/api/fx": { base: "CNY", quote: "JPY", rate: 23.334, baseLabel: "元", quoteLabel: "円" },
  "/api/anime": [{ time: "21:00", title: "テスト番組" }, { time: "26:00", title: "深夜番組" }],
  "/api/holiday": [{ date: "2099-08-11", name: "山の日" }],
  "/api/earthquake/recent": [],
  "/api/earthquake/current": {},
  "/api/health": { status: "ok", degraded: [], quake: { connected: true, fallbackActive: false } },
  "/api/radar/frames": {
    zoom: 8, lat: 35.69, lon: 139.69, now: 2,
    frames: [
      { time: "2026-11-22T20:40:00+09:00", kind: "obs", tile: "/api/radar/tile/nowc/none/20261122114000/20261122114000" },
      { time: "2026-11-22T20:50:00+09:00", kind: "obs", tile: "/api/radar/tile/nowc/none/20261122115000/20261122115000" },
      { time: "2026-11-22T21:00:00+09:00", kind: "obs", tile: "/api/radar/tile/nowc/none/20261122120000/20261122120000" },
      { time: "2026-11-22T21:10:00+09:00", kind: "fcst", tile: "/api/radar/tile/nowc/none/20261122120000/20261122121000" },
      { time: "2026-11-23T00:00:00+09:00", kind: "fcst", tile: "/api/radar/tile/rasrf/immed/20261122115000/20261122150000" },
    ],
  },
};

// `t` is node:test's context — used to close the window afterwards. app.js installs
// several setInterval timers at boot, so a window left open keeps the whole test
// process alive forever.
// `setup(w)` runs before any script, e.g. to break localStorage.
function boot(t, { api = {}, setup } = {}) {
  const routes = { ...API, ...api };
  const html = fs.readFileSync(path.join(FE, "index.html"), "utf8");
  const dom = new JSDOM(html, { url: "http://test/", runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window;
  const errs = [];
  w.onerror = (m) => errs.push(String(m));
  w.addEventListener("error", (e) => errs.push(String(e.error || e.message)));

  w.fetch = async (url) => {
    const key = String(url).split("?")[0].replace(/^http:\/\/test/, "");
    if (!(key in routes)) return { ok: false, json: async () => { throw new Error("404 " + key); },
                                   text: async () => { throw new Error("404 " + key); } };
    const body = routes[key];
    return { ok: true, json: async () => body, text: async () => JSON.stringify(body) };
  };
  // opens on the next tick, like a real socket — the 🗾 badge treats "no socket" as a fault
  w.WebSocket = class {
    constructor() { this.readyState = 0; setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen(); }, 0); }
    close() { this.readyState = 3; }
  };
  w.ResizeObserver = class { observe() {} disconnect() {} };
  if (setup) setup(w);

  // index.html's own order is the contract; read it rather than hard-coding a list
  const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);
  assert.ok(scripts.length >= 6, "index.html script list looks wrong: " + scripts);
  for (const f of scripts) {
    const el = w.document.createElement("script");
    el.textContent = fs.readFileSync(path.join(FE, f), "utf8");
    w.document.body.appendChild(el);
  }
  // the real map parses 600 KB of geojson into SVG — minutes under jsdom
  w.eval('window.QuakeMap = { init: function () {}, paint: function () {} };');
  t.after(() => { try { w.close(); } catch { /* already gone */ } });
  return { w, errs, $: (id) => w.document.getElementById(id), ev: (src) => w.eval(src),
           txt: (id) => (w.document.getElementById(id) || {}).textContent || "" };
}

const settle = () => new Promise((r) => setTimeout(r, 250));

// ------------------------------------------------------------------- render
test("boots and renders every panel", async (t) => {
  const { errs, $, txt, ev } = boot(t);
  await settle();
  assert.deepEqual(errs, [], "script errors during boot");
  assert.equal(txt("city"), "東京");
  assert.match(txt("today"), /34°/);
  assert.equal($("weekly").children.length, 6);
  assert.equal($("hourly").children.length, 4);
  assert.equal($("news-ai").children.length, 1);
  assert.equal($("news-japan").children.length, 1);
  assert.match(txt("fx-body"), /=/);
  assert.match(txt("holiday-line"), /山の日/);
  assert.match(txt("anime-body"), /テスト番組/);
  assert.equal(ev("recentCount"), 5, "recentCount should come from /api/config");
});

test("a failing /api/config still boots with defaults", async (t) => {
  const { errs, txt } = boot(t, { api: { "/api/config": undefined } });
  await settle();
  assert.deepEqual(errs, [], "a missing config blew up the boot");
  assert.equal(txt("city"), "東京", "weather still rendered");
});

test("hourly fallback stops at the midnight wrap", async (t) => {
  // the strip rolls a full 24h forward; using all of it put TOMORROW's peak on
  // today's card (observed: 35° at 19:00 on a day whose real high was 34°)
  const { ev } = boot(t);
  await settle();
  ev("lastHourly = [{hour:20,temp:28},{hour:22,temp:27},{hour:0,temp:26},{hour:14,temp:35}];");
  assert.equal(ev("hourlyTempsUntilMidnight().join(',')"), "28,27");
});

test("today card falls back to hourly only when JMA has no high/low", async (t) => {
  const noTemps = JSON.parse(JSON.stringify(API["/api/weather"]));
  noTemps.today.tempMax = null;
  noTemps.today.tempMin = null;
  const { ev, txt } = boot(t, { api: { "/api/weather": noTemps } });
  await settle();
  ev("lastHourly = [{hour:20,temp:28},{hour:22,temp:27},{hour:0,temp:40}];");
  ev("renderWeather(lastWeather)");
  assert.match(txt("today"), /28°/);
  assert.ok(!txt("today").includes("40°"), "used a point from after midnight");
});

// ---------------------------------------------------------------------- XSS
test("hostile upstream strings render as text, never as markup", async (t) => {
  const { w, ev, txt } = boot(t);
  await settle();
  const EVIL = '<img src=x onerror=alert(1)>"\'&';
  const J = JSON.stringify(EVIL);
  ev(`renderWeather({city:${J},today:{icon:"x",text:${J},pop:5,tempMax:1,tempMin:0},weekly:[{date:"2026-08-06",icon:"x",tempMax:1,tempMin:2,pop:3}]})`);
  ev(`renderHoliday([{date:"2099-01-01",name:${J}}])`);
  ev(`lastAnime=[{time:"01:00",title:${J}}]; renderAnime(lastAnime)`);
  ev(`fillNewsList("news-ai",[{title:${J},source:${J},link:"x"}],"ja")`);
  ev(`renderHourly([{hour:1,temp:1,icon:"x",text:"y",precip:${J}}])`);
  assert.equal(w.document.querySelectorAll("img, iframe, [onerror]").length, 0, "markup was injected");
  assert.ok(w.document.body.textContent.includes("<img src=x"), "payload should survive as literal text");
  assert.ok(!txt("hourly").includes("mm"), "non-numeric precipitation rendered");
});

// -------------------------------------------------------------- earthquake
const quake = (o = {}) => Object.assign({
  kind: "quake", id: "1", bulletin: "r", originTime: "2026/08/05 10:00:00",
  hypocenter: { name: "東京湾", depth: 30, magnitude: 6.1, latitude: 35.5, longitude: 139.8 },
  maxScale: 50, maxIntensity: "5強", tsunami: "None",
  regions: [{ name: "東京都", scale: 50, label: "5強" }], cancelled: false, holdFor: 90,
}, o);

test("a quake takes over the screen and shows its detail", async (t) => {
  const { ev, $, txt } = boot(t);
  await settle();
  ev(`handleQuake(${JSON.stringify(quake())})`);
  assert.ok(!$("quake-overlay").classList.contains("hidden"));
  assert.equal(txt("quake-badge"), "5強");
  assert.equal(txt("q-epicenter"), "東京湾");
  assert.ok(!$("q-tsunami-row").classList.contains("hidden"));
  assert.ok($("quake-overlay").classList.contains("is-live"), "a system-shown 地震情報 gets the caution-tape frame too");
});

test("dismissing a quake keeps its follow-up bulletins closed, but not a new quake", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  ev(`handleQuake(${JSON.stringify(quake())})`);
  ev("closeQuake()");
  assert.ok(hidden());
  ev(`handleQuake(${JSON.stringify(quake({ bulletin: "r2" }))})`);
  assert.ok(hidden(), "a follow-up report of a dismissed quake reopened the screen");
  ev(`handleQuake(${JSON.stringify(quake({ id: "2", originTime: "2026/08/05 12:00:00" }))})`);
  assert.ok(!hidden(), "a genuinely new quake must still take over");
});

test("the per-device 震度 threshold gates the takeover but not the 🗾 list", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  ev("hideQuake(); dismissedBase=null; minScale=45;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "3", originTime: "2026/08/05 13:00:00", maxScale: 30, maxIntensity: "3" }))})`);
  assert.ok($("quake-overlay").classList.contains("hidden"), "a sub-threshold quake grabbed the screen");
  assert.equal(ev("recentQuakes[0].id"), "3", "it should still be browsable");
});

test("unknown intensity: an EEW fails safe, a 551 does not", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  ev("hideQuake(); dismissedBase=null; minScale=45;");
  // every second counts for an EEW — show it even though the intensity is unknown
  ev(`handleQuake(${JSON.stringify(quake({ kind: "eew", id: "4", originTime: "2026/08/05 14:00:00", maxScale: -1, maxIntensity: "" }))})`);
  assert.ok(!hidden());
  assert.ok($("quake-overlay").classList.contains("is-eew"));
  // 震源に関する情報 carries no intensity; it must not bypass the threshold
  ev("hideQuake(); dismissedBase=null;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "5", originTime: "2026/08/05 15:00:00", maxScale: -1, maxIntensity: "" }))})`);
  assert.ok(hidden(), "a 551 with no intensity bypassed the threshold");
});

test("a cancellation closes only the event it refers to", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  ev("hideQuake(); dismissedBase=null; minScale=30;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "6", originTime: "2026/08/05 16:00:00" }))})`);
  ev(`handleQuake(${JSON.stringify(quake({ id: "7", originTime: "2026/08/05 17:00:00", cancelled: true }))})`);
  assert.ok(!hidden(), "an unrelated cancellation closed the takeover");
  ev(`handleQuake(${JSON.stringify(quake({ id: "6", originTime: "2026/08/05 16:00:00", cancelled: true }))})`);
  assert.ok(hidden(), "the matching cancellation should have closed it");
});

test("malformed events never throw", async (t) => {
  const { ev, errs } = boot(t);
  await settle();
  const before = errs.length;
  for (const bad of ["null", "undefined", "{}", '{"kind":"quake"}', '{"kind":"eew","regions":null}',
                     '{"kind":"quake","hypocenter":null,"regions":[{"name":null}],"maxScale":"x"}',
                     '{"kind":"quake","tsunami":"__proto__"}', '{"kind":"quake","tsunami":"constructor"}']) {
    ev(`handleQuake(${bad})`);
  }
  assert.equal(errs.length, before, errs.slice(before).join(" | "));
});

// --------------------------------------------------------------- settings
test("the threshold in force always has a button, even outside the presets", async (t) => {
  // EARTHQUAKE_MIN_SCALE accepts any JMA code; a value that isn't one of the three
  // presets used to leave the panel with nothing highlighted while still applying
  const cfg = { ...API["/api/config"], minScale: 55 };   // 6弱, not a preset
  const { w, ev, $ } = boot(t, { api: { "/api/config": cfg } });
  await settle();
  assert.equal(ev("minScale"), 55);
  const opts = ev("thresholdOptions().map(o => o.scale).join(',')");
  assert.equal(opts, "30,40,45,55", "the configured value was not offered");
  const active = [...$("threshold-options").children].filter((b) => b.classList.contains("active"));
  assert.equal(active.length, 1, "no button reflects the threshold actually in force");
  assert.equal(active[0].textContent, "6弱+");
  w.localStorage.clear();
});

test("a preset threshold produces exactly the three presets", async (t) => {
  const { ev } = boot(t);
  await settle();
  assert.equal(ev("thresholdOptions().map(o => o.scale).join(',')"), "30,40,45");
});

// ------------------------------------------------------------- feed health
test("the 🗾 button reports a degraded quake feed", async (t) => {
  const down = { status: "degraded", degraded: ["earthquake.live"],
                 quake: { connected: false, fallbackActive: true } };
  const { w, $ } = boot(t, { api: { "/api/health": down } });
  await settle();
  const btn = $("quake-toggle");
  assert.ok(btn.classList.contains("feed-degraded"), "no badge while running on the fallback");
  assert.ok(!btn.classList.contains("feed-down"));
  assert.match(btn.title, /P2P/);
  // and clears again when P2P returns
  w.eval('quakeFeedState="ok"');
});

test("no quake source at all is flagged more severely", async (t) => {
  const dead = { status: "degraded", quake: { connected: false, fallbackActive: false } };
  const { $ } = boot(t, { api: { "/api/health": dead } });
  await settle();
  assert.ok($("quake-toggle").classList.contains("feed-down"));
});

test("a healthy feed carries no badge", async (t) => {
  const { $ } = boot(t);
  await settle();
  const btn = $("quake-toggle");
  assert.ok(!btn.classList.contains("feed-degraded") && !btn.classList.contains("feed-down"));
});

test("the loading placeholder stops claiming to be loading once a feed is confirmed down", async (t) => {
  // we do NOT fabricate a card full of "—": an empty shape that looks like real
  // data is worse than an honest empty state. But "データ取得中…" forever is a lie.
  const broken = { status: "degraded", degraded: ["weather"], quake: { connected: true, fallbackActive: false } };
  const { $ } = boot(t, { api: { "/api/weather": undefined, "/api/health": broken } });
  await settle();
  const el = $("today").querySelector(".loading");
  assert.ok(el, "the placeholder should still be there — no weather data arrived");
  assert.ok(el.classList.contains("is-error"), "placeholder never switched to the error state");
  assert.ok(!el.textContent.includes("取得中"), el.textContent);
});

test("a slow first load still reads as loading, not as an error", async (t) => {
  const { $ } = boot(t, { api: { "/api/weather": undefined } });   // health says everything is fine
  await settle();
  const el = $("today").querySelector(".loading");
  assert.ok(el && !el.classList.contains("is-error"), "a healthy slow load was reported as broken");
});

test("a feed that stopped updating dims and labels its panel", async (t) => {
  // the data on the panel is still the last good — by design — but a viewer must be
  // able to tell "quiet day" from "this column stopped hours ago"
  const stale = { status: "ok", degraded: [], quake: { connected: true, fallbackActive: false },
                  feeds: { weather: { ok: true, stale: true }, fx: { ok: true, stale: false } } };
  const { $, ev } = boot(t, { api: { "/api/health": stale } });
  await settle();
  assert.ok($("weather").classList.contains("is-stale"), "stale weather panel not marked");
  assert.equal($("weather").getAttribute("data-stale"), "更新停止");
  assert.ok(!$("holiday-cell").classList.contains("is-stale"), "a fresh panel was marked");
  ev("markStalePanels({ feeds: { weather: { stale: false } } })");
  assert.ok(!$("weather").classList.contains("is-stale"), "the mark did not clear");
  assert.equal($("weather").getAttribute("data-stale"), null);
});

test("a tsunami warning takes the screen over even below the 震度 threshold", async (t) => {
  // the 2011 shape: moderate local shaking, catastrophic wave. Warning-class
  // forecasts bypass the per-device threshold; an advisory (注意報) does not.
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  ev("hideQuake(); dismissedBase=null; minScale=45;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "t1", originTime: "2026/08/05 20:00:00", maxScale: 40, maxIntensity: "4", tsunami: "Watch" }))})`);
  assert.ok(hidden(), "an advisory alone should not bypass the threshold");
  ev(`handleQuake(${JSON.stringify(quake({ id: "t2", originTime: "2026/08/05 20:01:00", maxScale: 40, maxIntensity: "4", tsunami: "MajorWarning" }))})`);
  assert.ok(!hidden(), "大津波警報 must take over regardless of 震度");
  assert.match(txtOf($("q-tsunami")), /大津波警報/);
});
const txtOf = (el) => (el && el.textContent) || "";

test("dismissing an early bulletin does not silence an escalation of the same event", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  ev("hideQuake(); dismissedBase=null; minScale=30;");
  const eew = (o) => quake({ kind: "eew", id: "EV1", originTime: "2026/08/05 21:00:00", tsunami: "Unknown", ...o });
  ev(`handleQuake(${JSON.stringify(eew({ bulletin: "1", maxScale: 30, maxIntensity: "3" }))})`);
  ev("closeQuake()");
  assert.ok(hidden());
  ev(`handleQuake(${JSON.stringify(eew({ bulletin: "2", maxScale: 30, maxIntensity: "3", originTime: "2026/08/05 20:59:58" }))})`);
  assert.ok(hidden(), "same intensity (even with JMA's revised origin time) must stay dismissed");
  ev(`handleQuake(${JSON.stringify(eew({ bulletin: "3", maxScale: 55, maxIntensity: "6弱" }))})`);
  assert.ok(!hidden(), "第3報 escalating to 6弱 must reopen the screen");
  // and a 地震情報 that gains a tsunami warning reopens too
  ev("hideQuake(); dismissedBase=null;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "q9", originTime: "2026/08/05 22:00:00", maxScale: 40, tsunami: "None" }))})`);
  ev("closeQuake()");
  ev(`handleQuake(${JSON.stringify(quake({ id: "q9", bulletin: "r2", originTime: "2026/08/05 22:00:00", maxScale: 40, tsunami: "Warning" }))})`);
  assert.ok(!hidden(), "a follow-up adding 津波警報 must reopen");
});

test("a 予報 is not worded as a warning, and P2P's 警報 of the same serial replaces it", async (t) => {
  const { ev, txt } = boot(t);
  await settle();
  ev("hideQuake(); dismissedBase=null; minScale=30;");
  const eew = (o) => quake({ kind: "eew", id: "EV7", originTime: "2026/08/05 21:00:00", tsunami: "Unknown", ...o });
  ev(`handleQuake(${JSON.stringify(eew({ bulletin: "3予報", warn: false, maxScale: 40, maxIntensity: "4", regions: [] }))})`);
  assert.equal(txt("quake-sub"), ev('t("eewForecastSub")'));
  ev(`handleQuake(${JSON.stringify(eew({ bulletin: "3", warn: true, maxScale: 45, maxIntensity: "5弱" }))})`);
  assert.equal(txt("quake-sub"), ev('t("eewSub")'), "the 警報 was skipped as a repeat of the 予報");
});

test("a 警報 always shows, outranks a 予報, and a 予報 never covers it", async (t) => {
  const { ev, $ } = boot(t);
  await settle();
  const hidden = () => $("quake-overlay").classList.contains("hidden");
  const eew = (o) => quake({ kind: "eew", originTime: "2026/08/05 21:00:00", tsunami: "Unknown", ...o });
  ev("hideQuake(); dismissedBase=null; minScale=45;");
  // a 予報 below this tablet's threshold stays off; a 警報 shows whatever the threshold
  ev(`handleQuake(${JSON.stringify(eew({ id: "A", bulletin: "1予報", warn: false, maxScale: 40, maxIntensity: "4" }))})`);
  assert.ok(hidden());
  ev(`handleQuake(${JSON.stringify(eew({ id: "A", bulletin: "2", warn: true, maxScale: 40, maxIntensity: "4" }))})`);
  assert.ok(!hidden(), "a 警報 must show regardless of the threshold");
  assert.ok($("quake-overlay").classList.contains("is-live"), "no caution-tape frame");
  // another quake's 予報 must not cover the live 警報
  ev("minScale=30;");
  ev(`handleQuake(${JSON.stringify(eew({ id: "B", bulletin: "1予報", warn: false, maxScale: 40, maxIntensity: "4" }))})`);
  assert.equal(ev("shownEvent.id"), "A", "a 予報 covered a 警報");
  // a dismissed 予報 does not hold back its quake's 警報, even one of unknown intensity
  ev("hideQuake(); dismissedBase=null;");
  ev(`handleQuake(${JSON.stringify(eew({ id: "C", bulletin: "1予報", warn: false, maxScale: 40, maxIntensity: "4" }))})`);
  assert.ok($("quake-overlay").classList.contains("is-live"), "a 予報 also gets the frame");
  ev("closeQuake()");
  ev(`handleQuake(${JSON.stringify(eew({ id: "C", bulletin: "2", warn: true, maxScale: -1, maxIntensity: "" }))})`);
  assert.ok(!hidden(), "the dismissed 予報 held back its 警報");
});

test("a replay of the bulletin already on screen keeps its countdown", async (t) => {
  const { ev, txt } = boot(t);
  await settle();
  ev("hideQuake(); dismissedBase=null; minScale=30;");
  ev(`handleQuake(${JSON.stringify(quake({ id: "r1", originTime: "2026/08/05 23:00:00", holdFor: 40 }))})`);
  assert.match(txt("quake-remaining"), /40s/);
  ev(`handleQuake(${JSON.stringify(quake({ id: "r1", originTime: "2026/08/05 23:00:00", holdFor: 90 }))})`);
  assert.match(txt("quake-remaining"), /40s/, "the replay restarted the countdown");
});

test("the settings panel closes on a backdrop tap and sits below the quake overlay", async (t) => {
  const { w, $, ev } = boot(t);
  await settle();
  ev("openSettings()");
  assert.ok(!$("settings-overlay").classList.contains("hidden"));
  $("settings-overlay").dispatchEvent(new w.Event("click", { bubbles: true }));
  assert.ok($("settings-overlay").classList.contains("hidden"), "a tap on the backdrop did not close it");
  // z-order is CSS, which jsdom does not apply: pin it from the stylesheet text
  const css = fs.readFileSync(path.join(FE, "style.css"), "utf8");
  const z = (sel) => Number((css.match(new RegExp(sel.replace(/[#.]/g, "\\$&") + "\\s*\\{[^}]*z-index:\\s*(\\d+)")) || [])[1]);
  assert.ok(z("#settings-overlay") < z("#quake-overlay"), `settings z=${z("#settings-overlay")} must be below quake z=${z("#quake-overlay")}`);
});

test("a NERV item flagged for the banner shows it, and it ages out", async (t) => {
  const now = Math.floor(Date.now() / 1000);
  const japan = [
    { title: "【津波警報】津波警報を発表しました", source: "NERV", alert: true, banner: true, ts: now },
    { title: "主要ニュース", link: "b", source: "産経" },
  ];
  const { $, ev } = boot(t, { api: { "/api/news": { ai: [{ title: "x" }], japan } } });
  await settle();
  assert.ok(!$("alert-banner").classList.contains("hidden"), "banner not shown");
  assert.match($("alert-banner").textContent, /津波警報/);
  assert.ok($("dashboard").classList.contains("has-alert"));
  ev(`renderAlertBanner([{ title: "old", alert: true, banner: true, ts: ${now - 4 * 3600} }])`);
  assert.ok($("alert-banner").classList.contains("hidden"), "a 4-hour-old alert should have aged out");
  assert.ok(!$("dashboard").classList.contains("has-alert"));
  ev(`renderAlertBanner([{ title: "<b>x</b>", alert: true, banner: true, ts: ${now} }])`);
  assert.equal($("alert-banner").querySelectorAll("b").length, 0, "banner text must be inert");
});

test("switching the AI source clears the old source's headlines at once", async (t) => {
  const { $, ev } = boot(t);
  await settle();
  assert.equal($("news-ai").lang, "zh");
  ev('resetAiColumn("ja")');
  assert.equal($("news-ai").lang, "ja");
  assert.equal($("news-ai").children.length, 1);
  assert.match($("news-ai").textContent, /取得中/);
  assert.equal(ev("lastNewsText"), "");
});

test("an unchanged forecast does not rebuild the weather DOM", async (t) => {
  const { $, ev } = boot(t);
  await settle();
  const before = $("weekly").firstChild;
  await ev("loadWeather()");
  await settle();
  assert.equal($("weekly").firstChild, before, "the weekly strip was rebuilt for identical data");
});

test("an EEW is keyed by its eventId, a 地震情報 by its origin time", async (t) => {
  const { ev } = boot(t);
  await settle();
  assert.equal(ev('quakeKey({ kind: "eew", id: "E1", originTime: "t" })'), "E1");
  assert.equal(ev('quakeKey({ kind: "quake", id: "Q1", originTime: "t" })'), "t");
});

// ---------------------------------------------------------------- push path
test("an alerts push refreshes the news column at once", async (t) => {
  const { w, ev } = boot(t);
  await settle();
  let n = 0;
  const orig = w.fetch;
  w.fetch = (u) => { if (String(u).includes("/api/news")) n++; return orig(u); };
  ev('ws.onmessage({ data: JSON.stringify({ type: "alerts", items: [] }) })');
  await settle();
  assert.equal(n, 1, "the tablet did not refetch the news after an alerts push");
  ev('ws.onmessage({ data: JSON.stringify({ type: "ping" }) })');   // heartbeat: no fetch
  await settle();
  assert.equal(n, 1);
});

test("a WebSocket that has gone silent is replaced", async (t) => {
  // a half-open socket stays OPEN forever from the browser's point of view; three
  // missed server heartbeats is the only signal, and an EEW must not wait for it
  const { ev } = boot(t);
  await settle();
  const before = ev("ws");
  ev("ws.readyState = 1; wsLastMsg = Date.now() - 1000;");
  ev("watchWS()");
  assert.equal(ev("ws"), before, "a recently-heard socket was dropped");
  ev("wsLastMsg = Date.now() - WS_STALE_MS - 1; watchWS()");
  assert.notEqual(ev("ws"), before, "the silent socket was not replaced");
  assert.equal(before.onclose, null, "the old socket could still trigger a second reconnect");
  await settle();   // let the replacement socket "open" while the window is still alive
});

test("empty panels are re-asked until they have data, then left alone", async (t) => {
  const { w, ev } = boot(t, { api: { "/api/fx": undefined } });
  await settle();
  let n = 0;
  const orig = w.fetch;
  w.fetch = (u) => { if (String(u).includes("/api/fx")) n++; return orig(u); };
  ev("startColdStartRetry([{ has: () => !!lastFx, load: loadFx }], 10)");
  await new Promise((r) => setTimeout(r, 120));
  assert.ok(n >= 2, `fx was retried ${n} times`);
  w.fetch = async (u) => String(u).includes("/api/fx")
    ? { ok: true, json: async () => ({ rate: 1, baseLabel: "a", quoteLabel: "b" }) } : orig(u);
  await new Promise((r) => setTimeout(r, 150));
  assert.ok(ev("lastFx"), "the retry never delivered the data");
  const settled = n;
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(n, settled, "kept polling after the panel had data");
});

test("restarting a named retry chain replaces it instead of stacking", async (t) => {
  // Jikan stays down; every midnight starts another anime chain. Unnamed, a week
  // of outage left 8 chains polling in parallel — a request every 15 s from a
  // mechanism meant to fire every 120 s.
  const { w, ev } = boot(t, { api: { "/api/anime": undefined } });
  await settle();
  let n = 0;
  const orig = w.fetch;
  w.fetch = (u) => { if (String(u).includes("/api/anime")) n++; return orig(u); };
  for (let day = 0; day < 5; day++) {
    ev('startColdStartRetry([{ has: () => false, load: loadAnime }], 10, "anime-day")');
  }
  await new Promise((r) => setTimeout(r, 120));
  assert.ok(n <= 4, `5 nights of outage fired ${n} anime fetches — the chains stacked`);
  // a chain under a different name is unaffected
  ev('startColdStartRetry([{ has: () => false, load: loadFx }], 10, "boot")');
  await new Promise((r) => setTimeout(r, 60));
  assert.ok(ev('coldRetryToken["anime-day"] > 0 && coldRetryToken["boot"] > 0'));
});

test("a backend blip does not clear a tsunami banner that is still in force", async (t) => {
  // the news list already keeps last-good on an empty response; the banner has to
  // follow the same rule, or a backend restart wipes a live warning off every wall
  const now = Math.floor(Date.now() / 1000);
  const japan = [{ title: "【津波警報】発表", source: "NERV", alert: true, banner: true, ts: now }];
  const { $, ev } = boot(t, { api: { "/api/news": { ai: [{ title: "x" }], japan } } });
  await settle();
  assert.ok(!$("alert-banner").classList.contains("hidden"), "banner never appeared");
  ev('renderNews({ ai: [], japan: [] })');          // backend cold-started
  assert.ok(!$("alert-banner").classList.contains("hidden"), "an empty response cleared a live warning");
  assert.equal($("news-japan").children.length, 1, "the list should also keep last-good");
  ev('renderNews({ ai: [], japan: [{ title: "平常のニュース" }] })');   // alert genuinely lifted
  assert.ok($("alert-banner").classList.contains("hidden"), "a real clear should hide the banner");
});

// --------------------------------------------------------------- rain radar
const RADAR = API["/api/radar/frames"];
// Tracks the radar's animation chain (setTimeout(radarStep)) and its auto-close:
// which are still pending, and the delays they were armed with. Scripts resolve
// setTimeout on the window at call time, so swapping it here is enough.
function spyRadarTimers(w) {
  const live = new Set(), delays = [], idle = [];
  const st = w.setTimeout.bind(w), ct = w.clearTimeout.bind(w);
  w.setTimeout = (fn, ms, ...a) => {
    const name = fn && fn.name;
    if (name !== "radarStep" && name !== "closeRadar") return st(fn, ms, ...a);
    (name === "radarStep" ? delays : idle).push(ms);
    const id = st(() => { live.delete(id); fn(); }, ms);
    if (name === "radarStep") live.add(id);
    return id;
  };
  w.clearTimeout = (id) => { live.delete(id); ct(id); };
  return { live, delays, idle };
}
const radarRain = (w) => [...w.document.querySelectorAll("#radar-map .radar-rain")];
const shownRain = (w) => radarRain(w).filter((l) => l.classList.contains("on"));

test("tapping the weather panel opens the radar: base layer, one rain layer per frame, city at the centre", async (t) => {
  const { w, $, txt, errs } = boot(t);
  await settle();
  const asked = [];
  const orig = w.fetch;
  w.fetch = (u, o) => { asked.push(String(u)); return orig(u, o); };
  $("weather").click();
  await settle();
  assert.deepEqual(errs, []);
  assert.ok(!$("radar-overlay").classList.contains("hidden"));
  assert.ok(asked.includes("/api/radar/frames?city=tokyo"), asked.join(","));
  const base = w.document.querySelectorAll("#radar-map .radar-base");
  assert.equal(base.length, 1);
  assert.equal(radarRain(w).length, RADAR.frames.length);
  assert.deepEqual(shownRain(w), [radarRain(w)[0]], "only the first frame should be visible");
  assert.equal(txt("radar-time"), "20:40");
  assert.equal(txt("radar-kind"), "実況");
  assert.ok($("radar-status").classList.contains("hidden"));
  // every rain layer uses the same grid as the base, with its frame's tile path
  const n = base[0].children.length;
  assert.ok(n >= 12, `${n} tiles cannot cover a 1024x768 viewport`);
  radarRain(w).forEach((l, i) => {
    assert.equal(l.children.length, n);
    assert.ok(l.firstChild.getAttribute("src").startsWith(RADAR.frames[i].tile + "/8/"));
  });
  // the tile holding Tokyo sits so that Tokyo lands on the viewport centre
  const size = 256 * 2 ** 8;
  const wx = (RADAR.lon + 180) / 360 * size;
  const s = Math.sin(RADAR.lat * Math.PI / 180);
  const wy = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * size;
  const tx = Math.floor(wx / 256), ty = Math.floor(wy / 256);
  const img = [...base[0].children].find((i) => i.getAttribute("src") === `/api/radar/base/8/${tx}/${ty}.png`);
  assert.ok(img, "the city's own tile is missing");
  const px = (el, k) => parseFloat(el.style[k]);
  const cx = px(base[0], "left") + px(img, "left") + (wx - tx * 256);
  const cy = px(base[0], "top") + px(img, "top") + (wy - ty * 256);
  assert.ok(Math.abs(cx - w.innerWidth / 2) <= 1 && Math.abs(cy - w.innerHeight / 2) <= 1, `city at ${cx},${cy}`);
  assert.ok(w.document.querySelector("#radar-map .radar-marker"), "no city marker");
});

test("the radar loop plays every frame, holds the last one, then wraps", async (t) => {
  const { w, ev, txt } = boot(t);
  await settle();
  const spy = spyRadarTimers(w);
  ev("openRadar()");
  await settle();
  assert.deepEqual(spy.delays, [ev("RADAR_FRAME_MS")]);
  const last = RADAR.frames.length - 1;
  for (let i = 1; i <= last; i++) {
    ev("radarStep()");
    assert.deepEqual(shownRain(w), [radarRain(w)[i]], `frame ${i} not the only one shown`);
  }
  assert.equal(txt("radar-kind"), "予測");
  assert.equal(txt("radar-time"), "00:00");
  assert.equal(spy.delays.at(-1), ev("RADAR_HOLD_MS"), "the last frame was not held");
  ev("radarStep()");
  assert.deepEqual(shownRain(w), [radarRain(w)[0]], "did not wrap to the first frame");
  assert.equal(txt("radar-kind"), "実況");
  assert.equal(spy.delays.at(-1), ev("RADAR_FRAME_MS"));
  assert.equal(spy.live.size, 1);
  ev("closeRadar()");
});

test("reopening the radar never stacks animation timers or layers", async (t) => {
  const { w, ev } = boot(t);
  await settle();
  const spy = spyRadarTimers(w);
  ev("openRadar()");
  await settle();
  ev("closeRadar()");              // close
  ev("openRadar()");               // and straight back open
  ev("openRadar(); openRadar();"); // even a double open while one is loading
  await settle();
  assert.equal(spy.live.size, 1, `${spy.live.size} animation chains running`);
  assert.equal(radarRain(w).length, RADAR.frames.length, "layers stacked");
  assert.equal(w.document.querySelectorAll("#radar-map .radar-base").length, 1);
  ev("closeRadar()");
});

test("closing the radar stops the loop and frees every layer", async (t) => {
  const { w, $, ev } = boot(t);
  await settle();
  const spy = spyRadarTimers(w);
  $("weather").click();
  await settle();
  assert.deepEqual(spy.idle, [5 * 60 * 1000], "a wall tablet must return to the dashboard by itself");
  $("radar-close").click();
  assert.ok($("radar-overlay").classList.contains("hidden"));
  assert.equal($("radar-map").children.length, 0, "tile layers left in the DOM");
  assert.equal(spy.live.size, 0, "the animation kept running");
  assert.equal(ev("radarTimer"), null);
  assert.equal(ev("radarIdleTimer"), null);
  // a response that lands after the close must not resurrect anything
  $("weather").click();
  ev("closeRadar()");
  await settle();
  assert.ok($("radar-overlay").classList.contains("hidden"));
  assert.equal($("radar-map").children.length, 0, "a late response rebuilt the layers");
  assert.equal(spy.live.size, 0);
});

test("the radar re-lays its tiles for a new viewport", async (t) => {
  const { w, ev } = boot(t);
  await settle();
  ev("openRadar()");
  await settle();
  ev("radarStep(); radarStep();");
  const before = w.document.querySelector("#radar-map .radar-base").children.length;
  Object.defineProperty(w, "innerWidth", { value: 400, configurable: true });
  Object.defineProperty(w, "innerHeight", { value: 1200, configurable: true });
  w.dispatchEvent(new w.Event("resize"));
  await settle();
  const base = w.document.querySelectorAll("#radar-map .radar-base");
  assert.equal(base.length, 1);
  assert.notEqual(base[0].children.length, before, "the grid was not rebuilt");
  assert.equal(radarRain(w).length, RADAR.frames.length);
  assert.deepEqual(shownRain(w), [radarRain(w)[2]], "the rebuild lost the current frame");
  ev("closeRadar()");
});

test("no radar frames (or no response) shows a message, not a blank screen", async (t) => {
  for (const body of [{ ...RADAR, frames: [] }, undefined, { ...RADAR, frames: [{ time: "x", kind: "obs", tile: "//evil/x" }] }]) {
    const { w, $, txt, ev } = boot(t, { api: { "/api/radar/frames": body } });
    await settle();
    const spy = spyRadarTimers(w);
    ev("openRadar()");
    await settle();
    assert.ok(!$("radar-overlay").classList.contains("hidden"));
    assert.equal(txt("radar-status"), ev('t("noDataError")'));
    assert.ok(!$("radar-status").classList.contains("hidden"));
    assert.ok($("radar-play").classList.contains("hidden"));
    assert.equal($("radar-map").children.length, 0);
    assert.equal(w.document.querySelectorAll("#radar-map img").length, 0);
    assert.equal(spy.live.size, 0);
    ev("closeRadar()");
  }
});

test("a rain frame with a failed tile is labelled, not shown as a dry sky; one retry can heal it", async (t) => {
  const { w, $, ev, txt } = boot(t);
  await settle();
  const retries = [];
  const st = w.setTimeout.bind(w);
  w.setTimeout = (fn, ms, ...a) => (ms === ev("RADAR_RETRY_MS") ? (retries.push(fn), 0) : st(fn, ms, ...a));
  ev("openRadar()");
  await settle();
  const fail = (img) => img.dispatchEvent(new w.Event("error"));
  const bad = radarRain(w)[1].children[3];
  fail(bad);
  assert.equal(txt("radar-kind"), "実況", "frame 0 loaded fine and must keep its label");
  ev("radarStep()");
  assert.equal(txt("radar-kind"), ev('t("noDataError")'));
  assert.ok($("radar-kind").classList.contains("err"));
  assert.equal(bad.style.visibility, "hidden", "no broken-image icon");
  ev("radarStep()");
  assert.equal(txt("radar-kind"), "実況");
  assert.ok(!$("radar-kind").classList.contains("err"));
  // a tile failing while its frame is on screen relabels it at once
  fail(radarRain(w)[2].children[0]);
  assert.equal(txt("radar-kind"), ev('t("noDataError")'));
  // exactly one delayed retry per tile, and a success puts the tile back
  fail(bad);
  assert.equal(retries.length, 2, "one retry per failed tile, never a loop");
  ev("radarStep(); radarStep(); radarStep(); radarStep();");             // back to frame 1
  bad.dispatchEvent(new w.Event("load"));
  assert.equal(bad.style.visibility, "");
  assert.equal(txt("radar-kind"), "実況");
  ev("closeRadar()");
  retries.forEach((fn) => fn());                                         // late retries on removed tiles
  assert.equal($("radar-map").children.length, 0);
});

test("a radar list whose newest observation is old is marked stale", async (t) => {
  const at = (min) => new Date(Date.now() + min * 60000).toISOString();
  const body = (ago) => ({ ...RADAR, frames: RADAR.frames.map((f, i) => ({ ...f, time: at(-ago + (i - RADAR.now) * 10) })) });
  for (const [ago, stale] of [[5, false], [45, true]]) {
    const { $, ev } = boot(t, { api: { "/api/radar/frames": body(ago) } });
    await settle();
    ev("openRadar()");
    await settle();
    assert.equal(!$("radar-stale").classList.contains("hidden"), stale, `${ago} min old`);
    assert.ok(!$("radar-play").classList.contains("hidden"), "old frames still play");
    ev("closeRadar()");
    assert.ok($("radar-stale").classList.contains("hidden"), "the mark outlived the overlay");
  }
});

test("the radar sits below the quake overlay, no ☔ button, radar.js before app.js", async (t) => {
  const { $ } = boot(t);
  await settle();
  const css = fs.readFileSync(path.join(FE, "style.css"), "utf8");
  const z = (sel) => Number((css.match(new RegExp(sel.replace(/[#.]/g, "\\$&") + "\\s*\\{[^}]*z-index:\\s*(\\d+)")) || [])[1]);
  assert.ok(z("#radar-overlay") < z("#quake-overlay"), `radar z=${z("#radar-overlay")} must be below quake z=${z("#quake-overlay")}`);
  assert.equal($("radar-toggle"), null);
  const html = fs.readFileSync(path.join(FE, "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map((m) => m[1]);
  assert.equal(scripts.at(-1), "app.js");
  assert.ok(scripts.indexOf("radar.js") >= 0 && scripts.indexOf("radar.js") < scripts.indexOf("app.js"));
});

// The 地域/全国 switch is gone: the radar is the city view only, in every language,
// whatever an older build left in localStorage.
test("the radar has no view toggle and always shows the city at RADAR_ZOOM", async (t) => {
  const { w, ev, errs } = boot(t, { setup: (w2) => w2.localStorage.setItem("radarView", "national") });
  await settle();
  ev("openRadar()");
  await settle();
  assert.equal(w.document.getElementById("radar-view"), null);
  assert.equal(w.document.querySelectorAll("#radar-overlay button").length, 1, "only ✕ is left");
  assert.equal(ev("typeof toggleRadarView"), "undefined");
  const imgs = [...w.document.querySelectorAll("#radar-map img")].map((i) => i.getAttribute("src"));
  assert.ok(imgs.length && imgs.every((s) => /\/8\/\d+\/\d+\.png$/.test(s)), "city view must be z8 only");
  const text = (l) => { ev(`lang="${l}"; applyI18n();`); return w.document.getElementById("radar-overlay").textContent; };
  for (const l of ["ja", "zh", "en"]) assert.doesNotMatch(text(l), /全国|地域|本地|Japan|Local/, l);
  const i18n = fs.readFileSync(path.join(FE, "i18n.js"), "utf8");
  assert.doesNotMatch(i18n, /radarNational|radarLocal/);
  assert.deepEqual(errs, []);
  ev("closeRadar()");
});

// ------------------------------------------------- rain over the quake map
// The real QuakeMap is stubbed out in boot(); this one records what quake.js asks
// of it, in order, alongside the fetches.
function rainMap(w) {
  const log = [];
  w.__rainLog = log;
  w.eval(`window.QuakeMap = {
    init: function () {},
    paint: function () { window.__rainLog.push("paint"); },
    setRain: function (f, z) { window.__rainLog.push(["setRain", f.length, z]); },
    loadRain: function (i) { window.__rainLog.push(["loadRain", i]); return (window.__rainLoad || Promise.resolve.bind(Promise))(i); },
    showRain: function (i) { window.__rainLog.push(["showRain", i]); },
    clearRain: function () { window.__rainLog.push("clearRain"); },
  };`);
  const orig = w.fetch;
  w.fetch = (u, o) => { if (String(u).startsWith("/api/radar/quake")) log.push("fetch"); return orig(u, o); };
  return {
    log,
    count: (name) => log.filter((e) => e === name || (Array.isArray(e) && e[0] === name)).length,
    shown: () => log.filter((e) => Array.isArray(e) && e[0] === "showRain").map((e) => e[1]),
    loads: () => log.filter((e) => Array.isArray(e) && e[0] === "loadRain").map((e) => e[1]),
  };
}
const tick = () => new Promise((r) => setTimeout(r, 0));   // lets a step's tile wait resolve
function spyQuakeRainTimers(w) {
  const live = new Set(), delays = [];
  const st = w.setTimeout.bind(w), ct = w.clearTimeout.bind(w);
  w.setTimeout = (fn, ms, ...a) => {
    if (!fn || fn.name !== "quakeRainStep") return st(fn, ms, ...a);
    delays.push(ms);
    const id = st(() => { live.delete(id); fn(); }, ms);
    live.add(id);
    return id;
  };
  w.clearTimeout = (id) => { live.delete(id); ct(id); };
  return { live, delays };
}
// times relative to the real clock, so the "+Nh" label is deterministic
const outlook = (n = 3) => ({
  zoom: 6,
  frames: Array.from({ length: n }, (_, i) => ({
    time: new Date(Date.now() + (i + 1) * 3600e3).toISOString(), coarse: i === n - 1,
    tile: `/api/radar/tile/rasrf/${i === n - 1 ? "none" : "immed"}/20261122110000/2026112212${i}000`,
  })),
});

test("a quake screen loops the rain outlook, asked for only after the quake is painted", async (t) => {
  const OUT = outlook();
  const { w, ev, $, txt, errs } = boot(t, { api: { "/api/radar/quake": OUT } });
  await settle();
  const map = rainMap(w);
  const spy = spyQuakeRainTimers(w);
  ev(`handleQuake(${JSON.stringify(quake())})`);
  assert.equal(map.log[0], "paint", "the quake must be painted before the rain is even asked for");
  await settle();
  assert.deepEqual(errs, []);
  assert.deepEqual(JSON.parse(JSON.stringify(map.log.slice(0, 6))),   // entries come from the page's realm
                   ["paint", "fetch", ["setRain", 3, 6], ["loadRain", 0], ["showRain", 0], ["loadRain", 1]]);
  assert.ok(!$("quake-rain").classList.contains("hidden"));
  assert.equal(txt("quake-rain"), `雨 +1h ${ev(`radarClock(${JSON.stringify(OUT.frames[0].time)})`)}`);
  assert.deepEqual(spy.delays, [ev("QUAKE_RAIN_FRAME_MS")]);
  assert.equal(ev("QUAKE_RAIN_FRAME_MS"), 800);
  ev("quakeRainStep()");
  await tick();
  ev("quakeRainStep()");
  await tick();
  assert.deepEqual(map.shown(), [0, 1, 2]);
  assert.match(txt("quake-rain"), /^雨 \+3h \d\d:\d\d$/, "a coarse frame reads like any other");
  assert.equal($("quake-rain").children.length, 0, "no coarse mark element");
  assert.equal(spy.delays.at(-1), ev("QUAKE_RAIN_HOLD_MS"), "the last frame was not held");
  assert.equal(ev("QUAKE_RAIN_HOLD_MS"), 2000);
  ev("quakeRainStep()");
  await tick();
  assert.deepEqual(map.shown().at(-1), 0, "did not wrap to the first frame");
  assert.equal(spy.live.size, 1);
  ev("hideQuake()");
});

test("the rain label carries no coarse mark in any language", async (t) => {
  const { w, ev, $, txt } = boot(t, { api: { "/api/radar/quake": outlook(1) } });   // its one frame is coarse
  await settle();
  rainMap(w);
  for (const l of ["ja", "zh", "en"]) {
    ev(`lang="${l}"; applyI18n();`);
    ev(`handleQuake(${JSON.stringify(quake())})`);
    await settle();
    assert.match(txt("quake-rain"), /^\S+ \+1h \d\d:\d\d$/, l);
    assert.doesNotMatch(txt("quake-rain"), /粗|coarse/i, l);
    ev("hideQuake()");
  }
  for (const f of ["i18n.js", "quake.js", "style.css"])
    assert.doesNotMatch(fs.readFileSync(path.join(FE, f), "utf8"), /quakeRainCoarse|\.coarse\b|f\.coarse/, f);
});

// The tablet has ~6 connections to this host: the loop asks one frame's tiles
// at a time and never shows a frame before they are in.
test("the rain loop waits for the next frame's tiles; a wait outliving the screen is dropped", async (t) => {
  const { w, ev, $ } = boot(t, { api: { "/api/radar/quake": outlook(4) } });
  await settle();
  const map = rainMap(w);
  const spy = spyQuakeRainTimers(w);
  const gates = {};
  w.__rainLoad = (i) => new w.Promise((r) => { gates[i] = r; });
  ev(`handleQuake(${JSON.stringify(quake())})`);
  await settle();
  assert.deepEqual(map.loads(), [0], "more than the first frame asked for");
  assert.deepEqual(map.shown(), [], "frame 0 shown before its tiles");
  assert.equal(spy.live.size, 0);
  gates[0]();
  await tick();
  assert.deepEqual(map.shown(), [0]);
  assert.deepEqual(map.loads(), [0, 1], "the next frame is fetched while this one shows");
  ev("quakeRainStep()");                  // the delay is up, frame 1's tiles are not
  await tick();
  assert.deepEqual(map.shown(), [0], "ran ahead of the tiles");
  assert.equal(spy.live.size, 0);
  gates[1]();
  await tick();
  assert.deepEqual(map.shown(), [0, 1]);
  assert.deepEqual(map.loads(), [0, 1, 2]);
  assert.equal(spy.live.size, 1);
  ev("quakeRainStep()");
  ev("hideQuake()");
  gates[2]();                             // settles after the screen closed
  await tick();
  assert.deepEqual(map.shown(), [0, 1]);
  assert.equal(spy.live.size, 0);
  assert.ok($("quake-rain").classList.contains("hidden"));
});

test("hiding the quake screen stops the loop and drops the rain; showing it again never stacks", async (t) => {
  const { w, ev, $ } = boot(t, { api: { "/api/radar/quake": outlook() } });
  await settle();
  const map = rainMap(w);
  const spy = spyQuakeRainTimers(w);
  ev(`handleQuake(${JSON.stringify(quake())})`);
  await settle();
  $("quake-close").click();
  assert.ok($("quake-overlay").classList.contains("hidden"));
  assert.equal(map.log.at(-1), "clearRain");
  assert.equal(spy.live.size, 0, "the rain loop kept running");
  assert.equal(ev("quakeRainTimer"), null);
  assert.ok($("quake-rain").classList.contains("hidden"));
  assert.equal($("quake-rain").textContent, "");
  // new quakes, a follow-up bulletin and a second takeover while it is up
  ev(`handleQuake(${JSON.stringify(quake({ id: "2", originTime: "2026/08/05 11:00:00" }))})`);
  ev(`handleQuake(${JSON.stringify(quake({ id: "2", originTime: "2026/08/05 11:00:00", bulletin: "s" }))})`);
  await settle();
  ev(`handleQuake(${JSON.stringify(quake({ id: "3", originTime: "2026/08/05 12:00:00" }))})`);
  await settle();
  assert.equal(map.count("fetch"), 2, "one fetch per opening, reused while it stays open");
  assert.equal(map.count("setRain"), 2);
  assert.equal(spy.live.size, 1, `${spy.live.size} rain loops running`);
  ev("hideQuake()");
  assert.equal(spy.live.size, 0);
});

test("manual 🗾 browsing shows the rain too, and picking another quake keeps it", async (t) => {
  const recent = [quake(), quake({ id: "9", originTime: "2026/08/04 09:00:00", maxIntensity: "3", maxScale: 30 })];
  const { w, ev, $ } = boot(t, { api: { "/api/radar/quake": outlook(), "/api/earthquake/recent": recent } });
  await settle();
  const map = rainMap(w);
  const spy = spyQuakeRainTimers(w);
  $("quake-toggle").click();
  await settle();
  assert.equal(map.count("setRain"), 1);
  assert.ok(!$("quake-overlay").classList.contains("is-live"), "manual browsing is not a takeover: no frame");
  ev("selectRecent(1)");
  ev("selectRecent(0)");
  await settle();
  assert.equal(map.count("fetch"), 1);
  assert.equal(map.count("setRain"), 1);
  assert.equal(map.count("clearRain"), 0);
  assert.equal(spy.live.size, 1);
  $("quake-toggle").click();          // 🗾 again closes it
  assert.equal(spy.live.size, 0);
  assert.equal(map.log.at(-1), "clearRain");
});

test("no rain (failure, empty, junk, or a throwing map) leaves the quake screen exactly as before", async (t) => {
  const good = outlook();
  const bodies = [undefined, { zoom: 6, frames: [] }, { frames: good.frames },
                  { zoom: 6, frames: [{ time: "x", tile: "//evil/x" }, { tile: "/api/radar/tile/x" }] }, "throw"];
  for (const body of bodies) {
    const { w, ev, $, txt, errs } = boot(t, { api: { "/api/radar/quake": body === "throw" ? good : body } });
    await settle();
    const map = rainMap(w);
    if (body === "throw") ev('QuakeMap.setRain = function () { throw new Error("boom"); };');
    const spy = spyQuakeRainTimers(w);
    ev(`handleQuake(${JSON.stringify(quake())})`);
    await settle();
    assert.deepEqual(errs, [], JSON.stringify(body));
    assert.ok(!$("quake-overlay").classList.contains("hidden"));
    assert.equal(txt("quake-badge"), "5強");
    assert.equal(txt("q-epicenter"), "東京湾");
    assert.ok($("quake-rain").classList.contains("hidden"));
    assert.equal(map.count("setRain"), 0);
    assert.equal(spy.live.size, 0);
    ev("hideQuake()");
  }
});

test("a rain response that lands after the quake screen closed is dropped", async (t) => {
  const { w, ev, $ } = boot(t, { api: { "/api/radar/quake": outlook() } });
  await settle();
  const map = rainMap(w);
  const spy = spyQuakeRainTimers(w);
  ev(`handleQuake(${JSON.stringify(quake())})`);
  ev("closeQuake()");
  await settle();
  assert.equal(map.count("setRain"), 0);
  assert.ok($("quake-rain").classList.contains("hidden"));
  assert.equal(spy.live.size, 0);
});

// -------------------------------------------------------------------- i18n
test("every data-i18n label is translated in all three languages", async (t) => {
  const { w, ev } = boot(t);
  await settle();
  const KEYS = /^(newsAI|newsJapan|fxTitle|comingSoon|holidayTitle|animeTitle|thresholdLabel|maxIntensity|epicenter|magnitude|depth|origin|tsunami|settings|language|cityLabel|aiSourceLabel|close|noData|radarTitle)$/;
  for (const lang of ["ja", "zh", "en"]) {
    ev(`lang="${lang}"; applyI18n();`);
    const raw = [...w.document.querySelectorAll("[data-i18n]")]
      .filter((el) => KEYS.test(el.textContent.trim()))
      .map((el) => el.dataset.i18n);
    assert.deepEqual(raw, [], `${lang}: untranslated ${raw.join(",")}`);
    assert.ok(w.document.title.length > 0);
    assert.equal(w.document.documentElement.lang, lang);
  }
});

test("i18n covers the same key set in every language", (t) => {
  const src = fs.readFileSync(path.join(FE, "i18n.js"), "utf8");
  const sandbox = { window: {} };
  new Function("window", src).call(sandbox, sandbox.window);
  const langs = Object.keys(sandbox.window.I18N);
  assert.ok(langs.length >= 3);
  const base = new Set(Object.keys(sandbox.window.I18N.ja));
  for (const l of langs) {
    const keys = new Set(Object.keys(sandbox.window.I18N[l]));
    const missing = [...base].filter((k) => !keys.has(k));
    const extra = [...keys].filter((k) => !base.has(k));
    assert.deepEqual(missing, [], `${l} is missing ${missing.join(",")}`);
    assert.deepEqual(extra, [], `${l} has stray ${extra.join(",")}`);
  }
});
