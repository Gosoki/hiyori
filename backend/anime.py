"""Anime broadcast schedule from Jikan (MyAnimeList v4), free / no key.

The "broadcast day" runs 00:00 today → 05:59 tomorrow (JST). Tomorrow's pre-6am
late-night shows are written in the Japanese 24h+ convention (02:00 → 26:00) so
they sort after today's and read as "tonight's late night". Everything is then in
plain chronological order.
"""
import datetime

import httpx

JST = datetime.timezone(datetime.timedelta(hours=9))
JIKAN = "https://api.jikan.moe/v4/schedules"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


async def _day(client, weekday_idx):
    """[(time 'HH:MM', title)] for one weekday. `filter=<day>`; do NOT add limit."""
    r = await client.get(JIKAN, params={"filter": DAYS[weekday_idx]})
    r.raise_for_status()
    out, seen = [], set()
    for a in (r.json().get("data") or []):
        t = ((a.get("broadcast") or {}).get("time") or "").strip()
        title = (a.get("title_japanese") or a.get("title") or "").strip()
        key = a.get("mal_id") or (t, title)
        if t and title and key not in seen:   # Jikan sometimes lists the same anime twice
            seen.add(key)
            out.append((t, title))
    return out


async def fetch_anime(count=24):
    now = datetime.datetime.now(JST)
    today_i, tmr_i = now.weekday(), (now.weekday() + 1) % 7
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "hiyori/1.0"}) as client:
        today = await _day(client, today_i)
        tomorrow = await _day(client, tmr_i)
    rows = [{"time": t, "title": ttl} for t, ttl in today]
    for t, ttl in tomorrow:
        try:
            hh = int(t[:2])
        except ValueError:
            continue
        if hh < 6:                                    # only next-day shows before 06:00
            rows.append({"time": f"{hh + 24:02d}:{t[3:]}", "title": ttl})   # 02:00 → 26:00
    rows.sort(key=lambda x: x["time"])                # "00:00".."29:59" sorts lexically
    return rows[:count]
