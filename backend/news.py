"""Aggregate RSS/Atom headlines. Only titles are kept — no article bodies."""
import calendar
import re

import feedparser
import httpx


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
    # cut at the earliest separator (": ", " - ", " | ", "：", …)
    title = re.split(r"[:：|]|\s[-–—]\s", title)[0]
    return title.strip()[:16]


async def fetch_news(feeds_by_category, max_per_category):
    result = {}
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True,
        headers={"User-Agent": "hiyori/1.0"},
    ) as client:
        for category, urls in feeds_by_category.items():
            items = []
            for url in urls:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    parsed = feedparser.parse(r.content)
                    source = _short_source(parsed.feed.get("title", ""))
                    for e in parsed.entries:
                        title = (e.get("title") or "").strip()
                        if title:
                            items.append({
                                "title": title,
                                "link": e.get("link", ""),
                                "source": source,
                                "ts": _timestamp(e),
                            })
                except Exception:
                    continue  # skip a broken feed, keep the rest

            seen, unique = set(), []
            for it in sorted(items, key=lambda x: x["ts"], reverse=True):
                if it["title"] not in seen:
                    seen.add(it["title"])
                    unique.append(it)
            result[category] = unique[:max_per_category]
    return result
