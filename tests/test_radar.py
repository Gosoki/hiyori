"""雨雲レーダー: frame selection from JMA's target-time lists, and the tile proxy.

Offline: the target-time lists are hand-built in the shape JMA serves (captured
2026-11-22), and every upstream fetch is replaced. The one `live` test checks the
real indexes still look like that.
"""
import asyncio
import datetime

import httpx
import pytest

import config
import main
import radar

UTC = datetime.timezone.utc
NEWEST = datetime.datetime(2026, 11, 22, 12, 5, tzinfo=UTC)     # 21:05 JST
IS_CURRENT = radar.is_current                                    # the real one; tests see a fixed clock


def stamp(dt):
    return dt.strftime("%Y%m%d%H%M%S")


def mins(n):
    return datetime.timedelta(minutes=n)


def n1_list(newest=NEWEST, count=37):
    """targetTimes_N1: newest first, 5-minutely, basetime == validtime, ~3 h."""
    return [{"basetime": stamp(newest - mins(5 * i)), "validtime": stamp(newest - mins(5 * i)),
             "elements": ["hrpns", "hrpns_nd"]} for i in range(count)]


def n2_list(base=NEWEST):
    """targetTimes_N2: one basetime, validtime +5..+60 min, newest first."""
    return [{"basetime": stamp(base), "validtime": stamp(base + mins(5 * i)),
             "elements": ["hrpns", "hrpns_nd"]} for i in range(12, 0, -1)]


def rasrf_run(base, member, hours, extra=("rasrf_point", "rasrf_nd")):
    return [{"basetime": stamp(base), "validtime": stamp(base + datetime.timedelta(hours=h)),
             "member": member, "elements": ["rasrf", *extra]} for h in hours]


def rasrf_list():
    """rasrf/targetTimes as JMA lists it: the newest 10-minute run still incomplete
    (one frame), the previous one complete to +6 h, an hourly "none" run with only
    its analysis and +8..+15 h, single-frame analyses, and unrelated elements."""
    b = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    return (
        rasrf_run(b + mins(10), "immed", [0])                       # incomplete: tiles 404
        + rasrf_run(b, "immed", range(0, 7))                        # 12:00..18:00 UTC
        + rasrf_run(b - mins(10), "immed", range(0, 7))             # older complete run
        + rasrf_run(b - mins(60), "none", [0, *range(8, 16)])       # hourly run, +8..+15 h
        + rasrf_run(b - mins(30), "none", [0])                      # 解析雨量 only
        + [{"basetime": stamp(b), "validtime": stamp(b), "member": "none", "elements": ["sjfcstmap"]}]
    )


@pytest.fixture(autouse=True)
def _reset():
    def clear():
        radar.cache.clear()
        radar._inflight.clear()
        radar._failing = False
        radar._down_until.clear()
        main.radar_feed.cache.clear()
        main.radar_feed.fail_ts.clear()
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _ten_minute_steps(monkeypatch):
    """The frame-by-frame expectations below are written for a 10-minute step;
    pin it so they test the selection logic, not today's config default (the
    default's own cadence is covered by test_step_and_past_window)."""
    monkeypatch.setattr(config, "RADAR_STEP_MIN", 10)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The canned lists' newest observation is NEWEST; the route judges its age
    against this clock rather than the wall clock."""
    state = {"now": NEWEST + mins(5)}
    monkeypatch.setattr(radar, "is_current", lambda data: IS_CURRENT(data, now=state["now"]))
    return state


def run(coro):
    return asyncio.run(coro)


def times(frames):
    return [f["time"] for f in frames]


# --------------------------------------------------------------------------
# Frame selection
# --------------------------------------------------------------------------
def test_frames_past_hour_then_nowcast_then_hourly_forecast():
    out = radar.build_frames(n1_list(), n2_list(), rasrf_list())
    frames = out["frames"]
    assert times(frames) == [
        "2026-11-22T20:05:00+09:00", "2026-11-22T20:15:00+09:00", "2026-11-22T20:25:00+09:00",
        "2026-11-22T20:35:00+09:00", "2026-11-22T20:45:00+09:00", "2026-11-22T20:55:00+09:00",
        "2026-11-22T21:05:00+09:00",                                            # newest observation
        "2026-11-22T21:15:00+09:00", "2026-11-22T21:25:00+09:00", "2026-11-22T21:35:00+09:00",
        "2026-11-22T21:45:00+09:00", "2026-11-22T21:55:00+09:00", "2026-11-22T22:05:00+09:00",
        "2026-11-22T23:00:00+09:00", "2026-11-23T00:00:00+09:00", "2026-11-23T01:00:00+09:00",
        "2026-11-23T02:00:00+09:00", "2026-11-23T03:00:00+09:00",               # rasrf, ≤ +6 h
    ]
    assert out["now"] == 6
    assert [f["kind"] for f in frames] == ["obs"] * 7 + ["fcst"] * 11


def test_tiles_carry_their_own_basetime_and_member():
    frames = radar.build_frames(n1_list(), n2_list(), rasrf_list())["frames"]
    assert frames[0]["tile"] == "/api/radar/tile/nowc/none/20261122110500/20261122110500"
    assert frames[6]["tile"] == "/api/radar/tile/nowc/none/20261122120500/20261122120500"
    assert frames[7]["tile"] == "/api/radar/tile/nowc/none/20261122120500/20261122121500"
    assert frames[12]["tile"] == "/api/radar/tile/nowc/none/20261122120500/20261122130500"
    # the complete 12:00 run, not the newer one-frame 12:10 run, and its member is
    # "immed" — "none" in that URL segment is a 404 upstream
    assert frames[13]["tile"] == "/api/radar/tile/rasrf/immed/20261122120000/20261122140000"
    assert frames[-1]["tile"] == "/api/radar/tile/rasrf/immed/20261122120000/20261122180000"


def test_frames_are_strictly_increasing_and_never_overlap_the_nowcast():
    frames = radar.build_frames(n1_list(), n2_list(), rasrf_list())["frames"]
    ts = [datetime.datetime.fromisoformat(f["time"]) for f in frames]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    # 13:00 UTC is listed by the rasrf run but the nowcast already reaches 13:05
    assert "2026-11-22T22:00:00+09:00" not in times(frames)


def test_times_are_jst():
    late = datetime.datetime(2026, 11, 22, 15, 0, tzinfo=UTC)          # 00:00 JST next day
    frames = radar.build_frames(n1_list(late, 1), [], [])["frames"]
    assert times(frames) == ["2026-11-23T00:00:00+09:00"]


@pytest.mark.parametrize("step,past,n_obs,n_fcst", [(5, 60, 13, 12), (10, 60, 7, 6), (30, 60, 3, 2), (10, 30, 4, 6)])
def test_step_and_past_window(monkeypatch, step, past, n_obs, n_fcst):
    monkeypatch.setattr(config, "RADAR_STEP_MIN", step)
    monkeypatch.setattr(config, "RADAR_PAST_MIN", past)
    out = radar.build_frames(n1_list(), n2_list(), [])
    kinds = [f["kind"] for f in out["frames"]]
    assert kinds.count("obs") == n_obs and kinds.count("fcst") == n_fcst
    assert out["now"] == n_obs - 1


def test_a_gap_in_the_observations_is_skipped_not_invented():
    n1 = [e for e in n1_list() if e["validtime"] != "20261122113500"]
    frames = radar.build_frames(n1, [], [])["frames"]
    assert "2026-11-22T20:35:00+09:00" not in times(frames)
    assert len(frames) == 6


def test_forecast_grid_follows_the_newest_observation_when_n2_lags():
    # N1 already moved to 12:10 while N2 is still the 12:05 run
    n1 = n1_list(NEWEST + mins(5))
    frames = radar.build_frames(n1, n2_list(NEWEST), [])["frames"]
    fcst = [f for f in frames if f["kind"] == "fcst"]
    assert times(fcst) == ["2026-11-22T21:20:00+09:00", "2026-11-22T21:30:00+09:00",
                           "2026-11-22T21:40:00+09:00", "2026-11-22T21:50:00+09:00",
                           "2026-11-22T22:00:00+09:00"]
    assert all("/20261122120500/" in f["tile"] for f in fcst)


def test_horizon_follows_radar_hours(monkeypatch):
    monkeypatch.setattr(config, "RADAR_HOURS", 3)
    frames = radar.build_frames(n1_list(), n2_list(), rasrf_list())["frames"]
    assert times(frames)[-2:] == ["2026-11-22T23:00:00+09:00", "2026-11-23T00:00:00+09:00"]


def test_immed_run_is_preferred_over_a_newer_hourly_run():
    b = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    rasrf = rasrf_run(b, "none", [0, *range(7, 16)]) + rasrf_run(b - mins(10), "immed", range(0, 7))
    frames = radar.build_frames(n1_list(), n2_list(), rasrf)["frames"]
    assert frames[13]["tile"] == "/api/radar/tile/rasrf/immed/20261122115000/20261122135000"


def test_hourly_run_is_the_fallback_and_keeps_member_none(monkeypatch):
    monkeypatch.setattr(config, "RADAR_HOURS", 15)
    b = datetime.datetime(2026, 11, 22, 11, 0, tzinfo=UTC)
    rasrf = rasrf_run(b + mins(70), "immed", [0]) + rasrf_run(b, "none", [0, *range(8, 16)])
    rain = [f for f in radar.build_frames(n1_list(), n2_list(), rasrf)["frames"] if "/rasrf/" in f["tile"]]
    assert rain and all(f["tile"].startswith("/api/radar/tile/rasrf/none/20261122110000/") for f in rain)
    assert times(rain)[0] == "2026-11-23T04:00:00+09:00"            # +8 h of the run


def test_no_complete_rasrf_run_leaves_just_the_nowcast():
    b = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    frames = radar.build_frames(n1_list(), n2_list(), rasrf_run(b, "immed", [0, 1, 2]))["frames"]
    assert len(frames) == 13 and "/rasrf/" not in frames[-1]["tile"]


def test_junk_entries_are_ignored():
    n1 = n1_list() + [{"basetime": "x", "validtime": "x"}, {"validtime": "20261399000000", "basetime": "20261399000000",
                                                            "elements": ["hrpns"]}, {}]
    rasrf = rasrf_list() + [{"basetime": stamp(NEWEST), "validtime": stamp(NEWEST + mins(600)),
                             "member": "../../x", "elements": ["rasrf"]}]
    out = radar.build_frames(n1, n2_list() + [{"basetime": None}], rasrf)
    assert len(out["frames"]) == 18
    assert all("../" not in f["tile"] for f in out["frames"])


def test_no_observation_at_all_is_an_error_not_an_empty_list():
    with pytest.raises(ValueError):
        radar.build_frames([], n2_list(), rasrf_list())


# --------------------------------------------------------------------------
# /api/radar/frames
# --------------------------------------------------------------------------
@pytest.fixture
def client():
    def make():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
    return make


def get(client, url, **kw):
    async def go():
        async with client() as c:
            return await c.get(url, **kw)
    return asyncio.run(go())


def fake_lists(monkeypatch, fail=False):
    calls = []
    payload = {radar.N1_URL: n1_list(), radar.N2_URL: n2_list(), radar.RASRF_URL: rasrf_list()}

    async def fake(url):
        calls.append(url)
        if fail:
            raise httpx.ConnectError("down")
        return payload[url]
    monkeypatch.setattr(radar, "_get_json", fake)
    return calls


def test_frames_route_centres_on_the_city_and_shares_one_cache(client, monkeypatch):
    calls = fake_lists(monkeypatch)
    osaka = get(client, "/api/radar/frames", params={"city": "osaka"}).json()
    junk = get(client, "/api/radar/frames", params={"city": "../../nope"}).json()
    default = next(c for c in config.CITIES if c["id"] == config.DEFAULT_CITY)
    assert (osaka["lat"], osaka["lon"]) == (34.69, 135.50)
    assert (junk["lat"], junk["lon"]) == (config.CITIES[0]["lat"], config.CITIES[0]["lon"])
    assert (get(client, "/api/radar/frames").json()["lat"]) == default["lat"]
    assert osaka["zoom"] == config.RADAR_ZOOM and osaka["now"] == 6 and len(osaka["frames"]) == 18
    assert osaka["frames"] == junk["frames"]
    assert len(calls) == 3, "the frame list is country-wide: one fetch, whatever the city"
    assert set(main.radar_feed.cache) == {"jma"}


def test_frames_route_cold_failure_is_an_empty_list_not_an_error(client, monkeypatch):
    fake_lists(monkeypatch, fail=True)
    r = get(client, "/api/radar/frames")
    assert r.status_code == 200
    assert r.json()["frames"] == [] and r.json()["zoom"] == config.RADAR_ZOOM


def test_frames_route_keeps_last_good(client, monkeypatch):
    fake_lists(monkeypatch)
    good = get(client, "/api/radar/frames").json()
    fake_lists(monkeypatch, fail=True)
    main.radar_feed.cache["jma"]["ts"] = 0                           # expire it
    assert get(client, "/api/radar/frames").json()["frames"] == good["frames"]
    assert main.HEALTH["radar"].fails == 1


def test_list_age_bound():
    data = radar.build_frames(n1_list(), n2_list(), rasrf_list())
    assert IS_CURRENT(data, now=NEWEST + mins(5))
    assert IS_CURRENT(data, now=NEWEST + datetime.timedelta(seconds=radar.MAX_LIST_AGE))
    assert not IS_CURRENT(data, now=NEWEST + datetime.timedelta(seconds=radar.MAX_LIST_AGE + 1))
    assert not IS_CURRENT({"now": 0, "frames": []}, now=NEWEST)


def test_frames_route_refuses_an_hours_old_last_good(client, monkeypatch, clock):
    """Nothing refreshes the radar between opens, so last-good can be from this
    morning: looping it as 実況 would be worse than saying there is no data."""
    fake_lists(monkeypatch)
    assert get(client, "/api/radar/frames").json()["frames"]
    fake_lists(monkeypatch, fail=True)
    main.radar_feed.cache["jma"]["ts"] = 0
    clock["now"] = NEWEST + datetime.timedelta(hours=9)
    r = get(client, "/api/radar/frames").json()
    assert r["frames"] == [] and r["now"] == 0
    assert main.radar_feed.cache["jma"]["data"]["frames"], "the feed's own last-good is untouched"


def test_frames_route_refuses_a_list_that_answers_but_stopped_updating(client, monkeypatch, clock):
    fake_lists(monkeypatch)
    clock["now"] = NEWEST + datetime.timedelta(hours=2)
    assert get(client, "/api/radar/frames").json()["frames"] == []


def test_a_radar_failure_nobody_rechecks_stops_counting_as_degraded(client, monkeypatch):
    """Only an open re-tries the radar; one blip must not leave /api/health
    degraded for days on a wall tablet."""
    fake_lists(monkeypatch)
    get(client, "/api/radar/frames")
    fake_lists(monkeypatch, fail=True)
    main.radar_feed.cache["jma"]["ts"] = 0
    get(client, "/api/radar/frames")
    assert "radar" in get(client, "/api/health").json()["degraded"], "a current failure must show"
    t0 = main.time.time()
    forget = 10 * main.radar_feed.ttl
    monkeypatch.setattr(main.time, "time", lambda: t0 + forget - 5)
    assert "radar" in get(client, "/api/health").json()["degraded"]
    monkeypatch.setattr(main.time, "time", lambda: t0 + forget + 5)
    feed = get(client, "/api/health").json()["feeds"]["radar"]
    assert feed["ok"] and feed["consecutiveFails"] == 1, "counters still tell the truth"
    assert "radar" not in get(client, "/api/health").json()["degraded"]


def test_radar_health_appears_only_once_someone_has_looked(client, monkeypatch):
    """Nothing keeps the radar warm; "never opened" must not read as degraded."""
    main.HEALTH.pop("radar", None)
    assert "radar" not in get(client, "/api/health").json()["feeds"]
    fake_lists(monkeypatch)
    get(client, "/api/radar/frames")
    feed = get(client, "/api/health").json()["feeds"]["radar"]
    assert feed["ok"] and not feed["stale"]


# --------------------------------------------------------------------------
# Earthquake-map outlook (/api/radar/quake)
# --------------------------------------------------------------------------
def hours(n):
    return datetime.timedelta(hours=n)


def quake_times(frames):
    return [f["time"][11:16] for f in frames]


def test_quake_outlook_is_immed_to_6h_then_hourly_to_12h():
    frames = radar.build_quake_frames(rasrf_list(), NEWEST)
    # immed 12:00 UTC run (the newer 12:10 one is unfinished) → 22:00..03:00 JST,
    # then the hourly 11:00 run from its next frame up to NEWEST + 12 h (09:05 JST)
    assert quake_times(frames) == ["22:00", "23:00", "00:00", "01:00", "02:00", "03:00",
                                   "04:00", "05:00", "06:00", "07:00", "08:00", "09:00"]
    assert [f["coarse"] for f in frames] == [False] * 6 + [True] * 6
    assert frames[0]["tile"] == "/api/radar/tile/rasrf/immed/20261122120000/20261122130000"
    assert frames[5]["tile"] == "/api/radar/tile/rasrf/immed/20261122120000/20261122180000"
    assert frames[6]["tile"] == "/api/radar/tile/rasrf/none/20261122110000/20261122190000"
    assert frames[-1]["tile"] == "/api/radar/tile/rasrf/none/20261122110000/20261123000000"
    assert times(frames) == sorted(times(frames)) and len(set(times(frames))) == 12
    assert all(f["time"].endswith("+09:00") for f in frames)


def test_quake_outlook_skips_hourly_runs_that_stop_short_of_the_horizon():
    b = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    rasrf = (rasrf_run(b, "immed", range(1, 7))
             + rasrf_run(b, "none", [0])                              # newest hourly: analysis only
             + rasrf_run(b - hours(1), "none", [0, *range(7, 12)])    # ends at 22:00 UTC < NEWEST + 12 h
             + rasrf_run(b - hours(2), "none", [0, *range(7, 16)]))   # older, but covers it
    frames = radar.build_quake_frames(rasrf, NEWEST)
    coarse = [f for f in frames if f["coarse"]]
    assert len(coarse) == 6
    assert all("/rasrf/none/20261122100000/" in f["tile"] for f in coarse)


def test_quake_outlook_keeps_the_hourly_part_hourly():
    """A 10-minute run at hh:50 ends at hh:50 + 6 h; the hourly run's frame ten
    minutes later would make one 10-minute step in an hourly loop."""
    b = datetime.datetime(2026, 11, 22, 11, 0, tzinfo=UTC)
    rasrf = rasrf_run(b + mins(50), "immed", range(1, 7)) + rasrf_run(b, "none", [0, *range(7, 16)])
    frames = radar.build_quake_frames(rasrf, NEWEST)
    assert quake_times(frames)[5:7] == ["02:50", "04:00"], "18:00 UTC sits 10 min after the last immed frame"
    gaps = [datetime.datetime.fromisoformat(b2["time"]) - datetime.datetime.fromisoformat(a["time"])
            for a, b2 in zip(frames, frames[1:])]
    assert min(gaps) >= radar.QUAKE_MIN_GAP


@pytest.mark.parametrize("n, last, count", [(12, "09:00", 12), (8, "05:00", 8), (3, "00:00", 3)])
def test_quake_outlook_stops_at_radar_quake_hours(monkeypatch, n, last, count):
    monkeypatch.setattr(config, "RADAR_QUAKE_HOURS", n)
    frames = radar.build_quake_frames(rasrf_list(), NEWEST)
    assert quake_times(frames)[-1] == last and len(frames) == count
    assert datetime.datetime.fromisoformat(frames[-1]["time"]) <= NEWEST + hours(n)


def test_quake_outlook_halves_stand_alone():
    b = datetime.datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    only_hourly = radar.build_quake_frames(rasrf_run(b, "immed", [0]) + rasrf_run(b, "none", [0, *range(7, 16)]), NEWEST)
    assert quake_times(only_hourly) == ["04:00", "05:00", "06:00", "07:00", "08:00", "09:00"]
    assert all(f["coarse"] for f in only_hourly)
    only_immed = radar.build_quake_frames(rasrf_run(b, "immed", range(0, 7)), NEWEST)
    assert len(only_immed) == 6 and not any(f["coarse"] for f in only_immed)
    assert radar.build_quake_frames([], NEWEST) == []
    assert radar.build_quake_frames(rasrf_run(b, "immed", [0, 1, 2]), NEWEST) == []


def test_quake_route_shape_and_shared_cache(client, monkeypatch):
    calls = fake_lists(monkeypatch)
    r = get(client, "/api/radar/quake")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"zoom", "frames"} and body["zoom"] == config.RADAR_QUAKE_ZOOM == 6
    assert len(body["frames"]) == 12
    assert all(set(f) == {"time", "tile", "coarse"} for f in body["frames"])
    assert all(f["tile"].startswith("/api/radar/tile/rasrf/") for f in body["frames"])
    get(client, "/api/radar/frames")
    assert len(calls) == 3, "the quake outlook rides on the radar's own cached lists"
    assert "quake" not in get(client, "/api/radar/frames").json()


def test_quake_route_failure_is_an_empty_list(client, monkeypatch, clock):
    fake_lists(monkeypatch, fail=True)
    r = get(client, "/api/radar/quake")
    assert r.status_code == 200 and r.json() == {"zoom": 6, "frames": []}
    fake_lists(monkeypatch)
    main.radar_feed.fail_ts.clear()
    assert get(client, "/api/radar/quake").json()["frames"]
    clock["now"] = NEWEST + hours(2)                  # last-good that stopped updating
    assert get(client, "/api/radar/quake").json()["frames"] == []


# --------------------------------------------------------------------------
# Tile routes
# --------------------------------------------------------------------------
Z = str(config.RADAR_ZOOM)
X, Y = 227, 100          # Tokyo at z8
RAIN = "/api/radar/tile/nowc/none/20261122120500/20261122120500"
PNG = radar.PNG_MAGIC + b"\x00" * 400


@pytest.fixture
def upstream(monkeypatch):
    """Replaces the network: `answers[url]` is (status, body) or an exception."""
    state = {"calls": [], "answers": {}, "default": (200, PNG)}

    async def fake(url):
        state["calls"].append(url)
        ans = state["answers"].get(url, state["default"])
        if isinstance(ans, Exception):
            raise ans
        return ans
    monkeypatch.setattr(radar, "_download", fake)
    return state


@pytest.mark.parametrize("path", [
    f"/api/radar/tile/foo/none/20261122120500/20261122120500/{Z}/{X}/{Y}.png",       # product
    f"/api/radar/tile/nowc/immed/20261122120500/20261122120500/{Z}/{X}/{Y}.png",     # nowc is none only
    f"/api/radar/tile/rasrf/xx/20261122120000/20261122140000/{Z}/{X}/{Y}.png",       # member
    f"/api/radar/tile/nowc/none/2026112212050/20261122120500/{Z}/{X}/{Y}.png",        # 13 digits
    f"/api/radar/tile/nowc/none/20261122120500/2026112212050a/{Z}/{X}/{Y}.png",
    f"/api/radar/tile/nowc/none/202611221205000/20261122120500/{Z}/{X}/{Y}.png",      # 15 digits
    f"{RAIN}/7/{X // 2}/{Y // 2}.png", f"{RAIN}/9/{X * 2}/{Y * 2}.png",               # zoom
    f"{RAIN}/{Z}/0/0.png", f"{RAIN}/{Z}/{X}/0.png", f"{RAIN}/{Z}/210/{Y}.png",       # outside Japan
    f"{RAIN}/{Z}/239/{Y}.png", f"{RAIN}/{Z}/{X}/115.png", f"{RAIN}/{Z}/{X}/85.png",
    f"{RAIN}/{Z}/-1/{Y}.png", f"{RAIN}/{Z}/{X}/1e2.png", f"{RAIN}/{Z}/{X}/%EF%BC%91%EF%BC%90%EF%BC%90.png",
    f"{RAIN}/{Z}/{X}/{Y}.jpg", f"{RAIN}/{Z}/{X}/{Y}",
    f"/api/radar/base/7/{X}/{Y}.png", f"/api/radar/base/{Z}/300/{Y}.png", f"/api/radar/base/{Z}/{X}/x.png",
    f"/api/radar/base/{Z}/{X}/{Y}.png/..", f"/api/radar/base/{Z}/{X}/0{Y}0.png",
])
def test_invalid_tile_requests_404_without_touching_upstream(client, upstream, path):
    assert get(client, path).status_code == 404
    assert upstream["calls"] == []


def test_bbox_edges_are_served(client, upstream):
    for x, y in ((211, 86), (238, 114), (211, 114), (238, 86)):     # lon 118/156, lat 50/18 at z8
        assert get(client, f"{RAIN}/{Z}/{x}/{y}.png").status_code == 200, (x, y)


NZ = config.RADAR_QUAKE_ZOOM


def test_quake_overlay_z6_tiles_are_served(client, upstream):
    """The earthquake map's rain tiles over Japan (x 54..57, y 22..27 at z6: the four
    main islands to Okinawa 本島) pass the same validation as the radar's z8 ones."""
    tiles = [(x, y) for x in range(54, 58) for y in range(22, 28)]
    assert NZ == 6
    for x, y in tiles:
        assert get(client, f"{RAIN}/{NZ}/{x}/{y}.png").status_code == 200, (x, y)
    assert upstream["calls"][0] == \
        f"https://www.jma.go.jp/bosai/jmatile/data/nowc/20261122120500/none/20261122120500/surf/hrpns/{NZ}/54/22.png"
    assert len(upstream["calls"]) == len(tiles)


@pytest.mark.parametrize("path", [
    f"{RAIN}/{NZ}/51/24.png", f"{RAIN}/{NZ}/60/24.png",                  # z6 outside Japan (x 52..59)
    f"{RAIN}/{NZ}/55/20.png", f"{RAIN}/{NZ}/55/29.png",                  # (y 21..28)
    f"/api/radar/base/{NZ}/0/0.png", f"/api/radar/base/{NZ}/{X}/{Y}.png",  # z8 numbers at z6
    f"/api/radar/base/{NZ}/56/25.png",                                   # base tiles: radar-only, z8
    f"{RAIN}/7/111/50.png", f"/api/radar/base/7/111/50.png",              # z7: neither zoom
    f"{RAIN}/5/27/12.png", f"/api/radar/base/5/27/12.png",
])
def test_only_the_configured_zooms_are_served(client, upstream, path):
    assert get(client, path).status_code == 404
    assert upstream["calls"] == []


def test_rain_tile_is_proxied_with_a_long_cache(client, upstream):
    r = get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert upstream["calls"] == [
        f"https://www.jma.go.jp/bosai/jmatile/data/nowc/20261122120500/none/20261122120500/surf/hrpns/{Z}/{X}/{Y}.png"]


def test_rasrf_tile_url_uses_the_member_segment(client, upstream):
    assert get(client, f"/api/radar/tile/rasrf/immed/20261122120000/20261122140000/{Z}/{X}/{Y}.png").status_code == 200
    assert upstream["calls"] == [
        f"https://www.jma.go.jp/bosai/jmatile/data/rasrf/20261122120000/immed/20261122140000/surf/rasrf/{Z}/{X}/{Y}.png"]


def test_base_tile_is_proxied_with_a_week_of_cache(client, upstream):
    r = get(client, f"/api/radar/base/{Z}/{X}/{Y}.png")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=604800"
    assert upstream["calls"] == [f"https://cyberjapandata.gsi.go.jp/xyz/pale/{Z}/{X}/{Y}.png"]


def test_second_request_is_served_from_cache(client, upstream):
    for _ in range(3):
        assert get(client, f"{RAIN}/{Z}/{X}/{Y}.png").status_code == 200
    assert len(upstream["calls"]) == 1


def test_upstream_404_is_no_rain_and_briefly_remembered(client, upstream, monkeypatch):
    """JMA publishes no 降水短時間予報 tile wholly outside its domain (far-offshore
    sea): that 404 must read as "no rain", not as a failed frame on the tablet."""
    url = radar.rain_tile_url("nowc", "none", "20261122120500", "20261122120500", Z, str(X), str(Y))
    upstream["answers"][url] = (404, b"")
    r = get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    assert r.headers["cache-control"] == f"public, max-age={main.EMPTY_MAX_AGE}"
    get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert len(upstream["calls"]) == 1, "a 404 should be cached for a moment"
    now = radar.time.monotonic()
    monkeypatch.setattr(radar.time, "monotonic", lambda: now + radar.NEGATIVE_TTL + 1)
    get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert len(upstream["calls"]) == 2, "…and only for a moment: an incomplete run fills in"


@pytest.mark.parametrize("answer", [httpx.ConnectError("down"), ValueError("tile larger than MAX_BODY"),
                                    (500, b""), (403, b""), (200, b"<html>maintenance</html>")])
def test_upstream_failure_is_a_502_and_not_cached(client, upstream, answer, monkeypatch):
    monkeypatch.setattr(radar, "HOST_COOLDOWN", 0)
    upstream["default"] = answer
    r = get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert r.status_code == 502 and r.headers["cache-control"] == "no-cache"
    get(client, f"{RAIN}/{Z}/{X}/{Y}.png")
    assert len(upstream["calls"]) == 2
    assert not radar.cache.entries


def test_failures_are_logged_on_the_transition_only(client, upstream, caplog, monkeypatch):
    monkeypatch.setattr(radar, "HOST_COOLDOWN", 0)
    upstream["default"] = httpx.ConnectError("down")
    with caplog.at_level("WARNING", logger="hiyori.radar"):
        for y in (100, 101, 102):
            get(client, f"{RAIN}/{Z}/{X}/{y}.png")
        upstream["default"] = (200, PNG)
        get(client, f"{RAIN}/{Z}/{X}/103.png")
    msgs = [r.getMessage() for r in caplog.records if r.name == "hiyori.radar"]
    assert len(msgs) == 2 and "failed" in msgs[0] and "recovered" in msgs[1]


# --------------------------------------------------------------------------
# Proxy internals: coalescing, timeout, size cap, byte-bounded LRU
# --------------------------------------------------------------------------
def test_concurrent_misses_share_one_upstream_fetch(monkeypatch):
    calls = []

    async def slow(url):
        calls.append(url)
        await asyncio.sleep(0.05)
        return 200, PNG
    monkeypatch.setattr(radar, "_download", slow)

    async def go():
        return await asyncio.gather(*(radar.get_tile("u") for _ in range(20)))
    assert run(go()) == [(200, PNG)] * 20
    assert calls == ["u"] and not radar._inflight


def test_a_cancelled_waiter_does_not_cancel_the_shared_fetch(monkeypatch):
    calls = []

    async def slow(url):
        calls.append(url)
        await asyncio.sleep(0.05)
        return 200, PNG
    monkeypatch.setattr(radar, "_download", slow)

    async def go():
        quitter = asyncio.ensure_future(radar.get_tile("u"))
        stayer = asyncio.ensure_future(radar.get_tile("u"))
        await asyncio.sleep(0.01)
        quitter.cancel()
        return await stayer
    assert run(go()) == (200, PNG)
    assert calls == ["u"] and radar.cache.get("u") == (200, PNG)


def test_a_hung_upstream_is_cut_off(monkeypatch):
    async def hang(url):
        await asyncio.sleep(10)
    monkeypatch.setattr(radar, "_download", hang)
    monkeypatch.setattr(radar, "FETCH_TIMEOUT", 0.05)
    assert run(radar.get_tile("u")) == (502, b"")


def test_an_unreachable_host_fails_fast_for_a_while(monkeypatch):
    """One timeout on a host answers its other tiles 502 at once for HOST_COOLDOWN,
    instead of each of ~600 tiles holding a tablet connection for FETCH_TIMEOUT."""
    calls = []

    async def hang(url):
        calls.append(url)
        if "gsi" in url:
            await asyncio.sleep(10)
        return 200, PNG
    monkeypatch.setattr(radar, "_download", hang)
    monkeypatch.setattr(radar, "FETCH_TIMEOUT", 0.05)
    base = radar.base_tile_url(Z, str(X), str(Y))
    other = radar.base_tile_url(Z, str(X + 1), str(Y))
    rain = radar.rain_tile_url("nowc", "none", "20261122120500", "20261122120500", Z, str(X), str(Y))
    assert run(radar.get_tile(base)) == (502, b"")
    assert run(radar.get_tile(other)) == (502, b"")
    assert calls == [base], "the cooling-down host was asked again"
    assert run(radar.get_tile(rain)) == (200, PNG), "another host is unaffected"
    radar._down_until["cyberjapandata.gsi.go.jp"] -= radar.HOST_COOLDOWN + 1   # the cooldown runs out
    # (not by patching time.monotonic: that is asyncio's clock too, and wait_for would never fire)
    run(radar.get_tile(other))
    assert calls[-1] == other, "the host is tried again after the cooldown"


def test_a_per_tile_answer_does_not_cool_the_host_down(client, upstream):
    url = radar.rain_tile_url("nowc", "none", "20261122120500", "20261122120500", Z, str(X), str(Y))
    upstream["answers"][url] = (500, b"")
    assert get(client, f"{RAIN}/{Z}/{X}/{Y}.png").status_code == 502
    assert get(client, f"{RAIN}/{Z}/{X}/{Y + 1}.png").status_code == 200
    assert not radar._down_until


def mock_client(monkeypatch, handler):
    monkeypatch.setattr(radar, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_download_streams_a_normal_tile(monkeypatch):
    mock_client(monkeypatch, lambda req: httpx.Response(200, content=PNG))
    assert run(radar._download("https://example.test/t.png")) == (200, PNG)


def test_download_refuses_an_oversized_body(monkeypatch):
    monkeypatch.setattr(radar, "MAX_BODY", 1000)

    def chunks():
        for _ in range(5):
            yield PNG                                             # no content-length: streamed
    mock_client(monkeypatch, lambda req: httpx.Response(200, content=chunks()))
    assert run(radar.get_tile("https://example.test/big.png")) == (502, b"")
    mock_client(monkeypatch, lambda req: httpx.Response(200, content=b"x" * 10, headers={"content-length": "5000"}))
    assert run(radar.get_tile("https://example.test/big2.png")) == (502, b"")
    assert not radar.cache.entries


def test_download_passes_a_404_through(monkeypatch):
    mock_client(monkeypatch, lambda req: httpx.Response(404, content=b"not found"))
    assert run(radar.get_tile("https://example.test/missing.png")) == (404, b"")


def test_cache_is_bounded_by_bytes_and_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(radar, "ENTRY_OVERHEAD", 0)
    c = radar.TileCache(1000)
    c.put("a", 200, b"a" * 400)
    c.put("b", 200, b"b" * 400)
    assert c.get("a")                                  # a is now the most recent
    c.put("c", 200, b"c" * 400)                        # 1200 > 1000: evict b, not a
    assert set(c.entries) == {"a", "c"} and c.bytes == 800
    c.put("a", 200, b"a" * 100)                        # replacing re-counts, never double-counts
    assert c.bytes == 500
    c.put("huge", 200, b"h" * 1001)                    # bigger than the whole cache: not stored
    assert "huge" not in c.entries and c.bytes == 500
    c.put("d", 200, b"d" * 500)                        # exactly full is fine
    assert c.bytes == 1000
    c.put("e", 200, b"e" * 1)                          # one byte over: c, the oldest, goes
    assert list(c.entries) == ["a", "d", "e"] and c.bytes == 601
    c.put("f", 200, b"f" * 1000)                       # evicts as many as it takes
    assert list(c.entries) == ["f"] and c.bytes == 1000


def test_cache_charges_overhead_so_tiny_tiles_are_bounded_too():
    c = radar.TileCache(radar.ENTRY_OVERHEAD * 10)
    for i in range(50):
        c.put(str(i), 404, b"", ttl=60)
    assert len(c.entries) == 10


def test_default_cache_budget():
    assert radar.cache.max_bytes == 32 * 1024 * 1024


# --------------------------------------------------------------------------
# Live contract (deselected by default: `pytest -m live`)
# --------------------------------------------------------------------------
@pytest.mark.live
def test_live_frames_and_tiles():
    async def go():
        try:
            data = await radar.fetch_frames()
            frames = data["frames"]
            assert frames[data["now"]]["kind"] == "obs" and frames[-1]["kind"] == "fcst"
            assert any("/rasrf/" in f["tile"] for f in frames), "no 降水短時間予報 run was usable"
            assert any(f["coarse"] for f in data["quake"]) and not data["quake"][0]["coarse"], data["quake"]
            for f in (frames[0], frames[data["now"] + 1], frames[-1]):
                product, member, base, valid = f["tile"].split("/")[4:8]
                status, body = await radar.get_tile(radar.rain_tile_url(product, member, base, valid, Z, str(X), str(Y)))
                assert status == 200, f["tile"]
            assert (await radar.get_tile(radar.base_tile_url(Z, str(X), str(Y))))[0] == 200
        finally:
            await radar.aclose()
    run(go())


def test_a_missing_base_tile_is_still_a_404(client, upstream):
    """Only rain tiles turn an upstream 404 into "empty": a base map must exist."""
    url = radar.base_tile_url(Z, str(X), str(Y))
    upstream["answers"][url] = (404, b"")
    assert get(client, f"/api/radar/base/{Z}/{X}/{Y}.png").status_code == 404
