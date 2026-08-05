"""Contract tests against the real upstreams — `pytest -m live`.

Everything else in this suite runs offline against captured payloads, which cannot
notice the thing most likely to break this dashboard: a free API quietly changing
its shape. These do exactly that, and nothing else. They are deselected by default
because they are slow and go red for reasons that are not our fault.
"""
import asyncio

import pytest

import config

pytestmark = pytest.mark.live


def run(coro):
    return asyncio.run(coro)


def test_jma_forecast_still_parses_for_every_configured_city():
    from weather import fetch_weather
    for city in config.CITIES:
        out = run(fetch_weather(city, config.WEEKLY_COUNT))
        assert out["city"] == city["city_name"]
        assert out["today"]["icon"], f'{city["id"]}: no icon'
        assert out["weekly"], f'{city["id"]}: empty weekly strip'


def test_jma_area_codes_are_all_still_valid():
    """A stale class10_code would silently serve a neighbouring region's numbers."""
    import httpx
    for city in config.CITIES:
        r = httpx.get(f'https://www.jma.go.jp/bosai/forecast/data/forecast/{city["area_code"]}.json',
                      timeout=30, headers={"User-Agent": "hiyori/1.0"})
        r.raise_for_status()
        codes = {a["area"]["code"] for a in r.json()[0]["timeSeries"][0]["areas"]}
        assert city["class10_code"] in codes, \
            f'{city["id"]}: class10_code {city["class10_code"]} is not in {sorted(codes)}'


def test_metno_hourly_still_parses():
    from weather import fetch_hourly
    out = run(fetch_hourly(config.CITIES[0], count=12, step=2))
    assert len(out) >= 6
    assert all("hour" in h and "icon" in h for h in out)


def test_every_configured_news_feed_still_returns_headlines():
    from news import fetch_news
    for src in config.AI_SOURCES:
        out = run(fetch_news({"ai": {"mode": src["mode"], "urls": src["urls"]}},
                             config.NEWS_MAX_PER_CATEGORY))
        assert out["ai"], f'{src["id"]}: no headlines'
    out = run(fetch_news({"japan": config.NEWS_JAPAN}, config.NEWS_MAX_PER_CATEGORY))
    assert out["japan"]


def test_nerv_alert_feed_is_reachable():
    from news import fetch_alerts
    run(fetch_alerts(config.ALERT_FEED, config.ALERT_KEYWORDS, config.ALERT_MAX))   # [] is the normal answer


def test_fx_anime_holiday_still_parse():
    from fx import fetch_fx
    from anime import fetch_anime
    from holiday import fetch_holidays
    rate = run(fetch_fx(config.FX_BASE, config.FX_QUOTE))
    assert rate["rate"] > 0
    assert run(fetch_anime(config.ANIME_COUNT))
    assert run(fetch_holidays())


def test_p2p_history_still_parses():
    from earthquake import fetch_recent_quakes
    out = run(fetch_recent_quakes(5))
    assert out and out[0]["hypocenter"]["name"]


def test_jma_quake_fallback_still_parses():
    from earthquake_jma import fetch_reports
    seen = set()
    run(fetch_reports(config.JMA_QUAKE_FEED, seen))     # priming pass
    assert any("VXSE53" in u for u in seen), "no 震源・震度に関する情報 in the JMA feed"


def test_the_two_quake_sources_still_agree_on_the_same_quake():
    """The fallback is only safe while both sources key a quake identically — P2P's
    `time` is JMA's ArrivalTime. If JMA changes that, quakes would list twice."""
    import httpx
    import xml.etree.ElementTree as ET
    import earthquake_jma as J
    from earthquake import normalize_quake, quake_key

    with httpx.Client(timeout=30, headers=J.UA, follow_redirects=True) as c:
        feed = ET.fromstring(c.get("https://www.data.jma.go.jp/developer/xml/feed/eqvol_l.xml").content)
        urls = [e.find(J.ATOM + "link").get("href") for e in feed.findall(J.ATOM + "entry")]
        jma = {}
        for u in [x for x in urls if "VXSE53" in x][:6]:
            ev = J.parse_report(c.get(u).content)
            if ev:
                jma[quake_key(ev)] = ev
        p2p = {}
        for m in c.get("https://api.p2pquake.net/v2/history",
                       params={"codes": 551, "limit": 40}).json():
            ev = normalize_quake(m)
            p2p.setdefault(quake_key(ev), ev)

    overlap = set(jma) & set(p2p)
    assert overlap, "no quake reported by both sources — cannot verify the key"
    for key in overlap:
        a, b = jma[key], p2p[key]
        assert a["hypocenter"]["name"] == b["hypocenter"]["name"], key
        assert a["maxScale"] == b["maxScale"], key
