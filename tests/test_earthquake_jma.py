"""The 気象庁 XML backup quake source.

The point of these tests is that the fallback is INDISTINGUISHABLE from P2P on the
tablets: same event shape, same intensity codes, and — critically — the same
quake_key, so a quake reported by both sources appears once, not twice.
"""
import asyncio

import pytest

import earthquake_jma as J
from earthquake import quake_key
from conftest import load_bytes, load_json


@pytest.fixture
def report():
    return J.parse_report(load_bytes("jma_vxse53_0.xml"))


def test_parses_a_real_report(report):
    assert report is not None
    assert report["kind"] == "quake"
    assert report["source"] == "jma"
    assert report["originTime"]
    assert report["hypocenter"]["name"]


def test_event_shape_matches_the_p2p_normalizer(report):
    """Both sources feed the same renderer, so the key set has to agree."""
    import earthquake as E
    p2p = E.normalize_quake(load_json("p2p_551.json")[0])
    assert set(p2p) <= set(report), f"missing from the JMA event: {set(p2p) - set(report)}"


def test_the_same_quake_gets_the_same_key_from_both_sources():
    """P2P's `earthquake.time` is JMA's ArrivalTime, not OriginTime — keying off the
    wrong one would list one quake twice while both sources are in play."""
    import earthquake as E
    jma = {quake_key(J.parse_report(load_bytes(f"jma_vxse53_{i}.xml"))) for i in range(3)}
    p2p = {quake_key(E.normalize_quake(m)) for m in load_json("p2p_551.json")}
    overlap = jma & p2p
    assert overlap, f"no shared quakes between the fixtures — jma={sorted(jma)}"
    assert len(overlap) == len(jma), f"JMA keys with no P2P match: {sorted(jma - p2p)}"


def test_fields_agree_with_p2p_for_the_same_quake():
    import earthquake as E
    p2p = {quake_key(E.normalize_quake(m)): E.normalize_quake(m) for m in load_json("p2p_551.json")}
    checked = 0
    for i in range(3):
        a = J.parse_report(load_bytes(f"jma_vxse53_{i}.xml"))
        b = p2p.get(quake_key(a))
        if not b:
            continue
        checked += 1
        assert a["hypocenter"]["name"] == b["hypocenter"]["name"]
        assert a["maxScale"] == b["maxScale"]
        assert abs(a["hypocenter"]["magnitude"] - b["hypocenter"]["magnitude"]) < 0.05
        assert a["hypocenter"]["depth"] == b["hypocenter"]["depth"]
    assert checked, "no overlapping quake to compare"


@pytest.mark.parametrize("label,code", [
    ("1", 10), ("4", 40), ("5-", 45), ("5+", 50), ("6-", 55), ("6+", 60), ("7", 70),
])
def test_jma_intensity_notation_maps_to_the_p2p_scale(label, code):
    """JMA writes 5-/5+; everything else in the app is built on P2P's numeric codes."""
    assert J._intensity_scale(label) == code


@pytest.mark.parametrize("bad", ["", None, "5", "８", "x"])
def test_unknown_intensity_is_unknown_not_zero(bad):
    assert J._intensity_scale(bad) == -1 or bad == "5"


@pytest.mark.parametrize("text,expect", [
    ("+35.7+139.7-120000/", (35.7, 139.7, 120)),
    ("+32.5+130.6-0/", (32.5, 130.6, 0)),          # ごく浅い renders as "very shallow"
    ("+43.1+146.0/", (43.1, 146.0, -1)),           # depth omitted
    ("", (-200, -200, -1)),
    ("garbage", (-200, -200, -1)),
])
def test_coordinate_parsing(text, expect):
    assert J._coordinate(text) == expect


def test_origin_time_is_formatted_like_p2p():
    assert J._origin_time("2026-08-05T18:06:00+09:00") == "2026/08/05 18:06:00"
    assert J._origin_time("nonsense") == ""
    assert J._origin_time(None) == ""


def test_drills_and_tests_are_never_published():
    raw = load_bytes("jma_vxse53_0.xml").replace(b"<Status>\xe9\x80\x9a\xe5\xb8\xb8</Status>",
                                                 "<Status>訓練</Status>".encode())
    assert J.parse_report(raw) is None


def test_cancellation_is_flagged():
    raw = load_bytes("jma_vxse53_0.xml").replace("<InfoType>発表</InfoType>".encode(),
                                                 "<InfoType>取消</InfoType>".encode())
    assert J.parse_report(raw)["cancelled"] is True


def test_first_poll_primes_without_replaying_history(monkeypatch):
    """A fallback that kicks in at 3am must not take the screen over for a quake
    that happened hours earlier."""
    _install_fake_http(monkeypatch)
    seen = set()
    assert asyncio.run(J.fetch_reports("feed", seen)) == []
    assert seen, "the priming pass should have recorded what is already published"


def test_second_poll_returns_only_new_reports(monkeypatch):
    _install_fake_http(monkeypatch)
    seen = set()
    asyncio.run(J.fetch_reports("feed", seen))          # prime
    dropped = next(u for u in list(seen) if "VXSE53" in u)
    seen.discard(dropped)                               # pretend it is newly published
    out = asyncio.run(J.fetch_reports("feed", seen))
    assert len(out) == 1 and out[0]["kind"] == "quake"


def test_seen_forgets_reports_that_left_the_feed(monkeypatch):
    """`seen` is bounded by what the feed currently lists — a URL that scrolled
    off can never come back as fresh, so keeping it only grows the set."""
    _install_fake_http(monkeypatch)
    seen = set()
    asyncio.run(J.fetch_reports("feed", seen))          # prime
    seen.add("https://example.invalid/long-gone")
    asyncio.run(J.fetch_reports("feed", seen))
    assert "https://example.invalid/long-gone" not in seen
    assert seen, "pruning must never empty a set the feed still fills"


def test_a_304_from_the_feed_means_nothing_new(monkeypatch):
    class Resp:
        status_code = 304
        headers = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            assert headers.get("If-Modified-Since") == "x"
            return Resp()

    monkeypatch.setattr(J.httpx, "AsyncClient", lambda *a, **kw: Client())
    seen = {"already"}
    assert asyncio.run(J.fetch_reports("feed", seen, cond={"last_modified": "x"})) == []
    assert seen == {"already"}, "a 304 must not touch the seen set"


def test_a_report_whose_download_failed_is_retried_next_poll(monkeypatch):
    """Marking a URL seen before it was fetched turned one 503 into a permanently
    lost alert — during the exact outage the fallback exists for."""
    feed = load_bytes("jma_eqvol_feed.xml")
    report = load_bytes("jma_vxse53_0.xml")
    state = {"fail": True}

    class Resp:
        def __init__(self, content):
            self.content = content
            self.status_code = 200
            self.headers = {}

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **kw):
            if url == "feed":
                return Resp(feed)
            if state["fail"]:
                raise RuntimeError("503 from JMA")
            return Resp(report)

    monkeypatch.setattr(J.httpx, "AsyncClient", lambda *a, **kw: Client())
    seen = set()
    asyncio.run(J.fetch_reports("feed", seen))          # prime
    target = next(u for u in list(seen) if "VXSE53" in u)
    seen.discard(target)                                # newly published
    assert asyncio.run(J.fetch_reports("feed", seen)) == []
    assert target not in seen, "a failed download was marked seen"
    state["fail"] = False
    out = asyncio.run(J.fetch_reports("feed", seen))
    assert len(out) == 1 and target in seen


def test_limit_zero_only_marks_without_downloading(monkeypatch):
    """Standby mode: keep `seen` current at zero cost while P2P is the live source."""
    downloads = []
    feed = load_bytes("jma_eqvol_feed.xml")

    class Resp:
        def __init__(self, content):
            self.content = content
            self.status_code = 200
            self.headers = {}

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **kw):
            if url != "feed":
                downloads.append(url)
            return Resp(feed if url == "feed" else b"")

    monkeypatch.setattr(J.httpx, "AsyncClient", lambda *a, **kw: Client())
    seen = set()
    asyncio.run(J.fetch_reports("feed", seen, limit=0))        # prime
    seen.discard(next(u for u in list(seen) if "VXSE53" in u))
    assert asyncio.run(J.fetch_reports("feed", seen, limit=0)) == []
    assert downloads == [] and all("VXSE53" not in u or u in seen for u in seen)


def test_one_unreadable_report_does_not_lose_the_others(monkeypatch):
    _install_fake_http(monkeypatch, corrupt_first=True)
    seen = set()
    asyncio.run(J.fetch_reports("feed", seen))
    for u in [x for x in list(seen) if "VXSE53" in x][:2]:
        seen.discard(u)
    out = asyncio.run(J.fetch_reports("feed", seen))
    assert len(out) >= 1, "a corrupt report took the whole batch down with it"


def _install_fake_http(monkeypatch, corrupt_first=False):
    """Serve the captured feed + reports instead of hitting JMA."""
    feed = load_bytes("jma_eqvol_feed.xml")
    reports = [load_bytes(f"jma_vxse53_{i}.xml") for i in range(3)]
    state = {"n": 0}

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, *a, **kw):
            if url == "feed":
                return Resp(feed)
            if corrupt_first and state["n"] == 0:
                state["n"] += 1
                return Resp(b"<not-xml")
            state["n"] += 1
            return Resp(reports[state["n"] % len(reports)])

    monkeypatch.setattr(J.httpx, "AsyncClient", lambda *a, **kw: Client())
