#!/usr/bin/env bash
# Verifica o espaço da partição RMAN e executa limpeza automática se a ocupação exceder o limite (por defeito 90%).
# O comando de limpeza é executado com o utilizador oracle via rlrman (ou comando configurado).
# O output é gravado num ficheiro de texto em logs/ e notificado por email com anexo.
set -uo pipefail
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MONITOR_HOME="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

for f in rman_monitor.env rman_monitor.conf; do
    [[ -r "${MONITOR_HOME}/config/$f" ]] && { set -a; source "${MONITOR_HOME}/config/$f"; set +a; }
done

EXEC_LOG="${EXEC_LOG:-${MONITOR_HOME}/logs/monitor_execution.log}"
STATE_FILE="${MONITOR_HOME}/logs/last_cleanup.env"
CMD_FILE="${CMD_FILE:-${MONITOR_HOME}/config/rman_cleanup.cmd}"
mkdir -p -- "$(dirname -- "$EXEC_LOG")" 2>/dev/null || true

log() {
    local message="$(date '+%Y-%m-%dT%H:%M:%S%z') $*"
    printf '%s\n' "$message"
    printf '%s\n' "$message" >> "$EXEC_LOG" 2>/dev/null || true
}

# 1. Verificar se o limiar de limpeza está configurado e ativo (por defeito 90%)
THRESHOLD="${RMAN_CLEANUP_THRESHOLD:-90}"
if [[ -z "$THRESHOLD" || "$THRESHOLD" == "0" || "$THRESHOLD" =~ ^(off|disabled|nao|no)$ ]]; then
    log "Limpeza automática de espaço RMAN desativada (RMAN_CLEANUP_THRESHOLD=${THRESHOLD})."
    rm -f "$STATE_FILE" 2>/dev/null || true
    exit 0
fi

# 2. Determinar a partição/diretório do RMAN a verificar
target_part="${RMAN_PARTITION:-}"
if [[ -z "$target_part" ]]; then
    # Procurar primeiro em MONITORED_PARTITIONS por algum caminho que contenha 'rman'
    if [[ -n "${MONITORED_PARTITIONS:-}" ]]; then
        IFS=',' read -r -a _parts <<< "${MONITORED_PARTITIONS}"
        for p in "${_parts[@]}"; do
            p="$(echo "$p" | xargs)"
            if [[ "$p" =~ [rR][mM][aA][nN] && -e "$p" ]]; then
                target_part="$p"
                break
            fi
        done
    fi
fi
if [[ -z "$target_part" && -n "${RMAN_LOG_DIR:-}" && -e "${RMAN_LOG_DIR}" ]]; then
    target_part="${RMAN_LOG_DIR}"
fi
if [[ -z "$target_part" ]]; then
    for cand in /oracle/rman /oracle /u01/app/oracle; do
        if [[ -e "$cand" ]]; then
            target_part="$cand"
            break
        fi
    done
fi

if [[ -z "$target_part" || ! -e "$target_part" ]]; then
    log "AVISO: Nenhuma partição/diretório RMAN válido encontrado para verificação de espaço (${target_part:-vazio})."
    rm -f "$STATE_FILE" 2>/dev/null || true
    exit 0
fi

# 3. Obter a percentagem de ocupação em disco através de df -Pk
cap_info="$(df -Pk "$target_part" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5, $6}')"
if [[ -z "$cap_info" ]]; then
    log "AVISO: Não foi possível obter o espaço em disco de '${target_part}' via df."
    rm -f "$STATE_FILE" 2>/dev/null || true
    exit 0
fi
read -r used_pct mount_point <<< "$cap_info"

# Validação numérica
if ! [[ "$used_pct" =~ ^[0-9]+$ ]]; then
    log "AVISO: Valor de ocupação inválido obtido para '${target_part}': '${used_pct}'."
    rm -f "$STATE_FILE" 2>/dev/null || true
    exit 0
fi

if (( used_pct <= THRESHOLD )); then
    log "Espaço RMAN OK: '${target_part}' (${mount_point}) com ${used_pct}% ocupado (limite: ${THRESHOLD}%)."
    rm -f "$STATE_FILE" 2>/dev/null || true
    exit 0
fi

# 4. Limiar ultrapassado: Executar limpeza com o utilizador oracle
log "ALERTA: Partição RMAN '${target_part}' (${mount_point}) com ${used_pct}% de ocupação > limite de ${THRESHOLD}%. A iniciar limpeza automática..."

mkdir -p -- "${MONITOR_HOME}/logs" 2>/dev/null || true
ts_clean="$(date +%Y%m%d_%H%M%S)"
CLEANUP_LOG="${MONITOR_HOME}/logs/rman_cleanup_${ts_clean}.txt"

RMAN_CMD="${RMAN_CLEANUP_CMD:-rlrman target / catalog /@ADMINDB}"

# Comandos predefinidos caso o ficheiro não exista
if [[ -r "$CMD_FILE" ]]; then
    rman_commands="$(cat "$CMD_FILE")"
else
    rman_commands="CROSSCHECK BACKUP;
DELETE NOPROMPT BACKUP COMPLETED BEFORE 'SYSDATE-7';
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'sysdate-7';
EXIT;"
fi

{
    echo "================================================================================"
    echo "RELATÓRIO DE LIMPEZA AUTOMÁTICA DE ESPAÇO RMAN"
    echo "Data/Hora de Início: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "Servidor:            $(hostname -f 2>/dev/null || hostname)"
    echo "Partição Analisada:  ${target_part} (Ponto de montagem: ${mount_point})"
    echo "Ocupação Anterior:   ${used_pct}% (Limite configurado: ${THRESHOLD}%)"
    echo "Comando Executado:   ${RMAN_CMD}"
    echo "Utilizador Destino:  oracle"
    echo "================================================================================"
    echo
    echo "Comandos RMAN submetidos:"
    echo "--------------------------------------------------------------------------------"
    echo "$rman_commands"
    echo "--------------------------------------------------------------------------------"
    echo
    echo "Output da execução:"
    echo "--------------------------------------------------------------------------------"
} > "$CLEANUP_LOG"

current_user="$(id -un 2>/dev/null || whoami)"
exec_rc=0

# Execução com o utilizador oracle
if [[ "$current_user" == "oracle" ]]; then
    eval "$RMAN_CMD" <<< "$rman_commands" >> "$CLEANUP_LOG" 2>&1 || exec_rc=$?
elif [[ "$current_user" == "root" ]]; then
    su - oracle -c "$RMAN_CMD" <<< "$rman_commands" >> "$CLEANUP_LOG" 2>&1 || exec_rc=$?
elif command -v sudo >/dev/null 2>&1 && sudo -n -u oracle true 2>/dev/null; then
    sudo -u oracle bash -l -c "$RMAN_CMD" <<< "$rman_commands" >> "$CLEANUP_LOG" 2>&1 || exec_rc=$?
else
    if command -v su >/dev/null 2>&1; then
        su - oracle -c "$RMAN_CMD" <<< "$rman_commands" >> "$CLEANUP_LOG" 2>&1 || exec_rc=$?
    else
        eval "$RMAN_CMD" <<< "$rman_commands" >> "$CLEANUP_LOG" 2>&1 || exec_rc=$?
    fi
fi

# Obter espaço em disco pós-limpeza
cap_after="$(df -Pk "$target_part" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5, $6}')"
read -r used_pct_after mount_after <<< "${cap_after:-desconhecido $mount_point}"

{
    echo
    echo "--------------------------------------------------------------------------------"
    echo "Código de saída (RC): ${exec_rc}"
    echo "Ocupação pós-limpeza: ${used_pct_after}%"
    echo "Data/Hora de Fim:    $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "================================================================================"
} >> "$CLEANUP_LOG"

chmod 644 "$CLEANUP_LOG" 2>/dev/null || true

# Gravar ficheiro de estado para que notify_rman.sh notifique por email e anexe o .txt
{
    echo "CLEANUP_EXECUTED=true"
    echo "CLEANUP_PARTITION=\"${target_part}\""
    echo "CLEANUP_MOUNT=\"${mount_point}\""
    echo "CLEANUP_USAGE_BEFORE=\"${used_pct}\""
    echo "CLEANUP_USAGE_AFTER=\"${used_pct_after}\""
    echo "CLEANUP_THRESHOLD=\"${THRESHOLD}\""
    echo "CLEANUP_LOG_PATH=\"${CLEANUP_LOG}\""
    echo "CLEANUP_RC=\"${exec_rc}\""
    echo "CLEANUP_DATE=\"$(date '+%Y-%m-%d %H:%M:%S')\""
} > "$STATE_FILE"
chmod 600 "$STATE_FILE" 2>/dev/null || true

log "Limpeza RMAN concluída (RC=${exec_rc}). Ocupação: ${used_pct}% -> ${used_pct_after}%. Log gerado: ${CLEANUP_LOG}"
exit 0

