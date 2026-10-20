#!/bin/bash
# 사용자 데이터 백업: _data (모든 도구 기록 + portal.db) → tar.gz 하루 1개, 최근 7일치만 남김. 로그: backup.log
#   ./backup.sh                 # 지금 한 번 (같은 날 다시 돌리면 그날 파일을 새로 덮어씀)
# 포털(:8700)이 매일 03시(KST)쯤 부르고, autostart.sh 가 도구를 다 띄운 뒤 한 번 부른다.
# 저장 위치: $AGENT_BACKUP_DIR (기본 ~/agent-page-backup). ⚠ 지금은 _data 와 같은 디스크 — 컨테이너 재시작·실수 삭제는 막지만 디스크 고장은 못 막음.
# 빼는 것: 다시 만들 수 있는 캐시·모델·가상환경. sqlite DB 는 쓰는 중이어도 깨지지 않게 sqlite3 backup API 로 따로 복사.
# 되살리기: tar xzf <파일> -C ~/agent-page   (기존 _data 를 먼저 옮겨 두고)
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더 (상위 폴더 backup.sh 의 사본)
BASE="$PWD" DATA="${AGENT_DATA:-$PWD/_data}" OUT="${AGENT_BACKUP_DIR:-$HOME/agent-page-backup}" KEEP="${BACKUP_KEEP:-7}"
umask 077   # 백업에는 PIN 해시·세션·비밀 파일이 들어 있음 — 주인만 읽게
mkdir -p "$OUT" && chmod 700 "$OUT"
exec 9>"$OUT/.lock"; flock -n 9 || { echo "$(TZ=Asia/Seoul date '+%F %T KST') 이미 백업 중 — 건너뜀" >> "$BASE/backup.log"; exit 0; }
{
  t0=$(date +%s); day=$(TZ=Asia/Seoul date +%F)
  stage=$(mktemp -d "$OUT/.stage.XXXX"); trap 'rm -rf "$stage" "$OUT/.part.tar" "$OUT/.part.tar.gz"' EXIT
  # sqlite DB: WAL 쓰는 중에도 일관된 사본
  (cd "$DATA" && find . \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -type f) | python3 -c '
import os, shutil, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
for rel in sys.stdin.read().split():
    a, b = os.path.join(src, rel), os.path.join(dst, "_data", rel)
    os.makedirs(os.path.dirname(b), exist_ok=True)
    try:
        s = sqlite3.connect(f"file:{a}?mode=ro", uri=True, timeout=30); d = sqlite3.connect(b)
        s.backup(d); d.close(); s.close()
    except sqlite3.Error as e:
        print("sqlite 아님/실패 → 그대로 복사:", rel, e); shutil.copy2(a, b)
' "$DATA" "$stage"
  tar -cf "$OUT/.part.tar" \
      --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' --exclude='*.sqlite' --exclude='*.sqlite3' --exclude='*.sqlite-wal' --exclude='*.sqlite-shm' \
      --exclude='*/cache' --exclude='*/.cache' --exclude='*/__pycache__' --exclude='*/venv' --exclude='*/.venv' --exclude='*/hf_home' \
      --exclude='*.safetensors' --exclude='*.ckpt' --exclude='*.gguf' --exclude='*.onnx' --exclude='./tls' --exclude='tls' --exclude='*.key' \
      --warning=no-file-changed --warning=no-file-removed \
      -C "$(dirname "$DATA")" "$(basename "$DATA")"
  rc=$?
  [ $rc -le 1 ] && { [ -d "$stage/_data" ] && tar -rf "$OUT/.part.tar" -C "$stage" _data; gzip -1 "$OUT/.part.tar" || rc=2; }  # DB 사본은 위 제외 규칙 없이 덧붙임
  if [ $rc -le 1 ]; then   # 1 = 백업 중에 파일이 바뀜(쓰는 중인 로그 등) — 정상으로 본다
    mv -f "$OUT/.part.tar.gz" "$OUT/agent-data-$day.tar.gz"
    ls -1t "$OUT"/agent-data-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
    echo "$(TZ=Asia/Seoul date '+%F %T KST') OK agent-data-$day.tar.gz $(du -h "$OUT/agent-data-$day.tar.gz" | cut -f1) $(( $(date +%s) - t0 ))s · 보관 $(ls "$OUT"/agent-data-*.tar.gz | wc -l)개 · 남은 공간 $(df -h "$OUT" | awk 'NR==2{print $4}')"
  else
    echo "$(TZ=Asia/Seoul date '+%F %T KST') 실패 tar rc=$rc"; exit $rc
  fi
} >> "$BASE/backup.log" 2>&1
