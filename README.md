# 日和 Hiyori

*常駐型のタブレット情報ダッシュボード — 天気・ニュース・地震速報。*
（「日和」= 好天/好日子）

平板上显示：**天气预报（上）** + **AI/科技新闻・日本新闻（下）**，地震发生时**全屏切换到地震布局并保持 90 秒**。

地震布局：**全屏日本地图**——主岛（北海道〜九州）居中放大，**冲绳做左上角小图**（仿气象厅/专业地震图）；红色 ✕ 标震源（南部地震会标在冲绳小图里），各都道府县按震度上色。日本列岛沿「左下→右上」斜向分布，左上/右下正好是两块空海域，所以**冲绳小图放左上、地震信息浮层放右下**（仿 kotoho7/NHK），列岛对角线完整不被遮挡。浮层含 震源地/震级/深度/最大震度/**各地震度（按震度分级列出，仿地震速报样式）**。启动时会从 P2P 历史接口拉取**最近一次地震**，右上角 🗾 随时可查看；真实地震/EEW 到来时自动切换并保持 90 秒，右上角 **✕** 可随时关闭（关闭后同一次地震不会再自动弹出）。地图为纯 SVG 自绘，只在事件发生时更新，**无持续渲染开销，不依赖任何第三方站点**。

- 后端跑在 Linux 内网机器，聚合数据并推送地震警报。
- 平板（Windows）只需用浏览器全屏打开一个网址，**零框架、无额外性能开销**。
- 数据源全部免费、无需 API key：
  - 天气：気象庁 (JMA) 今日/週間预报 + met.no (yr.no) 当天逐小时 — 点左上角地区名可切换城市(内置 12 个日本主要城市,默认東京)
  - 地震：P2P地震情報 (WebSocket) — 地震情報(551) + 緊急地震速報 EEW(556)
  - 主要ニュース：Google ニュース トップ(按跨媒体报道量排序 → 重大事件优先),置顶 **NERV 严重灾害警报**(特別警報/津波/緊急地震速報/噴火/Jアラート,红色高亮,平时不显示)
  - AI・テック：每台设备可在设置里切换 **中文**(量子位 + Solidot)/ **日本語**(ITmedia AI+)/ **Global**(Hacker News)
  - 汇率：open.er-api.com(底部小卡片,双向显示,保留小数;结果整数部分不足 1 时基数 ×10,如 `100円=4.201元`)
  - 新番：Jikan (MyAnimeList)(底部小卡片,今日 TV 放送时刻表,日文标题+JST 时间,"下一场"优先、2 行)

界面默认日语，点左上角地区名打开设置，可切换 城市 / AI ソース / 语言(日本語・中文・English) / **地震全屏最小震度**(3+/4+/5弱+) / 全屏开关（均每台设备各自记忆）。低于阈值的小地震不抢屏，只进 🗾 列表。

---

## 1. 后端部署（Linux 内网机器）

```bash
cd hiyori/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                    # 或: uvicorn main:app --host 0.0.0.0 --port 8000
```

打开 `http://<linux机器IP>:8000` 就能看到界面。记下这个 IP。

### 开机自启（systemd，可选）

`/etc/systemd/system/hiyori.service`：

```ini
[Unit]
Description=Hiyori Dashboard
After=network-online.target

[Service]
WorkingDirectory=/path/to/hiyori/backend
ExecStart=/path/to/hiyori/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
User=youruser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now hiyori
```

---

## 2. 平板显示（Windows，浏览器 kiosk 全屏）

用 Chrome 或 Edge 全屏打开后端地址即可。新建一个快捷方式，目标填：

```
chrome.exe --kiosk --app=http://<linux机器IP>:8000 --incognito --noerrdialogs --disable-pinch --overscroll-history-navigation=0
```

（Edge 把 `chrome.exe` 换成 `msedge.exe`，其余相同。）

把该快捷方式放进「启动」文件夹（`Win+R` → `shell:startup`）即可开机自动全屏显示。
关闭平板的休眠/锁屏（设置 → 电源和睡眠 → 屏幕/睡眠：从不）。

> 平板端只是一个浏览器页面，所有抓取/解析都在 Linux 后端完成，平板负担极小。

---

## 3. 预览地震界面（不用等真地震）

`config.py` 里 `ENABLE_DEMO = True` 时，浏览器访问：

- `http://<IP>:8000/api/demo/quake` → 模拟一次「地震情報」全屏
- `http://<IP>:8000/api/demo/eew`   → 模拟一次「緊急地震速報」全屏（红色脉冲）

90 秒后自动消失。上线后可把 `ENABLE_DEMO` 设为 `False`。

---

## 4. 自定义

全部改 `backend/config.py`（改完重启后端）：

| 想改的东西 | 改哪里 |
|---|---|
| 切换城市 / AI 源 | 平板上点左上角地区名进设置选(每台设备各自记忆);默认值改 `DEFAULT_CITY` / `DEFAULT_AI_SOURCE` |
| 增删城市 | 改 `config.py` 的 `CITIES` 列表(加一行:id / 名称 / JMA `area_code` `class10_code` / 经纬度,码见 [JMA area.json](https://www.jma.go.jp/bosai/common/const/area.json)) |
| 增删 AI 源分组 | 改 `AI_SOURCES`(加一组:id / 显示名 / mode / feeds);主要ニュース源改 `NEWS_JAPAN` |
| 严重灾害警报关键词 | `ALERT_KEYWORDS`(想收台风/暴风就加 `"台風"` `"暴風"`);`ALERT_FEED` 为 NERV 源 |
| 地震全屏保持时长 | `EARTHQUAKE_HOLD_SECONDS`（默认 90 秒） |
| 全屏最小震度(默认值) | `EARTHQUAKE_MIN_SCALE`（默认 30=震度3；每台设备可在设置里改。10=1 40=4 45=5弱…） |
| 汇率货币对 | `FX_BASE` / `FX_QUOTE`（币种代码,如 CNY/JPY）+ `FX_BASE_LABEL` / `FX_QUOTE_LABEL`（显示名,如 元/円） |
| 是否显示 EEW 训练报 | `EARTHQUAKE_SHOW_TEST` |
| 默认语言 | `DEFAULT_LANGUAGE`（`ja`/`zh`/`en`） |
| 加新语言 | 在 `frontend/i18n.js` 复制一个语言块翻译即可 |

---

## 结构

```
hiyori/
├── backend/
│   ├── main.py          FastAPI：API + WebSocket + 托管前端
│   ├── config.py        所有可调参数
│   ├── weather.py       JMA 天气抓取与解析
│   ├── news.py          RSS 标题聚合
│   ├── fx.py            汇率抓取 (open.er-api)
│   ├── anime.py         今日新番放送 (Jikan/MAL)
│   ├── earthquake.py    P2P地震情報 WebSocket 客户端
│   ├── requirements.txt
│   └── run.sh
├── frontend/
│   ├── index.html
│   ├── style.css        暗色主题，vh/vw 自适应
│   ├── app.js           轮询天气/新闻 + WebSocket 接收地震 + 布局切换
│   ├── map.js           自绘日本地图（SVG，震源+震度上色）
│   ├── i18n.js          多语言文案
│   └── japan.geo.json   47 都道府县边界（已生成，602KB）
└── tools/
    └── build_map.py     从 dataofjapan/land 生成 japan.geo.json（一般无需再跑）
```

> 地图数据来自 [dataofjapan/land](https://github.com/dataofjapan/land)（MIT），已简化为 2 位小数精度（~1km，仪表盘尺度下无差别）。想改精度重新生成：`python3 tools/build_map.py`。

---

## License

本项目采用 **GNU General Public License v3.0**，全文见 [LICENSE](LICENSE)。

```
Copyright (C) 2026 <gosoki>

日和 Hiyori is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License v3.0 as published by the Free
Software Foundation. It is distributed WITHOUT ANY WARRANTY. See the LICENSE
file for details.
```

第三方素材/数据(各自版权方所有,非本项目 GPL 覆盖):
- 地图边界:[dataofjapan/land](https://github.com/dataofjapan/land)(MIT)
- 数据源:気象庁 (JMA) / P2P地震情報 / NHK・Yahoo!・Hacker News・ITmedia (RSS)
