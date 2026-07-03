"""P2P地震情報 v2 WebSocket client.

Connects to the free P2P地震情報 feed and normalizes two message kinds into a
single event shape the frontend understands:

  code 551  地震情報 (JMAQuake) — a real earthquake report with intensities
  code 556  緊急地震速報 EEW (JMAEEW) — early warning, may arrive before shaking

Numbers (magnitude, depth, intensity) are sent raw so the frontend can format
and localize them. Japanese place names are passed through unchanged.
"""
import asyncio
import datetime
import json
import time

import httpx
import websockets

P2P_HISTORY_URL = "https://api.p2pquake.net/v2/history"

# JMA 震度 (shindo) scale code -> label
SCALE = {10: "1", 20: "2", 30: "3", 40: "4",
         45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7"}

# P2P 551 issue.type -> human label (the kind of earthquake bulletin)
ISSUE_LABEL = {
    "ScalePrompt": "震度速報",
    "Destination": "震源に関する情報",
    "DetailScale": "各地の震度に関する情報",
    "Foreshock": "地震情報",
    "Other": "地震情報",
}


def scale_label(v):
    try:
        return SCALE.get(int(v), "")
    except (ValueError, TypeError):
        return ""


def _now():
    return time.time()


def _regions_from(pairs):
    """pairs: list of (group_name, scale_code). Keep max scale per group, sort desc."""
    best = {}
    for name, scale in pairs:
        if not name:
            continue
        try:
            s = int(scale)
        except (ValueError, TypeError):
            s = -1
        if name not in best or s > best[name]:
            best[name] = s
    out = [{"name": n, "scale": s, "label": scale_label(s)} for n, s in best.items()]
    out.sort(key=lambda x: x["scale"], reverse=True)
    return out


def normalize_quake(msg):
    eq = msg.get("earthquake", {}) or {}
    hypo = eq.get("hypocenter", {}) or {}
    regions = _regions_from(
        (p.get("pref", ""), p.get("scale", -1)) for p in msg.get("points", []) or []
    )
    return {
        "kind": "quake",
        "id": str(msg.get("id") or msg.get("_id") or ""),
        "revision": msg.get("issue", {}).get("type", ""),
        "issueLabel": ISSUE_LABEL.get(msg.get("issue", {}).get("type", ""), "地震情報"),
        "originTime": eq.get("time", ""),
        "hypocenter": {
            "name": hypo.get("name", "") or "調査中",
            "depth": hypo.get("depth", -1),
            "magnitude": hypo.get("magnitude", -1),
            "latitude": hypo.get("latitude", -200),
            "longitude": hypo.get("longitude", -200),
        },
        "maxScale": eq.get("maxScale", -1),
        "maxIntensity": scale_label(eq.get("maxScale", -1)),
        "tsunami": eq.get("domesticTsunami", "Unknown"),
        "regions": regions,
        "cancelled": False,
    }


def normalize_eew(msg):
    eq = msg.get("earthquake", {}) or {}
    hypo = eq.get("hypocenter", {}) or {}
    areas = msg.get("areas", []) or []
    regions = _regions_from((a.get("pref") or a.get("name", ""), a.get("scaleTo", -1)) for a in areas)
    max_scale = max((r["scale"] for r in regions), default=-1)
    return {
        "kind": "eew",
        "id": str(msg.get("issue", {}).get("eventId") or msg.get("id") or ""),
        "revision": str(msg.get("issue", {}).get("serial", "")),
        "originTime": eq.get("originTime", ""),
        "hypocenter": {
            "name": hypo.get("name", "") or hypo.get("reduceName", "") or "調査中",
            "depth": hypo.get("depth", -1),
            "magnitude": hypo.get("magnitude", -1),
            "latitude": hypo.get("latitude", -200),
            "longitude": hypo.get("longitude", -200),
        },
        "maxScale": max_scale,
        "maxIntensity": scale_label(max_scale),
        "tsunami": "Unknown",
        "regions": regions,
        "cancelled": bool(msg.get("cancelled", False)),
    }


def _quake_key(event):
    return event.get("originTime") or event.get("id")


def _merge_recent(recent, event, cap):
    """Prepend a live quake, replacing an earlier bulletin of the same quake."""
    key = _quake_key(event)
    merged = [e for e in recent if _quake_key(e) != key]
    merged.insert(0, event)
    return merged[:cap]


async def fetch_recent_quakes(n=5):
    """Fetch the last N distinct 地震情報 (551) from P2P's history REST API.

    The WebSocket only pushes *new* events, so this seeds the recent-quakes list
    on startup and lets the 🗾 button show a browsable history when it's quiet.
    Multiple bulletins for the same quake are de-duplicated (newest bulletin wins).
    """
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(P2P_HISTORY_URL, params={"codes": 551, "limit": n * 3})
        r.raise_for_status()
        data = r.json()
    out, seen = [], set()
    for msg in data:                       # newest first; first bulletin per quake wins
        event = normalize_quake(msg)
        key = _quake_key(event)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
        if len(out) >= n:
            break
    return out


class EarthquakeService:
    """Maintains a resilient WebSocket connection and the currently-active event."""

    def __init__(self, url, hold_seconds, on_event, show_test=False, recent_cap=5):
        self.url = url
        self.hold = hold_seconds
        self.on_event = on_event          # async callback(event) for broadcasting
        self.show_test = show_test
        self.recent_cap = recent_cap
        self.current = None               # active event (with receivedAt / expiresAt)
        self.recent = []                  # last N 地震情報 (newest first), for 🗾 browsing

    def active(self):
        if self.current and self.current["expiresAt"] > _now():
            return self.current
        return None

    async def run(self):
        while True:
            try:
                async with websockets.connect(
                    self.url, ping_interval=30, ping_timeout=20,
                    open_timeout=15, max_queue=64,
                ) as ws:
                    async for raw in ws:
                        await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)     # reconnect with a small backoff

    async def _handle(self, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        code = msg.get("code")
        if code == 551:
            event = normalize_quake(msg)
        elif code == 556:
            if msg.get("test") and not self.show_test:
                return
            event = normalize_eew(msg)
        else:
            return

        now = _now()
        event["receivedAt"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).isoformat(timespec="seconds")
        event["expiresAt"] = now + self.hold

        if event.get("cancelled"):
            self.current = None            # EEW cancellation clears the screen
        else:
            self.current = event
        if event["kind"] == "quake":
            self.recent = _merge_recent(self.recent, event, self.recent_cap)
        await self.on_event(event)
