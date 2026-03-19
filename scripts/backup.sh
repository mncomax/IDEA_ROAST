#!/usr/bin/env bash
# Idea Roast — SQLite-Backup mit Rotation (Docker oder Host via Env).
# Ausführbar: chmod +x scripts/backup.sh
# Voraussetzung: sqlite3-CLI (im Container: z. B. apt install sqlite3).

set -euo pipefail

readonly MAX_DAILY="${MAX_DAILY:-7}"
readonly MAX_WEEKLY="${MAX_WEEKLY:-4}"
readonly BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
readonly DB_PATH="${DB_PATH:-/app/data/idearoast.db}"
readonly TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

log() {
  echo "[$(date -Iseconds 2>/dev/null || date)] $*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 nicht gefunden (PATH prüfen oder sqlite3 installieren)"

if [[ ! -f "$DB_PATH" ]]; then
  fail "Datenbank nicht gefunden: $DB_PATH"
fi

if [[ ! -r "$DB_PATH" ]]; then
  fail "Datenbank nicht lesbar: $DB_PATH"
fi

mkdir -p "$BACKUP_DIR" || fail "Backup-Verzeichnis konnte nicht angelegt werden: $BACKUP_DIR"

DAILY_BASE="${BACKUP_DIR}/daily_${TIMESTAMP}.db"
DAILY_GZ="${DAILY_BASE}.gz"

log "Start Backup: DB=$DB_PATH -> $DAILY_GZ"

# Konsistentes Online-Backup (SQLite .backup)
if ! sqlite3 "$DB_PATH" ".backup '${DAILY_BASE}'"; then
  fail "sqlite3 .backup fehlgeschlagen"
fi

if ! gzip -f "$DAILY_BASE"; then
  fail "gzip fehlgeschlagen für $DAILY_BASE"
fi

log "Tages-Backup erstellt: $DAILY_GZ"

# Rotation: nur die neuesten MAX_DAILY daily_*.db.gz behalten
shopt -s nullglob
DAILY_MATCH=( "${BACKUP_DIR}"/daily_*.db.gz )
shopt -u nullglob
if ((${#DAILY_MATCH[@]} > MAX_DAILY)); then
  while IFS= read -r rmfile; do
    [[ -z "$rmfile" ]] && continue
    rm -f "$rmfile"
    log "Altes Tages-Backup entfernt: $rmfile"
  done < <(ls -t "${DAILY_MATCH[@]}" | tail -n +$((MAX_DAILY + 1)))
fi

# Sonntag: wöchentliche Kopie des letzten Daily-Backups
# date +%w: 0 = Sonntag (GNU und BSD)
if [[ "$(date +%w)" == "0" ]]; then
  shopt -s nullglob
  DAILY_FOR_WEEK=( "${BACKUP_DIR}"/daily_*.db.gz )
  shopt -u nullglob
  if ((${#DAILY_FOR_WEEK[@]} > 0)); then
    LATEST_DAILY="$(ls -t "${DAILY_FOR_WEEK[@]}" | head -1)"
    WEEKLY_GZ="${BACKUP_DIR}/weekly_${TIMESTAMP}.db.gz"
    if cp -f "$LATEST_DAILY" "$WEEKLY_GZ"; then
      log "Wochen-Backup angelegt: $WEEKLY_GZ (Kopie von $LATEST_DAILY)"
    else
      fail "Kopie für Wochen-Backup fehlgeschlagen"
    fi

    shopt -s nullglob
    WEEK_MATCH=( "${BACKUP_DIR}"/weekly_*.db.gz )
    shopt -u nullglob
    if ((${#WEEK_MATCH[@]} > MAX_WEEKLY)); then
      while IFS= read -r rmw; do
        [[ -z "$rmw" ]] && continue
        rm -f "$rmw"
        log "Altes Wochen-Backup entfernt: $rmw"
      done < <(ls -t "${WEEK_MATCH[@]}" | tail -n +$((MAX_WEEKLY + 1)))
    fi
  else
    log "WARNUNG: Sonntag, aber kein daily_*.db.gz für Wochen-Backup gefunden"
  fi
fi

log "Backup abgeschlossen (OK)"
exit 0
