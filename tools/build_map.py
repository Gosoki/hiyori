"""Decode dataofjapan/land TopoJSON -> a small, simplified prefecture GeoJSON.

Output: frontend/japan.geo.json  (properties: nam_ja, id)
Run again to regenerate: python3 tools/build_map.py
"""
import json, os, urllib.request

SRC = "https://raw.githubusercontent.com/dataofjapan/land/master/japan.topojson"
OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "japan.geo.json")
PRECISION = 2  # decimal places (~1km) — visually identical at full-Japan dashboard scale

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

def simplify_ring(r):
    out = []
    for p in r:
        q = [round(p[0], PRECISION), round(p[1], PRECISION)]
        if not out or out[-1] != q:
            out.append(q)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out

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
    simplified.sort(key=poly_points, reverse=True)
    kept = [simplified[0]] + [p for p in simplified[1:] if poly_points(p) >= 6]
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
