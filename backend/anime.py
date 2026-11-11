"""Anime broadcast schedule from AniList, free / no key; Jikan (MyAnimeList v4,
also free) stands in when AniList fails. AniList is primary because its times are
per episode (skips breaks) and Jikan has frequent, days-long outages.

The "broadcast day" runs 00:00 today → 05:59 tomorrow (JST). Tomorrow's pre-6am
late-night shows are written in the Japanese 24h+ convention (02:00 → 26:00) so
they sort after today's and read as "tonight's late night". Everything is then in
plain chronological order.
"""
import datetime
import logging
import re

import httpx

log = logging.getLogger("hiyori.anime")

JST = datetime.timezone(datetime.timedelta(hours=9))
JIKAN = "https://api.jikan.moe/v4/schedules"
ANILIST = "https://graphql.anilist.co"
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


async def _jikan(client, now):
    today = await _day(client, now.weekday())
    tomorrow = await _day(client, (now.weekday() + 1) % 7)
    rows = [{"time": t, "title": ttl} for t, ttl in today]
    for t, ttl in tomorrow:
        hh = int(t[:2])                               # _hhmm guarantees 'HH:MM'
        if hh < 6:                                    # only next-day shows before 06:00
            rows.append({"time": f"{hh + 24:02d}:{t[3:]}", "title": ttl})   # 02:00 → 26:00
    return rows


_ANILIST_Q = """query($lo:Int,$hi:Int,$page:Int){Page(page:$page,perPage:50){pageInfo{hasNextPage}
airingSchedules(airingAt_greater:$lo,airingAt_lesser:$hi,sort:TIME){airingAt
media{id isAdult countryOfOrigin title{native romaji}}}}}"""


async def _anilist(client, now):
    """Per-episode airing times (unix), so the broadcast day is just [00:00, +30h)."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lo = int(start.timestamp()) - 1                   # airingAt_greater is exclusive
    hi = int(start.timestamp()) + 30 * 3600           # < 06:00 tomorrow
    rows, seen = [], set()
    for page in range(1, 5):                          # a busy day is well under 200 episodes
        r = await client.post(ANILIST, json={"query": _ANILIST_Q,
                                             "variables": {"lo": lo, "hi": hi, "page": page}})
        r.raise_for_status()
        p = (r.json().get("data") or {}).get("Page") or {}
        for s in p.get("airingSchedules") or []:
            m = (s or {}).get("media") or {}
            # JP only (AniList also lists donghua), no adult; a double episode → one row
            if m.get("isAdult") or m.get("countryOfOrigin") != "JP" or m.get("id") in seen:
                continue
            names = m.get("title") or {}
            title = (names.get("native") or names.get("romaji") or "").strip()
            if not title or not isinstance(s.get("airingAt"), int):
                continue
            seen.add(m.get("id"))
            at = datetime.datetime.fromtimestamp(s["airingAt"], JST)
            hh = at.hour + (24 if at.date() > start.date() else 0)   # 02:00 tomorrow → 26:00
            rows.append({"time": f"{hh:02d}:{at.minute:02d}", "title": title})
        if not (p.get("pageInfo") or {}).get("hasNextPage"):
            break
    return rows


async def fetch_anime(count=24):
    now = datetime.datetime.now(JST)
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "hiyori/1.0"}) as client:
        # Jikan's gateway 504s on compressed responses during its (frequent, days-long)
        # outages while identity responses keep working — don't request compression.
        # Payload is ~10-20 shows twice a day; the bandwidth cost is irrelevant.
        client.headers.pop("accept-encoding", None)
        try:
            rows = await _anilist(client, now)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("AniList unavailable (%s: %s); using Jikan", type(e).__name__, e)
            rows = []
        if not rows:
            rows = await _jikan(client, now)
    rows.sort(key=lambda x: x["time"])                # "00:00".."29:59" sorts lexically
    return rows[:count]
