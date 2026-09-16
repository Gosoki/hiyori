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
import logging
import time

import httpx
import websockets

log = logging.getLogger("hiyori.earthquake")
P2P_HISTORY_URL = "https://api.p2pquake.net/v2/history"
_startup = time.time()   # so offline_for() is meaningful before the first connect

# JMA 震度 (shindo) scale code -> label
# 46 = 震度5弱以上と推定 (P2P sends it when the exact intensity isn't determined yet)
SCALE = {10: "1", 20: "2", 30: "3", 40: "4",
         45: "5弱", 46: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7"}

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


def _obj(d, key):
    """`d[key]` when it is an object, else {}.

    P2P sends explicit JSON nulls for absent sub-objects (e.g. `"issue": null`),
    so `d.get(key, {})` still hands back None and the next `.get` blows up — and
    an exception here would tear down the whole quake feed. Never do that.
    """
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _dicts(d, key):
    """`d[key]` as a list of dicts, dropping nulls/scalars the feed may include."""
    return [x for x in (d.get(key) or []) if isinstance(x, dict)]


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
    eq = _obj(msg, "earthquake")
    hypo = _obj(eq, "hypocenter")
    issue_type = _obj(msg, "issue").get("type", "")
    regions = _regions_from(
        (p.get("pref", ""), p.get("scale", -1)) for p in _dicts(msg, "points")
    )
    return {
        "kind": "quake",
        "id": str(msg.get("id") or msg.get("_id") or ""),
        # Which bulletin OF THIS QUAKE this is — a 551 identifies its bulletins by
        # type (ScalePrompt → DetailScale → …), a 556 by serial number. Different
        # spellings, same job, and it is only ever compared for equality ("is this
        # a report I have already shown?"), so one field covers both honestly.
        "bulletin": issue_type,
        "issueLabel": ISSUE_LABEL.get(issue_type, "地震情報"),
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
    eq = _obj(msg, "earthquake")
    hypo = _obj(eq, "hypocenter")
    issue = _obj(msg, "issue")
    areas = _dicts(msg, "areas")

    def _area_scale(a):
        # scaleTo=99 means "〜程度以上" (upper bound unknown) — typical of the FIRST
        # serial of a warning. Fall back to scaleFrom so the map/badge show the
        # known lower bound instead of an unmapped 99 (which would render as nothing).
        s = a.get("scaleTo", -1)
        return a.get("scaleFrom", -1) if s == 99 else s

    regions = _regions_from((a.get("pref") or a.get("name", ""), _area_scale(a)) for a in areas)
    max_scale = max((r["scale"] for r in regions), default=-1)
    return {
        "kind": "eew",
        "id": str(issue.get("eventId") or msg.get("id") or ""),
        "bulletin": str(issue.get("serial", "")),   # EEW serial: 第1報, 第2報 …
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


def quake_key(event):
    """Identity of the *quake itself*, so successive bulletins (第2報, 詳報…) of one
    quake collapse onto a single entry. Always a str, so it stays sortable.

    A 地震情報 is keyed by its origin time (P2P's `earthquake.time`, which is what
    the JMA fallback matches on). An EEW is keyed by `issue.eventId`: JMA REVISES
    the origin time between serials (16:27:18 → 16:27:15 in tests/fixtures/
    p2p_556.json), so keyed by time a キャンセル報 would never match the 第1報 it
    retracts and a dismissed 第1報 would pop straight back as 第2報.
    Mirrored by quakeKey() in frontend/quake.js.
    """
    if event.get("kind") == "eew":
        return event.get("id") or event.get("originTime") or ""
    return event.get("originTime") or event.get("id") or ""


def _event_base(event):
    """Identity of a takeover: the quake plus its kind, matching the frontend's
    `eventBase()`. An EEW and the 地震情報 for the same quake are separate screens."""
    return f"{event.get('kind', '')}:{quake_key(event)}"


def _merge_recent(recent, event, cap):
    """Fold a live quake into the browse list, replacing an earlier bulletin of the
    same quake. Newest quake first BY ORIGIN TIME, not by arrival: bulletins of
    two quakes interleave (A's 詳報 can land after B's), and /api/earthquake/latest
    and the head of the 🗾 list must mean "the most recent quake"."""
    key = quake_key(event)
    merged = [e for e in recent if quake_key(e) != key]
    merged.insert(0, event)
    merged.sort(key=lambda e: e.get("originTime") or "", reverse=True)
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
    for msg in data if isinstance(data, list) else []:   # newest first; first bulletin per quake wins
        if not isinstance(msg, dict):
            continue
        try:
            event = normalize_quake(msg)
        except Exception:
            continue                       # one bad row must not lose the whole history
        key = quake_key(event)
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
        # --- connection health, surfaced by /api/health and the tablets' 🗾 badge ---
        self.connected = False
        self.connected_since = 0.0
        self.last_message = 0.0           # any frame, not just a quake — proof of life
        self.reconnects = 0
        self.last_error = ""
        self._announced_loss = False      # a loss was logged and no recovery yet
        self._recovery_pending = None     # `opened` of a session whose recovery is not yet announced

    def active(self):
        if self.current and self.current.get("expiresAt", 0) > _now():
            return self.current
        return None

    def offline_for(self):
        """Seconds the live feed has been down, 0 while connected.

        Measured from the last *successful connection*, not the last quake: Japan
        can be quiet for hours, so silence alone proves nothing.
        """
        if self.connected:
            return 0.0
        return _now() - (self.connected_since or _startup)

    def status(self):
        return {
            "connected": self.connected,
            "offlineFor": round(self.offline_for()),
            "lastMessageAge": round(_now() - self.last_message) if self.last_message else None,
            "reconnects": self.reconnects,
            "lastError": self.last_error,
        }

    # A session counts as "held" — i.e. the feed was really up — once it has lasted
    # this long or delivered a frame. A server that completes the handshake and
    # drops the socket at once (overloaded, a proxy that upgrades then dies) must
    # not reset offline_for() every 5 s: that would keep the JMA fallback from ever
    # arming, and log a lost/reconnected pair 17,000 times a day.
    HELD_AFTER = 60

    def _mark_recovered(self, opened):
        """The session opened at `opened` has proved itself (held HELD_AFTER seconds
        or delivered a frame): now — not at the handshake — it counts as recovered."""
        if self._recovery_pending == opened:
            self._recovery_pending = None
            self._announced_loss = False
            log.warning("P2P quake feed reconnected (attempt %d)", self.reconnects)

    async def run(self):
        self._announced_loss = False
        self._recovery_pending = None
        while True:
            opened, timer = 0.0, None
            try:
                async with websockets.connect(
                    self.url, ping_interval=30, ping_timeout=20,
                    open_timeout=15, max_queue=64,
                ) as ws:
                    opened = _now()
                    self.connected = True
                    if not self.connected_since:
                        self.connected_since = opened            # first ever connect
                    self.last_error = ""
                    if self._announced_loss:
                        # recovery is announced once the session has held, so a
                        # server that accepts and drops does not log a recovery per flap
                        self._recovery_pending = opened
                        timer = asyncio.get_running_loop().call_later(
                            self.HELD_AFTER, self._mark_recovered, opened)
                    elif not self.reconnects:
                        log.info("P2P quake feed connected")
                    async for raw in ws:
                        self.last_message = _now()
                        self._mark_recovered(opened)             # a frame proves the link
                        await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            finally:
                if timer:
                    timer.cancel()
            self.connected = False
            held = bool(opened) and (_now() - opened >= self.HELD_AFTER or self.last_message >= opened)
            if held:
                self._mark_recovered(opened)       # held but dropped before the timer fired: still a recovery
                self.connected_since = opened      # only a session that held counts as "last time it was up"
            self.reconnects += 1
            if held and not self._announced_loss:
                # Only the transition, so a long outage (or a flapping server)
                # doesn't fill the journal.
                log.warning("P2P quake feed lost (%s); retrying every 5s",
                            self.last_error or "server closed the connection")
                self._announced_loss = True
            # unconditional: a graceful server close exits the `async with` without
            # raising, which would otherwise reconnect in a tight loop
            await asyncio.sleep(5)

    async def _handle(self, raw):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        code = msg.get("code")
        try:
            if code == 551:
                event = normalize_quake(msg)
            elif code == 556:
                if msg.get("test") and not self.show_test:
                    return
                event = normalize_eew(msg)
            else:
                return
        except Exception:
            # One weird bulletin must not propagate to run()'s reconnect handler:
            # that would drop the feed for 5s — exactly when quakes come in bursts.
            return
        await self.publish(event)

    async def publish(self, event, source="p2p"):
        """Stamp an event with its hold window, fold it into the 🗾 list, broadcast.

        Shared by the live P2P socket and the JMA fallback poller, so both sources
        produce identical behaviour on the tablets.
        """
        event.setdefault("source", source)
        now = _now()
        event["receivedAt"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).isoformat(timespec="seconds")
        event["expiresAt"] = now + self.hold
        event["holdFor"] = self.hold   # duration, clock-skew-proof (tablets compute their own deadline)

        # Always keep the 🗾 browse list complete, regardless of size.
        if event["kind"] == "quake":
            self.recent = _merge_recent(self.recent, event, self.recent_cap)

        if event.get("cancelled"):
            # A cancellation retracts the event it refers to — it must not blank an
            # unrelated takeover that happens to still be active (the frontend
            # already scopes it this way; keep /api/earthquake/current in step).
            if self.current and _event_base(self.current) == _event_base(event):
                self.current = None
        elif self._supersedes(event):
            self.current = event

        # Broadcast every event; each device decides — by its own 震度 threshold —
        # whether to take over the full screen (the filter lives in the frontend).
        await self.on_event(event)

    def _supersedes(self, event):
        """Should `event` become what /api/earthquake/current and the replay-on-
        connect hand out? `current` is what a tablet that reboots, reconnects or
        polls mid-hold gets to see, so it must stay the STRONGEST live picture:

        - a follow-up of the same quake wins unless it carries no intensity — the
          震源に関する情報 that lands between 震度速報 and 各地の震度 has maxScale -1,
          and a 551 without intensity never takes a screen over (quake.js);
        - a different quake only wins if it is at least as strong: a 震度2
          aftershock 40 s into a 震度6弱 hold must not hide the big one.
        Every bulletin still reaches the tablets live via on_event; this only
        governs the replay/poll snapshot.
        """
        prev = self.active()
        if prev is None:
            return True
        new_s = event.get("maxScale", -1)
        try:
            new_s = int(new_s)
        except (TypeError, ValueError):
            new_s = -1
        if _event_base(prev) == _event_base(event):
            return new_s >= 0
        try:
            old_s = int(prev.get("maxScale", -1))
        except (TypeError, ValueError):
            old_s = -1
        return new_s >= old_s

    def knows(self, event):
        """Have we already reported this quake (from either source)?

        The fallback uses this so a quake P2P delivered before it went down doesn't
        get replayed as breaking news when JMA reports the same one a minute later.
        """
        key = quake_key(event)
        return bool(key) and any(quake_key(e) == key for e in self.recent)
