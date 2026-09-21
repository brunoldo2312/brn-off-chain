#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
[ -f .env ] && { set -a; source .env; set +a; }
echo "🚀 BRN prod em ${BRN_WEB_HOST:-0.0.0.0}:${BRN_WEB_PORT:-5000}"
exec waitress-serve --host="${BRN_WEB_HOST:-0.0.0.0}" --port="${BRN_WEB_PORT:-5000}" --threads=4 web_server:app
