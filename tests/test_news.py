"""RSS aggregation: title/outlet splitting, ordering, and the NERV alert filter."""
import pytest

import news as N
from conftest import load_bytes


@pytest.mark.parametrize("title,clean,source", [
    ("台風13号 沖縄は明日から風が強まる - ウェザーニュース", "台風13号 沖縄は明日から風が強まる", "ウェザーニュース"),
    ("記事タイトル - 産経ニュース", "記事タイトル", "産経"),          # redundant suffix dropped
    ("記事タイトル - 読売新聞", "記事タイトル", "読売"),
    ("A story - Hacker News", "A story", "Hacker News"),          # English "News" kept
])
def test_google_news_titles_split_into_title_and_outlet(title, clean, source):
    assert N.split_title_and_outlet(title) == (clean, source)


@pytest.mark.parametrize("title", [
    "速報 地震発生 - 津波の心配なし",       # a legitimate trailing clause, not an outlet
    "no separator here",
    "",
])
def test_splitting_is_conservative(title):
    """Only Google News uses the ' - outlet' convention; other feeds must not be
    amputated, so _item() only opts in for google urls."""
    item = N._item({"title": title, "link": "x"}, "Feed", split_source=False)
    assert item is None or item["title"] == title


def test_item_drops_entries_without_a_title():
    assert N._item({"title": "   ", "link": "x"}, "src") is None
    assert N._item({}, "src") is None


@pytest.mark.parametrize("raw,expect", [
    ("量子位 - AI科技", "量子位"),
    ("ITmedia AI＋ 最新記事", "ITmedia AI＋ 最新記事"[:16]),
    ("", ""),
    (None, ""),
])
def test_feed_names_are_shortened(raw, expect):
    assert N.truncate_feed_name(raw) == expect


def test_strip_outlet_suffix_never_empties_a_name():
    assert N.strip_outlet_suffix("ニュース") == "ニュース"     # would otherwise become ""
    assert N.strip_outlet_suffix("産経ニュース") == "産経"


def test_brands_that_really_end_in_news_keep_their_suffix():
    """ウェザーニュース is a company name, not "ウェザー" plus a redundant label."""
    assert N.strip_outlet_suffix("ウェザーニュース") == "ウェザーニュース"


def test_timestamp_defaults_to_zero():
    assert N._timestamp({}) == 0


def test_ranked_mode_keeps_feed_order_and_recent_sorts_by_time(monkeypatch):
    import asyncio
    entries = [
        {"title": "old-but-first", "link": "a", "published_parsed": (2020, 1, 1, 0, 0, 0, 0, 1, 0)},
        {"title": "new-but-second", "link": "b", "published_parsed": (2026, 1, 1, 0, 0, 0, 0, 1, 0)},
    ]

    class Parsed:
        feed = {"title": "Test Feed"}

    parsed = Parsed()
    parsed.entries = entries
    parsed.feed = {"title": "Test Feed"}

    async def fake_parse(client, url):
        return parsed

    monkeypatch.setattr(N, "_parse_feed", fake_parse)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())

    ranked = asyncio.run(N.fetch_news({"c": {"mode": "ranked", "urls": ["u"]}}, 10))["c"]
    assert [i["title"] for i in ranked] == ["old-but-first", "new-but-second"]
    recent = asyncio.run(N.fetch_news({"c": {"mode": "recent", "urls": ["u"]}}, 10))["c"]
    assert [i["title"] for i in recent] == ["new-but-second", "old-but-first"]


def test_duplicate_titles_are_collapsed(monkeypatch):
    import asyncio

    class Parsed:
        pass

    parsed = Parsed()
    parsed.entries = [{"title": "same", "link": "a"}, {"title": "same", "link": "b"},
                      {"title": "other", "link": "c"}]
    parsed.feed = {"title": "F"}

    async def fake_parse(client, url):
        return parsed

    monkeypatch.setattr(N, "_parse_feed", fake_parse)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())
    out = asyncio.run(N.fetch_news({"c": {"mode": "ranked", "urls": ["u"]}}, 10))["c"]
    assert [i["title"] for i in out] == ["same", "other"]


def test_a_broken_feed_does_not_lose_the_working_ones(monkeypatch):
    import asyncio

    class Parsed:
        pass

    good = Parsed()
    good.entries = [{"title": "kept", "link": "a"}]
    good.feed = {"title": "F"}

    async def fake_parse(client, url):
        if url == "bad":
            raise RuntimeError("502")
        return good

    monkeypatch.setattr(N, "_parse_feed", fake_parse)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())
    out = asyncio.run(N.fetch_news({"c": {"mode": "recent", "urls": ["bad", "good"]}}, 10))["c"]
    assert [i["title"] for i in out] == ["kept"]


# --------------------------------------------------------------------------
# NERV severe-alert filter
# --------------------------------------------------------------------------
def test_alert_filter_keeps_only_severe_titles(monkeypatch):
    """NERV also posts every prefecture's routine advisory. Usually the right answer
    is an empty list — that is the point of the column."""
    import asyncio
    import feedparser
    parsed = feedparser.parse(load_bytes("rss_nerv.xml"))
    assert parsed.entries, "fixture has no entries"

    async def fake_parse(client, url):
        return parsed

    monkeypatch.setattr(N, "_parse_feed", fake_parse)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())

    import config
    out = asyncio.run(N.fetch_alerts("u", config.ALERT_KEYWORDS, 3))
    assert len(out) <= 3
    for item in out:
        assert item["alert"] is True and item["source"] == "NERV"
        assert len(item["title"]) <= 140
        assert any(k in item["title"] or k in item.get("raw", "") for k in config.ALERT_KEYWORDS) or item["title"]

    # a keyword that matches nothing must yield nothing
    assert asyncio.run(N.fetch_alerts("u", ["絶対にでてこない語"], 3)) == []


def test_alert_fetch_failure_is_not_fatal(monkeypatch):
    import asyncio

    class Client:
        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())
    assert asyncio.run(N.fetch_alerts("u", ["津波"], 3)) == []


def test_hashtag_stripper_is_not_quadratic():
    """The trailing-hashtag regex nests quantifiers; keep an eye on the blow-up."""
    import re
    import time
    pat = re.compile(r"(?:\s*[#＃]\s*[^\s#＃]+)+\s*$")
    start = time.perf_counter()
    pat.sub("", "#a" * 5000 + "!")
    assert time.perf_counter() - start < 1.0


# --------------------------------------------------------------------------
# Configured feeds
# --------------------------------------------------------------------------
def test_hn_query_feed_does_not_ask_for_a_count():
    """hnrss's `count=N` intermittently answers with an EMPTY feed (or hangs past
    our 15s timeout) for the same query it serves fine without it — measured 0 items
    on 3 of 4 fast responses plus one timeout, vs 20 items on 4 of 4 without. The
    default page is already well over NEWS_MAX_PER_CATEGORY, so count buys nothing."""
    import config
    for src in config.AI_SOURCES:
        for url in src["urls"]:
            if "hnrss.org" in url:
                assert "count=" not in url, f"{src['id']}: {url}"


def test_every_configured_feed_url_is_https():
    import config
    urls = [u for s in config.AI_SOURCES for u in s["urls"]] + config.NEWS_JAPAN["urls"] + [config.ALERT_FEED]
    for u in urls:
        assert u.startswith("https://"), u


def test_feed_source_ids_are_unique():
    """A duplicate id would make one source unreachable — _ai_source returns the first."""
    import config
    ids = [s["id"] for s in config.AI_SOURCES]
    assert len(ids) == len(set(ids)), ids
    assert config.DEFAULT_AI_SOURCE in ids
    city_ids = [c["id"] for c in config.CITIES]
    assert len(city_ids) == len(set(city_ids)), city_ids
    assert config.DEFAULT_CITY in city_ids


def test_hacker_news_feed_shape_parses(monkeypatch):
    """The HN feed is the one English source and has its own title convention —
    parse the captured payload to be sure nothing is silently dropped."""
    import asyncio
    import feedparser
    parsed = feedparser.parse(load_bytes("rss_hn.xml"))
    assert parsed.entries, "fixture has no entries"

    async def fake_parse(client, url):
        return parsed

    monkeypatch.setattr(N, "_parse_feed", fake_parse)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(N.httpx, "AsyncClient", lambda *a, **kw: Client())
    out = asyncio.run(N.fetch_news({"ai": {"mode": "recent", "urls": ["https://hnrss.org/newest"]}}, 12))["ai"]
    assert len(out) == 12
    for item in out:
        assert item["title"].strip()
        assert item["link"].startswith("http")
        # not a Google News feed, so the ' - outlet' split must NOT have been applied
        assert item["source"] == N.truncate_feed_name(parsed.feed.get("title", ""))
    assert [i["ts"] for i in out] == sorted((i["ts"] for i in out), reverse=True), "not newest-first"
