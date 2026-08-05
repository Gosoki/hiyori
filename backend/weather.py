"""Fetch and normalize weather from the 気象庁 (JMA) free forecast API."""
import datetime

import httpx

FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/{area}.json"
JST = datetime.timezone(datetime.timedelta(hours=9))

# JMA weather code -> (emoji, short JP label). Fallback by first digit below.
WEATHER_CODES = {
    "100": ("☀️", "晴"), "101": ("🌤️", "晴時々曇"), "102": ("🌦️", "晴一時雨"),
    "103": ("🌦️", "晴時々雨"), "104": ("🌨️", "晴一時雪"), "105": ("🌨️", "晴時々雪"),
    "110": ("🌤️", "晴後曇"), "111": ("🌤️", "晴後曇"), "112": ("🌦️", "晴後雨"),
    "114": ("🌦️", "晴後雨"), "115": ("🌨️", "晴後雪"), "116": ("🌨️", "晴後雪"),
    "119": ("⛈️", "晴後雷雨"), "125": ("⛈️", "晴後雷雨"), "128": ("🌤️", "晴後曇"),
    "200": ("☁️", "曇"), "201": ("⛅", "曇時々晴"), "202": ("🌧️", "曇一時雨"),
    "203": ("🌧️", "曇時々雨"), "204": ("🌨️", "曇一時雪"), "205": ("🌨️", "曇時々雪"),
    "206": ("🌧️", "曇後雨"), "207": ("🌧️", "曇後雨"), "209": ("🌫️", "霧"),
    "210": ("⛅", "曇後晴"), "211": ("⛅", "曇後晴"), "212": ("🌧️", "曇後雨"),
    "214": ("🌧️", "曇後雨"), "215": ("🌨️", "曇後雪"), "216": ("🌨️", "曇後雪"),
    "219": ("🌧️", "曇後雨"), "223": ("⛅", "曇時々晴"), "224": ("🌧️", "曇一時雨"),
    "260": ("🌨️", "曇時々雪"), "270": ("🌨️", "曇時々雪"),
    "300": ("🌧️", "雨"), "301": ("🌦️", "雨時々晴"), "302": ("🌧️", "雨時々止む"),
    "303": ("🌨️", "雨時々雪"), "304": ("🌨️", "雨か雪"), "306": ("🌧️", "大雨"),
    "308": ("🌧️", "暴風雨"), "311": ("🌦️", "雨後晴"), "313": ("🌧️", "雨後曇"),
    "314": ("🌨️", "雨後雪"), "315": ("❄️", "雨後雪"), "316": ("🌦️", "雨か雪後晴"),
    "317": ("🌧️", "雨か雪後曇"), "320": ("🌦️", "雨後晴"), "321": ("🌧️", "雨後曇"),
    "323": ("🌦️", "雨後晴"), "328": ("🌧️", "大雨"), "329": ("🌨️", "雨一時みぞれ"),
    "340": ("🌨️", "雪か雨"), "350": ("⛈️", "雷雨"), "361": ("🌦️", "雪か雨後晴"),
    "371": ("🌧️", "雪か雨後曇"),
    "400": ("❄️", "雪"), "401": ("🌨️", "雪時々晴"), "402": ("❄️", "雪時々止む"),
    "403": ("🌨️", "雪時々雨"), "405": ("❄️", "大雪"), "406": ("🌨️", "風雪強い"),
    "407": ("❄️", "暴風雪"), "409": ("🌨️", "雪一時雨"), "411": ("🌨️", "雪後晴"),
    "413": ("❄️", "雪後曇"), "414": ("🌨️", "雪後雨"), "420": ("🌨️", "雪後晴"),
    "421": ("❄️", "雪後曇"), "422": ("🌨️", "雪後雨"), "425": ("❄️", "大雪"),
    "450": ("⛈️", "雷雪"),
}
FALLBACK = {"1": ("☀️", "晴"), "2": ("☁️", "曇"), "3": ("🌧️", "雨"), "4": ("❄️", "雪")}
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _icon(code):
    code = str(code or "")
    if code in WEATHER_CODES:
        return WEATHER_CODES[code]
    return FALLBACK.get(code[:1], ("❓", "—"))


def int_at(seq, i):
    """seq[i] as an int, or None for missing / blank / non-numeric."""
    try:
        v = seq[i]
        if v in ("", None):
            return None
        return int(float(v))
    except (IndexError, ValueError, TypeError):
        return None


def date_labels(iso):
    d = datetime.date.fromisoformat(iso[:10])
    return {"md": f"{d.month}/{d.day}", "weekday": WEEKDAY_JA[d.weekday()]}


def _ints(pairs):
    """[(when, raw)] -> {date: [int, …]}, dropping blanks and non-numeric values.

    Converts BEFORE inserting: `setdefault(d, []).append(int(x))` would leave an
    empty list behind whenever int() raised, and a later max() on it would blow up
    the whole forecast parse.
    """
    out = {}
    for when, raw in pairs:
        if raw in ("", None):
            continue
        try:
            v = int(float(raw))
        except (ValueError, TypeError):
            continue
        out.setdefault(str(when)[:10], []).append(v)
    return out


# JMA keys the two halves of a forecast differently:
#   weather / pops  -> class10 sub-region codes  (東京地方 130010, 愛知西部 230010)
#   temps           -> AMeDAS observatory codes  (東京 44132, 名古屋 51106)
# so they need different lookups. Both fall back to the first area, which is the
# prefecture's principal region/observatory — right for every city in CITIES, and
# the only sane guess for one whose sub-region JMA doesn't list separately.
def _pick_area(areas, field, want):
    areas = [a for a in (areas or []) if isinstance(a, dict)]
    for a in areas:
        if (a.get("area") or {}).get(field) == want:
            return a
    return areas[0] if areas else {}      # no match / no areas → no-data dict, never an IndexError


def _match_area(areas, code):
    """Sub-region by class10 code (weather + precip probability series)."""
    return _pick_area(areas, "code", code)


def _match_station(areas, city_name):
    """Observatory by name (temperature series — coded, not class10-coded)."""
    return _pick_area(areas, "name", city_name)


# JMA publishes today's high/low only while they are still forecasts: by ~17:00 JST
# the short-term series has rolled over to tomorrow and today's numbers are simply
# gone. Remember the last ones we saw (per city, per JST day) so the today card
# keeps showing the real daily high/low for the rest of the day instead of blanking
# — otherwise the frontend falls back to the hourly strip, which starts at the
# current hour and so reports the *evening* peak as "today's high".
_today_temp_memo = {}   # city id -> {"date": "YYYY-MM-DD", "tempMax": int|None, "tempMin": int|None}


def _apply_today_temp_memo(cfg, today):
    date = datetime.datetime.now(JST).date().isoformat()
    memo = _today_temp_memo.get(cfg["id"])
    if not memo or memo["date"] != date:            # new JST day → forget yesterday's
        memo = {"date": date, "tempMax": None, "tempMin": None}
        _today_temp_memo[cfg["id"]] = memo
    for key in ("tempMax", "tempMin"):
        if today.get(key) is not None:
            memo[key] = today[key]
        elif memo[key] is not None:
            today[key] = memo[key]


async def fetch_weather(cfg, weekly_count=6):
    url = FORECAST_URL.format(area=cfg["area_code"])
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    result = _parse(data, cfg)
    result["weekly"] = result["weekly"][:weekly_count]   # fixed length (JMA gives 6–7)
    _apply_today_temp_memo(cfg, result["today"])
    return result


def _parse(data, cfg):
    today = datetime.datetime.now(JST).date().isoformat()
    short = data[0]
    ts = short["timeSeries"]

    warea = _match_area(ts[0]["areas"], cfg["class10_code"])
    code0 = (warea.get("weatherCodes") or [""])[0] or ""     # element may be null
    icon0, short0 = _icon(code0)
    # JMA separates the text with full-width spaces at odd points (e.g. around
    # "まで"), which reads awkwardly. Japanese has no spaces — drop them entirely.
    text0 = ((warea.get("weathers") or [""])[0] or "").replace("　", "").replace(" ", "").strip() or short0
    tmax, tmin = _today_temps(ts, cfg, today)

    result = {
        "city": cfg["city_name"],
        "updated": datetime.datetime.now(JST).isoformat(timespec="minutes"),
        "today": {
            "code": code0, "icon": icon0, "text": text0,
            "pop": _today_pop(ts, cfg, today),
            "tempMax": tmax, "tempMin": tmin,
        },
        "weekly": _weekly(data[1], cfg, today) if len(data) > 1 else [],
    }
    # JMA's weekly series often lacks tomorrow's temps — fill from the short-term forecast.
    st = _shortterm_temps(ts, cfg)
    stp = _shortterm_pops(ts, cfg)
    for d in result["weekly"]:
        if d["date"] in st:
            mx, mn = st[d["date"]]
            if d["tempMax"] is None:
                d["tempMax"] = mx
            if d["tempMin"] is None:
                d["tempMin"] = mn
        if d["pop"] is None and d["date"] in stp:
            d["pop"] = stp[d["date"]]
    return result


def _pops_by_date(ts, cfg):
    """date -> [precip probability, …] from the short-term 6-hourly series."""
    if len(ts) < 2:
        return {}
    block = ts[1]
    area = _match_area(block.get("areas"), cfg["class10_code"])
    return _ints(zip(block.get("timeDefines", []), area.get("pops", [])))


def _shortterm_pops(ts, cfg):
    """date -> max precip probability from the short-term 6-hourly series."""
    return {date: max(v) for date, v in _pops_by_date(ts, cfg).items()}


def _shortterm_temps(ts, cfg):
    """date -> (max, min) from the short-term temp series (today + next ~2 days)."""
    if len(ts) < 3:
        return {}
    block = ts[2]
    area = _match_station(block.get("areas"), cfg["city_name"])
    by_date = _ints(zip(block.get("timeDefines", []), area.get("temps", [])))
    return {date: (max(v), min(v)) for date, v in by_date.items()}


def _today_pop(ts, cfg, today):
    """Today's 降水確率: the highest of today's remaining 6-hour blocks. Once JMA has
    rolled the series over to tomorrow, fall back to the earliest block it still has
    (better than a blank) — the card shows the nearest meaningful figure either way."""
    by_date = _pops_by_date(ts, cfg)
    if today in by_date:
        return max(by_date[today])
    nxt = [by_date[d] for d in sorted(by_date) if d > today]
    return nxt[0][0] if nxt else None


def _today_temps(ts, cfg, today):
    return _shortterm_temps(ts, cfg).get(today, (None, None))


def _weekly(week, cfg, today):
    try:
        w0, w1 = week["timeSeries"][0], week["timeSeries"][1]
        # weekly regroups into coarser class10 areas (東京地方 + 伊豆諸島 as one), so a
        # city's exact sub-region may be absent — both lookups fall back to the first.
        a0 = _match_area(w0["areas"], cfg["class10_code"])
        a1 = _match_station(w1["areas"], cfg["city_name"])
        if not a0 or not a1:                      # empty areas → no weekly, keep today's card
            return []
    except (KeyError, IndexError, TypeError):     # degrade to no-weekly, keep today's card
        return []
    defs = w0.get("timeDefines", [])
    codes = a0.get("weatherCodes", [])
    pops = a0.get("pops", [])
    tmin = a1.get("tempsMin", [])
    tmax = a1.get("tempsMax", [])
    out = []
    for i, dt in enumerate(defs):
        if dt[:10] <= today:      # skip today (shown in the big card) and past
            continue
        code = codes[i] if i < len(codes) else ""
        icon, short = _icon(code)
        out.append({
            "date": dt[:10], **date_labels(dt),
            "code": code, "icon": icon, "text": short,
            "pop": int_at(pops, i), "tempMin": int_at(tmin, i), "tempMax": int_at(tmax, i),
        })
    return out


# ---- Hourly forecast (met.no / yr.no) --------------------------------------
# JMA gives no hourly data, so today's hourly strip comes from met.no (free, no
# key). met.no requires a descriptive User-Agent and returns times in UTC.
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
MET_UA = "hiyori/1.0 (github.com/hiyori-dashboard)"


def _met_icon(sym, hour):
    """met.no symbol_code -> (emoji, short JP label), keyword-based for robustness."""
    s = sym.replace("_day", "").replace("_night", "").replace("_polartwilight", "")
    night = "_night" in sym or hour < 5 or hour >= 19
    if "thunder" in s: return ("⛈️", "雷雨")
    if "snow" in s: return ("❄️", "雪")
    if "sleet" in s: return ("🌨️", "みぞれ")
    if "rain" in s and "showers" in s: return ("🌦️", "にわか雨")
    if "rain" in s: return ("🌧️", "雨")
    if s == "fog": return ("🌫️", "霧")
    if s == "cloudy": return ("☁️", "曇")
    if s == "partlycloudy": return ("🌙" if night else "⛅", "曇時々晴")
    if s == "fair": return ("🌙" if night else "🌤️", "晴")
    if s == "clearsky": return ("🌙" if night else "☀️", "快晴")
    return ("❓", "—")


async def fetch_hourly(cfg, count=12, step=1):
    """`count` forecast points from the current hour, every `step` hours (rolling;
    may cross midnight). e.g. count=12, step=2 → a full day at 2-hour intervals."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": MET_UA}) as client:
        r = await client.get(MET_URL, params={"lat": cfg["lat"], "lon": cfg["lon"]})
        r.raise_for_status()
        data = r.json()
    cur = datetime.datetime.now(JST).replace(minute=0, second=0, microsecond=0)
    out = []
    for e in data["properties"]["timeseries"]:
        t = datetime.datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone(JST)
        if t < cur:
            continue                           # skip past hours
        delta = round((t - cur).total_seconds() / 3600)
        if delta % step != 0:
            continue                           # keep only every `step`-th hour
        detail = e["data"]["instant"]["details"]
        nxt = e["data"].get("next_1_hours") or e["data"].get("next_6_hours") or {}
        sym = nxt.get("summary", {}).get("symbol_code", "")
        icon, text = _met_icon(sym, t.hour)
        temp = detail.get("air_temperature")
        out.append({
            "hour": t.hour,
            "temp": round(temp) if temp is not None else None,
            "icon": icon, "text": text,
            "precip": nxt.get("details", {}).get("precipitation_amount", 0) or 0,
        })
        if len(out) >= count:
            break
    return out
