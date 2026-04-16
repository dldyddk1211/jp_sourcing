#!/bin/bash
# users.db 자동 백업 (하루 2회: 낮12시, 밤12시)
# 회원정보, 주문서, 고객요청 등 중요 데이터 보호
# 저장: 로컬 + NAS(ipdisk 공유폴더) 양쪽

DB_DIR="/Users/ya/Documents/theone/srv/data/vintage/db"
LOCAL_BACKUP="$DB_DIR/backups"
NAS_BACKUP="/Volumes/파일공유/00 이용아/thone/srv/data/vintage/backups"
TIMESTAMP=$(date '+%Y%m%d_%H%M')
MAX_BACKUPS=60  # 최대 보관 개수 (약 1달분, 하루 2회)

mkdir -p "$LOCAL_BACKUP"

# ── 1. 로컬 백업 ──
if [ -f "$DB_DIR/users.db" ]; then
    sqlite3 "$DB_DIR/users.db" ".backup '$LOCAL_BACKUP/users_${TIMESTAMP}.db'"
    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 로컬 백업 완료 → users_${TIMESTAMP}.db"
    else
        cp "$DB_DIR/users.db" "$LOCAL_BACKUP/users_${TIMESTAMP}.db"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 로컬 백업 완료 (복사) → users_${TIMESTAMP}.db"
    fi
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] users.db 없음 — 백업 스킵"
    exit 1
fi

# 로컬 오래된 백업 정리
cd "$LOCAL_BACKUP"
ls -t users_*.db 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null

# ── 2. NAS 백업 ──
if [ -d "/Volumes/파일공유" ]; then
    mkdir -p "$NAS_BACKUP"
    cp "$LOCAL_BACKUP/users_${TIMESTAMP}.db" "$NAS_BACKUP/users_${TIMESTAMP}.db"
    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] NAS 백업 완료 → $NAS_BACKUP/users_${TIMESTAMP}.db"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] NAS 백업 실패"
    fi
    # NAS 오래된 백업 정리
    cd "$NAS_BACKUP"
    ls -t users_*.db 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] NAS 미연결 — NAS 백업 스킵"
fi

# ── 현황 ──
LOCAL_CNT=$(ls -1 "$LOCAL_BACKUP"/users_*.db 2>/dev/null | wc -l | tr -d ' ')
LOCAL_SIZE=$(du -sh "$LOCAL_BACKUP" 2>/dev/null | awk '{print $1}')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 로컬 백업: ${LOCAL_CNT}개 (${LOCAL_SIZE})"

if [ -d "$NAS_BACKUP" ]; then
    NAS_CNT=$(ls -1 "$NAS_BACKUP"/users_*.db 2>/dev/null | wc -l | tr -d ' ')
    NAS_SIZE=$(du -sh "$NAS_BACKUP" 2>/dev/null | awk '{print $1}')
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] NAS 백업: ${NAS_CNT}개 (${NAS_SIZE})"
fi
