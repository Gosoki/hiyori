# tests

```bash
# 后端（离线，用 fixtures/ 里的真实抓包，不联网）
python3 -m pytest

# 前端（jsdom；首次需要 cd tests/frontend && npm install）
node --test "tests/frontend/*.test.mjs"

# 一次跑完两边
bash tests/run.sh

# 契约测试：真的去打上游，确认它们的返回结构还和我们假设的一样（慢，默认跳过）
python3 -m pytest -m live
```

## 为什么分成两类

这个项目的所有失败路径都**故意**降级成"保留上一份好数据"——上游挂了，屏幕上什么都不变。
好处是墙上的平板永远不会空白，代价是**bug 和上游故障看起来一模一样**。所以：

- **离线单元测试**（默认）：跑在 `fixtures/` 里的真实抓包上。确定、快、不依赖网络，
  验证的是"给定这份数据，我们的处理对不对"。
- **`-m live` 契约测试**：真的去打 JMA / met.no / P2P / 気象庁XML / 各 RSS。
  验证的是"上游的结构还没变"。默认跳过——它们慢，而且会因为不是我们的错而变红。

## fixtures/

真实上游响应的抓包（共约 270 KB）。重新抓取见 `AUDIT.md`；注意 `jma_eqvol_feed.xml`
是**裁剪过的**（原始 500 KB，只留了测试用得到的 10 条 entry）。

`p2p_551.json` / `jma_vxse53_*.xml` 是同一时间窗的两个来源，**故意保持重叠**——
`test_earthquake_jma.py` 靠这个重叠验证两个数据源对同一次地震算出的 key 一致。
换 fixture 时如果丢了重叠，那几个测试会明确报 "no shared quakes"。

## 重点覆盖的地方

| 文件 | 盯着什么 |
|---|---|
| `test_earthquake.py` | 电文归一化、1500 条变异电文不炸、取消报作用域、占屏状态机 |
| `test_earthquake_jma.py` | 备用源与 P2P **完全等价**（尤其是 key 一致，否则同一次地震会出现两次） |
| `test_weather.py` | JMA 两套区域编码不能混用；当天气温 memo；`max([])` 崩溃回归 |
| `test_feed.py` | 缓存"永不抛、永不空"契约；冷却；并发合并；健康只在状态**变化**时打日志 |
| `test_api.py` | 全部接口 + 安全响应头 + 路径穿越 + WebSocket 二进制帧 + **并发扇出延迟** |
| `test_news.py` / `test_anime.py` | 标题/媒体名切分、NERV 严重度过滤、24h+ 记法与排序 |
| `frontend/frontend.test.mjs` | 渲染、XSS 零逃逸、**地震占屏规则**（阈值 / 未知震度 / 取消作用域）、i18n |
| `frontend/map.test.mjs` | EEW 无后缀地名、北海道四分区合并、冲绳小图路由与越界钳制 |

地震那条链路的测试最密，因为它是唯一"错了会有实际后果"的功能，而且电文格式不受我们控制。
