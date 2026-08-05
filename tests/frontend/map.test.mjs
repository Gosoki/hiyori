// map.js against the real 600 KB japan.geo.json, with a lightweight DOM stub —
// jsdom's real SVG DOM is far too slow for 47 prefectures of path data.
//
// What matters here is the naming mismatches between the feed and the geojson:
// EEW prefecture names arrive without their 県/府/都 suffix, JMA splits Hokkaido
// into four forecast regions the map has as one shape, and Okinawa lives in its
// own inset that offshore epicentres still have to reach.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FE = path.join(HERE, "..", "..", "frontend");

function El(tag) {
  const e = {
    tagName: tag, children: [], attrs: {}, style: {}, _cls: [], lastChild: null,
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); this.lastChild = c; return c; },
    set innerHTML(_v) { this.children = []; },
    set textContent(v) { this._text = v; },
  };
  e.classList = { add: (...c) => e._cls.push(...c), remove: () => {}, contains: (c) => e._cls.includes(c) };
  return e;
}

function load() {
  const container = El("div");
  global.window = {};
  global.document = {
    getElementById: (id) => (id === "quake-map" ? container : null),
    createElementNS: (_ns, tag) => El(tag),
    createElement: (tag) => El(tag),
  };
  global.fetch = async (u) => ({ json: async () => JSON.parse(fs.readFileSync(path.join(FE, u), "utf8")) });
  // map.js reads SCALE_CLASS from core.js. Lift the real declaration out rather
  // than restating it here — a copy in the test would defeat the point of having
  // deduplicated it, and this fails loudly if core.js ever drops it.
  const core = fs.readFileSync(path.join(FE, "core.js"), "utf8");
  const decl = core.match(/^const SCALE_CLASS = .*$/m);
  assert.ok(decl, "core.js no longer declares SCALE_CLASS");
  // eslint-disable-next-line no-eval
  (0, eval)(decl[0].replace("const", "var") + "\n" + fs.readFileSync(path.join(FE, "map.js"), "utf8"));
  return { container, QuakeMap: global.window.QuakeMap };
}

const paths = (svg) => svg.children.filter((c) => c.tagName === "path");
const painted = (svg) => paths(svg).filter((p) => p.attrs.class !== "pref").map((p) => p.attrs.class);

let ctx;
test("builds both maps from the real geojson", async () => {
  ctx = load();
  await ctx.QuakeMap.init("quake-map");
  const [mainSvg, insetWrap] = ctx.container.children;
  ctx.mainSvg = mainSvg;
  ctx.insetSvg = insetWrap.children[0];
  assert.ok(paths(mainSvg).length >= 40, `only ${paths(mainSvg).length} prefectures on the main map`);
  assert.ok(paths(ctx.insetSvg).length >= 1, "the Okinawa inset is empty");
  for (const svg of [mainSvg, ctx.insetSvg]) {
    const vb = svg.getAttribute("viewBox");
    assert.match(vb, /^0 0 [\d.]+ [\d.]+$/, `bad viewBox: ${vb}`);
    assert.ok(!vb.includes("NaN"));
  }
  assert.ok(paths(mainSvg).every((p) => (p.attrs.d || "").startsWith("M") && !p.attrs.d.includes("NaN")));
});

test("a 551 prefecture name colours the map and places the epicentre", () => {
  ctx.QuakeMap.paint([{ name: "東京都", scale: 50, label: "5強" }], { latitude: 35.5, longitude: 139.8 });
  assert.deepEqual(painted(ctx.mainSvg), ["pref i5s"]);
  assert.equal(ctx.mainSvg.lastChild.style.display, "");
});

test("EEW names arrive without the 県/府/都 suffix and must still match", () => {
  ctx.QuakeMap.paint([{ name: "大阪", scale: 40, label: "4" }, { name: "岩手", scale: 30, label: "3" }], null);
  assert.equal(painted(ctx.mainSvg).length, 2);
});

test("JMA's four Hokkaido regions collapse onto one shape, strongest wins", () => {
  ctx.QuakeMap.paint([
    { name: "北海道道央", scale: 55, label: "6弱" },
    { name: "北海道道東", scale: 30, label: "3" },
  ], null);
  const hk = painted(ctx.mainSvg);
  assert.equal(hk.length, 1, "Hokkaido painted more than once");
  assert.match(hk[0], /i6w/, "the weaker region overwrote the stronger one");
});

test("Okinawa quakes go to the inset, not the main map", () => {
  ctx.QuakeMap.paint([{ name: "沖縄県", scale: 40, label: "4" }], { latitude: 26.2, longitude: 127.7 });
  assert.ok(painted(ctx.insetSvg).length >= 1);
  assert.equal(ctx.insetSvg.lastChild.style.display, "");
  assert.equal(ctx.mainSvg.lastChild.style.display, "none");
});

test("an epicentre west of the inset's own bbox is clamped into it", () => {
  // 与那国島近海 sits west of the westernmost island; it must still be visible
  ctx.QuakeMap.paint([], { latitude: 24.0, longitude: 121.0 });
  assert.equal(ctx.insetSvg.lastChild.style.display, "");
  assert.ok(!ctx.insetSvg.lastChild.attrs.transform.includes("NaN"));
});

test("coordinates outside Japan hide the marker instead of drawing it off-map", () => {
  ctx.QuakeMap.paint([], { latitude: -200, longitude: -200 });
  assert.equal(ctx.mainSvg.lastChild.style.display, "none");
  assert.equal(ctx.insetSvg.lastChild.style.display, "none");
});

test("malformed region rows are survivable", () => {
  ctx.QuakeMap.paint([{ name: "不明県", scale: 70 }, { name: "", scale: 10 }, { name: "東京都", scale: -1 }], {});
  ctx.QuakeMap.paint(null, undefined);
  ctx.QuakeMap.paint([], null);
});
