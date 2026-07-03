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


def _num(seq, i):
    try:
        v = seq[i]
        if v in ("", None):
            return None
        return int(float(v))
    except (IndexError, ValueError, TypeError):
        return None


def _dparts(iso):
    d = datetime.date.fromisoformat(iso[:10])
    return {"md": f"{d.month}/{d.day}", "weekday": WEEKDAY_JA[d.weekday()]}


def _match_area(areas, code):
    for a in areas:
        if a.get("area", {}).get("code") == code:
            return a
    return areas[0]


async def fetch_weather(cfg, weekly_count=6):
    url = FORECAST_URL.format(area=cfg["area_code"])
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "hiyori/1.0"}) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
    result = _parse(data, cfg)
    result["weekly"] = result["weekly"][:weekly_count]   # fixed length (JMA gives 6–7)
    return result


def _parse(data, cfg):
    today = datetime.datetime.now(JST).date().isoformat()
    short = data[0]
    ts = short["timeSeries"]

    warea = _match_area(ts[0]["areas"], cfg["class10_code"])
    code0 = (warea.get("weatherCodes") or [""])[0]
    icon0, short0 = _icon(code0)
    # JMA separates the text with full-width spaces at odd points (e.g. around
    # "まで"), which reads awkwardly. Japanese has no spaces — drop them entirely.
    text0 = (warea.get("weathers") or [""])[0].replace("　", "").replace(" ", "").strip() or short0
    tmax, tmin = _today_temps(ts, today)

    result = {
        "city": cfg["city_name"],
        "updated": datetime.datetime.now(JST).isoformat(timespec="minutes"),
        "today": {
            "code": code0, "icon": icon0, "text": text0,
            "pop": _first_pop(ts[1] if len(ts) > 1 else {}),
            "tempMax": tmax, "tempMin": tmin,
        },
        "weekly": _weekly(data[1], today) if len(data) > 1 else [],
    }
    # JMA's weekly series often lacks tomorrow's temps — fill from the short-term forecast.
    st = _shortterm_temps(ts)
    stp = _shortterm_pops(ts)
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


def _shortterm_pops(ts):
    """date -> max precip probability from the short-term 6-hourly series."""
    out = {}
    try:
        block = ts[1]
        by_date = {}
        for d, p in zip(block.get("timeDefines", []), block["areas"][0].get("pops", [])):
            if p in ("", None):
                continue
            try:
                by_date.setdefault(d[:10], []).append(int(p))
            except ValueError:
                pass
        out = {date: max(v) for date, v in by_date.items()}
    except (KeyError, IndexError):
        pass
    return out


def _shortterm_temps(ts):
    """date -> (max, min) from the short-term temp series (today + next ~2 days)."""
    out = {}
    try:
        block = ts[2]
        by_date = {}
        for d, t in zip(block.get("timeDefines", []), block["areas"][0].get("temps", [])):
            if t in ("", None):
                continue
            try:
                by_date.setdefault(d[:10], []).append(int(float(t)))
            except ValueError:
                pass
        out = {date: (max(v), min(v)) for date, v in by_date.items()}
    except (KeyError, IndexError):
        pass
    return out


def _first_pop(block):
    try:
        for p in block["areas"][0].get("pops", []):
            if p not in ("", None):
                return int(p)
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return None


def _today_temps(ts, today):
    try:
        block = ts[2]
        area = block["areas"][0]
        vals = []
        for d, t in zip(block.get("timeDefines", []), area.get("temps", [])):
            if d[:10] == today and t not in ("", None):
                try:
                    vals.append(int(float(t)))
                except ValueError:
                    pass
        if vals:
            return max(vals), min(vals)
    except (KeyError, IndexError):
        pass
    return None, None


def _weekly(week, today):
    try:
        w0, w1 = week["timeSeries"][0], week["timeSeries"][1]
    except (KeyError, IndexError):
        return []
    defs = w0.get("timeDefines", [])
    a0, a1 = w0["areas"][0], w1["areas"][0]
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
            "date": dt[:10], **_dparts(dt),
            "code": code, "icon": icon, "text": short,
            "pop": _num(pops, i), "tempMin": _num(tmin, i), "tempMax": _num(tmax, i),
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
