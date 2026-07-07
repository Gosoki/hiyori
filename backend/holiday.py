"""Upcoming Japanese public holidays from holidays-jp (free, no key)."""
import datetime

import httpx

JST = datetime.timezone(datetime.timedelta(hours=9))
URL = "https://holidays-jp.github.io/api/v1/date.json"


async def fetch_holidays():
    """Return the next few Japanese holidays as [{date: 'YYYY-MM-DD', name}]."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(URL)
        r.raise_for_status()
        data = r.json()   # {"2026-07-20": "海の日", ...}
    today = datetime.datetime.now(JST).date().isoformat()
    upcoming = sorted((d, name) for d, name in data.items() if d >= today)
    return [{"date": d, "name": name} for d, name in upcoming[:6]]
