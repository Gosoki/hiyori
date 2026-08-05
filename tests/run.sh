#!/usr/bin/env bash
# Run both suites. Offline by default — see tests/README.md for `-m live`.
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in backend/.venv/bin/python python3 python; do
    command -v "$cand" >/dev/null 2>&1 && { PY="$cand"; break; }
    [ -x "$cand" ] && { PY="$cand"; break; }
  done
fi

fail=0
echo "── backend ────────────────────────────────────────────────"
if "$PY" -c "import pytest" 2>/dev/null; then
  "$PY" -m pytest "$@" || fail=1
else
  echo "SKIP: pytest not installed ($PY -m pip install pytest)"
fi

echo
echo "── frontend ───────────────────────────────────────────────"
if [ -d tests/frontend/node_modules ]; then
  # glob, not the directory: node's runner resolves a bare dir as a module path
  node --test "tests/frontend/*.test.mjs" || fail=1
else
  echo "SKIP: run 'cd tests/frontend && npm install' first (jsdom is test-only)"
fi

echo
[ "$fail" -eq 0 ] && echo "✅ all green" || echo "❌ failures above"
exit "$fail"
