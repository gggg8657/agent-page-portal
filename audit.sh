#!/usr/bin/env bash
# 전체 패키지 점검: 외부 URL 유출(CDN) · 포트 중복 · LICENSE/NOTICE · selftest.  bash portal/audit.sh
cd "$(dirname "$0")/.."
printf '%-20s %-6s %-5s %-7s %-7s %s\n' 패키지 포트 CDN LICENSE NOTICE selftest
python3 -c "import json;[print(t['dir'],t['port']) for t in json.load(open('portal/tools.json'))]" | while read d p; do
  [ -d "$d" ] || { printf '%-20s %-6s (미설치)\n' "$d" "$p"; continue; }
  cdn=$(grep -hoE '(src|href)="https?://[^"]+' "$d"/ui.html "$d"/*.html 2>/dev/null | grep -v 'github.com' | wc -l | tr -d ' ')
  py=$([ -x "$d/venv/bin/python" ] && echo "$d/venv/bin/python" || echo python3)
  if [ -f "$d/selftest.py" ]; then r=$(cd "$d" && timeout 300 "$OLDPWD/$py" selftest.py >/dev/null 2>&1 && echo OK || echo FAIL); else r=없음; fi
  printf '%-20s %-6s %-5s %-7s %-7s %s\n' "$d" "$p" "$cdn" "$([ -f $d/LICENSE ] && echo y || echo n)" "$([ -f $d/NOTICE ] && echo y || echo n)" "$r"
done
echo "--- 포트 중복 ---"; grep -ohE '"PORT", "[0-9]+"' */app.py | sort | uniq -d || echo 없음
