"""Anime broadcast schedule from Jikan (MyAnimeList v4), free / no key.

The "broadcast day" runs 00:00 today → 05:59 tomorrow (JST). Tomorrow's pre-6am
late-night shows are written in the Japanese 24h+ convention (02:00 → 26:00) so
they sort after today's and read as "tonight's late night". Everything is then in
plain chronological order.
"""
import datetime
import re

import httpx

JST = datetime.timezone(datetime.timedelta(hours=9))
JIKAN = "https://api.jikan.moe/v4/schedules"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _hhmm(raw):
    """Zero-pad a broadcast time to 'HH:MM'; '' if it isn't a time at all.

    Everything downstream (the 24h+ shift, the chronological sort, the frontend's
    18:00 cutoff) compares these as plain strings, so a stray '7:05' would sort
    after '23:00'. Normalize once, here.
    """
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", raw or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


async def _day(client, weekday_idx):
    """[(time 'HH:MM', title)] for one weekday. `filter=<day>`; do NOT add limit."""
    r = await client.get(JIKAN, params={"filter": DAYS[weekday_idx]})
    r.raise_for_status()
    out, seen = [], set()
    for a in (r.json().get("data") or []):
        if not isinstance(a, dict):
            continue
        t = _hhmm((a.get("broadcast") or {}).get("time"))
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
        # Jikan's gateway 504s on compressed responses during its (frequent, days-long)
        # outages while identity responses keep working — don't request compression.
        # Payload is ~10-20 shows twice a day; the bandwidth cost is irrelevant.
        client.headers.pop("accept-encoding", None)
        today = await _day(client, today_i)
        tomorrow = await _day(client, tmr_i)
    rows = [{"time": t, "title": ttl} for t, ttl in today]
    for t, ttl in tomorrow:
        hh = int(t[:2])                               # _hhmm guarantees 'HH:MM'
        if hh < 6:                                    # only next-day shows before 06:00
            rows.append({"time": f"{hh + 24:02d}:{t[3:]}", "title": ttl})   # 02:00 → 26:00
    rows.sort(key=lambda x: x["time"])                # "00:00".."29:59" sorts lexically
    return rows[:count]
