#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$PROJECT_ROOT/backups}"
DATABASE_PATH="${DATABASE_PATH:-$PROJECT_ROOT/data/app.db}"
UPLOAD_DIR="${UPLOAD_DIR:-$PROJECT_ROOT/data/uploads}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"

mkdir -p "$BACKUP_ROOT"
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9 || { echo "backup already running" >&2; exit 75; }

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" scripts/backup.py \
  --database "$DATABASE_PATH" \
  --uploads "$UPLOAD_DIR" \
  --backup-root "$BACKUP_ROOT" \
  --retention-days "${BACKUP_RETENTION_DAYS:-7}"
