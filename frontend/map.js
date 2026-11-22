// Lightweight self-drawn Japan map (SVG). Loaded once at startup; afterwards it
// is static — only fills/epicenter update when an earthquake arrives.
//
// To avoid wasting screen space (Japan runs on a long SW→NE diagonal and Okinawa
// trails far to the southwest), we split into two maps like 気象庁/pro quake maps:
//   - main islands (Hokkaido〜Kyushu) enlarged to fill the screen, right-aligned
//   - Okinawa (沖縄県) in a small inset box, bottom-left
(function () {
  "use strict";
  // Reads SCALE_CLASS from core.js — index.html loads core.js first (see the
  // comment on the script tags; load order is the contract for these files).
  const NS = "http://www.w3.org/2000/svg";
  // Southwest island chain (Okinawa + Amami + Tokara, south of ~30°N and west of
  // ~132°E) -> Okinawa inset. Far-southeast remote islets (小笠原 etc.) are dropped
  // so the main map compacts to Kyushu〜Hokkaido and fills the frame.
  const NANSEI_LAT = 30, NANSEI_LON = 132;

  let mainPaths = {}, insetPaths = {};
  let mainEpi = null, insetEpi = null;
  let projMain = null, projInset = null;
  let boxMain = null, boxInset = null;
  let mainSvg = null, insetSvg = null;
  let ready = false, pending = null;
  // Rain outlook laid over both maps (quake.js runs the loop): the frames asked
  // for, and the one nested <svg class="rain"> per map holding a <g> per frame.
  const TILE = 256, TILE_HAIR = 1 / TILE;   // neighbouring tiles overlap by one tile pixel: no seams
  let rain = null, rainIdx = 0, rainRoots = [];
  // Tiles load one frame at a time (loadRain): all frames at once would hold the
  // browser's few connections to this host for as long as JMA is slow, and the
  // quake screen's own requests would queue behind them.
  let rainLoads = [];   // per frame, once asked: Promise settled when its tiles (both maps) have
  let rainWaits = [];   // asked before the map was built: resolvers, run once it is

  async function init(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    let geo;
    try {
      geo = await (await fetch("japan.geo.json")).json();
    } catch (_) {
      return;   // map is optional; the info panel still works without it
    }

    // Split each prefecture's polygons: SW island chain -> inset, rest -> main.
    const mainFeats = [], insetFeats = [];
    ((geo && geo.features) || []).forEach((f) => {
      if (!f || !f.geometry || !Array.isArray(f.geometry.coordinates)) return;
      const mainPolys = [], insetPolys = [];
      f.geometry.coordinates.forEach((poly) => {
        if (!Array.isArray(poly) || !Array.isArray(poly[0])) return;
        let maxLat = -Infinity, minLon = Infinity;
        poly[0].forEach((p) => { if (p[1] > maxLat) maxLat = p[1]; if (p[0] < minLon) minLon = p[0]; });
        const south = maxLat < NANSEI_LAT;
        if (south && minLon < NANSEI_LON) insetPolys.push(poly);   // SW chain -> inset
        else if (!south) mainPolys.push(poly);                     // mainland -> main
        // else: far-southeast remote islets (小笠原) -> dropped
      });
      if (mainPolys.length) mainFeats.push(feat(f, mainPolys));
      if (insetPolys.length) insetFeats.push(feat(f, insetPolys));
    });

    if (!mainFeats.length || !insetFeats.length) return;   // empty bbox -> NaN viewBox; skip the map
    boxMain = bbox(mainFeats);
    boxMain.minLat = Math.max(boxMain.minLat, 30.0);   // fill the bottom with Kyushu (a bit of ocean margin below)
    boxMain.maxLat += 0.5;                             // small breathing margin above Hokkaido
    boxInset = pad(bbox(insetFeats), 0.04);            // tight margin so the islands fill the inset
    projMain = makeProject(boxMain);
    projInset = makeProject(boxInset);

    container.innerHTML = "";

    mainSvg = buildSvg(mainFeats, projMain, boxMain, "xMidYMid meet");
    mainSvg.classList.add("japan-svg", "japan-main");
    mainPaths = mainSvg._paths;
    mainEpi = mainSvg._epi;
    container.appendChild(mainSvg);

    insetSvg = buildSvg(insetFeats, projInset, boxInset, "xMidYMid meet");
    insetSvg.classList.add("japan-svg");
    insetPaths = insetSvg._paths;
    insetEpi = insetSvg._epi;
    const insetWrap = document.createElement("div");
    insetWrap.className = "okinawa-inset";
    const label = document.createElement("div");
    label.className = "okinawa-label";
    label.textContent = "沖縄";
    insetWrap.appendChild(insetSvg);
    insetWrap.appendChild(label);
    container.appendChild(insetWrap);

    ready = true;
    if (pending) { const p = pending; pending = null; paint(p.regions, p.hypo); }
    if (rain) buildRain();
  }

  function bbox(feats) {
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    feats.forEach((f) => f.geometry.coordinates.forEach((poly) => poly.forEach((ring) =>
      ring.forEach(([lon, lat]) => {
        if (lon < minLon) minLon = lon; if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
      })
    )));
    return { minLon, minLat, maxLon, maxLat };
  }

  function feat(f, polys) {
    return { properties: f.properties, geometry: { type: "MultiPolygon", coordinates: polys } };
  }

  function pad(b, frac) {
    const dlon = (b.maxLon - b.minLon) * frac, dlat = (b.maxLat - b.minLat) * frac;
    return { minLon: b.minLon - dlon, maxLon: b.maxLon + dlon, minLat: b.minLat - dlat, maxLat: b.maxLat + dlat };
  }

  function makeProject(b) {
    const kx = Math.cos(((b.minLat + b.maxLat) / 2) * Math.PI / 180);
    return (lon, lat) => [(lon - b.minLon) * kx, (b.maxLat - lat)];
  }

  function buildSvg(feats, project, b, par) {
    const kx = Math.cos(((b.minLat + b.maxLat) / 2) * Math.PI / 180);
    const W = (b.maxLon - b.minLon) * kx, H = (b.maxLat - b.minLat);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W.toFixed(3)} ${H.toFixed(3)}`);
    svg.setAttribute("preserveAspectRatio", par);

    const paths = {};
    feats.forEach((f) => {
      const name = (f.properties && f.properties.nam_ja) || "";
      if (!name) return;   // unnamed feature can't be matched to a 震度 anyway
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", buildPath(f.geometry.coordinates, project));
      path.setAttribute("class", "pref");
      svg.appendChild(path);
      paths[name] = path;
      // P2P EEW pref names come WITHOUT the 県/府/都 suffix (e.g. 岩手, 大阪) while
      // the geojson uses 岩手県/大阪府 — register a stripped alias so EEW coloring works
      paths[name.replace(/[県府都]$/, "")] = path;
    });

    svg.appendChild(makeEpi(W));
    svg._paths = paths;
    svg._epi = svg.lastChild;
    return svg;
  }

  function buildPath(coords, project) {
    const parts = [];
    coords.forEach((poly) => poly.forEach((ring) => {
      const d = ring.map(([lon, lat]) => {
        const [x, y] = project(lon, lat);
        return `${x.toFixed(2)} ${y.toFixed(2)}`;
      });
      parts.push("M" + d.join("L") + "Z");
    }));
    return parts.join("");
  }

  // Epicenter marker (pulsing ring + ✕), sized relative to this map's viewBox.
  function makeEpi(W) {
    const u = W / 26;
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "epi");
    g.style.display = "none";
    const ring = document.createElementNS(NS, "circle");
    ring.setAttribute("class", "epi-ring");
    ring.setAttribute("cx", "0"); ring.setAttribute("cy", "0");
    ring.setAttribute("r", (u * 0.9).toFixed(3));
    ring.setAttribute("stroke-width", (u * 0.22).toFixed(3));
    g.appendChild(ring);
    const a = u * 0.55, sw = (u * 0.26).toFixed(3);   // ✕ arms (smaller than the pulse ring)
    [[-a, -a, a, a], [-a, a, a, -a]].forEach(([x1, y1, x2, y2]) => {
      const l = document.createElementNS(NS, "line");
      l.setAttribute("class", "epi-x");
      l.setAttribute("x1", x1.toFixed(3)); l.setAttribute("y1", y1.toFixed(3));
      l.setAttribute("x2", x2.toFixed(3)); l.setAttribute("y2", y2.toFixed(3));
      l.setAttribute("stroke-width", sw);
      g.appendChild(l);
    });
    return g;
  }

  function paint(regions, hypo) {
    if (!ready) { pending = { regions, hypo }; return; }
    for (const k in mainPaths) mainPaths[k].setAttribute("class", "pref");
    for (const k in insetPaths) insetPaths[k].setAttribute("class", "pref");
    // JMA's EEW forecast regions split Hokkaido into 北海道道央/道南/道北/道東 while
    // the geojson has a single 北海道 — fall back to it. Regions arrive sorted by
    // scale desc, so first-wins keeps the strongest color when several collapse.
    const painted = new Set();
    (regions || []).forEach((r) => {
      if (r.scale > 0) {
        const cls = "pref " + (SCALE_CLASS[r.scale] || "i1");
        [mainPaths, insetPaths].forEach((paths) => {
          const p = paths[r.name] || (r.name.startsWith("北海道") ? paths["北海道"] : null);
          if (p && !painted.has(p)) { p.setAttribute("class", cls); painted.add(p); }
        });
      }
    });

    mainEpi.style.display = "none";
    insetEpi.style.display = "none";
    const lat = hypo && hypo.latitude, lon = hypo && hypo.longitude;
    if (typeof lat === "number" && typeof lon === "number" &&
        lat > 20 && lat < 50 && lon > 120 && lon < 156) {
      // Route by region (not the tight island box) so offshore Okinawa quakes —
      // e.g. 与那国島近海 west of the westernmost island — still land in the inset.
      if (lat < NANSEI_LAT && lon < NANSEI_LON) place(insetEpi, projInset, boxInset, lon, lat);
      else place(mainEpi, projMain, boxMain, lon, lat);
    }
  }

  // Place the epicenter marker, clamped to stay inside its map so offshore
  // epicenters near the edges still show (pinned to the nearest border).
  function place(epi, project, box, lon, lat) {
    const kx = Math.cos(((box.minLat + box.maxLat) / 2) * Math.PI / 180);
    const W = (box.maxLon - box.minLon) * kx, H = box.maxLat - box.minLat;
    const m = (W / 26) * 1.4;
    let [x, y] = project(lon, lat);
    x = Math.max(m, Math.min(W - m, x));
    y = Math.max(m, Math.min(H - m, y));
    epi.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
    epi.style.display = "";
  }

  // ---- rain outlook ------------------------------------------------------
  // The map is equirectangular (makeProject), JMA's rain tiles are Web-Mercator.
  // Each tile is placed on its own: its corners' lon/lat (inverse Mercator for the
  // top and bottom edges) go through this map's projection, and the image is
  // stretched into that box. Within one z6 tile (~4–5° of latitude) the leftover
  // curvature is a pixel or two on screen.
  function tileLon(x, n) { return x / n * 360 - 180; }
  function tileLat(y, n) { return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI; }
  function tileX(lon, n) { return Math.floor((lon + 180) / 360 * n); }
  function tileY(lat, n) {
    const r = lat * Math.PI / 180;
    return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n);
  }

  // [{x, y, w, h}] in this map's viewBox units for every zoom-z tile meeting its box.
  function tilesFor(project, b, z) {
    const n = Math.pow(2, z), out = [];
    for (let ty = tileY(b.maxLat, n); ty <= tileY(b.minLat, n); ty++) {
      for (let tx = tileX(b.minLon, n); tx <= tileX(b.maxLon, n); tx++) {
        const [x0, y0] = project(tileLon(tx, n), tileLat(ty, n));
        const [x1, y1] = project(tileLon(tx + 1, n), tileLat(ty + 1, n));
        out.push({ tx, ty, x: x0, y: y0, w: (x1 - x0) * (1 + TILE_HAIR), h: (y1 - y0) * (1 + TILE_HAIR) });
      }
    }
    return out;
  }

  function buildRain() {
    removeRain();
    [[mainSvg, projMain, boxMain], [insetSvg, projInset, boxInset]].forEach(([svg, project, b]) => {
      const kx = Math.cos(((b.minLat + b.maxLat) / 2) * Math.PI / 180);
      const W = ((b.maxLon - b.minLon) * kx).toFixed(3), H = (b.maxLat - b.minLat).toFixed(3);
      // a nested <svg> the size of the viewBox clips the tiles to the map's own
      // frame (the letterbox and the Okinawa inset's corner stay clean)
      const root = document.createElementNS(NS, "svg");
      root.setAttribute("class", "rain");
      root.setAttribute("x", "0"); root.setAttribute("y", "0");
      root.setAttribute("width", W); root.setAttribute("height", H);
      root.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const tiles = tilesFor(project, b, rain.zoom);
      rain.frames.forEach((f, i) => {
        const g = document.createElementNS(NS, "g");
        g.setAttribute("class", "rain-frame");
        g.style.display = i === rainIdx ? "" : "none";
        tiles.forEach((t) => {
          const img = document.createElementNS(NS, "image");
          img._src = `${f.tile}/${rain.zoom}/${t.tx}/${t.ty}.png`;   // set as href by loadRain
          img.setAttribute("x", t.x.toFixed(4)); img.setAttribute("y", t.y.toFixed(4));
          img.setAttribute("width", t.w.toFixed(4)); img.setAttribute("height", t.h.toFixed(4));
          img.setAttribute("preserveAspectRatio", "none");
          g.appendChild(img);
        });
        root.appendChild(g);
      });
      svg.insertBefore(root, svg._epi);   // above the 震度 fills, below the epicentre
      rainRoots.push(root);
      // JMA's light-rain colour is near-white: redraw the prefecture borders over
      // the rain (unfilled, so the 震度 colours stay underneath), still under the epicentre
      const ol = document.createElementNS(NS, "g");
      ol.setAttribute("class", "rain-outline");
      new Set(Object.values(svg._paths)).forEach((p) => {
        const o = document.createElementNS(NS, "path");
        o.setAttribute("d", p.getAttribute("d"));
        ol.appendChild(o);
      });
      svg.insertBefore(ol, svg._epi);
      rainRoots.push(ol);
    });
    rainWaits.forEach((done, i) => { if (done) frameLoad(i).then(done); });
    rainWaits = [];
  }

  // rainRoots minus the outline groups: the per-map roots holding a <g> per frame
  function rainFrameRoots() { return rainRoots.filter((r) => r.getAttribute("class") === "rain"); }

  function removeRain() {
    // dropping the href cancels a tile still queued; detaching alone leaves it loading
    rainFrameRoots().forEach((r) => r.querySelectorAll("image").forEach((img) => img.removeAttribute("href")));
    rainRoots.forEach((r) => { if (r.parentNode) r.parentNode.removeChild(r); });
    rainRoots = [];
  }

  // Set frame i's hrefs on both maps; settles once every one of its images has
  // fired load or error (a failed tile hides itself rather than show a broken image).
  function frameLoad(i) {
    const imgs = [];
    rainFrameRoots().forEach((root) => { if (root.children[i]) imgs.push(...root.children[i].children); });
    return new Promise((resolve) => {
      let left = imgs.length;
      if (!left) { resolve(); return; }
      const done = () => { if (--left === 0) resolve(); };
      imgs.forEach((img) => {
        img.onload = done;
        img.onerror = () => { img.style.visibility = "hidden"; done(); };
        img.setAttribute("href", img._src);
      });
    });
  }

  // frames: [{tile, ...}] (each tile path + "/{z}/{x}/{y}.png" is one image).
  // Replaces any rain already laid; before the map has loaded it waits for init().
  // No tile is fetched until its frame is asked for with loadRain.
  function setRain(frames, zoom) {
    rain = { frames: frames.slice(), zoom };
    rainIdx = 0;
    rainLoads = [];
    rainWaits = [];
    if (ready) buildRain();
  }

  // Fetch frame i's tiles (once); the Promise settles when they have all loaded or failed.
  function loadRain(i) {
    if (!rain || !(i >= 0 && i < rain.frames.length)) return Promise.resolve();
    if (!rainLoads[i]) rainLoads[i] = ready ? frameLoad(i) : new Promise((r) => { rainWaits[i] = r; });
    return rainLoads[i];
  }

  // Show frame i only, on both maps.
  function showRain(i) {
    rainIdx = i;
    rainFrameRoots().forEach((root) => [...root.children].forEach((g, k) => { g.style.display = k === i ? "" : "none"; }));
  }

  function clearRain() {
    rain = null;
    rainIdx = 0;
    rainLoads = [];
    rainWaits = [];
    removeRain();
  }

  window.QuakeMap = { init, paint, setRain, loadRain, showRain, clearRain };
})();
