"""JMA forecast parsing and the met.no hourly strip.

JMA keys the halves of one response differently (class10 sub-region codes for
weather/precipitation, AMeDAS observatory codes for temperature) and drops today's
high/low partway through the day. Both have produced visible wrong numbers on the
dashboard, so both are pinned here.
"""
import datetime

import pytest

import weather as W
from conftest import load_json


@pytest.fixture
def cfg():
    import config
    return config.CITIES[0]


# The JMA fixtures were captured on this day; parse them AS OF that day, or the
# weekly strip (which drops today and the past) empties as soon as the fixture ages.
CAPTURED = "2026-08-05"


# --------------------------------------------------------------------------
# Whole-response parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("city", ["tokyo", "nagoya", "naha"])
def test_parses_real_responses(city):
    import config
    cfg = next(c for c in config.CITIES if c["id"] == city)
    out = W._parse(load_json(f"jma_forecast_{city}.json"), cfg, today=CAPTURED)
    assert out["city"] == cfg["city_name"]
    assert out["today"]["icon"] and out["today"]["text"]
    assert out["weekly"], "weekly strip is empty"
    for day in out["weekly"]:
        assert len(day["date"]) == 10
        assert day["icon"]
        for key in ("tempMax", "tempMin", "pop"):
            assert day[key] is None or isinstance(day[key], int)


def test_weekly_never_includes_today_or_the_past(jma_tokyo, cfg):
    out = W._parse(jma_tokyo, cfg, today=CAPTURED)
    assert out["weekly"], "fixture should yield a weekly strip as of its capture day"
    assert all(d["date"] > CAPTURED for d in out["weekly"])
    # and parsed as of today (the production path) it must simply not raise
    live = W._parse(jma_tokyo, cfg)
    today = datetime.datetime.now(W.JST).date().isoformat()
    assert all(d["date"] > today for d in live["weekly"])


def test_full_width_spaces_are_stripped_from_the_forecast_text(jma_tokyo, cfg):
    """JMA pads the text with 　 at odd points; Japanese has no spaces."""
    assert "　" not in W._parse(jma_tokyo, cfg)["today"]["text"]


# --------------------------------------------------------------------------
# Area matching — the codes come from two different code spaces
# --------------------------------------------------------------------------
def test_precipitation_follows_the_configured_subregion():
    """timeSeries[1] is keyed by class10 code. Taking areas[0] happens to work for
    every shipped city, but silently reads the wrong region for an added one."""
    areas = [
        {"area": {"name": "東部", "code": "999999"}, "pops": ["10"]},
        {"area": {"name": "西部", "code": "130010"}, "pops": ["90"]},
    ]
    assert W._match_area(areas, "130010")["pops"] == ["90"]


def test_temperature_follows_the_observatory_name():
    """timeSeries[2] is keyed by AMeDAS station code, not class10 — match by name."""
    areas = [
        {"area": {"name": "八丈島", "code": "44263"}, "temps": ["1"]},
        {"area": {"name": "東京", "code": "44132"}, "temps": ["2"]},
    ]
    assert W._match_station(areas, "東京")["temps"] == ["2"]


@pytest.mark.parametrize("areas", [[], None, [None], ["x"], [{"area": None}]])
def test_area_lookup_degrades_instead_of_raising(areas):
    assert W._match_area(areas, "130010") in ({}, areas[0] if areas and isinstance(areas[0], dict) else {})


def test_unmatched_code_falls_back_to_the_principal_area():
    areas = [{"area": {"code": "130010"}, "pops": ["10"]}]
    assert W._match_area(areas, "nope")["pops"] == ["10"]


# --------------------------------------------------------------------------
# Numeric coercion
# --------------------------------------------------------------------------
def test_ints_drops_bad_values_without_leaving_an_empty_bucket():
    """`setdefault(d, []).append(int(x))` created the bucket before int() could
    fail, and the later max() on that empty list took the whole forecast down."""
    out = W._ints([("2026-08-05T00:00", "10"), ("2026-08-05T06:00", "bad"),
                   ("2026-08-06T00:00", ""), ("2026-08-06T06:00", None)])
    assert out == {"2026-08-05": [10]}
    assert all(v for v in out.values()), "an empty bucket survived"


def test_parse_survives_a_non_numeric_temperature():
    data = [{"timeSeries": [
        {"areas": [{"area": {"code": "130010"}, "weatherCodes": ["100"], "weathers": ["晴"]}]},
        {"areas": [{"area": {"code": "130010"}, "pops": ["x"]}], "timeDefines": ["2026-08-05T00:00"]},
        {"areas": [{"area": {"name": "東京"}, "temps": ["not-a-number"]}], "timeDefines": ["2026-08-05T00:00"]},
    ]}]
    import config
    out = W._parse(data, config.CITIES[0])            # must not raise
    assert out["today"]["tempMax"] is None


# --------------------------------------------------------------------------
# Today's high/low memo
# --------------------------------------------------------------------------
def test_memo_keeps_todays_temps_after_jma_drops_them(cfg):
    """JMA removes today's high/low around 17:00 JST. Without the memo the frontend
    falls back to the hourly strip and reports an evening reading as the day's high."""
    today = {"tempMax": 34, "tempMin": 26}
    W._apply_today_temp_memo(cfg, today)              # remember while JMA still has them
    later = {"tempMax": None, "tempMin": None}
    W._apply_today_temp_memo(cfg, later)
    assert later == {"tempMax": 34, "tempMin": 26}


def test_memo_is_dropped_when_the_jst_day_rolls_over(cfg):
    W._apply_today_temp_memo(cfg, {"tempMax": 34, "tempMin": 26})
    W._today_temp_memo[cfg["id"]]["date"] = "1999-01-01"
    fresh = {"tempMax": None, "tempMin": None}
    W._apply_today_temp_memo(cfg, fresh)
    assert fresh == {"tempMax": None, "tempMin": None}, "yesterday's temperatures leaked"


def test_memo_is_per_city(cfg):
    import config
    other = config.CITIES[1]
    W._apply_today_temp_memo(cfg, {"tempMax": 34, "tempMin": 26})
    out = {"tempMax": None, "tempMin": None}
    W._apply_today_temp_memo(other, out)
    assert out["tempMax"] is None


def test_live_values_always_win_over_the_memo(cfg):
    W._apply_today_temp_memo(cfg, {"tempMax": 30, "tempMin": 20})
    fresh = {"tempMax": 35, "tempMin": 25}
    W._apply_today_temp_memo(cfg, fresh)
    assert fresh == {"tempMax": 35, "tempMin": 25}


# --------------------------------------------------------------------------
# Precipitation probability
# --------------------------------------------------------------------------
def test_today_pop_takes_the_peak_of_todays_blocks(cfg):
    ts = [{}, {"timeDefines": ["2026-08-05T06:00", "2026-08-05T12:00", "2026-08-06T00:00"],
               "areas": [{"area": {"code": cfg["class10_code"]}, "pops": ["10", "70", "90"]}]}]
    assert W._today_pop(ts, cfg, "2026-08-05") == 70


def test_today_pop_falls_forward_once_jma_rolls_the_series(cfg):
    ts = [{}, {"timeDefines": ["2026-08-06T00:00", "2026-08-06T06:00"],
               "areas": [{"area": {"code": cfg["class10_code"]}, "pops": ["40", "80"]}]}]
    assert W._today_pop(ts, cfg, "2026-08-05") == 40


def test_today_pop_is_none_when_there_is_nothing(cfg):
    assert W._today_pop([], cfg, "2026-08-05") is None


# --------------------------------------------------------------------------
# Icons
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code,emoji", [("100", "☀️"), ("200", "☁️"), ("300", "🌧️"), ("400", "❄️")])
def test_known_weather_codes(code, emoji):
    assert W._icon(code)[0] == emoji


@pytest.mark.parametrize("code", ["199", "299", "399", "499"])
def test_unknown_codes_fall_back_by_first_digit(code):
    assert W._icon(code)[0] != "❓"


@pytest.mark.parametrize("code", [None, "", "999", "abc", "0"])
def test_unmappable_codes_render_as_unknown(code):
    """"999" still starts with 9, which has no family — unknown rather than a guess."""
    assert W._icon(code) == ("❓", "—")


def test_a_numeric_code_is_coerced_before_the_family_lookup():
    assert W._icon(12345) == W._icon("12345") == W.FALLBACK["1"]


# --------------------------------------------------------------------------
# Hourly strip (met.no)
# --------------------------------------------------------------------------
def test_hourly_respects_count_and_step(monkeypatch, cfg):
    import asyncio
    payload = load_json("metno_tokyo.json")

    class Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return Resp()

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda *a, **kw: Client())
    out = asyncio.run(W.fetch_hourly(cfg, count=12, step=2))
    assert len(out) <= 12
    hours = [h["hour"] for h in out]
    assert len(hours) == len(set(hours)) or len(hours) > 12   # no duplicate hour in one day
    for h in out:
        assert 0 <= h["hour"] <= 23
        assert h["temp"] is None or isinstance(h["temp"], int)
        assert isinstance(h["precip"], (int, float))


@pytest.mark.parametrize("sym,hour,expect", [
    ("clearsky_day", 12, "☀️"), ("clearsky_night", 2, "🌙"),
    ("lightrainshowers_day", 12, "🌦️"), ("heavyrain", 12, "🌧️"),
    ("snow", 12, "❄️"), ("fog", 12, "🌫️"), ("", 12, "❓"),
])
def test_met_icons(sym, hour, expect):
    assert W._met_icon(sym, hour)[0] == expect


def test_hourly_polls_met_no_conditionally(monkeypatch, cfg):
    """met.no's terms ask for If-Modified-Since; a 304 must re-slice the cached
    forecast, never blank the strip or re-download it."""
    import asyncio
    payload = load_json("metno_tokyo.json")
    calls = []

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {"last-modified": "Thu, 17 Sep 2026 00:00:00 GMT"} if code == 200 else {}

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            calls.append(dict(headers or {}))
            return Resp(304 if headers and headers.get("If-Modified-Since") else 200)

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda *a, **kw: Client())
    first = asyncio.run(W.fetch_hourly(cfg, count=12, step=2))
    second = asyncio.run(W.fetch_hourly(cfg, count=12, step=2))
    assert calls[0] == {} and calls[1]["If-Modified-Since"].startswith("Thu")
    assert second == first, "the 304 path produced a different strip"


def test_hourly_304_without_a_cached_body_is_an_error_not_a_crash(monkeypatch, cfg):
    import asyncio

    class Resp:
        status_code = 304
        headers = {}

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return Resp()

    monkeypatch.setattr(W.httpx, "AsyncClient", lambda *a, **kw: Client())
    W._met_cache[cfg["id"]] = {"cond": {"etag": "stale"}, "data": None}
    with pytest.raises(ValueError):
        asyncio.run(W.fetch_hourly(cfg))
    assert W._met_cache[cfg["id"]]["cond"] == {}, "stale validators were kept"
