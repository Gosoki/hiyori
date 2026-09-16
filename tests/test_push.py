"""The push side: severe alerts on their own clock, ordered fan-out through the
outbox, heartbeat, replay-on-connect timing, and the stale flags behind the
per-panel indicators.

Everything here is offline; main's background loops never start.
"""
import asyncio
import time

import pytest

import config
import main
from main import FeedHealth


@pytest.fixture(autouse=True)
def _isolate():
    saved_health = dict(main.HEALTH)
    saved_latest = dict(main.latest)
    main.HEALTH.clear()
    yield
    main.HEALTH.clear()
    main.HEALTH.update(saved_health)
    main.latest.clear()
    main.latest.update(saved_latest)


@pytest.fixture
def sent(monkeypatch):
    """Capture what would be pushed to the tablets."""
    out = []
    monkeypatch.setattr(main, "enqueue", out.append)
    return out


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Severe alerts
# --------------------------------------------------------------------------
def _alert(title):
    return {"title": title, "link": "", "source": "NERV", "ts": 0, "alert": True}


def test_new_alert_is_stored_and_pushed(monkeypatch, sent):
    async def fetch(*a, **kw):
        return [_alert("【津波警報】…")]

    monkeypatch.setattr(main, "fetch_alerts", fetch)
    main.latest["alerts"] = None
    assert run(main.refresh_alerts({})) is True
    assert main.latest["alerts"][0]["title"] == "【津波警報】…"
    assert sent == [{"type": "alerts", "items": main.latest["alerts"]}]
    assert main.health("alerts").report()["ok"]


def test_unchanged_alerts_are_not_pushed_again(monkeypatch, sent):
    async def fetch(*a, **kw):
        return [_alert("【津波警報】…")]

    monkeypatch.setattr(main, "fetch_alerts", fetch)
    main.latest["alerts"] = [_alert("【津波警報】…")]
    assert run(main.refresh_alerts({})) is False
    assert sent == []


def test_a_304_keeps_the_alerts_in_force(monkeypatch, sent):
    """None from fetch_alerts means "feed unchanged" — it must not be read as
    "no alerts", which would silently un-pin a warning that is still current."""
    async def fetch(*a, **kw):
        return None

    monkeypatch.setattr(main, "fetch_alerts", fetch)
    main.latest["alerts"] = [_alert("【津波警報】…")]
    assert run(main.refresh_alerts({})) is False
    assert main.latest["alerts"], "a 304 cleared the alerts"
    assert sent == []


def test_alerts_clearing_is_pushed_too(monkeypatch, sent):
    async def fetch(*a, **kw):
        return []

    monkeypatch.setattr(main, "fetch_alerts", fetch)
    main.latest["alerts"] = [_alert("【津波警報】…")]
    assert run(main.refresh_alerts({})) is True
    assert main.latest["alerts"] == []
    assert sent == [{"type": "alerts", "items": []}]


def test_alert_feed_failure_is_recorded_and_keeps_the_last_alerts(monkeypatch, sent):
    async def fetch(*a, **kw):
        raise RuntimeError("NERV down")

    monkeypatch.setattr(main, "fetch_alerts", fetch)
    main.latest["alerts"] = [_alert("【津波警報】…")]
    assert run(main.refresh_alerts({})) is False
    assert main.latest["alerts"], "an outage un-pinned a live warning"
    assert sent == []
    r = main.health("alerts").report()
    assert not r["ok"] and "NERV down" in r["lastError"]


def test_japan_column_pins_alerts_first_and_respects_the_cap():
    main.latest["alerts"] = [_alert("A1"), _alert("A2")]
    main.latest["japan"] = [{"title": f"n{i}"} for i in range(config.NEWS_MAX_PER_CATEGORY)]
    col = main.japan_column()
    assert [it["title"] for it in col[:2]] == ["A1", "A2"]
    assert len(col) == config.NEWS_MAX_PER_CATEGORY
    main.latest["alerts"] = None
    assert [it["title"] for it in main.japan_column()][:1] == ["n0"]


def test_api_news_serves_alerts_composed_at_request_time(monkeypatch):
    """Alerts arrive on a faster clock than the news loop; /api/news must show
    them without waiting for the next news refresh."""
    import httpx

    async def fake(sid):
        return [{"title": "ai"}]

    monkeypatch.setattr(main.ai_feed, "fetch", fake)
    main.ai_feed.cache.clear()
    main.latest["japan"] = [{"title": "headline"}]
    main.latest["alerts"] = [_alert("【津波警報】now")]

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as c:
            return (await c.get("/api/news")).json()

    body = run(go())
    assert body["japan"][0]["title"] == "【津波警報】now" and body["japan"][0]["alert"]
    assert body["japan"][1]["title"] == "headline"


# --------------------------------------------------------------------------
# Outbox / heartbeat
# --------------------------------------------------------------------------
def test_on_earthquake_returns_without_waiting_for_a_wedged_tablet():
    """publish() sits in the P2P reader; it must not stall on a socket."""
    class Wedged:
        async def send_json(self, m):
            await asyncio.sleep(3600)

    main.clients.clear()
    main.clients.add(Wedged())
    start = time.time()
    run(main.on_earthquake({"kind": "eew"}))
    assert time.time() - start < 0.5
    msg = main.outbox.get_nowait()
    assert msg == {"type": "earthquake", "event": {"kind": "eew"}}
    main.clients.clear()


def test_outbox_preserves_order():
    while not main.outbox.empty():
        main.outbox.get_nowait()
    main.enqueue({"n": 1})
    main.enqueue({"n": 2})
    main.enqueue({"n": 3})
    assert [main.outbox.get_nowait()["n"] for _ in range(3)] == [1, 2, 3]


def test_heartbeat_only_when_someone_is_listening(monkeypatch, sent):
    ticks = {"n": 0}

    async def fake_sleep(_):
        ticks["n"] += 1
        if ticks["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.clients.clear()
    with pytest.raises(asyncio.CancelledError):
        run(main.heartbeat_loop())
    assert sent == [], "pinged an empty room"
    main.clients.add(object())
    ticks["n"] = 0
    with pytest.raises(asyncio.CancelledError):
        run(main.heartbeat_loop())
    assert sent == [{"type": "ping"}, {"type": "ping"}]
    main.clients.clear()


# --------------------------------------------------------------------------
# Replay on connect
# --------------------------------------------------------------------------
def test_websocket_replay_carries_the_remaining_hold_not_the_full_one():
    """A tablet reconnecting 48 s into a 90 s hold must count down the last 42 s,
    exactly as /api/earthquake/current does — not restart at 90."""
    from fastapi.testclient import TestClient
    main.eq_service.current = {"kind": "quake", "id": "1", "bulletin": "r",
                               "expiresAt": time.time() + 42, "holdFor": 90}
    main.clients.clear()
    client = TestClient(main.app)
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "earthquake"
        assert 40 <= msg["event"]["holdFor"] <= 42, msg["event"]["holdFor"]
    main.eq_service.current = None
    main.clients.clear()


def test_current_event_is_none_when_nothing_is_active():
    main.eq_service.current = None
    assert main.current_event() is None


# --------------------------------------------------------------------------
# Stale flags
# --------------------------------------------------------------------------
def test_stale_is_judged_from_the_age_of_the_last_success():
    h = FeedHealth("x", stale_after=10)
    assert h.report()["stale"] is False, "never succeeded → the panel shows its own placeholder"
    h.succeeded()
    assert h.report()["stale"] is False
    h.last_ok = time.time() - 11
    assert h.report()["stale"] is True
    assert h.report()["staleAfter"] == 10
    h.succeeded()
    assert h.report()["stale"] is False


def test_feeds_get_three_refreshes_of_grace():
    assert main.weather_feed.health.stale_after == 3 * config.WEATHER_REFRESH
    assert main.health("fx").stale_after == 3 * config.FX_REFRESH
    assert main.health("anime").stale_after == 3 * config.ANIME_REFRESH
    assert main.health("holiday").stale_after == 3 * config.HOLIDAY_REFRESH
    assert main.health("news.japan").stale_after == 3 * config.NEWS_REFRESH
    assert main.health("alerts").stale_after is None, "alerts are additive; the column is not stale without them"


# --------------------------------------------------------------------------
# Conditional GET
# --------------------------------------------------------------------------
def test_conditional_get_round_trip():
    from condget import get_if_changed

    class Resp:
        def __init__(self, code, headers=None):
            self.status_code = code
            self.headers = headers or {}
            self.content = b"body"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class Client:
        def __init__(self):
            self.seen = []

        async def get(self, url, headers=None):
            self.seen.append(dict(headers or {}))
            if headers and headers.get("If-None-Match") == "E1":
                return Resp(304)
            return Resp(200, {"etag": "E1", "last-modified": "Tue, 01 Sep 2026 00:00:00 GMT"})

    c, state = Client(), {}
    assert run(get_if_changed(c, "u", state)) is not None      # first: full body
    assert state == {"etag": "E1", "last_modified": "Tue, 01 Sep 2026 00:00:00 GMT"}
    assert run(get_if_changed(c, "u", state)) is None          # second: 304
    assert c.seen[0] == {} and c.seen[1]["If-None-Match"] == "E1"
    assert c.seen[1]["If-Modified-Since"].startswith("Tue")


def test_conditional_get_raises_on_http_errors():
    from condget import get_if_changed

    class Resp:
        status_code = 503
        headers = {}

        def raise_for_status(self):
            raise RuntimeError("503")

    class Client:
        async def get(self, url, headers=None):
            return Resp()

    with pytest.raises(RuntimeError):
        run(get_if_changed(Client(), "u", {}))


# --------------------------------------------------------------------------
# Yesterday's anime is not today's
# --------------------------------------------------------------------------
def test_anime_from_a_previous_jst_day_is_not_served():
    import httpx

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as c:
            return (await c.get("/api/anime")).json()

    main.latest["anime"] = [{"time": "21:00", "title": "x"}]
    main.latest["anime_day"] = "1999-01-01"
    assert run(go()) == [], "a stale day's line-up was served as today's"
    main.latest["anime_day"] = main.datetime.datetime.now(main.JST).date().isoformat()
    assert run(go())[0]["title"] == "x"
