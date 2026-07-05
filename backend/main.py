"""FastAPI backend: aggregates weather + news, pushes earthquake alerts, serves the frontend."""
import asyncio
import functools
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import config
from earthquake import EarthquakeService, fetch_recent_quakes
from fx import fetch_fx
from news import fetch_alerts, fetch_news
from weather import fetch_hourly, fetch_weather

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

state = {"japan": None, "fx": None}   # 主要ニュース column + exchange rate (shared)
weather_cache = {}   # city id -> {"data": ..., "ts": ...}
hourly_cache = {}
ai_cache = {}        # ai source id -> {"data": [...], "ts": ...}
clients = set()   # connected frontend WebSockets


async def broadcast(message):
    for ws in list(clients):
        try:
            await ws.send_json(message)
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
    ent = cache.get(cid)
    if ent and time.time() - ent["ts"] <= ttl:
        return ent["data"]
    try:
        data = await fetch_fn(_city(cid))
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


async def recent_quake_loop():
    # Seed the recent-quakes list from P2P history, then keep it fresh. Live WS
    # events also prepend to eq_service.recent, so this mainly covers startup/gaps.
    while True:
        try:
            recent = await fetch_recent_quakes(config.EARTHQUAKE_RECENT_COUNT)
            if recent:
                eq_service.recent = recent
        except Exception:
            pass
        await asyncio.sleep(180)


@asynccontextmanager
async def lifespan(app):
    tasks = [
        asyncio.create_task(warm_loop()),
        asyncio.create_task(news_loop()),
        asyncio.create_task(fx_loop()),
        asyncio.create_task(recent_quake_loop()),
        asyncio.create_task(eq_service.run()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_store(request, call_next):
    # Force the browser to revalidate on every load so frontend edits show up on a
    # normal refresh (StaticFiles still answers 304 when unchanged — cheap).
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/config")
async def api_config():
    return {"language": config.DEFAULT_LANGUAGE, "city": config.DEFAULT_CITY,
            "aiSource": config.DEFAULT_AI_SOURCE, "minScale": config.EARTHQUAKE_MIN_SCALE}


@app.get("/api/cities")
async def api_cities():
    return [{"id": c["id"], "name": c["city_name"]} for c in config.CITIES]


@app.get("/api/ai-sources")
async def api_ai_sources():
    return [{"id": s["id"], "name": s["name"], "lang": s.get("lang", "ja")} for s in config.AI_SOURCES]


@app.get("/api/weather")
async def api_weather(city: str = None):
    return await _ensure(weather_cache, weather_fetch, city or config.DEFAULT_CITY, config.WEATHER_REFRESH, {})


@app.get("/api/weather/hourly")
async def api_weather_hourly(city: str = None):
    return await _ensure(hourly_cache, hourly_fetch, city or config.DEFAULT_CITY, config.HOURLY_REFRESH, [])


@app.get("/api/news")
async def api_news(ai: str = None):
    return {"ai": await _ensure_ai(ai or config.DEFAULT_AI_SOURCE),
            "japan": state["japan"] or []}


@app.get("/api/fx")
async def api_fx():
    return state["fx"] or {}


@app.get("/api/earthquake/current")
async def api_earthquake():
    return eq_service.active() or {}


@app.get("/api/earthquake/recent")
async def api_earthquake_recent():
    return eq_service.recent


@app.get("/api/earthquake/latest")
async def api_earthquake_latest():
    return (eq_service.recent[0] if eq_service.recent else {})


if config.ENABLE_DEMO:
    import time

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

    @app.get("/api/demo/quake")
    async def demo_quake():
        ev = _demo_event("quake")
        eq_service.current = ev
        await broadcast({"type": "earthquake", "event": ev})
        return {"ok": True}

    @app.get("/api/demo/eew")
    async def demo_eew():
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
