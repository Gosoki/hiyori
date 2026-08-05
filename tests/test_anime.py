"""The 新番 broadcast list.

Everything downstream compares these times as plain strings — the 24h+ shift, the
chronological sort, and the frontend's 18:00 cutoff — so the only real invariant is
that every time is a zero-padded "HH:MM" in "00:00".."29:59".
"""
import asyncio

import pytest

import anime as A


@pytest.mark.parametrize("raw,expect", [
    ("07:05", "07:05"),
    ("7:05", "07:05"),        # a single-digit hour would sort after "23:00"
    (" 23:30 ", "23:30"),
    ("00:00", "00:00"),
    ("", ""),
    (None, ""),
    ("nonsense", ""),
    ("25:00", "25:00"),
    ("7:5", ""),
])
def test_broadcast_times_are_normalized(raw, expect):
    assert A._hhmm(raw) == expect


def _fake_client(monkeypatch, by_day):
    class Resp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self._data}

    class Client:
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, **kw):
            return Resp(by_day.get(params["filter"], []))

    monkeypatch.setattr(A.httpx, "AsyncClient", lambda *a, **kw: Client())


def _show(mal_id, time, title):
    return {"mal_id": mal_id, "broadcast": {"time": time}, "title_japanese": title}


def test_next_day_late_night_uses_24h_plus_notation(monkeypatch):
    """A 02:00 show tomorrow is "tonight's late night" — it must sort after 23:00,
    which plain "02:00" would not."""
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    tomorrow = A.DAYS[(now.weekday() + 1) % 7]
    _fake_client(monkeypatch, {
        today: [_show(1, "23:00", "tonight"), _show(2, "07:00", "morning")],
        tomorrow: [_show(3, "02:00", "late night"), _show(4, "19:00", "tomorrow evening")],
    })
    rows = asyncio.run(A.fetch_anime(24))
    times = [r["time"] for r in rows]
    assert times == sorted(times), "output is not chronological"
    assert "26:00" in times, "tomorrow's 02:00 show was not shifted"
    assert "19:00" not in times, "tomorrow's evening show should not be in today's list"
    assert times == ["07:00", "23:00", "26:00"]


def test_duplicate_listings_are_collapsed(monkeypatch):
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    _fake_client(monkeypatch, {today: [_show(1, "21:00", "same"), _show(1, "21:00", "same")]})
    assert len(asyncio.run(A.fetch_anime(24))) == 1


def test_unparseable_times_are_dropped(monkeypatch):
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    _fake_client(monkeypatch, {today: [
        _show(1, "21:00", "ok"), _show(2, "", "no time"), _show(3, "???", "junk"),
        {"mal_id": 4, "broadcast": None, "title": "no broadcast"},
        {"mal_id": 5, "broadcast": {"time": "22:00"}, "title_japanese": "  "},
    ]})
    rows = asyncio.run(A.fetch_anime(24))
    assert [r["title"] for r in rows] == ["ok"]


def test_non_dict_rows_do_not_crash_the_fetch(monkeypatch):
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    _fake_client(monkeypatch, {today: [None, "x", 3, _show(1, "21:00", "ok")]})
    assert [r["title"] for r in asyncio.run(A.fetch_anime(24))] == ["ok"]


def test_count_is_respected(monkeypatch):
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    _fake_client(monkeypatch, {today: [_show(i, f"{i:02d}:00", f"show{i}") for i in range(6, 20)]})
    assert len(asyncio.run(A.fetch_anime(5))) == 5


def test_every_emitted_time_is_well_formed(monkeypatch):
    now = A.datetime.datetime.now(A.JST)
    today = A.DAYS[now.weekday()]
    tomorrow = A.DAYS[(now.weekday() + 1) % 7]
    _fake_client(monkeypatch, {
        today: [_show(i, f"{i:02d}:30", f"a{i}") for i in range(0, 24)],
        tomorrow: [_show(100 + i, f"{i:02d}:15", f"b{i}") for i in range(0, 24)],
    })
    for row in asyncio.run(A.fetch_anime(50)):
        hh, mm = row["time"].split(":")
        assert len(hh) == 2 and len(mm) == 2
        assert 0 <= int(hh) <= 29 and 0 <= int(mm) <= 59
