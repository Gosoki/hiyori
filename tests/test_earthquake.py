"""P2P bulletin normalization and the takeover state machine.

This is the only feature in the dashboard where being wrong has consequences, and
the bulletin format is not under our control — so the emphasis here is on "never
raises, never silently drops an alert" rather than on pretty output.
"""
import asyncio
import copy
import json
import random

import pytest

import earthquake as E
from conftest import load_json


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
def test_real_551_bulletins_all_normalize(p2p_quakes):
    assert p2p_quakes, "fixture is empty"
    for msg in p2p_quakes:
        ev = E.normalize_quake(msg)
        assert ev["kind"] == "quake"
        assert set(ev) >= {"id", "originTime", "hypocenter", "maxScale", "regions", "cancelled"}
        assert isinstance(ev["regions"], list)


def test_real_556_bulletins_all_normalize(p2p_eews):
    assert p2p_eews, "fixture is empty"
    for msg in p2p_eews:
        ev = E.normalize_eew(msg)
        assert ev["kind"] == "eew"
        assert isinstance(ev["regions"], list)


@pytest.mark.parametrize("msg", [
    {},
    {"code": 551},
    {"earthquake": None, "points": None, "issue": None},
    {"earthquake": {}, "points": [None, 3, "x"]},
    {"earthquake": {"hypocenter": None}, "points": [{"pref": None, "scale": "x"}]},
    {"issue": None, "earthquake": {"maxScale": "abc"}},
    {"points": [{"pref": "東京都", "scale": 10 ** 20}]},
])
def test_normalize_quake_survives_malformed(msg):
    """P2P sends explicit nulls; an exception here would tear down the live feed."""
    assert E.normalize_quake(msg)["kind"] == "quake"


@pytest.mark.parametrize("msg", [
    {},
    {"earthquake": None, "areas": None, "issue": None},
    {"areas": [None, "x"]},
    {"areas": [{"scaleTo": 99, "scaleFrom": 45, "pref": None, "name": "東京"}]},
    {"issue": None, "earthquake": {"hypocenter": {"reduceName": "X"}}},
])
def test_normalize_eew_survives_malformed(msg):
    assert E.normalize_eew(msg)["kind"] == "eew"


def test_eew_scale_to_99_falls_back_to_scale_from():
    """scaleTo=99 means "or above" — unmapped, so the known lower bound must show."""
    ev = E.normalize_eew({"areas": [{"pref": "東京", "scaleTo": 99, "scaleFrom": 45}]})
    assert ev["maxScale"] == 45
    assert ev["maxIntensity"] == "5弱"


def test_regions_keep_the_strongest_per_prefecture():
    ev = E.normalize_quake({"points": [
        {"pref": "東京都", "scale": 20}, {"pref": "東京都", "scale": 50},
        {"pref": "千葉県", "scale": 30}, {"pref": "", "scale": 70},
    ]})
    assert [(r["name"], r["scale"]) for r in ev["regions"]] == [("東京都", 50), ("千葉県", 30)]


@pytest.mark.parametrize("value,expected", [
    (10, "1"), (45, "5弱"), (46, "5弱"), (50, "5強"), (70, "7"),
    (-1, ""), (99, ""), (None, ""), ("x", ""), ("50", "5強"),
])
def test_scale_label(value, expected):
    assert E.scale_label(value) == expected


# --------------------------------------------------------------------------
# Identity / dedup
# --------------------------------------------------------------------------
def test_quake_key_is_always_a_string():
    assert E.quake_key({}) == ""
    assert E.quake_key({"id": "a"}) == "a"
    assert E.quake_key({"originTime": "t", "id": "a"}) == "t"   # the quake, not the bulletin


def test_an_eew_is_keyed_by_event_id_because_jma_revises_its_origin_time(p2p_eews):
    """In the captured feed the same eventId reports 16:27:18 in 第1報 and
    16:27:15 in 第2報. Keyed by time, a キャンセル報 could never retract the 第1報
    on screen, and a dismissed 第1報 would pop straight back as 第2報."""
    by_event = {}
    for m in p2p_eews:
        ev = E.normalize_eew(m)
        by_event.setdefault(ev["id"], set()).add(ev["originTime"])
    revised = [eid for eid, times in by_event.items() if len(times) > 1]
    assert revised, "fixture no longer shows a revised origin time — find another"
    for m in p2p_eews:
        ev = E.normalize_eew(m)
        assert E.quake_key(ev) == ev["id"]


def test_cancel_reaches_an_eew_whose_origin_time_was_revised():
    svc, _ = make_service()
    first = {"code": 556, "issue": {"eventId": "E9", "serial": 1},
             "earthquake": {"originTime": "2026/08/05 16:27:18"},
             "areas": [{"pref": "東京", "scaleTo": 55}]}
    asyncio.run(svc._handle(json.dumps(first)))
    assert svc.active()["kind"] == "eew"
    cancel = {"code": 556, "cancelled": True, "issue": {"eventId": "E9", "serial": 2},
              "earthquake": {"originTime": "2026/08/05 16:27:15"}, "areas": []}
    asyncio.run(svc._handle(json.dumps(cancel)))
    assert svc.active() is None, "the retraction did not reach the screen"


def test_merge_recent_replaces_an_earlier_bulletin_of_the_same_quake():
    first = {"originTime": "T1", "bulletin": "ScalePrompt"}
    second = {"originTime": "T1", "bulletin": "DetailScale"}
    other = {"originTime": "T0"}
    out = E._merge_recent([other, first], second, cap=5)
    assert out[0] is second
    assert first not in out
    assert other in out


def test_merge_recent_orders_by_origin_time_not_arrival():
    """A's 詳報 can land after B's first bulletin; "latest quake" must still be B."""
    a, b = {"originTime": "2026/08/05 18:00:00"}, {"originTime": "2026/08/05 18:03:00"}
    recent = E._merge_recent([], a, cap=5)
    recent = E._merge_recent(recent, b, cap=5)
    recent = E._merge_recent(recent, dict(a, bulletin="DetailScale"), cap=5)   # A's follow-up arrives last
    assert [e["originTime"] for e in recent] == [b["originTime"], a["originTime"]]


def test_merge_recent_honours_the_cap():
    recent = [{"originTime": f"T{i}"} for i in range(10)]
    assert len(E._merge_recent(recent, {"originTime": "NEW"}, cap=3)) == 3


# --------------------------------------------------------------------------
# Service behaviour
# --------------------------------------------------------------------------
def make_service(**kw):
    seen = []

    async def on_event(ev):
        seen.append(ev)

    svc = E.EarthquakeService("ws://test", 90, on_event, **kw)
    return svc, seen


def test_handle_publishes_and_stamps(p2p_quakes):
    svc, seen = make_service()
    asyncio.run(svc._handle(json.dumps(p2p_quakes[0])))
    assert len(seen) == 1
    ev = seen[0]
    assert ev["holdFor"] == 90 and ev["expiresAt"] > 0 and ev["receivedAt"]
    assert ev["source"] == "p2p"
    assert svc.active() is ev


@pytest.mark.parametrize("raw", ["", "null", "[]", "3", "true", "{", "\x00", '{"code":"551"}'])
def test_handle_ignores_garbage_frames(raw):
    svc, seen = make_service()
    asyncio.run(svc._handle(raw))
    assert seen == []


def test_handle_never_raises_on_mutated_bulletins(p2p_quakes, p2p_eews):
    """A malformed bulletin must not reach run()'s reconnect path: that drops the
    live feed for 5s, precisely when quakes arrive in bursts."""
    rng = random.Random(1234)
    real = p2p_quakes + p2p_eews

    def fuzz(obj, depth=0):
        if depth > 4:
            return obj
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                r = rng.random()
                if r < 0.10:
                    continue
                elif r < 0.20:
                    out[k] = None
                elif r < 0.26:
                    out[k] = "☠"
                elif r < 0.30:
                    out[k] = []
                else:
                    out[k] = fuzz(v, depth + 1)
            return out
        if isinstance(obj, list):
            return [None if rng.random() < 0.15 else fuzz(v, depth + 1) for v in obj]
        return obj

    svc, _ = make_service()
    for _ in range(1500):
        msg = fuzz(copy.deepcopy(rng.choice(real)))
        asyncio.run(svc._handle(json.dumps(msg, default=str)))   # must not raise


def test_drill_eew_is_hidden_unless_configured():
    drill = {"code": 556, "test": True, "areas": [{"pref": "東京", "scaleTo": 50}]}
    svc, seen = make_service(show_test=False)
    asyncio.run(svc._handle(json.dumps(drill)))
    assert seen == []
    svc, seen = make_service(show_test=True)
    asyncio.run(svc._handle(json.dumps(drill)))
    assert len(seen) == 1


def test_cancel_only_retracts_its_own_event():
    """A cancellation must not blank an unrelated takeover that is still up."""
    svc, _ = make_service()
    quake = {"code": 551, "id": "A", "earthquake": {"time": "2026/08/05 10:00:00", "maxScale": 50},
             "issue": {"type": "DetailScale"}, "points": [{"pref": "東京都", "scale": 50}]}
    asyncio.run(svc._handle(json.dumps(quake)))
    assert svc.active()

    unrelated = {"code": 556, "cancelled": True, "issue": {"eventId": "ZZ", "serial": 1},
                 "earthquake": {"originTime": "2026/08/05 23:59:59"}, "areas": []}
    asyncio.run(svc._handle(json.dumps(unrelated)))
    assert svc.active(), "an unrelated cancel wiped the active event"

    eew = {"code": 556, "issue": {"eventId": "E1", "serial": 1},
           "earthquake": {"originTime": "2026/08/05 11:00:00"},
           "areas": [{"pref": "東京", "scaleTo": 50}]}
    asyncio.run(svc._handle(json.dumps(eew)))
    assert svc.active()["kind"] == "eew"
    asyncio.run(svc._handle(json.dumps(dict(eew, cancelled=True))))
    assert svc.active() is None, "a matching cancel should have cleared it"


def test_active_expires_on_the_servers_clock():
    svc, _ = make_service()
    svc.current = {"expiresAt": E._now() - 1}
    assert svc.active() is None
    svc.current = {"expiresAt": E._now() + 60}
    assert svc.active() is not None
    svc.current = {}                       # missing key must not raise
    assert svc.active() is None


def test_only_quakes_enter_the_browse_list():
    """The 🗾 list is 地震情報 history; an EEW is a prediction, not a record."""
    svc, _ = make_service()
    asyncio.run(svc._handle(json.dumps({
        "code": 556, "issue": {"eventId": "E", "serial": 1},
        "earthquake": {"originTime": "2026/08/05 11:00:00"},
        "areas": [{"pref": "東京", "scaleTo": 50}]})))
    assert svc.recent == []


def test_knows_recognises_a_quake_already_reported():
    svc, _ = make_service()
    svc.recent = [{"originTime": "2026/08/05 18:06:00"}]
    assert svc.knows({"originTime": "2026/08/05 18:06:00"})
    assert not svc.knows({"originTime": "2026/08/05 18:07:00"})
    assert not svc.knows({})               # keyless event is never "known"


def test_offline_for_is_zero_while_connected():
    svc, _ = make_service()
    svc.connected = True
    assert svc.offline_for() == 0
    svc.connected = False
    svc.connected_since = E._now() - 120
    assert 119 <= svc.offline_for() <= 121


def test_status_shape():
    svc, _ = make_service()
    assert set(svc.status()) == {"connected", "offlineFor", "lastMessageAge", "reconnects", "lastError"}


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def test_fetch_recent_dedups_bulletins_of_one_quake(monkeypatch, p2p_quakes):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, data):
            self._data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return FakeResponse(self._data)

    monkeypatch.setattr(E.httpx, "AsyncClient", lambda *a, **kw: FakeClient(p2p_quakes))
    out = asyncio.run(E.fetch_recent_quakes(5))
    assert len(out) <= 5
    keys = [E.quake_key(e) for e in out]
    assert len(keys) == len(set(keys)), "the same quake appears twice"


# --------------------------------------------------------------------------
# What a reconnecting / polling tablet gets to see
# --------------------------------------------------------------------------
def _q551(t, scale, issue="DetailScale", points=True):
    return {"code": 551, "id": t, "earthquake": {"time": t, "maxScale": scale},
            "issue": {"type": issue},
            "points": [{"pref": "東京都", "scale": scale}] if points and scale > 0 else []}


def test_an_intensity_less_followup_does_not_hide_the_takeover():
    """The 震源に関する情報 between 震度速報 and 各地の震度 carries maxScale -1. A tablet
    that reconnects in that window must still be handed the 震度 it should show."""
    svc, _ = make_service()
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:00", 30, "ScalePrompt"))))
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:00", -1, "Destination", points=False))))
    assert svc.active()["maxScale"] == 30
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:00", 40, "DetailScale"))))
    assert svc.active()["maxScale"] == 40, "the detailed report should have replaced the prompt"


def test_a_weaker_aftershock_does_not_hide_a_major_takeover():
    svc, _ = make_service()
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:00", 55))))
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:40", 20))))
    assert svc.active()["maxScale"] == 55
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:01:10", 60))))
    assert svc.active()["maxScale"] == 60, "a stronger new quake must take over"


def test_anything_replaces_an_expired_takeover():
    svc, _ = make_service()
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:00:00", 55))))
    svc.current["expiresAt"] = E._now() - 1
    asyncio.run(svc._handle(json.dumps(_q551("2026/08/04 23:30:00", 10))))
    assert svc.active()["maxScale"] == 10


# --------------------------------------------------------------------------
# A flapping upstream is not "up"
# --------------------------------------------------------------------------
def test_a_flapping_connection_does_not_reset_offline_for_or_spam_the_log(monkeypatch, caplog):
    """A server that completes the handshake and drops the socket at once must not
    read as "connected 5 s ago" forever — that would keep the JMA fallback from ever
    arming — nor log a lost/reconnected pair every 5 seconds."""
    import logging
    clock = {"t": 1000.0}
    monkeypatch.setattr(E, "_now", lambda: clock["t"])

    class Flap:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration                 # server closes right after the upgrade

    monkeypatch.setattr(E.websockets, "connect", lambda *a, **kw: Flap())
    n = {"i": 0}

    async def fake_sleep(sec):
        clock["t"] += sec
        n["i"] += 1
        if n["i"] >= 50:
            raise asyncio.CancelledError

    monkeypatch.setattr(E.asyncio, "sleep", fake_sleep)
    svc, _ = make_service()
    with caplog.at_level(logging.WARNING, logger="hiyori.earthquake"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(svc.run())
    assert svc.reconnects == 50
    assert svc.offline_for() >= 200, f"offline_for pinned at {svc.offline_for()} by the flapping"
    assert not any("lost" in r.message for r in caplog.records), "a flap was logged as an outage"


def test_a_held_session_counts_and_the_flaps_after_it_stay_quiet(monkeypatch, caplog):
    """One real session (two minutes up), then a server that flaps: the loss is
    logged once, offline_for() runs from the end of the real session, and the
    flaps neither log nor reset it."""
    import logging
    clock = {"t": 1000.0}
    monkeypatch.setattr(E, "_now", lambda: clock["t"])
    sessions = {"n": 0}

    class Sock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            sessions["n"] += 1
            if sessions["n"] == 1:
                clock["t"] += 120                    # the first session genuinely held
            raise StopAsyncIteration                 # then every connect drops at once

    monkeypatch.setattr(E.websockets, "connect", lambda *a, **kw: Sock())
    n = {"i": 0}

    async def fake_sleep(sec):
        clock["t"] += sec
        n["i"] += 1
        if n["i"] >= 20:
            raise asyncio.CancelledError

    monkeypatch.setattr(E.asyncio, "sleep", fake_sleep)
    svc, _ = make_service()
    with caplog.at_level(logging.WARNING, logger="hiyori.earthquake"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(svc.run())
    assert svc.offline_for() >= 19 * 5 - 1, "offline_for should run from the end of the held session"
    assert sum("lost" in r.message for r in caplog.records) == 1
    assert not any("reconnected" in r.message for r in caplog.records), "a flap was logged as a recovery"
