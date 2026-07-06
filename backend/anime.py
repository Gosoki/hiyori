"""Today's anime broadcast schedule from Jikan (MyAnimeList v4), free / no key."""
import datetime

import httpx

JST = datetime.timezone(datetime.timedelta(hours=9))
JIKAN = "https://api.jikan.moe/v4/schedules"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


async def fetch_anime(count=24):
    """Return [{time: 'HH:MM', title: 'JP title'}] for today (JST), sorted by air time.

    Notes from probing Jikan: use the per-day `filter=<weekday>` endpoint (the
    weekly/`seasons/now` paths are flaky); do NOT append `limit` (it returns an
    HTML error and breaks JSON); some shows have no broadcast time — skip those.
    """
    day = DAYS[datetime.datetime.now(JST).weekday()]
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(JIKAN, params={"filter": day})
        r.raise_for_status()
        data = r.json()
    out = []
    for a in data.get("data", []) or []:
        t = ((a.get("broadcast") or {}).get("time") or "").strip()
        if not t:
            continue
        title = (a.get("title_japanese") or a.get("title") or "").strip()
        if not title:
            continue
        out.append({"time": t, "title": title})
    out.sort(key=lambda x: x["time"])
    return out[:count]
