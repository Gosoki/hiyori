"""Tablet dashboard configuration.

Everything you might reasonably want to tweak lives here.
Restart the backend after editing.
"""

# --- Weather (気象庁 / JMA, free JSON API, no key required) -----------------
# Area codes: https://www.jma.go.jp/bosai/common/const/area.json
WEATHER = {
    "city_name": "東京",
    "area_code": "130000",     # 都道府県コード (東京都) — forecast endpoint
    "class10_code": "130010",  # 地方コード (東京地方) — weather text / precip
}

# --- News feeds (headlines only, grouped by category) -----------------------
# Add / remove RSS or Atom feed URLs freely. Broken feeds are skipped silently.
NEWS_FEEDS = {
    "ai": [
        "https://hnrss.org/newest?q=AI+OR+LLM+OR+OpenAI+OR+Anthropic&count=25",
        "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
    ],
    "japan": [
        "https://www.nhk.or.jp/rss/news/cat0.xml",              # NHK 主要ニュース
        "https://news.yahoo.co.jp/rss/topics/top-picks.xml",    # Yahoo!ニュース 主要
    ],
    # 中国のニュースは将来用に確保。使うときはコメントを外す:
    # "china": [
    #     "https://www.chinanews.com.cn/rss/scroll-news.xml",
    # ],
}
NEWS_MAX_PER_CATEGORY = 15

# --- Earthquake (P2P地震情報 v2 WebSocket, free, no key) ---------------------
P2P_WS_URL = "wss://api.p2pquake.net/v2/ws"
EARTHQUAKE_HOLD_SECONDS = 300   # keep the earthquake screen for 5 minutes
EARTHQUAKE_SHOW_TEST = False    # show EEW drill (訓練) messages as full-screen?
EARTHQUAKE_RECENT_COUNT = 5     # how many recent quakes the 🗾 button lets you browse

# --- UI ---------------------------------------------------------------------
DEFAULT_LANGUAGE = "ja"   # ja / zh / en. Each device can override in Settings.

# --- Demo -------------------------------------------------------------------
# When True, /api/demo/quake and /api/demo/eew inject a sample event so you can
# preview the earthquake screen. Turn off in production if you like.
ENABLE_DEMO = True

# --- Refresh intervals (seconds) --------------------------------------------
WEATHER_REFRESH = 600
NEWS_REFRESH = 300
