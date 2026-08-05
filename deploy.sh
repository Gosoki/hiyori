#!/usr/bin/env bash
# 日和 Hiyori 一键部署（Debian/Ubuntu LXC 或裸机，root 运行）。
#
#   bash deploy.sh
#
# 做三件事：装 uv（自带独立 Python，不依赖系统 python）→ 建 .venv 装依赖 →
# 生成 systemd 服务并启动（uvicorn 同时托管 API + WebSocket + 前端静态页）。
#
# 幂等：任何一步失败会立刻停下并报错，修完重跑即可，不会留半成品。
# 路径全部由脚本自身位置推导，不写死，所以仓库克隆到哪都行。
set -euo pipefail

SVC=hiyori
PORT_DEFAULT=12345
PYVER=3.12

# ---- 1. 定位项目（脚本就在项目根目录，自我定位，无需 cd 或传参）----
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$APP/backend"
[ -f "$BACKEND/main.py" ]          || { echo "❌ $BACKEND 下没有 main.py —— deploy.sh 必须放在 hiyori 项目根目录"; exit 1; }
[ -f "$BACKEND/requirements.txt" ] || { echo "❌ $BACKEND 下没有 requirements.txt"; exit 1; }
[ -d "$APP/frontend" ]             || { echo "❌ $APP 下没有 frontend/ —— 后端启动时要托管它"; exit 1; }
case "$APP" in *[[:space:]]*) echo "❌ 项目路径含空格（systemd ExecStart 不好处理）: $APP"; exit 1;; esac
[ "$(id -u)" -eq 0 ]               || { echo "❌ 需要 root（要写 /etc/systemd/system 并安装依赖）"; exit 1; }
echo "✔ 项目目录: $APP"

# ---- 1.5 端口（只问一次，回车用默认；前端由后端同端口托管，无需第二个端口）----
# 非交互运行（管道/CI）不问，直接用默认；也可 PORT=xxxx bash deploy.sh 预设跳过询问。
if [ -z "${PORT:-}" ] && [ -t 0 ]; then
  read -r -p "服务端口（前端+API 同一端口）[${PORT_DEFAULT}]: " PORT
fi
PORT="${PORT:-$PORT_DEFAULT}"
case "$PORT" in *[!0-9]*|'') echo "❌ 端口必须是数字: $PORT"; exit 1;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { echo "❌ 端口超范围(1-65535): $PORT"; exit 1; }
echo "✔ 端口: $PORT"

# ---- 2. 确保 uv 可用（始终显式加 PATH，不依赖登录 shell 的 source）----
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "→ 安装 uv …"
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { echo "❌ uv 安装失败，检查容器能否联网"; exit 1; }
echo "✔ uv $(uv --version)"

# ---- 3. 虚拟环境 + 依赖（uv 下载独立 CPython，与系统 python 版本无关）----
cd "$BACKEND"
uv python install "$PYVER"
uv venv --python "$PYVER"
uv pip install -r requirements.txt

PY="$BACKEND/.venv/bin/python"
UVICORN="$BACKEND/.venv/bin/uvicorn"
[ -x "$PY" ]      || { echo "❌ 虚拟环境没建出来：$PY"; exit 1; }
[ -x "$UVICORN" ] || { echo "❌ uvicorn 没装上：$UVICORN"; exit 1; }
"$PY" -c "import fastapi, uvicorn, httpx, feedparser, websockets" || { echo "❌ 依赖没装全"; exit 1; }
echo "✔ 依赖 OK（$("$PY" -V)）"

# ---- 4. systemd（路径用上面检测到的，杜绝写错）----
cat > "/etc/systemd/system/${SVC}.service" <<EOF
[Unit]
Description=Hiyori Dashboard
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$BACKEND
ExecStart="$UVICORN" main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
# 只读取自己的代码 + 出网抓数据，不需要写系统任何位置。以下限制都不影响运行，
# 但能把「万一被上游数据打穿」的后果限制住。ProtectHome 没开：仓库常放在 /root 或 /home。
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1 || true
systemctl restart "$SVC"
sleep 3

# is-active 还不够：Restart=always 时崩溃重启循环里也可能瞬时是 active。
# 真去打一次接口，确认端口上确实是本服务在应答。
health_ok() {
  command -v curl >/dev/null 2>&1 || return 0     # 没 curl 就只信 is-active
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/config" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

if systemctl is-active --quiet "$SVC" && health_ok; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "✅ 部署完成 → http://${IP:-<机器IP>}:${PORT}"
  echo "   平板全屏打开上面地址即可（kiosk 快捷方式见 README §2）。"
  echo "   API 文档: http://${IP:-<机器IP>}:${PORT}/docs"
  echo "   想预览地震全屏：把 backend/config.py 的 ENABLE_DEMO 临时改为 True 并重启，"
  echo "   访问 /api/demo/quake 看完后改回 False（该接口无鉴权，开着谁都能触发假警报）。"
  echo
  echo "   日志: journalctl -u ${SVC} -f     重启: systemctl restart ${SVC}"
  echo "   升级: git pull && bash deploy.sh"
else
  echo "❌ 启动失败或端口 ${PORT} 无应答（端口被占用？看下面日志）："
  journalctl -u "$SVC" -n 30 --no-pager
  exit 1
fi
