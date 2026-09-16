# 日和 Hiyori

*常駐型のタブレット情報ダッシュボード — 天気・ニュース・地震速報。*
（「日和」= 好天/好日子）

平板上显示：**天气预报（上）** + **AI/科技新闻・日本新闻（下）**，地震发生时**全屏切换到地震布局并保持 90 秒**。

地震布局：**全屏日本地图**——主岛（北海道〜九州）居中放大，**冲绳做左下角小图**（正方形，仿气象厅/专业地震图）；红色 ✕ 标震源（南部地震会标在冲绳小图里），各都道府县按震度上色。日本列岛沿「左下→右上」斜向分布，左上/右下正好是两块空海域，所以**最近地震列表放左上、冲绳小图放左下、地震信息浮层放右下**（仿 kotoho7/NHK），列岛对角线完整不被遮挡。浮层含 震源地/震级/深度/最大震度/**各地震度（按震度分级列出，仿地震速报样式）**。启动时会从 P2P 历史接口拉取**最近一次地震**，右上角 🗾 随时可查看；真实地震/EEW 到来时自动切换并保持 90 秒，右上角 **✕** 可随时关闭（关闭后同一次地震不会再自动弹出）。地图为纯 SVG 自绘，只在事件发生时更新，**无持续渲染开销，不依赖任何第三方站点**。

- 后端跑在 Linux 内网机器，聚合数据并推送地震警报。
- 平板（Windows）只需用浏览器全屏打开一个网址，**零框架、无额外性能开销**。
- 数据源全部免费、无需 API key：
  - 天气：気象庁 (JMA) 今日/週間预报 + met.no (yr.no) 当天逐小时 — 点左上角地区名可切换城市(内置 12 个日本主要城市,默认東京)
  - 地震：P2P地震情報 (WebSocket) — 地震情報(551) + 緊急地震速報 EEW(556);
    **P2P 断线超过 5 分钟自动切到気象庁 XML 备用源**(仅地震情報,无 EEW),右上角 🗾 会亮琥珀色小点提示
  - 主要ニュース：Google ニュース トップ(按跨媒体报道量排序 → 重大事件优先),置顶 **NERV 严重灾害警报**(特別警報/津波/緊急地震速報/噴火/Jアラート,红色高亮,平时不显示;每 60 秒轮询、变化即推送到平板;其中 津波警報/特別警報/Jアラート 还会在**页面顶部拉出一条红色横幅**,3 小时内有效)
  - AI・テック：每台设备可在设置里切换 **中文**(量子位 + Solidot)/ **日本語**(ITmedia AI+)/ **Global**(Hacker News)
  - 汇率：open.er-api.com(底部小卡片,双向显示,保留小数;结果整数部分不足 1 时基数 ×10,如 `100円=4.201元`)
  - 新番：Jikan (MyAnimeList)(底部小卡片,广播日 00:00→次日 06:00 的 TV 放送,时间序,3×3 网格,傍晚 18 点后剔除已播)
  - 节日：holidays-jp(底部独立"节日"栏,"距 <假日名·红> N 天",天数客户端实时算)

界面默认日语，点左上角地区名打开设置，可切换 城市 / AI ソース / 语言(日本語・中文・English) / **地震全屏最小震度**(3+/4+/5弱+) / **夜间减光**(23〜6 时把面板调暗,地震占屏不受影响) / 全屏开关（均每台设备各自记忆）。低于阈值的小地震不抢屏，只进 🗾 列表——**但带津波警報/大津波警報的地震不看阈值,一律占屏**。设置面板 60 秒无操作自动关闭,点空白处也能关,且永远压不住地震占屏。

---

## 1. 后端部署（Linux 内网机器）

**一键部署**（Debian/Ubuntu，root；装 uv+Python+依赖、注册 systemd 服务并启动）：

```bash
bash deploy.sh    # 幂等，升级时 git pull 后重跑即可；会询问端口(回车=12345)，PORT=xxxx 预设可跳过询问
```

或手动：

```bash
cd hiyori/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                    # 默认 12345；换端口：PORT=8000 ./run.sh
```

打开 `http://<linux机器IP>:12345` 就能看到界面。记下这个 IP。

### 开机自启（systemd，可选）

`/etc/systemd/system/hiyori.service`：

```ini
[Unit]
Description=Hiyori Dashboard
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/path/to/hiyori/backend
ExecStart=/path/to/hiyori/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 12345
Restart=always
RestartSec=5
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
chrome.exe --kiosk --app=http://<linux机器IP>:12345 --user-data-dir=C:\hiyori-kiosk --noerrdialogs --disable-pinch --overscroll-history-navigation=0
```

（Edge 把 `chrome.exe` 换成 `msedge.exe`，其余相同。）

> **别加 `--incognito`。** 每台设备的设置（城市 / AI 源 / 语言 / **地震全屏最小震度** / 夜间减光）
> 都存在浏览器的 localStorage 里；无痕模式在窗口关闭（也就是每次平板重启）时全部丢弃，
> 你设好的 5弱+ 第二天就悄悄变回服务器默认值。`--user-data-dir` 给它一个独立、持久的配置目录。

把该快捷方式放进「启动」文件夹（`Win+R` → `shell:startup`）即可开机自动全屏显示。
关闭平板的休眠/锁屏（设置 → 电源和睡眠 → 屏幕/睡眠：从不）。

> 平板端只是一个浏览器页面，所有抓取/解析都在 Linux 后端完成，平板负担极小。

---

## 3. 预览地震界面（不用等真地震）

**默认关闭**。想预览时把 `config.py` 的 `ENABLE_DEMO` 改成 `True` 并重启后端，浏览器访问：

- `http://<IP>:12345/api/demo/quake` → 模拟一次「地震情報」全屏
- `http://<IP>:12345/api/demo/eew`   → 模拟一次「緊急地震速報」全屏（红色脉冲）

90 秒后自动消失。**看完记得改回 `False`。**

> ⚠️ 为什么默认关：这两个接口**无鉴权**,而且是 `GET`——任何能访问该端口的人,
> 以及任何链接预取 / 浏览器推测导航 / 爬虫碰到这个 URL,都能让所有平板弹出一次假地震全屏。
> 假警报会训练人忽略真警报,而地震警报是这个仪表盘里唯一真正要紧的功能。
> 开着的时候后端每次启动都会在 `journalctl -u hiyori` 里打一条 WARNING 提醒。

---

## 4. 自定义

全部改 `backend/config.py`（改完重启后端）：

| 想改的东西 | 改哪里 |
|---|---|
| 切换城市 / AI 源 | 平板上点左上角地区名进设置选(每台设备各自记忆);默认值改 `DEFAULT_CITY` / `DEFAULT_AI_SOURCE` |
| 增删城市 | 改 `config.py` 的 `CITIES` 列表(加一行:id / 名称 / JMA `area_code` `class10_code` / 经纬度,码见 [JMA area.json](https://www.jma.go.jp/bosai/common/const/area.json)) |
| 增删 AI 源分组 | 改 `AI_SOURCES`(加一组:id / 显示名 / mode / feeds);主要ニュース源改 `NEWS_JAPAN` |
| 严重灾害警报关键词 | `ALERT_KEYWORDS`(想收台风/暴风就加 `"台風"` `"暴風"`);`ALERT_FEED` 为 NERV 源;`ALERT_REFRESH` 为轮询间隔(默认 60 秒,条件请求,变化即推送) |
| 地震全屏保持时长 | `EARTHQUAKE_HOLD_SECONDS`（默认 90 秒） |
| 全屏最小震度(默认值) | `EARTHQUAKE_MIN_SCALE`（默认 30=震度3；每台设备可在设置里改。10=1 40=4 45=5弱…） |
| 汇率货币对 | `FX_BASE` / `FX_QUOTE`（币种代码,如 CNY/JPY）+ `FX_BASE_LABEL` / `FX_QUOTE_LABEL`（显示名,如 元/円） |
| 是否显示 EEW 训练报 | `EARTHQUAKE_SHOW_TEST` |
| 默认语言 | `DEFAULT_LANGUAGE`（`ja`/`zh`/`en`） |
| 加新语言 | 在 `frontend/i18n.js` 复制一个语言块翻译即可 |

---

## 5. API 接口与文档

后端是 FastAPI,自带**交互式接口文档**(数据源全免费无 key,接口也无鉴权):

- **Swagger UI** → `http://<IP>:12345/docs` （可直接点 “Try it out” 调用）
- **ReDoc** → `http://<IP>:12345/redoc`
- **OpenAPI JSON** → `http://<IP>:12345/openapi.json`

> 这几个页面供**运维/开发**在有网的机器上查看;平板前端本身零外部依赖(严格 CSP),不受影响。

> 接口无鉴权,只应暴露在内网。后端对所有响应附带 `X-Content-Type-Options: nosniff`、
> `Referrer-Policy: no-referrer`、`Content-Security-Policy: frame-ancestors 'none'`
> (`frame-ancestors` 在 `<meta>` 里按规范无效,只能走响应头,故与页面内的 CSP 分开下发)。

### HTTP 接口一览

| 方法 · 路径 | 说明 |
|---|---|
| `GET /api/health` | **各上游存活状态**(每路数据源的成功/失败/陈旧秒数、`stale` 标记 + 地震长连接状态)。见下 |
| `GET /api/config` | 前端启动默认值:语言 / 城市 / AI 源 / 地震全屏阈值 / 🗾 列表条数 |
| `GET /api/cities` | 可选城市列表 `[{id,name}]` |
| `GET /api/ai-sources` | 可选 AI 源分组 `[{id,name,lang}]` |
| `GET /api/weather?city=` | 今日+周间预报(JMA);省略 `city` 用默认城市 |
| `GET /api/weather/hourly?city=` | 逐时预报条(met.no) |
| `GET /api/news?ai=` | `{ai, japan}`:AI 栏(按 `ai=` 选源)+ 主要新闻(Google News,NERV 警报置顶 `alert:true`) |
| `GET /api/fx` | 汇率 `{base,quote,rate,updated,baseLabel,quoteLabel}` |
| `GET /api/anime` | 今日新番 `[{time,title}]`(次日凌晨用 24:00–29:59) |
| `GET /api/holiday` | 未来日本节日 `[{date,name}]` |
| `GET /api/earthquake/current` | 当前占屏中的地震事件(否则 `{}`) |
| `GET /api/earthquake/recent` | 最近 N 次地震(新→旧,供 🗾 浏览) |
| `GET /api/earthquake/latest` | 最新一次地震 |
| `GET /api/demo/quake` · `GET /api/demo/eew` | 注入样例事件预览地震屏(仅 `ENABLE_DEMO=True` 时挂载) |

所有数据接口在上游抓取失败时保留上一份好数据(`last-good`),永不返回空白栏。

### 上游存活状态 `/api/health`

正因为**所有失败路径都降级成"保留上一份好数据"**,上游挂掉时屏幕上什么都不会变——
"一切正常"和"这栏已经三小时没更新了"从外面看一模一样。这个接口是唯一能区分两者的地方:

```jsonc
{
  "status": "ok",              // 任一数据源失败或地震长连接断开 → "degraded"
  "degraded": [],              // 具体是哪几路
  "uptime": 3600, "clients": 2,
  "quake": { "connected": true, "offlineFor": 0, "reconnects": 0,
             "fallbackActive": false },   // fallbackActive=已切到気象庁备用源
  "feeds": { "weather": { "ok": true, "lastOkAge": 42, "consecutiveFails": 0,
                          "lastError": "", "staleAfter": 1800, "stale": false }, /* … */ }
}
```

`stale` = 这路数据上一次成功距今已超过 `staleAfter` 秒(三个刷新周期)。平板据此把对应面板
**调暗并打上「更新停止」角标**——数据还是最后一份好的(设计如此),但看的人能分清
「今天没新闻」和「这一栏三小时前就停了」。从未成功过的数据源不算 stale,面板上是它自己的占位文案。

数据源失败/恢复时后端会往 `journalctl -u hiyori` 打一条 WARNING(**只在状态变化时打**,
长时间故障不会刷屏)。平板上则表现为右上角 🗾 出现小圆点:琥珀=正在用備用地震源、红=完全收不到地震信息。

### WebSocket 实时地震推送

```
ws://<IP>:12345/ws
```

连接后:若当前有仍在保持期内的地震,立即补推一次;之后每来一次地震/EEW 推送一条:

```json
{ "type": "earthquake", "event": { "kind": "eew|quake", "maxScale": 50,
  "hypocenter": {"name":"…","depth":…,"magnitude":…}, "regions": […],
  "expiresAt": 1780000000, "cancelled": false, "…": "…" } }
```

`kind` 为 `eew`(緊急地震速報,红色脉冲)或 `quake`(地震情報)。前端据此切换全屏地震布局;`maxScale` 低于本机阈值则只进 🗾 列表(`maxScale=-1` 未知强度时:EEW 按“宁可误报”仍全屏,551 则遵守阈值)。

`bulletin` = 同一次地震的**第几报**——551 用报文类型(`ScalePrompt`→`DetailScale`…),556 用序号(第1報/第2報)。写法不同但作用一样,只用于相等比较("这条我是不是已经显示过了")。
`source` = `p2p` 或 `jma`(备用源)。完整字段见 `backend/earthquake.py` 的 `normalize_quake` / `normalize_eew`。
连接时补推的事件 `holdFor` 是**剩余**秒数(与 `/api/earthquake/current` 一致),不是整段保持时长。

同一条连接上还有两种消息:

```json
{ "type": "alerts", "items": [ { "title": "【津波警報】…", "source": "NERV", "alert": true } ] }
{ "type": "ping" }
```

- `alerts`:NERV 严重灾害警报(津波/特別警報/Jアラート…)集合**发生变化**时推送。
  后端每 `ALERT_REFRESH`(60 秒)条件轮询一次 NERV,不再跟着 5 分钟的新闻循环走——
  以前一条海啸警报要等新闻循环 + 平板 5 分钟轮询,最坏约 10 分钟才上墙,现在约 1 分钟。
  平板收到后立刻重取 `/api/news`(警报置顶在主要ニュース栏,`/api/news` 在请求时合成,不等新闻刷新)。
- `ping`:每 25 秒一次的心跳。浏览器自己发现不了半开的 WebSocket(Wi-Fi 闪断后 readyState 仍是 OPEN、
  却什么都收不到,包括下一条緊急地震速報);平板连续 3 次没收到心跳就主动重连,服务端连接时会补推仍在保持期内的事件。

---

## 结构

```
hiyori/
├── backend/
│   ├── main.py             FastAPI：API + WebSocket + 托管前端 + 缓存/健康
│   ├── config.py           所有可调参数
│   ├── weather.py          JMA 天气抓取与解析
│   ├── news.py             RSS 标题聚合
│   ├── fx.py               汇率抓取 (open.er-api)
│   ├── anime.py            今日新番放送 (Jikan/MAL)
│   ├── holiday.py          日本祝日倒计时 (holidays-jp)
│   ├── earthquake.py       P2P地震情報 WebSocket 客户端
│   ├── earthquake_jma.py   気象庁 XML 备用地震源（P2P 挂掉时接管）
│   ├── requirements.txt
│   └── run.sh
├── frontend/               零依赖零构建；classic script，加载顺序即契约
│   ├── index.html
│   ├── style.css           暗色主题，vh/vw 自适应
│   ├── core.js             共享状态 / t() / JST 日期助手 / 时钟
│   ├── weather.js          今日卡片 + 周间 + 逐时
│   ├── news.js             两栏新闻 + 裁切适配
│   ├── widgets.js          汇率 / 祝日 / 新番
│   ├── quake.js            地震占屏 + WebSocket + 🗾 列表 + 数据源健康角标
│   ├── app.js             设置面板 + 事件绑定 + init（**必须最后加载**）
│   ├── map.js              自绘日本地图（SVG，震源+震度上色）
│   ├── i18n.js             多语言文案
│   └── japan.geo.json      47 都道府县边界（已生成，~200KB，传输时 gzip 到 ~40KB）
├── tests/                  见 tests/README.md（离线单测 + `-m live` 契约测试）
└── tools/
    └── build_map.py        从 dataofjapan/land 生成 japan.geo.json（一般无需再跑）
```

> 前端拆成多个 `<script>` 但**没有引入任何构建步骤或模块加载器**——它们共享一个全局作用域，
> 所以 `index.html` 里的**加载顺序就是唯一的契约**：`i18n.js`、`core.js` 最先（文案与共享状态），
> `app.js` 最后（唯一有顶层执行代码的文件）。改动时别打乱顺序。

> 地图数据来自 [dataofjapan/land](https://github.com/dataofjapan/land)（MIT），已做 Douglas–Peucker 简化（容差 0.006°≈半个像素，比都道府县描边还细）并剔除亚像素小岛，
> 再取 2 位小数：4 万点 → 1.3 万点，地震屏首次栅格化的工作量降到三分之一，肉眼无差别。想改精度重新生成：`python3 tools/build_map.py`（参数在文件顶部）。

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
