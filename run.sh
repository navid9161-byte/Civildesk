#!/usr/bin/env sh
# اجرای سیویل‌دسک روی لینوکس/مک
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
echo "CivilDesk: http://localhost:8000"
exec python -m uvicorn civildesk.main:app --port 8000 "$@"
