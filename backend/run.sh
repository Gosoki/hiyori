#!/usr/bin/env bash
# Start the dashboard backend. Serves the frontend + API + WebSocket.
# Port: $PORT, else 12345 — same default deploy.sh uses, so a deployment on a
# custom port can be reproduced by hand with `PORT=xxxx ./run.sh`.
set -e
cd "$(dirname "$0")"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-12345}"
