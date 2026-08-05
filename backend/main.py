"""FastAPI backend: aggregates weather + news, pushes earthquake alerts, serves the frontend."""
import asyncio
import datetime
import functools
import logging
import os
import time
from contextlib import asynccontextmanager

JST = datetime.timezone(datetime.timedelta(hours=9))

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

import config
from anime import fetch_anime
from earthquake import EarthquakeService, fetch_recent_quakes, quake_key
from earthquake_jma import fetch_reports as fetch_jma_reports
from fx import fetch_fx
from holiday import fetch_holidays
from news import fetch_alerts, fetch_news
from weather import fetch_hourly, fetch_weather

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

log = logging.getLogger("hiyori")
STARTED_AT = time.time()

# Latest value from each singleton background loop (one global result, not per-key —
# those live in the Feed caches below).
latest = {"japan": None, "fx": None, "anime": None, "holiday": None}
clients = set()   # connected frontend WebSockets


# ---------------------------------------------------------------------------
# Upstream health
#
# Every failure path in this backend deliberately degrades to last-good data, so
# nothing on screen changes when an upstream dies — which is exactly why it has to
# be recorded somewhere. Without this, "the news column has been three hours stale"
# and "everything is fine" look identical from the outside.
# ---------------------------------------------------------------------------
class FeedHealth:
    """Success/failure bookkeeping for one upstream, behind /api/health."""

    def __init__(self, name):
        self.name = name
        self.last_ok = 0.0
        self.last_error = ""
        self.last_error_ts = 0.0
        self.fails = 0          # CONSECUTIVE failures; reset by the next success
        self.successes = 0

    def succeeded(self):
        if self.fails:
            log.warning("%s: recovered after %d consecutive failures", self.name, self.fails)
        self.fails = 0
        self.successes += 1
        self.last_ok = time.time()

    def failed(self, exc):
        self.fails += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:200] if exc else "empty response"
        self.last_error_ts = time.time()
        if self.fails == 1:
            # Log the transition only. An upstream that is down for a day would
            # otherwise write thousands of identical lines into the journal.
            log.warning("%s: upstream failed (%s)", self.name, self.last_error)

    def report(self):
        return {
            "ok": self.fails == 0 and self.successes > 0,
            "lastOkAge": round(time.time() - self.last_ok) if self.last_ok else None,
            "consecutiveFails": self.fails,
            "lastError": self.last_error,
            "lastErrorAge": round(time.time() - self.last_error_ts) if self.last_error_ts else None,
        }


HEALTH = {}


def health(name):
    return HEALTH.setdefault(name, FeedHealth(name))


FAIL_COOLDOWN = 30   # after a failure, don't re-hit a sick upstream for this long


class Feed:
    """A keyed, TTL'd cache in front of one upstream fetcher.

    Guarantees for callers: never raises, never returns nothing. On a failure it
    hands back the last good value (or `cold_value` if there has never been one) and
    starts a short cooldown, so an upstream outage can't be amplified into a
    request hammer by tablets that keep polling.

    `empty_is_failure` is the one behavioural knob: an empty AI-news column is
    always a broken fetch, whereas an empty holiday list is a legitimate answer.
    """

    def __init__(self, name, ttl, fetch, cold_value, empty_is_failure=False):
        self.name = name
        self.ttl = ttl
        self.fetch = fetch
        self.cold_value = cold_value   # served only before the first success ever
        self.empty_is_failure = empty_is_failure
        self.cache = {}      # key -> {"data": ..., "ts": ...}
        self.locks = {}      # key -> Lock, collapses concurrent misses into one fetch
        self.fail_ts = {}    # key -> when the last attempt failed
        self.health = health(name)

    def _fresh(self, key):
        ent = self.cache.get(key)
        return ent if ent and time.time() - ent["ts"] <= self.ttl else None

    def _last_good(self, key):
        ent = self.cache.get(key)
        return ent["data"] if ent else self.cold_value

    async def get(self, key, *args):
        """Cached value for `key`; `*args` are passed to the fetcher (default: key)."""
        ent = self._fresh(key)
        if ent:
            return ent["data"]
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            ent = self._fresh(key)                     # someone may have filled it while we waited
            if ent:
                return ent["data"]
            if time.time() - self.fail_ts.get(key, 0) < FAIL_COOLDOWN:
                return self._last_good(key)
            try:
                data = await self.fetch(*(args or (key,)))
            except Exception as e:
                self.fail_ts[key] = time.time()
                self.health.failed(e)
                return self._last_good(key)
            if not data and self.empty_is_failure:
                self.fail_ts[key] = time.time()
                self.health.failed(None)
                return self._last_good(key)
            self.cache[key] = {"data": data, "ts": time.time()}
            self.health.succeeded()
            return data


SEND_TIMEOUT = 5   # seconds a single tablet gets to accept a push before it's dropped


async def _send_or_drop(ws, message):
    try:
        # bound each send so one wedged/half-open tablet can't stall the
        # earthquake fan-out to every other screen
        await asyncio.wait_for(ws.send_json(message), timeout=SEND_TIMEOUT)
    except Exception:
        clients.discard(ws)


async def broadcast(message):
    # Fan out CONCURRENTLY. Sending in sequence would make every screen wait out
    # the timeout of each wedged one ahead of it (n stalled tablets = 5n seconds
    # before the last healthy screen sees an EEW); this caps the whole fan-out at
    # one SEND_TIMEOUT no matter how many sockets are half-open.
    targets = list(clients)
    if targets:
        await asyncio.gather(*(_send_or_drop(ws, message) for ws in targets))


async def on_earthquake(event):
    await broadcast({"type": "earthquake", "event": event})


eq_service = EarthquakeService(
    config.P2P_WS_URL, config.EARTHQUAKE_HOLD_SECONDS,
    on_earthquake, show_test=config.EARTHQUAKE_SHOW_TEST,
    recent_cap=config.EARTHQUAKE_RECENT_COUNT,
)

# fetchers bound to their configured counts (per-city)
weather_fetch = functools.partial(fetch_weather, weekly_count=config.WEEKLY_COUNT)
hourly_fetch = functools.partial(fetch_hourly, count=config.HOURLY_COUNT, step=config.HOURLY_STEP)


def find_city(cid):
    for c in config.CITIES:
        if c["id"] == cid:
            return c
    return config.CITIES[0]


def find_ai_source(sid):
    for s in config.AI_SOURCES:
        if s["id"] == sid:
            return s
    return config.AI_SOURCES[0]


async def _fetch_ai(sid):
    src = find_ai_source(sid)
    res = await fetch_news({"ai": {"mode": src["mode"], "urls": src["urls"]}}, config.NEWS_MAX_PER_CATEGORY)
    return res.get("ai", [])


# Per-key caches. Keys are normalized to a configured id before lookup, so a client
# passing junk can neither miss the cache forever nor grow it without bound.
weather_feed = Feed("weather", config.WEATHER_REFRESH, weather_fetch, {})
hourly_feed = Feed("weather.hourly", config.HOURLY_REFRESH, hourly_fetch, [])
ai_feed = Feed("news.ai", config.NEWS_REFRESH, _fetch_ai, [], empty_is_failure=True)


async def city_weather(feed, cid):
    c = find_city(cid)                      # unknown id → default city, cached under its canonical id
    return await feed.get(c["id"], c)


async def ai_news(sid):
    return await ai_feed.get(find_ai_source(sid)["id"])


async def warm_loop():
    # Keep the default city warm so first paint is instant. Other cities are
    # fetched on request and kept fresh by the frontend's own refresh timer.
    while True:
        await city_weather(weather_feed, config.DEFAULT_CITY)
        await city_weather(hourly_feed, config.DEFAULT_CITY)
        await asyncio.sleep(min(config.WEATHER_REFRESH, config.HOURLY_REFRESH))


async def news_loop():
    while True:
        try:
            fresh = await fetch_news({"japan": config.NEWS_JAPAN}, config.NEWS_MAX_PER_CATEGORY)
            alerts = await fetch_alerts(config.ALERT_FEED, config.ALERT_KEYWORDS, config.ALERT_MAX)
            base = [it for it in fresh.get("japan", []) if not it.get("alert")]
            japan = (alerts + base)[:config.NEWS_MAX_PER_CATEGORY]
            if japan:                      # keep last-good if the feed blipped
                latest["japan"] = japan
                health("news.japan").succeeded()
            else:
                health("news.japan").failed(None)
        except Exception as e:
            health("news.japan").failed(e)
        await ai_news(config.DEFAULT_AI_SOURCE)   # keep the default source warm
        await asyncio.sleep(config.NEWS_REFRESH)


async def fx_loop():
    while True:
        try:
            fresh = await fetch_fx(config.FX_BASE, config.FX_QUOTE)
            if fresh:
                fresh["baseLabel"] = config.FX_BASE_LABEL
                fresh["quoteLabel"] = config.FX_QUOTE_LABEL
                latest["fx"] = fresh          # keep last good on error
                health("fx").succeeded()
            else:
                health("fx").failed(None)
        except Exception as e:
            health("fx").failed(e)
        await asyncio.sleep(config.FX_REFRESH)


async def anime_loop():
    while True:
        ok = False
        try:
            fresh = await fetch_anime(config.ANIME_COUNT)
            if fresh:
                latest["anime"] = fresh          # keep last good on error / empty
                ok = True
                health("anime").succeeded()
            else:
                health("anime").failed(None)
        except Exception as e:
            health("anime").failed(e)
        if not ok:
            # Jikan blipped (it 504s now and then). Waiting for the next 6h
            # boundary would leave the widget empty/stale for hours — retry soon.
            await asyncio.sleep(600)
            continue
        # sleep to the next ANIME_REFRESH boundary in JST (00/06/12/18) so the
        # broadcast day rolls right at midnight rather than drifting.
        now = datetime.datetime.now(JST)
        sod = now.hour * 3600 + now.minute * 60 + now.second
        await asyncio.sleep(max(60, config.ANIME_REFRESH - (sod % config.ANIME_REFRESH)))


async def holiday_loop():
    while True:
        try:
            fresh = await fetch_holidays()
            if fresh:
                latest["holiday"] = fresh          # keep last good on error
                health("holiday").succeeded()
            else:
                health("holiday").failed(None)
        except Exception as e:
            health("holiday").failed(e)
        await asyncio.sleep(config.HOLIDAY_REFRESH)


async def quake_fallback_loop():
    """Poll 気象庁's XML feed whenever the P2P WebSocket has been down too long.

    P2P is the only live quake source, and every failure path in this backend is
    silent by design — so an outage would otherwise mean the one safety-relevant
    feature simply stops, with nothing on screen to say so. This keeps 地震情報
    flowing (EEW cannot be recovered this way; JMA's public feed has none).
    """
    if not config.EARTHQUAKE_FALLBACK_AFTER:
        return                                    # fallback disabled in config
    seen = set()                                  # report URLs already accounted for
    active = False
    while True:
        await asyncio.sleep(config.EARTHQUAKE_FALLBACK_POLL)
        offline = eq_service.offline_for()
        if offline < config.EARTHQUAKE_FALLBACK_AFTER:
            if active:
                log.warning("P2P is back; standing down the JMA fallback")
                active = False
            continue
        if not active:
            log.warning("P2P offline for %ds — falling back to the JMA XML feed "
                        "(地震情報 only, no EEW)", round(offline))
            active = True
        try:
            events = await fetch_jma_reports(config.JMA_QUAKE_FEED, seen)
            health("earthquake.jma").succeeded()
        except Exception as e:
            health("earthquake.jma").failed(e)
            continue
        for event in events:
            if eq_service.knows(event):
                continue                          # P2P already reported it before going down
            log.warning("JMA fallback: publishing %s %s",
                        event.get("originTime"), event.get("hypocenter", {}).get("name"))
            await eq_service.publish(event, source="jma")


async def recent_quake_loop():
    # Seed the recent-quakes list from P2P history, then keep it fresh. Live WS
    # events also prepend to eq_service.recent, so this mainly covers startup/gaps.
    while True:
        try:
            recent = await fetch_recent_quakes(config.EARTHQUAKE_RECENT_COUNT)
            health("earthquake.history").succeeded()
            if recent:
                # merge, don't overwrite: a live WS quake not yet in the REST
                # history (which lags) must not be dropped. Dedup by quake key
                # (live copy wins), then keep newest-first by origin time.
                merged = {}
                for ev in eq_service.recent + recent:
                    merged.setdefault(quake_key(ev), ev)
                eq_service.recent = sorted(
                    merged.values(), key=lambda e: e.get("originTime") or "", reverse=True
                )[:config.EARTHQUAKE_RECENT_COUNT]
        except Exception as e:
            health("earthquake.history").failed(e)
        # live WS events keep the list current in real time; this poll only seeds
        # startup and heals rare WS gaps, so 30 min is plenty (polite to P2P's free API)
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app):
    if config.ENABLE_DEMO:
        # Loud on purpose: while this is on, anyone who can reach the port can put a
        # fake earthquake full-screen on every tablet with a plain GET. Easy to
        # forget after setup, so say it in journalctl on every boot.
        logging.getLogger("uvicorn.error").warning(
            "ENABLE_DEMO is on — /api/demo/quake and /api/demo/eew can trigger a "
            "full-screen earthquake alert on every screen, with no authentication. "
            "Set ENABLE_DEMO = False in config.py once you have finished previewing."
        )
    tasks = [
        asyncio.create_task(warm_loop()),
        asyncio.create_task(news_loop()),
        asyncio.create_task(fx_loop()),
        asyncio.create_task(anime_loop()),
        asyncio.create_task(holiday_loop()),
        asyncio.create_task(recent_quake_loop()),
        asyncio.create_task(quake_fallback_loop()),
        asyncio.create_task(eq_service.run()),
    ]
    yield
    for t in tasks:
        t.cancel()


TAGS_METADATA = [
    {"name": "meta", "description": "Boot-time defaults and the selectable city / AI-source lists the frontend reads on startup."},
    {"name": "weather", "description": "気象庁 (JMA) today + weekly forecast and met.no hourly strip. Cached per city; keeps last-good on upstream failure."},
    {"name": "news", "description": "主要ニュース (Google News Top, with NERV severe alerts pinned) and the switchable AI・テック column."},
    {"name": "widgets", "description": "Bottom-bar widgets: 為替 (open.er-api), 新番 anime schedule (Jikan), and 祝日 holidays (holidays-jp)."},
    {"name": "earthquake", "description": "P2P地震情報: the active/most-recent quakes over HTTP, plus live EEW/quake push over the `/ws` WebSocket."},
    {"name": "ops", "description": "Liveness of every upstream. Nothing on screen changes when a feed dies (each column keeps its last-good data), so this is the only place a silent outage is visible."},
]

if config.ENABLE_DEMO:
    # Listed only when the routes actually exist, so /docs never shows an empty
    # section for endpoints that aren't mounted.
    TAGS_METADATA.append(
        {"name": "demo", "description": "Sample events to preview the earthquake takeover screen. "
                                        "Unauthenticated GETs — mounted only while `config.ENABLE_DEMO` is on."})

app = FastAPI(
    title="日和 Hiyori API",
    version="1.0.0",
    description=(
        "Backend for **日和 Hiyori**, an always-on tablet information dashboard.\n\n"
        "Aggregates weather (JMA + met.no), news (Google News Top + NERV severe alerts), "
        "AI/tech headlines, exchange rate, anime schedule, Japanese holidays, and live "
        "earthquake / EEW alerts — **all from free, keyless sources**. Every column keeps "
        "its last-good data if an upstream fails, so the display never goes blank.\n\n"
        "* The tablet frontend (SPA) is served at `/`.\n"
        "* Live earthquakes/EEW are pushed over the WebSocket at `/ws` as "
        "`{\"type\":\"earthquake\",\"event\":{…}}` — on connect, any still-active event is "
        "replayed immediately. See the **earthquake** tag for the event shape.\n\n"
        "All tunables live in `config.py`; restart the backend after editing."
    ),
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)


# Compress before anything else runs, so it wraps every response below. Worth it
# for exactly one file — japan.geo.json is 600 KB of coordinates that shrinks by
# ~5× — but the API payloads ride along for free. Below 1 KB compression costs
# more than it saves.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Generated, effectively immutable asset: regenerating it means re-running
# tools/build_map.py by hand. A day of caching saves a 600 KB revalidation on every
# tablet reload, and still picks up a rebuild by the next day.
LONG_CACHED = {"/japan.geo.json"}


@app.middleware("http")
async def security_and_cache_headers(request, call_next):
    response = await call_next(request)
    # Force the browser to revalidate on every load so frontend edits show up on a
    # normal refresh (StaticFiles still answers 304 when unchanged — cheap).
    response.headers["Cache-Control"] = (
        "public, max-age=86400" if request.url.path in LONG_CACHED else "no-cache"
    )
    # index.html carries the content CSP in a <meta>; frame-ancestors is ignored
    # there by spec, so it has to be a real header. Enforced alongside the meta
    # policy (multiple CSPs intersect), and harmless for /docs.
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/api/config", tags=["meta"], summary="Boot-time defaults")
async def api_config():
    """The defaults the frontend boots with: UI `language`, `city`, `aiSource`, the
    earthquake full-screen `minScale` (each tablet may override these locally), and
    `recentCount` so the 🗾 browse list caps at the same length the backend keeps."""
    return {"language": config.DEFAULT_LANGUAGE, "city": config.DEFAULT_CITY,
            "aiSource": config.DEFAULT_AI_SOURCE, "minScale": config.EARTHQUAKE_MIN_SCALE,
            "recentCount": config.EARTHQUAKE_RECENT_COUNT}


@app.get("/api/health", tags=["ops"], summary="Upstream liveness")
async def api_health():
    """Per-upstream liveness plus the live quake feed's connection state.

    `status` is `ok` only when every feed has succeeded at least once and none is
    currently failing; otherwise `degraded`. Ages are in seconds. This is the one
    endpoint that distinguishes "the dashboard is fine" from "the dashboard has been
    showing you three-hour-old data" — the display itself looks identical either way.
    """
    feeds = {name: h.report() for name, h in sorted(HEALTH.items())}
    quake = eq_service.status()
    quake["fallbackActive"] = bool(
        config.EARTHQUAKE_FALLBACK_AFTER
        and not quake["connected"]
        and quake["offlineFor"] >= config.EARTHQUAKE_FALLBACK_AFTER
    )
    degraded = [n for n, f in feeds.items() if not f["ok"]]
    return {
        "status": "ok" if not degraded and quake["connected"] else "degraded",
        "degraded": degraded + ([] if quake["connected"] else ["earthquake.live"]),
        "uptime": round(time.time() - STARTED_AT),
        "clients": len(clients),
        "quake": quake,
        "feeds": feeds,
    }


@app.get("/api/cities", tags=["meta"], summary="Selectable cities")
async def api_cities():
    """The cities offered in Settings, as `{id, name}` (configured in `config.CITIES`)."""
    return [{"id": c["id"], "name": c["city_name"]} for c in config.CITIES]


@app.get("/api/ai-sources", tags=["meta"], summary="Selectable AI-news sources")
async def api_ai_sources():
    """The AI・テック source groups offered in Settings, as `{id, name, lang}`
    (`lang` picks the column's font; configured in `config.AI_SOURCES`)."""
    return [{"id": s["id"], "name": s["name"], "lang": s.get("lang", "ja")} for s in config.AI_SOURCES]


@app.get("/api/weather", tags=["weather"], summary="Today + weekly forecast")
async def api_weather(city: str = Query(None, description="City id from /api/cities; omitted → DEFAULT_CITY.")):
    """JMA today + weekly forecast for `city`. Cached for `WEATHER_REFRESH`s;
    returns the last-good payload (or `{}` on a cold-start upstream failure)."""
    return await city_weather(weather_feed, city or config.DEFAULT_CITY)


@app.get("/api/weather/hourly", tags=["weather"], summary="Hourly forecast strip")
async def api_weather_hourly(city: str = Query(None, description="City id from /api/cities; omitted → DEFAULT_CITY.")):
    """met.no hourly points for `city` (`HOURLY_COUNT` points, `HOURLY_STEP`h apart)."""
    return await city_weather(hourly_feed, city or config.DEFAULT_CITY)


@app.get("/api/news", tags=["news"], summary="News columns (AI + main)")
async def api_news(ai: str = Query(None, description="AI-source id from /api/ai-sources; omitted → DEFAULT_AI_SOURCE.")):
    """Returns `{ai, japan}`. `ai` is the chosen AI/tech source group; `japan` is
    Google News Top with any NERV severe alerts (`alert: true`) pinned to the front.
    Both keep last-good so a flaky feed never blanks a column."""
    return {"ai": await ai_news(ai or config.DEFAULT_AI_SOURCE),
            "japan": latest["japan"] or []}


@app.get("/api/fx", tags=["widgets"], summary="Exchange rate")
async def api_fx():
    """Current `FX_BASE`↔`FX_QUOTE` rate as `{base, quote, rate, updated, baseLabel, quoteLabel}` (or `{}` before the first fetch)."""
    return latest["fx"] or {}


@app.get("/api/anime", tags=["widgets"], summary="Today's anime schedule")
async def api_anime():
    """Today's TV-anime broadcast list as `[{time, title}]`, chronological; late-night
    next-day shows use 24:00–29:59 notation (or `[]` before the first fetch)."""
    return latest["anime"] or []


@app.get("/api/holiday", tags=["widgets"], summary="Upcoming Japanese holidays")
async def api_holiday():
    """Upcoming Japanese holidays as `[{date, name}]` (the countdown itself is computed client-side)."""
    return latest["holiday"] or []


@app.get("/api/earthquake/current", tags=["earthquake"], summary="Active earthquake event")
async def api_earthquake():
    """The event currently holding the takeover screen (within its `EARTHQUAKE_HOLD_SECONDS` window), else `{}`.
    `holdFor` is rewritten to the REMAINING seconds so clients can count down on
    their own clock (robust against tablet/server clock skew)."""
    ev = eq_service.active()
    if not ev:
        return {}
    out = dict(ev)
    out["holdFor"] = max(1, round(ev["expiresAt"] - time.time()))
    return out


@app.get("/api/earthquake/recent", tags=["earthquake"], summary="Recent quakes (browsable)")
async def api_earthquake_recent():
    """The last `EARTHQUAKE_RECENT_COUNT` distinct quakes (newest first) that the 🗾 button lets you browse."""
    return eq_service.recent


@app.get("/api/earthquake/latest", tags=["earthquake"], summary="Most recent quake")
async def api_earthquake_latest():
    """The single most recent quake (or `{}` if none recorded yet)."""
    return (eq_service.recent[0] if eq_service.recent else {})


if config.ENABLE_DEMO:
    def _demo_event(kind):
        base = {
            "kind": kind,
            "id": "demo",
            "bulletin": "1",
            "originTime": "2026/07/02 14:30:00",
            "hypocenter": {"name": "東京湾", "depth": 30, "magnitude": 6.1,
                           "latitude": 35.5, "longitude": 139.8},
            "maxScale": 50,
            "maxIntensity": "5強",
            "tsunami": "None" if kind == "quake" else "Unknown",
            "regions": [
                {"name": "東京都", "scale": 50, "label": "5強"},
                {"name": "神奈川県", "scale": 45, "label": "5弱"},
                {"name": "千葉県", "scale": 40, "label": "4"},
                {"name": "茨城県", "scale": 40, "label": "4"},
                {"name": "埼玉県", "scale": 30, "label": "3"},
                {"name": "群馬県", "scale": 30, "label": "3"},
                {"name": "栃木県", "scale": 30, "label": "3"},
                {"name": "静岡県", "scale": 20, "label": "2"},
                {"name": "山梨県", "scale": 20, "label": "2"},
            ],
            "cancelled": False,
            "receivedAt": "2026-07-02T14:30:05+09:00",
            "expiresAt": time.time() + config.EARTHQUAKE_HOLD_SECONDS,
            "holdFor": config.EARTHQUAKE_HOLD_SECONDS,
        }
        return base

    @app.get("/api/demo/quake", tags=["demo"], summary="Preview a 地震情報 takeover")
    async def demo_quake():
        """Inject a sample 地震情報 (震度5強) and push it to all screens for `EARTHQUAKE_HOLD_SECONDS`."""
        ev = _demo_event("quake")
        eq_service.current = ev
        await broadcast({"type": "earthquake", "event": ev})
        return {"ok": True}

    @app.get("/api/demo/eew", tags=["demo"], summary="Preview an EEW takeover")
    async def demo_eew():
        """Inject a sample 緊急地震速報 (EEW, red pulse) and push it to all screens."""
        ev = _demo_event("eew")
        eq_service.current = ev
        await broadcast({"type": "earthquake", "event": ev})
        return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if len(clients) >= config.MAX_WS_CLIENTS:
        # A handful of wall screens is the intended scale; refusing past the cap
        # keeps a looping/runaway client from growing the fan-out set without bound.
        log.warning("refusing /ws: already at MAX_WS_CLIENTS (%d)", config.MAX_WS_CLIENTS)
        await ws.close(code=1013)      # 1013 = try again later
        return
    clients.add(ws)
    try:
        # replay inside try/finally: a failed send must not leak the socket into `clients`
        active = eq_service.active()
        if active:
            await ws.send_json({"type": "earthquake", "event": active})
        while True:
            # receive() (not receive_text()) so a binary frame — which has no
            # "text" key — doesn't kill an otherwise healthy screen's socket.
            # Nothing inbound is meaningful; we only read to notice the close.
            if (await ws.receive()).get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(ws)


# Serve the static frontend last so /api and /ws take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
