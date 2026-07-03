"""Aggregate RSS/Atom headlines. Only titles are kept — no article bodies."""
import calendar
import re

import feedparser
import httpx

UA = {"User-Agent": "hiyori/1.0"}


def _timestamp(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return calendar.timegm(t)
    return 0


def _short_source(title):
    """Feed titles can be very long (e.g. the HN query feed). Keep just the name."""
    if not title:
        return ""
    # cut at the earliest separator: ： | ｜ ・, a full-width/en/em dash, or a spaced ASCII dash
    title = re.split(r"[:：|｜・]|\s*[－—―–]\s*|\s[-]\s", title)[0]
    return title.strip()[:16]


def _split_source(title):
    """Google News titles end with ' - 媒体名'. Split it off → (clean_title, source)."""
    m = re.match(r"^(.*\S)\s[-–—]\s([^-–—]{1,20})$", title)
    if m:
        return m.group(1).strip(), _clean_source(m.group(2).strip())
    return title, ""


def _clean_source(src):
    """Drop the redundant ニュース / 新聞 suffix from a source name (産経ニュース → 産経,
    読売新聞 → 読売). English 'News' (Hacker News, 47NEWS) is left as-is."""
    return re.sub(r"(ニュース|新聞|新闻)$", "", src).strip() or src


async def _parse_feed(client, url):
    r = await client.get(url)
    r.raise_for_status()
    return feedparser.parse(r.content)


def _item(entry, feed_source):
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    clean, src = _split_source(title)
    return {
        "title": clean,
        "link": entry.get("link", ""),
        "source": src or _clean_source(feed_source),
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
                    source = _short_source(parsed.feed.get("title", ""))
                    for e in parsed.entries:
                        it = _item(e, source)
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


async def fetch_alerts(feed_url, keywords, max_n):
    """Severe real-time disaster alerts from NERV (特務機関NERV). NERV posts every
    prefecture's routine advisory too, so we keep only titles containing a severe
    keyword — usually nothing, which is the point."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=UA) as client:
            parsed = await _parse_feed(client, feed_url)
    except Exception:
        return []
    out = []
    for e in parsed.entries:                       # NERV RSS is newest-first
        title = (e.get("title") or "").strip()
        title = re.sub(r"^UN_NERV:\s*", "", title).strip().strip('“”"').strip()
        if title and any(k in title for k in keywords):
            out.append({
                "title": title[:80],
                "link": e.get("link", ""),
                "source": "NERV",
                "ts": _timestamp(e),
                "alert": True,
            })
            if len(out) >= max_n:
                break
    return out
