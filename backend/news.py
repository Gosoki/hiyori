"""Aggregate RSS/Atom headlines. Only titles are kept — no article bodies."""
import asyncio
import calendar
import re

import feedparser
import httpx

from condget import get_if_changed

UA = {"User-Agent": "hiyori/1.0"}


def _timestamp(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return calendar.timegm(t)
    return 0


def truncate_feed_name(title):
    """Feed titles can be very long (e.g. the HN query feed). Keep just the name."""
    if not title:
        return ""
    # cut at the earliest separator: ： | ｜ ・, a full-width/en/em dash, or a spaced ASCII dash
    title = re.split(r"[:：|｜・]|\s*[－—―–]\s*|\s[-]\s", title)[0]
    return title.strip()[:16]


def split_title_and_outlet(title):
    """Google News titles end with ' - 媒体名'. Split it off → (clean_title, source)."""
    m = re.match(r"^(.*\S)\s[-–—]\s([^-–—]{1,20})$", title)
    if m:
        return m.group(1).strip(), strip_outlet_suffix(m.group(2).strip())
    return title, ""


# Outlets whose name genuinely ends in ニュース — the suffix is the brand, not a
# redundant label, so stripping it produces a wrong name (ウェザーニュース → ウェザー).
# There is no way to tell the two cases apart from the string alone.
SOURCE_KEEP_SUFFIX = {"ウェザーニュース", "アベマニュース", "テレ朝ニュース", "日テレニュース"}


def strip_outlet_suffix(src):
    """Drop the redundant ニュース / 新聞 suffix from a source name (産経ニュース → 産経,
    読売新聞 → 読売). English 'News' (Hacker News, 47NEWS) is left as-is."""
    if src in SOURCE_KEEP_SUFFIX:
        return src
    return re.sub(r"(ニュース|新聞|新闻)$", "", src).strip() or src


async def _parse_feed(client, url):
    r = await client.get(url)
    r.raise_for_status()
    # feedparser is synchronous and takes ~65 ms on the Google News feed. That is
    # 65 ms the event loop cannot read the earthquake WebSocket or fan out an EEW,
    # so keep it off the loop entirely.
    return await asyncio.to_thread(feedparser.parse, r.content)


def _item(entry, feed_source, split_source=False):
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    # only Google News titles use the ' - 媒体名' convention; splitting other feeds
    # would amputate legitimate trailing clauses (e.g. '速報 地震発生 - 津波の心配なし')
    clean, src = split_title_and_outlet(title) if split_source else (title, "")
    return {
        "title": clean,
        "link": entry.get("link", ""),
        "source": src or strip_outlet_suffix(feed_source),
        "ts": _timestamp(entry),
    }


async def fetch_news(feeds_by_category, max_per_category):
    result = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=UA) as client:
        for category, spec in feeds_by_category.items():
            mode = spec.get("mode", "recent")
            items = []
            for url in spec.get("urls", []):
                try:
                    parsed = await _parse_feed(client, url)
                    source = truncate_feed_name(parsed.feed.get("title", ""))
                    is_google = "news.google." in url
                    for e in parsed.entries:
                        it = _item(e, source, split_source=is_google)
                        if it:
                            items.append(it)
                except Exception:
                    continue  # skip a broken feed, keep the rest

            # "ranked" keeps the feed's own importance order; "recent" sorts by time.
            ordered = items if mode == "ranked" else sorted(items, key=lambda x: x["ts"], reverse=True)
            seen, unique = set(), []
            for it in ordered:
                if it["title"] not in seen:
                    seen.add(it["title"])
                    unique.append(it)
            result[category] = unique[:max_per_category]
    return result


async def fetch_alerts(feed_url, keywords, max_n, cond=None, banner_keywords=()):
    """Severe real-time disaster alerts from NERV (特務機関NERV). NERV posts every
    prefecture's routine advisory too, so we keep only titles containing a severe
    keyword — usually nothing, which is the point.

    Network/HTTP failures propagate: the caller (main.alert_loop) records them in
    /api/health, because "NERV is unreachable" must not look like "all quiet".

    `cond` is an optional per-URL dict for conditional GET (see condget.py). With
    it, an unchanged feed answers 304 and this returns None — "same as last time",
    which is NOT the same as [] ("checked, nothing severe").

    Items whose full text matches one of `banner_keywords` — and that are not
    announcing a lift (解除) — are flagged `banner: True` for the tablets' banner.
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=UA) as client:
        if cond is None:
            parsed = await _parse_feed(client, feed_url)
        else:
            r = await get_if_changed(client, feed_url, cond)
            if r is None:
                return None
            parsed = await asyncio.to_thread(feedparser.parse, r.content)
    out = []
    seen = set()
    for e in parsed.entries:                       # NERV RSS is newest-first
        title = (e.get("title") or "").strip()
        title = re.sub(r"^UN_NERV:\s*", "", title).strip().strip('“”"').strip()
        if not (title and any(k in title for k in keywords)):
            continue
        # NERV truncates the <title> to just "【…】…"; the toot body carries the
        # real detail (発生時刻・震源地・深さ・規模・最大震度) — use it as the headline.
        body = e.get("summary") or e.get("description") or ""
        body = re.sub(r"<[^>]+>", " ", body)                       # strip HTML tags
        body = re.sub(r"(?:\s*[#＃]\s*[^\s#＃]+)+\s*$", "", body)     # drop trailing hashtags
        body = re.sub(r"\s+", " ", body).strip()
        headline = body or title
        # trim the 【…】 header to just the alert type (drop the report no. + date)
        headline = re.sub(r"^【([^】\s]+)[^】]*】", r"【\1】", headline)
        # collapse repeat bulletins of one quake (第3報/第6報/最終報 → keep the newest)
        tm = re.search(r"\d+時\d+分", headline)
        ep = re.search(r"([^、。\s]{2,}?)を震源", headline)
        key = (tm.group(0) if tm else headline[:16], ep.group(1) if ep else "")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": headline[:140],
            "link": e.get("link", ""),
            "source": "NERV",
            "ts": _timestamp(e),
            "alert": True,
            "banner": any(k in headline for k in banner_keywords) and "解除" not in headline,
        })
        if len(out) >= max_n:
            break
    return out
