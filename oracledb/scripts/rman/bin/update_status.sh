#!/usr/bin/env bash
# Atualiza a tabela de estado (config/../logs/rman_status.txt).
# Uso: update_status.sh SUCCESS|FAILED "<ficheiro de log>" "<detalhe>"
# Formato (separador |): data_hora|hostname|ORACLE_SID|estado|log|detalhe
set -uo pipefail
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_HOME="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
for f in rman_monitor.env rman_monitor.conf; do
    [[ -r "${MONITOR_HOME}/config/$f" ]] && { set -a; source "${MONITOR_HOME}/config/$f"; set +a; }
done
STATUS_FILE="${STATUS_FILE:-${MONITOR_HOME}/logs/rman_status.txt}"

status="${1:?estado em falta (SUCCESS|FAILED)}"
log_path="${2:-nenhum}"
detail="${3:-}"
[[ "$status" == SUCCESS || "$status" == FAILED ]] || { echo "Estado inválido: $status" >&2; exit 2; }

mkdir -p -- "$(dirname -- "$STATUS_FILE")"
detail="$(printf '%s' "$detail" | tr '\n|' '  ' | cut -c1-300)"
printf '%s|%s|%s|%s|%s|%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$(hostname -s)" "${ORACLE_SID:-n/d}" \
    "$status" "$log_path" "$detail" >> "$STATUS_FILE"
