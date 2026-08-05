"""The caching layer and upstream health bookkeeping.

The contract every panel depends on: Feed.get never raises and never returns
nothing. A dead upstream must degrade to the last good value, must not be hammered,
and must be *visible* in /api/health — because nothing on screen changes when it
happens.
"""
import asyncio
import logging

import pytest

import main
from main import Feed, FeedHealth


@pytest.fixture(autouse=True)
def _isolate_health():
    saved = dict(main.HEALTH)
    main.HEALTH.clear()
    yield
    main.HEALTH.clear()
    main.HEALTH.update(saved)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Cache semantics
# --------------------------------------------------------------------------
def test_returns_fetched_data_and_caches_it():
    calls = []

    async def fetch(key):
        calls.append(key)
        return ["v"]

    f = Feed("t", ttl=60, fetch=fetch, cold_value=[])
    assert run(f.get("k")) == ["v"]
    assert run(f.get("k")) == ["v"]
    assert calls == ["k"], "the second call should have hit the cache"


def test_failure_returns_last_good_not_empty():
    state = {"fail": False}

    async def fetch(key):
        if state["fail"]:
            raise RuntimeError("upstream on fire")
        return ["good"]

    f = Feed("t", ttl=0, fetch=fetch, cold_value=[])
    assert run(f.get("k")) == ["good"]
    state["fail"] = True
    assert run(f.get("k")) == ["good"], "a blip blanked the column"


def test_cold_start_failure_returns_the_configured_empty_value():
    async def fetch(key):
        raise RuntimeError("nope")

    assert run(Feed("t", ttl=60, fetch=fetch, cold_value={}).get("k")) == {}
    assert run(Feed("t2", ttl=60, fetch=fetch, cold_value=[]).get("k")) == []


def test_cooldown_stops_a_sick_upstream_being_hammered():
    """Tablets keep polling during an outage; without the cooldown every poll would
    become an upstream request."""
    calls = []

    async def fetch(key):
        calls.append(1)
        raise RuntimeError("down")

    f = Feed("t", ttl=0, fetch=fetch, cold_value=[])
    for _ in range(10):
        run(f.get("k"))
    assert len(calls) == 1, f"{len(calls)} upstream calls during the cooldown"


def test_cooldown_expires():
    calls = []

    async def fetch(key):
        calls.append(1)
        raise RuntimeError("down")

    f = Feed("t", ttl=0, fetch=fetch, cold_value=[])
    run(f.get("k"))
    f.fail_ts["k"] = 0                      # pretend the cooldown elapsed
    run(f.get("k"))
    assert len(calls) == 2


def test_concurrent_misses_collapse_into_one_fetch():
    calls = []

    async def slow(key):
        calls.append(1)
        await asyncio.sleep(0.05)
        return [key]

    f = Feed("t", ttl=60, fetch=slow, cold_value=[])

    async def race():
        return await asyncio.gather(*[f.get("k") for _ in range(30)])

    results = run(race())
    assert len(calls) == 1, f"{len(calls)} upstream fetches for one cache miss"
    assert all(r == ["k"] for r in results)


def test_keys_are_independent():
    async def fetch(key):
        return [key]

    f = Feed("t", ttl=60, fetch=fetch, cold_value=[])
    assert run(f.get("a")) == ["a"]
    assert run(f.get("b")) == ["b"]


def test_extra_args_are_forwarded_to_the_fetcher():
    async def fetch(cfg):
        return cfg["city_name"]

    f = Feed("t", ttl=60, fetch=fetch, cold_value=None)
    assert run(f.get("tokyo", {"city_name": "東京"})) == "東京"


def test_empty_is_failure_only_when_configured():
    async def fetch(key):
        return []

    strict = Feed("strict", ttl=0, fetch=fetch, cold_value=[], empty_is_failure=True)
    run(strict.get("k"))
    assert not strict.health.report()["ok"]

    lenient = Feed("lenient", ttl=0, fetch=fetch, cold_value=[])
    run(lenient.get("k"))
    assert lenient.health.report()["ok"], "an empty list is a legitimate answer here"


def test_empty_is_failure_keeps_the_previous_headlines():
    state = {"empty": False}

    async def fetch(key):
        return [] if state["empty"] else ["headline"]

    f = Feed("t", ttl=0, fetch=fetch, cold_value=[], empty_is_failure=True)
    assert run(f.get("k")) == ["headline"]
    state["empty"] = True
    assert run(f.get("k")) == ["headline"], "an empty response blanked the column"


# --------------------------------------------------------------------------
# Health bookkeeping
# --------------------------------------------------------------------------
def test_health_starts_not_ok_until_something_succeeds():
    assert FeedHealth("x").report()["ok"] is False


def test_health_tracks_consecutive_failures_and_recovery():
    h = FeedHealth("x")
    h.succeeded()
    assert h.report()["ok"]
    h.failed(RuntimeError("boom"))
    h.failed(RuntimeError("boom"))
    r = h.report()
    assert r["ok"] is False and r["consecutiveFails"] == 2
    assert "RuntimeError" in r["lastError"]
    h.succeeded()
    assert h.report()["consecutiveFails"] == 0


def test_only_the_failure_TRANSITION_is_logged(caplog):
    """A day-long outage must not write thousands of identical journal lines."""
    h = FeedHealth("noisy")
    with caplog.at_level(logging.WARNING, logger="hiyori"):
        for _ in range(20):
            h.failed(RuntimeError("same"))
    assert sum("noisy: upstream failed" in r.message for r in caplog.records) == 1


def test_recovery_is_logged(caplog):
    h = FeedHealth("x")
    h.failed(RuntimeError("boom"))
    with caplog.at_level(logging.WARNING, logger="hiyori"):
        h.succeeded()
    assert any("recovered after" in r.message for r in caplog.records)


def test_empty_response_is_reported_as_such():
    h = FeedHealth("x")
    h.failed(None)
    assert h.report()["lastError"] == "empty response"


def test_health_registry_returns_the_same_object():
    assert main.health("dup") is main.health("dup")
