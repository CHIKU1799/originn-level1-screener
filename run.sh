#!/usr/bin/env bash
# Originn Level 1 Screener — one-command launcher.
#
# Usage:
#   bash run.sh            # install deps + start server on :8000
#   PORT=9000 bash run.sh  # custom port
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8000}"

# ── Load .env if present (without exporting secrets to the shell on error) ─
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "❌  OPENAI_API_KEY is not set."
  echo "    Copy .env.example → .env and fill it in, or:"
  echo "    export OPENAI_API_KEY=sk-..."
  exit 1
fi
echo "✅  OPENAI_API_KEY is set."

echo "→ Installing Python dependencies…"
pip3 install -q -r requirements.txt

echo ""
echo "→ Starting server on http://localhost:${PORT}"
echo "   Swagger UI: http://localhost:${PORT}/docs"
echo "   Press Ctrl+C to stop."
echo ""

python3 -m uvicorn originn_level1.api:app \
  --host 0.0.0.0 --port "$PORT" --workers 1 --log-level info
