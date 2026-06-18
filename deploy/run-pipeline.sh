#!/bin/bash
# Run TRAK daily scrape + pipeline in a separate one-shot container.
# API auto only handles leftovers (admin scrape, errors) — not the full daily cycle.
# Usage (VPS): ./deploy/run-pipeline.sh
# Cron (daily 02:00): 0 2 * * * /home/shahroz/trak/deploy/run-pipeline.sh

set -euo pipefail

ROOT="${TRAK_ROOT:-$HOME/trak}"
LOG_DIR="${TRAK_LOG_DIR:-$ROOT/logs}"
LOG_FILE="${LOG_DIR}/pipeline.log"

mkdir -p "$LOG_DIR"
cd "$ROOT"

log() {
  echo "$@" | tee -a "$LOG_FILE"
}

log "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline start ==="
docker compose --profile pipeline run --rm pipeline 2>&1 | tee -a "$LOG_FILE"
log "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) pipeline done (exit ${PIPESTATUS[0]}) ==="
