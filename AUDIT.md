# 日和 Hiyori — 全项目审计报告

审计日期：2026-08-05 ｜ 基线 commit：`0713021`

审计范围：`backend/` 全部 7 个模块、`frontend/` 全部 4 个 JS/CSS/HTML、`deploy.sh`、
`backend/run.sh`、`tools/build_map.py`、`README.md`。

**验证方式（不是纸面 review，全部实跑过）：**

| 手段 | 内容 |
|---|---|
| 上游实测 | 12 个城市全部拉取 JMA 真实响应，确认 `timeSeries` 各段的编码体系 |
| 全链路冒烟 | 9 个 fetcher 全部对真实上游跑通（JMA / met.no / Google News / NERV / 量子位 / Solidot / ITmedia / HN / open.er-api / Jikan / holidays-jp / P2P） |
| 畸形输入 | 对 `normalize_quake` / `normalize_eew` / `_parse` 等纯函数注入 null / 错类型 / 空容器 |
| 模糊测试 | 拉 25 条 P2P 真实电文，随机变异出 **4000 条**灌进 `_handle` |
| 服务端行为 | 真起 uvicorn：并发穿透、304 协商、路径穿越、WebSocket 补推/二进制帧、demo 接口 |
| 前端 | jsdom 载入真实 `index.html`+`app.js`，对活后端跑 **30 项**断言（渲染 / XSS / 地震占屏逻辑 / i18n） |
| 地图 | 用真实 602 KB `japan.geo.json` 跑 **16 项**（投影、北海道合并、冲绳小图路由、越界钳制） |

---

## 一、已修复（按严重度）

### 🔴 严重

**1. `broadcast()` 串行扇出 → EEW 被卡住的平板拖慢数秒**
`backend/main.py:45`

原实现逐个 `await asyncio.wait_for(send, 5)`。一台半开/卡死的平板会让排在它后面的
每一块屏都多等一个 5 秒超时。**实测：4 台卡死 + 1 台健康时，健康平板 10.0 秒后才收到
EEW，整轮扇出耗时 20.0 秒。** 紧急地震速报的全部价值就是那几秒。

改为 `asyncio.gather` 并发扇出，整轮上限恒为一个 `SEND_TIMEOUT`。
**修复后实测：健康平板 0.0 秒收到，整轮 5.0 秒。**

**2. 一条畸形 P2P 电文会把地震长连接打断 5 秒**
`backend/earthquake.py:48`（`_obj`/`_dicts`）、`:225`（`_handle` 兜底）

`normalize_*` 里 `msg.get("issue", {}).get(...)` 这种写法，在上游发**显式 JSON null**
（`"issue": null`）时 `.get` 会抛 `AttributeError`；`points: [None]` / `areas: [None]`
同理。而 `_handle` 当时没有兜底，异常会一路冒到 `run()` 的 `except Exception` ——
后果是**断开重连、静默丢 5 秒**，偏偏地震电文是成串来的。

实测原代码在 11 组畸形输入里挂 6 组。现在：
- 新增 `_obj()` / `_dicts()`，把「显式 null / 类型不对」统一收敛掉；
- `_handle` 对 normalize 段加兜底 —— 单条坏电文只丢自己，不动连接；
- `fetch_recent_quakes` 同样逐条兜底，一条坏记录不再毁掉整份历史。

**4000 条变异电文 + 9 种垃圾帧，零崩溃。**

### 🟠 中

**3. JMA 收走当天最高/最低气温后，主卡片显示的是错的**
`backend/weather.py:113`、`frontend/app.js:160`

JMA 约 17:00 JST 就把当天气温从短期预报里撤掉了（**已实测**：18:47 时
`timeSeries[2]` 只剩明天的两个点）。此时前端回退到 met.no 逐时条取 min/max——
但**逐时条是从当前小时起滚动 24 小时、会跨过午夜**，于是拿到的是*明天下午*的峰值。
**实测：19:00 时今日卡片显示 35°/25°，而当天真实最高约 34°、此刻只有 28°。**

两层修复：
- 后端按「城市 + JST 日期」记住最后一次见到的当天气温（`_today_temp_memo`），
  常驻运行时全天都能给出真值，跨日自动作废；
- 前端回退**只取到午夜为止**的逐时点（`hourlyTempsUntilMidnight()`，靠"小时数不再
  递增"识别跨日），冷启动兜底时至少不会再把明天的天气写成今天。
  **修复后实测：28°/26°。**

**4. `max()` 空序列会炸掉整份天气解析**
`backend/weather.py:64`

`by_date.setdefault(d[:10], []).append(int(p))` —— `setdefault` 先建了空列表，
`int()` 才抛异常，于是留下一个空列表；后面 `max(v)` 抛 `ValueError`，
而外层只 catch 了 `(KeyError, IndexError)`。只要 JMA 回一个非数字气温/降水值，
整次天气拉取就失败（靠 `_ensure` 兜住旧数据，所以表现是"天气默默不更新了"）。
抽出 `_ints()`：**先转换、再插入**。

**5. 分区匹配用的是 `areas[0]` 而不是配置的 `class10_code`**
`backend/weather.py:89`

实测确认 JMA 两套编码：`timeSeries[0]/[1]` 用 class10 分区码（東京地方 130010、
愛知西部 230010），`timeSeries[2]` 和周间气温用 **AMeDAS 观测点码**（東京 44132、
名古屋 51106）。原代码降水概率、气温、周间预报全部硬取 `areas[0]`。

现在 12 个城市恰好 class10 都排在第 0 位，所以**当前不会出错**——但 README 明确
教用户往 `CITIES` 里加城市，加个伊豆諸島南部(130030)或愛知東部(230020)就会静默
拿到隔壁分区的数据。改为按码匹配（`_match_area`）、气温按观测点名匹配
（`_match_station`），都保留 `areas[0]` 兜底。

**6. 取消报会误清掉不相干的占屏事件**
`backend/earthquake.py:264`

原 `self.current = None if event.get("cancelled") else event` —— 任何一条取消报
都会把 `current` 清空，哪怕它取消的是另一次地震。前端本来就按 `eventBase` 做了
区分，但 `/api/earthquake/current`（WebSocket 断线时的轮询兜底）没有。
现在服务端也按 `_event_base` 比对，与前端语义对齐。**已用配对/非配对取消报实测。**

**7. 一个二进制帧就能踢掉一块屏的 WebSocket**
`backend/main.py:458`

`ws.receive_text()` 遇到二进制帧时取 `message["text"]` 抛 `KeyError`，被通用
`except` 吞掉后连接关闭。**实测原代码收到二进制帧后 1006 断开**；改用
`ws.receive()` 并只在 `websocket.disconnect` 时退出。**实测修复后连接保持 OPEN。**

**8. 🗾 列表的按需刷新被丢弃**
`frontend/app.js`（`loadRecentQuakes`）

`toggleQuakeLayout()` 专门 fire 了一次 `loadRecentQuakes()` 来刷新列表，但那是个
没 await 的 promise，落地后只更新变量、不重绘——用户看到的还是旧列表。现在在
🗾 打开状态下会重绘并保持当前选中项；空列表状态下拉到数据也会正确切换到列表。

**9. `feedparser` 在事件循环上同步阻塞 66 ms**
`backend/news.py:49`

**实测**：Google News 源 84 KB / 66 ms、HN 29 ms、NERV 27 ms。这段时间事件循环
读不了地震 WebSocket、也发不出 EEW。改为 `asyncio.to_thread`。

### 🟡 低 / 健壮性

| # | 位置 | 问题 |
|---|---|---|
| 10 | `frontend/app.js` `renderHourly` | `h.precip` 是唯一进 `innerHTML` 的未强转上游数值，加 `Number()` |
| 11 | `frontend/app.js` `loadNews` | `lastNewsText` 在 render **之前**赋值，render 一旦抛异常，之后每次轮询都会"内容未变"短路，该栏再也不刷新。改为 render 成功后再赋值 |
| 12 | `frontend/app.js` | 🗾 列表条数硬编码 `slice(0,5)`，与 `EARTHQUAKE_RECENT_COUNT` 脱钩。改为从 `/api/config` 取 `recentCount` |
| 13 | `frontend/map.js` | `f.properties.nam_ja` 为 null 会抛；空 `bbox()` 产出 `Infinity` → `NaN` viewBox；`poly[0]` 未校验。均已加守卫 |
| 14 | `backend/anime.py:18` | 新增 `_hhmm()`：把 `7:05` 补零成 `07:05`。全链路（24h+ 偏移、排序、前端 18:00 截断）都按字符串比较，单位数小时会排到 `23:00` 之后 |
| 15 | `backend/earthquake.py` | `active()` 用 `["expiresAt"]` 硬取 → 改 `.get(..., 0)` |
| 16 | `backend/main.py:28` | `_locks` 的键用 `id(cache)`（对象地址，不透明且理论上可被回收复用）→ 改为显式 feed 名 |
| 17 | 全站响应头 | 补 `X-Content-Type-Options: nosniff` / `Referrer-Policy: no-referrer` / `Content-Security-Policy: frame-ancestors 'none'`。**注意**：`frame-ancestors` 按规范在 `<meta>` 里是被忽略的，只能走响应头，所以与页面内 CSP 分开下发 |
| 18 | `deploy.sh` | systemd 补 `PrivateTmp` / `ProtectSystem=full` / `ProtectKernelTunables` / `ProtectControlGroups` / `RestrictSUIDSGID`（未开 `ProtectHome`：仓库常放在 `/root` 或 `/home`）。另外 `Restart=always` 下崩溃重启循环里 `is-active` 也可能瞬时为真，加了真打一次 `/api/config` 的健康检查 |
| 19 | `backend/run.sh` | 端口硬编码 12345，而 `deploy.sh` 会问端口 → 改为 `${PORT:-12345}` |
| 20 | `backend/main.py` lifespan | `ENABLE_DEMO` 开启时每次启动往 journalctl 打一条 WARNING |

---

## 二、需要你拍板的事项

### ✅ D1. `ENABLE_DEMO` 默认值 —— **已定：改成 `False`**（2026-08-05 你拍板）

原来是 `True`,意味着任何能访问该端口的人用一个普通 `GET /api/demo/quake`
就能让所有平板弹出假地震全屏(已实测可触发)。现在:

- `config.py`: `ENABLE_DEMO = False`,注释里写清了为什么默认关(GET 会被链接预取 /
  浏览器推测导航 / 爬虫误触发;假警报会训练人忽略真警报)。
- 两个 demo 路由不再挂载(路由数 21 → 19),`/api/demo/*` 返回 404。
- `demo` 这个 OpenAPI tag 也改成条件注册,`/docs` 里不会再出现一个空的 demo 分区。
- README §3 改成"想预览时临时打开、看完改回 False"。
- `deploy.sh` 结尾提示同步改口径。
- 开着的时候后端每次启动仍会打一条 WARNING(现在默认启动日志是干净的)。
- `test_api.py` 里那条记录该暴露面的测试带 `skipif`,现在自动跳过(201 passed, 1 skipped)。

### 🟡 D2. `_parse` 对结构性畸形的 JMA 响应仍然直接抛

`data` 为空数组、缺 `timeSeries` 等情况会抛异常，由 `_ensure` 兜住 → 保留上一份好
数据；但**冷启动**时就一直是空白卡片。

我认为**现状是对的**：与其编一张写满"—"的卡片，不如让"データ取得中…"继续显示。
但如果你希望冷启动也有个降级卡片（只有城市名+日期），说一声，改起来很快。

### 🟡 D3. 全屏阈值档位与 config 默认值可能对不上

`app.js` 的 `THRESHOLDS` 只有 `30 / 40 / 45` 三档。如果 `EARTHQUAKE_MIN_SCALE`
被改成别的值（比如 50），设置面板里**不会有任何一档高亮**，虽然阈值本身是生效的。
要不要：(a) 保持（档位就是产品定义的三档）；(b) 从 config 下发档位列表；
(c) 把 config 值不在列表时也追加一档。我倾向 (a)，未改。

### 🟡 D4. `anime.py` 的 `accept-encoding` 处理

现在是 `client.headers.pop("accept-encoding", None)`（你上一个 commit 的修复）。
按 RFC 9110，**不带**该头表示"任何编码都可以"，服务端仍可返回 gzip；真正表达
"别压缩"的写法是 `accept-encoding: identity`。

**但你的写法是实测有效的，而 `identity` 在个别不合规网关上会返回 406。**
我没有改。要不要换成 `identity`，你定。

---

## 三、命名 / 可读性记录（未改，交给你判断）

明显的我已经改了（`_quake_key` → `quake_key`，因为它是跨模块 import 的，
不该带下划线前缀；`no_store` → `security_and_cache_headers`，因为它早就不只做
缓存头了）。下面这些**语义有歧义但改动面较大**，列出来你定：

| 名字 | 位置 | 歧义在哪 | 建议 |
|---|---|---|---|
| `revision` | `earthquake.py` 两个 normalize | **同名两义**：551 里塞的是电文*类型*（`DetailScale`），556 里才是真的报数序号（第2報）。目前只做相等比较所以不出错，但字段名在 551 上是骗人的 | 拆成 `bulletin`（551）/ `serial`（556），或改叫 `stamp`。已加注释说明 |
| `_ensure` / `_ensure_ai` | `main.py` | "ensure"什么？实际是"取缓存，过期就重拉，失败保底" | `cached_city_feed` / `cached_ai_feed` |
| `empty`（`_ensure` 形参） | `main.py:80` | 不是"空"，是**冷启动兜底值** | `cold_start_value` |
| `state` | `main.py:24` | 太泛，实际是"共享的全局数据源快照" | `feeds` / `shared_feeds` |
| `_short_source` / `_clean_source` / `_split_source` | `news.py` | 三个名字极像、做的事完全不同（截断源名 / 去后缀 / 从标题里切源名） | `truncate_feed_name` / `strip_outlet_suffix` / `split_title_and_outlet` |
| `SCALE_CLASS` | `app.js` + `map.js` 各一份 | 两份完全相同的字面量，改一处漏一处 | 提到 `i18n.js` 或新建 `shindo.js` 共享 |
| `quakeScale()` 返回 `999` | `app.js` | 哨兵值，含义是"未知强度的 EEW，必须占屏" | 返回 `Infinity`，或显式返回 `{scale, alwaysShow}` |
| `_dparts` | `weather.py` | "d parts"？实际是"月/日 + 星期" | `date_labels` |
| `_num` | `weather.py:49` | 泛，实际是"按下标安全取整数" | `int_at` |
| `warea` / `a0` / `a1` / `w0` / `w1` | `weather.py` `_parse`/`_weekly` | 单字母+数字，读的时候要回去数 `timeSeries` 下标 | `weather_area` / `weekly_codes_area` / `weekly_temps_area` |
| `_city` / `_ai_source` | `main.py` | 名词形式但是查找函数 | `find_city` / `find_ai_source` |

---

## 四、复查结果

服务器全新启动 + 全套回归：

```
adversarial      3 个"失败"= D2 里说的刻意行为（结构性畸形 JMA 响应直接抛，由 _ensure 兜底）
earthquake fuzz  25 条真实电文 + 4000 条变异 + 9 种垃圾帧 → 0 崩溃；取消报作用域正确
broadcast        4 卡死 + 1 健康 → 健康端 0.0s 收到，整轮 5.0s（修复前 10.0s / 20.0s）
server/ws        路径穿越 404；602KB geo.json 304 协商正常；25 并发冷穿透 0.12s（锁合并生效）
                 WebSocket 补推正常；二进制帧后连接保持 OPEN
frontend (jsdom) 30/30 通过（渲染 / XSS 注入 0 逃逸 / 占屏阈值 / 取消作用域 / i18n 三语）
map              16/16 通过（46 主岛 + 2 冲绳要素、北海道四分区合并取最强、
                 与那国近海钳进小图、越界坐标隐藏震源标记）
live fetchers    11/11 上游全部通
```

前后端 JS 全部 `node --check` 通过，两个 shell 脚本 `bash -n` 通过。

---

## 五、优化方向 A–H（已全部实施）

> 第一轮审计提出 8 条方向，你批了「ABCDEFGH 你都可以优化」。以下是实际做了什么。
> 全部改动都有测试覆盖，见 `tests/`。

### A. 自动化测试 —— 从 0 到 226 个

之前**一个测试都没有**。现在 `tests/`：**202 个后端 + 24 个前端**，全部离线跑（用
`tests/fixtures/` 里 272 KB 的真实上游抓包），确定、快（后端 8 秒、前端 6 秒）、不联网。

另有 **9 个 `-m live` 契约测试**默认跳过：它们真的去打 JMA / met.no / P2P / 気象庁XML /
各 RSS，验证的是"上游结构还没变"——这是离线测试**永远看不到**的失效模式，也是这类项目
最可能坏掉的地方。已实跑通过。

```bash
bash tests/run.sh                # 两边一起
python3 -m pytest -m live        # 契约测试（慢，会因为不是你的错而变红）
```

地震那条链路覆盖最密（1500 条变异电文、取消报作用域、阈值规则、两个数据源的 key 一致性），
因为它是唯一"错了会有实际后果"的功能。

**测试本身就抓到了 3 个问题**，其中 1 个是我在这轮引入的真 bug：
`/api/config` 若返回 `null`（合法 JSON，不会 throw），`init()` 会在第一次取属性时抛异常，
**整个仪表盘永久空白**。已在 `core.js:getJson` 加 `data == null → fallback` 兜底。

### B. 可观测性 —— 新增 `/api/health` + 状态变化日志

之前所有失败路径都是 `except Exception: pass`。**这是设计使然**（永不空白），但代价是
"一切正常"和"这栏三小时没更新了"从外面看**完全一样**。

- 新增 `FeedHealth`：每路上游记录 最后成功时间 / 连续失败次数 / 最后错误。
- 新增 `GET /api/health`：8 路数据源 + 地震长连接状态 + `status: ok|degraded`。
- 日志**只在状态变化时打**（`fails` 0→1 打一次失败，恢复时打一次）——
  故障一整天也不会刷屏。有测试专门钉这条（`test_only_the_failure_TRANSITION_is_logged`）。

### C. 地震数据源单点 —— 新增気象庁 XML 备用源

P2P 挂了 = 唯一有安全意义的功能**静默停摆**。现在 `backend/earthquake_jma.py`：
P2P 断连超过 `EARTHQUAKE_FALLBACK_AFTER`（默认 5 分钟）自动切到気象庁 XML 电文源。

**验证得很实在**：拿同一时间窗的两个来源做交叉比对，**12/12 次地震 key 完全一致、
震源地/震级/深度/最大震度全部相同**。过程中发现一个关键细节——
P2P 的 `earthquake.time` 对应的是 JMA 的 **ArrivalTime 而不是 OriginTime**（实测差 1 分钟）。
按 OriginTime 取 key 的话，切换期间同一次地震会在 🗾 列表里出现两次。已按 ArrivalTime 对齐，
并且 `-m live` 里有一个测试专门守这条契约。

诚实的边界：**JMA 公开源没有 EEW**。P2P 断线期间只有地震情報，没有緊急地震速報——
这一点写进了 config 注释、README 和界面提示。首次轮询只"记录不播报"，
否则凌晨切换会为几小时前的地震抢屏。

界面上：右上角 🗾 出现小圆点（琥珀=正在用备用源、红=完全收不到），
悬停有说明文字，三语齐全。

### D. `_ensure` / `_ensure_ai` 去重 —— 合并为 `Feed` 类

两份近乎重复的双检锁+冷却逻辑合成一个 `Feed`，差异收敛成一个参数
`empty_is_failure`（AI 新闻栏空=抓取坏了；节日列表空=合法答案）。
顺带把不透明的 `id(cache)` 锁键换成了显式 feed 名。契约（永不抛、永不空、
冷却、并发合并）现在有 15 个测试钉着。

### E. `app.js` 800 行 —— 拆成 6 个模块

`core.js`（共享状态/t()/JST助手/时钟）、`weather.js`、`news.js`、`widgets.js`、
`quake.js`、`app.js`（设置+绑定+init）。**没有引入构建步骤或模块加载器**——
仍是 classic script 共享全局作用域，符合"平板上零框架开销"的原意。

拆分用脚本按行区间搬运并做了核对：**86 个顶层声明零丢失、零重复、非注释代码行零丢失**。
代价是**加载顺序成了唯一契约**，所以 `test_api.py` 里加了一条测试守 `index.html` 的
script 顺序（core.js 第二、app.js 最后），README 也写明了。

### F. 首屏 —— 省掉 2 个 RTT

`init()` 原本串行 `await` 了 `/api/config` → `/api/cities` → `/api/ai-sources`
才开始拉数据。三者互相独立，改成 `Promise.all`。

### G. `japan.geo.json` 602 KB —— gzip 6.7×

挂上 `GZipMiddleware`（`minimum_size=1024`）：**602,722 B → 89,718 B**（实测）。
另给这一个文件单独发 `max-age=86400`（生成产物，改它要手动重跑 `tools/build_map.py`），
其余静态资源维持 `no-cache`，改前端仍然刷新即生效。

### H. 客户端数量无上限 —— `MAX_WS_CLIENTS`

`config.MAX_WS_CLIENTS = 32`，超出的 `/ws` 连接以 1013（try again later）拒绝并记日志。

---

## 六、A–H 之后的复查

```
pytest                202 passed,  9 deselected      8.0s   （离线，用 fixtures）
pytest -m live          9 passed                     8.6s   （真打上游，全通）
node --test            24 passed                     6.4s   （jsdom + 真实 geojson）
tests/run.sh          ✅ all green

真实服务器启动：17 个端点全 200；/api/health = ok，8/8 数据源健康，P2P 已连接
gzip：japan.geo.json 602,722 B → 89,718 B
安全头：nosniff / no-referrer / frame-ancestors 'none' 全部下发
```

Python 全部 `py_compile` 通过，前端 8 个 JS 全部 `node --check` 通过，
两个 shell 脚本 `bash -n` 通过。

### 这一轮新增的待办（都很小，未做）

- `tests/frontend/` 没有提交 `package-lock.json`（jsdom 是唯一 dev 依赖）。
- `_clean_source` 的 ニュース 后缀剥离对**品牌名本身以ニュース结尾**的媒体是错的
  （ウェザーニュース → ウェザー）。已加一个小的例外名单
  （`SOURCE_KEEP_SUFFIX`），但这本质上无法从字符串本身判断，名单需要偶尔补。
- `/api/health` 无鉴权。内网场景无所谓，但它比其他接口多暴露一点内部状态
  （错误串、连续失败次数）。如果哪天要往公网放，这个要先关掉。

---

## 七、剩余项择优处理（2026-08-05）

你说「剩下的东西择优优化」，以下是取舍和理由。

### 做了

**D3 全屏阈值档位 —— 真 bug，已修** `frontend/app.js`
`EARTHQUAKE_MIN_SCALE` 接受任意 JMA 震度码，但设置面板只硬编码了 3 档（30/40/45）。
设成别的值（比如 55=6弱）时**阈值照常生效，面板却没有任何一档高亮**——看起来像没设置成功。
现在 `thresholdOptions()` 会把当前实际生效的值补进列表并按震度排序，永远有且只有一个高亮。
两条测试守着（预设值仍然只出 3 档；非预设值出 4 档且高亮正确）。

**`revision` 同名两义 —— 已改名为 `bulletin`**
551 里塞的是报文*类型*（`DetailScale`），556 里是报数*序号*——同一个字段名，两个意思，
在 551 上是骗人的。改名为 `bulletin`（"这次地震的第几报"），两种写法都诚实覆盖。
后端 3 处 + 前端 2 处 + 测试 + README 事件结构说明全部同步。实跑确认线上返回的是 `bulletin`。

**`SCALE_CLASS` 两份重复 —— 已合并到 `core.js`**
`quake.js` 和 `map.js` 各有一份完全相同的震度码→CSS 类字面量，加一个新码就得改两处。
现在统一在 `core.js`（同时新增 `SCALE_LABEL`，D3 要用）。`map.js` 依赖它，
已在文件头注明——`index.html` 的加载顺序本来就是契约。
`map.test.mjs` 改成从 `core.js` 里**抽取真实声明**再 eval，而不是在测试里再抄一份
（否则去重就白做了），core.js 一旦删掉它测试会直接报错。

**几处内部命名**（都不涉及对外接口）
`state` → `latest`（"最新一份"比"状态"准确）、`_city`/`_ai_source` → `find_city`/`find_ai_source`
（名词形式的查找函数）、`Feed.empty` → `cold_value`（它不是"空"，是首次成功之前的兜底值）、
`_dparts` → `date_labels`、`_num` → `int_at`、news.py 三个长得极像的
`_short_source`/`_clean_source`/`_split_source` → `truncate_feed_name`/`strip_outlet_suffix`/`split_title_and_outlet`。

**`tests/frontend/package-lock.json`** —— 已生成（64 条），测试环境可复现。

### D2 —— 折中处理，没有编造降级卡片

原问题：JMA 返回结构性畸形时 `_parse` 直接抛，冷启动会永远停在「データ取得中…」。

我**没有**加"城市名+日期+一堆 —"的降级卡片：一个长得像真数据的空壳，比一个诚实的空状态更糟。
但「データ取得中…」挂一整天确实是在撒谎。所以改成：前端本来就在轮询 `/api/health`，
当后端**确认**该数据源正在失败、且本地又一份数据都没有时，占位文案切成「データを取得できません」并转红。
慢加载仍然显示"取得中"——两种状态现在看起来不一样了。两条测试分别守这两种情况。

### 没做（附理由）

**D4 `anime.py` 的 `accept-encoding`** —— 维持 `pop` 掉整个头。
按 RFC 表达"别压缩"确实该写 `identity`，但**你现在的写法是实测有效的**，
而 `identity` 在个别不合规网关上会返回 406。为了措辞规范去动一个已经解决了真实故障
（Jikan 网关 504）的修复，是负期望值的交易。

**`/api/health` 鉴权** —— 内网设计，已在 README 写明"只应暴露在内网"。
它比其他接口多暴露的只是错误串和失败计数。真要放公网的话，该关的不止这一个接口。

**其余命名**（`quakeScale` 的 999 哨兵、weather.py 里的 `w0`/`a0` 局部名等）——
都在函数内 3～5 行的可视范围里，改动的收益不抵 diff 噪音。

---

## 八、最终复查

```
pytest        201 passed, 1 skipped, 9 deselected   8.2s
pytest -m live  9 passed                                   （真打上游）
node --test    28 passed                                   （+4：D3 两条、D2 两条）
tests/run.sh  ✅ all green

真实服务器：18 个路径全部符合预期（/api/demo/* = 404），启动日志 0 warning / 0 error
/api/health = ok，8/8 数据源健康，P2P 已连接
事件字段实测：bulletin='DetailScale'，revision 已不存在
```
