// Lightweight self-drawn Japan map (SVG). Loaded once at startup; afterwards it
// is static — only fills/epicenter update when an earthquake arrives.
//
// To avoid wasting screen space (Japan runs on a long SW→NE diagonal and Okinawa
// trails far to the southwest), we split into two maps like 気象庁/pro quake maps:
//   - main islands (Hokkaido〜Kyushu) enlarged to fill the screen, right-aligned
//   - Okinawa (沖縄県) in a small inset box, bottom-left
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const SCALE_CLASS = { 10: "i1", 20: "i2", 30: "i3", 40: "i4", 45: "i5w", 50: "i5s", 55: "i6w", 60: "i6s", 70: "i7" };
  // Southwest island chain (Okinawa + Amami + Tokara, south of ~30°N and west of
  // ~132°E) -> Okinawa inset. Far-southeast remote islets (小笠原 etc.) are dropped
  // so the main map compacts to Kyushu〜Hokkaido and fills the frame.
  const NANSEI_LAT = 30, NANSEI_LON = 132;

  let mainPaths = {}, insetPaths = {};
  let mainEpi = null, insetEpi = null;
  let projMain = null, projInset = null;
  let boxMain = null, boxInset = null;
  let ready = false, pending = null;

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
    geo.features.forEach((f) => {
      const mainPolys = [], insetPolys = [];
      f.geometry.coordinates.forEach((poly) => {
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

    boxMain = bbox(mainFeats);
    boxMain.minLat = Math.max(boxMain.minLat, 30.0);   // fill the bottom with Kyushu (a bit of ocean margin below)
    boxMain.maxLat += 0.5;                             // small breathing margin above Hokkaido
    boxInset = pad(bbox(insetFeats), 0.04);            // tight margin so the islands fill the inset
    projMain = makeProject(boxMain);
    projInset = makeProject(boxInset);

    container.innerHTML = "";

    const mainSvg = buildSvg(mainFeats, projMain, boxMain, "xMidYMid meet");
    mainSvg.classList.add("japan-svg", "japan-main");
    mainPaths = mainSvg._paths;
    mainEpi = mainSvg._epi;
    container.appendChild(mainSvg);

    const insetSvg = buildSvg(insetFeats, projInset, boxInset, "xMidYMid meet");
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
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", buildPath(f.geometry.coordinates, project));
      path.setAttribute("class", "pref");
      svg.appendChild(path);
      paths[f.properties.nam_ja] = path;
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
    (regions || []).forEach((r) => {
      if (r.scale > 0) {
        const cls = "pref " + (SCALE_CLASS[r.scale] || "i1");
        if (mainPaths[r.name]) mainPaths[r.name].setAttribute("class", cls);
        if (insetPaths[r.name]) insetPaths[r.name].setAttribute("class", cls);
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

  window.QuakeMap = { init, paint };
})();
