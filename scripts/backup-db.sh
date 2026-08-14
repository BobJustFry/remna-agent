#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/data/postgres"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST_ROOT="$ROOT/data/backups"
DEST="$DEST_ROOT/postgres-$STAMP"

if [ ! -d "$SRC" ]; then
  echo "DB path not found: $SRC" >&2
  exit 1
fi

mkdir -p "$DEST_ROOT"
echo "Stopping db for consistent backup..."
cd "$ROOT"
docker compose stop db
echo "Copying $SRC -> $DEST"
cp -a "$SRC" "$DEST"
echo "Starting db..."
docker compose start db
echo "Backup ready: $DEST"
