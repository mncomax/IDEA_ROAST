#!/usr/bin/env bash
# Richtet Cron-Jobs auf dem Hetzner-Host ein (Backup + Integritätscheck im Container).
# Ausführbar: chmod +x scripts/backup-cron-setup.sh
# Auf dem Server als User mit Docker-Rechten ausführen (oft root oder Mitglied der docker-Gruppe).

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-idearoast-bot}"
BACKUP_SCRIPT_IN_CONTAINER="${BACKUP_SCRIPT_IN_CONTAINER:-/app/scripts/backup.sh}"
DB_IN_CONTAINER="${DB_IN_CONTAINER:-/app/data/idearoast.db}"
CRON_LOG="${CRON_LOG:-/var/log/idearoast-cron.log}"

CRON_DAILY='# Idea Roast: tägliches SQLite-Backup (03:00)'
CRON_DAILY_LINE="0 3 * * * docker exec ${CONTAINER_NAME} ${BACKUP_SCRIPT_IN_CONTAINER} >> ${CRON_LOG} 2>&1"

CRON_WEEKLY='# Idea Roast: PRAGMA integrity_check (Sonntag 04:00)'
CRON_WEEKLY_LINE="0 4 * * 0 docker exec ${CONTAINER_NAME} sqlite3 ${DB_IN_CONTAINER} \"PRAGMA integrity_check;\" >> ${CRON_LOG} 2>&1"

echo "=========================================="
echo "Idea Roast — Cron-Setup (Dry-Run / Vorschau)"
echo "=========================================="
echo ""
echo "Folgende Einträge werden zur Crontab hinzugefügt (falls noch nicht vorhanden):"
echo ""
echo "  ${CRON_DAILY}"
echo "  ${CRON_DAILY_LINE}"
echo ""
echo "  ${CRON_WEEKLY}"
echo "  ${CRON_WEEKLY_LINE}"
echo ""
echo "Log-Datei (auf dem Host): ${CRON_LOG}"
echo "Container: ${CONTAINER_NAME}"
echo ""
echo "Hinweis: Stelle sicher, dass ${BACKUP_SCRIPT_IN_CONTAINER} im Image existiert"
echo "         und sqlite3 im Container installiert ist."
echo ""

if ! command -v docker >/dev/null 2>&1; then
  echo "FEHLER: docker nicht im PATH." >&2
  exit 1
fi

read -r -p "Fortfahren und Crontab aktualisieren? [j/N] " confirm
case "${confirm}" in
  j|J|ja|Ja|y|Y|yes|Yes) ;;
  *)
    echo "Abgebrochen."
    exit 0
    ;;
esac

# Logdatei anlegen (best effort; Cron schreibt sonst evtl. fehl)
if [[ ! -f "${CRON_LOG}" ]] && [[ -w "$(dirname "${CRON_LOG}")" ]] 2>/dev/null; then
  touch "${CRON_LOG}" 2>/dev/null || true
fi

TMP_CRON="$(mktemp)"
trap 'rm -f "${TMP_CRON}"' EXIT

crontab -l 2>/dev/null > "${TMP_CRON}" || true

append_if_missing() {
  local line="$1"
  grep -Fxq "${line}" "${TMP_CRON}" 2>/dev/null && return 0
  printf '%s\n' "${line}" >> "${TMP_CRON}"
}

append_if_missing "${CRON_DAILY}"
append_if_missing "${CRON_DAILY_LINE}"
append_if_missing "${CRON_WEEKLY}"
append_if_missing "${CRON_WEEKLY_LINE}"

crontab "${TMP_CRON}"

echo ""
echo "=========================================="
echo "Aktuelle Crontab:"
echo "=========================================="
crontab -l
echo ""

echo "=========================================="
echo "Manuelles Backup (einmalig testen)"
echo "=========================================="
echo "  docker exec ${CONTAINER_NAME} ${BACKUP_SCRIPT_IN_CONTAINER}"
echo ""
echo "Integritätscheck manuell:"
echo "  docker exec ${CONTAINER_NAME} sqlite3 ${DB_IN_CONTAINER} \"PRAGMA integrity_check;\""
echo ""
echo "Logs (Host): tail -f ${CRON_LOG}"
echo ""
