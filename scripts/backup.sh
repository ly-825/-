#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
BACKUP_ROOT="$PROJECT_ROOT/backups"
TIMESTAMP="$(date '+%Y-%m-%d_%H%M%S')"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"

mkdir -p "$BACKUP_DIR"

if [ -f "$DATA_DIR/app.db" ]; then
  cp "$DATA_DIR/app.db" "$BACKUP_DIR/app.db"
fi

if [ -d "$DATA_DIR/uploads" ]; then
  mkdir -p "$BACKUP_DIR/uploads"
  cp -R "$DATA_DIR/uploads/." "$BACKUP_DIR/uploads/"
fi

cat > "$BACKUP_DIR/README.txt" <<EOF
Backup time: $TIMESTAMP
Source data directory: $DATA_DIR

Restore manually:
1. Stop backend service.
2. Copy app.db back to data/app.db.
3. Copy uploads contents back to data/uploads.
4. Restart backend service.
EOF

echo "Backup created: $BACKUP_DIR"
