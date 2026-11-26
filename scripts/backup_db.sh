#!/usr/bin/env bash
# ==============================================================================
# METEORO TRADER SOLANA — WAL-Safe SQLite Online Backup Script (14-day retention)
# Usage (cron on Ubuntu 24.04 VPS):
#   0 3 * * * /opt/meteoro-trader/scripts/backup_db.sh >> /opt/meteoro-trader/backend/logs/backup.log 2>&1
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DB_PATH="${1:-${PROJECT_ROOT}/backend/data/desk.db}"
BACKUP_DIR="${2:-${PROJECT_ROOT}/backend/data/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[ERROR] sqlite3 binary is required but not installed." >&2
  exit 1
fi

if [[ ! -f "${DB_PATH}" ]]; then
  echo "[WARN] Database file not found at ${DB_PATH}; skipping backup."
  exit 0
fi

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
TMP_BACKUP="${BACKUP_DIR}/desk_${TIMESTAMP}.db"
FINAL_ARCHIVE="${TMP_BACKUP}.gz"

# Perform WAL-safe online SQLite backup (never plain cp on active WAL db)
sqlite3 "${DB_PATH}" ".timeout 5000" ".backup '${TMP_BACKUP}'"
sqlite3 "${TMP_BACKUP}" "PRAGMA integrity_check;" | grep -qx "ok"

gzip -9 "${TMP_BACKUP}"
chmod 600 "${FINAL_ARCHIVE}"

# Prune backups older than RETENTION_DAYS (default 14 days)
DELETED_COUNT="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'desk_*.db.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"

echo "[OK] $(date -u +%Y-%m-%dT%H:%M:%SZ) Created ${FINAL_ARCHIVE} | Pruned ${DELETED_COUNT} backup(s) older than ${RETENTION_DAYS} days."
