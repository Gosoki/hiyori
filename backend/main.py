"""FastAPI backend: aggregates weather + news, pushes earthquake alerts, serves the frontend."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import config
from earthquake import EarthquakeService, fetch_recent_quakes
from news import fetch_news
from weather import fetch_weather

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

state = {"weather": None, "news": None}
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


async def weather_loop():
    while True:
        try:
            state["weather"] = await fetch_weather(config.WEATHER)
        except Exception:
            pass
        await asyncio.sleep(config.WEATHER_REFRESH)


async def news_loop():
    while True:
        try:
            state["news"] = await fetch_news(config.NEWS_FEEDS, config.NEWS_MAX_PER_CATEGORY)
        except Exception:
            pass
        await asyncio.sleep(config.NEWS_REFRESH)


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
        asyncio.create_task(weather_loop()),
        asyncio.create_task(news_loop()),
        asyncio.create_task(recent_quake_loop()),
        asyncio.create_task(eq_service.run()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/api/config")
async def api_config():
    return {"language": config.DEFAULT_LANGUAGE, "city": config.WEATHER["city_name"]}


@app.get("/api/weather")
async def api_weather():
    return state["weather"] or {}


@app.get("/api/news")
async def api_news():
    return state["news"] or {}


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
