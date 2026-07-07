"""FastAPI backend: aggregates weather + news, pushes earthquake alerts, serves the frontend."""
import asyncio
import datetime
import functools
import os
import time
from contextlib import asynccontextmanager

JST = datetime.timezone(datetime.timedelta(hours=9))

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import config
from anime import fetch_anime
from earthquake import EarthquakeService, fetch_recent_quakes, _quake_key
from fx import fetch_fx
from holiday import fetch_holidays
from news import fetch_alerts, fetch_news
from weather import fetch_hourly, fetch_weather

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

state = {"japan": None, "fx": None, "anime": None, "holiday": None}   # shared feeds
weather_cache = {}   # city id -> {"data": ..., "ts": ...}
hourly_cache = {}
ai_cache = {}        # ai source id -> {"data": [...], "ts": ...}
_locks = {}       # (cache-id, key) -> asyncio.Lock, collapses concurrent misses into one fetch
clients = set()   # connected frontend WebSockets


async def broadcast(message):
    for ws in list(clients):
        try:
            # bound each send so one wedged/half-open tablet can't stall the
            # earthquake fan-out to every other screen
            await asyncio.wait_for(ws.send_json(message), timeout=5)
        except Exception:
            clients.discard(ws)


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


def _city(cid):
    for c in config.CITIES:
        if c["id"] == cid:
            return c
    return config.CITIES[0]


async def _ensure(cache, fetch_fn, cid, ttl, empty):
    """Return cached city data, (re)fetching if missing/stale; keep last on error."""
    c = _city(cid)                       # resolve bogus/unknown ids to a known city…
    cid = c["id"]                        # …and cache under the canonical id (bounds the cache)
    ent = cache.get(cid)
    if ent and time.time() - ent["ts"] <= ttl:
        return ent["data"]
    lock = _locks.setdefault((id(cache), cid), asyncio.Lock())
    async with lock:                     # collapse a burst of concurrent misses into one upstream fetch
        ent = cache.get(cid)
        if ent and time.time() - ent["ts"] <= ttl:
            return ent["data"]
        try:
            data = await fetch_fn(c)
            cache[cid] = {"data": data, "ts": time.time()}
            return data
        except Exception:
            return ent["data"] if ent else empty


async def warm_loop():
    # Keep the default city warm so first paint is instant. Other cities are
    # fetched on request and kept fresh by the frontend's own refresh timer.
    while True:
        await _ensure(weather_cache, weather_fetch, config.DEFAULT_CITY, config.WEATHER_REFRESH, {})
        await _ensure(hourly_cache, hourly_fetch, config.DEFAULT_CITY, config.HOURLY_REFRESH, [])
        await asyncio.sleep(min(config.WEATHER_REFRESH, config.HOURLY_REFRESH))


def _ai_source(sid):
    for s in config.AI_SOURCES:
        if s["id"] == sid:
            return s
    return config.AI_SOURCES[0]


async def _fetch_ai(sid):
    src = _ai_source(sid)
    res = await fetch_news({"ai": {"mode": src["mode"], "urls": src["urls"]}}, config.NEWS_MAX_PER_CATEGORY)
    return res.get("ai", [])


async def _ensure_ai(sid):
    """Return the cached AI headlines for a source group, (re)fetching if stale;
    keep last-good on empty/error so a flaky feed never blanks the column."""
    sid = _ai_source(sid)["id"]          # normalize → cache key is bounded to configured sources
    ent = ai_cache.get(sid)
    if ent and time.time() - ent["ts"] <= config.NEWS_REFRESH:
        return ent["data"]
    lock = _locks.setdefault(("ai", sid), asyncio.Lock())
    async with lock:
        ent = ai_cache.get(sid)
        if ent and time.time() - ent["ts"] <= config.NEWS_REFRESH:
            return ent["data"]
        try:
            data = await _fetch_ai(sid)
            if data:
                ai_cache[sid] = {"data": data, "ts": time.time()}
                return data
        except Exception:
            pass
        return ent["data"] if ent else []


async def news_loop():
    while True:
        try:
            fresh = await fetch_news({"japan": config.NEWS_JAPAN}, config.NEWS_MAX_PER_CATEGORY)
            alerts = await fetch_alerts(config.ALERT_FEED, config.ALERT_KEYWORDS, config.ALERT_MAX)
            base = [it for it in fresh.get("japan", []) if not it.get("alert")]
            japan = (alerts + base)[:config.NEWS_MAX_PER_CATEGORY]
            if japan:                      # keep last-good if the feed blipped
                state["japan"] = japan
        except Exception:
            pass
        await _ensure_ai(config.DEFAULT_AI_SOURCE)   # keep the default source warm
        await asyncio.sleep(config.NEWS_REFRESH)


async def fx_loop():
    while True:
        try:
            fresh = await fetch_fx(config.FX_BASE, config.FX_QUOTE)
            if fresh:
                fresh["baseLabel"] = config.FX_BASE_LABEL
                fresh["quoteLabel"] = config.FX_QUOTE_LABEL
                state["fx"] = fresh          # keep last good on error
        except Exception:
            pass
        await asyncio.sleep(config.FX_REFRESH)


async def anime_loop():
    while True:
        try:
            fresh = await fetch_anime(config.ANIME_COUNT)
            if fresh:
                state["anime"] = fresh          # keep last good on error / empty
        except Exception:
            pass
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
                state["holiday"] = fresh          # keep last good on error
        except Exception:
            pass
        await asyncio.sleep(config.HOLIDAY_REFRESH)


async def recent_quake_loop():
    # Seed the recent-quakes list from P2P history, then keep it fresh. Live WS
    # events also prepend to eq_service.recent, so this mainly covers startup/gaps.
    while True:
        try:
            recent = await fetch_recent_quakes(config.EARTHQUAKE_RECENT_COUNT)
            if recent:
                # merge, don't overwrite: a live WS quake not yet in the REST
                # history (which lags) must not be dropped. Dedup by quake key
                # (live copy wins), then keep newest-first by origin time.
                merged = {}
                for ev in eq_service.recent + recent:
                    merged.setdefault(_quake_key(ev), ev)
                eq_service.recent = sorted(
                    merged.values(), key=lambda e: e.get("originTime") or "", reverse=True
                )[:config.EARTHQUAKE_RECENT_COUNT]
        except Exception:
            pass
        # live WS events keep the list current in real time; this poll only seeds
        # startup and heals rare WS gaps, so 30 min is plenty (polite to P2P's free API)
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(app):
    tasks = [
        asyncio.create_task(warm_loop()),
        asyncio.create_task(news_loop()),
        asyncio.create_task(fx_loop()),
        asyncio.create_task(anime_loop()),
        asyncio.create_task(holiday_loop()),
        asyncio.create_task(recent_quake_loop()),
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
    {"name": "demo", "description": "Sample events to preview the earthquake takeover screen (only mounted when `config.ENABLE_DEMO`)."},
]

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


@app.middleware("http")
async def no_store(request, call_next):
    # Force the browser to revalidate on every load so frontend edits show up on a
    # normal refresh (StaticFiles still answers 304 when unchanged — cheap).
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/config", tags=["meta"], summary="Boot-time defaults")
async def api_config():
    """The defaults the frontend boots with: UI `language`, `city`, `aiSource`, and
    the earthquake full-screen `minScale` (each tablet may override these locally)."""
    return {"language": config.DEFAULT_LANGUAGE, "city": config.DEFAULT_CITY,
            "aiSource": config.DEFAULT_AI_SOURCE, "minScale": config.EARTHQUAKE_MIN_SCALE}


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
    return await _ensure(weather_cache, weather_fetch, city or config.DEFAULT_CITY, config.WEATHER_REFRESH, {})


@app.get("/api/weather/hourly", tags=["weather"], summary="Hourly forecast strip")
async def api_weather_hourly(city: str = Query(None, description="City id from /api/cities; omitted → DEFAULT_CITY.")):
    """met.no hourly points for `city` (`HOURLY_COUNT` points, `HOURLY_STEP`h apart)."""
    return await _ensure(hourly_cache, hourly_fetch, city or config.DEFAULT_CITY, config.HOURLY_REFRESH, [])


@app.get("/api/news", tags=["news"], summary="News columns (AI + main)")
async def api_news(ai: str = Query(None, description="AI-source id from /api/ai-sources; omitted → DEFAULT_AI_SOURCE.")):
    """Returns `{ai, japan}`. `ai` is the chosen AI/tech source group; `japan` is
    Google News Top with any NERV severe alerts (`alert: true`) pinned to the front.
    Both keep last-good so a flaky feed never blanks a column."""
    return {"ai": await _ensure_ai(ai or config.DEFAULT_AI_SOURCE),
            "japan": state["japan"] or []}


@app.get("/api/fx", tags=["widgets"], summary="Exchange rate")
async def api_fx():
    """Current `FX_BASE`↔`FX_QUOTE` rate as `{base, quote, rate, updated, baseLabel, quoteLabel}` (or `{}` before the first fetch)."""
    return state["fx"] or {}


@app.get("/api/anime", tags=["widgets"], summary="Today's anime schedule")
async def api_anime():
    """Today's TV-anime broadcast list as `[{time, title}]`, chronological; late-night
    next-day shows use 24:00–29:59 notation (or `[]` before the first fetch)."""
    return state["anime"] or []


@app.get("/api/holiday", tags=["widgets"], summary="Upcoming Japanese holidays")
async def api_holiday():
    """Upcoming Japanese holidays as `[{date, name}]` (the countdown itself is computed client-side)."""
    return state["holiday"] or []


@app.get("/api/earthquake/current", tags=["earthquake"], summary="Active earthquake event")
async def api_earthquake():
    """The event currently holding the takeover screen (within its `EARTHQUAKE_HOLD_SECONDS` window), else `{}`."""
    return eq_service.active() or {}


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
            "revision": "1",
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
    clients.add(ws)
    active = eq_service.active()
    if active:
        await ws.send_json({"type": "earthquake", "event": active})
    try:
        while True:
            await ws.receive_text()   # ignore inbound; keep the socket open
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(ws)


# Serve the static frontend last so /api and /ws take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
