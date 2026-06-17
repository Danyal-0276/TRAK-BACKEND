#!/bin/sh
# Run TRAK daily scrape + pipeline in a separate one-shot container.
# Usage (VPS): ./deploy/run-pipeline.sh
# Cron (daily 02:00): 0 2 * * * /home/shahroz/trak/deploy/run-pipeline.sh >> /home/shahroz/trak/logs/pipeline.log 2>&1

set -e

ROOT="${TRAK_ROOT:-$HOME/trak}"
LOG_DIR="${TRAK_LOG_DIR:-$ROOT/logs}"

mkdir -p "$LOG_DIR"
cd "$ROOT"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline start ==="
docker compose --profile pipeline run --rm pipeline
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline done ==="
