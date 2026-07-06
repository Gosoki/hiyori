"""Tablet dashboard configuration.

Everything you might reasonably want to tweak lives here.
Restart the backend after editing.
"""

# --- Weather cities (気象庁 JMA area codes + lat/lon for the hourly forecast) -
# JMA weather uses area_code (forecast endpoint) + class10_code (sub-region);
# codes from https://www.jma.go.jp/bosai/common/const/area.json . lat/lon feed
# the met.no hourly forecast. Each device picks a city in ⚙ Settings.
# Add a city by appending a row (id must be unique).
CITIES = [
    {"id": "tokyo",     "city_name": "東京",   "area_code": "130000", "class10_code": "130010", "lat": 35.69, "lon": 139.69},
    {"id": "osaka",     "city_name": "大阪",   "area_code": "270000", "class10_code": "270000", "lat": 34.69, "lon": 135.50},
    {"id": "nagoya",    "city_name": "名古屋", "area_code": "230000", "class10_code": "230010", "lat": 35.18, "lon": 136.91},
    {"id": "yokohama",  "city_name": "横浜",   "area_code": "140000", "class10_code": "140010", "lat": 35.44, "lon": 139.64},
    {"id": "kyoto",     "city_name": "京都",   "area_code": "260000", "class10_code": "260010", "lat": 35.01, "lon": 135.77},
    {"id": "kobe",      "city_name": "神戸",   "area_code": "280000", "class10_code": "280010", "lat": 34.69, "lon": 135.20},
    {"id": "sapporo",   "city_name": "札幌",   "area_code": "016000", "class10_code": "016010", "lat": 43.06, "lon": 141.35},
    {"id": "sendai",    "city_name": "仙台",   "area_code": "040000", "class10_code": "040010", "lat": 38.27, "lon": 140.87},
    {"id": "hiroshima", "city_name": "広島",   "area_code": "340000", "class10_code": "340010", "lat": 34.39, "lon": 132.46},
    {"id": "fukuoka",   "city_name": "福岡",   "area_code": "400000", "class10_code": "400010", "lat": 33.59, "lon": 130.40},
    {"id": "kanazawa",  "city_name": "金沢",   "area_code": "170000", "class10_code": "170010", "lat": 36.56, "lon": 136.66},
    {"id": "naha",      "city_name": "那覇",   "area_code": "471000", "class10_code": "471010", "lat": 26.21, "lon": 127.68},
]
DEFAULT_CITY = "tokyo"   # id from CITIES; each device can override in Settings

# --- News feeds (headlines only) --------------------------------------------
# A feed group is {"mode", "urls"}. Broken feeds are skipped silently.
#   "ranked" – keep the feed's own order. Google News Top ranks stories by how many
#              outlets cover them, so genuinely big events float to the top → 大事件流.
#   "recent" – merge all feeds, dedupe, sort newest-first (good for a topic stream).

# 主要ニュース column (fixed): Google News Top, importance-ranked.
NEWS_JAPAN = {"mode": "ranked", "urls": [
    "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
]}

# AI/テック column: each device picks one source group in Settings (⚙→AI ソース).
# Add a group by appending a row (unique id + display name + feeds).
AI_SOURCES = [
    {"id": "cn", "name": "中文", "lang": "zh", "mode": "recent", "urls": [
        "https://www.qbitai.com/feed",           # 量子位 (AI)
        "https://www.solidot.org/index.rss",     # Solidot 奇客 (极客/AI)
    ]},
    {"id": "jp", "name": "日本語", "lang": "ja", "mode": "recent", "urls": [
        "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",   # ITmedia AI+
    ]},
    {"id": "global", "name": "Global", "lang": "en", "mode": "recent", "urls": [
        "https://hnrss.org/newest?q=AI+OR+LLM+OR+OpenAI+OR+Anthropic&count=25",   # Hacker News
    ]},
]
DEFAULT_AI_SOURCE = "cn"   # id from AI_SOURCES; each device can override in Settings

NEWS_MAX_PER_CATEGORY = 12

# --- Severe real-time alerts (特務機関NERV / @UN_NERV; aggregates JMA + Jアラート) -
# NERV also posts every prefecture's routine advisory, so we keep only titles that
# signal a genuinely severe event. Usually empty — it lights up only when it matters,
# and those items are pinned to the top of the main-news column, highlighted.
ALERT_FEED = "https://unnerv.jp/@UN_NERV.rss"
ALERT_KEYWORDS = ["特別警報", "津波", "緊急地震速報", "噴火", "Ｊアラート", "Jアラート", "記録的短時間大雨"]
ALERT_MAX = 3

# --- Earthquake (P2P地震情報 v2 WebSocket, free, no key) ---------------------
P2P_WS_URL = "wss://api.p2pquake.net/v2/ws"
EARTHQUAKE_HOLD_SECONDS = 90    # keep the earthquake screen for 90 seconds
EARTHQUAKE_SHOW_TEST = False    # show EEW drill (訓練) messages as full-screen?
EARTHQUAKE_RECENT_COUNT = 5     # how many recent quakes the 🗾 button lets you browse
# Default full-screen 震度 threshold for a new device (each device can change it in
# ⚙ Settings; smaller quakes stay in the 🗾 list without a full-screen alert).
# Codes: 10=1 20=2 30=3 40=4 45=5弱 50=5強 55=6弱 60=6強 70=7 . Default 30 (震度3+).
EARTHQUAKE_MIN_SCALE = 30

# --- UI ---------------------------------------------------------------------
DEFAULT_LANGUAGE = "ja"   # ja / zh / en. Each device can override in Settings.

# --- Demo -------------------------------------------------------------------
# When True, /api/demo/quake and /api/demo/eew inject a sample event so you can
# preview the earthquake screen. Turn off in production if you like.
ENABLE_DEMO = True

# --- Hourly forecast (met.no / yr.no, free, no key; needs a User-Agent) -----
HOURLY_REFRESH = 1800   # hourly forecast refreshed every 30 min
HOURLY_COUNT = 12       # how many points the strip shows (rolling, from the current hour)
HOURLY_STEP = 2         # hours between points (2 → 12 points cover a full day)

# --- Weekly forecast --------------------------------------------------------
WEEKLY_COUNT = 6    # days shown in the weekly strip (fixed; JMA gives 6–7 → capped)

# --- Exchange rate (open.er-api.com, free, no key; ~daily rates) -------------
# The bottom widget shows both directions: 1 FX_BASE = x FX_QUOTE and 1 FX_QUOTE
# = x FX_BASE. When x would round below 1, the base amount is padded (×10) so the
# shown number is always a whole number (e.g. 100 円 = 4 元).
FX_BASE = "CNY"
FX_QUOTE = "JPY"
FX_BASE_LABEL = "元"    # short display label per currency
FX_QUOTE_LABEL = "円"
FX_REFRESH = 3600

# --- Anime (Jikan / MyAnimeList, free, no key) ------------------------------
# The 新番 slot shows today's TV broadcast schedule (JP titles + JST times).
ANIME_REFRESH = 3600    # refresh hourly (also catches the midnight day-rollover)
ANIME_COUNT = 24

# --- Refresh intervals (seconds) --------------------------------------------
WEATHER_REFRESH = 600
NEWS_REFRESH = 300
