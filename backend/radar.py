"""雨雲レーダー: the frame list and a caching tile proxy.

Frames come from three of 気象庁's tile indexes (free, no key):
  * 高解像度降水ナウキャスト observed (targetTimes_N1) — the past, every 5 min
  * 高解像度降水ナウキャスト forecast (targetTimes_N2) — +5..+60 min from the newest
  * 降水短時間予報 (rasrf/targetTimes) — hourly rainfall out to +6 h (and beyond)
and the base map is 国土地理院's 淡色地図. Every timestamp in those indexes is UTC
"YYYYMMDDhhmmss"; the frames go out in JST.

The tablets never fetch a tile from JMA or GSI themselves (the page's CSP is
'self' only), so every tile goes through get_tile(): validated against a fixed
zoom (the city view's or the earthquake overlay's) and a Japan bounding box before any upstream request, coalesced while in
flight, and kept in one LRU bounded by total bytes. Rain tiles are keyed by their
base/valid times and never change, so they cache like static files.
"""
import asyncio
import collections
import datetime
import logging
import math
import re
import time

import httpx

import config

log = logging.getLogger("hiyori.radar")

UTC = datetime.timezone.utc
JST = datetime.timezone(datetime.timedelta(hours=9))

JMA = "https://www.jma.go.jp/bosai/jmatile/data"
N1_URL = f"{JMA}/nowc/targetTimes_N1.json"
N2_URL = f"{JMA}/nowc/targetTimes_N2.json"
RASRF_URL = f"{JMA}/rasrf/targetTimes.json"
RAIN_TILE = {
    "nowc": JMA + "/nowc/{basetime}/{member}/{validtime}/surf/hrpns/{z}/{x}/{y}.png",
    "rasrf": JMA + "/rasrf/{basetime}/{member}/{validtime}/surf/rasrf/{z}/{x}/{y}.png",
}
BASE_TILE = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"

# product → the members its URL may carry. A nowcast is always "none"; a 降水短時間
# 予報 run is "immed" (the 10-minute runs, +1..+6 h) or "none" (hourly, +7..+15 h).
MEMBERS = {"nowc": {"none"}, "rasrf": {"none", "immed"}}

FRAMES_TTL = 60          # s; JMA adds a nowcast every 5 min, the tablet asks on open
MAX_LIST_AGE = 60 * 60   # s; a newest observation older than this is last-good from a past outage, not 実況
FETCH_TIMEOUT = 15       # s, hard cap on one upstream fetch, connect to last byte
HOST_COOLDOWN = 30       # s a host that timed out / refused is answered 502 without asking it
MAX_BODY = 2 * 1024 * 1024        # a real tile is ≤ ~150 KB; anything bigger is not one
CACHE_BYTES = 32 * 1024 * 1024    # all tiles, base + rain, together
ENTRY_OVERHEAD = 256     # bytes charged per entry on top of the body (key, tuple, dict slot)
NEGATIVE_TTL = 60        # s an upstream 404 is remembered (an incomplete run fills in later)
RAIN_MAX_AGE = 86400     # browser cache for rain tiles: time-keyed, never change
BASE_MAX_AGE = 604800    # browser cache for base-map tiles
RASRF_RUN_HOURS = 6      # a complete 10-minute 降水短時間予報 run reaches +6 h
QUAKE_MIN_GAP = datetime.timedelta(minutes=30)   # quake-map outlook: an hourly run's frame this close after the last 10-minute one is skipped

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_STAMP = re.compile(r"[0-9]{14}")
_NUM = re.compile(r"[0-9]{1,6}")

# Tiles outside this box (lon W, lat S, lon E, lat N) are refused without asking
# upstream: it covers every city JMA's rain tiles do, from Yonaguni to Etorofu.
JAPAN_BBOX = (118.0, 18.0, 156.0, 50.0)

_client = None


def _http():
    # One client for the process: a radar open is a few hundred tile requests, and
    # a fresh client per request would pay a TLS handshake for each of them.
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            # identity: JMA's CloudFront caches each Accept-Encoding variant apart and
            # has been seen holding a stale 404 for the gzip one of a valid tile; PNGs
            # gain nothing from gzip anyway.
            headers={"User-Agent": "hiyori/1.0", "Accept-Encoding": "identity"}, timeout=10,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _client


async def aclose():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------
def _parse(stamp):
    """UTC datetime for a JMA "YYYYMMDDhhmmss", or None for anything else."""
    if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
        return None
    try:
        return datetime.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _frame(t, kind, tile):
    return {"time": t.astimezone(JST).isoformat(), "kind": kind, "tile": tile}


def build_frames(n1, n2, rasrf):
    """{"now", "frames"} from the three target-time lists (oldest → newest).

    Observed frames run back RADAR_PAST_MIN from the newest nowcast, every
    RADAR_STEP_MIN; the nowcast forecast continues on the same grid to +60 min; the
    hourly 降水短時間予報 then takes over up to RADAR_HOURS past the newest observation.
    Raises ValueError when there is no observation at all — that is a broken
    upstream, not a dry day, and the caller keeps its last good list instead.
    """
    step = datetime.timedelta(minutes=config.RADAR_STEP_MIN)
    zero = datetime.timedelta(0)

    obs = {}
    for e in n1:
        t = _parse(e.get("validtime"))
        if t and e.get("basetime") == e.get("validtime") and "hrpns" in (e.get("elements") or []):
            obs[t] = e["basetime"]
    if not obs:
        raise ValueError("no observed nowcast frames")
    newest = max(obs)
    past = datetime.timedelta(minutes=config.RADAR_PAST_MIN)
    frames = [_frame(t, "obs", f"/api/radar/tile/nowc/none/{b}/{b}")
              for t, b in sorted(obs.items())
              if newest - t <= past and (newest - t) % step == zero]
    now = len(frames) - 1

    # The grid is anchored on the newest observation, not on N2's own basetime: the
    # two lists are fetched separately and can straddle a 5-minute update.
    fcst = {}
    for e in n2:
        t, b = _parse(e.get("validtime")), _parse(e.get("basetime"))
        if not (t and b) or "hrpns" not in (e.get("elements") or []):
            continue
        ahead = t - newest
        if zero < ahead <= datetime.timedelta(minutes=60) and ahead % step == zero:
            if t not in fcst or b > _parse(fcst[t][0]):     # newest run wins a tie
                fcst[t] = (e["basetime"], e["validtime"])
    frames += [_frame(t, "fcst", f"/api/radar/tile/nowc/none/{b}/{v}")
               for t, (b, v) in sorted(fcst.items())]
    last = max(fcst) if fcst else newest

    run = _pick_rasrf_run(rasrf)
    if run:
        member, base, valids = run
        horizon = newest + datetime.timedelta(hours=config.RADAR_HOURS)
        frames += [_frame(t, "fcst", f"/api/radar/tile/rasrf/{member}/{base}/{v}")
                   for t, v in sorted(valids.items()) if last < t <= horizon]
    return {"now": now, "frames": frames}


def _pick_rasrf_run(entries):
    """(member, basetime, {valid dt: validtime}) of the run to show, or None.

    The newest run is often listed before it is finished (one frame, tiles 404),
    so only a run reaching +RASRF_RUN_HOURS counts. A 10-minute "immed" run is
    preferred over an hourly "none" one even when the latter is newer: the hourly
    runs list only +7..+15 h, which would leave the next six hours empty.
    """
    complete = [(member == "immed", _parse(base), member, base, valids)
                for (member, base), valids in _rasrf_runs(entries).items()
                if max(valids) >= _parse(base) + datetime.timedelta(hours=RASRF_RUN_HOURS)]
    if not complete:
        return None
    _, _, member, base, valids = max(complete, key=lambda r: (r[0], r[1]))
    return member, base, valids


def _rasrf_runs(entries):
    """{(member, basetime): {valid dt: validtime}} for every listed rasrf frame."""
    runs = {}
    for e in entries:
        member, base = e.get("member"), _parse(e.get("basetime"))
        t = _parse(e.get("validtime"))
        if base and t and member in MEMBERS["rasrf"] and "rasrf" in (e.get("elements") or []):
            runs.setdefault((member, e["basetime"]), {})[t] = e["validtime"]
    return runs


def _newest_run(runs, member, reach):
    """(basetime, valids) of the newest `member` run listing a frame at or after
    `reach(base dt)`, or None — an unfinished run lists only its first frame."""
    ok = [(_parse(b), b, v) for (m, b), v in runs.items() if m == member and max(v) >= reach(_parse(b))]
    if not ok:
        return None
    _, base, valids = max(ok)
    return base, valids



def build_quake_frames(rasrf, now):
    """The hourly rain outlook laid over the earthquake map: [{time, tile, coarse}],
    oldest → newest, from just after `now` to +RADAR_QUAKE_HOURS.

    +1..+6 h come from the newest complete 10-minute run ("immed"). Beyond that
    only the hourly runs ("none": analysis + 5-km numerical forecast to +15 h)
    reach, so the newest of those that still covers the horizon continues after
    the last immed frame — at least QUAKE_MIN_GAP later, so the loop stays hourly.
    Its frames are `coarse`. Either half may be missing; then the other stands alone.
    """
    horizon = now + datetime.timedelta(hours=config.RADAR_QUAKE_HOURS)
    runs = _rasrf_runs(rasrf)
    frames, last = [], None
    immed = _newest_run(runs, "immed", lambda b: b + datetime.timedelta(hours=RASRF_RUN_HOURS))
    if immed:
        base, valids = immed
        for t, v in sorted(valids.items()):
            if now < t <= horizon:
                frames.append({"time": t.astimezone(JST).isoformat(), "coarse": False,
                               "tile": f"/api/radar/tile/rasrf/immed/{base}/{v}"})
                last = t
    hourly = _newest_run(runs, "none", lambda _b: horizon)
    if hourly:
        base, valids = hourly
        for t, v in sorted(valids.items()):
            if now < t <= horizon and (last is None or t - last >= QUAKE_MIN_GAP):
                frames.append({"time": t.astimezone(JST).isoformat(), "coarse": True,
                               "tile": f"/api/radar/tile/rasrf/none/{base}/{v}"})
                last = t
    return frames


def is_current(data, now=None):
    """True if `data`'s newest observation is recent enough to show at all.

    The radar feed refreshes only when someone opens it, so its last-good list
    can be from any time; past MAX_LIST_AGE it is refused rather than looped as 実況
    (the tablet marks a list as stale itself well before that)."""
    frames = data.get("frames") or []
    if not frames:
        return False
    newest = datetime.datetime.fromisoformat(frames[data["now"]]["time"])
    return ((now or datetime.datetime.now(UTC)) - newest).total_seconds() <= MAX_LIST_AGE


async def _get_json(url):
    r = await asyncio.wait_for(_http().get(url), FETCH_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"{url}: expected a list")
    return data


async def fetch_frames():
    """The current frame list. All three indexes or nothing: a list missing its
    forecast half would be cached as a success and look like a dry outlook."""
    n1, n2, rasrf = await asyncio.gather(_get_json(N1_URL), _get_json(N2_URL), _get_json(RASRF_URL))
    data = build_frames(n1, n2, rasrf)
    # The earthquake map's outlook rides on the same lists, anchored on the same
    # newest observation, so /api/radar/quake and /api/radar/frames share one cache.
    newest = datetime.datetime.fromisoformat(data["frames"][data["now"]]["time"])
    data["quake"] = build_quake_frames(rasrf, newest)
    return data


# ---------------------------------------------------------------------------
# Tile proxy
# ---------------------------------------------------------------------------
def _tile_x(lon, z):
    return int((lon + 180.0) / 360.0 * (1 << z))


def _tile_y(lat, z):
    r = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * (1 << z))


def _tile_ok(z, x, y, zooms):
    """z/x/y as they arrive in the path: decimal strings, one of `zooms`, inside
    JAPAN_BBOX at that zoom. Anything else never reaches upstream."""
    if not all(isinstance(v, str) and _NUM.fullmatch(v) for v in (z, x, y)):
        return False
    z, x, y = int(z), int(x), int(y)
    if z not in zooms:
        return False
    w, s, e, n = JAPAN_BBOX
    return _tile_x(w, z) <= x <= _tile_x(e, z) and _tile_y(n, z) <= y <= _tile_y(s, z)


def rain_tile_url(product, member, basetime, validtime, z, x, y):
    """Upstream URL for a rain tile, or None if any part is invalid."""
    if member not in MEMBERS.get(product, ()):
        return None
    if not (_STAMP.fullmatch(basetime) and _STAMP.fullmatch(validtime)):
        return None
    if not _tile_ok(z, x, y, (config.RADAR_ZOOM, config.RADAR_QUAKE_ZOOM)):
        return None
    return RAIN_TILE[product].format(basetime=basetime, member=member, validtime=validtime,
                                     z=int(z), x=int(x), y=int(y))


def base_tile_url(z, x, y):
    """Upstream URL for a base-map tile (radar map only, so z8 only), or None if
    z/x/y are invalid."""
    if not _tile_ok(z, x, y, (config.RADAR_ZOOM,)):
        return None
    return BASE_TILE.format(z=int(z), x=int(x), y=int(y))


class TileCache:
    """LRU of upstream answers, bounded by total bytes rather than entry count —
    a base tile is ~100 KB and an empty rain tile ~330 B, so a count says nothing."""

    def __init__(self, max_bytes):
        self.max_bytes = max_bytes
        self.entries = collections.OrderedDict()   # url -> (status, body, expires, size)
        self.bytes = 0

    def get(self, url):
        ent = self.entries.get(url)
        if ent is None:
            return None
        if ent[2] and time.monotonic() > ent[2]:
            self._drop(url)
            return None
        self.entries.move_to_end(url)
        return ent[0], ent[1]

    def put(self, url, status, body, ttl=None):
        size = len(body) + ENTRY_OVERHEAD
        self._drop(url)
        if size > self.max_bytes:
            return
        self.entries[url] = (status, body, time.monotonic() + ttl if ttl else None, size)
        self.bytes += size
        while self.bytes > self.max_bytes:
            _, old = self.entries.popitem(last=False)
            self.bytes -= old[3]

    def _drop(self, url):
        old = self.entries.pop(url, None)
        if old:
            self.bytes -= old[3]

    def clear(self):
        self.entries.clear()
        self.bytes = 0


cache = TileCache(CACHE_BYTES)
_inflight = {}        # url -> Task; concurrent misses for one tile share one fetch
_failing = False      # log upstream trouble on the transition only
_down_until = {}      # upstream host -> monotonic time its cooldown ends


async def _download(url):
    """(status, body) from upstream; body only for a 200. Raises on transport
    errors and on a body over MAX_BODY (read in chunks, never buffered whole)."""
    async with _http().stream("GET", url) as r:
        if r.status_code != 200:
            return r.status_code, b""
        if int(r.headers.get("content-length") or 0) > MAX_BODY:
            raise ValueError("tile larger than MAX_BODY")
        buf = bytearray()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) > MAX_BODY:
                raise ValueError("tile larger than MAX_BODY")
        return 200, bytes(buf)


def _note(ok, url, why=""):
    global _failing
    if not ok and not _failing:
        log.warning("radar: tile fetch failed (%s) for %s", why, url)
    elif ok and _failing:
        log.warning("radar: tile fetches recovered")
    _failing = not ok


async def _fetch_tile(url):
    """Fetch and cache one tile. Never raises: it runs as a shared task that may
    have no one left awaiting it."""
    host = httpx.URL(url).host
    if time.monotonic() < _down_until.get(host, 0):
        return 502, b""                          # host just failed: don't make every tile wait FETCH_TIMEOUT
    try:
        status, body = await asyncio.wait_for(_download(url), FETCH_TIMEOUT)
    except (httpx.TransportError, asyncio.TimeoutError) as e:
        # unreachable / hanging host, not a per-tile answer: fail fast for a while
        _down_until[host] = time.monotonic() + HOST_COOLDOWN
        _note(False, url, f"{type(e).__name__}: {e}"[:200])
        return 502, b""
    except Exception as e:                       # noqa: BLE001 - per-tile trouble (oversized body…) → 502
        _note(False, url, f"{type(e).__name__}: {e}"[:200])
        return 502, b""
    _down_until.pop(host, None)
    if status == 404:
        cache.put(url, 404, b"", ttl=NEGATIVE_TTL)
        _note(True, url)
        return 404, b""
    if status != 200 or not body.startswith(PNG_MAGIC):
        _note(False, url, f"HTTP {status}" if status != 200 else "not a PNG")
        return 502, b""
    cache.put(url, 200, body)
    _note(True, url)
    return 200, body


async def get_tile(url):
    """(status, body) for an upstream tile URL: 200 with PNG bytes, 404, or 502."""
    hit = cache.get(url)
    if hit:
        return hit
    task = _inflight.get(url)
    if task is None:
        task = asyncio.ensure_future(_fetch_tile(url))
        _inflight[url] = task
        task.add_done_callback(lambda _t: _inflight.pop(url, None))
    # shield: a tablet that gives up (navigates away, closes the overlay) cancels
    # its own wait, not the fetch the other waiters — and the cache — depend on.
    return await asyncio.shield(task)
