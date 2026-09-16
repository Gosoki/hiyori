"""Decode dataofjapan/land TopoJSON -> a small, simplified prefecture GeoJSON.

Output: frontend/japan.geo.json  (properties: nam_ja, id)
Run again to regenerate: python3 tools/build_map.py
"""
import json, os, urllib.request

SRC = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.topojson"
OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "japan.geo.json")
PRECISION = 2  # decimal places (~1km) — visually identical at full-Japan dashboard scale
# Douglas–Peucker tolerance in degrees. The main map's viewBox is ~16 units tall
# and renders at 67 px/unit on a 1080p screen with a 2 px prefecture stroke, so
# any detail finer than ~0.007° is under the stroke that covers it. 0.006° removes
# about two thirds of the points with no visible change; the SVG then rasterizes
# in a third of the time when the quake screen appears.
SIMPLIFY_DEG = 0.006
# Rings whose bounding box is smaller than this (≈1 px at 1080p) are sub-pixel
# islets: drawn, they are a speck; dropped, nothing changes. The largest ring of a
# prefecture is always kept, whatever its size.
MIN_RING_DEG = 0.015

def load_topo():
    local = "/tmp/japan.topojson"
    if os.path.exists(local):
        return json.load(open(local))
    with urllib.request.urlopen(SRC) as r:
        data = r.read()
    open(local, "wb").write(data)
    return json.loads(data)

topo = load_topo()
scale = topo["transform"]["scale"]
trans = topo["transform"]["translate"]

def decode(arc):
    x = y = 0; out = []
    for dx, dy in arc:
        x += dx; y += dy
        out.append([x * scale[0] + trans[0], y * scale[1] + trans[1]])
    return out

arcs = [decode(a) for a in topo["arcs"]]

def arc_coords(i):
    return arcs[i] if i >= 0 else arcs[~i][::-1]

def ring(indices):
    pts = []
    for k, idx in enumerate(indices):
        seg = arc_coords(idx)
        pts.extend(seg if k == 0 else seg[1:])
    return pts

def _perp_dist(p, a, b):
    """Distance from p to segment ab (in degrees, flat-earth — fine at this scale)."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((p[0] - ax) ** 2 + (p[1] - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx, ay + t * dy
    return ((p[0] - qx) ** 2 + (p[1] - qy) ** 2) ** 0.5


def douglas_peucker(pts, tol):
    """Classic recursive DP on an open polyline (iterative to spare the stack)."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            d = _perp_dist(pts[k], pts[i], pts[j])
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [p for p, k in zip(pts, keep) if k]


def simplify_ring(r):
    # DP first (on full precision), then round: rounding first would create the
    # collinear runs that DP is supposed to remove, and emit duplicate points.
    # Closed ring: run DP on the open polyline and re-close it.
    open_pts = r[:-1] if len(r) > 1 and r[0] == r[-1] else list(r)
    if len(open_pts) >= 3:
        # split at the farthest point from the start so the ring's two halves are
        # both proper open polylines (DP pins both ends of what it is given)
        far = max(range(len(open_pts)),
                  key=lambda k: (open_pts[k][0] - open_pts[0][0]) ** 2 + (open_pts[k][1] - open_pts[0][1]) ** 2)
        half1 = douglas_peucker(open_pts[:far + 1], SIMPLIFY_DEG)
        half2 = douglas_peucker(open_pts[far:] + [open_pts[0]], SIMPLIFY_DEG)
        open_pts = half1 + half2[1:-1]
    out = []
    for p in open_pts:
        q = [round(p[0], PRECISION), round(p[1], PRECISION)]
        if not out or out[-1] != q:
            out.append(q)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def ring_extent(r):
    xs = [p[0] for p in r]
    ys = [p[1] for p in r]
    return max(max(xs) - min(xs), max(ys) - min(ys))

def poly_points(poly):
    return sum(len(r) for r in poly)

obj = list(topo["objects"].values())[0]
features = []
for g in obj["geometries"]:
    props = g.get("properties", {})
    gtype = g["type"]
    if gtype == "Polygon":
        polys = [[ring(r) for r in g["arcs"]]]
    elif gtype == "MultiPolygon":
        polys = [[ring(r) for r in poly] for poly in g["arcs"]]
    else:
        continue
    # simplify + drop degenerate islets, but always keep the largest polygon
    simplified = []
    for poly in polys:
        srings = [simplify_ring(r) for r in poly if len(r) >= 4]
        srings = [r for r in srings if len(r) >= 4]
        if srings:
            simplified.append(srings)
    if not simplified:
        continue
    # largest polygon first (by extent, not point count — a simplified main island
    # can have fewer points than a jagged islet); it is always kept, the rest only
    # if they would be visible at all
    simplified.sort(key=lambda poly: ring_extent(poly[0]), reverse=True)
    kept = [simplified[0]] + [p for p in simplified[1:]
                              if poly_points(p) >= 6 and ring_extent(p[0]) >= MIN_RING_DEG]
    features.append({
        "type": "Feature",
        "properties": {"nam_ja": props.get("nam_ja"), "id": props.get("id")},
        "geometry": {"type": "MultiPolygon", "coordinates": kept},
    })

fc = {"type": "FeatureCollection", "features": features}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(fc, open(OUT, "w"), separators=(",", ":"), ensure_ascii=False)
print("features:", len(features), "| bytes:", os.path.getsize(OUT))
print("sample:", features[0]["properties"], "polys:", len(features[0]["geometry"]["coordinates"]))
