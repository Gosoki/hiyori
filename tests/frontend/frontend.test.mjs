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
};

// `t` is node:test's context — used to close the window afterwards. app.js installs
// several setInterval timers at boot, so a window left open keeps the whole test
// process alive forever.
function boot(t, { api = {} } = {}) {
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
  w.WebSocket = class { constructor() { this.readyState = 0; } close() {} };
  w.ResizeObserver = class { observe() {} disconnect() {} };

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

// -------------------------------------------------------------------- i18n
test("every data-i18n label is translated in all three languages", async (t) => {
  const { w, ev } = boot(t);
  await settle();
  const KEYS = /^(newsAI|newsJapan|fxTitle|comingSoon|holidayTitle|animeTitle|thresholdLabel|maxIntensity|epicenter|magnitude|depth|origin|tsunami|settings|language|cityLabel|aiSourceLabel|close|noData)$/;
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
