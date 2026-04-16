#!/bin/bash
# vintage 서버 자동 재시작 스크립트
# 서버가 크래시해도 5초 후 자동 재시작

cd "$(dirname "$0")"

LOG_FILE="server_restart.log"
MAX_RESTARTS=50        # 최대 연속 재시작 횟수
RESTART_DELAY=5        # 재시작 대기 (초)
RAPID_CRASH_LIMIT=10   # 빠른 크래시 감지 (초 이내 종료 시)
rapid_count=0

echo "========================================"
echo "  Vintage Server (자동 재시작 모드)"
echo "  http://localhost:3002"
echo "========================================"

restart_count=0

while [ $restart_count -lt $MAX_RESTARTS ]; do
    start_time=$(date +%s)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 서버 시작 (재시작 #${restart_count})" | tee -a "$LOG_FILE"

    python app.py 2>&1

    exit_code=$?
    end_time=$(date +%s)
    elapsed=$((end_time - start_time))

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 서버 종료 (exit code: ${exit_code}, 실행시간: ${elapsed}초)" | tee -a "$LOG_FILE"

    # 정상 종료 (SIGTERM 등) 시 재시작 안 함
    if [ $exit_code -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 정상 종료 — 재시작 안 함" | tee -a "$LOG_FILE"
        break
    fi

    # 빠른 크래시 감지 (10초 이내 종료 반복)
    if [ $elapsed -lt $RAPID_CRASH_LIMIT ]; then
        rapid_count=$((rapid_count + 1))
        if [ $rapid_count -ge 5 ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] 빠른 크래시 5회 연속 — 재시작 중단" | tee -a "$LOG_FILE"
            break
        fi
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 빠른 크래시 감지 (${rapid_count}/5) — 대기시간 증가" | tee -a "$LOG_FILE"
        sleep $((RESTART_DELAY * rapid_count))
    else
        rapid_count=0
    fi

    restart_count=$((restart_count + 1))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${RESTART_DELAY}초 후 재시작..." | tee -a "$LOG_FILE"
    sleep $RESTART_DELAY
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 서버 스크립트 종료" | tee -a "$LOG_FILE"
