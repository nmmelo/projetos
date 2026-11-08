#!/usr/bin/env bash

set -uo pipefail
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_HOME="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${MONITOR_HOME}/config/rman_monitor.env"

if [[ -r "$CONFIG_FILE" ]]; then
    # This file is an administrator-managed shell configuration file.
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi
if [[ -r "${MONITOR_HOME}/config/rman_monitor.conf" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${MONITOR_HOME}/config/rman_monitor.conf"
    set +a
fi

SUCCESS_LIST_FILE="${SUCCESS_LIST_FILE:-${MONITOR_HOME}/config/tipos_success.txt}"
EXEC_LOG="${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}"
ERROR_LIST_FILE="${ERROR_LIST_FILE:-${MONITOR_HOME}/config/tipos_error.txt}"
mkdir -p -- "$(dirname -- "$EXEC_LOG")" 2>/dev/null || true

log() {
    local message="$(date '+%Y-%m-%dT%H:%M:%S%z') $*"
    printf '%s\n' "$message"
    printf '%s\n' "$message" >> "$EXEC_LOG" 2>/dev/null || true
}

finish_anomaly() {
    local detail="$1" log_path="${2:-nenhum}" excerpt="${3:-}"
    log "FAILED: ${detail}"
    "${SCRIPT_DIR}/clean_rman_space.sh" || true
    "${SCRIPT_DIR}/update_status.sh" FAILED "$log_path" "${detail} ${excerpt}" || true
    "${SCRIPT_DIR}/notify_rman.sh" || log "ERRO: falha ao enviar notificação SMTP"
    exit 1
}

# Diretório definido no setup (RMAN_LOG_DIR) > ORACLE_LOG do ambiente > fallback
if [[ -n "${RMAN_LOG_DIR:-}" ]]; then
    ORACLE_LOG="$RMAN_LOG_DIR"
elif [[ -z "${ORACLE_LOG:-}" ]]; then
    ORACLE_LOG="${FALLBACK_ORACLE_LOG:-}"
fi
if [[ -z "$ORACLE_LOG" ]]; then
    finish_anomaly 'ORACLE_LOG não está definido e FALLBACK_ORACLE_LOG está vazio.'
fi
if [[ ! -d "$ORACLE_LOG" || ! -r "$ORACLE_LOG" || ! -x "$ORACLE_LOG" ]]; then
    finish_anomaly "O diretório ORACLE_LOG não existe ou não é acessível: ${ORACLE_LOG}"
fi
ORACLE_LOG="$(cd -- "$ORACLE_LOG" && pwd -P)"

if [[ ! -r "$ERROR_LIST_FILE" ]]; then
    finish_anomaly "A lista de erros não existe ou não pode ser lida: ${ERROR_LIST_FILE}"
fi

shopt -s nullglob
# Padrões (globs, separados por espaço) dos nomes de log a monitorizar
LOG_FILE_PATTERNS="${LOG_FILE_PATTERNS:-FAPrmanINCU-* FAPSTrman_arch* FAPDBrman_arch* FAPrmanFULL* FAPrmanINCR1* *rman*}"
files=()
# Expande os globs só dentro de ORACLE_LOG (set -f evita expansão no diretório atual)
set -f
for pattern in $LOG_FILE_PATTERNS; do
    set +f
    files+=("$ORACLE_LOG"/$pattern)
    set -f
done
set +f
newest=""
for candidate in "${files[@]}"; do
    [[ -f "$candidate" ]] || continue
    if [[ -z "$newest" || "$candidate" -nt "$newest" ]]; then
        newest="$candidate"
    fi
done

if [[ -z "$newest" ]]; then
    finish_anomaly "Não foram encontrados logs RMAN monitorizados em ${ORACLE_LOG}."
fi

now_epoch=$(date +%s)
file_epoch=$(stat -c %Y -- "$newest" 2>/dev/null) || finish_anomaly "Não foi possível obter a data do log: ${newest}" "$newest"
age_seconds=$(( now_epoch - file_epoch ))
if (( age_seconds > 43200 )); then
    finish_anomaly "O log mais recente tem mais de 12 horas (${age_seconds} segundos)." "$newest"
fi

if grep -F -f "$ERROR_LIST_FILE" -- "$newest" >/dev/null; then
    error_excerpt=$(grep -F -B 1 -A 2 -f "$ERROR_LIST_FILE" -- "$newest" || true)
    finish_anomaly 'Foram encontrados padrões de erro crítico no log RMAN.' "$newest" "$error_excerpt"
else
    grep_status=$?
    if (( grep_status > 1 )); then
        finish_anomaly "Erro ao analisar o log ${newest} (grep: ${grep_status})." "$newest"
    fi
fi

if [[ ! -r "$SUCCESS_LIST_FILE" ]]; then
    finish_anomaly "A lista de sucesso não existe ou não pode ser lida: ${SUCCESS_LIST_FILE}" "$newest"
fi
if ! success_match=$(grep -F -m 1 -f "$SUCCESS_LIST_FILE" -- "$newest"); then
    finish_anomaly "O log não contém nenhum padrão de sucesso (${SUCCESS_LIST_FILE}); backup possivelmente incompleto." "$newest"
fi

log "OK: ${success_match}. Log analisado: ${newest}"
"${SCRIPT_DIR}/clean_rman_space.sh" || true
"${SCRIPT_DIR}/update_status.sh" SUCCESS "$newest" "$success_match" || true
"${SCRIPT_DIR}/notify_rman.sh" || log "ERRO: falha ao enviar notificação SMTP"
exit 0
