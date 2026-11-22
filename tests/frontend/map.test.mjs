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
    tagName: tag, children: [], attrs: {}, style: {}, _cls: [], lastChild: null, parentNode: null,
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    removeAttribute(k) { delete this.attrs[k]; },
    querySelectorAll(tag) { return this.children.flatMap((c) => [...(c.tagName === tag ? [c] : []), ...c.querySelectorAll(tag)]); },
    appendChild(c) { this.children.push(c); this.lastChild = c; c.parentNode = this; return c; },
    insertBefore(c, ref) {
      const i = this.children.indexOf(ref);
      this.children.splice(i < 0 ? this.children.length : i, 0, c);
      this.lastChild = this.children.at(-1); c.parentNode = this; return c;
    },
    removeChild(c) {
      this.children = this.children.filter((x) => x !== c);
      this.lastChild = this.children.at(-1) || null; c.parentNode = null; return c;
    },
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

// ---- rain outlook ----------------------------------------------------------
const FRAMES = [0, 1, 2].map((i) => ({ time: `2026-11-22T2${i}:00:00+09:00`, coarse: i === 2,
                                       tile: `/api/radar/tile/rasrf/immed/20261122110000/2026112212${i}000` }));
const rainOf = (svg) => svg.children.filter((c) => c.attrs.class === "rain");
const frameShown = (svg) => rainOf(svg)[0].children.map((g) => g.style.display !== "none");
const num = (el, k) => parseFloat(el.attrs[k]);
const tx = (lon, z) => (lon + 180) / 360 * 2 ** z;
const ty = (lat, z) => { const r = lat * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z; };

test("setRain lays one group per frame on both maps, above the fills and below the epicentre", () => {
  ctx.QuakeMap.paint([{ name: "東京都", scale: 50, label: "5強" }], { latitude: 35.69, longitude: 139.69 });
  ctx.QuakeMap.setRain(FRAMES, 6);
  for (const svg of [ctx.mainSvg, ctx.insetSvg]) {
    const roots = rainOf(svg);
    assert.equal(roots.length, 1);
    assert.equal(svg.lastChild.attrs.class, "epi", "the epicentre marker must stay on top");
    assert.equal(svg.children.at(-3), roots[0], "rain belongs right under the epicentre");
    assert.ok(svg.children.indexOf(roots[0]) > svg.children.indexOf(paths(svg).at(-1)), "rain under the 震度 fills");
    assert.equal(roots[0].attrs.viewBox, svg.attrs.viewBox, "the rain is clipped to the map's own frame");
    const groups = roots[0].children;
    assert.equal(groups.length, FRAMES.length);
    const n = groups[0].children.length;
    assert.ok(n >= 1);
    groups.forEach((g, i) => {
      assert.equal(g.children.length, n);
      g.children.forEach((img) => {
        assert.equal(img.tagName, "image");
        assert.equal(img.attrs.href, undefined, "no tile is fetched before its frame is asked for");
        assert.match(img._src, new RegExp("^" + FRAMES[i].tile + "/6/\\d+/\\d+\\.png$"));
        assert.equal(img.attrs.preserveAspectRatio, "none");
      });
    });
    assert.deepEqual(frameShown(svg), [true, false, false], "only the first frame is visible");
  }
  // z6 tiles: the main map needs a handful, the inset one or two — not hundreds
  assert.ok(rainOf(ctx.mainSvg)[0].children[0].children.length <= 30);
  assert.ok(rainOf(ctx.insetSvg)[0].children[0].children.length <= 6);
});

test("the tiles cover each map's whole frame", () => {
  for (const svg of [ctx.mainSvg, ctx.insetSvg]) {
    const [, , W, H] = svg.attrs.viewBox.split(" ").map(Number);
    const imgs = rainOf(svg)[0].children[0].children;
    assert.ok(Math.min(...imgs.map((i) => num(i, "x"))) <= 0);
    assert.ok(Math.min(...imgs.map((i) => num(i, "y"))) <= 0);
    assert.ok(Math.max(...imgs.map((i) => num(i, "x") + num(i, "width"))) >= W);
    assert.ok(Math.max(...imgs.map((i) => num(i, "y") + num(i, "height"))) >= H);
  }
});

// A point inside a Web-Mercator tile, located by the tile's own pixel grid, must
// land where the map's equirectangular projection puts it (the epicentre marker).
function checkPlacement(svg, lat, lon) {
  ctx.QuakeMap.paint([], { latitude: lat, longitude: lon });
  const [ex, ey] = svg.lastChild.attrs.transform.match(/[-\d.]+/g).map(Number);
  const X = Math.floor(tx(lon, 6)), Y = Math.floor(ty(lat, 6));
  const img = rainOf(svg)[0].children[0].children.find((i) => i._src.endsWith(`/6/${X}/${Y}.png`));
  assert.ok(img, `tile 6/${X}/${Y} is missing`);
  const k = 1 + 1 / 256;                                   // the seam overlap
  const px = num(img, "x") + (tx(lon, 6) - X) * num(img, "width") / k;
  const py = num(img, "y") + (ty(lat, 6) - Y) * num(img, "height") / k;
  // what is left is the Mercator curvature inside one z6 tile (≤ ~0.035° mid-tile)
  // plus the marker's 2-decimal rounding: under 0.05 viewBox units ≈ 5 km ≈ 2 px on
  // a tablet. Unplaced, a single Mercator image would be off by tens of km.
  assert.ok(Math.abs(px - ex) < 0.05 && Math.abs(py - ey) < 0.05, `tile puts it at ${px},${py}, map at ${ex},${ey}`);
}

test("a tile is placed by its own corners, so it lines up with the map (main)", () => {
  checkPlacement(ctx.mainSvg, 35.69, 139.69);   // 東京
  checkPlacement(ctx.mainSvg, 43.06, 141.35);   // 札幌, far from the projection's mean latitude
  checkPlacement(ctx.mainSvg, 31.6, 130.56);    // 鹿児島
});

test("a tile is placed by its own corners, so it lines up with the map (Okinawa inset)", () => {
  checkPlacement(ctx.insetSvg, 26.21, 127.68);  // 那覇
});

test("showRain shows exactly one frame, on both maps", () => {
  ctx.QuakeMap.showRain(2);
  assert.deepEqual(frameShown(ctx.mainSvg), [false, false, true]);
  assert.deepEqual(frameShown(ctx.insetSvg), [false, false, true]);
  ctx.QuakeMap.showRain(0);
  assert.deepEqual(frameShown(ctx.mainSvg), [true, false, false]);
});

// JMA's <1 mm/h colour is near-white: the borders are drawn again over the rain,
// unfilled (no 震度 class), and go away with it.
const outlineOf = (svg) => svg.children.filter((c) => c.attrs.class === "rain-outline");
test("prefecture borders are redrawn above the rain, below the epicentre", () => {
  for (const svg of [ctx.mainSvg, ctx.insetSvg]) {
    const [ol] = outlineOf(svg);
    assert.ok(ol, "no outline layer");
    assert.equal(svg.children.at(-2), ol);
    assert.ok(svg.children.indexOf(ol) > svg.children.indexOf(rainOf(svg)[0]));
    const ds = paths(svg).map((p) => p.attrs.d);
    assert.deepEqual(ol.children.map((o) => o.attrs.d), ds, "one border per prefecture shape");
    assert.ok(ol.children.every((o) => o.attrs.class === undefined), "a copied 震度 class would cover the rain");
    assert.ok(ol.children.every((o) => o.style.display !== "none"), "showRain hid borders as if they were frames");
  }
});

// Fire every image's load (or error) the way the browser would.
const fire = (imgs, ev = "onload") => imgs.forEach((i) => i[ev]());
const frameImgs = (i) => [ctx.mainSvg, ctx.insetSvg].flatMap((svg) => rainOf(svg)[0].children[i].children);
test("loadRain fetches one frame at a time, on both maps, and settles when its tiles have", async () => {
  ctx.QuakeMap.setRain(FRAMES, 6);
  let settled = false;
  const p = ctx.QuakeMap.loadRain(1);
  p.then(() => { settled = true; });
  assert.ok(frameImgs(1).every((i) => i.attrs.href === i._src), "frame 1 not asked on both maps");
  assert.ok([0, 2].every((k) => frameImgs(k).every((i) => i.attrs.href === undefined)), "other frames fetched too");
  assert.equal(ctx.QuakeMap.loadRain(1), p, "asked twice, fetched twice");
  const [bad, ...rest] = frameImgs(1);
  bad.onerror();
  fire(rest.slice(1));
  await new Promise((r) => setImmediate(r));
  assert.equal(settled, false, "settled with a tile still loading");
  fire(rest.slice(0, 1));
  await p;
  assert.equal(bad.style.visibility, "hidden", "a failed tile shows as a broken image");
  assert.ok(rest.every((i) => i.style.visibility !== "hidden"));
  await ctx.QuakeMap.loadRain(99);              // out of range: nothing to wait for
});

test("setRain again replaces the layers, clearRain removes them", () => {
  ctx.QuakeMap.setRain(FRAMES.slice(0, 2), 6);
  ctx.QuakeMap.loadRain(0);
  const old = frameImgs(0);
  ctx.QuakeMap.setRain(FRAMES, 6);
  assert.ok(old.every((i) => i.attrs.href === undefined), "a replaced layer's tiles keep loading");
  assert.equal(rainOf(ctx.mainSvg).length, 1, "layers stacked");
  assert.equal(rainOf(ctx.insetSvg).length, 1);
  assert.equal(outlineOf(ctx.mainSvg).length, 1, "outlines stacked");
  ctx.QuakeMap.loadRain(2);
  const cur = frameImgs(2);
  ctx.QuakeMap.clearRain();
  assert.ok(cur.every((i) => i.attrs.href === undefined), "detached tiles keep loading: drop their href");
  assert.equal(rainOf(ctx.mainSvg).length, 0);
  assert.equal(rainOf(ctx.insetSvg).length, 0);
  assert.equal(outlineOf(ctx.mainSvg).length, 0);
  assert.equal(outlineOf(ctx.insetSvg).length, 0);
  assert.equal(ctx.mainSvg.lastChild.attrs.class, "epi");
  ctx.QuakeMap.clearRain();                     // twice is harmless
});

test("rain asked for before the map has loaded is laid once it has", async () => {
  const fresh = load();
  fresh.QuakeMap.setRain(FRAMES, 6);
  fresh.QuakeMap.showRain(1);
  let loaded = false;
  fresh.QuakeMap.loadRain(1).then(() => { loaded = true; });
  await fresh.QuakeMap.init("quake-map");
  const [mainSvg, insetWrap] = fresh.container.children;
  assert.equal(rainOf(mainSvg)[0].children.length, FRAMES.length);
  assert.deepEqual(frameShown(insetWrap.children[0]), [false, true, false]);
  // the frame asked for early is fetched once the map exists, and its wait ends then
  const imgs = [mainSvg, insetWrap.children[0]].flatMap((svg) => rainOf(svg)[0].children[1].children);
  assert.ok(imgs.length && imgs.every((i) => i.attrs.href === i._src));
  assert.ok(rainOf(mainSvg)[0].children[0].children.every((i) => i.attrs.href === undefined));
  await new Promise((r) => setImmediate(r));
  assert.equal(loaded, false);
  fire(imgs);
  await new Promise((r) => setImmediate(r));
  assert.equal(loaded, true);
});
