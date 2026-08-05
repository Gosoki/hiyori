"""HTTP + WebSocket surface, driven in-process through the ASGI app.

No live server and no lifespan, so the background refresh loops never start and
nothing here touches the network: state is seeded directly.
"""
import asyncio

import httpx
import pytest

import config
import main


@pytest.fixture
def client():
    """A factory, not a live client: each request gets its own AsyncClient so a
    test can make several without the first `async with` closing the transport."""
    def make():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://test")
    return make


def get(client, url, **kw):
    async def go():
        async with client() as c:
            return await c.get(url, **kw)
    return asyncio.run(go())


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------
def test_config_exposes_every_boot_default(client):
    body = get(client, "/api/config").json()
    assert set(body) == {"language", "city", "aiSource", "minScale", "recentCount"}
    assert body["city"] == config.DEFAULT_CITY
    assert body["recentCount"] == config.EARTHQUAKE_RECENT_COUNT


def test_cities_and_sources_match_config(client):
    cities = get(client, "/api/cities").json()
    assert [c["id"] for c in cities] == [c["id"] for c in config.CITIES]
    sources = get(client, "/api/ai-sources").json()
    assert [s["id"] for s in sources] == [s["id"] for s in config.AI_SOURCES]
    assert all("lang" in s for s in sources)


# --------------------------------------------------------------------------
# Data endpoints degrade instead of erroring
# --------------------------------------------------------------------------
@pytest.mark.parametrize("url,empty", [
    ("/api/fx", {}), ("/api/anime", []), ("/api/holiday", []),
    ("/api/earthquake/current", {}), ("/api/earthquake/latest", {}),
    ("/api/earthquake/recent", []),
])
def test_cold_start_returns_an_empty_shape_not_an_error(client, url, empty):
    main.latest.update({"fx": None, "anime": None, "holiday": None, "japan": None})
    main.eq_service.current = None
    main.eq_service.recent = []
    r = get(client, url)
    assert r.status_code == 200 and r.json() == empty


def test_widgets_serve_seeded_state(client):
    main.latest["fx"] = {"base": "CNY", "quote": "JPY", "rate": 23.3}
    main.latest["anime"] = [{"time": "21:00", "title": "x"}]
    main.latest["holiday"] = [{"date": "2026-08-11", "name": "山の日"}]
    assert get(client, "/api/fx").json()["rate"] == 23.3
    assert get(client, "/api/anime").json()[0]["time"] == "21:00"
    assert get(client, "/api/holiday").json()[0]["name"] == "山の日"


@pytest.mark.parametrize("city", ["", "nope", "__proto__", "../../etc/passwd", "x" * 5000])
def test_unknown_city_falls_back_to_the_default(client, monkeypatch, city):
    """An unbounded cache key would be a memory leak; junk must normalize."""
    calls = []

    async def fake(cfg):
        calls.append(cfg["id"])
        return {"city": cfg["city_name"]}

    monkeypatch.setattr(main.weather_feed, "fetch", fake)
    main.weather_feed.cache.clear()
    r = get(client, "/api/weather", params={"city": city})
    assert r.status_code == 200
    assert calls == [config.DEFAULT_CITY]
    assert set(main.weather_feed.cache) <= {c["id"] for c in config.CITIES}


def test_news_returns_both_columns(client, monkeypatch):
    async def fake(sid):
        return [{"title": "ai item"}]

    monkeypatch.setattr(main.ai_feed, "fetch", fake)
    main.ai_feed.cache.clear()
    main.latest["japan"] = [{"title": "jp item"}]
    body = get(client, "/api/news").json()
    assert body["ai"][0]["title"] == "ai item"
    assert body["japan"][0]["title"] == "jp item"


def test_earthquake_current_rewrites_holdfor_to_the_remaining_time(client):
    import time
    main.eq_service.current = {"kind": "quake", "id": "1", "bulletin": "r",
                               "expiresAt": time.time() + 42, "holdFor": 90}
    body = get(client, "/api/earthquake/current").json()
    assert 40 <= body["holdFor"] <= 42, "clients count down on their own clock"


def test_expired_event_is_not_served(client):
    import time
    main.eq_service.current = {"kind": "quake", "expiresAt": time.time() - 1}
    assert get(client, "/api/earthquake/current").json() == {}


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
def test_health_reports_feeds_and_the_quake_connection(client):
    body = get(client, "/api/health").json()
    assert set(body) >= {"status", "degraded", "uptime", "clients", "quake", "feeds"}
    assert body["status"] in ("ok", "degraded")
    assert set(body["quake"]) >= {"connected", "offlineFor", "fallbackActive"}


def test_health_flags_a_broken_feed(client):
    main.health("test.broken").failed(RuntimeError("boom"))
    body = get(client, "/api/health").json()
    assert "test.broken" in body["degraded"]
    assert body["status"] == "degraded"
    del main.HEALTH["test.broken"]


def test_health_flags_the_live_quake_feed_when_it_is_down(client):
    main.eq_service.connected = False
    body = get(client, "/api/health").json()
    assert "earthquake.live" in body["degraded"]


# --------------------------------------------------------------------------
# Headers and static serving
# --------------------------------------------------------------------------
def test_security_headers_on_every_response(client):
    r = get(client, "/api/config")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    # frame-ancestors is ignored inside a <meta> CSP by spec, so it must be a header
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_frontend_is_revalidated_but_the_map_asset_is_cached(client):
    assert get(client, "/index.html").headers["cache-control"] == "no-cache"
    assert "max-age=86400" in get(client, "/japan.geo.json").headers["cache-control"]


@pytest.mark.parametrize("path", [
    "/../backend/config.py", "/..%2fbackend%2fconfig.py", "/%2e%2e/backend/config.py",
    "/../../etc/passwd",
])
def test_static_mount_refuses_path_traversal(client, path):
    assert get(client, path).status_code in (403, 404)


def test_index_declares_a_strict_csp():
    from pathlib import Path
    html = (Path(main.FRONTEND_DIR) / "index.html").read_text()
    assert "default-src 'self'" in html
    assert "connect-src 'self'" in html


def test_every_script_referenced_by_index_exists():
    """The split into modules made load ORDER the only contract; a typo here is a
    blank dashboard."""
    import re
    from pathlib import Path
    fe = Path(main.FRONTEND_DIR)
    scripts = re.findall(r'<script src="([^"]+)"></script>', (fe / "index.html").read_text())
    assert scripts, "no scripts referenced"
    for s in scripts:
        assert (fe / s).exists(), f"index.html references a missing {s}"
    assert scripts[0] == "i18n.js" and scripts[1] == "core.js", scripts
    assert scripts[-1] == "app.js", "app.js runs init(); it has to load last"


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
def test_websocket_replays_an_active_event_and_survives_a_binary_frame():
    from fastapi.testclient import TestClient
    import time
    main.eq_service.current = {"kind": "quake", "id": "1", "bulletin": "r",
                               "expiresAt": time.time() + 60, "holdFor": 90}
    main.clients.clear()
    with TestClient(main.app) as _:
        pass
    client = TestClient(main.app)
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "earthquake" and msg["event"]["kind"] == "quake"
        ws.send_bytes(b"\x00\x01")          # receive_text() used to kill the socket here
        ws.send_text("ping")
    main.eq_service.current = None


def test_broadcast_fans_out_concurrently():
    """Sending in sequence made every screen wait out the timeout of each wedged one
    ahead of it — n stalled tablets meant 5n seconds before the last screen saw an EEW."""
    import time

    class Wedged:
        async def send_json(self, m):
            await asyncio.sleep(3600)

    class Fine:
        def __init__(self):
            self.at = None

        async def send_json(self, m):
            self.at = time.time()

    good = Fine()
    main.clients.clear()
    for _ in range(4):
        main.clients.add(Wedged())
    main.clients.add(good)

    start = time.time()
    asyncio.run(main.broadcast({"type": "earthquake"}))
    elapsed = time.time() - start
    assert good.at is not None, "the healthy client never received the alert"
    assert good.at - start < 1.0, "the healthy client waited behind the wedged ones"
    assert elapsed < main.SEND_TIMEOUT + 2, f"fan-out took {elapsed:.1f}s"
    assert main.clients == {good}, "wedged sockets were not evicted"
    main.clients.clear()


def test_client_cap_is_enforced():
    from fastapi.testclient import TestClient
    main.clients.clear()
    for _ in range(config.MAX_WS_CLIENTS):
        main.clients.add(object())
    client = TestClient(main.app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    main.clients.clear()


# --------------------------------------------------------------------------
# Demo endpoints
# --------------------------------------------------------------------------
@pytest.mark.skipif(not config.ENABLE_DEMO, reason="demo endpoints not mounted")
def test_demo_endpoints_are_unauthenticated_GETs(client):
    """Documenting the exposure, not endorsing it: while ENABLE_DEMO is on, a plain
    GET from anyone who can reach the port puts a fake alert on every screen."""
    assert get(client, "/api/demo/quake").json() == {"ok": True}
    assert main.eq_service.active() is not None
    main.eq_service.current = None
