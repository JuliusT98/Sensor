#!/usr/bin/env bash
# Wrapper invoked by sensor-mission.service. One flight per Pi boot:
# waits for arm, runs the mission, exits. Writes one timestamped log file
# per boot so it survives being reviewed after an abrupt power-off.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_DIR/output/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/mission_$(date +%Y%m%d_%H%M%S).log"

cd "$REPO_DIR/src"
exec "$REPO_DIR/src/sprayvenv/bin/python3" -u mission.py \
    --port /dev/ttyUSB0 \
    --wait-for-arm \
    >>"$LOG_FILE" 2>&1
